"""Runtime Modbus client for Meltem Modbus ventilation units.

This module contains the long-lived :class:`MeltemModbusClient` that the
coordinator uses for all reads and writes during normal operation, and the pure
helpers that plan a room read and decode the mode registers.

The register blocks live in ``device/``. Setup-time helpers (link parameters,
scans, profile probes, and pure utility functions) live in ``modbus_helpers.py``.
"""

from __future__ import annotations

import math
import time
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field, replace
from typing import Any

from homeassistant.util import dt as dt_util
from modbus_connection import (
    ModbusConnectionError,
    ModbusError,
    ModbusExceptionError,
    ModbusTimeoutError,
    ModbusUnit,
)

from .const import (
    APP_UNBALANCED_PRESET_BASE,
    CO2_PROFILES,
    CONTROL_SETTING_LIMITS,
    CONTROL_SETTING_REGISTERS,
    DEFAULT_GATEWAY_DEVICE_ID,
    HUMIDITY_PROFILES,
    MODE_MANUAL,
    MODE_OFF,
    MODE_SENSOR_CONTROL,
    MODE_STATUS_SENSOR_MODE_TO_RAW_VALUE,
    MODE_UNBALANCED,
    OPERATION_MODE_MANUAL,
    OPERATION_MODE_OFF,
    OPERATION_MODE_UNBALANCED,
    PLAIN_PROFILES,
    PRESET_MODE_CODE_INTENSIVE,
    PRESET_MODE_EXTRACT_ONLY,
    PRESET_MODE_INTENSIVE,
    PRESET_MODE_SUPPLY_ONLY,
    PRESET_MODE_TO_RAW_CODE,
    RAW_CODE_TO_PRESET_MODE,
    RAW_VALUE_TO_SENSOR_MODE,
    READ_GROUP_ENTITY_KEYS,
    SENSOR_MODE_TO_RAW_VALUE,
    VOC_PROFILES,
    profile_max_airflow,
)
from .device import MeltemRoomDevice, PolicyUnit
from .device.components import ModeStatus
from .modbus_helpers import (
    MeltemConnectionError,
    MeltemModbusError,
    derive_balanced_airflow,
    detect_slave_details,
    discover_gateway_nodes,
    new_transport_policy,
    prepare_unit,
    read_gateway_node_count,
)
from .models import ReadHealth, RefreshPlan, RoomConfig, RoomState

_OPTIONAL_READ_BACKOFF_START_SECONDS = 30.0
_OPTIONAL_READ_BACKOFF_MAX_SECONDS = 300.0
_OPTIONAL_READ_BACKOFF_MAX_FAILURES = 5

# Fields of 0/1 registers that ``RoomState`` holds as bool.
_FLAG_FIELDS = frozenset(
    {"error_status", "filter_change_due", "frost_protection_active", "rf_comm_status"}
)

# Zero in both intensive shadow registers ends a running override.
_CLEAR_INTENSIVE: tuple[tuple[str, int], ...] = (("preset_mode", 0), ("preset_value", 0))

_BackoffKey = tuple[int, str]


@dataclass(slots=True, frozen=True)
class _FieldRead:
    """Where one due ``RoomState`` field is read from."""

    component: str
    attribute: str
    group: str


@dataclass(slots=True)
class _ReadJob:
    """Read errors of one room read, per read-health group."""

    errors: dict[str, str] = field(default_factory=dict)
    # Set once the unit timed out; the rest of the job is skipped then.
    silent_error: ModbusError | None = None

    def record_error(self, groups: tuple[str, ...], error: Exception | str) -> None:
        """Keep the first swallowed error of each affected group."""

        for group in groups:
            self.errors.setdefault(group, str(error))


@dataclass(slots=True)
class _ReadBackoff:
    """Growing pause per ``(slave, component)`` for reads a unit refuses."""

    failures: dict[_BackoffKey, int] = field(default_factory=dict)
    until: dict[_BackoffKey, float] = field(default_factory=dict)
    errors: dict[_BackoffKey, str] = field(default_factory=dict)

    def is_active(self, key: _BackoffKey) -> bool:
        """Return whether the read is still paused."""

        paused_until = self.until.get(key)
        if paused_until is None:
            return False
        if time.monotonic() >= paused_until:
            del self.until[key]
            return False
        return True

    def mark_failure(self, key: _BackoffKey, error: Exception) -> None:
        """Pause the read twice as long as last time, up to the maximum."""

        failures = min(self.failures.get(key, 0) + 1, _OPTIONAL_READ_BACKOFF_MAX_FAILURES)
        self.failures[key] = failures
        self.errors[key] = str(error)
        self.until[key] = time.monotonic() + min(
            _OPTIONAL_READ_BACKOFF_MAX_SECONDS,
            _OPTIONAL_READ_BACKOFF_START_SECONDS * 2 ** (failures - 1),
        )

    def clear(self, key: _BackoffKey) -> None:
        self.failures.pop(key, None)
        self.until.pop(key, None)
        self.errors.pop(key, None)

    def clear_slave(self, slave: int) -> None:
        for key in [key for key in self.failures if key[0] == slave]:
            self.clear(key)


@dataclass(slots=True, frozen=True)
class _ModeRead:
    """Decoded values from the read-only current-mode status map."""

    mode: int
    current_level: int
    extract_target_level: int


class MeltemModbusClient:
    """Async access to every Meltem unit behind one gateway.

    ``unit_factory`` hands out one ``ModbusUnit`` per Modbus address; all of
    them share one retry policy. Reads and writes are serialized by the
    coordinator, and the link serializes the requests themselves.
    """

    def __init__(
        self,
        unit_factory: Callable[[int], ModbusUnit],
        *,
        port: str,
        max_requests_per_second: float | None = None,
    ) -> None:
        self._unit_factory = unit_factory
        self._port = port
        self._policy = new_transport_policy()
        if max_requests_per_second is not None:
            self.update_request_rate(max_requests_per_second)
        self._units: dict[int, PolicyUnit] = {}
        self._devices: dict[int, MeltemRoomDevice] = {}
        # Avoid repeatedly polling a status block that a unit refuses.
        self._mode_backoff = _ReadBackoff()
        self._shut_down = False

    def seconds_since_successful_read(self, slave: int) -> float | None:
        """Return the age of the last answered register read for one unit.

        Every optional read swallows its error, so this is the only reliable
        signal that a unit behind the gateway went silent.
        """

        return self._policy.seconds_since_read_answer(slave)

    def transport_diagnostics(self) -> dict[str, float | int | None]:
        """Return the health counters of the gateway link."""

        return self._policy.diagnostics()

    def update_request_rate(self, max_requests_per_second: float) -> None:
        """Apply the shared read-telegram limit, including retries."""

        self._policy.update_request_rate(max_requests_per_second)

    def shutdown(self) -> None:
        """Reject every later operation, so a late readback cannot reach the link."""

        self._shut_down = True
        self._policy.shutdown()

    def _unit(self, slave: int) -> PolicyUnit:
        unit = self._units.get(slave)
        if unit is None:
            unit = self._units[slave] = prepare_unit(
                self._unit_factory(slave), slave, self._policy
            )
        return unit

    def _room_device(self, room: RoomConfig) -> MeltemRoomDevice:
        device = self._devices.get(room.slave)
        if device is None:
            device = self._devices[room.slave] = MeltemRoomDevice(
                self._unit(room.slave),
                exhaust_temperature_only=room.profile in PLAIN_PROFILES,
            )
        return device

    @asynccontextmanager
    async def _gateway_operation(self, description: str) -> AsyncIterator[None]:
        """Run one gateway operation and translate its errors."""

        if self._shut_down:
            raise MeltemConnectionError(
                f"Meltem gateway client on {self._port} was shut down"
            )
        try:
            yield
        except MeltemModbusError:
            raise
        except ModbusConnectionError as err:
            raise MeltemConnectionError(
                f"Lost the Meltem gateway on {self._port} while {description}: {err}"
            ) from err
        except ModbusError as err:
            raise MeltemModbusError(f"Modbus error while {description}: {err}") from err
        except Exception as err:
            raise MeltemModbusError(
                f"Unexpected error while {description}: {err!r}"
            ) from err

    async def async_validate_gateway(self) -> None:
        """Read the gateway's node count once to prove the link works.

        The gateway unit is acquired before the error translation, so a
        conflicting link configuration surfaces unchanged.
        """

        unit = self._unit(DEFAULT_GATEWAY_DEVICE_ID)
        async with self._gateway_operation("validating the gateway"):
            await read_gateway_node_count(unit)

    async def discover_gateway_units(self, start: int, end: int) -> list[int]:
        """Discover configured unit addresses using the current gateway link."""

        async with self._gateway_operation("discovering units"):
            return await discover_gateway_nodes(
                self._unit(DEFAULT_GATEWAY_DEVICE_ID),
                self._port,
                start=start,
                end=end,
            )

    async def probe_slave_details(
        self,
        slave: int,
    ) -> tuple[str, str | None]:
        """Probe one configured unit using the current gateway link."""

        async with self._gateway_operation(f"probing unit {slave}"):
            return await detect_slave_details(self._unit(slave))

    async def read_room_state(
        self,
        room: RoomConfig,
        previous_state: RoomState | None = None,
        refresh_plan: RefreshPlan | None = None,
    ) -> RoomState:
        """Read all relevant state for one room.

        ``RefreshPlan`` decides which groups are due in the current scheduler
        tick. Values outside the plan are copied forward from ``previous_state``.
        """

        previous_state = previous_state or RoomState()
        refresh_plan = refresh_plan or RefreshPlan()

        async with self._gateway_operation(f"reading room {room.key}"):
            device = self._room_device(room)
            job = _ReadJob()
            due = _due_fields(room, refresh_plan)
            updated = await self._poll(device, _due_reads(due), job)
            state = replace(previous_state, **_fresh_values(device, due, updated, job))
            if refresh_plan.refresh_airflow:
                state = await self._read_mode_group(room, device, job, state)
            return replace(
                state,
                group_read_health=_updated_read_health(room, previous_state, refresh_plan, job),
            )

    async def _poll(
        self,
        device: MeltemRoomDevice,
        reads: dict[str, tuple[str, ...]],
        job: _ReadJob,
    ) -> set[str]:
        """Read the due components and record failures per health group.

        ``reads`` maps each component to the health groups its outcome counts
        for. A timeout means the unit is silent, so the rest of the job is
        skipped instead of timing out once per block; before anything answered
        the poll itself stops, after that only the mode reads are spared.
        """

        if not reads:
            return set()
        try:
            report = await device.async_poll(list(reads))
        except ModbusTimeoutError as err:
            job.silent_error = err
            for groups in reads.values():
                job.record_error(groups, err)
            return set()
        for component, groups in reads.items():
            error = report.failed.get(component)
            if error is None:
                continue
            job.record_error(groups, error)
            if isinstance(error, ModbusTimeoutError):
                job.silent_error = error
        return report.updated

    # ------------------------------------------------------------------
    #  Writes
    # ------------------------------------------------------------------

    async def write_level(self, room: RoomConfig, level: int) -> None:
        """Write off/manual mode and target level for one room."""

        await self._write_mode_registers(
            room,
            "writing level",
            ("mode", MODE_OFF if level == 0 else MODE_MANUAL),
            ("current_level", _scale_airflow_to_raw(room, level)),
        )

    async def write_unbalanced_levels(
        self, room: RoomConfig, supply_level: int, extract_level: int
    ) -> None:
        """Write unbalanced mode with separate supply and extract levels."""

        await self._write_mode_registers(
            room,
            "writing unbalanced levels",
            ("mode", MODE_UNBALANCED),
            ("current_level", _scale_airflow_to_raw(room, supply_level)),
            ("extract_target_level", _scale_airflow_to_raw(room, extract_level)),
        )

    async def write_operating_mode(
        self,
        room: RoomConfig,
        operation_mode: str,
        balanced_level: int,
        extract_level: int,
    ) -> None:
        """Write one operating mode using the documented control registers."""

        balanced = ("current_level", _scale_airflow_to_raw(room, balanced_level))
        writes: tuple[tuple[str, int], ...]
        if operation_mode == OPERATION_MODE_OFF:
            writes = (("mode", MODE_OFF), ("current_level", 0))
        elif operation_mode == OPERATION_MODE_MANUAL:
            writes = (("mode", MODE_MANUAL), balanced)
        elif operation_mode == OPERATION_MODE_UNBALANCED:
            writes = (
                ("mode", MODE_UNBALANCED),
                balanced,
                ("extract_target_level", _scale_airflow_to_raw(room, extract_level)),
            )
        else:
            sensor_control_value = SENSOR_MODE_TO_RAW_VALUE.get(operation_mode)
            if sensor_control_value is None:
                raise MeltemModbusError(
                    f"Unsupported operating mode {operation_mode!r} for room {room.key}"
                )
            writes = (("mode", MODE_SENSOR_CONTROL), ("current_level", sensor_control_value))
        await self._write_mode_registers(room, "writing operating mode", *writes)

    async def write_preset_mode(
        self,
        room: RoomConfig,
        preset_mode: str,
    ) -> None:
        """Write one confirmed app-style preset mode."""

        raw_code = PRESET_MODE_TO_RAW_CODE.get(preset_mode)
        if raw_code is None:
            raise MeltemModbusError(
                f"Unsupported preset mode {preset_mode!r} for room {room.key}"
            )

        writes: tuple[tuple[str, int], ...]
        if preset_mode == PRESET_MODE_INTENSIVE:
            writes = (("preset_mode", MODE_MANUAL), ("preset_value", raw_code))
        else:
            writes = (*_CLEAR_INTENSIVE, ("mode", MODE_MANUAL), ("current_level", raw_code))
        await self._write_mode_registers(room, "writing preset mode", *writes)

    async def clear_intensive(self, room: RoomConfig) -> None:
        """Cancel a running intensive override.

        Only the dedicated shadow registers are touched, so the base quick mode
        and the airflow targets stay untouched.
        """

        await self._write_mode_registers(room, "clearing intensive mode", *_CLEAR_INTENSIVE)

    async def write_control_setting(
        self,
        room: RoomConfig,
        setting_key: str,
        value: int,
    ) -> int:
        """Write one humidity/CO2 control setting register."""

        stepped = normalize_control_setting(setting_key, value)
        async with self._gateway_operation(f"writing control setting for room {room.key}"):
            await self._room_device(room).control_settings.write(setting_key, stepped)
        return stepped

    async def _write_mode_registers(
        self, room: RoomConfig, action: str, *writes: tuple[str, int]
    ) -> None:
        """Write mode registers in order, then activate them with APPLY."""

        async with self._gateway_operation(f"{action} for room {room.key}"):
            device = self._room_device(room)
            for name, value in writes:
                await device.mode.write(name, value)
            await device.command.write("apply", 0)
            # Let the post-write readback try a refused status map again.
            self._mode_backoff.clear_slave(room.slave)

    # ------------------------------------------------------------------
    #  Mode family (optional reads with backoff)
    # ------------------------------------------------------------------

    async def _read_mode_status(
        self,
        room: RoomConfig,
        device: MeltemRoomDevice,
        job: _ReadJob,
    ) -> ModeStatus | None:
        """Read the current-state map, backing off only on exception responses.

        A timeout means the unit went silent, so the rest of the job is skipped.
        """

        if job.silent_error is not None:
            job.record_error(("flow_control", "intensive"), job.silent_error)
            return None

        key = (room.slave, "mode_status")
        if self._mode_backoff.is_active(key):
            job.record_error(
                ("flow_control", "intensive"),
                self._mode_backoff.errors.get(key, "Read is temporarily backed off"),
            )
            return None

        component = device.mode_status
        try:
            await component.async_update()
        except ModbusConnectionError:
            raise
        except ModbusTimeoutError as err:
            job.silent_error = err
            job.record_error(("flow_control", "intensive"), err)
            return None
        except ModbusExceptionError as err:
            self._mode_backoff.mark_failure(key, err)
            job.record_error(("flow_control", "intensive"), err)
            return None
        except ModbusError as err:
            job.record_error(("flow_control", "intensive"), err)
            return None

        self._mode_backoff.clear(key)
        return component

    async def _read_mode_block(
        self, room: RoomConfig, device: MeltemRoomDevice, job: _ReadJob
    ) -> _ModeRead | None:
        """Read the read-only current-state map shared by supported profiles.

        The set registers at 41120..41124 remain the write interface.
        """

        reports_mode = room.supports("operation_mode") or room.supports("preset_mode")
        if not reports_mode and not room.supports("intensive"):
            return None

        status = await self._read_mode_status(room, device, job)
        if status is None:
            return None

        mode = status.mode
        current_level = status.current_level
        extract_target_level = status.extract_target_level
        if mode is None or current_level is None or extract_target_level is None:
            job.record_error(
                ("flow_control", "intensive"),
                MeltemModbusError("Incomplete current-mode status read at 41100..41102"),
            )
            return None
        if mode == MODE_SENSOR_CONTROL:
            current_level = MODE_STATUS_SENSOR_MODE_TO_RAW_VALUE.get(
                current_level, current_level
            )
        return _ModeRead(
            mode=int(mode),
            current_level=int(current_level),
            extract_target_level=int(extract_target_level),
        )

    async def _read_mode_group(
        self,
        room: RoomConfig,
        device: MeltemRoomDevice,
        job: _ReadJob,
        state: RoomState,
    ) -> RoomState:
        """Read and decode operating mode, airflow targets, and preset state.

        ``state`` carries the fresh airflow and the previous mode values.
        """

        block = await self._read_mode_block(room, device, job)
        raw_current_level: int | None
        if block is not None and _mode_status_reports_intensive(block):
            # The intensive status hides the base mode, so the last known one stays.
            return replace(state, intensive_active=True)

        if block is not None:
            operation_mode = _decode_operation_mode(block.mode, block.current_level)
            raw_current_level = block.current_level
        else:
            operation_mode = state.operation_mode
            raw_current_level = None

        raw_extract_target: int | None = None
        extract_target_level: int | None = None
        if operation_mode == OPERATION_MODE_UNBALANCED:
            target_level = (
                _decode_unbalanced_target_readback(room, raw_current_level)
                if raw_current_level is not None
                else state.target_level
            )
            raw_extract_target = block.extract_target_level if block is not None else None
            extract_target_level = (
                _decode_unbalanced_target_readback(room, raw_extract_target)
                if raw_extract_target is not None
                else state.extract_target_level
            )
        elif block is not None and block.mode == MODE_SENSOR_CONTROL:
            # Here the current-level field carries a selector, not an airflow.
            target_level = derive_balanced_airflow(state.extract_air_flow, state.supply_air_flow)
        else:
            # Measured airflow can lag after writes, so prefer the current
            # target in the status map when it is plausible.
            target_level = _decode_balanced_target_readback(
                room,
                raw_current_level,
                state.extract_air_flow,
                state.supply_air_flow,
            )

        return replace(
            state,
            operation_mode=operation_mode,
            target_level=target_level,
            balanced_target_readback=(
                _scale_raw_level_to_airflow(room, raw_current_level)
                if operation_mode in (OPERATION_MODE_OFF, OPERATION_MODE_MANUAL)
                and raw_current_level is not None
                and 0 <= raw_current_level <= 200
                else None
            ),
            extract_target_level=extract_target_level,
            preset_mode=_decode_preset_mode(block, raw_extract_target, state.preset_mode),
            intensive_active=state.intensive_active if block is None else False,
        )


# ----------------------------------------------------------------------
#  Read planning
# ----------------------------------------------------------------------


def _due_fields(room: RoomConfig, plan: RefreshPlan) -> dict[str, _FieldRead]:
    """Map every ``RoomState`` field this plan reads to where it comes from.

    The order is the gateway's usual read order: each component is read at the
    position of its first due field.
    """

    due: dict[str, _FieldRead] = {}

    def add(
        name: str,
        component: str,
        group: str,
        when: bool,
        *,
        attribute: str | None = None,
        gated: bool = True,
    ) -> None:
        if when and (not gated or room.supports(name)):
            due[name] = _FieldRead(component, attribute or name, group)

    extended = room.profile not in PLAIN_PROFILES
    temperatures = plan.refresh_temperatures
    environment = plan.refresh_environment

    add("extract_air_flow", "airflow", "flow", plan.refresh_airflow)
    add("supply_air_flow", "airflow", "flow", plan.refresh_airflow)
    add("exhaust_temperature", "temperatures", "temperature", temperatures)
    add("outdoor_air_temperature", "temperatures", "temperature", extended and environment)
    add("extract_air_temperature", "temperatures", "temperature", extended and temperatures)
    add("supply_air_temperature", "supply_temperature", "temperature", extended and temperatures)
    for name, component, profiles in (
        ("humidity_extract_air", "extract_air_quality", HUMIDITY_PROFILES),
        ("co2_extract_air", "extract_air_quality", CO2_PROFILES),
        ("humidity_supply_air", "supply_air_quality", HUMIDITY_PROFILES),
        ("voc_supply_air", "supply_air_quality", VOC_PROFILES),
    ):
        add(name, component, "temperature", environment and room.profile in profiles)
    add("error_status", "status", "status", plan.refresh_status)
    add("frost_protection_active", "status", "status", plan.refresh_status)
    add("filter_change_due", "status", "filter", plan.refresh_filter_change_due)
    add(
        "days_until_filter_change",
        "days_until_filter_change",
        "filter",
        plan.refresh_filter_days,
        attribute="value",
    )
    add("operating_hours", "operating_hours", "hours", plan.refresh_operating_hours)
    # Feeds the device info, not an entity, so no supported key gates it.
    add(
        "software_version",
        "software_version",
        "hours",
        plan.refresh_operating_hours,
        attribute="value",
        gated=False,
    )
    for key in CONTROL_SETTING_REGISTERS:
        add(key, "control_settings", "control_settings", plan.refresh_control_settings)
    add("rf_comm_status", "rf_comm_status", "status", plan.refresh_status, attribute="value")
    return due


def _due_reads(due: dict[str, _FieldRead]) -> dict[str, tuple[str, ...]]:
    """Map each component to read to the health groups its outcome counts for."""

    reads: dict[str, tuple[str, ...]] = {}
    for source in due.values():
        groups = reads.get(source.component, ())
        if source.group not in groups:
            reads[source.component] = (*groups, source.group)
    return reads


def _fresh_values(
    device: MeltemRoomDevice, due: dict[str, _FieldRead], updated: set[str], job: _ReadJob
) -> dict[str, Any]:
    """Return the due fields whose component answered with a usable value."""

    values: dict[str, Any] = {}
    for name, source in due.items():
        if source.component not in updated:
            continue
        value = getattr(getattr(device, source.component), source.attribute)
        if value is None or (isinstance(value, float) and not math.isfinite(value)):
            job.record_error((source.group,), f"Invalid {name} readback: {value!r}")
            continue
        values[name] = bool(value) if name in _FLAG_FIELDS else value
    return values


def _updated_read_health(
    room: RoomConfig,
    previous_state: RoomState,
    refresh_plan: RefreshPlan,
    job: _ReadJob,
) -> tuple[tuple[str, ReadHealth], ...]:
    """Update health only for expected groups selected by this read plan."""

    group_health = dict(previous_state.group_read_health)
    now = dt_util.utcnow()
    for group_key in refresh_plan.read_groups():
        if not room.supports_any(READ_GROUP_ENTITY_KEYS[group_key]):
            continue

        error = job.errors.get(group_key)
        if error is None:
            group_health[group_key] = ReadHealth(last_attempt=now, last_successful_read=now)
            continue
        group_health[group_key] = previous_state.read_health_for(group_key).failed(now, error)
    return tuple(sorted(group_health.items()))


# ----------------------------------------------------------------------
#  Scaling and decoding
# ----------------------------------------------------------------------


def normalize_control_setting(setting_key: str, value: int) -> int:
    """Clamp one control setting to its limits and round it to the register step."""

    limits = CONTROL_SETTING_LIMITS.get(setting_key)
    if setting_key not in CONTROL_SETTING_REGISTERS or limits is None:
        raise MeltemModbusError(f"Unsupported control setting {setting_key!r}")
    min_val, max_val, step = limits
    clamped = max(min_val, min(max_val, value))
    return min_val + ((clamped - min_val + step // 2) // step) * step


def _scale_airflow_to_raw(room: RoomConfig, level: int) -> int:
    max_airflow = profile_max_airflow(room.profile)
    return max(0, min(200, round(level * 200 / max_airflow)))


def _scale_raw_level_to_airflow(room: RoomConfig, raw_level: int) -> int:
    return round(raw_level * profile_max_airflow(room.profile) / 200)


def _decode_balanced_target_readback(
    room: RoomConfig,
    raw_level: int | None,
    extract_air_flow: int | None,
    supply_air_flow: int | None,
) -> int | None:
    """Return a balanced target readback or fall back to measured airflow."""

    if raw_level is not None and 0 <= raw_level <= 200:
        return _scale_raw_level_to_airflow(room, raw_level)

    # A shared "current level" only makes sense when both airflow
    # directions are effectively balanced.
    return derive_balanced_airflow(extract_air_flow, supply_air_flow)


def _decode_unbalanced_target_readback(room: RoomConfig, raw_level: int) -> int | None:
    """Decode one unbalanced target, including app-side preset encodings."""

    if 0 <= raw_level <= 200:
        return _scale_raw_level_to_airflow(room, raw_level)
    if raw_level > APP_UNBALANCED_PRESET_BASE:
        # The app encodes the shortcut airflow as 200 + m3/h per 10. Quick
        # mode codes land in the same range but decode far above the rated
        # airflow, so they must not be reported as a target.
        max_airflow = profile_max_airflow(room.profile)
        airflow = (raw_level - APP_UNBALANCED_PRESET_BASE) * 10
        return airflow if airflow <= max_airflow else None
    return None


def _decode_operation_mode(mode_value: int, current_value: int) -> str | None:
    if mode_value == MODE_OFF:
        return OPERATION_MODE_OFF
    if mode_value == MODE_MANUAL:
        return OPERATION_MODE_MANUAL
    if mode_value == MODE_UNBALANCED:
        return OPERATION_MODE_UNBALANCED
    if mode_value != MODE_SENSOR_CONTROL:
        return None
    return RAW_VALUE_TO_SENSOR_MODE.get(current_value)


def _decode_preset_mode(
    block: _ModeRead | None,
    raw_extract_target: int | None,
    previous_preset_mode: str | None,
) -> str | None:
    """Return the base quick mode from a status read that does not report intensive."""

    if block is None:
        return previous_preset_mode
    if block.mode == MODE_UNBALANCED and raw_extract_target is not None:
        if block.current_level == 0 and raw_extract_target > APP_UNBALANCED_PRESET_BASE:
            return PRESET_MODE_EXTRACT_ONLY
        if raw_extract_target == 0 and block.current_level > APP_UNBALANCED_PRESET_BASE:
            return PRESET_MODE_SUPPLY_ONLY
    if block.mode != MODE_MANUAL:
        return None
    return RAW_CODE_TO_PRESET_MODE.get(block.current_level)


def _mode_status_reports_intensive(block: _ModeRead) -> bool:
    """Return whether the read-only mode map reports an intensive state.

    ``0/0`` was observed for an override started from the app; ``3/227`` is the
    community decode for intensive written to the base registers.
    """

    return (block.mode == MODE_MANUAL and block.current_level == PRESET_MODE_CODE_INTENSIVE) or (
        block.mode == 0 and block.current_level == 0
    )
