"""Coordinate serialized polling and writes for a Meltem gateway.

The coordinator keeps gateway access strictly single-file: one read/write job
at a time, no concurrency, and one shared Modbus client. Instead of full-state
polls it schedules small refresh jobs per room and per data group.
"""

from __future__ import annotations

import asyncio
import logging
import math
import operator
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .const import (
    AIRFLOW_STALE_AFTER_SECONDS,
    CONTROL_SETTINGS_REFRESH_SECONDS,
    DEFAULT_SCAN_SLAVE_END,
    DEFAULT_SCAN_SLAVE_START,
    DIRECTION_SUPPLY,
    DOMAIN,
    FILTER_REFRESH_SECONDS,
    FLOW_REFRESH_SECONDS,
    LEVEL_SOURCE_MEASURED,
    LEVEL_SOURCE_PENDING,
    LEVEL_SOURCE_TARGET,
    LEVEL_WRITE_FALLBACK_BALANCED,
    LEVEL_WRITE_FALLBACK_UNKNOWN_MODE,
    OPERATING_HOURS_REFRESH_SECONDS,
    OPERATION_MODE_MANUAL,
    OPERATION_MODE_OFF,
    OPERATION_MODE_UNBALANCED,
    POST_WRITE_REFRESH_INTERVAL_SECONDS,
    POST_WRITE_REFRESH_RETRIES,
    PRESET_MODE_EXTRACT_ONLY,
    PRESET_MODE_INACTIVE,
    PRESET_MODE_INTENSIVE,
    PRESET_MODE_SUPPLY_ONLY,
    READ_FAILURE_THRESHOLD,
    READ_GROUP_ENTITY_KEYS,
    SENSOR_OPERATION_MODES,
    STATUS_REFRESH_SECONDS,
    TARGET_OPTIMISTIC_SECONDS,
    TEMPERATURE_REFRESH_SECONDS,
    WRITE_CONFIRMATION_TIMEOUT_SECONDS,
    WRITE_HEALTH_RETENTION_SECONDS,
    WRITE_SETTLE_SECONDS,
)
from .modbus_client import MeltemModbusClient
from .modbus_helpers import MeltemModbusError
from .models import (
    EMPTY_ROOM_STATE,
    ReadHealth,
    RefreshPlan,
    RoomConfig,
    RoomState,
    WriteConfirmation,
    WriteValue,
)

_LOGGER = logging.getLogger(__name__)
async_sleep = asyncio.sleep

FULL_REFRESH_PLAN = RefreshPlan()
AIRFLOW_REFRESH_PLAN = RefreshPlan.only(refresh_airflow=True)
CONTROL_SETTINGS_REFRESH_PLAN = RefreshPlan.only(refresh_control_settings=True)

TRANSPORT_BACKOFF_AFTER_FAILURES = 3
TRANSPORT_BACKOFF_START_SECONDS = 5.0
TRANSPORT_BACKOFF_MAX_SECONDS = 60.0
ROOM_UNAVAILABLE_AFTER_FAILURES = 3
# The slowest job runs hourly, but the airflow job polls every 10 s, so a unit
# that answers nothing for this long is genuinely silent.
ROOM_SILENT_AFTER_SECONDS = 120.0
# Every unanswered read costs several timeouts, so silent units are polled
# rarely to keep the shared bus free for the units that still respond.
SILENT_ROOM_POLL_SECONDS = 60
PRESET_OPTIMISTIC_SECONDS = 15.0
# Rounding between m3/h and the raw 0..200 register costs at most 1 m3/h.
LEVEL_CONFIRM_TOLERANCE = 2
# Percent-to-m3/h rounding can leave two meant-to-be-equal directions one step apart.
BALANCED_LEVEL_TOLERANCE = 1
# Fallback wake-up for the degenerate case of a gateway without any poll job.
IDLE_TICK_SECONDS = 60.0


@dataclass(frozen=True, slots=True)
class JobGroup:
    """One refresh group: which registers it covers and how often it runs."""

    key: str
    interval_seconds: int
    refresh_plan: RefreshPlan

    @property
    def entity_keys(self) -> frozenset[str]:
        """Return the entities whose values this job refreshes."""

        return frozenset().union(
            *(READ_GROUP_ENTITY_KEYS[group] for group in self.refresh_plan.read_groups())
        )


JOB_GROUPS: tuple[JobGroup, ...] = (
    JobGroup("flow", FLOW_REFRESH_SECONDS, AIRFLOW_REFRESH_PLAN),
    JobGroup("status", STATUS_REFRESH_SECONDS, RefreshPlan.only(refresh_status=True)),
    JobGroup(
        "temperature",
        TEMPERATURE_REFRESH_SECONDS,
        RefreshPlan.only(refresh_temperatures=True, refresh_environment=True),
    ),
    JobGroup(
        "filter",
        FILTER_REFRESH_SECONDS,
        RefreshPlan.only(refresh_filter_change_due=True, refresh_filter_days=True),
    ),
    JobGroup(
        "hours",
        OPERATING_HOURS_REFRESH_SECONDS,
        RefreshPlan.only(refresh_operating_hours=True),
    ),
    JobGroup(
        "control_settings",
        CONTROL_SETTINGS_REFRESH_SECONDS,
        CONTROL_SETTINGS_REFRESH_PLAN,
    ),
)

READ_GROUP_INTERVAL_SECONDS: dict[str, int] = {
    group_key: job_group.interval_seconds
    for job_group in JOB_GROUPS
    for group_key in job_group.refresh_plan.read_groups()
}

# Readback for each register group a write can target.
_READBACK_PLANS: dict[str, RefreshPlan] = {
    "flow_control": AIRFLOW_REFRESH_PLAN,
    "control_settings": CONTROL_SETTINGS_REFRESH_PLAN,
}


@dataclass(slots=True)
class PollJob:
    """One scheduled read job for one room and one refresh group."""

    key: str
    room_key: str
    refresh_plan: RefreshPlan
    interval_seconds: int
    next_due: float


class _OptimisticOverlay[T]:
    """Pending write shown until the gateway confirms it or the window expires.

    Writes settle slowly, so without this the UI would jump back to the old
    value for a few seconds after every user action.
    """

    def __init__(
        self,
        ttl_seconds: float,
        on_change: Callable[[], None],
        matches: Callable[[T, T], bool] = operator.eq,
    ) -> None:
        self._ttl_seconds = ttl_seconds
        self._on_change = on_change
        self._matches = matches
        self._pending: dict[str, tuple[T, float]] = {}

    def set(self, room_key: str, value: T) -> None:
        self._pending[room_key] = (value, time.monotonic() + self._ttl_seconds)
        self._on_change()

    def clear(self, room_key: str) -> None:
        if self._pending.pop(room_key, None) is not None:
            self._on_change()

    def get(self, room_key: str, confirmed: T | None) -> T | None:
        """Return the pending value, or ``None`` once it is confirmed or stale."""

        pending = self._pending.get(room_key)
        if pending is None:
            return None

        value, expires_at = pending
        if time.monotonic() >= expires_at or (
            confirmed is not None and self._matches(confirmed, value)
        ):
            del self._pending[room_key]
            return None
        return value


def _room_supports_any(room: RoomConfig, entity_keys: frozenset[str]) -> bool:
    """Return whether the room has any of these entities; no key list means all."""

    supported = room.supported_entity_keys
    return not supported or bool(entity_keys & supported)


def _read_too_old(group_key: str, last_successful_read: datetime) -> bool:
    """Return whether a group's last successful read is past its stale limit."""

    stale_after = (
        AIRFLOW_STALE_AFTER_SECONDS
        if group_key == "flow"
        else READ_GROUP_INTERVAL_SECONDS[group_key] * 3
    )
    return (dt_util.utcnow() - last_successful_read).total_seconds() > stale_after


def _levels_reached(
    confirmed: tuple[int | None, int | None], expected: tuple[int, int]
) -> bool:
    """Return whether both directions reached their pending target."""

    return all(
        actual is not None and abs(actual - target) <= LEVEL_CONFIRM_TOLERANCE
        for actual, target in zip(confirmed, expected)
    )


def _levels_balanced(level: int, other: int) -> bool:
    """Return whether two direction levels should run as one balanced level."""

    if level == other:
        return True
    return level > 0 and other > 0 and abs(level - other) <= BALANCED_LEVEL_TOLERANCE


def _first_known(*values: int | None) -> int | None:
    return next((value for value in values if value is not None), None)


class MeltemDataUpdateCoordinator(DataUpdateCoordinator[dict[str, RoomState]]):
    """Coordinate polling and writes for all configured rooms."""

    def __init__(
        self,
        hass: HomeAssistant,
        *,
        config_entry: ConfigEntry,
        client: MeltemModbusClient,
        rooms: list[RoomConfig],
        max_requests_per_second: float,
    ) -> None:
        self.client = client
        self.rooms = rooms
        self._rooms_by_key = {room.key: room for room in rooms}
        self._tick_seconds = 1.0 / max(0.1, max_requests_per_second)
        self._gateway_lock = asyncio.Lock()
        self._last_job_error: MeltemModbusError | None = None
        self._consecutive_transport_failures = 0
        self._backoff_seconds: float | None = None
        self._room_failures: dict[str, int] = {}
        self._write_confirmations: dict[str, dict[str, WriteConfirmation]] = {}
        self._optimistic_presets = _OptimisticOverlay[str](
            PRESET_OPTIMISTIC_SECONDS, self.async_update_listeners
        )
        self._optimistic_intensive = _OptimisticOverlay[bool](
            PRESET_OPTIMISTIC_SECONDS, self.async_update_listeners, matches=operator.is_
        )
        self._optimistic_levels = _OptimisticOverlay[tuple[int, int]](
            TARGET_OPTIMISTIC_SECONDS, self.async_update_listeners, matches=_levels_reached
        )
        # A write under one of these keys shows its value until the readback confirms it.
        self._overlays: dict[str, _OptimisticOverlay[Any]] = {
            "airflow_levels": self._optimistic_levels,
            "preset_mode": self._optimistic_presets,
            "intensive": self._optimistic_intensive,
        }
        self._level_locks = {room.key: asyncio.Lock() for room in rooms}
        self._level_fallbacks: dict[str, tuple[str, datetime]] = {}
        self._started_at = time.monotonic()
        self._last_read_started: float | None = None
        # Jobs are precomputed once and then executed in a due-time round robin.
        self._jobs = self._build_jobs()

        super().__init__(
            hass,
            _LOGGER,
            config_entry=config_entry,
            name="Meltem Modbus",
            update_interval=timedelta(seconds=self._tick_seconds),
        )

    @property
    def safe_data(self) -> dict[str, RoomState]:
        """Return the current room state map, or an empty dict before the first poll."""
        return self.data if isinstance(self.data, dict) else {}

    @property
    def state_room_count(self) -> int:
        """Return how many rooms currently contain at least one state value."""

        return sum(self._room_state_has_data(state) for state in self.safe_data.values())

    @property
    def last_job_error(self) -> MeltemModbusError | None:
        """Return the last scheduler job error, if any."""
        return self._last_job_error

    @property
    def gateway_device_id(self) -> str | None:
        """Return the registry id of the gateway device the units hang off."""

        entry_id = self.config_entry.entry_id
        device = dr.async_get(self.hass).async_get_device_by_identifier(
            (DOMAIN, entry_id), entry_id
        )
        return device.id if device is not None else None

    def room_available(self, room_key: str) -> bool:
        """Return whether one room still delivers usable data.

        A single unreachable unit must not make the other units look healthy
        while showing frozen values, so availability is tracked per room.
        """

        if self._room_failures.get(room_key, 0) >= ROOM_UNAVAILABLE_AFTER_FAILURES:
            return False

        room = self._rooms_by_key.get(room_key)
        if room is not None:
            silent_for = self.client.seconds_since_successful_read(room.slave)
            if silent_for is not None and silent_for > ROOM_SILENT_AFTER_SECONDS:
                return False

        state = self.safe_data.get(room_key)
        if state is None:
            return False
        return self._room_state_has_data(state)

    def optimistic_preset_mode(self, room_key: str) -> str | None:
        """Return the pending preset selection while the gateway confirms it.

        The overlay is shared so the fan and the select entity never disagree.
        """
        state = self.safe_data.get(room_key)
        confirmed = state.preset_mode if state else None
        return self._optimistic_presets.get(room_key, confirmed)

    def optimistic_intensive(self, room_key: str) -> bool | None:
        """Return the pending intensive override while the gateway confirms it."""

        state = self.safe_data.get(room_key)
        return self._optimistic_intensive.get(
            room_key, state.intensive_active if state else None
        )

    def effective_levels(self, room_key: str) -> tuple[int | None, int | None]:
        """Return the supply/extract targets a fan entity should act on.

        Falls back to the pending write while the gateway confirms it, so the
        two directional fans never rebuild each other from a stale cache.
        """

        return self._resolve_levels(room_key)[0]

    def level_source(self, room_key: str) -> str | None:
        """Return whether the effective levels are pending, targets, or measurements."""

        return self._resolve_levels(room_key)[1]

    def _resolve_levels(
        self, room_key: str
    ) -> tuple[tuple[int | None, int | None], str | None]:
        """Return the effective supply/extract pair and where it comes from."""

        state = self.safe_data.get(room_key)
        confirmed: tuple[int | None, int | None] | None = None
        source: str | None = None
        if state is not None:
            airflow_is_fresh = self.read_group_fresh(room_key, "flow")
            mode_is_fresh = self.read_group_fresh(room_key, "flow_control")
            if not mode_is_fresh or state.operation_mode is None:
                if airflow_is_fresh:
                    confirmed = (state.supply_air_flow, state.extract_air_flow)
                    source = LEVEL_SOURCE_MEASURED
            else:
                confirmed = self._confirmed_levels(
                    state,
                    airflow_is_fresh=airflow_is_fresh,
                )
                targets = self._confirmed_levels(state, airflow_is_fresh=False)
                source = (
                    LEVEL_SOURCE_TARGET
                    if confirmed == targets
                    else LEVEL_SOURCE_MEASURED
                )
            if confirmed == (None, None):
                confirmed, source = None, None

        pending = self._optimistic_levels.get(room_key, confirmed)
        if pending is not None:
            return pending, LEVEL_SOURCE_PENDING
        return (confirmed if confirmed is not None else (None, None)), source

    def level_write_fallback(self, room_key: str) -> str | None:
        """Return the fallback used by the last fan write until a readback follows."""

        fallback = self._level_fallbacks.get(room_key)
        if fallback is None:
            return None
        marker, written_at = fallback
        state = self.safe_data.get(room_key)
        if state is not None:
            health = state.read_health_for("flow_control")
            if (
                health.last_error is None
                and health.last_successful_read is not None
                and health.last_successful_read >= written_at
            ):
                del self._level_fallbacks[room_key]
                return None
        return marker

    def _record_level_fallback(self, room_key: str, marker: str) -> None:
        self._level_fallbacks[room_key] = (marker, dt_util.utcnow())
        self.async_update_listeners()

    @staticmethod
    def _confirmed_levels(
        state: RoomState,
        *,
        airflow_is_fresh: bool,
    ) -> tuple[int | None, int | None]:
        """Split the room state into a supply/extract target pair."""

        if state.operation_mode == OPERATION_MODE_OFF:
            return 0, 0

        if state.operation_mode == OPERATION_MODE_UNBALANCED:
            supply = state.target_level
            if supply is None and airflow_is_fresh:
                supply = state.supply_air_flow
            extract = (
                state.extract_target_level
                if state.extract_target_level is not None
                else state.extract_air_flow if airflow_is_fresh else None
            )
            return supply, extract

        if state.operation_mode in SENSOR_OPERATION_MODES:
            # The unit picks the airflow itself and exposes no target register.
            if airflow_is_fresh:
                return state.supply_air_flow, state.extract_air_flow
            return None, None

        # Balanced modes drive both fans from a single register.
        common = state.target_level
        if common is None and airflow_is_fresh:
            common = state.supply_air_flow
        if common is None and airflow_is_fresh:
            common = state.extract_air_flow
        return common, common

    def read_group_fresh(self, room_key: str, group_key: str) -> bool:
        """Return whether the group's most recent attempt succeeded recently."""

        state = self.safe_data.get(room_key)
        if state is None:
            return False
        health = state.read_health_for(group_key)
        if (
            health.last_attempt is None
            or health.last_successful_read != health.last_attempt
            or health.consecutive_failures != 0
            or health.last_error is not None
        ):
            return False
        return not _read_too_old(group_key, health.last_successful_read)

    async def _async_update_data(self) -> dict[str, RoomState]:
        try:
            async with self._gateway_lock:
                if not self.safe_data:
                    self._last_read_started = time.monotonic()
                    states = await self._read_all_rooms_full()
                    self._on_transport_success()
                    self._schedule_next_tick()
                    return states

                now = time.monotonic()
                job = (
                    None
                    if self._read_spacing_remaining(now) > 0
                    else self._select_due_job(now)
                )
                if job is None:
                    self._schedule_next_tick()
                    return self.data

                # Move the job forward before running it so a failing read
                # cannot get stuck at the front of the queue forever.
                job.next_due = now + self._job_interval(job)
                self._last_read_started = now
                self._last_job_error = None
                updated_data = await self._read_one_job(self.data, job)
                # _read_one_job swallows transport errors to keep cached state,
                # so success has to be derived from the recorded job error.
                if self._last_job_error is None:
                    self._on_transport_success()
                else:
                    self._on_transport_failure()
                self._confirm_pending_writes(updated_data)
                self._schedule_next_tick()
                return updated_data
        except MeltemModbusError as err:
            # Only the initial full read gets here; jobs record errors per room.
            self._on_transport_failure()
            raise UpdateFailed(str(err)) from err

    def _read_spacing_remaining(self, now: float) -> float:
        """Return how long the request-rate cap still blocks the next read."""

        if self._last_read_started is None:
            return 0.0
        return self._last_read_started + self._tick_seconds - now

    def _job_interval(self, job: PollJob) -> float:
        """Return the job interval, stretched while its unit stays silent."""

        if self._room_silent(job.room_key):
            return max(job.interval_seconds, SILENT_ROOM_POLL_SECONDS)
        return job.interval_seconds

    def _room_silent(self, room_key: str) -> bool:
        """Return whether a unit has not answered any read for a long time."""

        room = self._rooms_by_key[room_key]
        silent_for = self.client.seconds_since_successful_read(room.slave)
        if silent_for is None:
            # Never answered since startup.
            silent_for = time.monotonic() - self._started_at
        return silent_for > ROOM_SILENT_AFTER_SECONDS

    def _schedule_next_tick(self) -> None:
        """Sleep until the next job is due instead of waking up on every tick.

        The configured request rate still caps how closely two jobs can follow
        each other.
        """

        if self._backoff_seconds is not None:
            return

        if not self._jobs:
            self.update_interval = timedelta(seconds=IDLE_TICK_SECONDS)
            return

        earliest_due = min(job.next_due for job in self._jobs)
        seconds = max(self._tick_seconds, earliest_due - time.monotonic())
        # HA schedules from int(loop.time()), which would fire up to a second early.
        loop_time = self.hass.loop.time()
        self.update_interval = timedelta(
            seconds=seconds + loop_time - math.floor(loop_time)
        )

    def _on_transport_success(self) -> None:
        """Reset failure tracking and any active polling backoff."""

        self._consecutive_transport_failures = 0
        if self._backoff_seconds is None:
            return
        self._backoff_seconds = None
        self._schedule_next_tick()
        _LOGGER.info("Meltem gateway reachable again, resuming normal polling rate")

    def _on_transport_failure(self) -> None:
        self._consecutive_transport_failures += 1
        self._apply_transport_backoff()

    def _apply_transport_backoff(self) -> None:
        """Slow down polling while the gateway keeps failing.

        Without this the scheduler would retry every tick forever, and each
        retry costs a full reconnect cycle on the serial port.
        """

        if self._consecutive_transport_failures < TRANSPORT_BACKOFF_AFTER_FAILURES:
            return

        exponent = self._consecutive_transport_failures - TRANSPORT_BACKOFF_AFTER_FAILURES
        seconds = min(
            TRANSPORT_BACKOFF_MAX_SECONDS,
            TRANSPORT_BACKOFF_START_SECONDS * (2**exponent),
        )
        if seconds == self._backoff_seconds:
            return

        self._backoff_seconds = seconds
        self.update_interval = timedelta(seconds=seconds)
        _LOGGER.warning(
            "Backing off Meltem gateway polling to %.0f s after %s consecutive transport failures",
            seconds,
            self._consecutive_transport_failures,
        )

    async def async_set_level(self, room_key: str, level: int) -> None:
        """Write a new target level for one room.

        No confirmation poll is forced here: the fast airflow job picks up the
        readback within a few seconds and an extra read only adds bus load.
        """

        room = self._rooms_by_key[room_key]
        self._optimistic_presets.clear(room_key)
        await self._async_write_with_confirmation(
            room,
            "airflow_levels",
            (level, level),
            self.client.write_level,
            room,
            level,
        )

    async def async_set_unbalanced_levels(
        self, room_key: str, supply_level: int, extract_level: int
    ) -> None:
        """Write separate supply and extract levels for one room.

        No confirmation poll is forced here: the fast airflow job picks up the
        readback within a few seconds and an extra read only adds bus load.
        """

        room = self._rooms_by_key[room_key]
        self._optimistic_presets.clear(room_key)
        await self._async_write_with_confirmation(
            room,
            "airflow_levels",
            (supply_level, extract_level),
            self.client.write_unbalanced_levels,
            room,
            supply_level,
            extract_level,
        )

    async def async_set_direction_level(
        self, room_key: str, direction: str, level: int
    ) -> None:
        """Set one airflow direction and keep the other one where it is.

        Resolving the opposite direction and writing share one per-unit lock,
        so quick successive or concurrent fan commands build on each other.
        """

        room = self._rooms_by_key[room_key]
        async with self._level_locks[room_key]:
            (supply, extract), source = self._resolve_levels(room_key)
            other = extract if direction == DIRECTION_SUPPLY else supply
            if other is None:
                if level == 0:
                    # Stopping both directions instead would be a silent surprise.
                    raise HomeAssistantError(
                        translation_domain=DOMAIN,
                        translation_key="opposite_airflow_unknown",
                        translation_placeholders={"unit": room.name},
                    )
                _LOGGER.warning(
                    "Room %s (slave %s): opposite airflow is unknown; setting both "
                    "directions to %s m3/h in balanced manual mode",
                    room.name,
                    room.slave,
                    level,
                )
                await self.async_set_level(room_key, level)
                self._record_level_fallback(room_key, LEVEL_WRITE_FALLBACK_BALANCED)
                return

            operation_mode = self.safe_data.get(
                room_key, EMPTY_ROOM_STATE
            ).operation_mode
            if (
                source == LEVEL_SOURCE_MEASURED
                and supply is not None
                and extract is not None
                and abs(supply - extract) <= LEVEL_CONFIRM_TOLERANCE
            ):
                # Measured airflow jitters around one balanced target.
                other = round((supply + extract) / 2)

            # A stopped unit restarts both directions, never single-sided (HW-5).
            starting_from_off = operation_mode == OPERATION_MODE_OFF
            # Under sensor control both fans only report fluctuating
            # measurements, so writing one direction would pin the other to a
            # sampled value. Turning a direction off still stays unbalanced.
            leaving_sensor_control = (
                level > 0 and operation_mode in SENSOR_OPERATION_MODES
            )
            # Equal levels written as unbalanced would leave the unit in a mode
            # it cannot return from on its own.
            if (
                _levels_balanced(level, other)
                or starting_from_off
                or leaving_sensor_control
            ):
                await self.async_set_level(room_key, level)
            else:
                supply_level, extract_level = (
                    (level, other) if direction == DIRECTION_SUPPLY else (other, level)
                )
                await self.async_set_unbalanced_levels(
                    room_key, supply_level, extract_level
                )

            if operation_mode is None:
                _LOGGER.warning(
                    "Room %s (slave %s): operating mode was unknown; the fan "
                    "command may have overridden it",
                    room.name,
                    room.slave,
                )
                self._record_level_fallback(
                    room_key, LEVEL_WRITE_FALLBACK_UNKNOWN_MODE
                )

    async def async_set_operation_mode(self, room_key: str, operation_mode: str) -> None:
        """Write a new operating mode for one room and refresh afterwards."""

        room = self._rooms_by_key[room_key]
        state = self.safe_data.get(room.key, EMPTY_ROOM_STATE)
        supply, extract = self.effective_levels(room_key)
        balanced_level = _first_known(
            supply,
            state.target_level,
            state.supply_air_flow,
            state.extract_air_flow,
        )
        extract_level = _first_known(
            extract,
            state.extract_target_level,
            state.extract_air_flow,
            balanced_level,
        )
        if balanced_level is None and operation_mode in (
            OPERATION_MODE_MANUAL,
            OPERATION_MODE_UNBALANCED,
        ):
            # Guessing 0 here would stop the unit instead of changing its mode.
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="airflow_unknown",
                translation_placeholders={"unit": room.name},
            )

        self._optimistic_levels.clear(room_key)
        await self._async_write_with_confirmation(
            room,
            "operation_mode",
            operation_mode,
            self.client.write_operating_mode,
            room,
            operation_mode,
            int(balanced_level or 0),
            int(extract_level or 0),
            refresh_attempts=1,
        )

    async def async_set_preset_mode(self, room_key: str, preset_mode: str) -> None:
        """Write one app-style preset mode and refresh afterwards."""

        room = self._rooms_by_key[room_key]
        self._optimistic_levels.clear(room_key)
        await self._async_write_with_confirmation(
            room,
            "preset_mode",
            preset_mode,
            self.client.write_preset_mode,
            room,
            preset_mode,
            refresh_attempts=2,
        )

    async def async_clear_preset_mode(self, room_key: str) -> None:
        """Leave the quick-mode shortcut and keep the current airflow behavior."""

        state = self.safe_data.get(room_key, EMPTY_ROOM_STATE)
        effective_preset_mode = self.optimistic_preset_mode(room_key) or state.preset_mode
        if effective_preset_mode in (None, PRESET_MODE_INACTIVE):
            self._optimistic_presets.clear(room_key)
            return

        operation_mode = (
            OPERATION_MODE_UNBALANCED
            if effective_preset_mode in (PRESET_MODE_EXTRACT_ONLY, PRESET_MODE_SUPPLY_ONLY)
            else OPERATION_MODE_MANUAL
        )
        await self.async_set_operation_mode(room_key, operation_mode)
        self._optimistic_presets.clear(room_key)

    async def async_activate_intensive(self, room_key: str) -> None:
        """Start temporary intensive ventilation without changing the base preset."""

        room = self._rooms_by_key[room_key]
        await self._async_write_with_confirmation(
            room,
            "intensive",
            True,
            self.client.write_preset_mode,
            room,
            PRESET_MODE_INTENSIVE,
            refresh_attempts=2,
        )

    async def async_deactivate_intensive(self, room_key: str) -> None:
        """Cancel a running intensive override and keep the base preset."""

        room = self._rooms_by_key[room_key]
        await self._async_write_with_confirmation(
            room,
            "intensive",
            False,
            self.client.clear_intensive,
            room,
            refresh_attempts=2,
        )

    async def async_set_control_setting(
        self,
        room_key: str,
        setting_key: str,
        value: int,
    ) -> None:
        """Write one humidity/CO2 control setting and refresh it afterwards."""

        room = self._rooms_by_key[room_key]
        await self._async_write_with_confirmation(
            room,
            f"control_setting:{setting_key}",
            value,
            self.client.write_control_setting,
            room,
            setting_key,
            value,
            refresh_attempts=1,
            use_write_result=True,
        )

    async def _async_write_with_confirmation(
        self,
        room: RoomConfig,
        write_key: str,
        expected_value: WriteValue,
        write_method: Callable[..., Awaitable[object]],
        *write_args: object,
        refresh_attempts: int = 0,
        use_write_result: bool = False,
    ) -> None:
        """Write one setting, preserve confirmed state, and verify by readback.

        Without ``refresh_attempts`` the regular poll jobs confirm the write.
        """

        readback_plan = _READBACK_PLANS[self._write_group(write_key)]
        overlay = self._overlays.get(write_key)
        async with self._gateway_lock:
            try:
                write_result = await write_method(*write_args)
            except Exception as err:
                # A failed write makes any earlier pending value doubtful.
                if overlay is not None:
                    overlay.clear(room.key)
                self._record_write_pending(room.key, write_key, expected_value)
                self._set_write_confirmation(
                    room.key,
                    write_key,
                    "failed",
                    error=str(err),
                )
                await self._async_refresh_room_after_write(
                    room,
                    refresh_plan=readback_plan,
                )
                raise HomeAssistantError(
                    translation_domain=DOMAIN,
                    translation_key="write_failed",
                    translation_placeholders={"unit": room.name, "error": str(err)},
                ) from err

            confirmed_expected = (
                write_result
                if use_write_result and isinstance(write_result, (str, int, bool))
                else expected_value
            )
            self._record_write_pending(room.key, write_key, confirmed_expected)
            if overlay is not None:
                overlay.set(room.key, expected_value)

            if not refresh_attempts:
                return

            await async_sleep(WRITE_SETTLE_SECONDS)
            await self._async_refresh_room_after_write(
                room,
                refresh_plan=readback_plan,
                min_refresh_attempts=refresh_attempts,
            )
            self._mark_write_unconfirmed(
                room.key,
                write_key,
                "No matching device readback was received",
            )

    def update_request_rate(self, max_requests_per_second: float) -> None:
        """Apply a new scheduler request rate without reloading the integration."""

        self._tick_seconds = 1.0 / max(0.1, max_requests_per_second)
        if self._backoff_seconds is None:
            self._schedule_next_tick()

    async def async_discover_gateway_units(self) -> list[int]:
        """Discover configured units using the active gateway client."""

        async with self._gateway_lock:
            return await self.client.discover_gateway_units(
                DEFAULT_SCAN_SLAVE_START,
                DEFAULT_SCAN_SLAVE_END,
            )

    async def async_probe_slave_details(
        self,
        slave: int,
    ) -> tuple[str, str | None, list[str]]:
        """Probe one unit using the active gateway client."""

        async with self._gateway_lock:
            return await self.client.probe_slave_details(slave)

    async def _async_refresh_room_after_write(
        self,
        room: RoomConfig,
        *,
        refresh_plan: RefreshPlan = AIRFLOW_REFRESH_PLAN,
        min_refresh_attempts: int = 1,
    ) -> None:
        """Refresh one room's affected group after a write settles."""

        for attempt in range(POST_WRITE_REFRESH_RETRIES + 1):
            previous_state = self.safe_data.get(room.key, EMPTY_ROOM_STATE)
            self._last_read_started = time.monotonic()
            failed = False
            try:
                refreshed_room = await self.client.read_room_state(
                    room,
                    previous_state,
                    refresh_plan,
                )
            except MeltemModbusError as err:
                _LOGGER.warning(
                    "Failed to refresh room %s immediately after write: %s",
                    room.key,
                    err,
                )
                refreshed_room = self._with_read_group_failures(
                    previous_state,
                    room,
                    refresh_plan,
                    err,
                )
                failed = True

            updated_states = {**self.safe_data, room.key: refreshed_room}
            if refreshed_room != previous_state:
                self.async_set_updated_data(updated_states)
            self._confirm_pending_writes(updated_states)

            if failed or attempt + 1 >= max(1, min_refresh_attempts):
                return

            if attempt < POST_WRITE_REFRESH_RETRIES:
                await async_sleep(POST_WRITE_REFRESH_INTERVAL_SECONDS)

    async def _read_all_rooms_full(self) -> dict[str, RoomState]:
        """Read a full initial state for all configured rooms."""

        states: dict[str, RoomState] = {}
        successful_reads = 0
        last_error: MeltemModbusError | None = None
        for room in self.rooms:
            try:
                states[room.key] = await self.client.read_room_state(
                    room,
                    EMPTY_ROOM_STATE,
                    FULL_REFRESH_PLAN,
                )
                successful_reads += 1
                self._room_failures.pop(room.key, None)
            except MeltemModbusError as err:
                _LOGGER.warning("Failed to read room %s during startup: %s", room.key, err)
                states[room.key] = self._with_read_group_failures(
                    EMPTY_ROOM_STATE,
                    room,
                    FULL_REFRESH_PLAN,
                    err,
                )
                last_error = err
                self._room_failures[room.key] = self._room_failures.get(room.key, 0) + 1
                # Give the serial port time to settle before the next room.
                await async_sleep(0.5)

        if successful_reads == 0 and last_error is not None:
            raise last_error

        self._prioritize_empty_rooms(states)
        return states

    def _prioritize_empty_rooms(self, states: dict[str, RoomState]) -> None:
        """Pull all jobs for rooms with no startup data to the front of the queue."""

        now = time.monotonic()
        for room_key, state in states.items():
            if not self._room_state_has_data(state):
                for job in self._jobs:
                    if job.room_key == room_key:
                        job.next_due = now - 1.0

    @staticmethod
    def _room_state_has_data(state: RoomState) -> bool:
        """Return whether a room state contains any meaningful value yet."""

        return any(
            getattr(state, field_name) is not None
            for field_name in RoomState.__dataclass_fields__
            if field_name != "group_read_health"
        )

    @staticmethod
    def _with_read_group_failures(
        state: RoomState,
        room: RoomConfig,
        refresh_plan: RefreshPlan,
        error: Exception,
    ) -> RoomState:
        """Record a transport failure for only the expected groups in a plan."""

        failed_state = state
        attempted_at = dt_util.utcnow()
        for group_key in refresh_plan.read_groups():
            if not _room_supports_any(room, READ_GROUP_ENTITY_KEYS[group_key]):
                continue
            previous_health = failed_state.read_health_for(group_key)
            failed_state = failed_state.with_read_health(
                group_key,
                ReadHealth(
                    last_attempt=attempted_at,
                    last_successful_read=previous_health.last_successful_read,
                    consecutive_failures=min(
                        previous_health.consecutive_failures + 1,
                        READ_FAILURE_THRESHOLD,
                    ),
                    last_error=str(error),
                ),
            )
        return failed_state

    @staticmethod
    def read_group_for_entity(entity_key: str) -> str | None:
        """Return the read-health group that owns one entity's value."""

        return next(
            (
                group_key
                for group_key, entity_keys in READ_GROUP_ENTITY_KEYS.items()
                if entity_key in entity_keys
            ),
            None,
        )

    def read_group_available(self, room_key: str, group_key: str) -> bool:
        """Return whether a group's values are recent and have recovered."""

        state = self.safe_data.get(room_key)
        if state is None:
            return False
        health = state.read_health_for(group_key)
        if (
            health.last_successful_read is None
            or health.consecutive_failures >= READ_FAILURE_THRESHOLD
        ):
            return False
        return not _read_too_old(group_key, health.last_successful_read)

    def read_group_stale(self, room_key: str, group_key: str) -> bool | None:
        """Return whether a group's last successful read is stale."""

        state = self.safe_data.get(room_key)
        if state is None:
            return None
        health = state.read_health_for(group_key)
        if health.last_attempt is None:
            return None
        if health.consecutive_failures >= READ_FAILURE_THRESHOLD:
            return True
        if health.last_successful_read is None:
            return None
        return _read_too_old(group_key, health.last_successful_read)

    def data_health_stale(self, room_key: str) -> bool | None:
        """Return whether any read group or write confirmation is unhealthy."""

        state = self.safe_data.get(room_key)
        if state is None:
            return None
        if any(
            self.read_group_stale(room_key, group_key) is True
            for group_key, _health in state.group_read_health
        ):
            return True
        now = dt_util.utcnow()
        if any(
            self._write_confirmation_status(confirmation) in {
                "unconfirmed",
                "mismatch",
                "failed",
            }
            and (now - confirmation.started_at).total_seconds()
            <= WRITE_HEALTH_RETENTION_SECONDS
            for confirmation in self._write_confirmations.get(room_key, {}).values()
        ):
            return True
        if state.group_read_health or self._write_confirmations.get(room_key):
            return False
        return None

    def data_health_attributes(
        self,
        room_key: str,
    ) -> dict[str, object]:
        """Return per-group read health and write outcomes for one room."""

        state = self.safe_data.get(room_key, EMPTY_ROOM_STATE)
        room = self._rooms_by_key[room_key]
        attributes = {}
        for group_key in READ_GROUP_ENTITY_KEYS:
            if not _room_supports_any(room, READ_GROUP_ENTITY_KEYS[group_key]):
                continue
            health = state.read_health_for(group_key)
            attributes[group_key] = {
                "last_attempt": health.last_attempt.isoformat()
                if health.last_attempt
                else None,
                "last_successful_read": health.last_successful_read.isoformat()
                if health.last_successful_read
                else None,
                "consecutive_failures": health.consecutive_failures,
                "last_error": health.last_error,
                "stale": self.read_group_stale(room_key, group_key),
            }
        attributes["writes"] = {
            write_key: {
                "status": self._write_confirmation_status(confirmation),
                "expected_value": confirmation.expected_value,
                "actual_value": confirmation.actual_value,
                "started_at": confirmation.started_at.isoformat(),
                "last_error": confirmation.last_error,
            }
            for write_key, confirmation in self._write_confirmations.get(
                room_key, {}
            ).items()
        }
        return attributes

    @staticmethod
    def _write_confirmation_status(confirmation: WriteConfirmation) -> str:
        """Treat a write that remains pending too long as unconfirmed."""

        if (
            confirmation.status == "pending"
            and (dt_util.utcnow() - confirmation.started_at).total_seconds()
            > WRITE_CONFIRMATION_TIMEOUT_SECONDS
        ):
            return "unconfirmed"
        return confirmation.status

    def _record_write_pending(
        self,
        room_key: str,
        write_key: str,
        expected_value: WriteValue,
    ) -> None:
        """Record a successful write awaiting device readback."""

        self._write_confirmations.setdefault(room_key, {})[write_key] = (
            WriteConfirmation(
                expected_value=expected_value,
                started_at=dt_util.utcnow(),
            )
        )
        self.async_update_listeners()

    def _set_write_confirmation(
        self,
        room_key: str,
        write_key: str,
        status: str,
        *,
        error: str | None = None,
        actual_value: WriteValue | None = None,
    ) -> None:
        """Update one write outcome without changing its expected value."""

        previous = self._write_confirmations.get(room_key, {}).get(write_key)
        if previous is None:
            return
        self._write_confirmations[room_key][write_key] = replace(
            previous,
            status=status,
            actual_value=actual_value,
            last_error=error,
        )
        self.async_update_listeners()

    def _mark_write_unconfirmed(
        self,
        room_key: str,
        write_key: str,
        error: Exception | str,
    ) -> None:
        """Mark a pending write unconfirmed after its readback failed."""

        confirmation = self._write_confirmations.get(room_key, {}).get(write_key)
        if confirmation is not None and confirmation.status == "pending":
            self._set_write_confirmation(
                room_key,
                write_key,
                "unconfirmed",
                error=str(error),
            )

    @staticmethod
    def _write_group(write_key: str) -> str:
        if write_key.startswith("control_setting:"):
            return "control_settings"
        if write_key in {"airflow_levels", "operation_mode", "preset_mode", "intensive"}:
            return "flow_control"
        raise ValueError(f"Unknown write confirmation key: {write_key}")

    @staticmethod
    def _write_readback_value(
        state: RoomState,
        write_key: str,
    ) -> WriteValue | None:
        if write_key == "airflow_levels":
            # Only target registers confirm a write; measured airflow lags behind.
            supply, extract = MeltemDataUpdateCoordinator._confirmed_levels(
                state, airflow_is_fresh=False
            )
            if supply is None or extract is None:
                return None
            return supply, extract
        if write_key == "operation_mode":
            return state.operation_mode
        if write_key == "preset_mode":
            return state.preset_mode
        if write_key == "intensive":
            return state.intensive_active
        if write_key.startswith("control_setting:"):
            return getattr(state, write_key.removeprefix("control_setting:"))
        raise ValueError(f"Unknown write confirmation key: {write_key}")

    @staticmethod
    def _write_values_match(
        write_key: str,
        expected: WriteValue,
        actual: WriteValue,
    ) -> bool:
        if write_key == "airflow_levels":
            return _levels_reached(actual, expected)  # type: ignore[arg-type]
        return actual == expected

    def _confirm_pending_writes(self, states: dict[str, RoomState]) -> None:
        """Compare pending writes with the latest successful group readbacks."""

        changed = False
        for room_key, confirmations in self._write_confirmations.items():
            state = states.get(room_key)
            if state is None:
                continue
            for write_key, confirmation in tuple(confirmations.items()):
                checked = self._checked_confirmation(state, write_key, confirmation)
                if checked is not None:
                    confirmations[write_key] = checked
                    changed = True
        if changed:
            self.async_update_listeners()

    @classmethod
    def _checked_confirmation(
        cls,
        state: RoomState,
        write_key: str,
        confirmation: WriteConfirmation,
    ) -> WriteConfirmation | None:
        """Return the new outcome of one write after a readback, or ``None`` if unchanged."""

        if confirmation.status in {"failed", "confirmed", "unverifiable"}:
            return None
        read_health = state.read_health_for(cls._write_group(write_key))
        if (
            read_health.last_attempt is None
            or read_health.last_attempt < confirmation.started_at
        ):
            return None
        if read_health.last_error is not None:
            if confirmation.status != "pending":
                return None
            return replace(
                confirmation, status="unconfirmed", last_error=read_health.last_error
            )

        if write_key == "intensive" and not cls._read_since(
            state, "intensive", confirmation.started_at
        ):
            # The unit answered the mode read but cannot report intensive (HW-4).
            return replace(confirmation, status="unverifiable", last_error=None)

        actual = cls._write_readback_value(state, write_key)
        if actual is None:
            return None
        status = (
            "confirmed"
            if cls._write_values_match(write_key, confirmation.expected_value, actual)
            else "mismatch"
        )
        if confirmation.status == status and confirmation.actual_value == actual:
            return None
        return replace(confirmation, status=status, actual_value=actual, last_error=None)

    @staticmethod
    def _read_since(state: RoomState, group_key: str, started_at: datetime) -> bool:
        """Return whether a group was read at or after one point in time."""

        last_attempt = state.read_health_for(group_key).last_attempt
        return last_attempt is not None and last_attempt >= started_at

    async def _read_one_job(
        self,
        previous_states: dict[str, RoomState],
        job: PollJob,
    ) -> dict[str, RoomState]:
        """Run one scheduled read job and merge the result into the state map."""

        room = self._rooms_by_key[job.room_key]
        previous_state = previous_states.get(room.key, EMPTY_ROOM_STATE)

        try:
            refreshed_state = await self.client.read_room_state(
                room,
                previous_state,
                job.refresh_plan,
            )
        except MeltemModbusError as err:
            _LOGGER.warning("Failed to read room %s for job %s: %s", room.key, job.key, err)
            self._last_job_error = err
            self._room_failures[room.key] = self._room_failures.get(room.key, 0) + 1
            refreshed_state = self._with_read_group_failures(
                previous_state,
                room,
                job.refresh_plan,
                err,
            )
        else:
            self._last_job_error = None
            self._room_failures.pop(room.key, None)

        if refreshed_state == previous_state:
            return previous_states
        return {**previous_states, room.key: refreshed_state}

    def _select_due_job(self, now: float) -> PollJob | None:
        """Return the job that has been due the longest, if any."""

        return min(
            (job for job in self._jobs if job.next_due <= now),
            key=operator.attrgetter("next_due"),
            default=None,
        )

    def _build_jobs(self) -> list[PollJob]:
        """Build the scheduled job list.

        Each job represents one compact block of related registers. Staggering
        them across time keeps the gateway load smooth instead of bursty.
        """

        now = time.monotonic()
        return [
            job
            for group in JOB_GROUPS
            for job in self._build_group_jobs(group, now)
        ]

    def _build_group_jobs(self, group: JobGroup, now: float) -> list[PollJob]:
        """Create one staggered job per room for one refresh group."""

        rooms = [room for room in self.rooms if self._room_needs_job(room, group)]
        if not rooms:
            return []

        # Spread jobs of one group across their full interval so a group does
        # not fire for every room at the same moment.
        spacing = group.interval_seconds / len(rooms)
        return [
            PollJob(
                key=group.key,
                room_key=room.key,
                refresh_plan=group.refresh_plan,
                interval_seconds=group.interval_seconds,
                next_due=now + (index * spacing),
            )
            for index, room in enumerate(rooms)
        ]

    @staticmethod
    def _room_needs_job(room: RoomConfig, group: JobGroup) -> bool:
        """Check whether a room has any entities covered by a job."""

        return _room_supports_any(room, group.entity_keys)
