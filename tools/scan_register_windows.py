#!/usr/bin/env python3
"""Scan Meltem register windows and summarize which ranges are readable.

This helper is intended for reverse-engineering work. It does not diff values;
it builds a quick "heatmap" of which address windows respond at all via Modbus
function 0x03 (holding registers) and/or 0x04 (input registers).
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import time

from pymodbus.client import ModbusSerialClient


FIXED_BAUDRATE = 19200
FIXED_BYTESIZE = 8
FIXED_PARITY = "E"
FIXED_STOPBITS = 1
FIXED_TIMEOUT = 0.8
DEFAULT_PORT = "/dev/ttyACM0"
REQUEST_GAP_SECONDS = 0.1
MAX_REGISTERS_PER_READ = 120


@dataclass(frozen=True)
class WindowResult:
    start: int
    end: int
    function_name: str
    status: str
    values_seen: int
    sample: tuple[int, ...]


def compat_read_holding(
    client: ModbusSerialClient,
    *,
    slave: int,
    address: int,
    count: int,
):
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


def compat_read_input(
    client: ModbusSerialClient,
    *,
    slave: int,
    address: int,
    count: int,
):
    try:
        return client.read_input_registers(
            address=address,
            count=count,
            device_id=slave,
        )
    except TypeError:
        return client.read_input_registers(
            address=address,
            count=count,
            slave=slave,
        )


def parse_function_mode(value: str) -> str:
    normalized = value.strip().lower()
    if normalized not in {"holding", "input", "both"}:
        raise argparse.ArgumentTypeError("function must be holding, input, or both")
    return normalized


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Scan readable Meltem register windows."
    )
    parser.add_argument("--port", default=DEFAULT_PORT)
    parser.add_argument("--slave", type=int, required=True)
    parser.add_argument("--start", type=int, required=True)
    parser.add_argument("--end", type=int, required=True)
    parser.add_argument(
        "--window",
        type=int,
        default=120,
        help="Registers to probe per request window. Max 120 recommended.",
    )
    parser.add_argument(
        "--step",
        type=int,
        help="Address step between windows. Defaults to --window.",
    )
    parser.add_argument(
        "--function",
        type=parse_function_mode,
        default="both",
        help="holding, input, or both",
    )
    parser.add_argument(
        "--show-ok",
        action="store_true",
        help="Print successful windows too, not only readable summaries/failures.",
    )
    parser.add_argument(
        "--only-ok",
        action="store_true",
        help="Print only successful windows.",
    )
    return parser.parse_args()


def read_window(
    client: ModbusSerialClient,
    *,
    slave: int,
    start: int,
    end: int,
    function_name: str,
) -> WindowResult:
    count = end - start + 1
    read_fn = compat_read_holding if function_name == "holding" else compat_read_input
    response = read_fn(client, slave=slave, address=start, count=count)
    time.sleep(REQUEST_GAP_SECONDS)

    if response is None:
        return WindowResult(
            start=start,
            end=end,
            function_name=function_name,
            status="none",
            values_seen=0,
            sample=(),
        )
    if response.isError():
        return WindowResult(
            start=start,
            end=end,
            function_name=function_name,
            status="error",
            values_seen=0,
            sample=(),
        )

    registers = getattr(response, "registers", None)
    if not registers:
        return WindowResult(
            start=start,
            end=end,
            function_name=function_name,
            status="empty",
            values_seen=0,
            sample=(),
        )

    visible_values = tuple(int(value) for value in registers)
    status = "ok" if len(visible_values) >= count else "partial"
    return WindowResult(
        start=start,
        end=end,
        function_name=function_name,
        status=status,
        values_seen=len(visible_values),
        sample=visible_values[: min(6, len(visible_values))],
    )


def format_result(result: WindowResult) -> str:
    sample_text = list(result.sample)
    return (
        f"{result.function_name:<7} {result.start:>5}..{result.end:<5} "
        f"{result.status:<7} values={result.values_seen:<3} sample={sample_text}"
    )


def main() -> int:
    args = parse_args()
    if args.end < args.start:
        print("ERROR: --end must be >= --start")
        return 2
    if args.window <= 0 or args.window > MAX_REGISTERS_PER_READ:
        print(f"ERROR: --window must be between 1 and {MAX_REGISTERS_PER_READ}")
        return 2

    step = args.step if args.step is not None else args.window
    if step <= 0:
        print("ERROR: --step must be > 0")
        return 2

    function_names = (
        ("holding", "input") if args.function == "both" else (args.function,)
    )

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

    totals: dict[str, dict[str, int]] = {
        name: {"ok": 0, "partial": 0, "error": 0, "none": 0, "empty": 0}
        for name in function_names
    }

    try:
        print(
            f"scanning slave {args.slave} on {args.port} "
            f"from {args.start} to {args.end} "
            f"window={args.window} step={step} function={args.function}"
        )
        print()

        current = args.start
        while current <= args.end:
            window_end = min(args.end, current + args.window - 1)
            for function_name in function_names:
                result = read_window(
                    client,
                    slave=args.slave,
                    start=current,
                    end=window_end,
                    function_name=function_name,
                )
                totals[function_name][result.status] += 1
                if args.only_ok:
                    if result.status == "ok":
                        print(format_result(result))
                elif args.show_ok or result.status != "ok":
                    print(format_result(result))
            current += step

        print()
        print("summary:")
        for function_name in function_names:
            summary = totals[function_name]
            print(
                f"  {function_name:<7} ok={summary['ok']:<3} "
                f"partial={summary['partial']:<3} error={summary['error']:<3} "
                f"none={summary['none']:<3} empty={summary['empty']:<3}"
            )
        return 0
    finally:
        client.close()


if __name__ == "__main__":
    raise SystemExit(main())
