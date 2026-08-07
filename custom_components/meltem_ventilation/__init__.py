"""Set up the Meltem Modbus integration entry and runtime objects.

This module keeps the config-entry setup path intentionally small:
- normalize the selected serial port
- ensure each configured room has the metadata needed by entity setup
- create one shared Modbus client and one shared coordinator

All actual Modbus traffic stays in ``modbus_client.py`` and all polling
decisions stay in ``coordinator.py``.
"""

from __future__ import annotations

import logging
from copy import deepcopy

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.device_registry import DeviceEntry

from .const import (
    BASE_SUPPORTED_ENTITY_KEYS,
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
    HUMIDITY_PROFILES,
    PLATFORMS,
)
from .coordinator import MeltemDataUpdateCoordinator
from .modbus_client import MeltemModbusClient
from .modbus_helpers import (
    MeltemModbusError,
    SerialSettings,
    build_setup_probe_settings,
    detect_slave_details,
    resolve_preferred_port_path,
    supported_entity_keys_for_profile,
)
from .models import MeltemRuntimeData, RoomConfig

_LOGGER = logging.getLogger(__name__)

REQUIRED_ENTITY_KEYS = BASE_SUPPORTED_ENTITY_KEYS

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


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Meltem Modbus from a config entry."""

    if hasattr(entry, "runtime_data"):
        object.__delattr__(entry, "runtime_data")

    entry_data = dict(entry.data)
    # Resolution walks /dev/serial/by-id, so it must not run in the event loop.
    normalized_port = await hass.async_add_executor_job(
        resolve_preferred_port_path, entry.data[CONF_PORT]
    )
    needs_update = (
        normalized_port != entry_data[CONF_PORT]
        or entry.unique_id != normalized_port
    )
    entry_data[CONF_PORT] = normalized_port

    settings = SerialSettings(
        port=normalized_port,
        baudrate=FIXED_BAUDRATE,
        bytesize=FIXED_BYTESIZE,
        parity=FIXED_PARITY,
        stopbits=FIXED_STOPBITS,
        timeout=float(FIXED_TIMEOUT),
    )
    rooms_data = deepcopy(entry_data[CONF_ROOMS])
    # Setup stores a compact snapshot per room. On load we make sure the
    # metadata is complete so entity setup does not have to probe the gateway.
    missing_metadata = any(
        not room.get("supported_entity_keys") for room in rooms_data
    )
    stale_supported_keys = any(
        not set(
            supported_entity_keys_for_profile(
                str(room.get("profile", "ii_plain"))
            )
        ).issubset(set(room.get("supported_entity_keys", [])))
        for room in rooms_data
    )

    if missing_metadata:
        probe_settings = build_setup_probe_settings(settings)
        updated_rooms_data: list[dict] = []
        for room in rooms_data:
            try:
                _, preview, supported_entity_keys = (
                    await hass.async_add_executor_job(
                        detect_slave_details,
                        probe_settings,
                        int(room["slave"]),
                    )
                )
            except Exception as err:
                _LOGGER.warning(
                    "Failed to refresh setup metadata for Meltem room %s during startup; using profile defaults: %s",
                    room.get("key", room.get("slave")),
                    err,
                )
                preview = room.get("preview")
                supported_entity_keys = supported_entity_keys_for_profile(
                    str(room.get("profile", "ii_plain"))
                )
            updated_rooms_data.append(
                {
                    **room,
                    "preview": room.get("preview") or preview,
                    # The probe only sees optional sensors; the thresholds and
                    # temperature points follow from the selected profile.
                    "supported_entity_keys": sorted(
                        set(supported_entity_keys)
                        | set(
                            supported_entity_keys_for_profile(
                                str(room.get("profile", "ii_plain"))
                            )
                        )
                    ),
                }
            )
        rooms_data = updated_rooms_data
        needs_update = True
    elif stale_supported_keys:
        updated_rooms_data = []
        for room in rooms_data:
            current_supported_entity_keys = set(room.get("supported_entity_keys", []))
            updated_rooms_data.append(
                {
                    **room,
                    "supported_entity_keys": sorted(
                        current_supported_entity_keys
                        | REQUIRED_ENTITY_KEYS
                        | set(
                            supported_entity_keys_for_profile(
                                str(room.get("profile", "ii_plain"))
                            )
                        )
                    ),
                }
            )
        rooms_data = updated_rooms_data
        needs_update = True

    if needs_update:
        hass.config_entries.async_update_entry(
            entry,
            data={**entry_data, CONF_PORT: normalized_port, CONF_ROOMS: rooms_data},
            unique_id=normalized_port,
        )

    rooms = [
        RoomConfig(
            key=room["key"],
            name=room["name"],
            profile=room["profile"],
            slave=int(room["slave"]),
            preview=room.get("preview"),
            supported_entity_keys=(
                frozenset(room["supported_entity_keys"])
                if room.get("supported_entity_keys")
                else None
            ),
        )
        for room in rooms_data
    ]
    max_requests_per_second = float(
        entry.options.get(
            CONF_MAX_REQUESTS_PER_SECOND,
            entry_data.get(
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
        await hass.async_add_executor_job(client.close)
        if hasattr(entry, "runtime_data"):
            object.__delattr__(entry, "runtime_data")
        raise ConfigEntryNotReady(str(err)) from err
    except Exception:
        await hass.async_add_executor_job(client.close)
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
        await hass.async_add_executor_job(runtime_data.coordinator.client.close)

    return unload_ok


async def async_remove_config_entry_device(
    hass: HomeAssistant, entry: ConfigEntry, device: DeviceEntry
) -> bool:
    """Allow deleting devices whose unit is no longer configured on the gateway.

    A rescan can drop units, and their devices would otherwise linger forever
    because Home Assistant never removes them on its own.
    """

    configured = {(DOMAIN, str(room["key"])) for room in entry.data[CONF_ROOMS]}
    return not any(identifier in configured for identifier in device.identifiers)
