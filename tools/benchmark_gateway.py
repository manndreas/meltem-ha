#!/usr/bin/env python3
"""Benchmark simple Modbus request patterns against the Meltem gateway."""

from __future__ import annotations

import argparse
import asyncio
from dataclasses import dataclass
import statistics
import time

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

REGISTER_GATEWAY_NUMBER_OF_NODES = 43901
REGISTER_GATEWAY_NODE_ADDRESS_1 = 43902
REGISTER_EXTRACT_AIR_FLOW = 41020
REGISTER_SUPPLY_AIR_FLOW = 41021
REGISTER_ERROR_STATUS = 41016
REGISTER_FILTER_CHANGE_DUE = 41017
REGISTER_FROST_PROTECTION_ACTIVE = 41018
REGISTER_OUTDOOR_AIR_TEMPERATURE = 41002
REGISTER_EXTRACT_AIR_TEMPERATURE = 41004
REGISTER_SUPPLY_AIR_TEMPERATURE = 41009


@dataclass
class Sample:
    """One measured Modbus request."""

    ok: bool
    latency_ms: float
    detail: str


async def read_block(unit: ModbusUnit, *, address: int, count: int, gap: float) -> Sample:
    """Read one register block and measure latency.

    The gap is slept after the measurement, so it never counts as latency.
    """

    start = time.perf_counter()
    try:
        registers = await unit.read_holding_registers(address, count)
    except ModbusError as err:
        elapsed_ms = (time.perf_counter() - start) * 1000
        await asyncio.sleep(gap)
        return Sample(False, elapsed_ms, f"{type(err).__name__}: {err}")

    elapsed_ms = (time.perf_counter() - start) * 1000
    await asyncio.sleep(gap)
    return Sample(True, elapsed_ms, str(registers))


async def discover_units(gateway: ModbusUnit, *, gap: float) -> list[int]:
    """Read configured unit addresses from bridge registers."""

    try:
        (node_count,) = await gateway.read_holding_registers(
            REGISTER_GATEWAY_NUMBER_OF_NODES, 1
        )
    except ModbusConnectionError:
        raise
    except ModbusError as err:
        raise RuntimeError(f"failed to read bridge node count: {err}") from err
    await asyncio.sleep(gap)

    try:
        addresses = await gateway.read_holding_registers(
            REGISTER_GATEWAY_NODE_ADDRESS_1, max(1, min(32, node_count))
        )
    except ModbusConnectionError:
        raise
    except ModbusError as err:
        raise RuntimeError("failed to read bridge node addresses") from err
    await asyncio.sleep(gap)

    return [int(value) for value in addresses if int(value) != 0]


def scenario_requests(name: str) -> list[tuple[int, int, str]]:
    """Return register requests for one scenario."""

    if name == "airflow_single":
        return [
            (REGISTER_EXTRACT_AIR_FLOW, 1, "extract_flow"),
            (REGISTER_SUPPLY_AIR_FLOW, 1, "supply_flow"),
        ]
    if name == "airflow_block":
        return [
            (REGISTER_EXTRACT_AIR_FLOW, 2, "flows_block"),
        ]
    if name == "status_single":
        return [
            (REGISTER_ERROR_STATUS, 1, "error"),
            (REGISTER_FILTER_CHANGE_DUE, 1, "filter_due"),
            (REGISTER_FROST_PROTECTION_ACTIVE, 1, "frost"),
        ]
    if name == "status_block":
        return [
            (REGISTER_ERROR_STATUS, 3, "status_block"),
        ]
    if name == "temps_single":
        return [
            (REGISTER_OUTDOOR_AIR_TEMPERATURE, 2, "outdoor_temp"),
            (REGISTER_EXTRACT_AIR_TEMPERATURE, 2, "extract_temp"),
            (REGISTER_SUPPLY_AIR_TEMPERATURE, 2, "supply_temp"),
        ]
    if name == "temps_mixed_block":
        return [
            (REGISTER_OUTDOOR_AIR_TEMPERATURE, 4, "outdoor_extract_block"),
            (REGISTER_SUPPLY_AIR_TEMPERATURE, 2, "supply_temp"),
        ]
    raise ValueError(f"unknown scenario: {name}")


async def run_scenario(
    connection: ModbusConnection,
    *,
    units: list[int],
    scenario: str,
    cycles: int,
    gap: float,
) -> list[Sample]:
    """Run one scenario across all units."""

    samples: list[Sample] = []
    requests = scenario_requests(scenario)
    for cycle in range(cycles):
        print(f"cycle {cycle + 1}/{cycles}")
        for unit in units:
            for address, count, label in requests:
                sample = await read_block(
                    connection.for_unit(unit),
                    address=address,
                    count=count,
                    gap=gap,
                )
                samples.append(sample)
                status = "OK" if sample.ok else "ERR"
                print(
                    f"  unit {unit:>2} {label:<22} {status:<3} {sample.latency_ms:>6.1f} ms  {sample.detail}"
                )
    return samples


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Benchmark simple request patterns against a Meltem gateway."
    )
    parser.add_argument("--port", default=DEFAULT_PORT)
    parser.add_argument("--gateway-id", type=int, default=DEFAULT_GATEWAY_DEVICE_ID)
    parser.add_argument(
        "--scenario",
        choices=[
            "airflow_single",
            "airflow_block",
            "status_single",
            "status_block",
            "temps_single",
            "temps_mixed_block",
        ],
        default="airflow_single",
    )
    parser.add_argument("--cycles", type=int, default=3)
    parser.add_argument("--gap", type=float, default=0.3)
    parser.add_argument(
        "--units",
        type=str,
        default="auto",
        help="Comma-separated unit addresses, or 'auto' to read them from the gateway.",
    )
    return parser.parse_args()


async def main() -> int:
    args = parse_args()
    # The gap is slept explicitly outside the measured latency, so the link
    # itself adds no extra spacing.
    connection = ModbusConnection(
        ModbusSerialParams(
            device=args.port,
            baudrate=FIXED_BAUDRATE,
            bytesize=FIXED_BYTESIZE,
            parity=FIXED_PARITY,
            stopbits=FIXED_STOPBITS,
        ),
        timeout=FIXED_TIMEOUT,
    )

    try:
        await connection.connect()
    except ModbusConnectionError as err:
        print(f"ERROR: could not open serial connection on {args.port}: {err}")
        return 2

    try:
        if args.units == "auto":
            units = await discover_units(
                connection.for_unit(args.gateway_id), gap=args.gap
            )
        else:
            units = [int(part) for part in args.units.split(",") if part.strip()]

        print(f"units: {units}")
        print(f"scenario: {args.scenario}")
        print(f"cycles: {args.cycles}")
        print(f"gap: {args.gap}s")
        print()

        samples = await run_scenario(
            connection,
            units=units,
            scenario=args.scenario,
            cycles=args.cycles,
            gap=args.gap,
        )

        oks = [sample for sample in samples if sample.ok]
        errors = [sample for sample in samples if not sample.ok]
        latencies = [sample.latency_ms for sample in oks]

        print()
        print("summary:")
        print(f"  total requests: {len(samples)}")
        print(f"  successful:     {len(oks)}")
        print(f"  failed:         {len(errors)}")
        if latencies:
            print(f"  avg latency:    {statistics.mean(latencies):.1f} ms")
            print(f"  p95 latency:    {statistics.quantiles(latencies, n=20)[18]:.1f} ms" if len(latencies) >= 20 else f"  max latency:    {max(latencies):.1f} ms")
        return 0 if not errors else 1
    except ModbusConnectionError as err:
        print(f"ERROR: lost the serial connection on {args.port}: {err}")
        return 2
    finally:
        await connection.close()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
