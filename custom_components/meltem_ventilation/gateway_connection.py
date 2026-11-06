"""Configuration and Modbus parameters for a gateway connection."""

from __future__ import annotations

import ipaddress
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, ClassVar

from modbus_connection import ModbusSerialParams, ModbusTcpParams

from .const import (
    CONF_CONNECTION_TYPE,
    CONF_HOST,
    CONF_NETWORK_PORT,
    CONF_PORT,
    CONNECTION_MODBUS_TCP,
    CONNECTION_RTU_OVER_TCP,
    CONNECTION_SERIAL,
    DEFAULT_MODBUS_TCP_PORT,
    DEFAULT_NETWORK_PORT,
    FIXED_BAUDRATE,
    FIXED_BYTESIZE,
    FIXED_PARITY,
    FIXED_STOPBITS,
    FIXED_TIMEOUT,
    NETWORK_TIMEOUT,
)

_HOST_LABEL = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\Z")
_IPV6_SCOPE = re.compile(r"[A-Za-z0-9_.-]+\Z")
_SOCKET_BAUDRATE = 115200


def _normalize_host(host: str) -> str:
    """Normalize an IP literal or DNS hostname without resolving it."""

    host = host.strip()
    if host.startswith("[") and host.endswith("]"):
        host = host[1:-1]
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        if (
            isinstance(address, ipaddress.IPv6Address)
            and address.scope_id is not None
            and not _IPV6_SCOPE.fullmatch(address.scope_id)
        ):
            raise ValueError("Invalid gateway hostname or IP address")
        return str(address)

    try:
        hostname = host.encode("idna").decode("ascii").lower()
    except UnicodeError as err:
        raise ValueError("Invalid gateway hostname or IP address") from err
    labels = hostname[:-1] if hostname.endswith(".") else hostname
    if (
        not labels
        or len(labels) > 253
        or any(not _HOST_LABEL.fullmatch(label) for label in labels.split("."))
    ):
        raise ValueError("Invalid gateway hostname or IP address")
    return hostname


@dataclass(frozen=True, slots=True)
class SerialGatewayConnection:
    """A gateway connected through a serial device."""

    port: str

    @property
    def unique_id(self) -> str:
        return self.port

    @property
    def timeout(self) -> float:
        return FIXED_TIMEOUT

    def as_data(self) -> dict[str, str | int]:
        return {CONF_PORT: self.port}

    def modbus_params(self) -> ModbusSerialParams:
        return ModbusSerialParams(
            device=self.port,
            baudrate=FIXED_BAUDRATE,
            bytesize=FIXED_BYTESIZE,
            parity=FIXED_PARITY,
            stopbits=FIXED_STOPBITS,
        )


@dataclass(frozen=True, slots=True)
class _NetworkGatewayConnection:
    """A gateway reached over TCP; each subclass sets its framing and default port."""

    host: str
    port: int

    _CONNECTION_TYPE: ClassVar[str]
    _SCHEME: ClassVar[str]

    def __post_init__(self) -> None:
        if not isinstance(self.host, str):
            raise ValueError("Gateway host must be a string")
        if (
            isinstance(self.port, bool)
            or not isinstance(self.port, int)
            or not 1 <= self.port <= 65535
        ):
            raise ValueError("Gateway TCP port must be between 1 and 65535")
        object.__setattr__(self, "host", _normalize_host(self.host))

    @property
    def _endpoint(self) -> str:
        host = f"[{self.host}]" if ":" in self.host else self.host
        return f"{host}:{self.port}"

    @property
    def unique_id(self) -> str:
        return f"{self._SCHEME}://{self._endpoint}"

    @property
    def timeout(self) -> float:
        return NETWORK_TIMEOUT

    def as_data(self) -> dict[str, str | int]:
        # Both network types store their port under ``network_port``; only the
        # flow form splits it into two fields, so each keeps its own default.
        return {
            CONF_CONNECTION_TYPE: self._CONNECTION_TYPE,
            CONF_HOST: self.host,
            CONF_NETWORK_PORT: self.port,
        }


@dataclass(frozen=True, slots=True)
class RtuTcpGatewayConnection(_NetworkGatewayConnection):
    """A gateway reached through raw RTU frames on a TCP connection."""

    port: int = DEFAULT_NETWORK_PORT

    _CONNECTION_TYPE: ClassVar[str] = CONNECTION_RTU_OVER_TCP
    _SCHEME: ClassVar[str] = "rtu-over-tcp"

    def modbus_params(self) -> ModbusSerialParams:
        return ModbusSerialParams(
            device=f"socket://{self._endpoint}",
            baudrate=_SOCKET_BAUDRATE,
            framer="rtu",
        )


@dataclass(frozen=True, slots=True)
class ModbusTcpGatewayConnection(_NetworkGatewayConnection):
    """A gateway reached through Modbus TCP with MBAP framing."""

    port: int = DEFAULT_MODBUS_TCP_PORT

    _CONNECTION_TYPE: ClassVar[str] = CONNECTION_MODBUS_TCP
    _SCHEME: ClassVar[str] = "modbus-tcp"

    def modbus_params(self) -> ModbusTcpParams:
        return ModbusTcpParams(host=self.host, port=self.port)


type GatewayConnection = (
    SerialGatewayConnection | RtuTcpGatewayConnection | ModbusTcpGatewayConnection
)


def gateway_connection_from_data(data: Mapping[str, Any]) -> GatewayConnection:
    """Read an entry; entries predating connection types are serial."""

    connection_type = data.get(CONF_CONNECTION_TYPE, CONNECTION_SERIAL)
    if connection_type == CONNECTION_SERIAL:
        port = data[CONF_PORT]
        if not isinstance(port, str):
            raise ValueError("Serial device path must be a string")
        return SerialGatewayConnection(port)
    if connection_type == CONNECTION_RTU_OVER_TCP:
        return RtuTcpGatewayConnection(data[CONF_HOST], data[CONF_NETWORK_PORT])
    if connection_type == CONNECTION_MODBUS_TCP:
        return ModbusTcpGatewayConnection(data[CONF_HOST], data[CONF_NETWORK_PORT])
    raise ValueError(f"Unknown gateway connection type: {connection_type!r}")
