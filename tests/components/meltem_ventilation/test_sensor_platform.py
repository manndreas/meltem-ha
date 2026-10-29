"""Tests for sensor entity creation, native values, and availability."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import MagicMock, PropertyMock, call, patch

import pytest
from homeassistant.components.sensor import SensorDeviceClass, SensorStateClass
from homeassistant.const import EntityCategory, UnitOfTime, UnitOfVolumeFlowRate
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import CONNECTION_NETWORK_MAC, DeviceInfo
from homeassistant.helpers.typing import UNDEFINED
from homeassistant.util import dt as dt_util
from pytest_homeassistant.common import MockConfigEntry

from custom_components.meltem_ventilation import entity as entity_module
from custom_components.meltem_ventilation.const import CONF_PORT, DOMAIN
from custom_components.meltem_ventilation.coordinator import MeltemDataUpdateCoordinator
from custom_components.meltem_ventilation.cumulative_diagnostics import CumulativeDiagnostics
from custom_components.meltem_ventilation.entity import MeltemEntity
from custom_components.meltem_ventilation.models import (
    RoomConfig,
    RoomState,
)
from custom_components.meltem_ventilation.sensor import (
    CUMULATIVE_GATEWAY_SENSOR_DESCRIPTIONS,
    GATEWAY_SENSOR_DESCRIPTIONS,
    ROOM_DIAGNOSTIC_DESCRIPTIONS,
    SENSOR_DESCRIPTIONS,
    MeltemCumulativeGatewayDiagnosticSensor,
    MeltemGatewayDiagnosticSensor,
    MeltemGatewayUptimeSensor,
    MeltemModbusDevicePathSensor,
    MeltemModbusSlaveSensor,
    MeltemRoomDiagnosticSensor,
    MeltemSensorEntity,
)

# ---------------------------------------------------------------------------
#  Helpers
# ---------------------------------------------------------------------------

_ROOM = RoomConfig(
    key="unit_1",
    name="Living Room",
    profile="ii_fc_voc",
    slave=2,
    preview="VOC",
    product_id=116852,
)
_SENSORS = {description.key: description for description in SENSOR_DESCRIPTIONS}
_GATEWAY_SENSORS = {description.key: description for description in GATEWAY_SENSOR_DESCRIPTIONS}


def _coordinator(state: RoomState | None = None) -> MagicMock:
    coordinator = MagicMock(spec=MeltemDataUpdateCoordinator)
    coordinator.last_update_success = True
    coordinator.room_available.return_value = True
    coordinator.read_group_for_entity.side_effect = (
        MeltemDataUpdateCoordinator.read_group_for_entity
    )
    coordinator.read_group_available.return_value = True
    coordinator.data = {} if state is None else {"unit_1": state}
    type(coordinator).safe_data = property(
        lambda self: self.data if isinstance(self.data, dict) else {}
    )
    return coordinator


def _sensor(key: str, state: RoomState | None = None) -> MeltemSensorEntity:
    return MeltemSensorEntity(_coordinator(state), _ROOM, _SENSORS[key])


# ---------------------------------------------------------------------------
#  Entity creation and metadata
# ---------------------------------------------------------------------------


class TestSensorEntityCreation:
    @pytest.mark.parametrize("gateway_mac", [None, "02:00:00:00:00:01"])
    def test_gateway_device_info_optional_mac(self, gateway_mac: str | None) -> None:
        info = entity_module.gateway_device_info("gateway-entry", gateway_mac)

        assert isinstance(info, DeviceInfo)
        assert info.identifiers == {(DOMAIN, "gateway-entry")}
        if gateway_mac is None:
            assert info.connections is UNDEFINED
        else:
            assert info.connections == {(CONNECTION_NETWORK_MAC, gateway_mac)}

    @pytest.mark.parametrize("gateway_device_id", [None, "gateway-device"])
    def test_room_device_info_optional_gateway(self, gateway_device_id: str | None) -> None:
        sensor = _sensor("exhaust_temperature", RoomState(software_version=42))
        setattr(sensor.coordinator, "gateway_device_id", gateway_device_id)

        info = sensor.device_info

        assert isinstance(info, DeviceInfo)
        assert info.identifiers == {(DOMAIN, "unit_1")}
        assert info.sw_version == "42"
        if gateway_device_id is None:
            assert info.via_device_id is UNDEFINED
        else:
            assert info.via_device_id == gateway_device_id

    async def test_cumulative_gateway_counter_metadata_value_and_attributes(
        self, hass: HomeAssistant
    ) -> None:
        diagnostics = await CumulativeDiagnostics.async_create(hass, "sensor-entry")
        diagnostics.record_request("success")
        diagnostics.record_request("no_answer")
        coordinator = _coordinator()
        coordinator.cumulative_diagnostics = diagnostics
        description = next(
            item
            for item in CUMULATIVE_GATEWAY_SENSOR_DESCRIPTIONS
            if item.key == "gateway_modbus_failures_total"
        )
        entity = MeltemCumulativeGatewayDiagnosticSensor(
            coordinator,
            "sensor-entry",
            description,
        )

        assert entity.unique_id == f"{DOMAIN}_sensor-entry_gateway_modbus_failures_total"
        assert entity.native_value == 1
        assert entity.state_class is SensorStateClass.TOTAL_INCREASING
        assert entity.entity_category is EntityCategory.DIAGNOSTIC
        assert entity.entity_registry_enabled_default is False
        assert entity.extra_state_attributes == {
            "period_started_at": diagnostics.period_started_at,
            "failures_by_category": {
                "no_answer": 1,
                "modbus_exception": 0,
                "other": 0,
            },
            "successful_attempts": 1,
        }

    @pytest.mark.parametrize(
        ("key", "value", "unit", "state_class"),
        [
            ("rf_last_seen_age_seconds", 50, "s", SensorStateClass.MEASUREMENT),
            ("rf_signal_strength", -43, "dBm", SensorStateClass.MEASUREMENT),
            ("fan_motor_hours", 38423, "h", SensorStateClass.TOTAL_INCREASING),
        ],
    )
    def test_optional_room_diagnostic_metadata_and_value(
        self, key: str, value: int, unit: str, state_class: SensorStateClass
    ) -> None:
        state = RoomState(
            fan_motor_hours=38423, rf_last_seen_age_seconds=50, rf_signal_strength=-43
        )
        entity = _sensor(key, state)
        assert entity.native_value == value
        assert entity.native_unit_of_measurement == unit
        assert entity.state_class == state_class
        assert entity.entity_category is EntityCategory.DIAGNOSTIC
        assert entity.entity_registry_enabled_default is False

    def test_gateway_diagnostics_remain_visible_without_modbus_requests(self) -> None:
        coordinator = _coordinator()
        coordinator.last_update_success = False
        client = coordinator.client = MagicMock()
        client.transport_diagnostics.return_value = {"last_answer_timestamp": 1792580112}

        entity = MeltemGatewayDiagnosticSensor(
            coordinator, "gateway-entry", _GATEWAY_SENSORS["gateway_last_answer"]
        )

        assert entity.unique_id == f"{DOMAIN}_gateway-entry_gateway_last_answer"
        info = entity.device_info
        assert isinstance(info, DeviceInfo)
        assert info.as_dict() == {
            "identifiers": {(DOMAIN, "gateway-entry")},
            "manufacturer": "Meltem",
            "model": "M-WRG-GW",
            "name": "Meltem Gateway M-WRG-GW",
        }
        assert entity.available is True
        assert entity.native_value == datetime.fromtimestamp(1792580112, tz=UTC)
        assert entity.device_class is SensorDeviceClass.TIMESTAMP
        assert entity.entity_category is EntityCategory.DIAGNOSTIC
        assert entity.entity_registry_enabled_default is False
        assert client.mock_calls == [call.transport_diagnostics()]

    def test_gateway_uptime_is_reported_in_days(self) -> None:
        coordinator = _coordinator()
        coordinator.gateway_uptime = 129_600

        entity = MeltemGatewayUptimeSensor(coordinator, "gateway-entry")

        assert entity.native_value == 1.5
        assert entity.native_unit_of_measurement == UnitOfTime.DAYS
        assert entity.device_class is SensorDeviceClass.DURATION
        assert entity.suggested_display_precision == 1

    def test_gateway_diagnostic_display_precision(self) -> None:
        descriptions = {description.key: description for description in GATEWAY_SENSOR_DESCRIPTIONS}

        assert descriptions["gateway_rf_load_current_hour"].suggested_display_precision == 2
        assert descriptions["gateway_rf_load_previous_hour"].suggested_display_precision == 2
        assert descriptions["gateway_response_time"].suggested_display_precision == 0
        assert descriptions["gateway_success_rate"].suggested_display_precision == 0

    def test_modbus_slave_sensor_metadata_and_value(self) -> None:
        entity = MeltemModbusSlaveSensor(_coordinator(), _ROOM)

        assert entity.unique_id == f"{DOMAIN}_unit_1_modbus_slave_id"
        assert entity.native_value == 2
        assert entity.entity_category is EntityCategory.DIAGNOSTIC
        assert entity.entity_registry_enabled_default is False

    def test_modbus_device_path_sensor_metadata_and_value(self) -> None:
        entry = MockConfigEntry(
            domain=DOMAIN,
            data={CONF_PORT: "/dev/serial/by-id/test-gateway"},
        )
        entity = MeltemModbusDevicePathSensor(entry)

        assert entity.unique_id == f"{DOMAIN}_{entry.entry_id}_modbus_device_path"
        assert entity.native_value == "/dev/serial/by-id/test-gateway"
        assert entity.entity_category is EntityCategory.DIAGNOSTIC
        assert entity.entity_registry_enabled_default is False

    def test_identity_follows_the_description(self) -> None:
        sensor = _sensor("extract_air_flow")

        assert sensor.unique_id == f"{DOMAIN}_unit_1_extract_air_flow"
        assert sensor.translation_key == "extract_air_flow"
        assert sensor.has_entity_name is True

    def test_device_info(self) -> None:
        sensor = _sensor("exhaust_temperature", RoomState(software_version=42))
        setattr(sensor.coordinator, "gateway_device_id", "gateway-device")

        info = sensor.device_info

        assert info.identifiers == {(DOMAIN, "unit_1")}
        assert info.manufacturer == "Meltem"
        name = info.name
        assert isinstance(name, str)
        assert "Living Room" in name
        assert info.sw_version == "42"
        assert info.hw_version == "116852"
        assert info.serial_number is UNDEFINED
        assert info.via_device_id == "gateway-device"

    def test_device_info_uses_structured_unit_identity(self) -> None:
        room = RoomConfig(
            key="unit_1",
            name="Living Room",
            profile="ii_fc_voc",
            slave=2,
            preview="VOC",
            product_id=116852,
            app_setup_code="R000001",
        )
        sensor = MeltemSensorEntity(_coordinator(), room, _SENSORS["exhaust_temperature"])

        info = sensor.device_info
        assert info.hw_version == "116852"
        assert info.identifiers == {
            (DOMAIN, "unit_1"),
            (DOMAIN, "app_setup_code:R000001"),
        }
        assert info.serial_number is UNDEFINED

    @pytest.mark.parametrize(
        ("key", "device_class", "unit"),
        [
            (
                "extract_air_flow",
                SensorDeviceClass.VOLUME_FLOW_RATE,
                UnitOfVolumeFlowRate.CUBIC_METERS_PER_HOUR,
            ),
            (
                "supply_air_flow",
                SensorDeviceClass.VOLUME_FLOW_RATE,
                UnitOfVolumeFlowRate.CUBIC_METERS_PER_HOUR,
            ),
            ("co2_extract_air", SensorDeviceClass.CO2, "ppm"),
        ],
    )
    def test_device_class_and_unit_come_from_the_description(
        self, key: str, device_class: SensorDeviceClass, unit: str
    ) -> None:
        sensor = _sensor(key)

        assert sensor.device_class == device_class
        assert sensor.native_unit_of_measurement == unit

    def test_coordinator_update_pushes_versions_to_the_device_registry(
        self, hass: HomeAssistant
    ) -> None:
        sensor = _sensor("exhaust_temperature", RoomState(software_version=42))
        sensor.hass = hass
        registry = MagicMock()

        with (
            patch.object(
                MeltemEntity,
                "device_entry",
                new_callable=PropertyMock,
                return_value=SimpleNamespace(id="device-1"),
            ),
            patch(
                "custom_components.meltem_ventilation.entity.dr.async_get",
                return_value=registry,
            ),
            patch(
                "homeassistant.helpers.update_coordinator.BaseCoordinatorEntity"
                "._handle_coordinator_update"
            ),
        ):
            sensor._handle_coordinator_update()

        registry.async_update_device.assert_called_once_with(
            "device-1", sw_version="42", hw_version="116852"
        )

    def test_coordinator_update_skips_device_registry_when_versions_match(
        self, hass: HomeAssistant
    ) -> None:
        sensor = _sensor("exhaust_temperature", RoomState(software_version=42))
        sensor.hass = hass
        registry = MagicMock()

        with (
            patch.object(
                MeltemEntity,
                "device_entry",
                new_callable=PropertyMock,
                return_value=SimpleNamespace(id="device-1", sw_version="42", hw_version="116852"),
            ),
            patch(
                "custom_components.meltem_ventilation.entity.dr.async_get",
                return_value=registry,
            ),
            patch(
                "homeassistant.helpers.update_coordinator.BaseCoordinatorEntity"
                "._handle_coordinator_update"
            ),
        ):
            sensor._handle_coordinator_update()

        registry.async_update_device.assert_not_called()


# ---------------------------------------------------------------------------
#  Native value from room state
# ---------------------------------------------------------------------------


class TestGatewayDiagnostics:
    @pytest.mark.parametrize(
        ("key", "expected"),
        [
            ("gateway_supply_air_flow", 60),
            ("gateway_extract_air_flow", 45),
            ("gateway_airflow_delta", 15),
        ],
    )
    def test_aggregates_and_incomplete_data(self, key: str, expected: int) -> None:
        coordinator = _coordinator()
        coordinator.rooms = [
            _ROOM,
            RoomConfig(key="unit_2", name="Other", profile="ii_plain", slave=3),
        ]
        coordinator.data = {
            "unit_1": RoomState(
                error_status=True, filter_change_due=False, supply_air_flow=0, extract_air_flow=15
            ),
            "unit_2": RoomState(
                error_status=False, filter_change_due=False, supply_air_flow=60, extract_air_flow=30
            ),
        }
        entity = MeltemGatewayDiagnosticSensor(coordinator, "entry", _GATEWAY_SENSORS[key])
        assert entity.native_value == expected
        attributes = entity.extra_state_attributes
        assert attributes is not None
        assert attributes["complete"] is True

        coordinator.read_group_available.side_effect = lambda room, group: room != "unit_2"
        assert entity.native_value is None
        attributes = entity.extra_state_attributes
        assert attributes is not None
        assert attributes["complete"] is False
        assert attributes["unknown_units"] == {"unit_2": "Other"}

    def test_totals_are_independent_per_direction(self) -> None:
        coordinator = _coordinator(RoomState(supply_air_flow=0, extract_air_flow=None))
        coordinator.rooms = [_ROOM]
        supply = MeltemGatewayDiagnosticSensor(
            coordinator, "entry", _GATEWAY_SENSORS["gateway_supply_air_flow"]
        )
        extract = MeltemGatewayDiagnosticSensor(
            coordinator, "entry", _GATEWAY_SENSORS["gateway_extract_air_flow"]
        )
        assert supply.native_value == 0
        assert extract.native_value is None
        delta = MeltemGatewayDiagnosticSensor(
            coordinator, "entry", _GATEWAY_SENSORS["gateway_airflow_delta"]
        )
        assert delta.native_value is None

    @pytest.mark.parametrize(
        "key",
        ["gateway_supply_air_flow", "gateway_extract_air_flow", "gateway_airflow_delta"],
    )
    def test_no_rooms_is_unknown(self, key: str) -> None:
        coordinator = _coordinator()
        coordinator.rooms = []
        entity = MeltemGatewayDiagnosticSensor(coordinator, "entry", _GATEWAY_SENSORS[key])
        assert entity.native_value is None

    def test_airflow_delta_unions_unknown_units_from_both_directions(self) -> None:
        coordinator = _coordinator()
        coordinator.rooms = [
            _ROOM,
            RoomConfig(key="unit_2", name="Other", profile="ii_plain", slave=3),
        ]
        coordinator.data = {
            "unit_1": RoomState(supply_air_flow=30, extract_air_flow=None),
            "unit_2": RoomState(supply_air_flow=None, extract_air_flow=20),
        }
        entity = MeltemGatewayDiagnosticSensor(
            coordinator, "entry", _GATEWAY_SENSORS["gateway_airflow_delta"]
        )

        assert entity.native_value is None
        assert entity.extra_state_attributes == {
            "complete": False,
            "unknown_units": {"unit_1": "Living Room", "unit_2": "Other"},
        }

    def test_airflow_delta_metadata(self) -> None:
        description = _GATEWAY_SENSORS["gateway_airflow_delta"]

        assert description.native_unit_of_measurement == UnitOfVolumeFlowRate.CUBIC_METERS_PER_HOUR
        assert description.state_class == SensorStateClass.MEASUREMENT
        assert description.entity_registry_enabled_default is True
        assert description.entity_category is None

    @pytest.mark.parametrize(
        ("key", "transport_key", "value"),
        [
            ("gateway_last_answer", "last_answer_timestamp", None),
            ("gateway_last_answer", "last_answer_timestamp", 0.0),
        ],
    )
    def test_transport_values(self, key: str, transport_key: str, value: float | None) -> None:
        coordinator = _coordinator()
        coordinator.client = MagicMock()
        coordinator.client.transport_diagnostics.return_value = {transport_key: value}
        entity = MeltemGatewayDiagnosticSensor(coordinator, "entry", _GATEWAY_SENSORS[key])

        expected = None if value is None else datetime.fromtimestamp(value, tz=UTC)
        assert entity.native_value == expected
        assert entity.extra_state_attributes is None


class TestRoomDiagnostics:
    def test_timestamp_stays_visible_without_live_data(self) -> None:
        coordinator = _coordinator()
        coordinator.last_update_success = False
        timestamp = dt_util.utcnow()
        coordinator.last_successful_room_update.return_value = timestamp
        description = next(
            item for item in ROOM_DIAGNOSTIC_DESCRIPTIONS if item.key == "last_successful_update"
        )
        entity = MeltemRoomDiagnosticSensor(coordinator, _ROOM, description)
        assert entity.native_value == timestamp
        assert entity.available is True
        coordinator.last_successful_room_update.assert_called_once_with("unit_1")


class TestSensorNativeValue:
    @pytest.mark.parametrize(
        ("supply", "extract", "expected"),
        [(30, 40, -10), (0, 0, 0), (40, 30, 10), (None, 30, None)],
    )
    def test_balance_requires_both_directions(
        self, supply: int | None, extract: int, expected: int | None
    ) -> None:
        coordinator = _coordinator(RoomState(supply_air_flow=supply, extract_air_flow=extract))
        sensor = MeltemSensorEntity(coordinator, _ROOM, _SENSORS["airflow_balance"])
        assert sensor.native_value == expected
        coordinator.read_group_available.return_value = False
        assert sensor.available is False

    @pytest.mark.parametrize(
        ("key", "state", "expected"),
        [
            pytest.param("exhaust_temperature", RoomState(exhaust_temperature=22.5), 22.5),
            pytest.param("humidity_extract_air", RoomState(humidity_extract_air=55), 55),
            pytest.param("co2_extract_air", RoomState(co2_extract_air=800), 800),
            pytest.param("voc_supply_air", RoomState(voc_supply_air=120), 120),
            pytest.param(
                "extract_air_flow", RoomState(extract_air_flow=65, supply_air_flow=70), 65
            ),
            pytest.param("operating_hours", RoomState(operating_hours=12345), 12345),
            pytest.param("days_until_filter_change", RoomState(days_until_filter_change=90), 90),
            pytest.param("exhaust_temperature", RoomState(), None, id="not-read-yet"),
            pytest.param("exhaust_temperature", None, None, id="room-missing"),
        ],
    )
    def test_native_value_comes_from_the_room_state(
        self, key: str, state: RoomState | None, expected: float | None
    ) -> None:
        assert _sensor(key, state).native_value == expected

    def test_value_follows_the_coordinator_data(self) -> None:
        sensor = _sensor("extract_air_flow", RoomState(extract_air_flow=10))
        assert sensor.native_value == 10

        sensor.coordinator.data = {"unit_1": RoomState(extract_air_flow=80)}
        with patch.object(sensor, "async_write_ha_state"):
            sensor._handle_coordinator_update()

        assert sensor.native_value == 80


# ---------------------------------------------------------------------------
#  Availability
# ---------------------------------------------------------------------------


class TestSensorAvailability:
    @pytest.mark.parametrize(
        ("key", "stale_group", "available"),
        [
            ("supply_air_flow", "flow", False),
            ("exhaust_temperature", "flow", True),
            ("exhaust_temperature", "temperature", False),
        ],
    )
    def test_sensor_follows_the_freshness_of_its_own_read_group(
        self, key: str, stale_group: str, available: bool
    ) -> None:
        state = RoomState(supply_air_flow=30, exhaust_temperature=22.5)
        sensor = _sensor(key, state)
        with patch.object(
            sensor.coordinator,
            "read_group_available",
            side_effect=lambda _room_key, group: group != stale_group,
        ):
            assert sensor.available is available
        # A stale value stays readable; only its availability changes.
        assert sensor.native_value == getattr(state, key)
