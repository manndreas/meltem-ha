"""Tests for the fan platform."""

from __future__ import annotations

from dataclasses import replace
from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.meltem_ventilation.fan import (
    DIRECTION_EXTRACT,
    DIRECTION_SUPPLY,
    MeltemDirectionalFanEntity,
)
from custom_components.meltem_ventilation.models import RoomConfig, RoomState

_ROOM = RoomConfig(key="unit_1", name="Unit 1", profile="ii_plain", slave=2)
# s-series units top out at 97 m3/h instead of 100.
_ROOM_S = replace(_ROOM, profile="s_plain")


def _coordinator(levels: tuple[int | None, int | None] = (40, 40)) -> MagicMock:
    coordinator = MagicMock()
    coordinator.rooms = [_ROOM]
    coordinator.hass = None
    coordinator.last_update_success = True
    coordinator.room_available.return_value = True
    coordinator.effective_levels.return_value = levels
    coordinator.level_source.return_value = None
    coordinator.level_write_fallback.return_value = None
    coordinator.async_set_direction_level = AsyncMock()
    coordinator.safe_data = {"unit_1": RoomState(target_level=40, operation_mode="manual")}
    return coordinator


def _fan(
    coordinator: MagicMock,
    direction: str = DIRECTION_SUPPLY,
    room: RoomConfig = _ROOM,
) -> MeltemDirectionalFanEntity:
    return MeltemDirectionalFanEntity(coordinator, room, direction)


def _assert_written(coordinator: MagicMock, direction: str, level: int) -> None:
    coordinator.async_set_direction_level.assert_awaited_once_with("unit_1", direction, level)


class TestReadState:
    @pytest.mark.parametrize(
        ("room", "levels", "direction", "percentage"),
        [
            pytest.param(_ROOM, (60, 30), DIRECTION_SUPPLY, 60, id="supply"),
            pytest.param(_ROOM, (60, 30), DIRECTION_EXTRACT, 30, id="extract"),
            pytest.param(_ROOM, (None, None), DIRECTION_SUPPLY, None, id="no-data"),
            pytest.param(_ROOM_S, (97, 97), DIRECTION_SUPPLY, 100, id="s-series-full"),
            # Intensive ventilation and profile mismatches can exceed the rated flow.
            pytest.param(_ROOM_S, (120, 120), DIRECTION_SUPPLY, 100, id="s-series-capped"),
            pytest.param(_ROOM, (120, 120), DIRECTION_SUPPLY, 100, id="ii-series-capped"),
        ],
    )
    def test_percentage_follows_the_own_airflow(
        self,
        room: RoomConfig,
        levels: tuple[int | None, int | None],
        direction: str,
        percentage: int | None,
    ) -> None:
        assert _fan(_coordinator(levels), direction, room).percentage == percentage

    def test_is_on_follows_the_own_direction(self) -> None:
        coordinator = _coordinator((50, 0))

        assert _fan(coordinator).is_on is True
        assert _fan(coordinator, DIRECTION_EXTRACT).is_on is False

    def test_unique_ids_differ_per_direction(self) -> None:
        coordinator = _coordinator()

        assert _fan(coordinator).unique_id != _fan(coordinator, DIRECTION_EXTRACT).unique_id

    def test_unavailable_when_room_is_unavailable(self) -> None:
        coordinator = _coordinator()
        fan = _fan(coordinator)
        assert fan.available is True

        coordinator.room_available.return_value = False
        assert fan.available is False

    def test_available_when_flow_control_is_stale(self) -> None:
        coordinator = _coordinator()
        coordinator.read_group_available.return_value = False

        assert _fan(coordinator).available is True
        coordinator.read_group_available.assert_not_called()


class TestWrites:
    """The fan converts percent to m3/h; the coordinator decides how to write."""

    async def test_set_percentage_delegates_one_direction(self) -> None:
        coordinator = _coordinator((40, 30))

        await _fan(coordinator, DIRECTION_EXTRACT).async_set_percentage(60)

        _assert_written(coordinator, DIRECTION_EXTRACT, 60)

    async def test_turn_off_writes_zero_for_the_own_direction(self) -> None:
        coordinator = _coordinator((40, 30))

        await _fan(coordinator).async_turn_off()

        _assert_written(coordinator, DIRECTION_SUPPLY, 0)

    async def test_percentage_is_clamped(self) -> None:
        coordinator = _coordinator((40, 30))

        await _fan(coordinator).async_set_percentage(150)

        _assert_written(coordinator, DIRECTION_SUPPLY, 100)

    async def test_percentage_is_converted_to_airflow(self) -> None:
        coordinator = _coordinator((0, 0))

        await _fan(coordinator, room=_ROOM_S).async_set_percentage(50)

        _assert_written(coordinator, DIRECTION_SUPPLY, 49)


class TestTurnOn:
    async def test_turn_on_from_zero_uses_a_default_speed(self) -> None:
        coordinator = _coordinator((0, 30))

        await _fan(coordinator).async_turn_on()

        _assert_written(coordinator, DIRECTION_SUPPLY, 50)

    async def test_turn_on_restores_the_last_running_speed(self) -> None:
        coordinator = _coordinator((70, 30))
        fan = _fan(coordinator)
        fan.async_write_ha_state = MagicMock()
        fan._handle_coordinator_update()
        coordinator.effective_levels.return_value = (0, 30)

        await fan.async_turn_on()

        _assert_written(coordinator, DIRECTION_SUPPLY, 70)

    async def test_turn_on_while_running_keeps_the_unit_untouched(self) -> None:
        """Re-sending the measured level would end a running sensor mode."""
        coordinator = _coordinator((48, 51))
        coordinator.safe_data = {"unit_1": RoomState(operation_mode="co2_control")}

        await _fan(coordinator).async_turn_on()

        coordinator.async_set_direction_level.assert_not_awaited()

    async def test_turn_on_with_an_explicit_percentage_still_writes(self) -> None:
        coordinator = _coordinator((48, 51))

        await _fan(coordinator).async_turn_on(percentage=80)

        _assert_written(coordinator, DIRECTION_SUPPLY, 80)


class TestAttributes:
    def test_level_source_and_opposite_airflow_are_exposed(self) -> None:
        coordinator = _coordinator((40, 30))
        coordinator.level_source.return_value = "target"

        attributes = _fan(coordinator).extra_state_attributes

        assert attributes["level_source"] == "target"
        assert attributes["opposite_airflow"] == 30
        assert attributes["opposite_airflow_known"] is True
        assert "last_write_fallback" not in attributes

    def test_unknown_opposite_announces_the_balanced_fallback(self) -> None:
        attributes = _fan(_coordinator((40, None))).extra_state_attributes

        assert attributes["next_write_fallback"] == "both_directions_balanced_manual"

    def test_last_write_fallback_comes_from_the_coordinator(self) -> None:
        coordinator = _coordinator((60, 60))
        coordinator.level_write_fallback.return_value = "unknown_mode_overridden"
        coordinator.safe_data = {"unit_1": RoomState(target_level=60)}

        attributes = _fan(coordinator).extra_state_attributes

        assert attributes["last_write_fallback"] == "unknown_mode_overridden"
        assert attributes["operating_mode_known"] is False


