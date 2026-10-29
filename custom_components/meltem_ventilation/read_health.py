"""How fresh the values of each read group of a unit are."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime
from typing import NamedTuple

from homeassistant.util import dt as dt_util

from .const import AIRFLOW_STALE_AFTER_SECONDS, READ_FAILURE_THRESHOLD, READ_GROUP_ENTITY_KEYS
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


def read_too_old(group_key: str, last_successful_read: datetime) -> bool:
    """Return whether a group's last successful read is past its stale limit."""

    stale_after = (
        AIRFLOW_STALE_AFTER_SECONDS
        if group_key == "flow"
        else READ_GROUP_INTERVAL_SECONDS[group_key] * 3
    )
    return (dt_util.utcnow() - last_successful_read).total_seconds() > stale_after


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
    return not read_too_old(group_key, health.last_successful_read)


def group_available(state: RoomState, group_key: str) -> bool:
    """Return whether a group's values are recent and have recovered."""

    health = state.read_health_for(group_key)
    if health.last_successful_read is None or health.consecutive_failures >= READ_FAILURE_THRESHOLD:
        return False
    return not read_too_old(group_key, health.last_successful_read)


def group_stale(state: RoomState, group_key: str) -> bool | None:
    """Return whether a group's last successful read is stale, or ``None`` if unknown."""

    health = state.read_health_for(group_key)
    if health.last_attempt is None:
        return None
    if health.consecutive_failures >= READ_FAILURE_THRESHOLD:
        return True
    if health.last_successful_read is None:
        return None
    return read_too_old(group_key, health.last_successful_read)


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


def mode_read_since(state: RoomState, written_at: datetime) -> bool:
    """Return whether the mode registers were read successfully after a write."""

    health = state.read_health_for("flow_control")
    return (
        health.last_error is None
        and health.last_successful_read is not None
        and health.last_successful_read >= written_at
    )


class JobReadFailure(NamedTuple):
    """A job whose attempted primary read groups all failed without raising."""

    error: MeltemModbusError
    # Every failed group timed out or was refused by the link, not by the unit.
    link_failure: bool


def job_read_failure(
    room: RoomConfig,
    state: RoomState,
    refresh_plan: RefreshPlan,
    read_started_at: datetime,
) -> JobReadFailure | None:
    """Return the failure when all attempted primary groups failed."""

    attempted = _attempted_read_health(
        room, state, _job_read_groups(room, refresh_plan), read_started_at
    )
    if not attempted or any(health.last_error is None for health in attempted):
        return None
    job = _current_job(state, read_started_at)
    return JobReadFailure(
        MeltemModbusError(str(attempted[0].last_error)),
        link_failure=(
            not job.answered if job is not None else all(h.last_error_timed_out for h in attempted)
        ),
    )


def job_read_attempted(
    room: RoomConfig,
    state: RoomState,
    refresh_plan: RefreshPlan,
    read_started_at: datetime,
) -> bool:
    """Return whether any supported group was actually read during this job."""

    if job := _current_job(state, read_started_at):
        return any(
            group in job.attempted_groups and room.supports_any(READ_GROUP_ENTITY_KEYS[group])
            for group in refresh_plan.read_groups()
        )
    return bool(_attempted_read_health(room, state, refresh_plan.read_groups(), read_started_at))


def job_read_unanswered(
    room: RoomConfig,
    state: RoomState,
    refresh_plan: RefreshPlan,
    read_started_at: datetime,
) -> bool:
    """Return whether every group actually attempted received no valid answer."""

    if job := _current_job(state, read_started_at):
        return job_read_attempted(room, state, refresh_plan, read_started_at) and not job.answered
    attempted = _attempted_read_health(room, state, refresh_plan.read_groups(), read_started_at)
    return bool(attempted) and all(
        health.last_error is not None and health.last_error_timed_out for health in attempted
    )


def job_read_received_values(
    room: RoomConfig, state: RoomState, refresh_plan: RefreshPlan, read_started_at: datetime
) -> bool:
    """Return whether an attempted primary group yielded a usable value."""

    job = _current_job(state, read_started_at)
    return job is not None and bool(
        job.value_groups.intersection(_job_read_groups(room, refresh_plan))
    )


def _current_job(state: RoomState, read_started_at: datetime) -> ReadJobOutcome | None:
    job = state.last_read_job
    return job if job is not None and job.completed_at >= read_started_at else None


def _attempted_read_health(
    room: RoomConfig, state: RoomState, groups: Iterable[str], read_started_at: datetime
) -> tuple[ReadHealth, ...]:
    return tuple(
        health
        for group_key in groups
        if room.supports_any(READ_GROUP_ENTITY_KEYS[group_key])
        and (health := state.read_health_for(group_key)).last_attempt is not None
        and health.last_attempt >= read_started_at
    )


def job_read_succeeded(
    room: RoomConfig,
    state: RoomState,
    refresh_plan: RefreshPlan,
    read_started_at: datetime,
) -> bool:
    """Return whether a primary group had a successful read during this job."""

    job = _current_job(state, read_started_at)
    return job_read_received_values(room, state, refresh_plan, read_started_at) or any(
        (job is None or group_key in job.unit_success_groups)
        and (health := state.read_health_for(group_key)).last_error is None
        and health.last_successful_read is not None
        and health.last_successful_read >= read_started_at
        for group_key in _job_read_groups(room, refresh_plan)
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
