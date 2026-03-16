"""Tests for coordinator logic and config-flow helper functions."""

from __future__ import annotations

import asyncio
import types
from unittest.mock import AsyncMock, patch

import pytest

from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.meltem_ventilation.config_flow import (
    CONF_MAX_REQUESTS_PER_SECOND,
    CONF_PORT,
    CONF_ROOMS,
    MeltemVentilationOptionsFlow,
    _build_rooms_from_profiles,
    _default_room_name,
    _detected_profile_default,
    _profile_label,
)
from custom_components.meltem_ventilation.const import DOMAIN
from custom_components.meltem_ventilation.coordinator import (
    MeltemDataUpdateCoordinator,
    PollJob,
)
from custom_components.meltem_ventilation.models import (
    RefreshPlan,
    RoomConfig,
    RoomState,
)
from custom_components.meltem_ventilation.modbus_helpers import MeltemModbusError


# ---------------------------------------------------------------------------
#  Test doubles
# ---------------------------------------------------------------------------


class _FakeClient:
    """Stand-in for MeltemModbusClient with no serial port dependency."""

    def __init__(self) -> None:
        self.discover_calls: list[tuple[int, int]] = []
        self.probe_calls: list[int] = []
        self.read_calls: list[tuple[str, RefreshPlan]] = []
        self.write_level_calls: list[tuple[str, int]] = []
        self.write_unbalanced_calls: list[tuple[str, int, int]] = []
        self.next_read_state = RoomState(current_level=42)

    def discover_gateway_units(self, start: int, end: int) -> list[int]:
        self.discover_calls.append((start, end))
        return [2, 3, 4]

    def probe_slave_details(self, slave: int) -> tuple[str, str | None, list[str]]:
        self.probe_calls.append(slave)
        return ("plain", f"ID {slave}", ["level"])

    def read_room_state(
        self,
        room: RoomConfig,
        previous_state: RoomState,
        refresh_plan: RefreshPlan,
    ) -> RoomState:
        self.read_calls.append((room.key, refresh_plan))
        if room.key == "broken":
            raise MeltemModbusError("boom")
        return self.next_read_state

    def write_level(self, room: RoomConfig, level: int) -> None:
        self.write_level_calls.append((room.key, level))

    def write_unbalanced_levels(
        self, room: RoomConfig, supply_level: int, extract_level: int
    ) -> None:
        self.write_unbalanced_calls.append((room.key, supply_level, extract_level))

    def reset_connection(self) -> None:
        return None


def _build_coordinator(
    hass: HomeAssistant,
    rooms: list[RoomConfig],
) -> tuple[MeltemDataUpdateCoordinator, _FakeClient]:
    """Create a coordinator with a fake client."""
    client = _FakeClient()
    coordinator = MeltemDataUpdateCoordinator(
        hass,
        client=client,
        rooms=rooms,
        max_requests_per_second=2.0,
    )
    return coordinator, client


# ---------------------------------------------------------------------------
#  Config-flow helpers
# ---------------------------------------------------------------------------


class TestConfigFlowHelpers:
    def test_detected_profile_defaults_map_capabilities_to_ii_profiles(self) -> None:
        assert _detected_profile_default(2, {2: "plain"}) == "ii_plain"
        assert _detected_profile_default(2, {2: "f"}) == "ii_f"
        assert _detected_profile_default(2, {2: "fc"}) == "ii_fc"
        assert _detected_profile_default(2, {2: "fc_voc"}) == "ii_fc_voc"
        assert _detected_profile_default(2, {}) == "ii_plain"

    def test_profile_label_includes_preview_when_present(self) -> None:
        assert _profile_label(1, 2, {}) == "Unit 1 profile"
        assert (
            _profile_label(1, 2, {2: "ID 116852 | basic"})
            == "Unit 1 profile (ID 116852 | basic)"
        )

    def test_build_rooms_from_profiles_preserves_existing_metadata(self) -> None:
        selected_profiles = {"Unit 1 profile (ID 116852 | basic)": "ii_plain"}
        rooms = _build_rooms_from_profiles(
            [2],
            selected_profiles,
            previews_by_slave={2: "ID 116852 | basic"},
            existing_rooms_by_slave={
                2: {
                    "key": "bathroom",
                    "name": "Bathroom",
                    "supported_entity_keys": ["level", "supply_level"],
                }
            },
        )

        assert rooms == [
            {
                "key": "bathroom",
                "name": "Bathroom",
                "slave": 2,
                "profile": "ii_plain",
                "preview": "ID 116852 | basic",
                "supported_entity_keys": rooms[0]["supported_entity_keys"],
            }
        ]
        assert "level" in rooms[0]["supported_entity_keys"]
        assert "supply_level" in rooms[0]["supported_entity_keys"]
        assert "humidity_extract_air" not in rooms[0]["supported_entity_keys"]

    def test_build_rooms_from_profiles_uses_english_field_keys(self) -> None:
        selected_profiles = {"Unit 1 profile (ID 116852 | basic)": "ii_plain"}
        rooms = _build_rooms_from_profiles(
            [2],
            selected_profiles,
            previews_by_slave={2: "ID 116852 | basic"},
        )

        assert rooms[0]["profile"] == "ii_plain"
        assert rooms[0]["name"] == "Unit 1"

    def test_default_room_name(self) -> None:
        assert _default_room_name(1) == "Unit 1"
        assert _default_room_name(2) == "Unit 2"

    def test_build_rooms_from_profiles_generates_unique_keys_for_new_rooms(self) -> None:
        selected_profiles = {
            "Unit 1 profile": "ii_plain",
            "Unit 2 profile": "ii_plain",
            "Unit 3 profile": "ii_plain",
        }
        rooms = _build_rooms_from_profiles(
            [2, 3, 4],
            selected_profiles,
            existing_rooms_by_slave={
                2: {"key": "unit_1", "name": "Unit 1"},
                4: {"key": "unit_2", "name": "Unit 2"},
            },
        )

        assert [room["key"] for room in rooms] == ["unit_1", "slave_3", "unit_2"]


# ---------------------------------------------------------------------------
#  Coordinator
# ---------------------------------------------------------------------------


class TestCoordinator:
    async def test_async_discover_gateway_units_uses_client(
        self, hass: HomeAssistant,
    ) -> None:
        coordinator, client = _build_coordinator(
            hass,
            [RoomConfig(key="unit_1", name="Unit 1", profile="ii_plain", slave=2)],
        )

        discovered = await coordinator.async_discover_gateway_units()

        assert discovered == [2, 3, 4]
        assert client.discover_calls == [(2, 16)]

    async def test_async_probe_slave_details_uses_client(
        self, hass: HomeAssistant,
    ) -> None:
        coordinator, client = _build_coordinator(
            hass,
            [RoomConfig(key="unit_1", name="Unit 1", profile="ii_plain", slave=2)],
        )

        details = await coordinator.async_probe_slave_details(4)

        assert details == ("plain", "ID 4", ["level"])
        assert client.probe_calls == [4]

    async def test_async_set_level_writes_without_forced_refresh(
        self, hass: HomeAssistant,
    ) -> None:
        coordinator, client = _build_coordinator(
            hass,
            [RoomConfig(key="unit_1", name="Unit 1", profile="ii_plain", slave=2)],
        )
        coordinator.data = {"unit_1": RoomState(current_level=30)}
        await coordinator.async_set_level("unit_1", 55)

        assert client.write_level_calls == [("unit_1", 55)]
        assert coordinator.data["unit_1"].current_level == 30
        assert client.read_calls == []

    async def test_async_set_unbalanced_levels_writes_without_forced_refresh(
        self, hass: HomeAssistant,
    ) -> None:
        coordinator, client = _build_coordinator(
            hass,
            [RoomConfig(key="unit_1", name="Unit 1", profile="ii_plain", slave=2)],
        )
        coordinator.data = {
            "unit_1": RoomState(current_level=30, extract_target_level=35)
        }
        await coordinator.async_set_unbalanced_levels("unit_1", 40, 35)

        assert client.write_unbalanced_calls == [("unit_1", 40, 35)]
        assert client.read_calls == []

    def test_build_jobs_only_includes_relevant_groups(
        self, hass: HomeAssistant,
    ) -> None:
        coordinator, _ = _build_coordinator(
            hass,
            [
                RoomConfig(
                    key="temps_only",
                    name="Temps Only",
                    profile="ii_plain",
                    slave=2,
                    supported_entity_keys=frozenset({"supply_air_temperature"}),
                ),
                RoomConfig(
                    key="flow_only",
                    name="Flow Only",
                    profile="ii_plain",
                    slave=3,
                    supported_entity_keys=frozenset(
                        {"extract_air_flow", "current_level"}
                    ),
                ),
            ],
        )

        jobs = {(job.room_key, job.key) for job in coordinator._jobs}

        assert ("temps_only", "temperature") in jobs
        assert ("temps_only", "flow") not in jobs
        assert ("flow_only", "flow") in jobs
        assert ("flow_only", "hours") not in jobs

    def test_select_due_job_returns_earliest_due(
        self, hass: HomeAssistant,
    ) -> None:
        coordinator, _ = _build_coordinator(
            hass,
            [RoomConfig(key="unit_1", name="Unit 1", profile="ii_plain", slave=2)],
        )
        coordinator._jobs = [
            PollJob(
                "status",
                "unit_1",
                RefreshPlan.only(refresh_status=True),
                60,
                15.0,
            ),
            PollJob(
                "flow",
                "unit_1",
                RefreshPlan.only(refresh_airflow=True),
                10,
                5.0,
            ),
        ]

        selected = coordinator._select_due_job(10.0)

        assert selected is not None
        assert selected.key == "flow"

    def test_read_one_job_keeps_previous_state_on_failure(
        self, hass: HomeAssistant,
    ) -> None:
        coordinator, _ = _build_coordinator(
            hass,
            [RoomConfig(key="broken", name="Broken", profile="ii_plain", slave=2)],
        )
        previous = {"broken": RoomState(current_level=30)}
        job = PollJob(
            "flow", "broken", RefreshPlan.only(refresh_airflow=True), 10, 0.0
        )

        state_map = coordinator._read_one_job(previous, job)

        assert state_map["broken"].current_level == 30


# ---------------------------------------------------------------------------
#  Options flow
# ---------------------------------------------------------------------------


class TestOptionsFlow:
    @staticmethod
    def _build_flow(hass: HomeAssistant):
        config_entry = MockConfigEntry(
            data={
                CONF_PORT: "/dev/serial/by-id/test",
                CONF_MAX_REQUESTS_PER_SECOND: 2.0,
                CONF_ROOMS: [
                    {
                        "key": "unit_1",
                        "name": "Unit 1",
                        "slave": 2,
                        "profile": "ii_plain",
                        "preview": "ID 116852 | basic",
                        "supported_entity_keys": ["level"],
                    }
                ],
            },
            options={},
            entry_id="entry-1",
            domain=DOMAIN,
            title="Meltem",
            version=1,
            source="user",
        )
        flow = MeltemVentilationOptionsFlow(config_entry)
        client = _FakeClient()
        coordinator = MeltemDataUpdateCoordinator(
            hass,
            client=client,
            rooms=[
                RoomConfig(
                    key="unit_1", name="Unit 1", profile="ii_plain", slave=2
                )
            ],
            max_requests_per_second=2.0,
        )
        hass.data.setdefault(DOMAIN, {})
        config_entry.runtime_data = types.SimpleNamespace(
            coordinator=coordinator
        )
        flow.hass = hass
        return flow, hass, config_entry, client

    async def test_options_init_shows_menu(
        self, hass: HomeAssistant,
    ) -> None:
        flow, _, _, _ = self._build_flow(hass)

        result = await flow.async_step_init(None)

        assert result["type"] == "menu"
        assert result["step_id"] == "init"

    async def test_options_init_menu_includes_expected_options(
        self, hass: HomeAssistant,
    ) -> None:
        flow, _, _, _ = self._build_flow(hass)

        result = await flow.async_step_init(None)

        assert set(result["menu_options"]) == {
            "edit_connection",
            "edit_profiles",
            "rescan_units",
        }

    async def test_options_profiles_updates_entry_data_and_reloads(
        self, hass: HomeAssistant,
    ) -> None:
        flow, hass_obj, config_entry, _client = self._build_flow(hass)
        flow._discovered_slaves = [2]
        flow._preview_by_slave = {2: "ID 116852 | basic"}
        flow._detected_profile_by_slave = {2: "plain"}
        flow._supported_entity_keys_by_slave = {2: ["level", "supply_level"]}
        flow.async_create_entry = lambda title="", data=None: {
            "type": "create_entry",
            "title": title,
            "data": data or {},
        }

        updated_entries: list = []
        reloaded: list = []

        def _sync_update_entry(entry, **kwargs):
            updated_entries.append((entry, kwargs))

        async def _async_reload(entry_id):
            reloaded.append(entry_id)

        hass.config_entries.async_update_entry = _sync_update_entry
        hass.config_entries.async_reload = _async_reload

        result = await flow.async_step_profiles(
            {"Unit 1 profile (ID 116852 | basic)": "ii_f"}
        )

        assert result["type"] == "create_entry"
        updated_data = updated_entries[0][1]["data"]
        assert updated_data[CONF_ROOMS][0]["profile"] == "ii_f"
        assert "humidity_extract_air" in updated_data[CONF_ROOMS][0]["supported_entity_keys"]
        assert "level" in updated_data[CONF_ROOMS][0]["supported_entity_keys"]
        assert reloaded == ["entry-1"]
