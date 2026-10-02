"""Run integration-like Meltem polling loops against a local gateway."""

from __future__ import annotations

import argparse
import asyncio
import importlib
import sys
import time
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from functools import partial
from pathlib import Path

from modbus_connection.tmodbus import ModbusConnection

from tools._link import (
    GATEWAY_DEVICE_ID,
    LinkOpenError,
    Sample,
    elapsed_ms,
    print_summary,
    read_or_none,
    run,
    tool_parser,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
PACKAGE = "custom_components.meltem_ventilation"


def _install_homeassistant_stub() -> None:
    """Provide the tiny Home Assistant surface needed by the loaded modules."""

    homeassistant_module = type(sys)("homeassistant")
    const_module = type(sys)("homeassistant.const")
    util_module = type(sys)("homeassistant.util")
    dt_module = type(sys)("homeassistant.util.dt")

    class Platform:
        SENSOR = "sensor"
        BINARY_SENSOR = "binary_sensor"
        NUMBER = "number"
        SELECT = "select"
        FAN = "fan"
        SWITCH = "switch"

    const_module.Platform = Platform
    dt_module.utcnow = lambda: datetime.now(UTC)
    util_module.dt = dt_module
    homeassistant_module.const = const_module
    homeassistant_module.util = util_module
    sys.modules.setdefault("homeassistant", homeassistant_module)
    sys.modules.setdefault("homeassistant.const", const_module)
    sys.modules.setdefault("homeassistant.util", util_module)
    sys.modules.setdefault("homeassistant.util.dt", dt_module)


def _install_package_stub() -> None:
    """Register a package shell, so the modules load without the package's __init__.py."""

    custom_components_module = sys.modules.setdefault(
        "custom_components",
        type(sys)("custom_components"),
    )
    package_module = sys.modules.setdefault(PACKAGE, type(sys)(PACKAGE))
    package_module.__path__ = [str(REPO_ROOT / "custom_components" / "meltem_ventilation")]
    custom_components_module.meltem_ventilation = package_module


_install_homeassistant_stub()
_install_package_stub()

const = importlib.import_module(f"{PACKAGE}.const")
models = importlib.import_module(f"{PACKAGE}.models")
modbus_helpers = importlib.import_module(f"{PACKAGE}.modbus_helpers")
modbus_client = importlib.import_module(f"{PACKAGE}.modbus_client")

FIXED_TIMEOUT = const.FIXED_TIMEOUT
MODEL_PROFILES: tuple[str, ...] = const.MODEL_PROFILES

RefreshPlan = models.RefreshPlan
RoomConfig = models.RoomConfig
RoomState = models.RoomState
MeltemModbusClient = modbus_client.MeltemModbusClient
MeltemConnectionError = modbus_helpers.MeltemConnectionError
build_serial_params = modbus_helpers.build_serial_params
detect_slave_details = modbus_helpers.detect_slave_details
discover_gateway_nodes = modbus_helpers.discover_gateway_nodes
new_transport_policy = modbus_helpers.new_transport_policy
prepare_unit = modbus_helpers.prepare_unit

AIRFLOW_PLAN = RefreshPlan.only(refresh_airflow=True)
SCHEDULER_PLANS: dict[str, RefreshPlan] = {
    "airflow": AIRFLOW_PLAN,
    "temperatures": RefreshPlan.only(refresh_temperatures=True, refresh_environment=True),
    "status": RefreshPlan.only(refresh_status=True, refresh_filter_change_due=True),
    "slow": RefreshPlan.only(
        refresh_filter_days=True, refresh_operating_hours=True, refresh_control_settings=True
    ),
}
PROFILE_BY_DETECTED_SUFFIX = {
    "plain": "ii_plain",
    "f": "ii_f",
    "fc": "ii_fc",
    "fc_voc": "ii_fc_voc",
}
SINGLE_ROOM_MODES = ("write_refresh", "write_idle_check", "write_observe", "airflow_long_observe")
MODES = ("full", "scheduler", *SINGLE_ROOM_MODES)


def open_connection(port: str) -> ModbusConnection:
    """Create the gateway link the way Home Assistant's modbus integration does.

    The link opens with the first request; ``prepare_unit`` asks for the
    timeout and spacing per unit before that.
    """

    return ModbusConnection(build_serial_params(port))


async def discover_rooms(
    connection: ModbusConnection,
    port: str,
    profiles: Mapping[int, str] | None = None,
) -> list[RoomConfig]:
    """Discover configured rooms and probe their supported keys.

    The series cannot be probed, so a unit counts as M-WRG-II unless
    ``profiles`` names its profile by slave address. Raises ``LinkOpenError``
    when the serial link cannot be opened.
    """

    profiles = profiles or {}
    policy = new_transport_policy()
    rooms: list[RoomConfig] = []
    try:
        gateway = prepare_unit(connection.for_unit(GATEWAY_DEVICE_ID), GATEWAY_DEVICE_ID, policy)
        slaves = await discover_gateway_nodes(gateway, port, start=2, end=16)
        for index, slave in enumerate(slaves, start=1):
            detected_suffix, preview = await detect_slave_details(
                prepare_unit(connection.for_unit(slave), slave, policy)
            )
            profile = profiles.get(slave) or PROFILE_BY_DETECTED_SUFFIX.get(
                detected_suffix, "ii_plain"
            )
            rooms.append(
                RoomConfig(
                    key=f"unit_{index}",
                    name=f"Unit {index}",
                    slave=slave,
                    profile=profile,
                    preview=preview,
                    supported_entity_keys=frozenset(
                        modbus_helpers.supported_entity_keys_for_profile(profile)
                    ),
                )
            )
    except MeltemConnectionError as err:
        raise LinkOpenError(f"could not open serial connection on {port}: {err}") from err
    return rooms


def print_rooms(rooms: list[RoomConfig]) -> None:
    """Print the discovered rooms with the position that --room-index uses."""

    print("rooms:")
    for index, room in enumerate(rooms, start=1):
        print(f"  {index}: slave {room.slave} {room.profile} ({room.preview})")


def parse_profile(value: str) -> tuple[int, str]:
    """Parse one SLAVE=PROFILE override."""

    slave, separator, profile = value.partition("=")
    if not separator or not slave.isdigit() or profile not in MODEL_PROFILES:
        raise argparse.ArgumentTypeError(
            f"use SLAVE=PROFILE with one of: {', '.join(MODEL_PROFILES)}"
        )
    return int(slave), profile


def record(
    samples: list[Sample],
    slave: int,
    label: str,
    start: float,
    detail: str = "",
    *,
    ok: bool = True,
    status: str | None = None,
) -> None:
    """Append one step timed from ``start`` to ``samples`` and print it."""

    sample = Sample(ok, elapsed_ms(start), detail)
    samples.append(sample)
    status = status or ("OK" if ok else "ERR")
    print(
        f"  unit {slave:>2} {label:<22} {status:<4} {sample.latency_ms:>6.1f} ms  {detail}".rstrip()
    )


def _airflow(state: RoomState) -> int | None:
    return state.target_level or state.supply_air_flow


async def run_cycles(
    client: MeltemModbusClient,
    rooms: list[RoomConfig],
    cycles: int,
    plans: Mapping[str, RefreshPlan],
) -> list[Sample]:
    """Read every plan for every room in each cycle, like the integration's scheduler."""

    states: dict[str, RoomState] = {}
    samples: list[Sample] = []
    for cycle in range(1, cycles + 1):
        print(f"cycle {cycle}/{cycles}")
        for label, plan in plans.items():
            for room in rooms:
                start = time.perf_counter()
                read_started_at = datetime.now(UTC)
                try:
                    states[room.key] = await client.read_room_state(
                        room, states.get(room.key, RoomState()), plan
                    )
                except Exception as err:
                    record(
                        samples, room.slave, label, start, f"{type(err).__name__}: {err}", ok=False
                    )
                else:
                    state = states[room.key]
                    failures = [
                        f"{group}: {health.last_error}"
                        for group in plan.read_groups()
                        if (health := state.read_health_for(group)).last_attempt is not None
                        and health.last_attempt >= read_started_at
                        and health.last_error is not None
                    ]
                    record(
                        samples, room.slave, label, start, "; ".join(failures), ok=not failures
                    )
    return samples


async def _read_baseline(client: MeltemModbusClient, room: RoomConfig) -> tuple[RoomState, int]:
    """Read the room's airflow; raise RuntimeError when it cannot be determined."""

    state = await client.read_room_state(room, RoomState(), AIRFLOW_PLAN)
    baseline = _airflow(state)
    if baseline is None:
        raise RuntimeError(f"Could not determine baseline airflow for slave {room.slave}")
    return state, baseline


async def _timed_write(
    client: MeltemModbusClient,
    room: RoomConfig,
    target: int,
    label: str,
    samples: list[Sample],
) -> None:
    """Write one balanced target and record how long the write sequence took."""

    start = time.perf_counter()
    await client.write_level(room, target)
    record(samples, room.slave, f"{label}_target", start, f"target={target}")


async def _observe(
    samples: list[Sample],
    slave: int,
    read: Callable[[], Awaitable[object]],
    observe_seconds: float,
    sample_interval: float,
) -> None:
    """Call ``read`` every ``sample_interval`` seconds for ``observe_seconds``."""

    for index in range(1, max(1, int(observe_seconds / sample_interval)) + 1):
        await asyncio.sleep(sample_interval)
        start = time.perf_counter()
        value = await read()
        record(samples, slave, f"observe_{index}", start, str(value))


@asynccontextmanager
async def _restore_after_write(
    slave: int, restore: Callable[[], Awaitable[None]]
) -> AsyncIterator[None]:
    """Attempt restoration on every exit without masking an experiment failure."""

    phase_failed = False
    try:
        yield
    except BaseException:
        phase_failed = True
        raise
    finally:
        try:
            await restore()
        except Exception as err:
            print(f"ERROR: restoring airflow of slave {slave} failed: {type(err).__name__}: {err}")
            if not phase_failed:
                raise


async def run_write_refresh(
    client: MeltemModbusClient,
    connection: ModbusConnection,
    room: RoomConfig,
    delta: int,
    settle_seconds: float,
    poll_interval: float,
    max_polls: int,
) -> list[Sample]:
    """Write one balanced airflow target, poll until applied, then restore."""

    unit = connection.for_unit(room.slave)
    baseline_state, baseline_flow = await _read_baseline(client, room)
    target_flow = max(0, baseline_flow + delta)
    samples: list[Sample] = []

    async def read_raw_snapshot() -> dict[str, object]:
        return {
            "mode_block_41120_41122": await read_or_none(unit, const.REGISTER_MODE, 3),
            "flow_block_41020_41021": await read_or_none(unit, const.REGISTER_EXTRACT_AIR_FLOW, 2),
        }

    async def write_and_poll(target: int, label: str) -> None:
        await _timed_write(client, room, target, label, samples)
        print(f"  raw after {label}: {await read_raw_snapshot()}")
        await asyncio.sleep(settle_seconds)
        phase = f"post_{label}"
        for attempt in range(1, max_polls + 1):
            start = time.perf_counter()
            airflow = _airflow(await client.read_room_state(room, baseline_state, AIRFLOW_PLAN))
            ok = airflow == target
            record(
                samples,
                room.slave,
                phase,
                start,
                f"poll {attempt}: airflow={airflow} expected={target}",
                ok=ok,
                status=None if ok else "WAIT",
            )
            if ok:
                return
            await asyncio.sleep(poll_interval)
        raise RuntimeError(
            f"{phase} did not reach expected airflow {target} for slave {room.slave}"
        )

    print(f"write_refresh slave={room.slave} baseline={baseline_flow} target={target_flow}")
    print(f"  raw before: {await read_raw_snapshot()}")
    async with _restore_after_write(room.slave, partial(write_and_poll, baseline_flow, "restore")):
        await write_and_poll(target_flow, "write")
    return samples


async def run_write_idle_check(
    client: MeltemModbusClient,
    room: RoomConfig,
    delta: int,
    idle_seconds: float,
) -> list[Sample]:
    """Write once, leave the gateway idle, then read back exactly once."""

    _, baseline_flow = await _read_baseline(client, room)
    target_flow = max(0, baseline_flow + delta)
    samples: list[Sample] = []

    async def read_back(label: str, expected: int) -> None:
        start = time.perf_counter()
        airflow = _airflow(await client.read_room_state(room, RoomState(), AIRFLOW_PLAN))
        record(
            samples,
            room.slave,
            label,
            start,
            f"airflow={airflow} expected={expected}",
            ok=airflow == expected,
        )

    print(
        f"write_idle_check slave={room.slave} baseline={baseline_flow} "
        f"target={target_flow} idle={idle_seconds}s"
    )

    async def restore() -> None:
        await _timed_write(client, room, baseline_flow, "restore", samples)
        print("  idling for 5.0s before final readback")
        await asyncio.sleep(5.0)
        await read_back("read_after_restore", baseline_flow)

    async with _restore_after_write(room.slave, restore):
        await _timed_write(client, room, target_flow, "write", samples)
        print(f"  idling for {idle_seconds:.1f}s without reads")
        await asyncio.sleep(idle_seconds)
        await read_back("read_after_idle", target_flow)
    return samples


async def run_write_observe(
    client: MeltemModbusClient,
    connection: ModbusConnection,
    room: RoomConfig,
    delta: int,
    observe_seconds: float,
    sample_interval: float,
) -> list[Sample]:
    """Write once, then observe several candidate readback registers over time."""

    unit = connection.for_unit(room.slave)

    async def single(address: int) -> int | None:
        registers = await read_or_none(unit, address, 1)
        return None if registers is None else registers[0]

    async def snapshot() -> dict[str, object]:
        return {
            "flow_block_41020_41021": await read_or_none(unit, const.REGISTER_EXTRACT_AIR_FLOW, 2),
            "mode_41120": await single(const.REGISTER_MODE),
            "current_level_41121": await single(const.REGISTER_CURRENT_LEVEL),
            "extract_target_41122": await single(const.REGISTER_EXTRACT_AIR_TARGET_LEVEL),
            "software_version_40004": await single(const.REGISTER_SOFTWARE_VERSION),
        }

    before = await snapshot()
    flow_block = before["flow_block_41020_41021"]
    if not isinstance(flow_block, list) or len(flow_block) < 2:
        raise RuntimeError(f"Could not determine baseline airflow for slave {room.slave}")
    baseline_flow = flow_block[1]
    target_flow = max(0, baseline_flow + delta)
    samples: list[Sample] = []

    print(
        f"write_observe slave={room.slave} baseline={baseline_flow} target={target_flow} "
        f"observe={observe_seconds}s interval={sample_interval}s"
    )
    print(f"  snapshot before: {before}")

    async def restore() -> None:
        await _timed_write(client, room, baseline_flow, "restore", samples)
        await asyncio.sleep(5.0)
        print(f"  snapshot after restore: {await snapshot()}")

    async with _restore_after_write(room.slave, restore):
        await _timed_write(client, room, target_flow, "write", samples)
        await _observe(samples, room.slave, snapshot, observe_seconds, sample_interval)
    return samples


async def run_airflow_long_observe(
    client: MeltemModbusClient,
    connection: ModbusConnection,
    room: RoomConfig,
    target: int,
    observe_seconds: float,
    sample_interval: float,
    restore_target: int,
) -> list[Sample]:
    """Write one target, read only airflow sparsely for a longer period, then restore."""

    read_flow_block = partial(
        read_or_none, connection.for_unit(room.slave), const.REGISTER_EXTRACT_AIR_FLOW, 2
    )
    samples: list[Sample] = []

    print(
        f"airflow_long_observe slave={room.slave} target={target} "
        f"observe={observe_seconds}s interval={sample_interval}s restore={restore_target}"
    )
    print(f"  flow before: {await read_flow_block()}")

    async def restore() -> None:
        await _timed_write(client, room, restore_target, "restore", samples)
        await asyncio.sleep(10.0)
        print(f"  flow after restore: {await read_flow_block()}")

    async with _restore_after_write(room.slave, restore):
        await _timed_write(client, room, target, "write", samples)
        await _observe(samples, room.slave, read_flow_block, observe_seconds, sample_interval)
    return samples


async def run_mode(
    args: argparse.Namespace,
    client: MeltemModbusClient,
    connection: ModbusConnection,
    rooms: list[RoomConfig],
) -> list[Sample]:
    """Run the experiment that ``--mode`` selects."""

    if args.mode == "full":
        return await run_cycles(client, rooms, args.cycles, {"full_refresh": RefreshPlan()})
    if args.mode == "scheduler":
        return await run_cycles(client, rooms, args.cycles, SCHEDULER_PLANS)

    room = rooms[args.room_index - 1]
    if args.mode == "write_refresh":
        return await run_write_refresh(
            client,
            connection,
            room,
            args.delta,
            args.settle_seconds,
            args.poll_interval,
            args.max_polls,
        )
    if args.mode == "write_idle_check":
        return await run_write_idle_check(client, room, args.delta, args.idle_seconds)
    if args.mode == "write_observe":
        return await run_write_observe(
            client, connection, room, args.delta, args.observe_seconds, args.sample_interval
        )
    return await run_airflow_long_observe(
        client,
        connection,
        room,
        args.target,
        args.observe_seconds,
        args.sample_interval,
        args.restore_target,
    )


def parse_args() -> argparse.Namespace:
    parser = tool_parser("Run integration-like polling loops against a Meltem gateway.")
    parser.add_argument("--gap", type=float, default=0.3)
    parser.add_argument("--cycles", type=int, default=2)
    parser.add_argument("--mode", choices=MODES, default="scheduler")
    parser.add_argument(
        "--room-index",
        type=int,
        help="1-based position in the rooms list; required for the single-room modes.",
    )
    parser.add_argument(
        "--profile",
        action="append",
        type=parse_profile,
        default=[],
        help="SLAVE=PROFILE, e.g. 3=s_plain for an M-WRG-S. Repeatable.",
    )
    parser.add_argument("--delta", type=int, default=4)
    parser.add_argument("--settle-seconds", type=float, default=1.5)
    parser.add_argument("--poll-interval", type=float, default=1.0)
    parser.add_argument("--max-polls", type=int, default=8)
    parser.add_argument("--idle-seconds", type=float, default=12.0)
    parser.add_argument("--observe-seconds", type=float, default=20.0)
    parser.add_argument("--sample-interval", type=float, default=2.0)
    parser.add_argument("--target", type=int, default=10)
    parser.add_argument("--restore-target", type=int, default=60)
    args = parser.parse_args()
    if args.mode in SINGLE_ROOM_MODES and args.room_index is None:
        parser.error(f"--room-index is required for --mode {args.mode}")
    return args


async def main() -> int:
    args = parse_args()

    # prepare_unit reads the spacing from its module when it sets a unit up.
    modbus_helpers.REQUEST_GAP_SECONDS = args.gap

    # The first request opens the link, after every unit asked for its
    # timeout, just like the shared connection in Home Assistant.
    connection = open_connection(args.port)
    try:
        rooms = await discover_rooms(connection, args.port, dict(args.profile))
        print_rooms(rooms)
        if args.mode in SINGLE_ROOM_MODES and not 1 <= args.room_index <= len(rooms):
            print(f"ERROR: --room-index must be between 1 and {len(rooms)}")
            return 2
        print(f"mode: {args.mode}")
        print(f"gap: {args.gap}s")
        print(f"timeout: {FIXED_TIMEOUT}s")
        print(f"cycles: {args.cycles}")
        print()

        client = MeltemModbusClient(connection.for_unit, port=args.port)
        samples = await run_mode(args, client, connection, rooms)
    finally:
        await connection.close()

    return print_summary(samples)


if __name__ == "__main__":
    run(main, lost_link_errors=(MeltemConnectionError,))
