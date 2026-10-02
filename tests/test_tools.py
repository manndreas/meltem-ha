"""Tests for the gateway tools that do not need a live gateway."""

from __future__ import annotations

import asyncio
import sys
from argparse import Namespace
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest
from modbus_connection import IllegalDataAddressError, IllegalDataValueError, ModbusTimeoutError
from modbus_connection.mock import MockModbusConnection, WriteEvent

from custom_components.meltem_ventilation.const import (
    DEFAULT_GATEWAY_DEVICE_ID,
    DEFAULT_PORT,
    FIXED_TIMEOUT,
    REGISTER_APPLY,
    REGISTER_CURRENT_LEVEL,
    REGISTER_GATEWAY_NODE_ADDRESS_1,
    REGISTER_GATEWAY_NUMBER_OF_NODES,
    REGISTER_MODE,
    REQUEST_GAP_SECONDS,
)
from custom_components.meltem_ventilation.modbus_helpers import (
    build_serial_params,
    supported_entity_keys_for_profile,
)
from custom_components.meltem_ventilation.models import ReadHealth, RoomConfig, RoomState
from tools import _link, write_registers
from tools import benchmark_integration_like as bil

_MANUAL_80_SEQUENCE = ((REGISTER_MODE, 3), (REGISTER_CURRENT_LEVEL, 80), (REGISTER_APPLY, 0))


def test_link_settings_match_the_integration() -> None:
    """The tools keep their own copy, so it must not drift from the integration."""

    assert _link.serial_params("/dev/ttyX") == build_serial_params("/dev/ttyX")
    assert _link.TIMEOUT == FIXED_TIMEOUT
    assert _link.REQUEST_GAP_SECONDS == REQUEST_GAP_SECONDS
    assert _link.DEFAULT_PORT == DEFAULT_PORT
    assert _link.GATEWAY_DEVICE_ID == DEFAULT_GATEWAY_DEVICE_ID
    assert _link.REGISTER_GATEWAY_NUMBER_OF_NODES == REGISTER_GATEWAY_NUMBER_OF_NODES
    assert _link.REGISTER_GATEWAY_NODE_ADDRESS_1 == REGISTER_GATEWAY_NODE_ADDRESS_1


class TestIntegrationLikeBenchmark:
    @pytest.fixture(name="link")
    def link_fixture(self) -> MockModbusConnection:
        link = MockModbusConnection()
        link.for_unit(1).holding.update({43901: 2, 43902: [2, 3]})
        humidity_unit = link.for_unit(2)
        humidity_unit.holding.update(
            {
                40002: [1, 0],
                41006: 45,
                41011: 50,
                41020: [30, 30],
                41120: [3, 60, 0, 0, 0],
            }
        )
        humidity_unit.fail_read(41007, IllegalDataAddressError())
        humidity_unit.fail_read(41013, IllegalDataAddressError())
        link.for_unit(3).fail_requests(ModbusTimeoutError("silent"))
        return link

    @pytest.fixture(name="room")
    def room_fixture(self) -> RoomConfig:
        return RoomConfig(key="unit_1", name="Unit 1", profile="ii_plain", slave=2)

    @pytest.fixture(name="experiment_args")
    def experiment_args_fixture(self) -> Namespace:
        return Namespace(
            mode="write_observe",
            room_index=1,
            delta=5,
            settle_seconds=0,
            poll_interval=0,
            max_polls=1,
            idle_seconds=0,
            observe_seconds=1,
            sample_interval=1,
            target=35,
            restore_target=30,
        )

    @pytest.mark.parametrize("profiles", [None, {2: "s_fc", 3: "s_plain"}])
    async def test_discovery_derives_entity_keys_from_the_selected_profile(
        self, link: MockModbusConnection, profiles: dict[int, str] | None,
    ) -> None:
        rooms = await bil.discover_rooms(link, "mock-port", profiles)

        assert [room.slave for room in rooms] == [2, 3]
        assert [room.profile for room in rooms] == (
            ["ii_f", "ii_plain"] if profiles is None else ["s_fc", "s_plain"]
        )
        for room in rooms:
            assert room.supported_entity_keys == frozenset(
                supported_entity_keys_for_profile(room.profile)
            )

    @pytest.mark.parametrize("slave", [2, 3])
    async def test_cycle_counts_swallowed_timeouts_as_failures(
        self, link: MockModbusConnection, room: RoomConfig, slave: int,
    ) -> None:
        client = bil.MeltemModbusClient(link.for_unit, port="mock-port")

        samples = await bil.run_cycles(
            client, [replace(room, slave=slave)], 1, {"airflow": bil.AIRFLOW_PLAN}
        )

        assert len(samples) == 1
        assert samples[0].ok is (slave == 2)
        if slave == 3:
            assert "flow: silent" in samples[0].detail
            assert len(link.for_unit(3).read_events) == 2
        else:
            assert samples[0].detail == ""

    async def test_cycle_counts_partial_mode_failures_as_failures(
        self, link: MockModbusConnection, room: RoomConfig,
    ) -> None:
        link.for_unit(2).fail_read(REGISTER_MODE, IllegalDataAddressError())
        client = bil.MeltemModbusClient(link.for_unit, port="mock-port")

        samples = await bil.run_cycles(client, [room], 1, {"airflow": bil.AIRFLOW_PLAN})

        assert samples[0].ok is False
        assert "flow_control:" in samples[0].detail

    @pytest.mark.parametrize("group", ["intensive", "temperature"])
    async def test_cycle_does_not_count_old_errors_from_skipped_groups(
        self, link: MockModbusConnection, room: RoomConfig, group: str,
    ) -> None:
        client = bil.MeltemModbusClient(link.for_unit, port="mock-port")
        state = RoomState(target_level=30).with_read_health(
            group,
            ReadHealth(
                last_attempt=datetime.now(UTC) - timedelta(minutes=1),
                consecutive_failures=3,
                last_error="old error",
            ),
        )
        with patch.object(client, "read_room_state", new=AsyncMock(return_value=state)):
            samples = await bil.run_cycles(client, [room], 1, {"airflow": bil.AIRFLOW_PLAN})

        assert samples[0].ok is True
        assert samples[0].detail == ""

    @pytest.mark.parametrize("mode", bil.SINGLE_ROOM_MODES)
    async def test_write_experiments_restore_after_success(
        self, link: MockModbusConnection, room: RoomConfig, experiment_args: Namespace, mode: str,
    ) -> None:
        experiment_args.mode = mode
        client = bil.MeltemModbusClient(link.for_unit, port="mock-port")
        baseline = RoomState(target_level=30)
        with (
            patch.object(
                client, "read_room_state", new=AsyncMock(
                    side_effect=[baseline, RoomState(target_level=35), baseline]
                )
            ),
            patch.object(bil, "_timed_write", new=AsyncMock(wraps=bil._timed_write)) as write,
            patch.object(bil, "_observe", new=AsyncMock()),
            patch.object(bil.asyncio, "sleep", new=AsyncMock()),
        ):
            await bil.run_mode(experiment_args, client, link, [room])

        assert [call.args[2:4] for call in write.call_args_list] == [
            (35, "write"), (30, "restore")
        ]

    @pytest.mark.parametrize("mode", bil.SINGLE_ROOM_MODES)
    @pytest.mark.parametrize(
        "error", [RuntimeError("observation failed"), asyncio.CancelledError("cancelled")]
    )
    async def test_write_experiments_restore_after_failure_or_cancellation(
        self,
        link: MockModbusConnection,
        room: RoomConfig,
        experiment_args: Namespace,
        mode: str,
        error: BaseException,
    ) -> None:
        experiment_args.mode = mode
        client = bil.MeltemModbusClient(link.for_unit, port="mock-port")
        baseline = RoomState(target_level=30)
        with (
            patch.object(
                client, "read_room_state", new=AsyncMock(side_effect=[baseline, error, baseline])
            ),
            patch.object(bil, "_timed_write", new=AsyncMock(wraps=bil._timed_write)) as write,
            patch.object(bil, "_observe", new=AsyncMock(side_effect=error)),
            patch.object(bil.asyncio, "sleep", new=AsyncMock()),
            pytest.raises(type(error), match=str(error)),
        ):
            await bil.run_mode(experiment_args, client, link, [room])

        assert [call.args[2:4] for call in write.call_args_list] == [
            (35, "write"), (30, "restore")
        ]

    @pytest.mark.parametrize("mode", bil.SINGLE_ROOM_MODES)
    async def test_write_experiments_restore_after_the_initial_write_fails(
        self, link: MockModbusConnection, room: RoomConfig, experiment_args: Namespace, mode: str,
    ) -> None:
        experiment_args.mode = mode
        client = bil.MeltemModbusClient(link.for_unit, port="mock-port")
        with (
            patch.object(
                client, "read_room_state", new=AsyncMock(return_value=RoomState(target_level=30))
            ),
            patch.object(
                bil, "_timed_write", new=AsyncMock(
                    side_effect=[bil.MeltemConnectionError("write failed"), None]
                )
            ) as write,
            patch.object(bil.asyncio, "sleep", new=AsyncMock()),
            pytest.raises(bil.MeltemConnectionError, match="write failed"),
        ):
            await bil.run_mode(experiment_args, client, link, [room])

        assert [call.args[2:4] for call in write.call_args_list] == [
            (35, "write"), (30, "restore")
        ]

    @pytest.mark.parametrize("phase_failed", [False, True])
    async def test_restore_errors_are_reported_without_masking_the_original_failure(
        self,
        link: MockModbusConnection,
        room: RoomConfig,
        experiment_args: Namespace,
        capsys: pytest.CaptureFixture[str],
        phase_failed: bool,
    ) -> None:
        client = bil.MeltemModbusClient(link.for_unit, port="mock-port")
        original_error = RuntimeError("observation failed") if phase_failed else None
        with (
            patch.object(
                bil, "_timed_write", new=AsyncMock(
                    side_effect=[None, RuntimeError("restore failed")]
                )
            ),
            patch.object(bil, "_observe", new=AsyncMock(side_effect=original_error)),
            pytest.raises(RuntimeError, match="observation failed" if phase_failed else "restore failed"),
        ):
            await bil.run_mode(experiment_args, client, link, [room])

        assert "ERROR: restoring airflow of slave 2 failed: RuntimeError: restore failed" in (
            capsys.readouterr().out
        )


class TestWriteRegisters:
    @pytest.fixture(name="link")
    def link_fixture(self) -> MockModbusConnection:
        link = MockModbusConnection()

        @asynccontextmanager
        async def _open_link(_port: str) -> AsyncIterator[MockModbusConnection]:
            yield link

        with patch.object(write_registers, "open_link", _open_link):
            yield link

    async def _run(self, *args: str) -> int:
        argv = ["write_registers", "--slave", "2"]
        for address, value in _MANUAL_80_SEQUENCE:
            argv += ["--write", f"{address}={value}"]
        with patch.object(sys, "argv", [*argv, *args]):
            return await write_registers.main()

    async def test_a_failed_write_stops_before_the_apply(
        self, link: MockModbusConnection
    ) -> None:
        unit = link.for_unit(2)
        unit.fail_write(REGISTER_CURRENT_LEVEL, IllegalDataValueError())
        written: list[WriteEvent] = []
        unit.on_write(written.append)

        assert await self._run() == 1
        assert [event.address for event in written] == [REGISTER_MODE]

    async def test_keep_going_sends_the_rest_but_still_fails(
        self, link: MockModbusConnection
    ) -> None:
        unit = link.for_unit(2)
        unit.fail_write(REGISTER_CURRENT_LEVEL, IllegalDataValueError())
        written: list[WriteEvent] = []
        unit.on_write(written.append)

        assert await self._run("--keep-going") == 1
        assert [event.address for event in written] == [REGISTER_MODE, REGISTER_APPLY]
