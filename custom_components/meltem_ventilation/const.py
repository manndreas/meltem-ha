"""Constants for the Meltem integration.

This file keeps protocol details in one place:
- config-entry keys
- scheduler defaults
- supported profile metadata
- Modbus register addresses
"""

from __future__ import annotations

from dataclasses import dataclass

from homeassistant.const import Platform

DOMAIN = "meltem_ventilation"
INTEGRATION_NAME = "Meltem Modbus"
GATEWAY_NAME = "Meltem Gateway M-WRG-GW"

PLATFORMS: list[Platform] = [
    Platform.SENSOR,
    Platform.BINARY_SENSOR,
    Platform.FAN,
    Platform.NUMBER,
    Platform.SELECT,
    Platform.SWITCH,
]

CONF_MAX_REQUESTS_PER_SECOND = "max_requests_per_second"
CONF_PORT = "port"
CONF_ROOMS = "rooms"

DEFAULT_PORT = "/dev/ttyACM0"
DEFAULT_GATEWAY_DEVICE_ID = 1
DEFAULT_SCAN_SLAVE_START = 2
DEFAULT_SCAN_SLAVE_END = 16
DEFAULT_MAX_REQUESTS_PER_SECOND = 4.0
MIN_MAX_REQUESTS_PER_SECOND = 0.5
MAX_MAX_REQUESTS_PER_SECOND = 10.0

FIXED_BAUDRATE = 19200
FIXED_BYTESIZE = 8
FIXED_PARITY = "E"
FIXED_STOPBITS = 1
FIXED_TIMEOUT = 0.8
SCAN_TIMEOUT = 0.8
SETUP_PROBE_TIMEOUT = 0.8
REQUEST_GAP_SECONDS = 0.1
FLOW_REFRESH_SECONDS = 10
STATUS_REFRESH_SECONDS = 60
TEMPERATURE_REFRESH_SECONDS = 60
OPERATING_HOURS_REFRESH_SECONDS = 3600
CONTROL_SETTINGS_REFRESH_SECONDS = 3600
FILTER_REFRESH_SECONDS = 3600

@dataclass(frozen=True, slots=True)
class ProfileMetadata:
    """User-facing and protocol-relevant facts about one supported unit family."""

    label: str
    series: str
    max_airflow: int
    capabilities: frozenset[str]


PROFILE_METADATA: dict[str, ProfileMetadata] = {
    "s_plain": ProfileMetadata("M-WRG-S", "s", 97, frozenset()),
    "s_f": ProfileMetadata("M-WRG-S (-F)", "s", 97, frozenset({"humidity"})),
    "s_fc": ProfileMetadata("M-WRG-S (-FC)", "s", 97, frozenset({"humidity", "co2"})),
    "ii_plain": ProfileMetadata("M-WRG-II", "ii", 100, frozenset()),
    "ii_f": ProfileMetadata("M-WRG-II (-F)", "ii", 100, frozenset({"humidity"})),
    "ii_fc": ProfileMetadata(
        "M-WRG-II (-FC)", "ii", 100, frozenset({"humidity", "co2"})
    ),
    "ii_fc_voc": ProfileMetadata(
        "M-WRG-II (O/VOC-AUL)", "ii", 100, frozenset({"humidity", "co2", "voc"})
    ),
}


def _profiles_with(capability: str) -> frozenset[str]:
    return frozenset(
        key
        for key, metadata in PROFILE_METADATA.items()
        if capability in metadata.capabilities
    )


MODEL_PROFILE_LABELS: dict[str, str] = {
    key: metadata.label for key, metadata in PROFILE_METADATA.items()
}
MODEL_PROFILES: tuple[str, ...] = tuple(MODEL_PROFILE_LABELS)
ALL_PROFILES: frozenset[str] = frozenset(MODEL_PROFILES)
HUMIDITY_PROFILES: frozenset[str] = _profiles_with("humidity")
CO2_PROFILES: frozenset[str] = _profiles_with("co2")
VOC_PROFILES: frozenset[str] = _profiles_with("voc")
PLAIN_PROFILES: frozenset[str] = frozenset(
    key for key, metadata in PROFILE_METADATA.items() if not metadata.capabilities
)

PRESET_MODE_LOW = "low"
PRESET_MODE_MEDIUM = "medium"
PRESET_MODE_HIGH = "high"
PRESET_MODE_INTENSIVE = "intensive"
PRESET_MODE_INACTIVE = "inactive"
# Still decoded from the device, but no longer selectable: the separate supply
# and extract fans express these states directly.
PRESET_MODE_EXTRACT_ONLY = "extract_only"
PRESET_MODE_SUPPLY_ONLY = "supply_only"
PRESET_MODE_OPTIONS: tuple[str, ...] = (
    PRESET_MODE_INACTIVE,
    PRESET_MODE_LOW,
    PRESET_MODE_MEDIUM,
    PRESET_MODE_HIGH,
)

PRESET_MODE_CODE_LOW = 228
PRESET_MODE_CODE_MEDIUM = 229
PRESET_MODE_CODE_HIGH = 230
PRESET_MODE_CODE_INTENSIVE = 227
PRESET_MODE_TO_RAW_CODE: dict[str, int] = {
    PRESET_MODE_LOW: PRESET_MODE_CODE_LOW,
    PRESET_MODE_MEDIUM: PRESET_MODE_CODE_MEDIUM,
    PRESET_MODE_HIGH: PRESET_MODE_CODE_HIGH,
    PRESET_MODE_INTENSIVE: PRESET_MODE_CODE_INTENSIVE,
}
RAW_CODE_TO_PRESET_MODE: dict[int, str] = {
    code: preset_mode for preset_mode, code in PRESET_MODE_TO_RAW_CODE.items()
}
APP_UNBALANCED_PRESET_BASE = 200

OPERATION_MODE_INACTIVE = "inactive"
OPERATION_MODE_OFF = "off"
OPERATION_MODE_MANUAL = "manual"
OPERATION_MODE_UNBALANCED = "unbalanced"
# Modes in which the user sets the airflow directly.
DIRECT_OPERATION_MODES: tuple[str, ...] = (
    OPERATION_MODE_OFF,
    OPERATION_MODE_MANUAL,
    OPERATION_MODE_UNBALANCED,
)
SENSOR_OPERATION_MODES: tuple[str, ...] = (
    "humidity_control",
    "co2_control",
    "automatic",
)

WRITE_SETTLE_SECONDS = 1.5
TARGET_OPTIMISTIC_SECONDS = 15.0
POST_WRITE_REFRESH_RETRIES = 2
POST_WRITE_REFRESH_INTERVAL_SECONDS = 2.5

# On the tested M-WRG-GW gateway, 41000 and 41004 are effectively swapped
# compared to the unit manual. We map the logical sensor names to the values
# actually observed on the gateway.
REGISTER_EXHAUST_AIR_TEMPERATURE = 41004
REGISTER_OUTDOOR_AIR_TEMPERATURE = 41002
REGISTER_EXTRACT_AIR_TEMPERATURE = 41000
REGISTER_SUPPLY_AIR_TEMPERATURE = 41009
REGISTER_ERROR_STATUS = 41016
REGISTER_FILTER_CHANGE_DUE = 41017
REGISTER_FROST_PROTECTION_ACTIVE = 41018
REGISTER_HUMIDITY_EXTRACT_AIR = 41006
REGISTER_CO2_EXTRACT_AIR = 41007
REGISTER_HUMIDITY_SUPPLY_AIR = 41011
REGISTER_VOC_SUPPLY_AIR = 41013
REGISTER_EXTRACT_AIR_FLOW = 41020
REGISTER_SUPPLY_AIR_FLOW = 41021
REGISTER_DAYS_UNTIL_FILTER_CHANGE = 41027
REGISTER_OPERATING_HOURS = 41030
REGISTER_GATEWAY_NUMBER_OF_NODES = 43901
REGISTER_GATEWAY_NODE_ADDRESS_1 = 43902
REGISTER_CURRENT_LEVEL = 41121
REGISTER_MODE = 41120
REGISTER_EXTRACT_AIR_TARGET_LEVEL = 41122
REGISTER_PRESET_MODE = 41123
REGISTER_PRESET_VALUE = 41124
REGISTER_APPLY = 41132
REGISTER_SOFTWARE_VERSION = 40004
REGISTER_PRODUCT_ID = 40002
REGISTER_RF_COMM_STATUS = 40101
REGISTER_HUMIDITY_STARTING_POINT = 42000
REGISTER_HUMIDITY_MIN_LEVEL = 42001
REGISTER_HUMIDITY_MAX_LEVEL = 42002
REGISTER_CO2_STARTING_POINT = 42003
REGISTER_CO2_MIN_LEVEL = 42004
REGISTER_CO2_MAX_LEVEL = 42005

# Contiguous 42000..42005 block, so the order also defines the block layout.
CONTROL_SETTING_REGISTERS: dict[str, int] = {
    "humidity_starting_point": REGISTER_HUMIDITY_STARTING_POINT,
    "humidity_min_level": REGISTER_HUMIDITY_MIN_LEVEL,
    "humidity_max_level": REGISTER_HUMIDITY_MAX_LEVEL,
    "co2_starting_point": REGISTER_CO2_STARTING_POINT,
    "co2_min_level": REGISTER_CO2_MIN_LEVEL,
    "co2_max_level": REGISTER_CO2_MAX_LEVEL,
}

# Minimum, maximum and step from the manufacturer register table.
CONTROL_SETTING_LIMITS: dict[str, tuple[int, int, int]] = {
    "humidity_starting_point": (40, 80, 1),
    "humidity_min_level": (0, 100, 10),
    "humidity_max_level": (10, 100, 10),
    "co2_starting_point": (500, 1200, 1),
    "co2_min_level": (0, 100, 10),
    "co2_max_level": (10, 100, 10),
}

MODE_OFF = 1
MODE_SENSOR_CONTROL = 2
MODE_MANUAL = 3
MODE_UNBALANCED = 4
MODE_HUMIDITY_CONTROL_VALUE = 112
MODE_CO2_CONTROL_VALUE = 144
MODE_AUTOMATIC_VALUE = 16

# In sensor control the selector lives in REGISTER_CURRENT_LEVEL, not the mode
# register, so read and write share this mapping.
SENSOR_MODE_TO_RAW_VALUE: dict[str, int] = {
    "humidity_control": MODE_HUMIDITY_CONTROL_VALUE,
    "co2_control": MODE_CO2_CONTROL_VALUE,
    "automatic": MODE_AUTOMATIC_VALUE,
}
RAW_VALUE_TO_SENSOR_MODE: dict[int, str] = {
    value: mode for mode, value in SENSOR_MODE_TO_RAW_VALUE.items()
}


def profile_label(profile: str) -> str:
    """Return the user-facing label for one supported profile."""

    return MODEL_PROFILE_LABELS.get(profile, "M-WRG")


def profile_max_airflow(profile: str) -> int:
    """Return the max airflow in m³/h for one supported profile."""

    metadata = PROFILE_METADATA.get(profile)
    return metadata.max_airflow if metadata is not None else 100


BASE_SUPPORTED_ENTITY_KEYS: frozenset[str] = frozenset(
    {
        "exhaust_temperature",
        "extract_air_flow",
        "supply_air_flow",
        "days_until_filter_change",
        "operating_hours",
        "error_status",
        "frost_protection_active",
        "filter_change_due",
        "intensive",
        "rf_comm_status",
        "operation_mode",
        "preset_mode",
        "supply_level",
        "extract_level",
    }
)

ENTITY_PLATFORM_BY_KEY: dict[str, Platform] = {
    **dict.fromkeys(
        {
            "exhaust_temperature",
            "outdoor_air_temperature",
            "extract_air_temperature",
            "supply_air_temperature",
            "humidity_extract_air",
            "humidity_supply_air",
            "co2_extract_air",
            "voc_supply_air",
            "extract_air_flow",
            "supply_air_flow",
            "days_until_filter_change",
            "operating_hours",
        },
        Platform.SENSOR,
    ),
    **dict.fromkeys(
        {
            "error_status",
            "frost_protection_active",
            "filter_change_due",
            "rf_comm_status",
        },
        Platform.BINARY_SENSOR,
    ),
    "supply_level": Platform.FAN,
    "extract_level": Platform.FAN,
    **dict.fromkeys(CONTROL_SETTING_REGISTERS, Platform.NUMBER),
    "operation_mode": Platform.SELECT,
    "preset_mode": Platform.SELECT,
    "intensive": Platform.SWITCH,
}
