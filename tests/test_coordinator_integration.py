"""Tests for coordinator update cycle, first refresh, and error handling."""

from __future__ import annotations

import time
from datetime import timedelta
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import UpdateFailed
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.meltem_ventilation.const import DOMAIN
from custom_components.meltem_ventilation.coordinator import (
    JOB_GROUPS,
    TRANSPORT_BACKOFF_MAX_SECONDS,
    JobGroup,
    MeltemDataUpdateCoordinator,
    PollJob,
)
from custom_components.meltem_ventilation.modbus_helpers import MeltemModbusError
from custom_components.meltem_ventilation.models import (
    ReadHealth,
    RefreshPlan,
    RoomConfig,
    RoomState,
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
        self.write_level_calls: list[tuple[str, int]] = []
        self.write_unbalanced_calls: list[tuple[str, int, int]] = []
        self.write_preset_mode_calls: list[tuple[str, str]] = []
        self.reset_calls = 0
        self.close_calls = 0
        self.next_read_state = RoomState(target_level=42)
        self._fail_rooms: set[str] = set()

    def read_room_state(
        self,
        room: RoomConfig,
        previous_state: RoomState,
        refresh_plan: RefreshPlan,
    ) -> RoomState:
        self.read_calls.append((room.key, refresh_plan))
        if room.key in self._fail_rooms:
            raise MeltemModbusError(f"boom: {room.key}")
        return self.next_read_state

    def write_level(self, room: RoomConfig, level: int) -> None:
        self.write_level_calls.append((room.key, level))

    def write_unbalanced_levels(
        self, room: RoomConfig, supply_level: int, extract_level: int
    ) -> None:
        self.write_unbalanced_calls.append((room.key, supply_level, extract_level))

    def write_preset_mode(
        self,
        room: RoomConfig,
        preset_mode: str,
    ) -> None:
        self.write_preset_mode_calls.append((room.key, preset_mode))

    def reset_connection(self) -> None:
        self.reset_calls += 1

    def close(self) -> None:
        self.close_calls += 1

    def discover_gateway_units(self, start: int, end: int) -> list[int]:
        return [2, 3]

    def probe_slave_details(self, slave: int):
        return ("plain", None, ["level"])


def _mock_entry(hass: HomeAssistant) -> MockConfigEntry:
    entry = MockConfigEntry(domain=DOMAIN, title="Meltem", version=1, source="user")
    entry.add_to_hass(hass)
    return entry


def _build(
    hass: HomeAssistant,
    rooms: list[RoomConfig] | None = None,
) -> tuple[MeltemDataUpdateCoordinator, _FakeClient]:
    client = _FakeClient()
    coordinator = MeltemDataUpdateCoordinator(
        hass,
        config_entry=_mock_entry(hass),
        client=client,
        rooms=rooms or [_ROOM_1],
        max_requests_per_second=2.0,
    )
    return coordinator, client


# ---------------------------------------------------------------------------
#  First refresh — _read_all_rooms_full
# ---------------------------------------------------------------------------


class TestFirstRefresh:
    async def test_first_refresh_reads_all_rooms(
        self, hass: HomeAssistant,
    ) -> None:
        coordinator, client = _build(hass, rooms=[_ROOM_1, _ROOM_2])
        client.next_read_state = RoomState(target_level=42)

        data = await hass.async_add_executor_job(coordinator._read_all_rooms_full)

        assert "unit_1" in data
        assert "unit_2" in data
        assert data["unit_1"].target_level == 42
        # Two rooms read with full RefreshPlan.
        assert len(client.read_calls) == 2

    async def test_first_refresh_partial_failure_still_returns_states(
        self, hass: HomeAssistant,
    ) -> None:
        """When one room fails during startup, it gets an empty state and the other succeeds."""
        coordinator, client = _build(hass, rooms=[_ROOM_1, _ROOM_2])
        client._fail_rooms = {"unit_1"}
        client.next_read_state = RoomState(target_level=50)

        with patch("custom_components.meltem_ventilation.coordinator.sync_sleep"):
            data = await hass.async_add_executor_job(
                coordinator._read_all_rooms_full
            )

        # Failed rooms keep empty values while recording the affected groups.
        failed_state = data["unit_1"]
        assert not coordinator._room_state_has_data(failed_state)
        assert failed_state.read_health_for("flow").last_error == "boom: unit_1"
        assert failed_state.read_health_for("status").consecutive_failures == 1
        # Successful room gets the mock state.
        assert data["unit_2"].target_level == 50
        assert client.reset_calls == 1
        # Empty startup rooms should be pulled to the front of the incremental queue.
        assert all(
            job.next_due <= time.monotonic()
            for job in coordinator._jobs
            if job.room_key == "unit_1"
        )
        assert all(
            job.next_due > time.monotonic()
            for job in coordinator._jobs
            if job.room_key == "unit_2" and job.key != "flow"
        )

    async def test_first_refresh_resets_connection_on_error(
        self, hass: HomeAssistant,
    ) -> None:
        coordinator, client = _build(hass, rooms=[_ROOM_1])
        client._fail_rooms = {"unit_1"}

        with (
            patch("custom_components.meltem_ventilation.coordinator.sync_sleep"),
            pytest.raises(MeltemModbusError),
        ):
            await hass.async_add_executor_job(
                coordinator._read_all_rooms_full
            )

        assert client.reset_calls == 1


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

        assert data["unit_1"].target_level == 42
        # Should call _read_all_rooms_full which reads all rooms.
        assert len(client.read_calls) == 1

    async def test_existing_data_triggers_incremental_read(
        self, hass: HomeAssistant,
    ) -> None:
        coordinator, client = _build(hass)
        coordinator.data = {"unit_1": RoomState(target_level=10)}

        # Make all jobs due now.
        for job in coordinator._jobs:
            job.next_due = 0.0

        await coordinator._async_update_data()

        # Should run exactly one job (the earliest due).
        assert len(client.read_calls) == 1

    async def test_no_due_job_returns_existing_data(
        self, hass: HomeAssistant,
    ) -> None:
        coordinator, client = _build(hass)
        existing = {"unit_1": RoomState(target_level=99)}
        coordinator.data = existing

        # Push all jobs far into the future.
        for job in coordinator._jobs:
            job.next_due = time.monotonic() + 9999

        data = await coordinator._async_update_data()

        assert data is existing
        assert len(client.read_calls) == 0

    async def test_modbus_error_raises_update_failed(
        self, hass: HomeAssistant,
    ) -> None:
        coordinator, client = _build(hass)
        coordinator.data = {}

        # Make _read_all_rooms_full raise MeltemModbusError directly
        # (e.g., simulating a complete connection loss, not per-room error).
        with (
            patch.object(
                coordinator,
                "_read_all_rooms_full",
                side_effect=MeltemModbusError("connection lost"),
            ),
            pytest.raises(UpdateFailed),
        ):
            await coordinator._async_update_data()


class TestSchedulerWakeups:
    async def test_idle_tick_sleeps_until_the_next_job_is_due(
        self, hass: HomeAssistant,
    ) -> None:
        coordinator, _ = _build(hass)
        coordinator.data = {"unit_1": RoomState(target_level=30)}
        now = time.monotonic()
        for job in coordinator._jobs:
            job.next_due = now + 30.0

        await coordinator._async_update_data()

        assert coordinator.update_interval is not None
        assert 25.0 < coordinator.update_interval.total_seconds() <= 30.0

    async def test_next_tick_never_undercuts_the_request_rate(
        self, hass: HomeAssistant,
    ) -> None:
        coordinator, _ = _build(hass)
        coordinator.data = {"unit_1": RoomState(target_level=30)}
        for job in coordinator._jobs:
            job.next_due = time.monotonic() - 1.0

        await coordinator._async_update_data()

        assert coordinator.update_interval is not None
        assert coordinator.update_interval.total_seconds() == coordinator._tick_seconds

    def test_backoff_interval_survives_rescheduling(self, hass: HomeAssistant) -> None:
        coordinator, _ = _build(hass)
        coordinator._backoff_seconds = TRANSPORT_BACKOFF_MAX_SECONDS
        coordinator.update_interval = timedelta(seconds=TRANSPORT_BACKOFF_MAX_SECONDS)

        coordinator._schedule_next_tick()

        assert coordinator.update_interval.total_seconds() == TRANSPORT_BACKOFF_MAX_SECONDS


class TestCoordinatorFailureHandling:

    async def test_total_startup_outage_raises_update_failed(
        self, hass: HomeAssistant,
    ) -> None:
        coordinator, client = _build(hass, rooms=[_ROOM_1, _ROOM_2])
        coordinator.data = {}
        client._fail_rooms = {"unit_1", "unit_2"}

        with (
            patch("custom_components.meltem_ventilation.coordinator.sync_sleep"),
            pytest.raises(UpdateFailed),
        ):
            await coordinator._async_update_data()

    async def test_incremental_transport_failure_keeps_cached_state(
        self, hass: HomeAssistant,
    ) -> None:
        coordinator, client = _build(hass)
        coordinator.data = {"unit_1": RoomState(target_level=10)}
        client._fail_rooms = {"unit_1"}
        for job in coordinator._jobs:
            job.next_due = 0.0

        data = await coordinator._async_update_data()

        assert data["unit_1"].target_level == 10
        assert client.reset_calls == 1

    async def test_transient_outer_transport_failures_keep_cached_state_before_unavailable(
        self, hass: HomeAssistant,
    ) -> None:
        coordinator, _client = _build(hass)
        coordinator.data = {"unit_1": RoomState(target_level=10)}

        with patch.object(
            hass,
            "async_add_executor_job",
            side_effect=MeltemModbusError("transport down"),
        ):
            first = await coordinator._async_update_data()
            second = await coordinator._async_update_data()
            third = await coordinator._async_update_data()
            with pytest.raises(UpdateFailed):
                await coordinator._async_update_data()

        assert first["unit_1"].target_level == 10
        assert second["unit_1"].target_level == 10
        assert third["unit_1"].target_level == 10


# ---------------------------------------------------------------------------
#  Job scheduling
# ---------------------------------------------------------------------------


class TestJobScheduling:
    @staticmethod
    def _group(key: str) -> JobGroup:
        return next(group for group in JOB_GROUPS if group.key == key)

    def test_build_group_jobs_stagger(
        self, hass: HomeAssistant,
    ) -> None:
        """Jobs for the same group across rooms should be staggered."""
        coordinator, _ = _build(hass, rooms=[_ROOM_1, _ROOM_2])

        flow_jobs = [j for j in coordinator._jobs if j.key == "flow"]
        assert len(flow_jobs) == 2
        # The second job should start later than the first.
        assert flow_jobs[1].next_due > flow_jobs[0].next_due

    def test_room_needs_job_without_supported_keys(
        self, hass: HomeAssistant,
    ) -> None:
        coordinator, _ = _build(hass)
        # Room without supported_entity_keys → needs all jobs.
        for group in JOB_GROUPS:
            assert coordinator._room_needs_job(_ROOM_1, group)

    def test_room_needs_job_with_constrained_keys(
        self, hass: HomeAssistant,
    ) -> None:
        room = RoomConfig(
            key="constrained",
            name="Constrained",
            profile="ii_plain",
            slave=5,
            supported_entity_keys=frozenset({"extract_air_flow"}),
        )
        coordinator, _ = _build(hass, rooms=[room])
        assert coordinator._room_needs_job(room, self._group("flow"))
        assert not coordinator._room_needs_job(room, self._group("hours"))
        assert not coordinator._room_needs_job(room, self._group("status"))

    def test_room_without_hours_entities_skips_hours_job(
        self, hass: HomeAssistant,
    ) -> None:
        room = RoomConfig(
            key="no_hours",
            name="No Hours",
            profile="ii_plain",
            slave=6,
            supported_entity_keys=frozenset({"error_status"}),
        )
        coordinator, _ = _build(hass, rooms=[room])

        assert not coordinator._room_needs_job(room, self._group("hours"))


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

    async def test_refresh_after_write_honors_minimum_attempt_count(
        self, hass: HomeAssistant,
    ) -> None:
        coordinator, client = _build(hass)
        coordinator.data = {"unit_1": RoomState(target_level=10)}

        stale = RoomState(
            target_level=10,
            extract_air_flow=10,
            supply_air_flow=10,
        )
        updated = RoomState(
            target_level=50,
            extract_air_flow=50,
            supply_air_flow=50,
        )

        with (
            patch.object(client, "read_room_state", side_effect=[stale, updated]) as read_mock,
            patch(
                "custom_components.meltem_ventilation.coordinator.async_sleep",
                new=AsyncMock(),
            ),
        ):
            await coordinator._async_refresh_room_after_write(
                _ROOM_1,
                min_refresh_attempts=2,
            )

        assert read_mock.call_count == 2
        assert coordinator.data["unit_1"].target_level == 50

    async def test_refresh_after_write_failure_resets_connection(
        self, hass: HomeAssistant,
    ) -> None:
        coordinator, client = _build(hass)
        coordinator.data = {"unit_1": RoomState(target_level=10)}
        client._fail_rooms = {"unit_1"}

        await coordinator._async_refresh_room_after_write(_ROOM_1)

        assert client.reset_calls == 1
        # Data should be preserved on failure.
        assert coordinator.data["unit_1"].target_level == 10


# ---------------------------------------------------------------------------
#  _read_one_job
# ---------------------------------------------------------------------------


class TestReadOneJob:
    def test_successful_read_merges_state(
        self, hass: HomeAssistant,
    ) -> None:
        coordinator, client = _build(hass, rooms=[_ROOM_1, _ROOM_2])
        client.next_read_state = RoomState(target_level=77)
        previous = {
            "unit_1": RoomState(target_level=10),
            "unit_2": RoomState(target_level=20),
        }
        job = PollJob(
            "flow", "unit_1", RefreshPlan.only(refresh_airflow=True), 10, 0.0
        )

        result = coordinator._read_one_job(previous, job)

        assert result["unit_1"].target_level == 77
        assert result["unit_2"].target_level == 20

    def test_failed_read_preserves_previous(
        self, hass: HomeAssistant,
    ) -> None:
        coordinator, client = _build(hass)
        client._fail_rooms = {"unit_1"}
        previous = {"unit_1": RoomState(target_level=55)}
        job = PollJob(
            "flow", "unit_1", RefreshPlan.only(refresh_airflow=True), 10, 0.0
        )

        result = coordinator._read_one_job(previous, job)

        assert result["unit_1"].target_level == 55
        assert client.reset_calls == 1
        assert result["unit_1"].airflow_consecutive_failures == 1

    def test_failed_status_job_only_increments_status_health(
        self, hass: HomeAssistant,
    ) -> None:
        coordinator, client = _build(hass)
        client._fail_rooms = {"unit_1"}
        flow_health = ReadHealth(
            last_attempt=dt_util.utcnow(),
            last_successful_read=dt_util.utcnow(),
            consecutive_failures=1,
        )
        previous = {
            "unit_1": RoomState(group_read_health=(("flow", flow_health),))
        }
        job = PollJob(
            "status", "unit_1", RefreshPlan.only(refresh_status=True), 60, 0.0
        )

        result = coordinator._read_one_job(previous, job)
        state = result["unit_1"]

        assert state.read_health_for("flow") == flow_health
        assert state.read_health_for("status").consecutive_failures == 1
        assert state.read_health_for("status").last_error == "boom: unit_1"

    @pytest.mark.parametrize(
        "job_key,refresh_plan,read_groups",
        [
            (
                "flow",
                RefreshPlan.only(refresh_airflow=True),
                ("flow", "flow_control"),
            ),
            ("status", RefreshPlan.only(refresh_status=True), ("status",)),
            (
                "temperature",
                RefreshPlan.only(refresh_temperatures=True),
                ("temperature",),
            ),
            (
                "filter",
                RefreshPlan.only(refresh_filter_change_due=True),
                ("filter",),
            ),
            (
                "hours",
                RefreshPlan.only(refresh_operating_hours=True),
                ("hours",),
            ),
            (
                "control_settings",
                RefreshPlan.only(refresh_control_settings=True),
                ("control_settings",),
            ),
        ],
        ids=("flow", "status", "temperature", "filter", "hours", "control-settings"),
    )
    def test_each_read_group_failure_is_isolated_and_recovers(
        self,
        hass: HomeAssistant,
        job_key: str,
        refresh_plan: RefreshPlan,
        read_groups: tuple[str, ...],
    ) -> None:
        room = RoomConfig(
            key="unit_1", name="Unit 1", profile="ii_fc_voc", slave=2
        )
        coordinator, client = _build(hass, rooms=[room])
        neighbor_group = "status" if "status" not in read_groups else "flow"
        previous_time = dt_util.utcnow()
        neighbor_health = ReadHealth(
            last_attempt=previous_time,
            last_successful_read=previous_time,
            consecutive_failures=1,
            last_error="neighbor group failed",
        )
        group_health = {
            group_key: ReadHealth(
                last_attempt=previous_time,
                last_successful_read=previous_time,
            )
            for group_key in read_groups
        }
        group_health[neighbor_group] = neighbor_health
        previous_state = RoomState(
            target_level=55,
            group_read_health=tuple(sorted(group_health.items())),
        )
        job = PollJob(job_key, room.key, refresh_plan, 60, 0.0)
        client._fail_rooms = {room.key}

        failed_states = coordinator._read_one_job({room.key: previous_state}, job)
        failed_state = failed_states[room.key]

        assert failed_state.target_level == 55
        for group_key in read_groups:
            health = failed_state.read_health_for(group_key)
            assert health.consecutive_failures == 1
            assert health.last_error == f"boom: {room.key}"
        assert failed_state.read_health_for(neighbor_group) == neighbor_health

        client._fail_rooms.clear()
        recovered_state = failed_state
        recovery_time = dt_util.utcnow()
        for group_key in read_groups:
            recovered_state = recovered_state.with_read_health(
                group_key,
                ReadHealth(
                    last_attempt=recovery_time,
                    last_successful_read=recovery_time,
                ),
            )
        client.next_read_state = recovered_state

        recovered_states = coordinator._read_one_job(failed_states, job)
        recovered_state = recovered_states[room.key]

        for group_key in read_groups:
            health = recovered_state.read_health_for(group_key)
            assert health.consecutive_failures == 0
            assert health.last_error is None
            assert health.last_successful_read == recovery_time
        assert recovered_state.read_health_for(neighbor_group) == neighbor_health
        assert recovered_state.target_level == 55

    def test_repeated_failed_flow_reads_mark_airflow_stale(
        self, hass: HomeAssistant,
    ) -> None:
        coordinator, client = _build(hass)
        client._fail_rooms = {"unit_1"}
        state_map = {"unit_1": RoomState(target_level=55)}
        job = PollJob(
            "flow", "unit_1", RefreshPlan.only(refresh_airflow=True), 10, 0.0
        )

        for _ in range(3):
            state_map = coordinator._read_one_job(state_map, job)

        state = state_map["unit_1"]
        coordinator.data = state_map
        assert state.airflow_consecutive_failures == 3
        assert coordinator.airflow_data_stale("unit_1") is True
        assert coordinator.airflow_data_available("unit_1") is False

    def test_other_job_success_does_not_clear_airflow_failures(
        self, hass: HomeAssistant,
    ) -> None:
        coordinator, client = _build(hass)
        flow_health = ReadHealth(
            last_attempt=dt_util.utcnow(),
            consecutive_failures=2,
            last_error="airflow block read failed",
        )
        previous = RoomState(
            group_read_health=(("flow", flow_health),),
        )
        client.next_read_state = RoomState(
            error_status=False,
            group_read_health=(("flow", flow_health),),
        )
        job = PollJob(
            "status", "unit_1", RefreshPlan.only(refresh_status=True), 60, 0.0
        )

        result = coordinator._read_one_job({"unit_1": previous}, job)

        assert result["unit_1"].airflow_consecutive_failures == 2

    def test_airflow_data_becomes_stale_after_success_timestamp_expires(
        self, hass: HomeAssistant,
    ) -> None:
        coordinator, _client = _build(hass)
        coordinator.data = {
            "unit_1": RoomState(
                supply_air_flow=30,
                group_read_health=(
                    (
                        "flow",
                        ReadHealth(
                            last_attempt=dt_util.utcnow() - timedelta(seconds=31),
                            last_successful_read=dt_util.utcnow()
                            - timedelta(seconds=31),
                        ),
                    ),
                ),
            )
        }

        assert coordinator.airflow_data_stale("unit_1") is True
        assert coordinator.airflow_data_available("unit_1") is False

    def test_airflow_health_metadata_does_not_count_as_room_data(
        self, hass: HomeAssistant,
    ) -> None:
        coordinator, _client = _build(hass)

        assert not coordinator._room_state_has_data(
            RoomState(
                group_read_health=(
                    (
                        "flow",
                        ReadHealth(
                            last_attempt=dt_util.utcnow(),
                            consecutive_failures=1,
                            last_error="read failed",
                        ),
                    ),
                ),
            )
        )


# ---------------------------------------------------------------------------
#  Tick interval
# ---------------------------------------------------------------------------


class TestTickInterval:
    def test_default_rate_matches_tick_seconds(
        self, hass: HomeAssistant,
    ) -> None:
        coordinator, _ = _build(hass)
        assert coordinator.update_interval is not None
        assert coordinator.update_interval.total_seconds() == 0.5

    def test_slow_rate_yields_longer_interval(
        self, hass: HomeAssistant,
    ) -> None:
        client = _FakeClient()
        coordinator = MeltemDataUpdateCoordinator(
            hass,
            config_entry=_mock_entry(hass),
            client=client,
            rooms=[_ROOM_1],
            max_requests_per_second=0.2,
        )
        assert coordinator.update_interval is not None
        assert coordinator.update_interval.total_seconds() == 5.0
