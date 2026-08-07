"""Verify which entities each unit profile produces.

The platform setup functions are otherwise untested, so a missing entry in
BASE_SUPPORTED_ENTITY_KEYS or a wrong supported_profiles filter would go
unnoticed. The expectations follow the manufacturer sensor matrix documented in
docs/MELTEM.md.
"""

from __future__ import annotations

import pytest
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.meltem_ventilation.const import (
    CONF_PORT,
    CONF_ROOMS,
    DOMAIN,
    MODEL_PROFILES,
)
from custom_components.meltem_ventilation.modbus_helpers import (
    supported_entity_keys_for_profile,
)

_BASE_SENSORS = {
    "exhaust_temperature",
    "extract_air_flow",
    "supply_air_flow",
    "days_until_filter_change",
    "operating_hours",
}
# The -F variant adds all remaining temperatures together with humidity.
_HUMIDITY_SENSORS = {
    "outdoor_air_temperature",
    "extract_air_temperature",
    "supply_air_temperature",
    "humidity_extract_air",
    "humidity_supply_air",
}
_CO2_SENSORS = {"co2_extract_air"}
_VOC_SENSORS = {"voc_supply_air"}

_BASE_BINARY_SENSORS = {
    "error_status",
    "frost_protection_active",
    "filter_change_due",
    "rf_comm_status",
}

_HUMIDITY_NUMBERS = {
    "humidity_starting_point",
    "humidity_min_level",
    "humidity_max_level",
}
_CO2_NUMBERS = {"co2_starting_point", "co2_min_level", "co2_max_level"}

_PROFILE_CAPABILITIES = {
    "s_plain": set(),
    "s_f": {"humidity"},
    "s_fc": {"humidity", "co2"},
    "ii_plain": set(),
    "ii_f": {"humidity"},
    "ii_fc": {"humidity", "co2"},
    "ii_fc_voc": {"humidity", "co2", "voc"},
}


def _expected(profile: str) -> dict[Platform, set[str]]:
    capabilities = _PROFILE_CAPABILITIES[profile]

    sensors = set(_BASE_SENSORS)
    numbers: set[str] = set()
    if "humidity" in capabilities:
        sensors |= _HUMIDITY_SENSORS
        numbers |= _HUMIDITY_NUMBERS
    if "co2" in capabilities:
        sensors |= _CO2_SENSORS
        numbers |= _CO2_NUMBERS
    if "voc" in capabilities:
        sensors |= _VOC_SENSORS

    selects = {"preset_mode"}
    if capabilities & {"humidity", "co2"}:
        selects.add("operation_mode")

    return {
        Platform.SENSOR: sensors,
        Platform.BINARY_SENSOR: set(_BASE_BINARY_SENSORS),
        Platform.NUMBER: numbers,
        Platform.SELECT: selects,
        Platform.FAN: {"supply_level", "extract_level"},
        Platform.SWITCH: {"intensive"},
    }


@pytest.fixture(name="setup_profile")
def setup_profile_fixture(hass: HomeAssistant):
    async def _setup(profile: str) -> dict[Platform, set[str]]:
        entry = MockConfigEntry(
            domain=DOMAIN,
            title="Meltem",
            data={
                CONF_PORT: "/dev/ttyACM0",
                CONF_ROOMS: [
                    {
                        "key": "unit_1",
                        "name": "Unit 1",
                        "slave": 2,
                        "profile": profile,
                        "preview": "ID 1 | basic",
                        "supported_entity_keys": supported_entity_keys_for_profile(
                            profile
                        ),
                    }
                ],
            },
            version=1,
            source="user",
        )
        entry.add_to_hass(hass)

        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        registry = er.async_get(hass)
        created: dict[Platform, set[str]] = {}
        prefix = f"{DOMAIN}_unit_1_"
        for platform in _expected(profile):
            created[platform] = {
                entity.unique_id.removeprefix(prefix)
                for entity in registry.entities.values()
                if entity.config_entry_id == entry.entry_id
                and entity.domain == platform
            }
        return created

    return _setup


@pytest.mark.parametrize("profile", MODEL_PROFILES)
async def test_profile_creates_the_expected_entities(
    hass: HomeAssistant, setup_profile, profile: str
) -> None:
    with_serial_stubs = pytest.MonkeyPatch()
    with_serial_stubs.setattr(
        "custom_components.meltem_ventilation.MeltemModbusClient.ensure_connected",
        lambda self: None,
    )
    with_serial_stubs.setattr(
        "custom_components.meltem_ventilation.coordinator."
        "MeltemDataUpdateCoordinator.async_refresh",
        _noop_refresh,
    )
    try:
        created = await setup_profile(profile)
    finally:
        with_serial_stubs.undo()

    assert created == _expected(profile)


async def _noop_refresh(self) -> None:
    return None
