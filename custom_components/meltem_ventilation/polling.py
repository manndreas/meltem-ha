"""Poll jobs and how they are planned for the configured rooms.

Each job reads one compact group of registers for one room. Jobs of one group
are staggered across their interval, so the gateway load stays smooth instead
of bursty.
"""

from __future__ import annotations

import math
import operator
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass

from .const import (
    CONTROL_SETTINGS_REFRESH_SECONDS,
    FILTER_REFRESH_SECONDS,
    FLOW_REFRESH_SECONDS,
    OPERATING_HOURS_REFRESH_SECONDS,
    READ_GROUP_ENTITY_KEYS,
    STATUS_REFRESH_SECONDS,
    TEMPERATURE_REFRESH_SECONDS,
)
from .models import EMPTY_ROOM_STATE, RefreshPlan, RoomConfig, RoomState

FULL_REFRESH_PLAN = RefreshPlan()
AIRFLOW_REFRESH_PLAN = RefreshPlan.only(refresh_airflow=True)
CONTROL_SETTINGS_REFRESH_PLAN = RefreshPlan.only(refresh_control_settings=True)

TRANSPORT_BACKOFF_AFTER_FAILURES = 3
TRANSPORT_BACKOFF_START_SECONDS = 5.0
TRANSPORT_BACKOFF_MAX_SECONDS = 60.0
# Every unanswered read costs several timeouts, so silent units are polled
# rarely to keep the shared bus free for the units that still respond.
SILENT_ROOM_POLL_SECONDS = 60
# First retry of a group that never answered; later retries wait as long as
# the integration has been running, so the extra reads stay few.
UNREAD_GROUP_RETRY_SECONDS = 60.0
# Fallback wake-up for the degenerate case of a gateway without any poll job.
IDLE_TICK_SECONDS = 60.0


@dataclass(frozen=True, slots=True)
class JobGroup:
    """One refresh group: which registers it covers and how often it runs."""

    key: str
    interval_seconds: int
    refresh_plan: RefreshPlan

    @property
    def entity_keys(self) -> frozenset[str]:
        """Return the entities whose values this job refreshes."""

        return frozenset().union(
            *(READ_GROUP_ENTITY_KEYS[group] for group in self.refresh_plan.read_groups())
        )


JOB_GROUPS: tuple[JobGroup, ...] = (
    JobGroup("flow", FLOW_REFRESH_SECONDS, AIRFLOW_REFRESH_PLAN),
    JobGroup("status", STATUS_REFRESH_SECONDS, RefreshPlan.only(refresh_status=True)),
    JobGroup(
        "temperature",
        TEMPERATURE_REFRESH_SECONDS,
        RefreshPlan.only(refresh_temperatures=True, refresh_environment=True),
    ),
    JobGroup(
        "filter",
        FILTER_REFRESH_SECONDS,
        RefreshPlan.only(refresh_filter_change_due=True, refresh_filter_days=True),
    ),
    JobGroup(
        "hours",
        OPERATING_HOURS_REFRESH_SECONDS,
        RefreshPlan.only(refresh_operating_hours=True),
    ),
    JobGroup(
        "control_settings",
        CONTROL_SETTINGS_REFRESH_SECONDS,
        CONTROL_SETTINGS_REFRESH_PLAN,
    ),
)

READ_GROUP_INTERVAL_SECONDS: dict[str, int] = {
    group_key: job_group.interval_seconds
    for job_group in JOB_GROUPS
    for group_key in job_group.refresh_plan.read_groups()
}


@dataclass(slots=True)
class PollJob:
    """One scheduled read job for one room and one refresh group."""

    key: str
    room_key: str
    refresh_plan: RefreshPlan
    interval_seconds: int
    next_due: float

    def never_read(self, room: RoomConfig, states: Mapping[str, RoomState]) -> bool:
        """Return whether a group of this job has no successful read yet."""

        state = states.get(self.room_key, EMPTY_ROOM_STATE)
        return any(
            room.supports_any(READ_GROUP_ENTITY_KEYS[group_key])
            and state.read_health_for(group_key).last_successful_read is None
            for group_key in self.refresh_plan.read_groups()
        )

    def interval(
        self,
        *,
        elapsed_since_start: float,
        never_read: bool = False,
        room_silent: bool = False,
    ) -> float:
        """Return the job interval, stretched while its unit stays silent."""

        interval = float(self.interval_seconds)
        if never_read:
            interval = min(interval, max(UNREAD_GROUP_RETRY_SECONDS, elapsed_since_start))
        if room_silent:
            return max(interval, SILENT_ROOM_POLL_SECONDS)
        return interval


def room_needs_job(room: RoomConfig, group: JobGroup) -> bool:
    """Return whether a room has any entity a job group refreshes."""

    return room.supports_any(group.entity_keys)


def build_jobs(rooms: Sequence[RoomConfig], now: float) -> list[PollJob]:
    """Return one job per room and group, staggered across the group interval."""

    jobs: list[PollJob] = []
    for group in JOB_GROUPS:
        group_rooms = [room for room in rooms if room_needs_job(room, group)]
        if not group_rooms:
            continue
        # Without the spread a group would fire for every room at once.
        spacing = group.interval_seconds / len(group_rooms)
        jobs.extend(
            PollJob(
                key=group.key,
                room_key=room.key,
                refresh_plan=group.refresh_plan,
                interval_seconds=group.interval_seconds,
                next_due=now + (index * spacing),
            )
            for index, room in enumerate(group_rooms)
        )
    return jobs


def select_due_job(jobs: Iterable[PollJob], now: float) -> PollJob | None:
    """Return the job that has been due the longest, if any."""

    return min(
        (job for job in jobs if job.next_due <= now),
        key=operator.attrgetter("next_due"),
        default=None,
    )


def next_tick_seconds(
    jobs: Sequence[PollJob],
    *,
    startup_pending: bool,
    tick_seconds: float,
    now: float,
    loop_time: float,
) -> float:
    """Return the delay until the next scheduler wake-up."""

    if not jobs:
        return IDLE_TICK_SECONDS
    earliest_due = now if startup_pending else min(job.next_due for job in jobs)
    seconds = max(tick_seconds, earliest_due - now)
    # HA schedules from int(loop.time()), which would fire up to a second early.
    return seconds + loop_time - math.floor(loop_time)


def backoff_seconds(consecutive_failures: int) -> float | None:
    """Return the gateway polling backoff delay for the current failure streak."""

    if consecutive_failures < TRANSPORT_BACKOFF_AFTER_FAILURES:
        return None
    exponent = min(
        consecutive_failures - TRANSPORT_BACKOFF_AFTER_FAILURES,
        math.ceil(math.log2(TRANSPORT_BACKOFF_MAX_SECONDS / TRANSPORT_BACKOFF_START_SECONDS)),
    )
    return min(
        TRANSPORT_BACKOFF_MAX_SECONDS,
        float(TRANSPORT_BACKOFF_START_SECONDS * (2**exponent)),
    )
