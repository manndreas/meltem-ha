"""Diagnostics support for the Meltem integration."""

from __future__ import annotations

import re
from dataclasses import asdict
from typing import Any, cast

from homeassistant.components.diagnostics import REDACTED, async_redact_data
from homeassistant.core import HomeAssistant

from .const import CONF_HOST, CONF_PORT
from .cumulative_diagnostics import CumulativeDiagnostics
from .gateway_connection import RtuTcpGatewayConnection, gateway_connection_from_data
from .models import (
    MeltemIntegrationConfigEntry,
    MeltemRuntimeData,
    RoomConfig,
    RoomState,
)
from .units import unit_subentries

TO_REDACT: set[str] = {CONF_PORT, CONF_HOST}
# Codes are formatted as R plus six uppercase hex digits, so that form is redacted
# even inside other text; any other casing only as a separate word, which keeps
# ordinary words such as "ReadBack" intact.
_SETUP_CODE = re.compile(r"R[0-9A-F]{6}|(?<![A-Za-z0-9])[Rr][0-9A-Fa-f]{6}(?![A-Za-z0-9])")


def _redact_setup_codes(value: Any) -> Any:
    if isinstance(value, str):
        return _SETUP_CODE.sub(REDACTED, value)
    if isinstance(value, dict):
        return {
            _redact_setup_codes(key): (
                REDACTED
                if key == "app_setup_code" and item is not None
                else _redact_setup_codes(item)
            )
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_redact_setup_codes(item) for item in value]
    if isinstance(value, tuple):
        return tuple(_redact_setup_codes(item) for item in value)
    return value


def redact_port(message: str | None, port: str) -> str | None:
    """Replace the serial port path in an error message."""

    return message.replace(port, REDACTED) if message and port else message


def _serialize_entry(entry: MeltemIntegrationConfigEntry) -> dict[str, Any]:
    return {
        "entry_id": entry.entry_id,
        "title": entry.title,
        "version": entry.version,
        "minor_version": entry.minor_version,
        "state": str(entry.state),
        "data": async_redact_data(dict(entry.data), TO_REDACT),
        "options": async_redact_data(dict(entry.options), TO_REDACT),
        "units": [
            {
                "subentry_id": subentry.subentry_id,
                "title": subentry.title,
                "unique_id": subentry.unique_id,
                "data": dict(subentry.data),
            }
            for subentry in unit_subentries(entry)
        ],
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
        health["last_error"] = redact_port(health["last_error"], port)
        # A monotonic clock reading means nothing outside this process.
        del health["last_success_monotonic"]
    return data


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant,
    entry: MeltemIntegrationConfigEntry,
) -> dict[str, Any]:
    """Return diagnostics for a config entry."""

    runtime_data: MeltemRuntimeData | None = getattr(entry, "runtime_data", None)
    if runtime_data is None:
        return cast(
            dict[str, Any],
            _redact_setup_codes({"entry": _serialize_entry(entry), "coordinator": None}),
        )

    coordinator = runtime_data.coordinator
    connection = gateway_connection_from_data(entry.data)
    port = connection.host if isinstance(connection, RtuTcpGatewayConnection) else connection.port
    transport = coordinator.client.transport_diagnostics()
    cumulative = getattr(coordinator, "cumulative_diagnostics", None)
    cumulative_snapshot = (
        cumulative.diagnostics() if isinstance(cumulative, CumulativeDiagnostics) else None
    )
    gateway_units: list[int] | None = None
    gateway_probe_error: str | None = None
    try:
        gateway_units = await coordinator.async_discover_gateway_units()
    except Exception as err:
        # A gateway that is down must not break the diagnostics download.
        gateway_probe_error = redact_port(f"{type(err).__name__}: {err}", port)

    last_job_error = coordinator.last_job_error
    return cast(
        dict[str, Any],
        _redact_setup_codes(
            {
                "entry": _serialize_entry(entry),
                "coordinator": {
                    "last_update_success": coordinator.last_update_success,
                    "next_poll_delay_seconds": round(coordinator.next_poll_delay, 3),
                    "poll_scheduled": coordinator.poll_scheduled,
                    "configured_room_count": len(coordinator.rooms),
                    "state_room_count": coordinator.state_room_count,
                    "gateway_units": gateway_units,
                    "gateway_probe_error": gateway_probe_error,
                    "transport": transport,
                    "cumulative_diagnostics": cumulative_snapshot,
                    "last_job_error": (
                        redact_port(str(last_job_error), port)
                        if last_job_error is not None
                        else None
                    ),
                    "last_room_errors": {
                        room_key: {
                            "error": redact_port(room_error.message, port),
                            "at": room_error.at.isoformat(),
                        }
                        for room_key, room_error in coordinator.last_room_errors.items()
                    },
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
        ),
    )
