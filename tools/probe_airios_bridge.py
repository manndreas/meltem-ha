#!/usr/bin/env python3
"""Probe whether the Meltem gateway exposes Airios-like bridge registers."""

from __future__ import annotations

import argparse
import asyncio
import struct
import sys
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
DEFAULT_DEVICE_ID = 1
REQUEST_GAP_SECONDS = 0.3


async def read_range(unit: ModbusUnit, address: int, count: int) -> list[int] | None:
    """Read a contiguous block of uint16 registers."""

    try:
        return await unit.read_holding_registers(address, count)
    except ModbusConnectionError:
        raise
    except ModbusError:
        return None


async def read_u16(unit: ModbusUnit, address: int) -> int | None:
    """Read one uint16 register."""

    registers = await read_range(unit, address, 1)
    return None if registers is None else registers[0]


async def read_u32_word_swap(unit: ModbusUnit, address: int) -> int | None:
    """Read one uint32 register pair with word swap."""

    registers = await read_range(unit, address, 2)
    if registers is None:
        return None
    return struct.unpack(">I", struct.pack(">HH", registers[1], registers[0]))[0]


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


async def main() -> int:
    args = parse_args()
    port = str(Path(args.port))
    device_id = int(args.device_id)

    connection = ModbusConnection(
        ModbusSerialParams(
            device=port,
            baudrate=FIXED_BAUDRATE,
            bytesize=FIXED_BYTESIZE,
            parity=FIXED_PARITY,
            stopbits=FIXED_STOPBITS,
        ),
        timeout=FIXED_TIMEOUT,
        message_spacing=REQUEST_GAP_SECONDS,
    )

    print(f"Probing Airios-like bridge registers on {port} with device_id={device_id}")

    try:
        try:
            await connection.connect()
        except ModbusConnectionError as err:
            print(f"ERROR: could not open serial connection: {err}")
            return 2

        unit = connection.for_unit(device_id)
        serial_parity = await read_u16(unit, 41998)
        serial_stop_bits = await read_u16(unit, 41999)
        serial_baudrate = await read_u16(unit, 42000)
        modbus_device_id = await read_u16(unit, 42001)
        number_of_nodes = await read_u16(unit, 43901)
        node_addresses = await read_range(unit, 43902, 16)
        uptime_seconds = await read_u32_word_swap(unit, 41019)

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
    except ModbusConnectionError as err:
        print(f"ERROR: lost the serial connection on {port}: {err}")
        return 2
    finally:
        await connection.close()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
