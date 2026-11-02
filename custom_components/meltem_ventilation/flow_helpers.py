"""Shared steps of the Meltem flows: unit discovery, probing, and the profile form."""

from __future__ import annotations

import logging
from collections import Counter
from collections.abc import AsyncGenerator, Awaitable, Callable, Iterable, Mapping
from contextlib import AsyncExitStack, asynccontextmanager
from typing import Any, cast

import probatio as vol
from homeassistant.components.modbus import async_get_temporary_unit
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import selector
from modbus_connection import ModbusUnit

from .const import (
    CONF_CONNECTION_TYPE,
    CONF_HOST,
    CONF_NETWORK_PORT,
    CONF_PORT,
    CONF_PROFILE,
    CONF_SLAVE,
    CONF_UNIT_KEY,
    CONNECTION_RTU_OVER_TCP,
    CONNECTION_SERIAL,
    DEFAULT_GATEWAY_DEVICE_ID,
    DEFAULT_NETWORK_PORT,
    DEFAULT_PORT,
    DEFAULT_SCAN_SLAVE_END,
    DEFAULT_SCAN_SLAVE_START,
    DOMAIN,
    MAX_MAX_REQUESTS_PER_SECOND,
    MIN_MAX_REQUESTS_PER_SECOND,
    MODEL_PROFILE_LABELS,
)
from .gateway_connection import GatewayConnection, RtuTcpGatewayConnection, SerialGatewayConnection
from .modbus_helpers import (
    MeltemConnectionError,
    MeltemModbusError,
    detect_slave_details,
    discover_gateway_nodes,
    new_transport_policy,
    prepare_unit,
    read_gateway_node_count,
    resolve_preferred_port_path,
)
from .models import UnitProbeResult

_LOGGER = logging.getLogger(__name__)

# Probed sensor suffix to the preselected profile; the series stays M-WRG-II.
_SUFFIX_DEFAULT_PROFILES: dict[str, str] = {
    "plain": "ii_plain",
    "f": "ii_f",
    "fc": "ii_fc",
    "fc_voc": "ii_fc_voc",
}

PROFILE_SELECTOR = selector.SelectSelector(
    selector.SelectSelectorConfig(
        mode=selector.SelectSelectorMode.DROPDOWN,
        options=[
            selector.SelectOptionDict(value=key, label=label)
            for key, label in MODEL_PROFILE_LABELS.items()
        ],
    )
)

MAX_REQUEST_RATE_SELECTOR = selector.NumberSelector(
    selector.NumberSelectorConfig(
        min=MIN_MAX_REQUESTS_PER_SECOND,
        max=MAX_MAX_REQUESTS_PER_SECOND,
        step=0.5,
        mode=selector.NumberSelectorMode.BOX,
    )
)

type _Probe = Callable[[int], Awaitable[UnitProbeResult]]


def port_schema(default: str) -> vol.Schema:
    return vol.Schema({vol.Required(CONF_PORT, default=default): str})


def connection_schema(
    connection: GatewayConnection, values: Mapping[str, Any] | None = None
) -> vol.Schema:
    """Build the form for either a local serial device or a network bridge."""

    if isinstance(connection, RtuTcpGatewayConnection):
        connection_type = CONNECTION_RTU_OVER_TCP
        serial_port = DEFAULT_PORT
        host = connection.host
        network_port = connection.port
    else:
        connection_type = CONNECTION_SERIAL
        serial_port = connection.port
        host = ""
        network_port = DEFAULT_NETWORK_PORT
    values = values or {}
    return vol.Schema(
        {
            vol.Required(
                CONF_CONNECTION_TYPE, default=values.get(CONF_CONNECTION_TYPE, connection_type)
            ): selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=[CONNECTION_SERIAL, CONNECTION_RTU_OVER_TCP],
                    translation_key="connection_type",
                    mode=selector.SelectSelectorMode.DROPDOWN,
                )
            ),
            vol.Optional(CONF_PORT, default=values.get(CONF_PORT, serial_port)): str,
            vol.Optional(CONF_HOST, default=values.get(CONF_HOST, host)): str,
            vol.Optional(
                CONF_NETWORK_PORT, default=values.get(CONF_NETWORK_PORT, network_port)
            ): vol.All(vol.Coerce(int), vol.Range(min=1, max=65535)),
        }
    )


def profiles_form(
    slaves: list[int],
    defaults_by_slave: Mapping[int, str],
    previews_by_slave: Mapping[int, str],
    names_by_slave: Mapping[int, str] | None = None,
) -> tuple[vol.Schema, dict[str, str]]:
    """Build the schema and description placeholders for one profile step."""

    data_schema = vol.Schema(
        {
            vol.Required(profile_field_key(slave), default=defaults_by_slave[slave]): (
                PROFILE_SELECTOR
            )
            for slave in slaves
        }
    )
    placeholders = {
        "device_count": str(len(slaves)),
        "unit_details": unit_details(slaves, previews_by_slave, names_by_slave),
    }
    return data_schema, placeholders


def profile_field_key(slave: int) -> str:
    """Build the stable schema key for one detected unit.

    The key is derived from the Modbus address so it survives rescans and can
    be translated in ``strings.json``.
    """

    return f"slave_{slave}"


def default_room_name(used_names: set[str]) -> str:
    """Return the first ``Unit N`` name that no kept unit uses yet."""

    number = 1
    while f"Unit {number}" in used_names:
        number += 1
    return f"Unit {number}"


def unit_details(
    slaves: list[int],
    previews_by_slave: Mapping[int, str],
    names_by_slave: Mapping[int, str] | None = None,
) -> str:
    """Build the markdown list that identifies each unit in the step description."""

    names_by_slave = names_by_slave or {}
    lines: list[str] = []
    for slave in slaves:
        details: list[str] = []
        name = names_by_slave.get(slave)
        if name:
            details.append(name)
        preview = previews_by_slave.get(slave)
        if preview:
            details.append(preview.replace("ID ", "Hardware ID "))
        suffix = f": {', '.join(details)}" if details else ""
        lines.append(f"- **{slave}**{suffix}")
    return "\n".join(lines)


@callback
def device_names_by_slave(
    hass: HomeAssistant, config_entry_id: str, rooms: Iterable[Mapping[str, Any]]
) -> dict[int, str]:
    """Map unit addresses to the device names the user set in Home Assistant.

    Naming belongs to the device registry, so the stored room name is only ever
    the initial value and is not shown here.
    """

    registry = dr.async_get(hass)
    names: dict[int, str] = {}
    for room in rooms:
        device = registry.async_get_device_by_identifier(
            (DOMAIN, str(room["key"])), config_entry_id
        )
        if device is not None and device.name_by_user:
            names[int(room["slave"])] = device.name_by_user
    return names


def detected_profile_default(slave: int, detected_profiles_by_slave: Mapping[int, str]) -> str:
    """Return the default profile selection for a detected unit."""

    detected_profile = detected_profiles_by_slave.get(slave) or ""
    return _SUFFIX_DEFAULT_PROFILES.get(detected_profile, "ii_plain")


def profile_defaults(
    slaves: list[int],
    detected_profiles_by_slave: Mapping[int, str] | None = None,
    existing_rooms_by_slave: Mapping[int, Mapping[str, Any]] | None = None,
) -> dict[int, str]:
    """Return the default profile per unit, preferring an already stored selection."""

    detected = detected_profiles_by_slave or {}
    existing = existing_rooms_by_slave or {}
    return {
        slave: str(
            existing.get(slave, {}).get("profile") or detected_profile_default(slave, detected)
        )
        for slave in slaves
    }


def match_existing_rooms(
    slaves: Iterable[int],
    probe_results_by_slave: Mapping[int, UnitProbeResult] | None = None,
    existing_rooms_by_slave: Mapping[int, Mapping[str, Any]] | None = None,
) -> dict[int, dict[str, Any]]:
    """Match discovered unit addresses to existing rooms by setup code, then address."""

    probe_results = probe_results_by_slave or {}
    existing = existing_rooms_by_slave or {}
    stored_codes = Counter(
        room.get("app_setup_code")
        for room in existing.values()
        if room.get("app_setup_code") is not None
    )
    probed_codes = Counter(
        result.app_setup_code
        for slave in slaves
        if (result := probe_results.get(slave)) is not None and result.app_setup_code is not None
    )
    by_code = {
        str(room["app_setup_code"]): (old_slave, room)
        for old_slave, room in existing.items()
        if room.get("app_setup_code") is not None and stored_codes[room.get("app_setup_code")] == 1
    }

    matched: dict[int, dict[str, Any]] = {}
    claimed: set[int] = set()
    for slave in slaves:
        result = probe_results.get(slave)
        code = result.app_setup_code if result is not None else None
        if code is not None and probed_codes[code] == 1 and code in by_code:
            old_slave, room = by_code[code]
            matched[slave] = {**room, CONF_SLAVE: slave}
            claimed.add(old_slave)
    for slave in slaves:
        if slave not in matched and slave not in claimed:
            if (same_slave := existing.get(slave)) is not None:
                matched[slave] = {**same_slave, CONF_SLAVE: slave}
    return matched


def build_rooms_from_profiles(
    slaves: list[int],
    selected_profiles: Mapping[str, Any],
    probe_results_by_slave: Mapping[int, UnitProbeResult] | None = None,
    existing_rooms_by_slave: Mapping[int, Mapping[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Build room config entries from selected per-device profiles.

    The entities of a room follow from its profile on load, so they are not stored.
    """

    rooms: list[dict[str, Any]] = []
    probe_results_by_slave = probe_results_by_slave or {}
    matched_rooms = match_existing_rooms(slaves, probe_results_by_slave, existing_rooms_by_slave)
    # A code a fresh probe reports belongs to that unit, so a code still stored
    # for another address is stale, for example after units changed addresses.
    probed_codes = {
        result.app_setup_code
        for result in probe_results_by_slave.values()
        if result.app_setup_code is not None
    }
    used_room_keys: set[str] = set()
    # Kept units keep their names, so a new unit must not reuse one.
    used_names = {
        str(matched_rooms[slave]["name"])
        for slave in slaves
        if "name" in matched_rooms.get(slave, {})
    }

    for slave in slaves:
        existing_room = matched_rooms.get(slave, {})
        probe_result = probe_results_by_slave.get(slave)
        selected_profile = str(selected_profiles[profile_field_key(slave)])
        preferred_room_key = str(existing_room.get("key") or f"slave_{slave}")
        room_key = preferred_room_key
        suffix = 2
        while room_key in used_room_keys:
            room_key = f"{preferred_room_key}_{suffix}"
            suffix += 1
        used_room_keys.add(room_key)
        name = str(existing_room.get("name") or default_room_name(used_names))
        used_names.add(name)
        room: dict[str, Any] = {
            "key": room_key,
            # Only the initial device name; renaming happens in the device registry.
            "name": name,
            "slave": slave,
            "profile": selected_profile,
            "preview": (probe_result.preview if probe_result else None)
            or existing_room.get("preview"),
        }
        product_id = (
            probe_result.product_id
            if probe_result and probe_result.product_id is not None
            else existing_room.get("product_id")
        )
        app_setup_code = (
            probe_result.app_setup_code
            if probe_result and probe_result.app_setup_code is not None
            else _retained_app_setup_code(existing_room, probed_codes)
        )
        if product_id is not None:
            room["product_id"] = product_id
        if app_setup_code is not None:
            room["app_setup_code"] = app_setup_code
        rooms.append(room)

    return rooms


def _retained_app_setup_code(
    existing_room: Mapping[str, Any], probed_codes: set[str]
) -> str | None:
    """Return the stored code of a unit that was not probed, unless another unit now has it."""

    code = existing_room.get("app_setup_code")
    if code is None or code in probed_codes:
        return None
    return str(code)


class PortInUseError(MeltemConnectionError):
    """Another integration holds the serial port with other link settings."""


@asynccontextmanager
async def temporary_units(
    hass: HomeAssistant, port: str | RtuTcpGatewayConnection
) -> AsyncGenerator[Callable[[int], Awaitable[ModbusUnit]], None]:
    """Hold units on the gateway link for one flow step.

    The link closes again on exit unless a loaded entry also holds it.
    """

    connection = SerialGatewayConnection(port) if isinstance(port, str) else port
    params = connection.modbus_params()
    policy = new_transport_policy()
    async with AsyncExitStack() as stack:

        async def unit_for(slave: int) -> ModbusUnit:
            try:
                unit = await stack.enter_async_context(
                    async_get_temporary_unit(hass, params, slave)
                )
            except HomeAssistantError as err:
                raise PortInUseError(str(err)) from err
            return prepare_unit(unit, slave, policy)

        yield unit_for


def connection_error(err: Exception, action: str, port: str) -> str:
    """Return the form error for a failed gateway access."""

    if isinstance(err, PortInUseError):
        return "port_in_use"
    if isinstance(err, MeltemModbusError):
        return "cannot_connect"
    _LOGGER.error("Unexpected error while %s %s", action, port, exc_info=err)
    return "unknown"


async def async_probe_units(slaves: list[int], probe: _Probe) -> dict[int, UnitProbeResult]:
    """Probe every unit and return its structured details by address.

    A failed probe leaves the unit plain and without preview.
    """

    results: dict[int, UnitProbeResult] = {}
    for slave in slaves:
        try:
            result = await probe(slave)
        except MeltemModbusError as err:
            _LOGGER.warning("Probe failed for Meltem unit at slave %s: %s", slave, err)
            result = UnitProbeResult(profile="plain", preview=None)
        results[slave] = result
    return results


def probe_previews(results: Mapping[int, UnitProbeResult]) -> dict[int, str]:
    return {slave: result.preview for slave, result in results.items() if result.preview}


def probe_profiles(results: Mapping[int, UnitProbeResult]) -> dict[int, str]:
    return {slave: result.profile for slave, result in results.items()}


async def async_discover_units(
    hass: HomeAssistant, port: str | RtuTcpGatewayConnection
) -> tuple[list[int], dict[int, UnitProbeResult]]:
    """Read the unit list from the gateway and probe every unit on one link.

    Returns the unit addresses and structured probe details.
    """

    async with temporary_units(hass, port) as unit_for:
        _LOGGER.info("Starting Meltem gateway-backed unit discovery on %s", port)
        slaves = await discover_gateway_nodes(
            await unit_for(DEFAULT_GATEWAY_DEVICE_ID),
            port if isinstance(port, str) else port.unique_id,
            start=DEFAULT_SCAN_SLAVE_START,
            end=DEFAULT_SCAN_SLAVE_END,
        )

        async def probe(slave: int) -> UnitProbeResult:
            return await detect_slave_details(await unit_for(slave))

        probe_results = await async_probe_units(slaves, probe)
    return slaves, probe_results


async def async_validate_port(hass: HomeAssistant, port: str | RtuTcpGatewayConnection) -> None:
    """Check that a gateway answers on the given connection."""

    async with temporary_units(hass, port) as unit_for:
        await read_gateway_node_count(await unit_for(DEFAULT_GATEWAY_DEVICE_ID))


async def async_resolve_port(hass: HomeAssistant, port: str) -> str:
    """Resolve the stable serial path off the event loop.

    Resolution walks ``/dev/serial/by-id``, which is blocking filesystem I/O.
    """

    return cast(
        str,
        await hass.async_add_executor_job(resolve_preferred_port_path, port),
    )


def new_unit(
    slave: int,
    profile: str,
    probe_result: UnitProbeResult | None,
    existing_rooms: Iterable[Mapping[str, Any]],
) -> dict[str, object]:
    """Build a unit added to an existing gateway, with a key and name no unit uses yet."""

    existing = list(existing_rooms)
    used_keys = {str(room[CONF_UNIT_KEY]) for room in existing}
    preferred_key = room_key = f"slave_{slave}"
    suffix = 2
    while room_key in used_keys:
        room_key = f"{preferred_key}_{suffix}"
        suffix += 1
    room: dict[str, object] = {
        CONF_UNIT_KEY: room_key,
        "name": default_room_name({str(room["name"]) for room in existing}),
        CONF_SLAVE: slave,
        CONF_PROFILE: profile,
    }
    if probe_result is not None:
        room["preview"] = probe_result.preview
        room["product_id"] = probe_result.product_id
        room["app_setup_code"] = probe_result.app_setup_code
    return room
