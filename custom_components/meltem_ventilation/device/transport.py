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

    One policy is shared by all units on a gateway link, so a single silent
    unit does not count as a dead link while its neighbours keep answering.
    """

    def __init__(
        self,
        *,
        disconnect_after_timeouts: int,
        connection_retry_delay: float,
    ) -> None:
        self._disconnect_after_timeouts = disconnect_after_timeouts
        self._connection_retry_delay = connection_retry_delay
        self._consecutive_timeouts = 0
        self._last_read_answer: dict[int, float] = {}

    def seconds_since_read_answer(self, unit_id: int) -> float | None:
        """Return the age of the last successful register read for one unit."""

        answered_at = self._last_read_answer.get(unit_id)
        if answered_at is None:
            return None
        return time.monotonic() - answered_at

    async def run[T](
        self,
        unit: ModbusUnit,
        unit_id: int,
        operation: Callable[..., Awaitable[T]],
        *args: Any,
        is_read: bool,
    ) -> T:
        """Run one request with the gateway's retry policy."""

        for attempt in (1, 2):
            last_attempt = attempt == 2
            try:
                result = await operation(*args)
            except ClientClosedError:
                raise
            except ModbusConnectionError:
                if last_attempt:
                    raise
                await async_sleep(self._connection_retry_delay)
            except _GATEWAY_TARGET_ERRORS:
                self._consecutive_timeouts = 0
                if last_attempt:
                    raise
            except _NO_ANSWER_ERRORS:
                await self._count_timeout(unit)
                if last_attempt:
                    raise
            except ModbusExceptionError:
                self._consecutive_timeouts = 0
                raise
            else:
                self._consecutive_timeouts = 0
                if is_read:
                    self._last_read_answer[unit_id] = time.monotonic()
                return result
        raise AssertionError("unreachable")

    async def _count_timeout(self, unit: ModbusUnit) -> None:
        self._consecutive_timeouts += 1
        if self._consecutive_timeouts < self._disconnect_after_timeouts:
            return
        self._consecutive_timeouts = 0
        _LOGGER.debug(
            "Recycling the Meltem gateway link after %s timeouts without any answer",
            self._disconnect_after_timeouts,
        )
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
        return await self._policy.run(
            self._unit,
            self._unit_id,
            self._unit.read_holding_registers,
            address,
            count,
            is_read=True,
        )

    async def write_register(self, address: int, value: int) -> None:
        await self._policy.run(
            self._unit,
            self._unit_id,
            self._unit.write_register,
            address,
            value,
            is_read=False,
        )

    async def write_registers(self, address: int, values: list[int]) -> None:
        await self._policy.run(
            self._unit,
            self._unit_id,
            self._unit.write_registers,
            address,
            values,
            is_read=False,
        )
