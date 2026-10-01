"""Tests for the diagnostics and system health helpers."""

from __future__ import annotations

import json
import types
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from homeassistant.components.diagnostics import REDACTED
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.helpers.json import ExtendedJSONEncoder
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.meltem_ventilation.const import CONF_PORT, CONF_ROOMS, DOMAIN
from custom_components.meltem_ventilation.diagnostics import (
    async_get_config_entry_diagnostics,
)
from custom_components.meltem_ventilation.modbus_helpers import MeltemModbusError
from custom_components.meltem_ventilation.models import ReadHealth, RoomConfig, RoomState
from custom_components.meltem_ventilation.system_health import (
    async_register,
    system_health_info,
)

_STRINGS = (
    Path(__file__).parent.parent / "custom_components" / "meltem_ventilation" / "strings.json"
)
_SECRET = "secret-device"
_PORT = f"/dev/serial/by-id/{_SECRET}"

_ROOM = RoomConfig(
    key="unit_1",
    name="Unit 1",
    profile="ii_fc",
    slave=2,
    preview="ID 42 | CO2",
    supported_entity_keys=frozenset({"supply_level", "extract_level"}),
)


def _coordinator() -> MagicMock:
    coordinator = MagicMock()
    coordinator.rooms = [_ROOM]
    coordinator.safe_data = {"unit_1": RoomState(target_level=40)}
    coordinator.state_room_count = 1
    coordinator.last_update_success = True
    coordinator.last_job_error = None
    coordinator.update_interval = None
    coordinator.room_available.return_value = True
    coordinator.data_health_attributes.return_value = {}
    coordinator.async_discover_gateway_units = AsyncMock(return_value=[2, 3])
    coordinator.client.transport_diagnostics.return_value = {
        "consecutive_timeouts": 0,
        "link_recycles": 1,
        "seconds_since_any_answer": 2.5,
    }
    return coordinator


def _entry(hass: HomeAssistant, *, with_runtime: bool = True) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Meltem",
        data={CONF_PORT: _PORT, CONF_ROOMS: [{"key": "unit_1", "slave": 2}]},
        options={CONF_PORT: _PORT},
        version=1,
        source="user",
    )
    entry.add_to_hass(hass)
    if with_runtime:
        entry.runtime_data = types.SimpleNamespace(coordinator=_coordinator())
    return entry


def _loaded_coordinator(hass: HomeAssistant) -> MagicMock:
    entry = _entry(hass)
    entry.mock_state(hass, ConfigEntryState.LOADED)
    return entry.runtime_data.coordinator


def _dump(diagnostics: dict[str, Any]) -> str:
    return json.dumps(diagnostics, cls=ExtendedJSONEncoder)


class TestDiagnostics:
    async def test_serial_port_is_redacted_everywhere(self, hass: HomeAssistant) -> None:
        """The diagnostics download is attached to public issues."""
        entry = _entry(hass)
        coordinator = entry.runtime_data.coordinator
        coordinator.async_discover_gateway_units.side_effect = MeltemModbusError(
            f"Probe failed at {_PORT}"
        )
        coordinator.last_job_error = MeltemModbusError(f"Job failed at {_PORT}")
        coordinator.safe_data = {
            "unit_1": RoomState().with_read_health(
                "flow", ReadHealth(last_error=f"Read failed at {_PORT}")
            )
        }

        result = await async_get_config_entry_diagnostics(hass, entry)
        diagnostics = result["coordinator"]

        assert _SECRET not in _dump(result)
        assert result["entry"]["data"][CONF_PORT] == REDACTED
        assert result["entry"]["options"][CONF_PORT] == REDACTED
        assert diagnostics["gateway_probe_error"] == (
            f"MeltemModbusError: Probe failed at {REDACTED}"
        )
        assert diagnostics["last_job_error"] == f"Job failed at {REDACTED}"
        assert diagnostics["room_states"]["unit_1"]["group_read_health"][0][1][
            "last_error"
        ] == f"Read failed at {REDACTED}"

    async def test_result_is_json_serialisable(self, hass: HomeAssistant) -> None:
        result = await async_get_config_entry_diagnostics(hass, _entry(hass))

        reloaded = json.loads(_dump(result))

        # frozensets would otherwise turn into an opaque type marker.
        assert reloaded["coordinator"]["rooms"][0]["supported_entity_keys"] == [
            "extract_level",
            "supply_level",
        ]

    async def test_reports_gateway_units(self, hass: HomeAssistant) -> None:
        result = await async_get_config_entry_diagnostics(hass, _entry(hass))
        diagnostics = result["coordinator"]

        assert diagnostics["gateway_units"] == [2, 3]
        assert diagnostics["gateway_probe_error"] is None
        assert diagnostics["update_interval_seconds"] is None
        assert diagnostics["transport"]["link_recycles"] == 1

    async def test_reports_a_failing_gateway_probe(self, hass: HomeAssistant) -> None:
        entry = _entry(hass)
        entry.runtime_data.coordinator.async_discover_gateway_units.side_effect = (
            MeltemModbusError("boom")
        )

        result = await async_get_config_entry_diagnostics(hass, entry)
        diagnostics = result["coordinator"]

        assert diagnostics["gateway_units"] is None
        assert diagnostics["gateway_probe_error"] == "MeltemModbusError: boom"

    async def test_lists_unavailable_rooms(self, hass: HomeAssistant) -> None:
        entry = _entry(hass)
        entry.runtime_data.coordinator.room_available.return_value = False

        result = await async_get_config_entry_diagnostics(hass, entry)

        assert result["coordinator"]["unavailable_rooms"] == ["unit_1"]

    async def test_survives_an_entry_without_runtime_data(
        self, hass: HomeAssistant,
    ) -> None:
        entry = _entry(hass, with_runtime=False)

        result = await async_get_config_entry_diagnostics(hass, entry)

        assert result["coordinator"] is None
        assert result["entry"]["data"][CONF_PORT] == REDACTED


class TestSystemHealth:
    async def test_reports_nothing_without_entries(self, hass: HomeAssistant) -> None:
        assert await system_health_info(hass) == {"loaded_entries": 0}

    async def test_ignores_entries_that_are_not_loaded(
        self, hass: HomeAssistant,
    ) -> None:
        """Accessing runtime_data of a failed entry would raise."""
        entry = _entry(hass, with_runtime=False)
        entry.mock_state(hass, ConfigEntryState.SETUP_RETRY)

        assert await system_health_info(hass) == {"loaded_entries": 0}

    async def test_reports_coordinator_state(self, hass: HomeAssistant) -> None:
        _loaded_coordinator(hass)

        assert await system_health_info(hass) == {
            "loaded_entries": 1,
            "configured_units": 1,
            "state_units": 1,
            "last_update_success": True,
            "last_job_error": "none",
            "unavailable_units": "none",
            "stale_read_groups": "none",
        }

    async def test_names_unavailable_units(self, hass: HomeAssistant) -> None:
        _loaded_coordinator(hass).room_available.return_value = False

        info = await system_health_info(hass)

        assert info["unavailable_units"] == "unit_1"

    @pytest.mark.parametrize(
        ("health", "expected"),
        [
            (
                {
                    "flow": {"stale": True},
                    "status": {"stale": None},
                    "temperature": {"stale": True},
                    "writes": {},
                },
                "unit_1: flow, temperature",
            ),
            ({"flow": {"stale": False}, "writes": {}}, "none"),
        ],
        ids=("stale", "fresh"),
    )
    async def test_summarizes_stale_read_groups_as_plain_text(
        self,
        hass: HomeAssistant,
        health: dict[str, dict[str, bool | None]],
        expected: str,
    ) -> None:
        """The system information dialog renders nested dicts as empty cells."""
        _loaded_coordinator(hass).data_health_attributes.return_value = health

        info = await system_health_info(hass)

        assert info["stale_read_groups"] == expected
        assert all(isinstance(value, (str, int, bool)) for value in info.values())

    async def test_keys_match_the_translations(self, hass: HomeAssistant) -> None:
        _loaded_coordinator(hass)
        strings = json.loads(_STRINGS.read_text(encoding="utf-8"))

        info = await system_health_info(hass)

        assert set(info) == set(strings["system_health"]["info"])

    def test_registers_the_info_callback(self, hass: HomeAssistant) -> None:
        register = MagicMock()

        async_register(hass, register)

        register.async_register_info.assert_called_once_with(system_health_info)
