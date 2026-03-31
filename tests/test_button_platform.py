"""Tests for the Meltem button platform."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.meltem_ventilation.button import MeltemActivateIntensiveButton
from custom_components.meltem_ventilation.models import RoomConfig


def _build_button() -> MeltemActivateIntensiveButton:
    coordinator = MagicMock()
    coordinator.async_activate_intensive = AsyncMock()
    coordinator.safe_data = {"unit_1": MagicMock()}
    room = RoomConfig(
        key="unit_1",
        name="Unit 1",
        profile="ii_plain",
        slave=2,
    )
    return MeltemActivateIntensiveButton(coordinator, room)


class TestMeltemActivateIntensiveButton:
    @pytest.mark.asyncio
    async def test_press_triggers_coordinator(self) -> None:
        entity = _build_button()

        await entity.async_press()

        entity.coordinator.async_activate_intensive.assert_awaited_once_with("unit_1")
