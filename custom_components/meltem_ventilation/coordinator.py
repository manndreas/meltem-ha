"""Coordinate serialized polling and writes for a Meltem gateway.

The coordinator keeps gateway access strictly single-file: one read/write job
at a time, no concurrency, and one shared Modbus client. Instead of full-state
polls it schedules small refresh jobs per room and per data group.
"""

from __future__ import annotations

import asyncio
import logging
import operator
import time
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import (
    CONTROL_SETTING_REGISTERS,
    CONTROL_SETTINGS_REFRESH_SECONDS,
    DEFAULT_SCAN_SLAVE_END,
    DEFAULT_SCAN_SLAVE_START,
    FILTER_REFRESH_SECONDS,
    FLOW_REFRESH_SECONDS,
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
    SENSOR_OPERATION_MODES,
    STATUS_REFRESH_SECONDS,
    TARGET_OPTIMISTIC_SECONDS,
    TEMPERATURE_REFRESH_SECONDS,
    WRITE_SETTLE_SECONDS,
)
from .modbus_client import MeltemModbusClient
from .modbus_helpers import MeltemModbusError
from .models import EMPTY_ROOM_STATE, RefreshPlan, RoomConfig, RoomState

_LOGGER = logging.getLogger(__name__)
async_sleep = asyncio.sleep
sync_sleep = time.sleep

FULL_REFRESH_PLAN = RefreshPlan()


@dataclass(frozen=True, slots=True)
class JobGroup:
    """One refresh group: which registers it covers and how often it runs."""

    key: str
    interval_seconds: int
    refresh_plan: RefreshPlan
    entity_keys: frozenset[str]


JOB_GROUPS: tuple[JobGroup, ...] = (
    JobGroup(
        key="flow",
        interval_seconds=FLOW_REFRESH_SECONDS,
        refresh_plan=RefreshPlan.only(refresh_airflow=True),
        entity_keys=frozenset(
            {
                "extract_air_flow",
                "supply_air_flow",
                "supply_level",
                "extract_level",
                "operation_mode",
                "preset_mode",
                "intensive",
            }
        ),
    ),
    JobGroup(
        key="status",
        interval_seconds=STATUS_REFRESH_SECONDS,
        refresh_plan=RefreshPlan.only(refresh_status=True),
        entity_keys=frozenset(
            {"error_status", "frost_protection_active", "rf_comm_status"}
        ),
    ),
    JobGroup(
        key="temperature",
        interval_seconds=TEMPERATURE_REFRESH_SECONDS,
        refresh_plan=RefreshPlan.only(
            refresh_temperatures=True,
            refresh_environment=True,
        ),
        entity_keys=frozenset(
            {
                "exhaust_temperature",
                "outdoor_air_temperature",
                "extract_air_temperature",
                "supply_air_temperature",
                "humidity_extract_air",
                "humidity_supply_air",
                "co2_extract_air",
                "voc_supply_air",
            }
        ),
    ),
    JobGroup(
        key="filter",
        interval_seconds=FILTER_REFRESH_SECONDS,
        refresh_plan=RefreshPlan.only(
            refresh_filter_change_due=True,
            refresh_filter_days=True,
        ),
        entity_keys=frozenset({"filter_change_due", "days_until_filter_change"}),
    ),
    JobGroup(
        key="hours",
        interval_seconds=OPERATING_HOURS_REFRESH_SECONDS,
        refresh_plan=RefreshPlan.only(refresh_operating_hours=True),
        entity_keys=frozenset({"operating_hours"}),
    ),
    JobGroup(
        key="control_settings",
        interval_seconds=CONTROL_SETTINGS_REFRESH_SECONDS,
        refresh_plan=RefreshPlan.only(refresh_control_settings=True),
        entity_keys=frozenset(CONTROL_SETTING_REGISTERS),
    ),
)

AIRFLOW_REFRESH_PLAN = JOB_GROUPS[0].refresh_plan
CONTROL_SETTINGS_REFRESH_PLAN = JOB_GROUPS[-1].refresh_plan

TRANSPORT_BACKOFF_AFTER_FAILURES = 3
TRANSPORT_BACKOFF_START_SECONDS = 5.0
TRANSPORT_BACKOFF_MAX_SECONDS = 60.0
ROOM_UNAVAILABLE_AFTER_FAILURES = 3
# The slowest job runs hourly, but the airflow job polls every 10 s, so a unit
# that answers nothing for this long is genuinely silent.
ROOM_SILENT_AFTER_SECONDS = 120.0
PRESET_OPTIMISTIC_SECONDS = 15.0
# Rounding between m3/h and the raw 0..200 register costs at most 1 m3/h.
LEVEL_CONFIRM_TOLERANCE = 2
# Fallback wake-up for the degenerate case of a gateway without any poll job.
IDLE_TICK_SECONDS = 60.0


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
        matches: Callable[[T, T], bool] = operator.eq,
    ) -> None:
        self._ttl_seconds = ttl_seconds
        self._matches = matches
        self._pending: dict[str, tuple[T, float]] = {}

    def set(self, room_key: str, value: T) -> None:
        self._pending[room_key] = (value, time.monotonic() + self._ttl_seconds)

    def clear(self, room_key: str) -> bool:
        """Drop the overlay and report whether there was one."""

        return self._pending.pop(room_key, None) is not None

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


def _levels_reached(
    confirmed: tuple[int | None, int | None], expected: tuple[int, int]
) -> bool:
    """Return whether both directions reached their pending target."""

    return all(
        actual is not None and abs(actual - target) <= LEVEL_CONFIRM_TOLERANCE
        for actual, target in zip(confirmed, expected)
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
        self._rooms_by_key = {room.key: room for room in rooms}
        self._max_requests_per_second = max(0.1, max_requests_per_second)
        self._tick_seconds = 1.0 / self._max_requests_per_second
        self._gateway_lock = asyncio.Lock()
        self._last_job_error: MeltemModbusError | None = None
        self._consecutive_transport_failures = 0
        self._backoff_seconds: float | None = None
        self._room_failures: dict[str, int] = {}
        self._optimistic_presets = _OptimisticOverlay[str](PRESET_OPTIMISTIC_SECONDS)
        self._optimistic_intensive = _OptimisticOverlay[bool](
            PRESET_OPTIMISTIC_SECONDS, matches=operator.is_
        )
        self._optimistic_levels = _OptimisticOverlay[tuple[int, int]](
            TARGET_OPTIMISTIC_SECONDS, matches=_levels_reached
        )
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
    def _safe_data(self) -> dict[str, RoomState]:
        """Return the current data dict, or an empty dict before the first poll."""
        return self.data if isinstance(self.data, dict) else {}

    @property
    def safe_data(self) -> dict[str, RoomState]:
        """Public access to the current room state map."""
        return self._safe_data

    @property
    def state_room_count(self) -> int:
        """Return how many rooms currently contain at least one state value."""

        return sum(self._room_state_has_data(state) for state in self._safe_data.values())

    @property
    def last_job_error(self) -> MeltemModbusError | None:
        """Return the last scheduler job error, if any."""
        return self._last_job_error

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

        state = self._safe_data.get(room_key)
        if state is None:
            return False
        return self._room_state_has_data(state)

    def optimistic_preset_mode(self, room_key: str) -> str | None:
        """Return the pending preset selection while the gateway confirms it.

        The overlay is shared so the fan and the select entity never disagree.
        """
        state = self._safe_data.get(room_key)
        confirmed = (state.preset_mode or PRESET_MODE_INACTIVE) if state else None
        return self._optimistic_presets.get(room_key, confirmed)

    def _set_optimistic_preset_mode(self, room_key: str, preset_mode: str) -> None:
        self._optimistic_presets.set(room_key, preset_mode)
        self.async_update_listeners()

    def _clear_optimistic_preset_mode(self, room_key: str) -> None:
        if self._optimistic_presets.clear(room_key):
            self.async_update_listeners()

    def optimistic_intensive(self, room_key: str) -> bool | None:
        """Return the pending intensive override while the gateway confirms it."""

        state = self._safe_data.get(room_key)
        return self._optimistic_intensive.get(
            room_key, state.intensive_active if state else None
        )

    def _set_optimistic_intensive(self, room_key: str, intensive_active: bool) -> None:
        self._optimistic_intensive.set(room_key, intensive_active)
        self.async_update_listeners()

    def _clear_optimistic_intensive(self, room_key: str) -> None:
        if self._optimistic_intensive.clear(room_key):
            self.async_update_listeners()

    def effective_levels(self, room_key: str) -> tuple[int | None, int | None]:
        """Return the supply/extract targets a fan entity should act on.

        Falls back to the pending write while the gateway confirms it, so the
        two directional fans never rebuild each other from a stale cache.
        """

        confirmed = self._confirmed_levels(self._safe_data.get(room_key, EMPTY_ROOM_STATE))
        return self._optimistic_levels.get(room_key, confirmed) or confirmed

    @staticmethod
    def _confirmed_levels(state: RoomState) -> tuple[int | None, int | None]:
        """Split the room state into a supply/extract target pair."""

        if state.operation_mode == OPERATION_MODE_OFF:
            return 0, 0

        if state.operation_mode == OPERATION_MODE_UNBALANCED:
            supply = state.target_level if state.target_level is not None else state.supply_air_flow
            extract = (
                state.extract_target_level
                if state.extract_target_level is not None
                else state.extract_air_flow
            )
            return supply, extract

        if state.operation_mode in SENSOR_OPERATION_MODES:
            # The unit picks the airflow itself and exposes no target register.
            return state.supply_air_flow, state.extract_air_flow

        # Balanced modes drive both fans from a single register.
        common = state.target_level
        if common is None:
            common = state.supply_air_flow
        if common is None:
            common = state.extract_air_flow
        return common, common

    def _set_optimistic_levels(self, room_key: str, supply: int, extract: int) -> None:
        self._optimistic_levels.set(room_key, (supply, extract))
        self.async_update_listeners()

    def _clear_optimistic_levels(self, room_key: str) -> None:
        if self._optimistic_levels.clear(room_key):
            self.async_update_listeners()

    async def _async_update_data(self) -> dict[str, RoomState]:
        try:
            async with self._gateway_lock:
                if not self._safe_data:
                    states = await self.hass.async_add_executor_job(self._read_all_rooms_full)
                    self._on_transport_success()
                    self._schedule_next_tick()
                    return states

                now = time.monotonic()
                job = self._select_due_job(now)
                if job is None:
                    self._schedule_next_tick()
                    return self.data

                # Move the job forward before running it so a failing read
                # cannot get stuck at the front of the queue forever.
                job.next_due = now + job.interval_seconds
                self._last_job_error = None
                updated_data = await self.hass.async_add_executor_job(
                    self._read_one_job,
                    self.data,
                    job,
                )
                # _read_one_job swallows transport errors to keep cached state,
                # so success has to be derived from the recorded job error.
                if self._last_job_error is None:
                    self._on_transport_success()
                else:
                    self._consecutive_transport_failures += 1
                    self._apply_transport_backoff()
                self._schedule_next_tick()
                return updated_data
        except MeltemModbusError as err:
            self._consecutive_transport_failures += 1
            self._apply_transport_backoff()
            if self._safe_data and self._consecutive_transport_failures <= 3:
                self._last_job_error = err
                self.client.reset_connection()
                _LOGGER.warning(
                    "Keeping cached Meltem state after transient transport error (%s/%s): %s",
                    self._consecutive_transport_failures,
                    3,
                    err,
                )
                return self.data
            raise UpdateFailed(str(err)) from err

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
        self.update_interval = timedelta(seconds=seconds)

    def _on_transport_success(self) -> None:
        """Reset failure tracking and any active polling backoff."""

        self._consecutive_transport_failures = 0
        if self._backoff_seconds is None:
            return
        self._backoff_seconds = None
        self._schedule_next_tick()
        _LOGGER.info("Meltem gateway reachable again, resuming normal polling rate")

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

        # A manual airflow change leaves any quick mode behind.
        self._clear_optimistic_preset_mode(room_key)
        self._set_optimistic_levels(room_key, level, level)
        try:
            async with self._gateway_lock:
                await self.hass.async_add_executor_job(self.client.write_level, room, level)
        except Exception:
            self._clear_optimistic_levels(room_key)
            raise

    async def async_set_unbalanced_levels(
        self, room_key: str, supply_level: int, extract_level: int
    ) -> None:
        """Write separate supply and extract levels for one room.

        No confirmation poll is forced here: the fast airflow job picks up the
        readback within a few seconds and an extra read only adds bus load.
        """

        room = self._rooms_by_key[room_key]

        # A manual airflow change leaves any quick mode behind.
        self._clear_optimistic_preset_mode(room_key)
        self._set_optimistic_levels(room_key, supply_level, extract_level)
        try:
            async with self._gateway_lock:
                await self.hass.async_add_executor_job(
                    self.client.write_unbalanced_levels,
                    room,
                    supply_level,
                    extract_level,
                )
        except Exception:
            self._clear_optimistic_levels(room_key)
            raise

    async def async_set_operation_mode(self, room_key: str, operation_mode: str) -> None:
        """Write a new operating mode for one room and refresh afterwards."""

        room = self._rooms_by_key[room_key]
        state = self._safe_data.get(room.key, EMPTY_ROOM_STATE)
        balanced_level = (
            state.target_level
            if state.target_level is not None
            else state.supply_air_flow
            if state.supply_air_flow is not None
            else state.extract_air_flow
            if state.extract_air_flow is not None
            else 0
        )
        extract_level = (
            state.extract_target_level
            if state.extract_target_level is not None
            else state.extract_air_flow
            if state.extract_air_flow is not None
            else balanced_level
        )

        self._clear_optimistic_levels(room_key)
        async with self._gateway_lock:
            await self.hass.async_add_executor_job(
                self.client.write_operating_mode,
                room,
                operation_mode,
                int(balanced_level),
                int(extract_level),
            )
            await async_sleep(WRITE_SETTLE_SECONDS)
            await self._async_refresh_room_after_write(room)

    async def async_set_preset_mode(self, room_key: str, preset_mode: str) -> None:
        """Write one app-style preset mode and refresh afterwards."""

        room = self._rooms_by_key[room_key]

        self._set_optimistic_preset_mode(room_key, preset_mode)
        self._clear_optimistic_levels(room_key)
        try:
            async with self._gateway_lock:
                await self.hass.async_add_executor_job(
                    self.client.write_preset_mode,
                    room,
                    preset_mode,
                )
                await async_sleep(WRITE_SETTLE_SECONDS)
                await self._async_refresh_room_after_write(
                    room,
                    min_refresh_attempts=2,
                )
        except Exception:
            self._clear_optimistic_preset_mode(room_key)
            raise

    async def async_clear_preset_mode(self, room_key: str) -> None:
        """Leave the quick-mode shortcut and keep the current airflow behavior."""

        state = self._safe_data.get(room_key, EMPTY_ROOM_STATE)
        pending_preset_mode = self.optimistic_preset_mode(room_key)
        effective_preset_mode = pending_preset_mode or state.preset_mode
        if effective_preset_mode is None or effective_preset_mode == PRESET_MODE_INACTIVE:
            self._clear_optimistic_preset_mode(room_key)
            return

        operation_mode = (
            OPERATION_MODE_UNBALANCED
            if effective_preset_mode in (PRESET_MODE_EXTRACT_ONLY, PRESET_MODE_SUPPLY_ONLY)
            else OPERATION_MODE_MANUAL
        )
        self._set_optimistic_preset_mode(room_key, PRESET_MODE_INACTIVE)
        try:
            await self.async_set_operation_mode(room_key, operation_mode)
        except Exception:
            self._clear_optimistic_preset_mode(room_key)
            raise

    async def async_activate_intensive(self, room_key: str) -> None:
        """Start temporary intensive ventilation without changing the base preset."""

        room = self._rooms_by_key[room_key]

        self._set_optimistic_intensive(room_key, True)
        try:
            async with self._gateway_lock:
                await self.hass.async_add_executor_job(
                    self.client.write_preset_mode,
                    room,
                    PRESET_MODE_INTENSIVE,
                )
                await async_sleep(WRITE_SETTLE_SECONDS)
                await self._async_refresh_room_after_write(
                    room,
                    min_refresh_attempts=2,
                )
        except Exception:
            self._clear_optimistic_intensive(room_key)
            raise

    async def async_deactivate_intensive(self, room_key: str) -> None:
        """Cancel a running intensive override and keep the base preset."""

        room = self._rooms_by_key[room_key]

        self._set_optimistic_intensive(room_key, False)
        try:
            async with self._gateway_lock:
                await self.hass.async_add_executor_job(
                    self.client.clear_intensive,
                    room,
                )
                await async_sleep(WRITE_SETTLE_SECONDS)
                await self._async_refresh_room_after_write(
                    room,
                    min_refresh_attempts=2,
                )
        except Exception:
            self._clear_optimistic_intensive(room_key)
            raise

    async def async_set_control_setting(
        self,
        room_key: str,
        setting_key: str,
        value: int,
    ) -> None:
        """Write one humidity/CO2 control setting and refresh it afterwards."""

        room = self._rooms_by_key[room_key]

        async with self._gateway_lock:
            written_value = await self.hass.async_add_executor_job(
                self.client.write_control_setting,
                room,
                setting_key,
                value,
            )

            previous_states = self._safe_data
            previous_state = previous_states.get(room.key, EMPTY_ROOM_STATE)
            optimistic_state = replace(
                previous_state, **{setting_key: written_value}
            )
            optimistic_states = dict(previous_states)
            optimistic_states[room.key] = optimistic_state
            self.async_set_updated_data(optimistic_states)

            await async_sleep(WRITE_SETTLE_SECONDS)
            try:
                refreshed_room = await self.hass.async_add_executor_job(
                    self.client.read_room_state,
                    room,
                    optimistic_state,
                    CONTROL_SETTINGS_REFRESH_PLAN,
                )
            except MeltemModbusError as err:
                self.client.reset_connection()
                _LOGGER.warning(
                    "Failed to confirm control setting %s for room %s: %s",
                    setting_key,
                    room.key,
                    err,
                )
                return

            if refreshed_room != optimistic_state:
                updated_states = dict(self._safe_data)
                updated_states[room.key] = refreshed_room
                self.async_set_updated_data(updated_states)

    def update_request_rate(self, max_requests_per_second: float) -> None:
        """Apply a new scheduler request rate without reloading the integration."""

        self._max_requests_per_second = max(0.1, max_requests_per_second)
        self._tick_seconds = 1.0 / self._max_requests_per_second
        if self._backoff_seconds is None:
            self._schedule_next_tick()

    async def async_discover_gateway_units(self) -> list[int]:
        """Discover configured units using the active gateway client."""

        async with self._gateway_lock:
            return await self.hass.async_add_executor_job(
                self.client.discover_gateway_units,
                DEFAULT_SCAN_SLAVE_START,
                DEFAULT_SCAN_SLAVE_END,
            )

    async def async_probe_slave_details(
        self,
        slave: int,
    ) -> tuple[str, str | None, list[str]]:
        """Probe one unit using the active gateway client."""

        async with self._gateway_lock:
            return await self.hass.async_add_executor_job(
                self.client.probe_slave_details,
                slave,
            )

    async def _async_refresh_room_after_write(
        self,
        room: RoomConfig,
        *,
        min_refresh_attempts: int = 1,
    ) -> None:
        """Refresh the changed room after a write settles.

        Writes only change one device, so the follow-up refresh only reads the
        airflow-related state of that same room.
        """

        for attempt in range(POST_WRITE_REFRESH_RETRIES + 1):
            previous_state = self._safe_data.get(room.key, EMPTY_ROOM_STATE)
            try:
                refreshed_room = await self.hass.async_add_executor_job(
                    self.client.read_room_state,
                    room,
                    previous_state,
                    AIRFLOW_REFRESH_PLAN,
                )
            except MeltemModbusError as err:
                self.client.reset_connection()
                _LOGGER.warning(
                    "Failed to refresh room %s immediately after write: %s",
                    room.key,
                    err,
                )
                return

            if refreshed_room != previous_state:
                updated_states = dict(self._safe_data)
                updated_states[room.key] = refreshed_room
                self.async_set_updated_data(updated_states)

            if attempt + 1 >= max(1, min_refresh_attempts):
                return

            if attempt < POST_WRITE_REFRESH_RETRIES:
                await async_sleep(POST_WRITE_REFRESH_INTERVAL_SECONDS)

    def _read_all_rooms_full(self) -> dict[str, RoomState]:
        """Read a full initial state for all configured rooms."""

        states: dict[str, RoomState] = {}
        successful_reads = 0
        last_error: MeltemModbusError | None = None
        for room in self.rooms:
            try:
                states[room.key] = self.client.read_room_state(
                    room,
                    EMPTY_ROOM_STATE,
                    FULL_REFRESH_PLAN,
                )
                successful_reads += 1
                self._room_failures.pop(room.key, None)
            except MeltemModbusError as err:
                _LOGGER.warning("Failed to read room %s during startup: %s", room.key, err)
                states[room.key] = EMPTY_ROOM_STATE
                last_error = err
                self._room_failures[room.key] = self._room_failures.get(room.key, 0) + 1
                # Reset after a failure so the next room starts with a clean connection.
                self.client.reset_connection()
                sync_sleep(0.5)

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
        )

    def _read_one_job(
        self,
        previous_states: dict[str, RoomState],
        job: PollJob,
    ) -> dict[str, RoomState]:
        """Run one scheduled read job and merge the result into the state map."""

        room = self._rooms_by_key[job.room_key]
        previous_state = previous_states.get(room.key, EMPTY_ROOM_STATE)

        try:
            refreshed_state = self.client.read_room_state(
                room,
                previous_state,
                job.refresh_plan,
            )
        except MeltemModbusError as err:
            _LOGGER.warning("Failed to read room %s for job %s: %s", room.key, job.key, err)
            self.client.reset_connection()
            self._last_job_error = err
            self._room_failures[room.key] = self._room_failures.get(room.key, 0) + 1
            return previous_states
        else:
            self._last_job_error = None
            self._room_failures.pop(room.key, None)
            if refreshed_state == previous_state:
                return previous_states
            state_map = dict(previous_states)
            state_map[room.key] = refreshed_state
        return state_map

    def _select_due_job(self, now: float) -> PollJob | None:
        """Return the next due job, if any."""

        if not self._jobs:
            return None

        due_job: PollJob | None = None
        for job in self._jobs:
            if job.next_due > now:
                continue
            if due_job is None or job.next_due < due_job.next_due:
                due_job = job
        return due_job

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

        supported = room.supported_entity_keys
        if not supported:
            return True
        return bool(group.entity_keys & supported)
