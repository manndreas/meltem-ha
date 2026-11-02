"""Tests for the reusable RTU-over-TCP mock gateway."""

from __future__ import annotations

import argparse
import asyncio
import json
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest
from homeassistant import config_entries
from homeassistant.components.modbus import async_get_temporary_unit
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.json import ExtendedJSONEncoder
from pytest_homeassistant.common import MockConfigEntry

from custom_components.meltem_ventilation.const import (
    CONF_CONNECTION_TYPE,
    CONF_HOST,
    CONF_MAX_REQUESTS_PER_SECOND,
    CONF_NETWORK_PORT,
    CONNECTION_RTU_OVER_TCP,
    DOMAIN,
    MODE_MANUAL,
    NETWORK_TIMEOUT,
    REGISTER_CO2_STARTING_POINT,
    REGISTER_MODE_STATUS,
    SUBENTRY_TYPE_UNIT,
)
from custom_components.meltem_ventilation.diagnostics import (
    async_get_config_entry_diagnostics,
)
from custom_components.meltem_ventilation.flow_helpers import detect_slave_details
from custom_components.meltem_ventilation.gateway_connection import (
    RtuTcpGatewayConnection,
)
from custom_components.meltem_ventilation.identifiers import room_entity_unique_id
from custom_components.meltem_ventilation.modbus_client import MeltemModbusClient
from custom_components.meltem_ventilation.modbus_helpers import (
    discover_gateway_nodes,
    read_gateway_node_count,
)
from custom_components.meltem_ventilation.models import RoomConfig
from custom_components.meltem_ventilation.units import unit_subentry_data
from tools.mock_gateway import DEFAULT_UNITS, RtuTcpMockGateway, parse_units

_REPO_ROOT = Path(__file__).resolve().parents[3]
_MOCK_READY = "Mock Meltem gateway listening on 127.0.0.1:"


async def _start_cli_gateway(
    *options: str, port: int = 0
) -> tuple[asyncio.subprocess.Process, int]:
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "tools.mock_gateway",
        "--port",
        str(port),
        *options,
        cwd=_REPO_ROOT,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    try:
        assert process.stdout is not None
        ready = (await asyncio.wait_for(process.stdout.readline(), 5)).decode().strip()
        assert ready.startswith(_MOCK_READY), ready
        return process, int(ready.removeprefix(_MOCK_READY))
    except BaseException:
        if process.returncode is None:
            process.terminate()
            await process.wait()
        raise


async def _stop_cli_gateway(process: asyncio.subprocess.Process) -> bytes:
    if process.returncode is None:
        process.terminate()
    output, _ = await asyncio.wait_for(process.communicate(), 5)
    return output or b""


async def _wait_for_entities(hass: HomeAssistant, entity_ids: tuple[str, ...]) -> None:
    for _ in range(60):
        await hass.async_block_till_done()
        if all(
            (state := hass.states.get(entity_id)) is not None and state.state != STATE_UNAVAILABLE
            for entity_id in entity_ids
        ):
            return
        await asyncio.sleep(0.25)
    states = {entity_id: hass.states.get(entity_id) for entity_id in entity_ids}
    raise AssertionError(f"Smoke-test entities did not become available: {states}")


@pytest.mark.parametrize(
    "value",
    ["", "2", "1:ii_plain", "2:unknown", "2:ii_f,2:ii_fc"],
)
def test_parse_units_rejects_invalid_addresses_and_profiles(value: str) -> None:
    with pytest.raises(argparse.ArgumentTypeError):
        parse_units(value)


def test_parse_units_reads_the_documented_default() -> None:
    assert parse_units(DEFAULT_UNITS) == {2: "ii_fc_voc", 3: "ii_f"}


async def test_mock_gateway_serves_discovery_profiles_and_committed_writes(
    hass: HomeAssistant, socket_enabled: None
) -> None:
    gateway = RtuTcpMockGateway(parse_units(DEFAULT_UNITS))
    port = await gateway.start()
    connection = RtuTcpGatewayConnection("127.0.0.1", port)
    try:
        async with (
            async_get_temporary_unit(hass, connection.modbus_params(), 1) as gateway_unit,
            async_get_temporary_unit(hass, connection.modbus_params(), 2) as room_unit,
        ):
            client = MeltemModbusClient(
                lambda slave: {1: gateway_unit, 2: room_unit}[slave],
                port=connection.unique_id,
                timeout=NETWORK_TIMEOUT,
            )
            try:
                await client.async_validate_gateway()
                assert await read_gateway_node_count(client.unit_for(1)) == 2
                assert await discover_gateway_nodes(
                    client.unit_for(1), connection.unique_id, start=2, end=16
                ) == [2, 3]
                assert (await detect_slave_details(client.unit_for(2))).profile == "fc_voc"

                room = RoomConfig(key="slave_2", name="Unit 2", profile="ii_fc_voc", slave=2)
                await client.write_level(room, 50)
                assert await client.unit_for(2).read_holding_registers(REGISTER_MODE_STATUS, 3) == [
                    MODE_MANUAL,
                    100,
                    100,
                ]
            finally:
                client.shutdown()
    finally:
        await gateway.close()

    assert gateway.writes == 3


async def test_mock_without_units_returns_a_valid_empty_discovery(
    hass: HomeAssistant, socket_enabled: None
) -> None:
    gateway = RtuTcpMockGateway(parse_units(DEFAULT_UNITS), no_units=True)
    port = await gateway.start()
    connection = RtuTcpGatewayConnection("127.0.0.1", port)

    try:
        async with async_get_temporary_unit(hass, connection.modbus_params(), 1) as gateway_unit:
            assert await read_gateway_node_count(gateway_unit) == 0
            assert (
                await discover_gateway_nodes(gateway_unit, connection.unique_id, start=2, end=16)
                == []
            )
    finally:
        await gateway.close()


async def test_end_to_end_setup_entities_diagnostics_options_and_reconfigure(
    hass: HomeAssistant, socket_enabled: None
) -> None:
    gateway_process, port = await _start_cli_gateway("--single-client")
    loop = asyncio.get_running_loop()
    getaddrinfo = loop.getaddrinfo
    gateway_output = b""
    try:
        flow = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
        flow = await hass.config_entries.flow.async_configure(
            flow["flow_id"],
            {
                CONF_CONNECTION_TYPE: CONNECTION_RTU_OVER_TCP,
                CONF_HOST: "127.0.0.1",
                CONF_NETWORK_PORT: port,
            },
        )
        assert flow["step_id"] == "profiles"
        flow = await hass.config_entries.flow.async_configure(
            flow["flow_id"], {"slave_2": "ii_fc_voc"}
        )
        entry = flow["result"]
        await hass.async_block_till_done()
        assert entry.state is ConfigEntryState.LOADED

        entities = er.async_entries_for_config_entry(er.async_get(hass), entry.entry_id)
        assert {entity.domain for entity in entities} >= {
            "sensor",
            "binary_sensor",
            "fan",
            "number",
            "select",
            "switch",
        }
        registry = er.async_get(hass)

        fan_entity_id = registry.async_get_entity_id(
            "fan",
            DOMAIN,
            room_entity_unique_id(entry.entry_id, "slave_2", "supply_level"),
        )
        extract_entity_id = registry.async_get_entity_id(
            "fan",
            DOMAIN,
            room_entity_unique_id(entry.entry_id, "slave_2", "extract_level"),
        )
        assert fan_entity_id is not None
        assert extract_entity_id is not None
        number_entity_id = registry.async_get_entity_id(
            "number",
            DOMAIN,
            room_entity_unique_id(entry.entry_id, "slave_2", "co2_starting_point"),
        )
        assert number_entity_id is not None
        await _wait_for_entities(hass, (fan_entity_id, extract_entity_id, number_entity_id))
        await hass.services.async_call(
            "fan",
            "set_percentage",
            {"entity_id": fan_entity_id, "percentage": 50},
            blocking=True,
        )
        await hass.services.async_call(
            "fan",
            "set_percentage",
            {"entity_id": extract_entity_id, "percentage": 50},
            blocking=True,
        )
        await hass.services.async_call(
            "number",
            "set_value",
            {"entity_id": number_entity_id, "value": 850},
            blocking=True,
        )

        diagnostics = await async_get_config_entry_diagnostics(hass, entry)
        assert diagnostics["entry"]["data"][CONF_HOST] != "127.0.0.1"
        assert "127.0.0.1" not in json.dumps(diagnostics, cls=ExtendedJSONEncoder)

        options = await hass.config_entries.options.async_init(entry.entry_id)
        options = await hass.config_entries.options.async_configure(
            options["flow_id"], {"next_step_id": "edit_request_rate"}
        )
        options = await hass.config_entries.options.async_configure(
            options["flow_id"], {CONF_MAX_REQUESTS_PER_SECOND: 5.0}
        )
        assert entry.options[CONF_MAX_REQUESTS_PER_SECOND] == 5.0

        async def resolve_localhost(host: str, *args: object, **kwargs: object) -> object:
            return await getaddrinfo("127.0.0.1" if host == "localhost" else host, *args, **kwargs)

        with (
            patch.object(loop, "getaddrinfo", side_effect=resolve_localhost),
            patch.object(hass.config_entries, "async_schedule_reload"),
        ):
            reconfigure = await hass.config_entries.flow.async_init(
                DOMAIN,
                context={
                    "source": config_entries.SOURCE_RECONFIGURE,
                    "entry_id": entry.entry_id,
                },
            )
            reconfigure = await hass.config_entries.flow.async_configure(
                reconfigure["flow_id"],
                {
                    CONF_CONNECTION_TYPE: CONNECTION_RTU_OVER_TCP,
                    CONF_HOST: "localhost",
                    CONF_NETWORK_PORT: port,
                },
            )
        assert reconfigure["type"] is FlowResultType.ABORT
        assert reconfigure["reason"] == "reconfigure_successful"
        assert entry.data[CONF_HOST] == "localhost"
    finally:
        for entry in hass.config_entries.async_entries(DOMAIN):
            if entry.state is ConfigEntryState.LOADED:
                await hass.config_entries.async_unload(entry.entry_id)
        if gateway_process is not None:
            gateway_output += await _stop_cli_gateway(gateway_process)
    assert b"WRITE unit=2 41120 = 3" in gateway_output
    assert b"WRITE unit=2 41121 = 100" in gateway_output
    assert b"WRITE unit=2 41132 = 0" in gateway_output
    assert f"WRITE unit=2 {REGISTER_CO2_STARTING_POINT} = 850".encode() in gateway_output


async def test_add_unit_flow_reloads_from_the_live_cli_mock(
    hass: HomeAssistant, socket_enabled: None
) -> None:
    gateway_process, port = await _start_cli_gateway("--single-client")
    connection = RtuTcpGatewayConnection("127.0.0.1", port)
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Meltem Gateway M-WRG-GW",
        data=connection.as_data(),
        unique_id=connection.unique_id,
        subentries_data=[
            unit_subentry_data(
                {
                    "key": "slave_2",
                    "name": "Unit 1",
                    "slave": 2,
                    "profile": "ii_fc_voc",
                }
            )
        ],
        version=2,
        minor_version=1,
        source="user",
    )
    entry.add_to_hass(hass)
    gateway_output = b""
    try:
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        add_unit = await hass.config_entries.subentries.async_init(
            (entry.entry_id, SUBENTRY_TYPE_UNIT),
            context={"source": config_entries.SOURCE_USER},
        )
        assert add_unit["step_id"] == "profile"
        assert add_unit["description_placeholders"]["slave"] == "3"
        add_unit = await hass.config_entries.subentries.async_configure(
            add_unit["flow_id"], {"profile": "ii_f"}
        )
        assert add_unit["type"] is FlowResultType.CREATE_ENTRY
        await hass.async_block_till_done()
        assert entry.state is ConfigEntryState.LOADED
        assert len(entry.subentries) == 2
        assert er.async_get(hass).async_get_entity_id(
            "fan",
            DOMAIN,
            room_entity_unique_id(entry.entry_id, "slave_3", "supply_level"),
        )
    finally:
        if entry.state is ConfigEntryState.LOADED:
            await hass.config_entries.async_unload(entry.entry_id)
        gateway_output = await _stop_cli_gateway(gateway_process)
    assert b"READ unit=3" in gateway_output


async def test_no_units_setup_error_can_retry_against_a_live_cli_mock(
    hass: HomeAssistant, socket_enabled: None
) -> None:
    empty_gateway: asyncio.subprocess.Process | None = None
    working_gateway: asyncio.subprocess.Process | None = None
    try:
        empty_gateway, empty_port = await _start_cli_gateway("--no-units")
        flow = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
        flow = await hass.config_entries.flow.async_configure(
            flow["flow_id"],
            {
                CONF_CONNECTION_TYPE: CONNECTION_RTU_OVER_TCP,
                CONF_HOST: "127.0.0.1",
                CONF_NETWORK_PORT: empty_port,
            },
        )
        assert flow["step_id"] == "user"
        assert flow["errors"] == {"base": "no_devices_found"}

        await _stop_cli_gateway(empty_gateway)
        empty_gateway = None
        working_gateway, working_port = await _start_cli_gateway()
        flow = await hass.config_entries.flow.async_configure(
            flow["flow_id"],
            {
                CONF_CONNECTION_TYPE: CONNECTION_RTU_OVER_TCP,
                CONF_HOST: "127.0.0.1",
                CONF_NETWORK_PORT: working_port,
            },
        )
        assert flow["step_id"] == "profiles"
        flow = await hass.config_entries.flow.async_configure(
            flow["flow_id"], {"slave_2": "ii_fc_voc", "slave_3": "ii_f"}
        )
        entry = flow["result"]
        await hass.async_block_till_done()
        assert entry.state is ConfigEntryState.LOADED
    finally:
        for entry in hass.config_entries.async_entries(DOMAIN):
            if entry.state is ConfigEntryState.LOADED:
                await hass.config_entries.async_unload(entry.entry_id)
        if empty_gateway is not None:
            await _stop_cli_gateway(empty_gateway)
        if working_gateway is not None:
            await _stop_cli_gateway(working_gateway)


def test_mock_gateway_cli_help() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "tools.mock_gateway", "--help"],
        cwd=_REPO_ROOT,
        capture_output=True,
        check=True,
        text=True,
    )
    assert "RTU-over-TCP mock" in result.stdout
