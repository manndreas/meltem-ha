#!/usr/bin/env python3
"""Run integration-like Meltem polling loops against a local gateway."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import importlib.util
from pathlib import Path
import statistics
import sys
import time

from pymodbus.client import ModbusSerialClient

DEFAULT_PORT = "/dev/ttyACM0"
DEFAULT_GATEWAY_DEVICE_ID = 1
REPO_ROOT = Path(__file__).resolve().parent.parent


def _install_homeassistant_stub() -> None:
    """Provide the tiny Home Assistant surface needed by the loaded modules."""

    homeassistant_module = type(sys)("homeassistant")
    const_module = type(sys)("homeassistant.const")

    class Platform:
        SENSOR = "sensor"
        BINARY_SENSOR = "binary_sensor"
        NUMBER = "number"
        SELECT = "select"

    const_module.Platform = Platform
    homeassistant_module.const = const_module
    sys.modules.setdefault("homeassistant", homeassistant_module)
    sys.modules.setdefault("homeassistant.const", const_module)


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

FIXED_BAUDRATE = const_module.FIXED_BAUDRATE
FIXED_BYTESIZE = const_module.FIXED_BYTESIZE
FIXED_PARITY = const_module.FIXED_PARITY
FIXED_STOPBITS = const_module.FIXED_STOPBITS
FIXED_TIMEOUT = const_module.FIXED_TIMEOUT

RefreshPlan = models_module.RefreshPlan
RoomConfig = models_module.RoomConfig
RoomState = models_module.RoomState
MeltemModbusClient = modbus_client_module.MeltemModbusClient
SerialSettings = modbus_helpers_module.SerialSettings
detect_slave_details = modbus_helpers_module.detect_slave_details
discover_gateway_nodes = modbus_helpers_module.discover_gateway_nodes
build_client = modbus_helpers_module.build_client


def _install_pymodbus_compat() -> None:
    """Accept both `device_id=` and `slave=` on this local pymodbus version."""

    original_read = ModbusSerialClient.read_holding_registers
    original_write = ModbusSerialClient.write_register

    def compat_read(self, *args, **kwargs):
        if "device_id" in kwargs and "slave" not in kwargs:
            kwargs["slave"] = kwargs.pop("device_id")
        return original_read(self, *args, **kwargs)

    def compat_write(self, *args, **kwargs):
        if "device_id" in kwargs and "slave" not in kwargs:
            kwargs["slave"] = kwargs.pop("device_id")
        return original_write(self, *args, **kwargs)

    ModbusSerialClient.read_holding_registers = compat_read
    ModbusSerialClient.write_register = compat_write


_install_pymodbus_compat()


@dataclass
class Sample:
    """One measured integration-like poll."""

    ok: bool
    latency_ms: float
    detail: str


def _profile_from_detected_suffix(suffix: str) -> str:
    mapping = {
        "plain": "ii_plain",
        "f": "ii_f",
        "fc": "ii_fc",
        "fc_voc": "ii_fc_voc",
    }
    return mapping.get(suffix, "ii_plain")


def discover_rooms(settings: SerialSettings) -> list[RoomConfig]:
    """Discover configured rooms and probe their supported keys."""

    client = build_client(settings)
    try:
        if not client.connect():
            raise RuntimeError(f"Could not open serial connection on {settings.port}")
        slaves = discover_gateway_nodes(client, settings.port, start=2, end=16)
    finally:
        client.close()

    rooms: list[RoomConfig] = []
    for index, slave in enumerate(slaves, start=1):
        detected_profile, preview, supported_entity_keys = detect_slave_details(
            settings,
            slave,
        )
        rooms.append(
            RoomConfig(
                key=f"unit_{index}",
                name=f"Unit {index}",
                slave=slave,
                profile=_profile_from_detected_suffix(detected_profile),
                preview=preview,
                supported_entity_keys=frozenset(supported_entity_keys),
            )
        )
    return rooms


def run_full_cycles(
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
                state = client.read_room_state(
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


def run_scheduler_cycles(
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
                    state = client.read_room_state(
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


def run_write_refresh(
    client: MeltemModbusClient,
    rooms: list[RoomConfig],
    room_index: int,
    delta: int,
    settle_seconds: float,
    poll_interval: float,
    max_polls: int,
) -> list[Sample]:
    """Write one balanced airflow target, poll until applied, then restore."""

    if room_index < 1 or room_index > len(rooms):
        raise RuntimeError(f"room_index must be between 1 and {len(rooms)}")

    room = rooms[room_index - 1]

    def read_raw_snapshot() -> dict[str, object]:
        modbus = client._ensure_client()
        mode_block = client._read_optional_uint16_block(modbus, room.slave, 41120, 3)
        flow_block = client._read_optional_uint16_block(modbus, room.slave, 41020, 2)
        return {
            "mode_block_41120_41122": mode_block,
            "flow_block_41020_41021": flow_block,
        }

    baseline_state = client.read_room_state(
        room,
        RoomState(),
        RefreshPlan.only(refresh_airflow=True),
    )
    baseline_flow = baseline_state.target_level or baseline_state.supply_air_flow
    if baseline_flow is None:
        raise RuntimeError(f"Could not determine baseline airflow for slave {room.slave}")

    target_flow = max(0, baseline_flow + delta)
    samples: list[Sample] = []

    def poll_until(expected: int, phase: str) -> None:
        for attempt in range(1, max_polls + 1):
            start = time.perf_counter()
            state = client.read_room_state(
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
            time.sleep(poll_interval)
        raise RuntimeError(
            f"{phase} did not reach expected airflow {expected} for slave {room.slave}"
        )

    print(
        f"write_refresh room_index={room_index} slave={room.slave} baseline={baseline_flow} target={target_flow}"
    )
    print(f"  raw before: {read_raw_snapshot()}")

    start = time.perf_counter()
    client.write_level(room, target_flow)
    samples.append(
        Sample(
            ok=True,
            latency_ms=(time.perf_counter() - start) * 1000,
            detail=f"slave {room.slave} write target={target_flow}",
        )
    )
    print(
        f"  unit {room.slave:>2} write_target            OK   {samples[-1].latency_ms:>6.1f} ms  target={target_flow}"
    )
    print(f"  raw after write: {read_raw_snapshot()}")
    time.sleep(settle_seconds)
    poll_until(target_flow, "post_write")

    start = time.perf_counter()
    client.write_level(room, baseline_flow)
    samples.append(
        Sample(
            ok=True,
            latency_ms=(time.perf_counter() - start) * 1000,
            detail=f"slave {room.slave} restore target={baseline_flow}",
        )
    )
    print(
        f"  unit {room.slave:>2} restore_target          OK   {samples[-1].latency_ms:>6.1f} ms  target={baseline_flow}"
    )
    print(f"  raw after restore: {read_raw_snapshot()}")
    time.sleep(settle_seconds)
    poll_until(baseline_flow, "post_restore")

    return samples


def run_write_idle_check(
    client: MeltemModbusClient,
    rooms: list[RoomConfig],
    room_index: int,
    delta: int,
    idle_seconds: float,
) -> list[Sample]:
    """Write once, leave the gateway idle, then read back exactly once."""

    if room_index < 1 or room_index > len(rooms):
        raise RuntimeError(f"room_index must be between 1 and {len(rooms)}")

    room = rooms[room_index - 1]

    def read_airflow() -> int | None:
        state = client.read_room_state(
            room,
            RoomState(),
            RefreshPlan.only(refresh_airflow=True),
        )
        return state.target_level or state.supply_air_flow

    baseline_flow = read_airflow()
    if baseline_flow is None:
        raise RuntimeError(f"Could not determine baseline airflow for slave {room.slave}")

    target_flow = max(0, baseline_flow + delta)
    samples: list[Sample] = []

    print(
        f"write_idle_check room_index={room_index} slave={room.slave} baseline={baseline_flow} target={target_flow} idle={idle_seconds}s"
    )

    start = time.perf_counter()
    client.write_level(room, target_flow)
    samples.append(
        Sample(
            ok=True,
            latency_ms=(time.perf_counter() - start) * 1000,
            detail=f"slave {room.slave} write target={target_flow}",
        )
    )
    print(
        f"  unit {room.slave:>2} write_target            OK   {samples[-1].latency_ms:>6.1f} ms  target={target_flow}"
    )

    print(f"  idling for {idle_seconds:.1f}s without reads")
    time.sleep(idle_seconds)

    start = time.perf_counter()
    airflow_after_idle = read_airflow()
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

    start = time.perf_counter()
    client.write_level(room, baseline_flow)
    samples.append(
        Sample(
            ok=True,
            latency_ms=(time.perf_counter() - start) * 1000,
            detail=f"slave {room.slave} restore target={baseline_flow}",
        )
    )
    print(
        f"  unit {room.slave:>2} restore_target          OK   {samples[-1].latency_ms:>6.1f} ms  target={baseline_flow}"
    )

    print("  idling for 5.0s before final readback")
    time.sleep(5.0)

    start = time.perf_counter()
    restored_airflow = read_airflow()
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


def run_write_observe(
    client: MeltemModbusClient,
    rooms: list[RoomConfig],
    room_index: int,
    delta: int,
    observe_seconds: float,
    sample_interval: float,
) -> list[Sample]:
    """Write once, then observe several candidate readback registers over time."""

    if room_index < 1 or room_index > len(rooms):
        raise RuntimeError(f"room_index must be between 1 and {len(rooms)}")

    room = rooms[room_index - 1]

    def snapshot() -> dict[str, object]:
        modbus = client._ensure_client()
        return {
            "flow_block_41020_41021": client._read_optional_uint16_block(
                modbus, room.slave, 41020, 2
            ),
            "mode_41120": client._read_optional_uint16(modbus, room.slave, 41120),
            "current_level_41121": client._read_optional_uint16(
                modbus, room.slave, 41121
            ),
            "extract_target_41122": client._read_optional_uint16(
                modbus, room.slave, 41122
            ),
            "software_version_40004": client._read_optional_uint16(
                modbus, room.slave, 40004
            ),
        }

    baseline_flow_block = snapshot().get("flow_block_41020_41021")
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
    print(f"  snapshot before: {snapshot()}")

    start = time.perf_counter()
    client.write_level(room, target_flow)
    samples.append(
        Sample(
            ok=True,
            latency_ms=(time.perf_counter() - start) * 1000,
            detail=f"slave {room.slave} write target={target_flow}",
        )
    )
    print(
        f"  unit {room.slave:>2} write_target            OK   {samples[-1].latency_ms:>6.1f} ms  target={target_flow}"
    )

    checks = max(1, int(observe_seconds / sample_interval))
    for index in range(1, checks + 1):
        time.sleep(sample_interval)
        start = time.perf_counter()
        snap = snapshot()
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

    start = time.perf_counter()
    client.write_level(room, baseline_flow)
    samples.append(
        Sample(
            ok=True,
            latency_ms=(time.perf_counter() - start) * 1000,
            detail=f"slave {room.slave} restore target={baseline_flow}",
        )
    )
    print(
        f"  unit {room.slave:>2} restore_target          OK   {samples[-1].latency_ms:>6.1f} ms  target={baseline_flow}"
    )

    time.sleep(5.0)
    print(f"  snapshot after restore: {snapshot()}")

    return samples


def run_airflow_long_observe(
    client: MeltemModbusClient,
    rooms: list[RoomConfig],
    room_index: int,
    target: int,
    observe_seconds: float,
    sample_interval: float,
    restore_target: int,
) -> list[Sample]:
    """Write one target, read only airflow sparsely for a longer period, then restore."""

    if room_index < 1 or room_index > len(rooms):
        raise RuntimeError(f"room_index must be between 1 and {len(rooms)}")

    room = rooms[room_index - 1]

    def read_flow_block() -> list[int] | None:
        modbus = client._ensure_client()
        return client._read_optional_uint16_block(modbus, room.slave, 41020, 2)

    samples: list[Sample] = []

    print(
        f"airflow_long_observe room_index={room_index} slave={room.slave} target={target} observe={observe_seconds}s interval={sample_interval}s restore={restore_target}"
    )
    print(f"  flow before: {read_flow_block()}")

    start = time.perf_counter()
    client.write_level(room, target)
    samples.append(
        Sample(
            ok=True,
            latency_ms=(time.perf_counter() - start) * 1000,
            detail=f"slave {room.slave} write target={target}",
        )
    )
    print(
        f"  unit {room.slave:>2} write_target            OK   {samples[-1].latency_ms:>6.1f} ms  target={target}"
    )

    checks = max(1, int(observe_seconds / sample_interval))
    for index in range(1, checks + 1):
        time.sleep(sample_interval)
        start = time.perf_counter()
        flow_block = read_flow_block()
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

    start = time.perf_counter()
    client.write_level(room, restore_target)
    samples.append(
        Sample(
            ok=True,
            latency_ms=(time.perf_counter() - start) * 1000,
            detail=f"slave {room.slave} restore target={restore_target}",
        )
    )
    print(
        f"  unit {room.slave:>2} restore_target          OK   {samples[-1].latency_ms:>6.1f} ms  target={restore_target}"
    )

    time.sleep(10.0)
    print(f"  flow after restore: {read_flow_block()}")

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
    parser.add_argument("--room-index", type=int, default=3)
    parser.add_argument("--delta", type=int, default=4)
    parser.add_argument("--settle-seconds", type=float, default=1.5)
    parser.add_argument("--poll-interval", type=float, default=1.0)
    parser.add_argument("--max-polls", type=int, default=8)
    parser.add_argument("--idle-seconds", type=float, default=12.0)
    parser.add_argument("--observe-seconds", type=float, default=20.0)
    parser.add_argument("--sample-interval", type=float, default=2.0)
    parser.add_argument("--target", type=int, default=10)
    parser.add_argument("--restore-target", type=int, default=60)
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    const_module.REQUEST_GAP_SECONDS = args.gap
    modbus_client_module.REQUEST_GAP_SECONDS = args.gap
    modbus_helpers_module.REQUEST_GAP_SECONDS = args.gap

    settings = SerialSettings(
        port=args.port,
        baudrate=FIXED_BAUDRATE,
        bytesize=FIXED_BYTESIZE,
        parity=FIXED_PARITY,
        stopbits=FIXED_STOPBITS,
        timeout=float(FIXED_TIMEOUT),
    )

    rooms = discover_rooms(settings)
    print(f"rooms: {[room.slave for room in rooms]}")
    print(f"mode: {args.mode}")
    print(f"gap: {args.gap}s")
    print(f"cycles: {args.cycles}")
    print()

    client = MeltemModbusClient(settings)
    try:
        if args.mode == "full":
            samples = run_full_cycles(client, rooms, args.cycles)
        elif args.mode == "airflow_long_observe":
            samples = run_airflow_long_observe(
                client,
                rooms,
                args.room_index,
                args.target,
                args.observe_seconds,
                args.sample_interval,
                args.restore_target,
            )
        elif args.mode == "write_observe":
            samples = run_write_observe(
                client,
                rooms,
                args.room_index,
                args.delta,
                args.observe_seconds,
                args.sample_interval,
            )
        elif args.mode == "write_idle_check":
            samples = run_write_idle_check(
                client,
                rooms,
                args.room_index,
                args.delta,
                args.idle_seconds,
            )
        elif args.mode == "write_refresh":
            samples = run_write_refresh(
                client,
                rooms,
                args.room_index,
                args.delta,
                args.settle_seconds,
                args.poll_interval,
                args.max_polls,
            )
        else:
            samples = run_scheduler_cycles(client, rooms, args.cycles)
    finally:
        client.close()

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
    raise SystemExit(main())
