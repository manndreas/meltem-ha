#!/usr/bin/env python3
"""Run the integration client against the gateway and count the requests per unit.

Every request is counted, including the retries of the transport policy. For
custom test scripts, ``import tools.count_requests as cr`` and combine
``cr.counting_client`` with the integration building blocks in ``cr.bil``.
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import time
from collections import Counter
from dataclasses import dataclass, fields
from datetime import datetime
from pathlib import Path
from typing import Any

from modbus_connection import ModbusConnectionError
from modbus_connection.tmodbus import ModbusConnection

import tools.benchmark_integration_like as bil
from tools._link import DEFAULT_PORT

PLANS = {
    "airflow": bil.RefreshPlan.only(refresh_airflow=True),
    "temperatures": bil.RefreshPlan.only(refresh_temperatures=True, refresh_environment=True),
    "status": bil.RefreshPlan.only(refresh_status=True, refresh_filter_change_due=True),
    "slow": bil.RefreshPlan.only(
        refresh_filter_days=True, refresh_operating_hours=True, refresh_control_settings=True
    ),
    "full": bil.RefreshPlan(),
}
CSV_HEADER = ("time", "slave", "plan", "result", "requests", "latency_ms")


class CountingUnit:
    """Unit wrapper that counts read and write requests per slave."""

    counts: Counter[int] = Counter()

    def __init__(self, unit: Any, slave: int) -> None:
        self._unit = unit
        self._slave = slave

    def __getattr__(self, name: str) -> Any:
        attr = getattr(self._unit, name)
        if not name.startswith(("read_", "write_")):
            return attr

        async def counted(*args: Any, **kwargs: Any) -> Any:
            CountingUnit.counts[self._slave] += 1
            return await attr(*args, **kwargs)

        return counted


def counting_client(link: ModbusConnection, port: str) -> Any:
    """Return an integration client whose requests land in ``CountingUnit.counts``."""

    return bil.MeltemModbusClient(
        lambda slave: CountingUnit(link.for_unit(slave), slave), port=port
    )


@dataclass(frozen=True)
class ScheduledPlan:
    name: str
    every: int


def parse_plan(value: str) -> ScheduledPlan:
    """Parse ``NAME`` or ``NAME/EVERY``, which runs the plan in every EVERY-th round."""

    name, _, every = value.partition("/")
    if name not in PLANS or (every and (not every.isdigit() or int(every) == 0)):
        raise argparse.ArgumentTypeError(
            f"use NAME or NAME/EVERY with NAME one of: {', '.join(PLANS)}"
        )
    return ScheduledPlan(name, int(every or 1))


def parse_slaves(value: str) -> set[int]:
    return {int(part) for part in value.split(",") if part.strip()}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run the integration client against a Meltem gateway and count requests."
    )
    parser.add_argument("--port", default=DEFAULT_PORT)
    parser.add_argument(
        "--plan",
        dest="plans",
        action="append",
        type=parse_plan,
        help=(
            f"Refresh plan ({', '.join(PLANS)}), optionally NAME/EVERY to run it only "
            "in every EVERY-th round, e.g. temperatures/6. Repeatable; default: airflow."
        ),
    )
    parser.add_argument("--rounds", type=int, default=1)
    parser.add_argument(
        "--interval",
        type=float,
        default=10.0,
        help="Seconds from the start of one round to the start of the next.",
    )
    parser.add_argument(
        "--slaves",
        type=parse_slaves,
        help="Comma-separated slave addresses to poll; default: all discovered units.",
    )
    parser.add_argument(
        "--ghost", type=int, help="Also poll this unconfigured address as an ii_plain room."
    )
    parser.add_argument(
        "--profile",
        action="append",
        type=bil.parse_profile,
        default=[],
        help="SLAVE=PROFILE, e.g. 3=s_plain for an M-WRG-S. Repeatable.",
    )
    parser.add_argument(
        "--show-state",
        action="store_true",
        help="Print the decoded room state after every job.",
    )
    parser.add_argument(
        "--diagnostics-every",
        type=int,
        default=1,
        help="Print the transport diagnostics and request counts every N rounds.",
    )
    parser.add_argument("--csv", type=Path, help="Also append one line per job to this file.")
    return parser.parse_args()


def describe(state: Any) -> str:
    """Return the known values of a room state, without the read health."""

    return " ".join(
        f"{field.name}={getattr(state, field.name)}"
        for field in fields(state)
        if field.name != "group_read_health" and getattr(state, field.name) is not None
    )


async def run_job(
    client: Any,
    room: Any,
    previous: Any,
    plan_name: str,
    *,
    show_state: bool,
    writer: Any,
) -> Any:
    """Read one plan for one room and print requests, latency, and failed groups."""

    plan = PLANS[plan_name]
    requests_before = CountingUnit.counts[room.slave]
    start = time.perf_counter()
    try:
        state = await client.read_room_state(room, previous, plan)
    except bil.MeltemConnectionError:
        raise
    except Exception as err:
        state, result = previous, f"{type(err).__name__}: {err}"
    else:
        failures = [
            f"{group}: {state.read_health_for(group).last_error}"
            for group in plan.read_groups()
            if state.read_health_for(group).consecutive_failures
        ]
        result = "; ".join(failures) or "ok"
    elapsed_ms = (time.perf_counter() - start) * 1000
    requests = CountingUnit.counts[room.slave] - requests_before

    print(f"  slave {room.slave:>2} {plan_name:<12} {requests:>2} req {elapsed_ms:7.1f} ms  {result}")
    if show_state:
        print(f"    {describe(state)}")
    if writer is not None:
        writer.writerow(
            (
                datetime.now().isoformat(timespec="seconds"),
                room.slave,
                plan_name,
                result,
                requests,
                f"{elapsed_ms:.1f}",
            )
        )
    return state


async def main() -> int:
    args = parse_args()
    plans = args.plans or [ScheduledPlan("airflow", 1)]
    csv_file = args.csv.open("a", newline="", encoding="utf-8") if args.csv else None
    writer = csv.writer(csv_file) if csv_file is not None else None
    if writer is not None and csv_file.tell() == 0:
        writer.writerow(CSV_HEADER)

    link = bil.open_connection(args.port)
    try:
        try:
            rooms = await bil.discover_rooms(link, args.port, dict(args.profile))
        except bil.MeltemConnectionError as err:
            print(f"ERROR: could not open serial connection on {args.port}: {err}")
            return 2
        bil.print_rooms(rooms)
        if args.slaves:
            rooms = [room for room in rooms if room.slave in args.slaves]
        if args.ghost is not None:
            rooms.append(
                bil.RoomConfig(key="ghost", name="Ghost", profile="ii_plain", slave=args.ghost)
            )
        print()

        client = counting_client(link, args.port)
        states = {room.key: bil.RoomState() for room in rooms}
        for round_number in range(1, args.rounds + 1):
            round_start = time.monotonic()
            print(f"round {round_number}/{args.rounds} {datetime.now():%H:%M:%S}")
            for plan in plans:
                if (round_number - 1) % plan.every:
                    continue
                for room in rooms:
                    states[room.key] = await run_job(
                        client,
                        room,
                        states[room.key],
                        plan.name,
                        show_state=args.show_state,
                        writer=writer,
                    )
            if round_number % args.diagnostics_every == 0 or round_number == args.rounds:
                print(f"  transport: {client.transport_diagnostics()}")
                print(f"  requests:  {dict(sorted(CountingUnit.counts.items()))}")
            if round_number < args.rounds:
                await asyncio.sleep(max(0.0, args.interval - (time.monotonic() - round_start)))
    except (ModbusConnectionError, bil.MeltemConnectionError) as err:
        print(f"ERROR: lost the serial connection on {args.port}: {err}")
        return 2
    finally:
        await link.close()
        if csv_file is not None:
            csv_file.close()
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(asyncio.run(main()))
    except KeyboardInterrupt:
        raise SystemExit(0) from None
