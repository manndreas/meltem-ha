"""Optimistic display of a pending write until the gateway confirms it."""

from __future__ import annotations

import operator
import time
from collections.abc import Callable
from datetime import datetime
from functools import partial

from homeassistant.core import CALLBACK_TYPE, HassJob, HassJobType, HomeAssistant, callback
from homeassistant.helpers.event import async_call_later
from homeassistant.util import dt as dt_util

from .models import EMPTY_ROOM_STATE, RoomState
from .read_health import group_fresh


class OptimisticOverlay[T, C]:
    """Pending write shown until the gateway confirms it or the window expires.

    Writes settle slowly, so without this the UI would jump back to the old
    value for a few seconds after every user action. ``C`` is the type of the
    confirmed value a readback reports.
    """

    def __init__(
        self,
        hass: HomeAssistant,
        ttl_seconds: float,
        on_change: Callable[[], None],
        matches: Callable[[C, T], bool] = operator.eq,
        *,
        read_groups: tuple[str, ...] = (),
        state_fn: Callable[[str], RoomState | None] | None = None,
    ) -> None:
        self._hass = hass
        self._ttl_seconds = ttl_seconds
        self._on_change = on_change
        self._matches = matches
        self._read_groups = read_groups
        self._state_fn = state_fn
        self._pending: dict[str, tuple[T, float]] = {}
        self._started_at: dict[str, datetime] = {}
        self._baseline: dict[str, tuple[object, ...]] = {}
        self._cancel_expiry: dict[str, CALLBACK_TYPE] = {}

    def set(self, room_key: str, value: T) -> None:
        self._stop_expiry(room_key)
        self._pending[room_key] = (value, time.monotonic() + self._ttl_seconds)
        self._started_at[room_key] = dt_util.utcnow()
        if self._state_fn is not None and self._read_groups:
            state = self._state_fn(room_key) or EMPTY_ROOM_STATE
            self._baseline[room_key] = tuple(
                state.read_health_for(group_key) for group_key in self._read_groups
            )
        # The UI has to drop the pending value even if no poll follows.
        self._cancel_expiry[room_key] = async_call_later(
            self._hass,
            self._ttl_seconds,
            HassJob(
                partial(self._expire, room_key),
                job_type=HassJobType.Callback,
                cancel_on_shutdown=True,
            ),
        )
        self._on_change()

    def clear(self, room_key: str) -> None:
        self._stop_expiry(room_key)
        self._started_at.pop(room_key, None)
        self._baseline.pop(room_key, None)
        if self._pending.pop(room_key, None) is not None:
            self._on_change()

    def read_since_set(self, room_key: str, state: RoomState | None) -> bool:
        """Return whether a fresh read of ``read_groups`` arrived after ``set``."""

        if state is None or not self._read_groups:
            return False
        baseline = self._baseline.get(room_key)
        current = tuple(state.read_health_for(group_key) for group_key in self._read_groups)
        if baseline is not None and current == baseline:
            return False
        started_at = self._started_at.get(room_key)
        return any(
            group_fresh(state, group_key)
            and (
                started_at is None
                or (
                    (last_read := state.read_health_for(group_key).last_successful_read) is not None
                    and last_read >= started_at
                )
            )
            for group_key in self._read_groups
        )

    def active(self, room_key: str, state: RoomState | None, confirmed: C | None) -> T | None:
        """Return the pending value, or ``None`` once a post-write read confirms it."""

        return self.get(
            room_key,
            confirmed if self.read_since_set(room_key, state) else None,
        )

    def settle_if_read(self, room_key: str, state: RoomState | None, confirmed: C | None) -> bool:
        """Drop the pending value when a fresh post-write read confirms it."""

        return self.read_since_set(room_key, state) and self.settle(room_key, confirmed)

    def settle(self, room_key: str, confirmed: C | None) -> bool:
        """Drop the pending value once a readback confirms it; return whether it was dropped."""

        pending = self._pending.get(room_key)
        if pending is None or confirmed is None or not self._matches(confirmed, pending[0]):
            return False
        self._stop_expiry(room_key)
        del self._pending[room_key]
        self._started_at.pop(room_key, None)
        self._baseline.pop(room_key, None)
        return True

    def get(self, room_key: str, confirmed: C | None) -> T | None:
        """Return the pending value, or ``None`` once it is confirmed or stale."""

        pending = self._pending.get(room_key)
        if pending is None:
            return None
        value, expires_at = pending
        if time.monotonic() >= expires_at or (
            confirmed is not None and self._matches(confirmed, value)
        ):
            return None
        return value

    def shutdown(self) -> None:
        for cancel in self._cancel_expiry.values():
            cancel()
        self._cancel_expiry.clear()
        self._pending.clear()
        self._started_at.clear()
        self._baseline.clear()

    def _stop_expiry(self, room_key: str) -> None:
        if (cancel := self._cancel_expiry.pop(room_key, None)) is not None:
            cancel()

    @callback
    def _expire(self, room_key: str, _now: datetime) -> None:
        self._cancel_expiry.pop(room_key, None)
        self._started_at.pop(room_key, None)
        self._baseline.pop(room_key, None)
        if self._pending.pop(room_key, None) is not None:
            self._on_change()
