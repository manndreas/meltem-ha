"""Coordinator bookkeeping of one unit between its reads and writes."""

from __future__ import annotations

from dataclasses import dataclass

from .const import LevelWriteFallback
from .models import ReadHealth, RoomConfig, RoomReadError


@dataclass(slots=True)
class RoomRuntime:
    """Per-unit state the coordinator keeps besides the polled ``RoomState``.

    Pending display values live in ``OptimisticOverlay`` and write outcomes in
    ``WriteConfirmations``; everything else the coordinator tracks for a unit is
    here, instead of in parallel dicts keyed by the room key.
    """

    room: RoomConfig
    # Consecutive read jobs without a usable answer from the unit.
    read_failures: int = 0
    # The most recent failed read job, kept after the unit recovers.
    last_read_error: RoomReadError | None = None
    # Whether the unit's change to unavailable has been logged.
    unavailable_logged: bool = False
    # Whether the set-side target read failed last time, so a streak logs once.
    target_read_failing: bool = False
    # Mode-group health when a flow write was interrupted; a later read clears it.
    uncertain_flow: ReadHealth | None = None
    # Fallback of the last fan write and the mode-group health it was made at.
    level_fallback: tuple[LevelWriteFallback, ReadHealth] | None = None
