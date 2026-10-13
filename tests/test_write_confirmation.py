"""Tests for write confirmation outcomes."""

from __future__ import annotations

from datetime import timedelta

import pytest
from homeassistant.util import dt as dt_util

from custom_components.meltem_ventilation.const import OPERATION_MODE_UNBALANCED
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

    no_mode = RoomState(
        group_read_health=(("flow_control", health),)
    )
    assert _checked_confirmation(no_mode, "airflow_levels", confirmation) is None

    unbalanced_without_targets = RoomState(
        operation_mode=OPERATION_MODE_UNBALANCED,
        group_read_health=(("flow_control", health),),
    )
    assert (
        _checked_confirmation(
            unbalanced_without_targets, "airflow_levels", confirmation
        )
        is None
    )


def test_readback_rejects_unknown_write_key() -> None:
    with pytest.raises(ValueError, match="Unknown write confirmation key"):
        _readback_value(RoomState(), "unknown")
