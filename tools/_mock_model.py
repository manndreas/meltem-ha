"""Register model of the mock M-WRG-GW gateway and its units.

Behavior that follows a hardware observation names its section in
``docs/dev/MELTEM.md``. Everything else is a mock choice, not evidence of how
a real gateway or unit behaves.
"""

from __future__ import annotations

import json
import struct
import time
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from custom_components.meltem_ventilation.const import (
    APP_UNBALANCED_PRESET_BASE,
    MODE_MANUAL,
    MODE_OFF,
    MODE_SENSOR_CONTROL,
    MODE_UNBALANCED,
    PRESET_MODE_CODE_HIGH,
    PRESET_MODE_CODE_INTENSIVE,
    PRESET_MODE_CODE_LOW,
    PRESET_MODE_CODE_MEDIUM,
    PRODUCT_ID_M_WRG,
    PRODUCT_ID_M_WRG_II,
    PROFILE_METADATA,
    REGISTER_APP_SETUP_CODE,
    REGISTER_APPLY,
    REGISTER_CO2_EXTRACT_AIR,
    REGISTER_CO2_MAX_LEVEL,
    REGISTER_CO2_MIN_LEVEL,
    REGISTER_CO2_STARTING_POINT,
    REGISTER_CO2_SUPPLY_AIR,
    REGISTER_CURRENT_LEVEL,
    REGISTER_DAYS_UNTIL_FILTER_CHANGE,
    REGISTER_ERROR_STATUS,
    REGISTER_EXHAUST_AIR_TEMPERATURE,
    REGISTER_EXTERNAL_INPUT_LEVEL,
    REGISTER_EXTERNAL_INPUT_ON_DELAY,
    REGISTER_EXTERNAL_INPUT_RUN_ON,
    REGISTER_EXTRACT_AIR_FLOW,
    REGISTER_EXTRACT_AIR_TARGET_LEVEL,
    REGISTER_EXTRACT_AIR_TEMPERATURE,
    REGISTER_EXTRACT_MODE_STATUS,
    REGISTER_FAN_MOTOR_HOURS,
    REGISTER_FILTER_CHANGE_DUE,
    REGISTER_FROST_PROTECTION_ACTIVE,
    REGISTER_GATEWAY_MAC_START,
    REGISTER_GATEWAY_NODE_ADDRESS_1,
    REGISTER_GATEWAY_NUMBER_OF_NODES,
    REGISTER_GATEWAY_RF_LOAD_CURRENT_HOUR,
    REGISTER_GATEWAY_RF_LOAD_PREVIOUS_HOUR,
    REGISTER_GATEWAY_RF_MESSAGES_CURRENT_HOUR,
    REGISTER_GATEWAY_RF_MESSAGES_PREVIOUS_HOUR,
    REGISTER_GATEWAY_UPTIME,
    REGISTER_GATEWAY_UTC_CLOCK,
    REGISTER_HUMIDITY_EXTRACT_AIR,
    REGISTER_HUMIDITY_MAX_LEVEL,
    REGISTER_HUMIDITY_MIN_LEVEL,
    REGISTER_HUMIDITY_STARTING_POINT,
    REGISTER_HUMIDITY_SUPPLY_AIR,
    REGISTER_MODE,
    REGISTER_MODE_STATUS,
    REGISTER_OPERATING_HOURS,
    REGISTER_OUTDOOR_AIR_TEMPERATURE,
    REGISTER_PRESET_MODE,
    REGISTER_PRESET_VALUE,
    REGISTER_PRODUCT_ID,
    REGISTER_SUPPLY_AIR_FLOW,
    REGISTER_SUPPLY_AIR_TEMPERATURE,
    REGISTER_SUPPLY_MODE_STATUS,
    REGISTER_VOC_EXTRACT_AIR,
    REGISTER_VOC_SUPPLY_AIR,
    profile_max_airflow,
)

GATEWAY_ADDRESS = 1
MIN_UNIT_ADDRESS = 2
MAX_UNIT_ADDRESS = 16
MAX_REGISTER_COUNT = 125
# What the sensor registers of units without that sensor reported on hardware
# (MELTEM.md, "App-derived register survey on 2026-11-03").
NO_HUMIDITY_VALUE = 239
NO_SENSOR_VALUE = 32767

ILLEGAL_DATA_ADDRESS = 0x02
ILLEGAL_DATA_VALUE = 0x03
ACKNOWLEDGE = 0x05

INITIAL_UPTIME_SECONDS = 12345
REGISTER_MAP_FORMAT = 1

# An inclusive ``(first, last)`` register range.
type RegisterRange = tuple[int, int]

# Single-register islands of the R-2 scan on units 3 and 5 (MELTEM.md,
# "Product registers and readable islands"). A block read that touches any
# other register fails as a whole with Illegal Data Address. 41113 answered in
# the later app-derived survey.
UNIT_READABLE_RANGES: tuple[RegisterRange, ...] = (
    (40000, 40022),
    (40024, 40033),
    (40041, 40041),
    (40050, 40051),
    (40071, 40071),
    (40100, 40104),
    (40109, 40110),
    (40120, 40120),
    (40200, 40209),
    (41000, 41034),
    (41100, 41113),
    (42000, 42009),
)
# The gateway serves only its own registers; 41998..42001 returned 0x02
# (MELTEM.md, "Gateway registers and function codes").
GATEWAY_READABLE_RANGES: tuple[RegisterRange, ...] = (
    (REGISTER_GATEWAY_MAC_START, REGISTER_GATEWAY_UTC_CLOCK + 1),
    (REGISTER_GATEWAY_UPTIME, REGISTER_GATEWAY_UPTIME + 1),
    (REGISTER_GATEWAY_RF_MESSAGES_CURRENT_HOUR, REGISTER_GATEWAY_RF_LOAD_PREVIOUS_HOUR + 1),
    (REGISTER_GATEWAY_NUMBER_OF_NODES, REGISTER_GATEWAY_NODE_ADDRESS_1 + 15),
)
# Set registers refuse reads with Acknowledge until they were written in this
# gateway session (MELTEM.md, "Set-register readback at 41120..41124").
SET_REGISTERS = range(REGISTER_MODE, REGISTER_PRESET_VALUE + 1)
# Blocks a unit answered with Acknowledge during the RF warm-up after the
# gateway connection, while it already answered others (MELTEM.md, "Request
# pacing and block reads", T-8).
WARMUP_ACKNOWLEDGE_RANGES: tuple[RegisterRange, ...] = (
    (41000, 41006),
    (41009, 41011),
    (41016, 41021),
)
# Mock choice for the quick-mode airflow; intensive ran at 80/80 m³/h in W-8.
QUICK_MODE_AIRFLOW = {
    PRESET_MODE_CODE_LOW: 20,
    PRESET_MODE_CODE_MEDIUM: 30,
    PRESET_MODE_CODE_HIGH: 60,
    PRESET_MODE_CODE_INTENSIVE: 80,
}
QUICK_MODE_CODES = {
    "low": PRESET_MODE_CODE_LOW,
    "medium": PRESET_MODE_CODE_MEDIUM,
    "high": PRESET_MODE_CODE_HIGH,
}
# The sensor-control status subcodes are the write values with bit 32 set,
# as in the community decode the integration implements (MELTEM.md, "Status
# values without hardware evidence").
SENSOR_STATUS_BIT = 0x20
STATUS_FLAGS = {
    "error": REGISTER_ERROR_STATUS,
    "filter": REGISTER_FILTER_CHANGE_DUE,
    "frost": REGISTER_FROST_PROTECTION_ACTIVE,
}


class RegisterMapError(ValueError):
    """A register map file cannot be applied."""


def word_swapped_u32(address: int, value: int) -> dict[int, int]:
    return {address: value & 0xFFFF, address + 1: (value >> 16) & 0xFFFF}


def word_swapped_float(address: int, value: float) -> dict[int, int]:
    high_word, low_word = struct.unpack(">HH", struct.pack(">f", value))
    return {address: low_word, address + 1: high_word}


def in_ranges(register: int, ranges: Iterable[RegisterRange]) -> bool:
    return any(first <= register <= last for first, last in ranges)


def parse_register_range(text: str) -> RegisterRange:
    """Parse ``REG`` or ``FIRST-LAST`` into an inclusive range."""

    first_text, _, last_text = text.partition("-")
    try:
        first = int(first_text)
        last = int(last_text) if last_text else first
    except ValueError as err:
        raise ValueError(f"invalid register range {text!r}") from err
    if not 0 <= first <= last <= 0xFFFF:
        raise ValueError(f"invalid register range {text!r}")
    return first, last


def validate_profile(profile: str) -> str:
    if profile not in PROFILE_METADATA:
        raise ValueError(f"unknown profile {profile!r}; choose from {', '.join(PROFILE_METADATA)}")
    return profile


def validate_unit_address(address: int) -> int:
    if not MIN_UNIT_ADDRESS <= address <= MAX_UNIT_ADDRESS:
        raise ValueError(
            f"unit addresses must be between {MIN_UNIT_ADDRESS} and {MAX_UNIT_ADDRESS}"
        )
    return address


def unit_registers(profile: str, app_setup_code: int) -> dict[int, int]:
    """Build the readable registers with sensor values matching ``profile``."""

    registers = {
        address: 0 for first, last in UNIT_READABLE_RANGES for address in range(first, last + 1)
    }
    capabilities = PROFILE_METADATA[profile].capabilities
    product_id = PRODUCT_ID_M_WRG if profile.startswith("s_") else PRODUCT_ID_M_WRG_II
    registers.update(word_swapped_u32(REGISTER_PRODUCT_ID, product_id))
    registers[REGISTER_APP_SETUP_CODE] = app_setup_code
    registers[REGISTER_APP_SETUP_CODE + 1] = 0
    has_humidity = "humidity" in capabilities
    has_voc = "voc" in capabilities
    registers[REGISTER_HUMIDITY_EXTRACT_AIR] = 48 if has_humidity else NO_HUMIDITY_VALUE
    registers[REGISTER_HUMIDITY_SUPPLY_AIR] = 44 if has_humidity else NO_HUMIDITY_VALUE
    registers[REGISTER_CO2_EXTRACT_AIR] = 720 if "co2" in capabilities else NO_SENSOR_VALUE
    registers[REGISTER_VOC_SUPPLY_AIR] = 360 if has_voc else NO_SENSOR_VALUE
    # Only the app's register map knows these two; the VOC profile simulates them.
    registers[REGISTER_VOC_EXTRACT_AIR] = 340 if has_voc else NO_SENSOR_VALUE
    registers[REGISTER_CO2_SUPPLY_AIR] = 650 if has_voc else NO_SENSOR_VALUE
    for address, temperature in (
        (REGISTER_EXTRACT_AIR_TEMPERATURE, 21.5),
        (REGISTER_OUTDOOR_AIR_TEMPERATURE, 8.0),
        (REGISTER_EXHAUST_AIR_TEMPERATURE, 19.0),
        (REGISTER_SUPPLY_AIR_TEMPERATURE, 17.5),
    ):
        registers.update(word_swapped_float(address, temperature))
    registers.update(word_swapped_u32(REGISTER_OPERATING_HOURS, 12345))
    registers.update(word_swapped_u32(REGISTER_FAN_MOTOR_HOURS, 6789))
    registers[REGISTER_DAYS_UNTIL_FILTER_CHANGE] = 120
    registers[REGISTER_EXTRACT_AIR_FLOW] = 40
    registers[REGISTER_SUPPLY_AIR_FLOW] = 40
    registers[REGISTER_HUMIDITY_STARTING_POINT] = 55
    registers[REGISTER_HUMIDITY_MIN_LEVEL] = 20
    registers[REGISTER_HUMIDITY_MAX_LEVEL] = 60
    registers[REGISTER_CO2_STARTING_POINT] = 800
    registers[REGISTER_CO2_MIN_LEVEL] = 30
    registers[REGISTER_CO2_MAX_LEVEL] = 50
    # Factory defaults from docs/dev/reference/modbus.md.
    registers[REGISTER_EXTERNAL_INPUT_LEVEL] = 60
    registers[REGISTER_EXTERNAL_INPUT_ON_DELAY] = 1
    registers[REGISTER_EXTERNAL_INPUT_RUN_ON] = 15
    # Values of unknown meaning seen on every unit in the app-derived survey.
    registers.update({41015: 0xBFF8, 41019: 31, 41113: 425, 42006: 12})
    registers[REGISTER_MODE_STATUS] = MODE_MANUAL
    registers[REGISTER_SUPPLY_MODE_STATUS] = 80
    registers[REGISTER_EXTRACT_MODE_STATUS] = 80
    # The set side starts as the status map reports it, but stays unreadable
    # until written.
    registers.update(
        {
            REGISTER_MODE: MODE_MANUAL,
            REGISTER_CURRENT_LEVEL: 80,
            REGISTER_EXTRACT_AIR_TARGET_LEVEL: 80,
            REGISTER_PRESET_MODE: 0,
            REGISTER_PRESET_VALUE: 0,
            REGISTER_APPLY: 0,
        }
    )
    return registers


def gateway_registers() -> dict[int, int]:
    registers = {
        address: 0 for first, last in GATEWAY_READABLE_RANGES for address in range(first, last + 1)
    }
    registers.update(
        {
            REGISTER_GATEWAY_MAC_START: 0x0200,
            REGISTER_GATEWAY_MAC_START + 1: 0,
            REGISTER_GATEWAY_MAC_START + 2: 1,
            REGISTER_GATEWAY_RF_MESSAGES_CURRENT_HOUR: 75,
            REGISTER_GATEWAY_RF_MESSAGES_PREVIOUS_HOUR: 147,
        }
    )
    registers.update(word_swapped_float(REGISTER_GATEWAY_RF_LOAD_CURRENT_HOUR, 18.5))
    registers.update(word_swapped_float(REGISTER_GATEWAY_RF_LOAD_PREVIOUS_HOUR, 12.25))
    return registers


def load_register_map(path: str | Path) -> dict[str, Any]:
    """Read a register map or a ``capture_setting_family`` capture."""

    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as err:
        raise RegisterMapError(f"cannot read register map {path}: {err}") from err
    if not isinstance(data, dict):
        raise RegisterMapError(f"register map {path} must contain a JSON object")
    return data


def _airflow(raw_level: int, max_airflow: int, *, quick_modes: bool = True) -> int:
    """Return the airflow for a raw target, quick-mode code, or app preset value."""

    if quick_modes and raw_level in QUICK_MODE_AIRFLOW:
        return min(QUICK_MODE_AIRFLOW[raw_level], max_airflow)
    if 0 <= raw_level <= 200:
        return round(raw_level * max_airflow / 200)
    return min((raw_level - APP_UNBALANCED_PRESET_BASE) * 10, max_airflow)


def _status(
    mode: int, supply: int, extract: int, extract_airflow: int | None, supply_airflow: int | None
) -> dict[int, int]:
    updates = {
        REGISTER_MODE_STATUS: mode,
        REGISTER_SUPPLY_MODE_STATUS: supply,
        REGISTER_EXTRACT_MODE_STATUS: extract,
    }
    if extract_airflow is not None and supply_airflow is not None:
        updates[REGISTER_EXTRACT_AIR_FLOW] = extract_airflow
        updates[REGISTER_SUPPLY_AIR_FLOW] = supply_airflow
    return updates


@dataclass(slots=True)
class _Device:
    profile: str | None
    registers: dict[int, int]
    readable_ranges: tuple[RegisterRange, ...]
    readable_extra: set[int] = field(default_factory=set)
    refused: set[int] = field(default_factory=set)
    written: set[int] = field(default_factory=set)
    pending: tuple[float, dict[int, int]] | None = None

    def readable(self, register: int) -> bool:
        return (
            in_ranges(register, self.readable_ranges)
            or register in self.readable_extra
            or (self.profile is not None and register in SET_REGISTERS)
        )


class MockGatewayModel:
    """Registers and modeled behavior of one gateway and its units.

    ``clock`` drives the warm-up, settle delay and uptime; ``wall_clock`` the
    gateway's UTC clock. Both are injectable so tests can step time.
    """

    def __init__(
        self,
        units: Mapping[int, str],
        *,
        no_units: bool = False,
        silent_units: Iterable[int] = (),
        warmup_seconds: float = 0.0,
        settle_seconds: float = 0.0,
        clock: Callable[[], float] = time.monotonic,
        wall_clock: Callable[[], float] = time.time,
    ) -> None:
        if warmup_seconds < 0 or settle_seconds < 0:
            raise ValueError("warm-up and settle times must be non-negative")
        self._clock = clock
        self._wall_clock = wall_clock
        self.warmup_seconds = warmup_seconds
        self.settle_seconds = settle_seconds
        self.clock_offset = 0
        self.writes = 0
        self._devices: dict[int, _Device] = {
            GATEWAY_ADDRESS: _Device(None, gateway_registers(), GATEWAY_READABLE_RANGES)
        }
        for address, profile in units.items():
            self._devices[validate_unit_address(address)] = self._new_unit(address, profile)
        self.silent: set[int] = set()
        for address in silent_units:
            self.set_silent(address, True)
        # A list, as the real gateway reports its units in pairing order.
        self.paired: list[int] = [] if no_units else sorted(units)
        self._sync_node_list()
        self._boot = clock()
        self._uptime_at_boot = INITIAL_UPTIME_SECONDS
        self._warmup_until = self._boot + warmup_seconds

    # -- Inspection -------------------------------------------------------

    @property
    def registers(self) -> dict[int, dict[int, int]]:
        """Return the raw registers by address; reading them applies no rules."""

        self._refresh_gateway_clock()
        for device in self._devices.values():
            self._settle(device)
        return {address: device.registers for address, device in self._devices.items()}

    @property
    def units(self) -> dict[int, str]:
        return {
            address: device.profile
            for address, device in self._devices.items()
            if device.profile is not None
        }

    @property
    def warmup_remaining(self) -> float:
        return max(0.0, self._warmup_until - self._clock())

    def answers(self, address: int) -> bool:
        """Return whether a request to ``address`` gets any reply at all.

        A real gateway relays nothing for an address it has no unit for, so
        such requests time out instead of getting an exception response.
        """

        if address == GATEWAY_ADDRESS:
            return True
        return address in self.paired and address not in self.silent

    def check_address(self, address: int) -> None:
        """Raise ``ValueError`` unless ``address`` is the gateway or a configured unit."""

        self._device(address)

    def refused(self, address: int) -> frozenset[int]:
        return frozenset(self._device(address).refused)

    def readable_set_registers(self, address: int) -> frozenset[int]:
        return frozenset(self._device(address).written)

    def describe(self) -> str:
        """Return a short multi-line summary of the simulated state."""

        uptime = self._uptime_at_boot + int(self._clock() - self._boot)
        lines = [
            f"gateway: uptime {uptime} s, clock offset {self.clock_offset} s, "
            f"warm-up {self.warmup_remaining:.0f} s left, settle {self.settle_seconds * 1000:.0f} ms,"
            f" units listed {self.paired}",
        ]
        if refused := self._device(GATEWAY_ADDRESS).refused:
            lines.append(f"gateway refuses {_format_registers(refused)}")
        for address, device in sorted(self._devices.items()):
            if device.profile is None:
                continue
            self._settle(device)
            registers = device.registers
            state = "paired" if address in self.paired else "not paired"
            if address in self.silent:
                state += ", silent"
            details = [
                f"unit {address}: {device.profile} ({state})",
                "status {}/{}/{}".format(
                    registers.get(REGISTER_MODE_STATUS),
                    registers.get(REGISTER_SUPPLY_MODE_STATUS),
                    registers.get(REGISTER_EXTRACT_MODE_STATUS),
                ),
                "airflow {}/{} m3/h".format(
                    registers.get(REGISTER_EXTRACT_AIR_FLOW),
                    registers.get(REGISTER_SUPPLY_AIR_FLOW),
                ),
            ]
            if device.pending is not None:
                details.append(f"settling for {max(0.0, device.pending[0] - self._clock()):.1f} s")
            if device.written:
                details.append(f"readable set registers {_format_registers(device.written)}")
            if device.refused:
                details.append(f"refuses {_format_registers(device.refused)}")
            lines.append(", ".join(details))
        return "\n".join(lines)

    # -- Modbus requests --------------------------------------------------

    def read(self, address: int, register: int, count: int) -> list[int] | int:
        """Return the values of a holding-register read, or an exception code."""

        if not 1 <= count <= MAX_REGISTER_COUNT or register + count > 0x10000:
            return ILLEGAL_DATA_VALUE
        device = self._device(address)
        if device.profile is None:
            self._refresh_gateway_clock()
        self._settle(device)
        registers = range(register, register + count)
        if any(item in device.refused or not device.readable(item) for item in registers):
            return ILLEGAL_DATA_ADDRESS
        if device.profile is not None:
            if any(item in SET_REGISTERS and item not in device.written for item in registers):
                return ACKNOWLEDGE
            if self.warmup_remaining > 0 and any(
                in_ranges(item, WARMUP_ACKNOWLEDGE_RANGES) for item in registers
            ):
                return ACKNOWLEDGE
        return [device.registers.get(item, 0) for item in registers]

    def write(self, address: int, register: int, value: int) -> int | None:
        """Apply a single-register write; return an exception code on refusal."""

        device = self._device(address)
        if device.profile is None or register in device.refused:
            return ILLEGAL_DATA_ADDRESS
        self._settle(device)
        device.registers[register] = value
        if register in SET_REGISTERS:
            device.written.add(register)
        self.writes += 1
        if register == REGISTER_APPLY and value == 0:
            self._commit(device)
        return None

    # -- Runtime changes --------------------------------------------------

    def pair(self, address: int, profile: str) -> None:
        """Add a unit, or pair it again, and list it in the gateway."""

        validate_unit_address(address)
        validate_profile(profile)
        device = self._devices.get(address)
        if device is None or device.profile != profile:
            self._devices[address] = self._new_unit(address, profile)
        if address not in self.paired:
            self.paired.append(address)
        self._sync_node_list()

    def unpair(self, address: int) -> None:
        self._unit(address)
        if address in self.paired:
            self.paired.remove(address)
        self._sync_node_list()

    def set_silent(self, address: int, silent: bool) -> None:
        self._unit(address)
        if silent:
            self.silent.add(address)
        else:
            self.silent.discard(address)

    def set_register(self, address: int, register: int, value: int) -> None:
        if not 0 <= register <= 0xFFFF or not 0 <= value <= 0xFFFF:
            raise ValueError("registers and values must be between 0 and 65535")
        device = self._device(address)
        self._settle(device)
        device.registers[register] = value
        if not device.readable(register):
            device.readable_extra.add(register)

    def peek(self, address: int, register: int, count: int = 1) -> list[int]:
        device = self._device(address)
        if device.profile is None:
            self._refresh_gateway_clock()
        self._settle(device)
        return [device.registers.get(item, 0) for item in range(register, register + count)]

    def refuse(self, address: int, registers: RegisterRange, refused: bool = True) -> None:
        device = self._device(address)
        first, last = registers
        for register in range(first, last + 1):
            if refused:
                device.refused.add(register)
            else:
                device.refused.discard(register)

    def set_flag(self, address: int, flag: str, active: bool) -> None:
        register = STATUS_FLAGS.get(flag)
        if register is None:
            raise ValueError(f"unknown flag {flag!r}; choose from {', '.join(STATUS_FLAGS)}")
        self._unit(address).registers[register] = int(active)

    def app_change(self, address: int, supply: str, extract: str | None = None) -> None:
        """Change the unit as the Meltem app would, without any Modbus write."""

        device = self._unit(address)
        assert device.profile is not None
        max_airflow = profile_max_airflow(device.profile)
        device.pending = None
        if supply == "off":
            updates = _status(MODE_OFF, 0, 0, 0, 0)
        elif supply == "intensive":
            # An override started in the app showed 0/0/0 (MELTEM.md, "Read-only
            # mode status at 41100..41102").
            airflow = _airflow(PRESET_MODE_CODE_INTENSIVE, max_airflow)
            updates = _status(0, 0, 0, airflow, airflow)
        elif supply in QUICK_MODE_CODES:
            code = QUICK_MODE_CODES[supply]
            airflow = _airflow(code, max_airflow)
            updates = _status(MODE_MANUAL, code, code, airflow, airflow)
        else:
            supply_airflow = self._parse_airflow(supply, max_airflow)
            extract_airflow = (
                supply_airflow if extract is None else self._parse_airflow(extract, max_airflow)
            )
            supply_raw = round(supply_airflow * 200 / max_airflow)
            extract_raw = round(extract_airflow * 200 / max_airflow)
            mode = MODE_MANUAL if supply_raw == extract_raw else MODE_UNBALANCED
            updates = _status(mode, supply_raw, extract_raw, extract_airflow, supply_airflow)
        device.registers.update(updates)

    def reboot(self, warmup_seconds: float | None = None) -> None:
        """Restart the gateway: uptime restarts, warm-up begins, set registers lock."""

        if warmup_seconds is not None:
            if warmup_seconds < 0:
                raise ValueError("the warm-up must be non-negative")
            self.warmup_seconds = warmup_seconds
        self._boot = self._clock()
        self._uptime_at_boot = 0
        self._warmup_until = self._boot + self.warmup_seconds
        for device in self._devices.values():
            self._settle(device, force=True)
            device.written.clear()

    def start_warmup(self, seconds: float) -> None:
        if seconds < 0:
            raise ValueError("the warm-up must be non-negative")
        self._warmup_until = self._clock() + seconds

    # -- Register maps ----------------------------------------------------

    def apply_register_map(self, data: Mapping[str, Any]) -> None:
        """Apply a register map or a ``capture_setting_family`` capture.

        Values override the generated registers and become readable even
        outside the modeled islands; ``null`` marks a register as refused.
        """

        if "metadata" in data and "values" in data:
            metadata = data["metadata"]
            address = metadata.get("slave") if isinstance(metadata, Mapping) else None
            if not isinstance(address, int) or address not in self.units:
                raise RegisterMapError(
                    f"the capture is for unit {address}; add that unit with --units first"
                )
            self._apply_values(address, data["values"])
            return
        if data.get("format") != REGISTER_MAP_FORMAT:
            raise RegisterMapError(
                f"unknown register map format {data.get('format')!r}; "
                f"expected {REGISTER_MAP_FORMAT} or a capture_setting_family capture"
            )
        gateway = data.get("gateway", {})
        if not isinstance(gateway, Mapping):
            raise RegisterMapError("'gateway' must be an object")
        self._apply_values(GATEWAY_ADDRESS, gateway.get("registers", {}))
        units = data.get("units", {})
        if not isinstance(units, Mapping):
            raise RegisterMapError("'units' must be an object")
        for key, unit in units.items():
            if not isinstance(unit, Mapping):
                raise RegisterMapError(f"unit {key} must be an object")
            try:
                address = validate_unit_address(int(key))
                profile = unit.get("profile", self.units.get(address))
                if profile is None:
                    raise RegisterMapError(f"unit {key} needs a profile")
                self.pair(address, str(profile))
            except ValueError as err:
                raise RegisterMapError(f"unit {key}: {err}") from err
            if not unit.get("paired", True):
                self.unpair(address)
            self._apply_values(address, unit.get("registers", {}))

    def to_register_map(self) -> dict[str, Any]:
        """Return the current registers in the format ``apply_register_map`` reads."""

        def values(device: _Device) -> dict[str, int | None]:
            self._settle(device)
            result: dict[str, int | None] = {
                str(register): value for register, value in sorted(device.registers.items())
            }
            result.update({str(register): None for register in sorted(device.refused)})
            return result

        self._refresh_gateway_clock()
        return {
            "format": REGISTER_MAP_FORMAT,
            "gateway": {"registers": values(self._devices[GATEWAY_ADDRESS])},
            "units": {
                str(address): {
                    "profile": profile,
                    "paired": address in self.paired,
                    "registers": values(self._devices[address]),
                }
                for address, profile in sorted(self.units.items())
            },
        }

    # -- Internals --------------------------------------------------------

    def _new_unit(self, address: int, profile: str) -> _Device:
        return _Device(
            validate_profile(profile),
            unit_registers(profile, 0xA000 + address),
            UNIT_READABLE_RANGES,
        )

    def _device(self, address: int) -> _Device:
        device = self._devices.get(address)
        if device is None:
            raise ValueError(f"no unit {address} is configured")
        return device

    def _unit(self, address: int) -> _Device:
        device = self._device(address)
        if device.profile is None:
            raise ValueError("this needs a unit address, not the gateway")
        return device

    def _sync_node_list(self) -> None:
        registers = self._devices[GATEWAY_ADDRESS].registers
        registers[REGISTER_GATEWAY_NUMBER_OF_NODES] = len(self.paired)
        for index in range(16):
            registers[REGISTER_GATEWAY_NODE_ADDRESS_1 + index] = (
                self.paired[index] if index < len(self.paired) else 0
            )

    def _refresh_gateway_clock(self) -> None:
        registers = self._devices[GATEWAY_ADDRESS].registers
        uptime = self._uptime_at_boot + int(self._clock() - self._boot)
        registers.update(word_swapped_u32(REGISTER_GATEWAY_UPTIME, uptime))
        utc = int(self._wall_clock()) + self.clock_offset
        registers.update(word_swapped_u32(REGISTER_GATEWAY_UTC_CLOCK, utc & 0xFFFFFFFF))

    def _commit(self, device: _Device) -> None:
        updates = self._mode_updates(device)
        if self.settle_seconds:
            device.pending = (self._clock() + self.settle_seconds, updates)
        else:
            device.pending = None
            device.registers.update(updates)

    def _settle(self, device: _Device, *, force: bool = False) -> None:
        if device.pending is not None and (force or self._clock() >= device.pending[0]):
            device.registers.update(device.pending[1])
            device.pending = None

    @staticmethod
    def _mode_updates(device: _Device) -> dict[int, int]:
        assert device.profile is not None
        registers = device.registers
        mode = registers.get(REGISTER_MODE, MODE_MANUAL)
        level = registers.get(REGISTER_CURRENT_LEVEL, 0)
        extract = registers.get(REGISTER_EXTRACT_AIR_TARGET_LEVEL, level)
        max_airflow = profile_max_airflow(device.profile)
        # Intensive keeps running until both shadow registers are cleared, even
        # when the base registers switch the unit off (MELTEM.md, W-8).
        if (
            registers.get(REGISTER_PRESET_MODE) == MODE_MANUAL
            and registers.get(REGISTER_PRESET_VALUE) == PRESET_MODE_CODE_INTENSIVE
        ):
            airflow = _airflow(PRESET_MODE_CODE_INTENSIVE, max_airflow)
            return _status(
                MODE_MANUAL,
                PRESET_MODE_CODE_INTENSIVE,
                PRESET_MODE_CODE_INTENSIVE,
                airflow,
                airflow,
            )
        if mode == MODE_SENSOR_CONTROL:
            status = level | SENSOR_STATUS_BIT
            return _status(mode, status, status, None, None)
        if mode == MODE_OFF:
            return _status(mode, level, level, 0, 0)
        if mode == MODE_UNBALANCED:
            # Values above 200 are app presets of 200 + airflow / 10 here.
            return _status(
                mode,
                level,
                extract,
                _airflow(extract, max_airflow, quick_modes=False),
                _airflow(level, max_airflow, quick_modes=False),
            )
        airflow = _airflow(level, max_airflow)
        return _status(mode, level, level, airflow, airflow)

    def _apply_values(self, address: int, values: Any) -> None:
        if not isinstance(values, Mapping):
            raise RegisterMapError(f"the registers of address {address} must be an object")
        device = self._device(address)
        for key, value in values.items():
            try:
                register = int(key)
            except ValueError as err:
                raise RegisterMapError(f"invalid register {key!r}") from err
            if not 0 <= register <= 0xFFFF:
                raise RegisterMapError(f"invalid register {key!r}")
            if value is None:
                # Unreadable set registers in a capture mean "not written yet",
                # which the set-register model already covers.
                if register not in SET_REGISTERS and register != REGISTER_APPLY:
                    device.refused.add(register)
                continue
            if not isinstance(value, int) or isinstance(value, bool) or not 0 <= value <= 0xFFFF:
                raise RegisterMapError(f"register {key} needs a value between 0 and 65535")
            device.refused.discard(register)
            device.registers[register] = value
            if not device.readable(register):
                device.readable_extra.add(register)
        if address == GATEWAY_ADDRESS:
            # The node list follows the paired units, not the map.
            self._sync_node_list()

    @staticmethod
    def _parse_airflow(text: str, max_airflow: int) -> int:
        try:
            airflow = int(text)
        except ValueError as err:
            raise ValueError(
                f"expected off, low, medium, high, intensive, or an airflow in m3/h, not {text!r}"
            ) from err
        if not 0 <= airflow <= max_airflow:
            raise ValueError(f"the airflow must be between 0 and {max_airflow} m3/h")
        return airflow


def _format_registers(registers: Iterable[int]) -> str:
    ordered = sorted(registers)
    ranges: list[str] = []
    start = previous = ordered[0]
    for register in ordered[1:]:
        if register != previous + 1:
            ranges.append(f"{start}" if start == previous else f"{start}-{previous}")
            start = register
        previous = register
    ranges.append(f"{start}" if start == previous else f"{start}-{previous}")
    return ", ".join(ranges)
