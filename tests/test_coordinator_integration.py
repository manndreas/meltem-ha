"""Tests for coordinator update cycle, first refresh, and error handling."""

from __future__ import annotations

import time
from collections.abc import Iterable, Iterator
from dataclasses import replace
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import UpdateFailed
from homeassistant.util import dt as dt_util
from modbus_connection import IllegalDataAddressError
from modbus_connection.mock import MockModbusConnection, MockModbusUnit, WriteEvent
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.meltem_ventilation.binary_sensor import (
    BINARY_SENSOR_DESCRIPTIONS,
    MeltemBinarySensorEntity,
    MeltemDataHealthBinarySensor,
)
from custom_components.meltem_ventilation.const import DOMAIN
from custom_components.meltem_ventilation.coordinator import (
    TRANSPORT_BACKOFF_MAX_SECONDS,
    TRANSPORT_BACKOFF_START_SECONDS,
    UNREAD_GROUP_RETRY_SECONDS,
    MeltemDataUpdateCoordinator,
)
from custom_components.meltem_ventilation.modbus_client import MeltemModbusClient
from custom_components.meltem_ventilation.modbus_helpers import (
    MeltemModbusError,
    supported_entity_keys_for_profile,
)
from custom_components.meltem_ventilation.models import (
    ReadHealth,
    RefreshPlan,
    RoomConfig,
    RoomState,
)
from custom_components.meltem_ventilation.polling import (
    AIRFLOW_REFRESH_PLAN,
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
#  Progressive startup
# ---------------------------------------------------------------------------


@pytest.fixture(name="clock")
def clock_fixture(monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    clock = SimpleNamespace(now=time.monotonic())
    monkeypatch.setattr(
        "custom_components.meltem_ventilation.coordinator.time",
        SimpleNamespace(monotonic=lambda: clock.now),
    )
    return clock


class TestFirstRefresh:
    async def test_first_refresh_publishes_one_room_before_reading_the_next(
        self, hass: HomeAssistant, clock: SimpleNamespace,
    ) -> None:
        coordinator, client = _build(hass, _ROOM_1, _ROOM_2)

        await coordinator.async_refresh()

        assert coordinator.data == {"unit_1": client.next_read_state}
        assert client.read_calls == [("unit_1", AIRFLOW_REFRESH_PLAN)]
        assert coordinator.data_health_stale("unit_2") is None
        assert not coordinator.room_available("unit_2")
        clock.now += coordinator.update_interval.total_seconds()

        await coordinator.async_refresh()

        assert coordinator.data == {
            "unit_1": client.next_read_state, "unit_2": client.next_read_state
        }
        assert client.read_calls == [
            ("unit_1", AIRFLOW_REFRESH_PLAN),
            ("unit_2", AIRFLOW_REFRESH_PLAN),
        ]

    async def test_startup_reads_every_supported_job_without_hour_long_delays(
        self, hass: HomeAssistant, clock: SimpleNamespace,
    ) -> None:
        coordinator, client = _build(hass, _ROOM_1, _ROOM_2)
        expected = {(job.room_key, job.refresh_plan) for job in coordinator._jobs}
        await coordinator.async_refresh()
        attempts = 1
        while coordinator._startup_jobs:
            clock.now += coordinator.update_interval.total_seconds()
            await coordinator.async_refresh()
            attempts += 1
            assert attempts <= len(expected) * 2

        assert set(client.read_calls) == expected
        assert all(plan != FULL_REFRESH_PLAN for _, plan in client.read_calls)
        assert len(coordinator.data) == 2

    async def test_startup_failure_does_not_prevent_the_next_room_from_recovering(
        self, hass: HomeAssistant, clock: SimpleNamespace,
    ) -> None:
        coordinator, client = _build(hass, _ROOM_1, _ROOM_2)
        client.fail_rooms = {"unit_1"}

        await coordinator.async_refresh()

        assert not coordinator.last_update_success
        assert coordinator.update_interval.total_seconds() == TRANSPORT_BACKOFF_START_SECONDS
        clock.now += coordinator.update_interval.total_seconds()

        await coordinator.async_refresh()

        failed_state = coordinator.data["unit_1"]
        assert not failed_state.has_data
        assert failed_state.read_health_for("flow").last_error == "boom: unit_1"
        assert failed_state.read_health_for("status").last_attempt is None
        assert coordinator.data["unit_2"] == client.next_read_state
        assert coordinator.last_update_success
        assert coordinator._backoff_seconds is None

    async def test_startup_keeps_the_job_rate_between_rooms(
        self, hass: HomeAssistant, clock: SimpleNamespace,
    ) -> None:
        coordinator, client = _build(hass, _ROOM_1, _ROOM_2, max_requests_per_second=2.0)

        await coordinator.async_refresh()
        await coordinator.async_refresh()

        assert len(client.read_calls) == 1
        clock.now += 0.5
        await coordinator.async_refresh()
        assert len(client.read_calls) == 2

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
        self, hass: HomeAssistant, clock: SimpleNamespace,
    ) -> None:
        coordinator, client = _build(hass, _ROOM_1, _ROOM_2)
        unanswered = RoomState().with_read_health(
            "flow",
            ReadHealth(last_attempt=dt_util.utcnow(), consecutive_failures=1, last_error="timeout"),
        )
        working = RoomState(target_level=42)
        with patch.object(client, "read_room_state", side_effect=[unanswered, working]):
            await coordinator.async_refresh()
            clock.now += coordinator.update_interval.total_seconds()
            await coordinator.async_refresh()

        assert coordinator.data == {"unit_1": unanswered, "unit_2": working}
        assert coordinator.last_update_success

    async def test_periodic_flow_jobs_continue_during_slow_startup(
        self, hass: HomeAssistant, clock: SimpleNamespace,
    ) -> None:
        coordinator, client = _build(hass, _ROOM_1, _ROOM_2)
        await coordinator.async_refresh()
        pending_count = len(coordinator._startup_jobs)
        clock.now += 11.0

        await coordinator.async_refresh()
        clock.now += coordinator.update_interval.total_seconds()
        await coordinator.async_refresh()

        assert client.read_calls == [
            ("unit_1", AIRFLOW_REFRESH_PLAN),
            ("unit_1", AIRFLOW_REFRESH_PLAN),
            ("unit_2", AIRFLOW_REFRESH_PLAN),
        ]
        assert len(coordinator._startup_jobs) == pending_count - 1

    async def test_startup_job_is_rescheduled_from_completion(
        self, hass: HomeAssistant, clock: SimpleNamespace,
    ) -> None:
        coordinator, client = _build(hass)

        async def _slow_read(*args: object) -> RoomState:
            clock.now += 15.0
            return client.next_read_state

        with patch.object(client, "read_room_state", side_effect=_slow_read):
            await coordinator.async_refresh()

        flow = next(job for job in coordinator._jobs if job.key == "flow")
        assert flow.next_due == clock.now + flow.interval_seconds

    async def test_persistent_outage_keeps_backoff_and_failed_health(
        self, hass: HomeAssistant, clock: SimpleNamespace,
    ) -> None:
        coordinator, client = _build(hass)
        client.fail_rooms = {"unit_1"}
        for _ in range(20):
            await coordinator.async_refresh()
            assert not coordinator.last_update_success
            assert coordinator.update_interval.total_seconds() >= TRANSPORT_BACKOFF_START_SECONDS
            clock.now += coordinator.update_interval.total_seconds()
        assert not coordinator._startup_jobs
        assert coordinator._startup_states["unit_1"].read_health_for("flow").consecutive_failures >= 3

    async def test_group_unread_at_startup_is_retried_before_its_interval(
        self, hass: HomeAssistant, clock: SimpleNamespace,
    ) -> None:
        """Units can refuse every read for minutes after a gateway restart (H-3)."""
        coordinator, client = _build(hass)
        client.fail_rooms = {"unit_1"}
        started = clock.now
        hours = next(job for job in coordinator._jobs if job.key == "hours")
        for _ in range(50):
            await coordinator.async_refresh()
            if ("unit_1", hours.refresh_plan) in client.read_calls:
                break
            clock.now += coordinator.update_interval.total_seconds()

        retry = max(UNREAD_GROUP_RETRY_SECONDS, clock.now - started)
        assert retry < hours.interval_seconds
        assert hours.next_due == pytest.approx(clock.now + retry)

        async def _answer(
            room: RoomConfig, previous: RoomState, plan: RefreshPlan
        ) -> RoomState:
            client.read_calls.append((room.key, plan))
            at = dt_util.utcnow()
            for group_key in plan.read_groups():
                previous = previous.with_read_health(group_key, _read_ok(at))
            return replace(previous, target_level=42)

        with patch.object(client, "read_room_state", side_effect=_answer):
            for _ in range(200):
                if client.read_calls.count(("unit_1", hours.refresh_plan)) >= 2:
                    break
                clock.now += coordinator.update_interval.total_seconds()
                await coordinator.async_refresh()

        assert client.read_calls.count(("unit_1", hours.refresh_plan)) == 2
        assert hours.next_due == pytest.approx(clock.now + hours.interval_seconds)

    async def test_status_only_room_starts_without_an_airflow_read(
        self, hass: HomeAssistant, clock: SimpleNamespace,
    ) -> None:
        room = replace(_ROOM_1, supported_entity_keys=frozenset({"error_status"}))
        coordinator, client = _build(hass, room)

        await coordinator.async_refresh()

        assert client.read_calls == [("unit_1", RefreshPlan.only(refresh_status=True))]
        assert not coordinator._startup_jobs


class TestStartupWithRealClient:
    @pytest.mark.parametrize("rate", [0.5, 3.0, 10.0])
    async def test_six_rooms_publish_incrementally_with_a_shared_telegram_limit(
        self, hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch,
        clock: SimpleNamespace, rate: float,
    ) -> None:
        link = MockModbusConnection()
        rooms = [
            RoomConfig(
                key=f"unit_{slave}", name=f"Unit {slave}", profile="ii_plain", slave=slave,
                supported_entity_keys=supported_entity_keys_for_profile("ii_plain"),
            )
            for slave in range(2, 8)
        ]
        writes: list[WriteEvent] = []
        for room in rooms:
            link.for_unit(room.slave).holding.update(
                {41020: [30, 30], 41100: [3, 60, 0]}
            )
            link.for_unit(room.slave).on_write(writes.append)
        slaves = {link.for_unit(slave): slave for slave in range(1, 8)}
        link.for_unit(1).holding[43901] = len(rooms)
        client = MeltemModbusClient(
            link.for_unit, port="/dev/test", max_requests_per_second=rate
        )
        entry = MockConfigEntry(domain=DOMAIN, title="Meltem", source="user")
        entry.add_to_hass(hass)
        coordinator = MeltemDataUpdateCoordinator(
            hass, config_entry=entry, client=client, rooms=rooms,
            max_requests_per_second=rate,
        )
        baseline = clock.now
        utc = dt_util.utcnow()
        logical_time = SimpleNamespace(monotonic=lambda: clock.now)
        monkeypatch.setattr(
            "custom_components.meltem_ventilation.device.transport.time", logical_time
        )
        monkeypatch.setattr(
            "custom_components.meltem_ventilation.modbus_client.time", logical_time
        )
        utc_time = SimpleNamespace(utcnow=lambda: utc + timedelta(seconds=clock.now - baseline))
        monkeypatch.setattr(
            "custom_components.meltem_ventilation.modbus_client.dt_util", utc_time
        )
        monkeypatch.setattr(
            "custom_components.meltem_ventilation.read_health.dt_util", utc_time
        )

        async def _sleep(seconds: float) -> None:
            clock.now += seconds

        monkeypatch.setattr(
            "custom_components.meltem_ventilation.device.transport.async_sleep", _sleep
        )
        reads: list[tuple[float, int, int]] = []
        original_read = MockModbusUnit.read_holding_registers

        async def _read(unit: MockModbusUnit, address: int, count: int) -> list[int]:
            reads.append((clock.now, slaves[unit], address))
            return await original_read(unit, address, count)

        monkeypatch.setattr(MockModbusUnit, "read_holding_registers", _read)
        monkeypatch.setattr(coordinator, "_schedule_refresh", lambda: None)
        snapshots: list[dict[str, RoomState]] = []
        remove_listener = coordinator.async_add_listener(
            lambda: snapshots.append(coordinator.safe_data)
        )
        try:
            await client.async_validate_gateway()
            await coordinator.async_refresh()

            assert len(snapshots) == 1
            assert set(snapshots[0]) == {"unit_2"}
            assert snapshots[0]["unit_2"].supply_air_flow == 30
            assert snapshots[0]["unit_2"].error_status is None
            assert coordinator.read_group_stale("unit_2", "status") is None
            status_description = next(
                description for description in BINARY_SENSOR_DESCRIPTIONS
                if description.key == "error_status"
            )
            status_sensor = MeltemBinarySensorEntity(
                coordinator, rooms[0], status_description
            )
            assert not status_sensor.available
            health_sensor = MeltemDataHealthBinarySensor(coordinator, rooms[0])
            assert health_sensor.is_on is False
            assert health_sensor.extra_state_attributes["status"]["stale"] is None

            steps = 0
            while coordinator._startup_jobs:
                clock.now += coordinator.update_interval.total_seconds()
                await coordinator.async_refresh()
                steps += 1
                assert steps < 100

            assert coordinator.state_room_count == 6
            assert status_sensor.available
            assert all(
                state.error_status is False and state.operating_hours == 0
                for state in coordinator.safe_data.values()
            )
            assert all(
                later[0] - earlier[0] >= 1 / rate - 1e-8
                for earlier, later in zip(reads, reads[1:])
            )
            assert all(
                len(set(later) - set(earlier)) <= 1
                for earlier, later in zip(snapshots, snapshots[1:])
            )
            assert not writes
        finally:
            remove_listener()
            await coordinator.async_shutdown()
            client.shutdown()

    async def test_mode_refusal_is_not_hidden_by_successful_startup_flow(
        self, hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch, clock: SimpleNamespace
    ) -> None:
        link = MockModbusConnection()
        unit = link.for_unit(_ROOM_1.slave)
        unit.holding[41020] = [30, 30]
        original_read = unit.read_holding_registers

        async def _read(address: int, count: int) -> list[int]:
            if address == 41100:
                raise IllegalDataAddressError()
            return await original_read(address, count)

        monkeypatch.setattr(unit, "read_holding_registers", _read)
        client = MeltemModbusClient(link.for_unit, port="/dev/test")
        entry = MockConfigEntry(domain=DOMAIN, title="Meltem", source="user")
        entry.add_to_hass(hass)
        coordinator = MeltemDataUpdateCoordinator(
            hass, config_entry=entry, client=client, rooms=[_ROOM_1],
            max_requests_per_second=2.0,
        )

        for _ in range(3):
            await coordinator.async_refresh()
            clock.now += 11.0

        # The second refresh is periodic flow; the third makes startup progress.
        await coordinator.async_refresh()

        assert coordinator.last_update_success
        assert coordinator.safe_data["unit_1"].supply_air_flow == 30
        assert coordinator.read_group_available("unit_1", "flow")
        assert coordinator.read_group_stale("unit_1", "flow_control") is True
        assert MeltemDataHealthBinarySensor(coordinator, _ROOM_1).is_on is True
        client.shutdown()


# ---------------------------------------------------------------------------
#  _async_update_data — first vs incremental
# ---------------------------------------------------------------------------


class TestAsyncUpdateData:
    async def test_empty_data_triggers_one_startup_job(
        self, hass: HomeAssistant,
    ) -> None:
        coordinator, client = _build(hass)
        coordinator.data = {}

        data = await coordinator._async_update_data()

        assert data == {"unit_1": client.next_read_state}
        assert client.read_calls == [("unit_1", AIRFLOW_REFRESH_PLAN)]

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

    def test_failed_but_never_read_group_is_not_reported_as_healthy(
        self, hass: HomeAssistant,
    ) -> None:
        coordinator, _ = _build(hass)
        health = ReadHealth(
            last_attempt=dt_util.utcnow(), consecutive_failures=1, last_error="read failed"
        )
        coordinator.data = {"unit_1": RoomState().with_read_health("flow", health)}
        coordinator.last_update_success = True
        sensor = MeltemDataHealthBinarySensor(coordinator, _ROOM_1)

        assert sensor.is_on is None
        assert sensor.extra_state_attributes["flow"]["stale"] is None

        coordinator.data = {
            "unit_1": RoomState().with_read_health(
                "flow", replace(health, consecutive_failures=3)
            )
        }
        assert sensor.is_on is True


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
