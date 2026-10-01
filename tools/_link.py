"""Serial link settings and helpers shared by the gateway tools.

The tools run without Home Assistant, so they keep their own copy of the link
settings instead of importing them from the integration.
"""

from __future__ import annotations

import argparse
import asyncio
import statistics
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass

from modbus_connection import (
    ModbusConnectionError,
    ModbusError,
    ModbusSerialParams,
    ModbusUnit,
)
from modbus_connection.tmodbus import ModbusConnection

DEFAULT_PORT = "/dev/ttyACM0"
GATEWAY_DEVICE_ID = 1
TIMEOUT = 0.8
REQUEST_GAP_SECONDS = 0.1
MAX_REGISTERS_PER_READ = 120

REGISTER_GATEWAY_NUMBER_OF_NODES = 43901
REGISTER_GATEWAY_NODE_ADDRESS_1 = 43902


class LinkOpenError(Exception):
    """The serial port could not be opened, usually because it is in use."""


@dataclass(frozen=True)
class Sample:
    """One timed request, or one timed request sequence."""

    ok: bool
    latency_ms: float
    detail: str = ""


def serial_params(port: str) -> ModbusSerialParams:
    """Return the gateway's fixed link settings: 19200 baud, 8E1."""

    return ModbusSerialParams(device=port, baudrate=19200, bytesize=8, parity="E", stopbits=1)


@asynccontextmanager
async def open_link(
    port: str, *, message_spacing: float | None = REQUEST_GAP_SECONDS
) -> AsyncIterator[ModbusConnection]:
    """Open the serial link and close it again on exit.

    Benchmarks pass ``message_spacing=None`` and sleep their own gap, so the gap
    never counts as latency.
    """

    link = ModbusConnection(
        serial_params(port), timeout=TIMEOUT, message_spacing=message_spacing
    )
    try:
        await link.connect()
    except ModbusConnectionError as err:
        raise LinkOpenError(f"could not open serial connection on {port}: {err}") from err
    try:
        yield link
    finally:
        await link.close()


async def read_or_none(unit: ModbusUnit, address: int, count: int) -> list[int] | None:
    """Read holding registers, or return None when the unit refuses or stays silent.

    A lost link still raises, so a dead port does not pass for a silent unit.
    """

    try:
        return await unit.read_holding_registers(address, count)
    except ModbusConnectionError:
        raise
    except ModbusError:
        return None


async def discover_units(gateway: ModbusUnit, *, gap: float = 0.0) -> list[int]:
    """Return the unit addresses configured in the gateway's bridge registers."""

    (node_count,) = await gateway.read_holding_registers(REGISTER_GATEWAY_NUMBER_OF_NODES, 1)
    await asyncio.sleep(gap)
    addresses = await gateway.read_holding_registers(
        REGISTER_GATEWAY_NODE_ADDRESS_1, max(1, min(32, node_count))
    )
    await asyncio.sleep(gap)
    return [int(address) for address in addresses if int(address) != 0]


def elapsed_ms(start: float) -> float:
    """Return the milliseconds since ``start``, a ``time.perf_counter()`` reading."""

    return (time.perf_counter() - start) * 1000


def print_summary(samples: list[Sample]) -> int:
    """Print the request counts and latencies; return 1 if any sample failed, else 0."""

    latencies = [sample.latency_ms for sample in samples if sample.ok]
    failed = len(samples) - len(latencies)
    print()
    print("summary:")
    print(f"  total requests: {len(samples)}")
    print(f"  successful:     {len(latencies)}")
    print(f"  failed:         {failed}")
    if latencies:
        print(f"  avg latency:    {statistics.mean(latencies):.1f} ms")
        if len(latencies) >= 20:
            print(f"  p95 latency:    {statistics.quantiles(latencies, n=20)[18]:.1f} ms")
        else:
            print(f"  max latency:    {max(latencies):.1f} ms")
    return 1 if failed else 0


def tool_parser(description: str, *, slave: bool = False) -> argparse.ArgumentParser:
    """Return an argument parser with ``--port`` and, if asked for, a required ``--slave``."""

    parser = argparse.ArgumentParser(description=description)
    parser.add_argument(
        "--port",
        default=DEFAULT_PORT,
        help="Serial device, e.g. /dev/ttyACM0, /dev/serial/by-id/... or COM3 "
        "(default: %(default)s).",
    )
    if slave:
        parser.add_argument(
            "--slave", type=int, required=True, help="Modbus address of the unit."
        )
    return parser


def parse_addresses(value: str) -> list[int]:
    """Parse a comma-separated list of Modbus addresses."""

    return [int(part) for part in value.split(",") if part.strip()]


def parse_register_range(value: str) -> tuple[int, int]:
    """Parse ``start:count`` or ``start-end`` into an inclusive (start, end) pair."""

    if ":" in value:
        start_s, count_s = value.split(":", 1)
        start, count = int(start_s), int(count_s)
        if count <= 0:
            raise argparse.ArgumentTypeError("count must be > 0")
        return start, start + count - 1
    if "-" in value:
        start_s, end_s = value.split("-", 1)
        start, end = int(start_s), int(end_s)
        if end < start:
            raise argparse.ArgumentTypeError("end must be >= start")
        return start, end
    raise argparse.ArgumentTypeError("range must use start:count or start-end")


def run(
    main: Callable[[], Awaitable[int]],
    *,
    lost_link_errors: tuple[type[Exception], ...] = (),
) -> None:
    """Run a tool's main coroutine and exit with its code; link failures exit with 2.

    ``lost_link_errors`` names further exceptions that mean the link went down.
    """

    try:
        exit_code = asyncio.run(main())
    except KeyboardInterrupt:
        exit_code = 0
    except LinkOpenError as err:
        print(f"ERROR: {err}")
        exit_code = 2
    except (ModbusConnectionError, *lost_link_errors) as err:
        print(f"ERROR: lost the serial connection: {err}")
        exit_code = 2
    except ModbusError as err:
        print(f"ERROR: {type(err).__name__}: {err}")
        exit_code = 1
    raise SystemExit(exit_code)
