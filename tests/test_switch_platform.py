"""Tests for the Meltem switch platform."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.meltem_ventilation.coordinator import (
    MeltemDataUpdateCoordinator,
)
from custom_components.meltem_ventilation.models import RoomConfig, RoomState
from custom_components.meltem_ventilation.switch import MeltemIntensiveSwitch

_ROOM = RoomConfig(key="unit_1", name="Unit 1", profile="ii_plain", slave=2)


def _switch(
    state: RoomState | None = None,
    *,
    optimistic: bool | None = None,
) -> MeltemIntensiveSwitch:
    coordinator = MagicMock()
    coordinator.last_update_success = True
    coordinator.room_available.return_value = True
    coordinator.read_group_fresh.return_value = True
    coordinator.optimistic_intensive.return_value = optimistic
    coordinator.async_activate_intensive = AsyncMock()
    coordinator.async_deactivate_intensive = AsyncMock()
    coordinator.safe_data = {"unit_1": state or RoomState()}
    return MeltemIntensiveSwitch(coordinator, _ROOM)


@pytest.mark.parametrize(
    ("intensive_active", "optimistic", "is_on"),
    [
        pytest.param(True, None, True, id="running"),
        pytest.param(False, None, False, id="not-running"),
        pytest.param(None, None, None, id="mode-block-unreadable"),
        pytest.param(False, True, True, id="pending-write-before-confirmation"),
    ],
)
def test_is_on_reports_the_override(
    intensive_active: bool | None, optimistic: bool | None, is_on: bool | None
) -> None:
    switch = _switch(RoomState(intensive_active=intensive_active), optimistic=optimistic)

    assert switch.is_on is is_on


def test_available_when_intensive_status_is_stale() -> None:
    switch = _switch(RoomState(intensive_active=True))
    switch.coordinator.read_group_available.return_value = False
    switch.coordinator.read_group_fresh.return_value = False

    assert switch.available is True
    assert switch.is_on is None
    switch.coordinator.read_group_available.assert_not_called()


@pytest.mark.parametrize("pending", [True, False])
def test_pending_override_is_visible_with_stale_readback(pending: bool) -> None:
    switch = _switch(RoomState(intensive_active=pending), optimistic=pending)
    switch.coordinator.read_group_fresh.return_value = False

    assert switch.is_on is pending


def test_intensive_status_has_its_own_read_health_group() -> None:
    assert MeltemDataUpdateCoordinator.read_group_for_entity("intensive") == "intensive"


async def test_turning_on_starts_the_override() -> None:
    switch = _switch()

    await switch.async_turn_on()

    switch.coordinator.async_activate_intensive.assert_awaited_once_with("unit_1")


async def test_turning_off_cancels_the_override() -> None:
    switch = _switch(RoomState(intensive_active=True))

    await switch.async_turn_off()

    switch.coordinator.async_deactivate_intensive.assert_awaited_once_with("unit_1")


def test_unique_id_uses_the_intensive_key() -> None:
    assert _switch().unique_id == "meltem_ventilation_unit_1_intensive"
