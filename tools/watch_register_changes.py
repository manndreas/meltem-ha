"""Watch Meltem register ranges and print only changes.

This is intended for app-vs-Modbus comparison work: start the watcher, change
something in the Meltem app, and look for register ranges that moved.
"""

from __future__ import annotations

import argparse
import asyncio
from dataclasses import dataclass
from datetime import datetime

from tools._link import open_link, parse_register_range, read_or_none, run, tool_parser

DEFAULT_INTERVAL = 1.0


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

    start, end = parse_register_range(value)
    return RegisterRange(start, end - start + 1)


def parse_args() -> argparse.Namespace:
    parser = tool_parser("Watch Meltem holding-register ranges and print changes only.", slave=True)
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
    previous: dict[RegisterRange, list[int] | None] = {}

    async with open_link(args.port) as link:
        unit = link.for_unit(args.slave)
        print(f"watching slave {args.slave} on {args.port}")
        print("ranges:")
        for register_range in ranges:
            print(f"  {register_range.label}")
        print("press Ctrl+C to stop")
        print()

        while True:
            for register_range in ranges:
                current = await read_or_none(unit, register_range.start, register_range.count)
                if register_range in previous and previous[register_range] == current:
                    continue

                previous[register_range] = current
                stamp = datetime.now().strftime("%H:%M:%S")
                shown = "unavailable" if current is None else current
                print(f"{stamp}  {register_range.label:<16} {shown}", flush=True)

            await asyncio.sleep(args.interval)


if __name__ == "__main__":
    run(main)
