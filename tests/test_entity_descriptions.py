"""Tests for entity descriptions, filter logic, and metadata correctness."""

from __future__ import annotations

import pytest

from custom_components.meltem_ventilation.binary_sensor import (
    BINARY_SENSOR_DESCRIPTIONS,
    _supports_profile as binary_sensor_supports_profile,
)
from custom_components.meltem_ventilation.models import RoomConfig
from custom_components.meltem_ventilation.number import (
    CONTROL_SETTING_DESCRIPTIONS,
    _room_supports,
    _supports_control_setting,
)
from custom_components.meltem_ventilation.sensor import (
    SENSOR_DESCRIPTIONS,
    _supports_profile as sensor_supports_profile,
)
from custom_components.meltem_ventilation.select import _room_supports as select_supports_room


# ---------------------------------------------------------------------------
#  Helpers
# ---------------------------------------------------------------------------


def _sensor_desc(key: str):
    for desc in SENSOR_DESCRIPTIONS:
        if desc.key == key:
            return desc
    raise ValueError(f"No sensor description with key {key!r}")


def _binary_desc(key: str):
    for desc in BINARY_SENSOR_DESCRIPTIONS:
        if desc.key == key:
            return desc
    raise ValueError(f"No binary sensor description with key {key!r}")


def _control_setting_desc(key: str):
    for desc in CONTROL_SETTING_DESCRIPTIONS:
        if desc.key == key:
            return desc
    raise ValueError(f"No control-setting description with key {key!r}")


# ---------------------------------------------------------------------------
#  Number filter
# ---------------------------------------------------------------------------


class TestNumberFilter:
    def test_room_without_supported_keys_allows_all(self) -> None:
        room = RoomConfig(key="u1", name="U1", profile="ii_plain", slave=2)
        assert _room_supports(room, "level")
        assert _room_supports(room, "supply_level")
        assert _room_supports(room, "extract_level")

    def test_room_with_supported_keys_filters_correctly(self) -> None:
        room = RoomConfig(
            key="u1",
            name="U1",
            profile="ii_plain",
            slave=2,
            supported_entity_keys=frozenset({"level", "extract_level"}),
        )
        assert _room_supports(room, "level")
        assert not _room_supports(room, "supply_level")
        assert _room_supports(room, "extract_level")

    def test_room_with_empty_supported_keys_blocks_all(self) -> None:
        room = RoomConfig(
            key="u1",
            name="U1",
            profile="ii_plain",
            slave=2,
            supported_entity_keys=frozenset(),
        )
        assert not _room_supports(room, "level")
        assert not _room_supports(room, "supply_level")

    def test_humidity_control_setting_requires_humidity_profile(self) -> None:
        room = RoomConfig(key="u1", name="U1", profile="ii_plain", slave=2)
        assert not _supports_control_setting(
            room,
            _control_setting_desc("humidity_starting_point"),
        )

    def test_co2_control_setting_included_for_fc_profile(self) -> None:
        room = RoomConfig(key="u1", name="U1", profile="ii_fc", slave=2)
        assert _supports_control_setting(
            room,
            _control_setting_desc("co2_starting_point"),
        )


# ---------------------------------------------------------------------------
#  Sensor _supports_profile
# ---------------------------------------------------------------------------


class TestSensorSupportsProfile:
    def test_plain_profile_relies_on_supported_keys_for_outdoor_temperature(self) -> None:
        room = RoomConfig(
            key="u1",
            name="U1",
            profile="ii_plain",
            slave=2,
            supported_entity_keys=frozenset({"exhaust_temperature"}),
        )
        assert not sensor_supports_profile(room, _sensor_desc("outdoor_air_temperature"))

    def test_plain_profile_relies_on_supported_keys_for_extract_temperature(self) -> None:
        room = RoomConfig(
            key="u1",
            name="U1",
            profile="ii_plain",
            slave=2,
            supported_entity_keys=frozenset({"exhaust_temperature"}),
        )
        assert not sensor_supports_profile(room, _sensor_desc("extract_air_temperature"))

    def test_humidity_sensor_excluded_for_plain_profile(self) -> None:
        room = RoomConfig(key="u1", name="U1", profile="ii_plain", slave=2)
        assert not sensor_supports_profile(room, _sensor_desc("humidity_extract_air"))

    def test_humidity_sensor_included_for_humidity_profile(self) -> None:
        room = RoomConfig(key="u1", name="U1", profile="ii_f", slave=2)
        assert sensor_supports_profile(room, _sensor_desc("humidity_extract_air"))

    def test_supply_air_temperature_excluded_for_plain_profile(self) -> None:
        room = RoomConfig(key="u1", name="U1", profile="ii_plain", slave=2)
        assert not sensor_supports_profile(room, _sensor_desc("supply_air_temperature"))

    def test_supply_air_temperature_included_for_f_profile(self) -> None:
        room = RoomConfig(key="u1", name="U1", profile="ii_f", slave=2)
        assert sensor_supports_profile(room, _sensor_desc("supply_air_temperature"))

    def test_co2_sensor_excluded_for_humidity_only_profile(self) -> None:
        room = RoomConfig(key="u1", name="U1", profile="ii_f", slave=2)
        assert not sensor_supports_profile(room, _sensor_desc("co2_extract_air"))

    def test_voc_sensor_included_only_for_voc_profile(self) -> None:
        room_voc = RoomConfig(key="u1", name="U1", profile="ii_fc_voc", slave=2)
        room_fc = RoomConfig(key="u2", name="U2", profile="ii_fc", slave=3)
        desc = _sensor_desc("voc_supply_air")
        assert sensor_supports_profile(room_voc, desc)
        assert not sensor_supports_profile(room_fc, desc)

    def test_supported_entity_keys_can_override_profile_match(self) -> None:
        room = RoomConfig(
            key="u1",
            name="U1",
            profile="ii_f",
            slave=2,
            supported_entity_keys=frozenset({"exhaust_temperature"}),
        )
        assert not sensor_supports_profile(
            room, _sensor_desc("humidity_extract_air")
        )
        assert sensor_supports_profile(
            room, _sensor_desc("exhaust_temperature")
        )


# ---------------------------------------------------------------------------
#  Binary sensor _supports_profile
# ---------------------------------------------------------------------------


class TestBinarySensorSupportsProfile:
    def test_all_binary_sensors_included_for_any_profile(self) -> None:
        room = RoomConfig(key="u1", name="U1", profile="s_plain", slave=2)
        for desc in BINARY_SENSOR_DESCRIPTIONS:
            assert binary_sensor_supports_profile(room, desc), (
                f"{desc.key} should be supported for s_plain"
            )

    def test_supported_entity_keys_filter_binary_sensors(self) -> None:
        room = RoomConfig(
            key="u1",
            name="U1",
            profile="ii_plain",
            slave=2,
            supported_entity_keys=frozenset({"error_status"}),
        )
        assert binary_sensor_supports_profile(room, _binary_desc("error_status"))
        assert not binary_sensor_supports_profile(
            room, _binary_desc("frost_protection_active")
        )


class TestSelectSupportsRoom:
    def test_select_allowed_without_supported_keys(self) -> None:
        room = RoomConfig(key="u1", name="U1", profile="ii_plain", slave=2)
        assert select_supports_room(room, "operation_mode")
        assert select_supports_room(room, "preset_mode")

    def test_select_filtered_by_supported_keys(self) -> None:
        room = RoomConfig(
            key="u1",
            name="U1",
            profile="ii_plain",
            slave=2,
            supported_entity_keys=frozenset({"level"}),
        )
        assert not select_supports_room(room, "operation_mode")
        assert not select_supports_room(room, "preset_mode")


# ---------------------------------------------------------------------------
#  Sensor description metadata
# ---------------------------------------------------------------------------


class TestSensorDescriptionMetadata:
    @pytest.mark.parametrize(
        "key",
        [
            "exhaust_temperature",
            "outdoor_air_temperature",
            "extract_air_temperature",
        ],
    )
    def test_temperature_sensors_have_device_class_and_state_class(
        self, key: str,
    ) -> None:
        desc = _sensor_desc(key)
        assert desc.device_class == "temperature"
        assert desc.state_class == "measurement"

    @pytest.mark.parametrize("key", ["humidity_extract_air", "humidity_supply_air"])
    def test_humidity_sensors_have_device_class(self, key: str) -> None:
        desc = _sensor_desc(key)
        assert desc.device_class == "humidity"
        assert desc.state_class == "measurement"

    def test_co2_sensor_has_device_class(self) -> None:
        desc = _sensor_desc("co2_extract_air")
        assert desc.device_class == "carbon_dioxide"
        assert desc.state_class == "measurement"

    def test_voc_sensor_has_device_class(self) -> None:
        desc = _sensor_desc("voc_supply_air")
        assert desc.device_class == "volatile_organic_compounds_parts"
        assert desc.state_class == "measurement"

    def test_operating_hours_has_duration_device_class(self) -> None:
        desc = _sensor_desc("operating_hours")
        assert desc.device_class == "duration"
        assert desc.entity_category == "diagnostic"

    @pytest.mark.parametrize("key", ["extract_air_flow", "supply_air_flow"])
    def test_airflow_sensors_have_state_class(self, key: str) -> None:
        assert _sensor_desc(key).state_class == "measurement"

    def test_removed_derived_airflow_sensors_are_absent(self) -> None:
        sensor_keys = {desc.key for desc in SENSOR_DESCRIPTIONS}
        assert "current_level" not in sensor_keys
        assert "average_air_flow" not in sensor_keys
        assert "software_version" not in sensor_keys


# ---------------------------------------------------------------------------
#  Binary sensor description metadata
# ---------------------------------------------------------------------------


class TestBinarySensorDescriptionMetadata:
    @pytest.mark.parametrize("key", ["rf_comm_status"])
    def test_diagnostic_binary_sensors_have_entity_category(
        self, key: str,
    ) -> None:
        assert _binary_desc(key).entity_category == "diagnostic"

    @pytest.mark.parametrize(
        "key", ["error_status", "frost_protection_active", "filter_change_due", "intensive_active"]
    )
    def test_operational_binary_sensors_have_no_category(
        self, key: str,
    ) -> None:
        assert _binary_desc(key).entity_category is None
