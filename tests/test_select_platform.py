"""Tests for the Meltem operation-mode select platform."""

from __future__ import annotations

import time
from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.meltem_ventilation.models import RoomConfig, RoomState
from custom_components.meltem_ventilation.select import (
    MeltemOperationModeSelect,
    MeltemPresetModeSelect,
)


def _build_entity(profile: str) -> MeltemOperationModeSelect:
    coordinator = MagicMock()
    coordinator.async_set_operation_mode = AsyncMock()
    coordinator.safe_data = {
        "unit_1": RoomState(operation_mode="manual"),
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
    coordinator.async_set_preset_mode = AsyncMock()
    coordinator.optimistic_targets_by_room = {}
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
    def test_plain_profile_exposes_basic_modes_only(self) -> None:
        entity = _build_entity("ii_plain")
        assert entity.options == ["off", "manual", "unbalanced"]

    def test_f_profile_adds_humidity_control(self) -> None:
        entity = _build_entity("ii_f")
        assert entity.options == [
            "off",
            "manual",
            "unbalanced",
            "humidity_control",
        ]

    def test_fc_profile_adds_co2_and_automatic(self) -> None:
        entity = _build_entity("ii_fc")
        assert entity.options == [
            "off",
            "manual",
            "unbalanced",
            "humidity_control",
            "co2_control",
            "automatic",
        ]

    @pytest.mark.asyncio
    async def test_select_option_delegates_to_coordinator(self) -> None:
        entity = _build_entity("ii_fc_voc")
        await entity.async_select_option("automatic")
        entity.coordinator.async_set_operation_mode.assert_awaited_once_with(
            "unit_1",
            "automatic",
        )


class TestMeltemPresetModeSelect:
    def test_ii_profile_exposes_documented_defaults(self) -> None:
        entity = _build_preset_entity("ii_plain")
        assert entity.options == [
            "low",
            "medium",
            "high",
            "extract_only",
            "supply_only",
        ]

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
        assert entity.current_option is None

    def test_manual_slider_override_hides_preset_optimistically(self) -> None:
        entity = _build_preset_entity(
            "ii_plain",
            state=RoomState(preset_mode="medium"),
        )
        entity.coordinator.optimistic_targets_by_room["unit_1"] = object()

        assert entity.current_option is None

    @pytest.mark.asyncio
    async def test_select_option_sets_preset_mode(self) -> None:
        entity = _build_preset_entity("ii_plain")
        await entity.async_select_option("high")
        entity.coordinator.async_set_preset_mode.assert_awaited_once_with(
            "unit_1", "high"
        )

    @pytest.mark.asyncio
    async def test_select_option_updates_ui_optimistically(self) -> None:
        entity = _build_preset_entity("ii_plain")

        await entity.async_select_option("low")

        assert entity.current_option == "low"

    def test_coordinator_update_clears_optimistic_value_after_confirmation(self) -> None:
        entity = _build_preset_entity("ii_plain")
        entity._optimistic_option = "low"
        entity._optimistic_until = time.monotonic() + 30.0
        entity.coordinator.safe_data["unit_1"] = RoomState(preset_mode="low")

        entity._handle_coordinator_update()

        assert entity.current_option == "low"
        assert entity._optimistic_option is None
