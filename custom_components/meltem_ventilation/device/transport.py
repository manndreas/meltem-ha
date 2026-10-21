"""Retry policy for every Meltem unit reached through one gateway link."""

from __future__ import annotations

import asyncio
import logging
import math
import time
from collections import deque
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any, Literal

from modbus_connection import (
    ClientClosedError,
    GatewayPathUnavailableError,
    GatewayTargetError,
    ModbusConnectionError,
    ModbusExceptionError,
    ModbusProtocolError,
    ModbusTimeoutError,
    ModbusUnit,
)

from ..const import REGISTER_RF_COMM_STATUS

_LOGGER = logging.getLogger(__name__)

async_sleep = asyncio.sleep

# The gateway answered for a unit behind it that did not.
_GATEWAY_TARGET_ERRORS = (GatewayTargetError, GatewayPathUnavailableError)
_NO_ANSWER_ERRORS = (ModbusTimeoutError, ModbusProtocolError)
_RETRYABLE_ERRORS = (*_GATEWAY_TARGET_ERRORS, *_NO_ANSWER_ERRORS)
COMMUNICATION_WINDOW_SECONDS = 300.0
RequestOutcome = Literal["success", "no_answer", "modbus_exception", "other"]


class TransportPolicy:
    """Retry once, and recycle the link only when nothing answers at all.

    One policy is shared by all units on a gateway link. The link is recycled
    after a run of timeouts only if no request got any answer for a while, so
    silent units do not count as a dead link while their neighbours answer.
    """

    def __init__(
        self,
        *,
        disconnect_after_timeouts: int,
        link_quiet_seconds: float,
        connection_retry_delay: float,
        on_request: Callable[[RequestOutcome], None] | None = None,
        on_link_recycle: Callable[[], None] | None = None,
    ) -> None:
        self._disconnect_after_timeouts = disconnect_after_timeouts
        self._link_quiet_seconds = link_quiet_seconds
        self._connection_retry_delay = connection_retry_delay
        self._on_request = on_request
        self._on_link_recycle = on_link_recycle
        self._consecutive_timeouts = 0
        # Creating the policy and recycling the link both start a quiet window.
        self._quiet_since = time.monotonic()
        self._last_answer_at: float | None = None
        self._last_answer_timestamp: float | None = None
        self._last_read_answer: dict[int, float] = {}
        self._link_recycles = 0
        self._request_lock = asyncio.Lock()
        self._read_interval = 0.0
        self._last_read_finished: float | None = None
        self._closed = False
        self._request_samples: deque[tuple[float, float | None]] = deque(maxlen=10000)

    def update_request_rate(self, max_requests_per_second: float) -> None:
        """Limit read attempts across every unit sharing this policy."""

        if not math.isfinite(max_requests_per_second) or max_requests_per_second <= 0:
            raise ValueError("The maximum read request rate must be positive and finite")
        self._read_interval = 1.0 / max_requests_per_second

    def shutdown(self) -> None:
        """Reject requests still waiting for a read slot."""

        self._closed = True

    async def _wait_for_read_slot(self) -> None:
        while self._last_read_finished is not None and not self._closed:
            remaining = self._last_read_finished + self._read_interval - time.monotonic()
            if remaining <= 0:
                return
            await async_sleep(remaining)

    def seconds_since_read_answer(self, unit_id: int) -> float | None:
        """Return the age of the last successful register read for one unit."""

        answered_at = self._last_read_answer.get(unit_id)
        if answered_at is None:
            return None
        return time.monotonic() - answered_at

    def diagnostics(self) -> dict[str, float | int | None]:
        """Return the link health counters."""

        self._prune_samples(time.monotonic())
        durations = [duration for _, duration in self._request_samples if duration is not None]
        return {
            "consecutive_timeouts": self._consecutive_timeouts,
            "link_recycles": self._link_recycles,
            "last_answer_timestamp": self._last_answer_timestamp,
            "seconds_since_any_answer": (
                None
                if self._last_answer_at is None
                else round(time.monotonic() - self._last_answer_at, 1)
            ),
            "request_sample_count": len(self._request_samples),
            "request_window_seconds": COMMUNICATION_WINDOW_SECONDS,
            "request_success_rate": (
                100.0 * len(durations) / len(self._request_samples)
                if self._request_samples
                else None
            ),
            "mean_response_time_ms": sum(durations) / len(durations) if durations else None,
        }

    def _prune_samples(self, now: float) -> None:
        while (
            self._request_samples
            and self._request_samples[0][0] <= now - COMMUNICATION_WINDOW_SECONDS
        ):
            self._request_samples.popleft()

    def _record_request(self, started_at: float, *, success: bool) -> None:
        finished_at = time.monotonic()
        self._prune_samples(finished_at)
        self._request_samples.append(
            (finished_at, (finished_at - started_at) * 1000.0 if success else None)
        )

    @staticmethod
    def _request_outcome(error: Exception) -> RequestOutcome:
        if isinstance(error, ModbusExceptionError):
            return "modbus_exception"
        if isinstance(error, (*_NO_ANSWER_ERRORS, ModbusConnectionError)):
            return "no_answer"
        return "other"

    def _notify_request(self, outcome: RequestOutcome) -> None:
        if self._on_request is None:
            return
        try:
            self._on_request(outcome)
        except Exception:
            _LOGGER.exception("Failed to record cumulative Modbus request diagnostics")

    def _notify_link_recycle(self) -> None:
        if self._on_link_recycle is None:
            return
        try:
            self._on_link_recycle()
        except Exception:
            _LOGGER.exception("Failed to record cumulative Modbus link diagnostics")

    async def run[T](
        self,
        unit: ModbusUnit,
        unit_id: int,
        operation: Callable[..., Awaitable[T]],
        *args: Any,
        is_read: bool,
    ) -> T:
        """Run one request, retrying once on a lost link, timeout, or silent target."""

        try:
            return await self._attempt(unit, unit_id, operation, *args, is_read=is_read)
        except ClientClosedError:
            raise
        except ModbusConnectionError:
            await async_sleep(self._connection_retry_delay)
        except _RETRYABLE_ERRORS:
            pass
        return await self._attempt(unit, unit_id, operation, *args, is_read=is_read)

    async def _attempt[T](
        self,
        unit: ModbusUnit,
        unit_id: int,
        operation: Callable[..., Awaitable[T]],
        *args: Any,
        is_read: bool,
    ) -> T:
        """Run one serialized request and update link health."""

        try:
            async with self._request_lock:
                if is_read:
                    await self._wait_for_read_slot()
                if self._closed:
                    raise ClientClosedError("The Meltem gateway client was shut down")
                started_at = time.monotonic()
                try:
                    result = await operation(*args)
                except (asyncio.CancelledError, ClientClosedError):
                    raise
                except Exception as err:
                    self._record_request(started_at, success=False)
                    self._notify_request(self._request_outcome(err))
                    raise
                else:
                    self._record_request(started_at, success=True)
                    self._notify_request("success")
                finally:
                    if is_read:
                        # Completion-based spacing also covers a slow connect
                        # or time spent waiting inside the underlying link.
                        self._last_read_finished = time.monotonic()
        except ModbusExceptionError:
            self._link_answered()
            raise
        except _NO_ANSWER_ERRORS:
            await self._count_timeout(unit)
            raise

        self._link_answered()
        if is_read and (not args or args[0] != REGISTER_RF_COMM_STATUS):
            self._last_read_answer[unit_id] = time.monotonic()
        return result

    def _link_answered(self) -> None:
        self._consecutive_timeouts = 0
        self._quiet_since = self._last_answer_at = time.monotonic()
        self._last_answer_timestamp = datetime.now(UTC).timestamp()

    async def _count_timeout(self, unit: ModbusUnit) -> None:
        self._consecutive_timeouts += 1
        if self._consecutive_timeouts < self._disconnect_after_timeouts:
            return
        quiet_for = time.monotonic() - self._quiet_since
        if quiet_for < self._link_quiet_seconds:
            return
        _LOGGER.info(
            "Recycling the Meltem gateway link after %s timeouts and %.0f s without any answer",
            self._consecutive_timeouts,
            quiet_for,
        )
        self._consecutive_timeouts = 0
        self._quiet_since = time.monotonic()
        self._link_recycles += 1
        self._notify_link_recycle()
        try:
            await unit.disconnect()
        except ModbusConnectionError as err:
            _LOGGER.debug("Dropping the Meltem gateway link failed: %s", err)


class PolicyUnit:
    """A ``ModbusUnit`` whose register requests follow a ``TransportPolicy``."""

    def __init__(self, unit: ModbusUnit, unit_id: int, policy: TransportPolicy) -> None:
        self._unit = unit
        self._unit_id = unit_id
        self._policy = policy

    def __getattr__(self, name: str) -> Any:
        return getattr(self._unit, name)

    @property
    def unit_id(self) -> int:
        """The Modbus unit ID this handle talks to."""

        return self._unit_id

    @property
    def connected(self) -> bool:
        return self._unit.connected

    async def read_holding_registers(self, address: int, count: int) -> list[int]:
        return await self._run(self._unit.read_holding_registers, address, count, is_read=True)

    async def write_register(self, address: int, value: int) -> None:
        await self._run(self._unit.write_register, address, value, is_read=False)

    async def write_registers(self, address: int, values: list[int]) -> None:
        await self._run(self._unit.write_registers, address, values, is_read=False)

    async def _run[T](self, operation: Callable[..., Awaitable[T]], *args: Any, is_read: bool) -> T:
        return await self._policy.run(self._unit, self._unit_id, operation, *args, is_read=is_read)
