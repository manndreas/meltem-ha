"""Tests for the fan platform."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

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
    coordinator.read_group_for_entity.side_effect = lambda key: (
        "flow_control" if key in {"supply_level", "extract_level"} else None
    )
    coordinator.read_group_available.return_value = True
    coordinator.effective_levels.return_value = levels
    coordinator.level_source.return_value = None
    coordinator.level_write_fallback.return_value = None
    coordinator.async_set_direction_level = AsyncMock()
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

    def test_available_when_flow_control_is_stale(self) -> None:
        coordinator = _make_coordinator()
        coordinator.read_group_for_entity.return_value = "flow_control"
        coordinator.read_group_available.return_value = False

        assert _supply(coordinator).available is True
        coordinator.read_group_available.assert_not_called()


class TestWrites:
    """The fan converts percent to m3/h; the coordinator decides how to write."""

    def test_set_percentage_delegates_one_direction(self) -> None:
        coordinator = _make_coordinator(levels=(40, 30))

        asyncio.run(_extract(coordinator).async_set_percentage(60))

        coordinator.async_set_direction_level.assert_awaited_once_with(
            "unit_1", DIRECTION_EXTRACT, 60
        )

    def test_turn_off_writes_zero_for_the_own_direction(self) -> None:
        coordinator = _make_coordinator(levels=(40, 30))

        asyncio.run(_supply(coordinator).async_turn_off())

        coordinator.async_set_direction_level.assert_awaited_once_with(
            "unit_1", DIRECTION_SUPPLY, 0
        )

    def test_percentage_is_clamped(self) -> None:
        coordinator = _make_coordinator(levels=(40, 30))

        asyncio.run(_supply(coordinator).async_set_percentage(150))

        coordinator.async_set_direction_level.assert_awaited_once_with(
            "unit_1", DIRECTION_SUPPLY, 100
        )


class TestTurnOn:
    def test_turn_on_from_zero_uses_a_default_speed(self) -> None:
        coordinator = _make_coordinator(levels=(0, 30))

        asyncio.run(_supply(coordinator).async_turn_on())

        coordinator.async_set_direction_level.assert_awaited_once_with(
            "unit_1", DIRECTION_SUPPLY, 50
        )

    def test_turn_on_restores_the_last_running_speed(self) -> None:
        coordinator = _make_coordinator(levels=(70, 30))
        entity = _supply(coordinator)
        entity.async_write_ha_state = MagicMock()
        entity._handle_coordinator_update()
        coordinator.effective_levels.return_value = (0, 30)

        asyncio.run(entity.async_turn_on())

        coordinator.async_set_direction_level.assert_awaited_once_with(
            "unit_1", DIRECTION_SUPPLY, 70
        )

    def test_turn_on_while_running_keeps_the_unit_untouched(self) -> None:
        """Re-sending the measured level would end a running sensor mode."""
        coordinator = _make_coordinator(levels=(48, 51))
        coordinator.safe_data = {"unit_1": RoomState(operation_mode="co2_control")}

        asyncio.run(_supply(coordinator).async_turn_on())

        coordinator.async_set_direction_level.assert_not_awaited()

    def test_turn_on_with_an_explicit_percentage_still_writes(self) -> None:
        coordinator = _make_coordinator(levels=(48, 51))

        asyncio.run(_supply(coordinator).async_turn_on(percentage=80))

        coordinator.async_set_direction_level.assert_awaited_once_with(
            "unit_1", DIRECTION_SUPPLY, 80
        )


class TestAttributes:
    def test_level_source_and_opposite_airflow_are_exposed(self) -> None:
        coordinator = _make_coordinator(levels=(40, 30))
        coordinator.level_source.return_value = "target"

        attributes = _supply(coordinator).extra_state_attributes

        assert attributes["level_source"] == "target"
        assert attributes["opposite_airflow"] == 30
        assert attributes["opposite_airflow_known"] is True
        assert "last_write_fallback" not in attributes

    def test_unknown_opposite_announces_the_balanced_fallback(self) -> None:
        coordinator = _make_coordinator(levels=(40, None))

        attributes = _supply(coordinator).extra_state_attributes

        assert attributes["next_write_fallback"] == "both_directions_balanced_manual"

    def test_last_write_fallback_comes_from_the_coordinator(self) -> None:
        coordinator = _make_coordinator(levels=(60, 60))
        coordinator.level_write_fallback.return_value = "unknown_mode_overridden"
        coordinator.safe_data = {"unit_1": RoomState(target_level=60)}

        attributes = _supply(coordinator).extra_state_attributes

        assert attributes["last_write_fallback"] == "unknown_mode_overridden"
        assert attributes["operating_mode_known"] is False


class TestProfileScaling:
    def test_percentage_is_converted_to_airflow(self) -> None:
        coordinator = _make_coordinator(levels=(0, 0))

        asyncio.run(_supply(coordinator, _ROOM_S).async_set_percentage(50))

        # s-series units top out at 97 m3/h, so 50 % must not be written as 50.
        coordinator.async_set_direction_level.assert_awaited_once_with(
            "unit_1", DIRECTION_SUPPLY, 49
        )

    def test_full_airflow_reads_back_as_full_percentage(self) -> None:
        coordinator = _make_coordinator(levels=(97, 97))

        assert _supply(coordinator, _ROOM_S).percentage == 100

    def test_airflow_above_the_rated_maximum_is_capped(self) -> None:
        """Intensive ventilation and profile mismatches can exceed the rated flow."""
        coordinator = _make_coordinator(levels=(120, 120))

        assert _supply(coordinator, _ROOM_S).percentage == 100
        assert _supply(coordinator).percentage == 100


