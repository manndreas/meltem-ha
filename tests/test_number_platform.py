"""Tests for the number platform."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

from homeassistant.core import HomeAssistant

from custom_components.meltem_ventilation.coordinator import MeltemDataUpdateCoordinator
from custom_components.meltem_ventilation.entity import (
    room_supports_entity as _room_supports,
)
from custom_components.meltem_ventilation.models import RoomConfig, RoomState
from custom_components.meltem_ventilation.number import MeltemControlSettingNumber

_ROOM_ALL = RoomConfig(key="unit_1", name="Unit 1", profile="ii_plain", slave=2)
_ROOM_S = RoomConfig(key="unit_s", name="Unit S", profile="s_plain", slave=4)


def _fake_coordinator(
    hass: HomeAssistant | None = None,
    rooms: list[RoomConfig] | None = None,
    data: dict[str, RoomState] | None = None,
) -> MagicMock:
    coordinator = MagicMock(spec=MeltemDataUpdateCoordinator)
    coordinator.data = data or {}
    type(coordinator).safe_data = property(lambda self: self.data if isinstance(self.data, dict) else {})
    coordinator.rooms = rooms or [_ROOM_ALL]
    coordinator.hass = hass
    coordinator.async_set_control_setting = AsyncMock()
    coordinator.async_request_refresh = AsyncMock()
    coordinator.async_add_listener = MagicMock(return_value=lambda: None)
    coordinator.async_update_listeners = MagicMock()
    return coordinator


class TestRoomSupports:
    def test_all_keys_supported_when_none(self) -> None:
        room = RoomConfig(key="a", name="A", profile="ii_plain", slave=2)
        assert _room_supports(room, "humidity_min_level")

    def test_only_listed_keys_pass(self) -> None:
        room = RoomConfig(
            key="a",
            name="A",
            profile="ii_plain",
            slave=2,
            supported_entity_keys=frozenset({"humidity_min_level"}),
        )
        assert _room_supports(room, "humidity_min_level")
        assert not _room_supports(room, "humidity_max_level")


class TestControlSettingNumber:
    def test_reads_config_value(self) -> None:
        coordinator = _fake_coordinator(data={"unit_1": RoomState(humidity_min_level=30)})
        description = MagicMock()
        description.key = "humidity_min_level"
        description.native_min_value = 0
        description.native_max_value = 100
        description.native_step = 10
        description.native_unit_of_measurement = "%"
        description.icon = "mdi:fan-minus"
        description.supported_profiles = frozenset({"ii_plain"})
        description.value_fn = lambda state: state.humidity_min_level

        entity = MeltemControlSettingNumber(coordinator, _ROOM_ALL, description)

        assert entity.native_value == 30.0

    async def test_sets_config_value(self) -> None:
        coordinator = _fake_coordinator()
        description = MagicMock()
        description.key = "humidity_min_level"
        description.native_min_value = 0
        description.native_max_value = 100
        description.native_step = 10
        description.native_unit_of_measurement = "%"
        description.icon = "mdi:fan-minus"
        description.supported_profiles = frozenset({"ii_plain"})
        description.value_fn = lambda state: state.humidity_min_level

        entity = MeltemControlSettingNumber(coordinator, _ROOM_ALL, description)

        await entity.async_set_native_value(45.0)

        coordinator.async_set_control_setting.assert_awaited_once_with(
            "unit_1", "humidity_min_level", 45
        )
