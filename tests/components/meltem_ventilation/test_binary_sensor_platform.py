"""Tests for binary sensor entity creation, is_on property, and metadata."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from homeassistant.const import EntityCategory

from custom_components.meltem_ventilation.binary_sensor import (
    BINARY_SENSOR_DESCRIPTIONS,
    MeltemBinarySensorEntity,
    MeltemDataHealthBinarySensor,
    MeltemGatewayConnectivitySensor,
    MeltemRoomConnectivitySensor,
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
    preview="basic",
    product_id=116852,
)
_DESCRIPTIONS = {description.key: description for description in BINARY_SENSOR_DESCRIPTIONS}


def _fake_coordinator(data: dict[str, RoomState] | None = None) -> MagicMock:
    coordinator = MagicMock(spec=MeltemDataUpdateCoordinator)
    coordinator.data = data or {}
    type(coordinator).safe_data = property(
        lambda self: self.data if isinstance(self.data, dict) else {}
    )
    coordinator.last_update_success = True
    coordinator.async_add_listener = MagicMock(return_value=lambda: None)
    coordinator.read_group_for_entity.side_effect = lambda key: {
        "error_status": "status",
        "frost_protection_active": "status",
        "filter_change_due": "filter",
        "rf_comm_status": "status",
    }.get(key)
    coordinator.read_group_available.return_value = True
    return coordinator


def _entity(coordinator: MagicMock, key: str) -> MeltemBinarySensorEntity:
    return MeltemBinarySensorEntity(coordinator, _ROOM, _DESCRIPTIONS[key])


# ---------------------------------------------------------------------------
#  Entity creation and metadata
# ---------------------------------------------------------------------------


class TestBinarySensorEntityCreation:
    def test_unique_id_format(self) -> None:
        entity = _entity(_fake_coordinator(), "error_status")
        assert entity.unique_id == f"{DOMAIN}_unit_1_error_status"

    def test_device_info(self) -> None:
        coordinator = _fake_coordinator(data={"unit_1": RoomState(software_version=7)})
        info = _entity(coordinator, "error_status").device_info
        assert info.identifiers == {(DOMAIN, "unit_1")}
        assert info.manufacturer == "Meltem"
        assert info.sw_version == "7"
        assert info.hw_version == "116852"

    def test_data_health_entity_reports_group_details(self) -> None:
        coordinator = _fake_coordinator(data={"unit_1": RoomState()})
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
        flow = entity.extra_state_attributes["flow"]
        assert isinstance(flow, dict)
        assert flow["consecutive_failures"] == 3
        assert entity.entity_category == EntityCategory.DIAGNOSTIC

    def test_data_health_details_stay_out_of_the_recorder(self) -> None:
        """The timestamps change on every poll."""
        entity = MeltemDataHealthBinarySensor(_fake_coordinator(), _ROOM)

        assert {"flow", "flow_control", "intensive", "writes"} <= (entity._unrecorded_attributes)


# ---------------------------------------------------------------------------
#  State
# ---------------------------------------------------------------------------


class TestGatewayConnectivity:
    @pytest.mark.parametrize("connected", [None, False, True])
    def test_room_connectivity_stays_visible_when_room_unavailable(
        self, connected: bool | None
    ) -> None:
        coordinator = _fake_coordinator()
        coordinator.last_update_success = False
        coordinator.room_available.return_value = False
        coordinator.room_connectivity.return_value = connected
        entity = MeltemRoomConnectivitySensor(coordinator, _ROOM)
        assert entity.available is True
        assert entity.is_on is connected
        assert entity.unique_id == f"{DOMAIN}_unit_1_connectivity"

    @pytest.mark.parametrize(
        ("age", "expected"), [(None, None), (0.0, True), (30.0, True), (30.1, False)]
    )
    def test_recent_answer_controls_connectivity(
        self, age: float | None, expected: bool | None
    ) -> None:
        coordinator = _fake_coordinator()
        coordinator.last_update_success = False
        coordinator.room_available.return_value = False
        coordinator.client = MagicMock()
        coordinator.client.transport_diagnostics.return_value = {"seconds_since_any_answer": age}

        entity = MeltemGatewayConnectivitySensor(coordinator, "entry")

        assert entity.is_on is expected
        assert entity.available is True
        assert entity.unique_id == f"{DOMAIN}_entry_gateway_connectivity"
        assert entity.entity_category is EntityCategory.DIAGNOSTIC
        assert entity.entity_registry_enabled_default is True
        assert entity.device_class == "connectivity"

    def test_recovers_on_new_answer(self) -> None:
        coordinator = _fake_coordinator()
        coordinator.client = MagicMock()
        transport = {"seconds_since_any_answer": 31.0}
        coordinator.client.transport_diagnostics.return_value = transport
        entity = MeltemGatewayConnectivitySensor(coordinator, "entry")

        assert entity.is_on is False
        transport["seconds_since_any_answer"] = 0.0
        assert entity.is_on is True


class TestBinarySensorIsOn:
    @pytest.mark.parametrize("key", list(_DESCRIPTIONS))
    @pytest.mark.parametrize("value", [True, False, None])
    def test_is_on_reflects_state(self, key: str, value: bool | None) -> None:
        coordinator = _fake_coordinator(data={"unit_1": _state_for_sensor(key, value)})
        assert _entity(coordinator, key).is_on is value

    def test_value_changes_when_coordinator_data_changes(self) -> None:
        coordinator = _fake_coordinator(data={"unit_1": RoomState(error_status=False)})
        entity = _entity(coordinator, "error_status")
        assert entity.is_on is False

        coordinator.data = {"unit_1": RoomState(error_status=True)}
        with patch.object(entity, "async_write_ha_state"):
            entity._handle_coordinator_update()
        assert entity.is_on is True


def _state_for_sensor(key: str, value: bool | None) -> RoomState:
    if key == "error_status":
        return RoomState(error_status=value)
    if key == "frost_protection_active":
        return RoomState(frost_protection_active=value)
    if key == "filter_change_due":
        return RoomState(filter_change_due=value)
    if key == "rf_comm_status":
        return RoomState(rf_comm_status=value)
    if key == "rf_fault_status":
        return RoomState(rf_fault_status=value)
    if key == "value_error_status":
        return RoomState(value_error_status=value)
    raise AssertionError(f"Unexpected binary sensor key: {key}")

    def test_missing_room_returns_none(self) -> None:
        assert _entity(_fake_coordinator(data={}), "error_status").is_on is None

    def test_status_binary_sensor_is_unavailable_when_its_group_is_stale(self) -> None:
        coordinator = _fake_coordinator(data={"unit_1": RoomState(error_status=False)})
        coordinator.read_group_available.return_value = False

        assert _entity(coordinator, "error_status").available is False
