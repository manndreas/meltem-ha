"""Coordinate serialized polling and writes for a Meltem gateway.

The coordinator keeps gateway access strictly single-file: one read/write job
at a time, no concurrency, and one shared Modbus client. Instead of full-state
polls it schedules small refresh jobs per room and per data group.

Job planning lives in ``polling.py``, read freshness in ``read_health.py``,
airflow targets in ``levels.py``, pending values in ``overlay.py``, and write
outcomes in ``write_confirmation.py``.
"""

from __future__ import annotations

import asyncio
import logging
import math
import operator
import time
from collections.abc import Awaitable, Callable, Mapping
from datetime import datetime, timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from . import read_health
from .const import (
    CONTROL_LEVEL_RANGES,
    DEFAULT_SCAN_SLAVE_END,
    DEFAULT_SCAN_SLAVE_START,
    DIRECT_OPERATION_MODES,
    DIRECTION_SUPPLY,
    DOMAIN,
    LEVEL_SOURCE_MEASURED,
    LEVEL_SOURCE_PENDING,
    LEVEL_WRITE_FALLBACK_BALANCED,
    LEVEL_WRITE_FALLBACK_UNKNOWN_MODE,
    OPERATION_MODE_INACTIVE,
    OPERATION_MODE_MANUAL,
    OPERATION_MODE_OFF,
    OPERATION_MODE_UNBALANCED,
    POST_WRITE_REFRESH_INTERVAL_SECONDS,
    POST_WRITE_REFRESH_RETRIES,
    PRESET_MODE_EXTRACT_ONLY,
    PRESET_MODE_INACTIVE,
    PRESET_MODE_INTENSIVE,
    PRESET_MODE_SUPPLY_ONLY,
    READ_GROUP_ENTITY_KEYS,
    SENSOR_OPERATION_MODES,
    TARGET_OPTIMISTIC_SECONDS,
    WRITE_SETTLE_SECONDS,
)
from .levels import (
    LEVEL_CONFIRM_TOLERANCE,
    LevelPair,
    first_known,
    levels_balanced,
    levels_reached,
    reported_levels,
)
from .modbus_client import MeltemModbusClient, normalize_control_setting
from .modbus_helpers import MeltemModbusError
from .models import EMPTY_ROOM_STATE, RefreshPlan, RoomConfig, RoomState, WriteValue
from .overlay import OptimisticOverlay
from .polling import (
    AIRFLOW_REFRESH_PLAN,
    CONTROL_SETTINGS_REFRESH_PLAN,
    PollJob,
    build_jobs,
    select_due_job,
)
from .write_confirmation import CONTROL_SETTING_WRITE_PREFIX, WriteConfirmations, write_group

_LOGGER = logging.getLogger(__name__)
async_sleep = asyncio.sleep

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
# Fallback wake-up for the degenerate case of a gateway without any poll job.
IDLE_TICK_SECONDS = 60.0

# Readback for each register group a write can target.
_READBACK_PLANS: dict[str, RefreshPlan] = {
    "flow_control": AIRFLOW_REFRESH_PLAN,
    "control_settings": CONTROL_SETTINGS_REFRESH_PLAN,
}


def _mode_read_since(state: RoomState, written_at: datetime) -> bool:
    """Return whether the mode registers were read successfully after a write."""

    health = state.read_health_for("flow_control")
    return (
        health.last_error is None
        and health.last_successful_read is not None
        and health.last_successful_read >= written_at
    )


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
        self._entry_id = config_entry.entry_id
        self._rooms_by_key = {room.key: room for room in rooms}
        self._tick_seconds = 1.0 / max(0.1, max_requests_per_second)
        self._gateway_lock = asyncio.Lock()
        self._last_job_error: MeltemModbusError | None = None
        self._consecutive_transport_failures = 0
        self._backoff_seconds: float | None = None
        self._room_failures: dict[str, int] = {}
        self._writes = WriteConfirmations()
        self._optimistic_presets = OptimisticOverlay[str, str](
            hass, PRESET_OPTIMISTIC_SECONDS, self.async_update_listeners
        )
        self._optimistic_intensive = OptimisticOverlay[bool, bool](
            hass, PRESET_OPTIMISTIC_SECONDS, self.async_update_listeners, matches=operator.is_
        )
        self._optimistic_levels = OptimisticOverlay[tuple[int, int], LevelPair](
            hass, TARGET_OPTIMISTIC_SECONDS, self.async_update_listeners, matches=levels_reached
        )
        # A write under one of these keys shows its value until the readback confirms it.
        self._overlays: dict[str, OptimisticOverlay[Any, Any]] = {
            "airflow_levels": self._optimistic_levels,
            "preset_mode": self._optimistic_presets,
            "intensive": self._optimistic_intensive,
        }
        self._level_fallbacks: dict[str, tuple[str, datetime]] = {}
        self._started_at = time.monotonic()
        self._last_read_started: float | None = None
        # Jobs are precomputed once and then executed in a due-time round robin.
        self._jobs = build_jobs(rooms, time.monotonic())
        self._startup_jobs: list[PollJob] | None = None
        self._startup_states: dict[str, RoomState] = {}
        self._startup_turn = True

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

        return sum(state.has_data for state in self.safe_data.values())

    @property
    def last_job_error(self) -> MeltemModbusError | None:
        """Return the last scheduler job error, if any."""
        return self._last_job_error

    @property
    def gateway_device_id(self) -> str | None:
        """Return the registry id of the gateway device the units hang off."""

        device = dr.async_get(self.hass).async_get_device_by_identifier(
            (DOMAIN, self._entry_id), self._entry_id
        )
        return device.id if device is not None else None

    async def async_shutdown(self) -> None:
        for overlay in self._overlays.values():
            overlay.shutdown()
        await super().async_shutdown()

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
        return state is not None and state.has_data

    def optimistic_preset_mode(self, room_key: str) -> str | None:
        """Return the pending preset selection while the gateway confirms it.

        The overlay is shared so the fan and the select entity never disagree.
        """
        state = self.safe_data.get(room_key)
        confirmed = (
            state.preset_mode
            if state is not None and read_health.group_fresh(state, "flow_control")
            else None
        )
        return self._optimistic_presets.get(room_key, confirmed)

    def optimistic_intensive(self, room_key: str) -> bool | None:
        """Return the pending intensive override while the gateway confirms it."""

        state = self.safe_data.get(room_key)
        confirmed = (
            state.intensive_active
            if state is not None and read_health.group_fresh(state, "intensive")
            else None
        )
        return self._optimistic_intensive.get(room_key, confirmed)

    def effective_levels(self, room_key: str) -> LevelPair:
        """Return the supply/extract targets a fan entity should act on.

        Falls back to the pending write while the gateway confirms it, so the
        two directional fans never rebuild each other from a stale cache.
        """

        return self._resolve_levels(room_key)[0]

    def level_source(self, room_key: str) -> str | None:
        """Return whether the effective levels are pending, targets, or measurements."""

        return self._resolve_levels(room_key)[1]

    def _resolve_levels(self, room_key: str) -> tuple[LevelPair, str | None]:
        """Return the effective supply/extract pair and where it comes from."""

        state = self.safe_data.get(room_key)
        reported, source = (None, None) if state is None else reported_levels(state)
        pending = self._optimistic_levels.get(room_key, reported)
        if pending is not None:
            return pending, LEVEL_SOURCE_PENDING
        return (reported if reported is not None else (None, None)), source

    def level_write_fallback(self, room_key: str) -> str | None:
        """Return the fallback used by the last fan write until a mode readback follows."""

        fallback = self._level_fallbacks.get(room_key)
        if fallback is None:
            return None
        marker, written_at = fallback
        state = self.safe_data.get(room_key)
        if state is not None and _mode_read_since(state, written_at):
            return None
        return marker

    def _record_level_fallback(self, room_key: str, marker: str) -> None:
        self._level_fallbacks[room_key] = (marker, dt_util.utcnow())
        self.async_update_listeners()

    def read_group_fresh(self, room_key: str, group_key: str) -> bool:
        """Return whether the group's most recent attempt succeeded recently."""

        state = self.safe_data.get(room_key)
        return state is not None and read_health.group_fresh(state, group_key)

    async def _async_update_data(self) -> dict[str, RoomState]:
        try:
            async with self._gateway_lock:
                now = time.monotonic()
                job, startup = self._select_poll_job(now)
                if job is None:
                    self._schedule_next_tick()
                    return self.safe_data

                # Move the job forward before running it so a failing read
                # cannot get stuck at the front of the queue forever.
                job.next_due = now + self._job_interval(job)
                self._last_read_started = now
                self._last_job_error = None
                updated_data = await self._read_one_job(
                    self.safe_data or self._startup_states, job
                )
                if startup:
                    job.next_due = time.monotonic() + self._job_interval(job)
                if not any(state.has_data for state in updated_data.values()):
                    self._startup_states = updated_data
                    raise self._last_job_error or MeltemModbusError(
                        f"No state values received from room {job.room_key} "
                        f"for job {job.key} during startup"
                    )
                self._startup_states = {}
                # _read_one_job swallows transport errors to keep cached state,
                # so success has to be derived from the recorded job error.
                if self._last_job_error is None:
                    self._on_transport_success()
                else:
                    self._on_transport_failure()
                # The coordinator notifies the listeners once the new data is set.
                self._confirm_pending_writes(updated_data, notify=False)
                self._schedule_next_tick()
                return updated_data
        except MeltemModbusError as err:
            # Before any state arrives, an unsuccessful job is a startup failure.
            self._on_transport_failure()
            if self._backoff_seconds is None:
                # Retrying at the request rate would hammer a gateway that is down.
                self.update_interval = timedelta(seconds=TRANSPORT_BACKOFF_START_SECONDS)
            raise UpdateFailed(str(err)) from err

    def _select_poll_job(self, now: float) -> tuple[PollJob | None, bool]:
        """Warm up all groups while allowing already-read groups to refresh."""

        if self._startup_jobs is None and not self.safe_data:
            self._startup_jobs = list(self._jobs)
        if self._read_spacing_remaining(now) > 0:
            return None, False
        if self._startup_jobs:
            due = select_due_job(
                (job for job in self._jobs if job not in self._startup_jobs), now
            )
            if due is None or self._startup_turn:
                self._startup_turn = False
                return self._startup_jobs.pop(0), True
            self._startup_turn = True
            return due, False
        return select_due_job(self._jobs, now), False

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

        now = time.monotonic()
        earliest_due = (
            now if self._startup_jobs else min(job.next_due for job in self._jobs)
        )
        seconds = max(self._tick_seconds, earliest_due - now)
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

        exponent = min(
            self._consecutive_transport_failures - TRANSPORT_BACKOFF_AFTER_FAILURES,
            math.ceil(math.log2(TRANSPORT_BACKOFF_MAX_SECONDS / TRANSPORT_BACKOFF_START_SECONDS)),
        )
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

        async with self._gateway_lock:
            await self._async_set_level_locked(room_key, level)

    async def _async_set_level_locked(self, room_key: str, level: int) -> None:
        """Write a balanced level while holding the gateway lock."""

        room = self._rooms_by_key[room_key]
        self._optimistic_presets.clear(room_key)
        await self._async_write_with_confirmation_locked(
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

        async with self._gateway_lock:
            await self._async_set_unbalanced_levels_locked(room_key, supply_level, extract_level)

    async def _async_set_unbalanced_levels_locked(
        self, room_key: str, supply_level: int, extract_level: int
    ) -> None:
        """Write separate levels while holding the gateway lock."""

        room = self._rooms_by_key[room_key]
        self._optimistic_presets.clear(room_key)
        await self._async_write_with_confirmation_locked(
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

        Resolve the opposite direction under the same gateway lock as reads
        and mode writes, so every command builds on the latest accepted state.
        """

        room = self._rooms_by_key[room_key]
        async with self._gateway_lock:
            (supply, extract), source = self._resolve_levels(room_key)
            pending = self._optimistic_levels.get(room_key, None)
            if pending is not None:
                (supply, extract), source = pending, LEVEL_SOURCE_PENDING
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
                await self._async_set_level_locked(room_key, level)
                self._record_level_fallback(room_key, LEVEL_WRITE_FALLBACK_BALANCED)
                return

            operation_mode = self.safe_data.get(
                room_key, EMPTY_ROOM_STATE
            ).operation_mode
            if source == LEVEL_SOURCE_PENDING and supply is not None and extract is not None:
                operation_mode = (
                    OPERATION_MODE_OFF
                    if supply == extract == 0
                    else OPERATION_MODE_MANUAL
                    if levels_balanced(supply, extract)
                    else OPERATION_MODE_UNBALANCED
                )
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
                levels_balanced(level, other)
                or starting_from_off
                or leaving_sensor_control
            ):
                await self._async_set_level_locked(room_key, level)
            else:
                supply_level, extract_level = (
                    (level, other) if direction == DIRECTION_SUPPLY else (other, level)
                )
                await self._async_set_unbalanced_levels_locked(
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

        async with self._gateway_lock:
            await self._async_set_operation_mode_locked(room_key, operation_mode)

    async def _async_set_operation_mode_locked(
        self, room_key: str, operation_mode: str
    ) -> None:
        """Resolve the airflow and write a mode while holding the gateway lock."""

        room = self._rooms_by_key[room_key]
        state = self.safe_data.get(room.key, EMPTY_ROOM_STATE)
        if operation_mode == OPERATION_MODE_INACTIVE:
            if state.operation_mode in DIRECT_OPERATION_MODES and read_health.group_fresh(
                state, "flow_control"
            ):
                return
            operation_mode = OPERATION_MODE_MANUAL
        supply, extract = self.effective_levels(room_key)
        balanced_level = first_known(
            supply,
            state.target_level,
            state.supply_air_flow,
            state.extract_air_flow,
        )
        extract_level = first_known(
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
        await self._async_write_with_confirmation_locked(
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

        async with self._gateway_lock:
            room = self._rooms_by_key[room_key]
            self._optimistic_levels.clear(room_key)
            await self._async_write_with_confirmation_locked(
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

        async with self._gateway_lock:
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
            await self._async_set_operation_mode_locked(room_key, operation_mode)
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
            f"{CONTROL_SETTING_WRITE_PREFIX}{setting_key}",
            value,
            self.client.write_control_setting,
            room,
            setting_key,
            value,
            refresh_attempts=1,
            use_write_result=True,
        )

    def _check_control_level_range(self, room: RoomConfig, setting_key: str, value: int) -> None:
        """Refuse a minimum level above the maximum level of the same sensor control."""

        state = self.safe_data.get(room.key, EMPTY_ROOM_STATE)
        for min_key, max_key in CONTROL_LEVEL_RANGES:
            if setting_key not in (min_key, max_key):
                continue
            minimum = self._writes.unconfirmed_control_setting(room.key, min_key)
            if minimum is None:
                minimum = getattr(state, min_key)
            maximum = self._writes.unconfirmed_control_setting(room.key, max_key)
            if maximum is None:
                maximum = getattr(state, max_key)
            if setting_key == min_key:
                minimum = normalize_control_setting(min_key, value)
            else:
                maximum = normalize_control_setting(max_key, value)
            if minimum is not None and maximum is not None and minimum > maximum:
                raise HomeAssistantError(
                    translation_domain=DOMAIN,
                    translation_key="control_level_range",
                    translation_placeholders={
                        "unit": room.name,
                        "minimum": str(minimum),
                        "maximum": str(maximum),
                    },
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
        """Serialize one setting write and its readback."""

        async with self._gateway_lock:
            await self._async_write_with_confirmation_locked(
                room,
                write_key,
                expected_value,
                write_method,
                *write_args,
                refresh_attempts=refresh_attempts,
                use_write_result=use_write_result,
            )

    async def _async_write_with_confirmation_locked(
        self,
        room: RoomConfig,
        write_key: str,
        expected_value: WriteValue,
        write_method: Callable[..., Awaitable[object]],
        *write_args: object,
        refresh_attempts: int = 0,
        use_write_result: bool = False,
    ) -> None:
        """Write and verify one setting while holding the gateway lock.

        Without ``refresh_attempts`` the regular poll jobs confirm the write.
        """

        readback_plan = _READBACK_PLANS[write_group(write_key)]
        overlay = self._overlays.get(write_key)
        if write_key.startswith(CONTROL_SETTING_WRITE_PREFIX) and isinstance(
            expected_value, int
        ):
            self._check_control_level_range(
                room, write_key.removeprefix(CONTROL_SETTING_WRITE_PREFIX), expected_value
            )
        try:
            write_result = await write_method(*write_args)
        except Exception as err:
            # A failed write makes any earlier pending value doubtful.
            if overlay is not None:
                overlay.clear(room.key)
            self._writes.record_pending(room.key, write_key, expected_value)
            self._writes.set_status(room.key, write_key, "failed", error=str(err))
            self.async_update_listeners()
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
        superseded = self._writes.supersede(room.key, write_key)
        self._writes.record_pending(room.key, write_key, confirmed_expected)
        for previous_key in superseded:
            if previous_overlay := self._overlays.get(previous_key):
                previous_overlay.clear(room.key)
        if overlay is not None:
            overlay.set(room.key, expected_value)
        else:
            self.async_update_listeners()

        if not refresh_attempts:
            return

        await async_sleep(WRITE_SETTLE_SECONDS)
        await self._async_refresh_room_after_write(
            room,
            refresh_plan=readback_plan,
            min_refresh_attempts=refresh_attempts,
        )
        if self._writes.mark_unconfirmed(
            room.key, write_key, "No matching device readback was received"
        ):
            self.async_update_listeners()

    def update_request_rate(self, max_requests_per_second: float) -> None:
        """Retune the scheduler and shared read limit without reloading."""

        self.client.update_request_rate(max_requests_per_second)
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
    ) -> tuple[str, str | None]:
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
            await self._async_wait_for_read_slot()
            previous_state = self.safe_data.get(room.key, EMPTY_ROOM_STATE)
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
                refreshed_room = read_health.record_read_failures(
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

    async def _async_wait_for_read_slot(self) -> None:
        """Wait until the request-rate cap allows the next read, then claim it."""

        if (remaining := self._read_spacing_remaining(time.monotonic())) > 0:
            await async_sleep(remaining)
        self._last_read_started = time.monotonic()

    @staticmethod
    def read_group_for_entity(entity_key: str) -> str | None:
        """Return the read-health group that owns one entity's value."""

        return read_health.read_group_for_entity(entity_key)

    def read_group_available(self, room_key: str, group_key: str) -> bool:
        """Return whether a group's values are recent and have recovered."""

        state = self.safe_data.get(room_key)
        return state is not None and read_health.group_available(state, group_key)

    def read_group_stale(self, room_key: str, group_key: str) -> bool | None:
        """Return whether a group's last successful read is stale."""

        state = self.safe_data.get(room_key)
        return None if state is None else read_health.group_stale(state, group_key)

    def data_health_stale(self, room_key: str) -> bool | None:
        """Return whether any read group or write confirmation is unhealthy."""

        state = self.safe_data.get(room_key)
        if state is None:
            return None
        if any(
            read_health.group_stale(state, group_key) is True
            for group_key, _health in state.group_read_health
        ):
            return True
        if self._writes.unhealthy(room_key):
            return True
        if (
            any(
                read_health.group_stale(state, group_key) is False
                for group_key, _health in state.group_read_health
            )
            or self._writes.has_any(room_key)
        ):
            return False
        return None

    def data_health_attributes(self, room_key: str) -> dict[str, dict[str, object]]:
        """Return per-group read health and write outcomes for one room."""

        state = self.safe_data.get(room_key, EMPTY_ROOM_STATE)
        room = self._rooms_by_key[room_key]
        attributes: dict[str, dict[str, object]] = {}
        for group_key, entity_keys in READ_GROUP_ENTITY_KEYS.items():
            if not room.supports_any(entity_keys):
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
                "stale": read_health.group_stale(state, group_key),
            }
        attributes["writes"] = self._writes.attributes(room_key)
        return attributes

    def _confirm_pending_writes(
        self, states: Mapping[str, RoomState], *, notify: bool = True
    ) -> None:
        """Judge pending writes and optimistic values by the latest readbacks."""

        changed = self._writes.confirm(states)
        for room_key, state in states.items():
            reported, _source = reported_levels(state)
            changed |= self._optimistic_levels.settle(room_key, reported)
            if read_health.group_fresh(state, "flow_control"):
                changed |= self._optimistic_presets.settle(room_key, state.preset_mode)
            if read_health.group_fresh(state, "intensive"):
                changed |= self._optimistic_intensive.settle(room_key, state.intensive_active)
            fallback = self._level_fallbacks.get(room_key)
            if fallback is not None and _mode_read_since(state, fallback[1]):
                del self._level_fallbacks[room_key]
                changed = True
        if changed and notify:
            self.async_update_listeners()

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
            refreshed_state = read_health.record_read_failures(
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
