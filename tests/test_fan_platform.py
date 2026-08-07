"""Tests for the fan platform."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest
from homeassistant.exceptions import HomeAssistantError

from custom_components.meltem_ventilation.fan import (
    DIRECTION_EXTRACT,
    DIRECTION_SUPPLY,
    MeltemDirectionalFanEntity,
)
from custom_components.meltem_ventilation.models import RoomConfig, RoomState

_ROOM = RoomConfig(key="unit_1", name="Unit 1", profile="ii_plain", slave=2)
_ROOM_S = RoomConfig(key="unit_1", name="Unit 1", profile="s_plain", slave=2)


def _make_coordinator(levels: tuple[int | None, int | None] = (40, 40)) -> MagicMock:
    coordinator = MagicMock()
    coordinator.rooms = [_ROOM]
    coordinator.hass = None
    coordinator.last_update_success = True
    coordinator.room_available.return_value = True
    coordinator.effective_levels.return_value = levels
    coordinator.async_set_level = AsyncMock()
    coordinator.async_set_unbalanced_levels = AsyncMock()
    coordinator.safe_data = {"unit_1": RoomState(target_level=40, operation_mode="manual")}
    return coordinator


def _supply(coordinator, room=_ROOM) -> MeltemDirectionalFanEntity:
    return MeltemDirectionalFanEntity(coordinator, room, DIRECTION_SUPPLY)


def _extract(coordinator, room=_ROOM) -> MeltemDirectionalFanEntity:
    return MeltemDirectionalFanEntity(coordinator, room, DIRECTION_EXTRACT)


class TestReadState:
    def test_each_direction_reports_its_own_level(self) -> None:
        coordinator = _make_coordinator(levels=(60, 30))

        assert _supply(coordinator).percentage == 60
        assert _extract(coordinator).percentage == 30

    def test_is_on_follows_the_own_direction(self) -> None:
        coordinator = _make_coordinator(levels=(50, 0))

        assert _supply(coordinator).is_on is True
        assert _extract(coordinator).is_on is False

    def test_percentage_is_none_without_data(self) -> None:
        coordinator = _make_coordinator(levels=(None, None))

        assert _supply(coordinator).percentage is None

    def test_unique_ids_differ_per_direction(self) -> None:
        coordinator = _make_coordinator()

        assert _supply(coordinator).unique_id != _extract(coordinator).unique_id

    def test_unavailable_when_room_is_unavailable(self) -> None:
        coordinator = _make_coordinator()
        entity = _supply(coordinator)
        assert entity.available is True

        coordinator.room_available.return_value = False
        assert entity.available is False


class TestWrites:
    def test_supply_write_keeps_extract_from_effective_levels(self) -> None:
        coordinator = _make_coordinator(levels=(40, 30))

        asyncio.run(_supply(coordinator).async_set_percentage(60))

        coordinator.async_set_unbalanced_levels.assert_awaited_once_with("unit_1", 60, 30)

    def test_extract_write_keeps_supply_from_effective_levels(self) -> None:
        coordinator = _make_coordinator(levels=(40, 30))

        asyncio.run(_extract(coordinator).async_set_percentage(60))

        coordinator.async_set_unbalanced_levels.assert_awaited_once_with("unit_1", 40, 60)

    def test_turning_off_one_direction_keeps_the_unit_running(self) -> None:
        coordinator = _make_coordinator(levels=(40, 30))

        asyncio.run(_supply(coordinator).async_turn_off())

        coordinator.async_set_unbalanced_levels.assert_awaited_once_with("unit_1", 0, 30)
        coordinator.async_set_level.assert_not_awaited()

    def test_turning_off_the_last_direction_switches_the_unit_off(self) -> None:
        coordinator = _make_coordinator(levels=(40, 0))

        asyncio.run(_supply(coordinator).async_turn_off())

        coordinator.async_set_level.assert_awaited_once_with("unit_1", 0)
        coordinator.async_set_unbalanced_levels.assert_not_awaited()

    def test_turn_on_from_zero_uses_a_default_speed(self) -> None:
        coordinator = _make_coordinator(levels=(0, 30))

        asyncio.run(_supply(coordinator).async_turn_on())

        coordinator.async_set_unbalanced_levels.assert_awaited_once_with("unit_1", 50, 30)

    def test_turn_on_restores_the_current_speed(self) -> None:
        coordinator = _make_coordinator(levels=(70, 30))

        asyncio.run(_supply(coordinator).async_turn_on())

        coordinator.async_set_unbalanced_levels.assert_awaited_once_with("unit_1", 70, 30)

    def test_percentage_is_clamped(self) -> None:
        coordinator = _make_coordinator(levels=(40, 30))

        asyncio.run(_supply(coordinator).async_set_percentage(150))

        coordinator.async_set_unbalanced_levels.assert_awaited_once_with("unit_1", 100, 30)

    def test_write_is_refused_while_the_other_direction_is_unknown(self) -> None:
        """Writing always sends both directions, so a guess would stop the other fan."""
        coordinator = _make_coordinator(levels=(40, None))

        with pytest.raises(HomeAssistantError):
            asyncio.run(_supply(coordinator).async_set_percentage(60))

        coordinator.async_set_unbalanced_levels.assert_not_awaited()
        coordinator.async_set_level.assert_not_awaited()

    def test_turn_off_is_refused_while_the_other_direction_is_unknown(self) -> None:
        coordinator = _make_coordinator(levels=(40, None))

        with pytest.raises(HomeAssistantError):
            asyncio.run(_supply(coordinator).async_turn_off())

        coordinator.async_set_level.assert_not_awaited()


class TestBalancedOperation:
    """Equal levels must not leave the unit stuck in unbalanced mode."""

    def test_matching_levels_are_written_as_balanced(self) -> None:
        coordinator = _make_coordinator(levels=(40, 60))

        asyncio.run(_extract(coordinator).async_set_percentage(40))

        coordinator.async_set_level.assert_awaited_once_with("unit_1", 40)
        coordinator.async_set_unbalanced_levels.assert_not_awaited()

    def test_differing_levels_still_use_unbalanced(self) -> None:
        coordinator = _make_coordinator(levels=(40, 60))

        asyncio.run(_extract(coordinator).async_set_percentage(70))

        coordinator.async_set_unbalanced_levels.assert_awaited_once_with("unit_1", 40, 70)
        coordinator.async_set_level.assert_not_awaited()

    def test_starting_from_off_runs_both_directions(self) -> None:
        coordinator = _make_coordinator(levels=(0, 0))
        coordinator.safe_data = {"unit_1": RoomState(operation_mode="off")}

        asyncio.run(_supply(coordinator).async_turn_on())

        coordinator.async_set_level.assert_awaited_once_with("unit_1", 50)
        coordinator.async_set_unbalanced_levels.assert_not_awaited()

    def test_single_direction_is_still_reachable_while_running(self) -> None:
        coordinator = _make_coordinator(levels=(50, 50))

        asyncio.run(_extract(coordinator).async_set_percentage(0))

        coordinator.async_set_unbalanced_levels.assert_awaited_once_with("unit_1", 50, 0)

    def test_leaving_sensor_control_writes_a_balanced_level(self) -> None:
        """Under sensor control both fans only report fluctuating measurements."""
        coordinator = _make_coordinator(levels=(48, 51))
        coordinator.safe_data = {"unit_1": RoomState(operation_mode="co2_control")}

        asyncio.run(_supply(coordinator).async_set_percentage(60))

        coordinator.async_set_level.assert_awaited_once_with("unit_1", 60)
        coordinator.async_set_unbalanced_levels.assert_not_awaited()

    def test_turning_one_direction_off_under_sensor_control_keeps_the_unit_running(
        self,
    ) -> None:
        """Switching a single fan off must not stop the whole unit."""
        coordinator = _make_coordinator(levels=(48, 51))
        coordinator.safe_data = {"unit_1": RoomState(operation_mode="co2_control")}

        asyncio.run(_supply(coordinator).async_turn_off())

        coordinator.async_set_unbalanced_levels.assert_awaited_once_with("unit_1", 0, 51)
        coordinator.async_set_level.assert_not_awaited()


class TestProfileScaling:
    def test_percentage_is_converted_to_airflow(self) -> None:
        coordinator = _make_coordinator(levels=(0, 0))

        asyncio.run(_supply(coordinator, _ROOM_S).async_set_percentage(50))

        # s-series units top out at 97 m3/h, so 50 % must not be written as 50.
        coordinator.async_set_unbalanced_levels.assert_awaited_once_with("unit_1", 49, 0)

    def test_full_airflow_reads_back_as_full_percentage(self) -> None:
        coordinator = _make_coordinator(levels=(97, 97))

        assert _supply(coordinator, _ROOM_S).percentage == 100

    def test_airflow_above_the_rated_maximum_is_capped(self) -> None:
        """Intensive ventilation and profile mismatches can exceed the rated flow."""
        coordinator = _make_coordinator(levels=(120, 120))

        assert _supply(coordinator, _ROOM_S).percentage == 100
        assert _supply(coordinator).percentage == 100


class TestOptimisticOverlay:
    def test_second_direction_uses_the_pending_value_of_the_first(self) -> None:
        """The two fans must not rebuild each other from a stale cache."""
        coordinator = _make_coordinator(levels=(40, 30))

        asyncio.run(_supply(coordinator).async_set_percentage(80))
        # The coordinator overlay now reports the pending supply level.
        coordinator.effective_levels.return_value = (80, 30)
        asyncio.run(_extract(coordinator).async_set_percentage(20))

        assert coordinator.async_set_unbalanced_levels.await_args_list[1].args == (
            "unit_1",
            80,
            20,
        )
