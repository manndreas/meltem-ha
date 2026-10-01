"""Tests for sensor entity creation, native values, and availability."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, PropertyMock, patch

import pytest
from homeassistant.components.sensor import SensorDeviceClass
from homeassistant.const import EntityCategory, UnitOfVolumeFlowRate
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.meltem_ventilation.const import CONF_PORT, DOMAIN
from custom_components.meltem_ventilation.coordinator import MeltemDataUpdateCoordinator
from custom_components.meltem_ventilation.entity import MeltemEntity
from custom_components.meltem_ventilation.models import RoomConfig, RoomState
from custom_components.meltem_ventilation.sensor import (
    SENSOR_DESCRIPTIONS,
    MeltemModbusDevicePathSensor,
    MeltemModbusSlaveSensor,
    MeltemSensorEntity,
)

# ---------------------------------------------------------------------------
#  Helpers
# ---------------------------------------------------------------------------

_ROOM = RoomConfig(
    key="unit_1", name="Living Room", profile="ii_fc_voc", slave=2, preview="ID 116852 | VOC"
)
_SENSORS = {description.key: description for description in SENSOR_DESCRIPTIONS}


def _coordinator(state: RoomState | None = None) -> MagicMock:
    coordinator = MagicMock(spec=MeltemDataUpdateCoordinator)
    coordinator.last_update_success = True
    coordinator.room_available.return_value = True
    coordinator.read_group_for_entity.side_effect = (
        MeltemDataUpdateCoordinator.read_group_for_entity
    )
    coordinator.read_group_available.return_value = True
    coordinator.safe_data = {} if state is None else {"unit_1": state}
    return coordinator


def _sensor(key: str, state: RoomState | None = None) -> MeltemSensorEntity:
    return MeltemSensorEntity(_coordinator(state), _ROOM, _SENSORS[key])


# ---------------------------------------------------------------------------
#  Entity creation and metadata
# ---------------------------------------------------------------------------


class TestSensorEntityCreation:
    def test_modbus_slave_sensor_metadata_and_value(self) -> None:
        entity = MeltemModbusSlaveSensor(_coordinator(), _ROOM)

        assert entity.unique_id == f"{DOMAIN}_unit_1_modbus_slave_id"
        assert entity.native_value == 2
        assert entity.entity_category is EntityCategory.DIAGNOSTIC
        assert entity.entity_registry_enabled_default is False

    def test_modbus_device_path_sensor_metadata_and_value(self) -> None:
        entry = MockConfigEntry(
            domain=DOMAIN,
            data={CONF_PORT: "/dev/serial/by-id/meltem-gateway"},
        )
        entity = MeltemModbusDevicePathSensor(entry)

        assert entity.unique_id == f"{DOMAIN}_{entry.entry_id}_modbus_device_path"
        assert entity.native_value == "/dev/serial/by-id/meltem-gateway"
        assert entity.entity_category is EntityCategory.DIAGNOSTIC
        assert entity.entity_registry_enabled_default is False

    def test_identity_follows_the_description(self) -> None:
        sensor = _sensor("extract_air_flow")

        assert sensor.unique_id == f"{DOMAIN}_unit_1_extract_air_flow"
        assert sensor.translation_key == "extract_air_flow"
        assert sensor.has_entity_name is True

    def test_device_info(self) -> None:
        sensor = _sensor("exhaust_temperature", RoomState(software_version=42))
        sensor.coordinator.gateway_device_id = "gateway-device"

        info = sensor.device_info

        assert (DOMAIN, "unit_1") in info["identifiers"]
        assert info["manufacturer"] == "Meltem"
        assert "Living Room" in info["name"]
        assert info["sw_version"] == "42"
        assert info["hw_version"] == "116852"
        assert info["via_device_id"] == "gateway-device"

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

    def test_coordinator_update_pushes_versions_to_the_device_registry(self) -> None:
        sensor = _sensor("exhaust_temperature", RoomState(software_version=42))
        sensor.hass = object()
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
                "homeassistant.helpers.update_coordinator.CoordinatorEntity"
                "._handle_coordinator_update"
            ),
        ):
            sensor._handle_coordinator_update()

        registry.async_update_device.assert_called_once_with(
            "device-1", sw_version="42", hw_version="116852"
        )


# ---------------------------------------------------------------------------
#  Native value from room state
# ---------------------------------------------------------------------------


class TestSensorNativeValue:
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
            pytest.param(
                "days_until_filter_change", RoomState(days_until_filter_change=90), 90
            ),
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

        sensor.coordinator.safe_data = {"unit_1": RoomState(extract_air_flow=80)}

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
        sensor.coordinator.read_group_available.side_effect = (
            lambda _room_key, group: group != stale_group
        )

        assert sensor.available is available
        # A stale value stays readable; only its availability changes.
        assert sensor.native_value == getattr(state, key)
