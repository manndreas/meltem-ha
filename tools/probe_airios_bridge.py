#!/usr/bin/env python3
"""Probe whether the Meltem gateway exposes Airios-like bridge registers."""

from __future__ import annotations

import argparse
import struct
import sys
import time
from pathlib import Path

from pymodbus.client import ModbusSerialClient


FIXED_BAUDRATE = 19200
FIXED_BYTESIZE = 8
FIXED_PARITY = "E"
FIXED_STOPBITS = 1
FIXED_TIMEOUT = 0.8
DEFAULT_DEVICE_ID = 1
REQUEST_GAP_SECONDS = 0.3


def read_holding_registers_compat(
    client: ModbusSerialClient, *, address: int, count: int, device_id: int
):
    """Call pymodbus with the correct unit-id keyword for the local version."""

    try:
        return client.read_holding_registers(
            address=address,
            count=count,
            device_id=device_id,
        )
    except TypeError:
        return client.read_holding_registers(
            address=address,
            count=count,
            slave=device_id,
        )


def read_u16(client: ModbusSerialClient, device_id: int, address: int) -> int | None:
    """Read one uint16 register."""

    response = read_holding_registers_compat(
        client,
        address=address,
        count=1,
        device_id=device_id,
    )
    time.sleep(REQUEST_GAP_SECONDS)
    if response is None or response.isError():
        return None
    registers = getattr(response, "registers", None)
    if not registers or len(registers) < 1:
        return None
    return registers[0]


def read_u32_word_swap(
    client: ModbusSerialClient, device_id: int, address: int
) -> int | None:
    """Read one uint32 register pair with word swap."""

    response = read_holding_registers_compat(
        client,
        address=address,
        count=2,
        device_id=device_id,
    )
    time.sleep(REQUEST_GAP_SECONDS)
    if response is None or response.isError():
        return None
    registers = getattr(response, "registers", None)
    if not registers or len(registers) < 2:
        return None
    return struct.unpack(">I", struct.pack(">HH", registers[1], registers[0]))[0]


def read_range(
    client: ModbusSerialClient, device_id: int, address: int, count: int
) -> list[int] | None:
    """Read a contiguous block of uint16 registers."""

    response = read_holding_registers_compat(
        client,
        address=address,
        count=count,
        device_id=device_id,
    )
    time.sleep(REQUEST_GAP_SECONDS)
    if response is None or response.isError():
        return None
    registers = getattr(response, "registers", None)
    if not registers or len(registers) < count:
        return None
    return list(registers)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Probe a Meltem gateway for Airios-style bridge registers."
    )
    parser.add_argument(
        "--port",
        default="/dev/ttyACM0",
        help="Serial device path, for example /dev/ttyACM0 or /dev/serial/by-id/...",
    )
    parser.add_argument(
        "--device-id",
        type=int,
        default=DEFAULT_DEVICE_ID,
        help="Bridge Modbus device ID to probe (default: 1).",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    port = str(Path(args.port))
    device_id = int(args.device_id)

    client = ModbusSerialClient(
        port=port,
        baudrate=FIXED_BAUDRATE,
        bytesize=FIXED_BYTESIZE,
        parity=FIXED_PARITY,
        stopbits=FIXED_STOPBITS,
        timeout=FIXED_TIMEOUT,
    )

    print(f"Probing Airios-like bridge registers on {port} with device_id={device_id}")

    try:
        if not client.connect():
            print("ERROR: could not open serial connection")
            return 2

        serial_parity = read_u16(client, device_id, 41998)
        serial_stop_bits = read_u16(client, device_id, 41999)
        serial_baudrate = read_u16(client, device_id, 42000)
        modbus_device_id = read_u16(client, device_id, 42001)
        number_of_nodes = read_u16(client, device_id, 43901)
        node_addresses = read_range(client, device_id, 43902, 16)
        uptime_seconds = read_u32_word_swap(client, device_id, 41019)

        print()
        print("Bridge register results:")
        print(f"  41998 serial parity:      {serial_parity}")
        print(f"  41999 serial stop bits:   {serial_stop_bits}")
        print(f"  42000 serial baudrate:    {serial_baudrate}")
        print(f"  42001 modbus device id:   {modbus_device_id}")
        print(f"  41019 uptime:             {uptime_seconds}")
        print(f"  43901 number of nodes:    {number_of_nodes}")
        print(f"  43902..43917 node addrs:  {node_addresses}")

        if number_of_nodes is None and node_addresses is None:
            print()
            print("No bridge-style response detected on the tested registers.")
            print("This does not prove the gateway is not Airios-based,")
            print("but it suggests these bridge registers are not exposed on this path.")
            return 1

        print()
        print("At least some bridge-style registers responded.")
        return 0
    finally:
        client.close()


if __name__ == "__main__":
    sys.exit(main())
