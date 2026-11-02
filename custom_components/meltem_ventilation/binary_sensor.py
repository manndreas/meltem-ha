"""Binary sensor entities for Meltem units.

The binary sensors are primarily status/diagnostic flags exposed by the unit
or the RF link behind the gateway.
"""

# Home Assistant types entity state as cached_property; like Home Assistant Core,
# these entities override it with plain properties computed from coordinator data.
# pyright: reportIncompatibleVariableOverride=false
from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass

from homeassistant.components.binary_sensor import (
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.components.binary_sensor.const import BinarySensorDeviceClass
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import DATA_HEALTH_ENTITY_KEY, GATEWAY_SILENT_AFTER_SECONDS, READ_GROUP_ENTITY_KEYS
from .coordinator import MeltemDataUpdateCoordinator
from .entity import (
    MeltemEntity,
    MeltemGatewayEntity,
    MeltemRoomDiagnosticEntity,
    async_add_room_entities,
    room_has_entity,
)
from .models import (
    MeltemIntegrationConfigEntry,
    RoomConfig,
    RoomState,
)


@dataclass(frozen=True, kw_only=True)
class MeltemBinarySensorDescription(BinarySensorEntityDescription):
    """Describe a Meltem binary sensor."""

    value_fn: Callable[[RoomState], bool | None]


BINARY_SENSOR_DESCRIPTIONS: tuple[MeltemBinarySensorDescription, ...] = (
    MeltemBinarySensorDescription(
        key="error_status",
        device_class=BinarySensorDeviceClass.PROBLEM,
        value_fn=lambda state: state.error_status,
    ),
    MeltemBinarySensorDescription(
        key="frost_protection_active",
        value_fn=lambda state: state.frost_protection_active,
    ),
    MeltemBinarySensorDescription(
        key="filter_change_due",
        device_class=BinarySensorDeviceClass.PROBLEM,
        value_fn=lambda state: state.filter_change_due,
    ),
    MeltemBinarySensorDescription(
        key="rf_comm_status",
        device_class=BinarySensorDeviceClass.PROBLEM,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda state: state.rf_comm_status,
    ),
    MeltemBinarySensorDescription(
        key="rf_fault_status",
        device_class=BinarySensorDeviceClass.PROBLEM,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda state: state.rf_fault_status,
    ),
    MeltemBinarySensorDescription(
        key="value_error_status",
        device_class=BinarySensorDeviceClass.PROBLEM,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda state: state.value_error_status,
    ),
)


GATEWAY_CONNECTIVITY_DESCRIPTION = BinarySensorEntityDescription(
    key="gateway_connectivity",
    device_class=BinarySensorDeviceClass.CONNECTIVITY,
    entity_category=EntityCategory.DIAGNOSTIC,
)


ROOM_CONNECTIVITY_DESCRIPTION = BinarySensorEntityDescription(
    key="connectivity",
    device_class=BinarySensorDeviceClass.CONNECTIVITY,
    entity_category=EntityCategory.DIAGNOSTIC,
)


# The coordinator serializes all gateway access itself.
PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: MeltemIntegrationConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up Meltem binary sensor entities."""

    coordinator = entry.runtime_data.coordinator

    async_add_entities([MeltemGatewayConnectivitySensor(coordinator, entry.entry_id)])

    def entities_of(room: RoomConfig) -> list[BinarySensorEntity]:
        entities: list[BinarySensorEntity] = [
            MeltemBinarySensorEntity(coordinator, room, description)
            for description in BINARY_SENSOR_DESCRIPTIONS
            if room_has_entity(room, description.key)
        ]
        if room_has_entity(room, DATA_HEALTH_ENTITY_KEY):
            entities.append(MeltemDataHealthBinarySensor(coordinator, room))
        if room_has_entity(room, ROOM_CONNECTIVITY_DESCRIPTION.key):
            entities.append(MeltemRoomConnectivitySensor(coordinator, room))
        return entities

    async_add_room_entities(coordinator, async_add_entities, entities_of)


class MeltemGatewayConnectivitySensor(MeltemGatewayEntity, BinarySensorEntity):
    """Report recent gateway responses independently of individual units."""

    entity_description = GATEWAY_CONNECTIVITY_DESCRIPTION

    def __init__(self, coordinator: MeltemDataUpdateCoordinator, entry_id: str) -> None:
        super().__init__(coordinator, entry_id, self.entity_description.key)

    @property
    def is_on(self) -> bool | None:
        age = self.coordinator.client.transport_diagnostics()["seconds_since_any_answer"]
        return None if age is None else age <= GATEWAY_SILENT_AFTER_SECONDS


class MeltemRoomConnectivitySensor(MeltemRoomDiagnosticEntity, BinarySensorEntity):
    """Expose unit availability without hiding the disconnected state."""

    entity_description = ROOM_CONNECTIVITY_DESCRIPTION

    def __init__(self, coordinator: MeltemDataUpdateCoordinator, room: RoomConfig) -> None:
        super().__init__(coordinator, room, self.entity_description.key)

    @property
    def is_on(self) -> bool | None:
        return self.coordinator.room_connectivity(self.room.key)


class MeltemBinarySensorEntity(MeltemEntity, BinarySensorEntity):
    """Representation of one Meltem binary sensor."""

    entity_description: MeltemBinarySensorDescription

    def __init__(
        self,
        coordinator: MeltemDataUpdateCoordinator,
        room: RoomConfig,
        description: MeltemBinarySensorDescription,
    ) -> None:
        super().__init__(coordinator, room, description.key, description.key)
        self.entity_description = description

    @property
    def is_on(self) -> bool | None:
        return self.entity_description.value_fn(self.room_state)


class MeltemDataHealthBinarySensor(MeltemRoomDiagnosticEntity, BinarySensorEntity):
    """Report stale read groups and expose their last outcomes."""

    _attr_device_class = BinarySensorDeviceClass.PROBLEM
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    # The timestamps change on every poll and would bloat the recorder.
    _unrecorded_attributes = frozenset({*READ_GROUP_ENTITY_KEYS, "writes"})

    def __init__(self, coordinator: MeltemDataUpdateCoordinator, room: RoomConfig) -> None:
        super().__init__(coordinator, room, DATA_HEALTH_ENTITY_KEY, DATA_HEALTH_ENTITY_KEY)

    @property
    def is_on(self) -> bool | None:
        return self.coordinator.data_health_stale(self.room.key)

    @property
    def extra_state_attributes(self) -> Mapping[str, object]:
        return self.coordinator.data_health_attributes(self.room.key)
