"""Diagnostics support for the Meltem integration."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from homeassistant.components.diagnostics import REDACTED, async_redact_data
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import CONF_PORT
from .models import MeltemRuntimeData, RoomConfig, RoomState

TO_REDACT: set[str] = {CONF_PORT}


def _redact_port(message: str | None, port: str) -> str | None:
    return message.replace(port, REDACTED) if message and port else message


def _serialize_entry(entry: ConfigEntry) -> dict[str, Any]:
    return {
        "entry_id": entry.entry_id,
        "title": entry.title,
        "version": entry.version,
        "state": str(entry.state),
        "data": async_redact_data(dict(entry.data), TO_REDACT),
        "options": async_redact_data(dict(entry.options), TO_REDACT),
    }


def _serialize_room(room: RoomConfig) -> dict[str, Any]:
    """Convert a room config to plain diagnostics data.

    ``supported_entity_keys`` is a frozenset, which the diagnostics JSON encoder
    would otherwise render as an opaque type marker.
    """

    data = asdict(room)
    if room.supported_entity_keys is not None:
        data["supported_entity_keys"] = sorted(room.supported_entity_keys)
    return data


def _serialize_room_state(state: RoomState, port: str) -> dict[str, Any]:
    data = asdict(state)
    for _group_key, health in data["group_read_health"]:
        health["last_error"] = _redact_port(health["last_error"], port)
    return data


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant,
    entry: ConfigEntry,
) -> dict[str, Any]:
    """Return diagnostics for a config entry."""

    runtime_data: MeltemRuntimeData | None = getattr(entry, "runtime_data", None)
    if runtime_data is None:
        return {"entry": _serialize_entry(entry), "coordinator": None}

    coordinator = runtime_data.coordinator
    port = str(entry.data[CONF_PORT])
    gateway_units: list[int] | None = None
    gateway_probe_error: str | None = None
    try:
        gateway_units = await coordinator.async_discover_gateway_units()
    except Exception as err:
        # A gateway that is down must not break the diagnostics download.
        gateway_probe_error = _redact_port(f"{type(err).__name__}: {err}", port)

    last_job_error = coordinator.last_job_error
    return {
        "entry": _serialize_entry(entry),
        "coordinator": {
            "last_update_success": coordinator.last_update_success,
            "update_interval_seconds": (
                coordinator.update_interval.total_seconds()
                if coordinator.update_interval is not None
                else None
            ),
            "configured_room_count": len(coordinator.rooms),
            "state_room_count": coordinator.state_room_count,
            "gateway_units": gateway_units,
            "gateway_probe_error": gateway_probe_error,
            "transport": coordinator.client.transport_diagnostics(),
            "last_job_error": (
                _redact_port(str(last_job_error), port)
                if last_job_error is not None
                else None
            ),
            "unavailable_rooms": [
                room.key
                for room in coordinator.rooms
                if not coordinator.room_available(room.key)
            ],
            "rooms": [_serialize_room(room) for room in coordinator.rooms],
            "room_states": {
                room_key: _serialize_room_state(room_state, port)
                for room_key, room_state in coordinator.safe_data.items()
            },
        },
    }
