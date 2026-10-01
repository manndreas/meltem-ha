"""Tests for the gateway retry policy shared by all units on one link."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from modbus_connection import (
    ClientClosedError,
    GatewayTargetError,
    IllegalDataAddressError,
    ModbusConnectionError,
    ModbusProtocolError,
    ModbusTimeoutError,
    ServerDeviceBusyError,
)
from modbus_connection.mock import MockModbusConnection

from custom_components.meltem_ventilation.device import PolicyUnit, TransportPolicy

_RETRY_DELAY = 0.5
_SILENT = ModbusTimeoutError("silent")


@pytest.fixture(name="sleeps", autouse=True)
def sleeps_fixture(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    sleeps: list[float] = []

    async def _sleep(seconds: float) -> None:
        sleeps.append(seconds)

    monkeypatch.setattr(
        "custom_components.meltem_ventilation.device.transport.async_sleep", _sleep
    )
    return sleeps


def _policy(link_quiet_seconds: float) -> TransportPolicy:
    return TransportPolicy(
        disconnect_after_timeouts=3,
        link_quiet_seconds=link_quiet_seconds,
        connection_retry_delay=_RETRY_DELAY,
    )


@pytest.fixture(name="policy")
def policy_fixture() -> TransportPolicy:
    # No quiet window, so the timeout count alone decides.
    return _policy(link_quiet_seconds=0)


@pytest.fixture(name="quiet_policy")
def quiet_policy_fixture() -> TransportPolicy:
    return _policy(link_quiet_seconds=10)


@pytest.fixture(name="unit")
def unit_fixture() -> AsyncMock:
    return AsyncMock()


async def _run(policy: TransportPolicy, unit: AsyncMock, operation: AsyncMock) -> object:
    return await policy.run(unit, 2, operation, 41020, 2, is_read=True)


async def _run_failing(
    policy: TransportPolicy,
    unit: AsyncMock,
    operation: AsyncMock,
    *,
    times: int = 1,
    expected: type[Exception] | tuple[type[Exception], ...] = ModbusTimeoutError,
) -> None:
    for _ in range(times):
        with pytest.raises(expected):
            await _run(policy, unit, operation)


class TestRetry:
    @pytest.mark.parametrize(
        "error",
        [_SILENT, ModbusProtocolError("garbled"), GatewayTargetError()],
        ids=["timeout", "protocol", "silent-unit-behind-the-gateway"],
    )
    async def test_an_unanswered_request_is_retried_once(
        self, policy: TransportPolicy, unit: AsyncMock, error: Exception
    ) -> None:
        operation = AsyncMock(side_effect=[error, [30, 30]])

        assert await _run(policy, unit, operation) == [30, 30]
        assert operation.await_count == 2

    async def test_a_second_timeout_is_raised(
        self, policy: TransportPolicy, unit: AsyncMock
    ) -> None:
        operation = AsyncMock(side_effect=_SILENT)

        await _run_failing(policy, unit, operation)

        assert operation.await_count == 2

    async def test_a_lost_link_is_retried_after_a_pause(
        self, policy: TransportPolicy, unit: AsyncMock, sleeps: list[float]
    ) -> None:
        operation = AsyncMock(side_effect=[ModbusConnectionError("gone"), [1]])

        assert await _run(policy, unit, operation) == [1]
        assert sleeps == [_RETRY_DELAY]

    async def test_a_closed_link_is_not_retried(
        self, policy: TransportPolicy, unit: AsyncMock, sleeps: list[float]
    ) -> None:
        operation = AsyncMock(side_effect=ClientClosedError("closed"))

        await _run_failing(policy, unit, operation, expected=ClientClosedError)

        assert operation.await_count == 1
        assert sleeps == []

    @pytest.mark.parametrize(
        "error",
        [IllegalDataAddressError(), ServerDeviceBusyError()],
        ids=["illegal-address", "busy"],
    )
    async def test_an_exception_response_is_not_retried(
        self, policy: TransportPolicy, unit: AsyncMock, error: Exception
    ) -> None:
        operation = AsyncMock(side_effect=error)

        await _run_failing(policy, unit, operation, expected=type(error))

        assert operation.await_count == 1


class TestLinkRecycling:
    async def test_consecutive_timeouts_drop_the_link(
        self, policy: TransportPolicy, unit: AsyncMock
    ) -> None:
        operation = AsyncMock(side_effect=_SILENT)

        await _run_failing(policy, unit, operation)
        unit.disconnect.assert_not_awaited()

        await _run_failing(policy, unit, operation)
        unit.disconnect.assert_awaited_once()

    async def test_an_answer_resets_the_timeout_count(
        self, policy: TransportPolicy, unit: AsyncMock
    ) -> None:
        operation = AsyncMock(side_effect=[_SILENT, [1]] * 3)

        for _ in range(3):
            await _run(policy, unit, operation)

        unit.disconnect.assert_not_awaited()

    async def test_an_answering_gateway_does_not_count_as_a_dead_link(
        self, policy: TransportPolicy, unit: AsyncMock
    ) -> None:
        operation = AsyncMock(side_effect=GatewayTargetError())

        await _run_failing(policy, unit, operation, times=3, expected=GatewayTargetError)

        unit.disconnect.assert_not_awaited()

    async def test_a_failing_disconnect_is_not_raised(
        self, policy: TransportPolicy, unit: AsyncMock
    ) -> None:
        unit.disconnect.side_effect = ModbusConnectionError("already gone")
        operation = AsyncMock(side_effect=[_SILENT] * 3 + [[1]])

        await _run_failing(policy, unit, operation)

        assert await _run(policy, unit, operation) == [1]


class TestQuietWindow:
    async def test_a_recent_answer_keeps_the_link_up(
        self, quiet_policy: TransportPolicy, unit: AsyncMock
    ) -> None:
        """Other units answered a moment ago, so the silent one is the problem."""
        await _run(quiet_policy, unit, AsyncMock(return_value=[1]))

        await _run_failing(quiet_policy, unit, AsyncMock(side_effect=_SILENT), times=3)

        unit.disconnect.assert_not_awaited()

    async def test_a_fresh_link_is_not_recycled_at_once(
        self, quiet_policy: TransportPolicy, unit: AsyncMock
    ) -> None:
        await _run_failing(quiet_policy, unit, AsyncMock(side_effect=_SILENT), times=2)

        unit.disconnect.assert_not_awaited()

    async def test_a_link_quiet_for_the_whole_window_is_recycled_once(
        self, quiet_policy: TransportPolicy, unit: AsyncMock
    ) -> None:
        quiet_policy._quiet_since -= 11

        await _run_failing(quiet_policy, unit, AsyncMock(side_effect=_SILENT), times=3)

        # Recycling starts a new quiet window, so the next timeouts wait for it.
        unit.disconnect.assert_awaited_once()
        assert quiet_policy.diagnostics()["link_recycles"] == 1

    async def test_an_exception_response_proves_the_link_is_alive(
        self, quiet_policy: TransportPolicy, unit: AsyncMock
    ) -> None:
        quiet_policy._quiet_since -= 11
        operation = AsyncMock(
            side_effect=[_SILENT] * 2 + [IllegalDataAddressError()] + [_SILENT] * 4
        )

        await _run_failing(
            quiet_policy,
            unit,
            operation,
            times=4,
            expected=(ModbusTimeoutError, IllegalDataAddressError),
        )

        unit.disconnect.assert_not_awaited()


class TestReadAnswerAge:
    async def test_only_answered_reads_count(
        self, policy: TransportPolicy, unit: AsyncMock
    ) -> None:
        assert policy.seconds_since_read_answer(2) is None
        assert policy.diagnostics()["seconds_since_any_answer"] is None

        await policy.run(unit, 2, AsyncMock(), 41120, 1, is_read=False)
        assert policy.seconds_since_read_answer(2) is None
        assert policy.diagnostics()["seconds_since_any_answer"] is not None

        await _run(policy, unit, AsyncMock(return_value=[1, 2]))
        age = policy.seconds_since_read_answer(2)
        assert age is not None
        assert age >= 0
        assert policy.seconds_since_read_answer(3) is None


class TestPolicyUnit:
    async def test_register_requests_go_through_the_policy(
        self, policy: TransportPolicy
    ) -> None:
        link = MockModbusConnection()
        mock_unit = link.for_unit(2)
        mock_unit.holding[41020] = [30, 31]
        unit = PolicyUnit(mock_unit, 2, policy)

        assert await unit.read_holding_registers(41020, 2) == [30, 31]
        await unit.write_register(41120, 3)
        await unit.write_registers(42000, [50, 10])

        assert mock_unit.holding[41120] == 3
        assert mock_unit.holding[42001] == 10
        assert policy.seconds_since_read_answer(2) is not None

    async def test_everything_else_is_passed_through(
        self, policy: TransportPolicy
    ) -> None:
        mock_unit = MockModbusConnection().for_unit(4)
        unit = PolicyUnit(mock_unit, 4, policy)

        unit.set_message_spacing(0.1)

        assert unit.unit_id == 4
        assert mock_unit.message_spacing == 0.1
        assert unit.connected is False
