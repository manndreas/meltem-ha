"""Provide info for Home Assistant system health."""

from __future__ import annotations

from typing import Any

from homeassistant.components import system_health
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


async def _async_probe_gateway_units(hass: HomeAssistant) -> str:
    """Probe the gateway for configured unit addresses."""

    entries = hass.config_entries.async_entries(DOMAIN)
    if not entries:
        return "not_loaded"

    runtime_data: MeltemRuntimeData = entries[0].runtime_data

    try:
        units = await runtime_data.coordinator.async_discover_gateway_units()
    except Exception as err:  # pragma: no cover - best-effort health path
        return f"error: {type(err).__name__}: {err}"

    return ", ".join(str(unit) for unit in units) if units else "none"


async def system_health_info(hass: HomeAssistant) -> dict[str, Any]:
    """Return info for the system health page."""

    entries = hass.config_entries.async_entries(DOMAIN)
    if not entries:
        return {
            "loaded_entries": 0,
            "gateway_units": "not_loaded",
        }

    entry = entries[0]
    runtime_data: MeltemRuntimeData = entry.runtime_data
    coordinator = runtime_data.coordinator

    return {
        "loaded_entries": len(entries),
        "serial_port": entry.data.get("port"),
        "configured_units": len(coordinator.rooms),
        "state_units": len(coordinator.safe_data),
        "last_update_success": coordinator.last_update_success,
        "last_job_error": (
            str(coordinator.last_job_error)
            if coordinator.last_job_error is not None
            else "none"
        ),
        "gateway_units": await _async_probe_gateway_units(hass),
    }
