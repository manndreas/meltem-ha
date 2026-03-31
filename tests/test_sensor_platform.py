"""Tests for sensor entity creation, native values, and availability."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from custom_components.meltem_ventilation.coordinator import MeltemDataUpdateCoordinator
from custom_components.meltem_ventilation.const import DOMAIN, INTEGRATION_NAME
from custom_components.meltem_ventilation.models import RoomConfig, RoomState
from custom_components.meltem_ventilation.sensor import (
    SENSOR_DESCRIPTIONS,
    MeltemSensorEntity,
    _supports_profile,
)


# ---------------------------------------------------------------------------
#  Helpers
# ---------------------------------------------------------------------------

_ROOM_FC_VOC = RoomConfig(
    key="unit_1", name="Living Room", profile="ii_fc_voc", slave=2, preview="ID 116852 | VOC"
)
_ROOM_PLAIN = RoomConfig(
    key="unit_2", name="Bedroom", profile="ii_plain", slave=3
)
_ROOM_CONSTRAINED = RoomConfig(
    key="unit_3",
    name="Bathroom",
    profile="ii_fc",
    slave=4,
    supported_entity_keys=frozenset({"exhaust_temperature", "humidity_extract_air"}),
)


def _fake_coordinator(data: dict[str, RoomState] | None = None) -> MagicMock:
    coordinator = MagicMock(spec=MeltemDataUpdateCoordinator)
    coordinator.data = data or {}
    type(coordinator).safe_data = property(lambda self: self.data if isinstance(self.data, dict) else {})
    coordinator.async_add_listener = MagicMock(return_value=lambda: None)
    return coordinator


def _find_desc(key: str):
    for d in SENSOR_DESCRIPTIONS:
        if d.key == key:
            return d
    raise ValueError(f"No sensor description with key {key!r}")


# ---------------------------------------------------------------------------
#  Entity creation and metadata
# ---------------------------------------------------------------------------


class TestSensorEntityCreation:
    def test_unique_id_format(self) -> None:
        coordinator = _fake_coordinator()
        desc = _find_desc("exhaust_temperature")
        entity = MeltemSensorEntity(coordinator, _ROOM_FC_VOC, desc)
        assert entity.unique_id == f"{DOMAIN}_unit_1_exhaust_temperature"

    def test_translation_key(self) -> None:
        coordinator = _fake_coordinator()
        desc = _find_desc("extract_air_flow")
        entity = MeltemSensorEntity(coordinator, _ROOM_FC_VOC, desc)
        assert entity.translation_key == "extract_air_flow"

    def test_has_entity_name(self) -> None:
        coordinator = _fake_coordinator()
        desc = _find_desc("exhaust_temperature")
        entity = MeltemSensorEntity(coordinator, _ROOM_FC_VOC, desc)
        assert entity.has_entity_name is True

    def test_device_info(self) -> None:
        coordinator = _fake_coordinator(data={"unit_1": RoomState(software_version=42)})
        desc = _find_desc("exhaust_temperature")
        entity = MeltemSensorEntity(coordinator, _ROOM_FC_VOC, desc)
        info = entity.device_info
        assert (DOMAIN, "unit_1") in info["identifiers"]
        assert info["manufacturer"] == "Meltem"
        assert "Living Room" in info["name"]
        assert info["sw_version"] == "Version 42"
        assert info["hw_version"] == "Produkt-ID 116852"

    def test_handle_coordinator_update_pushes_versions_to_device_registry(self) -> None:
        coordinator = _fake_coordinator(data={"unit_1": RoomState(software_version=42)})
        desc = _find_desc("exhaust_temperature")
        entity = MeltemSensorEntity(coordinator, _ROOM_FC_VOC, desc)
        entity.hass = object()
        fake_registry = MagicMock()
        fake_registry.async_get_device.return_value = type("Device", (), {"id": "device-1"})()

        with (
            patch("custom_components.meltem_ventilation.entity.dr.async_get", return_value=fake_registry),
            patch("homeassistant.helpers.update_coordinator.CoordinatorEntity._handle_coordinator_update"),
        ):
            entity._handle_coordinator_update()

        fake_registry.async_update_device.assert_called_once_with(
            "device-1",
            sw_version="Version 42",
            hw_version="Produkt-ID 116852",
        )

    def test_entity_attributes_from_description(self) -> None:
        coordinator = _fake_coordinator()
        desc = _find_desc("co2_extract_air")
        entity = MeltemSensorEntity(coordinator, _ROOM_FC_VOC, desc)
        assert entity.native_unit_of_measurement == "ppm"
        assert entity.device_class is not None


# ---------------------------------------------------------------------------
#  Native value from room state
# ---------------------------------------------------------------------------


class TestSensorNativeValue:
    def test_temperature_value(self) -> None:
        state = RoomState(exhaust_temperature=22.5)
        coordinator = _fake_coordinator(data={"unit_1": state})
        desc = _find_desc("exhaust_temperature")
        entity = MeltemSensorEntity(coordinator, _ROOM_FC_VOC, desc)
        assert entity.native_value == 22.5

    def test_none_value_returns_none(self) -> None:
        coordinator = _fake_coordinator(data={"unit_1": RoomState()})
        desc = _find_desc("exhaust_temperature")
        entity = MeltemSensorEntity(coordinator, _ROOM_FC_VOC, desc)
        assert entity.native_value is None

    def test_humidity_value(self) -> None:
        state = RoomState(humidity_extract_air=55)
        coordinator = _fake_coordinator(data={"unit_1": state})
        desc = _find_desc("humidity_extract_air")
        entity = MeltemSensorEntity(coordinator, _ROOM_FC_VOC, desc)
        assert entity.native_value == 55

    def test_co2_value(self) -> None:
        state = RoomState(co2_extract_air=800)
        coordinator = _fake_coordinator(data={"unit_1": state})
        desc = _find_desc("co2_extract_air")
        entity = MeltemSensorEntity(coordinator, _ROOM_FC_VOC, desc)
        assert entity.native_value == 800

    def test_voc_value(self) -> None:
        state = RoomState(voc_supply_air=120)
        coordinator = _fake_coordinator(data={"unit_1": state})
        desc = _find_desc("voc_supply_air")
        entity = MeltemSensorEntity(coordinator, _ROOM_FC_VOC, desc)
        assert entity.native_value == 120

    def test_airflow_value(self) -> None:
        state = RoomState(extract_air_flow=65, supply_air_flow=70)
        coordinator = _fake_coordinator(data={"unit_1": state})
        desc = _find_desc("extract_air_flow")
        entity = MeltemSensorEntity(coordinator, _ROOM_FC_VOC, desc)
        assert entity.native_value == 65

    def test_operating_hours(self) -> None:
        state = RoomState(operating_hours=12345)
        coordinator = _fake_coordinator(data={"unit_1": state})
        desc = _find_desc("operating_hours")
        entity = MeltemSensorEntity(coordinator, _ROOM_FC_VOC, desc)
        assert entity.native_value == 12345

    def test_days_until_filter_change(self) -> None:
        state = RoomState(days_until_filter_change=90)
        coordinator = _fake_coordinator(data={"unit_1": state})
        desc = _find_desc("days_until_filter_change")
        entity = MeltemSensorEntity(coordinator, _ROOM_FC_VOC, desc)
        assert entity.native_value == 90


# ---------------------------------------------------------------------------
#  Room state fallback
# ---------------------------------------------------------------------------


class TestRoomStateFallback:
    def test_missing_room_returns_default_state(self) -> None:
        """When coordinator.data has no entry for this room, a default RoomState is used."""
        coordinator = _fake_coordinator(data={})
        desc = _find_desc("exhaust_temperature")
        entity = MeltemSensorEntity(coordinator, _ROOM_FC_VOC, desc)
        # Default RoomState has all None fields.
        assert entity.native_value is None


# ---------------------------------------------------------------------------
#  State update tracking
# ---------------------------------------------------------------------------


class TestSensorStateUpdate:
    def test_value_changes_when_coordinator_data_changes(self) -> None:
        state = RoomState(extract_air_flow=10)
        coordinator = _fake_coordinator(data={"unit_1": state})
        desc = _find_desc("extract_air_flow")
        entity = MeltemSensorEntity(coordinator, _ROOM_FC_VOC, desc)
        assert entity.native_value == 10

        # Simulate coordinator updating data.
        coordinator.data = {
            "unit_1": RoomState(extract_air_flow=80)
        }
        assert entity.native_value == 80
