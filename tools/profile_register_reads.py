"""Profile Meltem register reads across all discovered units."""

from __future__ import annotations

import argparse
import asyncio
import statistics
import time
from collections import Counter
from dataclasses import dataclass

from modbus_connection import ModbusError, ModbusExceptionError

from tools._link import (
    GATEWAY_DEVICE_ID,
    discover_units,
    elapsed_ms,
    open_link,
    run,
    tool_parser,
)


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


def parse_args() -> argparse.Namespace:
    parser = tool_parser("Profile register reads across all Meltem units.")
    parser.add_argument("--gap", type=float, default=0.1)
    parser.add_argument("--cycles", type=int, default=2)
    return parser.parse_args()


async def main() -> int:
    args = parse_args()
    # Answered requests only, including exception responses.
    latencies: dict[str, list[float]] = {spec.label: [] for spec in SPECS}
    failures: Counter[str] = Counter()

    # The gap is slept explicitly outside the measured latency, so the link
    # itself adds no extra spacing.
    async with open_link(args.port, message_spacing=None) as link:
        units = await discover_units(link.for_unit(GATEWAY_DEVICE_ID), gap=args.gap)
        print(f"units: {units}")
        print(f"gap: {args.gap}s")
        print(f"cycles: {args.cycles}")
        print()

        for cycle in range(1, args.cycles + 1):
            print(f"cycle {cycle}/{args.cycles}")
            for unit in units:
                modbus_unit = link.for_unit(unit)
                for spec in SPECS:
                    start = time.perf_counter()
                    try:
                        registers = await modbus_unit.read_holding_registers(
                            spec.address, spec.count
                        )
                    except ModbusExceptionError as err:
                        # The unit answered, just with an exception code.
                        status, answered, detail = "ERR", True, f"{type(err).__name__}: {err}"
                    except ModbusError as err:
                        status, answered, detail = "EXC", False, f"{type(err).__name__}: {err}"
                    else:
                        status, answered, detail = "OK", True, str(registers)
                    latency_ms = elapsed_ms(start)
                    if answered:
                        latencies[spec.label].append(latency_ms)
                    if status != "OK":
                        failures[spec.label] += 1
                    print(
                        f"  unit {unit:>2} {spec.label:<24} {status:<4} "
                        f"{latency_ms:>6.1f} ms  {detail}"
                    )
                    await asyncio.sleep(args.gap)
            print()

    total = args.cycles * len(units)
    print("summary:")
    for spec in SPECS:
        answered_latencies = latencies[spec.label]
        avg = statistics.mean(answered_latencies) if answered_latencies else 0.0
        print(
            f"  {spec.label:<24} total={total:<3} failures={failures[spec.label]:<3} "
            f"avg_ms={avg:>6.1f}"
        )
    return 0


if __name__ == "__main__":
    run(main)
