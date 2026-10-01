"""Poll jobs and how they are planned for the configured rooms.

Each job reads one compact group of registers for one room. Jobs of one group
are staggered across their interval, so the gateway load stays smooth instead
of bursty.
"""

from __future__ import annotations

import operator
from collections.abc import Iterable, Sequence
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
from .models import RefreshPlan, RoomConfig

FULL_REFRESH_PLAN = RefreshPlan()
AIRFLOW_REFRESH_PLAN = RefreshPlan.only(refresh_airflow=True)
CONTROL_SETTINGS_REFRESH_PLAN = RefreshPlan.only(refresh_control_settings=True)


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
