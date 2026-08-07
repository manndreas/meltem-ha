"""Switch entities for Meltem units."""

from __future__ import annotations

from homeassistant.components.switch import SwitchEntity
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
    """Set up Meltem switch entities."""

    runtime_data: MeltemRuntimeData = entry.runtime_data
    coordinator = runtime_data.coordinator

    async_add_entities(
        MeltemIntensiveSwitch(coordinator, room)
        for room in coordinator.rooms
        if room_supports_entity(room, "intensive")
    )


class MeltemIntensiveSwitch(MeltemEntity, SwitchEntity):
    """Temporary intensive ventilation override.

    The unit ends the override on its own after a runtime configured in the
    Meltem app, so the switch can also flip back without user interaction.
    """

    def __init__(self, coordinator, room: RoomConfig) -> None:
        super().__init__(coordinator, room, "intensive", "intensive")
        self._attr_icon = "mdi:fan-plus"

    @property
    def is_on(self) -> bool | None:
        optimistic = self.coordinator.optimistic_intensive(self.room.key)
        if optimistic is not None:
            return optimistic
        return self.room_state.intensive_active

    async def async_turn_on(self, **kwargs) -> None:
        await self.coordinator.async_activate_intensive(self.room.key)

    async def async_turn_off(self, **kwargs) -> None:
        await self.coordinator.async_deactivate_intensive(self.room.key)
