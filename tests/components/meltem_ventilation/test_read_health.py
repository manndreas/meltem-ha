"""Boundary tests for read-group freshness and availability."""

from __future__ import annotations

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


@pytest.mark.parametrize(
    ("signal_outcome", "expected_link_failure"),
    [("timeout", True), ("refused", False), ("success", None)],
)
def test_job_outcome_ignores_groups_skipped_during_this_attempt(
    signal_outcome: str, expected_link_failure: bool | None
) -> None:
    room = RoomConfig(key="unit_1", name="Unit 1", profile="ii_plain", slave=2)
    plan = RefreshPlan.only(refresh_rf_diagnostics=True)
    previous = ReadHealth(
        last_attempt=_NOW - timedelta(seconds=1), consecutive_failures=1, last_error="refused"
    )
    if signal_outcome == "success":
        signal = ReadHealth(last_attempt=_NOW, last_successful_read=_NOW)
    else:
        signal = ReadHealth(
            last_attempt=_NOW,
            consecutive_failures=1,
            last_error=signal_outcome,
            last_error_timed_out=signal_outcome == "timeout",
        )
    state = (
        RoomState()
        .with_read_health("rf_fault", previous)
        .with_read_health("value_error", previous)
        .with_read_health("rf_last_seen", previous)
        .with_read_health("rf_signal", signal)
    )

    outcome = read_health.job_read_failure(room, state, plan, _NOW)

    assert read_health.job_read_attempted(room, state, plan, _NOW)
    assert read_health.job_read_unanswered(room, state, plan, _NOW) is (signal_outcome == "timeout")
    assert read_health.job_read_succeeded(room, state, plan, _NOW) is (signal_outcome == "success")
    if expected_link_failure is None:
        assert outcome is None
    else:
        assert outcome is not None
        assert str(outcome.error) == signal_outcome
        assert outcome.link_failure is expected_link_failure


def test_job_with_no_current_attempt_is_neither_a_failure_nor_a_success() -> None:
    room = RoomConfig(key="unit_1", name="Unit 1", profile="ii_plain", slave=2)
    plan = RefreshPlan.only(refresh_rf_diagnostics=True)
    previous = ReadHealth(
        last_attempt=_NOW - timedelta(seconds=1), consecutive_failures=1, last_error="refused"
    )
    state = RoomState().with_read_health("rf_signal", previous)

    assert not read_health.job_read_attempted(room, state, plan, _NOW)
    assert not read_health.job_read_unanswered(room, state, plan, _NOW)
    assert read_health.job_read_failure(room, state, plan, _NOW) is None
    assert not read_health.job_read_succeeded(room, state, plan, _NOW)


@pytest.mark.parametrize("answer", ["success", "refused"])
def test_a_response_prevents_a_mixed_job_from_counting_as_unanswered(answer: str) -> None:
    room = RoomConfig(key="unit_1", name="Unit 1", profile="ii_plain", slave=2)
    plan = RefreshPlan.only(refresh_rf_diagnostics=True)
    timeout = ReadHealth(
        last_attempt=_NOW,
        consecutive_failures=1,
        last_error="timeout",
        last_error_timed_out=True,
    )
    answered = (
        ReadHealth(last_attempt=_NOW, last_successful_read=_NOW)
        if answer == "success"
        else ReadHealth(last_attempt=_NOW, consecutive_failures=1, last_error="refused")
    )
    state = (
        RoomState()
        .with_read_health("rf_signal", timeout)
        .with_read_health("rf_last_seen", answered)
    )

    assert read_health.job_read_attempted(room, state, plan, _NOW)
    assert not read_health.job_read_unanswered(room, state, plan, _NOW)
    assert read_health.job_read_succeeded(room, state, plan, _NOW) is (answer == "success")
