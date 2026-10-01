#!/usr/bin/env python3
"""Read register ranges once and print only differences to a saved baseline."""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from modbus_connection import (
    ModbusConnectionError,
    ModbusError,
    ModbusSerialParams,
    ModbusUnit,
)
from modbus_connection.tmodbus import ModbusConnection


FIXED_BAUDRATE = 19200
FIXED_BYTESIZE = 8
FIXED_PARITY = "E"
FIXED_STOPBITS = 1
FIXED_TIMEOUT = 0.8
DEFAULT_PORT = "/dev/ttyACM0"
REQUEST_GAP_SECONDS = 0.1
MAX_REGISTERS_PER_READ = 120


def parse_range(value: str) -> tuple[int, int]:
    """Parse one range in start-end form."""

    if "-" not in value:
        raise argparse.ArgumentTypeError("range must use start-end")
    start_s, end_s = value.split("-", 1)
    start = int(start_s)
    end = int(end_s)
    if end < start:
        raise argparse.ArgumentTypeError("end must be >= start")
    return start, end


async def read_range(
    unit: ModbusUnit,
    *,
    start: int,
    end: int,
) -> dict[int, int | None]:
    """Read one contiguous range as individual register values."""

    values: dict[int, int | None] = {address: None for address in range(start, end + 1)}

    chunk_start = start
    while chunk_start <= end:
        chunk_end = min(end, chunk_start + MAX_REGISTERS_PER_READ - 1)
        count = chunk_end - chunk_start + 1
        try:
            registers = await unit.read_holding_registers(chunk_start, count)
        except ModbusConnectionError:
            raise
        except ModbusError:
            chunk_start = chunk_end + 1
            continue

        for index, value in enumerate(registers[:count]):
            values[chunk_start + index] = int(value)
        chunk_start = chunk_end + 1
    return values


async def read_snapshot(
    unit: ModbusUnit,
    *,
    ranges: list[tuple[int, int]],
) -> dict[int, int | None]:
    """Read all configured ranges into one address->value map."""

    snapshot: dict[int, int | None] = {}
    for start, end in ranges:
        snapshot.update(await read_range(unit, start=start, end=end))
    return snapshot


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Capture or diff Meltem register snapshots."
    )
    parser.add_argument("--port", default=DEFAULT_PORT)
    parser.add_argument("--slave", type=int, required=True)
    parser.add_argument(
        "--range",
        dest="ranges",
        action="append",
        type=parse_range,
        required=True,
        help="Register range in start-end form. Repeatable.",
    )
    parser.add_argument(
        "--baseline",
        required=True,
        help="Path to JSON snapshot file.",
    )
    parser.add_argument(
        "--capture",
        action="store_true",
        help="Capture a fresh baseline instead of diffing against an existing one.",
    )
    return parser.parse_args()


async def main() -> int:
    args = parse_args()
    baseline_path = Path(args.baseline)

    connection = ModbusConnection(
        ModbusSerialParams(
            device=args.port,
            baudrate=FIXED_BAUDRATE,
            bytesize=FIXED_BYTESIZE,
            parity=FIXED_PARITY,
            stopbits=FIXED_STOPBITS,
        ),
        timeout=FIXED_TIMEOUT,
        message_spacing=REQUEST_GAP_SECONDS,
    )
    try:
        await connection.connect()
    except ModbusConnectionError as err:
        print(f"ERROR: could not open serial connection on {args.port}: {err}")
        return 2

    try:
        snapshot = await read_snapshot(
            connection.for_unit(args.slave),
            ranges=list(args.ranges),
        )

        if args.capture:
            baseline_path.write_text(
                json.dumps(snapshot, indent=2, sort_keys=True),
                encoding="utf-8",
            )
            print(f"captured baseline to {baseline_path}")
            return 0

        baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
        changed = []
        for address, value in sorted(snapshot.items()):
            baseline_value = baseline.get(str(address))
            if baseline_value != value:
                changed.append((address, baseline_value, value))

        if not changed:
            print("no changes")
            return 0

        for address, before, after in changed:
            print(f"{address}: {before} -> {after}")
        return 0
    except ModbusConnectionError as err:
        print(f"ERROR: lost the serial connection on {args.port}: {err}")
        return 2
    finally:
        await connection.close()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
