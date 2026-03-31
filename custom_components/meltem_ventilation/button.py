"""Button entities for Meltem actions."""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .entity import MeltemEntity, room_supports_entity
from .models import MeltemRuntimeData, RoomConfig


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Meltem button entities."""

    runtime_data: MeltemRuntimeData = entry.runtime_data
    coordinator = runtime_data.coordinator

    async_add_entities(
        MeltemActivateIntensiveButton(coordinator, room)
        for room in coordinator.rooms
        if _room_supports(room, "activate_intensive")
    )


def _room_supports(room: RoomConfig, entity_key: str) -> bool:
    """Return whether one button entity should exist for one room."""
    return room_supports_entity(room, entity_key)


class MeltemActivateIntensiveButton(MeltemEntity, ButtonEntity):
    """Button entity to start temporary intensive ventilation."""

    def __init__(self, coordinator, room: RoomConfig) -> None:
        super().__init__(coordinator, room, "activate_intensive", "activate_intensive")
        self._attr_icon = "mdi:fan-plus"

    async def async_press(self) -> None:
        await self.coordinator.async_activate_intensive(self.room.key)
