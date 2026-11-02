"""Boundary tests for read-group freshness and availability."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from custom_components.meltem_ventilation import read_health
from custom_components.meltem_ventilation.const import (
    AIRFLOW_STALE_AFTER_SECONDS,
    READ_FAILURE_THRESHOLD,
)
from custom_components.meltem_ventilation.models import (
    ReadHealth,
    ReadJobOutcome,
    RefreshPlan,
    RoomConfig,
    RoomState,
)
from custom_components.meltem_ventilation.polling import READ_GROUP_INTERVAL_SECONDS

_NOW = datetime(2026, 10, 29, 10, 0, tzinfo=UTC)


@pytest.fixture(autouse=True)
def _freeze_now(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(read_health, "dt_util", SimpleNamespace(utcnow=lambda: _NOW))


def test_airflow_freshness_limits() -> None:
    assert AIRFLOW_STALE_AFTER_SECONDS == 30
    assert READ_FAILURE_THRESHOLD == 3


@pytest.mark.parametrize(
    ("group", "stale_after"),
    [
        ("flow", AIRFLOW_STALE_AFTER_SECONDS),
        ("status", READ_GROUP_INTERVAL_SECONDS["status"] * 3),
    ],
)
@pytest.mark.parametrize(
    ("offset_us", "stale"), [(-1, False), (0, False), (1, True)], ids=("before", "at", "after")
)
def test_freshness_changes_only_after_the_time_limit(
    group: str, stale_after: int, offset_us: int, stale: bool
) -> None:
    read_at = _NOW - timedelta(seconds=stale_after, microseconds=offset_us)
    state = RoomState().with_read_health(
        group, ReadHealth(last_attempt=read_at, last_successful_read=read_at)
    )

    assert read_health.group_fresh(state, group) is not stale
    assert read_health.group_available(state, group) is not stale
    assert read_health.group_stale(state, group) is stale


@pytest.mark.parametrize(
    ("wall_clock_jump", "monotonic_age", "stale"),
    [
        (timedelta(days=2), 0.0, False),
        (-timedelta(days=2), READ_GROUP_INTERVAL_SECONDS["hours"] * 3 + 1, True),
    ],
    ids=("wall-clock-forward", "wall-clock-back"),
)
def test_monotonic_age_decides_across_wall_clock_jumps(
    monkeypatch: pytest.MonkeyPatch,
    wall_clock_jump: timedelta,
    monotonic_age: float,
    stale: bool,
) -> None:
    """A clock jump, such as the first NTP sync on a host without RTC, does not age values."""

    monkeypatch.setattr(read_health, "time", SimpleNamespace(monotonic=lambda: 1000.0))
    read_at = _NOW - wall_clock_jump
    state = RoomState().with_read_health(
        "hours",
        ReadHealth(
            last_attempt=read_at,
            last_successful_read=read_at,
            last_success_monotonic=1000.0 - monotonic_age,
        ),
    )

    assert read_health.group_fresh(state, "hours") is not stale
    assert read_health.group_available(state, "hours") is not stale
    assert read_health.group_stale(state, "hours") is stale


def test_a_failed_read_keeps_the_monotonic_stamp_of_the_last_success() -> None:
    health = ReadHealth(
        last_attempt=_NOW, last_successful_read=_NOW, last_success_monotonic=12.5
    ).failed(_NOW, "timeout")

    assert health.last_success_monotonic == 12.5


def test_a_group_without_any_successful_read_is_too_old() -> None:
    assert read_health.read_too_old("flow", ReadHealth())


@pytest.mark.parametrize("group", ["flow", "status"])
@pytest.mark.parametrize("failures", [0, 1, READ_FAILURE_THRESHOLD - 1, READ_FAILURE_THRESHOLD])
def test_failure_threshold_distinguishes_fresh_from_available(group: str, failures: int) -> None:
    read_at = _NOW - timedelta(seconds=1)
    state = RoomState().with_read_health(
        group,
        ReadHealth(
            last_attempt=_NOW if failures else read_at,
            last_successful_read=read_at,
            consecutive_failures=failures,
            last_error="timeout" if failures else None,
        ),
    )

    assert read_health.group_fresh(state, group) is (failures == 0)
    assert read_health.group_available(state, group) is (failures < READ_FAILURE_THRESHOLD)
    assert read_health.group_stale(state, group) is (failures >= READ_FAILURE_THRESHOLD)


@pytest.mark.parametrize(
    ("failures", "stale"),
    [(1, None), (READ_FAILURE_THRESHOLD - 1, None), (READ_FAILURE_THRESHOLD, True)],
)
def test_never_successful_reads_have_unknown_freshness_until_failure_threshold(
    failures: int, stale: bool | None
) -> None:
    state = RoomState().with_read_health(
        "flow",
        ReadHealth(last_attempt=_NOW, consecutive_failures=failures, last_error="timeout"),
    )

    assert not read_health.group_fresh(state, "flow")
    assert not read_health.group_available(state, "flow")
    assert read_health.group_stale(state, "flow") is stale


def _outcome(
    *, ok: tuple[str, ...] = (), failed: dict[str, str] | None = None, answered: bool = True
) -> ReadJobOutcome:
    """Describe one job: groups the unit answered, and groups that failed."""

    errors = failed or {}
    recorded = frozenset(ok) | frozenset(errors)
    return ReadJobOutcome(
        attempted_groups=recorded,
        recorded_groups=recorded,
        errors=errors,
        answered=answered,
        unit_success_groups=frozenset(ok),
    )


_ROOM = RoomConfig(key="unit_1", name="Unit 1", profile="ii_plain", slave=2)
_RF_PLAN = RefreshPlan.only(refresh_rf_diagnostics=True)


@pytest.mark.parametrize(
    ("signal_outcome", "expected_link_failure"),
    [("timeout", True), ("refused", False), ("success", None)],
)
def test_job_outcome_only_judges_the_groups_this_job_recorded(
    signal_outcome: str, expected_link_failure: bool | None
) -> None:
    # Only rf_signal was read; the other RF groups were skipped this time.
    outcome = (
        _outcome(ok=("rf_signal",))
        if signal_outcome == "success"
        else _outcome(failed={"rf_signal": signal_outcome}, answered=signal_outcome != "timeout")
    )

    failure = read_health.job_read_failure(_ROOM, outcome, _RF_PLAN)

    assert read_health.job_read_attempted(_ROOM, outcome, _RF_PLAN)
    assert read_health.job_read_unanswered(_ROOM, outcome, _RF_PLAN) is (
        signal_outcome == "timeout"
    )
    assert read_health.job_read_succeeded(_ROOM, outcome, _RF_PLAN) is (signal_outcome == "success")
    if expected_link_failure is None:
        assert failure is None
    else:
        assert failure is not None
        assert str(failure.error) == signal_outcome
        assert failure.link_failure is expected_link_failure


def test_job_with_no_attempt_is_neither_a_failure_nor_a_success() -> None:
    outcome = ReadJobOutcome()

    assert not read_health.job_read_attempted(_ROOM, outcome, _RF_PLAN)
    assert not read_health.job_read_unanswered(_ROOM, outcome, _RF_PLAN)
    assert read_health.job_read_failure(_ROOM, outcome, _RF_PLAN) is None
    assert not read_health.job_read_succeeded(_ROOM, outcome, _RF_PLAN)


@pytest.mark.parametrize("answer", ["success", "refused"])
def test_a_response_prevents_a_mixed_job_from_counting_as_unanswered(answer: str) -> None:
    outcome = (
        _outcome(ok=("rf_last_seen",), failed={"rf_signal": "timeout"})
        if answer == "success"
        else _outcome(failed={"rf_signal": "timeout", "rf_last_seen": "refused"})
    )

    assert read_health.job_read_attempted(_ROOM, outcome, _RF_PLAN)
    assert not read_health.job_read_unanswered(_ROOM, outcome, _RF_PLAN)
    assert read_health.job_read_succeeded(_ROOM, outcome, _RF_PLAN) is (answer == "success")


def test_values_from_the_airflow_group_decide_an_airflow_job() -> None:
    plan = RefreshPlan.only(refresh_airflow=True)
    outcome = replace(
        _outcome(ok=("flow",), failed={"flow_control": "refused"}),
        value_groups=frozenset({"flow"}),
    )

    assert read_health.job_read_failure(_ROOM, outcome, plan) is None
    assert read_health.job_read_received_values(_ROOM, outcome, plan)
    assert read_health.job_read_succeeded(_ROOM, outcome, plan)


@pytest.mark.parametrize(
    "baseline_at",
    [_NOW - timedelta(seconds=5), _NOW + timedelta(hours=1)],
    ids=("steady-clock", "clock-set-back-after-the-write"),
)
def test_a_read_after_the_baseline_counts_whatever_the_wall_clock_did(
    baseline_at: datetime,
) -> None:
    """Reads and writes share the gateway lock, so any later read changes the health.

    When the wall clock is set back after a write, the next read carries an
    earlier timestamp than the write; it must still count as later.
    """
    baseline = ReadHealth(last_attempt=baseline_at, last_successful_read=baseline_at)
    unchanged = RoomState().with_read_health("flow_control", baseline)
    later = RoomState().with_read_health(
        "flow_control", ReadHealth(last_attempt=_NOW, last_successful_read=_NOW)
    )
    failed = RoomState().with_read_health("flow_control", baseline.failed(_NOW, "timeout"))

    assert not read_health.read_since(unchanged, "flow_control", baseline)
    assert not read_health.mode_read_since(unchanged, baseline)
    assert read_health.read_since(later, "flow_control", baseline)
    assert read_health.mode_read_since(later, baseline)
    assert not read_health.read_since(failed, "flow_control", baseline)
    assert not read_health.mode_read_since(failed, baseline)


def test_unsupported_optional_group_does_not_flag_data_health_stale() -> None:
    flow_ok = ReadHealth(last_attempt=_NOW, last_successful_read=_NOW)
    refused_optional = ReadHealth(
        last_attempt=_NOW,
        consecutive_failures=READ_FAILURE_THRESHOLD,
        last_error="illegal address",
        last_error_timed_out=False,
    )
    state = (
        RoomState(supply_air_flow=30, extract_air_flow=30)
        .with_read_health("flow", flow_ok)
        .with_read_health("fan_hours", refused_optional)
        .with_read_health("rf_signal", refused_optional)
    )

    assert read_health.group_stale(state, "fan_hours") is None
    assert read_health.group_stale(state, "rf_signal") is None
    assert read_health.data_health_stale(state, writes_unhealthy=False, has_writes=False) is False

    timed_out_optional = ReadHealth(
        last_attempt=_NOW,
        consecutive_failures=READ_FAILURE_THRESHOLD,
        last_error="timeout",
        last_error_timed_out=True,
    )
    timed_out_state = state.with_read_health("rf_signal", timed_out_optional)
    assert read_health.group_stale(timed_out_state, "rf_signal") is True
    assert (
        read_health.data_health_stale(timed_out_state, writes_unhealthy=False, has_writes=False)
        is True
    )
