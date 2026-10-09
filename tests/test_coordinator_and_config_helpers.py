"""Tests for coordinator logic and config-flow helper functions."""

from __future__ import annotations

import asyncio
import types
from collections.abc import Iterator
from dataclasses import replace
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)

from custom_components.meltem_ventilation.config_flow import (
    CONF_MAX_REQUESTS_PER_SECOND,
    CONF_PORT,
    CONF_ROOMS,
    MeltemVentilationOptionsFlow,
    _async_probe_units,
    _build_rooms_from_profiles,
    _default_room_name,
    _detected_profile_default,
    _profile_field_key,
    _unit_details,
)
from custom_components.meltem_ventilation.const import (
    DIRECTION_EXTRACT,
    DIRECTION_SUPPLY,
    DOMAIN,
    TARGET_OPTIMISTIC_SECONDS,
    WRITE_CONFIRMATION_TIMEOUT_SECONDS,
    WRITE_HEALTH_RETENTION_SECONDS,
)
from custom_components.meltem_ventilation.coordinator import (
    ROOM_SILENT_AFTER_SECONDS,
    ROOM_UNAVAILABLE_AFTER_FAILURES,
    SILENT_ROOM_POLL_SECONDS,
    TRANSPORT_BACKOFF_AFTER_FAILURES,
    TRANSPORT_BACKOFF_MAX_SECONDS,
    TRANSPORT_BACKOFF_START_SECONDS,
    MeltemDataUpdateCoordinator,
    PollJob,
)
from custom_components.meltem_ventilation.fan import MeltemDirectionalFanEntity
from custom_components.meltem_ventilation.modbus_helpers import MeltemModbusError
from custom_components.meltem_ventilation.models import (
    ReadHealth,
    RefreshPlan,
    RoomConfig,
    RoomState,
    WriteConfirmation,
)
from custom_components.meltem_ventilation.polling import select_due_job

# ---------------------------------------------------------------------------
#  Test doubles
# ---------------------------------------------------------------------------


class _FakeClient:
    """Stand-in for MeltemModbusClient with no serial port dependency."""

    def __init__(self) -> None:
        self.discover_calls: list[tuple[int, int]] = []
        self.probe_calls: list[int] = []
        self.read_calls: list[tuple[str, RefreshPlan]] = []
        self.write_level_calls: list[tuple[str, int]] = []
        self.write_unbalanced_calls: list[tuple[str, int, int]] = []
        self.write_operating_mode_calls: list[tuple[str, str]] = []
        self.write_preset_mode_calls: list[tuple[str, str]] = []
        self.clear_intensive_calls: list[str] = []
        self.write_control_setting_calls: list[tuple[str, str, int]] = []
        self.silent_seconds_by_slave: dict[int, float] = {}
        self.next_read_state = RoomState(target_level=42)

    async def discover_gateway_units(self, start: int, end: int) -> list[int]:
        self.discover_calls.append((start, end))
        return [2, 3, 4]

    async def probe_slave_details(self, slave: int) -> tuple[str, str | None]:
        self.probe_calls.append(slave)
        return ("plain", f"ID {slave}")

    async def read_room_state(
        self,
        room: RoomConfig,
        previous_state: RoomState,
        refresh_plan: RefreshPlan,
    ) -> RoomState:
        self.read_calls.append((room.key, refresh_plan))
        if room.key == "broken":
            raise MeltemModbusError("boom")
        state = self.next_read_state
        read_time = dt_util.utcnow()
        for group_key in refresh_plan.read_groups():
            state = state.with_read_health(
                group_key,
                ReadHealth(
                    last_attempt=read_time,
                    last_successful_read=read_time,
                ),
            )
        return state

    async def write_level(self, room: RoomConfig, level: int) -> None:
        self.write_level_calls.append((room.key, level))

    async def write_unbalanced_levels(
        self, room: RoomConfig, supply_level: int, extract_level: int
    ) -> None:
        self.write_unbalanced_calls.append((room.key, supply_level, extract_level))

    async def write_operating_mode(
        self,
        room: RoomConfig,
        operation_mode: str,
        balanced_level: int,
        extract_level: int,
    ) -> None:
        self.write_operating_mode_calls.append((room.key, operation_mode))

    async def write_preset_mode(
        self,
        room: RoomConfig,
        preset_mode: str,
    ) -> None:
        self.write_preset_mode_calls.append((room.key, preset_mode))

    async def clear_intensive(self, room: RoomConfig) -> None:
        self.clear_intensive_calls.append(room.key)

    async def write_control_setting(
        self,
        room: RoomConfig,
        setting_key: str,
        value: int,
    ) -> int:
        self.write_control_setting_calls.append((room.key, setting_key, value))
        return value

    def seconds_since_successful_read(self, slave: int) -> float | None:
        return self.silent_seconds_by_slave.get(slave)

    def update_request_rate(self, max_requests_per_second: float) -> None:
        self.max_requests_per_second = max_requests_per_second


_SLEEP = "custom_components.meltem_ventilation.coordinator.async_sleep"
_UNIT_1 = RoomConfig(key="unit_1", name="Unit 1", profile="ii_plain", slave=2)
# Humidity units are the ones that have control settings.
_UNIT_F = RoomConfig(key="unit_1", name="Unit 1", profile="ii_f", slave=2)
_HUMIDITY_START_WRITE = "control_setting:humidity_starting_point"


@pytest.fixture(autouse=True)
def _skip_settle_delays() -> Iterator[None]:
    with patch(_SLEEP, new=AsyncMock()):
        yield


def _failing(message: str = "boom") -> AsyncMock:
    return AsyncMock(side_effect=MeltemModbusError(message))


def _with_fresh_read_groups(state: RoomState, *group_keys: str) -> RoomState:
    read_at = dt_util.utcnow()
    for group_key in group_keys:
        state = state.with_read_health(
            group_key,
            ReadHealth(last_attempt=read_at, last_successful_read=read_at),
        )
    return state


def _fresh(state: RoomState) -> dict[str, RoomState]:
    return {"unit_1": _with_fresh_read_groups(state, "flow", "flow_control")}


def _control_settings_read_at(read_at: datetime) -> tuple[tuple[str, ReadHealth], ...]:
    return (("control_settings", ReadHealth(last_attempt=read_at, last_successful_read=read_at)),)


def _mock_entry(hass: HomeAssistant) -> MockConfigEntry:
    entry = MockConfigEntry(domain=DOMAIN, title="Meltem", version=1, source="user")
    entry.add_to_hass(hass)
    return entry


def _build_coordinator(
    hass: HomeAssistant,
    rooms: list[RoomConfig],
) -> tuple[MeltemDataUpdateCoordinator, _FakeClient]:
    """Create a coordinator with a fake client."""
    client = _FakeClient()
    coordinator = MeltemDataUpdateCoordinator(
        hass,
        config_entry=_mock_entry(hass),
        client=client,
        rooms=rooms,
        max_requests_per_second=2.0,
    )
    return coordinator, client


def _start_backoff(coordinator: MeltemDataUpdateCoordinator) -> None:
    for _ in range(TRANSPORT_BACKOFF_AFTER_FAILURES):
        coordinator._on_transport_failure()


# ---------------------------------------------------------------------------
#  Resilience: backoff, idle ticks, per-room availability
# ---------------------------------------------------------------------------


class TestCoordinatorResilience:
    def test_polling_backs_off_after_repeated_transport_failures(
        self, hass: HomeAssistant,
    ) -> None:
        coordinator, _ = _build_coordinator(hass, [_UNIT_1])

        _start_backoff(coordinator)

        assert coordinator.update_interval.total_seconds() == TRANSPORT_BACKOFF_START_SECONDS

        for _ in range(1100):
            coordinator._on_transport_failure()

        assert coordinator.update_interval.total_seconds() == TRANSPORT_BACKOFF_MAX_SECONDS

        coordinator._on_transport_success()
        assert coordinator._backoff_seconds is None
        assert coordinator.update_interval.total_seconds() < TRANSPORT_BACKOFF_START_SECONDS

    def test_request_rate_change_does_not_cancel_active_backoff(
        self, hass: HomeAssistant,
    ) -> None:
        coordinator, client = _build_coordinator(hass, [_UNIT_1])
        _start_backoff(coordinator)

        coordinator.update_request_rate(1.0)

        assert coordinator.update_interval.total_seconds() == TRANSPORT_BACKOFF_START_SECONDS
        assert client.max_requests_per_second == 1.0

    def test_room_becomes_unavailable_after_repeated_read_failures(
        self, hass: HomeAssistant,
    ) -> None:
        coordinator, _ = _build_coordinator(hass, [_UNIT_1])
        coordinator.data = {"unit_1": RoomState(target_level=30)}
        assert coordinator.room_available("unit_1")

        coordinator._room_failures["unit_1"] = ROOM_UNAVAILABLE_AFTER_FAILURES
        assert not coordinator.room_available("unit_1")

    def test_room_without_any_data_is_unavailable(self, hass: HomeAssistant) -> None:
        coordinator, _ = _build_coordinator(hass, [_UNIT_1])
        coordinator.data = {"unit_1": RoomState()}

        assert not coordinator.room_available("unit_1")

    def test_room_is_unavailable_before_the_first_poll(
        self, hass: HomeAssistant
    ) -> None:
        coordinator, _ = _build_coordinator(hass, [_UNIT_1])

        assert coordinator.data is None
        assert not coordinator.room_available("unit_1")

    def test_state_room_count_ignores_empty_room_states(
        self, hass: HomeAssistant
    ) -> None:
        coordinator, _ = _build_coordinator(
            hass,
            [
                RoomConfig(key="empty", name="Empty", profile="ii_plain", slave=2),
                RoomConfig(key="live", name="Live", profile="ii_plain", slave=3),
            ],
        )
        coordinator.data = {
            "empty": RoomState(),
            "live": RoomState(target_level=30),
        }

        assert coordinator.state_room_count == 1

    def test_silent_unit_becomes_unavailable_despite_cached_values(
        self, hass: HomeAssistant,
    ) -> None:
        """Optional reads swallow errors, so cached values alone prove nothing."""
        coordinator, client = _build_coordinator(hass, [_UNIT_1])
        coordinator.data = {"unit_1": RoomState(target_level=30)}

        client.silent_seconds_by_slave[2] = ROOM_SILENT_AFTER_SECONDS - 1
        assert coordinator.room_available("unit_1")

        client.silent_seconds_by_slave[2] = ROOM_SILENT_AFTER_SECONDS + 1
        assert not coordinator.room_available("unit_1")

    async def test_failed_job_marks_only_the_affected_room(
        self, hass: HomeAssistant
    ) -> None:
        rooms = [
            RoomConfig(key="broken", name="Broken", profile="ii_plain", slave=2),
            RoomConfig(key="unit_2", name="Unit 2", profile="ii_plain", slave=3),
        ]
        coordinator, _ = _build_coordinator(hass, rooms)
        previous = {"broken": RoomState(target_level=10), "unit_2": RoomState(target_level=20)}

        for _ in range(ROOM_UNAVAILABLE_AFTER_FAILURES):
            await coordinator._read_one_job(
                previous,
                PollJob(
                    key="flow_broken",
                    room_key="broken",
                    refresh_plan=RefreshPlan.only(refresh_airflow=True),
                    interval_seconds=10,
                    next_due=0.0,
                ),
            )

        coordinator.data = previous
        assert not coordinator.room_available("broken")
        assert coordinator.room_available("unit_2")

    async def test_failing_jobs_trigger_the_polling_backoff(
        self, hass: HomeAssistant,
    ) -> None:
        """_read_one_job swallows the error, so success must not be assumed."""
        coordinator, _ = _build_coordinator(
            hass,
            [RoomConfig(key="broken", name="Broken", profile="ii_plain", slave=2)],
        )
        coordinator.data = {"broken": RoomState(target_level=30)}
        normal_interval = coordinator.update_interval

        for _ in range(TRANSPORT_BACKOFF_AFTER_FAILURES):
            for job in coordinator._jobs:
                job.next_due = 0.0
            # Lift the request-rate cap so every call runs a job.
            coordinator._last_read_started = None
            await coordinator._async_update_data()

        assert coordinator.update_interval != normal_interval
        assert (
            coordinator.update_interval.total_seconds()
            >= TRANSPORT_BACKOFF_START_SECONDS
        )

    async def test_successful_job_restores_the_normal_rate(
        self, hass: HomeAssistant,
    ) -> None:
        coordinator, _ = _build_coordinator(hass, [_UNIT_1])
        coordinator.data = {"unit_1": RoomState(target_level=30)}
        _start_backoff(coordinator)

        for job in coordinator._jobs:
            job.next_due = 0.0
        await coordinator._async_update_data()

        assert coordinator._backoff_seconds is None
        assert coordinator.update_interval.total_seconds() < TRANSPORT_BACKOFF_START_SECONDS


# ---------------------------------------------------------------------------
#  Airflow target levels
# ---------------------------------------------------------------------------


class TestEffectiveLevels:
    def test_balanced_mode_reports_one_value_for_both_directions(
        self, hass: HomeAssistant,
    ) -> None:
        coordinator, _ = _build_coordinator(hass, [_UNIT_1])
        coordinator.data = {
            "unit_1": _with_fresh_read_groups(
                RoomState(operation_mode="manual", target_level=45),
                "flow_control",
            )
        }

        assert coordinator.effective_levels("unit_1") == (45, 45)

    def test_unbalanced_mode_reports_both_targets(self, hass: HomeAssistant) -> None:
        coordinator, _ = _build_coordinator(hass, [_UNIT_1])
        coordinator.data = {
            "unit_1": _with_fresh_read_groups(
                RoomState(
                    operation_mode="unbalanced",
                    target_level=60,
                    extract_target_level=20,
                ),
                "flow_control",
            )
        }

        assert coordinator.effective_levels("unit_1") == (60, 20)

    def test_off_reports_zero(self, hass: HomeAssistant) -> None:
        coordinator, _ = _build_coordinator(hass, [_UNIT_1])
        coordinator.data = {
            "unit_1": _with_fresh_read_groups(
                RoomState(operation_mode="off", target_level=40), "flow_control"
            )
        }

        assert coordinator.effective_levels("unit_1") == (0, 0)

    def test_falls_back_to_measured_airflow(self, hass: HomeAssistant) -> None:
        coordinator, _ = _build_coordinator(hass, [_UNIT_1])
        coordinator.data = {
            "unit_1": _with_fresh_read_groups(
                RoomState(
                    operation_mode="unbalanced",
                    supply_air_flow=55,
                    extract_air_flow=25,
                ),
                "flow_control",
                "flow",
            )
        }

        assert coordinator.effective_levels("unit_1") == (55, 25)

    @pytest.mark.parametrize(
        "sensor_mode", ["humidity_control", "co2_control", "automatic"]
    )
    def test_sensor_modes_report_measured_airflow(
        self, hass: HomeAssistant, sensor_mode: str,
    ) -> None:
        """41121 holds the mode selector there, so it must not become a level."""
        coordinator, _ = _build_coordinator(hass, [_UNIT_1])
        coordinator.data = {
            "unit_1": _with_fresh_read_groups(
                RoomState(
                    operation_mode=sensor_mode,
                    target_level=56,
                    supply_air_flow=22,
                    extract_air_flow=21,
                ),
                "flow_control",
                "flow",
            )
        }

        assert coordinator.effective_levels("unit_1") == (22, 21)

    def test_unknown_mode_uses_independent_fresh_airflow_values(
        self, hass: HomeAssistant,
    ) -> None:
        coordinator, _ = _build_coordinator(hass, [_UNIT_1])
        coordinator.data = {
            "unit_1": _with_fresh_read_groups(
                RoomState(
                    target_level=56,
                    supply_air_flow=22,
                    extract_air_flow=21,
                ),
                "flow_control",
                "flow",
            )
        }

        assert coordinator.effective_levels("unit_1") == (22, 21)

    def test_unknown_mode_does_not_duplicate_one_fresh_airflow_value(
        self, hass: HomeAssistant,
    ) -> None:
        coordinator, _ = _build_coordinator(hass, [_UNIT_1])
        coordinator.data = {
            "unit_1": _with_fresh_read_groups(
                RoomState(target_level=56, supply_air_flow=22), "flow"
            )
        }

        assert coordinator.effective_levels("unit_1") == (22, None)

    def test_unknown_mode_does_not_use_stale_airflow_values(
        self, hass: HomeAssistant,
    ) -> None:
        coordinator, _ = _build_coordinator(hass, [_UNIT_1])
        failed_at = dt_util.utcnow()
        stale_at = failed_at - timedelta(seconds=40)
        state = RoomState(
            supply_air_flow=22,
            extract_air_flow=21,
        ).with_read_health(
            "flow",
            ReadHealth(
                last_attempt=failed_at,
                last_successful_read=stale_at,
                consecutive_failures=1,
                last_error="flow read failed",
            ),
        )
        coordinator.data = {"unit_1": state}

        assert coordinator.effective_levels("unit_1") == (None, None)

    def test_pending_write_wins_over_stale_state(self, hass: HomeAssistant) -> None:
        coordinator, _ = _build_coordinator(hass, [_UNIT_1])
        coordinator.data = {
            "unit_1": RoomState(operation_mode="manual", target_level=40)
        }

        coordinator._optimistic_levels.set("unit_1", (80, 20))

        assert coordinator.effective_levels("unit_1") == (80, 20)

    def test_overlay_is_dropped_once_the_gateway_confirms(
        self, hass: HomeAssistant,
    ) -> None:
        coordinator, _ = _build_coordinator(hass, [_UNIT_1])
        coordinator._optimistic_levels.set("unit_1", (80, 20))
        coordinator.data = {
            "unit_1": _with_fresh_read_groups(
                RoomState(
                    operation_mode="unbalanced",
                    target_level=80,
                    extract_target_level=20,
                ),
                "flow_control",
            )
        }

        assert coordinator.effective_levels("unit_1") == (80, 20)
        coordinator._confirm_pending_writes(coordinator.data)
        assert coordinator._optimistic_levels.get("unit_1", None) is None
        assert coordinator.level_source("unit_1") == "target"

    def test_overlay_tolerates_rounding_between_percent_and_raw(
        self, hass: HomeAssistant,
    ) -> None:
        coordinator, _ = _build_coordinator(hass, [_UNIT_1])
        coordinator._optimistic_levels.set("unit_1", (80, 20))
        coordinator.data = {
            "unit_1": _with_fresh_read_groups(
                RoomState(
                    operation_mode="unbalanced",
                    target_level=79,
                    extract_target_level=21,
                ),
                "flow_control",
            )
        }

        coordinator._confirm_pending_writes(coordinator.data)
        assert coordinator._optimistic_levels.get("unit_1", None) is None

    def test_reading_the_levels_leaves_the_overlay_in_place(
        self, hass: HomeAssistant,
    ) -> None:
        coordinator, _ = _build_coordinator(hass, [_UNIT_1])
        coordinator._optimistic_levels.set("unit_1", (80, 20))
        coordinator.data = {
            "unit_1": _with_fresh_read_groups(
                RoomState(
                    operation_mode="unbalanced", target_level=80, extract_target_level=20
                ),
                "flow_control",
            )
        }

        coordinator.effective_levels("unit_1")

        assert "unit_1" in coordinator._optimistic_levels._pending

    async def test_an_expired_overlay_updates_the_entities(
        self, hass: HomeAssistant,
    ) -> None:
        coordinator, _ = _build_coordinator(hass, [_UNIT_1])
        updates: list[None] = []
        coordinator.async_add_listener(lambda: updates.append(None))
        coordinator._optimistic_levels.set("unit_1", (80, 20))
        updates.clear()

        async_fire_time_changed(
            hass, dt_util.utcnow() + timedelta(seconds=TARGET_OPTIMISTIC_SECONDS + 1)
        )
        await hass.async_block_till_done()

        assert "unit_1" not in coordinator._optimistic_levels._pending
        assert updates

    async def test_shutdown_drops_pending_values_and_their_timers(
        self, hass: HomeAssistant,
    ) -> None:
        coordinator, _ = _build_coordinator(hass, [_UNIT_1])
        coordinator._optimistic_presets.set("unit_1", "high")

        await coordinator.async_shutdown()

        assert coordinator.optimistic_preset_mode("unit_1") is None
        assert not coordinator._optimistic_presets._cancel_expiry

    def test_overlay_expires(self, hass: HomeAssistant) -> None:
        coordinator, _ = _build_coordinator(hass, [_UNIT_1])
        coordinator.data = {
            "unit_1": _with_fresh_read_groups(
                RoomState(operation_mode="manual", target_level=40), "flow_control"
            )
        }
        coordinator._optimistic_levels._pending["unit_1"] = ((80, 20), 0.0)

        assert coordinator.effective_levels("unit_1") == (40, 40)

    async def test_writes_show_pending_levels_and_reconcile_write_failures(
        self, hass: HomeAssistant,
    ) -> None:
        coordinator, client = _build_coordinator(hass, [_UNIT_1])
        coordinator.data = {
            "unit_1": _with_fresh_read_groups(
                RoomState(operation_mode="manual", target_level=40), "flow_control"
            )
        }

        await coordinator.async_set_unbalanced_levels("unit_1", 70, 30)
        assert coordinator.effective_levels("unit_1") == (70, 30)
        assert coordinator.level_source("unit_1") == "pending"
        assert (
            coordinator._writes.by_room["unit_1"]["airflow_levels"].status
            == "pending"
        )

        client.write_unbalanced_levels = _failing()
        client.next_read_state = RoomState(operation_mode="manual", target_level=40)
        with pytest.raises(HomeAssistantError) as err:
            await coordinator.async_set_unbalanced_levels("unit_1", 10, 90)

        assert err.value.translation_key == "write_failed"
        assert coordinator.effective_levels("unit_1") == (40, 40)
        assert client.read_calls[-1][1] == RefreshPlan.only(refresh_airflow=True)
        assert coordinator._writes.by_room["unit_1"]["airflow_levels"].status == "failed"

    async def test_mode_change_discards_a_pending_overlay(
        self, hass: HomeAssistant,
    ) -> None:
        coordinator, _ = _build_coordinator(hass, [_UNIT_1])
        coordinator.data = {
            "unit_1": RoomState(operation_mode="manual", target_level=40)
        }
        coordinator._optimistic_levels.set("unit_1", (80, 20))

        await coordinator.async_set_operation_mode("unit_1", "co2_control")

        assert coordinator._optimistic_levels.get("unit_1", None) is None

    async def test_manual_mode_without_any_known_airflow_is_refused(
        self, hass: HomeAssistant,
    ) -> None:
        """Guessing 0 m3/h would stop the unit instead of changing its mode."""
        coordinator, client = _build_coordinator(hass, [_UNIT_1])
        coordinator.data = {"unit_1": RoomState(operation_mode="co2_control")}

        with pytest.raises(HomeAssistantError) as err:
            await coordinator.async_set_operation_mode("unit_1", "manual")

        assert err.value.translation_key == "airflow_unknown"
        assert client.write_operating_mode_calls == []

    @pytest.mark.parametrize("mode", ["off", "manual", "unbalanced"])
    async def test_inactive_keeps_a_fresh_direct_mode(
        self, hass: HomeAssistant, mode: str,
    ) -> None:
        coordinator, client = _build_coordinator(hass, [_UNIT_1])
        coordinator.data = _fresh(
            RoomState(operation_mode=mode, target_level=40, extract_target_level=20)
        )

        await coordinator.async_set_operation_mode("unit_1", "inactive")

        assert client.write_operating_mode_calls == []

    async def test_inactive_does_not_skip_a_stale_direct_mode(
        self, hass: HomeAssistant,
    ) -> None:
        coordinator, client = _build_coordinator(hass, [_UNIT_1])
        coordinator.data = {
            "unit_1": _with_fresh_read_groups(
                RoomState(operation_mode="manual", supply_air_flow=60, extract_air_flow=60),
                "flow",
            )
        }

        await coordinator.async_set_operation_mode("unit_1", "inactive")

        assert client.write_operating_mode_calls == [("unit_1", "manual")]


class TestDirectionalWrites:
    """One fan direction changes without disturbing the other one."""

    async def test_supply_write_keeps_the_extract_target(
        self, hass: HomeAssistant,
    ) -> None:
        coordinator, client = _build_coordinator(hass, [_UNIT_1])
        coordinator.data = _fresh(
            RoomState(
                operation_mode="unbalanced", target_level=40, extract_target_level=30
            )
        )

        await coordinator.async_set_direction_level("unit_1", DIRECTION_SUPPLY, 60)

        assert client.write_unbalanced_calls == [("unit_1", 60, 30)]

    async def test_extract_write_keeps_the_supply_target(
        self, hass: HomeAssistant,
    ) -> None:
        coordinator, client = _build_coordinator(hass, [_UNIT_1])
        coordinator.data = _fresh(
            RoomState(
                operation_mode="unbalanced", target_level=40, extract_target_level=30
            )
        )

        await coordinator.async_set_direction_level("unit_1", DIRECTION_EXTRACT, 60)

        assert client.write_unbalanced_calls == [("unit_1", 40, 60)]

    async def test_turning_off_one_direction_keeps_the_unit_running(
        self, hass: HomeAssistant,
    ) -> None:
        coordinator, client = _build_coordinator(hass, [_UNIT_1])
        coordinator.data = _fresh(RoomState(operation_mode="manual", target_level=40))

        await coordinator.async_set_direction_level("unit_1", DIRECTION_SUPPLY, 0)

        assert client.write_unbalanced_calls == [("unit_1", 0, 40)]
        assert client.write_level_calls == []

    async def test_turning_off_the_last_direction_switches_the_unit_off(
        self, hass: HomeAssistant,
    ) -> None:
        coordinator, client = _build_coordinator(hass, [_UNIT_1])
        coordinator.data = _fresh(
            RoomState(
                operation_mode="unbalanced", target_level=40, extract_target_level=0
            )
        )

        await coordinator.async_set_direction_level("unit_1", DIRECTION_SUPPLY, 0)

        assert client.write_level_calls == [("unit_1", 0)]
        assert client.write_unbalanced_calls == []

    @pytest.mark.parametrize("extract_level", [40, 41])
    async def test_matching_levels_are_written_as_balanced(
        self, hass: HomeAssistant, extract_level: int,
    ) -> None:
        """One m3/h apart is a rounding artifact, not an intended imbalance."""
        coordinator, client = _build_coordinator(hass, [_UNIT_1])
        coordinator.data = _fresh(
            RoomState(
                operation_mode="unbalanced", target_level=40, extract_target_level=60
            )
        )

        await coordinator.async_set_direction_level(
            "unit_1", DIRECTION_EXTRACT, extract_level
        )

        assert client.write_level_calls == [("unit_1", extract_level)]
        assert client.write_unbalanced_calls == []

    async def test_starting_from_off_runs_both_directions(
        self, hass: HomeAssistant,
    ) -> None:
        coordinator, client = _build_coordinator(hass, [_UNIT_1])
        coordinator.data = _fresh(RoomState(operation_mode="off"))

        await coordinator.async_set_direction_level("unit_1", DIRECTION_SUPPLY, 50)

        assert client.write_level_calls == [("unit_1", 50)]

    async def test_leaving_sensor_control_writes_a_balanced_level(
        self, hass: HomeAssistant,
    ) -> None:
        coordinator, client = _build_coordinator(hass, [_UNIT_1])
        coordinator.data = _fresh(
            RoomState(
                operation_mode="co2_control", supply_air_flow=48, extract_air_flow=51
            )
        )

        await coordinator.async_set_direction_level("unit_1", DIRECTION_SUPPLY, 60)

        assert client.write_level_calls == [("unit_1", 60)]

    async def test_turning_one_direction_off_under_sensor_control_keeps_the_unit_running(
        self, hass: HomeAssistant,
    ) -> None:
        coordinator, client = _build_coordinator(hass, [_UNIT_1])
        coordinator.data = _fresh(
            RoomState(
                operation_mode="co2_control", supply_air_flow=48, extract_air_flow=51
            )
        )

        await coordinator.async_set_direction_level("unit_1", DIRECTION_SUPPLY, 0)

        assert client.write_unbalanced_calls == [("unit_1", 0, 51)]

    async def test_close_measured_airflows_count_as_balanced(
        self, hass: HomeAssistant,
    ) -> None:
        """Without a target readback, measurement jitter must not force unbalanced."""
        coordinator, client = _build_coordinator(hass, [_UNIT_1])
        coordinator.data = {
            "unit_1": _with_fresh_read_groups(
                RoomState(
                    operation_mode="manual", supply_air_flow=60, extract_air_flow=58
                ),
                "flow",
            )
        }
        assert coordinator.level_source("unit_1") == "measured"

        await coordinator.async_set_direction_level("unit_1", DIRECTION_SUPPLY, 60)

        assert client.write_level_calls == [("unit_1", 60)]
        assert client.write_unbalanced_calls == []

    async def test_unknown_opposite_uses_a_balanced_fallback(
        self, hass: HomeAssistant,
    ) -> None:
        coordinator, client = _build_coordinator(hass, [_UNIT_1])
        coordinator.data = {"unit_1": RoomState(operation_mode="manual")}

        await coordinator.async_set_direction_level("unit_1", DIRECTION_SUPPLY, 60)

        assert client.write_level_calls == [("unit_1", 60)]
        assert (
            coordinator.level_write_fallback("unit_1")
            == "both_directions_balanced_manual"
        )

    async def test_unknown_opposite_refuses_to_switch_off_one_direction(
        self, hass: HomeAssistant,
    ) -> None:
        coordinator, client = _build_coordinator(hass, [_UNIT_1])
        coordinator.data = {"unit_1": RoomState(operation_mode="manual")}

        with pytest.raises(HomeAssistantError) as err:
            await coordinator.async_set_direction_level("unit_1", DIRECTION_SUPPLY, 0)

        assert err.value.translation_key == "opposite_airflow_unknown"
        assert client.write_level_calls == []
        assert client.write_unbalanced_calls == []

    async def test_unknown_mode_is_reported_after_an_explicit_write(
        self, hass: HomeAssistant,
    ) -> None:
        coordinator, client = _build_coordinator(hass, [_UNIT_1])
        coordinator.data = _fresh(RoomState(supply_air_flow=40, extract_air_flow=30))

        await coordinator.async_set_direction_level("unit_1", DIRECTION_SUPPLY, 60)

        assert client.write_unbalanced_calls == [("unit_1", 60, 30)]
        assert coordinator.level_write_fallback("unit_1") == "unknown_mode_overridden"

    async def test_fallback_is_cleared_by_the_next_successful_readback(
        self, hass: HomeAssistant,
    ) -> None:
        coordinator, _ = _build_coordinator(hass, [_UNIT_1])
        coordinator.data = {"unit_1": RoomState(operation_mode="manual")}
        await coordinator.async_set_direction_level("unit_1", DIRECTION_SUPPLY, 60)
        assert coordinator.level_write_fallback("unit_1") is not None

        coordinator.data = _fresh(RoomState(operation_mode="manual", target_level=60))

        assert coordinator.level_write_fallback("unit_1") is None

    async def test_fan_shows_the_pending_level_right_after_a_write(
        self, hass: HomeAssistant,
    ) -> None:
        coordinator, _ = _build_coordinator(hass, [_UNIT_1])
        coordinator.data = _fresh(RoomState(operation_mode="manual", target_level=60))
        supply = MeltemDirectionalFanEntity(coordinator, _UNIT_1, DIRECTION_SUPPLY)

        await supply.async_set_percentage(80)

        assert supply.percentage == 80
        assert supply.extra_state_attributes["level_source"] == "pending"

    async def test_successive_fan_commands_build_on_each_other(
        self, hass: HomeAssistant,
    ) -> None:
        coordinator, client = _build_coordinator(hass, [_UNIT_1])
        coordinator.data = _fresh(RoomState(operation_mode="manual", target_level=60))
        supply = MeltemDirectionalFanEntity(coordinator, _UNIT_1, DIRECTION_SUPPLY)
        extract = MeltemDirectionalFanEntity(coordinator, _UNIT_1, DIRECTION_EXTRACT)

        await supply.async_set_percentage(40)
        await extract.async_set_percentage(40)

        assert client.write_unbalanced_calls == [("unit_1", 40, 60)]
        assert client.write_level_calls == [("unit_1", 40)]

    async def test_concurrent_fan_commands_from_a_scene_end_balanced(
        self, hass: HomeAssistant,
    ) -> None:
        coordinator, client = _build_coordinator(hass, [_UNIT_1])
        coordinator.data = _fresh(RoomState(operation_mode="manual", target_level=60))
        supply = MeltemDirectionalFanEntity(coordinator, _UNIT_1, DIRECTION_SUPPLY)
        extract = MeltemDirectionalFanEntity(coordinator, _UNIT_1, DIRECTION_EXTRACT)

        await asyncio.gather(
            supply.async_set_percentage(40), extract.async_set_percentage(40)
        )

        assert client.write_level_calls[-1] == ("unit_1", 40)
        assert coordinator.effective_levels("unit_1") == (40, 40)

    @pytest.mark.parametrize(
        ("change", "readback"),
        [
            (
                "preset",
                RoomState(
                    operation_mode="manual",
                    preset_mode="high",
                    target_level=80,
                    supply_air_flow=80,
                    extract_air_flow=80,
                ),
            ),
            (
                "co2_control",
                RoomState(
                    operation_mode="co2_control", supply_air_flow=80, extract_air_flow=80
                ),
            ),
            ("off", RoomState(operation_mode="off", target_level=0)),
        ],
    )
    async def test_direction_write_waits_for_a_pending_mode_readback(
        self, hass: HomeAssistant, change: str, readback: RoomState,
    ) -> None:
        coordinator, client = _build_coordinator(hass, [_UNIT_1])
        coordinator.data = _fresh(RoomState(operation_mode="manual", target_level=30))
        client.next_read_state = readback
        change_started = asyncio.Event()
        release_change = asyncio.Event()
        fan_started = asyncio.Event()
        method_name = "write_preset_mode" if change == "preset" else "write_operating_mode"
        original_write = getattr(client, method_name)

        async def delayed_write(*args: object) -> None:
            change_started.set()
            await release_change.wait()
            await original_write(*args)

        async def write_direction() -> None:
            fan_started.set()
            await coordinator.async_set_direction_level("unit_1", DIRECTION_EXTRACT, 40)

        with patch.object(client, method_name, new=delayed_write):
            change_task = asyncio.create_task(
                coordinator.async_set_preset_mode("unit_1", "high")
                if change == "preset"
                else coordinator.async_set_operation_mode("unit_1", change)
            )
            await change_started.wait()
            fan_task = asyncio.create_task(write_direction())
            await fan_started.wait()
            release_change.set()
            await asyncio.gather(change_task, fan_task)

        if change == "preset":
            assert client.write_unbalanced_calls == [("unit_1", 80, 40)]
            assert client.write_level_calls == []
        else:
            assert client.write_level_calls == [("unit_1", 40)]
            assert client.write_unbalanced_calls == []

    @pytest.mark.parametrize("operation_mode", ["off", "humidity_control", "co2_control", "automatic"])
    @pytest.mark.parametrize("concurrent", [False, True], ids=("successive", "concurrent"))
    @pytest.mark.parametrize("first_level", [40, 60], ids=("matches-measurement", "new-airflow"))
    async def test_followup_direction_write_uses_the_pending_mode(
        self, hass: HomeAssistant, operation_mode: str, concurrent: bool, first_level: int,
    ) -> None:
        coordinator, client = _build_coordinator(hass, [_UNIT_1])
        coordinator.data = _fresh(
            RoomState(operation_mode=operation_mode, supply_air_flow=40, extract_air_flow=40)
        )
        write_level = client.write_level

        async def _yielding_write_level(room: RoomConfig, level: int) -> None:
            await asyncio.sleep(0)
            await write_level(room, level)

        client.write_level = _yielding_write_level
        if concurrent:
            await asyncio.gather(
                coordinator.async_set_direction_level("unit_1", DIRECTION_SUPPLY, first_level),
                coordinator.async_set_direction_level("unit_1", DIRECTION_EXTRACT, 30),
            )
        else:
            await coordinator.async_set_direction_level("unit_1", DIRECTION_SUPPLY, first_level)
            await coordinator.async_set_direction_level("unit_1", DIRECTION_EXTRACT, 30)

        assert client.write_level_calls == [("unit_1", first_level)]
        assert client.write_unbalanced_calls == [("unit_1", first_level, 30)]
        assert coordinator.effective_levels("unit_1") == (first_level, 30)

    async def test_switching_off_one_direction_after_start_keeps_the_other_running(
        self, hass: HomeAssistant,
    ) -> None:
        coordinator, client = _build_coordinator(hass, [_UNIT_1])
        coordinator.data = _fresh(RoomState(operation_mode="off"))

        await coordinator.async_set_direction_level("unit_1", DIRECTION_SUPPLY, 60)
        await coordinator.async_set_direction_level("unit_1", DIRECTION_EXTRACT, 0)

        assert client.write_level_calls == [("unit_1", 60)]
        assert client.write_unbalanced_calls == [("unit_1", 60, 0)]
        assert coordinator.effective_levels("unit_1") == (60, 0)

    @pytest.mark.parametrize("initial_level", [0, 40])
    async def test_restart_after_a_pending_off_write_starts_both_directions(
        self, hass: HomeAssistant, initial_level: int,
    ) -> None:
        coordinator, client = _build_coordinator(hass, [_UNIT_1])
        coordinator.data = _fresh(RoomState(operation_mode="manual", target_level=initial_level))

        await coordinator.async_set_level("unit_1", 0)
        await coordinator.async_set_direction_level("unit_1", DIRECTION_SUPPLY, 60)

        assert client.write_level_calls == [("unit_1", 0), ("unit_1", 60)]
        assert client.write_unbalanced_calls == []
        assert coordinator.effective_levels("unit_1") == (60, 60)


class TestAirflowWriteConfirmation:
    @pytest.mark.parametrize(
        ("initial_key", "replacement_key"),
        [
            ("airflow_levels", "operation_mode"),
            ("airflow_levels", "preset_mode"),
            ("operation_mode", "airflow_levels"),
            ("operation_mode", "preset_mode"),
            ("preset_mode", "airflow_levels"),
            ("preset_mode", "operation_mode"),
        ],
    )
    async def test_replaced_base_write_does_not_flag_data_health(
        self, hass: HomeAssistant, initial_key: str, replacement_key: str,
    ) -> None:
        coordinator, client = _build_coordinator(hass, [_UNIT_1])
        coordinator.data = _fresh(RoomState(operation_mode="manual", target_level=40))
        client.next_read_state = coordinator.safe_data["unit_1"]
        if initial_key == "airflow_levels":
            await coordinator.async_set_level("unit_1", 70)
        elif initial_key == "operation_mode":
            await coordinator.async_set_operation_mode("unit_1", "co2_control")
        else:
            await coordinator.async_set_preset_mode("unit_1", "low")

        if replacement_key == "airflow_levels":
            await coordinator.async_set_level("unit_1", 80)
        elif replacement_key == "operation_mode":
            client.next_read_state = RoomState(operation_mode="automatic")
            await coordinator.async_set_operation_mode("unit_1", "automatic")
        else:
            client.next_read_state = RoomState(operation_mode="manual", preset_mode="medium")
            await coordinator.async_set_preset_mode("unit_1", "medium")

        confirmation = coordinator._writes.by_room["unit_1"][initial_key]
        coordinator._writes.by_room["unit_1"][initial_key] = replace(
            confirmation,
            started_at=dt_util.utcnow() - timedelta(seconds=WRITE_CONFIRMATION_TIMEOUT_SECONDS + 1),
        )
        readback_level = 80 if replacement_key == "airflow_levels" else 70
        coordinator._confirm_pending_writes(_fresh(
            RoomState(
                operation_mode="manual",
                target_level=readback_level,
                balanced_target_readback=readback_level,
            )
        ))

        assert coordinator.data_health_attributes("unit_1")["writes"][initial_key]["status"] == (
            "superseded"
        )
        assert coordinator.data_health_stale("unit_1") is False
        if initial_key == "preset_mode":
            assert coordinator.optimistic_preset_mode("unit_1") is None

    async def test_failed_replacement_does_not_supersede_the_previous_command(
        self, hass: HomeAssistant,
    ) -> None:
        coordinator, client = _build_coordinator(hass, [_UNIT_1])
        coordinator.data = _fresh(RoomState(operation_mode="manual", target_level=40))
        await coordinator.async_set_level("unit_1", 70)
        client.write_operating_mode = _failing("write failed")
        client.read_room_state = _failing("readback failed")

        with pytest.raises(HomeAssistantError):
            await coordinator.async_set_operation_mode("unit_1", "automatic")

        confirmations = coordinator._writes.by_room["unit_1"]
        assert confirmations["airflow_levels"].status == "unconfirmed"
        assert confirmations["operation_mode"].status == "failed"
        assert coordinator.data_health_stale("unit_1") is True

    async def test_base_write_keeps_independent_write_confirmations(
        self, hass: HomeAssistant,
    ) -> None:
        coordinator, _ = _build_coordinator(hass, [_UNIT_1])
        coordinator.data = _fresh(RoomState(operation_mode="manual", target_level=40))
        coordinator._writes.record_pending("unit_1", "intensive", True)
        coordinator._writes.record_pending("unit_1", _HUMIDITY_START_WRITE, 70)

        await coordinator.async_set_level("unit_1", 60)

        confirmations = coordinator._writes.by_room["unit_1"]
        assert confirmations["intensive"].status == "pending"
        assert confirmations[_HUMIDITY_START_WRITE].status == "pending"

    async def test_intensive_write_does_not_supersede_a_base_command(
        self, hass: HomeAssistant,
    ) -> None:
        coordinator, client = _build_coordinator(hass, [_UNIT_1])
        coordinator.data = _fresh(RoomState(operation_mode="manual", target_level=40))
        await coordinator.async_set_level("unit_1", 70)
        client.next_read_state = RoomState(
            operation_mode="manual", target_level=40, intensive_active=True
        )

        await coordinator.async_activate_intensive("unit_1")

        confirmations = coordinator._writes.by_room["unit_1"]
        assert confirmations["airflow_levels"].status == "unverifiable"
        assert confirmations["intensive"].status == "confirmed"

        coordinator._confirm_pending_writes(
            _fresh(
                RoomState(
                    operation_mode="manual",
                    target_level=70,
                    balanced_target_readback=70,
                    intensive_active=False,
                )
            )
        )

        assert confirmations["airflow_levels"].status == "confirmed"

    async def test_poll_after_a_fan_write_confirms_it_without_failing_the_update(
        self, hass: HomeAssistant,
    ) -> None:
        coordinator, client = _build_coordinator(hass, [_UNIT_1])
        coordinator.data = _fresh(RoomState(operation_mode="manual", target_level=60))
        await coordinator.async_set_direction_level("unit_1", DIRECTION_SUPPLY, 70)
        client.next_read_state = RoomState(
            operation_mode="unbalanced", target_level=70, extract_target_level=60
        )

        await coordinator.async_refresh()

        assert coordinator.last_update_success, coordinator.last_exception
        assert (
            coordinator._writes.by_room["unit_1"]["airflow_levels"].status
            == "confirmed"
        )
        assert coordinator.level_source("unit_1") == "target"

    def test_measured_airflow_does_not_confirm_a_level_write(
        self, hass: HomeAssistant,
    ) -> None:
        """41020/41021 lag behind the target, so only target registers count."""
        coordinator, _ = _build_coordinator(hass, [_UNIT_1])
        coordinator._writes.by_room["unit_1"] = {
            "airflow_levels": WriteConfirmation(
                expected_value=(70, 60),
                started_at=dt_util.utcnow() - timedelta(seconds=1),
            )
        }
        state = _fresh(
            RoomState(operation_mode="manual", supply_air_flow=70, extract_air_flow=60)
        )

        coordinator._confirm_pending_writes(state)

        assert (
            coordinator._writes.by_room["unit_1"]["airflow_levels"].status
            == "pending"
        )

    def test_derived_target_does_not_confirm_a_level_write(
        self, hass: HomeAssistant,
    ) -> None:
        coordinator, _ = _build_coordinator(hass, [_UNIT_1])
        coordinator._writes.by_room["unit_1"] = {
            "airflow_levels": WriteConfirmation(
                expected_value=(30, 30),
                started_at=dt_util.utcnow() - timedelta(seconds=1),
            )
        }
        state = _fresh(
            RoomState(
                operation_mode="manual",
                target_level=30,
                supply_air_flow=30,
                extract_air_flow=30,
            )
        )

        coordinator._confirm_pending_writes(state)

        assert coordinator._writes.by_room["unit_1"]["airflow_levels"].status == "pending"

    def test_balanced_target_readback_confirms_a_level_write(
        self, hass: HomeAssistant,
    ) -> None:
        coordinator, _ = _build_coordinator(hass, [_UNIT_1])
        coordinator._writes.by_room["unit_1"] = {
            "airflow_levels": WriteConfirmation(
                expected_value=(30, 30),
                started_at=dt_util.utcnow() - timedelta(seconds=1),
            )
        }
        state = _fresh(
            RoomState(operation_mode="manual", target_level=30, balanced_target_readback=30)
        )

        coordinator._confirm_pending_writes(state)

        assert coordinator._writes.by_room["unit_1"]["airflow_levels"].status == "confirmed"

    def test_level_write_hidden_by_intensive_is_not_a_mismatch(
        self, hass: HomeAssistant,
    ) -> None:
        coordinator, _ = _build_coordinator(hass, [_UNIT_1])
        coordinator._writes.by_room["unit_1"] = {
            "airflow_levels": WriteConfirmation(
                expected_value=(50, 50),
                started_at=dt_util.utcnow() - timedelta(seconds=1),
            )
        }
        state = _fresh(
            RoomState(
                operation_mode="manual",
                target_level=30,
                balanced_target_readback=30,
                intensive_active=True,
            )
        )

        coordinator._confirm_pending_writes(state)

        assert coordinator._writes.by_room["unit_1"]["airflow_levels"].status == "unverifiable"
        assert not coordinator._writes.unhealthy("unit_1")

    def test_preset_write_still_mismatches_while_intensive_is_reported(
        self, hass: HomeAssistant,
    ) -> None:
        """A quick-mode write clears intensive, so a remaining override is a real mismatch."""
        coordinator, _ = _build_coordinator(hass, [_UNIT_1])
        coordinator._writes.by_room["unit_1"] = {
            "preset_mode": WriteConfirmation(
                expected_value="low",
                started_at=dt_util.utcnow() - timedelta(seconds=1),
            )
        }
        state = _fresh(RoomState(preset_mode="medium", intensive_active=True))

        coordinator._confirm_pending_writes(state)

        assert coordinator._writes.by_room["unit_1"]["preset_mode"].status == "mismatch"

    def test_old_write_failures_stop_flagging_data_health(
        self, hass: HomeAssistant,
    ) -> None:
        coordinator, _ = _build_coordinator(hass, [_UNIT_1])
        coordinator.data = {
            "unit_1": _with_fresh_read_groups(RoomState(target_level=40), "flow")
        }
        failed = WriteConfirmation(
            expected_value=(40, 40),
            started_at=dt_util.utcnow(),
            status="failed",
        )
        coordinator._writes.by_room["unit_1"] = {"airflow_levels": failed}
        assert coordinator.data_health_stale("unit_1") is True

        coordinator._writes.by_room["unit_1"]["airflow_levels"] = replace(
            failed,
            started_at=dt_util.utcnow()
            - timedelta(seconds=WRITE_HEALTH_RETENTION_SECONDS + 1),
        )

        assert coordinator.data_health_stale("unit_1") is False
        writes = coordinator.data_health_attributes("unit_1")["writes"]
        assert writes["airflow_levels"]["status"] == "failed"

    def test_stale_read_group_flags_data_health(
        self, hass: HomeAssistant,
    ) -> None:
        coordinator, _ = _build_coordinator(hass, [_UNIT_1])
        now = dt_util.utcnow()
        state = RoomState(
            group_read_health=(
                (
                    "flow",
                    ReadHealth(
                        last_attempt=now - timedelta(seconds=31),
                        last_successful_read=now - timedelta(seconds=31),
                    ),
                ),
                ("status", ReadHealth(last_attempt=now, last_successful_read=now)),
            ),
        )
        coordinator.data = {"unit_1": state}

        assert coordinator.read_group_stale("unit_1", "flow") is True
        assert coordinator.data_health_stale("unit_1") is True

        coordinator.data["unit_1"] = state.with_read_health(
            "flow", ReadHealth(last_attempt=now, last_successful_read=now)
        )
        assert coordinator.data_health_stale("unit_1") is False


class TestSilentUnitScheduling:
    def test_silent_units_are_polled_less_often(self, hass: HomeAssistant) -> None:
        coordinator, client = _build_coordinator(hass, [_UNIT_1])
        flow_job = next(job for job in coordinator._jobs if job.key == "flow")
        hours_job = next(job for job in coordinator._jobs if job.key == "hours")
        assert coordinator._job_interval(flow_job) == flow_job.interval_seconds

        client.silent_seconds_by_slave[_UNIT_1.slave] = ROOM_SILENT_AFTER_SECONDS + 1

        assert coordinator._job_interval(flow_job) == SILENT_ROOM_POLL_SECONDS
        assert coordinator._job_interval(hours_job) == hours_job.interval_seconds

    def test_units_that_never_answered_count_as_silent(
        self, hass: HomeAssistant,
    ) -> None:
        coordinator, _ = _build_coordinator(hass, [_UNIT_1])
        assert not coordinator._room_silent("unit_1")

        coordinator._started_at -= ROOM_SILENT_AFTER_SECONDS + 1

        assert coordinator._room_silent("unit_1")


# ---------------------------------------------------------------------------
#  Optimistic preset overlay
# ---------------------------------------------------------------------------


class TestOptimisticPresetOverlay:
    def test_missing_readback_is_not_reported_as_inactive(
        self, hass: HomeAssistant,
    ) -> None:
        coordinator, _ = _build_coordinator(hass, [_UNIT_1])
        coordinator.data = {"unit_1": RoomState(error_status=False)}

        assert coordinator.optimistic_preset_mode("unit_1") is None

    def test_overlay_is_returned_until_the_gateway_confirms(
        self, hass: HomeAssistant,
    ) -> None:
        coordinator, _ = _build_coordinator(hass, [_UNIT_1])
        coordinator.data = {"unit_1": RoomState(preset_mode="low")}

        coordinator._optimistic_presets.set("unit_1", "high")
        assert coordinator.optimistic_preset_mode("unit_1") == "high"

        coordinator.data = _fresh(RoomState(preset_mode="high"))
        assert coordinator.optimistic_preset_mode("unit_1") is None

    def test_stale_matching_readback_does_not_clear_the_pending_selection(
        self, hass: HomeAssistant,
    ) -> None:
        coordinator, _ = _build_coordinator(hass, [_UNIT_1])
        state = RoomState(preset_mode="high")
        coordinator.data = {"unit_1": state}
        coordinator._optimistic_presets.set("unit_1", "high")

        coordinator._confirm_pending_writes(coordinator.safe_data)

        assert coordinator.optimistic_preset_mode("unit_1") == "high"
        assert "unit_1" in coordinator._optimistic_presets._pending

    def test_overlay_expires(self, hass: HomeAssistant) -> None:
        coordinator, _ = _build_coordinator(hass, [_UNIT_1])
        coordinator.data = {"unit_1": RoomState(preset_mode="low")}
        coordinator._optimistic_presets._pending["unit_1"] = ("high", 0.0)

        assert coordinator.optimistic_preset_mode("unit_1") is None

    async def test_failed_preset_write_drops_the_overlay(
        self, hass: HomeAssistant,
    ) -> None:
        coordinator, client = _build_coordinator(hass, [_UNIT_1])
        coordinator.data = {"unit_1": RoomState(preset_mode="low")}
        client.write_preset_mode = _failing()

        with pytest.raises(HomeAssistantError):
            await coordinator.async_set_preset_mode("unit_1", "high")

        assert coordinator.optimistic_preset_mode("unit_1") is None

    async def test_successful_preset_write_shows_the_selection_until_confirmed(
        self, hass: HomeAssistant,
    ) -> None:
        coordinator, client = _build_coordinator(hass, [_UNIT_1])
        coordinator.data = {"unit_1": RoomState(preset_mode="low")}
        client.next_read_state = RoomState(preset_mode="low")

        await coordinator.async_set_preset_mode("unit_1", "high")

        assert coordinator.optimistic_preset_mode("unit_1") == "high"

    async def test_clear_pending_preset_still_writes_manual_mode(
        self, hass: HomeAssistant,
    ) -> None:
        coordinator, client = _build_coordinator(hass, [_UNIT_1])
        coordinator.data = {
            "unit_1": RoomState(operation_mode="manual", preset_mode=None, target_level=30)
        }
        coordinator._optimistic_presets.set("unit_1", "low")

        await coordinator.async_clear_preset_mode("unit_1")

        assert client.write_operating_mode_calls == [("unit_1", "manual")]


class TestOptimisticIntensiveOverlay:
    def test_overlay_is_returned_until_the_gateway_confirms(
        self, hass: HomeAssistant,
    ) -> None:
        coordinator, _ = _build_coordinator(hass, [_UNIT_1])
        coordinator.data = {"unit_1": RoomState(intensive_active=False)}

        coordinator._optimistic_intensive.set("unit_1", True)
        assert coordinator.optimistic_intensive("unit_1") is True

        coordinator.data = {
            "unit_1": _with_fresh_read_groups(RoomState(intensive_active=True), "intensive")
        }
        assert coordinator.optimistic_intensive("unit_1") is None

    @pytest.mark.parametrize("pending", [True, False])
    def test_stale_matching_readback_does_not_clear_the_pending_override(
        self, hass: HomeAssistant, pending: bool,
    ) -> None:
        coordinator, _ = _build_coordinator(hass, [_UNIT_1])
        coordinator.data = {"unit_1": RoomState(intensive_active=pending)}
        coordinator._optimistic_intensive.set("unit_1", pending)

        coordinator._confirm_pending_writes(coordinator.safe_data)

        assert coordinator.optimistic_intensive("unit_1") is pending
        assert "unit_1" in coordinator._optimistic_intensive._pending

    def test_overlay_expires(self, hass: HomeAssistant) -> None:
        coordinator, _ = _build_coordinator(hass, [_UNIT_1])
        coordinator.data = {"unit_1": RoomState(intensive_active=False)}
        coordinator._optimistic_intensive._pending["unit_1"] = (True, 0.0)

        assert coordinator.optimistic_intensive("unit_1") is None

    async def test_failed_write_drops_the_overlay(
        self, hass: HomeAssistant,
    ) -> None:
        coordinator, client = _build_coordinator(hass, [_UNIT_1])
        coordinator.data = {"unit_1": RoomState(intensive_active=False)}
        client.write_preset_mode = _failing()

        with pytest.raises(HomeAssistantError):
            await coordinator.async_activate_intensive("unit_1")

        assert coordinator.optimistic_intensive("unit_1") is None

    async def test_successful_write_shows_the_switch_state_until_confirmed(
        self, hass: HomeAssistant,
    ) -> None:
        coordinator, client = _build_coordinator(hass, [_UNIT_1])
        coordinator.data = {"unit_1": RoomState(intensive_active=False)}
        client.next_read_state = RoomState(intensive_active=False)

        await coordinator.async_activate_intensive("unit_1")

        assert coordinator.optimistic_intensive("unit_1") is True


# ---------------------------------------------------------------------------
#  Config-flow helpers
# ---------------------------------------------------------------------------


class TestConfigFlowHelpers:
    def test_detected_profile_defaults_map_capabilities_to_ii_profiles(self) -> None:
        assert _detected_profile_default(2, {2: "plain"}) == "ii_plain"
        assert _detected_profile_default(2, {2: "f"}) == "ii_f"
        assert _detected_profile_default(2, {2: "fc"}) == "ii_fc"
        assert _detected_profile_default(2, {2: "fc_voc"}) == "ii_fc_voc"
        assert _detected_profile_default(2, {}) == "ii_plain"

    def test_profile_field_keys_are_stable_per_modbus_address(self) -> None:
        assert _profile_field_key(2) == "slave_2"
        assert _profile_field_key(16) == "slave_16"

    def test_unit_details_lists_preview_and_device_name(self) -> None:
        assert _unit_details([2], {}) == "- **2**"
        assert (
            _unit_details([2], {2: "ID 116852 | basic"})
            == "- **2**: Hardware ID 116852 | basic"
        )
        assert (
            _unit_details([2], {2: "ID 116852 | basic"}, {2: "Wohnzimmer"})
            == "- **2**: Wohnzimmer, Hardware ID 116852 | basic"
        )

    def test_build_rooms_from_profiles_preserves_existing_metadata(self) -> None:
        selected_profiles = {"slave_2": "ii_plain"}
        rooms = _build_rooms_from_profiles(
            [2],
            selected_profiles,
            previews_by_slave={2: "ID 116852 | basic"},
            existing_rooms_by_slave={
                2: {
                    "key": "bathroom",
                    "name": "Bathroom",
                    "supported_entity_keys": ["level"],
                }
            },
        )

        assert rooms == [
            {
                "key": "bathroom",
                "name": "Bathroom",
                "slave": 2,
                "profile": "ii_plain",
                "preview": "ID 116852 | basic",
            }
        ]

    def test_build_rooms_from_profiles_uses_stable_field_keys(self) -> None:
        selected_profiles = {"slave_2": "ii_plain"}
        rooms = _build_rooms_from_profiles(
            [2],
            selected_profiles,
            previews_by_slave={2: "ID 116852 | basic"},
        )

        assert rooms[0]["profile"] == "ii_plain"
        assert rooms[0]["name"] == "Unit 1"

    def test_default_room_name(self) -> None:
        assert _default_room_name(set()) == "Unit 1"
        assert _default_room_name({"Unit 1", "Unit 3"}) == "Unit 2"

    def test_rescan_does_not_reuse_the_name_of_a_kept_unit(self) -> None:
        rooms = _build_rooms_from_profiles(
            [2, 3, 4],
            {"slave_2": "ii_plain", "slave_3": "ii_plain", "slave_4": "ii_plain"},
            existing_rooms_by_slave={
                3: {"key": "slave_3", "name": "Unit 1"},
                4: {"key": "slave_4", "name": "Unit 2"},
            },
        )

        assert [room["name"] for room in rooms] == ["Unit 3", "Unit 1", "Unit 2"]

    def test_build_rooms_from_profiles_generates_unique_keys_for_new_rooms(self) -> None:
        selected_profiles = {
            "slave_2": "ii_plain",
            "slave_3": "ii_plain",
            "slave_4": "ii_plain",
        }
        rooms = _build_rooms_from_profiles(
            [2, 3, 4],
            selected_profiles,
            existing_rooms_by_slave={
                2: {"key": "unit_1", "name": "Unit 1"},
                4: {"key": "unit_2", "name": "Unit 2"},
            },
        )

        assert [room["key"] for room in rooms] == ["unit_1", "slave_3", "unit_2"]

    def test_build_rooms_from_profiles_suffixes_a_taken_key(self) -> None:
        rooms = _build_rooms_from_profiles(
            [2, 3],
            {"slave_2": "ii_plain", "slave_3": "ii_plain"},
            existing_rooms_by_slave={2: {"key": "slave_3", "name": "Bathroom"}},
        )

        assert [room["key"] for room in rooms] == ["slave_3", "slave_3_2"]

    async def test_failed_probe_leaves_the_unit_plain_without_preview(self) -> None:
        async def _probe(slave: int) -> tuple[str, str | None]:
            if slave == 3:
                raise MeltemModbusError("no answer")
            return "f", f"ID {slave}"

        previews, profiles = await _async_probe_units([2, 3], _probe)

        assert previews == {2: "ID 2"}
        assert profiles == {2: "f", 3: "plain"}


# ---------------------------------------------------------------------------
#  Coordinator
# ---------------------------------------------------------------------------


class TestCoordinator:
    async def test_async_discover_gateway_units_uses_client(
        self, hass: HomeAssistant,
    ) -> None:
        coordinator, client = _build_coordinator(hass, [_UNIT_1])

        discovered = await coordinator.async_discover_gateway_units()

        assert discovered == [2, 3, 4]
        assert client.discover_calls == [(2, 16)]

    async def test_async_probe_slave_details_uses_client(
        self, hass: HomeAssistant,
    ) -> None:
        coordinator, client = _build_coordinator(hass, [_UNIT_1])

        details = await coordinator.async_probe_slave_details(4)

        assert details == ("plain", "ID 4")
        assert client.probe_calls == [4]

    async def test_async_set_level_writes_without_forced_refresh(
        self, hass: HomeAssistant,
    ) -> None:
        coordinator, client = _build_coordinator(hass, [_UNIT_1])
        coordinator.data = {"unit_1": RoomState(target_level=30)}

        await coordinator.async_set_level("unit_1", 55)

        assert client.write_level_calls == [("unit_1", 55)]
        assert coordinator.data["unit_1"].target_level == 30
        assert client.read_calls == []

    async def test_async_set_unbalanced_levels_writes_without_forced_refresh(
        self, hass: HomeAssistant,
    ) -> None:
        coordinator, client = _build_coordinator(hass, [_UNIT_1])
        coordinator.data = {"unit_1": RoomState(target_level=30, extract_target_level=35)}

        await coordinator.async_set_unbalanced_levels("unit_1", 40, 35)

        assert client.write_unbalanced_calls == [("unit_1", 40, 35)]
        assert client.read_calls == []

    async def test_async_set_preset_mode_writes_only_the_preset_and_reads_it_back_twice(
        self, hass: HomeAssistant,
    ) -> None:
        coordinator, client = _build_coordinator(hass, [_UNIT_1])
        coordinator.data = {"unit_1": RoomState(target_level=50, extract_target_level=70)}

        await coordinator.async_set_preset_mode("unit_1", "medium")

        assert client.write_preset_mode_calls == [("unit_1", "medium")]
        assert client.write_level_calls == client.write_unbalanced_calls == []
        assert len(client.read_calls) == 2

    async def test_async_activate_intensive_writes_intensive_preset(
        self, hass: HomeAssistant,
    ) -> None:
        coordinator, client = _build_coordinator(hass, [_UNIT_1])
        coordinator.data = {"unit_1": RoomState(target_level=30)}

        await coordinator.async_activate_intensive("unit_1")

        assert client.write_preset_mode_calls == [("unit_1", "intensive")]

    async def test_mode_write_during_intensive_is_unverifiable_not_a_problem(
        self, hass: HomeAssistant,
    ) -> None:
        """The intensive status hides the base mode the write changed (HW-1)."""
        coordinator, client = _build_coordinator(hass, [_UNIT_F])
        coordinator.data = {
            "unit_1": RoomState(operation_mode="manual", target_level=30, intensive_active=True)
        }
        client.next_read_state = RoomState(
            operation_mode="manual", target_level=30, intensive_active=True
        )

        await coordinator.async_set_operation_mode("unit_1", "humidity_control")

        confirmation = coordinator._writes.by_room["unit_1"]["operation_mode"]
        assert confirmation.status == "unverifiable"
        assert coordinator.data_health_stale("unit_1") is False

    async def test_intensive_write_is_confirmed_when_the_unit_reports_it(
        self, hass: HomeAssistant,
    ) -> None:
        coordinator, client = _build_coordinator(hass, [_UNIT_1])
        coordinator.data = {"unit_1": RoomState(target_level=30)}
        client.next_read_state = RoomState(target_level=30, intensive_active=True)

        await coordinator.async_activate_intensive("unit_1")

        confirmation = coordinator._writes.by_room["unit_1"]["intensive"]
        assert confirmation.status == "confirmed"

    async def test_async_deactivate_intensive_clears_only_the_override(
        self, hass: HomeAssistant,
    ) -> None:
        coordinator, client = _build_coordinator(hass, [_UNIT_1])
        coordinator.data = {"unit_1": RoomState(target_level=30, intensive_active=True)}
        client.next_read_state = RoomState(target_level=30, intensive_active=False)

        await coordinator.async_deactivate_intensive("unit_1")

        assert client.clear_intensive_calls == ["unit_1"]
        assert client.write_preset_mode_calls == []
        assert coordinator.safe_data["unit_1"].intensive_active is False
        confirmation = coordinator._writes.by_room["unit_1"]["intensive"]
        assert confirmation.status == "confirmed"

    async def test_control_setting_keeps_confirmed_value_until_readback(
        self, hass: HomeAssistant,
    ) -> None:
        coordinator, client = _build_coordinator(hass, [_UNIT_F])
        coordinator.data = {"unit_1": RoomState(humidity_starting_point=50)}
        observed_during_settle: list[int | None] = []

        async def _observe_confirmed_state(_seconds: float) -> None:
            observed_during_settle.append(
                coordinator.safe_data["unit_1"].humidity_starting_point
            )

        client.next_read_state = RoomState(humidity_starting_point=70)
        with patch(_SLEEP, side_effect=_observe_confirmed_state):
            await coordinator.async_set_control_setting(
                "unit_1", "humidity_starting_point", 70
            )

        assert observed_during_settle == [50]
        assert coordinator.safe_data["unit_1"].humidity_starting_point == 70
        assert (
            coordinator._writes.by_room["unit_1"][_HUMIDITY_START_WRITE].status
            == "confirmed"
        )
        assert client.write_control_setting_calls == [
            ("unit_1", "humidity_starting_point", 70)
        ]

    def test_write_confirmation_ignores_values_from_before_the_write(
        self, hass: HomeAssistant,
    ) -> None:
        coordinator, _ = _build_coordinator(hass, [_UNIT_F])
        started_at = dt_util.utcnow()
        coordinator._writes.by_room["unit_1"] = {
            _HUMIDITY_START_WRITE: WriteConfirmation(expected_value=70, started_at=started_at)
        }
        state = RoomState(
            humidity_starting_point=70,
            group_read_health=_control_settings_read_at(started_at - timedelta(seconds=1)),
        )

        coordinator._confirm_pending_writes({"unit_1": state})

        confirmation = coordinator._writes.by_room["unit_1"][_HUMIDITY_START_WRITE]
        assert confirmation.status == "pending"
        assert confirmation.actual_value is None

        state = replace(
            state,
            group_read_health=_control_settings_read_at(started_at + timedelta(seconds=1)),
        )
        coordinator._confirm_pending_writes({"unit_1": state})

        assert (
            coordinator._writes.by_room["unit_1"][_HUMIDITY_START_WRITE].status
            == "confirmed"
        )

    def test_confirmed_write_is_not_downgraded_after_value_changes(
        self, hass: HomeAssistant,
    ) -> None:
        coordinator, _ = _build_coordinator(hass, [_UNIT_F])
        started_at = dt_util.utcnow()
        coordinator._writes.by_room["unit_1"] = {
            _HUMIDITY_START_WRITE: WriteConfirmation(
                expected_value=70,
                started_at=started_at,
                status="confirmed",
                actual_value=70,
            )
        }
        state = RoomState(
            humidity_starting_point=60,
            group_read_health=_control_settings_read_at(started_at + timedelta(seconds=5)),
        )

        coordinator._confirm_pending_writes({"unit_1": state})

        confirmation = coordinator._writes.by_room["unit_1"][_HUMIDITY_START_WRITE]
        assert confirmation.status == "confirmed"
        assert confirmation.actual_value == 70

    def test_pending_write_without_readback_turns_unconfirmed(
        self, hass: HomeAssistant,
    ) -> None:
        coordinator, _ = _build_coordinator(hass, [_UNIT_F])
        coordinator.data = {"unit_1": RoomState(humidity_starting_point=50)}
        now = dt_util.utcnow()
        coordinator._writes.by_room["unit_1"] = {
            _HUMIDITY_START_WRITE: WriteConfirmation(expected_value=70, started_at=now)
        }

        assert coordinator.data_health_stale("unit_1") is False

        coordinator._writes.by_room["unit_1"][_HUMIDITY_START_WRITE] = WriteConfirmation(
            expected_value=70,
            started_at=now - timedelta(seconds=WRITE_CONFIRMATION_TIMEOUT_SECONDS + 1),
        )

        writes = coordinator.data_health_attributes("unit_1")["writes"]
        assert writes[_HUMIDITY_START_WRITE]["status"] == "unconfirmed"
        assert coordinator.data_health_stale("unit_1") is True

    async def test_control_setting_keeps_confirmed_value_when_refresh_fails(
        self, hass: HomeAssistant,
    ) -> None:
        coordinator, client = _build_coordinator(hass, [_UNIT_F])
        coordinator.data = {"unit_1": RoomState(humidity_starting_point=50)}
        client.read_room_state = _failing("confirmation failed")  # type: ignore[method-assign]

        await coordinator.async_set_control_setting("unit_1", "humidity_starting_point", 70)

        assert coordinator.safe_data["unit_1"].humidity_starting_point == 50
        assert (
            coordinator._writes.by_room["unit_1"][_HUMIDITY_START_WRITE].status
            == "unconfirmed"
        )

    async def test_control_setting_publishes_the_actual_written_step(
        self, hass: HomeAssistant,
    ) -> None:
        coordinator, client = _build_coordinator(hass, [_UNIT_F])
        coordinator.data = {"unit_1": RoomState(humidity_min_level=10)}
        observed_during_settle: list[int | None] = []

        async def _observe(_seconds: float) -> None:
            observed_during_settle.append(
                coordinator.safe_data["unit_1"].humidity_min_level
            )

        # The client rounds 15 onto the 10-step grid.
        client.write_control_setting = AsyncMock(return_value=20)  # type: ignore[method-assign]
        client.next_read_state = RoomState(humidity_min_level=20)
        with patch(_SLEEP, side_effect=_observe):
            await coordinator.async_set_control_setting("unit_1", "humidity_min_level", 15)

        assert observed_during_settle == [10]
        assert coordinator.safe_data["unit_1"].humidity_min_level == 20

    @pytest.mark.parametrize(
        ("state", "setting_key", "value"),
        [
            (RoomState(humidity_max_level=50), "humidity_min_level", 60),
            (RoomState(humidity_min_level=50), "humidity_max_level", 40),
        ],
        ids=("minimum-above-maximum", "maximum-below-minimum"),
    )
    async def test_an_inverted_level_range_is_refused(
        self, hass: HomeAssistant, state: RoomState, setting_key: str, value: int
    ) -> None:
        coordinator, client = _build_coordinator(hass, [_UNIT_F])
        coordinator.data = {"unit_1": state}

        with pytest.raises(HomeAssistantError) as err:
            await coordinator.async_set_control_setting("unit_1", setting_key, value)

        assert err.value.translation_key == "control_level_range"
        assert client.write_control_setting_calls == []

    async def test_levels_are_compared_after_rounding_to_the_register_step(
        self, hass: HomeAssistant,
    ) -> None:
        coordinator, client = _build_coordinator(hass, [_UNIT_F])
        coordinator.data = {"unit_1": RoomState(humidity_max_level=50)}

        await coordinator.async_set_control_setting("unit_1", "humidity_min_level", 54)

        assert client.write_control_setting_calls == [("unit_1", "humidity_min_level", 54)]

    @pytest.mark.parametrize("family", ["humidity", "co2"])
    @pytest.mark.parametrize("minimum_first", [False, True], ids=("maximum-first", "minimum-first"))
    async def test_concurrent_setting_writes_cannot_invert_the_range(
        self, hass: HomeAssistant, family: str, minimum_first: bool,
    ) -> None:
        coordinator, client = _build_coordinator(hass, [_UNIT_F])
        min_key, max_key = f"{family}_min_level", f"{family}_max_level"
        client.next_read_state = RoomState(**{min_key: 10, max_key: 100})
        coordinator.data = {"unit_1": client.next_read_state}

        async def _yielding_write(room: RoomConfig, setting_key: str, value: int) -> int:
            await asyncio.sleep(0)
            client.write_control_setting_calls.append((room.key, setting_key, value))
            client.next_read_state = replace(client.next_read_state, **{setting_key: value})
            return value

        client.write_control_setting = _yielding_write
        writes = [(min_key, 80), (max_key, 20)]
        if not minimum_first:
            writes.reverse()
        results = await asyncio.gather(
            *(coordinator.async_set_control_setting("unit_1", key, value) for key, value in writes),
            return_exceptions=True,
        )

        assert results[0] is None
        assert isinstance(results[1], HomeAssistantError)
        assert results[1].translation_key == "control_level_range"
        assert client.write_control_setting_calls == [("unit_1", *writes[0])]
        state = coordinator.safe_data["unit_1"]
        assert getattr(state, min_key) <= getattr(state, max_key)

    @pytest.mark.parametrize("family", ["humidity", "co2"])
    @pytest.mark.parametrize("minimum_first", [False, True], ids=("maximum-first", "minimum-first"))
    async def test_unconfirmed_setting_is_used_when_the_readback_fails(
        self, hass: HomeAssistant, family: str, minimum_first: bool,
    ) -> None:
        coordinator, client = _build_coordinator(hass, [_UNIT_F])
        min_key, max_key = f"{family}_min_level", f"{family}_max_level"
        coordinator.data = {"unit_1": RoomState(**{min_key: 10, max_key: 100})}
        client.read_room_state = _failing("readback failed")
        writes = [(min_key, 80), (max_key, 20)]
        if not minimum_first:
            writes.reverse()

        await coordinator.async_set_control_setting("unit_1", *writes[0])
        with pytest.raises(HomeAssistantError) as err:
            await coordinator.async_set_control_setting("unit_1", *writes[1])

        assert err.value.translation_key == "control_level_range"
        assert client.write_control_setting_calls == [("unit_1", *writes[0])]

    async def test_mismatched_setting_readback_is_used_instead_of_the_requested_value(
        self, hass: HomeAssistant,
    ) -> None:
        coordinator, client = _build_coordinator(hass, [_UNIT_F])
        coordinator.data = {"unit_1": RoomState(humidity_min_level=10, humidity_max_level=100)}
        client.next_read_state = RoomState(humidity_min_level=30, humidity_max_level=100)

        await coordinator.async_set_control_setting("unit_1", "humidity_min_level", 80)
        client.next_read_state = RoomState(humidity_min_level=30, humidity_max_level=40)
        await coordinator.async_set_control_setting("unit_1", "humidity_max_level", 40)

        assert client.write_control_setting_calls == [
            ("unit_1", "humidity_min_level", 80),
            ("unit_1", "humidity_max_level", 40),
        ]

    def test_build_jobs_only_includes_relevant_groups(
        self, hass: HomeAssistant,
    ) -> None:
        coordinator, _ = _build_coordinator(
            hass,
            [
                RoomConfig(
                    key="temps_only",
                    name="Temps Only",
                    profile="ii_plain",
                    slave=2,
                    supported_entity_keys=frozenset({"supply_air_temperature"}),
                ),
                RoomConfig(
                    key="flow_only",
                    name="Flow Only",
                    profile="ii_plain",
                    slave=3,
                    supported_entity_keys=frozenset(
                        {"extract_air_flow", "supply_air_flow"}
                    ),
                ),
            ],
        )

        jobs = {(job.room_key, job.key) for job in coordinator._jobs}

        assert ("temps_only", "temperature") in jobs
        assert ("temps_only", "flow") not in jobs
        assert ("flow_only", "flow") in jobs
        assert ("flow_only", "hours") not in jobs

    def test_select_due_job_returns_earliest_due(
        self, hass: HomeAssistant,
    ) -> None:
        coordinator, _ = _build_coordinator(hass, [_UNIT_1])
        coordinator._jobs = [
            PollJob("status", "unit_1", RefreshPlan.only(refresh_status=True), 60, 15.0),
            PollJob("flow", "unit_1", RefreshPlan.only(refresh_airflow=True), 10, 5.0),
        ]

        selected = select_due_job(coordinator._jobs, 10.0)

        assert selected is not None
        assert selected.key == "flow"

    async def test_read_one_job_keeps_previous_state_on_failure(
        self, hass: HomeAssistant,
    ) -> None:
        coordinator, _ = _build_coordinator(
            hass,
            [RoomConfig(key="broken", name="Broken", profile="ii_plain", slave=2)],
        )
        previous = {"broken": RoomState(target_level=30)}
        job = PollJob("flow", "broken", RefreshPlan.only(refresh_airflow=True), 10, 0.0)

        state_map = await coordinator._read_one_job(previous, job)

        assert state_map["broken"].target_level == 30


# ---------------------------------------------------------------------------
#  Options flow
# ---------------------------------------------------------------------------


class TestOptionsFlow:
    @staticmethod
    def _build_flow(hass: HomeAssistant) -> MeltemVentilationOptionsFlow:
        config_entry = MockConfigEntry(
            data={
                CONF_PORT: "/dev/serial/by-id/test",
                CONF_MAX_REQUESTS_PER_SECOND: 2.0,
                CONF_ROOMS: [
                    {
                        "key": "unit_1",
                        "name": "Unit 1",
                        "slave": 2,
                        "profile": "ii_plain",
                        "preview": "ID 116852 | basic",
                        "supported_entity_keys": ["level"],
                    }
                ],
            },
            options={},
            entry_id="entry-1",
            domain=DOMAIN,
            title="Meltem",
            version=1,
            source="user",
        )
        config_entry.add_to_hass(hass)
        config_entry.runtime_data = types.SimpleNamespace(
            coordinator=MeltemDataUpdateCoordinator(
                hass,
                config_entry=config_entry,
                client=_FakeClient(),
                rooms=[_UNIT_1],
                max_requests_per_second=2.0,
            )
        )
        flow = MeltemVentilationOptionsFlow()
        flow.hass = hass
        # OptionsFlow.config_entry resolves through handler + hass.
        flow.handler = config_entry.entry_id
        return flow

    async def test_options_init_shows_the_menu(self, hass: HomeAssistant) -> None:
        result = await self._build_flow(hass).async_step_init(None)

        assert result["type"] == "menu"
        assert result["step_id"] == "init"
        assert set(result["menu_options"]) == {
            "edit_request_rate",
            "edit_profiles",
            "rescan_units",
        }

    async def test_options_rescan_keeps_known_profiles_and_suggests_detected_ones(
        self, hass: HomeAssistant,
    ) -> None:
        flow = self._build_flow(hass)
        flow._coordinator.client.probe_slave_details = AsyncMock(  # type: ignore[union-attr]
            side_effect=lambda slave: ("f", f"ID {slave}")
        )

        result = await flow.async_step_rescan_units({})

        assert result["type"] == "form"
        assert result["step_id"] == "profiles"
        assert result["description_placeholders"]["device_count"] == "3"
        defaults = {key.schema: key.default() for key in result["data_schema"].schema}
        assert defaults == {"slave_2": "ii_plain", "slave_3": "ii_f", "slave_4": "ii_f"}
        assert flow._preview_by_slave == {2: "ID 2", 3: "ID 3", 4: "ID 4"}

    @pytest.mark.parametrize(
        ("discover", "error"),
        [
            pytest.param(AsyncMock(return_value=[]), "no_devices_found", id="no-units"),
            pytest.param(_failing(), "cannot_connect", id="gateway-error"),
        ],
    )
    async def test_options_rescan_reports_why_no_units_were_found(
        self, hass: HomeAssistant, discover: AsyncMock, error: str,
    ) -> None:
        flow = self._build_flow(hass)
        flow._coordinator.client.discover_gateway_units = discover  # type: ignore[union-attr]

        result = await flow.async_step_rescan_units({})

        assert result["step_id"] == "rescan_units"
        assert result["errors"] == {"base": error}
        assert flow._discovered_slaves == []

    async def test_options_profiles_without_a_rescan_return_to_the_menu(
        self, hass: HomeAssistant,
    ) -> None:
        result = await self._build_flow(hass).async_step_profiles(None)

        assert result["type"] == "menu"
        assert result["step_id"] == "init"

    async def test_options_profiles_updates_entry_data_and_reloads(
        self, hass: HomeAssistant,
    ) -> None:
        flow = self._build_flow(hass)
        flow._discovered_slaves = [2]
        flow._preview_by_slave = {2: "ID 116852 | basic"}
        flow._detected_profile_by_slave = {2: "plain"}
        flow.async_create_entry = lambda title="", data=None: {
            "type": "create_entry",
            "title": title,
            "data": data or {},
        }

        with (
            patch.object(hass.config_entries, "async_update_entry") as update_entry,
            patch.object(hass.config_entries, "async_reload", new=AsyncMock()) as reload,
        ):
            result = await flow.async_step_profiles({"slave_2": "ii_f"})

        assert result["type"] == "create_entry"
        room = update_entry.call_args.kwargs["data"][CONF_ROOMS][0]
        assert room["profile"] == "ii_f"
        assert "supported_entity_keys" not in room
        reload.assert_awaited_once_with("entry-1")
