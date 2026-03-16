"""Tests for Modbus helpers and the MeltemModbusClient runtime class."""

from __future__ import annotations

import pytest

from custom_components.meltem_ventilation.const import (
    MODE_MANUAL,
    MODE_UNBALANCED,
    REGISTER_CO2_EXTRACT_AIR,
    REGISTER_CURRENT_LEVEL,
    REGISTER_EXTRACT_AIR_TARGET_LEVEL,
    REGISTER_GATEWAY_NODE_ADDRESS_1,
    REGISTER_GATEWAY_NUMBER_OF_NODES,
    REGISTER_HUMIDITY_EXTRACT_AIR,
    REGISTER_HUMIDITY_SUPPLY_AIR,
    REGISTER_MODE,
    REGISTER_PRODUCT_ID,
    REGISTER_VOC_SUPPLY_AIR,
)
from custom_components.meltem_ventilation.modbus_client import MeltemModbusClient
from custom_components.meltem_ventilation.modbus_helpers import (
    MeltemModbusError,
    SerialSettings,
    derive_balanced_airflow,
    detect_slave_details_with_client,
    discover_gateway_nodes,
)
from custom_components.meltem_ventilation.models import RefreshPlan, RoomConfig, RoomState


# ---------------------------------------------------------------------------
#  Test doubles
# ---------------------------------------------------------------------------


class _FakeResponse:
    def __init__(self, registers: list[int], error: bool = False) -> None:
        self.registers = registers
        self._error = error

    def isError(self) -> bool:
        return self._error


class _DispatchingClient:
    """Fake Modbus client that returns pre-configured register maps."""

    def __init__(self, register_map: dict[tuple[int, int], list[int]]) -> None:
        self._register_map = register_map
        self.calls: list[tuple[int, int, int]] = []

    def read_holding_registers(self, *, address: int, count: int, device_id: int):
        self.calls.append((device_id, address, count))
        registers = self._register_map.get((address, count))
        if registers is None:
            return _FakeResponse([], error=True)
        return _FakeResponse(registers)


# ---------------------------------------------------------------------------
#  derive_balanced_airflow
# ---------------------------------------------------------------------------


class TestDeriveBalancedAirflow:
    def test_returns_average_when_flows_match_closely(self) -> None:
        assert derive_balanced_airflow(30, 30) == 30
        assert derive_balanced_airflow(30, 31) == 30

    def test_returns_none_when_flows_diverge(self) -> None:
        assert derive_balanced_airflow(30, 40) is None

    def test_returns_existing_single_value_when_one_side_missing(self) -> None:
        assert derive_balanced_airflow(30, None) == 30
        assert derive_balanced_airflow(None, 45) == 45


# ---------------------------------------------------------------------------
#  Gateway discovery
# ---------------------------------------------------------------------------


class TestGatewayDiscovery:
    def test_discovers_gateway_nodes_from_bridge_registers(self) -> None:
        client = _DispatchingClient(
            {
                (REGISTER_GATEWAY_NUMBER_OF_NODES, 1): [6],
                (REGISTER_GATEWAY_NODE_ADDRESS_1, 6): [3, 2, 4, 5, 7, 6],
            }
        )

        discovered = discover_gateway_nodes(client, "/dev/ttyACM0", start=2, end=16)

        assert discovered == [3, 2, 4, 5, 7, 6]

    def test_ignores_zero_and_out_of_range_addresses(self) -> None:
        client = _DispatchingClient(
            {
                (REGISTER_GATEWAY_NUMBER_OF_NODES, 1): [6],
                (REGISTER_GATEWAY_NODE_ADDRESS_1, 6): [0, 1, 3, 17, 5, 5],
            }
        )

        discovered = discover_gateway_nodes(client, "/dev/ttyACM0", start=2, end=16)

        assert discovered == [3, 5]


# ---------------------------------------------------------------------------
#  Setup probe
# ---------------------------------------------------------------------------


class TestSetupProbe:
    def test_detects_voc_profile_and_uses_minimal_reads(self) -> None:
        client = _DispatchingClient(
            {
                (REGISTER_PRODUCT_ID, 2): [0xC874, 0x0001],
                (REGISTER_HUMIDITY_EXTRACT_AIR, 1): [45],
                (REGISTER_HUMIDITY_SUPPLY_AIR, 1): [48],
                (REGISTER_CO2_EXTRACT_AIR, 1): [800],
                (REGISTER_VOC_SUPPLY_AIR, 1): [120],
            }
        )

        detected_profile, preview, supported_entity_keys = (
            detect_slave_details_with_client(client, 4)
        )

        assert detected_profile == "fc_voc"
        assert preview == "ID 116852 | VOC"
        assert "humidity_extract_air" in supported_entity_keys
        assert "humidity_supply_air" in supported_entity_keys
        assert "co2_extract_air" in supported_entity_keys
        assert "voc_supply_air" in supported_entity_keys
        assert client.calls == [
            (4, REGISTER_PRODUCT_ID, 2),
            (4, REGISTER_HUMIDITY_EXTRACT_AIR, 1),
            (4, REGISTER_HUMIDITY_SUPPLY_AIR, 1),
            (4, REGISTER_CO2_EXTRACT_AIR, 1),
            (4, REGISTER_VOC_SUPPLY_AIR, 1),
        ]

    def test_detects_plain_profile_when_capabilities_are_missing(self) -> None:
        client = _DispatchingClient(
            {
                (REGISTER_PRODUCT_ID, 2): [0xC874, 0x0001],
            }
        )

        detected_profile, preview, supported_entity_keys = (
            detect_slave_details_with_client(client, 2)
        )

        assert detected_profile == "plain"
        assert preview == "ID 116852 | basic"
        assert "humidity_extract_air" not in supported_entity_keys
        assert "co2_extract_air" not in supported_entity_keys
        assert "voc_supply_air" not in supported_entity_keys


# ---------------------------------------------------------------------------
#  Float read (NaN handling)
# ---------------------------------------------------------------------------


class TestFloatRead:
    def test_nan_temperature_is_returned_as_none(self) -> None:
        settings = SerialSettings(
            port="/dev/null",
            baudrate=19200,
            bytesize=8,
            parity="E",
            stopbits=1,
            timeout=0.8,
        )
        client = MeltemModbusClient(settings)
        # Monkey-patch the retry wrapper to return NaN registers directly.
        client._read_holding_registers_with_retry = (
            lambda *_args, **_kwargs: _FakeResponse([0x0000, 0x7FC0])
        )

        value = client._read_float32_word_swap(object(), 2, 41009)

        assert value is None


class TestRetryableTransportError:
    def test_detects_retryable_lock_and_transport_errors(self) -> None:
        settings = SerialSettings(
            port="/dev/null",
            baudrate=19200,
            bytesize=8,
            parity="E",
            stopbits=1,
            timeout=0.8,
        )
        client = MeltemModbusClient(settings)

        assert client._is_retryable_transport_error(
            RuntimeError("Could not exclusively lock port /dev/ttyACM0")
        )
        assert client._is_retryable_transport_error(
            OSError("Resource temporarily unavailable")
        )
        assert client._is_retryable_transport_error(
            TimeoutError("timed out waiting for response")
        )
        assert client._is_retryable_transport_error(
            ConnectionError("transport fail")
        )

    def test_does_not_treat_plain_modbus_exception_as_retryable_transport_error(self) -> None:
        settings = SerialSettings(
            port="/dev/null",
            baudrate=19200,
            bytesize=8,
            parity="E",
            stopbits=1,
            timeout=0.8,
        )
        client = MeltemModbusClient(settings)

        assert not client._is_retryable_transport_error(
            RuntimeError("ExceptionResponse(dev_id=4, status=1)")
        )


# ---------------------------------------------------------------------------
#  read_room_state
# ---------------------------------------------------------------------------


class TestReadRoomState:
    @staticmethod
    def _build_client() -> MeltemModbusClient:
        settings = SerialSettings(
            port="/dev/null",
            baudrate=19200,
            bytesize=8,
            parity="E",
            stopbits=1,
            timeout=0.8,
        )
        client = MeltemModbusClient(settings)
        client._ensure_client = lambda: object()
        client._read_status_group = lambda *_a, **_kw: (None, None, None)
        client._supports = lambda _room, _key: True
        client._read_optional_uint32_word_swap = lambda *_a, **_kw: None
        return client

    def test_current_level_uses_scaled_raw_target_readback_when_available(self) -> None:
        client = self._build_client()
        client._read_airflow_pair = lambda *_a, **_kw: (65, 65)
        client._supports = lambda _room, _key: True
        client._read_uint16 = (
            lambda _client, _slave, address: (
                120
                if address == REGISTER_CURRENT_LEVEL
                else None
            )
        )
        client._read_holding_registers_with_retry = lambda *_a, **_kw: _FakeResponse(
            registers=[MODE_MANUAL, 120]
        )
        room = RoomConfig(key="unit_1", name="Unit 1", profile="ii_plain", slave=2)

        state = client.read_room_state(
            room,
            RoomState(current_level=30),
            RefreshPlan.only(refresh_airflow=True),
        )

        assert state.current_level == 60
        assert state.extract_target_level is None

    def test_current_level_falls_back_to_balanced_airflow_when_raw_target_missing(self) -> None:
        client = self._build_client()
        client._read_airflow_pair = lambda *_a, **_kw: (65, 65)
        client._supports = lambda _room, _key: True
        client._read_uint16 = lambda *_a, **_kw: None
        client._read_holding_registers_with_retry = lambda *_a, **_kw: _FakeResponse(
            registers=[MODE_MANUAL, 120]
        )
        room = RoomConfig(key="unit_1", name="Unit 1", profile="ii_plain", slave=2)

        state = client.read_room_state(
            room,
            RoomState(current_level=30),
            RefreshPlan.only(refresh_airflow=True),
        )

        assert state.current_level == 65
        assert state.extract_target_level is None

    def test_current_level_is_none_when_airflows_diverge(self) -> None:
        client = self._build_client()
        client._read_airflow_pair = lambda *_a, **_kw: (30, 40)
        client._supports = lambda _room, key: key != "operation_mode"
        client._read_uint16 = lambda *_a, **_kw: None
        room = RoomConfig(key="unit_1", name="Unit 1", profile="ii_plain", slave=2)

        state = client.read_room_state(
            room,
            RoomState(),
            RefreshPlan.only(refresh_airflow=True),
        )

        assert state.current_level is None

    def test_current_level_clears_stale_balanced_value_when_flows_diverge(self) -> None:
        client = self._build_client()
        client._read_airflow_pair = lambda *_a, **_kw: (30, 40)
        client._supports = lambda _room, key: key != "operation_mode"
        client._read_uint16 = lambda *_a, **_kw: None
        room = RoomConfig(key="unit_1", name="Unit 1", profile="ii_plain", slave=2)

        state = client.read_room_state(
            room,
            RoomState(current_level=55),
            RefreshPlan.only(refresh_airflow=True),
        )

        assert state.current_level is None

    def test_failed_optional_current_level_read_enters_temporary_backoff(self) -> None:
        client = self._build_client()
        client._read_airflow_pair = lambda *_a, **_kw: (30, 30)
        client._supports = lambda _room, _key: True
        calls: list[int] = []

        def _patched_read_uint16(_client, _slave, address):
            calls.append(address)
            if address == REGISTER_CURRENT_LEVEL:
                raise MeltemModbusError("unsupported current level readback")
            return None

        client._read_uint16 = _patched_read_uint16
        client._read_holding_registers_with_retry = lambda *_a, **_kw: _FakeResponse(
            registers=[MODE_MANUAL, 60]
        )
        room = RoomConfig(key="unit_1", name="Unit 1", profile="ii_plain", slave=2)

        first = client.read_room_state(
            room,
            RoomState(),
            RefreshPlan.only(refresh_airflow=True),
        )
        second = client.read_room_state(
            room,
            first,
            RefreshPlan.only(refresh_airflow=True),
        )

        assert first.current_level == 30
        assert second.current_level == 30
        assert calls.count(REGISTER_CURRENT_LEVEL) == 1

    def test_extract_target_is_only_read_in_unbalanced_mode(self) -> None:
        client = self._build_client()
        client._read_airflow_pair = lambda *_a, **_kw: (30, 30)
        client._supports = lambda _room, _key: True
        read_addresses: list[int] = []

        def _patched_read_uint16(_client, _slave, address):
            read_addresses.append(address)
            if address == REGISTER_EXTRACT_AIR_TARGET_LEVEL:
                return 120
            if address == REGISTER_CURRENT_LEVEL:
                return 120
            return None

        client._read_uint16 = _patched_read_uint16
        room = RoomConfig(key="unit_1", name="Unit 1", profile="ii_plain", slave=2)

        client._read_holding_registers_with_retry = lambda *_a, **_kw: _FakeResponse(
            registers=[MODE_MANUAL, 120]
        )
        manual_state = client.read_room_state(
            room,
            RoomState(operation_mode="manual", extract_target_level=60),
            RefreshPlan.only(refresh_airflow=True),
        )

        client._read_holding_registers_with_retry = lambda *_a, **_kw: _FakeResponse(
            registers=[MODE_UNBALANCED, 120]
        )
        unbalanced_state = client.read_room_state(
            room,
            RoomState(operation_mode="unbalanced"),
            RefreshPlan.only(refresh_airflow=True),
        )

        assert manual_state.extract_target_level is None
        assert unbalanced_state.extract_target_level == 60
        assert read_addresses.count(REGISTER_EXTRACT_AIR_TARGET_LEVEL) == 1

    def test_successful_write_clears_optional_airflow_backoff(self) -> None:
        client = self._build_client()
        client._write_uint16 = lambda *_a, **_kw: None
        client._ensure_client = lambda: object()
        room = RoomConfig(key="unit_1", name="Unit 1", profile="ii_plain", slave=2)

        client._mark_optional_read_failure((room.slave, REGISTER_MODE, 2))
        client._mark_optional_read_failure((room.slave, REGISTER_CURRENT_LEVEL, 1))
        client._mark_optional_read_failure((room.slave, REGISTER_EXTRACT_AIR_TARGET_LEVEL, 1))

        client.write_level(room, 40)

        assert not client._is_optional_read_backed_off((room.slave, REGISTER_MODE, 2))
        assert not client._is_optional_read_backed_off((room.slave, REGISTER_CURRENT_LEVEL, 1))
        assert not client._is_optional_read_backed_off((room.slave, REGISTER_EXTRACT_AIR_TARGET_LEVEL, 1))

    def test_plain_profile_reads_only_exhaust_temperature(self) -> None:
        client = self._build_client()
        client._read_temperature_if_due = (
            lambda _client, _room, key, _reg, _prev, _should: {
                "exhaust_temperature": 21.0,
            }.get(key)
        )
        room = RoomConfig(key="unit_1", name="Unit 1", profile="ii_plain", slave=2)

        state = client._read_profile_state(
            object(),
            room,
            RoomState(),
            RefreshPlan.only(refresh_temperatures=True, refresh_environment=True),
        )

        assert state.exhaust_temperature == 21.0
        assert state.outdoor_air_temperature is None
        assert state.extract_air_temperature is None
        assert state.supply_air_temperature is None

    def test_fc_voc_profile_reads_environment_values(self) -> None:
        client = self._build_client()
        client._read_temperature_if_due = (
            lambda _client, _room, key, _reg, _prev, _should: {
                "exhaust_temperature": 18.0,
                "outdoor_air_temperature": 5.0,
                "extract_air_temperature": 22.0,
                "supply_air_temperature": 19.0,
            }[key]
        )
        # _read_uint16_if_due handles both plain uint16 and environment registers
        original_read_uint16_if_due = client._read_uint16_if_due
        env_values = {
            "humidity_extract_air": 44,
            "humidity_supply_air": 46,
            "co2_extract_air": 780,
            "voc_supply_air": 120,
        }

        def _patched_read_uint16_if_due(_client, _room, key, _reg, _prev, _should):
            if key in env_values:
                return env_values[key]
            return original_read_uint16_if_due(_client, _room, key, _reg, _prev, _should)

        client._read_uint16_if_due = _patched_read_uint16_if_due
        room = RoomConfig(key="unit_1", name="Unit 1", profile="ii_fc_voc", slave=2)

        state = client._read_profile_state(
            object(),
            room,
            RoomState(),
            RefreshPlan.only(refresh_temperatures=True, refresh_environment=True),
        )

        assert state.humidity_extract_air == 44
        assert state.humidity_supply_air == 46
        assert state.co2_extract_air == 780
        assert state.voc_supply_air == 120
