"""Constants for the Meltem integration.

This file keeps the fixed facts in one place: config-entry keys, link and
scheduler timing, unit profiles, operating modes with their raw codes, Modbus
register addresses, and which entities exist on which platform.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

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

# ---------------------------------------------------------------------------
#  Serial link, polling, and write timing
# ---------------------------------------------------------------------------

FIXED_BAUDRATE = 19200
FIXED_BYTESIZE: Final = 8
FIXED_PARITY: Final = "E"
FIXED_STOPBITS: Final = 1
FIXED_TIMEOUT = 0.8
REQUEST_GAP_SECONDS = 0.1
# A silent unit behind the gateway also times out, so only a run of timeouts
# while no unit answered for a while points at a wedged serial link.
TRANSPORT_DISCONNECT_AFTER_TIMEOUTS = 3
TRANSPORT_LINK_QUIET_SECONDS = 10.0
# Give a disconnected USB serial endpoint time to re-enumerate before retrying.
TRANSPORT_RETRY_DELAY_SECONDS = 5.0
TRANSPORT_READ_RECOVERY_SECONDS = 5.0
GATEWAY_SILENT_AFTER_SECONDS = 30.0
GATEWAY_UPTIME_REFRESH_SECONDS = 60.0
GATEWAY_UPTIME_STALE_SECONDS = 180.0
GATEWAY_UTC_CLOCK_REFRESH_SECONDS = 60.0
GATEWAY_UTC_CLOCK_STALE_SECONDS = 180.0
GATEWAY_CONNECTED_DEVICES_REFRESH_SECONDS = 60.0
GATEWAY_CONNECTED_DEVICES_STALE_SECONDS = 180.0
GATEWAY_RF_STATISTICS_REFRESH_SECONDS = 60.0
GATEWAY_RF_STATISTICS_STALE_SECONDS = 180.0

GATEWAY_ENTITY_PLATFORM_BY_KEY: dict[str, Platform] = {
    "modbus_device_path": Platform.SENSOR,
    "gateway_connectivity": Platform.BINARY_SENSOR,
    "gateway_last_answer": Platform.SENSOR,
    "gateway_connected_devices": Platform.SENSOR,
    "gateway_rf_messages_current_hour": Platform.SENSOR,
    "gateway_rf_messages_previous_hour": Platform.SENSOR,
    "gateway_rf_load_current_hour": Platform.SENSOR,
    "gateway_rf_load_previous_hour": Platform.SENSOR,
    "gateway_response_time": Platform.SENSOR,
    "gateway_success_rate": Platform.SENSOR,
    "gateway_supply_air_flow": Platform.SENSOR,
    "gateway_extract_air_flow": Platform.SENSOR,
    "gateway_airflow_delta": Platform.SENSOR,
    "gateway_uptime": Platform.SENSOR,
    "gateway_utc_clock": Platform.SENSOR,
    "gateway_modbus_attempts_total": Platform.SENSOR,
    "gateway_modbus_failures_total": Platform.SENSOR,
    "gateway_link_recycles_total": Platform.SENSOR,
    "gateway_failed_read_groups_total": Platform.SENSOR,
    "gateway_write_issues_total": Platform.SENSOR,
}

FLOW_REFRESH_SECONDS = 10
STATUS_REFRESH_SECONDS = 60
TEMPERATURE_REFRESH_SECONDS = 60
OPERATING_HOURS_REFRESH_SECONDS = 3600
CONTROL_SETTINGS_REFRESH_SECONDS = 3600
FILTER_REFRESH_SECONDS = 3600
AIRFLOW_STALE_AFTER_SECONDS = 30
READ_FAILURE_THRESHOLD = 3

WRITE_SETTLE_SECONDS = 1.5
WRITE_CONFIRMATION_TIMEOUT_SECONDS = 30.0
# Older write outcomes stay visible as attributes but no longer flag data health.
WRITE_HEALTH_RETENTION_SECONDS = 600.0
TARGET_OPTIMISTIC_SECONDS = 15.0
POST_WRITE_REFRESH_RETRIES = 2
POST_WRITE_REFRESH_INTERVAL_SECONDS = 2.5

# ---------------------------------------------------------------------------
#  Unit profiles
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ProfileMetadata:
    """User-facing and protocol-relevant facts about one supported unit family."""

    label: str
    max_airflow: int
    capabilities: frozenset[str]


PROFILE_METADATA: dict[str, ProfileMetadata] = {
    "s_plain": ProfileMetadata("M-WRG-S", 97, frozenset()),
    "s_f": ProfileMetadata("M-WRG-S (-F)", 97, frozenset({"humidity"})),
    "s_fc": ProfileMetadata("M-WRG-S (-FC)", 97, frozenset({"humidity", "co2"})),
    "ii_plain": ProfileMetadata("M-WRG-II", 100, frozenset()),
    "ii_f": ProfileMetadata("M-WRG-II (-F)", 100, frozenset({"humidity"})),
    "ii_fc": ProfileMetadata("M-WRG-II (-FC)", 100, frozenset({"humidity", "co2"})),
    "ii_fc_voc": ProfileMetadata(
        "M-WRG-II (O/VOC-AUL)", 100, frozenset({"humidity", "co2", "voc"})
    ),
}


def _profiles_with(capability: str) -> frozenset[str]:
    return frozenset(
        key for key, metadata in PROFILE_METADATA.items() if capability in metadata.capabilities
    )


MODEL_PROFILE_LABELS: dict[str, str] = {
    key: metadata.label for key, metadata in PROFILE_METADATA.items()
}
MODEL_PROFILES: tuple[str, ...] = tuple(MODEL_PROFILE_LABELS)
ALL_PROFILES: frozenset[str] = frozenset(MODEL_PROFILES)
HUMIDITY_PROFILES: frozenset[str] = _profiles_with("humidity")
CO2_PROFILES: frozenset[str] = _profiles_with("co2")
VOC_PROFILES: frozenset[str] = _profiles_with("voc")
# Units that can run a sensor-driven control mode at all.
SENSOR_CONTROL_PROFILES: frozenset[str] = HUMIDITY_PROFILES | CO2_PROFILES
PLAIN_PROFILES: frozenset[str] = ALL_PROFILES - SENSOR_CONTROL_PROFILES


def profile_label(profile: str) -> str:
    """Return the user-facing label for one supported profile."""

    return MODEL_PROFILE_LABELS.get(profile, "M-WRG")


def profile_max_airflow(profile: str) -> int:
    """Return the max airflow in m³/h for one supported profile."""

    metadata = PROFILE_METADATA.get(profile)
    return metadata.max_airflow if metadata is not None else 100


# ---------------------------------------------------------------------------
#  Operating and preset modes
# ---------------------------------------------------------------------------

PRESET_MODE_INTENSIVE = "intensive"
PRESET_MODE_INACTIVE = "inactive"
# Still decoded from the device, but no longer selectable: the separate supply
# and extract fans express these states directly.
PRESET_MODE_EXTRACT_ONLY = "extract_only"
PRESET_MODE_SUPPLY_ONLY = "supply_only"
PRESET_MODE_OPTIONS: tuple[str, ...] = (
    PRESET_MODE_INACTIVE,
    "low",
    "medium",
    "high",
)

PRESET_MODE_CODE_LOW = 228
PRESET_MODE_CODE_MEDIUM = 229
PRESET_MODE_CODE_HIGH = 230
PRESET_MODE_CODE_INTENSIVE = 227
PRESET_MODE_TO_RAW_CODE: dict[str, int] = {
    "low": PRESET_MODE_CODE_LOW,
    "medium": PRESET_MODE_CODE_MEDIUM,
    "high": PRESET_MODE_CODE_HIGH,
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

MODE_OFF = 1
MODE_SENSOR_CONTROL = 2
MODE_MANUAL = 3
MODE_UNBALANCED = 4
MODE_HUMIDITY_CONTROL_VALUE = 112
MODE_CO2_CONTROL_VALUE = 144
MODE_AUTOMATIC_VALUE = 16

# Sensor-control selectors written to REGISTER_CURRENT_LEVEL.
SENSOR_MODE_TO_RAW_VALUE: dict[str, int] = {
    "humidity_control": MODE_HUMIDITY_CONTROL_VALUE,
    "co2_control": MODE_CO2_CONTROL_VALUE,
    "automatic": MODE_AUTOMATIC_VALUE,
}
RAW_VALUE_TO_SENSOR_MODE: dict[int, str] = {
    value: mode for mode, value in SENSOR_MODE_TO_RAW_VALUE.items()
}
SENSOR_OPERATION_MODES: tuple[str, ...] = tuple(SENSOR_MODE_TO_RAW_VALUE)

# Read-side sensor subcodes in 41101, from the ioBroker decode and not yet seen
# on hardware; 144 is an unverified extra (HW-4). Used for every profile.
MODE_STATUS_SENSOR_MODE_TO_RAW_VALUE: dict[int, int] = {
    48: MODE_AUTOMATIC_VALUE,
    MODE_HUMIDITY_CONTROL_VALUE: MODE_HUMIDITY_CONTROL_VALUE,
    MODE_CO2_CONTROL_VALUE: MODE_CO2_CONTROL_VALUE,
    176: MODE_CO2_CONTROL_VALUE,
}

DIRECTION_SUPPLY = "supply"
DIRECTION_EXTRACT = "extract"

# Where the airflow shown by a fan entity comes from.
LEVEL_SOURCE_PENDING = "pending"
LEVEL_SOURCE_TARGET = "target"
LEVEL_SOURCE_MEASURED = "measured"

LEVEL_WRITE_FALLBACK_BALANCED = "both_directions_balanced_manual"
LEVEL_WRITE_FALLBACK_UNKNOWN_MODE = "unknown_mode_overridden"

# ---------------------------------------------------------------------------
#  Modbus registers
# ---------------------------------------------------------------------------

REGISTER_PRODUCT_ID = 40002
REGISTER_APP_SETUP_CODE = 40000
REGISTER_SOFTWARE_VERSION = 40004
REGISTER_RF_LAST_SEEN_AGE = 40100
REGISTER_RF_COMM_STATUS = 40101
REGISTER_RF_FAULT_STATUS = 40103
REGISTER_VALUE_ERROR_STATUS = 40104
REGISTER_RF_SIGNAL_STRENGTH = 40109

# On the tested M-WRG-GW gateway, 41000 and 41004 are effectively swapped
# compared to the unit manual. We map the logical sensor names to the values
# actually observed on the gateway.
REGISTER_EXTRACT_AIR_TEMPERATURE = 41000
REGISTER_OUTDOOR_AIR_TEMPERATURE = 41002
REGISTER_EXHAUST_AIR_TEMPERATURE = 41004
REGISTER_HUMIDITY_EXTRACT_AIR = 41006
REGISTER_CO2_EXTRACT_AIR = 41007
REGISTER_SUPPLY_AIR_TEMPERATURE = 41009
REGISTER_HUMIDITY_SUPPLY_AIR = 41011
REGISTER_VOC_SUPPLY_AIR = 41013
REGISTER_ERROR_STATUS = 41016
REGISTER_FILTER_CHANGE_DUE = 41017
REGISTER_FROST_PROTECTION_ACTIVE = 41018
REGISTER_EXTRACT_AIR_FLOW = 41020
REGISTER_SUPPLY_AIR_FLOW = 41021
REGISTER_DAYS_UNTIL_FILTER_CHANGE = 41027
REGISTER_OPERATING_HOURS = 41030
REGISTER_FAN_MOTOR_HOURS = 41032

# Current-state registers, distinct from the set registers below.
REGISTER_MODE_STATUS = 41100
REGISTER_SUPPLY_MODE_STATUS = 41101
REGISTER_EXTRACT_MODE_STATUS = 41102

# Registers written to change the operating mode and target.
REGISTER_MODE = 41120
REGISTER_CURRENT_LEVEL = 41121
REGISTER_EXTRACT_AIR_TARGET_LEVEL = 41122
REGISTER_PRESET_MODE = 41123
REGISTER_PRESET_VALUE = 41124
REGISTER_APPLY = 41132

REGISTER_HUMIDITY_STARTING_POINT = 42000
REGISTER_HUMIDITY_MIN_LEVEL = 42001
REGISTER_HUMIDITY_MAX_LEVEL = 42002
REGISTER_CO2_STARTING_POINT = 42003
REGISTER_CO2_MIN_LEVEL = 42004
REGISTER_CO2_MAX_LEVEL = 42005

REGISTER_GATEWAY_NUMBER_OF_NODES = 43901
REGISTER_GATEWAY_MAC_START = 41012
REGISTER_GATEWAY_UTC_CLOCK = 41015
REGISTER_GATEWAY_UPTIME = 41019
REGISTER_GATEWAY_NODE_ADDRESS_1 = 43902
REGISTER_GATEWAY_RF_MESSAGES_CURRENT_HOUR = 42100
REGISTER_GATEWAY_RF_MESSAGES_PREVIOUS_HOUR = 42101
REGISTER_GATEWAY_RF_LOAD_CURRENT_HOUR = 42102
REGISTER_GATEWAY_RF_LOAD_PREVIOUS_HOUR = 42104

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

# Minimum and maximum airflow level of each sensor control; min must not exceed max.
CONTROL_LEVEL_RANGES: tuple[tuple[str, str], ...] = (
    ("humidity_min_level", "humidity_max_level"),
    ("co2_min_level", "co2_max_level"),
)

# ---------------------------------------------------------------------------
#  Entity groups and platforms
# ---------------------------------------------------------------------------

# Entities whose values one read-health group delivers; shared by the client
# (health bookkeeping) and the coordinator (scheduling and availability).
READ_GROUP_ENTITY_KEYS: dict[str, frozenset[str]] = {
    "flow": frozenset({"extract_air_flow", "supply_air_flow", "airflow_balance"}),
    "flow_control": frozenset({"operation_mode", "preset_mode", "supply_level", "extract_level"}),
    "intensive": frozenset({"intensive"}),
    "status": frozenset({"error_status", "frost_protection_active", "rf_comm_status"}),
    "temperature": frozenset(
        {
            "exhaust_temperature",
            "outdoor_air_temperature",
            "extract_air_temperature",
            "supply_air_temperature",
            "humidity_extract_air",
            "humidity_supply_air",
            "co2_extract_air",
            "voc_supply_air",
        }
    ),
    "filter": frozenset({"filter_change_due", "days_until_filter_change"}),
    "hours": frozenset({"operating_hours"}),
    "fan_hours": frozenset({"fan_motor_hours"}),
    "rf_last_seen": frozenset({"rf_last_seen_age_seconds"}),
    "rf_signal": frozenset({"rf_signal_strength"}),
    "rf_fault": frozenset({"rf_fault_status"}),
    "value_error": frozenset({"value_error_status"}),
    "control_settings": frozenset(CONTROL_SETTING_REGISTERS),
}

BASE_SUPPORTED_ENTITY_KEYS: frozenset[str] = frozenset(
    {
        "exhaust_temperature",
        "extract_air_flow",
        "supply_air_flow",
        "days_until_filter_change",
        "operating_hours",
        "fan_motor_hours",
        "rf_last_seen_age_seconds",
        "rf_signal_strength",
        "app_setup_code",
        "modbus_slave_id",
        "error_status",
        "frost_protection_active",
        "filter_change_due",
        "intensive",
        "rf_comm_status",
        "rf_fault_status",
        "value_error_status",
        "operation_mode",
        "preset_mode",
        "supply_level",
        "extract_level",
        "airflow_balance",
        "connectivity",
        "last_successful_update",
    }
)

ENTITY_PLATFORM_BY_KEY: dict[str, Platform] = {
    **dict.fromkeys(
        (
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
            "fan_motor_hours",
            "rf_last_seen_age_seconds",
            "rf_signal_strength",
            "app_setup_code",
            "modbus_slave_id",
            "airflow_balance",
            "last_successful_update",
        ),
        Platform.SENSOR,
    ),
    **dict.fromkeys(
        (
            "error_status",
            "frost_protection_active",
            "filter_change_due",
            "rf_comm_status",
            "rf_fault_status",
            "value_error_status",
            "data_health",
            "connectivity",
        ),
        Platform.BINARY_SENSOR,
    ),
    **dict.fromkeys(("supply_level", "extract_level"), Platform.FAN),
    **dict.fromkeys(CONTROL_SETTING_REGISTERS, Platform.NUMBER),
    **dict.fromkeys(("operation_mode", "preset_mode"), Platform.SELECT),
    "intensive": Platform.SWITCH,
}
