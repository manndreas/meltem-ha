"""Optimistic display of a pending write until the gateway confirms it."""

from __future__ import annotations

import operator
import time
from collections.abc import Callable
from datetime import datetime
from functools import partial

from homeassistant.core import CALLBACK_TYPE, HassJob, HassJobType, HomeAssistant, callback
from homeassistant.helpers.event import async_call_later


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
    ) -> None:
        self._hass = hass
        self._ttl_seconds = ttl_seconds
        self._on_change = on_change
        self._matches = matches
        self._pending: dict[str, tuple[T, float]] = {}
        self._cancel_expiry: dict[str, CALLBACK_TYPE] = {}

    def set(self, room_key: str, value: T) -> None:
        self._stop_expiry(room_key)
        self._pending[room_key] = (value, time.monotonic() + self._ttl_seconds)
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
        if self._pending.pop(room_key, None) is not None:
            self._on_change()

    def settle(self, room_key: str, confirmed: C | None) -> bool:
        """Drop the pending value once a readback confirms it; return whether it was dropped."""

        pending = self._pending.get(room_key)
        if pending is None or confirmed is None or not self._matches(confirmed, pending[0]):
            return False
        self._stop_expiry(room_key)
        del self._pending[room_key]
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

    def _stop_expiry(self, room_key: str) -> None:
        if (cancel := self._cancel_expiry.pop(room_key, None)) is not None:
            cancel()

    @callback
    def _expire(self, room_key: str, _now: datetime) -> None:
        self._cancel_expiry.pop(room_key, None)
        if self._pending.pop(room_key, None) is not None:
            self._on_change()
