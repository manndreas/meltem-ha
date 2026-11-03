"""How fresh the values of each read group of a unit are."""

from __future__ import annotations

import time
from typing import NamedTuple

from homeassistant.util import dt as dt_util

from .const import (
    AIRFLOW_STALE_AFTER_SECONDS,
    OPTIONAL_DIAGNOSTIC_READ_GROUPS,
    READ_FAILURE_THRESHOLD,
    READ_GROUP_ENTITY_KEYS,
)
from .modbus_helpers import MeltemModbusError
from .models import ReadHealth, ReadJobOutcome, RefreshPlan, RoomConfig, RoomState
from .polling import READ_GROUP_INTERVAL_SECONDS


def read_group_for_entity(entity_key: str) -> str | None:
    """Return the read group that delivers one entity's value."""

    return next(
        (
            group_key
            for group_key, entity_keys in READ_GROUP_ENTITY_KEYS.items()
            if entity_key in entity_keys
        ),
        None,
    )


def read_too_old(group_key: str, health: ReadHealth) -> bool:
    """Return whether a group's last successful read is past its stale limit.

    The age comes from the monotonic clock, so a wall-clock jump, for example
    the first NTP sync of a host without a real-time clock, neither ages
    nor rejuvenates the values. Health without a monotonic stamp falls back
    to the wall clock.
    """

    stale_after = (
        AIRFLOW_STALE_AFTER_SECONDS
        if group_key == "flow"
        else READ_GROUP_INTERVAL_SECONDS[group_key] * 3
    )
    if health.last_success_monotonic is not None:
        return time.monotonic() - health.last_success_monotonic > stale_after
    if health.last_successful_read is None:
        return True
    return (dt_util.utcnow() - health.last_successful_read).total_seconds() > stale_after


def group_fresh(state: RoomState, group_key: str) -> bool:
    """Return whether the group's most recent attempt succeeded recently."""

    health = state.read_health_for(group_key)
    if (
        health.last_successful_read is None
        or health.last_successful_read != health.last_attempt
        or health.consecutive_failures != 0
        or health.last_error is not None
    ):
        return False
    return not read_too_old(group_key, health)


def group_available(state: RoomState, group_key: str) -> bool:
    """Return whether a group's values are recent and have recovered."""

    health = state.read_health_for(group_key)
    if health.last_successful_read is None or health.consecutive_failures >= READ_FAILURE_THRESHOLD:
        return False
    return not read_too_old(group_key, health)


def _unsupported_optional_group(group_key: str, health: ReadHealth) -> bool:
    return (
        group_key in OPTIONAL_DIAGNOSTIC_READ_GROUPS
        and health.last_successful_read is None
        and health.last_attempt is not None
        and not health.last_error_timed_out
    )


def group_stale(state: RoomState, group_key: str) -> bool | None:
    """Return whether a group's last successful read is stale, or ``None`` if unknown."""

    health = state.read_health_for(group_key)
    if health.last_attempt is None or _unsupported_optional_group(group_key, health):
        return None
    if health.consecutive_failures >= READ_FAILURE_THRESHOLD:
        return True
    if health.last_successful_read is None:
        # A group the unit refused before ever delivering it is only retried
        # after a backoff, while its entities are already unavailable. A
        # timeout says nothing about the unit and waits for the threshold.
        return True if health.last_error is not None and not health.last_error_timed_out else None
    return read_too_old(group_key, health)


def record_read_failures(
    state: RoomState, room: RoomConfig, refresh_plan: RefreshPlan, error: Exception
) -> RoomState:
    """Record a failed read for the groups of a plan that the room has."""

    attempted_at = dt_util.utcnow()
    for group_key in refresh_plan.read_groups():
        if room.supports_any(READ_GROUP_ENTITY_KEYS[group_key]):
            state = state.with_read_health(
                group_key, state.read_health_for(group_key).failed(attempted_at, str(error))
            )
    return state


def mode_read_since(state: RoomState, baseline: ReadHealth) -> bool:
    """Return whether the mode registers were read successfully after ``baseline``.

    ``baseline`` is the mode group's health when a write was accepted. Reads
    and writes share the gateway lock, so only a later read changes it.
    """

    health = state.read_health_for("flow_control")
    return (
        health.last_error is None
        and health.last_successful_read is not None
        and health.last_successful_read != baseline.last_successful_read
    )


def read_since(state: RoomState, group_key: str, baseline: ReadHealth) -> bool:
    """Return whether a group has a fresh successful read made after ``baseline``."""

    return group_fresh(state, group_key) and state.read_health_for(group_key) != baseline


class JobReadFailure(NamedTuple):
    """A job whose recorded primary read groups all failed without raising."""

    error: MeltemModbusError
    # No request of the job got a valid answer from the link.
    link_failure: bool


def job_read_failure(
    room: RoomConfig, outcome: ReadJobOutcome, refresh_plan: RefreshPlan
) -> JobReadFailure | None:
    """Return the failure when all recorded primary groups failed."""

    recorded = [
        group for group in _job_read_groups(room, refresh_plan) if group in outcome.recorded_groups
    ]
    if not recorded or any(group not in outcome.errors for group in recorded):
        return None
    return JobReadFailure(
        MeltemModbusError(outcome.errors[recorded[0]]), link_failure=not outcome.answered
    )


def job_read_attempted(
    room: RoomConfig, outcome: ReadJobOutcome, refresh_plan: RefreshPlan
) -> bool:
    """Return whether any supported group was actually read during this job."""

    return any(
        group in outcome.attempted_groups and room.supports_any(READ_GROUP_ENTITY_KEYS[group])
        for group in refresh_plan.read_groups()
    )


def job_read_unanswered(
    room: RoomConfig, outcome: ReadJobOutcome, refresh_plan: RefreshPlan
) -> bool:
    """Return whether the job sent requests and none of them got a valid answer."""

    return job_read_attempted(room, outcome, refresh_plan) and not outcome.answered


def job_read_received_values(
    room: RoomConfig, outcome: ReadJobOutcome, refresh_plan: RefreshPlan
) -> bool:
    """Return whether an attempted primary group yielded a usable value."""

    return bool(outcome.value_groups.intersection(_job_read_groups(room, refresh_plan)))


def job_read_succeeded(
    room: RoomConfig, outcome: ReadJobOutcome, refresh_plan: RefreshPlan
) -> bool:
    """Return whether a primary group had a successful read during this job."""

    return job_read_received_values(room, outcome, refresh_plan) or any(
        group in outcome.unit_success_groups
        and group in outcome.recorded_groups
        and group not in outcome.errors
        for group in _job_read_groups(room, refresh_plan)
    )


def _job_read_groups(room: RoomConfig, refresh_plan: RefreshPlan) -> tuple[str, ...]:
    """Return the groups whose failures determine the outcome of one job."""

    if refresh_plan.refresh_airflow and room.supports_any(READ_GROUP_ENTITY_KEYS["flow"]):
        return ("flow",)
    return tuple(
        group_key
        for group_key in refresh_plan.read_groups()
        if room.supports_any(READ_GROUP_ENTITY_KEYS[group_key])
    )


def data_health_stale(
    state: RoomState | None, *, writes_unhealthy: bool, has_writes: bool
) -> bool | None:
    """Return whether any read group or write confirmation is unhealthy."""

    if state is None:
        return None
    if any(group_stale(state, group_key) is True for group_key, _ in state.group_read_health):
        return True
    if writes_unhealthy:
        return True
    if (
        any(group_stale(state, group_key) is False for group_key, _ in state.group_read_health)
        or has_writes
    ):
        return False
    return None


def data_health_attributes(
    room: RoomConfig, state: RoomState, write_attributes: dict[str, object]
) -> dict[str, dict[str, object]]:
    """Return per-group read health and write outcomes for one room."""

    attributes: dict[str, dict[str, object]] = {}
    for group_key, entity_keys in READ_GROUP_ENTITY_KEYS.items():
        if not room.supports_any(entity_keys):
            continue
        health = state.read_health_for(group_key)
        attributes[group_key] = {
            "last_attempt": health.last_attempt.isoformat() if health.last_attempt else None,
            "last_successful_read": health.last_successful_read.isoformat()
            if health.last_successful_read
            else None,
            "consecutive_failures": health.consecutive_failures,
            "last_error": health.last_error,
            "stale": group_stale(state, group_key),
        }
    attributes["writes"] = write_attributes
    return attributes
