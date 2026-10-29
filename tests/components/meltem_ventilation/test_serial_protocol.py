"""Exercise the real tmodbus RTU client against a local pseudo-terminal."""

from __future__ import annotations

import asyncio
import os
import sys
import time
from collections import deque
from collections.abc import AsyncIterator
from types import SimpleNamespace

import pytest
from homeassistant.components.modbus.connection import async_get_connection_info
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
from modbus_connection import IllegalDataAddressError, ModbusConnection, ModbusTimeoutError
from modbus_connection.tmodbus import TmodbusConnection
from pytest_homeassistant.common import MockConfigEntry

from custom_components.meltem_ventilation.const import (
    CONF_MAX_REQUESTS_PER_SECOND,
    CONF_PORT,
    CONF_ROOMS,
    DOMAIN,
    MODE_MANUAL,
    MODE_UNBALANCED,
    REGISTER_APPLY,
    REGISTER_CURRENT_LEVEL,
    REGISTER_DAYS_UNTIL_FILTER_CHANGE,
    REGISTER_EXTRACT_AIR_FLOW,
    REGISTER_EXTRACT_AIR_TARGET_LEVEL,
    REGISTER_GATEWAY_NODE_ADDRESS_1,
    REGISTER_GATEWAY_NUMBER_OF_NODES,
    REGISTER_MODE,
    REGISTER_MODE_STATUS,
    TRANSPORT_READ_RECOVERY_SECONDS,
)
from custom_components.meltem_ventilation.modbus_client import MeltemModbusClient
from custom_components.meltem_ventilation.modbus_helpers import (
    MeltemModbusError,
    build_serial_params,
)
from custom_components.meltem_ventilation.models import RefreshPlan, RoomConfig, RoomState

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="Pseudo-terminals require POSIX")


def _frame(payload: bytes) -> bytes:
    crc = 0xFFFF
    for byte in payload:
        crc ^= byte
        for _ in range(8):
            crc = (crc >> 1) ^ (0xA001 if crc & 1 else 0)
    return payload + crc.to_bytes(2, "little")


def _connection(device: str) -> ModbusConnection:
    return TmodbusConnection(build_serial_params(device))


class _RtuGateway:
    def __init__(self) -> None:
        self.master, self.slave = os.openpty()
        self.device = os.ttyname(self.slave)
        os.set_blocking(self.master, False)
        self.loop = asyncio.get_running_loop()
        self.loop.add_reader(self.master, self._read)
        self.buffer = bytearray()
        self.requests: list[tuple[int, int, int, int]] = []
        self.actions: deque[str] = deque()
        self.late_response: bytes | None = None
        self.errors: list[str] = []
        self.pending: list[asyncio.TimerHandle] = []
        self.registers: dict[int, dict[int, int]] = {
            1: {REGISTER_GATEWAY_NUMBER_OF_NODES: 1, REGISTER_GATEWAY_NODE_ADDRESS_1: 2},
            2: {
                REGISTER_MODE: 0,
                REGISTER_CURRENT_LEVEL: 0,
                REGISTER_EXTRACT_AIR_TARGET_LEVEL: 0,
                REGISTER_APPLY: 0,
            },
            3: {
                REGISTER_MODE: 0,
                REGISTER_CURRENT_LEVEL: 0,
                REGISTER_EXTRACT_AIR_TARGET_LEVEL: 0,
                REGISTER_APPLY: 0,
            },
        }

    def _read(self) -> None:
        try:
            data = os.read(self.master, 4096)
        except BlockingIOError:
            return
        self.buffer.extend(data)
        while len(self.buffer) >= 8:
            request = bytes(self.buffer[:8])
            del self.buffer[:8]
            if _frame(request[:-2]) != request:
                self.errors.append(f"Invalid request CRC: {request.hex()}")
                continue
            unit, function = request[:2]
            address = int.from_bytes(request[2:4], "big")
            value = int.from_bytes(request[4:6], "big")
            self.requests.append((unit, function, address, value))
            if function == 4:
                action = (
                    self.actions.popleft()
                    if self.actions and self.actions[0].startswith("sync_")
                    else "sync_normal"
                )
                if action == "sync_timeout":
                    continue
                if action in ("sync_late", "sync_late_twice"):
                    if self.late_response is None:
                        self.errors.append("No delayed response available")
                    else:
                        os.write(self.master, self.late_response)
                        if action == "sync_late_twice":
                            os.write(self.master, self.late_response)
                        self.late_response = None
                os.write(self.master, _frame(bytes((unit, function | 0x80, 1))))
                continue
            action = self.actions.popleft() if self.actions else "normal"
            if action == "timeout":
                continue
            if action == "send_late":
                if self.late_response is None:
                    self.errors.append("No delayed response available")
                else:
                    os.write(self.master, self.late_response)
                    self.late_response = None
                continue
            registers = self.registers.get(unit, {})
            if action == "exception":
                response = _frame(bytes((unit, function | 0x80, 2)))
            elif function == 3:
                addresses = range(address, address + value)
                if not all(register in registers for register in addresses):
                    response = _frame(bytes((unit, function | 0x80, 2)))
                else:
                    values = b"".join(
                        registers[register].to_bytes(2, "big") for register in addresses
                    )
                    response = _frame(bytes((unit, function, len(values))) + values)
            elif function == 6:
                if address not in registers:
                    os.write(self.master, _frame(bytes((unit, function | 0x80, 2))))
                    continue
                registers[address] = value
                if action == "lost_write_response":
                    continue
                response = request
            else:
                self.errors.append(f"Unexpected function: {function}")
                continue
            if action == "bad_crc":
                response = response[:-1] + bytes((response[-1] ^ 0xFF,))
            elif action == "short":
                response = response[:-2]
            elif action == "wrong_unit":
                response = _frame(bytes((unit + 1,)) + response[1:-2])
            elif action == "wrong_function":
                response = _frame(response[:1] + bytes((4,)) + response[2:-2])
            elif action == "wrong_length":
                response = _frame(response[:2] + bytes((4,)) + response[3:-2] + b"\x00\x01")
            elif action == "wrong_write_address":
                response = _frame(response[:2] + (address + 1).to_bytes(2, "big") + response[4:-2])
            elif action == "wrong_write_value":
                response = _frame(response[:4] + (value + 1).to_bytes(2, "big"))
            elif action == "fragmented":
                os.write(self.master, response[:3])
                self.pending.append(self.loop.call_later(0.02, os.write, self.master, response[3:]))
                continue
            elif action == "delayed":
                self.pending.append(self.loop.call_later(0.05, os.write, self.master, response))
                continue
            elif action == "late":
                self.late_response = response
                continue
            elif action not in ("normal", "exception", "lost_write_response"):
                self.errors.append(f"Unexpected action: {action}")
                continue
            os.write(self.master, response)

    def close(self) -> None:
        self.loop.remove_reader(self.master)
        for handle in self.pending:
            handle.cancel()
        os.close(self.master)
        os.close(self.slave)


@pytest.fixture
async def serial_gateway() -> AsyncIterator[_RtuGateway]:
    gateway = _RtuGateway()
    try:
        yield gateway
    finally:
        gateway.close()
        assert gateway.errors == []
        assert gateway.buffer == b""


async def test_real_serial_client_reads_writes_and_reconnects(serial_gateway: _RtuGateway) -> None:
    connection = _connection(serial_gateway.device)
    client = MeltemModbusClient(connection.for_unit, port=serial_gateway.device)
    unit = connection.for_unit(2)
    room = RoomConfig(key="unit_1", name="Unit 1", profile="ii_plain", slave=2)
    serial_gateway.actions.append("delayed")

    try:
        await client.async_validate_gateway()
        await client.write_level(room, 50)
        assert await unit.read_holding_registers(REGISTER_CURRENT_LEVEL, 1) == [100]
        await connection.disconnect()
        await client.async_validate_gateway()
    finally:
        client.shutdown()
        await connection.close()

    assert serial_gateway.requests == [
        (1, 3, REGISTER_GATEWAY_NUMBER_OF_NODES, 1),
        (2, 6, REGISTER_MODE, MODE_MANUAL),
        (2, 6, REGISTER_CURRENT_LEVEL, 100),
        (2, 6, REGISTER_APPLY, 0),
        (2, 3, REGISTER_CURRENT_LEVEL, 1),
        (1, 3, REGISTER_GATEWAY_NUMBER_OF_NODES, 1),
    ]


@pytest.mark.parametrize("first_response", ["timeout", "bad_crc", "short"])
async def test_real_serial_client_quarantines_a_missing_or_malformed_response(
    serial_gateway: _RtuGateway, first_response: str
) -> None:
    connection = _connection(serial_gateway.device)
    client = MeltemModbusClient(connection.for_unit, port=serial_gateway.device)
    serial_gateway.actions.extend((first_response, "normal"))
    try:
        with pytest.raises(MeltemModbusError):
            await client.async_validate_gateway()
    finally:
        client.shutdown()
        await connection.close()
    assert serial_gateway.requests == [(1, 3, REGISTER_GATEWAY_NUMBER_OF_NODES, 1)]


async def test_real_serial_client_does_not_retry_after_timeout(
    serial_gateway: _RtuGateway,
) -> None:
    connection = _connection(serial_gateway.device)
    client = MeltemModbusClient(connection.for_unit, port=serial_gateway.device)
    serial_gateway.actions.extend(("timeout", "timeout"))
    try:
        with pytest.raises(MeltemModbusError) as err:
            await client.async_validate_gateway()
        assert isinstance(err.value.__cause__, ModbusTimeoutError)
        with pytest.raises(MeltemModbusError, match="quarantined"):
            await client.async_validate_gateway()
    finally:
        client.shutdown()
        await connection.close()
    assert serial_gateway.requests == [(1, 3, REGISTER_GATEWAY_NUMBER_OF_NODES, 1)]


async def test_real_serial_client_does_not_retry_a_delayed_read(
    serial_gateway: _RtuGateway,
) -> None:
    connection = _connection(serial_gateway.device)
    outcomes: list[str] = []
    client = MeltemModbusClient(
        connection.for_unit, port=serial_gateway.device, on_request=outcomes.append
    )
    serial_gateway.actions.append("late")
    try:
        with pytest.raises(MeltemModbusError):
            await client.async_validate_gateway()
        assert serial_gateway.late_response is not None
        os.write(serial_gateway.master, serial_gateway.late_response)
        serial_gateway.late_response = None
        await asyncio.sleep(0)
        with pytest.raises(MeltemModbusError, match="quarantined"):
            await client.async_validate_gateway()
    finally:
        client.shutdown()
        await connection.close()

    assert outcomes == ["no_answer"]
    assert serial_gateway.requests == [(1, 3, REGISTER_GATEWAY_NUMBER_OF_NODES, 1)]
    assert serial_gateway.late_response is None


async def test_real_serial_client_retries_a_write_applied_before_its_reply_was_lost(
    serial_gateway: _RtuGateway,
) -> None:
    connection = _connection(serial_gateway.device)
    client = MeltemModbusClient(connection.for_unit, port=serial_gateway.device)
    room = RoomConfig(key="unit_1", name="Unit 1", profile="ii_plain", slave=2)
    serial_gateway.actions.extend(("normal", "normal", "lost_write_response", "normal"))
    try:
        await client.write_level(room, 50)
    finally:
        client.shutdown()
        await connection.close()

    assert serial_gateway.requests == [
        (2, 6, REGISTER_MODE, MODE_MANUAL),
        (2, 6, REGISTER_CURRENT_LEVEL, 100),
        (2, 6, REGISTER_APPLY, 0),
        (2, 6, REGISTER_APPLY, 0),
    ]
    assert serial_gateway.registers[2][REGISTER_CURRENT_LEVEL] == 100
    assert serial_gateway.registers[2][REGISTER_APPLY] == 0


@pytest.mark.parametrize("response", ["wrong_write_address", "wrong_write_value"])
@pytest.mark.parametrize("bad_replies", [1, 2])
async def test_real_serial_client_rejects_write_echo_mismatches(
    serial_gateway: _RtuGateway, response: str, bad_replies: int
) -> None:
    connection = _connection(serial_gateway.device)
    client = MeltemModbusClient(connection.for_unit, port=serial_gateway.device)
    room = RoomConfig(key="unit_1", name="Unit 1", profile="ii_plain", slave=2)
    serial_gateway.actions.extend(("normal", response, response if bad_replies == 2 else "normal"))
    try:
        if bad_replies == 1:
            await client.write_level(room, 50)
        else:
            with pytest.raises(MeltemModbusError):
                await client.write_level(room, 50)
        assert serial_gateway.requests == [
            (2, 6, REGISTER_MODE, MODE_MANUAL),
            (2, 6, REGISTER_CURRENT_LEVEL, 100),
            (2, 6, REGISTER_CURRENT_LEVEL, 100),
            *([(2, 6, REGISTER_APPLY, 0)] if bad_replies == 1 else []),
        ]
        await client.write_level(room, 40)
        assert serial_gateway.requests[-3:] == [
            (2, 6, REGISTER_MODE, MODE_MANUAL),
            (2, 6, REGISTER_CURRENT_LEVEL, 80),
            (2, 6, REGISTER_APPLY, 0),
        ]
    finally:
        client.shutdown()
        await connection.close()


async def test_real_serial_client_surfaces_modbus_exception(serial_gateway: _RtuGateway) -> None:
    connection = _connection(serial_gateway.device)
    client = MeltemModbusClient(connection.for_unit, port=serial_gateway.device)
    serial_gateway.actions.append("exception")
    try:
        with pytest.raises(MeltemModbusError) as err:
            await client.async_validate_gateway()
        assert isinstance(err.value.__cause__, IllegalDataAddressError)
    finally:
        client.shutdown()
        await connection.close()
    assert serial_gateway.requests == [(1, 3, REGISTER_GATEWAY_NUMBER_OF_NODES, 1)]


async def test_real_serial_client_reads_isolated_units_and_rejects_unknown_registers(
    serial_gateway: _RtuGateway,
) -> None:
    serial_gateway.registers[2][REGISTER_CURRENT_LEVEL] = 60
    serial_gateway.registers[3][REGISTER_CURRENT_LEVEL] = 90
    connection = _connection(serial_gateway.device)
    client = MeltemModbusClient(connection.for_unit, port=serial_gateway.device)
    first = RoomConfig(key="unit_1", name="Unit 1", profile="ii_plain", slave=2)
    second = RoomConfig(key="unit_2", name="Unit 2", profile="ii_plain", slave=3)
    try:
        assert await client.read_balanced_target_readback(first) == 30
        assert await client.read_balanced_target_readback(second) == 45
        await client.write_level(first, 50)
        assert await client.read_balanced_target_readback(first) == 50
        assert await client.read_balanced_target_readback(second) == 45
        with pytest.raises(IllegalDataAddressError):
            await connection.for_unit(2).read_holding_registers(49999, 1)
    finally:
        client.shutdown()
        await connection.close()

    assert serial_gateway.requests == [
        (2, 3, REGISTER_CURRENT_LEVEL, 1),
        (3, 3, REGISTER_CURRENT_LEVEL, 1),
        (2, 6, REGISTER_MODE, MODE_MANUAL),
        (2, 6, REGISTER_CURRENT_LEVEL, 100),
        (2, 6, REGISTER_APPLY, 0),
        (2, 3, REGISTER_CURRENT_LEVEL, 1),
        (3, 3, REGISTER_CURRENT_LEVEL, 1),
        (2, 3, 49999, 1),
    ]


@pytest.mark.parametrize("response", ["wrong_unit", "wrong_function", "wrong_length"])
async def test_real_serial_client_quarantines_a_reply_with_the_wrong_header_or_length(
    serial_gateway: _RtuGateway, response: str
) -> None:
    connection = _connection(serial_gateway.device)
    client = MeltemModbusClient(connection.for_unit, port=serial_gateway.device)
    serial_gateway.actions.extend((response, "normal"))
    try:
        with pytest.raises(MeltemModbusError):
            await client.async_validate_gateway()
    finally:
        client.shutdown()
        await connection.close()

    assert serial_gateway.requests == [(1, 3, REGISTER_GATEWAY_NUMBER_OF_NODES, 1)]


async def test_real_serial_client_accepts_a_fragmented_response(
    serial_gateway: _RtuGateway,
) -> None:
    connection = _connection(serial_gateway.device)
    client = MeltemModbusClient(connection.for_unit, port=serial_gateway.device)
    serial_gateway.actions.append("fragmented")
    try:
        assert await client.async_read_gateway_node_count() == 1
    finally:
        client.shutdown()
        await connection.close()

    assert serial_gateway.requests == [(1, 3, REGISTER_GATEWAY_NUMBER_OF_NODES, 1)]


async def test_late_reply_from_another_unit_cannot_be_used_as_the_next_units_value(
    serial_gateway: _RtuGateway,
) -> None:
    serial_gateway.registers[2][REGISTER_CURRENT_LEVEL] = 60
    serial_gateway.registers[3][REGISTER_CURRENT_LEVEL] = 90
    connection = _connection(serial_gateway.device)
    client = MeltemModbusClient(connection.for_unit, port=serial_gateway.device)
    first = RoomConfig(key="unit_1", name="Unit 1", profile="ii_plain", slave=2)
    second = RoomConfig(key="unit_2", name="Unit 2", profile="ii_plain", slave=3)
    serial_gateway.actions.extend(("late", "normal"))
    try:
        with pytest.raises(MeltemModbusError):
            await client.read_balanced_target_readback(first)
        assert serial_gateway.late_response is not None
        os.write(serial_gateway.master, serial_gateway.late_response)
        serial_gateway.late_response = None
        await asyncio.sleep(0)
        assert await client.read_balanced_target_readback(second) == 45
    finally:
        client.shutdown()
        await connection.close()

    assert serial_gateway.late_response is None
    assert serial_gateway.requests == [
        (2, 3, REGISTER_CURRENT_LEVEL, 1),
        (3, 3, REGISTER_CURRENT_LEVEL, 1),
    ]


async def test_late_reply_cannot_be_used_for_a_quarantined_unit(
    serial_gateway: _RtuGateway,
) -> None:
    serial_gateway.registers[2][REGISTER_CURRENT_LEVEL] = 60
    serial_gateway.registers[2][REGISTER_DAYS_UNTIL_FILTER_CHANGE] = 90
    serial_gateway.registers[3][REGISTER_CURRENT_LEVEL] = 100
    connection = _connection(serial_gateway.device)
    client = MeltemModbusClient(connection.for_unit, port=serial_gateway.device)
    room = RoomConfig(key="unit_1", name="Unit 1", profile="ii_plain", slave=2)
    serial_gateway.actions.append("late")
    try:
        with pytest.raises(MeltemModbusError):
            await client.read_balanced_target_readback(room)
        assert serial_gateway.late_response is not None
        os.write(serial_gateway.master, serial_gateway.late_response)
        serial_gateway.late_response = None
        await asyncio.sleep(0)
        reads_before = list(serial_gateway.requests)
        state = await client.read_room_state(
            room,
            RoomState(days_until_filter_change=25),
            RefreshPlan.only(refresh_filter_days=True),
        )
        other = RoomConfig(key="unit_2", name="Unit 2", profile="ii_plain", slave=3)
        assert await client.read_balanced_target_readback(other) == 50
        assert serial_gateway.requests == [*reads_before, (3, 3, REGISTER_CURRENT_LEVEL, 1)]
    finally:
        client.shutdown()
        await connection.close()

    assert state.days_until_filter_change == 25
    assert state.last_read_job is not None
    assert not state.last_read_job.attempted_groups
    assert state.read_health_for("filter").last_successful_read is None


async def test_two_late_replies_cannot_be_accepted_as_a_filter_value(
    serial_gateway: _RtuGateway,
) -> None:
    serial_gateway.registers[2][REGISTER_CURRENT_LEVEL] = 60
    serial_gateway.registers[2][REGISTER_DAYS_UNTIL_FILTER_CHANGE] = 90
    connection = _connection(serial_gateway.device)
    client = MeltemModbusClient(connection.for_unit, port=serial_gateway.device)
    room = RoomConfig(key="unit_1", name="Unit 1", profile="ii_plain", slave=2)
    serial_gateway.actions.append("late")
    try:
        with pytest.raises(MeltemModbusError):
            await client.read_balanced_target_readback(room)
        assert serial_gateway.late_response is not None
        os.write(serial_gateway.master, serial_gateway.late_response * 2)
        serial_gateway.late_response = None
        await asyncio.sleep(0)
        state = await client.read_room_state(
            room,
            RoomState(days_until_filter_change=25),
            RefreshPlan.only(refresh_filter_days=True),
        )
        assert state.days_until_filter_change == 25
        assert serial_gateway.requests == [(2, 3, REGISTER_CURRENT_LEVEL, 1)]
    finally:
        client.shutdown()
        await connection.close()


@pytest.mark.parametrize("reply_count", [1, 2])
async def test_late_replies_during_recovery_pause_are_not_used_as_new_values(
    serial_gateway: _RtuGateway, monkeypatch: pytest.MonkeyPatch, reply_count: int
) -> None:
    clock = SimpleNamespace(now=time.monotonic())
    monkeypatch.setattr(
        "custom_components.meltem_ventilation.device.transport.time",
        SimpleNamespace(monotonic=lambda: clock.now),
    )
    serial_gateway.registers[2][REGISTER_CURRENT_LEVEL] = 60
    serial_gateway.registers[2][REGISTER_DAYS_UNTIL_FILTER_CHANGE] = 90
    connection = _connection(serial_gateway.device)
    client = MeltemModbusClient(connection.for_unit, port=serial_gateway.device)
    room = RoomConfig(key="unit_1", name="Unit 1", profile="ii_plain", slave=2)
    serial_gateway.actions.append("late")
    try:
        with pytest.raises(MeltemModbusError):
            await client.read_balanced_target_readback(room)
        assert serial_gateway.late_response is not None
        os.write(serial_gateway.master, serial_gateway.late_response * reply_count)
        serial_gateway.late_response = None
        await asyncio.sleep(0)
        clock.now += TRANSPORT_READ_RECOVERY_SECONDS
        state = await client.read_room_state(
            room,
            RoomState(days_until_filter_change=25),
            RefreshPlan.only(refresh_filter_days=True),
        )
        assert state.days_until_filter_change == 90
        assert state.read_health_for("filter").last_error is None
        assert not client.read_quarantined(2)
        assert serial_gateway.requests == [
            (2, 3, REGISTER_CURRENT_LEVEL, 1),
            (2, 3, REGISTER_DAYS_UNTIL_FILTER_CHANGE, 1),
        ]
    finally:
        client.shutdown()
        await connection.close()


@pytest.fixture
async def serial_hass_entry(
    hass: HomeAssistant, serial_gateway: _RtuGateway
) -> AsyncIterator[MockConfigEntry]:
    registers = serial_gateway.registers[2]
    registers.update(
        {
            REGISTER_EXTRACT_AIR_FLOW: 30,
            REGISTER_EXTRACT_AIR_FLOW + 1: 30,
            REGISTER_MODE_STATUS: MODE_MANUAL,
            REGISTER_MODE_STATUS + 1: 60,
            REGISTER_MODE_STATUS + 2: 0,
        }
    )
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Meltem",
        data={
            CONF_PORT: serial_gateway.device,
            CONF_ROOMS: [{"key": "unit_1", "name": "Unit 1", "profile": "ii_plain", "slave": 2}],
        },
        options={CONF_MAX_REQUESTS_PER_SECOND: 1000.0},
        source="user",
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    try:
        await hass.async_block_till_done(wait_background_tasks=True)
        yield entry
    finally:
        assert await hass.config_entries.async_unload(entry.entry_id)
        assert not async_get_connection_info(hass)


async def test_home_assistant_fan_service_reaches_the_real_serial_client(
    hass: HomeAssistant, serial_gateway: _RtuGateway, serial_hass_entry: MockConfigEntry
) -> None:
    supply_id = er.async_get(hass).async_get_entity_id(
        Platform.FAN, DOMAIN, f"{DOMAIN}_unit_1_supply_level"
    )
    assert supply_id is not None
    initial = hass.states.get(supply_id)
    assert initial is not None
    assert initial.attributes["percentage"] == 30

    before_write = len(serial_gateway.requests)
    await hass.services.async_call(
        "fan",
        "set_percentage",
        {"entity_id": supply_id, "percentage": 60},
        blocking=True,
    )
    state = hass.states.get(supply_id)
    assert state is not None
    assert state.attributes["percentage"] == 60
    assert state.attributes["level_source"] == "pending"
    requests = serial_gateway.requests[before_write:]
    writes = [request for request in requests if request[1] == 6]
    assert writes == [
        (2, 6, REGISTER_MODE, MODE_UNBALANCED),
        (2, 6, REGISTER_CURRENT_LEVEL, 120),
        (2, 6, REGISTER_EXTRACT_AIR_TARGET_LEVEL, 60),
        (2, 6, REGISTER_APPLY, 0),
    ]
    first_write = requests.index(writes[0])
    assert requests[first_write : first_write + len(writes)] == writes

    registers = serial_gateway.registers[2]
    registers.update(
        {
            REGISTER_EXTRACT_AIR_FLOW: 30,
            REGISTER_EXTRACT_AIR_FLOW + 1: 60,
            REGISTER_MODE_STATUS: MODE_UNBALANCED,
            REGISTER_MODE_STATUS + 1: 120,
            REGISTER_MODE_STATUS + 2: 60,
        }
    )
    coordinator = serial_hass_entry.runtime_data.coordinator
    coordinator._startup_jobs = []
    for job in coordinator._jobs:
        job.next_due = 0.0 if job.key == "flow" else float("inf")
    coordinator._last_read_started = None
    await coordinator.async_refresh()
    await hass.async_block_till_done()
    state = hass.states.get(supply_id)
    assert state is not None
    assert state.attributes["percentage"] == 60
    assert state.attributes["level_source"] == "target"


async def test_home_assistant_fan_service_reports_and_recovers_from_rtu_write_rejection(
    hass: HomeAssistant, serial_gateway: _RtuGateway, serial_hass_entry: MockConfigEntry
) -> None:
    supply_id = er.async_get(hass).async_get_entity_id(
        Platform.FAN, DOMAIN, f"{DOMAIN}_unit_1_supply_level"
    )
    assert supply_id is not None
    serial_gateway.actions.extend(("normal", "exception"))
    before_write = len(serial_gateway.requests)
    with pytest.raises(HomeAssistantError):
        await hass.services.async_call(
            "fan",
            "set_percentage",
            {"entity_id": supply_id, "percentage": 60},
            blocking=True,
        )
    writes = [request for request in serial_gateway.requests[before_write:] if request[1] == 6]
    assert writes == [
        (2, 6, REGISTER_MODE, MODE_UNBALANCED),
        (2, 6, REGISTER_CURRENT_LEVEL, 120),
    ]
    state = hass.states.get(supply_id)
    assert state is not None
    assert state.attributes["percentage"] == 30

    before_retry = len(serial_gateway.requests)
    await hass.services.async_call(
        "fan",
        "set_percentage",
        {"entity_id": supply_id, "percentage": 60},
        blocking=True,
    )
    retry_writes = [
        request for request in serial_gateway.requests[before_retry:] if request[1] == 6
    ]
    assert retry_writes == [
        (2, 6, REGISTER_MODE, MODE_UNBALANCED),
        (2, 6, REGISTER_CURRENT_LEVEL, 120),
        (2, 6, REGISTER_EXTRACT_AIR_TARGET_LEVEL, 60),
        (2, 6, REGISTER_APPLY, 0),
    ]
    requests = serial_gateway.requests[before_retry:]
    first_write = requests.index(retry_writes[0])
    assert requests[first_write : first_write + len(retry_writes)] == retry_writes
    state = hass.states.get(supply_id)
    assert state is not None
    assert state.attributes["percentage"] == 60
