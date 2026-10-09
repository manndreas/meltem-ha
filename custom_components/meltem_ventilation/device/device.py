"""Device objects that group the Meltem components per Modbus unit."""

from __future__ import annotations

from modbus_connection import ModbusUnit
from modbus_connection.model import Component, Device, ManualComponent, raw_register

from ..const import (
    REGISTER_CO2_EXTRACT_AIR,
    REGISTER_DAYS_UNTIL_FILTER_CHANGE,
    REGISTER_GATEWAY_NODE_ADDRESS_1,
    REGISTER_GATEWAY_NUMBER_OF_NODES,
    REGISTER_HUMIDITY_EXTRACT_AIR,
    REGISTER_HUMIDITY_SUPPLY_AIR,
    REGISTER_RF_COMM_STATUS,
    REGISTER_SOFTWARE_VERSION,
    REGISTER_VOC_SUPPLY_AIR,
)
from .components import (
    Airflow,
    Command,
    ControlSettings,
    ExtractAirQuality,
    ModeBlock,
    ModeStatus,
    OperatingHours,
    ProductId,
    Register,
    Status,
    SupplyAirQuality,
    SupplyTemperature,
    Temperatures,
)


def _only[C: Component](component: C, *fields: str) -> C:
    component.restrict_fields(fields)
    return component


class MeltemRoomDevice(Device):
    """One ventilation unit behind the gateway."""

    def __init__(self, unit: ModbusUnit, *, exhaust_temperature_only: bool) -> None:
        super().__init__(unit)
        self.airflow = Airflow(unit)
        self.temperatures = Temperatures(unit)
        if exhaust_temperature_only:
            # Plain units only expose the exhaust temperature (unit matrix).
            _only(self.temperatures, "exhaust_temperature")
        self.supply_temperature = SupplyTemperature(unit)
        self.extract_air_quality = ExtractAirQuality(unit)
        self.supply_air_quality = SupplyAirQuality(unit)
        self.status = Status(unit)
        self.days_until_filter_change = Register(
            unit, base_offset=REGISTER_DAYS_UNTIL_FILTER_CHANGE
        )
        self.operating_hours = OperatingHours(unit)
        self.software_version = Register(unit, base_offset=REGISTER_SOFTWARE_VERSION)
        self.control_settings = ControlSettings(unit)
        self.rf_comm_status = Register(unit, base_offset=REGISTER_RF_COMM_STATUS)
        self.mode = ModeBlock(unit)
        self.mode_status = ModeStatus(unit)
        self.command = Command(unit)


class MeltemProbe(Device):
    """The minimal register set read while setting a unit up."""

    def __init__(self, unit: ModbusUnit) -> None:
        super().__init__(unit)
        self.product_id = ProductId(unit)
        self.humidity_extract_air = Register(unit, base_offset=REGISTER_HUMIDITY_EXTRACT_AIR)
        self.humidity_supply_air = Register(unit, base_offset=REGISTER_HUMIDITY_SUPPLY_AIR)
        self.co2_extract_air = Register(unit, base_offset=REGISTER_CO2_EXTRACT_AIR)
        self.voc_supply_air = Register(unit, base_offset=REGISTER_VOC_SUPPLY_AIR)


class MeltemGateway(Device):
    """The bridge registers the gateway serves on its own unit ID."""

    def __init__(self, unit: ModbusUnit) -> None:
        super().__init__(unit)
        self.node_count = Register(unit, base_offset=REGISTER_GATEWAY_NUMBER_OF_NODES)

    async def async_read_node_count(self) -> int:
        """Read how many units the gateway is configured for."""

        await self.node_count.async_update()
        return int(self.node_count.value or 0)

    async def async_read_node_addresses(self, count: int) -> list[int]:
        """Read the configured unit addresses in one block."""

        first = REGISTER_GATEWAY_NODE_ADDRESS_1
        block = ManualComponent(self.modbus_unit, holding_ranges=((first, first + count - 1),))
        keys = [str(index) for index in range(count)]
        for index, key in enumerate(keys):
            block.add(key, raw_register(first + index))
        values = await block.async_update()
        return [int(values[key]) for key in keys]
