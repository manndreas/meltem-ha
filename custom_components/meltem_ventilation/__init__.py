"""Set up the Meltem Modbus integration entry and runtime objects.

This module keeps the config-entry setup path intentionally small:
- normalize the selected serial port
- ensure each configured room has the metadata needed by entity setup
- create one shared Modbus client and one shared coordinator

All actual Modbus traffic stays in ``modbus_client.py`` and all polling
decisions stay in ``coordinator.py``.
"""

from __future__ import annotations

from copy import deepcopy
import inspect
import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import (
    BASE_SUPPORTED_ENTITY_KEYS,
    CONF_MAX_REQUESTS_PER_SECOND,
    CONF_PORT,
    CONF_ROOMS,
    DEFAULT_MAX_REQUESTS_PER_SECOND,
    DOMAIN,
    FIXED_BAUDRATE,
    FIXED_BYTESIZE,
    FIXED_PARITY,
    FIXED_STOPBITS,
    FIXED_TIMEOUT,
    PLATFORMS,
)
from .coordinator import MeltemDataUpdateCoordinator
from .models import MeltemRuntimeData, RoomConfig
from .modbus_client import MeltemModbusClient
from .modbus_helpers import (
    SerialSettings,
    build_setup_probe_settings,
    detect_slave_details,
    resolve_preferred_port_path,
    supported_entity_keys_for_profile,
)

_LOGGER = logging.getLogger(__name__)

REQUIRED_ENTITY_KEYS = BASE_SUPPORTED_ENTITY_KEYS


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Meltem Modbus from a config entry."""

    entry_data = dict(entry.data)
    normalized_port = resolve_preferred_port_path(entry.data[CONF_PORT])
    needs_update = normalized_port != entry_data[CONF_PORT]
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
        not REQUIRED_ENTITY_KEYS.issubset(set(room.get("supported_entity_keys", [])))
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
                    "supported_entity_keys": supported_entity_keys,
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

    # All entities for one config entry share one serial client so the gateway
    # only ever sees one active connection from Home Assistant.
    client = MeltemModbusClient(settings)
    coordinator = MeltemDataUpdateCoordinator(
        hass,
        client=client,
        rooms=rooms,
        max_requests_per_second=max_requests_per_second,
    )
    entry.runtime_data = MeltemRuntimeData(coordinator=coordinator)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    hass.async_create_task(coordinator.async_refresh())
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""

    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        runtime_data: MeltemRuntimeData = entry.runtime_data
        await runtime_data.coordinator.async_cancel_room_write_tasks()
        shutdown_result = runtime_data.coordinator.async_shutdown()
        if inspect.isawaitable(shutdown_result):
            await shutdown_result
        await hass.async_add_executor_job(runtime_data.coordinator.client.close)

    return unload_ok
