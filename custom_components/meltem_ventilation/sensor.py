"""Sensor entities for Meltem units.

This platform exposes read-only measurements. Which sensors a unit gets follows
from the profile selected for it during setup.
"""

# Home Assistant types entity state as cached_property; like Home Assistant Core,
# these entities override it with plain properties computed from coordinator data.
# pyright: reportIncompatibleVariableOverride=false
from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import (
    PERCENTAGE,
    EntityCategory,
    UnitOfRatio,
    UnitOfTemperature,
    UnitOfTime,
    UnitOfVolumeFlowRate,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import (
    ALL_PROFILES,
    CO2_PROFILES,
    CONF_PORT,
    DOMAIN,
    HUMIDITY_PROFILES,
    VOC_PROFILES,
)
from .coordinator import MeltemDataUpdateCoordinator
from .entity import (
    MeltemEntity,
    MeltemGatewayEntity,
    MeltemRoomDiagnosticEntity,
    gateway_device_info,
    room_supports_entity,
)
from .models import (
    MeltemIntegrationConfigEntry,
    MeltemRuntimeData,
    RoomConfig,
    RoomState,
)


@dataclass(frozen=True, kw_only=True)
class MeltemSensorDescription(SensorEntityDescription):
    """Describe a Meltem sensor."""

    value_fn: Callable[[RoomState], int | float | None]
    supported_profiles: frozenset[str] = ALL_PROFILES


MODBUS_SLAVE_ID_DESCRIPTION = SensorEntityDescription(
    key="modbus_slave_id",
    entity_category=EntityCategory.DIAGNOSTIC,
    entity_registry_enabled_default=False,
)

MODBUS_DEVICE_PATH_DESCRIPTION = SensorEntityDescription(
    key="modbus_device_path",
    translation_key="modbus_device_path",
    entity_category=EntityCategory.DIAGNOSTIC,
    entity_registry_enabled_default=False,
)


@dataclass(frozen=True, kw_only=True)
class MeltemGatewaySensorDescription(SensorEntityDescription):
    """Describe a locally derived gateway diagnostic."""

    value_fn: Callable[[MeltemDataUpdateCoordinator], int | float | datetime | None]
    aggregate_key: str | None = None
    rf_statistics_key: str | None = None
    entity_category: EntityCategory | None = EntityCategory.DIAGNOSTIC
    entity_registry_enabled_default: bool = False


@dataclass(frozen=True)
class _GatewayAggregate:
    value: int | None
    attributes: dict[str, object]


def _gateway_aggregate(coordinator: MeltemDataUpdateCoordinator, key: str) -> _GatewayAggregate:
    if key == "airflow_delta":
        supply = _gateway_aggregate(coordinator, "supply_air_flow")
        extract = _gateway_aggregate(coordinator, "extract_air_flow")
        unknown_units: dict[str, str] = {}
        for aggregate in (supply, extract):
            units = aggregate.attributes.get("unknown_units")
            if isinstance(units, dict):
                unknown_units.update(units)
        if supply.value is None or extract.value is None:
            delta = None
            complete = False
        else:
            delta = supply.value - extract.value
            complete = True
        return _GatewayAggregate(
            delta,
            {"complete": complete, "unknown_units": unknown_units},
        )

    values: dict[str, int] = {}
    unknown: dict[str, str] = {}
    group_key = coordinator.read_group_for_entity(key)
    for room in coordinator.rooms:
        state = coordinator.safe_data.get(room.key)
        value = getattr(state, key, None)
        if (
            not room.supports(key)
            or not isinstance(value, int)
            or not coordinator.room_available(room.key)
            or group_key is None
            or not coordinator.read_group_available(room.key, group_key)
        ):
            unknown[room.key] = room.name
            continue
        values[room.key] = int(value)
    complete = bool(values) and not unknown
    attributes: dict[str, object] = {"complete": complete, "unknown_units": unknown}
    return _GatewayAggregate(sum(values.values()) if complete else None, attributes)


def _gateway_last_answer(coordinator: MeltemDataUpdateCoordinator) -> datetime | None:
    timestamp = coordinator.client.transport_diagnostics()["last_answer_timestamp"]
    return None if timestamp is None else datetime.fromtimestamp(timestamp, tz=UTC)


def _gateway_rf_value(coordinator: MeltemDataUpdateCoordinator, key: str) -> int | float | None:
    statistics = coordinator.gateway_rf_statistics
    return None if statistics is None else getattr(statistics, key)


GATEWAY_SENSOR_DESCRIPTIONS: tuple[MeltemGatewaySensorDescription, ...] = (
    MeltemGatewaySensorDescription(
        key="gateway_rf_messages_current_hour",
        rf_statistics_key="messages_current_hour",
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
        value_fn=lambda coordinator: _gateway_rf_value(coordinator, "messages_current_hour"),
    ),
    MeltemGatewaySensorDescription(
        key="gateway_rf_messages_previous_hour",
        rf_statistics_key="messages_previous_hour",
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
        value_fn=lambda coordinator: _gateway_rf_value(coordinator, "messages_previous_hour"),
    ),
    MeltemGatewaySensorDescription(
        key="gateway_rf_load_current_hour",
        rf_statistics_key="load_current_hour",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=4,
        value_fn=lambda coordinator: _gateway_rf_value(coordinator, "load_current_hour"),
    ),
    MeltemGatewaySensorDescription(
        key="gateway_rf_load_previous_hour",
        rf_statistics_key="load_previous_hour",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=4,
        value_fn=lambda coordinator: _gateway_rf_value(coordinator, "load_previous_hour"),
    ),
    MeltemGatewaySensorDescription(
        key="gateway_supply_air_flow",
        aggregate_key="supply_air_flow",
        entity_registry_enabled_default=True,
        entity_category=None,
        device_class=SensorDeviceClass.VOLUME_FLOW_RATE,
        native_unit_of_measurement=UnitOfVolumeFlowRate.CUBIC_METERS_PER_HOUR,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda coordinator: _gateway_aggregate(coordinator, "supply_air_flow").value,
    ),
    MeltemGatewaySensorDescription(
        key="gateway_extract_air_flow",
        aggregate_key="extract_air_flow",
        entity_registry_enabled_default=True,
        entity_category=None,
        device_class=SensorDeviceClass.VOLUME_FLOW_RATE,
        native_unit_of_measurement=UnitOfVolumeFlowRate.CUBIC_METERS_PER_HOUR,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda coordinator: _gateway_aggregate(coordinator, "extract_air_flow").value,
    ),
    MeltemGatewaySensorDescription(
        key="gateway_airflow_delta",
        aggregate_key="airflow_delta",
        entity_registry_enabled_default=True,
        entity_category=None,
        device_class=SensorDeviceClass.VOLUME_FLOW_RATE,
        native_unit_of_measurement=UnitOfVolumeFlowRate.CUBIC_METERS_PER_HOUR,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
        value_fn=lambda coordinator: _gateway_aggregate(coordinator, "airflow_delta").value,
    ),
    MeltemGatewaySensorDescription(
        key="gateway_response_time",
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.MILLISECONDS,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        value_fn=lambda coordinator: coordinator.client.transport_diagnostics()[
            "mean_response_time_ms"
        ],
    ),
    MeltemGatewaySensorDescription(
        key="gateway_success_rate",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        value_fn=lambda coordinator: coordinator.client.transport_diagnostics()[
            "request_success_rate"
        ],
    ),
    MeltemGatewaySensorDescription(
        key="gateway_last_answer",
        device_class=SensorDeviceClass.TIMESTAMP,
        value_fn=_gateway_last_answer,
    ),
)


SENSOR_DESCRIPTIONS: tuple[MeltemSensorDescription, ...] = (
    MeltemSensorDescription(
        key="airflow_balance",
        device_class=SensorDeviceClass.VOLUME_FLOW_RATE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfVolumeFlowRate.CUBIC_METERS_PER_HOUR,
        value_fn=lambda state: (
            state.supply_air_flow - state.extract_air_flow
            if state.supply_air_flow is not None and state.extract_air_flow is not None
            else None
        ),
    ),
    MeltemSensorDescription(
        key="exhaust_temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        suggested_display_precision=1,
        value_fn=lambda state: state.exhaust_temperature,
    ),
    MeltemSensorDescription(
        key="outdoor_air_temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        suggested_display_precision=1,
        # Only the -F and -FC variants carry this sensor, see docs/dev/reference/models.md.
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
        native_unit_of_measurement=PERCENTAGE,
        supported_profiles=HUMIDITY_PROFILES,
        value_fn=lambda state: state.humidity_extract_air,
    ),
    MeltemSensorDescription(
        key="humidity_supply_air",
        device_class=SensorDeviceClass.HUMIDITY,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=PERCENTAGE,
        supported_profiles=HUMIDITY_PROFILES,
        value_fn=lambda state: state.humidity_supply_air,
    ),
    MeltemSensorDescription(
        key="co2_extract_air",
        device_class=SensorDeviceClass.CO2,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfRatio.PARTS_PER_MILLION,
        supported_profiles=CO2_PROFILES,
        value_fn=lambda state: state.co2_extract_air,
    ),
    MeltemSensorDescription(
        key="voc_supply_air",
        device_class=SensorDeviceClass.VOLATILE_ORGANIC_COMPOUNDS_PARTS,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfRatio.PARTS_PER_MILLION,
        supported_profiles=VOC_PROFILES,
        value_fn=lambda state: state.voc_supply_air,
    ),
    MeltemSensorDescription(
        key="extract_air_flow",
        device_class=SensorDeviceClass.VOLUME_FLOW_RATE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfVolumeFlowRate.CUBIC_METERS_PER_HOUR,
        value_fn=lambda state: state.extract_air_flow,
    ),
    MeltemSensorDescription(
        key="supply_air_flow",
        device_class=SensorDeviceClass.VOLUME_FLOW_RATE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfVolumeFlowRate.CUBIC_METERS_PER_HOUR,
        value_fn=lambda state: state.supply_air_flow,
    ),
    MeltemSensorDescription(
        key="days_until_filter_change",
        device_class=SensorDeviceClass.DURATION,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfTime.DAYS,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda state: state.days_until_filter_change,
    ),
    MeltemSensorDescription(
        key="operating_hours",
        device_class=SensorDeviceClass.DURATION,
        state_class=SensorStateClass.TOTAL_INCREASING,
        native_unit_of_measurement=UnitOfTime.HOURS,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda state: state.operating_hours,
    ),
    MeltemSensorDescription(
        key="fan_motor_hours",
        device_class=SensorDeviceClass.DURATION,
        state_class=SensorStateClass.TOTAL_INCREASING,
        native_unit_of_measurement=UnitOfTime.HOURS,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda state: state.fan_motor_hours,
    ),
    MeltemSensorDescription(
        key="rf_last_seen_age_seconds",
        device_class=SensorDeviceClass.DURATION,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfTime.SECONDS,
        suggested_display_precision=0,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda state: state.rf_last_seen_age_seconds,
    ),
    MeltemSensorDescription(
        key="rf_signal_strength",
        device_class=SensorDeviceClass.SIGNAL_STRENGTH,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement="dBm",
        suggested_display_precision=0,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda state: state.rf_signal_strength,
    ),
)


GATEWAY_UPTIME_DESCRIPTION = SensorEntityDescription(
    key="gateway_uptime",
    device_class=SensorDeviceClass.DURATION,
    native_unit_of_measurement=UnitOfTime.SECONDS,
    state_class=SensorStateClass.MEASUREMENT,
    suggested_display_precision=0,
    entity_category=EntityCategory.DIAGNOSTIC,
    entity_registry_enabled_default=False,
)


GATEWAY_UTC_CLOCK_DESCRIPTION = SensorEntityDescription(
    key="gateway_utc_clock",
    device_class=SensorDeviceClass.TIMESTAMP,
    entity_category=EntityCategory.DIAGNOSTIC,
    entity_registry_enabled_default=False,
)


GATEWAY_CONNECTED_DEVICES_DESCRIPTION = SensorEntityDescription(
    key="gateway_connected_devices",
    state_class=SensorStateClass.MEASUREMENT,
    suggested_display_precision=0,
    entity_category=EntityCategory.DIAGNOSTIC,
)


ROOM_DIAGNOSTIC_DESCRIPTIONS: tuple[SensorEntityDescription, ...] = (
    SensorEntityDescription(
        key="last_successful_update",
        device_class=SensorDeviceClass.TIMESTAMP,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
    ),
)


# The coordinator serializes all gateway access itself.
PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: MeltemIntegrationConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up Meltem sensor entities."""

    runtime_data: MeltemRuntimeData = entry.runtime_data
    coordinator = runtime_data.coordinator

    entities: list[SensorEntity] = [MeltemModbusDevicePathSensor(entry)]
    entities.append(MeltemGatewayUptimeSensor(coordinator, entry.entry_id))
    entities.append(MeltemGatewayUtcClockSensor(coordinator, entry.entry_id))
    entities.append(MeltemGatewayConnectedDevicesSensor(coordinator, entry.entry_id))
    entities.extend(
        MeltemGatewayDiagnosticSensor(coordinator, entry.entry_id, description)
        for description in GATEWAY_SENSOR_DESCRIPTIONS
    )
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
    entities.extend(
        MeltemRoomDiagnosticSensor(coordinator, room, description)
        for room in coordinator.rooms
        for description in ROOM_DIAGNOSTIC_DESCRIPTIONS
        if room.supports(description.key)
    )
    async_add_entities(entities)


class MeltemSensorEntity(MeltemEntity, SensorEntity):
    """Representation of one Meltem sensor."""

    entity_description: MeltemSensorDescription

    def __init__(
        self,
        coordinator: MeltemDataUpdateCoordinator,
        room: RoomConfig,
        description: MeltemSensorDescription,
    ) -> None:
        super().__init__(coordinator, room, description.key, description.key)
        self.entity_description = description

    @property
    def native_value(self) -> int | float | None:
        return self.entity_description.value_fn(self.room_state)


class MeltemGatewayUptimeSensor(MeltemGatewayEntity, SensorEntity):
    """Enable the optional uptime read only while this entity is active."""

    entity_description = GATEWAY_UPTIME_DESCRIPTION

    def __init__(self, coordinator: MeltemDataUpdateCoordinator, entry_id: str) -> None:
        super().__init__(coordinator, entry_id, self.entity_description.key)

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.coordinator.set_gateway_uptime_enabled(True)
        self.async_on_remove(lambda: self.coordinator.set_gateway_uptime_enabled(False))

    @property
    def available(self) -> bool:
        return self.coordinator.gateway_uptime is not None

    @property
    def native_value(self) -> int | None:
        return self.coordinator.gateway_uptime


class MeltemGatewayUtcClockSensor(MeltemGatewayEntity, SensorEntity):
    """Expose the gateway's optional UTC clock as a Home Assistant timestamp."""

    entity_description = GATEWAY_UTC_CLOCK_DESCRIPTION

    def __init__(self, coordinator: MeltemDataUpdateCoordinator, entry_id: str) -> None:
        super().__init__(coordinator, entry_id, self.entity_description.key)

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.coordinator.set_gateway_utc_clock_enabled(True)
        self.async_on_remove(lambda: self.coordinator.set_gateway_utc_clock_enabled(False))

    @property
    def available(self) -> bool:
        return self.coordinator.gateway_utc_clock is not None

    @property
    def native_value(self) -> datetime | None:
        return self.coordinator.gateway_utc_clock


class MeltemGatewayConnectedDevicesSensor(MeltemGatewayEntity, SensorEntity):
    """Show the gateway's registered device count, not HA room availability."""

    entity_description = GATEWAY_CONNECTED_DEVICES_DESCRIPTION

    def __init__(self, coordinator: MeltemDataUpdateCoordinator, entry_id: str) -> None:
        super().__init__(coordinator, entry_id, self.entity_description.key)

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.coordinator.set_gateway_connected_devices_enabled(True)
        self.async_on_remove(lambda: self.coordinator.set_gateway_connected_devices_enabled(False))

    @property
    def available(self) -> bool:
        return self.coordinator.gateway_connected_devices is not None

    @property
    def native_value(self) -> int | None:
        return self.coordinator.gateway_connected_devices


class MeltemRoomDiagnosticSensor(MeltemRoomDiagnosticEntity, SensorEntity):
    """Expose the last successful room read through outages."""

    entity_description: SensorEntityDescription

    def __init__(
        self,
        coordinator: MeltemDataUpdateCoordinator,
        room: RoomConfig,
        description: SensorEntityDescription,
    ) -> None:
        super().__init__(coordinator, room, description.key)
        self.entity_description = description

    @property
    def native_value(self) -> datetime | None:
        return self.coordinator.last_successful_room_update(self.room.key)


class MeltemGatewayDiagnosticSensor(MeltemGatewayEntity, SensorEntity):
    """Expose local diagnostics and optionally polled RF statistics."""

    entity_description: MeltemGatewaySensorDescription
    _unrecorded_attributes = frozenset({"unknown_units"})

    def __init__(
        self,
        coordinator: MeltemDataUpdateCoordinator,
        entry_id: str,
        description: MeltemGatewaySensorDescription,
    ) -> None:
        super().__init__(coordinator, entry_id, description.key)
        self.entity_description = description

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        if self.entity_description.rf_statistics_key is not None:
            key = self.entity_description.key
            self.coordinator.set_gateway_rf_statistics_enabled(key, True)
            self.async_on_remove(
                lambda: self.coordinator.set_gateway_rf_statistics_enabled(key, False)
            )

    @property
    def available(self) -> bool:
        if self.entity_description.rf_statistics_key is not None:
            return self.native_value is not None
        return super().available

    @property
    def native_value(self) -> int | float | datetime | None:
        return self.entity_description.value_fn(self.coordinator)

    @property
    def extra_state_attributes(self) -> Mapping[str, object] | None:
        if self.entity_description.aggregate_key is not None:
            return _gateway_aggregate(
                self.coordinator, self.entity_description.aggregate_key
            ).attributes
        if self.entity_description.key in {"gateway_response_time", "gateway_success_rate"}:
            diagnostics = self.coordinator.client.transport_diagnostics()
            return {
                "sample_count": diagnostics["request_sample_count"],
                "window_seconds": diagnostics["request_window_seconds"],
            }
        return None


class MeltemModbusSlaveSensor(MeltemEntity, SensorEntity):
    """Representation of one room's configured Modbus slave ID."""

    entity_description = MODBUS_SLAVE_ID_DESCRIPTION

    def __init__(self, coordinator: MeltemDataUpdateCoordinator, room: RoomConfig) -> None:
        key = MODBUS_SLAVE_ID_DESCRIPTION.key
        super().__init__(coordinator, room, key, key)
        self._attr_native_value = room.slave


class MeltemModbusDevicePathSensor(SensorEntity):
    """Representation of the serial device path for one config entry."""

    entity_description = MODBUS_DEVICE_PATH_DESCRIPTION
    _attr_has_entity_name = True

    def __init__(self, entry: MeltemIntegrationConfigEntry) -> None:
        self._attr_unique_id = f"{DOMAIN}_{entry.entry_id}_modbus_device_path"
        self._attr_device_info = gateway_device_info(entry.entry_id)
        self._attr_native_value = entry.data[CONF_PORT]
