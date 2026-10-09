"""Run the pinned pymodbus benchmark with the installed API's unit keyword."""

from __future__ import annotations

import importlib
import runpy
import sys
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from types import ModuleType

import pymodbus
from _pymodbus_compat import install_pymodbus_unit_keyword_compat
from pymodbus.client import ModbusSerialClient


class _StubPlatform(StrEnum):
    SENSOR = "sensor"
    BINARY_SENSOR = "binary_sensor"
    FAN = "fan"
    NUMBER = "number"
    SELECT = "select"
    SWITCH = "switch"


def _install_homeassistant_stub() -> None:
    homeassistant_module = ModuleType("homeassistant")
    const_module = ModuleType("homeassistant.const")
    util_module = ModuleType("homeassistant.util")
    dt_module = ModuleType("homeassistant.util.dt")

    const_module.Platform = _StubPlatform
    dt_module.utcnow = lambda: datetime.now(UTC)
    util_module.dt = dt_module
    homeassistant_module.const = const_module
    homeassistant_module.util = util_module
    sys.modules.update(
        {
            "homeassistant": homeassistant_module,
            "homeassistant.const": const_module,
            "homeassistant.util": util_module,
            "homeassistant.util.dt": dt_module,
        }
    )


def _ensure_homeassistant_modules() -> None:
    try:
        importlib.import_module("homeassistant.const")
    except ModuleNotFoundError as err:
        if err.name != "homeassistant":
            raise
        _install_homeassistant_stub()
        print("Home Assistant not installed; using benchmark-only Platform and UTC stubs")
        return

    importlib.import_module("homeassistant.util.dt")


def main() -> None:
    """Launch the legacy integration-like runner from the baseline worktree."""

    baseline_script = Path.cwd() / "tools" / "benchmark_integration_like.py"
    if not baseline_script.is_file():
        raise SystemExit(
            "Run this launcher from the 4009a32 baseline worktree; "
            f"not found: {baseline_script}"
        )

    _ensure_homeassistant_modules()
    install_pymodbus_unit_keyword_compat(ModbusSerialClient)
    print(f"pymodbus {pymodbus.__version__}; legacy keyword compatibility enabled")

    sys.argv = [str(baseline_script), *sys.argv[1:]]
    runpy.run_path(str(baseline_script), run_name="__main__")


if __name__ == "__main__":
    main()
