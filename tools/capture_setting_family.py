#!/usr/bin/env python3
"""Capture and diff Meltem setting-related register families.

This tool is meant for focused before/after experiments against one logical
setting family at a time, or against ad-hoc ``--range`` values. It stores a
timestamped snapshot with metadata and can diff against the previous capture
of the same family or a specified file.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from modbus_connection import ModbusConnectionError, ModbusError, ModbusUnit

from tools._link import (
    DEFAULT_PORT,
    MAX_REGISTERS_PER_READ,
    open_link,
    parse_register_range,
    run,
)

CUSTOM_FAMILY = "custom"


@dataclass(frozen=True)
class RegisterRange:
    start: int
    end: int
    label: str

    @property
    def count(self) -> int:
        return self.end - self.start + 1


@dataclass(frozen=True)
class FamilySpec:
    name: str
    description: str
    ranges: tuple[RegisterRange, ...]


COMMON_SHADOW_RANGES: tuple[RegisterRange, ...] = (
    RegisterRange(51100, 51113, "shadow_a"),
    RegisterRange(51120, 51133, "shadow_b"),
    RegisterRange(51150, 51151, "shadow_commit"),
    RegisterRange(52000, 52010, "meta_words"),
)

DISCOVERY_RANGES: tuple[RegisterRange, ...] = (
    RegisterRange(40000, 40025, "product_and_device_info"),
    RegisterRange(40200, 40209, "unknown_402xx"),
    RegisterRange(41000, 41029, "status_and_measurements"),
    RegisterRange(41100, 41113, "mode_and_runtime_state"),
)

RUNTIME_RANGES: tuple[RegisterRange, ...] = (
    RegisterRange(41120, 41124, "runtime_preset"),
    RegisterRange(41132, 41132, "runtime_commit"),
)

FAMILY_SPECS: dict[str, FamilySpec] = {
    "intensive": FamilySpec(
        name="intensive",
        description="Intensive ventilation airflow and run-on settings.",
        ranges=RUNTIME_RANGES + COMMON_SHADOW_RANGES,
    ),
    "keypad": FamilySpec(
        name="keypad",
        description="LOW/MED/HIGH and one-sided shortcut default settings.",
        ranges=DISCOVERY_RANGES
        + RUNTIME_RANGES
        + (
            RegisterRange(42000, 42009, "documented_config"),
        )
        + COMMON_SHADOW_RANGES,
    ),
    "cross_ventilation": FamilySpec(
        name="cross_ventilation",
        description="Supply-only and extract-only related default airflow settings.",
        ranges=RUNTIME_RANGES + COMMON_SHADOW_RANGES,
    ),
    "humidity": FamilySpec(
        name="humidity",
        description="Documented humidity configuration registers and related shadows.",
        ranges=(
            RegisterRange(42000, 42002, "documented_humidity"),
        )
        + COMMON_SHADOW_RANGES,
    ),
    "co2": FamilySpec(
        name="co2",
        description="Documented CO2 configuration registers and related shadows.",
        ranges=(
            RegisterRange(42003, 42005, "documented_co2"),
        )
        + COMMON_SHADOW_RANGES,
    ),
    "all_known": FamilySpec(
        name="all_known",
        description="Known runtime, documented config, and shadow ranges combined.",
        ranges=RUNTIME_RANGES
        + (
            RegisterRange(42000, 42009, "documented_config"),
        )
        + COMMON_SHADOW_RANGES,
    ),
}


async def read_registers(unit: ModbusUnit, address: int, count: int) -> list[int] | None:
    """Read holding registers, or None when the unit rejects or ignores the read."""

    try:
        return await unit.read_holding_registers(address, count)
    except ModbusConnectionError:
        raise
    except ModbusError:
        return None


async def read_range(
    unit: ModbusUnit,
    *,
    register_range: RegisterRange,
) -> dict[int, int | None]:
    """Read one configured range as individual address values."""

    values: dict[int, int | None] = {
        address: None for address in range(register_range.start, register_range.end + 1)
    }

    chunk_start = register_range.start
    while chunk_start <= register_range.end:
        chunk_end = min(register_range.end, chunk_start + MAX_REGISTERS_PER_READ - 1)
        count = chunk_end - chunk_start + 1
        registers = await read_registers(unit, chunk_start, count)

        if registers is None:
            if count > 1:
                for address in range(chunk_start, chunk_end + 1):
                    single = await read_registers(unit, address, 1)
                    if single:
                        values[address] = int(single[0])
            chunk_start = chunk_end + 1
            continue

        for index, value in enumerate(registers[:count]):
            values[chunk_start + index] = int(value)
        chunk_start = chunk_end + 1

    return values


async def capture_snapshot(
    unit: ModbusUnit,
    *,
    family: FamilySpec,
) -> dict[str, int | None]:
    """Capture all ranges for one family into a string-keyed map."""

    values: dict[str, int | None] = {}
    for register_range in family.ranges:
        for address, value in (
            await read_range(unit, register_range=register_range)
        ).items():
            values[str(address)] = value
    return values


def build_output_path(output_dir: Path, *, family: str, slave: int, label: str) -> Path:
    """Build a timestamped output path for one capture."""

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    safe_label = "_".join(label.strip().split()) or "capture"
    return output_dir / f"{family}-slave{slave}-{stamp}-{safe_label}.json"


def load_values(path: Path) -> dict[str, int | None]:
    """Load values from either a structured capture file or a plain snapshot map."""

    data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data, dict) and isinstance(data.get("values"), dict):
        return data["values"]
    if isinstance(data, dict):
        return {str(key): value for key, value in data.items()}
    raise ValueError(f"unsupported snapshot format: {path}")


def print_diff(before: dict[str, int | None], after: dict[str, int | None]) -> int:
    """Print changed values only and return the number of changed addresses."""

    changed = 0
    for key in sorted(set(before) | set(after), key=lambda item: int(item)):
        old_value = before.get(key)
        new_value = after.get(key)
        if old_value == new_value:
            continue
        print(f"{key}: {old_value} -> {new_value}")
        changed += 1
    if changed == 0:
        print("no changes")
    return changed


def find_latest_capture(output_dir: Path, *, family: str, slave: int, exclude: Path) -> Path | None:
    """Find the newest earlier capture file for the same family and slave."""

    pattern = f"{family}-slave{slave}-*.json"
    candidates = sorted(output_dir.glob(pattern))
    for candidate in reversed(candidates):
        if candidate != exclude:
            return candidate
    return None


def parse_custom_range(value: str) -> RegisterRange:
    """Parse one ad-hoc ``--range`` in start:count or start-end form."""

    start, end = parse_register_range(value)
    return RegisterRange(start, end, f"range_{start}_{end}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Capture or diff focused Meltem setting register families."
    )
    parser.add_argument("--port", default=DEFAULT_PORT)
    parser.add_argument("--slave", type=int)
    parser.add_argument(
        "--family",
        choices=sorted(FAMILY_SPECS),
        help="Named setting family to capture.",
    )
    parser.add_argument(
        "--range",
        dest="ranges",
        action="append",
        type=parse_custom_range,
        help=(
            f"Ad-hoc register range instead of --family, e.g. 41120:5 or 42000-42009. "
            f"Repeatable; stored as family '{CUSTOM_FAMILY}'."
        ),
    )
    parser.add_argument(
        "--label",
        help="Short human label for this capture, e.g. baseline or low-60.",
    )
    parser.add_argument(
        "--output-dir",
        default="tmp/setting-captures",
        help="Directory for captured JSON snapshots.",
    )
    parser.add_argument(
        "--compare-to",
        help="Optional path to an older capture file to diff against.",
    )
    parser.add_argument(
        "--compare-latest",
        action="store_true",
        help="Diff against the newest earlier capture of the same family and slave.",
    )
    parser.add_argument(
        "--list-families",
        action="store_true",
        help="List known families and exit.",
    )
    return parser.parse_args()


async def main() -> int:
    args = parse_args()

    if args.list_families:
        for name, spec in sorted(FAMILY_SPECS.items()):
            print(f"{name}: {spec.description}")
            for register_range in spec.ranges:
                print(
                    f"  - {register_range.label}: {register_range.start}..{register_range.end}"
                )
        return 0

    if args.slave is None or args.label is None or (args.family is None) == (args.ranges is None):
        print(
            "ERROR: --slave, --label, and either --family or --range are required "
            "unless --list-families is used"
        )
        return 2

    family = (
        FAMILY_SPECS[args.family]
        if args.family is not None
        else FamilySpec(CUSTOM_FAMILY, "Ad-hoc register ranges.", tuple(args.ranges))
    )
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = build_output_path(
        output_dir,
        family=family.name,
        slave=args.slave,
        label=args.label,
    )

    async with open_link(args.port) as link:
        values = await capture_snapshot(link.for_unit(args.slave), family=family)

    payload = {
        "metadata": {
            "timestamp": datetime.now().isoformat(timespec="seconds"),
            "family": family.name,
            "description": family.description,
            "slave": args.slave,
            "port": args.port,
            "label": args.label,
            "ranges": [
                {
                    "label": register_range.label,
                    "start": register_range.start,
                    "end": register_range.end,
                }
                for register_range in family.ranges
            ],
        },
        "values": values,
    }
    output_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    print(f"captured {family.name} snapshot to {output_path}")

    compare_path: Path | None = None
    if args.compare_to:
        compare_path = Path(args.compare_to)
    elif args.compare_latest:
        compare_path = find_latest_capture(
            output_dir,
            family=family.name,
            slave=args.slave,
            exclude=output_path,
        )

    if compare_path is None:
        return 0

    if not compare_path.exists():
        print(f"ERROR: compare snapshot does not exist: {compare_path}")
        return 3

    print(f"diff against {compare_path}")
    print_diff(load_values(compare_path), values)
    return 0


if __name__ == "__main__":
    run(main)