"""Sensor entities for Meltem units.

This platform exposes read-only measurements. Whether a sensor is created is
decided from the stored room profile plus the minimal setup-time capability
probe.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory, UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    ALL_PROFILES,
    CO2_PROFILES,
    CONF_PORT,
    DOMAIN,
    HUMIDITY_PROFILES,
    VOC_PROFILES,
)
from .entity import MeltemEntity, room_supports_entity
from .models import MeltemRuntimeData, RoomState


@dataclass(frozen=True, kw_only=True)
class MeltemSensorDescription(SensorEntityDescription):
    """Describe a Meltem sensor."""

    supported_profiles: frozenset[str]
    value_fn: Callable[[RoomState], int | float | None]


MODBUS_SLAVE_ID_DESCRIPTION = SensorEntityDescription(
    key="modbus_slave_id",
    entity_category=EntityCategory.DIAGNOSTIC,
    entity_registry_enabled_default=False,
)

MODBUS_DEVICE_PATH_DESCRIPTION = SensorEntityDescription(
    key="modbus_device_path",
    entity_category=EntityCategory.DIAGNOSTIC,
    entity_registry_enabled_default=False,
)


SENSOR_DESCRIPTIONS: tuple[MeltemSensorDescription, ...] = (
    MeltemSensorDescription(
        key="exhaust_temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        suggested_display_precision=1,
        supported_profiles=ALL_PROFILES,
        value_fn=lambda state: state.exhaust_temperature,
    ),
    MeltemSensorDescription(
        key="outdoor_air_temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        suggested_display_precision=1,
        # Only the -F and -FC variants carry this sensor, see docs/MELTEM.md.
        supported_profiles=HUMIDITY_PROFILES,
        value_fn=lambda state: state.outdoor_air_temperature,
    ),
    MeltemSensorDescription(
        key="extract_air_temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        suggested_display_precision=1,
        supported_profiles=HUMIDITY_PROFILES,
        value_fn=lambda state: state.extract_air_temperature,
    ),
    MeltemSensorDescription(
        key="supply_air_temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        suggested_display_precision=1,
        supported_profiles=HUMIDITY_PROFILES,
        value_fn=lambda state: state.supply_air_temperature,
    ),
    MeltemSensorDescription(
        key="humidity_extract_air",
        device_class=SensorDeviceClass.HUMIDITY,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement="%",
        supported_profiles=HUMIDITY_PROFILES,
        value_fn=lambda state: state.humidity_extract_air,
    ),
    MeltemSensorDescription(
        key="humidity_supply_air",
        device_class=SensorDeviceClass.HUMIDITY,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement="%",
        supported_profiles=HUMIDITY_PROFILES,
        value_fn=lambda state: state.humidity_supply_air,
    ),
    MeltemSensorDescription(
        key="co2_extract_air",
        device_class=SensorDeviceClass.CO2,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement="ppm",
        supported_profiles=CO2_PROFILES,
        value_fn=lambda state: state.co2_extract_air,
    ),
    MeltemSensorDescription(
        key="voc_supply_air",
        device_class=SensorDeviceClass.VOLATILE_ORGANIC_COMPOUNDS_PARTS,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement="ppm",
        supported_profiles=VOC_PROFILES,
        value_fn=lambda state: state.voc_supply_air,
    ),
    MeltemSensorDescription(
        key="extract_air_flow",
        icon="mdi:home-export-outline",
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement="m³/h",
        supported_profiles=ALL_PROFILES,
        value_fn=lambda state: state.extract_air_flow,
    ),
    MeltemSensorDescription(
        key="supply_air_flow",
        icon="mdi:home-import-outline",
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement="m³/h",
        supported_profiles=ALL_PROFILES,
        value_fn=lambda state: state.supply_air_flow,
    ),
    MeltemSensorDescription(
        key="days_until_filter_change",
        icon="mdi:calendar-clock",
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement="d",
        entity_category=EntityCategory.DIAGNOSTIC,
        supported_profiles=ALL_PROFILES,
        value_fn=lambda state: state.days_until_filter_change,
    ),
    MeltemSensorDescription(
        key="operating_hours",
        icon="mdi:fan-clock",
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement="h",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        supported_profiles=ALL_PROFILES,
        value_fn=lambda state: state.operating_hours,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Meltem sensor entities."""

    runtime_data: MeltemRuntimeData = entry.runtime_data
    coordinator = runtime_data.coordinator

    entities: list[SensorEntity] = [MeltemModbusDevicePathSensor(entry)]
    entities.extend(
        MeltemModbusSlaveSensor(coordinator, room)
        for room in coordinator.rooms
        if room_supports_entity(room, MODBUS_SLAVE_ID_DESCRIPTION.key)
    )
    entities.extend(
        MeltemSensorEntity(coordinator, room, description)
        for room in coordinator.rooms
        for description in SENSOR_DESCRIPTIONS
        if room_supports_entity(room, description.key, description.supported_profiles)
    )
    async_add_entities(entities)


class MeltemSensorEntity(MeltemEntity, SensorEntity):
    """Representation of one Meltem sensor."""

    entity_description: MeltemSensorDescription

    def __init__(
        self,
        coordinator,
        room,
        description: MeltemSensorDescription,
    ) -> None:
        super().__init__(coordinator, room, description.key, description.key)
        self.entity_description = description

    @property
    def native_value(self) -> int | float | None:
        return self.entity_description.value_fn(self.room_state)


class MeltemModbusSlaveSensor(MeltemEntity, SensorEntity):
    """Representation of one room's configured Modbus slave ID."""

    entity_description = MODBUS_SLAVE_ID_DESCRIPTION

    def __init__(self, coordinator, room) -> None:
        super().__init__(coordinator, room, "modbus_slave_id", "modbus_slave_id")

    @property
    def native_value(self) -> int:
        return self.room.slave


class MeltemModbusDevicePathSensor(SensorEntity):
    """Representation of the serial device path for one config entry."""

    entity_description = MODBUS_DEVICE_PATH_DESCRIPTION
    _attr_has_entity_name = True

    def __init__(self, entry: ConfigEntry) -> None:
        self._attr_unique_id = f"{DOMAIN}_{entry.entry_id}_modbus_device_path"
        self._attr_translation_key = MODBUS_DEVICE_PATH_DESCRIPTION.key
        self._device_path = entry.data[CONF_PORT]

    @property
    def native_value(self) -> str:
        return self._device_path
