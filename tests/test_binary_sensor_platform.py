"""Tests for binary sensor entity creation, is_on property, and metadata."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from custom_components.meltem_ventilation.binary_sensor import (
    BINARY_SENSOR_DESCRIPTIONS,
    MeltemBinarySensorEntity,
    MeltemDataHealthBinarySensor,
)
from custom_components.meltem_ventilation.const import DOMAIN
from custom_components.meltem_ventilation.coordinator import MeltemDataUpdateCoordinator
from custom_components.meltem_ventilation.models import RoomConfig, RoomState

# ---------------------------------------------------------------------------
#  Helpers
# ---------------------------------------------------------------------------

_ROOM = RoomConfig(
    key="unit_1",
    name="Unit 1",
    profile="ii_plain",
    slave=2,
    preview="ID 116852 | basic",
)
_ROOM_CONSTRAINED = RoomConfig(
    key="unit_2",
    name="Unit 2",
    profile="ii_plain",
    slave=3,
    supported_entity_keys=frozenset({"error_status"}),
)


def _fake_coordinator(data: dict[str, RoomState] | None = None) -> MagicMock:
    coordinator = MagicMock(spec=MeltemDataUpdateCoordinator)
    coordinator.data = data or {}
    type(coordinator).safe_data = property(lambda self: self.data if isinstance(self.data, dict) else {})
    coordinator.async_add_listener = MagicMock(return_value=lambda: None)
    coordinator.read_group_for_entity.side_effect = lambda key: {
        "error_status": "status",
        "frost_protection_active": "status",
        "filter_change_due": "filter",
        "rf_comm_status": "status",
    }.get(key)
    coordinator.read_group_available.return_value = True
    return coordinator


def _find_desc(key: str):
    for d in BINARY_SENSOR_DESCRIPTIONS:
        if d.key == key:
            return d
    raise ValueError(f"No binary sensor description with key {key!r}")


# ---------------------------------------------------------------------------
#  Entity creation and metadata
# ---------------------------------------------------------------------------


class TestBinarySensorEntityCreation:
    def test_unique_id_format(self) -> None:
        coordinator = _fake_coordinator()
        desc = _find_desc("error_status")
        entity = MeltemBinarySensorEntity(coordinator, _ROOM, desc)
        assert entity.unique_id == f"{DOMAIN}_unit_1_error_status"

    def test_device_info(self) -> None:
        coordinator = _fake_coordinator(data={"unit_1": RoomState(software_version=7)})
        desc = _find_desc("error_status")
        entity = MeltemBinarySensorEntity(coordinator, _ROOM, desc)
        info = entity.device_info
        assert (DOMAIN, "unit_1") in info["identifiers"]
        assert info["manufacturer"] == "Meltem"
        assert info["sw_version"] == "7"
        assert info["hw_version"] == "116852"

    def test_data_health_entity_reports_group_details(self) -> None:
        coordinator = _fake_coordinator(data={"unit_1": RoomState()})
        coordinator.last_update_success = True
        coordinator.data_health_stale.return_value = True
        coordinator.data_health_attributes.return_value = {
            "flow": {
                "last_successful_read": "2026-09-30T12:00:00+00:00",
                "consecutive_failures": 3,
                "last_error": "airflow block read failed",
            },
            "writes": {},
        }

        entity = MeltemDataHealthBinarySensor(coordinator, _ROOM)

        assert entity.unique_id == f"{DOMAIN}_unit_1_data_health"
        assert entity.is_on is True
        assert entity.available is True
        assert entity.extra_state_attributes["flow"]["consecutive_failures"] == 3
        assert entity.entity_category.value == "diagnostic"

    def test_data_health_details_stay_out_of_the_recorder(self) -> None:
        """The timestamps change on every poll."""
        entity = MeltemDataHealthBinarySensor(_fake_coordinator(), _ROOM)

        assert {"flow", "flow_control", "intensive", "writes"} <= (
            entity._unrecorded_attributes
        )


# ---------------------------------------------------------------------------
#  is_on property
# ---------------------------------------------------------------------------


class TestBinarySensorIsOn:
    @pytest.mark.parametrize(
        "key,state_kwargs,expected",
        [
            ("error_status", {"error_status": True}, True),
            ("error_status", {"error_status": False}, False),
            ("error_status", {}, None),
            ("frost_protection_active", {"frost_protection_active": True}, True),
            ("frost_protection_active", {"frost_protection_active": False}, False),
            ("filter_change_due", {"filter_change_due": True}, True),
            ("filter_change_due", {"filter_change_due": False}, False),
            ("rf_comm_status", {"rf_comm_status": True}, True),
        ],
    )
    def test_is_on_reflects_state(
        self, key: str, state_kwargs: dict, expected: bool | None
    ) -> None:
        state = RoomState(**state_kwargs)
        coordinator = _fake_coordinator(data={"unit_1": state})
        desc = _find_desc(key)
        entity = MeltemBinarySensorEntity(coordinator, _ROOM, desc)
        assert entity.is_on is expected

    def test_status_binary_sensor_is_unavailable_when_its_group_is_stale(self) -> None:
        coordinator = _fake_coordinator(data={"unit_1": RoomState(error_status=False)})
        coordinator.last_update_success = True
        coordinator.read_group_available.return_value = False
        entity = MeltemBinarySensorEntity(coordinator, _ROOM, _find_desc("error_status"))

        assert entity.available is False


# ---------------------------------------------------------------------------
#  State updates
# ---------------------------------------------------------------------------


class TestBinarySensorStateUpdate:
    def test_value_changes_when_coordinator_data_changes(self) -> None:
        state = RoomState(error_status=False)
        coordinator = _fake_coordinator(data={"unit_1": state})
        desc = _find_desc("error_status")
        entity = MeltemBinarySensorEntity(coordinator, _ROOM, desc)
        assert entity.is_on is False

        coordinator.data = {"unit_1": RoomState(error_status=True)}
        assert entity.is_on is True


# ---------------------------------------------------------------------------
#  Missing room fallback
# ---------------------------------------------------------------------------


class TestBinarySensorFallback:
    def test_missing_room_returns_none(self) -> None:
        coordinator = _fake_coordinator(data={})
        desc = _find_desc("error_status")
        entity = MeltemBinarySensorEntity(coordinator, _ROOM, desc)
        assert entity.is_on is None
