"""Coordinate serialized polling and writes for a Meltem gateway.

The coordinator keeps gateway access strictly single-file: one read/write job
at a time, no concurrency, and one shared Modbus client. Instead of full-state
polls it schedules small refresh jobs per room and per data group.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from datetime import timedelta
import logging
import time
from typing import cast

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import (
    CONTROL_SETTINGS_REFRESH_SECONDS,
    DEFAULT_SCAN_SLAVE_END,
    DEFAULT_SCAN_SLAVE_START,
    FILTER_REFRESH_SECONDS,
    FLOW_REFRESH_SECONDS,
    OPERATING_HOURS_REFRESH_SECONDS,
    PRESET_MODE_EXTRACT_ONLY,
    PRESET_MODE_SUPPLY_ONLY,
    POST_WRITE_REFRESH_INTERVAL_SECONDS,
    POST_WRITE_REFRESH_RETRIES,
    REQUEST_GAP_SECONDS,
    STATUS_REFRESH_SECONDS,
    TEMPERATURE_REFRESH_SECONDS,
    WRITE_SETTLE_SECONDS,
)
from .modbus_client import MeltemModbusClient
from .modbus_helpers import MeltemModbusError
from .models import EMPTY_ROOM_STATE, RefreshPlan, RoomConfig, RoomState

_LOGGER = logging.getLogger(__name__)

FULL_REFRESH_PLAN = RefreshPlan()
AIRFLOW_REFRESH_PLAN = RefreshPlan.only(refresh_airflow=True)
STATUS_REFRESH_PLAN = RefreshPlan.only(refresh_status=True)
TEMPERATURE_REFRESH_PLAN = RefreshPlan.only(
    refresh_temperatures=True,
    refresh_environment=True,
)
FILTER_REFRESH_PLAN = RefreshPlan.only(
    refresh_filter_change_due=True,
    refresh_filter_days=True,
)
OPERATING_HOURS_REFRESH_PLAN = RefreshPlan.only(refresh_operating_hours=True)
CONTROL_SETTINGS_REFRESH_PLAN = RefreshPlan.only(refresh_control_settings=True)


class _CoordinatorLoggerProxy:
    """Proxy HA coordinator logs so idle scheduler ticks do not spam debug output."""

    def __init__(self, logger: logging.Logger, should_suppress_finished_fetch: Callable[[], bool]) -> None:
        self._logger = logger
        self._should_suppress_finished_fetch = should_suppress_finished_fetch

    def debug(self, msg, *args, **kwargs) -> None:
        if (
            isinstance(msg, str)
            and msg.startswith("Finished fetching ")
            and self._should_suppress_finished_fetch()
        ):
            return
        self._logger.debug(msg, *args, **kwargs)

    def info(self, msg, *args, **kwargs) -> None:
        self._logger.info(msg, *args, **kwargs)

    def warning(self, msg, *args, **kwargs) -> None:
        self._logger.warning(msg, *args, **kwargs)

    def error(self, msg, *args, **kwargs) -> None:
        self._logger.error(msg, *args, **kwargs)

    def exception(self, msg, *args, **kwargs) -> None:
        self._logger.exception(msg, *args, **kwargs)

    def __getattr__(self, name: str):
        return getattr(self._logger, name)


@dataclass(slots=True)
class PollJob:
    """One scheduled read job for one room and one refresh group."""

    key: str
    room_key: str
    refresh_plan: RefreshPlan
    interval_seconds: int
    next_due: float


class MeltemDataUpdateCoordinator(DataUpdateCoordinator[dict[str, RoomState]]):
    """Coordinate polling and writes for all configured rooms."""

    def __init__(
        self,
        hass: HomeAssistant,
        *,
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
        self._optimistic_targets_by_room: dict = {}
        self._pending_writes_by_room: dict = {}
        self._write_tasks_by_room: dict = {}
        self._active_write_rooms: set[str] = set()
        self._last_preset_levels_by_room: dict[str, dict[str, int]] = {}
        self._suppress_finished_fetch_log = False
        # Jobs are precomputed once and then executed in a due-time round robin.
        self._jobs = self._build_jobs()

        super().__init__(
            hass,
            cast(logging.Logger, _CoordinatorLoggerProxy(
                _LOGGER,
                lambda: self._suppress_finished_fetch_log,
            )),
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
    def last_job_error(self) -> MeltemModbusError | None:
        """Return the last scheduler job error, if any."""
        return self._last_job_error

    @property
    def optimistic_targets_by_room(self) -> dict:
        """Return the shared optimistic target store."""
        return self._optimistic_targets_by_room

    @property
    def pending_writes_by_room(self) -> dict:
        """Return the pending write command store."""
        return self._pending_writes_by_room

    @property
    def write_tasks_by_room(self) -> dict:
        """Return the write task store."""
        return self._write_tasks_by_room

    @property
    def active_write_rooms(self) -> set[str]:
        """Return the set of rooms with active write tasks."""
        return self._active_write_rooms

    async def _async_update_data(self) -> dict[str, RoomState]:
        self._suppress_finished_fetch_log = False
        try:
            async with self._gateway_lock:
                if not self._safe_data:
                    states = await self.hass.async_add_executor_job(self._read_all_rooms_full)
                    self._consecutive_transport_failures = 0
                    self._remember_preset_shortcut_levels(states)
                    return states

                now = time.monotonic()
                job = self._select_due_job(now)
                if job is None:
                    self._suppress_finished_fetch_log = True
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
                self._consecutive_transport_failures = 0
                self._remember_preset_shortcut_levels(updated_data)
                return updated_data
        except MeltemModbusError as err:
            self._consecutive_transport_failures += 1
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

    async def async_set_level(self, room_key: str, level: int) -> None:
        """Write a new target level for one room.

        Number entities keep an optimistic overlay locally, so there is no need
        to force an immediate confirmation poll here. The normal scheduler will
        pick up the later readback/current airflow state.
        """

        room = self._rooms_by_key[room_key]

        async with self._gateway_lock:
            await self.hass.async_add_executor_job(self.client.write_level, room, level)

    async def async_set_unbalanced_levels(
        self, room_key: str, supply_level: int, extract_level: int
    ) -> None:
        """Write separate supply and extract levels for one room.

        Number entities keep an optimistic overlay locally, so there is no need
        to force an immediate confirmation poll here. The normal scheduler will
        pick up the later readback/current airflow state.
        """

        room = self._rooms_by_key[room_key]

        async with self._gateway_lock:
            await self.hass.async_add_executor_job(
                self.client.write_unbalanced_levels,
                room,
                supply_level,
                extract_level,
            )

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

        async with self._gateway_lock:
            await self.hass.async_add_executor_job(
                self.client.write_operating_mode,
                room,
                operation_mode,
                int(balanced_level),
                int(extract_level),
            )
            await asyncio.sleep(WRITE_SETTLE_SECONDS)
            await self._async_refresh_room_after_write(room)

    async def async_set_preset_mode(self, room_key: str, preset_mode: str) -> None:
        """Write one app-style preset mode and refresh afterwards."""

        room = self._rooms_by_key[room_key]
        state = self._safe_data.get(room.key, EMPTY_ROOM_STATE)
        preferred_level = (
            self._last_preset_levels_by_room.get(room.key, {}).get(preset_mode)
            if preset_mode in (PRESET_MODE_EXTRACT_ONLY, PRESET_MODE_SUPPLY_ONLY)
            else None
        )
        if preferred_level is None:
            preferred_level = (
            state.target_level
            if state.target_level is not None and state.target_level > 0
            else state.extract_target_level
            if state.extract_target_level is not None and state.extract_target_level > 0
            else state.supply_air_flow
            if state.supply_air_flow is not None and state.supply_air_flow > 0
            else state.extract_air_flow
            if state.extract_air_flow is not None and state.extract_air_flow > 0
            else None
            )

        async with self._gateway_lock:
            await self.hass.async_add_executor_job(
                self.client.write_preset_mode,
                room,
                preset_mode,
                preferred_level,
            )
            await asyncio.sleep(WRITE_SETTLE_SECONDS)
            await self._async_refresh_room_after_write(
                room,
                min_refresh_attempts=2,
            )

    async def async_activate_intensive(self, room_key: str) -> None:
        """Start temporary intensive ventilation without changing the base preset."""

        room = self._rooms_by_key[room_key]

        async with self._gateway_lock:
            await self.hass.async_add_executor_job(
                self.client.write_preset_mode,
                room,
                "intensive",
                None,
            )
            await asyncio.sleep(WRITE_SETTLE_SECONDS)
            await self._async_refresh_room_after_write(
                room,
                min_refresh_attempts=2,
            )

    async def async_set_control_setting(
        self,
        room_key: str,
        setting_key: str,
        value: int,
    ) -> None:
        """Write one humidity/CO2 control setting and refresh it afterwards."""

        room = self._rooms_by_key[room_key]

        async with self._gateway_lock:
            await self.hass.async_add_executor_job(
                self.client.write_control_setting,
                room,
                setting_key,
                value,
            )
            await asyncio.sleep(WRITE_SETTLE_SECONDS)
            previous_states = self._safe_data
            previous_state = previous_states.get(room.key, EMPTY_ROOM_STATE)
            refreshed_room = await self.hass.async_add_executor_job(
                self.client.read_room_state,
                room,
                previous_state,
                CONTROL_SETTINGS_REFRESH_PLAN,
            )
            if refreshed_room != previous_state:
                updated_states = dict(previous_states)
                updated_states[room.key] = refreshed_room
                self.async_set_updated_data(updated_states)

    def update_request_rate(self, max_requests_per_second: float) -> None:
        """Apply a new scheduler request rate without reloading the integration."""

        self._max_requests_per_second = max(0.1, max_requests_per_second)
        self._tick_seconds = 1.0 / self._max_requests_per_second
        self.update_interval = timedelta(seconds=self._tick_seconds)

    async def async_cancel_room_write_tasks(self) -> None:
        """Cancel any queued number-entity write tasks owned by this coordinator."""

        tasks_by_room = self._write_tasks_by_room
        pending_writes_by_room = self._pending_writes_by_room
        optimistic_targets_by_room = self._optimistic_targets_by_room

        tasks = [task for task in tasks_by_room.values() if not task.done()]
        for task in tasks:
            task.cancel()

        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

        tasks_by_room.clear()
        pending_writes_by_room.clear()
        optimistic_targets_by_room.clear()
        self._active_write_rooms.clear()

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
        expected_supply_level: int | None = None,
        expected_extract_level: int | None = None,
        min_refresh_attempts: int = 1,
    ) -> None:
        """Refresh the changed room after a write settles.

        Writes only change one device, so the follow-up refresh only reads the
        airflow-related state of that same room.
        """

        previous_states = self._safe_data
        refreshed_room = previous_states.get(room.key, EMPTY_ROOM_STATE)

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
                self._remember_preset_shortcut_level(room.key, refreshed_room)
                self.async_set_updated_data(updated_states)

            reached_target = self._post_write_refresh_reached_target(
                refreshed_room,
                expected_supply_level=expected_supply_level,
                expected_extract_level=expected_extract_level,
            )
            if reached_target and attempt + 1 >= max(1, min_refresh_attempts):
                return

            if attempt < POST_WRITE_REFRESH_RETRIES:
                await asyncio.sleep(POST_WRITE_REFRESH_INTERVAL_SECONDS)

    def _post_write_refresh_reached_target(
        self,
        state: RoomState,
        *,
        expected_supply_level: int | None = None,
        expected_extract_level: int | None = None,
    ) -> bool:
        """Return whether a post-write airflow refresh reflects the requested targets."""

        if expected_supply_level is None and expected_extract_level is None:
            return True

        supply_value = (
            state.target_level if state.target_level is not None else state.supply_air_flow
        )
        extract_value = (
            state.extract_target_level
            if state.extract_target_level is not None
            else state.extract_air_flow
        )

        if expected_supply_level is not None:
            if supply_value is None or abs(supply_value - expected_supply_level) > 3:
                return False

        if expected_extract_level is not None:
            if extract_value is None or abs(extract_value - expected_extract_level) > 3:
                return False

        return True

    def _remember_preset_shortcut_level(self, room_key: str, state: RoomState) -> None:
        """Remember the last observed app-style single-direction shortcut level."""

        if state.preset_mode == PRESET_MODE_EXTRACT_ONLY:
            level = (
                state.extract_target_level
                if state.extract_target_level is not None and state.extract_target_level > 0
                else state.extract_air_flow
                if state.extract_air_flow is not None and state.extract_air_flow > 0
                else None
            )
            if level is not None:
                self._last_preset_levels_by_room.setdefault(room_key, {})[
                    PRESET_MODE_EXTRACT_ONLY
                ] = level
        elif state.preset_mode == PRESET_MODE_SUPPLY_ONLY:
            level = (
                state.target_level
                if state.target_level is not None and state.target_level > 0
                else state.supply_air_flow
                if state.supply_air_flow is not None and state.supply_air_flow > 0
                else None
            )
            if level is not None:
                self._last_preset_levels_by_room.setdefault(room_key, {})[
                    PRESET_MODE_SUPPLY_ONLY
                ] = level

    def _remember_preset_shortcut_levels(self, states: dict[str, RoomState]) -> None:
        """Update remembered shortcut levels from a room-state map."""

        for room_key, state in states.items():
            self._remember_preset_shortcut_level(room_key, state)

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
            except MeltemModbusError as err:
                _LOGGER.warning("Failed to read room %s during startup: %s", room.key, err)
                states[room.key] = EMPTY_ROOM_STATE
                last_error = err
                # Reset after a failure so the next room starts with a clean connection.
                self.client.reset_connection()
                time.sleep(0.5)

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
            return previous_states
        else:
            self._last_job_error = None
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
        jobs: list[PollJob] = []

        jobs.extend(
            self._build_group_jobs(
                "flow",
                FLOW_REFRESH_SECONDS,
                AIRFLOW_REFRESH_PLAN,
                now,
            )
        )
        jobs.extend(
            self._build_group_jobs(
                "status",
                STATUS_REFRESH_SECONDS,
                STATUS_REFRESH_PLAN,
                now,
            )
        )
        jobs.extend(
            self._build_group_jobs(
                "temperature",
                TEMPERATURE_REFRESH_SECONDS,
                TEMPERATURE_REFRESH_PLAN,
                now,
            )
        )
        jobs.extend(
            self._build_group_jobs(
                "filter",
                FILTER_REFRESH_SECONDS,
                FILTER_REFRESH_PLAN,
                now,
            )
        )
        jobs.extend(
            self._build_group_jobs(
                "hours",
                OPERATING_HOURS_REFRESH_SECONDS,
                OPERATING_HOURS_REFRESH_PLAN,
                now,
            )
        )
        jobs.extend(
            self._build_group_jobs(
                "control_settings",
                CONTROL_SETTINGS_REFRESH_SECONDS,
                CONTROL_SETTINGS_REFRESH_PLAN,
                now,
            )
        )

        return jobs

    def _build_group_jobs(
        self,
        key: str,
        interval_seconds: int,
        refresh_plan: RefreshPlan,
        now: float,
    ) -> list[PollJob]:
        """Create one staggered job per room for one refresh group."""

        rooms = [
            room
            for room in self.rooms
            if self._room_needs_job(room, key)
        ]
        if not rooms:
            return []

        # Spread jobs of one group across their full interval so a group does
        # not fire for every room at the same moment.
        spacing = interval_seconds / len(rooms)
        jobs: list[PollJob] = []
        for index, room in enumerate(rooms):
            jobs.append(
                PollJob(
                    key=key,
                    room_key=room.key,
                    refresh_plan=refresh_plan,
                    interval_seconds=interval_seconds,
                    next_due=now + (index * spacing),
                )
            )
        return jobs

    def _room_needs_job(self, room: RoomConfig, job_key: str) -> bool:
        """Check whether a room has any entities covered by a job."""

        supported = set(room.supported_entity_keys or ())

        if not supported:
            return True

        job_entities: dict[str, set[str]] = {
            "flow": {
                "extract_air_flow",
                "supply_air_flow",
                "level",
                "supply_level",
                "extract_level",
                "operation_mode",
                "preset_mode",
                "intensive_active",
            },
            "status": {"error_status", "frost_protection_active"},
            "temperature": {
                "exhaust_temperature",
                "outdoor_air_temperature",
                "extract_air_temperature",
                "supply_air_temperature",
                "humidity_extract_air",
                "humidity_supply_air",
                "co2_extract_air",
                "voc_supply_air",
            },
            "filter": {"filter_change_due", "days_until_filter_change"},
            "hours": {
                "operating_hours",
            },
            "control_settings": {
                "humidity_starting_point",
                "humidity_min_level",
                "humidity_max_level",
                "co2_starting_point",
                "co2_min_level",
                "co2_max_level",
            },
        }
        return bool(job_entities[job_key] & supported)
