"""Verify which entities each unit profile produces.

The platform setup functions are otherwise untested, so a missing entry in
BASE_SUPPORTED_ENTITY_KEYS or a wrong supported_profiles filter would go
unnoticed. The expectations follow the manufacturer sensor matrix documented in
docs/reference/models.md.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from unittest.mock import AsyncMock

import pytest
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.meltem_ventilation.const import (
    CONF_PORT,
    CONF_ROOMS,
    DOMAIN,
    MODEL_PROFILES,
    PLATFORMS,
    READ_GROUP_ENTITY_KEYS,
)
from custom_components.meltem_ventilation.modbus_helpers import (
    supported_entity_keys_for_profile,
)

type SetupProfile = Callable[..., Awaitable[str]]

_BASE_SENSORS = {
    "exhaust_temperature",
    "extract_air_flow",
    "supply_air_flow",
    "days_until_filter_change",
    "operating_hours",
    "modbus_slave_id",
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
    "data_health",
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


def _entries(hass: HomeAssistant, entry_id: str) -> list[er.RegistryEntry]:
    return er.async_entries_for_config_entry(er.async_get(hass), entry_id)


def _unit_entity_keys(hass: HomeAssistant, entry_id: str) -> dict[Platform, set[str]]:
    """Return the entity keys of unit_1, grouped by platform."""
    prefix = f"{DOMAIN}_unit_1_"
    created: dict[Platform, set[str]] = {platform: set() for platform in PLATFORMS}
    for entity in _entries(hass, entry_id):
        if entity.unique_id.startswith(prefix):
            created[Platform(entity.domain)].add(entity.unique_id.removeprefix(prefix))
    return created


@pytest.fixture(name="setup_profile")
def setup_profile_fixture(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> SetupProfile:
    """Set up an entry whose units all use one profile, without a gateway."""
    monkeypatch.setattr(
        "custom_components.meltem_ventilation.MeltemModbusClient.async_validate_gateway",
        AsyncMock(),
    )
    monkeypatch.setattr(
        "custom_components.meltem_ventilation.coordinator."
        "MeltemDataUpdateCoordinator.async_refresh",
        AsyncMock(),
    )

    async def _setup(profile: str, room_count: int = 1) -> str:
        entry = MockConfigEntry(
            domain=DOMAIN,
            title="Meltem",
            data={
                CONF_PORT: "/dev/ttyACM0",
                CONF_ROOMS: [
                    {
                        "key": f"unit_{room_number}",
                        "name": f"Unit {room_number}",
                        "slave": room_number + 1,
                        "profile": profile,
                        "preview": "ID 1 | basic",
                        "supported_entity_keys": supported_entity_keys_for_profile(
                            profile
                        ),
                    }
                    for room_number in range(1, room_count + 1)
                ],
            },
            version=1,
            source="user",
        )
        entry.add_to_hass(hass)

        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        return entry.entry_id

    return _setup


@pytest.mark.parametrize("profile", MODEL_PROFILES)
async def test_profile_creates_the_expected_entities(
    hass: HomeAssistant, setup_profile: SetupProfile, profile: str
) -> None:
    entry_id = await setup_profile(profile)

    assert _unit_entity_keys(hass, entry_id) == _expected(profile)


@pytest.mark.parametrize("profile", MODEL_PROFILES)
def test_profile_has_only_supported_read_health_groups(profile: str) -> None:
    supported_entities = set(supported_entity_keys_for_profile(profile))
    actual_groups = {
        group_key
        for group_key, entity_keys in READ_GROUP_ENTITY_KEYS.items()
        if supported_entities & entity_keys
    }
    expected_groups = {
        "flow",
        "flow_control",
        "intensive",
        "status",
        "temperature",
        "filter",
        "hours",
    }
    if _PROFILE_CAPABILITIES[profile] & {"humidity", "co2"}:
        expected_groups.add("control_settings")

    assert actual_groups == expected_groups


async def test_diagnostic_connection_entities_are_created_once_and_disabled(
    hass: HomeAssistant, setup_profile: SetupProfile
) -> None:
    entry_id = await setup_profile("ii_plain", room_count=2)

    entities = _entries(hass, entry_id)
    slave_entities = [e for e in entities if e.unique_id.endswith("_modbus_slave_id")]
    path_entities = [e for e in entities if e.unique_id.endswith("_modbus_device_path")]

    assert len(slave_entities) == 2
    assert len(path_entities) == 1
    assert all(
        entity.disabled_by is er.RegistryEntryDisabler.INTEGRATION
        for entity in (*slave_entities, *path_entities)
    )


async def test_unit_devices_hang_off_the_gateway_device(
    hass: HomeAssistant, setup_profile: SetupProfile
) -> None:
    entry_id = await setup_profile("ii_plain", room_count=2)

    registry = dr.async_get(hass)
    gateway = registry.async_get_device_by_identifier((DOMAIN, entry_id), entry_id)
    assert gateway is not None
    units = [
        device
        for device in dr.async_entries_for_config_entry(registry, entry_id)
        if device.id != gateway.id
    ]
    assert len(units) == 2
    assert all(device.via_device_id == gateway.id for device in units)
    # The entry was created as 1.1 and migrated on setup.
    assert hass.config_entries.async_get_entry(entry_id).minor_version == 2


async def test_device_path_sensor_keeps_its_registry_entry_across_reloads(
    hass: HomeAssistant, setup_profile: SetupProfile
) -> None:
    """The registry cleanup must not recreate it, which would drop user settings."""
    entry_id = await setup_profile("ii_plain")
    registry = er.async_get(hass)
    path_entity = next(
        entity
        for entity in _entries(hass, entry_id)
        if entity.unique_id.endswith("_modbus_device_path")
    )
    registry.async_update_entity(path_entity.entity_id, name="Gateway port")

    assert await hass.config_entries.async_reload(entry_id)
    await hass.async_block_till_done()

    reloaded = registry.async_get(path_entity.entity_id)
    assert reloaded is not None
    assert reloaded.id == path_entity.id
    assert reloaded.name == "Gateway port"
    device = dr.async_get(hass).async_get(reloaded.device_id)
    assert (DOMAIN, entry_id) in device.identifiers
