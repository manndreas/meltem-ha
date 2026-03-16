"""Select entities for Meltem operating modes."""

from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import CO2_PROFILES, HUMIDITY_PROFILES
from .entity import MeltemEntity, room_supports_entity
from .models import MeltemRuntimeData, RoomConfig


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Meltem select entities."""

    runtime_data: MeltemRuntimeData = entry.runtime_data
    coordinator = runtime_data.coordinator

    async_add_entities(
        MeltemOperationModeSelect(coordinator, room)
        for room in coordinator.rooms
        if _room_supports(room, "operation_mode")
    )


def _room_supports(room: RoomConfig, entity_key: str) -> bool:
    """Return whether one select entity should exist for one room."""
    return room_supports_entity(room, entity_key)


class MeltemOperationModeSelect(MeltemEntity, SelectEntity):
    """Select entity for the documented Meltem operation modes."""

    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator, room: RoomConfig) -> None:
        super().__init__(coordinator, room, "operation_mode", "operation_mode")
        options = ["off", "manual", "unbalanced"]
        if room.profile in HUMIDITY_PROFILES:
            options.append("humidity_control")
        if room.profile in CO2_PROFILES:
            options.extend(["co2_control", "automatic"])
        self._attr_options = options
        self._attr_icon = "mdi:fan-auto"

    @property
    def current_option(self) -> str | None:
        return self.room_state.operation_mode

    async def async_select_option(self, option: str) -> None:
        await self.coordinator.async_set_operation_mode(self.room.key, option)
