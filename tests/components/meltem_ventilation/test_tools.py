"""Tests for the gateway tools that do not need a live gateway."""

from __future__ import annotations

import asyncio
import subprocess
import sys
from argparse import Namespace
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import cast
from unittest.mock import AsyncMock, patch

import pytest
from modbus_connection import IllegalDataAddressError, IllegalDataValueError, ModbusTimeoutError
from modbus_connection.mock import MockModbusConnection, WriteEvent
from modbus_connection.tmodbus import ModbusConnection, TmodbusUnit

from custom_components.meltem_ventilation.const import (
    DEFAULT_GATEWAY_DEVICE_ID,
    DEFAULT_MAX_REQUESTS_PER_SECOND,
    DEFAULT_PORT,
    FIXED_TIMEOUT,
    REGISTER_APPLY,
    REGISTER_CURRENT_LEVEL,
    REGISTER_GATEWAY_NODE_ADDRESS_1,
    REGISTER_GATEWAY_NUMBER_OF_NODES,
    REGISTER_MODE,
    REGISTER_MODE_STATUS,
    REQUEST_GAP_SECONDS,
)
from custom_components.meltem_ventilation.modbus_helpers import (
    build_serial_params,
    supported_entity_keys_for_profile,
)
from custom_components.meltem_ventilation.models import (
    ReadHealth,
    ReadJobOutcome,
    RoomConfig,
    RoomRead,
    RoomState,
)
from tools import _link, raw_requests, write_registers
from tools import benchmark_integration_like as bil
from tools._pymodbus_compat import install_pymodbus_unit_keyword_compat
from tools.profile_register_reads import SPECS as PROFILE_READ_SPECS

_REPO_ROOT = Path(__file__).resolve().parents[3]
_MANUAL_80_SEQUENCE = ((REGISTER_MODE, 3), (REGISTER_CURRENT_LEVEL, 80), (REGISTER_APPLY, 0))


def _benchmark_connection(link: MockModbusConnection) -> ModbusConnection:
    return cast(ModbusConnection, link)


def test_link_settings_match_the_integration() -> None:
    """The tools keep their own copy, so it must not drift from the integration."""

    assert _link.serial_params("/dev/ttyX") == build_serial_params("/dev/ttyX")
    assert _link.TIMEOUT == FIXED_TIMEOUT
    assert _link.REQUEST_GAP_SECONDS == REQUEST_GAP_SECONDS
    assert _link.DEFAULT_PORT == DEFAULT_PORT
    assert _link.GATEWAY_DEVICE_ID == DEFAULT_GATEWAY_DEVICE_ID
    assert _link.REGISTER_GATEWAY_NUMBER_OF_NODES == REGISTER_GATEWAY_NUMBER_OF_NODES
    assert _link.REGISTER_GATEWAY_NODE_ADDRESS_1 == REGISTER_GATEWAY_NODE_ADDRESS_1


class TestGatewaySetupBlockGuard:
    """No tool may read the gateway block that holds its cloud credential."""

    @pytest.mark.parametrize(
        ("address", "count"),
        [(41030, 1), (41098, 1), (41034, 57), (41000, 31), (41090, 20), (41000, 120)],
    )
    def test_gateway_reads_touching_the_block_are_refused(self, address: int, count: int) -> None:
        with pytest.raises(_link.ForbiddenReadError):
            _link.check_read_allowed(_link.GATEWAY_DEVICE_ID, address, count)

    @pytest.mark.parametrize(
        ("unit_id", "address", "count"),
        [
            (1, 41012, 3),
            (1, 41019, 2),
            (1, 41000, 30),
            (1, 41099, 1),
            (2, 41030, 4),
            (4, 41000, 34),
        ],
    )
    def test_other_reads_are_allowed(self, unit_id: int, address: int, count: int) -> None:
        _link.check_read_allowed(unit_id, address, count)

    async def test_guarded_unit_refuses_before_sending(self) -> None:
        connection = _link.GuardedModbusConnection(_link.serial_params("/dev/ttyX"))
        gateway = connection.for_unit(_link.GATEWAY_DEVICE_ID)
        with (
            patch.object(
                TmodbusUnit, "read_holding_registers", AsyncMock(return_value=[0])
            ) as hold,
            patch.object(TmodbusUnit, "read_input_registers", AsyncMock(return_value=[0])) as inp,
        ):
            with pytest.raises(_link.ForbiddenReadError):
                await gateway.read_holding_registers(41034, 57)
            with pytest.raises(_link.ForbiddenReadError):
                await gateway.read_input_registers(41030, 4)
            assert await gateway.read_holding_registers(41012, 3) == [0]
            assert await connection.for_unit(2).read_input_registers(41030, 4) == [0]

        hold.assert_awaited_once_with(41012, 3)
        inp.assert_awaited_once_with(41030, 4)

    def test_tools_open_guarded_connections(self) -> None:
        assert isinstance(bil.open_connection("/dev/ttyX"), _link.GuardedModbusConnection)

    def test_run_reports_a_forbidden_read(self, capsys: pytest.CaptureFixture[str]) -> None:
        async def main() -> int:
            _link.check_read_allowed(_link.GATEWAY_DEVICE_ID, 41030, 1)
            return 0

        with pytest.raises(SystemExit) as exit_info:
            _link.run(main)

        assert exit_info.value.code == 1
        assert "refusing to read 41030..41030 on the gateway" in capsys.readouterr().out


def test_register_profile_scan_includes_mode_status_and_single_reads() -> None:
    assert {(spec.address, spec.count) for spec in PROFILE_READ_SPECS} >= {
        (41100, 3),
        (41100, 1),
        (41101, 1),
        (41102, 1),
    }


class _DeviceIdPymodbusClient:
    def read_holding_registers(
        self, address: int, *, count: int = 1, device_id: int = 1
    ) -> tuple[int, int, int]:
        return address, count, device_id

    def write_register(
        self, address: int, value: int, *, device_id: int = 1
    ) -> tuple[int, int, int]:
        return address, value, device_id


class _SlavePymodbusClient:
    def read_holding_registers(
        self, address: int, *, count: int = 1, slave: int = 1
    ) -> tuple[int, int, int]:
        return address, count, slave

    def write_register(self, address: int, value: int, *, slave: int = 1) -> tuple[int, int, int]:
        return address, value, slave


def _install_legacy_baseline_keyword_shim(client_class: type[object]) -> None:
    """Mirror the alias conversion used by the pinned benchmark script."""

    original_read = getattr(client_class, "read_holding_registers")
    original_write = getattr(client_class, "write_register")

    def compat_read(self: object, *args: object, **kwargs: object) -> object:
        if "device_id" in kwargs and "slave" not in kwargs:
            kwargs["slave"] = kwargs.pop("device_id")
        return original_read(self, *args, **kwargs)

    def compat_write(self: object, *args: object, **kwargs: object) -> object:
        if "device_id" in kwargs and "slave" not in kwargs:
            kwargs["slave"] = kwargs.pop("device_id")
        return original_write(self, *args, **kwargs)

    setattr(client_class, "read_holding_registers", compat_read)
    setattr(client_class, "write_register", compat_write)


@pytest.mark.parametrize("client_class", [_DeviceIdPymodbusClient, _SlavePymodbusClient])
def test_pymodbus_compat_supports_the_pinned_benchmark_shim(
    client_class: type[object],
) -> None:
    isolated_client_class = type(f"Compat{client_class.__name__}", (client_class,), {})
    install_pymodbus_unit_keyword_compat(isolated_client_class)
    _install_legacy_baseline_keyword_shim(isolated_client_class)

    client = isolated_client_class()
    read_holding_registers = getattr(client, "read_holding_registers")
    write_register = getattr(client, "write_register")
    assert read_holding_registers(41020, count=2, device_id=7) == (41020, 2, 7)
    assert write_register(41121, value=40, device_id=7) == (41121, 40, 7)


def test_pymodbus_compat_rejects_conflicting_unit_keywords() -> None:
    client_class = type("ConflictingClient", (_DeviceIdPymodbusClient,), {})
    install_pymodbus_unit_keyword_compat(client_class)

    with pytest.raises(TypeError, match="Pass only one unit address"):
        getattr(client_class(), "read_holding_registers")(41020, count=2, slave=6, device_id=7)


def test_pymodbus_compat_fails_clearly_on_unknown_api() -> None:
    class UnsupportedClient:
        def read_holding_registers(self, address: int, *, unit: int = 1) -> int:
            return address + unit

        def write_register(self, address: int, value: int, *, unit: int = 1) -> int:
            return address + value + unit

    with pytest.raises(
        RuntimeError,
        match="Unsupported pymodbus read_holding_registers signature",
    ):
        install_pymodbus_unit_keyword_compat(UnsupportedClient)


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
                41008: 32767,
                41011: 50,
                41012: 32767,
                41020: [30, 30],
                41100: [3, 60, 0],
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
        self,
        link: MockModbusConnection,
        profiles: dict[int, str] | None,
    ) -> None:
        rooms = await bil.discover_rooms(_benchmark_connection(link), "mock-port", profiles)

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
        self,
        link: MockModbusConnection,
        room: RoomConfig,
        slave: int,
    ) -> None:
        client = bil.MeltemModbusClient(link.for_unit, port="mock-port")

        samples = await bil.run_cycles(
            client, [replace(room, slave=slave)], 1, {"airflow": bil.AIRFLOW_PLAN}
        )

        assert len(samples) == 1
        assert samples[0].ok is (slave == 2)
        if slave == 3:
            assert "flow: silent" in samples[0].detail
            assert len(link.for_unit(3).read_events) == 1
        else:
            assert samples[0].detail == ""

    async def test_cycle_counts_partial_mode_failures_as_failures(
        self,
        link: MockModbusConnection,
        room: RoomConfig,
    ) -> None:
        link.for_unit(2).fail_read(REGISTER_MODE_STATUS, IllegalDataAddressError())
        client = bil.MeltemModbusClient(link.for_unit, port="mock-port")

        samples = await bil.run_cycles(client, [room], 1, {"airflow": bil.AIRFLOW_PLAN})

        assert samples[0].ok is False
        assert "flow_control:" in samples[0].detail

    @pytest.mark.parametrize("group", ["intensive", "temperature"])
    async def test_cycle_does_not_count_old_errors_from_skipped_groups(
        self,
        link: MockModbusConnection,
        room: RoomConfig,
        group: str,
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
        read = RoomRead(state, ReadJobOutcome(attempted_groups=frozenset({"flow"})))
        with patch.object(client, "read_room", new=AsyncMock(return_value=read)):
            samples = await bil.run_cycles(client, [room], 1, {"airflow": bil.AIRFLOW_PLAN})

        assert samples[0].ok is True
        assert samples[0].detail == ""

    @pytest.mark.parametrize("mode", bil.SINGLE_ROOM_MODES)
    async def test_write_experiments_restore_after_success(
        self,
        link: MockModbusConnection,
        room: RoomConfig,
        experiment_args: Namespace,
        mode: str,
    ) -> None:
        experiment_args.mode = mode
        client = bil.MeltemModbusClient(link.for_unit, port="mock-port")
        baseline = RoomState(target_level=30)
        with (
            patch.object(
                client,
                "read_room_state",
                new=AsyncMock(side_effect=[baseline, RoomState(target_level=35), baseline]),
            ),
            patch.object(bil, "_timed_write", new=AsyncMock(wraps=bil._timed_write)) as write,
            patch.object(bil, "_observe", new=AsyncMock()),
            patch.object(bil.asyncio, "sleep", new=AsyncMock()),
        ):
            await bil.run_mode(experiment_args, client, [room])

        assert [call.args[2:4] for call in write.call_args_list] == [(35, "write"), (30, "restore")]

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
            await bil.run_mode(experiment_args, client, [room])

        assert [call.args[2:4] for call in write.call_args_list] == [(35, "write"), (30, "restore")]

    @pytest.mark.parametrize("mode", bil.SINGLE_ROOM_MODES)
    async def test_write_experiments_restore_after_the_initial_write_fails(
        self,
        link: MockModbusConnection,
        room: RoomConfig,
        experiment_args: Namespace,
        mode: str,
    ) -> None:
        experiment_args.mode = mode
        client = bil.MeltemModbusClient(link.for_unit, port="mock-port")
        with (
            patch.object(
                client, "read_room_state", new=AsyncMock(return_value=RoomState(target_level=30))
            ),
            patch.object(
                bil,
                "_timed_write",
                new=AsyncMock(side_effect=[bil.MeltemConnectionError("write failed"), None]),
            ) as write,
            patch.object(bil.asyncio, "sleep", new=AsyncMock()),
            pytest.raises(bil.MeltemConnectionError, match="write failed"),
        ):
            await bil.run_mode(experiment_args, client, [room])

        assert [call.args[2:4] for call in write.call_args_list] == [(35, "write"), (30, "restore")]

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
                bil,
                "_timed_write",
                new=AsyncMock(side_effect=[None, RuntimeError("restore failed")]),
            ),
            patch.object(bil, "_observe", new=AsyncMock(side_effect=original_error)),
            pytest.raises(
                RuntimeError, match="observation failed" if phase_failed else "restore failed"
            ),
        ):
            await bil.run_mode(experiment_args, client, [room])

        assert "ERROR: restoring airflow of slave 2 failed: RuntimeError: restore failed" in (
            capsys.readouterr().out
        )

    def test_defaults_follow_the_integration(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(sys, "argv", ["benchmark_integration_like"])

        args = bil.parse_args()

        assert args.gap == REQUEST_GAP_SECONDS
        assert args.rate == DEFAULT_MAX_REQUESTS_PER_SECOND

    @pytest.mark.parametrize(
        ("state", "expected"),
        [
            (RoomState(target_level=0, supply_air_flow=12), 0),
            (RoomState(target_level=40, supply_air_flow=38), 40),
            (RoomState(supply_air_flow=38), 38),
        ],
        ids=("off-target", "target", "measured-only"),
    )
    def test_airflow_keeps_a_target_of_zero(self, state: RoomState, expected: int) -> None:
        assert bil._airflow(state) == expected

    async def test_raw_snapshots_share_the_client_read_quarantine(
        self, link: MockModbusConnection, room: RoomConfig
    ) -> None:
        client = bil.MeltemModbusClient(link.for_unit, port="mock-port")
        unit = link.for_unit(room.slave)
        unit.fail_read(REGISTER_MODE_STATUS, ModbusTimeoutError("late reply pending"))
        await client.read_room(room, RoomState(), bil.AIRFLOW_PLAN)
        reads_before = len(unit.read_events)

        snapshot = await _link.read_or_none(client.unit_for(room.slave), 41020, 2)

        # A raw read must not pick up the late reply of the unanswered request.
        assert snapshot is None
        assert len(unit.read_events) == reads_before


def test_benchmark_runs_without_home_assistant() -> None:
    """The tool brings its own Home Assistant stub; pytest always has the real one loaded."""

    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys, runpy; sys.argv = ['benchmark_integration_like', '--help'];"
            "sys.modules['homeassistant'] = None;"
            "runpy.run_module('tools.benchmark_integration_like', run_name='__main__')",
        ],
        cwd=_REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "--rate" in result.stdout


class TestRawRequests:
    @pytest.mark.parametrize(
        "failed_addresses",
        [(), (REGISTER_MODE_STATUS,), (41020,), (REGISTER_MODE_STATUS, 41020)],
        ids=("success", "first-fails", "second-fails", "all-fail"),
    )
    async def test_exit_code_reports_any_failed_request(
        self, failed_addresses: tuple[int, ...], capsys: pytest.CaptureFixture[str]
    ) -> None:
        link = MockModbusConnection()
        unit = link.for_unit(2)
        unit.holding.update({REGISTER_MODE_STATUS: [3, 60, 0], 41020: [30, 30]})
        for address in failed_addresses:
            unit.fail_read(
                address,
                ModbusTimeoutError("timeout")
                if address == REGISTER_MODE_STATUS
                else IllegalDataAddressError(),
            )

        @asynccontextmanager
        async def open_link(_port: str) -> AsyncIterator[MockModbusConnection]:
            yield link

        with (
            patch.object(raw_requests, "open_link", open_link),
            patch.object(
                sys,
                "argv",
                [
                    "raw_requests",
                    "--slave",
                    "2",
                    "--read",
                    f"{REGISTER_MODE_STATUS}:3",
                    "--read",
                    "41020:2",
                    "--rounds",
                    "2",
                ],
            ),
        ):
            assert await raw_requests.main() == (1 if failed_addresses else 0)

        output = capsys.readouterr().out
        summary = output.split("summary:\n", 1)[1]
        for line, address in zip(summary.splitlines(), (REGISTER_MODE_STATUS, 41020), strict=True):
            assert f"ok={0 if address in failed_addresses else 2}/2" in line
        if REGISTER_MODE_STATUS in failed_addresses:
            assert "ModbusTimeoutError=2" in summary
        if 41020 in failed_addresses:
            assert "IllegalDataAddressError=2" in summary


class TestWriteRegisters:
    @pytest.fixture(name="link")
    def link_fixture(self) -> Iterator[MockModbusConnection]:
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

    async def test_a_failed_write_stops_before_the_apply(self, link: MockModbusConnection) -> None:
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
