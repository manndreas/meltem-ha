#!/usr/bin/env python3
"""Watch Meltem register ranges and print only changes.

This is intended for app-vs-Modbus comparison work: start the watcher, change
something in the Meltem app, and look for register ranges that moved.
"""

from __future__ import annotations

import argparse
import asyncio
from dataclasses import dataclass
from datetime import datetime

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
DEFAULT_INTERVAL = 1.0
REQUEST_GAP_SECONDS = 0.1


@dataclass(frozen=True)
class RegisterRange:
    start: int
    count: int

    @property
    def label(self) -> str:
        end = self.start + self.count - 1
        return f"{self.start}..{end}"


DEFAULT_RANGES: tuple[RegisterRange, ...] = (
    RegisterRange(41016, 6),
    RegisterRange(41020, 2),
    RegisterRange(41120, 3),
    RegisterRange(41132, 1),
    RegisterRange(42000, 10),
)


def parse_range(value: str) -> RegisterRange:
    """Parse one CLI range in either start:count or start-end form."""

    if ":" in value:
        start_s, count_s = value.split(":", 1)
        start = int(start_s)
        count = int(count_s)
        if count <= 0:
            raise argparse.ArgumentTypeError("count must be > 0")
        return RegisterRange(start, count)

    if "-" in value:
        start_s, end_s = value.split("-", 1)
        start = int(start_s)
        end = int(end_s)
        if end < start:
            raise argparse.ArgumentTypeError("end must be >= start")
        return RegisterRange(start, end - start + 1)

    raise argparse.ArgumentTypeError("range must use start:count or start-end")


async def read_range(
    unit: ModbusUnit,
    *,
    register_range: RegisterRange,
) -> tuple[int, ...] | None:
    """Read one register range and return a stable tuple."""

    try:
        registers = await unit.read_holding_registers(
            register_range.start, register_range.count
        )
    except ModbusConnectionError:
        raise
    except ModbusError:
        return None

    return tuple(int(value) for value in registers[: register_range.count])


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Watch Meltem holding-register ranges and print changes only."
    )
    parser.add_argument("--port", default=DEFAULT_PORT)
    parser.add_argument("--slave", type=int, required=True)
    parser.add_argument("--interval", type=float, default=DEFAULT_INTERVAL)
    parser.add_argument(
        "--range",
        dest="ranges",
        action="append",
        type=parse_range,
        help="Register range to watch, e.g. 41120:3 or 42000-42009. Repeatable.",
    )
    return parser.parse_args()


async def main() -> int:
    args = parse_args()
    ranges = tuple(args.ranges) if args.ranges else DEFAULT_RANGES

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

    unit = connection.for_unit(args.slave)
    previous: dict[RegisterRange, tuple[int, ...] | None] = {}

    try:
        print(f"watching slave {args.slave} on {args.port}")
        print("ranges:")
        for register_range in ranges:
            print(f"  {register_range.label}")
        print("press Ctrl+C to stop")
        print()

        while True:
            for register_range in ranges:
                current = await read_range(unit, register_range=register_range)
                if previous.get(register_range) == current:
                    continue

                previous[register_range] = current
                stamp = datetime.now().strftime("%H:%M:%S")
                if current is None:
                    print(f"{stamp}  {register_range.label:<16} unavailable", flush=True)
                else:
                    print(
                        f"{stamp}  {register_range.label:<16} {list(current)}",
                        flush=True,
                    )

            await asyncio.sleep(args.interval)
    finally:
        await connection.close()


if __name__ == "__main__":
    try:
        raise SystemExit(asyncio.run(main()))
    except KeyboardInterrupt:
        raise SystemExit(0) from None
