"""Sensor entities for Meltem units.

This platform exposes read-only measurements. Whether a sensor is created is
decided from the stored room profile plus the minimal setup-time capability
probe.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorEntityDescription, SensorStateClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory, UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import ALL_PROFILES, CO2_PROFILES, HUMIDITY_PROFILES, VOC_PROFILES
from .entity import MeltemEntity, room_supports_entity
from .models import MeltemRuntimeData, RoomConfig, RoomState


@dataclass(frozen=True, kw_only=True)
class MeltemSensorDescription(SensorEntityDescription):
    """Describe a Meltem sensor."""

    supported_profiles: frozenset[str]
    value_fn: Callable[[RoomState], int | float | None]


def _average_air_flow(state: RoomState) -> float | None:
    """Return the average airflow across supply and extract."""

    if state.extract_air_flow is None and state.supply_air_flow is None:
        return None
    if state.extract_air_flow is None:
        return float(state.supply_air_flow)
    if state.supply_air_flow is None:
        return float(state.extract_air_flow)
    return float(round((state.extract_air_flow + state.supply_air_flow) / 2))


def _common_air_flow(state: RoomState) -> float | None:
    """Return one shared airflow only when both measured directions still match."""

    if state.extract_air_flow is None or state.supply_air_flow is None:
        return None
    if abs(state.extract_air_flow - state.supply_air_flow) > 1:
        return None
    return float(round((state.extract_air_flow + state.supply_air_flow) / 2))


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
        supported_profiles=ALL_PROFILES,
        value_fn=lambda state: state.outdoor_air_temperature,
    ),
    MeltemSensorDescription(
        key="extract_air_temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        suggested_display_precision=1,
        supported_profiles=ALL_PROFILES,
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
        icon="mdi:fan-chevron-up",
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement="m³/h",
        supported_profiles=ALL_PROFILES,
        value_fn=lambda state: state.extract_air_flow,
    ),
    MeltemSensorDescription(
        key="supply_air_flow",
        icon="mdi:fan-chevron-down",
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
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement="h",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        supported_profiles=ALL_PROFILES,
        value_fn=lambda state: state.operating_hours,
    ),
    MeltemSensorDescription(
        key="software_version",
        icon="mdi:information-outline",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        supported_profiles=ALL_PROFILES,
        value_fn=lambda state: state.software_version,
    ),
    MeltemSensorDescription(
        key="current_level",
        icon="mdi:fan",
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement="m³/h",
        supported_profiles=ALL_PROFILES,
        value_fn=_common_air_flow,
    ),
    MeltemSensorDescription(
        key="average_air_flow",
        icon="mdi:fan-auto",
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement="m³/h",
        supported_profiles=ALL_PROFILES,
        value_fn=_average_air_flow,
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

    async_add_entities(
        MeltemSensorEntity(coordinator, room, description)
        for room in coordinator.rooms
        for description in SENSOR_DESCRIPTIONS
        if _supports_profile(room, description)
    )


def _supports_profile(
    room: RoomConfig, description: MeltemSensorDescription
) -> bool:
    """Return whether one sensor description should exist for one room."""
    if room.profile not in description.supported_profiles:
        return False
    return room_supports_entity(room, description.key)


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
