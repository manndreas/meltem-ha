"""Provide info for Home Assistant system health."""

from __future__ import annotations

from typing import Any

from homeassistant.components import system_health
from homeassistant.config_entries import ConfigEntry, ConfigEntryState
from homeassistant.core import HomeAssistant, callback

from .const import DOMAIN
from .models import MeltemRuntimeData


@callback
def async_register(
    hass: HomeAssistant,
    register: system_health.SystemHealthRegistration,
) -> None:
    """Register system health callbacks."""

    register.async_register_info(system_health_info)


def _loaded_entry(hass: HomeAssistant) -> ConfigEntry | None:
    """Return the first loaded config entry, if any."""

    for entry in hass.config_entries.async_entries(DOMAIN):
        if entry.state is ConfigEntryState.LOADED:
            return entry
    return None


async def system_health_info(hass: HomeAssistant) -> dict[str, Any]:
    """Return info for the system health page.

    The page is rendered on demand, so it only reports what the running
    coordinator already knows instead of putting extra load on the gateway.
    """

    entry = _loaded_entry(hass)
    if entry is None:
        return {"loaded_entries": 0}

    runtime_data: MeltemRuntimeData = entry.runtime_data
    coordinator = runtime_data.coordinator

    return {
        "loaded_entries": 1,
        "configured_units": len(coordinator.rooms),
        "state_units": coordinator.state_room_count,
        "last_update_success": coordinator.last_update_success,
        "last_job_error": (
            str(coordinator.last_job_error)
            if coordinator.last_job_error is not None
            else "none"
        ),
        "unavailable_units": ", ".join(
            room.key
            for room in coordinator.rooms
            if not coordinator.room_available(room.key)
        )
        or "none",
    }
