"""Optional values read through the gateway only while a consumer needs them."""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable, Coroutine
from typing import Any

from homeassistant.core import HomeAssistant

from .modbus_helpers import MeltemModbusError

_LOGGER = logging.getLogger(__name__)


class OptionalGatewayRead[T]:
    """One optional value, read only while at least one consumer needs it.

    Its read runs in the coordinator's poll tick under the gateway lock, and
    disabling the last consumer cancels a read that is still waiting.
    """

    def __init__(
        self,
        hass: HomeAssistant,
        name: str,
        read: Callable[[], Coroutine[Any, Any, T]],
        *,
        refresh_seconds: float,
        stale_seconds: float,
        first_read_delay: float = 0.0,
        on_value: Callable[[T], None] | None = None,
        clear_on_disable: bool = False,
    ) -> None:
        self._hass = hass
        self.name = name
        self._read = read
        self._refresh_seconds = refresh_seconds
        self._stale_seconds = stale_seconds
        self._first_read_delay = first_read_delay
        self._on_value = on_value
        self._clear_on_disable = clear_on_disable
        self._consumers: set[str] = set()
        self._next_due = 0.0
        self._value: T | None = None
        self._read_at: float | None = None
        self._task: asyncio.Task[T] | None = None
        self._disable_generation = 0

    @property
    def consumers(self) -> frozenset[str]:
        """Return the entities that currently need this value."""

        return frozenset(self._consumers)

    @property
    def enabled(self) -> bool:
        """Return whether the value is read at all."""

        return bool(self._consumers)

    @property
    def reading(self) -> bool:
        """Return whether a read is in flight."""

        return self._task is not None

    @property
    def value(self) -> T | None:
        """Return the last value only while its observation is recent."""

        if self._read_at is None or time.monotonic() - self._read_at > self._stale_seconds:
            return None
        return self._value

    def set_enabled(self, consumer: str, enabled: bool) -> bool:
        """Add or remove one consumer; return whether reading started or stopped."""

        was_enabled = self.enabled
        if enabled:
            self._consumers.add(consumer)
        else:
            self._consumers.discard(consumer)
        if was_enabled == self.enabled:
            return False
        if self.enabled:
            self._next_due = time.monotonic() + self._first_read_delay
        else:
            self._disable_generation += 1
            if self._clear_on_disable:
                self._value = None
                self._read_at = None
            if self._task is not None:
                self._task.cancel()
        return True

    def seconds_until_due(self) -> float | None:
        """Return how long until the next read, or ``None`` while disabled."""

        return self._next_due - time.monotonic() if self.enabled else None

    async def async_read_if_due(self) -> bool:
        """Read the value if it is due; the caller holds the gateway lock."""

        if not self.enabled or time.monotonic() < self._next_due:
            return False
        generation = self._disable_generation
        self._task = self._hass.async_create_task(self._read(), f"Meltem gateway {self.name}")
        try:
            self._value = value = await self._task
            self._read_at = time.monotonic()
            if self._on_value is not None:
                self._on_value(value)
        except asyncio.CancelledError:
            if self._disable_generation == generation:
                raise
        except MeltemModbusError as err:
            _LOGGER.debug("Optional gateway %s read failed: %s", self.name, err)
        finally:
            self._task = None
            delay = (
                self._first_read_delay
                if self.enabled and self._disable_generation != generation
                else self._refresh_seconds
            )
            self._next_due = time.monotonic() + delay
        return True
