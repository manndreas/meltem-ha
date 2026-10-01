"""Benchmark simple Modbus request patterns against the Meltem gateway."""

from __future__ import annotations

import argparse
import asyncio
import time

from modbus_connection import ModbusError, ModbusUnit
from modbus_connection.tmodbus import ModbusConnection

from tools._link import (
    GATEWAY_DEVICE_ID,
    Sample,
    discover_units,
    elapsed_ms,
    open_link,
    parse_addresses,
    print_summary,
    run,
    tool_parser,
)

REGISTER_OUTDOOR_AIR_TEMPERATURE = 41002
REGISTER_EXTRACT_AIR_TEMPERATURE = 41004
REGISTER_SUPPLY_AIR_TEMPERATURE = 41009
REGISTER_ERROR_STATUS = 41016
REGISTER_FILTER_CHANGE_DUE = 41017
REGISTER_FROST_PROTECTION_ACTIVE = 41018
REGISTER_EXTRACT_AIR_FLOW = 41020
REGISTER_SUPPLY_AIR_FLOW = 41021

# Each request is (address, count, label).
SCENARIOS: dict[str, tuple[tuple[int, int, str], ...]] = {
    "airflow_single": (
        (REGISTER_EXTRACT_AIR_FLOW, 1, "extract_flow"),
        (REGISTER_SUPPLY_AIR_FLOW, 1, "supply_flow"),
    ),
    "airflow_block": ((REGISTER_EXTRACT_AIR_FLOW, 2, "flows_block"),),
    "status_single": (
        (REGISTER_ERROR_STATUS, 1, "error"),
        (REGISTER_FILTER_CHANGE_DUE, 1, "filter_due"),
        (REGISTER_FROST_PROTECTION_ACTIVE, 1, "frost"),
    ),
    "status_block": ((REGISTER_ERROR_STATUS, 3, "status_block"),),
    "temps_single": (
        (REGISTER_OUTDOOR_AIR_TEMPERATURE, 2, "outdoor_temp"),
        (REGISTER_EXTRACT_AIR_TEMPERATURE, 2, "extract_temp"),
        (REGISTER_SUPPLY_AIR_TEMPERATURE, 2, "supply_temp"),
    ),
    "temps_mixed_block": (
        (REGISTER_OUTDOOR_AIR_TEMPERATURE, 4, "outdoor_extract_block"),
        (REGISTER_SUPPLY_AIR_TEMPERATURE, 2, "supply_temp"),
    ),
}


async def read_block(unit: ModbusUnit, address: int, count: int) -> Sample:
    """Read one register block and measure its latency."""

    start = time.perf_counter()
    try:
        registers = await unit.read_holding_registers(address, count)
    except ModbusError as err:
        return Sample(False, elapsed_ms(start), f"{type(err).__name__}: {err}")
    return Sample(True, elapsed_ms(start), str(registers))


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
    for cycle in range(1, cycles + 1):
        print(f"cycle {cycle}/{cycles}")
        for unit in units:
            for address, count, label in SCENARIOS[scenario]:
                sample = await read_block(connection.for_unit(unit), address, count)
                # Slept after the measurement, so the gap never counts as latency.
                await asyncio.sleep(gap)
                samples.append(sample)
                status = "OK" if sample.ok else "ERR"
                print(
                    f"  unit {unit:>2} {label:<22} {status:<3} "
                    f"{sample.latency_ms:>6.1f} ms  {sample.detail}"
                )
    return samples


def parse_units(value: str) -> list[int] | None:
    """Parse a comma-separated address list; ``auto`` means None."""

    return None if value == "auto" else parse_addresses(value)


def parse_args() -> argparse.Namespace:
    parser = tool_parser("Benchmark simple request patterns against a Meltem gateway.")
    parser.add_argument("--gateway-id", type=int, default=GATEWAY_DEVICE_ID)
    parser.add_argument("--scenario", choices=SCENARIOS, default="airflow_single")
    parser.add_argument("--cycles", type=int, default=3)
    parser.add_argument("--gap", type=float, default=0.3)
    parser.add_argument(
        "--units",
        type=parse_units,
        default="auto",
        help="Comma-separated unit addresses, or 'auto' to read them from the gateway.",
    )
    return parser.parse_args()


async def main() -> int:
    args = parse_args()
    # The gap is slept explicitly outside the measured latency, so the link
    # itself adds no extra spacing.
    async with open_link(args.port, message_spacing=None) as link:
        units = args.units
        if units is None:
            units = await discover_units(link.for_unit(args.gateway_id), gap=args.gap)

        print(f"units: {units}")
        print(f"scenario: {args.scenario}")
        print(f"cycles: {args.cycles}")
        print(f"gap: {args.gap}s")
        print()

        samples = await run_scenario(
            link,
            units=units,
            scenario=args.scenario,
            cycles=args.cycles,
            gap=args.gap,
        )

    return print_summary(samples)


if __name__ == "__main__":
    run(main)
