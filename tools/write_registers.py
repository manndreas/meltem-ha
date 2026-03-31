#!/usr/bin/env python3
"""Write one or more Meltem holding registers to a unit.

This helper is intentionally small and explicit so we can reproduce app-origin
register writes during reverse-engineering sessions.
"""

from __future__ import annotations

import argparse
import time

from pymodbus.client import ModbusSerialClient


FIXED_BAUDRATE = 19200
FIXED_BYTESIZE = 8
FIXED_PARITY = "E"
FIXED_STOPBITS = 1
FIXED_TIMEOUT = 0.8
DEFAULT_PORT = "/dev/ttyACM0"
REQUEST_GAP_SECONDS = 0.1


def compat_write(client: ModbusSerialClient, *, slave: int, address: int, value: int):
    """Write one holding register with either pymodbus keyword variant."""

    try:
        return client.write_register(
            address=address,
            value=value,
            device_id=slave,
        )
    except TypeError:
        return client.write_register(
            address=address,
            value=value,
            slave=slave,
        )


def parse_write(value: str) -> tuple[int, int]:
    """Parse one write pair in ADDRESS=VALUE form."""

    if "=" not in value:
        raise argparse.ArgumentTypeError("write must look like ADDRESS=VALUE")
    address_s, register_value_s = value.split("=", 1)
    return int(address_s), int(register_value_s)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Write one or more Meltem holding registers."
    )
    parser.add_argument("--port", default=DEFAULT_PORT)
    parser.add_argument("--slave", type=int, required=True)
    parser.add_argument(
        "--write",
        action="append",
        type=parse_write,
        required=True,
        help="Register write in ADDRESS=VALUE form. Repeat in execution order.",
    )
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
        for address, value in args.write:
            response = compat_write(
                client,
                slave=args.slave,
                address=address,
                value=value,
            )
            print(f"write {address}={value} -> {response}")
            time.sleep(REQUEST_GAP_SECONDS)
        return 0
    finally:
        client.close()


if __name__ == "__main__":
    raise SystemExit(main())
