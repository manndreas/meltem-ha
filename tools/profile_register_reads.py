#!/usr/bin/env python3
"""Profile Meltem register reads across all discovered units."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import statistics
import time

from pymodbus.client import ModbusSerialClient


FIXED_BAUDRATE = 19200
FIXED_BYTESIZE = 8
FIXED_PARITY = "E"
FIXED_STOPBITS = 1
FIXED_TIMEOUT = 0.8
DEFAULT_PORT = "/dev/ttyACM0"
DEFAULT_GATEWAY_DEVICE_ID = 1

REGISTER_GATEWAY_NUMBER_OF_NODES = 43901
REGISTER_GATEWAY_NODE_ADDRESS_1 = 43902


@dataclass(frozen=True)
class ReadSpec:
    label: str
    address: int
    count: int


SPECS: tuple[ReadSpec, ...] = (
    ReadSpec("flows_41020_41021", 41020, 2),
    ReadSpec("mode_41120", 41120, 1),
    ReadSpec("current_level_41121", 41121, 1),
    ReadSpec("extract_target_41122", 41122, 1),
    ReadSpec("mode_block_41120_41122", 41120, 3),
    ReadSpec("status_41016_41018", 41016, 3),
    ReadSpec("temps_41002_41005", 41002, 4),
    ReadSpec("supply_temp_41009_41010", 41009, 2),
    ReadSpec("control_42000_42005", 42000, 6),
    ReadSpec("software_40004", 40004, 1),
)


def compat_read(client: ModbusSerialClient, *, slave: int, address: int, count: int):
    try:
        return client.read_holding_registers(
            address=address,
            count=count,
            device_id=slave,
        )
    except TypeError:
        return client.read_holding_registers(
            address=address,
            count=count,
            slave=slave,
        )


def discover_units(client: ModbusSerialClient, *, gap: float) -> list[int]:
    count_response = compat_read(
        client,
        slave=DEFAULT_GATEWAY_DEVICE_ID,
        address=REGISTER_GATEWAY_NUMBER_OF_NODES,
        count=1,
    )
    time.sleep(gap)
    if (
        count_response is None
        or count_response.isError()
        or not getattr(count_response, "registers", None)
    ):
        raise RuntimeError(f"failed to read bridge node count: {count_response}")

    node_count = int(count_response.registers[0])
    addresses_response = compat_read(
        client,
        slave=DEFAULT_GATEWAY_DEVICE_ID,
        address=REGISTER_GATEWAY_NODE_ADDRESS_1,
        count=max(1, min(32, node_count)),
    )
    time.sleep(gap)
    if (
        addresses_response is None
        or addresses_response.isError()
        or not getattr(addresses_response, "registers", None)
    ):
        raise RuntimeError(f"failed to read bridge node addresses: {addresses_response}")

    return [int(value) for value in addresses_response.registers if int(value) != 0]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Profile register reads across all Meltem units."
    )
    parser.add_argument("--port", default=DEFAULT_PORT)
    parser.add_argument("--gap", type=float, default=0.1)
    parser.add_argument("--cycles", type=int, default=2)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    client = ModbusSerialClient(
        port=args.port,
        baudrate=FIXED_BAUDRATE,
        bytesize=FIXED_BYTESIZE,
        parity=FIXED_PARITY,
        stopbits=FIXED_STOPBITS,
        timeout=FIXED_TIMEOUT,
    )
    if not client.connect():
        print(f"ERROR: could not open serial connection on {args.port}")
        return 2

    try:
        units = discover_units(client, gap=args.gap)
        print(f"units: {units}")
        print(f"gap: {args.gap}s")
        print(f"cycles: {args.cycles}")
        print()

        latencies_by_label: dict[str, list[float]] = {spec.label: [] for spec in SPECS}
        failures_by_label: dict[str, int] = {spec.label: 0 for spec in SPECS}

        for cycle in range(1, args.cycles + 1):
            print(f"cycle {cycle}/{args.cycles}")
            for unit in units:
                for spec in SPECS:
                    start = time.perf_counter()
                    try:
                        response = compat_read(
                            client,
                            slave=unit,
                            address=spec.address,
                            count=spec.count,
                        )
                    except Exception as err:
                        elapsed_ms = (time.perf_counter() - start) * 1000
                        failures_by_label[spec.label] += 1
                        print(
                            f"  unit {unit:>2} {spec.label:<24} EXC  {elapsed_ms:>6.1f} ms  {type(err).__name__}: {err}"
                        )
                        time.sleep(args.gap)
                        continue

                    elapsed_ms = (time.perf_counter() - start) * 1000
                    latencies_by_label[spec.label].append(elapsed_ms)
                    if response is None:
                        failures_by_label[spec.label] += 1
                        print(
                            f"  unit {unit:>2} {spec.label:<24} NONE {elapsed_ms:>6.1f} ms"
                        )
                    elif response.isError():
                        failures_by_label[spec.label] += 1
                        print(
                            f"  unit {unit:>2} {spec.label:<24} ERR  {elapsed_ms:>6.1f} ms  {response}"
                        )
                    else:
                        registers = getattr(response, "registers", None)
                        print(
                            f"  unit {unit:>2} {spec.label:<24} OK   {elapsed_ms:>6.1f} ms  {registers}"
                        )
                    time.sleep(args.gap)
            print()

        print("summary:")
        for spec in SPECS:
            latencies = latencies_by_label[spec.label]
            failures = failures_by_label[spec.label]
            total = len(latencies) + failures
            avg = statistics.mean(latencies) if latencies else 0.0
            print(
                f"  {spec.label:<24} total={total:<3} failures={failures:<3} avg_ms={avg:>6.1f}"
            )

        return 0
    finally:
        client.close()


if __name__ == "__main__":
    raise SystemExit(main())
