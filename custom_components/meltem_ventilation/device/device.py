"""Device objects that group the Meltem components per Modbus unit."""

from __future__ import annotations

from modbus_connection import ModbusUnit
from modbus_connection.model import Device, ManualComponent, raw_register

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
    OperatingHours,
    ProductId,
    Register,
    Status,
    SupplyAirQuality,
    SupplyTemperature,
    Temperatures,
)


class MeltemRoomDevice(Device):
    """One ventilation unit behind the gateway."""

    def __init__(self, unit: ModbusUnit, *, exhaust_temperature_only: bool) -> None:
        super().__init__(unit)
        self.airflow = Airflow(unit)
        self.temperatures = Temperatures(unit)
        if exhaust_temperature_only:
            # Plain units only expose the exhaust temperature (unit matrix).
            self.temperatures.restrict_fields(["exhaust_temperature"])
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
        # Many units reject the long mode read until a first write (HW-4).
        self.mode_short = ModeBlock(unit)
        self.mode_short.restrict_fields(["mode", "current_level"])
        self.current_level = ModeBlock(unit)
        self.current_level.restrict_fields(["current_level"])
        self.extract_target_level = ModeBlock(unit)
        self.extract_target_level.restrict_fields(["extract_target_level"])
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

    async def async_read_node_addresses(self, count: int) -> list[int]:
        """Read the configured unit addresses in one block."""

        first = REGISTER_GATEWAY_NODE_ADDRESS_1
        block = ManualComponent(self.modbus_unit, holding_ranges=((first, first + count - 1),))
        for index in range(count):
            block.add(str(index), raw_register(first + index))
        values = await block.async_update()
        return [int(values[str(index)]) for index in range(count)]
