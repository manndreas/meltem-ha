"""Tests for coordinator update cycle, first refresh, and error handling."""

from __future__ import annotations

import time
from collections.abc import Iterable, Iterator
from dataclasses import replace
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import UpdateFailed
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.meltem_ventilation.const import DOMAIN
from custom_components.meltem_ventilation.coordinator import (
    TRANSPORT_BACKOFF_MAX_SECONDS,
    TRANSPORT_BACKOFF_START_SECONDS,
    MeltemDataUpdateCoordinator,
)
from custom_components.meltem_ventilation.modbus_helpers import MeltemModbusError
from custom_components.meltem_ventilation.models import (
    ReadHealth,
    RefreshPlan,
    RoomConfig,
    RoomState,
)
from custom_components.meltem_ventilation.polling import (
    FULL_REFRESH_PLAN,
    JOB_GROUPS,
    JobGroup,
    PollJob,
    room_needs_job,
)

# ---------------------------------------------------------------------------
#  Helpers
# ---------------------------------------------------------------------------

_ROOM_1 = RoomConfig(key="unit_1", name="Unit 1", profile="ii_plain", slave=2)
_ROOM_2 = RoomConfig(key="unit_2", name="Unit 2", profile="ii_fc", slave=3)


class _FakeClient:
    """Stand-in for MeltemModbusClient with no serial port."""

    def __init__(self) -> None:
        self.read_calls: list[tuple[str, RefreshPlan]] = []
        self.next_read_state = RoomState(target_level=42)
        self.fail_rooms: set[str] = set()

    async def read_room_state(
        self,
        room: RoomConfig,
        previous_state: RoomState,
        refresh_plan: RefreshPlan,
    ) -> RoomState:
        self.read_calls.append((room.key, refresh_plan))
        if room.key in self.fail_rooms:
            raise MeltemModbusError(f"boom: {room.key}")
        return self.next_read_state

    def seconds_since_successful_read(self, slave: int) -> float | None:
        return None


@pytest.fixture(autouse=True)
def _skip_settle_delays() -> Iterator[None]:
    with patch("custom_components.meltem_ventilation.coordinator.async_sleep", new=AsyncMock()):
        yield


def _build(
    hass: HomeAssistant,
    *rooms: RoomConfig,
    max_requests_per_second: float = 2.0,
) -> tuple[MeltemDataUpdateCoordinator, _FakeClient]:
    entry = MockConfigEntry(domain=DOMAIN, title="Meltem", version=1, source="user")
    entry.add_to_hass(hass)
    client = _FakeClient()
    coordinator = MeltemDataUpdateCoordinator(
        hass,
        config_entry=entry,
        client=client,
        rooms=list(rooms) or [_ROOM_1],
        max_requests_per_second=max_requests_per_second,
    )
    return coordinator, client


def _set_jobs_due(coordinator: MeltemDataUpdateCoordinator, next_due: float) -> None:
    for job in coordinator._jobs:
        job.next_due = next_due


def _due_job(group_key: str, room_key: str = "unit_1") -> PollJob:
    group = next(group for group in JOB_GROUPS if group.key == group_key)
    return PollJob(group.key, room_key, group.refresh_plan, group.interval_seconds, 0.0)


def _read_ok(at: datetime) -> ReadHealth:
    return ReadHealth(last_attempt=at, last_successful_read=at)


def _with_health(state: RoomState, group_keys: Iterable[str], health: ReadHealth) -> RoomState:
    for group_key in group_keys:
        state = state.with_read_health(group_key, health)
    return state


# ---------------------------------------------------------------------------
#  First refresh — _read_all_rooms_full
# ---------------------------------------------------------------------------


class TestFirstRefresh:
    async def test_first_refresh_reads_every_room_in_full(
        self, hass: HomeAssistant,
    ) -> None:
        coordinator, client = _build(hass, _ROOM_1, _ROOM_2)

        data = await coordinator._read_all_rooms_full()

        assert data == {"unit_1": client.next_read_state, "unit_2": client.next_read_state}
        assert client.read_calls == [
            ("unit_1", FULL_REFRESH_PLAN),
            ("unit_2", FULL_REFRESH_PLAN),
        ]

    async def test_first_refresh_partial_failure_still_returns_states(
        self, hass: HomeAssistant,
    ) -> None:
        """A room that fails at startup gets an empty state and is polled first."""
        coordinator, client = _build(hass, _ROOM_1, _ROOM_2)
        client.fail_rooms = {"unit_1"}

        data = await coordinator._read_all_rooms_full()
        now = time.monotonic()

        failed_state = data["unit_1"]
        assert not failed_state.has_data
        assert failed_state.read_health_for("flow").last_error == "boom: unit_1"
        assert failed_state.read_health_for("status").consecutive_failures == 1
        assert data["unit_2"] == client.next_read_state
        assert all(job.next_due <= now for job in coordinator._jobs if job.room_key == "unit_1")
        assert all(
            job.next_due > now
            for job in coordinator._jobs
            if job.room_key == "unit_2" and job.key != "flow"
        )

    async def test_first_refresh_keeps_the_request_rate_between_rooms(
        self, hass: HomeAssistant,
    ) -> None:
        coordinator, _ = _build(hass, _ROOM_1, _ROOM_2, max_requests_per_second=2.0)

        with patch(
            "custom_components.meltem_ventilation.coordinator.async_sleep", new=AsyncMock()
        ) as sleep:
            await coordinator._read_all_rooms_full()

        sleep.assert_awaited_once()
        assert 0 < sleep.await_args.args[0] <= 0.5

    async def test_a_failed_first_refresh_is_retried_after_the_backoff_start(
        self, hass: HomeAssistant,
    ) -> None:
        """Retrying at the request rate would hammer a gateway that is down."""
        coordinator, client = _build(hass)
        client.fail_rooms = {"unit_1"}

        await coordinator.async_refresh()

        assert not coordinator.last_update_success
        assert coordinator.update_interval == timedelta(
            seconds=TRANSPORT_BACKOFF_START_SECONDS
        )

    async def test_a_first_refresh_without_any_room_values_backs_off(
        self, hass: HomeAssistant,
    ) -> None:
        coordinator, client = _build(hass)
        client.next_read_state = RoomState().with_read_health(
            "flow",
            ReadHealth(last_attempt=dt_util.utcnow(), consecutive_failures=1, last_error="timeout"),
        )

        await coordinator.async_refresh()

        assert not coordinator.last_update_success
        assert coordinator.update_interval == timedelta(seconds=TRANSPORT_BACKOFF_START_SECONDS)

    async def test_first_refresh_keeps_working_room_when_another_has_no_values(
        self, hass: HomeAssistant,
    ) -> None:
        coordinator, client = _build(hass, _ROOM_1, _ROOM_2)
        unanswered = RoomState().with_read_health(
            "flow",
            ReadHealth(last_attempt=dt_util.utcnow(), consecutive_failures=1, last_error="timeout"),
        )
        working = RoomState(target_level=42)
        with patch.object(client, "read_room_state", side_effect=[unanswered, working]):
            states = await coordinator._read_all_rooms_full()

        assert states == {"unit_1": unanswered, "unit_2": working}
        assert coordinator._room_failures["unit_1"] == 1
        assert all(job.next_due < time.monotonic() for job in coordinator._jobs if job.room_key == "unit_1")


# ---------------------------------------------------------------------------
#  _async_update_data — first vs incremental
# ---------------------------------------------------------------------------


class TestAsyncUpdateData:
    async def test_empty_data_triggers_full_read(
        self, hass: HomeAssistant,
    ) -> None:
        coordinator, client = _build(hass)
        coordinator.data = {}

        data = await coordinator._async_update_data()

        assert data == {"unit_1": client.next_read_state}
        assert client.read_calls == [("unit_1", FULL_REFRESH_PLAN)]

    async def test_existing_data_runs_one_incremental_job(
        self, hass: HomeAssistant,
    ) -> None:
        coordinator, client = _build(hass)
        coordinator.data = {"unit_1": RoomState(target_level=10)}
        _set_jobs_due(coordinator, 0.0)

        await coordinator._async_update_data()

        assert len(client.read_calls) == 1
        assert client.read_calls[0][1] != FULL_REFRESH_PLAN

    async def test_no_due_job_returns_existing_data(
        self, hass: HomeAssistant,
    ) -> None:
        coordinator, client = _build(hass)
        existing = {"unit_1": RoomState(target_level=99)}
        coordinator.data = existing
        _set_jobs_due(coordinator, time.monotonic() + 9999)

        assert await coordinator._async_update_data() is existing
        assert client.read_calls == []

    async def test_total_startup_outage_raises_update_failed(
        self, hass: HomeAssistant,
    ) -> None:
        coordinator, client = _build(hass, _ROOM_1, _ROOM_2)
        coordinator.data = {}
        client.fail_rooms = {"unit_1", "unit_2"}

        with pytest.raises(UpdateFailed):
            await coordinator._async_update_data()

    async def test_incremental_transport_failure_keeps_cached_state(
        self, hass: HomeAssistant,
    ) -> None:
        coordinator, client = _build(hass)
        coordinator.data = {"unit_1": RoomState(target_level=10)}
        client.fail_rooms = {"unit_1"}
        _set_jobs_due(coordinator, 0.0)

        data = await coordinator._async_update_data()

        assert data["unit_1"].target_level == 10
        assert data["unit_1"].read_health_for("flow").last_error == "boom: unit_1"


# ---------------------------------------------------------------------------
#  Scheduler wakeups
# ---------------------------------------------------------------------------


class TestSchedulerWakeups:
    async def test_idle_tick_sleeps_until_the_next_job_is_due(
        self, hass: HomeAssistant,
    ) -> None:
        coordinator, _ = _build(hass)
        coordinator.data = {"unit_1": RoomState(target_level=30)}
        _set_jobs_due(coordinator, time.monotonic() + 30.0)

        await coordinator._async_update_data()

        assert coordinator.update_interval is not None
        # Up to one extra second compensates HA's whole-second alignment.
        assert 25.0 < coordinator.update_interval.total_seconds() <= 31.0

    async def test_next_tick_never_undercuts_the_request_rate(
        self, hass: HomeAssistant,
    ) -> None:
        coordinator, _ = _build(hass)
        coordinator.data = {"unit_1": RoomState(target_level=30)}
        _set_jobs_due(coordinator, time.monotonic() - 1.0)

        await coordinator._async_update_data()

        assert coordinator.update_interval is not None
        tick = coordinator._tick_seconds
        assert tick <= coordinator.update_interval.total_seconds() < tick + 1.0

    def test_next_tick_compensates_for_whole_second_alignment(
        self, hass: HomeAssistant
    ) -> None:
        """HA fires at int(loop.time()) + jitter + interval, never before the due time."""
        coordinator, _ = _build(hass)
        due_in = 3.0
        _set_jobs_due(coordinator, time.monotonic() + due_in)

        with patch.object(hass.loop, "time", return_value=1000.75):
            coordinator._schedule_next_tick()

        assert due_in + 0.7 < coordinator.update_interval.total_seconds() <= due_in + 0.75

    async def test_jobs_respect_the_request_rate_cap(
        self, hass: HomeAssistant,
    ) -> None:
        coordinator, client = _build(hass)
        coordinator.data = {"unit_1": RoomState(target_level=10)}
        _set_jobs_due(coordinator, 0.0)

        await coordinator._async_update_data()
        await coordinator._async_update_data()

        assert len(client.read_calls) == 1

    async def test_post_write_reads_count_against_the_request_rate_cap(
        self, hass: HomeAssistant,
    ) -> None:
        coordinator, client = _build(hass)
        coordinator.data = {"unit_1": RoomState(target_level=10)}
        _set_jobs_due(coordinator, 0.0)

        await coordinator._async_refresh_room_after_write(_ROOM_1)
        await coordinator._async_update_data()

        assert len(client.read_calls) == 1

    def test_backoff_interval_survives_rescheduling(self, hass: HomeAssistant) -> None:
        coordinator, _ = _build(hass)
        coordinator._backoff_seconds = TRANSPORT_BACKOFF_MAX_SECONDS
        coordinator.update_interval = timedelta(seconds=TRANSPORT_BACKOFF_MAX_SECONDS)

        coordinator._schedule_next_tick()

        assert coordinator.update_interval.total_seconds() == TRANSPORT_BACKOFF_MAX_SECONDS


# ---------------------------------------------------------------------------
#  Job scheduling
# ---------------------------------------------------------------------------


class TestJobScheduling:
    def test_jobs_of_one_group_are_staggered_across_rooms(
        self, hass: HomeAssistant,
    ) -> None:
        coordinator, _ = _build(hass, _ROOM_1, _ROOM_2)

        first, second = (job for job in coordinator._jobs if job.key == "flow")

        assert second.next_due > first.next_due

    @pytest.mark.parametrize(
        ("supported_entity_keys", "needed_jobs"),
        [
            (None, {group.key for group in JOB_GROUPS}),
            (frozenset({"extract_air_flow"}), {"flow"}),
            (frozenset({"error_status"}), {"status"}),
        ],
        ids=("unfiltered", "airflow-only", "status-only"),
    )
    def test_room_only_needs_jobs_for_its_entities(
        self,
        supported_entity_keys: frozenset[str] | None,
        needed_jobs: set[str],
    ) -> None:
        room = replace(_ROOM_1, supported_entity_keys=supported_entity_keys)

        assert {
            group.key
            for group in JOB_GROUPS
            if room_needs_job(room, group)
        } == needed_jobs


# ---------------------------------------------------------------------------
#  Post-write refresh
# ---------------------------------------------------------------------------


class TestPostWriteRefresh:
    async def test_refresh_after_write_updates_data(
        self, hass: HomeAssistant,
    ) -> None:
        coordinator, client = _build(hass)
        coordinator.data = {"unit_1": RoomState(target_level=10)}
        client.next_read_state = RoomState(
            target_level=50, extract_air_flow=50, supply_air_flow=50
        )

        await coordinator._async_refresh_room_after_write(_ROOM_1)

        assert coordinator.data["unit_1"].target_level == 50

    async def test_refresh_after_write_waits_for_the_request_rate_slot(
        self, hass: HomeAssistant,
    ) -> None:
        coordinator, client = _build(hass, max_requests_per_second=0.5)
        coordinator.data = {"unit_1": RoomState(target_level=10)}
        coordinator._last_read_started = time.monotonic()

        with patch(
            "custom_components.meltem_ventilation.coordinator.async_sleep", new=AsyncMock()
        ) as sleep:
            await coordinator._async_refresh_room_after_write(_ROOM_1)

        sleep.assert_awaited_once()
        assert 0 < sleep.await_args.args[0] <= 2.0
        assert len(client.read_calls) == 1

    async def test_refresh_after_write_honors_minimum_attempt_count(
        self, hass: HomeAssistant,
    ) -> None:
        coordinator, client = _build(hass)
        coordinator.data = {"unit_1": RoomState(target_level=10)}
        stale = RoomState(target_level=10, extract_air_flow=10, supply_air_flow=10)
        updated = RoomState(target_level=50, extract_air_flow=50, supply_air_flow=50)

        with patch.object(client, "read_room_state", side_effect=[stale, updated]) as read_mock:
            await coordinator._async_refresh_room_after_write(_ROOM_1, min_refresh_attempts=2)

        assert read_mock.call_count == 2
        assert coordinator.data["unit_1"].target_level == 50

    async def test_refresh_after_write_failure_keeps_cached_data(
        self, hass: HomeAssistant,
    ) -> None:
        coordinator, client = _build(hass)
        coordinator.data = {"unit_1": RoomState(target_level=10)}
        client.fail_rooms = {"unit_1"}

        await coordinator._async_refresh_room_after_write(_ROOM_1)

        assert coordinator.data["unit_1"].target_level == 10
        assert coordinator.data["unit_1"].read_health_for("flow").consecutive_failures == 1


# ---------------------------------------------------------------------------
#  _read_one_job
# ---------------------------------------------------------------------------


class TestReadOneJob:
    async def test_successful_read_merges_state(
        self, hass: HomeAssistant,
    ) -> None:
        coordinator, client = _build(hass, _ROOM_1, _ROOM_2)
        client.next_read_state = RoomState(target_level=77)
        previous = {
            "unit_1": RoomState(target_level=10),
            "unit_2": RoomState(target_level=20),
        }

        result = await coordinator._read_one_job(previous, _due_job("flow"))

        assert result["unit_1"].target_level == 77
        assert result["unit_2"].target_level == 20

    async def test_failed_read_preserves_previous(
        self, hass: HomeAssistant,
    ) -> None:
        coordinator, client = _build(hass)
        client.fail_rooms = {"unit_1"}
        previous = {"unit_1": RoomState(target_level=55)}

        result = await coordinator._read_one_job(previous, _due_job("flow"))

        assert result["unit_1"].target_level == 55
        assert result["unit_1"].read_health_for("flow").consecutive_failures == 1

    async def test_failed_status_job_only_increments_status_health(
        self, hass: HomeAssistant,
    ) -> None:
        coordinator, client = _build(hass)
        client.fail_rooms = {"unit_1"}
        flow_health = replace(_read_ok(dt_util.utcnow()), consecutive_failures=1)
        previous = {"unit_1": RoomState().with_read_health("flow", flow_health)}

        state = (await coordinator._read_one_job(previous, _due_job("status")))["unit_1"]

        assert state.read_health_for("flow") == flow_health
        assert state.read_health_for("status").consecutive_failures == 1
        assert state.read_health_for("status").last_error == "boom: unit_1"

    @pytest.mark.parametrize("group", JOB_GROUPS, ids=lambda group: group.key)
    async def test_each_read_group_failure_is_isolated_and_recovers(
        self, hass: HomeAssistant, group: JobGroup,
    ) -> None:
        room = replace(_ROOM_1, profile="ii_fc_voc")
        coordinator, client = _build(hass, room)
        read_groups = group.refresh_plan.read_groups()
        neighbor_group = "flow" if "status" in read_groups else "status"
        previous_time = dt_util.utcnow()
        neighbor_health = ReadHealth(
            last_attempt=previous_time,
            last_successful_read=previous_time,
            consecutive_failures=1,
            last_error="neighbor group failed",
        )
        previous_state = _with_health(
            RoomState(target_level=55), read_groups, _read_ok(previous_time)
        ).with_read_health(neighbor_group, neighbor_health)
        job = _due_job(group.key, room.key)
        client.fail_rooms = {room.key}

        failed_states = await coordinator._read_one_job({room.key: previous_state}, job)
        failed_state = failed_states[room.key]

        assert failed_state.target_level == 55
        for group_key in read_groups:
            health = failed_state.read_health_for(group_key)
            assert health.consecutive_failures == 1
            assert health.last_error == f"boom: {room.key}"
        assert failed_state.read_health_for(neighbor_group) == neighbor_health

        client.fail_rooms.clear()
        recovery_time = dt_util.utcnow()
        client.next_read_state = _with_health(failed_state, read_groups, _read_ok(recovery_time))

        recovered_state = (await coordinator._read_one_job(failed_states, job))[room.key]

        for group_key in read_groups:
            health = recovered_state.read_health_for(group_key)
            assert health.consecutive_failures == 0
            assert health.last_error is None
            assert health.last_successful_read == recovery_time
        assert recovered_state.read_health_for(neighbor_group) == neighbor_health
        assert recovered_state.target_level == 55

    async def test_repeated_failed_flow_reads_mark_airflow_stale(
        self, hass: HomeAssistant,
    ) -> None:
        coordinator, client = _build(hass)
        client.fail_rooms = {"unit_1"}
        state_map = {"unit_1": RoomState(target_level=55)}
        job = _due_job("flow")

        for _ in range(3):
            state_map = await coordinator._read_one_job(state_map, job)

        coordinator.data = state_map
        assert state_map["unit_1"].read_health_for("flow").consecutive_failures == 3
        assert coordinator.read_group_stale("unit_1", "flow") is True
        assert coordinator.read_group_available("unit_1", "flow") is False

    async def test_other_job_success_does_not_clear_airflow_failures(
        self, hass: HomeAssistant,
    ) -> None:
        coordinator, client = _build(hass)
        flow_health = ReadHealth(
            last_attempt=dt_util.utcnow(),
            consecutive_failures=2,
            last_error="airflow block read failed",
        )
        previous = RoomState().with_read_health("flow", flow_health)
        client.next_read_state = replace(previous, error_status=False)

        result = await coordinator._read_one_job({"unit_1": previous}, _due_job("status"))

        assert result["unit_1"].read_health_for("flow").consecutive_failures == 2

    def test_airflow_data_becomes_stale_after_success_timestamp_expires(
        self, hass: HomeAssistant,
    ) -> None:
        coordinator, _ = _build(hass)
        expired = _read_ok(dt_util.utcnow() - timedelta(seconds=31))
        coordinator.data = {
            "unit_1": RoomState(supply_air_flow=30).with_read_health("flow", expired)
        }

        assert coordinator.read_group_stale("unit_1", "flow") is True
        assert coordinator.read_group_available("unit_1", "flow") is False

    def test_airflow_health_metadata_does_not_count_as_room_data(self) -> None:
        failed_read = ReadHealth(
            last_attempt=dt_util.utcnow(),
            consecutive_failures=1,
            last_error="read failed",
        )

        assert not RoomState().with_read_health("flow", failed_read).has_data


# ---------------------------------------------------------------------------
#  Tick interval
# ---------------------------------------------------------------------------


class TestTickInterval:
    @pytest.mark.parametrize(
        ("max_requests_per_second", "interval_seconds"), [(2.0, 0.5), (0.2, 5.0)]
    )
    def test_initial_interval_follows_the_request_rate(
        self,
        hass: HomeAssistant,
        max_requests_per_second: float,
        interval_seconds: float,
    ) -> None:
        coordinator, _ = _build(hass, max_requests_per_second=max_requests_per_second)

        assert coordinator.update_interval == timedelta(seconds=interval_seconds)
