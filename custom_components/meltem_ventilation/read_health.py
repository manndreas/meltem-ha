"""How fresh the values of each read group of a unit are."""

from __future__ import annotations

from datetime import datetime

from homeassistant.util import dt as dt_util

from .const import AIRFLOW_STALE_AFTER_SECONDS, READ_FAILURE_THRESHOLD, READ_GROUP_ENTITY_KEYS
from .models import RefreshPlan, RoomConfig, RoomState
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
    if (
        health.last_successful_read is None
        or health.consecutive_failures >= READ_FAILURE_THRESHOLD
    ):
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
