"""Constants for the Meltem integration.

This file keeps protocol details in one place:
- config-entry keys
- scheduler defaults
- supported profile metadata
- Modbus register addresses
"""

from __future__ import annotations

from homeassistant.const import Platform

DOMAIN = "meltem_ventilation"
INTEGRATION_NAME = "Meltem Modbus"
GATEWAY_NAME = "Meltem Gateway M-WRG-GW"

PLATFORMS: list[Platform] = [
    Platform.SENSOR,
    Platform.BINARY_SENSOR,
    Platform.BUTTON,
    Platform.NUMBER,
    Platform.SELECT,
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

PROFILE_METADATA: dict[str, dict[str, object]] = {
    "s_plain": {
        "label": "M-WRG-S",
        "series": "s",
        "max_airflow": 97,
        "capabilities": frozenset(),
    },
    "s_f": {
        "label": "M-WRG-S (-F)",
        "series": "s",
        "max_airflow": 97,
        "capabilities": frozenset({"humidity"}),
    },
    "s_fc": {
        "label": "M-WRG-S (-FC)",
        "series": "s",
        "max_airflow": 97,
        "capabilities": frozenset({"humidity", "co2"}),
    },
    "ii_plain": {
        "label": "M-WRG-II",
        "series": "ii",
        "max_airflow": 100,
        "capabilities": frozenset(),
    },
    "ii_f": {
        "label": "M-WRG-II (-F)",
        "series": "ii",
        "max_airflow": 100,
        "capabilities": frozenset({"humidity"}),
    },
    "ii_fc": {
        "label": "M-WRG-II (-FC)",
        "series": "ii",
        "max_airflow": 100,
        "capabilities": frozenset({"humidity", "co2"}),
    },
    "ii_fc_voc": {
        "label": "M-WRG-II (O/VOC-AUL)",
        "series": "ii",
        "max_airflow": 100,
        "capabilities": frozenset({"humidity", "co2", "voc"}),
    },
}

MODEL_PROFILE_LABELS: dict[str, str] = {
    key: str(metadata["label"]) for key, metadata in PROFILE_METADATA.items()
}
MODEL_PROFILES: tuple[str, ...] = tuple(MODEL_PROFILE_LABELS)
ALL_PROFILES: frozenset[str] = frozenset(MODEL_PROFILES)
HUMIDITY_PROFILES: frozenset[str] = frozenset(
    key for key, metadata in PROFILE_METADATA.items() if "humidity" in metadata["capabilities"]
)
CO2_PROFILES: frozenset[str] = frozenset(
    key for key, metadata in PROFILE_METADATA.items() if "co2" in metadata["capabilities"]
)
VOC_PROFILES: frozenset[str] = frozenset(
    key for key, metadata in PROFILE_METADATA.items() if "voc" in metadata["capabilities"]
)
PLAIN_PROFILES: frozenset[str] = frozenset(
    key for key, metadata in PROFILE_METADATA.items() if not metadata["capabilities"]
)

PRESET_MODE_LOW = "low"
PRESET_MODE_MEDIUM = "medium"
PRESET_MODE_HIGH = "high"
PRESET_MODE_INTENSIVE = "intensive"
PRESET_MODE_INACTIVE = "inactive"
PRESET_MODE_EXTRACT_ONLY = "extract_only"
PRESET_MODE_SUPPLY_ONLY = "supply_only"
PRESET_MODE_OPTIONS: tuple[str, ...] = (
    PRESET_MODE_INACTIVE,
    PRESET_MODE_LOW,
    PRESET_MODE_MEDIUM,
    PRESET_MODE_HIGH,
    PRESET_MODE_EXTRACT_ONLY,
    PRESET_MODE_SUPPLY_ONLY,
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

DEBOUNCE_SECONDS = 0.8
WRITE_SETTLE_SECONDS = 1.5
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

MODE_OFF = 1
MODE_SENSOR_CONTROL = 2
MODE_MANUAL = 3
MODE_UNBALANCED = 4
MODE_HUMIDITY_CONTROL_VALUE = 112
MODE_CO2_CONTROL_VALUE = 144
MODE_AUTOMATIC_VALUE = 16


def profile_label(profile: str) -> str:
    """Return the user-facing label for one supported profile."""

    return MODEL_PROFILE_LABELS.get(profile, "M-WRG")


def profile_max_airflow(profile: str) -> int:
    """Return the max airflow in m³/h for one supported profile."""

    metadata = PROFILE_METADATA.get(profile, {})
    return int(metadata.get("max_airflow", 100))


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
        "intensive_active",
        "rf_comm_status",
        "activate_intensive",
        "operation_mode",
        "preset_mode",
        "level",
        "supply_level",
        "extract_level",
    }
)
