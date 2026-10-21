"""Tests for sensor entity creation, native values, and availability."""

from __future__ import annotations

from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock, PropertyMock, call, patch

import pytest
from homeassistant.components.sensor import SensorDeviceClass
from homeassistant.const import EntityCategory, UnitOfVolumeFlowRate
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.meltem_ventilation.const import CONF_PORT, DOMAIN
from custom_components.meltem_ventilation.coordinator import MeltemDataUpdateCoordinator
from custom_components.meltem_ventilation.entity import MeltemEntity
from custom_components.meltem_ventilation.models import (
    ReadHealth,
    RoomConfig,
    RoomState,
    WriteConfirmation,
)
from custom_components.meltem_ventilation.sensor import (
    GATEWAY_SENSOR_DESCRIPTIONS,
    ROOM_DIAGNOSTIC_DESCRIPTIONS,
    SENSOR_DESCRIPTIONS,
    MeltemGatewayDiagnosticSensor,
    MeltemModbusDevicePathSensor,
    MeltemModbusSlaveSensor,
    MeltemRoomDiagnosticSensor,
    MeltemSensorEntity,
)

# ---------------------------------------------------------------------------
#  Helpers
# ---------------------------------------------------------------------------

_ROOM = RoomConfig(
    key="unit_1", name="Living Room", profile="ii_fc_voc", slave=2, preview="ID 116852 | VOC"
)
_SENSORS = {description.key: description for description in SENSOR_DESCRIPTIONS}
_GATEWAY_SENSORS = {description.key: description for description in GATEWAY_SENSOR_DESCRIPTIONS}


def _coordinator(state: RoomState | None = None) -> MagicMock:
    coordinator = MagicMock(spec=MeltemDataUpdateCoordinator)
    coordinator.last_update_success = True
    coordinator.room_available.return_value = True
    coordinator.read_group_for_entity.side_effect = (
        MeltemDataUpdateCoordinator.read_group_for_entity
    )
    coordinator.read_group_available.return_value = True
    coordinator.data = {} if state is None else {"unit_1": state}
    type(coordinator).safe_data = property(
        lambda self: self.data if isinstance(self.data, dict) else {}
    )
    return coordinator


def _sensor(key: str, state: RoomState | None = None) -> MeltemSensorEntity:
    return MeltemSensorEntity(_coordinator(state), _ROOM, _SENSORS[key])


# ---------------------------------------------------------------------------
#  Entity creation and metadata
# ---------------------------------------------------------------------------


class TestSensorEntityCreation:
    def test_gateway_diagnostics_remain_visible_without_modbus_requests(self) -> None:
        coordinator = _coordinator()
        coordinator.last_update_success = False
        client = coordinator.client = MagicMock()
        client.transport_diagnostics.return_value = {"seconds_since_any_answer": 125.5}

        entity = MeltemGatewayDiagnosticSensor(
            coordinator, "gateway-entry", _GATEWAY_SENSORS["gateway_last_answer_age"]
        )

        assert entity.unique_id == f"{DOMAIN}_gateway-entry_gateway_last_answer_age"
        assert entity.device_info == {
            "identifiers": {(DOMAIN, "gateway-entry")},
            "manufacturer": "Meltem",
            "model": "M-WRG-GW",
            "name": "Meltem Gateway M-WRG-GW",
        }
        assert entity.available is True
        assert entity.native_value == 125.5
        assert entity.entity_category is EntityCategory.DIAGNOSTIC
        assert entity.entity_registry_enabled_default is False
        assert client.mock_calls == [call.transport_diagnostics()]

    def test_modbus_slave_sensor_metadata_and_value(self) -> None:
        entity = MeltemModbusSlaveSensor(_coordinator(), _ROOM)

        assert entity.unique_id == f"{DOMAIN}_unit_1_modbus_slave_id"
        assert entity.native_value == 2
        assert entity.entity_category is EntityCategory.DIAGNOSTIC
        assert entity.entity_registry_enabled_default is False

    def test_modbus_device_path_sensor_metadata_and_value(self) -> None:
        entry = MockConfigEntry(
            domain=DOMAIN,
            data={CONF_PORT: "/dev/serial/by-id/meltem-gateway"},
        )
        entity = MeltemModbusDevicePathSensor(entry)

        assert entity.unique_id == f"{DOMAIN}_{entry.entry_id}_modbus_device_path"
        assert entity.native_value == "/dev/serial/by-id/meltem-gateway"
        assert entity.entity_category is EntityCategory.DIAGNOSTIC
        assert entity.entity_registry_enabled_default is False

    def test_identity_follows_the_description(self) -> None:
        sensor = _sensor("extract_air_flow")

        assert sensor.unique_id == f"{DOMAIN}_unit_1_extract_air_flow"
        assert sensor.translation_key == "extract_air_flow"
        assert sensor.has_entity_name is True

    def test_device_info(self) -> None:
        sensor = _sensor("exhaust_temperature", RoomState(software_version=42))
        setattr(sensor.coordinator, "gateway_device_id", "gateway-device")

        info = sensor.device_info

        assert (DOMAIN, "unit_1") in (info.get("identifiers") or set())
        assert info.get("manufacturer") == "Meltem"
        name = info.get("name")
        assert name is not None
        assert "Living Room" in name
        assert info.get("sw_version") == "42"
        assert info.get("hw_version") == "116852"
        assert info.get("via_device_id") == "gateway-device"

    @pytest.mark.parametrize(
        ("key", "device_class", "unit"),
        [
            (
                "extract_air_flow",
                SensorDeviceClass.VOLUME_FLOW_RATE,
                UnitOfVolumeFlowRate.CUBIC_METERS_PER_HOUR,
            ),
            (
                "supply_air_flow",
                SensorDeviceClass.VOLUME_FLOW_RATE,
                UnitOfVolumeFlowRate.CUBIC_METERS_PER_HOUR,
            ),
            ("co2_extract_air", SensorDeviceClass.CO2, "ppm"),
        ],
    )
    def test_device_class_and_unit_come_from_the_description(
        self, key: str, device_class: SensorDeviceClass, unit: str
    ) -> None:
        sensor = _sensor(key)

        assert sensor.device_class == device_class
        assert sensor.native_unit_of_measurement == unit

    def test_coordinator_update_pushes_versions_to_the_device_registry(
        self, hass: HomeAssistant
    ) -> None:
        sensor = _sensor("exhaust_temperature", RoomState(software_version=42))
        sensor.hass = hass
        registry = MagicMock()

        with (
            patch.object(
                MeltemEntity,
                "device_entry",
                new_callable=PropertyMock,
                return_value=SimpleNamespace(id="device-1"),
            ),
            patch(
                "custom_components.meltem_ventilation.entity.dr.async_get",
                return_value=registry,
            ),
            patch(
                "homeassistant.helpers.update_coordinator.BaseCoordinatorEntity"
                "._handle_coordinator_update"
            ),
        ):
            sensor._handle_coordinator_update()

        registry.async_update_device.assert_called_once_with(
            "device-1", sw_version="42", hw_version="116852"
        )

    def test_coordinator_update_skips_device_registry_when_versions_match(
        self, hass: HomeAssistant
    ) -> None:
        sensor = _sensor("exhaust_temperature", RoomState(software_version=42))
        sensor.hass = hass
        registry = MagicMock()

        with (
            patch.object(
                MeltemEntity,
                "device_entry",
                new_callable=PropertyMock,
                return_value=SimpleNamespace(id="device-1", sw_version="42", hw_version="116852"),
            ),
            patch(
                "custom_components.meltem_ventilation.entity.dr.async_get",
                return_value=registry,
            ),
            patch(
                "homeassistant.helpers.update_coordinator.BaseCoordinatorEntity"
                "._handle_coordinator_update"
            ),
        ):
            sensor._handle_coordinator_update()

        registry.async_update_device.assert_not_called()


# ---------------------------------------------------------------------------
#  Native value from room state
# ---------------------------------------------------------------------------


class TestGatewayDiagnostics:
    @pytest.mark.parametrize(
        ("key", "expected"),
        [
            ("gateway_error_units", 1),
            ("gateway_filter_due_units", 0),
            ("gateway_supply_air_flow", 60),
            ("gateway_extract_air_flow", 45),
        ],
    )
    def test_aggregates_and_incomplete_data(self, key: str, expected: int) -> None:
        coordinator = _coordinator()
        coordinator.rooms = [
            _ROOM,
            RoomConfig(key="unit_2", name="Other", profile="ii_plain", slave=3),
        ]
        coordinator.data = {
            "unit_1": RoomState(
                error_status=True, filter_change_due=False, supply_air_flow=0, extract_air_flow=15
            ),
            "unit_2": RoomState(
                error_status=False, filter_change_due=False, supply_air_flow=60, extract_air_flow=30
            ),
        }
        entity = MeltemGatewayDiagnosticSensor(coordinator, "entry", _GATEWAY_SENSORS[key])
        assert entity.native_value == expected
        attributes = entity.extra_state_attributes
        assert attributes is not None
        assert attributes["complete"] is True
        if key == "gateway_error_units":
            assert attributes["affected_units"] == {"unit_1": "Living Room"}

        coordinator.read_group_available.side_effect = lambda room, group: room != "unit_2"
        assert entity.native_value is None
        attributes = entity.extra_state_attributes
        assert attributes is not None
        assert attributes["complete"] is False
        assert attributes["unknown_units"] == {"unit_2": "Other"}

    def test_totals_are_independent_per_direction(self) -> None:
        coordinator = _coordinator(RoomState(supply_air_flow=0, extract_air_flow=None))
        coordinator.rooms = [_ROOM]
        supply = MeltemGatewayDiagnosticSensor(
            coordinator, "entry", _GATEWAY_SENSORS["gateway_supply_air_flow"]
        )
        extract = MeltemGatewayDiagnosticSensor(
            coordinator, "entry", _GATEWAY_SENSORS["gateway_extract_air_flow"]
        )
        assert supply.native_value == 0
        assert extract.native_value is None

    @pytest.mark.parametrize(
        "key", ["gateway_error_units", "gateway_filter_due_units", "gateway_supply_air_flow"]
    )
    def test_no_rooms_is_unknown(self, key: str) -> None:
        coordinator = _coordinator()
        coordinator.rooms = []
        entity = MeltemGatewayDiagnosticSensor(coordinator, "entry", _GATEWAY_SENSORS[key])
        assert entity.native_value is None

    @pytest.mark.parametrize(
        ("key", "transport_key", "value"),
        [
            ("gateway_last_answer_age", "seconds_since_any_answer", None),
            ("gateway_last_answer_age", "seconds_since_any_answer", 0.0),
            ("gateway_consecutive_timeouts", "consecutive_timeouts", 0),
            ("gateway_consecutive_timeouts", "consecutive_timeouts", 3),
            ("gateway_link_recycles", "link_recycles", 2),
        ],
    )
    def test_transport_values(self, key: str, transport_key: str, value: float | None) -> None:
        coordinator = _coordinator()
        coordinator.client = MagicMock()
        coordinator.client.transport_diagnostics.return_value = {transport_key: value}
        entity = MeltemGatewayDiagnosticSensor(coordinator, "entry", _GATEWAY_SENSORS[key])

        assert entity.native_value == value
        assert entity.extra_state_attributes is None

    @pytest.mark.parametrize("available", [[], [False, False], [True, False], [True, True]])
    def test_unit_counts(self, available: list[bool]) -> None:
        coordinator = _coordinator()
        coordinator.rooms = [
            RoomConfig(key=f"unit_{index}", name="Unit", profile="ii_plain", slave=index + 2)
            for index in range(len(available))
        ]
        coordinator.room_available.side_effect = lambda key: available[int(key.split("_")[1])]

        values = [
            MeltemGatewayDiagnosticSensor(coordinator, "entry", _GATEWAY_SENSORS[key]).native_value
            for key in ("gateway_available_units", "gateway_unavailable_units")
        ]

        assert values == [sum(available), len(available) - sum(available)]

    @pytest.mark.parametrize("stale", [None, False, True])
    def test_stale_groups_use_supported_reads_only(self, stale: bool | None) -> None:
        coordinator = _coordinator()
        coordinator.rooms = [
            RoomConfig(
                key="unit_1",
                name="Unit",
                profile="ii_plain",
                slave=2,
                supported_entity_keys=frozenset({"supply_air_flow"}),
            )
        ]
        coordinator.read_group_stale.side_effect = lambda room, group: stale
        entity = MeltemGatewayDiagnosticSensor(
            coordinator, "entry", _GATEWAY_SENSORS["gateway_stale_read_groups"]
        )

        assert entity.native_value == (None if stale is None else int(stale))
        assert entity.extra_state_attributes == {
            "affected_units": {"unit_1": ["flow"]} if stale else {}
        }
        assert all(
            request.args == ("unit_1", "flow")
            for request in coordinator.read_group_stale.call_args_list
        )
        assert "affected_units" in entity._unrecorded_attributes

    def test_stale_count_combines_expired_reads_and_failures_across_units(self) -> None:
        now = dt_util.utcnow()
        old = now - timedelta(seconds=31)
        state_1 = (
            RoomState()
            .with_read_health("flow", ReadHealth(last_attempt=old, last_successful_read=old))
            .with_read_health("status", ReadHealth(last_attempt=now, last_successful_read=now))
        )
        state_2 = (
            RoomState()
            .with_read_health("flow", ReadHealth(last_attempt=now, last_successful_read=now))
            .with_read_health("status", ReadHealth(last_attempt=now, consecutive_failures=3))
        )
        coordinator = _coordinator()
        coordinator.data = {"unit_1": state_1, "unit_2": state_2}
        coordinator.rooms = [
            RoomConfig(
                key=key,
                name=key,
                profile="ii_plain",
                slave=slave,
                supported_entity_keys=frozenset({"supply_air_flow", "error_status"}),
            )
            for key, slave in (("unit_1", 2), ("unit_2", 3))
        ]
        coordinator.read_group_stale.side_effect = lambda room, group: (
            MeltemDataUpdateCoordinator.read_group_stale(coordinator, room, group)
        )
        entity = MeltemGatewayDiagnosticSensor(
            coordinator, "entry", _GATEWAY_SENSORS["gateway_stale_read_groups"]
        )

        assert entity.native_value == 2
        assert entity.extra_state_attributes == {
            "affected_units": {"unit_1": ["flow"], "unit_2": ["status"]}
        }


class TestRoomDiagnostics:
    def test_pending_write_times_out_without_polling(self) -> None:
        coordinator = _coordinator()
        coordinator.last_update_success = False
        now = dt_util.utcnow()
        coordinator.latest_write.return_value = (
            "preset_mode",
            WriteConfirmation(expected_value="low", started_at=now),
        )
        description = next(
            item for item in ROOM_DIAGNOSTIC_DESCRIPTIONS if item.key == "last_write_status"
        )
        entity = MeltemRoomDiagnosticSensor(coordinator, _ROOM, description)
        assert entity.available is True
        assert entity.native_value == "pending"
        with patch(
            "custom_components.meltem_ventilation.write_confirmation.dt_util.utcnow",
            return_value=now + timedelta(seconds=31),
        ):
            assert entity.native_value == "unconfirmed"
        attributes = entity.extra_state_attributes
        assert attributes is not None
        assert attributes["write_key"] == "preset_mode"
        assert "last_error" not in attributes

    def test_timestamp_stays_visible_without_live_data(self) -> None:
        coordinator = _coordinator()
        coordinator.last_update_success = False
        timestamp = dt_util.utcnow()
        coordinator.last_successful_room_update.return_value = timestamp
        description = next(
            item for item in ROOM_DIAGNOSTIC_DESCRIPTIONS if item.key == "last_successful_update"
        )
        entity = MeltemRoomDiagnosticSensor(coordinator, _ROOM, description)
        assert entity.native_value == timestamp
        assert entity.available is True
        coordinator.last_successful_room_update.assert_called_once_with("unit_1")


class TestSensorNativeValue:
    @pytest.mark.parametrize(
        ("supply", "extract", "expected"),
        [(30, 40, -10), (0, 0, 0), (40, 30, 10), (None, 30, None)],
    )
    def test_balance_requires_both_directions(
        self, supply: int | None, extract: int, expected: int | None
    ) -> None:
        coordinator = _coordinator(RoomState(supply_air_flow=supply, extract_air_flow=extract))
        sensor = MeltemSensorEntity(coordinator, _ROOM, _SENSORS["airflow_balance"])
        assert sensor.native_value == expected
        coordinator.read_group_available.return_value = False
        assert sensor.available is False

    @pytest.mark.parametrize(
        ("key", "state", "expected"),
        [
            pytest.param("exhaust_temperature", RoomState(exhaust_temperature=22.5), 22.5),
            pytest.param("humidity_extract_air", RoomState(humidity_extract_air=55), 55),
            pytest.param("co2_extract_air", RoomState(co2_extract_air=800), 800),
            pytest.param("voc_supply_air", RoomState(voc_supply_air=120), 120),
            pytest.param(
                "extract_air_flow", RoomState(extract_air_flow=65, supply_air_flow=70), 65
            ),
            pytest.param("operating_hours", RoomState(operating_hours=12345), 12345),
            pytest.param("days_until_filter_change", RoomState(days_until_filter_change=90), 90),
            pytest.param("exhaust_temperature", RoomState(), None, id="not-read-yet"),
            pytest.param("exhaust_temperature", None, None, id="room-missing"),
        ],
    )
    def test_native_value_comes_from_the_room_state(
        self, key: str, state: RoomState | None, expected: float | None
    ) -> None:
        assert _sensor(key, state).native_value == expected

    def test_value_follows_the_coordinator_data(self) -> None:
        sensor = _sensor("extract_air_flow", RoomState(extract_air_flow=10))
        assert sensor.native_value == 10

        sensor.coordinator.data = {"unit_1": RoomState(extract_air_flow=80)}
        with patch.object(sensor, "async_write_ha_state"):
            sensor._handle_coordinator_update()

        assert sensor.native_value == 80


# ---------------------------------------------------------------------------
#  Availability
# ---------------------------------------------------------------------------


class TestSensorAvailability:
    @pytest.mark.parametrize(
        ("key", "stale_group", "available"),
        [
            ("supply_air_flow", "flow", False),
            ("exhaust_temperature", "flow", True),
            ("exhaust_temperature", "temperature", False),
        ],
    )
    def test_sensor_follows_the_freshness_of_its_own_read_group(
        self, key: str, stale_group: str, available: bool
    ) -> None:
        state = RoomState(supply_air_flow=30, exhaust_temperature=22.5)
        sensor = _sensor(key, state)
        with patch.object(
            sensor.coordinator,
            "read_group_available",
            side_effect=lambda _room_key, group: group != stale_group,
        ):
            assert sensor.available is available
        # A stale value stays readable; only its availability changes.
        assert sensor.native_value == getattr(state, key)
