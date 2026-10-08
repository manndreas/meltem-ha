"""Set up the Meltem Modbus integration entry and runtime objects.

This module keeps the config-entry setup path intentionally small:
- normalize the selected serial port
- derive each configured room's entities from its profile
- create one shared Modbus client and one shared coordinator

The serial link itself belongs to Home Assistant's ``modbus`` integration,
which hands out units over a connection shared with any other integration.
All actual Modbus traffic stays in ``modbus_client.py`` and all polling
decisions stay in ``coordinator.py``.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from functools import partial
from typing import Any

from homeassistant.components.modbus import async_get_unit
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import (
    ConfigEntryError,
    ConfigEntryNotReady,
    HomeAssistantError,
)
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er

from .const import (
    CONF_MAX_REQUESTS_PER_SECOND,
    CONF_PORT,
    CONF_ROOMS,
    DEFAULT_MAX_REQUESTS_PER_SECOND,
    DOMAIN,
    ENTITY_PLATFORM_BY_KEY,
    PLATFORMS,
    SENSOR_CONTROL_PROFILES,
)
from .coordinator import MeltemDataUpdateCoordinator
from .entity import gateway_device_info
from .modbus_client import MeltemModbusClient
from .modbus_helpers import (
    MeltemModbusError,
    build_serial_params,
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


def _room_config(room: Mapping[str, Any]) -> RoomConfig:
    """Build one room whose entities follow from its profile.

    Deriving them on load keeps older entries complete when a release adds
    entities, without probing the gateway or rewriting the entry. Entity keys
    stored by older releases are ignored.
    """

    return RoomConfig(
        key=room["key"],
        name=room["name"],
        profile=room["profile"],
        slave=int(room["slave"]),
        preview=room.get("preview"),
        supported_entity_keys=frozenset(supported_entity_keys_for_profile(str(room["profile"]))),
    )


def _max_requests_per_second(entry: ConfigEntry) -> float:
    return float(
        entry.options.get(
            CONF_MAX_REQUESTS_PER_SECOND,
            entry.data.get(CONF_MAX_REQUESTS_PER_SECOND, DEFAULT_MAX_REQUESTS_PER_SECOND),
        )
    )


async def _async_normalize_port(hass: HomeAssistant, entry: ConfigEntry) -> str:
    """Store and return the stable ``/dev/serial/by-id`` path of the gateway port.

    A by-id link can appear after setup, so this is a runtime fix-up, not a migration.
    """

    # Resolution walks /dev/serial/by-id, so it must not run in the event loop.
    port = await hass.async_add_executor_job(
        resolve_preferred_port_path, entry.data[CONF_PORT]
    )
    if port != entry.data[CONF_PORT] or entry.unique_id != port:
        hass.config_entries.async_update_entry(
            entry, data={**entry.data, CONF_PORT: port}, unique_id=port
        )
    return port


def _async_remove_unsupported_entities(
    hass: HomeAssistant, entry: ConfigEntry, rooms: list[RoomConfig]
) -> None:
    """Drop registry entries that no configured room/profile creates anymore."""

    expected: dict[str, str] = {}
    for room in rooms:
        supported_keys = set(supported_entity_keys_for_profile(room.profile))
        if room.profile not in SENSOR_CONTROL_PROFILES:
            supported_keys.discard("operation_mode")
        if {"extract_air_flow", "supply_air_flow"} & supported_keys:
            supported_keys.add("data_health")
        for object_key in supported_keys:
            if platform := ENTITY_PLATFORM_BY_KEY.get(object_key):
                expected[f"{DOMAIN}_{room.key}_{object_key}"] = platform.value
    # Gateway-level entity, not tied to any room.
    expected[f"{DOMAIN}_{entry.entry_id}_modbus_device_path"] = Platform.SENSOR.value

    registry = er.async_get(hass)
    for existing in er.async_entries_for_config_entry(registry, entry.entry_id):
        if (
            not existing.unique_id.startswith(f"{DOMAIN}_")
            or expected.get(existing.unique_id) == existing.domain
        ):
            continue
        _LOGGER.info("Removing unsupported Meltem entity %s", existing.entity_id)
        registry.async_remove(existing.entity_id)


def _async_sync_devices(
    hass: HomeAssistant, entry: ConfigEntry, rooms: list[RoomConfig]
) -> None:
    """Register the gateway device and drop devices of units no longer configured."""

    registry = dr.async_get(hass)
    gateway = gateway_device_info(entry.entry_id)
    # Unit devices reference the gateway via ``via_device_id``, so it must exist first.
    registry.async_get_or_create(config_entry_id=entry.entry_id, **gateway)
    configured = {(DOMAIN, room.key) for room in rooms} | gateway["identifiers"]
    for device in dr.async_entries_for_config_entry(registry, entry.entry_id):
        if device.identifiers & configured:
            continue
        _LOGGER.info("Removing device of unconfigured Meltem unit %s", device.name)
        registry.async_remove_device(device.id)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Meltem Modbus from a config entry."""

    port = await _async_normalize_port(hass, entry)
    rooms = [_room_config(room) for room in entry.data[CONF_ROOMS]]
    max_requests_per_second = _max_requests_per_second(entry)
    _LOGGER.info(
        "Using Meltem max request rate of %.1f req/s for %s configured unit(s)",
        max_requests_per_second,
        len(rooms),
    )

    _async_remove_unsupported_entities(hass, entry, rooms)
    _async_sync_devices(hass, entry, rooms)

    # All rooms share one client, and the client asks Home Assistant's modbus
    # integration for its units, so the gateway only ever sees one connection.
    client = MeltemModbusClient(
        partial(async_get_unit, hass, entry, build_serial_params(port)),
        port=port,
        max_requests_per_second=max_requests_per_second,
    )
    try:
        await client.async_validate_gateway()
    except MeltemModbusError as err:
        client.shutdown()
        raise ConfigEntryNotReady(str(err)) from err
    except HomeAssistantError as err:
        # Another integration already uses this port with other link settings.
        client.shutdown()
        raise ConfigEntryError(str(err)) from err

    coordinator = MeltemDataUpdateCoordinator(
        hass,
        config_entry=entry,
        client=client,
        rooms=rooms,
        max_requests_per_second=max_requests_per_second,
    )
    entry.runtime_data = MeltemRuntimeData(coordinator=coordinator)
    try:
        await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    except Exception:
        client.shutdown()
        # Home Assistant only drops runtime_data on unload, not after a failed setup.
        del entry.runtime_data
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
        # A pending write readback must not reach the link after unloading;
        # the modbus integration closes the link once the entry releases it.
        runtime_data.coordinator.client.shutdown()

    return unload_ok
