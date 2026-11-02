"""Tests for the diagnostics and system health helpers."""

from __future__ import annotations

import json
import types
from copy import deepcopy
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from homeassistant.components.diagnostics import REDACTED
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.helpers.json import ExtendedJSONEncoder, JSONEncoder
from pytest_homeassistant.common import MockConfigEntry

from custom_components.meltem_ventilation.const import (
    CONF_CONNECTION_TYPE,
    CONF_HOST,
    CONF_NETWORK_PORT,
    CONF_PORT,
    CONF_ROOMS,
    CONNECTION_RTU_OVER_TCP,
    DOMAIN,
)
from custom_components.meltem_ventilation.coordinator import MeltemDataUpdateCoordinator
from custom_components.meltem_ventilation.cumulative_diagnostics import CumulativeDiagnostics
from custom_components.meltem_ventilation.diagnostics import (
    async_get_config_entry_diagnostics,
)
from custom_components.meltem_ventilation.modbus_client import MeltemModbusClient
from custom_components.meltem_ventilation.modbus_helpers import MeltemModbusError
from custom_components.meltem_ventilation.models import (
    ReadHealth,
    RoomConfig,
    RoomReadError,
    RoomState,
)
from custom_components.meltem_ventilation.system_health import (
    async_register,
    system_health_info,
)

from .common import mock_coordinator

_STRINGS = (
    Path(__file__).resolve().parents[3]
    / "custom_components"
    / "meltem_ventilation"
    / "strings.json"
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
    coordinator = mock_coordinator()
    coordinator.client = MagicMock()
    coordinator.rooms = [_ROOM]
    coordinator.safe_data = {"unit_1": RoomState(target_level=40)}
    coordinator.state_room_count = 1
    coordinator.last_update_success = True
    coordinator.last_job_error = None
    coordinator.last_room_errors = {}
    coordinator.next_poll_delay = 0.25
    coordinator.poll_scheduled = True
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
    @pytest.mark.parametrize("with_runtime", [False, True])
    async def test_network_host_is_redacted(self, hass: HomeAssistant, with_runtime: bool) -> None:
        entry = _entry(hass, with_runtime=with_runtime)
        hass.config_entries.async_update_entry(
            entry,
            data={
                CONF_CONNECTION_TYPE: CONNECTION_RTU_OVER_TCP,
                CONF_HOST: "private-bridge.local",
                CONF_NETWORK_PORT: 5020,
            },
        )
        if with_runtime:
            coordinator = entry.runtime_data.coordinator
            coordinator.last_job_error = MeltemModbusError("socket://private-bridge.local:5020")
            coordinator.async_discover_gateway_units.side_effect = MeltemModbusError(
                "private-bridge.local is unavailable"
            )
            coordinator.last_room_errors = {
                "unit_1": RoomReadError(
                    "private-bridge.local failed", datetime(2026, 11, 2, 12, 0, tzinfo=UTC)
                )
            }
            coordinator.safe_data = {
                "unit_1": RoomState().with_read_health(
                    "flow", ReadHealth(last_error="private-bridge.local failed")
                )
            }
        result = await async_get_config_entry_diagnostics(hass, entry)
        assert "private-bridge.local" not in _dump(result)
        assert result["entry"]["data"][CONF_HOST] == REDACTED

    @pytest.mark.parametrize("with_runtime", [False, True])
    async def test_setup_codes_are_redacted_everywhere_without_mutating_sources(
        self, hass: HomeAssistant, with_runtime: bool
    ) -> None:
        entry = _entry(hass, with_runtime=with_runtime)
        entry.data[CONF_ROOMS][0].update(app_setup_code="R00AB12", preview="R-Code R00AB12 | ID 42")
        entry.data[CONF_ROOMS].append({"key": "unit_2", "preview": "R-Code R000123 | ID 43"})
        original_data = deepcopy(dict(entry.data))
        if with_runtime:
            coordinator = entry.runtime_data.coordinator
            room = replace(_ROOM, app_setup_code="R00AB12", preview="R-Code R00AB12 | ID 42")
            coordinator.rooms = [room]
            coordinator.last_job_error = MeltemModbusError("Read R000456 failed")
            coordinator.async_discover_gateway_units.side_effect = MeltemModbusError(
                "Probe R000789 failed"
            )
            coordinator.safe_data = {
                "unit_1": RoomState().with_read_health(
                    "flow", ReadHealth(last_error="Read R000ABC failed")
                )
            }
            coordinator.client.transport_diagnostics.return_value = {
                "error": "R000DEF failed",
                "detail": "embeddedR000BADsuffix",
                "app_setup_code": "not-a-formatted-code",
            }

        result = await async_get_config_entry_diagnostics(hass, entry)
        rendered = _dump(result)

        for secret in ("R00AB12", "R000123", "R000456", "R000789", "R000ABC", "R000DEF", "R000BAD"):
            assert secret not in rendered
        assert "not-a-formatted-code" not in rendered
        assert result["entry"]["data"][CONF_ROOMS][0]["app_setup_code"] == REDACTED
        assert result["entry"]["data"][CONF_ROOMS][0]["preview"] == f"R-Code {REDACTED} | ID 42"
        assert dict(entry.data) == original_data
        if with_runtime:
            coordinator = entry.runtime_data.coordinator
            assert coordinator.rooms == [
                replace(_ROOM, app_setup_code="R00AB12", preview="R-Code R00AB12 | ID 42")
            ]
            assert coordinator.rooms[0].app_setup_code == "R00AB12"
            assert coordinator.last_job_error.args == ("Read R000456 failed",)

    @pytest.mark.parametrize(
        ("message", "expected"),
        [
            ("ReadBack failed", "ReadBack failed"),
            ("Read0001 refused", "Read0001 refused"),
            ("Rfaceb00c", "Rfaceb00c"),
            ("code r00ab12 seen", f"code {REDACTED} seen"),
            ("code R00ab12 seen", f"code {REDACTED} seen"),
            ("embeddedR000BADsuffix", f"embedded{REDACTED}suffix"),
        ],
    )
    async def test_setup_code_redaction_keeps_ordinary_words(
        self, hass: HomeAssistant, message: str, expected: str
    ) -> None:
        entry = _entry(hass)
        coordinator = entry.runtime_data.coordinator
        coordinator.last_job_error = MeltemModbusError(message)

        result = await async_get_config_entry_diagnostics(hass, entry)

        assert result["coordinator"]["last_job_error"] == expected

    async def test_serial_port_is_redacted_everywhere(self, hass: HomeAssistant) -> None:
        """The diagnostics download is attached to public issues."""
        entry = _entry(hass)
        coordinator = entry.runtime_data.coordinator
        coordinator.async_discover_gateway_units.side_effect = MeltemModbusError(
            f"Probe failed at {_PORT}"
        )
        coordinator.last_job_error = MeltemModbusError(f"Job failed at {_PORT}")
        coordinator.last_room_errors = {
            "unit_1": RoomReadError(
                f"Read failed at {_PORT}", datetime(2026, 11, 2, 12, 0, tzinfo=UTC)
            )
        }
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
        assert diagnostics["last_room_errors"] == {
            "unit_1": {"error": f"Read failed at {REDACTED}", "at": "2026-11-02T12:00:00+00:00"}
        }
        assert (
            diagnostics["room_states"]["unit_1"]["group_read_health"][0][1]["last_error"]
            == f"Read failed at {REDACTED}"
        )
        assert "last_read_job" not in diagnostics["room_states"]["unit_1"]

    async def test_result_is_json_serialisable(self, hass: HomeAssistant) -> None:
        entry = _entry(hass)
        coordinator = entry.runtime_data.coordinator
        read_at = datetime(2026, 10, 29, 10, 0, tzinfo=UTC)
        coordinator.next_poll_delay = 15.0
        coordinator.safe_data = {
            "unit_1": RoomState(
                supply_air_flow=40,
                extract_air_flow=35,
                operation_mode="manual",
                target_level=40,
            ).with_read_health(
                "flow",
                ReadHealth(
                    last_attempt=read_at,
                    last_successful_read=read_at,
                    consecutive_failures=1,
                    last_error="temporary failure",
                ),
            )
        }

        result = await async_get_config_entry_diagnostics(hass, entry)
        # Unlike ExtendedJSONEncoder, this fails on an unsupported object
        # instead of silently turning it into an opaque repr marker.
        reloaded = json.loads(json.dumps(result, cls=JSONEncoder))

        assert reloaded == json.loads(_dump(result))
        assert reloaded["entry"]["data"][CONF_PORT] == REDACTED
        assert reloaded["coordinator"]["next_poll_delay_seconds"] == 15
        assert reloaded["coordinator"]["poll_scheduled"] is True
        assert reloaded["coordinator"]["gateway_units"] == [2, 3]
        assert reloaded["coordinator"]["rooms"][0]["supported_entity_keys"] == [
            "extract_level",
            "supply_level",
        ]
        state = reloaded["coordinator"]["room_states"]["unit_1"]
        assert state["supply_air_flow"] == 40
        assert state["extract_air_flow"] == 35
        assert state["operation_mode"] == "manual"
        assert state["target_level"] == 40
        assert state["group_read_health"] == [
            [
                "flow",
                {
                    "last_attempt": "2026-10-29T10:00:00+00:00",
                    "last_successful_read": "2026-10-29T10:00:00+00:00",
                    "consecutive_failures": 1,
                    "last_error": "temporary failure",
                    "last_error_timed_out": False,
                },
            ]
        ]
        assert "last_read_job" not in state

    async def test_reports_gateway_units(self, hass: HomeAssistant) -> None:
        result = await async_get_config_entry_diagnostics(hass, _entry(hass))
        diagnostics = result["coordinator"]

        assert diagnostics["gateway_units"] == [2, 3]
        assert diagnostics["gateway_probe_error"] is None
        assert diagnostics["next_poll_delay_seconds"] == 0.25
        assert diagnostics["transport"]["link_recycles"] == 1

    async def test_reports_cumulative_diagnostics(self, hass: HomeAssistant) -> None:
        entry = _entry(hass)
        coordinator = entry.runtime_data.coordinator
        cumulative = await CumulativeDiagnostics.async_create(hass, entry.entry_id)
        cumulative.record_request("no_answer")
        cumulative.record_link_recycle()
        cumulative.record_read_failure("unit_1", "flow")
        cumulative.record_write_issue("unit_1", "airflow_levels", "mismatch")
        coordinator.cumulative_diagnostics = cumulative

        result = await async_get_config_entry_diagnostics(hass, entry)

        assert result["coordinator"]["cumulative_diagnostics"] == {
            "period_started_at": cumulative.period_started_at,
            "transport": {
                "request_attempts": 1,
                "request_failures": 1,
                "request_failures_by_category": {
                    "no_answer": 1,
                    "modbus_exception": 0,
                    "other": 0,
                },
                "successful_attempts": 0,
                "link_recycles": 1,
            },
            "read_failures": {"unit_1": {"flow": 1}},
            "write_issues": {"unit_1": {"readback_mismatch": {"airflow_levels": 1}}},
        }

    async def test_reports_a_failing_gateway_probe(self, hass: HomeAssistant) -> None:
        entry = _entry(hass)
        entry.runtime_data.coordinator.async_discover_gateway_units.side_effect = MeltemModbusError(
            "boom"
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

    async def test_captures_transport_counters_before_discovery_probe(
        self, hass: HomeAssistant
    ) -> None:
        from modbus_connection.mock import MockModbusConnection

        link = MockModbusConnection()
        link.for_unit(1).holding.update({43901: 1, 43902: [2]})
        client = MeltemModbusClient(link.for_unit, port=_PORT)
        client._policy._consecutive_timeouts = 2
        entry = _entry(hass, with_runtime=False)
        coordinator = MeltemDataUpdateCoordinator(
            hass,
            config_entry=entry,
            client=client,
            rooms=[_ROOM],
            max_requests_per_second=10.0,
        )
        coordinator._last_job_error = MeltemModbusError(f"Job failed at {_PORT}")
        entry.runtime_data = types.SimpleNamespace(coordinator=coordinator)

        result = await async_get_config_entry_diagnostics(hass, entry)

        assert result["coordinator"]["transport"]["consecutive_timeouts"] == 2
        assert result["coordinator"]["last_job_error"] == f"Job failed at {REDACTED}"
        client.shutdown()

    async def test_survives_an_entry_without_runtime_data(
        self,
        hass: HomeAssistant,
    ) -> None:
        entry = _entry(hass, with_runtime=False)

        result = await async_get_config_entry_diagnostics(hass, entry)

        assert result["coordinator"] is None
        assert result["entry"]["data"][CONF_PORT] == REDACTED


class TestSystemHealth:
    async def test_redacts_network_host_in_errors(self, hass: HomeAssistant) -> None:
        entry = _entry(hass)
        hass.config_entries.async_update_entry(
            entry,
            data={
                CONF_CONNECTION_TYPE: CONNECTION_RTU_OVER_TCP,
                CONF_HOST: "private-bridge.local",
                CONF_NETWORK_PORT: 5020,
            },
        )
        entry.mock_state(hass, ConfigEntryState.LOADED)
        entry.runtime_data.coordinator.last_job_error = MeltemModbusError(
            "socket://private-bridge.local:5020"
        )
        info = await system_health_info(hass)
        assert "private-bridge.local" not in str(info)
        assert info["last_job_error"] == f"socket://{REDACTED}:5020"

    async def test_reports_nothing_without_entries(self, hass: HomeAssistant) -> None:
        assert await system_health_info(hass) == {"loaded_entries": 0}

    async def test_ignores_entries_that_are_not_loaded(
        self,
        hass: HomeAssistant,
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
            "last_unit_errors": "none",
            "unavailable_units": "none",
            "stale_read_groups": "none",
        }

    async def test_lists_the_last_error_of_each_unit_with_its_time(
        self, hass: HomeAssistant
    ) -> None:
        coordinator = _loaded_coordinator(hass)
        unit_2 = replace(_ROOM, key="unit_2", slave=3)
        coordinator.rooms = [_ROOM, unit_2]
        at = datetime(2026, 11, 2, 12, 0, 5, 123456, tzinfo=UTC)
        coordinator.last_room_errors = {
            "unit_2": RoomReadError(f"Lost {_PORT}", at),
            "unit_1": RoomReadError("timeout", at),
        }

        info = await system_health_info(hass)

        assert info["last_unit_errors"] == (
            f"unit_1: timeout (2026-11-02T12:00:05+00:00); "
            f"unit_2: Lost {REDACTED} (2026-11-02T12:00:05+00:00)"
        )

    async def test_names_unavailable_units(self, hass: HomeAssistant) -> None:
        _loaded_coordinator(hass).room_available.return_value = False

        info = await system_health_info(hass)

        assert info["unavailable_units"] == "unit_1"

    async def test_redacts_the_serial_port_in_the_last_error(
        self,
        hass: HomeAssistant,
    ) -> None:
        """The system information is pasted into public issues as well."""
        _loaded_coordinator(hass).last_job_error = MeltemModbusError(f"Lost {_PORT}")

        info = await system_health_info(hass)

        assert _SECRET not in info["last_job_error"]
        assert info["last_job_error"] == f"Lost {REDACTED}"

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
