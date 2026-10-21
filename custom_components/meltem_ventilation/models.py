"""Shared lightweight models used across the Meltem integration.

The runtime deliberately passes small dataclasses around instead of dicts so
the coordinator, entities, and Modbus client can share a stable contract.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, fields, replace
from datetime import datetime
from typing import TYPE_CHECKING

from homeassistant.config_entries import ConfigEntry

from .const import READ_FAILURE_THRESHOLD

if TYPE_CHECKING:
    from .coordinator import MeltemDataUpdateCoordinator

# A mode or preset name, a level, a flag, or a supply/extract level pair.
type WriteValue = str | int | bool | tuple[int, int]


@dataclass(slots=True, frozen=True)
class GatewayRfStatistics:
    """Gateway RF message buckets and load percentages."""

    messages_current_hour: int
    messages_previous_hour: int
    load_current_hour: float | None
    load_previous_hour: float | None


@dataclass(slots=True, frozen=True)
class UnitProbeResult:
    """Identity and capability data collected by the setup-time probe."""

    profile: str
    preview: str | None
    product_id: int | None = None
    app_setup_code: str | None = None


@dataclass(slots=True, frozen=True)
class RoomConfig:
    """Configuration for one Meltem room/unit."""

    key: str
    name: str
    profile: str
    slave: int
    preview: str | None = None
    # ``None`` means every entity; set ones come from the selected profile.
    supported_entity_keys: frozenset[str] | None = None
    product_id: int | None = None
    app_setup_code: str | None = None

    def supports(self, entity_key: str) -> bool:
        """Return whether this unit has one entity."""

        return self.supported_entity_keys is None or entity_key in self.supported_entity_keys

    def supports_any(self, entity_keys: Iterable[str]) -> bool:
        """Return whether this unit has any of these entities."""

        return any(self.supports(key) for key in entity_keys)


@dataclass(slots=True, frozen=True)
class ReadHealth:
    """Freshness and failure state for one polled data group."""

    last_attempt: datetime | None = None
    last_successful_read: datetime | None = None
    consecutive_failures: int = 0
    last_error: str | None = None
    # A timeout says nothing about the unit's answer, unlike an exception response.
    last_error_timed_out: bool = False

    def failed(self, attempted_at: datetime, error: str, *, timed_out: bool = False) -> ReadHealth:
        """Return this health after one more failed read."""

        return ReadHealth(
            last_attempt=attempted_at,
            last_successful_read=self.last_successful_read,
            consecutive_failures=min(self.consecutive_failures + 1, READ_FAILURE_THRESHOLD),
            last_error=error,
            last_error_timed_out=timed_out,
        )


EMPTY_READ_HEALTH = ReadHealth()


@dataclass(slots=True, frozen=True)
class WriteConfirmation:
    """Outcome of one requested write and its device readback."""

    expected_value: WriteValue
    started_at: datetime
    status: str = "pending"
    actual_value: WriteValue | None = None
    last_error: str | None = None
    issue_counted: bool = False


@dataclass(slots=True, frozen=True)
class RoomState:
    """Polled state for one Meltem room/unit."""

    exhaust_temperature: float | None = None
    outdoor_air_temperature: float | None = None
    extract_air_temperature: float | None = None
    supply_air_temperature: float | None = None
    humidity_extract_air: int | None = None
    humidity_supply_air: int | None = None
    co2_extract_air: int | None = None
    voc_supply_air: int | None = None
    extract_air_flow: int | None = None
    supply_air_flow: int | None = None

    error_status: bool | None = None
    filter_change_due: bool | None = None
    frost_protection_active: bool | None = None
    rf_comm_status: bool | None = None
    rf_fault_status: bool | None = None
    value_error_status: bool | None = None

    operation_mode: str | None = None
    preset_mode: str | None = None
    intensive_active: bool | None = None
    target_level: int | None = None
    balanced_target_readback: int | None = None
    extract_target_level: int | None = None

    days_until_filter_change: int | None = None
    operating_hours: int | None = None
    fan_motor_hours: int | None = None
    rf_last_seen_age_seconds: int | None = None
    rf_signal_strength: int | None = None
    software_version: int | None = None

    humidity_starting_point: int | None = None
    humidity_min_level: int | None = None
    humidity_max_level: int | None = None
    co2_starting_point: int | None = None
    co2_min_level: int | None = None
    co2_max_level: int | None = None

    group_read_health: tuple[tuple[str, ReadHealth], ...] = ()

    @property
    def has_data(self) -> bool:
        """Return whether any value has been read yet."""

        return any(getattr(self, name) is not None for name in _ROOM_VALUE_FIELDS)

    def read_health_for(self, group_key: str) -> ReadHealth:
        """Return the last recorded health for one read group."""

        return dict(self.group_read_health).get(group_key, EMPTY_READ_HEALTH)

    def with_read_health(self, group_key: str, health: ReadHealth) -> RoomState:
        """Return a copy with one group's health replaced."""

        group_health = {**dict(self.group_read_health), group_key: health}
        return replace(self, group_read_health=tuple(sorted(group_health.items())))


_ROOM_VALUE_FIELDS: tuple[str, ...] = tuple(
    field.name for field in fields(RoomState) if field.name != "group_read_health"
)
EMPTY_ROOM_STATE = RoomState()


@dataclass(slots=True, frozen=True)
class RefreshPlan:
    """Describe which groups should be refreshed in the current scheduler tick."""

    refresh_airflow: bool = True
    refresh_temperatures: bool = True
    refresh_environment: bool = True
    refresh_status: bool = True
    refresh_filter_change_due: bool = True
    refresh_filter_days: bool = True
    refresh_operating_hours: bool = True
    refresh_rf_diagnostics: bool = True
    refresh_control_settings: bool = True

    @classmethod
    def only(cls, **kwargs: bool) -> RefreshPlan:
        """Return a plan that refreshes only the specified groups.

        Example::

            RefreshPlan.only(refresh_airflow=True)
        """
        return replace(_EMPTY_REFRESH_PLAN, **kwargs)

    def read_groups(self) -> tuple[str, ...]:
        """Return the read-health groups this plan refreshes."""

        groups: list[str] = []
        if self.refresh_airflow:
            groups.extend(("flow", "flow_control", "intensive"))
        if self.refresh_status:
            groups.append("status")
        if self.refresh_temperatures or self.refresh_environment:
            groups.append("temperature")
        if self.refresh_filter_change_due or self.refresh_filter_days:
            groups.append("filter")
        if self.refresh_operating_hours:
            groups.extend(("hours", "fan_hours"))
        if self.refresh_rf_diagnostics:
            groups.extend(("rf_last_seen", "rf_signal", "rf_fault", "value_error"))
        if self.refresh_control_settings:
            groups.append("control_settings")
        return tuple(groups)


_EMPTY_REFRESH_PLAN = RefreshPlan(
    **dict.fromkeys((field.name for field in fields(RefreshPlan)), False)
)


@dataclass(slots=True)
class MeltemRuntimeData:
    """Runtime objects kept for a config entry."""

    coordinator: MeltemDataUpdateCoordinator


type MeltemIntegrationConfigEntry = ConfigEntry[MeltemRuntimeData]
