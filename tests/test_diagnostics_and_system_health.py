"""Tests for the diagnostics and system health helpers."""

from __future__ import annotations

import json
import types
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.helpers.json import ExtendedJSONEncoder
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.meltem_ventilation.const import CONF_PORT, CONF_ROOMS, DOMAIN
from custom_components.meltem_ventilation.diagnostics import (
    async_get_config_entry_diagnostics,
)
from custom_components.meltem_ventilation.modbus_helpers import MeltemModbusError
from custom_components.meltem_ventilation.models import RoomConfig, RoomState
from custom_components.meltem_ventilation.system_health import (
    async_register,
    system_health_info,
)

_COMPONENT_DIR = (
    Path(__file__).parent.parent / "custom_components" / "meltem_ventilation"
)

_ROOM = RoomConfig(
    key="unit_1",
    name="Unit 1",
    profile="ii_fc",
    slave=2,
    preview="ID 42 | CO2",
    supported_entity_keys=frozenset({"supply_level", "extract_level"}),
)


def _entry(hass: HomeAssistant, *, with_runtime: bool = True) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Meltem",
        data={
            CONF_PORT: "/dev/serial/by-id/secret-device",
            CONF_ROOMS: [{"key": "unit_1", "slave": 2}],
        },
        options={CONF_PORT: "/dev/serial/by-id/secret-device"},
        version=1,
        source="user",
    )
    entry.add_to_hass(hass)
    if with_runtime:
        entry.runtime_data = types.SimpleNamespace(coordinator=_coordinator())
    return entry


def _coordinator() -> MagicMock:
    coordinator = MagicMock()
    coordinator.rooms = [_ROOM]
    coordinator.safe_data = {"unit_1": RoomState(target_level=40)}
    coordinator.state_room_count = 1
    coordinator.last_update_success = True
    coordinator.last_job_error = None
    coordinator.update_interval = None
    coordinator.room_available.return_value = True
    coordinator.async_discover_gateway_units = AsyncMock(return_value=[2, 3])
    return coordinator


class TestDiagnostics:
    async def test_serial_port_is_redacted(self, hass: HomeAssistant) -> None:
        """The diagnostics download is attached to public issues."""
        entry = _entry(hass)

        result = await async_get_config_entry_diagnostics(hass, entry)

        dumped = json.dumps(result, cls=ExtendedJSONEncoder)
        assert "secret-device" not in dumped
        assert result["entry"]["data"][CONF_PORT] == "**REDACTED**"
        assert result["entry"]["options"][CONF_PORT] == "**REDACTED**"

    async def test_result_is_json_serialisable(self, hass: HomeAssistant) -> None:
        entry = _entry(hass)

        result = await async_get_config_entry_diagnostics(hass, entry)
        reloaded = json.loads(json.dumps(result, cls=ExtendedJSONEncoder))

        # frozensets would otherwise turn into an opaque type marker.
        assert reloaded["coordinator"]["rooms"][0]["supported_entity_keys"] == [
            "extract_level",
            "supply_level",
        ]

    async def test_reports_gateway_units(self, hass: HomeAssistant) -> None:
        entry = _entry(hass)

        result = await async_get_config_entry_diagnostics(hass, entry)

        assert result["coordinator"]["gateway_units"] == [2, 3]
        assert result["coordinator"]["gateway_probe_error"] is None
        assert result["coordinator"]["update_interval_seconds"] is None

    async def test_reports_a_failing_gateway_probe(self, hass: HomeAssistant) -> None:
        entry = _entry(hass)
        entry.runtime_data.coordinator.async_discover_gateway_units = AsyncMock(
            side_effect=MeltemModbusError("boom")
        )

        result = await async_get_config_entry_diagnostics(hass, entry)

        assert result["coordinator"]["gateway_units"] is None
        assert "MeltemModbusError" in result["coordinator"]["gateway_probe_error"]

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
        assert result["entry"]["data"][CONF_PORT] == "**REDACTED**"


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
        entry = _entry(hass)
        entry.mock_state(hass, ConfigEntryState.LOADED)

        info = await system_health_info(hass)

        assert info["loaded_entries"] == 1
        assert info["configured_units"] == 1
        assert info["state_units"] == 1
        assert info["last_update_success"] is True
        assert info["last_job_error"] == "none"
        assert info["unavailable_units"] == "none"

    async def test_names_unavailable_units(self, hass: HomeAssistant) -> None:
        entry = _entry(hass)
        entry.mock_state(hass, ConfigEntryState.LOADED)
        entry.runtime_data.coordinator.room_available.return_value = False

        info = await system_health_info(hass)

        assert info["unavailable_units"] == "unit_1"

    async def test_keys_match_the_translations(self, hass: HomeAssistant) -> None:
        entry = _entry(hass)
        entry.mock_state(hass, ConfigEntryState.LOADED)
        strings = json.loads(
            (_COMPONENT_DIR / "strings.json").read_text(encoding="utf-8")
        )

        info = await system_health_info(hass)

        assert set(info) == set(strings["system_health"]["info"])

    def test_registers_the_info_callback(self, hass: HomeAssistant) -> None:
        register = MagicMock()

        async_register(hass, register)

        register.async_register_info.assert_called_once_with(system_health_info)
