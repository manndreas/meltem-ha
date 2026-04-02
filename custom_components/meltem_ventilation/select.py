"""Select entities for Meltem operating modes."""

from __future__ import annotations

import time

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    CO2_PROFILES,
    HUMIDITY_PROFILES,
    PRESET_MODE_INACTIVE,
    PRESET_MODE_OPTIONS,
)
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

    entities: list[SelectEntity] = []
    for room in coordinator.rooms:
        if _room_supports(room, "operation_mode"):
            entities.append(MeltemOperationModeSelect(coordinator, room))
        if _room_supports(room, "preset_mode"):
            entities.append(MeltemPresetModeSelect(coordinator, room))
    async_add_entities(entities)


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


class MeltemPresetModeSelect(MeltemEntity, SelectEntity):
    """Select entity for confirmed app-style keypad presets."""

    _optimistic_duration_seconds = 15.0

    def __init__(self, coordinator, room: RoomConfig) -> None:
        super().__init__(coordinator, room, "preset_mode", "preset_mode")
        self._attr_options = list(PRESET_MODE_OPTIONS)
        self._attr_icon = "mdi:flash-outline"
        self._optimistic_targets_by_room = coordinator.optimistic_targets_by_room
        self._optimistic_option: str | None = None
        self._optimistic_until: float = 0.0

    @property
    def current_option(self) -> str | None:
        if self.room.key in self._optimistic_targets_by_room:
            return PRESET_MODE_INACTIVE
        if self._optimistic_option is not None and time.monotonic() < self._optimistic_until:
            return self._optimistic_option
        self._optimistic_option = None
        return self.room_state.preset_mode or PRESET_MODE_INACTIVE

    async def async_select_option(self, option: str) -> None:
        self._optimistic_option = option
        self._optimistic_until = time.monotonic() + self._optimistic_duration_seconds
        if self.hass is not None:
            self.async_write_ha_state()
        try:
            if option == PRESET_MODE_INACTIVE:
                await self.coordinator.async_clear_preset_mode(self.room.key)
            else:
                await self.coordinator.async_set_preset_mode(self.room.key, option)
        except Exception:
            self._optimistic_option = None
            self._optimistic_until = 0.0
            if self.hass is not None:
                self.async_write_ha_state()
            raise

    def _handle_coordinator_update(self) -> None:
        if self._optimistic_option is not None:
            backend_option = self.room_state.preset_mode
            if (
                backend_option == self._optimistic_option
                or time.monotonic() >= self._optimistic_until
            ):
                self._optimistic_option = None
                self._optimistic_until = 0.0
        if self.hass is None:
            return
        super()._handle_coordinator_update()
