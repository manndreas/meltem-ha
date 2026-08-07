"""Tests for the Meltem switch platform."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

from custom_components.meltem_ventilation.models import RoomConfig, RoomState
from custom_components.meltem_ventilation.switch import MeltemIntensiveSwitch

_ROOM = RoomConfig(key="unit_1", name="Unit 1", profile="ii_plain", slave=2)


def _build_switch(
    state: RoomState | None = None,
    *,
    optimistic: bool | None = None,
) -> MeltemIntensiveSwitch:
    coordinator = MagicMock()
    coordinator.hass = None
    coordinator.last_update_success = True
    coordinator.room_available.return_value = True
    coordinator.optimistic_intensive.return_value = optimistic
    coordinator.async_activate_intensive = AsyncMock()
    coordinator.async_deactivate_intensive = AsyncMock()
    coordinator.safe_data = {"unit_1": state or RoomState()}
    return MeltemIntensiveSwitch(coordinator, _ROOM)


def test_reports_the_running_override() -> None:
    assert _build_switch(RoomState(intensive_active=True)).is_on is True
    assert _build_switch(RoomState(intensive_active=False)).is_on is False


def test_state_is_unknown_while_the_mode_block_is_unreadable() -> None:
    assert _build_switch(RoomState()).is_on is None


def test_pending_write_is_shown_before_the_gateway_confirms_it() -> None:
    entity = _build_switch(RoomState(intensive_active=False), optimistic=True)

    assert entity.is_on is True


def test_turning_on_starts_the_override() -> None:
    entity = _build_switch()

    asyncio.run(entity.async_turn_on())

    entity.coordinator.async_activate_intensive.assert_awaited_once_with("unit_1")


def test_turning_off_cancels_the_override() -> None:
    entity = _build_switch(RoomState(intensive_active=True))

    asyncio.run(entity.async_turn_off())

    entity.coordinator.async_deactivate_intensive.assert_awaited_once_with("unit_1")


def test_unique_id_uses_the_intensive_key() -> None:
    assert _build_switch().unique_id == "meltem_ventilation_unit_1_intensive"
