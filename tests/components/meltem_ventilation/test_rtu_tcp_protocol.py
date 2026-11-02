"""Exercise the Home Assistant RTU transport against a local TCP bridge."""

from __future__ import annotations

import asyncio
import time
from unittest.mock import patch

import pytest
from homeassistant.components.modbus import async_get_temporary_unit
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant.common import MockConfigEntry

from custom_components.meltem_ventilation.const import (
    CONF_CONNECTION_TYPE,
    CONF_HOST,
    CONF_NETWORK_PORT,
    CONNECTION_RTU_OVER_TCP,
    DOMAIN,
)
from custom_components.meltem_ventilation.coordinator import MeltemDataUpdateCoordinator
from custom_components.meltem_ventilation.flow_helpers import temporary_units
from custom_components.meltem_ventilation.gateway_connection import RtuTcpGatewayConnection
from custom_components.meltem_ventilation.modbus_client import MeltemModbusClient
from custom_components.meltem_ventilation.modbus_helpers import (
    new_transport_policy,
    prepare_unit,
    read_gateway_node_count,
)
from custom_components.meltem_ventilation.models import MeltemRuntimeData


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


@pytest.mark.parametrize("setup", [True, False])
async def test_network_allows_slow_tcp_connection(
    hass: HomeAssistant, socket_enabled: None, setup: bool
) -> None:
    async def bridge(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            await reader.readexactly(8)
            writer.write(_frame(bytes((1, 3, 2, 0, 1))))
            await writer.drain()
            await reader.read()
        finally:
            writer.close()
            await writer.wait_closed()

    server = await asyncio.start_server(bridge, "127.0.0.1", 0)
    assert server.sockets
    port = server.sockets[0].getsockname()[1]
    loop = asyncio.get_running_loop()
    create_connection = loop.create_connection
    getaddrinfo = loop.getaddrinfo
    resolved_hosts: list[str] = []

    async def slow_connect(*args, **kwargs):
        await asyncio.sleep(0.9)
        return await create_connection(*args, **kwargs)

    async def resolve_absolute(host, *args, **kwargs):
        resolved_hosts.append(host)
        return await getaddrinfo("127.0.0.1" if host == "localhost." else host, *args, **kwargs)

    connection = RtuTcpGatewayConnection("localhost.", port)
    try:
        with (
            patch.object(loop, "create_connection", side_effect=slow_connect),
            patch.object(loop, "getaddrinfo", side_effect=resolve_absolute),
        ):
            if setup:
                async with temporary_units(hass, connection) as units:
                    assert await read_gateway_node_count(await units(1)) == 1
            else:
                async with async_get_temporary_unit(hass, connection.modbus_params(), 1) as raw:
                    client = MeltemModbusClient(
                        lambda _slave: raw, port=connection.unique_id, timeout=connection.timeout
                    )
                    try:
                        await client.async_validate_gateway()
                    finally:
                        client.shutdown()
        assert resolved_hosts == ["localhost."]
    finally:
        server.close()
        await server.wait_closed()


@pytest.mark.parametrize("valid_target", [True, False])
async def test_reconfigure_single_client_bridge_and_restore_on_failure(
    hass: HomeAssistant, socket_enabled: None, valid_target: bool
) -> None:
    active = False

    async def bridge(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        nonlocal active
        if active:
            writer.close()
            await writer.wait_closed()
            return
        active = True
        try:
            while await reader.readexactly(8):
                writer.write(_frame(bytes((1, 3, 2, 0, 1))))
                await writer.drain()
        except asyncio.IncompleteReadError:
            pass
        finally:
            active = False
            writer.close()
            await writer.wait_closed()

    async def reject(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        writer.close()
        await writer.wait_closed()

    server = await asyncio.start_server(bridge, "127.0.0.1", 0)
    rejected_server = await asyncio.start_server(reject, "127.0.0.1", 0)
    assert server.sockets and rejected_server.sockets
    port = server.sockets[0].getsockname()[1]
    bad_port = rejected_server.sockets[0].getsockname()[1]
    current = RtuTcpGatewayConnection("127.0.0.1", port)
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Meltem",
        data=current.as_data(),
        unique_id=current.unique_id,
        version=2,
        source="user",
    )
    entry.add_to_hass(hass)
    entry.mock_state(hass, ConfigEntryState.LOADED)
    loop = asyncio.get_running_loop()
    getaddrinfo = loop.getaddrinfo

    async def resolve_localhost(host, *args, **kwargs):
        return await getaddrinfo("127.0.0.1" if host == "localhost" else host, *args, **kwargs)

    try:
        async with (
            async_get_temporary_unit(hass, current.modbus_params(), 1) as raw,
            async_get_temporary_unit(hass, current.modbus_params(), 1) as other_consumer,
        ):
            client = MeltemModbusClient(
                lambda _slave: raw, port=current.unique_id, timeout=current.timeout
            )
            coordinator = MeltemDataUpdateCoordinator(
                hass, config_entry=entry, client=client, rooms=[], max_requests_per_second=4
            )
            entry.runtime_data = MeltemRuntimeData(coordinator=coordinator)
            try:
                await client.async_validate_gateway()
                assert other_consumer.connected
                with (
                    patch.object(hass.config_entries, "async_schedule_reload"),
                    patch.object(loop, "getaddrinfo", side_effect=resolve_localhost),
                ):
                    result = await entry.start_reconfigure_flow(hass)
                    result = await hass.config_entries.flow.async_configure(
                        result["flow_id"],
                        {
                            CONF_CONNECTION_TYPE: CONNECTION_RTU_OVER_TCP,
                            CONF_HOST: "localhost",
                            CONF_NETWORK_PORT: port if valid_target else bad_port,
                        },
                    )
                if valid_target:
                    assert result.get("reason") == "reconfigure_successful"
                    assert entry.data[CONF_HOST] == "localhost"
                else:
                    assert result.get("type") == FlowResultType.FORM
                    assert result.get("errors") == {"base": "cannot_connect"}
                    assert entry.data == current.as_data()
                    assert not other_consumer.connected
                    assert await read_gateway_node_count(other_consumer) == 1
                    await client.async_validate_gateway()
            finally:
                await coordinator.async_shutdown()
                client.shutdown()
    finally:
        entry.mock_state(hass, ConfigEntryState.NOT_LOADED)
        server.close()
        rejected_server.close()
        await server.wait_closed()
        await rejected_server.wait_closed()
