"""Verify which entities each unit profile produces and that they follow updates.

The platform setup functions are otherwise untested, so a missing entry in
BASE_SUPPORTED_ENTITY_KEYS or a wrong supported_profiles filter would go
unnoticed. The expectations follow the manufacturer sensor matrix documented in
docs/dev/reference/models.md.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import timedelta
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.const import EntityCategory, Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_fire_time_changed

from custom_components.meltem_ventilation.const import (
    CONF_PORT,
    CONF_ROOMS,
    DOMAIN,
    GATEWAY_ENTITY_PLATFORM_BY_KEY,
    MODEL_PROFILES,
    PLATFORMS,
    READ_GROUP_ENTITY_KEYS,
)
from custom_components.meltem_ventilation.modbus_helpers import (
    supported_entity_keys_for_profile,
)
from custom_components.meltem_ventilation.models import GatewayRfStatistics, ReadHealth, RoomState

type SetupProfile = Callable[..., Awaitable[str]]

_BASE_SENSORS = {
    "exhaust_temperature",
    "extract_air_flow",
    "supply_air_flow",
    "days_until_filter_change",
    "operating_hours",
    "fan_motor_hours",
    "rf_last_seen_age_seconds",
    "rf_signal_strength",
    "modbus_slave_id",
    "airflow_balance",
    "last_successful_update",
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
    "rf_fault_status",
    "value_error_status",
    "data_health",
    "connectivity",
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
def setup_profile_fixture(hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch) -> SetupProfile:
    """Set up an entry whose units all use one profile, without a gateway."""
    monkeypatch.setattr(
        "custom_components.meltem_ventilation.MeltemModbusClient.async_validate_gateway",
        AsyncMock(),
    )
    monkeypatch.setattr(
        "custom_components.meltem_ventilation.MeltemModbusClient.async_read_gateway_uptime",
        AsyncMock(return_value=123),
    )
    monkeypatch.setattr(
        "custom_components.meltem_ventilation.MeltemModbusClient.async_read_gateway_mac",
        AsyncMock(return_value="58:f3:87:78:2e:24"),
    )
    monkeypatch.setattr(
        "custom_components.meltem_ventilation.MeltemModbusClient.async_read_gateway_utc_clock",
        AsyncMock(return_value=1792580112),
    )
    monkeypatch.setattr(
        "custom_components.meltem_ventilation.MeltemModbusClient.async_read_gateway_node_count",
        AsyncMock(return_value=6),
    )
    monkeypatch.setattr(
        "custom_components.meltem_ventilation.MeltemModbusClient.async_read_gateway_rf_statistics",
        AsyncMock(return_value=GatewayRfStatistics(75, 147, 0.260866672, 0.035489265)),
    )
    monkeypatch.setattr(
        "custom_components.meltem_ventilation.coordinator."
        "MeltemDataUpdateCoordinator.async_refresh",
        AsyncMock(),
    )
    monkeypatch.setattr(
        "custom_components.meltem_ventilation.MeltemModbusClient.read_room_state",
        AsyncMock(return_value=RoomState()),
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
                        "supported_entity_keys": supported_entity_keys_for_profile(profile),
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
        "fan_hours",
        "rf_last_seen",
        "rf_signal",
        "rf_fault",
        "value_error",
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
    migrated_entry = hass.config_entries.async_get_entry(entry_id)
    assert migrated_entry is not None
    assert migrated_entry.minor_version == 3


async def test_entity_states_follow_every_coordinator_update(
    hass: HomeAssistant, setup_profile: SetupProfile
) -> None:
    """Entity state is computed on every update instead of being cached across them."""
    entry_id = await setup_profile("ii_plain")
    entry = hass.config_entries.async_get_entry(entry_id)
    assert entry is not None
    coordinator = entry.runtime_data.coordinator
    registry = er.async_get(hass)
    sensor_id = registry.async_get_entity_id(
        Platform.SENSOR, DOMAIN, f"{DOMAIN}_unit_1_exhaust_temperature"
    )
    problem_id = registry.async_get_entity_id(
        Platform.BINARY_SENSOR, DOMAIN, f"{DOMAIN}_unit_1_error_status"
    )
    assert sensor_id is not None
    assert problem_id is not None

    for temperature, error in ((20.5, False), (21.5, True)):
        read_at = dt_util.utcnow()
        health = ReadHealth(last_attempt=read_at, last_successful_read=read_at)
        state = RoomState(exhaust_temperature=temperature, error_status=error)
        for group_key in ("temperature", "status"):
            state = state.with_read_health(group_key, health)
        coordinator.async_set_updated_data({"unit_1": state})
        await hass.async_block_till_done()

        sensor_state = hass.states.get(sensor_id)
        problem_state = hass.states.get(problem_id)
        assert sensor_state is not None
        assert problem_state is not None
        assert sensor_state.state == str(temperature)
        assert problem_state.state == ("on" if error else "off")


@pytest.mark.parametrize(
    "key",
    [
        "gateway_available_units",
        "gateway_unavailable_units",
        "gateway_error_units",
        "gateway_filter_due_units",
        "gateway_consecutive_timeouts",
        "gateway_stale_read_groups",
        "gateway_link_recycles",
        "gateway_last_answer_age",
        "last_write_status",
    ],
)
async def test_removed_sensor_entities_are_deleted_on_reload(
    hass: HomeAssistant, setup_profile: SetupProfile, key: str
) -> None:
    entry_id = await setup_profile("ii_plain")
    entry = hass.config_entries.async_get_entry(entry_id)
    assert entry is not None
    registry = er.async_get(hass)
    removed = registry.async_get_or_create(
        "sensor",
        DOMAIN,
        f"{DOMAIN}_{entry_id}_{key}",
        config_entry=entry,
    )
    assert await hass.config_entries.async_reload(entry_id)
    await hass.async_block_till_done()
    assert registry.async_get(removed.entity_id) is None


@pytest.mark.parametrize("key", GATEWAY_ENTITY_PLATFORM_BY_KEY)
async def test_gateway_sensor_keeps_its_registry_entry_across_reloads(
    hass: HomeAssistant, setup_profile: SetupProfile, key: str
) -> None:
    """The registry cleanup must not recreate it, which would drop user settings."""
    entry_id = await setup_profile("ii_plain")
    registry = er.async_get(hass)
    gateway_entity = next(
        entity
        for entity in _entries(hass, entry_id)
        if entity.unique_id == f"{DOMAIN}_{entry_id}_{key}"
    )
    registry.async_update_entity(gateway_entity.entity_id, name="My diagnostic", disabled_by=None)

    assert await hass.config_entries.async_reload(entry_id)
    await hass.async_block_till_done()

    reloaded = registry.async_get(gateway_entity.entity_id)
    assert reloaded is not None
    assert reloaded.id == gateway_entity.id
    assert reloaded.name == "My diagnostic"
    assert reloaded.disabled_by is None
    assert reloaded.device_id is not None
    device = dr.async_get(hass).async_get(reloaded.device_id)
    assert device is not None
    assert (DOMAIN, entry_id) in device.identifiers


async def test_gateway_diagnostics_are_created_once_on_the_gateway(
    hass: HomeAssistant, setup_profile: SetupProfile
) -> None:
    entry_id = await setup_profile("ii_plain", room_count=2)
    prefix = f"{DOMAIN}_{entry_id}_"
    entities = [
        entity for entity in _entries(hass, entry_id) if entity.unique_id.startswith(prefix)
    ]

    assert {entity.unique_id.removeprefix(prefix) for entity in entities} == set(
        GATEWAY_ENTITY_PLATFORM_BY_KEY
    )
    assert len(entities) == len(GATEWAY_ENTITY_PLATFORM_BY_KEY)
    assert len({entity.device_id for entity in entities}) == 1
    for entity in entities:
        key = entity.unique_id.removeprefix(prefix)
        assert entity.domain == GATEWAY_ENTITY_PLATFORM_BY_KEY[key].value
        assert entity.entity_category is (
            None
            if key
            in {
                "gateway_supply_air_flow",
                "gateway_extract_air_flow",
                "gateway_airflow_delta",
            }
            else EntityCategory.DIAGNOSTIC
        )
        assert entity.disabled_by is (
            None
            if key
            in {
                "gateway_connectivity",
                "gateway_connected_devices",
                "gateway_supply_air_flow",
                "gateway_extract_air_flow",
                "gateway_airflow_delta",
            }
            else er.RegistryEntryDisabler.INTEGRATION
        )


async def test_gateway_connected_devices_activation_and_unload(
    hass: HomeAssistant, setup_profile: SetupProfile
) -> None:
    entry_id = await setup_profile("ii_plain")
    entry = hass.config_entries.async_get_entry(entry_id)
    assert entry is not None
    registry = er.async_get(hass)
    entity_id = registry.async_get_entity_id(
        Platform.SENSOR, DOMAIN, f"{DOMAIN}_{entry_id}_gateway_connected_devices"
    )
    assert entity_id is not None
    assert entry.runtime_data.coordinator._gateway_connected_devices_enabled
    registry.async_update_entity(entity_id, disabled_by=er.RegistryEntryDisabler.USER)
    assert await hass.config_entries.async_reload(entry_id)
    await hass.async_block_till_done()
    assert not entry.runtime_data.coordinator._gateway_connected_devices_enabled
    registry.async_update_entity(entity_id, disabled_by=None)
    assert await hass.config_entries.async_reload(entry_id)
    await hass.async_block_till_done()
    coordinator = entry.runtime_data.coordinator
    assert coordinator._gateway_connected_devices_enabled
    async with coordinator._gateway_lock:
        await coordinator._async_read_gateway_connected_devices_due()
    coordinator.async_update_listeners()
    await hass.async_block_till_done()
    state = hass.states.get(entity_id)
    assert state is not None
    assert state.state == "6"
    assert await hass.config_entries.async_unload(entry_id)
    assert not coordinator._gateway_connected_devices_enabled
    assert coordinator._gateway_connected_devices_task is None


@pytest.mark.parametrize(
    ("key", "expected"),
    [
        ("gateway_rf_messages_current_hour", "75"),
        ("gateway_rf_messages_previous_hour", "147"),
        ("gateway_rf_load_current_hour", "0.260866672"),
        ("gateway_rf_load_previous_hour", "0.035489265"),
    ],
)
async def test_gateway_rf_activation_updates_and_unload(
    hass: HomeAssistant, setup_profile: SetupProfile, key: str, expected: str
) -> None:
    entry_id = await setup_profile("ii_plain")
    entry = hass.config_entries.async_get_entry(entry_id)
    assert entry is not None
    assert not entry.runtime_data.coordinator._gateway_rf_statistics_consumers
    registry = er.async_get(hass)
    entity_id = registry.async_get_entity_id(Platform.SENSOR, DOMAIN, f"{DOMAIN}_{entry_id}_{key}")
    assert entity_id is not None
    registry.async_update_entity(entity_id, disabled_by=None)
    assert await hass.config_entries.async_reload(entry_id)
    await hass.async_block_till_done()
    coordinator = entry.runtime_data.coordinator
    assert coordinator._gateway_rf_statistics_consumers == {key}
    async with coordinator._gateway_lock:
        await coordinator._async_read_gateway_rf_statistics_due()
    coordinator.async_update_listeners()
    await hass.async_block_till_done()
    state = hass.states.get(entity_id)
    assert state is not None
    assert state.state == expected
    assert state.attributes["state_class"] == "measurement"
    if "load" in key:
        assert state.attributes["unit_of_measurement"] == "%"
    assert await hass.config_entries.async_unload(entry_id)
    assert not coordinator._gateway_rf_statistics_consumers
    assert coordinator._gateway_rf_statistics_task is None


@pytest.mark.parametrize("key", ["rf_fault_status", "value_error_status"])
async def test_room_rf_error_diagnostics_follow_updates(
    hass: HomeAssistant, setup_profile: SetupProfile, key: str
) -> None:
    entry_id = await setup_profile("ii_plain")
    registry = er.async_get(hass)
    entity_id = registry.async_get_entity_id(
        Platform.BINARY_SENSOR, DOMAIN, f"{DOMAIN}_unit_1_{key}"
    )
    assert entity_id is not None
    registry.async_update_entity(entity_id, disabled_by=None)
    assert await hass.config_entries.async_reload(entry_id)
    await hass.async_block_till_done()
    entry = hass.config_entries.async_get_entry(entry_id)
    assert entry is not None
    coordinator = entry.runtime_data.coordinator
    await coordinator.async_shutdown()
    now = dt_util.utcnow()
    group = "rf_fault" if key == "rf_fault_status" else "value_error"
    for value, expected in [(True, "on"), (False, "off"), (None, "unknown")]:
        state = RoomState(
            rf_fault_status=value, value_error_status=value, supply_air_flow=30
        ).with_read_health(group, ReadHealth(last_attempt=now, last_successful_read=now))
        coordinator.async_set_updated_data({"unit_1": state})
        await hass.async_block_till_done()
        entity_state = hass.states.get(entity_id)
        assert entity_state is not None
        assert entity_state.state == expected


@pytest.mark.parametrize("key", ["gateway_uptime", "gateway_utc_clock"])
async def test_optional_gateway_activation_and_unload(
    hass: HomeAssistant, setup_profile: SetupProfile, key: str
) -> None:
    entry_id = await setup_profile("ii_plain")
    entry = hass.config_entries.async_get_entry(entry_id)
    assert entry is not None
    assert not getattr(entry.runtime_data.coordinator, f"_{key}_enabled")
    registry = er.async_get(hass)
    entity_id = registry.async_get_entity_id(Platform.SENSOR, DOMAIN, f"{DOMAIN}_{entry_id}_{key}")
    assert entity_id is not None
    registry.async_update_entity(entity_id, disabled_by=None)
    assert await hass.config_entries.async_reload(entry_id)
    await hass.async_block_till_done()
    coordinator = entry.runtime_data.coordinator
    assert getattr(coordinator, f"_{key}_enabled")
    assert await hass.config_entries.async_unload(entry_id)
    assert not getattr(coordinator, f"_{key}_enabled")
    assert getattr(coordinator, f"_{key}_task") is None


@pytest.mark.parametrize(
    ("key", "group", "value", "unit"),
    [
        ("fan_motor_hours", "fan_hours", 38423, "h"),
        ("rf_last_seen_age_seconds", "rf_last_seen", 50, "s"),
        ("rf_signal_strength", "rf_signal", -43, "dBm"),
    ],
)
async def test_optional_room_diagnostics_follow_updates(
    hass: HomeAssistant,
    setup_profile: SetupProfile,
    key: str,
    group: str,
    value: int,
    unit: str,
) -> None:
    entry_id = await setup_profile("ii_plain")
    registry = er.async_get(hass)
    entity_id = registry.async_get_entity_id(Platform.SENSOR, DOMAIN, f"{DOMAIN}_unit_1_{key}")
    assert entity_id is not None
    registered = registry.async_get(entity_id)
    assert registered is not None
    assert registered.disabled_by is er.RegistryEntryDisabler.INTEGRATION
    registry.async_update_entity(entity_id, disabled_by=None)
    assert await hass.config_entries.async_reload(entry_id)
    await hass.async_block_till_done()
    entry = hass.config_entries.async_get_entry(entry_id)
    assert entry is not None
    coordinator = entry.runtime_data.coordinator
    await coordinator.async_shutdown()
    now = dt_util.utcnow()
    state = RoomState(
        fan_motor_hours=38423, rf_last_seen_age_seconds=50, rf_signal_strength=-43
    ).with_read_health(group, ReadHealth(last_attempt=now, last_successful_read=now))
    coordinator.async_set_updated_data({"unit_1": state})
    await hass.async_block_till_done()
    entity_state = hass.states.get(entity_id)
    assert entity_state is not None
    assert entity_state.state == str(value)
    assert entity_state.attributes["unit_of_measurement"] == unit


async def test_room_connectivity_updates_without_reads_and_stops_after_unload(
    hass: HomeAssistant, setup_profile: SetupProfile
) -> None:
    entry_id = await setup_profile("ii_plain")
    entry = hass.config_entries.async_get_entry(entry_id)
    assert entry is not None
    coordinator = entry.runtime_data.coordinator
    await coordinator.async_shutdown()
    coordinator.client.read_room_state.reset_mock()
    registry = er.async_get(hass)
    connectivity_id = registry.async_get_entity_id(
        Platform.BINARY_SENSOR, DOMAIN, f"{DOMAIN}_unit_1_connectivity"
    )
    assert connectivity_id is not None
    now = dt_util.utcnow()
    state = RoomState(target_level=30).with_read_health(
        "flow", ReadHealth(last_attempt=now, last_successful_read=now)
    )
    coordinator.async_set_updated_data({"unit_1": state})
    await hass.async_block_till_done()
    connectivity_state = hass.states.get(connectivity_id)
    assert connectivity_state is not None
    assert connectivity_state.state == "on"

    coordinator._room_failures["unit_1"] = 3
    async_fire_time_changed(hass, now + timedelta(seconds=31))
    await hass.async_block_till_done()
    connectivity_state = hass.states.get(connectivity_id)
    assert connectivity_state is not None
    assert connectivity_state.state == "off"
    coordinator.client.read_room_state.assert_not_awaited()
    assert await hass.config_entries.async_unload(entry_id)
    with patch.object(coordinator, "room_available") as availability:
        async_fire_time_changed(hass, now + timedelta(seconds=42))
        await hass.async_block_till_done()
        availability.assert_not_called()


async def test_gateway_answer_timestamp_survives_outage_without_reads(
    hass: HomeAssistant, setup_profile: SetupProfile
) -> None:
    entry_id = await setup_profile("ii_plain")
    registry = er.async_get(hass)
    answer_id = registry.async_get_entity_id(
        Platform.SENSOR, DOMAIN, f"{DOMAIN}_{entry_id}_gateway_last_answer"
    )
    connectivity_id = registry.async_get_entity_id(
        Platform.BINARY_SENSOR, DOMAIN, f"{DOMAIN}_{entry_id}_gateway_connectivity"
    )
    assert answer_id is not None
    assert connectivity_id is not None
    registry.async_update_entity(answer_id, disabled_by=None)
    assert await hass.config_entries.async_reload(entry_id)
    await hass.async_block_till_done()
    entry = hass.config_entries.async_get_entry(entry_id)
    assert entry is not None
    coordinator = entry.runtime_data.coordinator
    await coordinator.async_shutdown()
    coordinator.client.read_room_state.reset_mock()
    coordinator.last_update_success = False

    now = dt_util.utcnow()
    with patch.object(
        coordinator.client,
        "transport_diagnostics",
        return_value={"seconds_since_any_answer": 0.0, "last_answer_timestamp": now.timestamp()},
    ) as diagnostics:
        coordinator.async_update_listeners()
        await hass.async_block_till_done()
        connectivity_state = hass.states.get(connectivity_id)
        answer_state = hass.states.get(answer_id)
        assert connectivity_state is not None
        assert answer_state is not None
        assert connectivity_state.state == "on"
        assert answer_state.state == now.replace(microsecond=0).isoformat()

        diagnostics.return_value = {
            "seconds_since_any_answer": 31.0,
            "last_answer_timestamp": now.timestamp(),
        }
        async_fire_time_changed(hass, now + timedelta(seconds=11))
        await hass.async_block_till_done()

        connectivity_state = hass.states.get(connectivity_id)
        answer_state = hass.states.get(answer_id)
        assert connectivity_state is not None
        assert answer_state is not None
        assert connectivity_state.state == "off"
        assert answer_state.state == now.replace(microsecond=0).isoformat()
        coordinator.client.read_room_state.assert_not_awaited()

        assert await hass.config_entries.async_unload(entry_id)
        diagnostics.reset_mock()
        async_fire_time_changed(hass, now + timedelta(seconds=22))
        await hass.async_block_till_done()
        diagnostics.assert_not_called()
