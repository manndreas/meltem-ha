"""Tests for write confirmation outcomes."""

from __future__ import annotations

from dataclasses import replace
from datetime import timedelta

import pytest
from homeassistant.util import dt as dt_util

from custom_components.meltem_ventilation.const import (
    OPERATION_MODE_MANUAL,
    OPERATION_MODE_UNBALANCED,
    WRITE_CONFIRMATION_TIMEOUT_SECONDS,
    WRITE_HEALTH_RETENTION_SECONDS,
)
from custom_components.meltem_ventilation.models import ReadHealth, RoomState, WriteConfirmation
from custom_components.meltem_ventilation.write_confirmation import (
    CONTROL_SETTING_WRITE_PREFIX,
    WriteConfirmations,
    _checked_confirmation,
    _readback_value,
    write_group,
)


def test_write_group_rejects_unknown_write_key() -> None:
    with pytest.raises(ValueError, match="Unknown write confirmation key"):
        write_group("unknown")


def test_set_status_returns_false_for_missing_confirmation() -> None:
    confirmations = WriteConfirmations()

    assert confirmations.set_status("unit_1", "preset_mode", "confirmed") is False


def test_problematic_write_command_is_counted_once_across_status_changes() -> None:
    issues: list[tuple[str, str, str]] = []
    confirmations = WriteConfirmations(on_issue=lambda *issue: issues.append(issue))
    confirmations.record_pending("unit_1", "airflow_levels", (60, 60))

    assert confirmations.mark_unconfirmed("unit_1", "airflow_levels", "no readback")
    assert confirmations.set_status("unit_1", "airflow_levels", "mismatch")

    assert issues == [("unit_1", "airflow_levels", "unconfirmed")]
    assert confirmations.by_room["unit_1"]["airflow_levels"].issue_counted


def test_timed_out_pending_write_is_counted_once_before_readback() -> None:
    issues: list[tuple[str, str, str]] = []
    confirmations = WriteConfirmations(on_issue=lambda *issue: issues.append(issue))
    write_key = f"{CONTROL_SETTING_WRITE_PREFIX}humidity_starting_point"
    started_at = dt_util.utcnow() - timedelta(seconds=WRITE_CONFIRMATION_TIMEOUT_SECONDS + 1)
    confirmations.record_pending("unit_1", write_key, 70)
    confirmations.by_room["unit_1"][write_key] = replace(
        confirmations.by_room["unit_1"][write_key],
        started_at=started_at,
    )

    assert confirmations.confirm({"unit_1": RoomState()})
    assert issues == [("unit_1", write_key, "unconfirmed")]

    read_at = dt_util.utcnow() + timedelta(seconds=1)
    mismatched_state = RoomState(
        humidity_starting_point=60,
        group_read_health=(
            (
                "control_settings",
                ReadHealth(last_attempt=read_at, last_successful_read=read_at),
            ),
        ),
    )
    assert confirmations.confirm({"unit_1": mismatched_state})
    assert confirmations.by_room["unit_1"][write_key].status == "mismatch"
    assert issues == [("unit_1", write_key, "unconfirmed")]


def test_late_mismatch_is_not_misclassified_as_unconfirmed() -> None:
    issues: list[tuple[str, str, str]] = []
    confirmations = WriteConfirmations(on_issue=lambda *issue: issues.append(issue))
    write_key = f"{CONTROL_SETTING_WRITE_PREFIX}humidity_starting_point"
    started_at = dt_util.utcnow() - timedelta(seconds=WRITE_CONFIRMATION_TIMEOUT_SECONDS + 1)
    confirmations.record_pending("unit_1", write_key, 70)
    confirmations.by_room["unit_1"][write_key] = replace(
        confirmations.by_room["unit_1"][write_key],
        started_at=started_at,
    )
    read_at = dt_util.utcnow()
    state = RoomState(
        humidity_starting_point=60,
        group_read_health=(
            (
                "control_settings",
                ReadHealth(last_attempt=read_at, last_successful_read=read_at),
            ),
        ),
    )

    assert confirmations.confirm({"unit_1": state})

    assert confirmations.by_room["unit_1"][write_key].status == "mismatch"
    assert issues == [("unit_1", write_key, "mismatch")]


def test_confirm_skips_rooms_without_state() -> None:
    confirmations = WriteConfirmations()
    confirmations.record_pending("unit_1", "preset_mode", "manual")

    assert confirmations.confirm({}) is False
    assert confirmations.by_room["unit_1"]["preset_mode"].status == "pending"


def test_read_error_does_not_change_an_already_unconfirmed_write() -> None:
    confirmations = WriteConfirmations()
    write_key = f"{CONTROL_SETTING_WRITE_PREFIX}humidity_starting_point"
    confirmations.record_pending("unit_1", write_key, 70)
    assert confirmations.mark_unconfirmed("unit_1", write_key, "write failed")

    started_at = confirmations.by_room["unit_1"][write_key].started_at
    state = RoomState(
        group_read_health=(
            (
                "control_settings",
                ReadHealth(
                    last_attempt=started_at + timedelta(seconds=1),
                    last_error="read failed",
                ),
            ),
        )
    )

    assert confirmations.confirm({"unit_1": state}) is False
    assert confirmations.by_room["unit_1"][write_key].last_error == "write failed"


def test_airflow_readback_waits_for_a_supported_mode_and_available_targets() -> None:
    started_at = dt_util.utcnow()
    read_at = started_at + timedelta(seconds=1)
    health = ReadHealth(last_attempt=read_at, last_successful_read=read_at)
    confirmation = WriteConfirmation(expected_value=(60, 60), started_at=started_at)

    no_mode = RoomState(group_read_health=(("flow_control", health),))
    assert _checked_confirmation(no_mode, "airflow_levels", confirmation) is None

    unbalanced_without_targets = RoomState(
        operation_mode=OPERATION_MODE_UNBALANCED,
        group_read_health=(("flow_control", health),),
    )
    assert _checked_confirmation(unbalanced_without_targets, "airflow_levels", confirmation) is None

    unverifiable = replace(confirmation, status="unverifiable")
    during_intensive = RoomState(
        intensive_active=True,
        group_read_health=(("flow_control", health),),
    )
    assert _checked_confirmation(during_intensive, "airflow_levels", unverifiable) is None


@pytest.mark.parametrize("target_level", [40, 60])
def test_app_encoded_balanced_readback_confirms_a_level_write(target_level: int) -> None:
    confirmations = WriteConfirmations()
    confirmations.record_pending("unit_1", "airflow_levels", (target_level, target_level))
    started_at = confirmations.by_room["unit_1"]["airflow_levels"].started_at
    read_at = started_at + timedelta(seconds=1)
    state = RoomState(
        operation_mode=OPERATION_MODE_MANUAL,
        balanced_target_readback=target_level,
        group_read_health=(
            ("flow_control", ReadHealth(last_attempt=read_at, last_successful_read=read_at)),
        ),
    )

    assert confirmations.confirm({"unit_1": state}) is True
    confirmation = confirmations.by_room["unit_1"]["airflow_levels"]
    assert confirmation.status == "confirmed"
    assert confirmation.actual_value == (target_level, target_level)


def test_readback_rejects_unknown_write_key() -> None:
    with pytest.raises(ValueError, match="Unknown write confirmation key"):
        _readback_value(RoomState(), "unknown")


@pytest.mark.parametrize("status", ["pending", "unconfirmed", "mismatch", "unverifiable"])
def test_open_write_needs_confirmation_until_the_health_retention_ends(status: str) -> None:
    confirmations = WriteConfirmations()
    confirmations.record_pending("unit_1", "airflow_levels", (60, 60))
    confirmations.set_status("unit_1", "airflow_levels", status)

    assert confirmations.needs_confirmation("unit_1", "airflow_levels")

    confirmation = confirmations.by_room["unit_1"]["airflow_levels"]
    confirmations.by_room["unit_1"]["airflow_levels"] = replace(
        confirmation,
        started_at=confirmation.started_at - timedelta(seconds=WRITE_HEALTH_RETENTION_SECONDS + 1),
    )

    assert not confirmations.needs_confirmation("unit_1", "airflow_levels")


@pytest.mark.parametrize("status", ["confirmed", "failed", "superseded"])
def test_final_write_needs_no_confirmation(status: str) -> None:
    confirmations = WriteConfirmations()
    confirmations.record_pending("unit_1", "airflow_levels", (60, 60))
    confirmations.set_status("unit_1", "airflow_levels", status)

    assert not confirmations.needs_confirmation("unit_1", "airflow_levels")
    assert not confirmations.needs_confirmation("unit_2", "airflow_levels")
