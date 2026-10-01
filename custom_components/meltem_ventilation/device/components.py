"""Meltem register blocks modelled as ``modbus-connection`` components.

Every component pins its ``register_ranges`` to exactly the block the
integration has always read, so the gateway sees the same requests as before.
"""

from __future__ import annotations

from modbus_connection.model import Component, float32, raw_register, uint32

from ..const import (
    REGISTER_APPLY,
    REGISTER_CO2_EXTRACT_AIR,
    REGISTER_CO2_MAX_LEVEL,
    REGISTER_CO2_MIN_LEVEL,
    REGISTER_CO2_STARTING_POINT,
    REGISTER_CURRENT_LEVEL,
    REGISTER_ERROR_STATUS,
    REGISTER_EXHAUST_AIR_TEMPERATURE,
    REGISTER_EXTRACT_AIR_FLOW,
    REGISTER_EXTRACT_AIR_TARGET_LEVEL,
    REGISTER_EXTRACT_AIR_TEMPERATURE,
    REGISTER_FILTER_CHANGE_DUE,
    REGISTER_FROST_PROTECTION_ACTIVE,
    REGISTER_HUMIDITY_EXTRACT_AIR,
    REGISTER_HUMIDITY_MAX_LEVEL,
    REGISTER_HUMIDITY_MIN_LEVEL,
    REGISTER_HUMIDITY_STARTING_POINT,
    REGISTER_HUMIDITY_SUPPLY_AIR,
    REGISTER_MODE,
    REGISTER_OPERATING_HOURS,
    REGISTER_OUTDOOR_AIR_TEMPERATURE,
    REGISTER_PRESET_MODE,
    REGISTER_PRESET_VALUE,
    REGISTER_PRODUCT_ID,
    REGISTER_SUPPLY_AIR_FLOW,
    REGISTER_SUPPLY_AIR_TEMPERATURE,
    REGISTER_VOC_SUPPLY_AIR,
)


class Register(Component):
    """One unsigned 16-bit register, placed with ``base_offset``."""

    register_ranges = ((0, 0),)

    value = raw_register(0)


class Airflow(Component):
    """Measured extract and supply airflow."""

    register_ranges = ((REGISTER_EXTRACT_AIR_FLOW, REGISTER_SUPPLY_AIR_FLOW),)

    extract_air_flow = raw_register(REGISTER_EXTRACT_AIR_FLOW)
    supply_air_flow = raw_register(REGISTER_SUPPLY_AIR_FLOW)


class ModeBlock(Component):
    """Mode, airflow targets, and the intensive shadow registers."""

    register_ranges = ((REGISTER_MODE, REGISTER_PRESET_VALUE),)

    mode = raw_register(REGISTER_MODE, writable=True)
    current_level = raw_register(REGISTER_CURRENT_LEVEL, writable=True)
    extract_target_level = raw_register(REGISTER_EXTRACT_AIR_TARGET_LEVEL, writable=True)
    preset_mode = raw_register(REGISTER_PRESET_MODE, writable=True)
    preset_value = raw_register(REGISTER_PRESET_VALUE, writable=True)


class Command(Component):
    """The APPLY latch that activates written control registers; never polled."""

    register_ranges = ((REGISTER_APPLY, REGISTER_APPLY),)

    apply = raw_register(REGISTER_APPLY, writable=True)


class Status(Component):
    """Error, filter, and frost flags."""

    register_ranges = ((REGISTER_ERROR_STATUS, REGISTER_FROST_PROTECTION_ACTIVE),)

    error_status = raw_register(REGISTER_ERROR_STATUS)
    filter_change_due = raw_register(REGISTER_FILTER_CHANGE_DUE)
    frost_protection_active = raw_register(REGISTER_FROST_PROTECTION_ACTIVE)


class Temperatures(Component):
    """Extract, outdoor, and exhaust air temperatures."""

    register_ranges = (
        (REGISTER_EXTRACT_AIR_TEMPERATURE, REGISTER_EXHAUST_AIR_TEMPERATURE + 1),
    )

    extract_air_temperature = float32(REGISTER_EXTRACT_AIR_TEMPERATURE, word_order="little")
    outdoor_air_temperature = float32(REGISTER_OUTDOOR_AIR_TEMPERATURE, word_order="little")
    exhaust_temperature = float32(REGISTER_EXHAUST_AIR_TEMPERATURE, word_order="little")


class SupplyTemperature(Component):
    """Supply air temperature."""

    register_ranges = (
        (REGISTER_SUPPLY_AIR_TEMPERATURE, REGISTER_SUPPLY_AIR_TEMPERATURE + 1),
    )

    supply_air_temperature = float32(REGISTER_SUPPLY_AIR_TEMPERATURE, word_order="little")


class ExtractAirQuality(Component):
    """Extract air humidity and CO2."""

    register_ranges = ((REGISTER_HUMIDITY_EXTRACT_AIR, REGISTER_CO2_EXTRACT_AIR),)

    humidity_extract_air = raw_register(REGISTER_HUMIDITY_EXTRACT_AIR)
    co2_extract_air = raw_register(REGISTER_CO2_EXTRACT_AIR)


class SupplyAirQuality(Component):
    """Supply air humidity and VOC."""

    register_ranges = ((REGISTER_HUMIDITY_SUPPLY_AIR, REGISTER_VOC_SUPPLY_AIR),)

    humidity_supply_air = raw_register(REGISTER_HUMIDITY_SUPPLY_AIR)
    voc_supply_air = raw_register(REGISTER_VOC_SUPPLY_AIR)


class OperatingHours(Component):
    """Operating hours counter."""

    register_ranges = ((REGISTER_OPERATING_HOURS, REGISTER_OPERATING_HOURS + 1),)

    operating_hours = uint32(REGISTER_OPERATING_HOURS, word_order="little")


class ControlSettings(Component):
    """Humidity and CO2 control settings; field names match the setting keys."""

    register_ranges = ((REGISTER_HUMIDITY_STARTING_POINT, REGISTER_CO2_MAX_LEVEL),)

    humidity_starting_point = raw_register(REGISTER_HUMIDITY_STARTING_POINT, writable=True)
    humidity_min_level = raw_register(REGISTER_HUMIDITY_MIN_LEVEL, writable=True)
    humidity_max_level = raw_register(REGISTER_HUMIDITY_MAX_LEVEL, writable=True)
    co2_starting_point = raw_register(REGISTER_CO2_STARTING_POINT, writable=True)
    co2_min_level = raw_register(REGISTER_CO2_MIN_LEVEL, writable=True)
    co2_max_level = raw_register(REGISTER_CO2_MAX_LEVEL, writable=True)


class ProductId(Component):
    """Product identifier, read once while setting a unit up."""

    register_ranges = ((REGISTER_PRODUCT_ID, REGISTER_PRODUCT_ID + 1),)

    product_id = uint32(REGISTER_PRODUCT_ID, word_order="little")
