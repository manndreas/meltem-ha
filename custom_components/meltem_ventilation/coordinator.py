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
import operator
import time
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from . import read_health
from .const import (
    DEFAULT_SCAN_SLAVE_END,
    DEFAULT_SCAN_SLAVE_START,
    DIRECT_OPERATION_MODES,
    DOMAIN,
    GATEWAY_CONNECTED_DEVICES_REFRESH_SECONDS,
    GATEWAY_CONNECTED_DEVICES_STALE_SECONDS,
    GATEWAY_RF_STATISTICS_REFRESH_SECONDS,
    GATEWAY_RF_STATISTICS_STALE_SECONDS,
    GATEWAY_UPTIME_REFRESH_SECONDS,
    GATEWAY_UPTIME_STALE_SECONDS,
    GATEWAY_UTC_CLOCK_REFRESH_SECONDS,
    GATEWAY_UTC_CLOCK_STALE_SECONDS,
    LEVEL_SOURCE_PENDING,
    LEVEL_WRITE_FALLBACK_BALANCED,
    OPERATION_MODE_INACTIVE,
    OPERATION_MODE_MANUAL,
    OPERATION_MODE_UNBALANCED,
    POST_WRITE_REFRESH_INTERVAL_SECONDS,
    POST_WRITE_REFRESH_RETRIES,
    PRESET_MODE_EXTRACT_ONLY,
    PRESET_MODE_INACTIVE,
    PRESET_MODE_INTENSIVE,
    PRESET_MODE_SUPPLY_ONLY,
    READ_GROUP_ENTITY_KEYS,
    REGISTER_CURRENT_LEVEL,
    TARGET_OPTIMISTIC_SECONDS,
    WRITE_SETTLE_SECONDS,
)
from .levels import (
    LevelPair,
    levels_reached,
    mode_write_levels,
    plan_direction_write,
    reported_levels,
)
from .modbus_helpers import MeltemModbusError
from .models import (
    EMPTY_ROOM_STATE,
    GatewayRfStatistics,
    MeltemIntegrationConfigEntry,
    RefreshPlan,
    RoomConfig,
    RoomState,
    WriteValue,
)
from .overlay import OptimisticOverlay
from .polling import (
    AIRFLOW_REFRESH_PLAN,
    CONTROL_SETTINGS_REFRESH_PLAN,
    IDLE_TICK_SECONDS,
    SILENT_ROOM_POLL_SECONDS,
    TRANSPORT_BACKOFF_AFTER_FAILURES,
    TRANSPORT_BACKOFF_MAX_SECONDS,
    TRANSPORT_BACKOFF_START_SECONDS,
    UNREAD_GROUP_RETRY_SECONDS,
    PollJob,
    backoff_seconds,
    build_jobs,
    next_tick_seconds,
    select_due_job,
)
from .write_confirmation import CONTROL_SETTING_WRITE_PREFIX, WriteConfirmations, write_group

_LOGGER = logging.getLogger(__name__)
async_sleep = asyncio.sleep

ROOM_UNAVAILABLE_AFTER_FAILURES = 3
# The slowest job runs hourly, but the airflow job polls every 10 s, so a unit
# that answers nothing for this long is genuinely silent.
ROOM_SILENT_AFTER_SECONDS = 120.0
PRESET_OPTIMISTIC_SECONDS = 15.0

__all__ = [
    "IDLE_TICK_SECONDS",
    "MeltemDataUpdateCoordinator",
    "PRESET_OPTIMISTIC_SECONDS",
    "PollJob",
    "ROOM_SILENT_AFTER_SECONDS",
    "ROOM_UNAVAILABLE_AFTER_FAILURES",
    "SILENT_ROOM_POLL_SECONDS",
    "TRANSPORT_BACKOFF_AFTER_FAILURES",
    "TRANSPORT_BACKOFF_MAX_SECONDS",
    "TRANSPORT_BACKOFF_START_SECONDS",
    "UNREAD_GROUP_RETRY_SECONDS",
]

# Readback for each register group a write can target.
_READBACK_PLANS: dict[str, RefreshPlan] = {
    "flow_control": AIRFLOW_REFRESH_PLAN,
    "control_settings": CONTROL_SETTINGS_REFRESH_PLAN,
}


class _CoordinatorClient(Protocol):
    async def async_read_gateway_uptime(self) -> int: ...

    async def async_read_gateway_utc_clock(self) -> int: ...

    async def async_read_gateway_node_count(self) -> int: ...

    async def async_read_gateway_rf_statistics(self) -> GatewayRfStatistics: ...

    def seconds_since_successful_read(self, slave: int) -> float | None: ...

    def transport_diagnostics(self) -> dict[str, float | int | None]: ...

    async def discover_gateway_units(self, start: int, end: int) -> list[int]: ...

    async def probe_slave_details(self, slave: int) -> tuple[str, str | None]: ...

    async def read_room_state(
        self, room: RoomConfig, previous_state: RoomState, refresh_plan: RefreshPlan
    ) -> RoomState: ...

    async def read_balanced_target_readback(self, room: RoomConfig) -> int | None: ...

    async def write_level(self, room: RoomConfig, level: int) -> None: ...

    async def write_unbalanced_levels(
        self, room: RoomConfig, supply_level: int, extract_level: int
    ) -> None: ...

    async def write_operating_mode(
        self, room: RoomConfig, operation_mode: str, balanced_level: int, extract_level: int
    ) -> None: ...

    async def write_preset_mode(self, room: RoomConfig, preset_mode: str) -> None: ...

    async def clear_intensive(self, room: RoomConfig) -> None: ...

    async def write_control_setting(
        self, room: RoomConfig, setting_key: str, value: int
    ) -> int: ...

    def update_request_rate(self, max_requests_per_second: float) -> None: ...

    def shutdown(self) -> None: ...


class MeltemDataUpdateCoordinator(DataUpdateCoordinator[dict[str, RoomState]]):
    """Coordinate polling and writes for all configured rooms."""

    def __init__(
        self,
        hass: HomeAssistant,
        *,
        config_entry: MeltemIntegrationConfigEntry,
        client: _CoordinatorClient,
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
        # Only timeouts and lost links count towards the gateway backoff; a unit
        # that answers with an exception response proves the link works.
        self._last_job_link_failure = False
        self._consecutive_transport_failures = 0
        self._backoff_seconds: float | None = None
        self._room_failures: dict[str, int] = {}
        self._logged_unavailable_rooms: set[str] = set()
        self._target_read_failing: set[str] = set()
        self._writes = WriteConfirmations()
        self._optimistic_presets = OptimisticOverlay[str, str](
            hass,
            PRESET_OPTIMISTIC_SECONDS,
            self.async_update_listeners,
            read_groups=("flow_control",),
            state_fn=self._room_state,
        )
        self._optimistic_intensive = OptimisticOverlay[bool, bool](
            hass,
            PRESET_OPTIMISTIC_SECONDS,
            self.async_update_listeners,
            matches=operator.is_,
            read_groups=("intensive",),
            state_fn=self._room_state,
        )
        self._optimistic_levels = OptimisticOverlay[tuple[int, int], LevelPair](
            hass,
            TARGET_OPTIMISTIC_SECONDS,
            self.async_update_listeners,
            matches=levels_reached,
            read_groups=("flow", "flow_control"),
            state_fn=self._room_state,
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
        self._gateway_uptime_enabled = False
        self._gateway_uptime_next_due = 0.0
        self._gateway_uptime_value: int | None = None
        self._gateway_uptime_read_at: float | None = None
        self._gateway_uptime_task: asyncio.Task[int] | None = None
        self._gateway_utc_clock_enabled = False
        self._gateway_utc_clock_next_due = 0.0
        self._gateway_utc_clock_value: datetime | None = None
        self._gateway_utc_clock_read_at: float | None = None
        self._gateway_utc_clock_task: asyncio.Task[int] | None = None
        self._gateway_connected_devices_enabled = False
        self._gateway_connected_devices_next_due = 0.0
        self._gateway_connected_devices_value: int | None = None
        self._gateway_connected_devices_read_at: float | None = None
        self._gateway_connected_devices_task: asyncio.Task[int] | None = None
        self._gateway_rf_statistics_consumers: set[str] = set()
        self._gateway_rf_statistics_next_due = 0.0
        self._gateway_rf_statistics_value: GatewayRfStatistics | None = None
        self._gateway_rf_statistics_read_at: float | None = None
        self._gateway_rf_statistics_task: asyncio.Task[GatewayRfStatistics] | None = None

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

    def _room_state(self, room_key: str) -> RoomState | None:
        return self.safe_data.get(room_key)

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
        self.set_gateway_uptime_enabled(False)
        self.set_gateway_utc_clock_enabled(False)
        self.set_gateway_connected_devices_enabled(False)
        for key in tuple(self._gateway_rf_statistics_consumers):
            self.set_gateway_rf_statistics_enabled(key, False)
        for overlay in self._overlays.values():
            overlay.shutdown()
        await super().async_shutdown()

    @property
    def gateway_uptime(self) -> int | None:
        """Return the last raw uptime only while its observation is recent."""

        if self._gateway_uptime_read_at is None or (
            time.monotonic() - self._gateway_uptime_read_at > GATEWAY_UPTIME_STALE_SECONDS
        ):
            return None
        return self._gateway_uptime_value

    def set_gateway_uptime_enabled(self, enabled: bool) -> None:
        """Poll the uptime register only while its entity is enabled."""

        if self._gateway_uptime_enabled == enabled:
            return
        self._gateway_uptime_enabled = enabled
        if enabled:
            self._gateway_uptime_next_due = 0.0
        elif self._gateway_uptime_task is not None:
            self._gateway_uptime_task.cancel()
        self._schedule_next_tick()

    async def _async_read_gateway_uptime_due(self) -> None:
        """Perform one optional read while the caller holds the gateway lock."""

        if not self._gateway_uptime_enabled or time.monotonic() < self._gateway_uptime_next_due:
            return
        self._gateway_uptime_task = self.hass.async_create_task(
            self.client.async_read_gateway_uptime(), "Meltem gateway uptime"
        )
        try:
            self._gateway_uptime_value = await self._gateway_uptime_task
            self._gateway_uptime_read_at = time.monotonic()
        except asyncio.CancelledError:
            if self._gateway_uptime_enabled:
                raise
        except MeltemModbusError as err:
            _LOGGER.debug("Optional gateway uptime read failed: %s", err)
        finally:
            self._gateway_uptime_task = None
            self._gateway_uptime_next_due = time.monotonic() + GATEWAY_UPTIME_REFRESH_SECONDS

    @property
    def gateway_utc_clock(self) -> datetime | None:
        """Return the gateway clock only while its observation is recent."""

        if self._gateway_utc_clock_read_at is None or (
            time.monotonic() - self._gateway_utc_clock_read_at > GATEWAY_UTC_CLOCK_STALE_SECONDS
        ):
            return None
        return self._gateway_utc_clock_value

    def set_gateway_utc_clock_enabled(self, enabled: bool) -> None:
        """Poll the gateway clock only while its entity is enabled."""

        if self._gateway_utc_clock_enabled == enabled:
            return
        self._gateway_utc_clock_enabled = enabled
        if enabled:
            self._gateway_utc_clock_next_due = 0.0
        elif self._gateway_utc_clock_task is not None:
            self._gateway_utc_clock_task.cancel()
        self._schedule_next_tick()

    async def _async_read_gateway_utc_clock_due(self) -> None:
        """Read the optional clock while the caller holds the gateway lock."""

        if (
            not self._gateway_utc_clock_enabled
            or time.monotonic() < self._gateway_utc_clock_next_due
        ):
            return
        self._gateway_utc_clock_task = self.hass.async_create_task(
            self.client.async_read_gateway_utc_clock(), "Meltem gateway UTC clock"
        )
        try:
            seconds = await self._gateway_utc_clock_task
            self._gateway_utc_clock_value = datetime.fromtimestamp(seconds, tz=UTC)
            self._gateway_utc_clock_read_at = time.monotonic()
        except asyncio.CancelledError:
            if self._gateway_utc_clock_enabled:
                raise
        except MeltemModbusError as err:
            _LOGGER.debug("Optional gateway UTC clock read failed: %s", err)
        finally:
            self._gateway_utc_clock_task = None
            self._gateway_utc_clock_next_due = time.monotonic() + GATEWAY_UTC_CLOCK_REFRESH_SECONDS

    @property
    def gateway_connected_devices(self) -> int | None:
        """Return the gateway's registered device count while it is recent."""

        if self._gateway_connected_devices_read_at is None or (
            time.monotonic() - self._gateway_connected_devices_read_at
            > GATEWAY_CONNECTED_DEVICES_STALE_SECONDS
        ):
            return None
        return self._gateway_connected_devices_value

    def set_gateway_connected_devices_enabled(self, enabled: bool) -> None:
        """Read the gateway's device count only while its entity is active."""

        if self._gateway_connected_devices_enabled == enabled:
            return
        self._gateway_connected_devices_enabled = enabled
        if enabled:
            self._gateway_connected_devices_next_due = 0.0
        elif self._gateway_connected_devices_task is not None:
            self._gateway_connected_devices_task.cancel()
        self._schedule_next_tick()

    async def _async_read_gateway_connected_devices_due(self) -> None:
        """Read registered devices while the caller holds the gateway lock."""

        if (
            not self._gateway_connected_devices_enabled
            or time.monotonic() < self._gateway_connected_devices_next_due
        ):
            return
        self._gateway_connected_devices_task = self.hass.async_create_task(
            self.client.async_read_gateway_node_count(), "Meltem gateway device count"
        )
        try:
            self._gateway_connected_devices_value = await self._gateway_connected_devices_task
            self._gateway_connected_devices_read_at = time.monotonic()
        except asyncio.CancelledError:
            if self._gateway_connected_devices_enabled:
                raise
        except MeltemModbusError as err:
            _LOGGER.debug("Optional gateway device count read failed: %s", err)
        finally:
            self._gateway_connected_devices_task = None
            self._gateway_connected_devices_next_due = (
                time.monotonic() + GATEWAY_CONNECTED_DEVICES_REFRESH_SECONDS
            )

    @property
    def gateway_rf_statistics(self) -> GatewayRfStatistics | None:
        """Return RF statistics only while the observation is recent."""

        if self._gateway_rf_statistics_read_at is None or (
            time.monotonic() - self._gateway_rf_statistics_read_at
            > GATEWAY_RF_STATISTICS_STALE_SECONDS
        ):
            return None
        return self._gateway_rf_statistics_value

    def set_gateway_rf_statistics_enabled(self, key: str, enabled: bool) -> None:
        """Share one optional read among the enabled RF entities."""

        was_enabled = bool(self._gateway_rf_statistics_consumers)
        if enabled:
            self._gateway_rf_statistics_consumers.add(key)
        else:
            self._gateway_rf_statistics_consumers.discard(key)
        is_enabled = bool(self._gateway_rf_statistics_consumers)
        if was_enabled == is_enabled:
            return
        if is_enabled:
            self._gateway_rf_statistics_next_due = 0.0
        elif self._gateway_rf_statistics_task is not None:
            self._gateway_rf_statistics_task.cancel()
        self._schedule_next_tick()

    async def _async_read_gateway_rf_statistics_due(self) -> None:
        """Read RF statistics while the caller holds the gateway lock."""

        if (
            not self._gateway_rf_statistics_consumers
            or time.monotonic() < self._gateway_rf_statistics_next_due
        ):
            return
        self._gateway_rf_statistics_task = self.hass.async_create_task(
            self.client.async_read_gateway_rf_statistics(), "Meltem gateway RF statistics"
        )
        try:
            self._gateway_rf_statistics_value = await self._gateway_rf_statistics_task
            self._gateway_rf_statistics_read_at = time.monotonic()
        except asyncio.CancelledError:
            if self._gateway_rf_statistics_consumers:
                raise
        except MeltemModbusError as err:
            _LOGGER.debug("Optional gateway RF statistics read failed: %s", err)
        finally:
            self._gateway_rf_statistics_task = None
            self._gateway_rf_statistics_next_due = (
                time.monotonic() + GATEWAY_RF_STATISTICS_REFRESH_SECONDS
            )

    def room_available(self, room_key: str) -> bool:
        """Return whether one room still delivers usable data.

        A single unreachable unit must not make the other units look healthy
        while showing frozen values, so availability is tracked per room.
        """

        if self._room_unavailable_reason(room_key) is not None:
            return False
        state = self.safe_data.get(room_key)
        return state is not None and state.has_data

    def _room_unavailable_reason(self, room_key: str) -> str | None:
        """Return why a room is unavailable despite cached data, or ``None``."""

        failures = self._room_failures.get(room_key, 0)
        if failures >= ROOM_UNAVAILABLE_AFTER_FAILURES:
            return f"after {failures} consecutive read failures"

        room = self._rooms_by_key.get(room_key)
        if room is not None:
            silent_for = self.client.seconds_since_successful_read(room.slave)
            if silent_for is not None and silent_for > ROOM_SILENT_AFTER_SECONDS:
                return f"no successful read for more than {ROOM_SILENT_AFTER_SECONDS:.0f} seconds"
        return None

    def room_connectivity(self, room_key: str) -> bool | None:
        """Return unknown until this unit has had a read attempt."""

        state = self.safe_data.get(room_key) or self._startup_states.get(room_key)
        if state is None or not any(health.last_attempt for _, health in state.group_read_health):
            return None
        return self.room_available(room_key)

    def last_successful_room_update(self, room_key: str) -> datetime | None:
        """Return this room's latest successful supported read group."""

        room = self._rooms_by_key[room_key]
        state = self.safe_data.get(room_key, EMPTY_ROOM_STATE)
        return max(
            (
                health.last_successful_read
                for group, health in state.group_read_health
                if health.last_successful_read is not None
                and room.supports_any(READ_GROUP_ENTITY_KEYS[group])
            ),
            default=None,
        )

    def _log_room_availability(
        self, room_key: str, state: RoomState, error: MeltemModbusError | None
    ) -> None:
        """Log once when a room becomes unavailable and once when it recovers.

        Runs after every read job of the room, so availability queries from
        entities, diagnostics, or system health stay free of side effects.
        """

        reason = self._room_unavailable_reason(room_key)
        if reason is not None:
            if room_key not in self._logged_unavailable_rooms:
                self._logged_unavailable_rooms.add(room_key)
                if error is not None:
                    reason = f"{reason}: {error}"
                _LOGGER.info("Meltem room %s unavailable: %s", room_key, reason)
        elif state.has_data and room_key in self._logged_unavailable_rooms:
            self._logged_unavailable_rooms.remove(room_key)
            _LOGGER.info("Meltem room %s reachable again", room_key)

    def optimistic_preset_mode(self, room_key: str) -> str | None:
        """Return the pending preset selection while the gateway confirms it.

        The overlay is shared so the fan and the select entity never disagree.
        """
        state = self.safe_data.get(room_key)
        return self._optimistic_presets.active(
            room_key, state, None if state is None else state.preset_mode
        )

    def optimistic_intensive(self, room_key: str) -> bool | None:
        """Return the pending intensive override while the gateway confirms it."""

        state = self.safe_data.get(room_key)
        return self._optimistic_intensive.active(
            room_key, state, None if state is None else state.intensive_active
        )

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
        if (pending := self._optimistic_levels.active(room_key, state, reported)) is not None:
            return pending, LEVEL_SOURCE_PENDING
        return (reported if reported is not None else (None, None)), source

    def level_write_fallback(self, room_key: str) -> str | None:
        """Return the fallback used by the last fan write until a mode readback follows."""

        fallback = self._level_fallbacks.get(room_key)
        if fallback is None:
            return None
        marker, written_at = fallback
        state = self.safe_data.get(room_key)
        if state is not None and read_health.mode_read_since(state, written_at):
            return None
        return marker

    def _apply_level_fallback(self, room: RoomConfig, level: int, fallback: str | None) -> None:
        """Record the fallback of a fan write that the gateway accepted."""

        if fallback is None:
            if self._level_fallbacks.pop(room.key, None) is not None:
                self.async_update_listeners()
            return
        if fallback == LEVEL_WRITE_FALLBACK_BALANCED:
            _LOGGER.warning(
                "Room %s (slave %s): opposite airflow is unknown; set both "
                "directions to %s m3/h in balanced manual mode",
                room.name,
                room.slave,
                level,
            )
        else:
            _LOGGER.warning(
                "Room %s (slave %s): operating mode was unknown; the fan "
                "command may have overridden it",
                room.name,
                room.slave,
            )
        self._level_fallbacks[room.key] = (fallback, dt_util.utcnow())
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
                    await self._async_read_gateway_uptime_due()
                    await self._async_read_gateway_utc_clock_due()
                    await self._async_read_gateway_connected_devices_due()
                    await self._async_read_gateway_rf_statistics_due()
                    self._schedule_next_tick()
                    return self.safe_data

                # Move the job forward before running it so a failing read
                # cannot get stuck at the front of the queue forever.
                job.next_due = now + self._job_interval(job)
                self._last_read_started = now
                self._last_job_error = None
                self._last_job_link_failure = False
                updated_data = await self._read_one_job(self.safe_data or self._startup_states, job)
                await self._async_read_gateway_uptime_due()
                await self._async_read_gateway_utc_clock_due()
                await self._async_read_gateway_connected_devices_due()
                await self._async_read_gateway_rf_statistics_due()
                never_read = job.never_read(self._rooms_by_key[job.room_key], updated_data)
                if startup or never_read:
                    job.next_due = time.monotonic() + self._job_interval(job, never_read=never_read)
                if not any(state.has_data for state in updated_data.values()):
                    self._startup_states = updated_data
                    raise self._last_job_error or MeltemModbusError(
                        f"No state values received from room {job.room_key} "
                        f"for job {job.key} during startup"
                    )
                self._startup_states = {}
                # _read_one_job swallows transport errors to keep cached state,
                # so success has to be derived from the recorded job failure.
                if self._last_job_link_failure:
                    self._on_transport_failure()
                else:
                    self._on_transport_success()
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
            raise UpdateFailed(
                translation_domain=DOMAIN,
                translation_key="gateway_communication_failed",
            ) from err

    def _select_poll_job(self, now: float) -> tuple[PollJob | None, bool]:
        """Warm up all groups while allowing already-read groups to refresh."""

        if self._startup_jobs is None and not self.safe_data:
            self._startup_jobs = list(self._jobs)
        if self._read_spacing_remaining(now) > 0:
            return None, False
        if self._startup_jobs:
            due = select_due_job((job for job in self._jobs if job not in self._startup_jobs), now)
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

    def _job_interval(self, job: PollJob, *, never_read: bool = False) -> float:
        """Return the job interval, stretched while its unit stays silent."""

        return job.interval(
            elapsed_since_start=time.monotonic() - self._started_at,
            never_read=never_read,
            room_silent=self._room_silent(job.room_key),
        )

    def _room_silent(self, room_key: str) -> bool:
        """Return whether a unit has not answered any read for a long time."""

        room = self._rooms_by_key[room_key]
        silent_for = self.client.seconds_since_successful_read(room.slave)
        if silent_for is None:
            # Never answered since startup.
            silent_for = time.monotonic() - self._started_at
        return silent_for > ROOM_SILENT_AFTER_SECONDS

    def _schedule_next_tick(self) -> None:
        """Sleep until the next job is due instead of waking up on every tick."""

        if self._backoff_seconds is not None:
            return
        seconds = next_tick_seconds(
            self._jobs,
            startup_pending=bool(self._startup_jobs),
            tick_seconds=self._tick_seconds,
            now=time.monotonic(),
            loop_time=self.hass.loop.time(),
        )
        if self._gateway_uptime_enabled:
            loop_time = self.hass.loop.time()
            uptime_delay = max(self._tick_seconds, self._gateway_uptime_next_due - time.monotonic())
            seconds = min(seconds, uptime_delay + loop_time - int(loop_time))
        if self._gateway_utc_clock_enabled:
            loop_time = self.hass.loop.time()
            clock_delay = max(
                self._tick_seconds, self._gateway_utc_clock_next_due - time.monotonic()
            )
            seconds = min(seconds, clock_delay + loop_time - int(loop_time))
        if self._gateway_connected_devices_enabled:
            loop_time = self.hass.loop.time()
            count_delay = max(
                self._tick_seconds, self._gateway_connected_devices_next_due - time.monotonic()
            )
            seconds = min(seconds, count_delay + loop_time - int(loop_time))
        if self._gateway_rf_statistics_consumers:
            loop_time = self.hass.loop.time()
            rf_delay = max(
                self._tick_seconds, self._gateway_rf_statistics_next_due - time.monotonic()
            )
            seconds = min(seconds, rf_delay + loop_time - int(loop_time))
        self.update_interval = timedelta(seconds=seconds)

    def _on_transport_success(self) -> None:
        """Reset failure tracking and any active polling backoff."""

        self._consecutive_transport_failures = 0
        if self._backoff_seconds is None:
            return
        self._backoff_seconds = None
        self._schedule_next_tick()
        _LOGGER.debug("Meltem gateway reachable again, resuming normal polling rate")

    def _on_transport_failure(self) -> None:
        self._consecutive_transport_failures += 1
        self._apply_transport_backoff()

    def _apply_transport_backoff(self) -> None:
        """Slow down polling while the gateway keeps failing."""

        seconds = backoff_seconds(self._consecutive_transport_failures)
        if seconds is None or seconds == self._backoff_seconds:
            return
        if self._backoff_seconds is None:
            _LOGGER.debug(
                "Backing off Meltem gateway polling to %.0f s after %s consecutive "
                "transport failures",
                seconds,
                self._consecutive_transport_failures,
            )
        else:
            _LOGGER.debug(
                "Increasing Meltem gateway polling backoff to %.0f s after %s "
                "consecutive transport failures",
                seconds,
                self._consecutive_transport_failures,
            )
        self._backoff_seconds = seconds
        self.update_interval = timedelta(seconds=seconds)

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

    async def async_set_direction_level(self, room_key: str, direction: str, level: int) -> None:
        """Set one airflow direction and keep the other one where it is.

        Resolve the opposite direction under the same gateway lock as reads
        and mode writes, so every command builds on the latest accepted state.
        """

        room = self._rooms_by_key[room_key]
        async with self._gateway_lock:
            levels, source = self._resolve_levels(room_key)
            if (pending := self._optimistic_levels.get(room_key, None)) is not None:
                levels, source = pending, LEVEL_SOURCE_PENDING
            state = self.safe_data.get(room_key, EMPTY_ROOM_STATE)
            planned, fallback = plan_direction_write(room, state, levels, source, direction, level)
            if isinstance(planned, tuple):
                await self._async_set_unbalanced_levels_locked(room_key, *planned)
            else:
                await self._async_set_level_locked(room_key, planned)
            self._apply_level_fallback(room, level, fallback)

    async def async_set_operation_mode(self, room_key: str, operation_mode: str) -> None:
        """Write a new operating mode for one room and refresh afterwards."""

        async with self._gateway_lock:
            await self._async_set_operation_mode_locked(room_key, operation_mode)

    async def _async_set_operation_mode_locked(self, room_key: str, operation_mode: str) -> None:
        """Resolve the airflow and write a mode while holding the gateway lock."""

        room = self._rooms_by_key[room_key]
        state = self.safe_data.get(room.key, EMPTY_ROOM_STATE)
        if operation_mode == OPERATION_MODE_INACTIVE:
            if state.operation_mode in DIRECT_OPERATION_MODES and read_health.group_fresh(
                state, "flow_control"
            ):
                return
            operation_mode = OPERATION_MODE_MANUAL
        balanced_level, extract_level = mode_write_levels(state, self.effective_levels(room_key))
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
        if write_key.startswith(CONTROL_SETTING_WRITE_PREFIX) and isinstance(expected_value, int):
            self._writes.check_control_level_range(
                room,
                self.safe_data.get(room.key, EMPTY_ROOM_STATE),
                write_key.removeprefix(CONTROL_SETTING_WRITE_PREFIX),
                expected_value,
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
            _LOGGER.warning("Failed to send the command to %s: %s", room.name, err)
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="write_failed",
                translation_placeholders={"unit": room.name},
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
            write_key=write_key,
        )
        if self._writes.mark_unconfirmed(
            room.key, write_key, "No matching device readback was received"
        ):
            self.async_update_listeners()

    def update_request_rate(self, max_requests_per_second: float) -> None:
        """Retune the scheduler and shared read limit without reloading.

        The wake-up that is already scheduled keeps its time, so the new rate
        applies from the next one, at most one poll interval away.
        """

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
        write_key: str | None = None,
    ) -> None:
        """Refresh one room's affected group after a write settles."""

        attempts = min(max(1, min_refresh_attempts), POST_WRITE_REFRESH_RETRIES + 1)
        for attempt in range(attempts):
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

            if not failed and refresh_plan.refresh_airflow:
                refreshed_room = await self._read_balanced_target_if_pending(
                    room, refreshed_room, previous_state
                )

            updated_states = {**self.safe_data, room.key: refreshed_room}
            if refreshed_room != previous_state:
                self.async_set_updated_data(updated_states)
            self._confirm_pending_writes(updated_states)

            confirmed = write_key is not None and self._writes.is_confirmed(room.key, write_key)
            if failed or confirmed or attempt + 1 >= attempts:
                return

            await async_sleep(POST_WRITE_REFRESH_INTERVAL_SECONDS)

    async def _read_balanced_target_if_pending(
        self,
        room: RoomConfig,
        state: RoomState,
        previous_state: RoomState = EMPTY_ROOM_STATE,
    ) -> RoomState:
        """Read the exact set register for a fresh, still-confirmable write."""

        state, should_read = self._writes.prepare_balanced_target_readback(
            room.key, state, previous_state
        )
        if not should_read:
            return state

        await self._async_wait_for_read_slot()
        try:
            target = await self.client.read_balanced_target_readback(room)
        except MeltemModbusError as err:
            # A unit can refuse the set register for a while; one warning per
            # failing streak keeps every later airflow poll out of the log.
            level = logging.DEBUG if room.key in self._target_read_failing else logging.WARNING
            self._target_read_failing.add(room.key)
            _LOGGER.log(
                level,
                "Could not read set-side airflow target register %s for room %s "
                "while confirming a write: %s",
                REGISTER_CURRENT_LEVEL,
                room.key,
                err,
            )
            return state
        self._target_read_failing.discard(room.key)
        if target is None:
            _LOGGER.debug(
                "Register %s of room %s holds no raw airflow target; the write stays unconfirmed",
                REGISTER_CURRENT_LEVEL,
                room.key,
            )
            return state
        return replace(state, balanced_target_readback=target)

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

        return read_health.data_health_stale(
            self.safe_data.get(room_key),
            writes_unhealthy=self._writes.unhealthy(room_key),
            has_writes=self._writes.has_any(room_key),
        )

    def data_health_attributes(self, room_key: str) -> dict[str, dict[str, object]]:
        """Return per-group read health and write outcomes for one room."""

        return read_health.data_health_attributes(
            self._rooms_by_key[room_key],
            self.safe_data.get(room_key, EMPTY_ROOM_STATE),
            self._writes.attributes(room_key),
        )

    def _confirm_pending_writes(
        self, states: Mapping[str, RoomState], *, notify: bool = True
    ) -> None:
        """Judge pending writes and optimistic values by the latest readbacks."""

        changed = self._writes.confirm(states)
        for room_key, state in states.items():
            reported, _source = reported_levels(state)
            changed |= self._optimistic_levels.settle_if_read(room_key, state, reported)
            changed |= self._optimistic_presets.settle_if_read(room_key, state, state.preset_mode)
            changed |= self._optimistic_intensive.settle_if_read(
                room_key, state, state.intensive_active
            )
            fallback = self._level_fallbacks.get(room_key)
            if fallback is not None and read_health.mode_read_since(state, fallback[1]):
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
        error: MeltemModbusError | None = None
        read_started_at = dt_util.utcnow()

        try:
            refreshed_state = await self.client.read_room_state(
                room,
                previous_state,
                job.refresh_plan,
            )
        except MeltemModbusError as err:
            _LOGGER.debug("Failed to read room %s for job %s: %s", room.key, job.key, err)
            error = self._last_job_error = err
            self._last_job_link_failure = True
            self._room_failures[room.key] = self._room_failures.get(room.key, 0) + 1
            refreshed_state = read_health.record_read_failures(
                previous_state,
                room,
                job.refresh_plan,
                err,
            )
        else:
            failure = read_health.job_read_failure(
                room, refreshed_state, job.refresh_plan, read_started_at
            )
            error = self._last_job_error = None if failure is None else failure.error
            self._last_job_link_failure = failure is not None and failure.link_failure
            if failure is not None:
                _LOGGER.debug("Failed to read room %s for job %s: %s", room.key, job.key, error)
                optional_rejection = not failure.link_failure and all(
                    group in {"rf_last_seen", "rf_signal", "rf_fault", "value_error", "fan_hours"}
                    for group in job.refresh_plan.read_groups()
                    if room.supports_any(READ_GROUP_ENTITY_KEYS[group])
                )
                if not optional_rejection:
                    self._room_failures[room.key] = self._room_failures.get(room.key, 0) + 1
            else:
                self._room_failures.pop(room.key, None)
                if job.refresh_plan.refresh_airflow:
                    refreshed_state = await self._read_balanced_target_if_pending(
                        room, refreshed_state, previous_state
                    )

        self._log_room_availability(room.key, refreshed_state, error)
        if refreshed_state == previous_state:
            return previous_states
        return {**previous_states, room.key: refreshed_state}
