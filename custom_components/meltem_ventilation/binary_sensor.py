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
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import ALL_PROFILES, READ_GROUP_ENTITY_KEYS
from .entity import MeltemEntity, room_supports_entity
from .models import MeltemRuntimeData, RoomState


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
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up Meltem binary sensor entities."""

    runtime_data: MeltemRuntimeData = entry.runtime_data
    coordinator = runtime_data.coordinator

    entities: list[BinarySensorEntity] = [
        MeltemBinarySensorEntity(coordinator, room, description)
        for room in coordinator.rooms
        for description in BINARY_SENSOR_DESCRIPTIONS
        if room_supports_entity(room, description.key, description.supported_profiles)
    ]
    entities.extend(
        MeltemDataHealthBinarySensor(coordinator, room)
        for room in coordinator.rooms
        if room_supports_entity(room, "supply_air_flow")
        or room_supports_entity(room, "extract_air_flow")
    )
    async_add_entities(entities)


class MeltemBinarySensorEntity(MeltemEntity, BinarySensorEntity):
    """Representation of one Meltem binary sensor."""

    entity_description: MeltemBinarySensorDescription

    def __init__(self, coordinator, room, description: MeltemBinarySensorDescription) -> None:
        super().__init__(coordinator, room, description.key, description.key)
        self.entity_description = description

    @property
    def is_on(self) -> bool | None:
        return self.entity_description.value_fn(self.room_state)


class MeltemDataHealthBinarySensor(MeltemEntity, BinarySensorEntity):
    """Report stale read groups and expose their last outcomes."""

    _attr_device_class = BinarySensorDeviceClass.PROBLEM
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_icon = "mdi:fan-alert"
    # The timestamps change on every poll and would bloat the recorder.
    _unrecorded_attributes = frozenset({*READ_GROUP_ENTITY_KEYS, "writes"})

    def __init__(self, coordinator, room) -> None:
        super().__init__(coordinator, room, "data_health", "data_health")

    @property
    def available(self) -> bool:
        return (
            self.coordinator.last_update_success
            and self.room.key in self.coordinator.safe_data
        )

    @property
    def is_on(self) -> bool | None:
        return self.coordinator.data_health_stale(self.room.key)

    @property
    def extra_state_attributes(self) -> dict:
        return self.coordinator.data_health_attributes(self.room.key)
