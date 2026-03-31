"""Binary sensor entities for Meltem units.

The binary sensors are primarily status/diagnostic flags exposed by the unit
or the RF link behind the gateway.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import ALL_PROFILES
from .entity import MeltemEntity, room_supports_entity
from .models import MeltemRuntimeData, RoomConfig, RoomState


@dataclass(frozen=True, kw_only=True)
class MeltemBinarySensorDescription(BinarySensorEntityDescription):
    """Describe a Meltem binary sensor."""

    supported_profiles: frozenset[str]
    value_fn: Callable[[RoomState], bool | None]


BINARY_SENSOR_DESCRIPTIONS: tuple[MeltemBinarySensorDescription, ...] = (
    MeltemBinarySensorDescription(
        key="error_status",
        icon="mdi:alert-circle-outline",
        device_class=BinarySensorDeviceClass.PROBLEM,
        supported_profiles=ALL_PROFILES,
        value_fn=lambda state: state.error_status,
    ),
    MeltemBinarySensorDescription(
        key="frost_protection_active",
        icon="mdi:snowflake-thermometer",
        supported_profiles=ALL_PROFILES,
        value_fn=lambda state: state.frost_protection_active,
    ),
    MeltemBinarySensorDescription(
        key="filter_change_due",
        icon="mdi:air-filter",
        device_class=BinarySensorDeviceClass.PROBLEM,
        supported_profiles=ALL_PROFILES,
        value_fn=lambda state: state.filter_change_due,
    ),
    MeltemBinarySensorDescription(
        key="intensive_active",
        icon="mdi:fan-clock",
        supported_profiles=ALL_PROFILES,
        value_fn=lambda state: state.intensive_active,
    ),
    MeltemBinarySensorDescription(
        key="rf_comm_status",
        icon="mdi:wifi-alert",
        device_class=BinarySensorDeviceClass.PROBLEM,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        supported_profiles=ALL_PROFILES,
        value_fn=lambda state: state.rf_comm_status,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Meltem binary sensor entities."""

    runtime_data: MeltemRuntimeData = entry.runtime_data
    coordinator = runtime_data.coordinator

    async_add_entities(
        MeltemBinarySensorEntity(coordinator, room, description)
        for room in coordinator.rooms
        for description in BINARY_SENSOR_DESCRIPTIONS
        if _supports_profile(room, description)
    )


def _supports_profile(
    room: RoomConfig, description: MeltemBinarySensorDescription
) -> bool:
    """Return whether one binary sensor description should exist for one room."""
    if room.profile not in description.supported_profiles:
        return False
    return room_supports_entity(room, description.key)


class MeltemBinarySensorEntity(MeltemEntity, BinarySensorEntity):
    """Representation of one Meltem binary sensor."""

    entity_description: MeltemBinarySensorDescription

    def __init__(self, coordinator, room, description: MeltemBinarySensorDescription) -> None:
        super().__init__(coordinator, room, description.key, description.key)
        self.entity_description = description

    @property
    def is_on(self) -> bool | None:
        return self.entity_description.value_fn(self.room_state)
