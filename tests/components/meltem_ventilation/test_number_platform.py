"""Tests for the number platform."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

from custom_components.meltem_ventilation.models import RoomConfig, RoomState
from custom_components.meltem_ventilation.number import (
    CONTROL_SETTING_DESCRIPTIONS,
    MeltemControlSettingNumber,
)

_ROOM = RoomConfig(key="unit_1", name="Unit 1", profile="ii_f", slave=2)
_HUMIDITY_MIN_LEVEL = next(
    description
    for description in CONTROL_SETTING_DESCRIPTIONS
    if description.key == "humidity_min_level"
)


def _number(state: RoomState | None = None) -> MeltemControlSettingNumber:
    coordinator = MagicMock()
    coordinator.last_update_success = True
    coordinator.room_available.return_value = True
    coordinator.read_group_for_entity.return_value = "control_settings"
    coordinator.read_group_available.return_value = True
    coordinator.async_set_control_setting = AsyncMock()
    coordinator.safe_data = {"unit_1": state or RoomState()}
    return MeltemControlSettingNumber(coordinator, _ROOM, _HUMIDITY_MIN_LEVEL)


class TestControlSettingNumber:
    def test_unavailable_when_control_settings_are_stale(self) -> None:
        number = _number(RoomState(humidity_min_level=30))
        assert number.available is True

        number.coordinator.read_group_available.return_value = False

        assert number.available is False
        number.coordinator.read_group_available.assert_called_with("unit_1", "control_settings")

    def test_reads_the_setting(self) -> None:
        assert _number(RoomState(humidity_min_level=30)).native_value == 30.0

    async def test_writes_the_rounded_setting(self) -> None:
        number = _number()

        await number.async_set_native_value(44.6)

        number.coordinator.async_set_control_setting.assert_awaited_once_with(
            "unit_1", "humidity_min_level", 45
        )
