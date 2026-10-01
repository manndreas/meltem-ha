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

import logging
from pathlib import Path

from modbus_connection import (
    ModbusConnectionError,
    ModbusError,
    ModbusSerialParams,
    ModbusTimeoutError,
    ModbusUnit,
)

from .const import (
    BASE_SUPPORTED_ENTITY_KEYS,
    DEFAULT_GATEWAY_DEVICE_ID,
    FIXED_BAUDRATE,
    FIXED_BYTESIZE,
    FIXED_PARITY,
    FIXED_STOPBITS,
    FIXED_TIMEOUT,
    PROFILE_METADATA,
    REQUEST_GAP_SECONDS,
    TRANSPORT_DISCONNECT_AFTER_TIMEOUTS,
    TRANSPORT_LINK_QUIET_SECONDS,
    TRANSPORT_RETRY_DELAY_SECONDS,
)
from .device import MeltemGateway, MeltemProbe, PolicyUnit, TransportPolicy

_LOGGER = logging.getLogger(__name__)

_MAX_NODE_ADDRESSES = 32

# Read in this order by the setup probe; the product ID only feeds the preview.
_PROBE_COMPONENTS = (
    "product_id",
    "humidity_extract_air",
    "humidity_supply_air",
    "co2_extract_air",
    "voc_supply_air",
)

# Value range in which a probed register counts as a fitted sensor.
_PLAUSIBLE_RANGES: dict[str, tuple[int, int]] = {
    "humidity_extract_air": (0, 100),
    "humidity_supply_air": (0, 100),
    "co2_extract_air": (250, 10000),
    "voc_supply_air": (0, 10000),
}

# Richest suffix first: the first one with a detected sensor wins.
_SUFFIXES: tuple[tuple[str, str, frozenset[str]], ...] = (
    ("fc_voc", "VOC", frozenset({"voc_supply_air"})),
    ("fc", "CO2", frozenset({"co2_extract_air"})),
    ("f", "humidity", frozenset({"humidity_extract_air", "humidity_supply_air"})),
)

# Units with any sensor suffix also report these temperatures.
_SUFFIX_ENTITY_KEYS = frozenset({"outdoor_air_temperature", "extract_air_temperature"})

_CAPABILITY_ENTITY_KEYS: dict[str, frozenset[str]] = {
    "humidity": frozenset(
        {
            "supply_air_temperature",
            "humidity_extract_air",
            "humidity_supply_air",
            "humidity_starting_point",
            "humidity_min_level",
            "humidity_max_level",
        }
    ),
    "co2": frozenset(
        {"co2_extract_air", "co2_starting_point", "co2_min_level", "co2_max_level"}
    ),
    "voc": frozenset({"voc_supply_air"}),
}


# ---------------------------------------------------------------------------
#  Exceptions
# ---------------------------------------------------------------------------


class MeltemModbusError(Exception):
    """Raised when Meltem Modbus communication fails."""


class MeltemConnectionError(MeltemModbusError):
    """Raised when the serial connection to the gateway cannot be established.

    Optional reads swallow ordinary Modbus errors, but never this one: a dead
    transport must reach the coordinator instead of looking like "no change".
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

    return ModbusSerialParams(
        device=port,
        baudrate=FIXED_BAUDRATE,
        bytesize=FIXED_BYTESIZE,
        parity=FIXED_PARITY,
        stopbits=FIXED_STOPBITS,
    )


def new_transport_policy() -> TransportPolicy:
    """Return the retry policy shared by all units on one gateway link."""

    return TransportPolicy(
        disconnect_after_timeouts=TRANSPORT_DISCONNECT_AFTER_TIMEOUTS,
        link_quiet_seconds=TRANSPORT_LINK_QUIET_SECONDS,
        connection_retry_delay=TRANSPORT_RETRY_DELAY_SECONDS,
    )


def prepare_unit(unit: ModbusUnit, unit_id: int, policy: TransportPolicy) -> PolicyUnit:
    """Ask the link for the gateway's timing and wrap the unit in the retry policy.

    The timeout must be asked for before the first request, since a lower
    timeout only takes effect on the next connect.
    """

    unit.set_message_spacing(REQUEST_GAP_SECONDS)
    unit.require_timeout(FIXED_TIMEOUT)
    return PolicyUnit(unit, unit_id, policy)


# ---------------------------------------------------------------------------
#  Gateway node discovery
# ---------------------------------------------------------------------------


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
    unit: ModbusUnit, port: str, *, start: int, end: int
) -> list[int]:
    """Discover configured unit addresses via the Airios-style bridge registers.

    Raises ``MeltemConnectionError`` when the serial link cannot be opened; a
    gateway that does not answer just yields no units.
    """

    gateway = MeltemGateway(unit)
    try:
        node_count = await gateway.async_read_node_count()
    except ModbusError as err:
        return _discovery_failed(err, port, "node count")
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
        return _discovery_failed(err, port, "node address list")

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


def _discovery_failed(err: ModbusError, port: str, what: str) -> list[int]:
    """Raise a dead link as ``MeltemConnectionError``; log any other error as no units."""

    if isinstance(err, ModbusConnectionError):
        raise MeltemConnectionError(f"Could not open serial connection on {port}: {err}") from err
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


async def detect_slave_details(unit: ModbusUnit) -> tuple[str, str | None]:
    """Run the minimal setup-time probe on one unit.

    Returns the detected sensor suffix and a short preview for the setup form.
    The entities follow the profile the user picks, not the probe.

    Raises ``MeltemConnectionError`` when the serial link is down; every other
    read error just leaves that capability undetected.
    """

    probe = MeltemProbe(unit)
    values: dict[str, int] = {}
    for name in _PROBE_COMPONENTS:
        component = getattr(probe, name)
        try:
            await component.async_update()
        except ModbusConnectionError as err:
            raise MeltemConnectionError(str(err)) from err
        except ModbusTimeoutError:
            # A silent unit would only time out on every further probe.
            break
        except ModbusError:
            continue
        values[name] = component.product_id if name == "product_id" else component.value

    detected = {key for key in _PLAUSIBLE_RANGES if _is_plausible(key, values.get(key))}
    detected_profile, capability_preview = next(
        (
            (suffix, preview)
            for suffix, preview, keys in _SUFFIXES
            if keys & detected
        ),
        ("plain", "basic"),
    )
    product_id = values.get("product_id")
    preview = (
        capability_preview
        if product_id is None
        else f"ID {product_id} | {capability_preview}"
    )
    return detected_profile, preview


# ---------------------------------------------------------------------------
#  Entity-key / plausibility helpers
# ---------------------------------------------------------------------------


def supported_entity_keys_for_profile(profile: str) -> list[str]:
    """Return the supported entity keys implied by one selected profile."""

    metadata = PROFILE_METADATA.get(profile)
    capabilities = metadata.capabilities if metadata is not None else frozenset()

    supported_entity_keys = set(BASE_SUPPORTED_ENTITY_KEYS)
    if capabilities:
        supported_entity_keys |= _SUFFIX_ENTITY_KEYS
    for capability, entity_keys in _CAPABILITY_ENTITY_KEYS.items():
        if capability in capabilities:
            supported_entity_keys |= entity_keys
    return sorted(supported_entity_keys)


def _is_plausible(key: str, value: int | None) -> bool:
    low, high = _PLAUSIBLE_RANGES[key]
    return value is not None and low <= value <= high


def derive_balanced_airflow(
    extract_air_flow: int | None,
    supply_air_flow: int | None,
) -> int | None:
    """Return one shared airflow value when supply and extract match closely."""

    if extract_air_flow is None and supply_air_flow is None:
        return None
    if extract_air_flow is None:
        return supply_air_flow
    if supply_air_flow is None:
        return extract_air_flow
    if abs(extract_air_flow - supply_air_flow) <= 1:
        return round((extract_air_flow + supply_air_flow) / 2)
    return None
