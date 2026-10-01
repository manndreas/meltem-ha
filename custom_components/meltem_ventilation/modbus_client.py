"""Runtime Modbus client for Meltem Modbus ventilation units.

This module contains only the long-lived :class:`MeltemModbusClient` that the
coordinator uses for all reads and writes during normal operation.

The register blocks live in ``device/``. Setup-time helpers (link parameters,
scans, profile probes, and pure utility functions) live in ``modbus_helpers.py``.
"""

from __future__ import annotations

import math
import time
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, replace
from typing import Any

from homeassistant.util import dt as dt_util
from modbus_connection import (
    ModbusConnectionError,
    ModbusError,
    ModbusExceptionError,
    ModbusTimeoutError,
    ModbusUnit,
)
from modbus_connection.model import Component

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
    READ_FAILURE_THRESHOLD,
    READ_GROUP_ENTITY_KEYS,
    SENSOR_MODE_TO_RAW_VALUE,
    SENSOR_OPERATION_MODES,
    VOC_PROFILES,
    profile_max_airflow,
)
from .device import MeltemRoomDevice, PolicyUnit
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


def _to_optional_bool(value: int | bool | None) -> bool | None:
    """Coerce 0/1 register values to bool, preserving None."""
    if isinstance(value, bool):
        return value
    return bool(value) if value is not None else None


_OPTIONAL_READ_BACKOFF_START_SECONDS = 30.0
_OPTIONAL_READ_BACKOFF_MAX_SECONDS = 300.0
_OPTIONAL_READ_BACKOFF_MAX_FAILURES = 5

# Mode-family components read on their own; they get a temporary backoff
# because many units reject them until a first write (HW-4).
_MODE_COMPONENTS = ("mode", "mode_short", "current_level", "extract_target_level")


@dataclass(slots=True, frozen=True)
class _ModeGroup:
    """Decoded mode and airflow-target state for one room."""

    operation_mode: str | None
    target_level: int | None
    extract_target_level: int | None
    preset_mode: str | None
    intensive_active: bool | None

    @classmethod
    def unchanged(cls, previous_state: RoomState) -> _ModeGroup:
        """Carry the previous values forward when the group is not due."""

        return cls(
            operation_mode=previous_state.operation_mode,
            target_level=previous_state.target_level,
            extract_target_level=previous_state.extract_target_level,
            preset_mode=previous_state.preset_mode,
            intensive_active=previous_state.intensive_active,
        )


@dataclass(slots=True, frozen=True)
class _DueRead:
    """One component a job reads, and the health groups its outcome counts for."""

    component: str
    groups: tuple[str, ...]


class MeltemModbusClient:
    """Async access to every Meltem unit behind one gateway.

    ``unit_factory`` hands out one ``ModbusUnit`` per Modbus address; all of
    them share one retry policy. Reads and writes are serialized by the
    coordinator, and the link serializes the requests themselves.
    """

    def __init__(self, unit_factory: Callable[[int], ModbusUnit], *, port: str) -> None:
        self._unit_factory = unit_factory
        self._port = port
        self._policy = new_transport_policy()
        self._units: dict[int, PolicyUnit] = {}
        self._devices: dict[int, MeltemRoomDevice] = {}
        self._optional_read_backoff_until: dict[tuple[int, str], float] = {}
        self._optional_read_failures: dict[tuple[int, str], int] = {}
        self._optional_read_errors: dict[tuple[int, str], str] = {}
        self._read_group_errors: dict[str, str] = {}
        self._read_group_skipped: set[str] = set()
        self._unit_silent_error: ModbusError | None = None
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

    def shutdown(self) -> None:
        """Reject every later operation, so a late readback cannot reach the link."""

        self._shut_down = True

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
    ) -> tuple[str, str | None, list[str]]:
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
            self._read_group_errors = {}
            self._read_group_skipped = set()
            self._unit_silent_error = None
            device = self._room_device(room)
            updated = await self._poll(device, self._due_reads(room, refresh_plan))
            prev = previous_state

            def fresh(component: str, field: str, previous: Any, *, due: bool = True) -> Any:
                if not due or component not in updated:
                    return previous
                value = getattr(getattr(device, component), field)
                if isinstance(value, float) and not math.isfinite(value):
                    value = None
                return self._coalesce(value, previous)

            extract_air_flow = fresh(
                "airflow",
                "extract_air_flow",
                prev.extract_air_flow,
                due=self._supports(room, "extract_air_flow"),
            )
            supply_air_flow = fresh(
                "airflow",
                "supply_air_flow",
                prev.supply_air_flow,
                due=self._supports(room, "supply_air_flow"),
            )
            environment = self._profile_state(room, prev, refresh_plan, fresh)
            error = fresh(
                "status",
                "error_status",
                prev.error_status,
                due=self._supports(room, "error_status") and refresh_plan.refresh_status,
            )
            filter_due = fresh(
                "status",
                "filter_change_due",
                prev.filter_change_due,
                due=self._supports(room, "filter_change_due")
                and refresh_plan.refresh_filter_change_due,
            )
            frost = fresh(
                "status",
                "frost_protection_active",
                prev.frost_protection_active,
                due=self._supports(room, "frost_protection_active")
                and refresh_plan.refresh_status,
            )
            days = fresh(
                "days_until_filter_change",
                "value",
                prev.days_until_filter_change,
            )
            hours = fresh("operating_hours", "operating_hours", prev.operating_hours)
            software_version = fresh("software_version", "value", prev.software_version)
            control_settings = {
                key: fresh(
                    "control_settings",
                    key,
                    getattr(prev, key),
                    due=self._supports(room, key),
                )
                for key in CONTROL_SETTING_REGISTERS
            }
            rf_comm_status = fresh("rf_comm_status", "value", prev.rf_comm_status)

            if refresh_plan.refresh_airflow:
                mode = await self._read_mode_group(
                    room,
                    device,
                    prev,
                    extract_air_flow,
                    supply_air_flow,
                )
            else:
                mode = _ModeGroup.unchanged(prev)
            group_read_health = self._updated_read_health(
                room,
                prev,
                refresh_plan,
            )

        # ``environment`` already carries the temperature and air-quality fields.
        return replace(
            environment,
            error_status=_to_optional_bool(error),
            filter_change_due=_to_optional_bool(filter_due),
            frost_protection_active=_to_optional_bool(frost),
            rf_comm_status=_to_optional_bool(rf_comm_status),
            extract_air_flow=extract_air_flow,
            supply_air_flow=supply_air_flow,
            operation_mode=mode.operation_mode,
            preset_mode=mode.preset_mode,
            intensive_active=mode.intensive_active,
            days_until_filter_change=days,
            operating_hours=hours,
            software_version=software_version,
            target_level=mode.target_level,
            extract_target_level=mode.extract_target_level,
            group_read_health=tuple(sorted(group_read_health.items())),
            **control_settings,
        )

    def _due_reads(self, room: RoomConfig, refresh_plan: RefreshPlan) -> list[_DueRead]:
        """Return the components this plan reads, in the gateway's usual order."""

        due: list[_DueRead] = []

        def add(component: str, *groups: str) -> None:
            due.append(_DueRead(component, groups))

        if refresh_plan.refresh_airflow and (
            self._supports(room, "extract_air_flow")
            or self._supports(room, "supply_air_flow")
        ):
            add("airflow", "flow")

        do_temp = refresh_plan.refresh_temperatures
        do_env = refresh_plan.refresh_environment
        if room.profile in PLAIN_PROFILES:
            if self._supports(room, "exhaust_temperature") and do_temp:
                add("temperatures", "temperature")
        else:
            if (
                (self._supports(room, "exhaust_temperature") and do_temp)
                or (self._supports(room, "outdoor_air_temperature") and do_env)
                or (self._supports(room, "extract_air_temperature") and do_temp)
            ):
                add("temperatures", "temperature")
            if self._supports(room, "supply_air_temperature") and do_temp:
                add("supply_temperature", "temperature")
            if do_env and (
                self._environment_due(room, "humidity_extract_air", HUMIDITY_PROFILES)
                or self._environment_due(room, "co2_extract_air", CO2_PROFILES)
            ):
                add("extract_air_quality", "temperature")
            if do_env and (
                self._environment_due(room, "humidity_supply_air", HUMIDITY_PROFILES)
                or self._environment_due(room, "voc_supply_air", VOC_PROFILES)
            ):
                add("supply_air_quality", "temperature")

        status_groups = tuple(
            group_key
            for group_key, is_due in (
                (
                    "status",
                    refresh_plan.refresh_status
                    and (
                        self._supports(room, "error_status")
                        or self._supports(room, "frost_protection_active")
                    ),
                ),
                (
                    "filter",
                    refresh_plan.refresh_filter_change_due
                    and self._supports(room, "filter_change_due"),
                ),
            )
            if is_due
        )
        if status_groups:
            add("status", *status_groups)

        if refresh_plan.refresh_filter_days and self._supports(
            room, "days_until_filter_change"
        ):
            add("days_until_filter_change", "filter")
        if refresh_plan.refresh_operating_hours:
            if self._supports(room, "operating_hours"):
                add("operating_hours", "hours")
            add("software_version", "hours")
        if refresh_plan.refresh_control_settings and any(
            self._supports(room, key) for key in CONTROL_SETTING_REGISTERS
        ):
            add("control_settings", "control_settings")
        if refresh_plan.refresh_status and self._supports(room, "rf_comm_status"):
            add("rf_comm_status", "status")
        return due

    def _environment_due(
        self, room: RoomConfig, key: str, profiles: frozenset[str]
    ) -> bool:
        return room.profile in profiles and self._supports(room, key)

    async def _poll(self, device: MeltemRoomDevice, due: list[_DueRead]) -> set[str]:
        """Read the due components and record failures per health group.

        A timeout before anything answered means the unit is silent, so the
        rest of the job is skipped instead of timing out once per block.
        """

        if not due:
            return set()
        try:
            report = await device.async_poll([read.component for read in due])
        except ModbusTimeoutError as err:
            self._unit_silent_error = err
            for read in due:
                self._record_group_read_error(read.groups, err)
            return set()
        for read in due:
            error = report.failed.get(read.component)
            if error is not None:
                self._record_group_read_error(read.groups, error)
        return report.updated

    def _profile_state(
        self,
        room: RoomConfig,
        prev: RoomState,
        refresh_plan: RefreshPlan,
        fresh: Callable[..., Any],
    ) -> RoomState:
        do_temp = refresh_plan.refresh_temperatures
        do_env = refresh_plan.refresh_environment
        extended = room.profile not in PLAIN_PROFILES

        return RoomState(
            exhaust_temperature=fresh(
                "temperatures",
                "exhaust_temperature",
                prev.exhaust_temperature,
                due=self._supports(room, "exhaust_temperature") and do_temp,
            ),
            outdoor_air_temperature=fresh(
                "temperatures",
                "outdoor_air_temperature",
                prev.outdoor_air_temperature,
                due=extended and self._supports(room, "outdoor_air_temperature") and do_env,
            ),
            extract_air_temperature=fresh(
                "temperatures",
                "extract_air_temperature",
                prev.extract_air_temperature,
                due=extended and self._supports(room, "extract_air_temperature") and do_temp,
            ),
            supply_air_temperature=fresh(
                "supply_temperature",
                "supply_air_temperature",
                prev.supply_air_temperature,
                due=extended,
            ),
            humidity_extract_air=fresh(
                "extract_air_quality",
                "humidity_extract_air",
                prev.humidity_extract_air,
                due=extended
                and self._environment_due(room, "humidity_extract_air", HUMIDITY_PROFILES),
            ),
            co2_extract_air=fresh(
                "extract_air_quality",
                "co2_extract_air",
                prev.co2_extract_air,
                due=extended and self._environment_due(room, "co2_extract_air", CO2_PROFILES),
            ),
            humidity_supply_air=fresh(
                "supply_air_quality",
                "humidity_supply_air",
                prev.humidity_supply_air,
                due=extended
                and self._environment_due(room, "humidity_supply_air", HUMIDITY_PROFILES),
            ),
            voc_supply_air=fresh(
                "supply_air_quality",
                "voc_supply_air",
                prev.voc_supply_air,
                due=extended and self._environment_due(room, "voc_supply_air", VOC_PROFILES),
            ),
        )

    def _updated_read_health(
        self,
        room: RoomConfig,
        previous_state: RoomState,
        refresh_plan: RefreshPlan,
    ) -> dict[str, ReadHealth]:
        """Update health only for expected groups selected by this read plan."""

        group_health = dict(previous_state.group_read_health)
        now = dt_util.utcnow()
        for group_key in refresh_plan.read_groups():
            if group_key in self._read_group_skipped:
                continue
            if not any(
                self._supports(room, key) for key in READ_GROUP_ENTITY_KEYS[group_key]
            ):
                continue

            previous_health = previous_state.read_health_for(group_key)
            error = self._read_group_errors.get(group_key)
            if error is None:
                group_health[group_key] = ReadHealth(
                    last_attempt=now,
                    last_successful_read=now,
                )
            else:
                group_health[group_key] = ReadHealth(
                    last_attempt=now,
                    last_successful_read=previous_health.last_successful_read,
                    consecutive_failures=min(
                        previous_health.consecutive_failures + 1,
                        READ_FAILURE_THRESHOLD,
                    ),
                    last_error=error,
                )
        return group_health

    # ------------------------------------------------------------------
    #  Writes
    # ------------------------------------------------------------------

    async def write_level(self, room: RoomConfig, level: int) -> None:
        """Write off/manual mode and target level for one room."""

        mode = MODE_OFF if level == 0 else MODE_MANUAL
        raw_level = self._scale_airflow_to_raw(room, level)

        async with self._gateway_operation(f"writing level for room {room.key}"):
            device = self._room_device(room)
            await device.mode.write("mode", mode)
            await device.mode.write("current_level", raw_level)
            await device.command.write("apply", 0)
            self._clear_optional_airflow_read_backoff(room.slave)

    async def write_unbalanced_levels(
        self, room: RoomConfig, supply_level: int, extract_level: int
    ) -> None:
        """Write unbalanced mode with separate supply and extract levels."""

        raw_supply_level = self._scale_airflow_to_raw(room, supply_level)
        raw_extract_level = self._scale_airflow_to_raw(room, extract_level)

        async with self._gateway_operation(
            f"writing unbalanced levels for room {room.key}"
        ):
            device = self._room_device(room)
            await device.mode.write("mode", MODE_UNBALANCED)
            await device.mode.write("current_level", raw_supply_level)
            await device.mode.write("extract_target_level", raw_extract_level)
            await device.command.write("apply", 0)
            self._clear_optional_airflow_read_backoff(room.slave)

    async def write_operating_mode(
        self,
        room: RoomConfig,
        operation_mode: str,
        balanced_level: int,
        extract_level: int,
    ) -> None:
        """Write one operating mode using the documented control registers."""

        async with self._gateway_operation(f"writing operating mode for room {room.key}"):
            mode_block = self._room_device(room).mode
            if operation_mode == OPERATION_MODE_OFF:
                await mode_block.write("mode", MODE_OFF)
                await mode_block.write("current_level", 0)
            elif operation_mode == OPERATION_MODE_MANUAL:
                await mode_block.write("mode", MODE_MANUAL)
                await mode_block.write(
                    "current_level", self._scale_airflow_to_raw(room, balanced_level)
                )
            elif operation_mode == OPERATION_MODE_UNBALANCED:
                await mode_block.write("mode", MODE_UNBALANCED)
                await mode_block.write(
                    "current_level", self._scale_airflow_to_raw(room, balanced_level)
                )
                await mode_block.write(
                    "extract_target_level",
                    self._scale_airflow_to_raw(room, extract_level),
                )
            else:
                sensor_control_value = SENSOR_MODE_TO_RAW_VALUE.get(operation_mode)
                if sensor_control_value is None:
                    raise MeltemModbusError(
                        f"Unsupported operating mode {operation_mode!r} for room {room.key}"
                    )
                await mode_block.write("mode", MODE_SENSOR_CONTROL)
                await mode_block.write("current_level", sensor_control_value)
            await self._room_device(room).command.write("apply", 0)
            self._clear_optional_airflow_read_backoff(room.slave)

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

        async with self._gateway_operation(f"writing preset mode for room {room.key}"):
            device = self._room_device(room)
            if preset_mode == PRESET_MODE_INTENSIVE:
                await device.mode.write("preset_mode", MODE_MANUAL)
                await device.mode.write("preset_value", raw_code)
            else:
                await self._clear_secondary_preset_registers(device)
                await device.mode.write("mode", MODE_MANUAL)
                await device.mode.write("current_level", raw_code)
            await device.command.write("apply", 0)
            self._clear_optional_airflow_read_backoff(room.slave)

    async def clear_intensive(self, room: RoomConfig) -> None:
        """Cancel a running intensive override.

        Only the dedicated shadow registers are touched, so the base quick mode
        and the airflow targets stay untouched.
        """

        async with self._gateway_operation(f"clearing intensive mode for room {room.key}"):
            device = self._room_device(room)
            await self._clear_secondary_preset_registers(device)
            await device.command.write("apply", 0)
            self._clear_optional_airflow_read_backoff(room.slave)

    async def write_control_setting(
        self,
        room: RoomConfig,
        setting_key: str,
        value: int,
    ) -> int:
        """Write one humidity/CO2 control setting register."""

        limits = CONTROL_SETTING_LIMITS.get(setting_key)
        if setting_key not in CONTROL_SETTING_REGISTERS or limits is None:
            raise MeltemModbusError(
                f"Unsupported control setting {setting_key!r} for room {room.key}"
            )

        min_val, max_val, step = limits
        clamped = max(min_val, min(max_val, int(round(value))))
        stepped = min_val + ((clamped - min_val + step // 2) // step) * step

        async with self._gateway_operation(f"writing control setting for room {room.key}"):
            await self._room_device(room).control_settings.write(setting_key, stepped)
        return stepped

    async def _clear_secondary_preset_registers(self, device: MeltemRoomDevice) -> None:
        """Clear the dedicated intensive-preset shadow registers before other presets."""

        await device.mode.write("preset_mode", 0)
        await device.mode.write("preset_value", 0)

    # ------------------------------------------------------------------
    #  Mode family (optional reads with backoff)
    # ------------------------------------------------------------------

    async def _read_mode_component(
        self,
        room: RoomConfig,
        device: MeltemRoomDevice,
        name: str,
        read_group: str | tuple[str, ...],
    ) -> Component | None:
        """Read one mode-family component.

        Only exception responses start the temporary backoff: they mean the
        unit refuses the registers (HW-4). A timeout means the unit went
        silent, so the rest of the job is skipped instead.
        """

        if self._unit_silent_error is not None:
            self._record_group_read_error(read_group, self._unit_silent_error)
            return None

        key = (room.slave, name)
        if self._is_optional_read_backed_off(key):
            self._record_group_read_error(
                read_group,
                self._optional_read_errors.get(key, "Read is temporarily backed off"),
            )
            return None

        component: Component = getattr(device, name)
        try:
            await component.async_update()
        except ModbusConnectionError:
            raise
        except ModbusTimeoutError as err:
            self._unit_silent_error = err
            self._record_group_read_error(read_group, err)
            return None
        except ModbusExceptionError as err:
            self._mark_optional_read_failure(key, err)
            self._record_group_read_error(read_group, err)
            return None
        except ModbusError as err:
            self._record_group_read_error(read_group, err)
            return None

        self._clear_optional_read_failure(key)
        return component

    async def _read_mode_value(
        self, room: RoomConfig, device: MeltemRoomDevice, name: str
    ) -> int | None:
        """Read one register of the mode block on its own."""

        component = await self._read_mode_component(room, device, name, "flow_control")
        return None if component is None else getattr(component, name)

    async def _read_mode_block(
        self,
        room: RoomConfig,
        device: MeltemRoomDevice,
    ) -> tuple[list[int] | None, bool]:
        """Read the mode register block, falling back to a shorter read.

        Returns the block and whether the full 5-register variant was readable;
        many units reject the longer read until a write has occurred.
        """

        needs_full_mode_block = (
            self._supports(room, "operation_mode")
            or self._supports(room, "preset_mode")
            or self._supports(room, "intensive")
        )
        if not needs_full_mode_block:
            return None, False

        full = await self._read_mode_component(
            room, device, "mode", ("flow_control", "intensive")
        )
        if full is not None:
            return [
                full.mode,
                full.current_level,
                full.extract_target_level,
                full.preset_mode,
                full.preset_value,
            ], True

        if self._supports(room, "operation_mode") or self._supports(room, "preset_mode"):
            short = await self._read_mode_component(
                room, device, "mode_short", "flow_control"
            )
            if short is not None:
                self._read_group_errors.pop("flow_control", None)
                # The unit answers, it just lacks the long read (HW-4), so the
                # intensive state is unknown rather than a read failure.
                self._read_group_errors.pop("intensive", None)
                self._read_group_skipped.add("intensive")
                return [short.mode, short.current_level], False
        return None, False

    async def _read_mode_group(
        self,
        room: RoomConfig,
        device: MeltemRoomDevice,
        previous_state: RoomState,
        extract_air_flow: int | None,
        supply_air_flow: int | None,
    ) -> _ModeGroup:
        """Read and decode operating mode, airflow targets, and preset state."""

        mode_block, full_mode_block_available = await self._read_mode_block(room, device)

        operation_mode = (
            self._decode_operation_mode(mode_block[0], mode_block[1])
            if mode_block is not None and len(mode_block) >= 2
            else previous_state.operation_mode
        )
        # 41121/41122 are part of the mode block, so only read them on their
        # own when the block did not deliver them.
        raw_current_level = (
            mode_block[1]
            if mode_block is not None and len(mode_block) >= 2
            else await self._read_mode_value(room, device, "current_level")
        )

        raw_extract_target: int | None = None
        if operation_mode == OPERATION_MODE_UNBALANCED:
            target_level = self._decode_unbalanced_target_readback(
                room,
                raw_current_level,
            )
            raw_extract_target = (
                mode_block[2]
                if full_mode_block_available and mode_block is not None
                else await self._read_mode_value(room, device, "extract_target_level")
            )
            extract_target_level = (
                self._decode_unbalanced_target_readback(room, raw_extract_target)
                if raw_extract_target is not None
                else previous_state.extract_target_level
            )
        elif operation_mode in SENSOR_OPERATION_MODES:
            # Here 41121 holds the mode selector (112/144/16), not an airflow.
            # Decoding it as a level would yield plausible-looking nonsense.
            target_level = derive_balanced_airflow(extract_air_flow, supply_air_flow)
            extract_target_level = None
        else:
            # On the tested gateway, REGISTER_CURRENT_LEVEL behaves as a fast
            # target readback after balanced writes even though the vendor docs
            # describe it primarily as a write path. The airflow registers can
            # lag noticeably behind after a write, so use 41121 for target
            # confirmation when it looks like a valid balanced raw level and
            # fall back to derived airflow otherwise.
            target_level = self._decode_balanced_target_readback(
                room,
                raw_current_level,
                extract_air_flow,
                supply_air_flow,
            )
            extract_target_level = None

        return _ModeGroup(
            operation_mode=operation_mode,
            target_level=target_level,
            extract_target_level=extract_target_level,
            preset_mode=self._decode_preset_mode_with_fallback(
                mode_block=mode_block,
                full_mode_block_available=full_mode_block_available,
                raw_extract_target=raw_extract_target,
                previous_preset_mode=previous_state.preset_mode,
            ),
            intensive_active=self._decode_intensive_active(
                mode_block=mode_block,
                full_mode_block_available=full_mode_block_available,
                previous_intensive_active=previous_state.intensive_active,
            ),
        )

    def _is_optional_read_backed_off(self, key: tuple[int, str]) -> bool:
        """Return whether one optional register read is temporarily suppressed."""

        backoff_until = self._optional_read_backoff_until.get(key)
        if backoff_until is None:
            return False
        if time.monotonic() >= backoff_until:
            self._optional_read_backoff_until.pop(key, None)
            return False
        return True

    def _mark_optional_read_failure(
        self,
        key: tuple[int, str],
        error: Exception | str,
    ) -> None:
        """Increase backoff after one optional register read failed."""

        failures = min(
            self._optional_read_failures.get(key, 0) + 1,
            _OPTIONAL_READ_BACKOFF_MAX_FAILURES,
        )
        self._optional_read_failures[key] = failures
        self._optional_read_errors[key] = str(error)
        delay_seconds = min(
            _OPTIONAL_READ_BACKOFF_MAX_SECONDS,
            _OPTIONAL_READ_BACKOFF_START_SECONDS * (2 ** (failures - 1)),
        )
        self._optional_read_backoff_until[key] = time.monotonic() + delay_seconds

    def _clear_optional_read_failure(self, key: tuple[int, str]) -> None:
        """Clear any failure/backoff state after a successful optional read."""

        self._optional_read_failures.pop(key, None)
        self._optional_read_backoff_until.pop(key, None)
        self._optional_read_errors.pop(key, None)

    def _record_group_read_error(
        self,
        group_keys: str | tuple[str, ...] | None,
        error: Exception | str,
    ) -> None:
        """Remember a swallowed optional-read error for each affected group."""

        if group_keys is None:
            return
        keys = (group_keys,) if isinstance(group_keys, str) else group_keys
        for group_key in keys:
            self._read_group_errors.setdefault(group_key, str(error))

    def _clear_optional_airflow_read_backoff(self, slave: int) -> None:
        """Clear airflow-related optional read backoff after a successful write."""

        for name in _MODE_COMPONENTS:
            self._clear_optional_read_failure((slave, name))

    # ------------------------------------------------------------------
    #  Tiny helpers
    # ------------------------------------------------------------------

    def _coalesce(self, value, previous_value):
        return previous_value if value is None else value

    def _scale_airflow_to_raw(self, room: RoomConfig, level: int) -> int:
        max_airflow = profile_max_airflow(room.profile)
        return max(0, min(200, round(level * 200 / max_airflow)))

    def _scale_raw_level_to_airflow(
        self, room: RoomConfig, raw_level: int | None
    ) -> int | None:
        if raw_level is None:
            return None
        return round(raw_level * profile_max_airflow(room.profile) / 200)

    def _decode_balanced_target_readback(
        self,
        room: RoomConfig,
        raw_level: int | None,
        extract_air_flow: int | None,
        supply_air_flow: int | None,
    ) -> int | None:
        """Return a balanced target readback or fall back to measured airflow."""

        if raw_level is not None and 0 <= raw_level <= 200:
            return self._scale_raw_level_to_airflow(room, raw_level)

        # A shared "current level" only makes sense when both airflow
        # directions are effectively balanced.
        return derive_balanced_airflow(extract_air_flow, supply_air_flow)

    def _decode_unbalanced_target_readback(
        self,
        room: RoomConfig,
        raw_level: int | None,
    ) -> int | None:
        """Decode one unbalanced target, including app-side preset encodings."""

        if raw_level is None:
            return None
        if 0 <= raw_level <= 200:
            return self._scale_raw_level_to_airflow(room, raw_level)
        if raw_level > APP_UNBALANCED_PRESET_BASE:
            # The app encodes the shortcut airflow as 200 + m3/h per 10. Quick
            # mode codes land in the same range but decode far above the rated
            # airflow, so they must not be reported as a target.
            max_airflow = profile_max_airflow(room.profile)
            airflow = (raw_level - APP_UNBALANCED_PRESET_BASE) * 10
            return airflow if airflow <= max_airflow else None
        return None

    def _decode_operation_mode(
        self,
        mode_value: int | None,
        current_value: int | None,
    ) -> str | None:
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
        self, mode_block: list[int] | None, raw_extract_target: int | None
    ) -> str | None:
        """Return the base quick mode from 41120..41122, including short reads.

        The intensive override lives in the 41123/41124 shadow registers and
        does not replace the base quick mode, so those are ignored here.
        """

        if mode_block is None or len(mode_block) < 2:
            return None
        extract_target = mode_block[2] if len(mode_block) >= 3 else raw_extract_target
        if mode_block[0] == MODE_UNBALANCED and extract_target is not None:
            if mode_block[1] == 0 and extract_target > APP_UNBALANCED_PRESET_BASE:
                return PRESET_MODE_EXTRACT_ONLY
            if extract_target == 0 and mode_block[1] > APP_UNBALANCED_PRESET_BASE:
                return PRESET_MODE_SUPPLY_ONLY
        if mode_block[0] != MODE_MANUAL:
            return None
        return RAW_CODE_TO_PRESET_MODE.get(mode_block[1])

    def _decode_preset_mode_with_fallback(
        self,
        *,
        mode_block: list[int] | None,
        full_mode_block_available: bool,
        raw_extract_target: int | None,
        previous_preset_mode: str | None,
    ) -> str | None:
        """Decode preset mode without keeping stale values after known non-preset states."""

        if full_mode_block_available:
            decoded = self._decode_preset_mode(mode_block, raw_extract_target)
            if decoded == PRESET_MODE_INTENSIVE:
                return previous_preset_mode
            return decoded

        if mode_block is None:
            return previous_preset_mode

        return self._decode_preset_mode(mode_block, raw_extract_target)

    def _decode_intensive_active(
        self,
        *,
        mode_block: list[int] | None,
        full_mode_block_available: bool,
        previous_intensive_active: bool | None,
    ) -> bool | None:
        """Return whether the temporary intensive override is currently active."""

        if full_mode_block_available and mode_block is not None:
            return (
                mode_block[3] == MODE_MANUAL
                and mode_block[4] == PRESET_MODE_CODE_INTENSIVE
            )
        if mode_block is None or len(mode_block) < 5:
            return previous_intensive_active
        return None

    def _supports(self, room: RoomConfig, entity_key: str) -> bool:
        if room.supported_entity_keys is None:
            return True
        return entity_key in room.supported_entity_keys
