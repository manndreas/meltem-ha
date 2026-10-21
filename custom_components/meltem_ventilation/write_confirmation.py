"""Outcome of each write, judged by the next readback of its register group."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import replace

from homeassistant.exceptions import ServiceValidationError
from homeassistant.util import dt as dt_util

from .const import (
    CONTROL_LEVEL_RANGES,
    DOMAIN,
    OPERATION_MODE_MANUAL,
    OPERATION_MODE_OFF,
    OPERATION_MODE_UNBALANCED,
    WRITE_CONFIRMATION_TIMEOUT_SECONDS,
    WRITE_HEALTH_RETENTION_SECONDS,
)
from .levels import levels_reached, target_levels
from .modbus_helpers import normalize_control_setting
from .models import RoomConfig, RoomState, WriteConfirmation, WriteValue
from .read_health import group_fresh

CONTROL_SETTING_WRITE_PREFIX = "control_setting:"

_BASE_MODE_WRITES = frozenset({"airflow_levels", "operation_mode", "preset_mode"})
_FLOW_CONTROL_WRITES = _BASE_MODE_WRITES | {"intensive"}
# Base writes that leave a running intensive override in place (HW-1).
_WRITES_HIDDEN_BY_INTENSIVE = frozenset({"airflow_levels", "operation_mode"})
# Outcomes a later readback can no longer change.
_FINAL_STATUSES = frozenset({"failed", "confirmed", "superseded"})
# Outcomes a later exact readback can still turn into a confirmation; an
# unverifiable write is judged once the intensive override has ended.
_CONFIRMABLE_STATUSES = frozenset({"pending", "unconfirmed", "mismatch", "unverifiable"})
# Outcomes that flag data health while they are recent.
_UNHEALTHY_STATUSES = frozenset({"unconfirmed", "mismatch", "failed"})


def write_group(write_key: str) -> str:
    """Return the read group whose readback confirms a write."""

    if write_key.startswith(CONTROL_SETTING_WRITE_PREFIX):
        return "control_settings"
    if write_key in _FLOW_CONTROL_WRITES:
        return "flow_control"
    raise ValueError(f"Unknown write confirmation key: {write_key}")


def confirmation_status(confirmation: WriteConfirmation) -> str:
    """Return the status, treating a write that stays pending too long as unconfirmed."""

    if (
        confirmation.status == "pending"
        and (dt_util.utcnow() - confirmation.started_at).total_seconds()
        > WRITE_CONFIRMATION_TIMEOUT_SECONDS
    ):
        return "unconfirmed"
    return confirmation.status


class WriteConfirmations:
    """The last outcome per unit and write key."""

    def __init__(self, on_issue: Callable[[str, str, str], None] | None = None) -> None:
        self.by_room: dict[str, dict[str, WriteConfirmation]] = {}
        self._on_issue = on_issue

    def record_pending(self, room_key: str, write_key: str, expected_value: WriteValue) -> None:
        """Record a write the transport accepted, awaiting its readback."""

        self.by_room.setdefault(room_key, {})[write_key] = WriteConfirmation(
            expected_value=expected_value,
            started_at=dt_util.utcnow(),
        )

    def supersede(self, room_key: str, write_key: str) -> tuple[str, ...]:
        """Finish earlier base-mode writes replaced by an accepted command."""

        if write_key not in _BASE_MODE_WRITES:
            return ()
        superseded: list[str] = []
        confirmations = self.by_room.get(room_key, {})
        for previous_key, confirmation in confirmations.items():
            if (
                previous_key != write_key
                and previous_key in _BASE_MODE_WRITES
                and confirmation.status not in _FINAL_STATUSES
            ):
                confirmations[previous_key] = replace(confirmation, status="superseded")
                superseded.append(previous_key)
        return tuple(superseded)

    def unconfirmed_control_setting(self, room_key: str, setting_key: str) -> int | None:
        """Return an accepted setting whose value has not been read back yet."""

        confirmation = self.by_room.get(room_key, {}).get(
            f"{CONTROL_SETTING_WRITE_PREFIX}{setting_key}"
        )
        if (
            confirmation is not None
            and confirmation.status in ("pending", "unconfirmed")
            and isinstance(confirmation.expected_value, int)
        ):
            return confirmation.expected_value
        return None

    def check_control_level_range(
        self, room: RoomConfig, state: RoomState, setting_key: str, value: int
    ) -> None:
        """Refuse a minimum level above the maximum level of the same sensor control."""

        for min_key, max_key in CONTROL_LEVEL_RANGES:
            if setting_key not in (min_key, max_key):
                continue
            minimum = self.unconfirmed_control_setting(room.key, min_key)
            if minimum is None:
                minimum = getattr(state, min_key)
            maximum = self.unconfirmed_control_setting(room.key, max_key)
            if maximum is None:
                maximum = getattr(state, max_key)
            if setting_key == min_key:
                minimum = normalize_control_setting(min_key, value)
            else:
                maximum = normalize_control_setting(max_key, value)
            if minimum is not None and maximum is not None and minimum > maximum:
                raise ServiceValidationError(
                    translation_domain=DOMAIN,
                    translation_key="control_level_range",
                    translation_placeholders={
                        "unit": room.name,
                        "minimum": str(minimum),
                        "maximum": str(maximum),
                    },
                )

    def is_confirmed(self, room_key: str, write_key: str) -> bool:
        """Return whether a write has been confirmed by a readback."""

        confirmation = self.by_room.get(room_key, {}).get(write_key)
        return confirmation is not None and confirmation.status == "confirmed"

    def prepare_balanced_target_readback(
        self, room_key: str, state: RoomState, previous_state: RoomState
    ) -> tuple[RoomState, bool]:
        """Return the state and whether register 41121 must be read to confirm a write."""

        if (
            not group_fresh(state, "flow_control")
            or state.intensive_active
            or state.operation_mode not in (OPERATION_MODE_MANUAL, OPERATION_MODE_OFF)
        ):
            return state, False

        if not self.needs_confirmation(room_key, "airflow_levels"):
            if (
                previous_state.balanced_target_readback is not None
                and state.operation_mode == previous_state.operation_mode
                and state.preset_mode is None
                and state.target_level == previous_state.target_level
            ):
                return (
                    replace(
                        state,
                        balanced_target_readback=previous_state.balanced_target_readback,
                    ),
                    False,
                )
            return state, False

        return state, True

    def set_status(
        self,
        room_key: str,
        write_key: str,
        status: str,
        *,
        error: str | None = None,
        actual_value: WriteValue | None = None,
    ) -> bool:
        """Update one outcome without changing its expected value; return whether it existed."""

        previous = self.by_room.get(room_key, {}).get(write_key)
        if previous is None:
            return False
        issue_counted = previous.issue_counted or status in _UNHEALTHY_STATUSES
        self.by_room[room_key][write_key] = replace(
            previous,
            status=status,
            actual_value=actual_value,
            last_error=error,
            issue_counted=issue_counted,
        )
        if status in _UNHEALTHY_STATUSES and not previous.issue_counted and self._on_issue:
            self._on_issue(room_key, write_key, status)
        return True

    def mark_unconfirmed(self, room_key: str, write_key: str, error: str) -> bool:
        """Mark a still pending write unconfirmed; return whether it changed."""

        confirmation = self.by_room.get(room_key, {}).get(write_key)
        if confirmation is None or confirmation.status != "pending":
            return False
        return self.set_status(room_key, write_key, "unconfirmed", error=error)

    def confirm(self, states: Mapping[str, RoomState]) -> bool:
        """Judge pending writes by the latest readbacks; return whether any changed."""

        changed = False
        now = dt_util.utcnow()
        for room_key, confirmations in self.by_room.items():
            state = states.get(room_key)
            if state is None:
                continue
            for write_key, confirmation in tuple(confirmations.items()):
                checked = _checked_confirmation(state, write_key, confirmation)
                if checked is not None:
                    if checked.status in _UNHEALTHY_STATUSES and not confirmation.issue_counted:
                        checked = replace(checked, issue_counted=True)
                        if self._on_issue:
                            self._on_issue(room_key, write_key, checked.status)
                    confirmations[write_key] = checked
                    changed = True
                    continue
                if (
                    confirmation.status == "pending"
                    and not confirmation.issue_counted
                    and (now - confirmation.started_at).total_seconds()
                    > WRITE_CONFIRMATION_TIMEOUT_SECONDS
                ):
                    confirmation = replace(confirmation, issue_counted=True)
                    confirmations[write_key] = confirmation
                    if self._on_issue:
                        self._on_issue(room_key, write_key, "unconfirmed")
                    changed = True
        return changed

    def has_any(self, room_key: str) -> bool:
        return bool(self.by_room.get(room_key))

    def needs_confirmation(self, room_key: str, write_key: str) -> bool:
        """Return whether a recent write can still be confirmed by a later readback.

        The window ends with the data-health retention, so an extra readback
        never outlives the outcome it could still clear.
        """

        confirmation = self.by_room.get(room_key, {}).get(write_key)
        return (
            confirmation is not None
            and confirmation.status in _CONFIRMABLE_STATUSES
            and _recent(confirmation)
        )

    def unhealthy(self, room_key: str) -> bool:
        """Return whether a recent write failed, mismatched, or stayed unconfirmed."""

        return any(
            confirmation_status(confirmation) in _UNHEALTHY_STATUSES and _recent(confirmation)
            for confirmation in self.by_room.get(room_key, {}).values()
        )

    def attributes(self, room_key: str) -> dict[str, object]:
        """Return the outcomes of one unit as plain state attributes."""

        return {
            write_key: {
                "status": confirmation_status(confirmation),
                "expected_value": confirmation.expected_value,
                "actual_value": confirmation.actual_value,
                "started_at": confirmation.started_at.isoformat(),
                "last_error": confirmation.last_error,
            }
            for write_key, confirmation in self.by_room.get(room_key, {}).items()
        }


def _recent(confirmation: WriteConfirmation) -> bool:
    age = (dt_util.utcnow() - confirmation.started_at).total_seconds()
    return age <= WRITE_HEALTH_RETENTION_SECONDS


def _checked_confirmation(
    state: RoomState, write_key: str, confirmation: WriteConfirmation
) -> WriteConfirmation | None:
    """Return the new outcome of one write after a readback, or ``None`` if unchanged."""

    if confirmation.status in _FINAL_STATUSES:
        return None
    read_health = state.read_health_for(write_group(write_key))
    if read_health.last_attempt is None or read_health.last_attempt < confirmation.started_at:
        return None
    if read_health.last_error is not None:
        if confirmation.status != "pending":
            return None
        return replace(confirmation, status="unconfirmed", last_error=read_health.last_error)

    if write_key in _WRITES_HIDDEN_BY_INTENSIVE and state.intensive_active:
        # The intensive status hides the base mode; a readback after it ends judges the write.
        if confirmation.status == "unverifiable":
            return None
        return replace(confirmation, status="unverifiable", last_error=None)

    actual = _readback_value(state, write_key)
    if actual is None:
        return None
    matches = _values_match(write_key, confirmation.expected_value, actual)
    status = "confirmed" if matches else "mismatch"
    if confirmation.status == status and confirmation.actual_value == actual:
        return None
    return replace(confirmation, status=status, actual_value=actual, last_error=None)


def _readback_value(state: RoomState, write_key: str) -> WriteValue | None:
    if write_key == "airflow_levels":
        if state.operation_mode in (OPERATION_MODE_MANUAL, OPERATION_MODE_OFF):
            readback = state.balanced_target_readback
            return (readback, readback) if readback is not None else None
        if state.operation_mode != OPERATION_MODE_UNBALANCED:
            return None
        supply, extract = target_levels(state, airflow_is_fresh=False)
        if supply is None or extract is None:
            return None
        return supply, extract
    if write_key == "operation_mode":
        return state.operation_mode
    if write_key == "preset_mode":
        return state.preset_mode
    if write_key == "intensive":
        return state.intensive_active
    if write_key.startswith(CONTROL_SETTING_WRITE_PREFIX):
        value: int | None = getattr(state, write_key.removeprefix(CONTROL_SETTING_WRITE_PREFIX))
        return value
    raise ValueError(f"Unknown write confirmation key: {write_key}")


def _values_match(write_key: str, expected: WriteValue, actual: WriteValue) -> bool:
    if write_key == "airflow_levels" and isinstance(expected, tuple) and isinstance(actual, tuple):
        return levels_reached(actual, expected)
    return actual == expected
