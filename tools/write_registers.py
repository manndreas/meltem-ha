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
    return parser.parse_args()


async def main() -> int:
    args = parse_args()
    async with open_link(args.port) as link:
        unit = link.for_unit(args.slave)
        for address, value in args.write:
            try:
                await unit.write_register(address, value)
            except ModbusError as err:
                print(f"write {address}={value} -> {type(err).__name__}: {err}")
                continue
            print(f"write {address}={value} -> ok")
    return 0


if __name__ == "__main__":
    run(main)
