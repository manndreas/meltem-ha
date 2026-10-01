"""Retry policy for every Meltem unit reached through one gateway link."""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from typing import Any

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

_LOGGER = logging.getLogger(__name__)

async_sleep = asyncio.sleep

# The gateway answered for a unit behind it that did not.
_GATEWAY_TARGET_ERRORS = (GatewayTargetError, GatewayPathUnavailableError)
_NO_ANSWER_ERRORS = (ModbusTimeoutError, ModbusProtocolError)


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
    ) -> None:
        self._disconnect_after_timeouts = disconnect_after_timeouts
        self._link_quiet_seconds = link_quiet_seconds
        self._connection_retry_delay = connection_retry_delay
        self._consecutive_timeouts = 0
        # Creating the policy and recycling the link both start a quiet window.
        self._quiet_since = time.monotonic()
        self._last_answer_at: float | None = None
        self._last_read_answer: dict[int, float] = {}
        self._link_recycles = 0

    def seconds_since_read_answer(self, unit_id: int) -> float | None:
        """Return the age of the last successful register read for one unit."""

        answered_at = self._last_read_answer.get(unit_id)
        if answered_at is None:
            return None
        return time.monotonic() - answered_at

    def diagnostics(self) -> dict[str, float | int | None]:
        """Return the link health counters."""

        return {
            "consecutive_timeouts": self._consecutive_timeouts,
            "link_recycles": self._link_recycles,
            "seconds_since_any_answer": (
                None
                if self._last_answer_at is None
                else round(time.monotonic() - self._last_answer_at, 1)
            ),
        }

    async def run[T](
        self,
        unit: ModbusUnit,
        unit_id: int,
        operation: Callable[..., Awaitable[T]],
        *args: Any,
        is_read: bool,
    ) -> T:
        """Run one request with the gateway's retry policy."""

        for last_attempt in (False, True):
            try:
                result = await operation(*args)
            except ClientClosedError:
                raise
            except ModbusConnectionError:
                if last_attempt:
                    raise
                await async_sleep(self._connection_retry_delay)
            except _GATEWAY_TARGET_ERRORS:
                self._link_answered()
                if last_attempt:
                    raise
            except _NO_ANSWER_ERRORS:
                await self._count_timeout(unit)
                if last_attempt:
                    raise
            except ModbusExceptionError:
                self._link_answered()
                raise
            else:
                self._link_answered()
                if is_read:
                    self._last_read_answer[unit_id] = time.monotonic()
                return result
        raise AssertionError("unreachable")

    def _link_answered(self) -> None:
        self._consecutive_timeouts = 0
        self._quiet_since = self._last_answer_at = time.monotonic()

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

    async def _run[T](
        self, operation: Callable[..., Awaitable[T]], *args: Any, is_read: bool
    ) -> T:
        return await self._policy.run(
            self._unit, self._unit_id, operation, *args, is_read=is_read
        )
