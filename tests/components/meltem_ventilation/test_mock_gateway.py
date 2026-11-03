"""Tests for the reusable RTU-over-TCP mock gateway."""

from __future__ import annotations

import argparse
import asyncio
import json
import subprocess
import sys
from collections.abc import AsyncIterator, Mapping
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from homeassistant import config_entries
from homeassistant.components.modbus import async_get_temporary_unit
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.json import ExtendedJSONEncoder
from modbus_connection import AcknowledgeError, IllegalDataAddressError
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
    PROFILE_METADATA,
    REGISTER_CO2_EXTRACT_AIR,
    REGISTER_CO2_STARTING_POINT,
    REGISTER_CO2_SUPPLY_AIR,
    REGISTER_CURRENT_LEVEL,
    REGISTER_EXTERNAL_INPUT_LEVEL,
    REGISTER_EXTERNAL_INPUT_ON_DELAY,
    REGISTER_EXTERNAL_INPUT_RUN_ON,
    REGISTER_EXTRACT_AIR_FLOW,
    REGISTER_EXTRACT_AIR_TEMPERATURE,
    REGISTER_FROST_PROTECTION_ACTIVE,
    REGISTER_GATEWAY_NUMBER_OF_NODES,
    REGISTER_HUMIDITY_EXTRACT_AIR,
    REGISTER_HUMIDITY_STARTING_POINT,
    REGISTER_HUMIDITY_SUPPLY_AIR,
    REGISTER_MODE_STATUS,
    REGISTER_PRODUCT_ID,
    REGISTER_RF_LAST_SEEN_AGE,
    REGISTER_VOC_EXTRACT_AIR,
    REGISTER_VOC_SUPPLY_AIR,
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
from tools.mock_gateway import (
    DEFAULT_UNITS,
    NO_HUMIDITY_VALUE,
    NO_SENSOR_VALUE,
    RtuTcpMockGateway,
    build_gateway,
    parse_args,
    parse_units,
    request_echo,
    rtu_frame,
)

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
        stdin=asyncio.subprocess.PIPE,
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


@pytest.mark.parametrize(
    ("profile", "product_id"),
    [
        ("s_plain", 116819),
        ("s_f", 116819),
        ("s_fc", 116819),
        ("ii_plain", 116852),
        ("ii_f", 116852),
        ("ii_fc", 116852),
        ("ii_fc_voc", 116852),
    ],
)
def test_mock_product_id_matches_simulated_series(profile: str, product_id: int) -> None:
    gateway = RtuTcpMockGateway({2: profile})
    registers = gateway.registers[2]
    assert registers[REGISTER_PRODUCT_ID] | registers[REGISTER_PRODUCT_ID + 1] << 16 == product_id


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
            async_get_temporary_unit(hass, connection.modbus_params(), 3) as humidity_unit,
        ):
            units = {1: gateway_unit, 2: room_unit, 3: humidity_unit}
            client = MeltemModbusClient(
                lambda slave: units[slave],
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
                assert (await detect_slave_details(client.unit_for(3))).profile == "f"

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


@pytest.mark.parametrize(
    ("units", "no_units", "silent_address"),
    [
        pytest.param({2: "ii_plain"}, False, 9, id="unknown-address"),
        pytest.param({2: "ii_plain", 3: "ii_f"}, True, 3, id="no-units"),
    ],
)
async def test_unpaired_addresses_time_out_like_the_gateway(
    socket_enabled: None, units: dict[int, str], no_units: bool, silent_address: int
) -> None:
    gateway = RtuTcpMockGateway(units, no_units=no_units)
    port = await gateway.start()
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    try:
        writer.write(_read_frame(silent_address, REGISTER_PRODUCT_ID, 2))
        await writer.drain()
        with pytest.raises(TimeoutError):
            await asyncio.wait_for(reader.read(1), 0.2)

        # The gateway itself still answers on the same connection.
        writer.write(_read_frame(1, REGISTER_GATEWAY_NUMBER_OF_NODES, 1))
        await writer.drain()
        response = await asyncio.wait_for(reader.readexactly(7), 2)
        assert response[:5] == bytes((1, 3, 2, 0, 0 if no_units else len(units)))
    finally:
        writer.close()
        await writer.wait_closed()
        await gateway.close()


async def test_close_returns_while_a_client_is_connected(socket_enabled: None) -> None:
    gateway = RtuTcpMockGateway({2: "ii_plain"})
    port = await gateway.start()
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    try:
        writer.write(_read_frame(1, REGISTER_GATEWAY_NUMBER_OF_NODES, 1))
        await writer.drain()
        await asyncio.wait_for(reader.readexactly(7), 2)

        await asyncio.wait_for(gateway.close(), 2)

        assert await asyncio.wait_for(reader.read(), 2) == b""
    finally:
        writer.close()
        await writer.wait_closed()


@pytest.mark.parametrize("profile", sorted(PROFILE_METADATA))
def test_mock_sensor_registers_report_the_hardware_placeholders(profile: str) -> None:
    capabilities = PROFILE_METADATA[profile].capabilities
    registers = RtuTcpMockGateway({2: profile}).registers[2]

    # Units without a sensor reported these placeholders on hardware (docs/dev/MELTEM.md).
    if "humidity" not in capabilities:
        assert registers[REGISTER_HUMIDITY_EXTRACT_AIR] == NO_HUMIDITY_VALUE
        assert registers[REGISTER_HUMIDITY_SUPPLY_AIR] == NO_HUMIDITY_VALUE
    if "co2" not in capabilities:
        assert registers[REGISTER_CO2_EXTRACT_AIR] == NO_SENSOR_VALUE
    if "voc" not in capabilities:
        for register in (
            REGISTER_VOC_EXTRACT_AIR,
            REGISTER_CO2_SUPPLY_AIR,
            REGISTER_VOC_SUPPLY_AIR,
        ):
            assert registers[register] == NO_SENSOR_VALUE


def test_mock_external_input_registers_hold_valid_defaults() -> None:
    registers = RtuTcpMockGateway({2: "ii_f"}).registers[2]

    # The integration rejects a level outside 10..100, so a zero would fail every read.
    assert registers[REGISTER_EXTERNAL_INPUT_LEVEL] == 60
    assert registers[REGISTER_EXTERNAL_INPUT_ON_DELAY] == 1
    assert registers[REGISTER_EXTERNAL_INPUT_RUN_ON] == 15


def _created_entry(result: Mapping[str, object]) -> config_entries.ConfigEntry:
    entry = result.get("result")
    assert isinstance(entry, config_entries.ConfigEntry)
    return entry


def _read_frame(unit_id: int, address: int, count: int) -> bytes:
    return rtu_frame(bytes((unit_id, 3)) + request_echo(address, count))


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
        assert flow.get("step_id") == "profiles"
        placeholders = flow.get("description_placeholders")
        assert placeholders is not None
        assert placeholders["slave_2_details"].startswith(": M-WRG-II")
        flow = await hass.config_entries.flow.async_configure(
            flow["flow_id"], {"slave_2": "ii_fc_voc"}
        )
        entry = _created_entry(flow)
        await hass.async_block_till_done()
        assert entry.state is ConfigEntryState.LOADED
        device = dr.async_get(hass).async_get_device_by_identifier(
            (DOMAIN, "slave_2"), entry.entry_id
        )
        assert device is not None
        assert device.name == "Meltem M-WRG-II (R00A002)"
        assert (device.model, device.model_id, device.hw_version) == (
            "M-WRG-II (O/VOC-AUL)",
            "VMD-22RPS44",
            "116852",
        )

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
        diagnostic_json = json.dumps(diagnostics, cls=ExtendedJSONEncoder)
        assert "127.0.0.1" not in diagnostic_json
        assert "R00A002" not in diagnostic_json

        options = await hass.config_entries.options.async_init(entry.entry_id)
        options = await hass.config_entries.options.async_configure(
            options["flow_id"], {"next_step_id": "edit_request_rate"}
        )
        options = await hass.config_entries.options.async_configure(
            options["flow_id"], {CONF_MAX_REQUESTS_PER_SECOND: 5.0}
        )
        assert entry.options[CONF_MAX_REQUESTS_PER_SECOND] == 5.0

        async def resolve_localhost(host: str, *args: Any, **kwargs: Any) -> Any:
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
        assert reconfigure.get("type") is FlowResultType.ABORT
        assert reconfigure.get("reason") == "reconfigure_successful"
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
        assert add_unit.get("step_id") == "profile"
        unit_placeholders = add_unit.get("description_placeholders")
        assert unit_placeholders is not None
        assert unit_placeholders["slave"] == "3"
        add_unit = await hass.config_entries.subentries.async_configure(
            add_unit["flow_id"], {"profile": "ii_f"}
        )
        assert add_unit.get("type") is FlowResultType.CREATE_ENTRY
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
        assert flow.get("step_id") == "user"
        assert flow.get("errors") == {"base": "no_devices_found"}

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
        assert flow.get("step_id") == "profiles"
        flow = await hass.config_entries.flow.async_configure(
            flow["flow_id"], {"slave_2": "ii_fc_voc", "slave_3": "ii_f"}
        )
        entry = _created_entry(flow)
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


_AIRFLOW_REPLY = rtu_frame(bytes((2, 3, 4)) + (40).to_bytes(2, "big") * 2)

type _MockLink = tuple[RtuTcpMockGateway, asyncio.StreamReader, asyncio.StreamWriter]


@pytest.fixture(name="mock_link")
async def mock_link_fixture(socket_enabled: None) -> AsyncIterator[_MockLink]:
    gateway = RtuTcpMockGateway({2: "ii_plain"})
    port = await gateway.start()
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    try:
        yield gateway, reader, writer
    finally:
        writer.close()
        await writer.wait_closed()
        await gateway.close()


async def _send(writer: asyncio.StreamWriter, frame: bytes) -> None:
    writer.write(frame)
    await writer.drain()


async def _assert_no_reply(reader: asyncio.StreamReader) -> None:
    with pytest.raises(TimeoutError):
        await asyncio.wait_for(reader.read(1), 0.2)


@pytest.mark.parametrize(
    ("action", "expected"),
    [
        ("bad-crc", _AIRFLOW_REPLY[:-1] + bytes((_AIRFLOW_REPLY[-1] ^ 0xFF,))),
        ("short", _AIRFLOW_REPLY[:-2]),
        ("wrong-unit", rtu_frame(bytes((3,)) + _AIRFLOW_REPLY[1:-2])),
        ("wrong-function", rtu_frame(bytes((2, 4)) + _AIRFLOW_REPLY[2:-2])),
        ("wrong-length", rtu_frame(bytes((2, 3, 6)) + _AIRFLOW_REPLY[3:-2] + b"\0\0")),
        # Write-only faults leave a read reply alone.
        ("wrong-address", _AIRFLOW_REPLY),
        ("fragmented", _AIRFLOW_REPLY),
        ("illegal-address", rtu_frame(bytes((2, 0x83, 0x02)))),
        ("ack", rtu_frame(bytes((2, 0x83, 0x05)))),
        ("busy", rtu_frame(bytes((2, 0x83, 0x06)))),
        ("gateway-path", rtu_frame(bytes((2, 0x83, 0x0A)))),
        ("gateway-target", rtu_frame(bytes((2, 0x83, 0x0B)))),
    ],
)
async def test_scripted_faults_shape_only_the_next_reply(
    mock_link: _MockLink, action: str, expected: bytes
) -> None:
    gateway, reader, writer = mock_link
    assert gateway.execute(f"fault 2 {action}") == f"next 1 reply to 2: {action}"

    await _send(writer, _read_frame(2, REGISTER_EXTRACT_AIR_FLOW, 2))
    assert await asyncio.wait_for(reader.readexactly(len(expected)), 2) == expected

    await _send(writer, _read_frame(2, REGISTER_EXTRACT_AIR_FLOW, 2))
    assert await asyncio.wait_for(reader.readexactly(len(_AIRFLOW_REPLY)), 2) == _AIRFLOW_REPLY


@pytest.mark.parametrize(
    ("action", "echo"),
    [
        ("wrong-address", request_echo(REGISTER_HUMIDITY_STARTING_POINT + 1, 50)),
        ("wrong-value", request_echo(REGISTER_HUMIDITY_STARTING_POINT, 51)),
        # Read-only faults leave a write echo alone.
        ("wrong-length", request_echo(REGISTER_HUMIDITY_STARTING_POINT, 50)),
    ],
)
async def test_write_echo_faults(mock_link: _MockLink, action: str, echo: bytes) -> None:
    gateway, reader, writer = mock_link
    gateway.inject_fault(action, 2)

    await _send(
        writer, rtu_frame(bytes((2, 6)) + request_echo(REGISTER_HUMIDITY_STARTING_POINT, 50))
    )

    assert await asyncio.wait_for(reader.readexactly(8), 2) == rtu_frame(bytes((2, 6)) + echo)
    assert gateway.model.peek(2, REGISTER_HUMIDITY_STARTING_POINT) == [50]


async def test_timeouts_lost_replies_and_late_replies(mock_link: _MockLink) -> None:
    gateway, reader, writer = mock_link
    write = rtu_frame(bytes((2, 6)) + request_echo(REGISTER_HUMIDITY_STARTING_POINT, 50))

    # A lost request never reaches the unit.
    gateway.inject_fault("timeout", 2)
    await _send(writer, write)
    await _assert_no_reply(reader)
    assert gateway.model.peek(2, REGISTER_HUMIDITY_STARTING_POINT) == [55]

    # A lost reply leaves the write applied.
    gateway.inject_fault("lost-reply")
    await _send(writer, write)
    await _assert_no_reply(reader)
    assert gateway.model.peek(2, REGISTER_HUMIDITY_STARTING_POINT) == [50]

    gateway.inject_fault("late", 2)
    await _send(writer, _read_frame(2, REGISTER_EXTRACT_AIR_FLOW, 2))
    await _assert_no_reply(reader)
    await _send(writer, _read_frame(2, REGISTER_RF_LAST_SEEN_AGE, 1))
    reply = rtu_frame(bytes((2, 3, 2, 0, 0)))
    assert (
        await asyncio.wait_for(reader.readexactly(len(_AIRFLOW_REPLY) + len(reply)), 2)
        == _AIRFLOW_REPLY + reply
    )


async def test_rates_and_unit_delays(mock_link: _MockLink) -> None:
    gateway, reader, writer = mock_link
    request = _read_frame(2, REGISTER_EXTRACT_AIR_FLOW, 2)

    gateway.execute("drop-rate 1")
    await _send(writer, request)
    await _assert_no_reply(reader)

    gateway.execute("drop-rate 0")
    gateway.execute("corrupt-rate 1")
    await _send(writer, request)
    reply = await asyncio.wait_for(reader.readexactly(len(_AIRFLOW_REPLY)), 2)
    assert reply[:-2] == _AIRFLOW_REPLY[:-2]
    assert reply != _AIRFLOW_REPLY

    gateway.execute("corrupt-rate 0")
    gateway.execute("delay 2 300")
    loop = asyncio.get_running_loop()
    started = loop.time()
    await _send(writer, request)
    assert await asyncio.wait_for(reader.readexactly(len(_AIRFLOW_REPLY)), 2) == _AIRFLOW_REPLY
    assert loop.time() - started >= 0.25


async def test_real_client_sees_warmup_refusals_and_locked_set_registers(
    hass: HomeAssistant, socket_enabled: None
) -> None:
    gateway = RtuTcpMockGateway({2: "ii_f"}, warmup_seconds=60, refused=[(2, (41007, 41007))])
    port = await gateway.start()
    connection = RtuTcpGatewayConnection("127.0.0.1", port)
    try:
        async with async_get_temporary_unit(hass, connection.modbus_params(), 2) as unit:
            with pytest.raises(AcknowledgeError):
                await unit.read_holding_registers(REGISTER_EXTRACT_AIR_TEMPERATURE, 6)
            # The merged air-quality block of a -F unit fails as a whole (HW-10).
            with pytest.raises(IllegalDataAddressError):
                await unit.read_holding_registers(REGISTER_HUMIDITY_EXTRACT_AIR, 6)
            with pytest.raises(AcknowledgeError):
                await unit.read_holding_registers(REGISTER_CURRENT_LEVEL, 1)

            gateway.execute("warmup 0")
            assert len(await unit.read_holding_registers(REGISTER_EXTRACT_AIR_TEMPERATURE, 6)) == 6
    finally:
        await gateway.close()


async def test_control_port_runs_runtime_commands(socket_enabled: None) -> None:
    gateway = RtuTcpMockGateway(parse_units(DEFAULT_UNITS))
    await gateway.start()
    port = await gateway.start_control()
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    try:
        await _send(writer, b"pair 4 ii_f\nget 1 43901 4\n")
        assert b"unit 4 paired" in await asyncio.wait_for(reader.readline(), 2)
        assert await asyncio.wait_for(reader.readline(), 2) == b"43901: [3, 2, 3, 4]\n"
        with pytest.raises(RuntimeError):
            await gateway.start_control()
    finally:
        writer.close()
        await writer.wait_closed()
        await gateway.close()


def test_runtime_commands_change_the_simulation(tmp_path: Path) -> None:
    gateway = RtuTcpMockGateway(parse_units(DEFAULT_UNITS))
    dump = tmp_path / "map.json"

    for command, expected in (
        ("help", "pair ADDR PROFILE"),
        ("bogus", "error: unknown command 'bogus'"),
        ("pair 4 ii_f", "unit 4 paired as ii_f; the gateway lists [2, 3, 4]"),
        ("unpair 3", "the gateway lists [2, 4]"),
        ("silent 4 on", "unit 4 drops requests"),
        ("silent 4 maybe", "error: expected on or off"),
        ("set 2 41006 55", "address 2 register 41006 = 55"),
        ("set 2 x 1", "error: invalid number 'x'"),
        ("get 2 41006 2", "41006: [55, 720]"),
        ("get 2 41006 200", "error: the count must be between 1 and 125"),
        ("refuse 2 41007-41008", "address 2 refuses 41007-41008"),
        ("allow 2 41008", "address 2 allows 41008"),
        ("flag 2 frost on", "unit 2 frost on"),
        ("app 2 high", "unit 2 changed as from the app"),
        ("app 2", "error: usage: app ADDR"),
        ("reboot 5", "gateway restarted; warm-up 5 s"),
        ("warmup 0", "warm-up 0 s"),
        ("clock -60", "gateway clock offset -60 s"),
        ("settle 1500", "settle 1500 ms"),
        ("delay 2 300", "extra reply delay for address 2: 300 ms"),
        ("delay 50", "reply delay 50 ms"),
        ("delay 9 10", "error: no unit 9 is configured"),
        ("drop-rate 2", "error: the drop rate must be between 0 and 1"),
        ("corrupt-rate 0.5", "corrupt rate 0.5"),
        ("fault * timeout 2", "next 2 replies to *: timeout"),
        ("fault 9 ack", "error: no unit 9 is configured"),
        ("fault 2 nope", "error: unknown fault 'nope'"),
        ("status", "scripted faults [* timeout x2]"),
        ("fault clear", "scripted faults cleared"),
        (f"dump {dump}", "register map written"),
        (f"dump {tmp_path}", "error: cannot write"),
    ):
        output = gateway.execute(command)
        assert expected in output, (command, output)
    assert gateway.execute("") == gateway.execute("# comment") == ""

    model = gateway.model
    assert model.paired == [2, 4]
    assert model.silent == {4}
    assert model.refused(2) == {41007}
    assert model.peek(2, REGISTER_FROST_PROTECTION_ACTIVE) == [1]
    assert model.peek(2, REGISTER_MODE_STATUS, 3) == [MODE_MANUAL, 230, 230]
    assert model.clock_offset == -60
    assert model.settle_seconds == 1.5
    assert (gateway.response_delay, gateway.unit_delays) == (0.05, {2: 0.3})
    assert gateway.corrupt_rate == 0.5
    assert json.loads(dump.read_text(encoding="utf-8"))["units"]["4"]["profile"] == "ii_f"


@pytest.mark.parametrize(
    "options",
    [
        {"response_delay": -1},
        {"drop_rate": 2},
        {"corrupt_rate": -0.1},
        {"unit_delays": {2: -1}},
        {"unit_delays": {9: 1}},
        {"settle_seconds": -1},
        {"silent_units": [9]},
        {"refused": [(9, (41007, 41007))]},
    ],
)
def test_invalid_gateway_options_are_rejected(options: dict[str, Any]) -> None:
    with pytest.raises(ValueError):
        RtuTcpMockGateway({2: "ii_plain"}, **options)


def test_invalid_scripted_faults_are_rejected() -> None:
    gateway = RtuTcpMockGateway({2: "ii_plain"})

    with pytest.raises(ValueError):
        gateway.inject_fault("timeout", 2, count=0)
    with pytest.raises(ValueError):
        gateway.inject_fault("explode")


@pytest.mark.parametrize(
    "argv",
    [
        ["--drop-rate", "2"],
        ["--corrupt-rate", "-1"],
        ["--settle-ms", "-1"],
        ["--warmup-seconds", "-5"],
        ["--response-delay-ms", "-1"],
        ["--refuse", "2:x"],
        ["--refuse", "2"],
        ["--unit-delay", "2:-1"],
        ["--unit-delay", "x:1"],
        ["--unit-delay", "2:a"],
        ["--control-port", "70000"],
        ["--units", "2:nope"],
    ],
)
def test_parse_args_rejects_invalid_options(argv: list[str]) -> None:
    with pytest.raises(SystemExit):
        parse_args(argv)


async def test_cli_reads_runtime_commands_from_stdin(socket_enabled: None) -> None:
    process, port = await _start_cli_gateway("--units", "2:ii_plain")
    try:
        assert process.stdin is not None
        assert process.stdout is not None
        process.stdin.write(b"pair 4 ii_f\n")
        await process.stdin.drain()

        async def wait_for_pairing() -> None:
            assert process.stdout is not None
            while b"unit 4 paired" not in await process.stdout.readline():
                pass

        await asyncio.wait_for(wait_for_pairing(), 5)
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        try:
            await _send(writer, _read_frame(1, REGISTER_GATEWAY_NUMBER_OF_NODES, 3))
            reply = await asyncio.wait_for(reader.readexactly(11), 2)
            assert reply[3:9] == bytes((0, 2, 0, 2, 0, 4))
        finally:
            writer.close()
            await writer.wait_closed()
    finally:
        await _stop_cli_gateway(process)


def test_cli_dumps_and_applies_register_maps(tmp_path: Path) -> None:
    dump = tmp_path / "map.json"
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "tools.mock_gateway",
            "--units",
            "2:ii_f",
            "--refuse",
            "2:41007",
            "--dump-register-map",
            str(dump),
        ],
        cwd=_REPO_ROOT,
        capture_output=True,
        check=True,
        text=True,
    )
    assert "Register map written" in result.stdout
    assert json.loads(dump.read_text(encoding="utf-8"))["units"]["2"]["registers"]["41007"] is None

    gateway = build_gateway(
        parse_args(["--units", "3:ii_plain", "--register-map", str(dump), "--unit-delay", "2:250"])
    )
    assert gateway.model.units == {2: "ii_f", 3: "ii_plain"}
    assert gateway.model.refused(2) == {41007}
    assert gateway.unit_delays == {2: 0.25}

    missing = subprocess.run(
        [sys.executable, "-m", "tools.mock_gateway", "--register-map", str(tmp_path / "x.json")],
        cwd=_REPO_ROOT,
        capture_output=True,
        text=True,
    )
    assert missing.returncode != 0
    assert "Could not start mock gateway: cannot read register map" in missing.stderr
