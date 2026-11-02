"""Encode Meltem mode commands and decode the read-only mode status map.

Pure functions: the register writes for a mode or preset command, the airflow
scaling of the set registers, and the decoding of ``41100..41102``.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

from .const import (
    APP_UNBALANCED_PRESET_BASE,
    MODE_MANUAL,
    MODE_OFF,
    MODE_SENSOR_CONTROL,
    MODE_UNBALANCED,
    OPERATION_MODE_MANUAL,
    OPERATION_MODE_OFF,
    OPERATION_MODE_UNBALANCED,
    PRESET_MODE_CODE_INTENSIVE,
    PRESET_MODE_EXTRACT_ONLY,
    PRESET_MODE_INTENSIVE,
    PRESET_MODE_SUPPLY_ONLY,
    PRESET_MODE_TO_RAW_CODE,
    RAW_CODE_TO_PRESET_MODE,
    RAW_VALUE_TO_SENSOR_MODE,
    SENSOR_MODE_TO_RAW_VALUE,
    profile_max_airflow,
)
from .modbus_helpers import MeltemModbusError, derive_balanced_airflow
from .models import RoomConfig, RoomState

# Zero in both intensive shadow registers ends a running override.
CLEAR_INTENSIVE: tuple[tuple[str, int], ...] = (("preset_mode", 0), ("preset_value", 0))


@dataclass(slots=True, frozen=True)
class ModeRead:
    """Decoded values from the read-only current-mode status map."""

    mode: int
    current_level: int
    extract_target_level: int


def scale_airflow_to_raw(room: RoomConfig, level: int) -> int:
    max_airflow = profile_max_airflow(room.profile)
    return max(0, min(200, round(level * 200 / max_airflow)))


def scale_raw_level_to_airflow(room: RoomConfig, raw_level: int) -> int:
    return round(raw_level * profile_max_airflow(room.profile) / 200)


def operating_mode_writes(
    room: RoomConfig,
    operation_mode: str,
    balanced_level: int,
    extract_level: int,
) -> tuple[tuple[str, int], ...]:
    """Build the mode-block register writes for ``operation_mode``."""

    balanced = ("current_level", scale_airflow_to_raw(room, balanced_level))
    if operation_mode == OPERATION_MODE_OFF:
        return (*CLEAR_INTENSIVE, ("mode", MODE_OFF), ("current_level", 0))
    if operation_mode == OPERATION_MODE_MANUAL:
        return (("mode", MODE_MANUAL), balanced)
    if operation_mode == OPERATION_MODE_UNBALANCED:
        return (
            ("mode", MODE_UNBALANCED),
            balanced,
            ("extract_target_level", scale_airflow_to_raw(room, extract_level)),
        )
    sensor_control_value = SENSOR_MODE_TO_RAW_VALUE.get(operation_mode)
    if sensor_control_value is None:
        raise MeltemModbusError(
            f"Unsupported operating mode {operation_mode!r} for room {room.key}"
        )
    return (("mode", MODE_SENSOR_CONTROL), ("current_level", sensor_control_value))


def preset_mode_writes(room: RoomConfig, preset_mode: str) -> tuple[tuple[str, int], ...]:
    """Build the mode-block register writes for ``preset_mode``."""

    raw_code = PRESET_MODE_TO_RAW_CODE.get(preset_mode)
    if raw_code is None:
        raise MeltemModbusError(f"Unsupported preset mode {preset_mode!r} for room {room.key}")
    if preset_mode == PRESET_MODE_INTENSIVE:
        return (("preset_mode", MODE_MANUAL), ("preset_value", raw_code))
    return (*CLEAR_INTENSIVE, ("mode", MODE_MANUAL), ("current_level", raw_code))


def apply_mode_read(room: RoomConfig, state: RoomState, block: ModeRead | None) -> RoomState:
    """Apply a decoded mode-status read (or its absence) to ``state``."""

    if block is not None and _mode_status_reports_intensive(block):
        # The intensive status hides the base mode, so the last known one stays.
        return replace(state, intensive_active=True)

    operation_mode = (
        _decode_operation_mode(block.mode, block.current_level)
        if block is not None
        else state.operation_mode
    )
    raw_current_level = block.current_level if block is not None else None
    extract_target_level: int | None = None

    if operation_mode == OPERATION_MODE_UNBALANCED:
        target_level = (
            _decode_target_level(room, raw_current_level)
            if raw_current_level is not None
            else state.target_level
        )
        extract_target_level = (
            _decode_target_level(room, block.extract_target_level)
            if block is not None
            else state.extract_target_level
        )
    elif operation_mode in (OPERATION_MODE_OFF, OPERATION_MODE_MANUAL):
        # Measured airflow can lag after writes, so prefer the current
        # target in the status map when it is plausible.
        target_level = _decode_target_level(room, raw_current_level)
        if target_level is None:
            target_level = derive_balanced_airflow(state.extract_air_flow, state.supply_air_flow)
    else:
        # Sensor control or an unknown mode carries no balanced target register.
        target_level = derive_balanced_airflow(state.extract_air_flow, state.supply_air_flow)

    return replace(
        state,
        operation_mode=operation_mode,
        target_level=target_level,
        balanced_target_readback=None,
        extract_target_level=extract_target_level,
        preset_mode=_decode_preset_mode(block, state.preset_mode),
        intensive_active=state.intensive_active if block is None else False,
    )


def _decode_target_level(room: RoomConfig, raw_level: int | None) -> int | None:
    """Decode a raw 0..200 target or app ``200 + airflow / 10`` preset value."""

    if raw_level is None:
        return None
    if 0 <= raw_level <= 200:
        return scale_raw_level_to_airflow(room, raw_level)
    airflow = (raw_level - APP_UNBALANCED_PRESET_BASE) * 10
    return airflow if airflow <= profile_max_airflow(room.profile) else None


_DIRECT_OPERATION_MODES: dict[int, str] = {
    MODE_OFF: OPERATION_MODE_OFF,
    MODE_MANUAL: OPERATION_MODE_MANUAL,
    MODE_UNBALANCED: OPERATION_MODE_UNBALANCED,
}


def _decode_operation_mode(mode_value: int, current_value: int) -> str | None:
    if mode_value == MODE_SENSOR_CONTROL:
        return RAW_VALUE_TO_SENSOR_MODE.get(current_value)
    return _DIRECT_OPERATION_MODES.get(mode_value)


def _decode_preset_mode(
    block: ModeRead | None,
    previous_preset_mode: str | None,
) -> str | None:
    """Return the base quick mode from a status read that does not report intensive."""

    if block is None:
        return previous_preset_mode
    if block.mode == MODE_UNBALANCED:
        if block.current_level == 0 and block.extract_target_level > APP_UNBALANCED_PRESET_BASE:
            return PRESET_MODE_EXTRACT_ONLY
        if block.extract_target_level == 0 and block.current_level > APP_UNBALANCED_PRESET_BASE:
            return PRESET_MODE_SUPPLY_ONLY
    if block.mode != MODE_MANUAL:
        return None
    return RAW_CODE_TO_PRESET_MODE.get(block.current_level)


def _mode_status_reports_intensive(block: ModeRead) -> bool:
    """Return whether the read-only mode map reports an intensive state.

    ``0/0`` was observed for an override started from the app; ``3/227`` is the
    community decode for intensive written to the base registers.
    """

    return (block.mode == MODE_MANUAL and block.current_level == PRESET_MODE_CODE_INTENSIVE) or (
        block.mode == 0 and block.current_level == 0
    )
