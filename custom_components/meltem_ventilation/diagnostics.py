"""Diagnostics support for the Meltem integration."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .models import MeltemRuntimeData

TO_REDACT: set[str] = {"port"}


def _serialize_room_state(state: Any) -> dict[str, Any]:
    """Convert a room state dataclass to plain diagnostics data."""

    return asdict(state)


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant,
    entry: ConfigEntry,
) -> dict[str, Any]:
    """Return diagnostics for a config entry."""

    runtime_data: MeltemRuntimeData = entry.runtime_data
    coordinator = runtime_data.coordinator

    try:
        gateway_units = await coordinator.async_discover_gateway_units()
        gateway_probe_error = None
    except Exception as err:  # pragma: no cover - best-effort diagnostics path
        gateway_units = None
        gateway_probe_error = f"{type(err).__name__}: {err}"

    room_states = {
        room_key: _serialize_room_state(room_state)
        for room_key, room_state in coordinator.safe_data.items()
    }

    return {
        "entry": {
            "entry_id": entry.entry_id,
            "title": entry.title,
            "version": entry.version,
            "data": async_redact_data(dict(entry.data), TO_REDACT),
            "options": async_redact_data(dict(entry.options), TO_REDACT),
        },
        "coordinator": {
            "last_update_success": coordinator.last_update_success,
            "update_interval_seconds": (
                coordinator.update_interval.total_seconds()
                if coordinator.update_interval is not None
                else None
            ),
            "configured_room_count": len(coordinator.rooms),
            "state_room_count": len(room_states),
            "gateway_units": gateway_units,
            "gateway_probe_error": gateway_probe_error,
            "last_job_error": (
                str(coordinator.last_job_error)
                if coordinator.last_job_error is not None
                else None
            ),
            "rooms": [asdict(room) for room in coordinator.rooms],
            "room_states": room_states,
        },
    }
