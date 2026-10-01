"""Set up the Meltem Modbus integration entry and runtime objects.

This module keeps the config-entry setup path intentionally small:
- normalize the selected serial port
- derive each configured room's entities from its profile
- create one shared Modbus client and one shared coordinator

All actual Modbus traffic stays in ``modbus_client.py`` and all polling
decisions stay in ``coordinator.py``.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er

from .const import (
    CO2_PROFILES,
    CONF_MAX_REQUESTS_PER_SECOND,
    CONF_PORT,
    CONF_ROOMS,
    DEFAULT_MAX_REQUESTS_PER_SECOND,
    DOMAIN,
    ENTITY_PLATFORM_BY_KEY,
    FIXED_BAUDRATE,
    FIXED_BYTESIZE,
    FIXED_PARITY,
    FIXED_STOPBITS,
    FIXED_TIMEOUT,
    GATEWAY_NAME,
    HUMIDITY_PROFILES,
    PLATFORMS,
)
from .coordinator import MeltemDataUpdateCoordinator
from .modbus_client import MeltemModbusClient
from .modbus_helpers import (
    MeltemModbusError,
    SerialSettings,
    resolve_preferred_port_path,
    supported_entity_keys_for_profile,
)
from .models import MeltemRuntimeData, RoomConfig

_LOGGER = logging.getLogger(__name__)


async def async_migrate_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Migrate a config entry from an older schema."""

    if entry.version > 1:
        return False

    if entry.minor_version < 2:
        _async_migrate_data_health_entities(hass, entry)
        hass.config_entries.async_update_entry(entry, minor_version=2)

    return True


def _async_migrate_data_health_entities(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Rename the airflow-only diagnostic while preserving registry settings."""

    registry = er.async_get(hass)
    entry_entities = {
        entity.unique_id: entity
        for entity in er.async_entries_for_config_entry(registry, entry.entry_id)
    }
    for room in entry.data[CONF_ROOMS]:
        old_entity = entry_entities.get(f"{DOMAIN}_{room['key']}_airflow_data_stale")
        new_unique_id = f"{DOMAIN}_{room['key']}_data_health"
        if old_entity is None or new_unique_id in entry_entities:
            continue
        registry.async_update_entity(old_entity.entity_id, new_unique_id=new_unique_id)
        entry_entities[new_unique_id] = old_entity


def _room_entity_keys(room: Mapping[str, Any]) -> frozenset[str]:
    """Return the stored entity keys plus everything the room's profile implies.

    Deriving the profile part on load keeps older entries complete when a
    release adds entities, without probing the gateway or rewriting the entry.
    """

    return frozenset(room.get("supported_entity_keys", ())) | frozenset(
        supported_entity_keys_for_profile(str(room["profile"]))
    )


def _async_remove_unsupported_entities(
    hass: HomeAssistant, entry: ConfigEntry, rooms: list[RoomConfig]
) -> None:
    """Drop registry entries that no configured room/profile creates anymore."""

    registry = er.async_get(hass)
    expected: dict[str, str] = {}
    for room in rooms:
        profile_keys = set(supported_entity_keys_for_profile(room.profile))
        supported_keys = set(room.supported_entity_keys or profile_keys) & profile_keys
        if room.profile not in HUMIDITY_PROFILES | CO2_PROFILES:
            supported_keys.discard("operation_mode")
        for object_key in supported_keys:
            if platform := ENTITY_PLATFORM_BY_KEY.get(object_key):
                expected[f"{DOMAIN}_{room.key}_{object_key}"] = platform.value
        if {"extract_air_flow", "supply_air_flow"} & supported_keys:
            expected[f"{DOMAIN}_{room.key}_data_health"] = (
                ENTITY_PLATFORM_BY_KEY["data_health"].value
            )
    # Gateway-level entity, not tied to any room.
    expected[f"{DOMAIN}_{entry.entry_id}_modbus_device_path"] = Platform.SENSOR.value

    for existing in list(registry.entities.values()):
        if existing.config_entry_id != entry.entry_id:
            continue
        unique_id = existing.unique_id
        if not unique_id.startswith(f"{DOMAIN}_"):
            continue
        expected_domain = expected.get(unique_id)
        actual_domain = existing.entity_id.partition(".")[0]
        if expected_domain == actual_domain:
            continue
        _LOGGER.info("Removing unsupported Meltem entity %s", existing.entity_id)
        registry.async_remove(existing.entity_id)


def _async_sync_devices(
    hass: HomeAssistant, entry: ConfigEntry, rooms: list[RoomConfig]
) -> None:
    """Register the gateway device and drop devices of units no longer configured."""

    registry = dr.async_get(hass)
    gateway_identifier = (DOMAIN, entry.entry_id)
    # Unit devices reference the gateway via ``via_device``, so it must exist first.
    registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={gateway_identifier},
        manufacturer="Meltem",
        model="M-WRG-GW",
        name=GATEWAY_NAME,
    )
    configured = {(DOMAIN, room.key) for room in rooms} | {gateway_identifier}
    for device in dr.async_entries_for_config_entry(registry, entry.entry_id):
        if device.identifiers & configured:
            continue
        _LOGGER.info("Removing device of unconfigured Meltem unit %s", device.name)
        registry.async_update_device(device.id, remove_config_entry_id=entry.entry_id)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Meltem Modbus from a config entry."""

    # Resolution walks /dev/serial/by-id, so it must not run in the event loop.
    normalized_port = await hass.async_add_executor_job(
        resolve_preferred_port_path, entry.data[CONF_PORT]
    )
    # A by-id link can appear after setup, so this is a runtime fix-up, not a migration.
    if normalized_port != entry.data[CONF_PORT] or entry.unique_id != normalized_port:
        hass.config_entries.async_update_entry(
            entry,
            data={**entry.data, CONF_PORT: normalized_port},
            unique_id=normalized_port,
        )

    settings = SerialSettings(
        port=normalized_port,
        baudrate=FIXED_BAUDRATE,
        bytesize=FIXED_BYTESIZE,
        parity=FIXED_PARITY,
        stopbits=FIXED_STOPBITS,
        timeout=float(FIXED_TIMEOUT),
    )
    rooms = [
        RoomConfig(
            key=room["key"],
            name=room["name"],
            profile=room["profile"],
            slave=int(room["slave"]),
            preview=room.get("preview"),
            supported_entity_keys=_room_entity_keys(room),
        )
        for room in entry.data[CONF_ROOMS]
    ]
    max_requests_per_second = float(
        entry.options.get(
            CONF_MAX_REQUESTS_PER_SECOND,
            entry.data.get(
                CONF_MAX_REQUESTS_PER_SECOND,
                DEFAULT_MAX_REQUESTS_PER_SECOND,
            ),
        )
    )
    _LOGGER.info(
        "Using Meltem max request rate of %.1f req/s for %s configured unit(s)",
        max_requests_per_second,
        len(rooms),
    )

    _async_remove_unsupported_entities(hass, entry, rooms)
    _async_sync_devices(hass, entry, rooms)

    # All entities for one config entry share one serial client so the gateway
    # only ever sees one active connection from Home Assistant.
    client = MeltemModbusClient(settings)
    try:
        await hass.async_add_executor_job(client.ensure_connected)

        coordinator = MeltemDataUpdateCoordinator(
            hass,
            config_entry=entry,
            client=client,
            rooms=rooms,
            max_requests_per_second=max_requests_per_second,
        )
        entry.runtime_data = MeltemRuntimeData(coordinator=coordinator)
        await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    except MeltemModbusError as err:
        # The serial port stays locked otherwise and reloads would fail.
        await hass.async_add_executor_job(client.shutdown)
        if hasattr(entry, "runtime_data"):
            object.__delattr__(entry, "runtime_data")
        raise ConfigEntryNotReady(str(err)) from err
    except Exception:
        await hass.async_add_executor_job(client.shutdown)
        if hasattr(entry, "runtime_data"):
            object.__delattr__(entry, "runtime_data")
        raise

    entry.async_create_background_task(
        hass, coordinator.async_refresh(), "meltem_first_refresh"
    )
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""

    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        runtime_data: MeltemRuntimeData = entry.runtime_data
        await runtime_data.coordinator.async_shutdown()
        # A pending write readback must not reopen the port the next entry needs.
        await hass.async_add_executor_job(runtime_data.coordinator.client.shutdown)

    return unload_ok
