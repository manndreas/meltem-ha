#!/usr/bin/env python3
"""Continuously monitor Meltem airflow values via the local gateway."""

from __future__ import annotations

import argparse
import asyncio
from datetime import datetime

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
DEFAULT_GATEWAY_DEVICE_ID = 1
DEFAULT_PORT = "/dev/ttyACM0"
REQUEST_GAP_SECONDS = 0.3

REGISTER_GATEWAY_NUMBER_OF_NODES = 43901
REGISTER_GATEWAY_NODE_ADDRESS_1 = 43902
REGISTER_EXTRACT_AIR_FLOW = 41020


async def discover_units(gateway: ModbusUnit) -> list[int]:
    """Read configured unit addresses from the gateway bridge registers."""

    try:
        (node_count,) = await gateway.read_holding_registers(
            REGISTER_GATEWAY_NUMBER_OF_NODES, 1
        )
    except ModbusConnectionError:
        raise
    except ModbusError as err:
        raise RuntimeError(f"failed to read bridge node count: {err}") from err

    try:
        addresses = await gateway.read_holding_registers(
            REGISTER_GATEWAY_NODE_ADDRESS_1, max(1, min(32, node_count))
        )
    except ModbusConnectionError:
        raise
    except ModbusError as err:
        raise RuntimeError(f"failed to read bridge node addresses: {err}") from err

    return [int(value) for value in addresses if int(value) != 0]


async def read_flows(unit: ModbusUnit) -> tuple[int | None, int | None]:
    """Read extract and supply airflow as one contiguous block."""

    try:
        registers = await unit.read_holding_registers(REGISTER_EXTRACT_AIR_FLOW, 2)
    except ModbusError:
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


async def main() -> int:
    args = parse_args()
    connection = ModbusConnection(
        ModbusSerialParams(
            device=args.port,
            baudrate=FIXED_BAUDRATE,
            bytesize=FIXED_BYTESIZE,
            parity=FIXED_PARITY,
            stopbits=FIXED_STOPBITS,
        ),
        timeout=FIXED_TIMEOUT,
        message_spacing=REQUEST_GAP_SECONDS,
    )
    try:
        await connection.connect()
    except ModbusConnectionError as err:
        print(f"ERROR: could not open serial connection on {args.port}: {err}")
        return 2

    try:
        if args.units == "auto":
            units = await discover_units(connection.for_unit(args.gateway_id))
        else:
            units = [int(part) for part in args.units.split(",") if part.strip()]

        print(f"monitoring units: {units}")
        print(f"interval: {args.interval}s")
        print("press Ctrl+C to stop")
        print()

        previous: dict[int, tuple[int | None, int | None]] = {}
        while True:
            # Reopen the link every round, as the pymodbus version did.
            await connection.disconnect()
            stamp = datetime.now().strftime("%H:%M:%S")
            line = [stamp]
            for unit in units:
                flows = await read_flows(connection.for_unit(unit))
                marker = ""
                if previous.get(unit) != flows:
                    marker = "*"
                previous[unit] = flows
                line.append(f"u{unit}:{flows[0]}/{flows[1]}{marker}")
            print("  ".join(line), flush=True)
            await asyncio.sleep(args.interval)
    except ModbusConnectionError as err:
        print(f"ERROR: lost the serial connection on {args.port}: {err}")
        return 2
    finally:
        await connection.close()


if __name__ == "__main__":
    try:
        raise SystemExit(asyncio.run(main()))
    except KeyboardInterrupt:
        raise SystemExit(0) from None
