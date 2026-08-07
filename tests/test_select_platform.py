"""Tests for the Meltem operation-mode select platform."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.meltem_ventilation.const import PRESET_MODE_INACTIVE
from custom_components.meltem_ventilation.models import RoomConfig, RoomState
from custom_components.meltem_ventilation.select import (
    MeltemOperationModeSelect,
    MeltemPresetModeSelect,
    _supports_sensor_control,
)


def _build_entity(
    profile: str,
    *,
    state: RoomState | None = None,
) -> MeltemOperationModeSelect:
    coordinator = MagicMock()
    coordinator.room_available.return_value = True
    coordinator.async_set_operation_mode = AsyncMock()
    coordinator.safe_data = {
        "unit_1": state or RoomState(operation_mode="manual"),
    }
    room = RoomConfig(
        key="unit_1",
        name="Unit 1",
        profile=profile,
        slave=2,
    )
    return MeltemOperationModeSelect(coordinator, room)


def _build_preset_entity(
    profile: str,
    *,
    state: RoomState | None = None,
) -> MeltemPresetModeSelect:
    coordinator = MagicMock()
    coordinator.room_available.return_value = True
    coordinator.optimistic_preset_mode.return_value = None
    coordinator.async_set_preset_mode = AsyncMock()
    coordinator.async_clear_preset_mode = AsyncMock()
    coordinator.safe_data = {
        "unit_1": state or RoomState(preset_mode="medium"),
    }
    room = RoomConfig(
        key="unit_1",
        name="Unit 1",
        profile=profile,
        slave=2,
    )
    return MeltemPresetModeSelect(coordinator, room)


class TestMeltemOperationModeSelect:
    def test_f_profile_offers_humidity_control_only(self) -> None:
        entity = _build_entity("ii_f")
        assert entity.options == ["inactive", "humidity_control"]

    def test_fc_profile_adds_co2_and_automatic(self) -> None:
        entity = _build_entity("ii_fc")
        assert entity.options == [
            "inactive",
            "humidity_control",
            "co2_control",
            "automatic",
        ]

    def test_manual_airflow_states_report_inactive(self) -> None:
        entity = _build_entity("ii_fc")
        for operation_mode in ("off", "manual", "unbalanced"):
            entity.coordinator.safe_data = {
                "unit_1": RoomState(operation_mode=operation_mode)
            }
            assert entity.current_option == "inactive"

    def test_sensor_mode_is_reported_directly(self) -> None:
        entity = _build_entity("ii_fc")
        entity.coordinator.safe_data = {
            "unit_1": RoomState(operation_mode="co2_control")
        }
        assert entity.current_option == "co2_control"

    def test_current_option_is_none_without_data(self) -> None:
        entity = _build_entity("ii_fc")
        entity.coordinator.safe_data = {"unit_1": RoomState()}
        assert entity.current_option is None

    @pytest.mark.asyncio
    async def test_select_option_delegates_to_coordinator(self) -> None:
        entity = _build_entity("ii_fc_voc")
        await entity.async_select_option("automatic")
        entity.coordinator.async_set_operation_mode.assert_awaited_once_with(
            "unit_1",
            "automatic",
        )

    @pytest.mark.asyncio
    async def test_selecting_inactive_leaves_sensor_control(self) -> None:
        entity = _build_entity("ii_fc", state=RoomState(operation_mode="co2_control"))
        await entity.async_select_option("inactive")
        entity.coordinator.async_set_operation_mode.assert_awaited_once_with(
            "unit_1",
            "manual",
        )

    @pytest.mark.asyncio
    async def test_selecting_inactive_keeps_an_unbalanced_setup(self) -> None:
        """Writing manual again would collapse both fans onto one airflow."""
        entity = _build_entity("ii_fc", state=RoomState(operation_mode="unbalanced"))
        await entity.async_select_option("inactive")
        entity.coordinator.async_set_operation_mode.assert_not_awaited()


class TestOperationModeSelectCreation:
    @pytest.mark.asyncio
    async def test_plain_profiles_get_no_sensor_control_entity(self) -> None:
        assert not _supports_sensor_control(
            RoomConfig(key="u1", name="U1", profile="ii_plain", slave=2)
        )
        assert not _supports_sensor_control(
            RoomConfig(key="u1", name="U1", profile="s_plain", slave=2)
        )

    @pytest.mark.asyncio
    async def test_sensor_profiles_get_the_entity(self) -> None:
        for profile in ("ii_f", "ii_fc", "ii_fc_voc", "s_f", "s_fc"):
            assert _supports_sensor_control(
                RoomConfig(key="u1", name="U1", profile=profile, slave=2)
            )


class TestMeltemPresetModeSelect:
    def test_only_app_quick_modes_are_selectable(self) -> None:
        entity = _build_preset_entity("ii_plain")
        assert entity.options == ["inactive", "low", "medium", "high"]

    def test_current_option_matches_state_preset_mode(self) -> None:
        entity = _build_preset_entity(
            "ii_plain",
            state=RoomState(preset_mode="medium"),
        )
        assert entity.current_option == "medium"

    def test_missing_preset_mode_has_no_matching_option(self) -> None:
        entity = _build_preset_entity(
            "ii_plain",
            state=RoomState(operation_mode="unbalanced"),
        )
        assert entity.current_option == PRESET_MODE_INACTIVE

    def test_single_direction_states_are_reported_as_individual(self) -> None:
        """extract_only/supply_only are expressed by the two fan entities."""
        for preset_mode in ("extract_only", "supply_only"):
            entity = _build_preset_entity(
                "ii_plain",
                state=RoomState(preset_mode=preset_mode),
            )
            assert entity.current_option == "inactive"

    def test_pending_selection_wins_over_confirmed_state(self) -> None:
        entity = _build_preset_entity(
            "ii_plain",
            state=RoomState(preset_mode="medium"),
        )
        entity.coordinator.optimistic_preset_mode.return_value = PRESET_MODE_INACTIVE

        assert entity.current_option == PRESET_MODE_INACTIVE

    @pytest.mark.asyncio
    async def test_select_option_sets_preset_mode(self) -> None:
        entity = _build_preset_entity("ii_plain")
        await entity.async_select_option("high")
        entity.coordinator.async_set_preset_mode.assert_awaited_once_with(
            "unit_1", "high"
        )

    @pytest.mark.asyncio
    async def test_select_option_can_clear_preset_mode(self) -> None:
        entity = _build_preset_entity("ii_plain")

        await entity.async_select_option(PRESET_MODE_INACTIVE)

        entity.coordinator.async_clear_preset_mode.assert_awaited_once_with("unit_1")
        entity.coordinator.async_set_preset_mode.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_select_option_updates_ui_optimistically(self) -> None:
        """The overlay now lives in the coordinator and is shared with the fans."""
        entity = _build_preset_entity("ii_plain")
        entity.coordinator.optimistic_preset_mode.return_value = "low"

        await entity.async_select_option("low")

        assert entity.current_option == "low"

    def test_confirmed_state_is_used_once_the_overlay_is_gone(self) -> None:
        entity = _build_preset_entity("ii_plain")
        entity.coordinator.optimistic_preset_mode.return_value = None
        entity.coordinator.safe_data["unit_1"] = RoomState(preset_mode="low")

        assert entity.current_option == "low"
