"""Device objects that group the Meltem components per Modbus unit."""

from __future__ import annotations

from math import isfinite

from modbus_connection import ModbusUnit
from modbus_connection.model import Component, Device, ManualComponent, raw_register

from ..const import (
    REGISTER_CO2_EXTRACT_AIR,
    REGISTER_CURRENT_LEVEL,
    REGISTER_DAYS_UNTIL_FILTER_CHANGE,
    REGISTER_GATEWAY_NODE_ADDRESS_1,
    REGISTER_GATEWAY_NUMBER_OF_NODES,
    REGISTER_HUMIDITY_EXTRACT_AIR,
    REGISTER_HUMIDITY_SUPPLY_AIR,
    REGISTER_RF_COMM_STATUS,
    REGISTER_RF_FAULT_STATUS,
    REGISTER_RF_LAST_SEEN_AGE,
    REGISTER_RF_SIGNAL_STRENGTH,
    REGISTER_SOFTWARE_VERSION,
    REGISTER_VALUE_ERROR_STATUS,
    REGISTER_VOC_SUPPLY_AIR,
)
from ..models import GatewayRfStatistics
from .components import (
    Airflow,
    AppSetupCode,
    Command,
    ControlSettings,
    ExtractAirQuality,
    FanMotorHours,
    GatewayMac,
    GatewayUptime,
    GatewayUtcClock,
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
from .components import GatewayRfStatistics as GatewayRfStatisticsComponent


def _only[C: Component](component: C, *fields: str) -> C:
    component.restrict_fields(fields)
    return component


def _format_app_setup_code(low_word: int | None, high_word: int | None) -> str | None:
    if low_word is None or high_word is None or high_word > 0xFF:
        return None
    code = (high_word << 16) | low_word
    if code == 0:
        return None
    return f"R{code:06X}"


class MeltemRoomDevice(Device):
    """One ventilation unit behind the gateway."""

    def __init__(
        self,
        unit: ModbusUnit,
        *,
        exhaust_temperature_only: bool,
        has_co2: bool = True,
        has_voc: bool = True,
    ) -> None:
        super().__init__(unit)
        self.airflow = Airflow(unit)
        self.temperatures = Temperatures(unit)
        if exhaust_temperature_only:
            # Plain units only expose the exhaust temperature (unit matrix).
            _only(self.temperatures, "exhaust_temperature")
        self.supply_temperature = SupplyTemperature(unit)
        self.extract_air_quality = ExtractAirQuality(unit)
        if not has_co2:
            _only(self.extract_air_quality, "humidity_extract_air")
        self.supply_air_quality = SupplyAirQuality(unit)
        if not has_voc:
            _only(self.supply_air_quality, "humidity_supply_air")
        self.status = Status(unit)
        self.days_until_filter_change = Register(
            unit, base_offset=REGISTER_DAYS_UNTIL_FILTER_CHANGE
        )
        self.operating_hours = OperatingHours(unit)
        self.fan_motor_hours = FanMotorHours(unit)
        self.software_version = Register(unit, base_offset=REGISTER_SOFTWARE_VERSION)
        self.app_setup_code = AppSetupCode(unit)
        self.control_settings = ControlSettings(unit)
        if not has_co2:
            _only(
                self.control_settings,
                "humidity_starting_point",
                "humidity_min_level",
                "humidity_max_level",
            )
        self.rf_comm_status = Register(unit, base_offset=REGISTER_RF_COMM_STATUS)
        self.rf_fault_status = Register(unit, base_offset=REGISTER_RF_FAULT_STATUS)
        self.value_error_status = Register(unit, base_offset=REGISTER_VALUE_ERROR_STATUS)
        self.rf_last_seen_age = Register(unit, base_offset=REGISTER_RF_LAST_SEEN_AGE)
        self.rf_signal_strength = Register(unit, base_offset=REGISTER_RF_SIGNAL_STRENGTH)
        self.mode = ModeBlock(unit)
        # Single-register read of the set-side balanced target, only used to
        # confirm a recent airflow write; the five-register block stays unread.
        self.current_level_readback = Register(unit, base_offset=REGISTER_CURRENT_LEVEL)
        self.mode_status = ModeStatus(unit)
        self.command = Command(unit)

    async def async_read_app_setup_code(self) -> str | None:
        """Read the six-digit DataMatrix code from the two identity registers."""

        await self.app_setup_code.async_update()
        return _format_app_setup_code(
            self.app_setup_code.low_word,
            self.app_setup_code.high_word,
        )


class MeltemProbe(Device):
    """The minimal register set read while setting a unit up."""

    def __init__(self, unit: ModbusUnit) -> None:
        super().__init__(unit)
        self.product_id = ProductId(unit)
        self.humidity_extract_air = Register(unit, base_offset=REGISTER_HUMIDITY_EXTRACT_AIR)
        self.humidity_supply_air = Register(unit, base_offset=REGISTER_HUMIDITY_SUPPLY_AIR)
        self.co2_extract_air = Register(unit, base_offset=REGISTER_CO2_EXTRACT_AIR)
        self.voc_supply_air = Register(unit, base_offset=REGISTER_VOC_SUPPLY_AIR)
        self.app_setup_code = AppSetupCode(unit)

    async def async_read_app_setup_code(self) -> str | None:
        """Read the six-digit DataMatrix code during setup discovery."""

        await self.app_setup_code.async_update()
        return _format_app_setup_code(
            self.app_setup_code.low_word,
            self.app_setup_code.high_word,
        )


class MeltemGateway(Device):
    """The bridge registers the gateway serves on its own unit ID."""

    def __init__(self, unit: ModbusUnit) -> None:
        super().__init__(unit)
        self.node_count = Register(unit, base_offset=REGISTER_GATEWAY_NUMBER_OF_NODES)
        self.mac = GatewayMac(unit)
        self.uptime = GatewayUptime(unit)
        self.utc_clock = GatewayUtcClock(unit)
        self.rf_statistics = GatewayRfStatisticsComponent(unit)

    async def async_read_rf_statistics(self) -> GatewayRfStatistics:
        """Read RF statistics without rescaling the reported percentages."""

        await self.rf_statistics.async_update()
        current = self.rf_statistics.messages_current_hour
        previous = self.rf_statistics.messages_previous_hour
        if current is None or previous is None:
            raise ValueError("The gateway RF counters returned no value")

        def valid_load(value: float | None) -> float | None:
            return value if value is not None and isfinite(value) and 0 <= value <= 100 else None

        return GatewayRfStatistics(
            messages_current_hour=int(current),
            messages_previous_hour=int(previous),
            load_current_hour=valid_load(self.rf_statistics.load_current_hour),
            load_previous_hour=valid_load(self.rf_statistics.load_previous_hour),
        )

    async def async_read_mac(self) -> str:
        """Read and decode the gateway's six-byte Ethernet MAC address."""

        await self.mac.async_update()
        word_1 = self.mac.word_1
        word_2 = self.mac.word_2
        word_3 = self.mac.word_3
        if word_1 is None or word_2 is None or word_3 is None:
            raise ValueError("The gateway MAC registers returned no value")
        octets = (
            word_1 >> 8,
            word_1 & 0xFF,
            word_2 >> 8,
            word_2 & 0xFF,
            word_3 >> 8,
            word_3 & 0xFF,
        )
        if not any(octets) or all(octet == 0xFF for octet in octets) or octets[0] & 1:
            raise ValueError("The gateway returned an invalid Ethernet MAC address")
        return ":".join(f"{octet:02x}" for octet in octets)

    async def async_read_uptime(self) -> int:
        """Read the 32-bit gateway uptime counter in seconds."""

        await self.uptime.async_update()
        if self.uptime.uptime is None:
            raise ValueError("The gateway uptime register returned no value")
        return int(self.uptime.uptime)

    async def async_read_node_count(self) -> int:
        """Read how many units the gateway is configured for."""

        await self.node_count.async_update()
        return int(self.node_count.value or 0)

    async def async_read_utc_clock(self) -> int:
        """Read the gateway's UTC clock as Unix epoch seconds."""

        await self.utc_clock.async_update()
        value = self.utc_clock.value
        if value is None or value == 0xFFFFFFFF:
            raise ValueError("The gateway UTC clock returned no valid time")
        return int(value)

    async def async_read_node_addresses(self, count: int) -> list[int]:
        """Read the configured unit addresses in one block."""

        first = REGISTER_GATEWAY_NODE_ADDRESS_1
        block = ManualComponent(self.modbus_unit, holding_ranges=((first, first + count - 1),))
        keys = [str(index) for index in range(count)]
        for index, key in enumerate(keys):
            block.add(key, raw_register(first + index))
        values = await block.async_update()
        return [int(values[key]) for key in keys]
