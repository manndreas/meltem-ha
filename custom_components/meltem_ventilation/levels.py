"""Supply and extract airflow of a unit, as targets and as measurements."""

from __future__ import annotations

from .const import (
    LEVEL_SOURCE_MEASURED,
    LEVEL_SOURCE_TARGET,
    OPERATION_MODE_OFF,
    OPERATION_MODE_UNBALANCED,
    SENSOR_OPERATION_MODES,
)
from .models import RoomState
from .read_health import group_fresh

# Rounding between m3/h and the raw 0..200 register costs at most 1 m3/h.
LEVEL_CONFIRM_TOLERANCE = 2
# Percent-to-m3/h rounding can leave two meant-to-be-equal directions one step apart.
BALANCED_LEVEL_TOLERANCE = 1

type LevelPair = tuple[int | None, int | None]


def levels_reached(confirmed: LevelPair, expected: tuple[int, int]) -> bool:
    """Return whether both directions reached their pending target."""

    return all(
        actual is not None and abs(actual - target) <= LEVEL_CONFIRM_TOLERANCE
        for actual, target in zip(confirmed, expected, strict=True)
    )


def levels_balanced(level: int, other: int) -> bool:
    """Return whether two direction levels should run as one balanced level."""

    if level == other:
        return True
    return level > 0 and other > 0 and abs(level - other) <= BALANCED_LEVEL_TOLERANCE


def first_known(*values: int | None) -> int | None:
    return next((value for value in values if value is not None), None)


def target_levels(state: RoomState, *, airflow_is_fresh: bool) -> LevelPair:
    """Split the room state into a supply/extract target pair.

    With ``airflow_is_fresh`` a missing target falls back to the measured airflow.
    """

    if state.operation_mode == OPERATION_MODE_OFF:
        return 0, 0

    if state.operation_mode == OPERATION_MODE_UNBALANCED:
        supply = state.target_level
        if supply is None and airflow_is_fresh:
            supply = state.supply_air_flow
        extract = (
            state.extract_target_level
            if state.extract_target_level is not None
            else state.extract_air_flow if airflow_is_fresh else None
        )
        return supply, extract

    if state.operation_mode in SENSOR_OPERATION_MODES:
        # The unit picks the airflow itself and exposes no target register.
        if airflow_is_fresh:
            return state.supply_air_flow, state.extract_air_flow
        return None, None

    # Balanced modes drive both fans from a single register.
    common = state.target_level
    if common is None and airflow_is_fresh:
        common = state.supply_air_flow
    if common is None and airflow_is_fresh:
        common = state.extract_air_flow
    return common, common


def reported_levels(state: RoomState) -> tuple[LevelPair | None, str | None]:
    """Return the supply/extract pair the unit reports and whether it is a target."""

    airflow_is_fresh = group_fresh(state, "flow")
    levels: LevelPair | None = None
    source: str | None = None
    if not group_fresh(state, "flow_control") or state.operation_mode is None:
        if airflow_is_fresh:
            levels = (state.supply_air_flow, state.extract_air_flow)
            source = LEVEL_SOURCE_MEASURED
    else:
        levels = target_levels(state, airflow_is_fresh=airflow_is_fresh)
        targets = target_levels(state, airflow_is_fresh=False)
        source = LEVEL_SOURCE_TARGET if levels == targets else LEVEL_SOURCE_MEASURED
    if levels == (None, None):
        return None, None
    return levels, source
