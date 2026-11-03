"""Runtime Modbus client for Meltem Modbus ventilation units.

This module contains the long-lived :class:`MeltemModbusClient` that the
coordinator uses for all reads and writes during normal operation, and the pure
helpers that plan a room read. ``mode_codec.py`` encodes the mode commands and
decodes the mode registers.

The register blocks live in ``device/``. Setup-time helpers (link parameters,
scans, profile probes, and pure utility functions) live in ``modbus_helpers.py``.
"""

from __future__ import annotations

import logging
import math
import time
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field, replace
from typing import Any, cast

from homeassistant.util import dt as dt_util
from modbus_connection import (
    AcknowledgeError,
    ModbusConnectionError,
    ModbusError,
    ModbusExceptionError,
    ModbusUnit,
)

from .const import (
    CO2_PROFILES,
    CONTROL_SETTING_REGISTERS,
    DEFAULT_GATEWAY_DEVICE_ID,
    EXTERNAL_INPUT_LIMITS,
    EXTERNAL_INPUT_REGISTERS,
    FIXED_TIMEOUT,
    HUMIDITY_PROFILES,
    MODE_MANUAL,
    MODE_OFF,
    MODE_UNBALANCED,
    PLAIN_PROFILES,
    READ_GROUP_ENTITY_KEYS,
    REGISTER_CURRENT_LEVEL,
    REQUEST_GAP_SECONDS,
    VOC_PROFILES,
)
from .device import MeltemGateway, MeltemRoomDevice
from .device.transport import NO_ANSWER_ERRORS, ReadQuarantinedError, RequestOutcome
from .environment import absolute_humidity
from .modbus_helpers import (
    PLAUSIBLE_RANGES,
    MeltemConnectionError,
    MeltemModbusError,
    detect_slave_details,
    discover_gateway_nodes,
    is_plausible,
    new_transport_policy,
    normalize_control_setting,
    prepare_unit,
    read_gateway_node_count,
)
from .mode_codec import (
    CLEAR_INTENSIVE,
    ModeRead,
    apply_mode_read,
    operating_mode_writes,
    preset_mode_writes,
    scale_airflow_to_raw,
    scale_raw_level_to_airflow,
)
from .models import (
    GatewayRfStatistics,
    ReadHealth,
    ReadJobOutcome,
    RefreshPlan,
    RoomConfig,
    RoomRead,
    RoomState,
    UnitProbeResult,
)

_LOGGER = logging.getLogger(__name__)

_OPTIONAL_READ_BACKOFF_START_SECONDS = 30.0
_OPTIONAL_READ_BACKOFF_MAX_SECONDS = 300.0
_OPTIONAL_READ_BACKOFF_MAX_FAILURES = 5
_COMPONENT_READ_BACKOFF_START_SECONDS = 10.0
_COMPONENT_READ_BACKOFF_MAX_SECONDS = 60.0
_COMPONENT_READ_BACKOFF_MAX_FAILURES = 4

_MODE_GROUPS: tuple[str, ...] = ("flow_control", "intensive")
_MODE_ENTITY_KEYS: tuple[str, ...] = ("operation_mode", "preset_mode", "intensive")

# Fields of 0/1 registers that ``RoomState`` holds as bool.
_FLAG_FIELDS = frozenset(
    {"error_status", "filter_change_due", "frost_protection_active", "rf_comm_status"}
)

_BRIDGE_RF_COMPONENTS = frozenset(
    {
        "rf_comm_status",
        "device_fault",
        "value_error_status",
        "rf_last_seen_age",
        "rf_signal_strength",
    }
)
_OPTIONAL_DIAGNOSTIC_COMPONENTS = (_BRIDGE_RF_COMPONENTS - {"rf_comm_status"}) | {"fan_motor_hours"}

# The two APK-only fields come from the block the VOC profile reads anyway.
_AIR_QUALITY_FIELDS: tuple[tuple[str, str, frozenset[str]], ...] = (
    ("humidity_extract_air", "air_quality", HUMIDITY_PROFILES),
    ("co2_extract_air", "air_quality", CO2_PROFILES),
    ("voc_extract_air", "air_quality", VOC_PROFILES),
    ("co2_supply_air", "air_quality", VOC_PROFILES),
    ("humidity_supply_air", "air_quality", HUMIDITY_PROFILES),
    ("voc_supply_air", "air_quality", VOC_PROFILES),
)

# Sensors that define a profile. Only the app's register map knows the other two
# (docs/dev/MELTEM.md), so a placeholder there says nothing about the profile.
PROFILE_SENSORS = frozenset(
    {"humidity_extract_air", "humidity_supply_air", "co2_extract_air", "voc_supply_air"}
)


def placeholder_sensors(room: RoomConfig) -> frozenset[str]:
    """Return the profile sensors of a unit that can report a "no sensor" placeholder."""

    return frozenset(
        name
        for name, _component, profiles in _AIR_QUALITY_FIELDS
        if name in PROFILE_SENSORS
        and name in PLAUSIBLE_RANGES
        and room.profile in profiles
        and room.supports(name)
    )


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
    skipped_groups: set[str] = field(default_factory=set)
    # Groups with no valid answer, rather than an exception response from the unit.
    timeouts: set[str] = field(default_factory=set)
    # Set once the unit timed out; the rest of the job is skipped then.
    silent_error: ModbusError | None = None
    had_answer: bool = False
    attempted_groups: set[str] = field(default_factory=set)
    value_groups: set[str] = field(default_factory=set)
    unit_success_groups: set[str] = field(default_factory=set)
    missing_sensor_values: set[str] = field(default_factory=set)
    sensor_values: set[str] = field(default_factory=set)

    def skip(self, groups: tuple[str, ...]) -> None:
        """Keep existing health unchanged when a component is backed off."""

        self.skipped_groups.update(groups)

    def record_error(self, groups: tuple[str, ...], error: Exception | str) -> None:
        """Keep the first swallowed error of each affected group."""

        for group in groups:
            self.errors.setdefault(group, str(error))
            if isinstance(error, NO_ANSWER_ERRORS):
                self.timeouts.add(group)


@dataclass(slots=True)
class _ReadBackoff:
    """Growing pause per ``(slave, component)`` for reads a unit refuses."""

    start_seconds: float = _OPTIONAL_READ_BACKOFF_START_SECONDS
    max_seconds: float = _OPTIONAL_READ_BACKOFF_MAX_SECONDS
    max_failures: int = _OPTIONAL_READ_BACKOFF_MAX_FAILURES
    failures: dict[_BackoffKey, int] = field(default_factory=dict)
    until: dict[_BackoffKey, float] = field(default_factory=dict)

    def is_active(self, key: _BackoffKey) -> bool:
        """Return whether the read is still paused."""

        paused_until = self.until.get(key)
        if paused_until is None:
            return False
        if time.monotonic() >= paused_until:
            del self.until[key]
            return False
        return True

    def mark_failure(self, key: _BackoffKey, *, now: float | None = None) -> None:
        """Pause the read twice as long as last time, up to the maximum."""

        failures = min(self.failures.get(key, 0) + 1, self.max_failures)
        self.failures[key] = failures
        base = time.monotonic() if now is None else now
        self.until[key] = base + min(
            self.max_seconds,
            self.start_seconds * 2 ** (failures - 1),
        )

    def clear(self, key: _BackoffKey) -> None:
        self.failures.pop(key, None)
        self.until.pop(key, None)

    def clear_slave(self, slave: int) -> None:
        for key in [key for key in self.failures if key[0] == slave]:
            self.clear(key)


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
        on_request: Callable[[RequestOutcome], None] | None = None,
        on_link_recycle: Callable[[], None] | None = None,
        message_spacing: float = REQUEST_GAP_SECONDS,
        timeout: float = FIXED_TIMEOUT,
    ) -> None:
        self._unit_factory = unit_factory
        self._port = port
        self._message_spacing = message_spacing
        self._timeout = timeout
        self._policy = new_transport_policy(
            timeout=timeout,
            on_request=on_request,
            on_link_recycle=on_link_recycle,
        )
        if max_requests_per_second is not None:
            self.update_request_rate(max_requests_per_second)
        self._units: dict[int, ModbusUnit] = {}
        self._devices: dict[tuple[int, str], MeltemRoomDevice] = {}
        self._component_backoff = _ReadBackoff(
            start_seconds=_COMPONENT_READ_BACKOFF_START_SECONDS,
            max_seconds=_COMPONENT_READ_BACKOFF_MAX_SECONDS,
            max_failures=_COMPONENT_READ_BACKOFF_MAX_FAILURES,
        )
        # Avoid repeatedly polling a status block that a unit refuses.
        self._mode_backoff = _ReadBackoff()
        # Mode registers an interrupted command wrote without its commit (HW-8).
        self._staged_mode_registers: dict[int, frozenset[str]] = {}
        self._shut_down = False

    def seconds_since_successful_read(self, slave: int) -> float | None:
        """Return the age of the last answered register read for one unit.

        Every optional read swallows its error, so this is the only reliable
        signal that a unit behind the gateway went silent.
        """

        return self._policy.seconds_since_read_answer(slave)

    def read_quarantined(self, slave: int) -> bool:
        """Return whether reads of a unit are blocked after an unanswered request."""

        return self._policy.read_quarantined(slave)

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

    def unit_for(self, slave: int) -> ModbusUnit:
        """Return the unit handle the client itself uses for ``slave``.

        Raw reads through it share the client's request-rate limit and read
        quarantine; the gateway tools use it for register snapshots.
        """

        return self._unit(slave)

    def _unit(self, slave: int) -> ModbusUnit:
        unit = self._units.get(slave)
        if unit is None:
            unit = self._units[slave] = prepare_unit(
                self._unit_factory(slave),
                slave,
                self._policy,
                message_spacing=self._message_spacing,
                timeout=self._timeout,
            )
        return unit

    def _room_device(self, room: RoomConfig) -> MeltemRoomDevice:
        key = (room.slave, room.profile)
        device = self._devices.get(key)
        if device is None:
            device = self._devices[key] = MeltemRoomDevice(
                self._unit(room.slave),
                exhaust_temperature_only=room.profile in PLAIN_PROFILES,
                has_co2=room.profile in CO2_PROFILES,
                has_voc=room.profile in VOC_PROFILES,
            )
        return device

    @asynccontextmanager
    async def _gateway_operation(self, description: str) -> AsyncIterator[None]:
        """Run one gateway operation and translate its errors."""

        if self._shut_down:
            raise MeltemConnectionError(f"Meltem gateway client on {self._port} was shut down")
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
        except Exception:
            _LOGGER.exception("Unexpected error while %s", description)
            raise

    async def async_read_gateway_uptime(self) -> int:
        """Read the gateway's uptime register pair."""

        async with self._gateway_operation("reading gateway uptime"):
            return await MeltemGateway(self._unit(DEFAULT_GATEWAY_DEVICE_ID)).async_read_uptime()

    async def async_read_room_app_setup_code(self, room: RoomConfig) -> str | None:
        """Read one room's DataMatrix code without adding it to regular polling."""

        async with self._gateway_operation(f"reading app setup code for room {room.key}"):
            return await self._room_device(room).async_read_app_setup_code()

    async def async_read_external_input_setting(self, room: RoomConfig, key: str) -> int:
        """Read one documented external-input setting without changing it."""

        if key not in EXTERNAL_INPUT_REGISTERS:
            raise MeltemModbusError(f"Unknown external input setting {key!r}")
        address = EXTERNAL_INPUT_REGISTERS[key]
        async with self._gateway_operation(f"reading {key} for room {room.key}"):
            (value,) = await self._unit(room.slave).read_holding_registers(address, 1)
        minimum, maximum = EXTERNAL_INPUT_LIMITS[key]
        if not minimum <= value <= maximum:
            raise MeltemModbusError(f"Invalid {key} readback: {value}")
        return value

    async def async_read_gateway_node_count(self) -> int:
        """Read how many devices are registered with the gateway."""

        async with self._gateway_operation("reading gateway device count"):
            return await MeltemGateway(
                self._unit(DEFAULT_GATEWAY_DEVICE_ID)
            ).async_read_node_count()

    async def async_read_gateway_rf_statistics(self) -> GatewayRfStatistics:
        """Read the gateway's hourly RF message counters and load."""

        async with self._gateway_operation("reading gateway RF statistics"):
            return await MeltemGateway(
                self._unit(DEFAULT_GATEWAY_DEVICE_ID)
            ).async_read_rf_statistics()

    async def async_read_gateway_mac(self) -> str:
        """Read the gateway's Ethernet MAC address."""

        async with self._gateway_operation("reading gateway MAC address"):
            return await MeltemGateway(self._unit(DEFAULT_GATEWAY_DEVICE_ID)).async_read_mac()

    async def async_read_gateway_utc_clock(self) -> int:
        """Read the gateway's UTC clock register pair."""

        async with self._gateway_operation("reading gateway UTC clock"):
            return await MeltemGateway(self._unit(DEFAULT_GATEWAY_DEVICE_ID)).async_read_utc_clock()

    async def async_validate_gateway(self) -> None:
        """Read the gateway's node count once to prove the link works.

        The gateway unit is acquired before the error translation, so a
        conflicting link configuration surfaces unchanged.
        """

        unit = self._unit(DEFAULT_GATEWAY_DEVICE_ID)
        async with self._gateway_operation("validating the gateway"):
            await read_gateway_node_count(unit)

    async def async_disconnect_gateway(self) -> None:
        """Release the live connection so a replacement endpoint can be checked."""

        async with self._gateway_operation("disconnecting the gateway"):
            try:
                await self._unit(DEFAULT_GATEWAY_DEVICE_ID).disconnect()
            except ModbusConnectionError as err:
                _LOGGER.debug("Dropping the Meltem gateway link failed: %s", err)

    async def discover_gateway_units(
        self, start: int, end: int, *, strict: bool = False
    ) -> list[int]:
        """Discover configured unit addresses using the current gateway link.

        With ``strict`` a failed read raises instead of reporting no units.
        """

        async with self._gateway_operation("discovering units"):
            return await discover_gateway_nodes(
                self._unit(DEFAULT_GATEWAY_DEVICE_ID),
                self._port,
                start=start,
                end=end,
                strict=strict,
            )

    async def probe_slave_details(
        self,
        slave: int,
    ) -> UnitProbeResult:
        """Probe one configured unit using the current gateway link."""

        async with self._gateway_operation(f"probing unit {slave}"):
            return await detect_slave_details(self._unit(slave))

    async def read_room_state(
        self,
        room: RoomConfig,
        previous_state: RoomState | None = None,
        refresh_plan: RefreshPlan | None = None,
    ) -> RoomState:
        """Read one room like ``read_room`` and return only its state."""

        return (await self.read_room(room, previous_state, refresh_plan)).state

    async def read_room(
        self,
        room: RoomConfig,
        previous_state: RoomState | None = None,
        refresh_plan: RefreshPlan | None = None,
    ) -> RoomRead:
        """Read all relevant state for one room and report what the read did.

        ``RefreshPlan`` decides which groups are due in the current scheduler
        tick. Values outside the plan are copied forward from ``previous_state``.
        """

        previous_state = previous_state or RoomState()
        refresh_plan = refresh_plan or RefreshPlan()

        async with self._gateway_operation(f"reading room {room.key}"):
            device = self._room_device(room)
            job = _ReadJob()
            answered_before = self._policy.answered_requests
            state = previous_state
            if self._policy.read_delayed(room.slave):
                job.skip(refresh_plan.read_groups())
            else:
                due = _due_fields(room, refresh_plan)
                updated = await self._poll(device, _due_reads(due), job, slave=room.slave)
                state = replace(previous_state, **_fresh_values(device, due, updated, job))
                if refresh_plan.refresh_airflow:
                    block = await self._read_mode_block(room, device, job)
                    state = apply_mode_read(room, state, block)
            job.had_answer = self._policy.answered_requests > answered_before
            group_health, recorded = _updated_read_health(room, previous_state, refresh_plan, job)
            supported = frozenset(
                group
                for group in refresh_plan.read_groups()
                if room.supports_any(READ_GROUP_ENTITY_KEYS[group])
            )
            return RoomRead(
                replace(state, group_read_health=group_health),
                ReadJobOutcome(
                    attempted_groups=frozenset(job.attempted_groups) & supported,
                    recorded_groups=recorded,
                    errors={group: job.errors[group] for group in recorded if group in job.errors},
                    answered=job.had_answer,
                    value_groups=frozenset(job.value_groups) & supported,
                    unit_success_groups=frozenset(job.unit_success_groups) & supported,
                    missing_sensor_values=frozenset(job.missing_sensor_values),
                    sensor_values=frozenset(job.sensor_values),
                ),
            )

    async def read_balanced_target_readback(self, room: RoomConfig) -> int | None:
        """Read the exact set-side airflow target used to confirm balanced writes.

        Returns ``None`` when the register holds no raw ``0..200`` airflow, for
        example a quick-mode code that the app or keypad wrote after the write.
        """

        async with self._gateway_operation(
            f"reading balanced airflow target register {REGISTER_CURRENT_LEVEL} for room {room.key}"
        ):
            readback = self._room_device(room).current_level_readback
            await readback.async_update()
            raw_level = readback.value
        if raw_level is None or not 0 <= raw_level <= 200:
            return None
        return scale_raw_level_to_airflow(room, int(raw_level))

    async def _poll(
        self,
        device: MeltemRoomDevice,
        reads: dict[str, tuple[str, ...]],
        job: _ReadJob,
        *,
        slave: int,
    ) -> set[str]:
        """Read the due components and record failures per health group.

        ``reads`` maps each component to the health groups its outcome counts
        for. A timeout or protocol error means there was no valid answer, so
        the mode reads are skipped. A timeout before anything answered also
        stops the poll itself instead of timing out once per block.
        """

        if not reads:
            return set()
        active_reads: dict[str, tuple[str, ...]] = {}
        for component, groups in reads.items():
            key = (slave, component)
            if self._component_backoff.is_active(key):
                job.skip(groups)
                continue
            active_reads[component] = groups
        if not active_reads:
            return set()
        for groups in active_reads.values():
            job.attempted_groups.update(groups)

        started_at = time.monotonic()
        try:
            report = await device.async_poll(list(active_reads))
        except ModbusConnectionError:
            for component in active_reads:
                self._component_backoff.clear((slave, component))
            raise
        except NO_ANSWER_ERRORS as err:
            job.silent_error = err
            for component, groups in active_reads.items():
                self._component_backoff.clear((slave, component))
                job.record_error(groups, err)
            return set()
        for component, groups in active_reads.items():
            error = report.failed.get(component)
            if error is None:
                if component in report.updated:
                    self._component_backoff.clear((slave, component))
                    if component not in _BRIDGE_RF_COMPONENTS:
                        job.unit_success_groups.update(groups)
                continue
            if isinstance(error, ReadQuarantinedError):
                job.skip(groups)
                continue
            job.record_error(groups, error)
            if isinstance(error, NO_ANSWER_ERRORS):
                job.silent_error = error
            if isinstance(error, AcknowledgeError) or (
                component in _OPTIONAL_DIAGNOSTIC_COMPONENTS
                and isinstance(error, ModbusExceptionError)
            ):
                self._component_backoff.mark_failure((slave, component), now=started_at)
            else:
                self._component_backoff.clear((slave, component))
        return report.updated

    # ------------------------------------------------------------------
    #  Writes
    # ------------------------------------------------------------------

    async def write_level(self, room: RoomConfig, level: int) -> None:
        """Write off/manual mode and target level for one room."""

        clear_intensive = CLEAR_INTENSIVE if level == 0 else ()
        await self._write_mode_registers(
            room,
            "writing level",
            *clear_intensive,
            ("mode", MODE_OFF if level == 0 else MODE_MANUAL),
            ("current_level", scale_airflow_to_raw(room, level)),
        )

    async def write_unbalanced_levels(
        self, room: RoomConfig, supply_level: int, extract_level: int
    ) -> None:
        """Write unbalanced mode with separate supply and extract levels."""

        await self._write_mode_registers(
            room,
            "writing unbalanced levels",
            ("mode", MODE_UNBALANCED),
            ("current_level", scale_airflow_to_raw(room, supply_level)),
            ("extract_target_level", scale_airflow_to_raw(room, extract_level)),
        )

    async def write_operating_mode(
        self,
        room: RoomConfig,
        operation_mode: str,
        balanced_level: int,
        extract_level: int,
    ) -> None:
        """Write one operating mode using the documented control registers."""

        await self._write_mode_registers(
            room,
            "writing operating mode",
            *operating_mode_writes(room, operation_mode, balanced_level, extract_level),
        )

    async def write_preset_mode(
        self,
        room: RoomConfig,
        preset_mode: str,
    ) -> None:
        """Write one confirmed app-style preset mode."""

        await self._write_mode_registers(
            room,
            "writing preset mode",
            *preset_mode_writes(room, preset_mode),
        )

    async def clear_intensive(self, room: RoomConfig) -> None:
        """Cancel a running intensive override.

        Only the dedicated shadow registers are touched, so the base quick mode
        and the airflow targets stay untouched.
        """

        await self._write_mode_registers(room, "clearing intensive mode", *CLEAR_INTENSIVE)

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
        # Allow the coordinator's immediate confirmation read after this write.
        self._component_backoff.clear((room.slave, "control_settings"))
        return stepped

    async def _write_mode_registers(
        self, room: RoomConfig, action: str, *writes: tuple[str, int]
    ) -> None:
        """Write mode registers in order, then activate them with APPLY.

        A register written before an interrupted command stays staged on the
        unit and takes effect with the next APPLY, so a later command that does
        not write it again logs a warning (HW-8).
        """

        staged = self._staged_mode_registers.get(room.slave, frozenset())
        written: list[str] = []
        try:
            async with self._gateway_operation(f"{action} for room {room.key}"):
                device = self._room_device(room)
                for name, value in writes:
                    try:
                        await device.mode.write(name, value)
                    except ModbusExceptionError:
                        # The unit refused this register, so it holds nothing staged.
                        raise
                    except BaseException:
                        # A write without a response may still have reached the unit.
                        written.append(name)
                        raise
                    written.append(name)
                if left_over := staged - {name for name, _ in writes}:
                    _LOGGER.warning(
                        "Room %s (slave %s): %s also applies %s, left staged by an "
                        "interrupted command",
                        room.name,
                        room.slave,
                        action,
                        ", ".join(sorted(left_over)),
                    )
                await device.command.write("apply", 0)
        except BaseException:
            if written:
                self._staged_mode_registers[room.slave] = staged | frozenset(written)
            raise
        self._staged_mode_registers.pop(room.slave, None)
        # Let the post-write readback try a refused status map again.
        self._mode_backoff.clear_slave(room.slave)

    # ------------------------------------------------------------------
    #  Mode family (optional reads with backoff)
    # ------------------------------------------------------------------

    async def _read_mode_block(
        self, room: RoomConfig, device: MeltemRoomDevice, job: _ReadJob
    ) -> ModeRead | None:
        """Read the read-only current-state map, backing off on exception responses.

        The set registers at 41120..41124 remain the write interface. A timeout
        or protocol error skips the rest of the job.
        """

        if not room.supports_any(_MODE_ENTITY_KEYS):
            return None
        key = (room.slave, "mode_status")
        if self._mode_backoff.is_active(key):
            job.skip(_MODE_GROUPS)
            return None
        if job.silent_error is not None:
            job.record_error(_MODE_GROUPS, job.silent_error)
            return None

        status = device.mode_status
        job.attempted_groups.update(_MODE_GROUPS)
        started_at = time.monotonic()
        try:
            await status.async_update()
        except ModbusConnectionError:
            raise
        except ModbusError as err:
            if isinstance(err, NO_ANSWER_ERRORS):
                job.silent_error = err
            elif isinstance(err, ModbusExceptionError):
                self._mode_backoff.mark_failure(key, now=started_at)
            job.record_error(_MODE_GROUPS, err)
            return None

        self._mode_backoff.clear(key)
        job.unit_success_groups.update(_MODE_GROUPS)
        return ModeRead(
            mode=cast(int, status.mode),
            current_level=cast(int, status.current_level),
            extract_target_level=cast(int, status.extract_target_level),
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
    supply_temp_component = "air_quality" if environment else "supply_temperature"
    add("supply_air_temperature", supply_temp_component, "temperature", extended and temperatures)
    for name, component, profiles in _AIR_QUALITY_FIELDS:
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
    add(
        "fan_motor_hours",
        "fan_motor_hours",
        "fan_hours",
        plan.refresh_operating_hours,
        attribute="value",
    )
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
        profiles = HUMIDITY_PROFILES if key.startswith("humidity_") else CO2_PROFILES
        add(
            key,
            "control_settings",
            "control_settings",
            plan.refresh_control_settings and room.profile in profiles,
        )
    add("rf_comm_status", "rf_comm_status", "status", plan.refresh_status, attribute="value")
    add(
        "device_fault",
        "device_fault",
        "device_fault",
        plan.refresh_rf_diagnostics,
        attribute="value",
    )
    add(
        "value_error_status",
        "value_error_status",
        "value_error",
        plan.refresh_rf_diagnostics,
        attribute="value",
    )
    add(
        "rf_last_seen_age_seconds",
        "rf_last_seen_age",
        "rf_last_seen",
        plan.refresh_rf_diagnostics,
        attribute="value",
    )
    add(
        "rf_signal_strength",
        "rf_signal_strength",
        "rf_signal",
        plan.refresh_rf_diagnostics,
        attribute="value",
    )
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
    """Return the due fields whose component answered with a usable value.

    A non-finite value fails the read group. A hardware sentinel such as 239 or
    32767 only means that one sensor reports nothing: that field becomes
    unknown instead of keeping a value that no longer tracks the room, and the
    rest of the group stays healthy.
    """

    values: dict[str, Any] = {}
    for name, source in due.items():
        if source.component not in updated:
            continue
        value = getattr(getattr(device, source.component), source.attribute)
        if value is None or (isinstance(value, float) and not math.isfinite(value)):
            job.record_error((source.group,), f"Invalid {name} readback: {value!r}")
            continue
        if name in PLAUSIBLE_RANGES:
            if not is_plausible(name, int(value)):
                _LOGGER.debug("Sensor %s reports no value (raw %r)", name, value)
                values[name] = None
                job.missing_sensor_values.add(name)
                continue
            job.sensor_values.add(name)
        if name == "rf_signal_strength" and value >= 0x8000:
            value -= 0x10000
        if name == "device_fault":
            values[name] = None if value == 0xFFFF else bool(value)
        elif name == "value_error_status":
            values[name] = bool(value) if value in (0, 1) else None
        else:
            values[name] = bool(value) if name in _FLAG_FIELDS else value
        if values[name] is not None and source.component not in _BRIDGE_RF_COMPONENTS:
            job.value_groups.add(source.group)
    for result_key, temperature_key, humidity_key in (
        ("absolute_humidity_extract_air", "extract_air_temperature", "humidity_extract_air"),
        ("absolute_humidity_supply_air", "supply_air_temperature", "humidity_supply_air"),
    ):
        if temperature_key in due or humidity_key in due:
            values[result_key] = absolute_humidity(
                values.get(temperature_key), values.get(humidity_key)
            )
    return values


def _updated_read_health(
    room: RoomConfig,
    previous_state: RoomState,
    refresh_plan: RefreshPlan,
    job: _ReadJob,
) -> tuple[tuple[tuple[str, ReadHealth], ...], frozenset[str]]:
    """Update health only for expected groups selected by this read plan.

    Also returns the groups whose health this read recorded.
    """

    group_health = dict(previous_state.group_read_health)
    recorded: set[str] = set()
    now = dt_util.utcnow()
    for group_key in refresh_plan.read_groups():
        if not room.supports_any(READ_GROUP_ENTITY_KEYS[group_key]):
            continue

        error = job.errors.get(group_key)
        if error is not None:
            group_health[group_key] = previous_state.read_health_for(group_key).failed(
                now, error, timed_out=group_key in job.timeouts
            )
            recorded.add(group_key)
        elif group_key not in job.skipped_groups:
            group_health[group_key] = ReadHealth(
                last_attempt=now, last_successful_read=now, last_success_monotonic=time.monotonic()
            )
            recorded.add(group_key)
    return tuple(sorted(group_health.items())), frozenset(recorded)
