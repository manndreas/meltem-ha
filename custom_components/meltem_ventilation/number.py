"""Writable number entities for Meltem units.

These entities are optimistic on purpose: Home Assistant updates the slider
immediately, then waits for the coordinator to confirm the new value from the
gateway. This keeps the UI responsive even though writes settle slowly.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from homeassistant.components.number import (
    NumberEntity,
    NumberEntityDescription,
    NumberMode,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import CO2_PROFILES, CONTROL_SETTING_LIMITS, HUMIDITY_PROFILES
from .entity import MeltemEntity, room_supports_entity
from .models import MeltemRuntimeData, RoomState


@dataclass(frozen=True, kw_only=True)
class MeltemControlSettingNumberDescription(NumberEntityDescription):
    """Describe one writable humidity/CO2 control setting."""

    supported_profiles: frozenset[str]
    value_fn: Callable[[RoomState], int | None]


CONTROL_SETTING_DESCRIPTIONS: tuple[MeltemControlSettingNumberDescription, ...] = (
    MeltemControlSettingNumberDescription(
        key="humidity_starting_point",
        native_min_value=CONTROL_SETTING_LIMITS["humidity_starting_point"][0],
        native_max_value=CONTROL_SETTING_LIMITS["humidity_starting_point"][1],
        native_step=CONTROL_SETTING_LIMITS["humidity_starting_point"][2],
        native_unit_of_measurement="%",
        icon="mdi:water-percent",
        supported_profiles=HUMIDITY_PROFILES,
        value_fn=lambda state: state.humidity_starting_point,
    ),
    MeltemControlSettingNumberDescription(
        key="humidity_min_level",
        native_min_value=CONTROL_SETTING_LIMITS["humidity_min_level"][0],
        native_max_value=CONTROL_SETTING_LIMITS["humidity_min_level"][1],
        native_step=CONTROL_SETTING_LIMITS["humidity_min_level"][2],
        native_unit_of_measurement="%",
        icon="mdi:fan-minus",
        supported_profiles=HUMIDITY_PROFILES,
        value_fn=lambda state: state.humidity_min_level,
    ),
    MeltemControlSettingNumberDescription(
        key="humidity_max_level",
        native_min_value=CONTROL_SETTING_LIMITS["humidity_max_level"][0],
        native_max_value=CONTROL_SETTING_LIMITS["humidity_max_level"][1],
        native_step=CONTROL_SETTING_LIMITS["humidity_max_level"][2],
        native_unit_of_measurement="%",
        icon="mdi:fan-plus",
        supported_profiles=HUMIDITY_PROFILES,
        value_fn=lambda state: state.humidity_max_level,
    ),
    MeltemControlSettingNumberDescription(
        key="co2_starting_point",
        native_min_value=CONTROL_SETTING_LIMITS["co2_starting_point"][0],
        native_max_value=CONTROL_SETTING_LIMITS["co2_starting_point"][1],
        native_step=CONTROL_SETTING_LIMITS["co2_starting_point"][2],
        native_unit_of_measurement="ppm",
        icon="mdi:molecule-co2",
        supported_profiles=CO2_PROFILES,
        value_fn=lambda state: state.co2_starting_point,
    ),
    MeltemControlSettingNumberDescription(
        key="co2_min_level",
        native_min_value=CONTROL_SETTING_LIMITS["co2_min_level"][0],
        native_max_value=CONTROL_SETTING_LIMITS["co2_min_level"][1],
        native_step=CONTROL_SETTING_LIMITS["co2_min_level"][2],
        native_unit_of_measurement="%",
        icon="mdi:fan-minus",
        supported_profiles=CO2_PROFILES,
        value_fn=lambda state: state.co2_min_level,
    ),
    MeltemControlSettingNumberDescription(
        key="co2_max_level",
        native_min_value=CONTROL_SETTING_LIMITS["co2_max_level"][0],
        native_max_value=CONTROL_SETTING_LIMITS["co2_max_level"][1],
        native_step=CONTROL_SETTING_LIMITS["co2_max_level"][2],
        native_unit_of_measurement="%",
        icon="mdi:fan-plus",
        supported_profiles=CO2_PROFILES,
        value_fn=lambda state: state.co2_max_level,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Meltem number entities."""

    runtime_data: MeltemRuntimeData = entry.runtime_data
    coordinator = runtime_data.coordinator

    entities: list[NumberEntity] = []
    for room in coordinator.rooms:
        for description in CONTROL_SETTING_DESCRIPTIONS:
            if room_supports_entity(
                room, description.key, description.supported_profiles
            ):
                entities.append(
                    MeltemControlSettingNumber(coordinator, room, description)
                )
    async_add_entities(entities)


class MeltemControlSettingNumber(MeltemEntity, NumberEntity):
    """Writable config number for humidity/CO2 automation thresholds."""

    entity_description: MeltemControlSettingNumberDescription
    _attr_entity_category = EntityCategory.CONFIG
    _attr_mode = NumberMode.BOX

    def __init__(
        self,
        coordinator,
        room,
        description: MeltemControlSettingNumberDescription,
    ) -> None:
        super().__init__(coordinator, room, description.key, description.key)
        self.entity_description = description

    @property
    def native_value(self) -> float | None:
        value = self.entity_description.value_fn(self.room_state)
        return float(value) if value is not None else None

    async def async_set_native_value(self, value: float) -> None:
        await self.coordinator.async_set_control_setting(
            self.room.key,
            self.entity_description.key,
            int(round(value)),
        )
