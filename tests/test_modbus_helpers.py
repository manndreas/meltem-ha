"""Tests for modbus_helpers.py: link setup, discovery, probes, plausibility checks."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

import pytest
from modbus_connection import (
    IllegalDataAddressError,
    ModbusConnectionError,
    ModbusSerialParams,
    ModbusTimeoutError,
)
from modbus_connection.mock import MockModbusConnection, MockModbusUnit

from custom_components.meltem_ventilation.const import (
    BASE_SUPPORTED_ENTITY_KEYS,
    REGISTER_CO2_EXTRACT_AIR,
    REGISTER_GATEWAY_NODE_ADDRESS_1,
    REGISTER_GATEWAY_NUMBER_OF_NODES,
    REGISTER_HUMIDITY_EXTRACT_AIR,
    REGISTER_HUMIDITY_SUPPLY_AIR,
    REGISTER_PRODUCT_ID,
    REGISTER_VOC_SUPPLY_AIR,
)
from custom_components.meltem_ventilation.device import PolicyUnit
from custom_components.meltem_ventilation.modbus_helpers import (
    MeltemConnectionError,
    MeltemModbusError,
    _is_plausible,
    build_serial_params,
    derive_balanced_airflow,
    detect_slave_details,
    discover_gateway_nodes,
    new_transport_policy,
    prepare_unit,
    read_gateway_node_count,
    resolve_preferred_port_path,
    supported_entity_keys_for_profile,
)

_PORT = "/dev/ttyACM0"
# Product ID 116852 as a little-endian uint32.
_PRODUCT_ID_WORDS = [0xC874, 0x0001]
_CAPABILITY_REGISTERS = (
    REGISTER_HUMIDITY_EXTRACT_AIR,
    REGISTER_HUMIDITY_SUPPLY_AIR,
    REGISTER_CO2_EXTRACT_AIR,
    REGISTER_VOC_SUPPLY_AIR,
)


@pytest.fixture(name="gateway")
def gateway_fixture() -> MockModbusUnit:
    return MockModbusConnection().for_unit(1)


@pytest.fixture(name="unit")
def unit_fixture() -> MockModbusUnit:
    return MockModbusConnection().for_unit(4)


def _reads(unit: MockModbusUnit) -> list[tuple[int, int]]:
    return [(event.address, event.count) for event in unit.read_events]


async def _discover(gateway: MockModbusUnit) -> list[int]:
    return await discover_gateway_nodes(gateway, _PORT, start=2, end=16)


# ---------------------------------------------------------------------------
#  resolve_preferred_port_path
# ---------------------------------------------------------------------------


class TestResolvePreferredPortPath:
    def test_by_id_path_returned_unchanged(self) -> None:
        path = "/dev/serial/by-id/usb-Honeywell-whatever"
        assert resolve_preferred_port_path(path) == path

    def test_returns_original_when_no_serial_dir(self) -> None:
        with patch(
            "custom_components.meltem_ventilation.modbus_helpers.Path.exists",
            return_value=False,
        ):
            assert resolve_preferred_port_path(_PORT) == _PORT

    @pytest.mark.skipif(
        sys.platform == "win32",
        reason="creating symlinks requires elevated rights on Windows",
    )
    def test_resolves_symlink_to_by_id(self, tmp_path) -> None:
        serial_dir = tmp_path / "dev" / "serial" / "by-id"
        serial_dir.mkdir(parents=True)
        real_device = tmp_path / "dev" / "ttyACM0"
        real_device.touch()
        link = serial_dir / "usb-honeywell-123"
        link.symlink_to(real_device)

        # Redirect the hard-coded by-id lookup into the temporary tree.
        real_path_cls = Path
        redirects = {
            "/dev/serial/by-id": serial_dir,
            "/dev/ttyACM0": real_device,
        }

        def _redirected_path(argument):
            return real_path_cls(redirects.get(str(argument), argument))

        with patch(
            "custom_components.meltem_ventilation.modbus_helpers.Path",
            side_effect=_redirected_path,
        ):
            assert resolve_preferred_port_path("/dev/ttyACM0") == str(link)


# ---------------------------------------------------------------------------
#  Link parameters and unit preparation
# ---------------------------------------------------------------------------


class TestLinkSetup:
    def test_serial_params_match_the_gateway(self) -> None:
        assert build_serial_params(_PORT) == ModbusSerialParams(
            device=_PORT, baudrate=19200, bytesize=8, parity="E", stopbits=1
        )

    def test_prepared_unit_asks_for_the_gateway_timing(
        self, unit: MockModbusUnit
    ) -> None:
        prepared = prepare_unit(unit, 4, new_transport_policy())

        assert isinstance(prepared, PolicyUnit)
        assert prepared.unit_id == 4
        assert unit.message_spacing == 0.1
        assert unit.required_timeout == 0.8


# ---------------------------------------------------------------------------
#  Gateway discovery
# ---------------------------------------------------------------------------


class TestReadGatewayNodeCount:
    async def test_returns_the_node_count(self, gateway: MockModbusUnit) -> None:
        gateway.holding[REGISTER_GATEWAY_NUMBER_OF_NODES] = 3

        assert await read_gateway_node_count(gateway) == 3

    async def test_a_dead_link_is_a_connection_error(
        self, gateway: MockModbusUnit
    ) -> None:
        gateway.fail_requests(ModbusConnectionError("no port"))

        with pytest.raises(MeltemConnectionError):
            await read_gateway_node_count(gateway)

    async def test_a_silent_gateway_is_a_modbus_error(
        self, gateway: MockModbusUnit
    ) -> None:
        gateway.fail_requests(ModbusTimeoutError("silent"))

        with pytest.raises(MeltemModbusError) as err:
            await read_gateway_node_count(gateway)

        assert not isinstance(err.value, MeltemConnectionError)


class TestDiscoverGatewayNodes:
    @pytest.mark.parametrize(
        ("addresses", "expected"),
        [
            pytest.param([3, 2, 4, 5, 7, 6], [3, 2, 4, 5, 7, 6], id="gateway-order"),
            pytest.param([0, 1, 3, 17, 5, 5], [3, 5], id="zero-out-of-range-and-duplicates"),
        ],
    )
    async def test_reads_the_bridge_registers(
        self, gateway: MockModbusUnit, addresses: list[int], expected: list[int]
    ) -> None:
        gateway.holding.update(
            {
                REGISTER_GATEWAY_NUMBER_OF_NODES: len(addresses),
                REGISTER_GATEWAY_NODE_ADDRESS_1: addresses,
            }
        )

        assert await _discover(gateway) == expected
        assert _reads(gateway) == [
            (REGISTER_GATEWAY_NUMBER_OF_NODES, 1),
            (REGISTER_GATEWAY_NODE_ADDRESS_1, len(addresses)),
        ]

    async def test_caps_the_address_list_at_32_nodes(
        self, gateway: MockModbusUnit
    ) -> None:
        gateway.holding[REGISTER_GATEWAY_NUMBER_OF_NODES] = 99

        await _discover(gateway)

        assert _reads(gateway)[-1] == (REGISTER_GATEWAY_NODE_ADDRESS_1, 32)

    async def test_no_configured_nodes_yields_no_units(
        self, gateway: MockModbusUnit
    ) -> None:
        gateway.holding[REGISTER_GATEWAY_NUMBER_OF_NODES] = 0

        assert await _discover(gateway) == []
        assert _reads(gateway) == [(REGISTER_GATEWAY_NUMBER_OF_NODES, 1)]

    async def test_a_silent_gateway_yields_no_units(
        self, gateway: MockModbusUnit
    ) -> None:
        gateway.fail_requests(ModbusTimeoutError("silent"))

        assert await _discover(gateway) == []

    async def test_an_unreadable_address_list_yields_no_units(
        self, gateway: MockModbusUnit
    ) -> None:
        gateway.holding[REGISTER_GATEWAY_NUMBER_OF_NODES] = 2
        gateway.fail_read(REGISTER_GATEWAY_NODE_ADDRESS_1, IllegalDataAddressError())

        assert await _discover(gateway) == []

    async def test_a_dead_link_is_raised(self, gateway: MockModbusUnit) -> None:
        gateway.fail_requests(ModbusConnectionError("no port"))

        with pytest.raises(MeltemConnectionError, match=_PORT):
            await _discover(gateway)


# ---------------------------------------------------------------------------
#  Setup probe
# ---------------------------------------------------------------------------


class TestDetectSlaveDetails:
    async def test_detects_voc_profile_with_minimal_reads(
        self, unit: MockModbusUnit
    ) -> None:
        unit.holding.update(
            {
                REGISTER_PRODUCT_ID: _PRODUCT_ID_WORDS,
                REGISTER_HUMIDITY_EXTRACT_AIR: 45,
                REGISTER_CO2_EXTRACT_AIR: 800,
                REGISTER_HUMIDITY_SUPPLY_AIR: 48,
                REGISTER_VOC_SUPPLY_AIR: 120,
            }
        )

        profile, preview, keys = await detect_slave_details(unit)

        assert profile == "fc_voc"
        assert preview == "ID 116852 | VOC"
        assert {
            "humidity_extract_air",
            "humidity_supply_air",
            "co2_extract_air",
            "voc_supply_air",
        } <= set(keys)
        assert _reads(unit) == [
            (REGISTER_PRODUCT_ID, 2),
            (REGISTER_HUMIDITY_EXTRACT_AIR, 1),
            (REGISTER_HUMIDITY_SUPPLY_AIR, 1),
            (REGISTER_CO2_EXTRACT_AIR, 1),
            (REGISTER_VOC_SUPPLY_AIR, 1),
        ]

    async def test_detects_plain_profile_when_capabilities_are_missing(
        self, unit: MockModbusUnit
    ) -> None:
        unit.holding[REGISTER_PRODUCT_ID] = _PRODUCT_ID_WORDS
        for register in _CAPABILITY_REGISTERS:
            unit.fail_read(register, IllegalDataAddressError())

        profile, preview, keys = await detect_slave_details(unit)

        assert profile == "plain"
        assert preview == "ID 116852 | basic"
        assert set(keys) == BASE_SUPPORTED_ENTITY_KEYS

    async def test_implausible_values_do_not_count_as_capabilities(
        self, unit: MockModbusUnit
    ) -> None:
        unit.holding.update(
            {
                REGISTER_HUMIDITY_EXTRACT_AIR: 0xFFFF,
                REGISTER_HUMIDITY_SUPPLY_AIR: 0xFFFF,
                REGISTER_CO2_EXTRACT_AIR: 0,
                REGISTER_VOC_SUPPLY_AIR: 0xFFFF,
            }
        )
        unit.fail_read(REGISTER_PRODUCT_ID, IllegalDataAddressError())

        profile, preview, _keys = await detect_slave_details(unit)

        assert profile == "plain"
        assert preview == "basic"

    async def test_a_silent_unit_is_probed_as_plain(self, unit: MockModbusUnit) -> None:
        unit.fail_requests(ModbusTimeoutError("silent"))

        profile, preview, _keys = await detect_slave_details(unit)

        assert profile == "plain"
        assert preview == "basic"
        # The remaining probes would only time out as well.
        assert _reads(unit) == [(REGISTER_PRODUCT_ID, 2)]

    async def test_a_dead_link_is_raised(self, unit: MockModbusUnit) -> None:
        unit.fail_requests(ModbusConnectionError("no port"))

        with pytest.raises(MeltemConnectionError):
            await detect_slave_details(unit)


# ---------------------------------------------------------------------------
#  Pure helpers
# ---------------------------------------------------------------------------


class TestDeriveBalancedAirflow:
    @pytest.mark.parametrize(
        ("extract", "supply", "expected"),
        [
            pytest.param(30, 30, 30, id="equal"),
            pytest.param(30, 31, 30, id="close"),
            pytest.param(30, 40, None, id="diverging"),
            pytest.param(30, None, 30, id="supply-missing"),
            pytest.param(None, 45, 45, id="extract-missing"),
        ],
    )
    def test_balanced_airflow(
        self, extract: int | None, supply: int | None, expected: int | None
    ) -> None:
        assert derive_balanced_airflow(extract, supply) == expected


class TestPlausibilityChecks:
    @pytest.mark.parametrize(
        ("key", "minimum", "maximum"),
        [
            ("humidity_extract_air", 0, 100),
            ("humidity_supply_air", 0, 100),
            ("co2_extract_air", 250, 10000),
            ("voc_supply_air", 0, 10000),
        ],
    )
    def test_only_values_in_the_sensor_range_are_plausible(
        self, key: str, minimum: int, maximum: int
    ) -> None:
        assert not _is_plausible(key, None)
        assert not _is_plausible(key, minimum - 1)
        assert _is_plausible(key, minimum)
        assert _is_plausible(key, maximum)
        assert not _is_plausible(key, maximum + 1)


class TestSupportedEntityKeysForProfile:
    def test_plain_units_get_only_the_base_keys(self) -> None:
        keys = set(supported_entity_keys_for_profile("ii_plain"))

        assert keys == BASE_SUPPORTED_ENTITY_KEYS
        assert "outdoor_air_temperature" not in keys
        assert "extract_air_temperature" not in keys
