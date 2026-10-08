"""Tests for the gateway retry policy shared by all units on one link."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
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


@pytest.fixture(name="clock")
def clock_fixture(
    monkeypatch: pytest.MonkeyPatch, sleeps: list[float]
) -> SimpleNamespace:
    clock = SimpleNamespace(now=100.0)
    monkeypatch.setattr(
        "custom_components.meltem_ventilation.device.transport.time",
        SimpleNamespace(monotonic=lambda: clock.now),
    )

    async def _sleep(seconds: float) -> None:
        sleeps.append(seconds)
        clock.now += seconds
        await asyncio.sleep(0)

    monkeypatch.setattr(
        "custom_components.meltem_ventilation.device.transport.async_sleep", _sleep
    )
    return clock


class TestReadRequestRate:
    @pytest.mark.parametrize("rate", [0.5, 3.0, 10.0])
    async def test_reads_share_one_limit_across_slaves(
        self, policy: TransportPolicy, unit: AsyncMock, clock: SimpleNamespace, rate: float
    ) -> None:
        policy.update_request_rate(rate)
        starts: list[float] = []

        async def _read(*args: object) -> list[int]:
            starts.append(clock.now)
            return [30, 30]

        for slave in (2, 3, 2):
            await policy.run(unit, slave, _read, 41020, 2, is_read=True)

        assert starts == pytest.approx([100.0, 100.0 + 1 / rate, 100.0 + 2 / rate])

    @pytest.mark.parametrize(
        "error",
        [_SILENT, ModbusProtocolError("garbled"), GatewayTargetError(),
         ModbusConnectionError("gone")],
    )
    async def test_retries_also_take_a_read_slot(
        self, policy: TransportPolicy, unit: AsyncMock, clock: SimpleNamespace,
        error: Exception,
    ) -> None:
        policy.update_request_rate(0.5)
        starts: list[float] = []

        async def _read(*args: object) -> list[int]:
            starts.append(clock.now)
            if len(starts) == 1:
                raise error
            return [1]

        assert await policy.run(unit, 2, _read, 41020, 1, is_read=True) == [1]
        assert starts == pytest.approx([100.0, 102.0])

    async def test_slow_connect_does_not_allow_a_wire_burst(
        self, policy: TransportPolicy, unit: AsyncMock, clock: SimpleNamespace
    ) -> None:
        policy.update_request_rate(2.0)
        completions: list[float] = []

        async def _read(*args: object) -> list[int]:
            clock.now += 1.0
            completions.append(clock.now)
            return [1]

        await policy.run(unit, 2, _read, 41020, 1, is_read=True)
        await policy.run(unit, 3, _read, 41020, 1, is_read=True)

        assert completions == pytest.approx([101.0, 102.5])

    async def test_concurrent_reads_do_not_claim_the_same_slot(
        self, policy: TransportPolicy, unit: AsyncMock, clock: SimpleNamespace
    ) -> None:
        policy.update_request_rate(2.0)
        starts: list[float] = []

        async def _read(*args: object) -> list[int]:
            starts.append(clock.now)
            await asyncio.sleep(0)
            return [1]

        await asyncio.gather(
            *(policy.run(unit, slave, _read, 41020, 1, is_read=True) for slave in (2, 3, 4))
        )

        assert starts == pytest.approx([100.0, 100.5, 101.0])

    async def test_writes_do_not_wait_for_read_slots(
        self, policy: TransportPolicy, unit: AsyncMock, clock: SimpleNamespace,
        sleeps: list[float],
    ) -> None:
        policy.update_request_rate(0.5)
        await _run(policy, unit, AsyncMock(return_value=[1]))
        for register in (41120, 41121, 41132):
            await policy.run(unit, 2, AsyncMock(), register, 0, is_read=False)
        assert sleeps == []

        await _run(policy, unit, AsyncMock(return_value=[1]))

        assert sleeps == [2.0]

    async def test_rate_change_is_applied_to_an_already_waiting_read(
        self, policy: TransportPolicy, unit: AsyncMock, clock: SimpleNamespace,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        policy.update_request_rate(2.0)
        await _run(policy, unit, AsyncMock(return_value=[1]))
        waits: list[float] = []

        async def _sleep(seconds: float) -> None:
            waits.append(seconds)
            clock.now += seconds
            policy.update_request_rate(0.5)

        monkeypatch.setattr(
            "custom_components.meltem_ventilation.device.transport.async_sleep", _sleep
        )

        await _run(policy, unit, AsyncMock(return_value=[1]))

        assert waits == [0.5, 1.5]
        assert clock.now == 102.0

    @pytest.mark.parametrize("shutdown", [False, True], ids=["cancel", "shutdown"])
    async def test_waiting_read_does_not_run_after_cancellation_or_shutdown(
        self, policy: TransportPolicy, unit: AsyncMock, clock: SimpleNamespace,
        monkeypatch: pytest.MonkeyPatch, shutdown: bool,
    ) -> None:
        policy.update_request_rate(2.0)
        await _run(policy, unit, AsyncMock(return_value=[1]))
        waiting = asyncio.Event()
        resume = asyncio.Event()

        async def _sleep(seconds: float) -> None:
            waiting.set()
            await resume.wait()
            clock.now += seconds

        monkeypatch.setattr(
            "custom_components.meltem_ventilation.device.transport.async_sleep", _sleep
        )
        operation = AsyncMock(return_value=[1])
        task = asyncio.create_task(_run(policy, unit, operation))
        await waiting.wait()
        if shutdown:
            policy.shutdown()
            resume.set()
            with pytest.raises(ClientClosedError):
                await task
        else:
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            clock.now += 1.0
            assert await _run(policy, unit, AsyncMock(return_value=[1])) == [1]
        operation.assert_not_awaited()

    @pytest.mark.parametrize("rate", [0.0, -1.0, float("nan"), float("inf")])
    def test_invalid_rate_is_rejected(self, policy: TransportPolicy, rate: float) -> None:
        with pytest.raises(ValueError, match="positive and finite"):
            policy.update_request_rate(rate)


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
