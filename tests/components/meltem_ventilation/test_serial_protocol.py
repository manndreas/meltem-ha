"""Exercise the real tmodbus RTU client against a local pseudo-terminal."""

from __future__ import annotations

import asyncio
import os
import sys
from collections import deque
from collections.abc import AsyncIterator

import pytest
from modbus_connection import IllegalDataAddressError, ModbusConnection, ModbusTimeoutError
from modbus_connection.tmodbus import TmodbusConnection

from custom_components.meltem_ventilation.const import (
    MODE_MANUAL,
    REGISTER_APPLY,
    REGISTER_CURRENT_LEVEL,
    REGISTER_GATEWAY_NUMBER_OF_NODES,
    REGISTER_MODE,
)
from custom_components.meltem_ventilation.modbus_client import MeltemModbusClient
from custom_components.meltem_ventilation.modbus_helpers import (
    MeltemModbusError,
    build_serial_params,
)
from custom_components.meltem_ventilation.models import RoomConfig

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
        self.registers = {REGISTER_GATEWAY_NUMBER_OF_NODES: 1}

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
            if action == "exception":
                response = _frame(bytes((unit, function | 0x80, 2)))
            elif function == 3:
                values = b"".join(
                    self.registers.get(address + offset, 0).to_bytes(2, "big")
                    for offset in range(value)
                )
                response = _frame(bytes((unit, function, len(values))) + values)
            elif function == 6:
                self.registers[address] = value
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
            elif action == "delayed":
                self.pending.append(self.loop.call_later(0.05, os.write, self.master, response))
                continue
            elif action == "late":
                self.late_response = response
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
async def test_real_serial_client_retries_a_missing_or_malformed_response(
    serial_gateway: _RtuGateway, first_response: str
) -> None:
    connection = _connection(serial_gateway.device)
    client = MeltemModbusClient(connection.for_unit, port=serial_gateway.device)
    serial_gateway.actions.extend((first_response, "normal"))
    try:
        await client.async_validate_gateway()
    finally:
        client.shutdown()
        await connection.close()
    assert serial_gateway.requests == [(1, 3, REGISTER_GATEWAY_NUMBER_OF_NODES, 1)] * 2


async def test_real_serial_client_reports_repeated_timeout_and_recovers(
    serial_gateway: _RtuGateway,
) -> None:
    connection = _connection(serial_gateway.device)
    client = MeltemModbusClient(connection.for_unit, port=serial_gateway.device)
    serial_gateway.actions.extend(("timeout", "timeout"))
    try:
        with pytest.raises(MeltemModbusError) as err:
            await client.async_validate_gateway()
        assert isinstance(err.value.__cause__, ModbusTimeoutError)
        await client.async_validate_gateway()
    finally:
        client.shutdown()
        await connection.close()
    assert serial_gateway.requests == [(1, 3, REGISTER_GATEWAY_NUMBER_OF_NODES, 1)] * 3


async def test_real_serial_client_accepts_late_reply_to_the_same_retried_read(
    serial_gateway: _RtuGateway,
) -> None:
    connection = _connection(serial_gateway.device)
    outcomes: list[str] = []
    client = MeltemModbusClient(
        connection.for_unit, port=serial_gateway.device, on_request=outcomes.append
    )
    serial_gateway.actions.extend(("late", "send_late"))
    try:
        await client.async_validate_gateway()
    finally:
        client.shutdown()
        await connection.close()

    assert outcomes == ["no_answer", "success"]
    assert serial_gateway.requests == [(1, 3, REGISTER_GATEWAY_NUMBER_OF_NODES, 1)] * 2
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
    assert serial_gateway.registers[REGISTER_CURRENT_LEVEL] == 100
    assert serial_gateway.registers[REGISTER_APPLY] == 0


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
