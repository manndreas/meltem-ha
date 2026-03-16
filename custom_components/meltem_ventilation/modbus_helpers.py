"""Setup-time helpers and shared utilities for Meltem Modbus access.

This module contains all functions that do **not** require the long-lived
:class:`MeltemModbusClient` runtime object:

* serial-settings helpers (build / validate / resolve)
* gateway node discovery
* setup-time profile probes
* plausibility checks and pure helper functions

The config flow imports exclusively from here. The runtime client in
``modbus_client.py`` also imports the shared helpers it needs.
"""

from __future__ import annotations

import logging
import struct
import time
from dataclasses import dataclass
from pathlib import Path

from pymodbus.client import ModbusSerialClient

from .const import (
    BASE_SUPPORTED_ENTITY_KEYS,
    DEFAULT_GATEWAY_DEVICE_ID,
    PROFILE_METADATA,
    REQUEST_GAP_SECONDS,
    SCAN_TIMEOUT,
    REGISTER_CO2_EXTRACT_AIR,
    REGISTER_GATEWAY_NODE_ADDRESS_1,
    REGISTER_GATEWAY_NUMBER_OF_NODES,
    REGISTER_HUMIDITY_EXTRACT_AIR,
    REGISTER_HUMIDITY_SUPPLY_AIR,
    REGISTER_PRODUCT_ID,
    REGISTER_VOC_SUPPLY_AIR,
    SETUP_PROBE_TIMEOUT,
)

_LOGGER = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
#  Exception
# ---------------------------------------------------------------------------


class MeltemModbusError(Exception):
    """Raised when Meltem Modbus communication fails."""


# ---------------------------------------------------------------------------
#  Serial settings
# ---------------------------------------------------------------------------


@dataclass(slots=True, frozen=True)
class SerialSettings:
    """Serial settings for the Meltem gateway."""

    port: str
    baudrate: int
    bytesize: int
    parity: str
    stopbits: int
    timeout: float


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


def validate_serial_connection(settings: SerialSettings) -> None:
    """Check whether the configured serial connection can be opened."""

    client = build_client(settings)
    try:
        if not client.connect():
            raise MeltemModbusError(
                f"Could not open serial connection on {settings.port}"
            )
    finally:
        client.close()


def build_scan_settings(settings: SerialSettings) -> SerialSettings:
    """Use a shorter timeout for device discovery scans."""

    return SerialSettings(
        port=settings.port,
        baudrate=settings.baudrate,
        bytesize=settings.bytesize,
        parity=settings.parity,
        stopbits=settings.stopbits,
        timeout=min(settings.timeout, SCAN_TIMEOUT),
    )


def build_setup_probe_settings(settings: SerialSettings) -> SerialSettings:
    """Use a moderate timeout for setup-time previews and profile detection."""

    return SerialSettings(
        port=settings.port,
        baudrate=settings.baudrate,
        bytesize=settings.bytesize,
        parity=settings.parity,
        stopbits=settings.stopbits,
        timeout=min(settings.timeout, SETUP_PROBE_TIMEOUT),
    )


# ---------------------------------------------------------------------------
#  Client builder
# ---------------------------------------------------------------------------


def build_client(settings: SerialSettings) -> ModbusSerialClient:
    """Create a new pymodbus serial client from the given settings."""

    return ModbusSerialClient(
        port=settings.port,
        baudrate=settings.baudrate,
        bytesize=settings.bytesize,
        parity=settings.parity,
        stopbits=settings.stopbits,
        timeout=settings.timeout,
    )


# ---------------------------------------------------------------------------
#  Setup-time profile detection
# ---------------------------------------------------------------------------


def detect_slave_details(
    settings: SerialSettings, slave: int
) -> tuple[str, str | None, list[str]]:
    """Best-effort setup-time profile detection with a minimal register probe."""

    client = build_client(settings)

    try:
        if not client.connect():
            return "plain", None, _base_supported_entity_keys()
        return detect_slave_details_with_client(client, slave)
    finally:
        client.close()


def detect_slave_details_with_client(
    client: ModbusSerialClient,
    slave: int,
) -> tuple[str, str | None, list[str]]:
    """Run the minimal setup-time probe on an already open client.

    The probe only answers two questions:
    - which suffix capabilities does this unit expose
    - which entities should Home Assistant create for it
    """

    supported_entity_keys = set(_base_supported_entity_keys())
    product_id = _safe_read_uint32_word_swap(
        client,
        slave,
        REGISTER_PRODUCT_ID,
    )

    humidity_extract_air = _safe_read_uint16(
        client, slave, REGISTER_HUMIDITY_EXTRACT_AIR
    )
    if _is_plausible_humidity(humidity_extract_air):
        supported_entity_keys.add("humidity_extract_air")

    humidity_supply_air = _safe_read_uint16(
        client, slave, REGISTER_HUMIDITY_SUPPLY_AIR
    )
    if _is_plausible_humidity(humidity_supply_air):
        supported_entity_keys.add("humidity_supply_air")

    co2_extract_air = _safe_read_uint16(client, slave, REGISTER_CO2_EXTRACT_AIR)
    if _is_plausible_co2(co2_extract_air):
        supported_entity_keys.add("co2_extract_air")

    voc_supply_air = _safe_read_uint16(client, slave, REGISTER_VOC_SUPPLY_AIR)
    if _is_plausible_voc(voc_supply_air):
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
#  Gateway node discovery
# ---------------------------------------------------------------------------


def scan_available_slaves(
    settings: SerialSettings, *, start: int, end: int
) -> list[int]:
    """Discover configured unit addresses via the gateway bridge registers."""

    client = build_client(settings)

    try:
        if not client.connect():
            raise MeltemModbusError(
                f"Could not open serial connection on {settings.port}"
            )

        _LOGGER.info(
            "Starting Meltem gateway-backed unit discovery on %s",
            settings.port,
        )

        discovered = discover_gateway_nodes(client, settings.port, start=start, end=end)
        if not discovered:
            _LOGGER.info(
                "Meltem gateway-backed unit discovery on %s found no configured units",
                settings.port,
            )
            return []

        _LOGGER.info(
            "Meltem gateway-backed unit discovery on %s found configured unit addresses: %s",
            settings.port,
            discovered,
        )
        return discovered
    finally:
        client.close()


def discover_gateway_nodes(
    client: ModbusSerialClient, port: str, *, start: int, end: int
) -> list[int]:
    """Try to discover configured units via Airios-style bridge registers."""

    try:
        count_response = client.read_holding_registers(
            address=REGISTER_GATEWAY_NUMBER_OF_NODES,
            count=1,
            device_id=DEFAULT_GATEWAY_DEVICE_ID,
        )
    except Exception as err:
        _LOGGER.warning(
            "Meltem gateway discovery on %s via device %s raised %r while reading node count",
            port,
            DEFAULT_GATEWAY_DEVICE_ID,
            err,
        )
        time.sleep(REQUEST_GAP_SECONDS)
        return []

    time.sleep(REQUEST_GAP_SECONDS)

    if (
        count_response is None
        or count_response.isError()
        or not getattr(count_response, "registers", None)
    ):
        _LOGGER.warning(
            "Meltem gateway discovery on %s via device %s returned no readable node count",
            port,
            DEFAULT_GATEWAY_DEVICE_ID,
        )
        return []

    node_count = int(count_response.registers[0])
    if node_count <= 0:
        _LOGGER.warning(
            "Meltem gateway discovery on %s via device %s reported zero configured units",
            port,
            DEFAULT_GATEWAY_DEVICE_ID,
        )
        return []

    address_count = max(1, min(32, node_count))

    try:
        addresses_response = client.read_holding_registers(
            address=REGISTER_GATEWAY_NODE_ADDRESS_1,
            count=address_count,
            device_id=DEFAULT_GATEWAY_DEVICE_ID,
        )
    except Exception as err:
        _LOGGER.warning(
            "Meltem gateway discovery on %s via device %s raised %r while reading node addresses",
            port,
            DEFAULT_GATEWAY_DEVICE_ID,
            err,
        )
        time.sleep(REQUEST_GAP_SECONDS)
        return []

    time.sleep(REQUEST_GAP_SECONDS)

    if (
        addresses_response is None
        or addresses_response.isError()
        or not getattr(addresses_response, "registers", None)
    ):
        _LOGGER.warning(
            "Meltem gateway discovery on %s via device %s returned no readable node address list",
            port,
            DEFAULT_GATEWAY_DEVICE_ID,
        )
        return []

    discovered: list[int] = []
    for raw_address in addresses_response.registers:
        address = int(raw_address)
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
#  Best-effort read helpers (setup-time only)
# ---------------------------------------------------------------------------


def _safe_read_uint16(
    client: ModbusSerialClient, slave: int, address: int
) -> int | None:
    """Best-effort uint16 read for setup previews."""

    try:
        response = client.read_holding_registers(
            address=address, count=1, device_id=slave
        )
        time.sleep(REQUEST_GAP_SECONDS)
    except Exception:
        return None

    if response is None or response.isError():
        return None

    registers = getattr(response, "registers", None)
    if not registers or len(registers) < 1:
        return None

    return registers[0]


def _safe_read_uint32_word_swap(
    client: ModbusSerialClient, slave: int, address: int
) -> int | None:
    """Best-effort uint32 read for setup previews."""

    try:
        response = client.read_holding_registers(
            address=address, count=2, device_id=slave
        )
        time.sleep(REQUEST_GAP_SECONDS)
    except Exception:
        return None

    if response is None or response.isError():
        return None

    registers = getattr(response, "registers", None)
    if not registers or len(registers) < 2:
        return None

    try:
        return struct.unpack(">I", struct.pack(">HH", registers[1], registers[0]))[0]
    except Exception:
        return None


# ---------------------------------------------------------------------------
#  Entity-key / plausibility helpers
# ---------------------------------------------------------------------------


def _base_supported_entity_keys() -> set[str]:
    """Return entities that are generally meaningful for all units."""

    return set(BASE_SUPPORTED_ENTITY_KEYS)


def supported_entity_keys_for_profile(profile: str) -> list[str]:
    """Return the supported entity keys implied by one selected profile."""

    supported_entity_keys = set(_base_supported_entity_keys())
    capabilities = PROFILE_METADATA.get(profile, {}).get("capabilities", frozenset())

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
