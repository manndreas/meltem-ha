"""Select entities for Meltem operating modes."""

from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import (
    CO2_PROFILES,
    DIRECT_OPERATION_MODES,
    HUMIDITY_PROFILES,
    OPERATION_MODE_INACTIVE,
    OPERATION_MODE_MANUAL,
    PRESET_MODE_INACTIVE,
    PRESET_MODE_OPTIONS,
    SENSOR_CONTROL_PROFILES,
)
from .entity import MeltemEntity, room_supports_entity
from .models import MeltemRuntimeData, RoomConfig


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up Meltem select entities."""

    runtime_data: MeltemRuntimeData = entry.runtime_data
    coordinator = runtime_data.coordinator

    entities: list[SelectEntity] = []
    for room in coordinator.rooms:
        if room_supports_entity(room, "operation_mode", SENSOR_CONTROL_PROFILES):
            entities.append(MeltemOperationModeSelect(coordinator, room))
        if room_supports_entity(room, "preset_mode"):
            entities.append(MeltemPresetModeSelect(coordinator, room))
    async_add_entities(entities)


class MeltemOperationModeSelect(MeltemEntity, SelectEntity):
    """Select entity for the sensor-driven control modes.

    Off, manual and unbalanced are reachable through the two fan entities and
    are therefore not offered here.
    """

    _requires_fresh_read_group = False
    _attr_entity_category = EntityCategory.CONFIG
    _attr_icon = "mdi:fan-auto"

    def __init__(self, coordinator, room: RoomConfig) -> None:
        super().__init__(coordinator, room, "operation_mode", "operation_mode")
        options = [OPERATION_MODE_INACTIVE]
        if room.profile in HUMIDITY_PROFILES:
            options.append("humidity_control")
        if room.profile in CO2_PROFILES:
            options.extend(["co2_control", "automatic"])
        self._attr_options = options

    @property
    def current_option(self) -> str | None:
        operation_mode = self.room_state.operation_mode
        if operation_mode is None or operation_mode in self._attr_options:
            return operation_mode
        # Any manual airflow state means no sensor control is running.
        return OPERATION_MODE_INACTIVE

    async def async_select_option(self, option: str) -> None:
        if option != OPERATION_MODE_INACTIVE:
            await self.coordinator.async_set_operation_mode(self.room.key, option)
        elif self.room_state.operation_mode not in DIRECT_OPERATION_MODES:
            # Off, manual and unbalanced are inactive already; writing manual
            # again would collapse an unbalanced setup onto a single airflow.
            await self.coordinator.async_set_operation_mode(
                self.room.key, OPERATION_MODE_MANUAL
            )


class MeltemPresetModeSelect(MeltemEntity, SelectEntity):
    """Select entity for the confirmed app-style keypad quick modes."""

    _requires_fresh_read_group = False
    _attr_icon = "mdi:flash-outline"
    _attr_options = list(PRESET_MODE_OPTIONS)

    def __init__(self, coordinator, room: RoomConfig) -> None:
        super().__init__(coordinator, room, "preset_mode", "preset_mode")

    @property
    def current_option(self) -> str | None:
        optimistic = self.coordinator.optimistic_preset_mode(self.room.key)
        if optimistic is not None:
            return optimistic
        state = self.room_state
        if state.preset_mode in self._attr_options:
            return state.preset_mode
        if state.preset_mode is None and state.operation_mode is None:
            return None
        # The decoder reports "no quick mode" as None once the mode is known, and
        # extract_only/supply_only are expressed by the two fan entities instead.
        return PRESET_MODE_INACTIVE

    async def async_select_option(self, option: str) -> None:
        if option == PRESET_MODE_INACTIVE:
            await self.coordinator.async_clear_preset_mode(self.room.key)
        else:
            await self.coordinator.async_set_preset_mode(self.room.key, option)
