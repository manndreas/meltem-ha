"""Tests for serial and RTU-over-TCP gateway configuration."""

import pytest
from modbus_connection import ModbusSerialParams

from custom_components.meltem_ventilation.const import (
    CONF_CONNECTION_TYPE,
    CONF_HOST,
    CONF_NETWORK_PORT,
    CONF_PORT,
    CONNECTION_RTU_OVER_TCP,
    CONNECTION_SERIAL,
)
from custom_components.meltem_ventilation.gateway_connection import (
    RtuTcpGatewayConnection,
    SerialGatewayConnection,
    gateway_connection_from_data,
)


@pytest.mark.parametrize(
    "data",
    [
        {CONF_PORT: "/dev/ttyACM0"},
        {CONF_CONNECTION_TYPE: CONNECTION_SERIAL, CONF_PORT: "/dev/ttyACM0"},
    ],
)
def test_serial_connection_accepts_legacy_entries(data: dict[str, object]) -> None:
    connection = gateway_connection_from_data(data)

    assert connection == SerialGatewayConnection("/dev/ttyACM0")
    assert connection.unique_id == "/dev/ttyACM0"
    assert connection.as_data() == {CONF_PORT: "/dev/ttyACM0"}
    assert connection.modbus_params() == ModbusSerialParams(
        device="/dev/ttyACM0", baudrate=19200, bytesize=8, parity="E", stopbits=1
    )


@pytest.mark.parametrize(
    ("input_host", "host", "address"),
    [
        (" Example.Local. ", "example.local", "example.local"),
        ("192.168.10.2", "192.168.10.2", "192.168.10.2"),
        ("[2001:DB8::1]", "2001:db8::1", "[2001:db8::1]"),
        ("FE80::1%en0", "fe80::1%en0", "[fe80::1%en0]"),
    ],
)
def test_network_connection_normalizes_endpoint(input_host: str, host: str, address: str) -> None:
    connection = RtuTcpGatewayConnection(input_host)

    assert connection.host == host
    assert connection.unique_id == f"rtu-over-tcp://{address}:5020"
    assert connection.modbus_params() == ModbusSerialParams(
        device=f"socket://{address}:5020", baudrate=115200, framer="rtu"
    )
    assert gateway_connection_from_data(connection.as_data()) == connection


def test_network_port_is_part_of_identity_and_params() -> None:
    connection = RtuTcpGatewayConnection("bridge.local", 1234)

    assert connection.unique_id == "rtu-over-tcp://bridge.local:1234"
    assert connection.modbus_params().device == "socket://bridge.local:1234"
    assert connection.as_data() == {
        CONF_CONNECTION_TYPE: CONNECTION_RTU_OVER_TCP,
        CONF_HOST: "bridge.local",
        CONF_NETWORK_PORT: 1234,
    }


@pytest.mark.parametrize(
    "host",
    [
        "",
        " ",
        "socket://bridge:5020",
        "bridge/path",
        "bad host",
        "a..b",
        "-bridge",
        "fe80::1%bad@host",
    ],
)
def test_invalid_network_host_is_rejected(host: str) -> None:
    with pytest.raises(ValueError, match="hostname or IP"):
        RtuTcpGatewayConnection(host)


@pytest.mark.parametrize("port", [0, -1, 65536, True, "5020"])
def test_invalid_network_port_is_rejected(port: object) -> None:
    with pytest.raises(ValueError, match="TCP port"):
        RtuTcpGatewayConnection("bridge.local", port)  # type: ignore[arg-type]


def test_unknown_connection_type_is_not_treated_as_serial() -> None:
    with pytest.raises(ValueError, match="Unknown gateway connection type"):
        gateway_connection_from_data(
            {CONF_CONNECTION_TYPE: "unexpected", CONF_PORT: "/dev/ttyACM0"}
        )


def test_non_string_serial_device_is_rejected() -> None:
    with pytest.raises(ValueError, match="Serial device path"):
        gateway_connection_from_data({CONF_PORT: 5020})
