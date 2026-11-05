"""Shared steps of the Meltem flows: unit discovery, probing, and the profile form."""

from __future__ import annotations

import logging
from collections import Counter
from collections.abc import AsyncGenerator, Awaitable, Callable, Iterable, Mapping
from contextlib import AsyncExitStack, asynccontextmanager
from typing import Any, cast

import probatio as vol
from homeassistant import data_entry_flow
from homeassistant.components.modbus import async_get_temporary_unit
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import selector
from homeassistant.helpers.field_visibility import ConditionOperator, FieldCondition
from modbus_connection import ModbusUnit

from .const import (
    CONF_CONNECTION_TYPE,
    CONF_HOST,
    CONF_MODBUS_TCP_PORT,
    CONF_NAME_IS_DEVICE_NAME,
    CONF_NETWORK_PORT,
    CONF_PORT,
    CONF_PROFILE,
    CONF_SLAVE,
    CONF_UNIT_KEY,
    CONNECTION_MODBUS_TCP,
    CONNECTION_RTU_OVER_TCP,
    CONNECTION_SERIAL,
    DEFAULT_GATEWAY_DEVICE_ID,
    DEFAULT_MODBUS_TCP_PORT,
    DEFAULT_NETWORK_PORT,
    DEFAULT_PORT,
    DEFAULT_SCAN_SLAVE_END,
    DEFAULT_SCAN_SLAVE_START,
    DOMAIN,
    MAX_MAX_REQUESTS_PER_SECOND,
    MIN_MAX_REQUESTS_PER_SECOND,
    MODEL_PROFILE_LABELS,
    PRODUCT_ID_M_WRG,
    PRODUCT_ID_M_WRG_II,
    product_metadata,
    profile_label,
)
from .gateway_connection import (
    GatewayConnection,
    ModbusTcpGatewayConnection,
    RtuTcpGatewayConnection,
    SerialGatewayConnection,
)
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

PROFILE_MISMATCH_SELECTOR = selector.SelectSelector(
    selector.SelectSelectorConfig(
        mode=selector.SelectSelectorMode.DROPDOWN,
        translation_key="profile_mismatch_action",
        options=["correct", "confirm"],
    )
)
PROFILE_MISMATCH_SCHEMA = vol.Schema(
    {vol.Required("action", default="correct"): PROFILE_MISMATCH_SELECTOR}
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
    elif isinstance(connection, ModbusTcpGatewayConnection):
        connection_type = CONNECTION_MODBUS_TCP
        serial_port = DEFAULT_PORT
        host = connection.host
    else:
        connection_type = CONNECTION_SERIAL
        serial_port = connection.port
        host = ""
    values = values or {}
    rtu_port = values.get(
        CONF_NETWORK_PORT,
        connection.port
        if isinstance(connection, RtuTcpGatewayConnection)
        else DEFAULT_NETWORK_PORT,
    )
    modbus_tcp_port = values.get(
        CONF_MODBUS_TCP_PORT,
        connection.port
        if isinstance(connection, ModbusTcpGatewayConnection)
        else DEFAULT_MODBUS_TCP_PORT,
    )
    network_visibility: FieldCondition = {
        "field": CONF_CONNECTION_TYPE,
        "operator": ConditionOperator.IN,
        "value": [CONNECTION_RTU_OVER_TCP, CONNECTION_MODBUS_TCP],
    }
    return vol.Schema(
        {
            vol.Required(
                CONF_CONNECTION_TYPE, default=values.get(CONF_CONNECTION_TYPE, connection_type)
            ): selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=[CONNECTION_SERIAL, CONNECTION_RTU_OVER_TCP, CONNECTION_MODBUS_TCP],
                    translation_key="connection_type",
                    mode=selector.SelectSelectorMode.DROPDOWN,
                )
            ),
            data_entry_flow.Optional(
                CONF_PORT,
                default=values.get(CONF_PORT, serial_port),
                visible={"field": CONF_CONNECTION_TYPE, "value": CONNECTION_SERIAL},
            ): str,
            data_entry_flow.Optional(
                CONF_HOST,
                default=values.get(CONF_HOST, host),
                visible=network_visibility,
            ): str,
            data_entry_flow.Optional(
                CONF_NETWORK_PORT,
                default=rtu_port,
                visible={
                    "field": CONF_CONNECTION_TYPE,
                    "value": CONNECTION_RTU_OVER_TCP,
                },
            ): vol.All(vol.Coerce(int), vol.Range(min=1, max=65535)),
            data_entry_flow.Optional(
                CONF_MODBUS_TCP_PORT,
                default=modbus_tcp_port,
                visible={
                    "field": CONF_CONNECTION_TYPE,
                    "value": CONNECTION_MODBUS_TCP,
                },
            ): vol.All(vol.Coerce(int), vol.Range(min=1, max=65535)),
        }
    )


def profiles_form(
    slaves: list[int],
    defaults_by_slave: Mapping[int, str],
    probes_by_slave: Mapping[int, UnitProbeResult],
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
        **unit_detail_placeholders(slaves, probes_by_slave, names_by_slave),
    }
    return data_schema, placeholders


def profile_field_key(slave: int) -> str:
    """Build the stable schema key for one detected unit.

    The key is derived from the Modbus address so it survives rescans and can
    be translated in ``strings.json``.
    """

    return f"slave_{slave}"


def default_room_name(profile: str, app_setup_code: str | None, used_names: set[str]) -> str:
    """Return a distinct initial device name based on the selected model family."""

    family = profile_label(profile).split(" (", 1)[0]
    base = f"Meltem {family}"
    if app_setup_code:
        base = f"{base} ({app_setup_code})"
    name = base
    number = 2
    while name in used_names:
        name = f"{base} ({number})"
        number += 1
    return name


def unit_details(
    slaves: list[int],
    probes_by_slave: Mapping[int, UnitProbeResult],
    names_by_slave: Mapping[int, str] | None = None,
) -> str:
    """Build the markdown list that identifies each unit in the step description."""

    names_by_slave = names_by_slave or {}
    lines: list[str] = []
    for slave in slaves:
        suffix = unit_detail(probes_by_slave.get(slave), names_by_slave.get(slave))
        lines.append(f"- **{slave}**{suffix}")
    return "\n".join(lines)


def unit_detail(probe: UnitProbeResult | None, name: str | None = None) -> str:
    """Return a compact identity suffix for a translated unit label."""

    details: list[str] = [name] if name else []
    if probe:
        if product := product_metadata(probe.product_id):
            details.append(product.series)
        if probe.app_setup_code:
            if details:
                details[-1] = f"{details[-1]} ({probe.app_setup_code})"
            else:
                details.append(probe.app_setup_code)
    return f": {' · '.join(details)}" if details else ""


def unit_detail_placeholders(
    slaves: Iterable[int],
    probes_by_slave: Mapping[int, UnitProbeResult],
    names_by_slave: Mapping[int, str] | None = None,
) -> dict[str, str]:
    """Supply each translated profile field with its unit's identity."""

    names = names_by_slave or {}
    return {
        f"{profile_field_key(slave)}_details": unit_detail(
            probes_by_slave.get(slave), names.get(slave)
        )
        for slave in slaves
    }


def stored_unit_probe(room: Mapping[str, Any]) -> UnitProbeResult:
    """Use a stored unit's identity in a flow without another gateway read."""

    product_id = room.get("product_id")
    setup_code = room.get("app_setup_code")
    return UnitProbeResult(
        profile="",
        preview=None,
        product_id=product_id if isinstance(product_id, int) else None,
        app_setup_code=setup_code if isinstance(setup_code, str) else None,
    )


def profile_mismatches(
    profiles_by_slave: Mapping[int, str], probes_by_slave: Mapping[int, UnitProbeResult]
) -> str:
    """List selections that contradict a known product family's catalog entry."""

    expected_prefixes = {PRODUCT_ID_M_WRG: "s_", PRODUCT_ID_M_WRG_II: "ii_"}
    lines: list[str] = []
    for slave, profile in profiles_by_slave.items():
        probe = probes_by_slave.get(slave)
        if probe is None or probe.product_id not in expected_prefixes:
            continue
        if profile.startswith(expected_prefixes[probe.product_id]):
            continue
        product = product_metadata(probe.product_id)
        if product is not None:
            lines.append(f"- **{slave}**: {product.series} → {MODEL_PROFILE_LABELS[profile]}")
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
    # A unit matched to an existing room keeps that room's key, wherever its
    # address now sorts; a new unit must not take it first.
    reserved_room_keys = {
        str(room["key"]) for room in matched_rooms.values() if room.get("key") is not None
    }
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
        existing_key = existing_room.get("key")
        preferred_room_key = str(existing_key or f"slave_{slave}")
        taken = used_room_keys if existing_key is not None else used_room_keys | reserved_room_keys
        room_key = preferred_room_key
        suffix = 2
        while room_key in taken:
            room_key = f"{preferred_room_key}_{suffix}"
            suffix += 1
        used_room_keys.add(room_key)
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
        name = str(
            existing_room.get("name")
            or default_room_name(selected_profile, app_setup_code, used_names)
        )
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
        if not existing_room.get("name") or existing_room.get(CONF_NAME_IS_DEVICE_NAME) is True:
            room[CONF_NAME_IS_DEVICE_NAME] = True
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
    hass: HomeAssistant, port: str | GatewayConnection
) -> AsyncGenerator[Callable[[int], Awaitable[ModbusUnit]], None]:
    """Hold units on the gateway link for one flow step.

    The link closes again on exit unless a loaded entry also holds it.
    """

    connection = SerialGatewayConnection(port) if isinstance(port, str) else port
    params = connection.modbus_params()
    policy = new_transport_policy(timeout=connection.timeout)
    async with AsyncExitStack() as stack:

        async def unit_for(slave: int) -> ModbusUnit:
            try:
                unit = await stack.enter_async_context(
                    async_get_temporary_unit(hass, params, slave)
                )
            except HomeAssistantError as err:
                raise PortInUseError(str(err)) from err
            return prepare_unit(unit, slave, policy, timeout=connection.timeout)

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


def probe_profiles(results: Mapping[int, UnitProbeResult]) -> dict[int, str]:
    return {slave: result.profile for slave, result in results.items()}


async def async_discover_units(
    hass: HomeAssistant, port: str | GatewayConnection
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


async def async_validate_port(hass: HomeAssistant, port: str | GatewayConnection) -> None:
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
        "name": default_room_name(
            profile,
            probe_result.app_setup_code if probe_result is not None else None,
            {str(room["name"]) for room in existing},
        ),
        CONF_NAME_IS_DEVICE_NAME: True,
        CONF_SLAVE: slave,
        CONF_PROFILE: profile,
    }
    if probe_result is not None:
        room["preview"] = probe_result.preview
        room["product_id"] = probe_result.product_id
        room["app_setup_code"] = probe_result.app_setup_code
    return room
