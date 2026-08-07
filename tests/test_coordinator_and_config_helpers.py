"""Tests for coordinator logic and config-flow helper functions."""

from __future__ import annotations

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
    _profile_field_key,
    _unit_details,
)
from custom_components.meltem_ventilation.const import DOMAIN
from custom_components.meltem_ventilation.coordinator import (
    ROOM_SILENT_AFTER_SECONDS,
    ROOM_UNAVAILABLE_AFTER_FAILURES,
    TRANSPORT_BACKOFF_AFTER_FAILURES,
    TRANSPORT_BACKOFF_MAX_SECONDS,
    TRANSPORT_BACKOFF_START_SECONDS,
    MeltemDataUpdateCoordinator,
    PollJob,
)
from custom_components.meltem_ventilation.modbus_helpers import MeltemModbusError
from custom_components.meltem_ventilation.models import (
    RefreshPlan,
    RoomConfig,
    RoomState,
)

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
        self.write_operating_mode_calls: list[tuple[str, str]] = []
        self.write_preset_mode_calls: list[tuple[str, str]] = []
        self.write_control_setting_calls: list[tuple[str, str, int]] = []
        self.silent_seconds_by_slave: dict[int, float] = {}
        self.next_read_state = RoomState(target_level=42)

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

    def write_operating_mode(
        self,
        room: RoomConfig,
        operation_mode: str,
        balanced_level: int,
        extract_level: int,
    ) -> None:
        self.write_operating_mode_calls.append((room.key, operation_mode))

    def write_preset_mode(
        self,
        room: RoomConfig,
        preset_mode: str,
    ) -> None:
        self.write_preset_mode_calls.append((room.key, preset_mode))

    def write_control_setting(
        self,
        room: RoomConfig,
        setting_key: str,
        value: int,
    ) -> int:
        self.write_control_setting_calls.append((room.key, setting_key, value))
        return value

    def reset_connection(self) -> None:
        return None

    def seconds_since_successful_read(self, slave: int) -> float | None:
        return self.silent_seconds_by_slave.get(slave)


def _mock_entry(hass: HomeAssistant) -> MockConfigEntry:
    entry = MockConfigEntry(domain=DOMAIN, title="Meltem", version=1, source="user")
    entry.add_to_hass(hass)
    return entry


def _build_coordinator(
    hass: HomeAssistant,
    rooms: list[RoomConfig],
) -> tuple[MeltemDataUpdateCoordinator, _FakeClient]:
    """Create a coordinator with a fake client."""
    client = _FakeClient()
    coordinator = MeltemDataUpdateCoordinator(
        hass,
        config_entry=_mock_entry(hass),
        client=client,
        rooms=rooms,
        max_requests_per_second=2.0,
    )
    return coordinator, client


# ---------------------------------------------------------------------------
#  Resilience: backoff, idle ticks, per-room availability
# ---------------------------------------------------------------------------


class TestCoordinatorResilience:
    def test_polling_backs_off_after_repeated_transport_failures(
        self, hass: HomeAssistant,
    ) -> None:
        coordinator, _ = _build_coordinator(
            hass,
            [RoomConfig(key="unit_1", name="Unit 1", profile="ii_plain", slave=2)],
        )
        normal_interval = coordinator.update_interval

        for _ in range(TRANSPORT_BACKOFF_AFTER_FAILURES):
            coordinator._consecutive_transport_failures += 1
            coordinator._apply_transport_backoff()

        assert coordinator.update_interval.total_seconds() == TRANSPORT_BACKOFF_START_SECONDS

        for _ in range(20):
            coordinator._consecutive_transport_failures += 1
            coordinator._apply_transport_backoff()

        assert coordinator.update_interval.total_seconds() == TRANSPORT_BACKOFF_MAX_SECONDS

        coordinator._on_transport_success()
        assert coordinator.update_interval == normal_interval

    def test_request_rate_change_does_not_cancel_active_backoff(
        self, hass: HomeAssistant,
    ) -> None:
        coordinator, _ = _build_coordinator(
            hass,
            [RoomConfig(key="unit_1", name="Unit 1", profile="ii_plain", slave=2)],
        )
        coordinator._consecutive_transport_failures = TRANSPORT_BACKOFF_AFTER_FAILURES
        coordinator._apply_transport_backoff()

        coordinator.update_request_rate(1.0)

        assert coordinator.update_interval.total_seconds() == TRANSPORT_BACKOFF_START_SECONDS

    def test_room_becomes_unavailable_after_repeated_read_failures(
        self, hass: HomeAssistant,
    ) -> None:
        coordinator, _ = _build_coordinator(
            hass,
            [RoomConfig(key="unit_1", name="Unit 1", profile="ii_plain", slave=2)],
        )
        coordinator.data = {"unit_1": RoomState(target_level=30)}
        assert coordinator.room_available("unit_1")

        coordinator._room_failures["unit_1"] = ROOM_UNAVAILABLE_AFTER_FAILURES
        assert not coordinator.room_available("unit_1")

    def test_room_without_any_data_is_unavailable(self, hass: HomeAssistant) -> None:
        coordinator, _ = _build_coordinator(
            hass,
            [RoomConfig(key="unit_1", name="Unit 1", profile="ii_plain", slave=2)],
        )
        coordinator.data = {"unit_1": RoomState()}

        assert not coordinator.room_available("unit_1")

    def test_room_is_unavailable_before_the_first_poll(
        self, hass: HomeAssistant
    ) -> None:
        coordinator, _ = _build_coordinator(
            hass,
            [RoomConfig(key="unit_1", name="Unit 1", profile="ii_plain", slave=2)],
        )

        assert coordinator.data is None
        assert not coordinator.room_available("unit_1")

    def test_state_room_count_ignores_empty_room_states(
        self, hass: HomeAssistant
    ) -> None:
        coordinator, _ = _build_coordinator(
            hass,
            [
                RoomConfig(key="empty", name="Empty", profile="ii_plain", slave=2),
                RoomConfig(key="live", name="Live", profile="ii_plain", slave=3),
            ],
        )
        coordinator.data = {
            "empty": RoomState(),
            "live": RoomState(target_level=30),
        }

        assert coordinator.state_room_count == 1

    def test_silent_unit_becomes_unavailable_despite_cached_values(
        self, hass: HomeAssistant,
    ) -> None:
        """Optional reads swallow errors, so cached values alone prove nothing."""
        coordinator, client = _build_coordinator(
            hass,
            [RoomConfig(key="unit_1", name="Unit 1", profile="ii_plain", slave=2)],
        )
        coordinator.data = {"unit_1": RoomState(target_level=30)}

        client.silent_seconds_by_slave[2] = ROOM_SILENT_AFTER_SECONDS - 1
        assert coordinator.room_available("unit_1")

        client.silent_seconds_by_slave[2] = ROOM_SILENT_AFTER_SECONDS + 1
        assert not coordinator.room_available("unit_1")

    def test_failed_job_marks_only_the_affected_room(self, hass: HomeAssistant) -> None:
        rooms = [
            RoomConfig(key="broken", name="Broken", profile="ii_plain", slave=2),
            RoomConfig(key="unit_2", name="Unit 2", profile="ii_plain", slave=3),
        ]
        coordinator, _ = _build_coordinator(hass, rooms)
        previous = {"broken": RoomState(target_level=10), "unit_2": RoomState(target_level=20)}

        for _ in range(ROOM_UNAVAILABLE_AFTER_FAILURES):
            coordinator._read_one_job(
                previous,
                PollJob(
                    key="flow_broken",
                    room_key="broken",
                    refresh_plan=RefreshPlan.only(refresh_airflow=True),
                    interval_seconds=10,
                    next_due=0.0,
                ),
            )

        coordinator.data = previous
        assert not coordinator.room_available("broken")
        assert coordinator.room_available("unit_2")

    async def test_failing_jobs_trigger_the_polling_backoff(
        self, hass: HomeAssistant,
    ) -> None:
        """_read_one_job swallows the error, so success must not be assumed."""
        coordinator, _ = _build_coordinator(
            hass,
            [RoomConfig(key="broken", name="Broken", profile="ii_plain", slave=2)],
        )
        coordinator.data = {"broken": RoomState(target_level=30)}
        normal_interval = coordinator.update_interval

        for _ in range(TRANSPORT_BACKOFF_AFTER_FAILURES):
            for job in coordinator._jobs:
                job.next_due = 0.0
            await coordinator._async_update_data()

        assert coordinator.update_interval != normal_interval
        assert (
            coordinator.update_interval.total_seconds()
            >= TRANSPORT_BACKOFF_START_SECONDS
        )

    async def test_successful_job_restores_the_normal_rate(
        self, hass: HomeAssistant,
    ) -> None:
        coordinator, _ = _build_coordinator(
            hass,
            [RoomConfig(key="unit_1", name="Unit 1", profile="ii_plain", slave=2)],
        )
        coordinator.data = {"unit_1": RoomState(target_level=30)}
        normal_interval = coordinator.update_interval
        coordinator._consecutive_transport_failures = TRANSPORT_BACKOFF_AFTER_FAILURES
        coordinator._apply_transport_backoff()

        for job in coordinator._jobs:
            job.next_due = 0.0
        await coordinator._async_update_data()

        assert coordinator.update_interval == normal_interval


# ---------------------------------------------------------------------------
#  Airflow target levels
# ---------------------------------------------------------------------------


_UNIT_1 = RoomConfig(key="unit_1", name="Unit 1", profile="ii_plain", slave=2)


class TestEffectiveLevels:
    def test_balanced_mode_reports_one_value_for_both_directions(
        self, hass: HomeAssistant,
    ) -> None:
        coordinator, _ = _build_coordinator(hass, [_UNIT_1])
        coordinator.data = {
            "unit_1": RoomState(operation_mode="manual", target_level=45)
        }

        assert coordinator.effective_levels("unit_1") == (45, 45)

    def test_unbalanced_mode_reports_both_targets(self, hass: HomeAssistant) -> None:
        coordinator, _ = _build_coordinator(hass, [_UNIT_1])
        coordinator.data = {
            "unit_1": RoomState(
                operation_mode="unbalanced",
                target_level=60,
                extract_target_level=20,
            )
        }

        assert coordinator.effective_levels("unit_1") == (60, 20)

    def test_off_reports_zero(self, hass: HomeAssistant) -> None:
        coordinator, _ = _build_coordinator(hass, [_UNIT_1])
        coordinator.data = {"unit_1": RoomState(operation_mode="off", target_level=40)}

        assert coordinator.effective_levels("unit_1") == (0, 0)

    def test_falls_back_to_measured_airflow(self, hass: HomeAssistant) -> None:
        coordinator, _ = _build_coordinator(hass, [_UNIT_1])
        coordinator.data = {
            "unit_1": RoomState(
                operation_mode="unbalanced",
                supply_air_flow=55,
                extract_air_flow=25,
            )
        }

        assert coordinator.effective_levels("unit_1") == (55, 25)

    @pytest.mark.parametrize(
        "sensor_mode", ["humidity_control", "co2_control", "automatic"]
    )
    def test_sensor_modes_report_measured_airflow(
        self, hass: HomeAssistant, sensor_mode: str,
    ) -> None:
        """41121 holds the mode selector there, so it must not become a level."""
        coordinator, _ = _build_coordinator(hass, [_UNIT_1])
        coordinator.data = {
            "unit_1": RoomState(
                operation_mode=sensor_mode,
                target_level=56,
                supply_air_flow=22,
                extract_air_flow=21,
            )
        }

        assert coordinator.effective_levels("unit_1") == (22, 21)

    def test_pending_write_wins_over_stale_state(self, hass: HomeAssistant) -> None:
        coordinator, _ = _build_coordinator(hass, [_UNIT_1])
        coordinator.data = {
            "unit_1": RoomState(operation_mode="manual", target_level=40)
        }

        coordinator._set_optimistic_levels("unit_1", 80, 20)

        assert coordinator.effective_levels("unit_1") == (80, 20)

    def test_overlay_is_dropped_once_the_gateway_confirms(
        self, hass: HomeAssistant,
    ) -> None:
        coordinator, _ = _build_coordinator(hass, [_UNIT_1])
        coordinator._set_optimistic_levels("unit_1", 80, 20)
        coordinator.data = {
            "unit_1": RoomState(
                operation_mode="unbalanced",
                target_level=80,
                extract_target_level=20,
            )
        }

        assert coordinator.effective_levels("unit_1") == (80, 20)
        assert coordinator._optimistic_levels.get("unit_1", None) is None

    def test_overlay_tolerates_rounding_between_percent_and_raw(
        self, hass: HomeAssistant,
    ) -> None:
        coordinator, _ = _build_coordinator(hass, [_UNIT_1])
        coordinator._set_optimistic_levels("unit_1", 80, 20)
        coordinator.data = {
            "unit_1": RoomState(
                operation_mode="unbalanced",
                target_level=79,
                extract_target_level=21,
            )
        }

        coordinator.effective_levels("unit_1")
        assert coordinator._optimistic_levels.get("unit_1", None) is None

    def test_overlay_expires(self, hass: HomeAssistant) -> None:
        coordinator, _ = _build_coordinator(hass, [_UNIT_1])
        coordinator.data = {
            "unit_1": RoomState(operation_mode="manual", target_level=40)
        }
        coordinator._optimistic_levels._pending["unit_1"] = ((80, 20), 0.0)

        assert coordinator.effective_levels("unit_1") == (40, 40)

    async def test_writes_publish_and_roll_back_the_overlay(
        self, hass: HomeAssistant,
    ) -> None:
        coordinator, client = _build_coordinator(hass, [_UNIT_1])
        coordinator.data = {
            "unit_1": RoomState(operation_mode="manual", target_level=40)
        }

        await coordinator.async_set_unbalanced_levels("unit_1", 70, 30)
        assert coordinator.effective_levels("unit_1") == (70, 30)

        def _raise(*args, **kwargs):
            raise MeltemModbusError("boom")

        client.write_unbalanced_levels = _raise
        with pytest.raises(MeltemModbusError):
            await coordinator.async_set_unbalanced_levels("unit_1", 10, 90)

        assert coordinator.effective_levels("unit_1") == (40, 40)

    async def test_mode_change_discards_a_pending_overlay(
        self, hass: HomeAssistant,
    ) -> None:
        coordinator, _ = _build_coordinator(hass, [_UNIT_1])
        coordinator.data = {
            "unit_1": RoomState(operation_mode="manual", target_level=40)
        }
        coordinator._set_optimistic_levels("unit_1", 80, 20)

        with patch(
            "custom_components.meltem_ventilation.coordinator.async_sleep",
            new=AsyncMock(),
        ):
            await coordinator.async_set_operation_mode("unit_1", "co2_control")

        assert coordinator._optimistic_levels.get("unit_1", None) is None


# ---------------------------------------------------------------------------
#  Optimistic preset overlay
# ---------------------------------------------------------------------------

class TestOptimisticPresetOverlay:
    def test_overlay_is_returned_until_the_gateway_confirms(
        self, hass: HomeAssistant,
    ) -> None:
        coordinator, _ = _build_coordinator(
            hass,
            [RoomConfig(key="unit_1", name="Unit 1", profile="ii_plain", slave=2)],
        )
        coordinator.data = {"unit_1": RoomState(preset_mode="low")}

        coordinator._set_optimistic_preset_mode("unit_1", "high")
        assert coordinator.optimistic_preset_mode("unit_1") == "high"

        coordinator.data = {"unit_1": RoomState(preset_mode="high")}
        assert coordinator.optimistic_preset_mode("unit_1") is None

    def test_overlay_expires(self, hass: HomeAssistant) -> None:
        coordinator, _ = _build_coordinator(
            hass,
            [RoomConfig(key="unit_1", name="Unit 1", profile="ii_plain", slave=2)],
        )
        coordinator.data = {"unit_1": RoomState(preset_mode="low")}
        coordinator._optimistic_presets._pending["unit_1"] = ("high", 0.0)

        assert coordinator.optimistic_preset_mode("unit_1") is None

    async def test_failed_preset_write_drops_the_overlay(
        self, hass: HomeAssistant,
    ) -> None:
        coordinator, client = _build_coordinator(
            hass,
            [RoomConfig(key="unit_1", name="Unit 1", profile="ii_plain", slave=2)],
        )
        coordinator.data = {"unit_1": RoomState(preset_mode="low")}

        def _raise(*args, **kwargs):
            raise MeltemModbusError("boom")

        client.write_preset_mode = _raise

        with pytest.raises(MeltemModbusError):
            await coordinator.async_set_preset_mode("unit_1", "high")

        assert coordinator.optimistic_preset_mode("unit_1") is None

    async def test_clear_pending_preset_still_writes_manual_mode(
        self, hass: HomeAssistant,
    ) -> None:
        coordinator, client = _build_coordinator(
            hass,
            [RoomConfig(key="unit_1", name="Unit 1", profile="ii_plain", slave=2)],
        )
        coordinator.data = {
            "unit_1": RoomState(
                operation_mode="manual",
                preset_mode=None,
                target_level=30,
            )
        }
        coordinator._set_optimistic_preset_mode("unit_1", "low")

        with patch(
            "custom_components.meltem_ventilation.coordinator.async_sleep",
            new=AsyncMock(),
        ):
            await coordinator.async_clear_preset_mode("unit_1")

        assert client.write_operating_mode_calls == [("unit_1", "manual")]


class TestOptimisticIntensiveOverlay:
    def test_overlay_is_returned_until_the_gateway_confirms(
        self, hass: HomeAssistant,
    ) -> None:
        coordinator, _ = _build_coordinator(
            hass,
            [RoomConfig(key="unit_1", name="Unit 1", profile="ii_plain", slave=2)],
        )
        coordinator.data = {"unit_1": RoomState(intensive_active=False)}

        coordinator._set_optimistic_intensive("unit_1", True)
        assert coordinator.optimistic_intensive("unit_1") is True

        coordinator.data = {"unit_1": RoomState(intensive_active=True)}
        assert coordinator.optimistic_intensive("unit_1") is None

    def test_overlay_expires(self, hass: HomeAssistant) -> None:
        coordinator, _ = _build_coordinator(
            hass,
            [RoomConfig(key="unit_1", name="Unit 1", profile="ii_plain", slave=2)],
        )
        coordinator.data = {"unit_1": RoomState(intensive_active=False)}
        coordinator._optimistic_intensive._pending["unit_1"] = (True, 0.0)

        assert coordinator.optimistic_intensive("unit_1") is None

    async def test_failed_write_drops_the_overlay(
        self, hass: HomeAssistant,
    ) -> None:
        coordinator, client = _build_coordinator(
            hass,
            [RoomConfig(key="unit_1", name="Unit 1", profile="ii_plain", slave=2)],
        )
        coordinator.data = {"unit_1": RoomState(intensive_active=False)}

        def _raise(*args, **kwargs):
            raise MeltemModbusError("boom")

        client.write_preset_mode = _raise

        with pytest.raises(MeltemModbusError):
            await coordinator.async_activate_intensive("unit_1")

        assert coordinator.optimistic_intensive("unit_1") is None


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

    def test_profile_field_keys_are_stable_per_modbus_address(self) -> None:
        assert _profile_field_key(2) == "slave_2"
        assert _profile_field_key(16) == "slave_16"

    def test_unit_details_lists_preview_and_device_name(self) -> None:
        assert _unit_details([2], {}) == "- **2**"
        assert (
            _unit_details([2], {2: "ID 116852 | basic"})
            == "- **2**: Hardware ID 116852 | basic"
        )
        assert (
            _unit_details([2], {2: "ID 116852 | basic"}, {2: "Wohnzimmer"})
            == "- **2**: Wohnzimmer, Hardware ID 116852 | basic"
        )

    def test_build_rooms_from_profiles_preserves_existing_metadata(self) -> None:
        selected_profiles = {"slave_2": "ii_plain"}
        rooms = _build_rooms_from_profiles(
            [2],
            selected_profiles,
            previews_by_slave={2: "ID 116852 | basic"},
            existing_rooms_by_slave={
                2: {
                    "key": "bathroom",
                    "name": "Bathroom",
                    "supported_entity_keys": ["level"],
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
        assert "supply_level" in rooms[0]["supported_entity_keys"]
        assert "extract_level" in rooms[0]["supported_entity_keys"]
        assert "humidity_extract_air" not in rooms[0]["supported_entity_keys"]

    def test_build_rooms_from_profiles_uses_stable_field_keys(self) -> None:
        selected_profiles = {"slave_2": "ii_plain"}
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
            "slave_2": "ii_plain",
            "slave_3": "ii_plain",
            "slave_4": "ii_plain",
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
        coordinator.data = {"unit_1": RoomState(target_level=30)}
        await coordinator.async_set_level("unit_1", 55)

        assert client.write_level_calls == [("unit_1", 55)]
        assert coordinator.data["unit_1"].target_level == 30
        assert client.read_calls == []

    async def test_async_set_unbalanced_levels_writes_without_forced_refresh(
        self, hass: HomeAssistant,
    ) -> None:
        coordinator, client = _build_coordinator(
            hass,
            [RoomConfig(key="unit_1", name="Unit 1", profile="ii_plain", slave=2)],
        )
        coordinator.data = {
            "unit_1": RoomState(target_level=30, extract_target_level=35)
        }
        await coordinator.async_set_unbalanced_levels("unit_1", 40, 35)

        assert client.write_unbalanced_calls == [("unit_1", 40, 35)]
        assert client.read_calls == []

    async def test_async_set_preset_mode_writes_and_refreshes(
        self, hass: HomeAssistant,
    ) -> None:
        coordinator, client = _build_coordinator(
            hass,
            [RoomConfig(key="unit_1", name="Unit 1", profile="ii_plain", slave=2)],
        )
        coordinator.data = {"unit_1": RoomState(target_level=30)}

        with patch("custom_components.meltem_ventilation.coordinator.async_sleep"):
            await coordinator.async_set_preset_mode("unit_1", "medium")

        assert client.write_preset_mode_calls == [("unit_1", "medium")]
        assert client.read_calls

    async def test_async_set_preset_mode_forces_at_least_two_refresh_attempts(
        self, hass: HomeAssistant,
    ) -> None:
        coordinator, client = _build_coordinator(
            hass,
            [RoomConfig(key="unit_1", name="Unit 1", profile="ii_plain", slave=2)],
        )
        coordinator.data = {"unit_1": RoomState(target_level=30)}

        with patch(
            "custom_components.meltem_ventilation.coordinator.async_sleep",
            new=AsyncMock(),
        ):
            await coordinator.async_set_preset_mode("unit_1", "medium")

        assert len(client.read_calls) == 2

    async def test_async_set_preset_mode_writes_only_the_preset(
        self, hass: HomeAssistant,
    ) -> None:
        coordinator, client = _build_coordinator(
            hass,
            [RoomConfig(key="unit_1", name="Unit 1", profile="ii_plain", slave=2)],
        )
        coordinator.data = {
            "unit_1": RoomState(target_level=50, extract_target_level=70)
        }

        recorded: list[tuple[str, str]] = []

        def _write_preset_mode(room: RoomConfig, preset_mode: str) -> None:
            recorded.append((room.key, preset_mode))

        client.write_preset_mode = _write_preset_mode  # type: ignore[method-assign]

        with patch("custom_components.meltem_ventilation.coordinator.async_sleep"):
            await coordinator.async_set_preset_mode("unit_1", "medium")

        assert recorded == [("unit_1", "medium")]

    async def test_async_activate_intensive_writes_intensive_preset(
        self, hass: HomeAssistant,
    ) -> None:
        coordinator, client = _build_coordinator(
            hass,
            [RoomConfig(key="unit_1", name="Unit 1", profile="ii_plain", slave=2)],
        )
        coordinator.data = {"unit_1": RoomState(target_level=30)}

        with patch(
            "custom_components.meltem_ventilation.coordinator.async_sleep",
            new=AsyncMock(),
        ):
            await coordinator.async_activate_intensive("unit_1")

        assert client.write_preset_mode_calls == [("unit_1", "intensive")]

    async def test_control_setting_is_published_before_confirmation(
        self, hass: HomeAssistant,
    ) -> None:
        coordinator, client = _build_coordinator(
            hass,
            [RoomConfig(key="unit_1", name="Unit 1", profile="ii_f", slave=2)],
        )
        coordinator.data = {
            "unit_1": RoomState(humidity_starting_point=50)
        }
        observed_during_settle: list[int | None] = []

        async def _observe_optimistic_state(_seconds: float) -> None:
            observed_during_settle.append(
                coordinator.safe_data["unit_1"].humidity_starting_point
            )

        client.next_read_state = RoomState(humidity_starting_point=70)
        with patch(
            "custom_components.meltem_ventilation.coordinator.async_sleep",
            side_effect=_observe_optimistic_state,
        ):
            await coordinator.async_set_control_setting(
                "unit_1", "humidity_starting_point", 70
            )

        assert observed_during_settle == [70]
        assert client.write_control_setting_calls == [
            ("unit_1", "humidity_starting_point", 70)
        ]

    async def test_control_setting_keeps_optimistic_value_when_refresh_fails(
        self, hass: HomeAssistant,
    ) -> None:
        coordinator, client = _build_coordinator(
            hass,
            [RoomConfig(key="unit_1", name="Unit 1", profile="ii_f", slave=2)],
        )
        coordinator.data = {
            "unit_1": RoomState(humidity_starting_point=50)
        }

        def _raise(*_args, **_kwargs):
            raise MeltemModbusError("confirmation failed")

        client.read_room_state = _raise  # type: ignore[method-assign]
        with patch(
            "custom_components.meltem_ventilation.coordinator.async_sleep",
            new=AsyncMock(),
        ):
            await coordinator.async_set_control_setting(
                "unit_1", "humidity_starting_point", 70
            )

        assert coordinator.safe_data["unit_1"].humidity_starting_point == 70

    async def test_control_setting_publishes_the_actual_written_step(
        self, hass: HomeAssistant,
    ) -> None:
        coordinator, client = _build_coordinator(
            hass,
            [RoomConfig(key="unit_1", name="Unit 1", profile="ii_f", slave=2)],
        )
        coordinator.data = {"unit_1": RoomState(humidity_min_level=10)}
        observed_during_settle: list[int | None] = []

        def _write(room: RoomConfig, setting_key: str, value: int) -> int:
            client.write_control_setting_calls.append((room.key, setting_key, value))
            return 20

        async def _observe(_seconds: float) -> None:
            observed_during_settle.append(
                coordinator.safe_data["unit_1"].humidity_min_level
            )

        client.write_control_setting = _write  # type: ignore[method-assign]
        client.next_read_state = RoomState(humidity_min_level=20)
        with patch(
            "custom_components.meltem_ventilation.coordinator.async_sleep",
            side_effect=_observe,
        ):
            await coordinator.async_set_control_setting(
                "unit_1", "humidity_min_level", 15
            )

        assert observed_during_settle == [20]

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
                        {"extract_air_flow", "supply_air_flow"}
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
        previous = {"broken": RoomState(target_level=30)}
        job = PollJob(
            "flow", "broken", RefreshPlan.only(refresh_airflow=True), 10, 0.0
        )

        state_map = coordinator._read_one_job(previous, job)

        assert state_map["broken"].target_level == 30


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
        flow = MeltemVentilationOptionsFlow()
        client = _FakeClient()
        coordinator = MeltemDataUpdateCoordinator(
            hass,
            config_entry=config_entry,
            client=client,
            rooms=[
                RoomConfig(
                    key="unit_1", name="Unit 1", profile="ii_plain", slave=2
                )
            ],
            max_requests_per_second=2.0,
        )
        hass.data.setdefault(DOMAIN, {})
        config_entry.add_to_hass(hass)
        config_entry.runtime_data = types.SimpleNamespace(
            coordinator=coordinator
        )
        flow.hass = hass
        # OptionsFlow.config_entry resolves through handler + hass.
        flow.handler = config_entry.entry_id
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

        result = await flow.async_step_profiles({"slave_2": "ii_f"})

        assert result["type"] == "create_entry"
        updated_data = updated_entries[0][1]["data"]
        assert updated_data[CONF_ROOMS][0]["profile"] == "ii_f"
        assert "humidity_extract_air" in updated_data[CONF_ROOMS][0]["supported_entity_keys"]
        assert "supply_level" in updated_data[CONF_ROOMS][0]["supported_entity_keys"]
        assert "extract_level" in updated_data[CONF_ROOMS][0]["supported_entity_keys"]
        assert reloaded == ["entry-1"]
