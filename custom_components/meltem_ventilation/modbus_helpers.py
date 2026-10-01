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


# ---------------------------------------------------------------------------
#  Exception
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

    gateway = MeltemGateway(unit)
    try:
        await gateway.node_count.async_update()
    except ModbusConnectionError as err:
        raise MeltemConnectionError(str(err)) from err
    except ModbusError as err:
        raise MeltemModbusError(str(err)) from err
    return int(gateway.node_count.value or 0)


async def discover_gateway_nodes(
    unit: ModbusUnit, port: str, *, start: int, end: int
) -> list[int]:
    """Discover configured unit addresses via the Airios-style bridge registers.

    Raises ``MeltemConnectionError`` when the serial link cannot be opened; a
    gateway that does not answer just yields no units.
    """

    gateway = MeltemGateway(unit)
    try:
        await gateway.node_count.async_update()
    except ModbusConnectionError as err:
        raise MeltemConnectionError(
            f"Could not open serial connection on {port}: {err}"
        ) from err
    except ModbusError as err:
        _LOGGER.warning(
            "Meltem gateway discovery on %s via device %s could not read the node count: %s",
            port,
            DEFAULT_GATEWAY_DEVICE_ID,
            err,
        )
        return []

    node_count = int(gateway.node_count.value or 0)
    if node_count <= 0:
        _LOGGER.warning(
            "Meltem gateway discovery on %s via device %s reported zero configured units",
            port,
            DEFAULT_GATEWAY_DEVICE_ID,
        )
        return []

    try:
        addresses = await gateway.async_read_node_addresses(max(1, min(32, node_count)))
    except ModbusConnectionError as err:
        raise MeltemConnectionError(
            f"Could not open serial connection on {port}: {err}"
        ) from err
    except ModbusError as err:
        _LOGGER.warning(
            "Meltem gateway discovery on %s via device %s could not read the node address list: %s",
            port,
            DEFAULT_GATEWAY_DEVICE_ID,
            err,
        )
        return []

    discovered: list[int] = []
    for address in addresses:
        if address == 0:
            continue
        if not (start <= address <= end):
            _LOGGER.warning(
                "Ignoring configured unit address %s from gateway on %s because it is outside %s..%s",
                address,
                port,
                start,
                end,
            )
            continue
        if address not in discovered:
            discovered.append(address)

    return discovered


# ---------------------------------------------------------------------------
#  Setup-time profile detection
# ---------------------------------------------------------------------------


async def detect_slave_details(unit: ModbusUnit) -> tuple[str, str | None, list[str]]:
    """Run the minimal setup-time probe on one unit.

    The probe only answers two questions:
    - which suffix capabilities does this unit expose
    - which entities should Home Assistant create for it

    Raises ``MeltemConnectionError`` when the serial link is down; every other
    read error just leaves that capability undetected.
    """

    probe = MeltemProbe(unit)
    answered: set[str] = set()
    for name in (
        "product_id",
        "humidity_extract_air",
        "humidity_supply_air",
        "co2_extract_air",
        "voc_supply_air",
    ):
        try:
            await getattr(probe, name).async_update()
        except ModbusConnectionError as err:
            raise MeltemConnectionError(str(err)) from err
        except ModbusTimeoutError:
            # A silent unit would only time out on every further probe.
            break
        except ModbusError:
            continue
        answered.add(name)

    def _probed(name: str) -> int | None:
        if name not in answered:
            return None
        component = getattr(probe, name)
        return component.product_id if name == "product_id" else component.value

    supported_entity_keys = set(_base_supported_entity_keys())
    if _is_plausible_humidity(_probed("humidity_extract_air")):
        supported_entity_keys.add("humidity_extract_air")
    if _is_plausible_humidity(_probed("humidity_supply_air")):
        supported_entity_keys.add("humidity_supply_air")
    if _is_plausible_co2(_probed("co2_extract_air")):
        supported_entity_keys.add("co2_extract_air")
    if _is_plausible_voc(_probed("voc_supply_air")):
        supported_entity_keys.add("voc_supply_air")

    # The suffix can be inferred from the optional sensor set alone.
    if "voc_supply_air" in supported_entity_keys:
        detected_profile = "fc_voc"
    elif "co2_extract_air" in supported_entity_keys:
        detected_profile = "fc"
    elif (
        "humidity_extract_air" in supported_entity_keys
        or "humidity_supply_air" in supported_entity_keys
    ):
        detected_profile = "f"
    else:
        detected_profile = "plain"

    preview_parts: list[str] = []
    product_id = _probed("product_id")
    if product_id is not None:
        preview_parts.append(f"ID {product_id}")
    capability_preview = {
        "fc_voc": "VOC",
        "fc": "CO2",
        "f": "humidity",
        "plain": "basic",
    }[detected_profile]
    preview_parts.append(capability_preview)

    preview = " | ".join(preview_parts) if preview_parts else None

    return detected_profile, preview, sorted(supported_entity_keys)


# ---------------------------------------------------------------------------
#  Entity-key / plausibility helpers
# ---------------------------------------------------------------------------


def _base_supported_entity_keys() -> set[str]:
    """Return entities that are generally meaningful for all units."""

    return set(BASE_SUPPORTED_ENTITY_KEYS)


def supported_entity_keys_for_profile(profile: str) -> list[str]:
    """Return the supported entity keys implied by one selected profile."""

    supported_entity_keys = set(_base_supported_entity_keys())
    metadata = PROFILE_METADATA.get(profile)
    capabilities = metadata.capabilities if metadata is not None else frozenset()

    if capabilities:
        supported_entity_keys.update(
            {
                "outdoor_air_temperature",
                "extract_air_temperature",
            }
        )

    if "humidity" in capabilities:
        supported_entity_keys.update(
            {
                "supply_air_temperature",
                "humidity_extract_air",
                "humidity_supply_air",
                "humidity_starting_point",
                "humidity_min_level",
                "humidity_max_level",
            }
        )
    if "co2" in capabilities:
        supported_entity_keys.update(
            {
                "co2_extract_air",
                "co2_starting_point",
                "co2_min_level",
                "co2_max_level",
            }
        )
    if "voc" in capabilities:
        supported_entity_keys.add("voc_supply_air")

    return sorted(supported_entity_keys)


def _is_plausible_humidity(value: int | None) -> bool:
    return value is not None and 0 <= value <= 100


def _is_plausible_co2(value: int | None) -> bool:
    return value is not None and 250 <= value <= 10000


def _is_plausible_voc(value: int | None) -> bool:
    return value is not None and 0 <= value <= 10000


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
