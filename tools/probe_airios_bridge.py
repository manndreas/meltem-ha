#!/usr/bin/env python3
"""Probe whether the Meltem gateway exposes Airios-like bridge registers."""

from __future__ import annotations

import argparse
import struct

from modbus_connection import ModbusConnectionError, ModbusError, ModbusUnit

from tools._link import DEFAULT_PORT, GATEWAY_DEVICE_ID, open_link, run


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
        default=DEFAULT_PORT,
        help="Serial device path, for example /dev/ttyACM0 or /dev/serial/by-id/...",
    )
    parser.add_argument(
        "--device-id",
        type=int,
        default=GATEWAY_DEVICE_ID,
        help="Bridge Modbus device ID to probe (default: 1).",
    )
    return parser.parse_args()


async def main() -> int:
    args = parse_args()
    print(f"Probing Airios-like bridge registers on {args.port} with device_id={args.device_id}")

    async with open_link(args.port, message_spacing=0.3) as link:
        unit = link.for_unit(args.device_id)
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


if __name__ == "__main__":
    run(main)
