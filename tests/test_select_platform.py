"""Tests for the Meltem operation-mode select platform."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.meltem_ventilation.models import RoomConfig, RoomState
from custom_components.meltem_ventilation.select import MeltemOperationModeSelect


def _build_entity(profile: str) -> MeltemOperationModeSelect:
    coordinator = MagicMock()
    coordinator.async_set_operation_mode = AsyncMock()
    coordinator.data = {
        "unit_1": RoomState(operation_mode="manual"),
    }
    room = RoomConfig(
        key="unit_1",
        name="Unit 1",
        profile=profile,
        slave=2,
    )
    return MeltemOperationModeSelect(coordinator, room)


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
