"""Setup-time helpers and shared utilities for Meltem Modbus access.

This module contains everything that does **not** need the long-lived
:class:`MeltemModbusClient` runtime object:

* serial link parameters and unit preparation
* gateway node discovery
* setup-time profile probes
* plausibility checks and pure helper functions

The config flow imports exclusively from here. The runtime client in
``modbus_client.py`` also imports the shared helpers it needs.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from enum import Enum, auto
from pathlib import Path
from typing import cast

from modbus_connection import (
    ModbusConnectionError,
    ModbusError,
    ModbusProtocolError,
    ModbusSerialParams,
    ModbusTimeoutError,
    ModbusUnit,
)

from .const import (
    BASE_SUPPORTED_ENTITY_KEYS,
    CONTROL_SETTING_LIMITS,
    CONTROL_SETTING_REGISTERS,
    DEFAULT_GATEWAY_DEVICE_ID,
    FIXED_TIMEOUT,
    PROFILE_METADATA,
    REQUEST_GAP_SECONDS,
    TRANSPORT_DISCONNECT_AFTER_TIMEOUTS,
    TRANSPORT_LINK_QUIET_SECONDS,
    TRANSPORT_READ_RECOVERY_SECONDS,
    TRANSPORT_RETRY_DELAY_SECONDS,
    product_metadata,
)
from .device import MeltemGateway, MeltemProbe, PolicyUnit, TransportPolicy
from .device.transport import RequestOutcome
from .gateway_connection import SerialGatewayConnection
from .models import UnitProbeResult

_LOGGER = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
#  Exceptions
# ---------------------------------------------------------------------------


class MeltemModbusError(Exception):
    """Raised when Meltem Modbus communication fails."""


class MeltemConnectionError(MeltemModbusError):
    """Raised when the serial connection to the gateway cannot be established.

    Room component and mode reads swallow ordinary Modbus errors, but never
    this one: a dead transport must reach the coordinator instead of looking
    like "no change".
    """


# ---------------------------------------------------------------------------
#  Serial link
# ---------------------------------------------------------------------------


def resolve_preferred_port_path(port: str) -> str:
    """Prefer a stable /dev/serial/by-id path when one points to the same device."""

    if port.startswith("/dev/serial/by-id/"):
        return port

    serial_by_id_dir = Path("/dev/serial/by-id")
    port_path = Path(port)

    if not serial_by_id_dir.exists() or not port_path.exists():
        return port

    try:
        resolved_port = port_path.resolve()
    except OSError:
        return port

    for candidate in sorted(serial_by_id_dir.iterdir()):
        try:
            if candidate.resolve() == resolved_port:
                return str(candidate)
        except OSError:
            continue

    return port


def build_serial_params(port: str) -> ModbusSerialParams:
    """Return the fixed serial link parameters of the Meltem gateway."""

    return SerialGatewayConnection(port).modbus_params()


def new_transport_policy(
    *,
    timeout: float = FIXED_TIMEOUT,
    on_request: Callable[[RequestOutcome], None] | None = None,
    on_link_recycle: Callable[[], None] | None = None,
) -> TransportPolicy:
    """Return the retry policy shared by all units on one gateway link."""

    return TransportPolicy(
        disconnect_after_timeouts=TRANSPORT_DISCONNECT_AFTER_TIMEOUTS,
        link_quiet_seconds=max(
            TRANSPORT_LINK_QUIET_SECONDS,
            timeout * TRANSPORT_DISCONNECT_AFTER_TIMEOUTS + TRANSPORT_READ_RECOVERY_SECONDS,
        ),
        connection_retry_delay=TRANSPORT_RETRY_DELAY_SECONDS,
        on_request=on_request,
        on_link_recycle=on_link_recycle,
    )


def prepare_unit(
    unit: ModbusUnit,
    unit_id: int,
    policy: TransportPolicy,
    *,
    message_spacing: float = REQUEST_GAP_SECONDS,
    timeout: float = FIXED_TIMEOUT,
) -> ModbusUnit:
    """Ask the link for the gateway's timing and wrap the unit in the retry policy.

    The timeout must be asked for before the first request, since a lower
    timeout only takes effect on the next connect. Only the gateway tools pass
    another ``message_spacing``.
    """

    unit.set_message_spacing(message_spacing)
    unit.require_timeout(timeout)
    # PolicyUnit overrides the register requests the integration sends and
    # hands every other ModbusUnit member to the wrapped unit via __getattr__,
    # which the protocol check cannot see.
    return cast(ModbusUnit, PolicyUnit(unit, unit_id, policy))


# ---------------------------------------------------------------------------
#  Gateway node discovery
# ---------------------------------------------------------------------------

_MAX_NODE_ADDRESSES = 32


async def read_gateway_node_count(unit: ModbusUnit) -> int:
    """Read how many units the gateway is configured for.

    Raises ``MeltemConnectionError`` when the serial link cannot be opened and
    ``MeltemModbusError`` when the gateway does not answer.
    """

    try:
        return await MeltemGateway(unit).async_read_node_count()
    except ModbusConnectionError as err:
        raise MeltemConnectionError(str(err)) from err
    except ModbusError as err:
        raise MeltemModbusError(str(err)) from err


async def discover_gateway_nodes(
    unit: ModbusUnit, port: str, *, start: int, end: int, strict: bool = False
) -> list[int]:
    """Discover configured unit addresses via the Airios-style bridge registers.

    Raises ``MeltemConnectionError`` when the serial link cannot be opened; a
    gateway that does not answer just yields no units, unless ``strict`` asks
    for a ``MeltemModbusError`` instead, so a failed check cannot pass for an
    empty unit list.
    """

    gateway = MeltemGateway(unit)
    try:
        node_count = await gateway.async_read_node_count()
    except ModbusError as err:
        return _discovery_failed(err, unit, port, "node count", strict=strict)
    if node_count <= 0:
        _LOGGER.warning(
            "Meltem gateway discovery on %s via device %s reported zero configured units",
            port,
            DEFAULT_GATEWAY_DEVICE_ID,
        )
        return []

    try:
        addresses = await gateway.async_read_node_addresses(min(_MAX_NODE_ADDRESSES, node_count))
    except ModbusError as err:
        return _discovery_failed(err, unit, port, "node address list", strict=strict)

    discovered: list[int] = []
    for address in addresses:
        if address == 0 or address in discovered:
            continue
        if start <= address <= end:
            discovered.append(address)
            continue
        _LOGGER.warning(
            "Ignoring configured unit address %s from gateway on %s because it is outside %s..%s",
            address,
            port,
            start,
            end,
        )
    return discovered


def _discovery_failed(
    err: ModbusError, unit: ModbusUnit, port: str, what: str, *, strict: bool
) -> list[int]:
    """Raise a dead link as ``MeltemConnectionError``; log any other error as no units."""

    if isinstance(err, ModbusConnectionError) or (
        isinstance(err, ModbusTimeoutError) and not unit.connected
    ):
        raise MeltemConnectionError(f"Could not open connection on {port}: {err}") from err
    if strict:
        raise MeltemModbusError(f"Could not read the {what} of the gateway: {err}") from err
    _LOGGER.warning(
        "Meltem gateway discovery on %s via device %s could not read the %s: %s",
        port,
        DEFAULT_GATEWAY_DEVICE_ID,
        what,
        err,
    )
    return []


# ---------------------------------------------------------------------------
#  Setup-time profile detection
# ---------------------------------------------------------------------------

# Read in this order by the setup probe; the product ID does not determine capabilities.
# Only the sensors of the manufacturer's matrix and the supply-air VOC sensor
# decide the profile; the app-only registers 41008 and 41012 are not probed.
_PROBE_COMPONENTS = (
    "product_id",
    "humidity_extract_air",
    "humidity_supply_air",
    "co2_extract_air",
    "voc_supply_air",
)

# Value range in which a probed or polled register counts as a fitted sensor.
PLAUSIBLE_RANGES: dict[str, tuple[int, int]] = {
    "humidity_extract_air": (0, 100),
    "humidity_supply_air": (0, 100),
    "co2_extract_air": (250, 10000),
    "co2_supply_air": (250, 10000),
    "voc_extract_air": (0, 10000),
    "voc_supply_air": (0, 10000),
}

# Richest suffix first: the first one with a detected sensor wins.
_SUFFIXES: tuple[tuple[str, str, frozenset[str]], ...] = (
    ("fc_voc", "VOC", frozenset({"voc_supply_air"})),
    ("fc", "CO2", frozenset({"co2_extract_air"})),
    ("f", "humidity", frozenset({"humidity_extract_air", "humidity_supply_air"})),
)

# Patched by tests; the probe waits out the transport's recovery pause before a retry.
async_sleep = asyncio.sleep


class _ProbeOutcome(Enum):
    VALUE = auto()
    # The unit answered with an exception response, so the register is absent.
    ABSENT = auto()
    SILENT = auto()


async def _probe_read[T](
    unit: ModbusUnit, read: Callable[[], Awaitable[T]], *, answered: bool
) -> tuple[_ProbeOutcome, T | None]:
    """Run one probe read and classify how the unit answered.

    A lost or garbled reply pauses further reads of the unit in the transport,
    so the retry waits for that pause. A unit that never answered is not
    retried after a timeout: it is most likely switched off, and every further
    read would only time out as well.
    """

    retried = False
    while True:
        try:
            return _ProbeOutcome.VALUE, await read()
        except ModbusConnectionError as err:
            raise MeltemConnectionError(str(err)) from err
        except ModbusTimeoutError as err:
            # A timeout without an open link failed to connect, for example to a bridge.
            if not unit.connected:
                raise MeltemConnectionError(str(err)) from err
            if retried or not answered:
                return _ProbeOutcome.SILENT, None
        except ModbusProtocolError:
            # Paused after an earlier lost reply, or a garbled answer arrived.
            if retried:
                return _ProbeOutcome.SILENT, None
        except ModbusError:
            return _ProbeOutcome.ABSENT, None
        retried = True
        await async_sleep(TRANSPORT_READ_RECOVERY_SECONDS)


async def detect_slave_details(unit: ModbusUnit) -> UnitProbeResult:
    """Run the minimal setup-time probe on one unit.

    Returns the detected sensor suffix and a short preview for the setup form.
    The entities follow the profile the user picks, not the probe.

    Raises ``MeltemConnectionError`` when the link is down or cannot be opened;
    an exception response just leaves that capability undetected. A unit that
    stays silent is given up, but one lost or garbled reply of a unit that is
    answering is read once more, so it does not turn a sensor unit into a
    plain one.
    """

    probe = MeltemProbe(unit)
    values: dict[str, int] = {}
    answered = silent = False
    for name in _PROBE_COMPONENTS:
        component = getattr(probe, name)
        outcome, _ = await _probe_read(unit, component.async_update, answered=answered)
        if outcome is _ProbeOutcome.SILENT:
            silent = True
            break
        answered = True
        if outcome is _ProbeOutcome.VALUE:
            values[name] = component.product_id if name == "product_id" else component.value

    app_setup_code = None
    if not silent:
        _, app_setup_code = await _probe_read(
            unit, probe.async_read_app_setup_code, answered=answered
        )

    detected = {key for key in PLAUSIBLE_RANGES if is_plausible(key, values.get(key))}
    detected_profile, capability_preview = next(
        ((suffix, preview) for suffix, preview, keys in _SUFFIXES if keys & detected),
        ("plain", "basic"),
    )
    product_id = values.get("product_id")
    preview_parts = [f"R-Code {app_setup_code}"] if app_setup_code is not None else []
    if product_id is not None:
        preview_parts.append(f"ID {product_id}")
        if product := product_metadata(product_id):
            preview_parts.append(f"App: {product.series} ({product.model_id})")
    preview_parts.append(capability_preview)
    preview = " | ".join(preview_parts)
    return UnitProbeResult(
        profile=detected_profile,
        preview=preview,
        product_id=product_id,
        app_setup_code=app_setup_code,
    )


# ---------------------------------------------------------------------------
#  Entity-key / plausibility helpers
# ---------------------------------------------------------------------------

# Units with any sensor suffix also report these temperatures.
_SUFFIX_ENTITY_KEYS = frozenset({"outdoor_air_temperature", "extract_air_temperature"})

_CAPABILITY_ENTITY_KEYS: dict[str, frozenset[str]] = {
    "humidity": frozenset(
        {
            "supply_air_temperature",
            "humidity_extract_air",
            "humidity_supply_air",
            "absolute_humidity_extract_air",
            "absolute_humidity_supply_air",
            "humidity_starting_point",
            "humidity_min_level",
            "humidity_max_level",
        }
    ),
    "co2": frozenset({"co2_extract_air", "co2_starting_point", "co2_min_level", "co2_max_level"}),
    # Supply-air CO2 shares the VOC profile's register block, see ENTITY_PROFILES.
    "voc": frozenset({"co2_supply_air", "voc_extract_air", "voc_supply_air"}),
}


def supported_entity_keys_for_profile(profile: str) -> list[str]:
    """Return the supported entity keys implied by one selected profile."""

    metadata = PROFILE_METADATA.get(profile)
    capabilities = metadata.capabilities if metadata is not None else frozenset()

    entity_keys = set(BASE_SUPPORTED_ENTITY_KEYS)
    if capabilities:
        entity_keys |= _SUFFIX_ENTITY_KEYS
    for capability in capabilities:
        entity_keys |= _CAPABILITY_ENTITY_KEYS.get(capability, frozenset())
    return sorted(entity_keys)


def is_plausible(key: str, value: int | None) -> bool:
    """Return whether a sensor register holds a value inside its plausible range."""

    low, high = PLAUSIBLE_RANGES[key]
    return value is not None and low <= value <= high


def normalize_control_setting(setting_key: str, value: int) -> int:
    """Clamp one control setting to its limits and round it to the register step."""

    limits = CONTROL_SETTING_LIMITS.get(setting_key)
    if setting_key not in CONTROL_SETTING_REGISTERS or limits is None:
        raise MeltemModbusError(f"Unsupported control setting {setting_key!r}")
    min_val, max_val, step = limits
    clamped = max(min_val, min(max_val, value))
    return min_val + ((clamped - min_val + step // 2) // step) * step


def derive_balanced_airflow(
    extract_air_flow: int | None,
    supply_air_flow: int | None,
) -> int | None:
    """Return one shared airflow value when supply and extract match closely."""

    if extract_air_flow is None:
        return supply_air_flow
    if supply_air_flow is None:
        return extract_air_flow
    if abs(extract_air_flow - supply_air_flow) <= 1:
        return round((extract_air_flow + supply_air_flow) / 2)
    return None
