"""Send single requests to one unit and print the answer or exception class with latency.

Without request options it reads current mode status at 41100 x3, followed by
the write-side mode registers for comparison.
"""

from __future__ import annotations

import argparse
import asyncio
import time
from collections import Counter
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from modbus_connection import ModbusError, ModbusUnit

from tools._link import elapsed_ms, open_link, parse_register_range, run, tool_parser

DEFAULT_READS = ("41100:3", "41120:5", "41120:2", "41121:1", "41122:1")
INPUT_PREFIX = "input:"


@dataclass(frozen=True)
class Request:
    label: str
    send: Callable[[ModbusUnit], Awaitable[object]]


@dataclass
class Tally:
    ok: int = 0
    errors: Counter[str] = field(default_factory=Counter)
    total_ms: float = 0.0


def parse_read(value: str) -> Request:
    """Parse ``start:count`` or ``start-end``; an ``input:`` prefix reads with 0x04."""

    is_input = value.startswith(INPUT_PREFIX)
    start, end = parse_register_range(value.removeprefix(INPUT_PREFIX))
    count = end - start + 1
    if is_input:
        return Request(
            f"in {start} x{count}",
            lambda unit: unit.read_input_registers(start, count),
        )
    return Request(f"{start} x{count}", lambda unit: unit.read_holding_registers(start, count))


def parse_args() -> argparse.Namespace:
    parser = tool_parser(
        "Send single requests to one Meltem unit and time the answers.", slave=True
    )
    parser.add_argument(
        "--read",
        dest="reads",
        action="append",
        type=parse_read,
        default=[],
        help=(
            "Holding registers as start:count or start-end, input registers with an "
            "'input:' prefix. Repeatable, sent in this order."
        ),
    )
    parser.add_argument(
        "--diagnostics",
        action="store_true",
        help="Function 0x08 sub-function 0 (return query data) with 0x1234.",
    )
    parser.add_argument(
        "--server-id", action="store_true", help="Function 0x11 (report server ID)."
    )
    parser.add_argument(
        "--device-id",
        action="store_true",
        help="Function 0x2B/0x0E (read device identification).",
    )
    parser.add_argument("--rounds", type=int, default=1)
    parser.add_argument(
        "--interval",
        type=float,
        default=0.0,
        help="Seconds from the start of one round to the start of the next.",
    )
    return parser.parse_args()


def build_requests(args: argparse.Namespace) -> list[Request]:
    requests = list(args.reads)
    if args.diagnostics:
        requests.append(Request("diag 0x1234", lambda unit: unit.diagnostics(0, 0x1234)))
    if args.server_id:
        requests.append(Request("server id", lambda unit: unit.report_server_id()))
    if args.device_id:
        requests.append(Request("device id", lambda unit: unit.read_device_identification()))
    return requests or [parse_read(value) for value in DEFAULT_READS]


async def main() -> int:
    args = parse_args()
    requests = build_requests(args)
    tallies = {request.label: Tally() for request in requests}

    async with open_link(args.port) as link:
        unit = link.for_unit(args.slave)
        for round_number in range(1, args.rounds + 1):
            round_start = time.monotonic()
            if args.rounds > 1:
                print(f"round {round_number}/{args.rounds}")
            for request in requests:
                tally = tallies[request.label]
                start = time.perf_counter()
                try:
                    result: object = await request.send(unit)
                except ModbusError as err:
                    result = f"{type(err).__name__}: {err}"
                    tally.errors[type(err).__name__] += 1
                else:
                    tally.ok += 1
                    if isinstance(result, int):
                        result = f"{result} (0x{result:04X})"
                latency_ms = elapsed_ms(start)
                tally.total_ms += latency_ms
                print(f"  {request.label:<14} {latency_ms:7.1f} ms  {result}")
            if round_number < args.rounds:
                await asyncio.sleep(max(0.0, args.interval - (time.monotonic() - round_start)))

    print()
    print("summary:")
    for label, tally in tallies.items():
        errors = " ".join(f"{name}={count}" for name, count in tally.errors.items())
        print(
            f"  {label:<14} ok={tally.ok}/{args.rounds}  "
            f"total={tally.total_ms:.1f} ms  {errors}".rstrip()
        )
    return 0


if __name__ == "__main__":
    run(main)
