"""Configuration and Modbus parameters for a gateway connection."""

from __future__ import annotations

import ipaddress
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from modbus_connection import ModbusSerialParams

from .const import (
    CONF_CONNECTION_TYPE,
    CONF_HOST,
    CONF_NETWORK_PORT,
    CONF_PORT,
    CONNECTION_RTU_OVER_TCP,
    CONNECTION_SERIAL,
    DEFAULT_NETWORK_PORT,
    FIXED_BAUDRATE,
    FIXED_BYTESIZE,
    FIXED_PARITY,
    FIXED_STOPBITS,
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
        hostname = host.rstrip(".").encode("idna").decode("ascii").lower()
    except UnicodeError as err:
        raise ValueError("Invalid gateway hostname or IP address") from err
    if (
        not hostname
        or len(hostname) > 253
        or any(not _HOST_LABEL.fullmatch(label) for label in hostname.split("."))
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
class RtuTcpGatewayConnection:
    """A gateway reached through raw RTU frames on a TCP connection."""

    host: str
    port: int = DEFAULT_NETWORK_PORT

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
    def unique_id(self) -> str:
        host = f"[{self.host}]" if ":" in self.host else self.host
        return f"rtu-over-tcp://{host}:{self.port}"

    def as_data(self) -> dict[str, str | int]:
        return {
            CONF_CONNECTION_TYPE: CONNECTION_RTU_OVER_TCP,
            CONF_HOST: self.host,
            CONF_NETWORK_PORT: self.port,
        }

    def modbus_params(self) -> ModbusSerialParams:
        host = f"[{self.host}]" if ":" in self.host else self.host
        return ModbusSerialParams(
            device=f"socket://{host}:{self.port}",
            baudrate=_SOCKET_BAUDRATE,
            framer="rtu",
        )


type GatewayConnection = SerialGatewayConnection | RtuTcpGatewayConnection


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
    raise ValueError(f"Unknown gateway connection type: {connection_type!r}")
