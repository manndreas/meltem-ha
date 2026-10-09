"""Tests for entity descriptions, filter logic, and metadata correctness."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
import yaml
from homeassistant.components.binary_sensor import BinarySensorDeviceClass
from homeassistant.const import STATE_OFF, STATE_ON, UnitOfRatio
from homeassistant.core import HomeAssistant, State
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.condition import ConditionConfig
from homeassistant.helpers.trigger import TriggerConfig

from custom_components.meltem_ventilation.automation import filter_change_due_entities
from custom_components.meltem_ventilation.binary_sensor import (
    BINARY_SENSOR_DESCRIPTIONS,
)
from custom_components.meltem_ventilation.condition import (
    FilterChangeDueCondition,
    async_get_conditions,
)
from custom_components.meltem_ventilation.const import (
    ALL_PROFILES,
    DOMAIN,
    OPERATION_MODE_INACTIVE,
    PRESET_MODE_OPTIONS,
    SENSOR_OPERATION_MODES,
)
from custom_components.meltem_ventilation.entity import room_supports_entity
from custom_components.meltem_ventilation.models import RoomConfig
from custom_components.meltem_ventilation.number import (
    CONTROL_SETTING_DESCRIPTIONS,
)
from custom_components.meltem_ventilation.sensor import (
    MODBUS_DEVICE_PATH_DESCRIPTION,
    MODBUS_SLAVE_ID_DESCRIPTION,
    SENSOR_DESCRIPTIONS,
)
from custom_components.meltem_ventilation.trigger import (
    FilterChangeDueTrigger,
    async_get_triggers,
)

# ---------------------------------------------------------------------------
#  Helpers
# ---------------------------------------------------------------------------

_COMPONENT_DIR = Path(__file__).parent.parent / "custom_components" / "meltem_ventilation"
_STRINGS = _COMPONENT_DIR / "strings.json"

_ALL_DESCRIPTIONS = (
    *SENSOR_DESCRIPTIONS,
    *BINARY_SENSOR_DESCRIPTIONS,
    *CONTROL_SETTING_DESCRIPTIONS,
)
_DESCRIPTIONS = {description.key: description for description in _ALL_DESCRIPTIONS}


def _keys(descriptions) -> set[str]:
    return {description.key for description in descriptions}


def _room(profile: str = "ii_plain", supported: set[str] | None = None) -> RoomConfig:
    return RoomConfig(
        key="u1",
        name="U1",
        profile=profile,
        slave=2,
        supported_entity_keys=None if supported is None else frozenset(supported),
    )


def _supports(room: RoomConfig, key: str) -> bool:
    """Return whether the entity with this description key exists for the room."""
    return room_supports_entity(room, key, _DESCRIPTIONS[key].supported_profiles)


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _flatten_keys(payload: object, prefix: str = "") -> set[str]:
    keys: set[str] = set()
    if isinstance(payload, dict):
        for key, value in payload.items():
            path = f"{prefix}/{key}"
            keys.add(path)
            keys |= _flatten_keys(value, path)
    return keys


# ---------------------------------------------------------------------------
#  Entity filter
# ---------------------------------------------------------------------------


class TestRoomSupportsEntity:
    @pytest.mark.parametrize(
        ("profile", "key", "expected"),
        [
            ("ii_plain", "humidity_starting_point", False),
            ("ii_fc", "co2_starting_point", True),
            ("ii_plain", "humidity_extract_air", False),
            ("ii_f", "humidity_extract_air", True),
            ("ii_plain", "supply_air_temperature", False),
            ("ii_f", "supply_air_temperature", True),
            ("ii_f", "co2_extract_air", False),
            ("ii_fc", "voc_supply_air", False),
            ("ii_fc_voc", "voc_supply_air", True),
        ],
    )
    def test_profile_limits_hardware_specific_entities(
        self, profile: str, key: str, expected: bool,
    ) -> None:
        assert _supports(_room(profile), key) is expected

    @pytest.mark.parametrize(
        ("profile", "supported", "key", "expected"),
        [
            ("ii_plain", {"exhaust_temperature"}, "outdoor_air_temperature", False),
            ("ii_plain", {"exhaust_temperature"}, "extract_air_temperature", False),
            ("ii_f", {"exhaust_temperature"}, "humidity_extract_air", False),
            ("ii_f", {"exhaust_temperature"}, "exhaust_temperature", True),
            ("ii_plain", {"error_status"}, "error_status", True),
            ("ii_plain", {"error_status"}, "frost_protection_active", False),
        ],
    )
    def test_probed_keys_override_the_profile_match(
        self, profile: str, supported: set[str], key: str, expected: bool,
    ) -> None:
        assert _supports(_room(profile, supported), key) is expected

    @pytest.mark.parametrize(
        ("supported", "key", "expected"),
        [
            (None, "level", True),
            (None, "humidity_starting_point", True),
            (None, "operation_mode", True),
            (None, "preset_mode", True),
            ({"level", "co2_min_level"}, "level", True),
            ({"level", "co2_min_level"}, "co2_min_level", True),
            ({"level", "co2_min_level"}, "humidity_starting_point", False),
            ({"level"}, "operation_mode", False),
            ({"level"}, "preset_mode", False),
            (set(), "level", False),
            (set(), "humidity_starting_point", False),
        ],
    )
    def test_supported_entity_keys_filter_by_key(
        self, supported: set[str] | None, key: str, expected: bool,
    ) -> None:
        assert room_supports_entity(_room(supported=supported), key) is expected

    @pytest.mark.parametrize("profile", sorted(ALL_PROFILES))
    def test_binary_sensors_apply_to_every_profile(self, profile: str) -> None:
        room = _room(profile)

        assert all(_supports(room, key) for key in _keys(BINARY_SENSOR_DESCRIPTIONS))


# ---------------------------------------------------------------------------
#  Description metadata
# ---------------------------------------------------------------------------


class TestDescriptionMetadata:
    def test_description_keys_are_unique_across_platforms(self) -> None:
        assert len(_DESCRIPTIONS) == len(_ALL_DESCRIPTIONS)

    @pytest.mark.parametrize(
        ("key", "minimum", "maximum", "step"),
        [
            ("humidity_starting_point", 40, 80, 1),
            ("humidity_min_level", 0, 100, 10),
            ("humidity_max_level", 10, 100, 10),
            ("co2_starting_point", 500, 1200, 1),
            ("co2_min_level", 0, 100, 10),
            ("co2_max_level", 10, 100, 10),
        ],
    )
    def test_control_setting_limits_match_the_manufacturer_table(
        self, key: str, minimum: int, maximum: int, step: int,
    ) -> None:
        description = _DESCRIPTIONS[key]

        assert description.native_min_value == minimum
        assert description.native_max_value == maximum
        assert description.native_step == step

    @pytest.mark.parametrize(
        ("key", "device_class", "state_class"),
        [
            ("exhaust_temperature", "temperature", "measurement"),
            ("outdoor_air_temperature", "temperature", "measurement"),
            ("extract_air_temperature", "temperature", "measurement"),
            ("humidity_extract_air", "humidity", "measurement"),
            ("humidity_supply_air", "humidity", "measurement"),
            ("co2_extract_air", "carbon_dioxide", "measurement"),
            ("voc_supply_air", "volatile_organic_compounds_parts", "measurement"),
            ("extract_air_flow", "volume_flow_rate", "measurement"),
            ("supply_air_flow", "volume_flow_rate", "measurement"),
            ("days_until_filter_change", "duration", "measurement"),
            ("operating_hours", "duration", "total_increasing"),
        ],
    )
    def test_sensor_device_and_state_class(
        self, key: str, device_class: str, state_class: str,
    ) -> None:
        description = _DESCRIPTIONS[key]

        assert description.device_class == device_class
        assert description.state_class == state_class

    def test_days_until_filter_change_counts_days(self) -> None:
        assert _DESCRIPTIONS["days_until_filter_change"].native_unit_of_measurement == "d"

    def test_concentration_entities_use_ratio_unit(self) -> None:
        for key in ("co2_extract_air", "voc_supply_air", "co2_starting_point"):
            assert _DESCRIPTIONS[key].native_unit_of_measurement == UnitOfRatio.PARTS_PER_MILLION

    @pytest.mark.parametrize(
        ("key", "entity_category"),
        [
            ("operating_hours", "diagnostic"),
            ("rf_comm_status", "diagnostic"),
            ("error_status", None),
            ("frost_protection_active", None),
            ("filter_change_due", None),
        ],
    )
    def test_entity_category(self, key: str, entity_category: str | None) -> None:
        assert _DESCRIPTIONS[key].entity_category == entity_category

    def test_removed_sensors_are_absent(self) -> None:
        assert not {"current_level", "average_air_flow", "software_version"} & set(_DESCRIPTIONS)


class TestPurposeSpecificAutomation:
    async def test_filter_change_platforms_register_the_expected_key(
        self, hass
    ) -> None:
        assert set(await async_get_triggers(hass)) == {"filter_change_due"}
        assert set(await async_get_conditions(hass)) == {"filter_change_due"}

    @pytest.mark.parametrize("filename", ["conditions.yaml", "triggers.yaml"])
    def test_filter_change_targets_only_integration_devices(self, filename: str) -> None:
        config = yaml.safe_load((_COMPONENT_DIR / filename).read_text(encoding="utf-8"))

        assert config["filter_change_due"]["target"] == {"device": [{"integration": DOMAIN}]}

    def test_trigger_and_condition_match_filter_due_state(self) -> None:
        hass = MagicMock()
        entity_id = "binary_sensor.living_room_filter_change_due"
        target = {"entity_id": [entity_id]}
        trigger = FilterChangeDueTrigger(
            hass, TriggerConfig(key=f"{DOMAIN}.filter_change_due", target=target, options={})
        )
        condition = FilterChangeDueCondition(
            hass, ConditionConfig(target=target, options={"behavior": "any"})
        )
        state_on = State(entity_id, STATE_ON, {"device_class": "problem"})
        state_off = State(entity_id, STATE_OFF, {"device_class": "problem"})

        def ignore_reason(_reason: str, **_data: object) -> None:
            return None

        assert trigger.is_valid_state(state_on, ignore_reason)
        assert not trigger.is_valid_state(state_off, ignore_reason)
        assert trigger.is_valid_transition(state_off, state_on)
        assert not trigger.is_valid_transition(state_on, state_on)
        assert condition.is_valid_state(state_on)
        assert not condition.is_valid_state(state_off)

    async def test_trigger_and_condition_target_only_the_filter_sensor(
        self, hass: HomeAssistant
    ) -> None:
        registry = er.async_get(hass)
        filter_due, error = (
            registry.async_get_or_create(
                "binary_sensor",
                DOMAIN,
                f"{DOMAIN}_unit_1_{key}",
                original_device_class=BinarySensorDeviceClass.PROBLEM,
            ).entity_id
            for key in ("filter_change_due", "error_status")
        )
        entities = {filter_due, error, "sensor.living_room_humidity"}
        target = {"entity_id": sorted(entities)}
        trigger = FilterChangeDueTrigger(
            hass, TriggerConfig(key=f"{DOMAIN}.filter_change_due", target=target, options={})
        )
        condition = FilterChangeDueCondition(
            hass, ConditionConfig(target=target, options={"behavior": "any"})
        )

        assert trigger.entity_filter(entities) == {filter_due}
        assert condition.entity_filter(entities) == {filter_due}

    def test_filter_target_excludes_other_problem_sensors(self) -> None:
        entries = {
            "binary_sensor.filter_due": SimpleNamespace(
                platform=DOMAIN, unique_id=f"{DOMAIN}_unit_1_filter_change_due"
            ),
            "binary_sensor.error": SimpleNamespace(
                platform=DOMAIN, unique_id=f"{DOMAIN}_unit_1_error_status"
            ),
            "binary_sensor.foreign": SimpleNamespace(
                platform="other_integration", unique_id="device_filter_change_due"
            ),
        }
        registry = MagicMock()
        registry.async_get.side_effect = entries.get

        with patch(
            "custom_components.meltem_ventilation.automation.er.async_get",
            return_value=registry,
        ):
            entities = filter_change_due_entities(object(), set(entries))

        assert entities == {"binary_sensor.filter_due"}


# ---------------------------------------------------------------------------
#  Translations
# ---------------------------------------------------------------------------


class TestTranslations:
    # en.json must exist because HA core does not compile strings.json for custom integrations.
    @pytest.mark.parametrize("language", ["en", "de"])
    def test_translation_files_match_strings_json(self, language: str) -> None:
        translations = _load_json(_COMPONENT_DIR / "translations" / f"{language}.json")

        assert _flatten_keys(translations) == _flatten_keys(_load_json(_STRINGS))

    def test_translations_cover_all_entity_keys(self) -> None:
        strings = _load_json(_STRINGS)["entity"]

        assert set(strings["sensor"]) == {
            *_keys(SENSOR_DESCRIPTIONS),
            MODBUS_DEVICE_PATH_DESCRIPTION.key,
            MODBUS_SLAVE_ID_DESCRIPTION.key,
        }
        assert set(strings["binary_sensor"]) == {
            *_keys(BINARY_SENSOR_DESCRIPTIONS),
            "data_health",
        }
        assert set(strings["number"]) == _keys(CONTROL_SETTING_DESCRIPTIONS)
        assert set(strings["fan"]) == {"supply_level", "extract_level"}
        assert set(strings["select"]) == {"operation_mode", "preset_mode"}
        assert set(strings["switch"]) == {"intensive"}

    def test_select_option_translations_match_the_offered_options(self) -> None:
        strings = _load_json(_STRINGS)["entity"]["select"]

        assert set(strings["preset_mode"]["state"]) == set(PRESET_MODE_OPTIONS)
        assert set(strings["operation_mode"]["state"]) == {
            OPERATION_MODE_INACTIVE,
            *SENSOR_OPERATION_MODES,
        }
