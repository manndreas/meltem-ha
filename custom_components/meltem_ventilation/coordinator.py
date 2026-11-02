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
from collections.abc import Awaitable, Callable, Iterable, Mapping
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from types import MappingProxyType
from typing import Any, Protocol

from homeassistant.core import CALLBACK_TYPE, HassJob, HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.event import async_call_later, async_track_time_interval
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from . import read_health
from .const import (
    CONTROL_SETTING_SETTLE_SECONDS,
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
    OPERATION_MODE_OFF,
    OPERATION_MODE_UNBALANCED,
    OPTIONAL_DIAGNOSTIC_READ_GROUPS,
    PRESET_MODE_EXTRACT_ONLY,
    PRESET_MODE_INACTIVE,
    PRESET_MODE_INTENSIVE,
    PRESET_MODE_SUPPLY_ONLY,
    READ_GROUP_ENTITY_KEYS,
    REGISTER_CURRENT_LEVEL,
    TARGET_OPTIMISTIC_SECONDS,
    WRITE_SETTLE_SECONDS,
    LevelSource,
    LevelWriteFallback,
)
from .cumulative_diagnostics import CumulativeDiagnostics
from .gateway_reads import OptionalGatewayRead
from .levels import (
    LevelPair,
    levels_reached,
    mode_write_levels,
    plan_direction_write,
    reported_levels,
)
from .missing_sensors import MissingSensorIssues
from .modbus_helpers import MeltemModbusError
from .models import (
    EMPTY_ROOM_STATE,
    GatewayRfStatistics,
    MeltemIntegrationConfigEntry,
    ReadHealth,
    RefreshPlan,
    RoomConfig,
    RoomRead,
    RoomReadError,
    RoomState,
    UnitProbeResult,
    WriteStatus,
    WriteValue,
)
from .new_units import (
    NEW_UNIT_CHECK_FIRST_DELAY_SECONDS,
    NEW_UNIT_CHECK_SECONDS,
    NewUnitIssue,
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
from .room_runtime import RoomRuntime
from .write_confirmation import CONTROL_SETTING_WRITE_PREFIX, WriteConfirmations, write_group

_LOGGER = logging.getLogger(__name__)
async_sleep = asyncio.sleep

ROOM_UNAVAILABLE_AFTER_FAILURES = 3
# The slowest job runs hourly, but the airflow job polls every 10 s, so a unit
# that answers nothing for this long is genuinely silent.
ROOM_SILENT_AFTER_SECONDS = 120.0
PRESET_OPTIMISTIC_SECONDS = 15.0
# How often diagnostics that age without a read, such as connectivity, refresh.
LOCAL_DIAGNOSTIC_REFRESH_INTERVAL = timedelta(seconds=10)

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
    async def async_disconnect_gateway(self) -> None: ...

    async def async_read_gateway_uptime(self) -> int: ...

    async def async_read_gateway_mac(self) -> str: ...

    async def async_read_gateway_utc_clock(self) -> int: ...

    async def async_read_gateway_node_count(self) -> int: ...

    async def async_read_gateway_rf_statistics(self) -> GatewayRfStatistics: ...

    async def async_read_room_app_setup_code(self, room: RoomConfig) -> str | None: ...

    def seconds_since_successful_read(self, slave: int) -> float | None: ...

    def transport_diagnostics(self) -> dict[str, float | int | None]: ...

    async def discover_gateway_units(self, start: int, end: int) -> list[int]: ...

    async def probe_slave_details(self, slave: int) -> UnitProbeResult: ...

    async def read_room(
        self, room: RoomConfig, previous_state: RoomState, refresh_plan: RefreshPlan
    ) -> RoomRead: ...

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
        cumulative_diagnostics: CumulativeDiagnostics | None = None,
    ) -> None:
        self.client = client
        self.rooms = rooms
        self.cumulative_diagnostics = cumulative_diagnostics
        self._entry = config_entry
        self._entry_id = config_entry.entry_id
        self._rooms_by_key = {room.key: room for room in rooms}
        self._runtimes = {room.key: RoomRuntime(room) for room in rooms}
        self._tick_seconds = 1.0 / max(0.1, max_requests_per_second)
        self._gateway_lock = asyncio.Lock()
        self._last_job_error: MeltemModbusError | None = None
        # Only timeouts and lost links count towards the gateway backoff; a unit
        # that answers with an exception response proves the link works.
        self._last_job_link_failure = False
        self._last_job_attempted = False
        self._consecutive_transport_failures = 0
        self._backoff_seconds: float | None = None
        # Outcome of each write per unit, judged by the readbacks after it.
        self.write_confirmations = WriteConfirmations(on_issue=self._record_write_issue)
        self.missing_sensors = MissingSensorIssues(
            hass, config_entry.entry_id, self._unit_display_name
        )
        self.missing_sensors.restore(rooms)
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
        self._started_at = time.monotonic()
        self._last_read_started: float | None = None
        # Jobs are precomputed once and then executed in a due-time round robin.
        self.poll_jobs: list[PollJob] = build_jobs(rooms, time.monotonic())
        self._startup_jobs: list[PollJob] | None = None
        self._startup_states: dict[str, RoomState] = {}
        self._startup_turn = True
        # Each read resolves the client method on call, so a replaced client is used.
        self._gateway_uptime = OptionalGatewayRead[int](
            hass,
            "uptime",
            lambda: self.client.async_read_gateway_uptime(),
            refresh_seconds=GATEWAY_UPTIME_REFRESH_SECONDS,
            stale_seconds=GATEWAY_UPTIME_STALE_SECONDS,
        )
        self._gateway_utc_clock = OptionalGatewayRead[datetime](
            hass,
            "UTC clock",
            self._async_read_gateway_utc_clock,
            refresh_seconds=GATEWAY_UTC_CLOCK_REFRESH_SECONDS,
            stale_seconds=GATEWAY_UTC_CLOCK_STALE_SECONDS,
        )
        self._gateway_connected_devices = OptionalGatewayRead[int](
            hass,
            "device count",
            lambda: self.client.async_read_gateway_node_count(),
            refresh_seconds=GATEWAY_CONNECTED_DEVICES_REFRESH_SECONDS,
            stale_seconds=GATEWAY_CONNECTED_DEVICES_STALE_SECONDS,
        )
        self._gateway_rf_statistics = OptionalGatewayRead[GatewayRfStatistics](
            hass,
            "RF statistics",
            lambda: self.client.async_read_gateway_rf_statistics(),
            refresh_seconds=GATEWAY_RF_STATISTICS_REFRESH_SECONDS,
            stale_seconds=GATEWAY_RF_STATISTICS_STALE_SECONDS,
        )
        # Not tied to an entity: it raises a repair issue for units not set up yet.
        self.new_unit_issue = NewUnitIssue(hass, config_entry.entry_id)
        self._gateway_units = OptionalGatewayRead[list[int]](
            hass,
            "unit list",
            lambda: self.client.discover_gateway_units(
                DEFAULT_SCAN_SLAVE_START, DEFAULT_SCAN_SLAVE_END
            ),
            refresh_seconds=NEW_UNIT_CHECK_SECONDS,
            stale_seconds=2 * NEW_UNIT_CHECK_SECONDS,
            first_read_delay=NEW_UNIT_CHECK_FIRST_DELAY_SECONDS,
            on_value=lambda units: self.new_unit_issue.update(
                units, (room.slave for room in self.rooms)
            ),
        )
        self._gateway_units.set_enabled("new_unit_issue", True)
        # Read in this order in every poll tick.
        self.optional_gateway_reads: Mapping[str, OptionalGatewayRead[Any]] = MappingProxyType(
            {
                "uptime": self._gateway_uptime,
                "utc_clock": self._gateway_utc_clock,
                "connected_devices": self._gateway_connected_devices,
                "rf_statistics": self._gateway_rf_statistics,
                "unit_list": self._gateway_units,
            }
        )

        super().__init__(
            hass,
            _LOGGER,
            config_entry=config_entry,
            name="Meltem Modbus",
            # Every poll tick schedules the next one itself, see async_poll.
            update_interval=None,
        )
        # Units whose entities the next listener update concerns; ``None`` means all.
        self._updated_rooms: frozenset[str] | None = None
        self._diagnostic_listeners: dict[object, CALLBACK_TYPE] = {}
        self._remove_diagnostic_timer: CALLBACK_TYPE | None = None
        self._next_poll_delay = self._tick_seconds
        self._cancel_poll_timer: CALLBACK_TYPE | None = None
        self._poll_running = False
        self._polling_stopped = False

    def room_updated(self, room_key: str) -> bool:
        """Return whether the current listener update can concern one unit."""

        return self._updated_rooms is None or room_key in self._updated_rooms

    @callback
    def async_update_listeners(self) -> None:
        try:
            super().async_update_listeners()
        finally:
            self._updated_rooms = None

    def _limit_next_update_to(self, room_keys: Iterable[str]) -> None:
        """Let the next listener update skip the entities of every other unit.

        After a failed update every entity changes availability, so the
        update stays complete then.
        """

        if self.last_update_success:
            self._updated_rooms = frozenset(room_keys)

    @property
    def next_poll_delay(self) -> float:
        """Return the seconds the scheduler waits after the current poll tick."""

        return self._next_poll_delay

    @property
    def poll_scheduled(self) -> bool:
        """Return whether a poll tick is waiting for its time."""

        return self._cancel_poll_timer is not None

    async def async_poll(self) -> None:
        """Run one poll tick now and schedule the next one.

        The coordinator plans its own wake-ups instead of handing an
        ``update_interval`` to Home Assistant, so a new request rate or a due
        optional read takes effect right away and the wake-up time is exact.
        """

        if self._polling_stopped or self.hass.is_stopping:
            # Home Assistant does not unload entries when it stops.
            return
        self._async_cancel_poll_timer()
        self._poll_running = True
        try:
            await self.async_refresh()
        finally:
            self._poll_running = False
            self._async_schedule_poll(self._next_poll_delay)

    @callback
    def _async_schedule_poll(self, delay: float) -> None:
        self._async_cancel_poll_timer()
        # "Enable polling for updates" off leaves refreshes to the user, as with
        # the update interval of a DataUpdateCoordinator.
        if self._polling_stopped or self.hass.is_stopping or self._entry.pref_disable_polling:
            return
        self._cancel_poll_timer = async_call_later(
            self.hass,
            max(0.0, delay),
            HassJob(self._async_poll_due, "Meltem poll tick", cancel_on_shutdown=True),
        )

    @callback
    def _async_poll_due(self, _now: datetime) -> None:
        self._cancel_poll_timer = None
        # Unloading the entry cancels a tick that is still running.
        self._entry.async_create_background_task(self.hass, self.async_poll(), "Meltem poll tick")

    @callback
    def _async_cancel_poll_timer(self) -> None:
        if self._cancel_poll_timer is not None:
            self._cancel_poll_timer()
            self._cancel_poll_timer = None

    @callback
    def _async_set_next_poll_delay(self, seconds: float) -> None:
        """Plan the next tick; a waiting tick moves to the new time at once."""

        self._next_poll_delay = seconds
        if not self._poll_running and self._cancel_poll_timer is not None:
            self._async_schedule_poll(seconds)

    @callback
    def async_add_diagnostic_listener(self, update_callback: CALLBACK_TYPE) -> CALLBACK_TYPE:
        """Call ``update_callback`` periodically; all callers share one timer.

        Time-dependent diagnostics change without a read, for example during an
        outage. The timer runs while at least one listener is registered.
        """

        key = object()
        self._diagnostic_listeners[key] = update_callback
        if self._remove_diagnostic_timer is None:
            self._remove_diagnostic_timer = async_track_time_interval(
                self.hass,
                self._async_refresh_diagnostics,
                LOCAL_DIAGNOSTIC_REFRESH_INTERVAL,
                cancel_on_shutdown=True,
            )

        @callback
        def remove_listener() -> None:
            self._diagnostic_listeners.pop(key, None)
            if not self._diagnostic_listeners and self._remove_diagnostic_timer is not None:
                self._remove_diagnostic_timer()
                self._remove_diagnostic_timer = None

        return remove_listener

    @callback
    def _async_refresh_diagnostics(self, _now: datetime) -> None:
        for update_callback in tuple(self._diagnostic_listeners.values()):
            update_callback()

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
    def backoff_seconds(self) -> float | None:
        """Return the polling interval while the gateway keeps failing, else ``None``."""

        return self._backoff_seconds

    @property
    def last_room_errors(self) -> Mapping[str, RoomReadError]:
        """Return the most recent failed read of each unit, including recovered ones."""

        return MappingProxyType(
            {
                room_key: runtime.last_read_error
                for room_key, runtime in self._runtimes.items()
                if runtime.last_read_error is not None
            }
        )

    @property
    def entry_id(self) -> str:
        """Return the ID of the config entry this coordinator serves."""

        return self._entry_id

    def room_runtime(self, room_key: str) -> RoomRuntime:
        """Return the coordinator bookkeeping of one unit."""

        return self._runtimes[room_key]

    @property
    def gateway_device_id(self) -> str | None:
        """Return the registry id of the gateway device the units hang off."""

        device = dr.async_get(self.hass).async_get_device_by_identifier(
            (DOMAIN, self._entry_id), self._entry_id
        )
        return device.id if device is not None else None

    def _unit_display_name(self, room: RoomConfig) -> str:
        """Return the name the user sees for a unit's device."""

        device = dr.async_get(self.hass).async_get_device_by_identifier(
            (DOMAIN, room.key), self._entry_id
        )
        if device is not None and (name := device.name_by_user or device.name):
            return name
        return room.name

    async def async_read_room_app_setup_code(self, room_key: str) -> str | None:
        """Read a unit's app setup code under the shared gateway lock."""

        async with self._gateway_lock:
            return await self.client.async_read_room_app_setup_code(self._rooms_by_key[room_key])

    async def async_read_gateway_mac(self) -> str:
        """Read the gateway MAC address under the shared gateway lock."""

        async with self._gateway_lock:
            return await self.client.async_read_gateway_mac()

    async def async_validate_replacement(self, validate: Callable[[], Awaitable[None]]) -> None:
        """Validate a replacement while polling cannot reopen the old link."""

        async with self._gateway_lock:
            await self.client.async_disconnect_gateway()
            await validate()

    async def async_shutdown(self) -> None:
        self._polling_stopped = True
        self._async_cancel_poll_timer()
        for read in self.optional_gateway_reads.values():
            for consumer in read.consumers:
                read.set_enabled(consumer, False)
        for overlay in self._overlays.values():
            overlay.shutdown()
        # Repair issues stay: deleting them would forget that the user ignored them.
        await super().async_shutdown()
        if self.cumulative_diagnostics is not None:
            await self.cumulative_diagnostics.async_flush()

    @property
    def gateway_uptime(self) -> int | None:
        """Return the last raw uptime only while its observation is recent."""

        return self._gateway_uptime.value

    def set_gateway_uptime_enabled(self, enabled: bool) -> None:
        """Poll the uptime register only while its entity is enabled."""

        self._set_optional_read_enabled(self._gateway_uptime, "uptime", enabled)

    @property
    def gateway_utc_clock(self) -> datetime | None:
        """Return the gateway clock only while its observation is recent."""

        return self._gateway_utc_clock.value

    def set_gateway_utc_clock_enabled(self, enabled: bool) -> None:
        """Poll the gateway clock only while its entity is enabled."""

        self._set_optional_read_enabled(self._gateway_utc_clock, "utc_clock", enabled)

    async def _async_read_gateway_utc_clock(self) -> datetime:
        seconds = await self.client.async_read_gateway_utc_clock()
        return datetime.fromtimestamp(seconds, tz=UTC)

    @property
    def gateway_connected_devices(self) -> int | None:
        """Return the gateway's registered device count while it is recent."""

        return self._gateway_connected_devices.value

    def set_gateway_connected_devices_enabled(self, enabled: bool) -> None:
        """Read the gateway's device count only while its entity is active."""

        self._set_optional_read_enabled(
            self._gateway_connected_devices, "connected_devices", enabled
        )

    @property
    def gateway_rf_statistics(self) -> GatewayRfStatistics | None:
        """Return RF statistics only while the observation is recent."""

        return self._gateway_rf_statistics.value

    def set_gateway_rf_statistics_enabled(self, key: str, enabled: bool) -> None:
        """Share one optional read among the enabled RF entities."""

        self._set_optional_read_enabled(self._gateway_rf_statistics, key, enabled)

    def _set_optional_read_enabled(
        self, read: OptionalGatewayRead[Any], consumer: str, enabled: bool
    ) -> None:
        if read.set_enabled(consumer, enabled):
            self._schedule_next_tick()

    async def _async_read_optional_gateway_values_due(self) -> None:
        """Read optional gateway values in order while holding the gateway lock."""

        for read in self.optional_gateway_reads.values():
            if await read.async_read_if_due():
                self._last_read_started = time.monotonic()

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
        """Return why a room is unavailable despite cached data, or ``None``.

        A single unanswered request only pauses reads of the unit; its cached
        values stay valid, so availability waits for a run of failed jobs or a
        long silence instead of flapping on one lost telegram.
        """

        runtime = self._runtimes[room_key]
        if runtime.read_failures >= ROOM_UNAVAILABLE_AFTER_FAILURES:
            return f"after {runtime.read_failures} consecutive read failures"
        silent_for = self.client.seconds_since_successful_read(runtime.room.slave)
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

        runtime = self._runtimes[room_key]
        reason = self._room_unavailable_reason(room_key)
        if reason is not None:
            if not runtime.unavailable_logged:
                runtime.unavailable_logged = True
                if error is not None:
                    reason = f"{reason}: {error}"
                _LOGGER.info("Meltem room %s unavailable: %s", room_key, reason)
        elif state.has_data and runtime.unavailable_logged:
            runtime.unavailable_logged = False
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

    def level_source(self, room_key: str) -> LevelSource | None:
        """Return whether the effective levels are pending, targets, or measurements."""

        return self._resolve_levels(room_key)[1]

    def _resolve_levels(self, room_key: str) -> tuple[LevelPair, LevelSource | None]:
        """Return the effective supply/extract pair and where it comes from."""

        if self._flow_write_uncertain(room_key):
            return (None, None), None
        state = self.safe_data.get(room_key)
        reported, source = (None, None) if state is None else reported_levels(state)
        if (pending := self._optimistic_levels.active(room_key, state, reported)) is not None:
            return pending, LEVEL_SOURCE_PENDING
        return (reported if reported is not None else (None, None)), source

    def _flow_write_uncertain(self, room_key: str) -> bool:
        runtime = self._runtimes[room_key]
        if runtime.uncertain_flow is None:
            return False
        state = self.safe_data.get(room_key)
        if state is not None and read_health.read_since(
            state, "flow_control", runtime.uncertain_flow
        ):
            runtime.uncertain_flow = None
            return False
        return True

    def _group_health(self, room_key: str, group_key: str) -> ReadHealth:
        """Return a group's current health, the baseline for ordering a write."""

        state = self.safe_data.get(room_key) or self._startup_states.get(room_key)
        return (state or EMPTY_ROOM_STATE).read_health_for(group_key)

    def level_write_fallback(self, room_key: str) -> LevelWriteFallback | None:
        """Return the fallback used by the last fan write until a mode readback follows."""

        fallback = self._runtimes[room_key].level_fallback
        if fallback is None:
            return None
        marker, baseline = fallback
        state = self.safe_data.get(room_key)
        if state is not None and read_health.mode_read_since(state, baseline):
            return None
        return marker

    def _apply_level_fallback(
        self, room: RoomConfig, level: int, fallback: LevelWriteFallback | None
    ) -> None:
        """Record the fallback of a fan write that the gateway accepted."""

        runtime = self._runtimes[room.key]
        if fallback is None:
            if runtime.level_fallback is not None:
                runtime.level_fallback = None
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
        runtime.level_fallback = (fallback, self._group_health(room.key, "flow_control"))
        self.async_update_listeners()

    def read_group_fresh(self, room_key: str, group_key: str) -> bool:
        """Return whether the group's most recent attempt succeeded recently."""

        state = self.safe_data.get(room_key)
        return state is not None and read_health.group_fresh(state, group_key)

    async def _async_update_data(self) -> dict[str, RoomState]:
        startup_without_values = False
        self._updated_rooms = None
        try:
            async with self._gateway_lock:
                now = time.monotonic()
                job, startup = self._select_poll_job(now)
                if job is None:
                    await self._async_read_optional_gateway_values_due()
                    self._schedule_next_tick()
                    if self._startup_states:
                        raise UpdateFailed(
                            translation_domain=DOMAIN,
                            translation_key="gateway_communication_failed",
                        ) from self._last_job_error
                    # No unit was read, so only gateway entities can change.
                    self._limit_next_update_to(())
                    return self.safe_data

                # Move the job forward before running it so a failing read
                # cannot get stuck at the front of the queue forever.
                job.next_due = now + self._job_interval(job)
                self._last_read_started = now
                self._last_job_error = None
                self._last_job_link_failure = False
                self._last_job_attempted = False
                updated_data = await self._read_one_job(self.safe_data or self._startup_states, job)
                await self._async_read_optional_gateway_values_due()
                never_read = job.never_read(self._rooms_by_key[job.room_key], updated_data)
                if startup or never_read:
                    job.next_due = time.monotonic() + self._job_interval(job, never_read=never_read)
                if not any(state.has_data for state in updated_data.values()):
                    self._startup_states = updated_data
                    startup_without_values = True
                    raise self._last_job_error or MeltemModbusError(
                        f"No state values received from room {job.room_key} "
                        f"for job {job.key} during startup"
                    )
                self._startup_states = {}
                # _read_one_job swallows transport errors to keep cached state,
                # so success has to be derived from the recorded job failure.
                if self._last_job_link_failure:
                    self._on_transport_failure()
                elif self._last_job_attempted:
                    self._on_transport_success()
                # The coordinator notifies the listeners once the new data is set.
                self._confirm_pending_writes(updated_data, notify=False)
                self._schedule_next_tick()
                self._limit_next_update_to((job.room_key,))
                return updated_data
        except MeltemModbusError as err:
            # Before any state arrives, an unsuccessful job is a startup failure.
            if not startup_without_values or self._last_job_link_failure:
                self._on_transport_failure()
            elif self._last_job_attempted:
                self._on_transport_success()
            if self._backoff_seconds is None:
                # Retrying at the request rate would hammer a gateway that is down.
                self._async_set_next_poll_delay(TRANSPORT_BACKOFF_START_SECONDS)
            raise UpdateFailed(
                translation_domain=DOMAIN,
                translation_key="gateway_communication_failed",
            ) from err

    def _select_poll_job(self, now: float) -> tuple[PollJob | None, bool]:
        """Warm up all groups while allowing already-read groups to refresh."""

        if self._startup_jobs is None and not self.safe_data:
            self._startup_jobs = list(self.poll_jobs)
        if self._read_spacing_remaining(now) > 0:
            return None, False
        if self._startup_jobs:
            due = select_due_job(
                (job for job in self.poll_jobs if job not in self._startup_jobs), now
            )
            if due is None or self._startup_turn:
                self._startup_turn = False
                return self._startup_jobs.pop(0), True
            self._startup_turn = True
            return due, False
        return select_due_job(self.poll_jobs, now), False

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
            self.poll_jobs,
            startup_pending=bool(self._startup_jobs),
            tick_seconds=self._tick_seconds,
            now=time.monotonic(),
        )
        for read in self.optional_gateway_reads.values():
            if (due_in := read.seconds_until_due()) is not None:
                seconds = min(seconds, max(self._tick_seconds, due_in))
        self._async_set_next_poll_delay(seconds)

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
        self._async_set_next_poll_delay(seconds)

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
        if level == 0:
            self._optimistic_intensive.clear(room_key)
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
            if self._flow_write_uncertain(room_key):
                raise HomeAssistantError(
                    translation_domain=DOMAIN,
                    translation_key="airflow_unknown",
                    translation_placeholders={"unit": room.name},
                )
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
        if self._flow_write_uncertain(room_key):
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="airflow_unknown",
                translation_placeholders={"unit": room.name},
            )
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

        if operation_mode == OPERATION_MODE_OFF:
            self._optimistic_intensive.clear(room_key)
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
            read_back=True,
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
                read_back=True,
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
            read_back=True,
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
            read_back=True,
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
            read_back=True,
            use_write_result=True,
        )

    async def _async_write_with_confirmation(
        self,
        room: RoomConfig,
        write_key: str,
        expected_value: WriteValue,
        write_method: Callable[..., Awaitable[object]],
        *write_args: object,
        read_back: bool = False,
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
                read_back=read_back,
                use_write_result=use_write_result,
            )

    async def _async_write_with_confirmation_locked(
        self,
        room: RoomConfig,
        write_key: str,
        expected_value: WriteValue,
        write_method: Callable[..., Awaitable[object]],
        *write_args: object,
        read_back: bool = False,
        use_write_result: bool = False,
    ) -> None:
        """Write and verify one setting while holding the gateway lock.

        Without ``read_back`` the regular poll jobs confirm the write.
        """

        readback_plan = _READBACK_PLANS[write_group(write_key)]
        overlay = self._overlays.get(write_key)
        # Only a read after the write changes this, regardless of wall-clock jumps.
        baseline = self._group_health(room.key, write_group(write_key))
        if write_key.startswith(CONTROL_SETTING_WRITE_PREFIX) and isinstance(expected_value, int):
            self.write_confirmations.check_control_level_range(
                room,
                self.safe_data.get(room.key, EMPTY_ROOM_STATE),
                write_key.removeprefix(CONTROL_SETTING_WRITE_PREFIX),
                expected_value,
            )
        try:
            write_result = await write_method(*write_args)
        except asyncio.CancelledError:
            if write_group(write_key) == "flow_control":
                self._runtimes[room.key].uncertain_flow = baseline
                for previous_key, flow_overlay in self._overlays.items():
                    if write_group(previous_key) == "flow_control":
                        flow_overlay.clear(room.key)
            elif overlay is not None:
                overlay.clear(room.key)
            self.write_confirmations.supersede(room.key, write_key)
            self.write_confirmations.record_pending(
                room.key, write_key, expected_value, baseline=baseline
            )
            self.write_confirmations.mark_unconfirmed(room.key, write_key, "Write was cancelled")
            self.async_update_listeners()
            raise
        except (MeltemModbusError, HomeAssistantError) as err:
            # A failed write makes any earlier pending value doubtful.
            if overlay is not None:
                overlay.clear(room.key)
            self.write_confirmations.record_pending(
                room.key, write_key, expected_value, baseline=baseline
            )
            self.write_confirmations.set_status(room.key, write_key, "failed", error=str(err))
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
        superseded = self.write_confirmations.supersede(room.key, write_key)
        self.write_confirmations.record_pending(
            room.key, write_key, confirmed_expected, baseline=baseline
        )
        for previous_key in superseded:
            if previous_overlay := self._overlays.get(previous_key):
                previous_overlay.clear(room.key)
        if overlay is not None:
            overlay.set(room.key, expected_value)
        else:
            self.async_update_listeners()

        if not read_back:
            return

        await async_sleep(
            CONTROL_SETTING_SETTLE_SECONDS
            if write_group(write_key) == "control_settings"
            else WRITE_SETTLE_SECONDS
        )
        await self._async_refresh_room_after_write(room, refresh_plan=readback_plan)
        if self.write_confirmations.mark_unconfirmed(
            room.key, write_key, "No matching device readback was received"
        ):
            self.async_update_listeners()

    def update_request_rate(self, max_requests_per_second: float) -> None:
        """Retune the scheduler and shared read limit without reloading.

        A tick that is already waiting moves to the time the new rate gives.
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
    ) -> UnitProbeResult:
        """Probe one unit using the active gateway client."""

        async with self._gateway_lock:
            return await self.client.probe_slave_details(slave)

    async def _async_refresh_room_after_write(
        self,
        room: RoomConfig,
        *,
        refresh_plan: RefreshPlan = AIRFLOW_REFRESH_PLAN,
    ) -> None:
        """Read one room's affected group once after a write settles."""

        await self._async_wait_for_read_slot()
        previous_state = self.safe_data.get(room.key, EMPTY_ROOM_STATE)
        failed = False
        try:
            refreshed_room = (await self.client.read_room(room, previous_state, refresh_plan)).state
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
            self._limit_next_update_to((room.key,))
            self.async_set_updated_data(updated_states)
        self._confirm_pending_writes(updated_states)

    async def _read_balanced_target_if_pending(
        self,
        room: RoomConfig,
        state: RoomState,
        previous_state: RoomState = EMPTY_ROOM_STATE,
    ) -> RoomState:
        """Read the exact set register for a fresh, still-confirmable write."""

        state, should_read = self.write_confirmations.prepare_balanced_target_readback(
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
            runtime = self._runtimes[room.key]
            level = logging.DEBUG if runtime.target_read_failing else logging.WARNING
            runtime.target_read_failing = True
            _LOGGER.log(
                level,
                "Could not read set-side airflow target register %s for room %s "
                "while confirming a write: %s",
                REGISTER_CURRENT_LEVEL,
                room.key,
                err,
            )
            return state
        self._runtimes[room.key].target_read_failing = False
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
            writes_unhealthy=self.write_confirmations.unhealthy(room_key),
            has_writes=self.write_confirmations.has_any(room_key),
        )

    def data_health_attributes(self, room_key: str) -> dict[str, dict[str, object]]:
        """Return per-group read health and write outcomes for one room."""

        return read_health.data_health_attributes(
            self._rooms_by_key[room_key],
            self.safe_data.get(room_key, EMPTY_ROOM_STATE),
            self.write_confirmations.attributes(room_key),
        )

    def _confirm_pending_writes(
        self, states: Mapping[str, RoomState], *, notify: bool = True
    ) -> None:
        """Judge pending writes and optimistic values by the latest readbacks."""

        changed = self.write_confirmations.confirm(states)
        for room_key, state in states.items():
            reported, _source = reported_levels(state)
            changed |= self._optimistic_levels.settle_if_read(room_key, state, reported)
            changed |= self._optimistic_presets.settle_if_read(room_key, state, state.preset_mode)
            changed |= self._optimistic_intensive.settle_if_read(
                room_key, state, state.intensive_active
            )
            runtime = self._runtimes.get(room_key)
            fallback = None if runtime is None else runtime.level_fallback
            if (
                runtime is not None
                and fallback is not None
                and read_health.mode_read_since(state, fallback[1])
            ):
                runtime.level_fallback = None
                changed = True
        if changed and notify:
            self.async_update_listeners()

    def _record_write_issue(self, room_key: str, write_key: str, status: WriteStatus) -> None:
        if self.cumulative_diagnostics is not None:
            self.cumulative_diagnostics.record_write_issue(room_key, write_key, status)

    async def _read_one_job(
        self,
        previous_states: dict[str, RoomState],
        job: PollJob,
    ) -> dict[str, RoomState]:
        """Run one scheduled read job and merge the result into the state map."""

        runtime = self._runtimes[job.room_key]
        room = runtime.room
        plan = job.refresh_plan
        previous_state = previous_states.get(room.key, EMPTY_ROOM_STATE)
        error: MeltemModbusError | None = None

        try:
            refreshed_state, outcome = await self.client.read_room(room, previous_state, plan)
        except MeltemModbusError as err:
            _LOGGER.debug("Failed to read room %s for job %s: %s", room.key, job.key, err)
            error = self._last_job_error = err
            self._last_job_link_failure = True
            self._last_job_attempted = True
            runtime.read_failures += 1
            refreshed_state = read_health.record_read_failures(previous_state, room, plan, err)
            failed_groups: Iterable[str] = (
                group
                for group in plan.read_groups()
                if room.supports_any(READ_GROUP_ENTITY_KEYS[group])
            )
        else:
            self.missing_sensors.record(room, outcome)
            self._last_job_attempted = read_health.job_read_attempted(room, outcome, plan)
            failure = read_health.job_read_failure(room, outcome, plan)
            error = self._last_job_error = None if failure is None else failure.error
            self._last_job_link_failure = read_health.job_read_unanswered(room, outcome, plan)
            if failure is not None:
                _LOGGER.debug("Failed to read room %s for job %s: %s", room.key, job.key, error)
                optional_rejection = not failure.link_failure and all(
                    group in OPTIONAL_DIAGNOSTIC_READ_GROUPS
                    for group in plan.read_groups()
                    if room.supports_any(READ_GROUP_ENTITY_KEYS[group])
                )
                if read_health.job_read_received_values(room, outcome, plan):
                    runtime.read_failures = 0
                elif not optional_rejection:
                    runtime.read_failures += 1
            elif read_health.job_read_succeeded(room, outcome, plan):
                runtime.read_failures = 0
                if plan.refresh_airflow:
                    refreshed_state = await self._read_balanced_target_if_pending(
                        room, refreshed_state, previous_state
                    )
            failed_groups = outcome.errors

        self._log_room_availability(room.key, refreshed_state, error)
        if error is not None:
            runtime.last_read_error = RoomReadError(str(error), dt_util.utcnow())
        if self.cumulative_diagnostics is not None:
            for group_key in failed_groups:
                self.cumulative_diagnostics.record_read_failure(room.key, group_key)
        if refreshed_state == previous_state:
            return previous_states
        return {**previous_states, room.key: refreshed_state}
