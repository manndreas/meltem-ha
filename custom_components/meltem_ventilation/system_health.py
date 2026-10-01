"""Provide info for Home Assistant system health."""

from __future__ import annotations

from typing import Any

from homeassistant.components import system_health
from homeassistant.core import HomeAssistant, callback

from .const import CONF_PORT, DOMAIN
from .coordinator import MeltemDataUpdateCoordinator
from .diagnostics import redact_port
from .models import MeltemRuntimeData


@callback
def async_register(
    hass: HomeAssistant,
    register: system_health.SystemHealthRegistration,
) -> None:
    """Register system health callbacks."""

    register.async_register_info(system_health_info)


async def system_health_info(hass: HomeAssistant) -> dict[str, Any]:
    """Return info for the system health page.

    The page is rendered on demand, so it only reports what the running
    coordinator already knows instead of putting extra load on the gateway.
    """

    entries = hass.config_entries.async_loaded_entries(DOMAIN)
    if not entries:
        return {"loaded_entries": 0}

    runtime_data: MeltemRuntimeData = entries[0].runtime_data
    coordinator = runtime_data.coordinator
    last_job_error = coordinator.last_job_error

    return {
        "loaded_entries": 1,
        "configured_units": len(coordinator.rooms),
        "state_units": coordinator.state_room_count,
        "last_update_success": coordinator.last_update_success,
        # The page is often pasted into issues, and by-id paths carry serial numbers.
        "last_job_error": (
            redact_port(str(last_job_error), str(entries[0].data[CONF_PORT]))
            if last_job_error is not None
            else "none"
        ),
        "unavailable_units": ", ".join(
            room.key
            for room in coordinator.rooms
            if not coordinator.room_available(room.key)
        )
        or "none",
        "stale_read_groups": _stale_read_groups(coordinator),
    }


def _stale_read_groups(coordinator: MeltemDataUpdateCoordinator) -> str:
    """Summarize stale read groups per unit.

    The system information dialog only renders plain values, not nested dicts.
    """

    units = []
    for room in coordinator.rooms:
        stale = [
            group_key
            for group_key, health in coordinator.data_health_attributes(room.key).items()
            if group_key != "writes" and health.get("stale") is True
        ]
        if stale:
            units.append(f"{room.key}: {', '.join(stale)}")
    return "; ".join(units) or "none"
