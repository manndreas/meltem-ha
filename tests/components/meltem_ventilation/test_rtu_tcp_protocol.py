"""Exercise the Home Assistant RTU transport against a local TCP bridge."""

from __future__ import annotations

import asyncio
import time

from homeassistant.components.modbus import async_get_temporary_unit
from homeassistant.core import HomeAssistant

from custom_components.meltem_ventilation.gateway_connection import RtuTcpGatewayConnection
from custom_components.meltem_ventilation.modbus_helpers import new_transport_policy, prepare_unit


def _frame(payload: bytes) -> bytes:
    crc = 0xFFFF
    for byte in payload:
        crc ^= byte
        for _ in range(8):
            crc = (crc >> 1) ^ (0xA001 if crc & 1 else 0)
    return payload + crc.to_bytes(2, "little")


async def test_real_rtu_tcp_client_reuses_connection_and_obeys_pacing(
    hass: HomeAssistant, socket_enabled: None
) -> None:
    requests: list[bytes] = []
    received_at: list[float] = []
    errors: list[str] = []
    connections = 0
    closed = asyncio.Event()

    async def bridge(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        nonlocal connections
        connections += 1
        try:
            while data := await reader.read(256):
                received_at.append(time.monotonic())
                frame = bytearray(data)
                while True:
                    try:
                        chunk = await asyncio.wait_for(reader.read(256), 0.04)
                    except TimeoutError:
                        break
                    if not chunk:
                        return
                    frame.extend(chunk)
                request = bytes(frame)
                requests.append(request)
                if len(request) != 8 or _frame(request[:-2]) != request or request[1] != 3:
                    errors.append(request.hex())
                    return
                address = int.from_bytes(request[2:4], "big")
                count = int.from_bytes(request[4:6], "big")
                values = b"".join((address + offset).to_bytes(2, "big") for offset in range(count))
                writer.write(_frame(bytes((request[0], 3, len(values))) + values))
                await writer.drain()
        finally:
            writer.close()
            await writer.wait_closed()
            closed.set()

    server = await asyncio.start_server(bridge, "127.0.0.1", 0)
    assert server.sockets
    port = server.sockets[0].getsockname()[1]
    connection = RtuTcpGatewayConnection("127.0.0.1", port)
    policy = new_transport_policy()
    policy.update_request_rate(4)
    try:
        async with async_get_temporary_unit(hass, connection.modbus_params(), 1) as raw:
            unit = prepare_unit(raw, 1, policy)
            assert await unit.read_holding_registers(100, 1) == [100]
            assert await unit.read_holding_registers(200, 1) == [200]
            assert await unit.read_holding_registers(300, 1) == [300]
        await asyncio.wait_for(closed.wait(), 2)
    finally:
        policy.shutdown()
        server.close()
        await server.wait_closed()

    assert connections == 1
    assert not errors
    assert [int.from_bytes(request[2:4], "big") for request in requests] == [100, 200, 300]
    assert all(later - earlier >= 0.24 for earlier, later in zip(received_at, received_at[1:]))
