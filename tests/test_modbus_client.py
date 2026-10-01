"""Tests for how MeltemModbusClient reads and decodes one room.

The client runs against an in-memory gateway, so every test exercises the
real request shapes, the retry policy, and the decode logic together.
"""

from __future__ import annotations

import struct

import pytest
from modbus_connection import IllegalDataAddressError
from modbus_connection.mock import MockModbusConnection, MockModbusUnit

from custom_components.meltem_ventilation.const import (
    MODE_AUTOMATIC_VALUE,
    MODE_CO2_CONTROL_VALUE,
    MODE_HUMIDITY_CONTROL_VALUE,
    MODE_MANUAL,
    MODE_SENSOR_CONTROL,
    MODE_UNBALANCED,
    REGISTER_CURRENT_LEVEL,
    REGISTER_EXHAUST_AIR_TEMPERATURE,
    REGISTER_EXTRACT_AIR_FLOW,
    REGISTER_EXTRACT_AIR_TARGET_LEVEL,
    REGISTER_EXTRACT_AIR_TEMPERATURE,
    REGISTER_HUMIDITY_EXTRACT_AIR,
    REGISTER_HUMIDITY_STARTING_POINT,
    REGISTER_HUMIDITY_SUPPLY_AIR,
    REGISTER_MODE,
    REGISTER_PRESET_MODE,
    REGISTER_SUPPLY_AIR_TEMPERATURE,
)
from custom_components.meltem_ventilation.modbus_client import MeltemModbusClient
from custom_components.meltem_ventilation.modbus_helpers import MeltemModbusError
from custom_components.meltem_ventilation.models import RefreshPlan, RoomConfig, RoomState

_PLAIN = RoomConfig(key="unit_1", name="Unit 1", profile="ii_plain", slave=2)
_FC = RoomConfig(key="unit_1", name="Unit 1", profile="ii_fc", slave=2)
_FLOW = RefreshPlan.only(refresh_airflow=True)
_CONTROL_SETTINGS = RefreshPlan.only(refresh_control_settings=True)
_REJECTED = IllegalDataAddressError()


@pytest.fixture(autouse=True)
def _no_transport_pause(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _sleep(_seconds: float) -> None:
        return None

    monkeypatch.setattr(
        "custom_components.meltem_ventilation.device.transport.async_sleep", _sleep
    )


@pytest.fixture(name="link")
def link_fixture() -> MockModbusConnection:
    return MockModbusConnection()


@pytest.fixture(name="client")
def client_fixture(link: MockModbusConnection) -> MeltemModbusClient:
    return MeltemModbusClient(link.for_unit, port="/dev/ttyACM0")


@pytest.fixture(name="unit")
def unit_fixture(link: MockModbusConnection) -> MockModbusUnit:
    return link.for_unit(2)


def _seed(
    unit: MockModbusUnit,
    *,
    flow: tuple[int, int] = (30, 30),
    mode: list[int] | None = None,
) -> None:
    """Seed extract/supply airflow and, optionally, the 41120..41124 mode block."""

    unit.holding[REGISTER_EXTRACT_AIR_FLOW] = list(flow)
    if mode is not None:
        unit.holding[REGISTER_MODE] = list(mode)


def _reject_long_mode_read(unit: MockModbusUnit) -> None:
    """Behave like HW-4 units: the 5-register read fails, the short one works."""

    unit.fail_read(REGISTER_PRESET_MODE, _REJECTED)


def _reject_all_mode_reads(unit: MockModbusUnit) -> None:
    unit.fail_read(REGISTER_MODE, _REJECTED)
    unit.fail_read(REGISTER_CURRENT_LEVEL, _REJECTED)


def _float_words(value: float) -> list[int]:
    high, low = struct.unpack(">HH", struct.pack(">f", value))
    return [low, high]


def _reads(unit: MockModbusUnit) -> list[tuple[int, int]]:
    return [(event.address, event.count) for event in unit.read_events]


# ---------------------------------------------------------------------------
#  Request shapes
# ---------------------------------------------------------------------------


class TestRequestShapes:
    async def test_full_read_keeps_the_established_block_order(
        self, client: MeltemModbusClient, unit: MockModbusUnit
    ) -> None:
        room = RoomConfig(key="unit_1", name="Unit 1", profile="ii_fc_voc", slave=2)

        await client.read_room_state(room, RoomState(), RefreshPlan())

        assert _reads(unit) == [
            (41020, 2),
            (41000, 6),
            (41009, 2),
            (41006, 2),
            (41011, 3),
            (41016, 3),
            (41027, 1),
            (41030, 2),
            (40004, 1),
            (42000, 6),
            (40101, 1),
            (41120, 5),
        ]

    async def test_plain_profile_reads_only_the_exhaust_temperature(
        self, client: MeltemModbusClient, unit: MockModbusUnit
    ) -> None:
        unit.holding[REGISTER_EXTRACT_AIR_TEMPERATURE] = (
            _float_words(20.0) + _float_words(5.0) + _float_words(21.0)
        )

        state = await client.read_room_state(
            _PLAIN,
            RoomState(),
            RefreshPlan.only(refresh_temperatures=True, refresh_environment=True),
        )

        assert _reads(unit) == [(REGISTER_EXHAUST_AIR_TEMPERATURE, 2)]
        assert state.exhaust_temperature == 21.0
        assert state.outdoor_air_temperature is None
        assert state.extract_air_temperature is None
        assert state.supply_air_temperature is None

    async def test_unsupported_control_settings_are_not_read(
        self, client: MeltemModbusClient, unit: MockModbusUnit
    ) -> None:
        room = RoomConfig(
            key="unit_1",
            name="Unit 1",
            profile="ii_plain",
            slave=2,
            supported_entity_keys=frozenset({"extract_air_flow"}),
        )

        state = await client.read_room_state(room, RoomState(), _CONTROL_SETTINGS)

        assert _reads(unit) == []
        assert state.read_health_for("control_settings").last_attempt is None


# ---------------------------------------------------------------------------
#  Measured values
# ---------------------------------------------------------------------------


class TestMeasuredValues:
    async def test_fc_voc_profile_reads_temperatures_and_air_quality(
        self, client: MeltemModbusClient, unit: MockModbusUnit
    ) -> None:
        unit.holding.update(
            {
                REGISTER_EXTRACT_AIR_TEMPERATURE: (
                    _float_words(22.0) + _float_words(5.5) + _float_words(19.0)
                ),
                REGISTER_SUPPLY_AIR_TEMPERATURE: _float_words(18.5),
                REGISTER_HUMIDITY_EXTRACT_AIR: [44, 780],
                REGISTER_HUMIDITY_SUPPLY_AIR: [46, 0, 120],
            }
        )
        room = RoomConfig(key="unit_1", name="Unit 1", profile="ii_fc_voc", slave=2)

        state = await client.read_room_state(
            room,
            RoomState(),
            RefreshPlan.only(refresh_temperatures=True, refresh_environment=True),
        )

        assert state.extract_air_temperature == 22.0
        assert state.outdoor_air_temperature == 5.5
        assert state.exhaust_temperature == 19.0
        assert state.supply_air_temperature == 18.5
        assert state.humidity_extract_air == 44
        assert state.humidity_supply_air == 46
        assert state.co2_extract_air == 780
        assert state.voc_supply_air == 120

    async def test_nan_temperature_keeps_the_previous_value(
        self, client: MeltemModbusClient, unit: MockModbusUnit
    ) -> None:
        unit.holding[REGISTER_SUPPLY_AIR_TEMPERATURE] = [0x0000, 0x7FC0]
        room = RoomConfig(key="unit_1", name="Unit 1", profile="ii_f", slave=2)
        plan = RefreshPlan.only(refresh_temperatures=True)

        assert (await client.read_room_state(room, RoomState(), plan)).supply_air_temperature is None
        previous = RoomState(supply_air_temperature=19.5)
        state = await client.read_room_state(room, previous, plan)
        assert state.supply_air_temperature == 19.5

    async def test_operating_hours_are_a_little_endian_uint32(
        self, client: MeltemModbusClient, unit: MockModbusUnit
    ) -> None:
        unit.holding[41030] = [5, 1]

        state = await client.read_room_state(
            _PLAIN, RoomState(), RefreshPlan.only(refresh_operating_hours=True)
        )

        assert state.operating_hours == 65541


# ---------------------------------------------------------------------------
#  Read health
# ---------------------------------------------------------------------------


class TestReadHealth:
    async def test_airflow_health_tracks_failure_and_recovery(
        self, client: MeltemModbusClient, unit: MockModbusUnit
    ) -> None:
        _seed(unit, mode=[MODE_MANUAL, 60, 0, 0, 0])

        first = await client.read_room_state(_PLAIN, RoomState(), _FLOW)
        assert first.supply_air_flow == 30
        first_health = first.read_health_for("flow")
        assert first_health.last_successful_read is not None
        assert first_health.consecutive_failures == 0
        assert first_health.last_error is None

        unit.fail_read(REGISTER_EXTRACT_AIR_FLOW, _REJECTED)
        failed = await client.read_room_state(_PLAIN, first, _FLOW)
        assert failed.supply_air_flow == 30
        failed_health = failed.read_health_for("flow")
        assert failed_health.last_successful_read == first_health.last_successful_read
        assert failed_health.consecutive_failures == 1
        assert failed_health.last_error is not None

        unit.fail_read(REGISTER_EXTRACT_AIR_FLOW, None)
        recovered = await client.read_room_state(_PLAIN, failed, _FLOW)
        recovered_health = recovered.read_health_for("flow")
        assert recovered_health.consecutive_failures == 0
        assert recovered_health.last_error is None

    async def test_control_settings_round_trip_and_recover(
        self, client: MeltemModbusClient, unit: MockModbusUnit
    ) -> None:
        unit.holding[REGISTER_HUMIDITY_STARTING_POINT] = [55, 10, 90, 800, 10, 90]
        unit.fail_read(REGISTER_HUMIDITY_STARTING_POINT, _REJECTED)
        room = RoomConfig(key="unit_1", name="Unit 1", profile="ii_f", slave=2)

        failed = await client.read_room_state(
            room, RoomState(humidity_starting_point=50), _CONTROL_SETTINGS
        )
        health = failed.read_health_for("control_settings")
        assert failed.humidity_starting_point == 50
        assert health.consecutive_failures == 1
        assert health.last_successful_read is None
        assert health.last_error is not None

        unit.fail_read(REGISTER_HUMIDITY_STARTING_POINT, None)
        recovered = await client.read_room_state(room, failed, _CONTROL_SETTINGS)
        health = recovered.read_health_for("control_settings")
        assert recovered.humidity_starting_point == 55
        assert health.consecutive_failures == 0
        assert health.last_error is None

        assert await client.write_control_setting(room, "humidity_starting_point", 70) == 70
        confirmed = await client.read_room_state(room, recovered, _CONTROL_SETTINGS)
        assert confirmed.humidity_starting_point == 70


# ---------------------------------------------------------------------------
#  Airflow targets
# ---------------------------------------------------------------------------


class TestAirflowTargets:
    async def test_balanced_target_uses_the_scaled_readback(
        self, client: MeltemModbusClient, unit: MockModbusUnit
    ) -> None:
        _seed(unit, flow=(65, 65), mode=[MODE_MANUAL, 120, 0, 0, 0])

        state = await client.read_room_state(_PLAIN, RoomState(target_level=30), _FLOW)

        assert state.target_level == 60
        assert state.extract_target_level is None

    async def test_target_falls_back_to_balanced_airflow(
        self, client: MeltemModbusClient, unit: MockModbusUnit
    ) -> None:
        _seed(unit, flow=(65, 65))
        _reject_all_mode_reads(unit)

        state = await client.read_room_state(_PLAIN, RoomState(target_level=30), _FLOW)

        assert state.target_level == 65
        assert state.extract_target_level is None

    @pytest.mark.parametrize("previous_target", [None, 55])
    async def test_target_is_cleared_when_airflows_diverge(
        self,
        client: MeltemModbusClient,
        unit: MockModbusUnit,
        previous_target: int | None,
    ) -> None:
        _seed(unit, flow=(30, 40))
        _reject_all_mode_reads(unit)

        state = await client.read_room_state(
            _PLAIN, RoomState(target_level=previous_target), _FLOW
        )

        assert state.target_level is None

    async def test_mode_block_supplies_the_targets_without_extra_reads(
        self, client: MeltemModbusClient, unit: MockModbusUnit
    ) -> None:
        _seed(unit, mode=[MODE_MANUAL, 120, 0, 0, 0])
        manual = await client.read_room_state(
            _PLAIN, RoomState(operation_mode="manual", extract_target_level=60), _FLOW
        )

        _seed(unit, mode=[MODE_UNBALANCED, 120, 120, 0, 0])
        unbalanced = await client.read_room_state(
            _PLAIN, RoomState(operation_mode="unbalanced"), _FLOW
        )

        assert manual.target_level == 60
        assert manual.extract_target_level is None
        assert unbalanced.extract_target_level == 60
        assert _reads(unit) == [(41020, 2), (41120, 5)] * 2

    async def test_extract_target_is_read_on_its_own_after_the_short_block(
        self, client: MeltemModbusClient, unit: MockModbusUnit
    ) -> None:
        _seed(unit, flow=(60, 40), mode=[MODE_UNBALANCED, 120, 80, 0, 0])
        _reject_long_mode_read(unit)

        state = await client.read_room_state(_PLAIN, RoomState(), _FLOW)

        assert state.target_level == 60
        assert state.extract_target_level == 40
        assert _reads(unit) == [
            (41020, 2),
            (41120, 5),
            (41120, 2),
            (REGISTER_EXTRACT_AIR_TARGET_LEVEL, 1),
        ]

    async def test_unbalanced_app_preset_targets_decode_to_airflow(
        self, client: MeltemModbusClient, unit: MockModbusUnit
    ) -> None:
        _seed(unit, flow=(30, 0), mode=[MODE_UNBALANCED, 0, 203, 0, 0])

        state = await client.read_room_state(
            _PLAIN, RoomState(operation_mode="unbalanced"), _FLOW
        )

        assert state.target_level == 0
        assert state.extract_target_level == 30

    async def test_quick_mode_code_is_not_decoded_as_an_unbalanced_target(
        self, client: MeltemModbusClient, unit: MockModbusUnit
    ) -> None:
        """227..230 share the >200 range but decode far above the rated airflow."""
        _seed(unit, flow=(30, 0), mode=[MODE_UNBALANCED, 229, 229, 0, 0])

        state = await client.read_room_state(
            _PLAIN, RoomState(operation_mode="unbalanced"), _FLOW
        )

        assert state.target_level is None
        assert state.extract_target_level is None

    @pytest.mark.parametrize(
        ("mode_value", "expected_mode"),
        [
            (MODE_HUMIDITY_CONTROL_VALUE, "humidity_control"),
            (MODE_CO2_CONTROL_VALUE, "co2_control"),
            (MODE_AUTOMATIC_VALUE, "automatic"),
        ],
    )
    async def test_sensor_mode_selector_is_not_decoded_as_a_target(
        self,
        client: MeltemModbusClient,
        unit: MockModbusUnit,
        mode_value: int,
        expected_mode: str,
    ) -> None:
        """41121 carries 112/144/16 there, which would scale to 56/72/8 m3/h."""
        _seed(unit, flow=(24, 24), mode=[MODE_SENSOR_CONTROL, mode_value, 0, 0, 0])

        state = await client.read_room_state(_FC, RoomState(), _FLOW)

        assert state.operation_mode == expected_mode
        # Derived from the measured airflow, not from the mode selector.
        assert state.target_level == 24


# ---------------------------------------------------------------------------
#  Optional mode reads and their backoff
# ---------------------------------------------------------------------------


class TestModeReadBackoff:
    async def test_failed_current_level_read_is_backed_off(
        self, client: MeltemModbusClient, unit: MockModbusUnit
    ) -> None:
        _seed(unit)
        _reject_all_mode_reads(unit)

        first = await client.read_room_state(_PLAIN, RoomState(), _FLOW)
        second = await client.read_room_state(_PLAIN, first, _FLOW)

        assert first.target_level == 30
        assert second.target_level == 30
        assert _reads(unit).count((REGISTER_CURRENT_LEVEL, 1)) == 1

    def test_backoff_caps_the_failure_counter(self, client: MeltemModbusClient) -> None:
        key = (2, "current_level")
        client._optional_read_failures[key] = 1024

        client._mark_optional_read_failure(key, MeltemModbusError("read failed"))

        assert client._optional_read_failures[key] == 5
        assert client._is_optional_read_backed_off(key)

    async def test_successful_write_clears_the_backoff(
        self, client: MeltemModbusClient
    ) -> None:
        names = ("mode", "mode_short", "current_level", "extract_target_level")
        for name in names:
            client._mark_optional_read_failure((2, name), MeltemModbusError("no"))

        await client.write_level(_PLAIN, 40)

        for name in names:
            assert not client._is_optional_read_backed_off((2, name))


# ---------------------------------------------------------------------------
#  Operating mode, presets, and intensive
# ---------------------------------------------------------------------------


class TestModeDecoding:
    async def test_preset_is_decoded_from_the_app_code(
        self, client: MeltemModbusClient, unit: MockModbusUnit
    ) -> None:
        _seed(unit, mode=[MODE_MANUAL, 229, 0, 0, 0])

        state = await client.read_room_state(
            _PLAIN, RoomState(operation_mode="manual"), _FLOW
        )

        assert state.preset_mode == "medium"

    async def test_extract_only_preset_is_decoded_from_the_unbalanced_code(
        self, client: MeltemModbusClient, unit: MockModbusUnit
    ) -> None:
        _seed(unit, flow=(50, 0), mode=[MODE_UNBALANCED, 0, 205, 0, 0])

        state = await client.read_room_state(
            _PLAIN, RoomState(operation_mode="unbalanced"), _FLOW
        )

        assert state.preset_mode == "extract_only"

    async def test_raw_200_is_not_misdecoded_as_extract_only(
        self, client: MeltemModbusClient, unit: MockModbusUnit
    ) -> None:
        _seed(unit, flow=(100, 0), mode=[MODE_UNBALANCED, 0, 200, 0, 0])

        state = await client.read_room_state(
            _PLAIN,
            RoomState(operation_mode="unbalanced", preset_mode="extract_only"),
            _FLOW,
        )

        assert state.extract_target_level == 100
        assert state.preset_mode is None

    async def test_preset_clears_in_automatic_mode(
        self, client: MeltemModbusClient, unit: MockModbusUnit
    ) -> None:
        _seed(unit, mode=[MODE_SENSOR_CONTROL, MODE_AUTOMATIC_VALUE, 0, 0, 0])

        state = await client.read_room_state(
            _FC, RoomState(operation_mode="manual", preset_mode="low"), _FLOW
        )

        assert state.operation_mode == "automatic"
        assert state.preset_mode is None

    async def test_cleared_shadow_registers_decode_the_base_preset(
        self, client: MeltemModbusClient, unit: MockModbusUnit
    ) -> None:
        _seed(unit, mode=[MODE_MANUAL, 228, 0, 0, 0])

        state = await client.read_room_state(
            _PLAIN, RoomState(preset_mode="intensive"), _FLOW
        )

        assert state.preset_mode == "low"

    @pytest.mark.parametrize(
        ("current_level", "previous_preset", "expected_preset"),
        [
            (229, "medium", "medium"),
            (228, None, "low"),
            (230, "medium", "high"),
        ],
        ids=["keeps-base", "after-restart", "base-changes"],
    )
    async def test_active_intensive_reports_the_base_preset(
        self,
        client: MeltemModbusClient,
        unit: MockModbusUnit,
        current_level: int,
        previous_preset: str | None,
        expected_preset: str,
    ) -> None:
        _seed(unit, mode=[MODE_MANUAL, current_level, 0, MODE_MANUAL, 227])

        state = await client.read_room_state(
            _PLAIN, RoomState(preset_mode=previous_preset), _FLOW
        )

        assert state.preset_mode == expected_preset
        assert state.intensive_active is True

    async def test_inactive_intensive_is_reported_as_false(
        self, client: MeltemModbusClient, unit: MockModbusUnit
    ) -> None:
        _seed(unit, mode=[MODE_MANUAL, 229, 0, 0, 0])

        state = await client.read_room_state(
            _PLAIN, RoomState(intensive_active=True), _FLOW
        )

        assert state.intensive_active is False

    async def test_medium_intensive_medium_does_not_flip_to_high(
        self, client: MeltemModbusClient, unit: MockModbusUnit
    ) -> None:
        state = RoomState()
        for block in (
            [MODE_MANUAL, 229, 0, 0, 0],
            [MODE_MANUAL, 229, 0, MODE_MANUAL, 227],
            [MODE_MANUAL, 229, 0, 0, 0],
        ):
            _seed(unit, mode=block)
            state = await client.read_room_state(_PLAIN, state, _FLOW)

        assert state.preset_mode == "medium"


class TestShortModeFallback:
    async def test_operation_mode_comes_from_the_short_read(
        self, client: MeltemModbusClient, unit: MockModbusUnit
    ) -> None:
        _seed(unit, mode=[MODE_MANUAL, 229, 0, 0, 0])
        _reject_long_mode_read(unit)

        state = await client.read_room_state(_PLAIN, RoomState(), _FLOW)

        assert state.operation_mode == "manual"
        assert state.preset_mode == "medium"
        assert state.intensive_active is None

    async def test_short_read_keeps_intensive_without_flagging_its_health(
        self, client: MeltemModbusClient, unit: MockModbusUnit
    ) -> None:
        """Units that only reject the long read (HW-4) are not failing."""
        _seed(unit, mode=[MODE_MANUAL, 229, 0, 0, 0])
        _reject_long_mode_read(unit)

        state = await client.read_room_state(
            _PLAIN, RoomState(intensive_active=True), _FLOW
        )

        assert state.intensive_active is True
        assert state.read_health_for("flow_control").last_error is None
        assert state.read_health_for("intensive").last_attempt is None

    async def test_short_read_clears_the_preset_in_plain_manual_mode(
        self, client: MeltemModbusClient, unit: MockModbusUnit
    ) -> None:
        _seed(unit, mode=[MODE_MANUAL, 120, 0, 0, 0])
        _reject_long_mode_read(unit)

        state = await client.read_room_state(
            _PLAIN, RoomState(operation_mode="manual", preset_mode="medium"), _FLOW
        )

        assert state.operation_mode == "manual"
        assert state.preset_mode is None

    async def test_short_read_clears_the_preset_in_a_sensor_mode(
        self, client: MeltemModbusClient, unit: MockModbusUnit
    ) -> None:
        _seed(unit, mode=[MODE_SENSOR_CONTROL, MODE_AUTOMATIC_VALUE, 0, 0, 0])
        _reject_long_mode_read(unit)

        state = await client.read_room_state(
            _FC, RoomState(operation_mode="manual", preset_mode="low"), _FLOW
        )

        assert state.operation_mode == "automatic"
        assert state.preset_mode is None

    async def test_preset_is_kept_when_no_mode_read_answers(
        self, client: MeltemModbusClient, unit: MockModbusUnit
    ) -> None:
        _seed(unit, mode=[MODE_MANUAL, 120, 0, 0, 0])
        unit.fail_read(REGISTER_MODE, _REJECTED)

        state = await client.read_room_state(
            _PLAIN, RoomState(operation_mode="manual", preset_mode="medium"), _FLOW
        )

        assert state.operation_mode == "manual"
        assert state.preset_mode == "medium"
