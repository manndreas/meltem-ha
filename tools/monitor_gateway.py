#!/usr/bin/env python3
"""Continuously monitor Meltem airflow values via the local gateway."""

from __future__ import annotations

import argparse
import time
from datetime import datetime

from pymodbus.client import ModbusSerialClient


FIXED_BAUDRATE = 19200
FIXED_BYTESIZE = 8
FIXED_PARITY = "E"
FIXED_STOPBITS = 1
FIXED_TIMEOUT = 0.8
DEFAULT_GATEWAY_DEVICE_ID = 1
DEFAULT_PORT = "/dev/ttyACM0"
REQUEST_GAP_SECONDS = 0.3

REGISTER_GATEWAY_NUMBER_OF_NODES = 43901
REGISTER_GATEWAY_NODE_ADDRESS_1 = 43902
REGISTER_EXTRACT_AIR_FLOW = 41020


def compat_read(client: ModbusSerialClient, *, slave: int, address: int, count: int):
    """Read holding registers with either pymodbus keyword variant."""

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


def discover_units(client: ModbusSerialClient, *, gateway_id: int) -> list[int]:
    """Read configured unit addresses from the gateway bridge registers."""

    count_response = compat_read(
        client,
        slave=gateway_id,
        address=REGISTER_GATEWAY_NUMBER_OF_NODES,
        count=1,
    )
    if (
        count_response is None
        or count_response.isError()
        or not getattr(count_response, "registers", None)
    ):
        raise RuntimeError(f"failed to read bridge node count: {count_response}")
    node_count = int(count_response.registers[0])

    addresses_response = compat_read(
        client,
        slave=gateway_id,
        address=REGISTER_GATEWAY_NODE_ADDRESS_1,
        count=max(1, min(32, node_count)),
    )
    if (
        addresses_response is None
        or addresses_response.isError()
        or not getattr(addresses_response, "registers", None)
    ):
        raise RuntimeError(f"failed to read bridge node addresses: {addresses_response}")

    return [int(value) for value in addresses_response.registers if int(value) != 0]


def read_flows(client: ModbusSerialClient, *, slave: int) -> tuple[int | None, int | None]:
    """Read extract and supply airflow as one contiguous block."""

    response = compat_read(
        client,
        slave=slave,
        address=REGISTER_EXTRACT_AIR_FLOW,
        count=2,
    )
    time.sleep(REQUEST_GAP_SECONDS)
    if response is None or response.isError():
        return None, None
    registers = getattr(response, "registers", None)
    if not registers or len(registers) < 2:
        return None, None
    return int(registers[0]), int(registers[1])


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Monitor airflow values from a locally attached Meltem gateway."
    )
    parser.add_argument("--port", default=DEFAULT_PORT)
    parser.add_argument("--gateway-id", type=int, default=DEFAULT_GATEWAY_DEVICE_ID)
    parser.add_argument("--interval", type=float, default=5.0)
    parser.add_argument(
        "--units",
        default="auto",
        help="Comma-separated unit addresses, or 'auto' to read them from the gateway.",
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
        if args.units == "auto":
            units = discover_units(client, gateway_id=args.gateway_id)
        else:
            units = [int(part) for part in args.units.split(",") if part.strip()]

        print(f"monitoring units: {units}")
        print(f"interval: {args.interval}s")
        print("press Ctrl+C to stop")
        print()

        previous: dict[int, tuple[int | None, int | None]] = {}
        while True:
            client.close()
            client.connect()
            stamp = datetime.now().strftime("%H:%M:%S")
            line = [stamp]
            for unit in units:
                flows = read_flows(client, slave=unit)
                marker = ""
                if previous.get(unit) != flows:
                    marker = "*"
                previous[unit] = flows
                line.append(f"u{unit}:{flows[0]}/{flows[1]}{marker}")
            print("  ".join(line), flush=True)
            time.sleep(args.interval)
    except KeyboardInterrupt:
        return 0
    finally:
        client.close()


if __name__ == "__main__":
    raise SystemExit(main())
