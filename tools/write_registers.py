"""Write one or more Meltem holding registers to a unit.

This helper is intentionally small and explicit so we can reproduce app-origin
register writes during reverse-engineering sessions.
"""

from __future__ import annotations

import argparse

from modbus_connection import ModbusError

from tools._link import open_link, run, tool_parser


def parse_write(value: str) -> tuple[int, int]:
    """Parse one write pair in ADDRESS=VALUE form."""

    address, separator, register_value = value.partition("=")
    if not separator:
        raise argparse.ArgumentTypeError("write must look like ADDRESS=VALUE")
    return int(address), int(register_value)


def parse_args() -> argparse.Namespace:
    parser = tool_parser("Write one or more Meltem holding registers.", slave=True)
    parser.add_argument(
        "--write",
        action="append",
        type=parse_write,
        required=True,
        help="Register write in ADDRESS=VALUE form. Repeat in execution order.",
    )
    parser.add_argument(
        "--keep-going",
        action="store_true",
        help="Continue after a failed write. By default the sequence stops, so a "
        "trailing apply (41132) never activates a half-written mode.",
    )
    return parser.parse_args()


async def main() -> int:
    args = parse_args()
    failed = False
    async with open_link(args.port) as link:
        unit = link.for_unit(args.slave)
        for index, (address, value) in enumerate(args.write):
            try:
                await unit.write_register(address, value)
            except ModbusError as err:
                print(f"write {address}={value} -> {type(err).__name__}: {err}")
                failed = True
                if not args.keep_going:
                    skipped = len(args.write) - index - 1
                    print(f"stopped; {skipped} later write(s) not sent")
                    break
                continue
            print(f"write {address}={value} -> ok")
    return 1 if failed else 0


if __name__ == "__main__":
    run(main)
