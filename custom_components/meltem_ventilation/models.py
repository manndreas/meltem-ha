"""Shared lightweight models used across the Meltem integration.

The runtime deliberately passes small dataclasses around instead of dicts so
the coordinator, entities, and Modbus client can share a stable contract.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .coordinator import MeltemDataUpdateCoordinator


@dataclass(slots=True, frozen=True)
class RoomConfig:
    """Configuration for one Meltem room/unit."""

    key: str
    name: str
    profile: str
    slave: int
    preview: str | None = None
    supported_entity_keys: frozenset[str] | None = None


@dataclass(slots=True, frozen=True)
class RoomState:
    """Polled state for one Meltem room/unit."""

    exhaust_temperature: float | None = None
    outdoor_air_temperature: float | None = None
    extract_air_temperature: float | None = None
    supply_air_temperature: float | None = None
    error_status: bool | None = None
    filter_change_due: bool | None = None
    frost_protection_active: bool | None = None
    rf_comm_status: bool | None = None
    humidity_extract_air: int | None = None
    humidity_supply_air: int | None = None
    co2_extract_air: int | None = None
    voc_supply_air: int | None = None
    extract_air_flow: int | None = None
    supply_air_flow: int | None = None
    operation_mode: str | None = None
    preset_mode: str | None = None
    intensive_active: bool | None = None
    days_until_filter_change: int | None = None
    operating_hours: int | None = None
    target_level: int | None = None
    extract_target_level: int | None = None
    software_version: int | None = None
    humidity_starting_point: int | None = None
    humidity_min_level: int | None = None
    humidity_max_level: int | None = None
    co2_starting_point: int | None = None
    co2_min_level: int | None = None
    co2_max_level: int | None = None


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
    refresh_control_settings: bool = True

    @classmethod
    def only(cls, **kwargs: bool) -> RefreshPlan:
        """Return a plan that refreshes only the specified groups.

        Example::

            RefreshPlan.only(refresh_airflow=True)
        """
        base = {
            "refresh_airflow": False,
            "refresh_temperatures": False,
            "refresh_environment": False,
            "refresh_status": False,
            "refresh_filter_change_due": False,
            "refresh_filter_days": False,
            "refresh_operating_hours": False,
            "refresh_control_settings": False,
        }
        base.update(kwargs)
        return cls(**base)

@dataclass(slots=True)
class MeltemRuntimeData:
    """Runtime objects kept for a config entry."""

    coordinator: MeltemDataUpdateCoordinator
