"""Supply and extract airflow of a unit, as targets and as measurements."""

from __future__ import annotations

from homeassistant.exceptions import HomeAssistantError

from .const import (
    DIRECTION_SUPPLY,
    DOMAIN,
    LEVEL_SOURCE_MEASURED,
    LEVEL_SOURCE_PENDING,
    LEVEL_SOURCE_TARGET,
    LEVEL_WRITE_FALLBACK_BALANCED,
    LEVEL_WRITE_FALLBACK_UNKNOWN_MODE,
    OPERATION_MODE_MANUAL,
    OPERATION_MODE_OFF,
    OPERATION_MODE_UNBALANCED,
    PRESET_MODE_TO_RAW_CODE,
    SENSOR_OPERATION_MODES,
)
from .models import RoomConfig, RoomState
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
        if not airflow_is_fresh:
            return state.target_level, state.extract_target_level
        return (
            first_known(state.target_level, state.supply_air_flow),
            first_known(state.extract_target_level, state.extract_air_flow),
        )

    if state.operation_mode in SENSOR_OPERATION_MODES:
        # The unit picks the airflow itself and exposes no target register.
        if airflow_is_fresh:
            return state.supply_air_flow, state.extract_air_flow
        return None, None

    # Balanced modes drive both fans from a single register; in preset modes,
    # target_level is derived from measured airflow rather than a register.
    preset_active = state.preset_mode in PRESET_MODE_TO_RAW_CODE
    common = first_known(
        state.balanced_target_readback,
        None if preset_active and not airflow_is_fresh else state.target_level,
    )
    if common is not None:
        return common, common
    if airflow_is_fresh:
        return (
            first_known(state.supply_air_flow, state.extract_air_flow),
            first_known(state.extract_air_flow, state.supply_air_flow),
        )
    return None, None


def reported_levels(state: RoomState) -> tuple[LevelPair | None, str | None]:
    """Return the supply/extract pair the unit reports and whether it is a target."""

    airflow_is_fresh = group_fresh(state, "flow")
    if not group_fresh(state, "flow_control") or state.operation_mode is None:
        if not airflow_is_fresh:
            return None, None
        levels: LevelPair = (state.supply_air_flow, state.extract_air_flow)
        source = LEVEL_SOURCE_MEASURED
    else:
        levels = target_levels(state, airflow_is_fresh=airflow_is_fresh)
        targets = target_levels(state, airflow_is_fresh=False)
        source = LEVEL_SOURCE_TARGET if levels == targets else LEVEL_SOURCE_MEASURED
    if levels == (None, None):
        return None, None
    return levels, source


def mode_write_levels(state: RoomState, effective: LevelPair) -> LevelPair:
    """Resolve balanced and extract airflow levels for an operating-mode write."""

    supply, extract = effective
    balanced_level = first_known(
        supply,
        state.target_level,
        state.supply_air_flow,
        state.extract_air_flow,
    )
    extract_level = first_known(
        extract,
        state.extract_target_level,
        state.extract_air_flow,
        balanced_level,
    )
    return balanced_level, extract_level


def plan_direction_write(
    room: RoomConfig,
    state: RoomState,
    levels: LevelPair,
    source: str | None,
    direction: str,
    level: int,
) -> tuple[tuple[int, int] | int, str | None]:
    """Plan a directional fan write as either one balanced level or a pair."""

    supply, extract = levels
    other = extract if direction == DIRECTION_SUPPLY else supply
    if other is None:
        if level == 0:
            # Stopping both directions instead would be a silent surprise.
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="opposite_airflow_unknown",
                translation_placeholders={"unit": room.name},
            )
        return level, LEVEL_WRITE_FALLBACK_BALANCED

    operation_mode = state.operation_mode
    if source == LEVEL_SOURCE_PENDING and supply is not None and extract is not None:
        operation_mode = (
            OPERATION_MODE_OFF
            if supply == extract == 0
            else OPERATION_MODE_MANUAL
            if levels_balanced(supply, extract)
            else OPERATION_MODE_UNBALANCED
        )
    if (
        source == LEVEL_SOURCE_MEASURED
        and supply is not None
        and extract is not None
        and abs(supply - extract) <= LEVEL_CONFIRM_TOLERANCE
    ):
        # Measured airflow jitters around one balanced target.
        other = round((supply + extract) / 2)

    fallback = LEVEL_WRITE_FALLBACK_UNKNOWN_MODE if operation_mode is None else None
    starting_from_off = operation_mode == OPERATION_MODE_OFF
    leaving_sensor_control = level > 0 and operation_mode in SENSOR_OPERATION_MODES
    if levels_balanced(level, other) or starting_from_off or leaving_sensor_control:
        return level, fallback

    pair = (level, other) if direction == DIRECTION_SUPPLY else (other, level)
    return pair, fallback
