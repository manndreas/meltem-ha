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
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.json import ExtendedJSONEncoder

from custom_components.meltem_ventilation.const import (
    CONF_CONNECTION_TYPE,
    CONF_HOST,
    CONF_MAX_REQUESTS_PER_SECOND,
    CONF_NETWORK_PORT,
    CONNECTION_RTU_OVER_TCP,
    DOMAIN,
    MODE_MANUAL,
    NETWORK_TIMEOUT,
    REGISTER_APPLY,
    REGISTER_CURRENT_LEVEL,
    REGISTER_MODE,
    REGISTER_MODE_STATUS,
)
from custom_components.meltem_ventilation.diagnostics import (
    async_get_config_entry_diagnostics,
)
from custom_components.meltem_ventilation.flow_helpers import detect_slave_details
from custom_components.meltem_ventilation.gateway_connection import (
    RtuTcpGatewayConnection,
)
from custom_components.meltem_ventilation.modbus_client import MeltemModbusClient
from custom_components.meltem_ventilation.modbus_helpers import (
    discover_gateway_nodes,
    read_gateway_node_count,
)
from custom_components.meltem_ventilation.models import RoomConfig
from tools.mock_gateway import DEFAULT_UNITS, RtuTcpMockGateway, parse_units

_REPO_ROOT = Path(__file__).resolve().parents[3]


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
    gateway = RtuTcpMockGateway(parse_units(DEFAULT_UNITS), single_client=True)
    port = await gateway.start()
    loop = asyncio.get_running_loop()
    getaddrinfo = loop.getaddrinfo
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
            flow["flow_id"], {"slave_2": "ii_fc_voc", "slave_3": "ii_f"}
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

        await entry.runtime_data.coordinator.async_set_level("slave_2", 50)
        assert gateway.registers[2][REGISTER_MODE] == MODE_MANUAL
        assert gateway.registers[2][REGISTER_CURRENT_LEVEL] == 100
        assert gateway.registers[2][REGISTER_APPLY] == 0
        assert gateway.writes >= 3

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
        await gateway.close()


def test_mock_gateway_cli_help() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "tools.mock_gateway", "--help"],
        cwd=_REPO_ROOT,
        capture_output=True,
        check=True,
        text=True,
    )
    assert "RTU-over-TCP mock" in result.stdout
