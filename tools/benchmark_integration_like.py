#!/usr/bin/env python3
"""Run integration-like Meltem polling loops against a local gateway."""

from __future__ import annotations

import argparse
import asyncio
import importlib.util
import statistics
import sys
import time
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from modbus_connection import ModbusConnectionError, ModbusError, ModbusSerialParams
from modbus_connection.tmodbus import ModbusConnection

from tools._link import DEFAULT_PORT, GATEWAY_DEVICE_ID

REPO_ROOT = Path(__file__).resolve().parent.parent


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


def _ensure_package_stub() -> None:
    """Register a package shell so relative imports work without __init__.py."""

    custom_components_module = sys.modules.setdefault(
        "custom_components",
        type(sys)("custom_components"),
    )
    package_module = sys.modules.setdefault(
        "custom_components.meltem_ventilation",
        type(sys)("custom_components.meltem_ventilation"),
    )
    package_module.__path__ = [str(REPO_ROOT / "custom_components" / "meltem_ventilation")]
    custom_components_module.meltem_ventilation = package_module


def _load_module(module_name: str, relative_path: str):
    """Load one integration module directly from source."""

    module_path = REPO_ROOT / relative_path
    spec = importlib.util.spec_from_file_location(module_name, module_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Could not load module {module_name} from {module_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


_install_homeassistant_stub()
_ensure_package_stub()

const_module = _load_module(
    "custom_components.meltem_ventilation.const",
    "custom_components/meltem_ventilation/const.py",
)
models_module = _load_module(
    "custom_components.meltem_ventilation.models",
    "custom_components/meltem_ventilation/models.py",
)
modbus_helpers_module = _load_module(
    "custom_components.meltem_ventilation.modbus_helpers",
    "custom_components/meltem_ventilation/modbus_helpers.py",
)
modbus_client_module = _load_module(
    "custom_components.meltem_ventilation.modbus_client",
    "custom_components/meltem_ventilation/modbus_client.py",
)

FIXED_TIMEOUT = const_module.FIXED_TIMEOUT
MODEL_PROFILES: tuple[str, ...] = const_module.MODEL_PROFILES
SINGLE_ROOM_MODES = ("write_refresh", "write_idle_check", "write_observe", "airflow_long_observe")

RefreshPlan = models_module.RefreshPlan
RoomConfig = models_module.RoomConfig
RoomState = models_module.RoomState
MeltemModbusClient = modbus_client_module.MeltemModbusClient
MeltemConnectionError = modbus_helpers_module.MeltemConnectionError
build_serial_params = modbus_helpers_module.build_serial_params
detect_slave_details = modbus_helpers_module.detect_slave_details
discover_gateway_nodes = modbus_helpers_module.discover_gateway_nodes
new_transport_policy = modbus_helpers_module.new_transport_policy
prepare_unit = modbus_helpers_module.prepare_unit


@dataclass
class Sample:
    """One measured integration-like poll."""

    ok: bool
    latency_ms: float
    detail: str


def open_connection(port: str) -> ModbusConnection:
    """Open the gateway link the way Home Assistant's modbus integration does.

    The timeout and spacing are asked for per unit by ``prepare_unit``.
    """

    params: ModbusSerialParams = build_serial_params(port)
    return ModbusConnection(params)


async def read_raw(
    connection: ModbusConnection, slave: int, address: int, count: int
) -> list[int] | None:
    """Read raw holding registers next to the client, or None if refused."""

    try:
        return await connection.for_unit(slave).read_holding_registers(address, count)
    except ModbusConnectionError:
        raise
    except ModbusError:
        return None


def _profile_from_detected_suffix(suffix: str) -> str:
    mapping = {
        "plain": "ii_plain",
        "f": "ii_f",
        "fc": "ii_fc",
        "fc_voc": "ii_fc_voc",
    }
    return mapping.get(suffix, "ii_plain")


async def discover_rooms(
    connection: ModbusConnection,
    port: str,
    profiles: Mapping[int, str] | None = None,
) -> list[RoomConfig]:
    """Discover configured rooms and probe their supported keys.

    The series cannot be probed, so a unit counts as M-WRG-II unless
    ``profiles`` names its profile by slave address.
    """

    policy = new_transport_policy()
    gateway = prepare_unit(connection.for_unit(GATEWAY_DEVICE_ID), GATEWAY_DEVICE_ID, policy)
    slaves = await discover_gateway_nodes(gateway, port, start=2, end=16)

    rooms: list[RoomConfig] = []
    for index, slave in enumerate(slaves, start=1):
        detected_profile, preview, supported_entity_keys = await detect_slave_details(
            prepare_unit(connection.for_unit(slave), slave, policy)
        )
        rooms.append(
            RoomConfig(
                key=f"unit_{index}",
                name=f"Unit {index}",
                slave=slave,
                profile=(profiles or {}).get(slave)
                or _profile_from_detected_suffix(detected_profile),
                preview=preview,
                supported_entity_keys=frozenset(supported_entity_keys),
            )
        )
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


def _room_at(rooms: list[RoomConfig], room_index: int) -> RoomConfig:
    if room_index < 1 or room_index > len(rooms):
        raise RuntimeError(f"room_index must be between 1 and {len(rooms)}")
    return rooms[room_index - 1]


async def run_full_cycles(
    client: MeltemModbusClient,
    rooms: list[RoomConfig],
    cycles: int,
) -> list[Sample]:
    """Run repeated full refreshes similar to initial integration startup."""

    previous_states: dict[str, RoomState] = {}
    samples: list[Sample] = []

    for cycle in range(cycles):
        print(f"cycle {cycle + 1}/{cycles}")
        for room in rooms:
            start = time.perf_counter()
            try:
                state = await client.read_room_state(
                    room,
                    previous_states.get(room.key, RoomState()),
                    RefreshPlan(),
                )
            except Exception as err:
                samples.append(
                    Sample(
                        ok=False,
                        latency_ms=(time.perf_counter() - start) * 1000,
                        detail=f"{room.slave}: {type(err).__name__}: {err}",
                    )
                )
                print(
                    f"  unit {room.slave:>2} full_refresh            ERR  {samples[-1].latency_ms:>6.1f} ms  {samples[-1].detail}"
                )
                continue

            previous_states[room.key] = state
            samples.append(
                Sample(
                    ok=True,
                    latency_ms=(time.perf_counter() - start) * 1000,
                    detail=f"{room.slave}: ok",
                )
            )
            print(
                f"  unit {room.slave:>2} full_refresh            OK   {samples[-1].latency_ms:>6.1f} ms"
            )

    return samples


async def run_scheduler_cycles(
    client: MeltemModbusClient,
    rooms: list[RoomConfig],
    cycles: int,
) -> list[Sample]:
    """Run a scheduler-like sequence of grouped refresh plans."""

    plans = [
        ("airflow", RefreshPlan.only(refresh_airflow=True)),
        (
            "temperatures",
            RefreshPlan.only(refresh_temperatures=True, refresh_environment=True),
        ),
        (
            "status",
            RefreshPlan.only(refresh_status=True, refresh_filter_change_due=True),
        ),
        (
            "slow",
            RefreshPlan.only(
                refresh_filter_days=True,
                refresh_operating_hours=True,
                refresh_control_settings=True,
            ),
        ),
    ]

    previous_states: dict[str, RoomState] = {}
    samples: list[Sample] = []

    for cycle in range(cycles):
        print(f"cycle {cycle + 1}/{cycles}")
        for label, plan in plans:
            for room in rooms:
                start = time.perf_counter()
                try:
                    state = await client.read_room_state(
                        room,
                        previous_states.get(room.key, RoomState()),
                        plan,
                    )
                except Exception as err:
                    samples.append(
                        Sample(
                            ok=False,
                            latency_ms=(time.perf_counter() - start) * 1000,
                            detail=f"{room.slave} {label}: {type(err).__name__}: {err}",
                        )
                    )
                    print(
                        f"  unit {room.slave:>2} {label:<22} ERR  {samples[-1].latency_ms:>6.1f} ms  {samples[-1].detail}"
                    )
                    continue

                previous_states[room.key] = state
                samples.append(
                    Sample(
                        ok=True,
                        latency_ms=(time.perf_counter() - start) * 1000,
                        detail=f"{room.slave} {label}: ok",
                    )
                )
                print(
                    f"  unit {room.slave:>2} {label:<22} OK   {samples[-1].latency_ms:>6.1f} ms"
                )

    return samples


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
    samples.append(
        Sample(
            ok=True,
            latency_ms=(time.perf_counter() - start) * 1000,
            detail=f"slave {room.slave} {label} target={target}",
        )
    )
    print(
        f"  unit {room.slave:>2} {label + '_target':<23} OK   {samples[-1].latency_ms:>6.1f} ms  target={target}"
    )


async def run_write_refresh(
    client: MeltemModbusClient,
    connection: ModbusConnection,
    rooms: list[RoomConfig],
    room_index: int,
    delta: int,
    settle_seconds: float,
    poll_interval: float,
    max_polls: int,
) -> list[Sample]:
    """Write one balanced airflow target, poll until applied, then restore."""

    room = _room_at(rooms, room_index)

    async def read_raw_snapshot() -> dict[str, object]:
        return {
            "mode_block_41120_41122": await read_raw(connection, room.slave, 41120, 3),
            "flow_block_41020_41021": await read_raw(connection, room.slave, 41020, 2),
        }

    baseline_state = await client.read_room_state(
        room,
        RoomState(),
        RefreshPlan.only(refresh_airflow=True),
    )
    baseline_flow = baseline_state.target_level or baseline_state.supply_air_flow
    if baseline_flow is None:
        raise RuntimeError(f"Could not determine baseline airflow for slave {room.slave}")

    target_flow = max(0, baseline_flow + delta)
    samples: list[Sample] = []

    async def poll_until(expected: int, phase: str) -> None:
        for attempt in range(1, max_polls + 1):
            start = time.perf_counter()
            state = await client.read_room_state(
                room,
                baseline_state,
                RefreshPlan.only(refresh_airflow=True),
            )
            airflow = state.target_level or state.supply_air_flow
            elapsed_ms = (time.perf_counter() - start) * 1000
            ok = airflow == expected
            detail = f"slave {room.slave} {phase} poll {attempt}: airflow={airflow}, expected={expected}"
            samples.append(Sample(ok=ok, latency_ms=elapsed_ms, detail=detail))
            status = "OK " if ok else "WAIT"
            print(
                f"  unit {room.slave:>2} {phase:<22} {status} {elapsed_ms:>6.1f} ms  airflow={airflow} expected={expected}"
            )
            if ok:
                return
            await asyncio.sleep(poll_interval)
        raise RuntimeError(
            f"{phase} did not reach expected airflow {expected} for slave {room.slave}"
        )

    print(
        f"write_refresh room_index={room_index} slave={room.slave} baseline={baseline_flow} target={target_flow}"
    )
    print(f"  raw before: {await read_raw_snapshot()}")

    await _timed_write(client, room, target_flow, "write", samples)
    print(f"  raw after write: {await read_raw_snapshot()}")
    await asyncio.sleep(settle_seconds)
    await poll_until(target_flow, "post_write")

    await _timed_write(client, room, baseline_flow, "restore", samples)
    print(f"  raw after restore: {await read_raw_snapshot()}")
    await asyncio.sleep(settle_seconds)
    await poll_until(baseline_flow, "post_restore")

    return samples


async def run_write_idle_check(
    client: MeltemModbusClient,
    rooms: list[RoomConfig],
    room_index: int,
    delta: int,
    idle_seconds: float,
) -> list[Sample]:
    """Write once, leave the gateway idle, then read back exactly once."""

    room = _room_at(rooms, room_index)

    async def read_airflow() -> int | None:
        state = await client.read_room_state(
            room,
            RoomState(),
            RefreshPlan.only(refresh_airflow=True),
        )
        return state.target_level or state.supply_air_flow

    baseline_flow = await read_airflow()
    if baseline_flow is None:
        raise RuntimeError(f"Could not determine baseline airflow for slave {room.slave}")

    target_flow = max(0, baseline_flow + delta)
    samples: list[Sample] = []

    print(
        f"write_idle_check room_index={room_index} slave={room.slave} baseline={baseline_flow} target={target_flow} idle={idle_seconds}s"
    )

    await _timed_write(client, room, target_flow, "write", samples)

    print(f"  idling for {idle_seconds:.1f}s without reads")
    await asyncio.sleep(idle_seconds)

    start = time.perf_counter()
    airflow_after_idle = await read_airflow()
    samples.append(
        Sample(
            ok=airflow_after_idle == target_flow,
            latency_ms=(time.perf_counter() - start) * 1000,
            detail=f"slave {room.slave} airflow_after_idle={airflow_after_idle}, expected={target_flow}",
        )
    )
    print(
        f"  unit {room.slave:>2} read_after_idle         {'OK ' if samples[-1].ok else 'ERR'}  {samples[-1].latency_ms:>6.1f} ms  airflow={airflow_after_idle} expected={target_flow}"
    )

    await _timed_write(client, room, baseline_flow, "restore", samples)

    print("  idling for 5.0s before final readback")
    await asyncio.sleep(5.0)

    start = time.perf_counter()
    restored_airflow = await read_airflow()
    samples.append(
        Sample(
            ok=restored_airflow == baseline_flow,
            latency_ms=(time.perf_counter() - start) * 1000,
            detail=f"slave {room.slave} restored_airflow={restored_airflow}, expected={baseline_flow}",
        )
    )
    print(
        f"  unit {room.slave:>2} read_after_restore      {'OK ' if samples[-1].ok else 'ERR'}  {samples[-1].latency_ms:>6.1f} ms  airflow={restored_airflow} expected={baseline_flow}"
    )

    return samples


async def run_write_observe(
    client: MeltemModbusClient,
    connection: ModbusConnection,
    rooms: list[RoomConfig],
    room_index: int,
    delta: int,
    observe_seconds: float,
    sample_interval: float,
) -> list[Sample]:
    """Write once, then observe several candidate readback registers over time."""

    room = _room_at(rooms, room_index)

    async def single(address: int) -> int | None:
        registers = await read_raw(connection, room.slave, address, 1)
        return None if registers is None else registers[0]

    async def snapshot() -> dict[str, object]:
        return {
            "flow_block_41020_41021": await read_raw(connection, room.slave, 41020, 2),
            "mode_41120": await single(41120),
            "current_level_41121": await single(41121),
            "extract_target_41122": await single(41122),
            "software_version_40004": await single(40004),
        }

    baseline_flow_block = (await snapshot()).get("flow_block_41020_41021")
    baseline_flow = None
    if isinstance(baseline_flow_block, list) and len(baseline_flow_block) >= 2:
        baseline_flow = baseline_flow_block[1]
    if baseline_flow is None:
        raise RuntimeError(f"Could not determine baseline airflow for slave {room.slave}")

    target_flow = max(0, baseline_flow + delta)
    samples: list[Sample] = []

    print(
        f"write_observe room_index={room_index} slave={room.slave} baseline={baseline_flow} target={target_flow} observe={observe_seconds}s interval={sample_interval}s"
    )
    print(f"  snapshot before: {await snapshot()}")

    await _timed_write(client, room, target_flow, "write", samples)

    checks = max(1, int(observe_seconds / sample_interval))
    for index in range(1, checks + 1):
        await asyncio.sleep(sample_interval)
        start = time.perf_counter()
        snap = await snapshot()
        samples.append(
            Sample(
                ok=True,
                latency_ms=(time.perf_counter() - start) * 1000,
                detail=f"observe_{index}: {snap}",
            )
        )
        print(
            f"  unit {room.slave:>2} observe_{index:<15} OK   {samples[-1].latency_ms:>6.1f} ms  {snap}"
        )

    await _timed_write(client, room, baseline_flow, "restore", samples)

    await asyncio.sleep(5.0)
    print(f"  snapshot after restore: {await snapshot()}")

    return samples


async def run_airflow_long_observe(
    client: MeltemModbusClient,
    connection: ModbusConnection,
    rooms: list[RoomConfig],
    room_index: int,
    target: int,
    observe_seconds: float,
    sample_interval: float,
    restore_target: int,
) -> list[Sample]:
    """Write one target, read only airflow sparsely for a longer period, then restore."""

    room = _room_at(rooms, room_index)

    async def read_flow_block() -> list[int] | None:
        return await read_raw(connection, room.slave, 41020, 2)

    samples: list[Sample] = []

    print(
        f"airflow_long_observe room_index={room_index} slave={room.slave} target={target} observe={observe_seconds}s interval={sample_interval}s restore={restore_target}"
    )
    print(f"  flow before: {await read_flow_block()}")

    await _timed_write(client, room, target, "write", samples)

    checks = max(1, int(observe_seconds / sample_interval))
    for index in range(1, checks + 1):
        await asyncio.sleep(sample_interval)
        start = time.perf_counter()
        flow_block = await read_flow_block()
        samples.append(
            Sample(
                ok=True,
                latency_ms=(time.perf_counter() - start) * 1000,
                detail=f"observe_{index}: {flow_block}",
            )
        )
        print(
            f"  unit {room.slave:>2} observe_{index:<15} OK   {samples[-1].latency_ms:>6.1f} ms  flow={flow_block}"
        )

    await _timed_write(client, room, restore_target, "restore", samples)

    await asyncio.sleep(10.0)
    print(f"  flow after restore: {await read_flow_block()}")

    return samples


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run integration-like polling loops against a Meltem gateway."
    )
    parser.add_argument("--port", default=DEFAULT_PORT)
    parser.add_argument("--gap", type=float, default=0.3)
    parser.add_argument("--cycles", type=int, default=2)
    parser.add_argument(
        "--mode",
        choices=[
            "full",
            "scheduler",
            "write_refresh",
            "write_idle_check",
            "write_observe",
            "airflow_long_observe",
        ],
        default="scheduler",
    )
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
    modbus_helpers_module.REQUEST_GAP_SECONDS = args.gap

    # The first request opens the link, after every unit asked for its
    # timeout, just like the shared connection in Home Assistant.
    connection = open_connection(args.port)
    try:
        try:
            rooms = await discover_rooms(connection, args.port, dict(args.profile))
        except MeltemConnectionError as err:
            print(f"ERROR: could not open serial connection on {args.port}: {err}")
            return 2
        print_rooms(rooms)
        print(f"mode: {args.mode}")
        print(f"gap: {args.gap}s")
        print(f"timeout: {FIXED_TIMEOUT}s")
        print(f"cycles: {args.cycles}")
        print()

        client = MeltemModbusClient(connection.for_unit, port=args.port)
        if args.mode == "full":
            samples = await run_full_cycles(client, rooms, args.cycles)
        elif args.mode == "airflow_long_observe":
            samples = await run_airflow_long_observe(
                client,
                connection,
                rooms,
                args.room_index,
                args.target,
                args.observe_seconds,
                args.sample_interval,
                args.restore_target,
            )
        elif args.mode == "write_observe":
            samples = await run_write_observe(
                client,
                connection,
                rooms,
                args.room_index,
                args.delta,
                args.observe_seconds,
                args.sample_interval,
            )
        elif args.mode == "write_idle_check":
            samples = await run_write_idle_check(
                client,
                rooms,
                args.room_index,
                args.delta,
                args.idle_seconds,
            )
        elif args.mode == "write_refresh":
            samples = await run_write_refresh(
                client,
                connection,
                rooms,
                args.room_index,
                args.delta,
                args.settle_seconds,
                args.poll_interval,
                args.max_polls,
            )
        else:
            samples = await run_scheduler_cycles(client, rooms, args.cycles)
    except (ModbusConnectionError, MeltemConnectionError) as err:
        print(f"ERROR: lost the serial connection on {args.port}: {err}")
        return 2
    finally:
        await connection.close()

    oks = [sample for sample in samples if sample.ok]
    latencies = [sample.latency_ms for sample in oks]
    print()
    print("summary:")
    print(f"  total requests: {len(samples)}")
    print(f"  successful:     {len(oks)}")
    print(f"  failed:         {len(samples) - len(oks)}")
    if latencies:
        print(f"  avg latency:    {statistics.mean(latencies):.1f} ms")
        if len(latencies) >= 20:
            print(f"  p95 latency:    {statistics.quantiles(latencies, n=20)[18]:.1f} ms")
        else:
            print(f"  max latency:    {max(latencies):.1f} ms")

    return 0 if len(oks) == len(samples) else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
