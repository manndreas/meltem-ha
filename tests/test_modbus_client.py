"""Tests for how MeltemModbusClient reads and decodes one room.

The client runs against an in-memory gateway, so every test exercises the
real request shapes, the retry policy, and the decode logic together.
"""

from __future__ import annotations

import struct
import time
from collections.abc import Mapping
from dataclasses import replace
from unittest.mock import AsyncMock

import pytest
from homeassistant.util import dt as dt_util
from modbus_connection import IllegalDataAddressError, ModbusTimeoutError
from modbus_connection.mock import MockModbusConnection, MockModbusUnit

from custom_components.meltem_ventilation.const import (
    MODE_AUTOMATIC_VALUE,
    MODE_CO2_CONTROL_VALUE,
    MODE_HUMIDITY_CONTROL_VALUE,
    MODE_MANUAL,
    MODE_OFF,
    MODE_SENSOR_CONTROL,
    MODE_STATUS_SENSOR_MODE_TO_RAW_VALUE,
    MODE_UNBALANCED,
    PRESET_MODE_CODE_INTENSIVE,
    PROFILE_METADATA,
    REGISTER_EXHAUST_AIR_TEMPERATURE,
    REGISTER_EXTRACT_AIR_FLOW,
    REGISTER_EXTRACT_AIR_TEMPERATURE,
    REGISTER_HUMIDITY_EXTRACT_AIR,
    REGISTER_HUMIDITY_STARTING_POINT,
    REGISTER_HUMIDITY_SUPPLY_AIR,
    REGISTER_MODE,
    REGISTER_MODE_STATUS,
    REGISTER_OPERATING_HOURS,
    REGISTER_SUPPLY_AIR_TEMPERATURE,
)
from custom_components.meltem_ventilation.modbus_client import MeltemModbusClient
from custom_components.meltem_ventilation.modbus_helpers import MeltemModbusError
from custom_components.meltem_ventilation.models import (
    EMPTY_ROOM_STATE,
    ReadHealth,
    RefreshPlan,
    RoomConfig,
    RoomState,
)

_PLAIN = RoomConfig(key="unit_1", name="Unit 1", profile="ii_plain", slave=2)
_S_PLAIN = replace(_PLAIN, profile="s_plain")
_F = replace(_PLAIN, profile="ii_f")
_FC = replace(_PLAIN, profile="ii_fc")
_FC_VOC = replace(_PLAIN, profile="ii_fc_voc")
_S_FC = replace(_S_PLAIN, profile="s_fc")
_FLOW = RefreshPlan.only(refresh_airflow=True)
_TEMPERATURES = RefreshPlan.only(refresh_temperatures=True, refresh_environment=True)
_CONTROL_SETTINGS = RefreshPlan.only(refresh_control_settings=True)
_REJECTED = IllegalDataAddressError()


@pytest.fixture(autouse=True)
def _no_transport_pause(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "custom_components.meltem_ventilation.device.transport.async_sleep", AsyncMock()
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
    """Seed airflow and, optionally, write registers plus current-mode status."""

    unit.holding[REGISTER_EXTRACT_AIR_FLOW] = list(flow)
    if mode is None:
        unit.holding[REGISTER_MODE_STATUS] = [MODE_OFF, 0, 0]
        return

    unit.holding[REGISTER_MODE] = list(mode)
    status_mode, status_current, status_extract = mode[:3]
    if status_mode == MODE_SENSOR_CONTROL:
        write_to_status_mode = {
            write_value: status_mode_value
            for status_mode_value, write_value in MODE_STATUS_SENSOR_MODE_TO_RAW_VALUE.items()
        }
        status_current = write_to_status_mode.get(status_current, status_current)
    if (
        len(mode) > 4
        and mode[3] == MODE_MANUAL
        and mode[4] == PRESET_MODE_CODE_INTENSIVE
    ):
        # Observed on slave 4 while an app-started override was running.
        status_mode, status_current, status_extract = 0, 0, 0
    unit.holding[REGISTER_MODE_STATUS] = [
        status_mode,
        status_current,
        status_extract,
    ]


def _reject_mode_status_read(unit: MockModbusUnit) -> None:
    """Make the read-only current-state map unavailable."""

    unit.fail_read(REGISTER_MODE_STATUS, _REJECTED)


def _float_words(value: float) -> list[int]:
    high, low = struct.unpack(">HH", struct.pack(">f", value))
    return [low, high]


def _reads(unit: MockModbusUnit) -> list[tuple[int, int]]:
    return [(event.address, event.count) for event in unit.read_events]


async def _read_flow(
    client: MeltemModbusClient,
    previous: RoomState = EMPTY_ROOM_STATE,
    room: RoomConfig = _PLAIN,
) -> RoomState:
    return await client.read_room_state(room, previous, _FLOW)


def _values(state: RoomState, expected: Mapping[str, object]) -> dict[str, object]:
    """Return the state's values for the fields named in ``expected``."""
    return {name: getattr(state, name) for name in expected}


# ---------------------------------------------------------------------------
#  Request shapes
# ---------------------------------------------------------------------------


class TestRequestShapes:
    async def test_full_read_keeps_the_established_block_order(
        self, client: MeltemModbusClient, unit: MockModbusUnit
    ) -> None:
        await client.read_room_state(_FC_VOC, RoomState(), RefreshPlan())

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
            (REGISTER_MODE_STATUS, 3),
        ]

    async def test_plain_profile_reads_only_the_exhaust_temperature(
        self, client: MeltemModbusClient, unit: MockModbusUnit
    ) -> None:
        unit.holding[REGISTER_EXTRACT_AIR_TEMPERATURE] = (
            _float_words(20.0) + _float_words(5.0) + _float_words(21.0)
        )

        state = await client.read_room_state(_PLAIN, RoomState(), _TEMPERATURES)

        expected = {
            "exhaust_temperature": 21.0,
            "outdoor_air_temperature": None,
            "extract_air_temperature": None,
            "supply_air_temperature": None,
        }
        assert _reads(unit) == [(REGISTER_EXHAUST_AIR_TEMPERATURE, 2)]
        assert _values(state, expected) == expected

    async def test_plain_profile_keeps_previous_extended_measurements(
        self, client: MeltemModbusClient, unit: MockModbusUnit
    ) -> None:
        extended = {
            "outdoor_air_temperature": 5.5,
            "extract_air_temperature": 22.0,
            "supply_air_temperature": 18.5,
            "humidity_extract_air": 44,
            "co2_extract_air": 780,
            "humidity_supply_air": 46,
            "voc_supply_air": 120,
        }

        state = await client.read_room_state(_PLAIN, RoomState(**extended), _TEMPERATURES)

        assert _reads(unit) == [(REGISTER_EXHAUST_AIR_TEMPERATURE, 2)]
        assert _values(state, extended) == extended

    async def test_unsupported_control_settings_are_not_read(
        self, client: MeltemModbusClient, unit: MockModbusUnit
    ) -> None:
        room = replace(_PLAIN, supported_entity_keys=frozenset({"extract_air_flow"}))

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

        state = await client.read_room_state(_FC_VOC, RoomState(), _TEMPERATURES)

        expected = {
            "extract_air_temperature": 22.0,
            "outdoor_air_temperature": 5.5,
            "exhaust_temperature": 19.0,
            "supply_air_temperature": 18.5,
            "humidity_extract_air": 44,
            "humidity_supply_air": 46,
            "co2_extract_air": 780,
            "voc_supply_air": 120,
        }
        assert _values(state, expected) == expected

    async def test_nan_temperature_keeps_the_previous_value(
        self, client: MeltemModbusClient, unit: MockModbusUnit
    ) -> None:
        unit.holding[REGISTER_SUPPLY_AIR_TEMPERATURE] = [0x0000, 0x7FC0]
        plan = RefreshPlan.only(refresh_temperatures=True)

        without_previous = await client.read_room_state(_F, RoomState(), plan)
        with_previous = await client.read_room_state(
            _F, RoomState(supply_air_temperature=19.5), plan
        )

        assert without_previous.supply_air_temperature is None
        assert with_previous.supply_air_temperature == 19.5
        assert with_previous.read_health_for("temperature").consecutive_failures == 1
        assert with_previous.read_health_for("temperature").last_successful_read is None

    async def test_nan_temperature_preserves_the_last_successful_read(
        self, client: MeltemModbusClient, unit: MockModbusUnit
    ) -> None:
        plan = RefreshPlan.only(refresh_temperatures=True)
        unit.holding[REGISTER_SUPPLY_AIR_TEMPERATURE] = _float_words(19.5)
        initial = await client.read_room_state(_F, RoomState(), plan)
        unit.holding[REGISTER_SUPPLY_AIR_TEMPERATURE] = [0x0000, 0x7FC0]

        failed = await client.read_room_state(_F, initial, plan)

        assert failed.supply_air_temperature == 19.5
        assert failed.read_health_for("temperature").last_successful_read == (
            initial.read_health_for("temperature").last_successful_read
        )
        assert failed.read_health_for("temperature").consecutive_failures == 1

    async def test_operating_hours_are_a_little_endian_uint32(
        self, client: MeltemModbusClient, unit: MockModbusUnit
    ) -> None:
        unit.holding[REGISTER_OPERATING_HOURS] = [5, 1]

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

        first = await _read_flow(client)
        assert first.supply_air_flow == 30
        first_health = first.read_health_for("flow")
        assert first_health.last_successful_read is not None
        assert first_health.consecutive_failures == 0
        assert first_health.last_error is None

        unit.fail_read(REGISTER_EXTRACT_AIR_FLOW, _REJECTED)
        failed = await _read_flow(client, first)
        assert failed.supply_air_flow == 30
        failed_health = failed.read_health_for("flow")
        assert failed_health.last_successful_read == first_health.last_successful_read
        assert failed_health.consecutive_failures == 1
        assert failed_health.last_error is not None

        unit.fail_read(REGISTER_EXTRACT_AIR_FLOW, None)
        recovered_health = (await _read_flow(client, failed)).read_health_for("flow")
        assert recovered_health.consecutive_failures == 0
        assert recovered_health.last_error is None

    async def test_control_settings_round_trip_and_recover(
        self, client: MeltemModbusClient, unit: MockModbusUnit
    ) -> None:
        unit.holding[REGISTER_HUMIDITY_STARTING_POINT] = [55, 10, 90, 800, 10, 90]
        unit.fail_read(REGISTER_HUMIDITY_STARTING_POINT, _REJECTED)

        failed = await client.read_room_state(
            _F, RoomState(humidity_starting_point=50), _CONTROL_SETTINGS
        )
        health = failed.read_health_for("control_settings")
        assert failed.humidity_starting_point == 50
        assert health.consecutive_failures == 1
        assert health.last_successful_read is None
        assert health.last_error is not None

        unit.fail_read(REGISTER_HUMIDITY_STARTING_POINT, None)
        recovered = await client.read_room_state(_F, failed, _CONTROL_SETTINGS)
        health = recovered.read_health_for("control_settings")
        assert recovered.humidity_starting_point == 55
        assert health.consecutive_failures == 0
        assert health.last_error is None

        assert await client.write_control_setting(_F, "humidity_starting_point", 70) == 70
        confirmed = await client.read_room_state(_F, recovered, _CONTROL_SETTINGS)
        assert confirmed.humidity_starting_point == 70


# ---------------------------------------------------------------------------
#  Airflow targets
# ---------------------------------------------------------------------------


class TestAirflowTargets:
    async def test_balanced_target_uses_the_scaled_readback(
        self, client: MeltemModbusClient, unit: MockModbusUnit
    ) -> None:
        _seed(unit, flow=(65, 65), mode=[MODE_MANUAL, 120, 0, 0, 0])

        state = await _read_flow(client, RoomState(target_level=30))

        assert state.target_level == 60
        assert state.balanced_target_readback == 60
        assert state.extract_target_level is None

    async def test_target_falls_back_to_balanced_airflow(
        self, client: MeltemModbusClient, unit: MockModbusUnit
    ) -> None:
        _seed(unit, flow=(65, 65))
        _reject_mode_status_read(unit)

        state = await _read_flow(client, RoomState(target_level=30))

        assert state.target_level == 65
        assert state.balanced_target_readback is None
        assert state.extract_target_level is None

    @pytest.mark.parametrize("previous_target", [None, 55])
    async def test_target_is_cleared_when_airflows_diverge(
        self,
        client: MeltemModbusClient,
        unit: MockModbusUnit,
        previous_target: int | None,
    ) -> None:
        _seed(unit, flow=(30, 40))
        _reject_mode_status_read(unit)

        state = await _read_flow(client, RoomState(target_level=previous_target))

        assert state.target_level is None

    async def test_mode_status_supplies_the_targets_without_extra_reads(
        self, client: MeltemModbusClient, unit: MockModbusUnit
    ) -> None:
        _seed(unit, mode=[MODE_MANUAL, 120, 0, 0, 0])
        manual = await _read_flow(
            client, RoomState(operation_mode="manual", extract_target_level=60)
        )

        _seed(unit, mode=[MODE_UNBALANCED, 120, 120, 0, 0])
        unbalanced = await _read_flow(client, RoomState(operation_mode="unbalanced"))

        assert manual.target_level == 60
        assert manual.extract_target_level is None
        assert unbalanced.extract_target_level == 60
        for state in (manual, unbalanced):
            for group in ("flow_control", "intensive"):
                health = state.read_health_for(group)
                assert health.consecutive_failures == 0
                assert health.last_successful_read == health.last_attempt
                assert health.last_error is None
        assert _reads(unit) == [
            (REGISTER_EXTRACT_AIR_FLOW, 2),
            (REGISTER_MODE_STATUS, 3),
        ] * 2

    @pytest.mark.parametrize("profile", sorted(PROFILE_METADATA))
    async def test_every_supported_profile_uses_the_status_map(
        self, client: MeltemModbusClient, unit: MockModbusUnit, profile: str
    ) -> None:
        unit.holding[REGISTER_EXTRACT_AIR_FLOW] = [30, 30]
        unit.holding[REGISTER_MODE_STATUS] = [MODE_MANUAL, 229, 0]

        state = await _read_flow(client, room=replace(_PLAIN, profile=profile))

        assert state.operation_mode == "manual"
        assert state.preset_mode == "medium"
        assert _reads(unit) == [
            (REGISTER_EXTRACT_AIR_FLOW, 2),
            (REGISTER_MODE_STATUS, 3),
        ]

    async def test_current_mode_status_clears_old_set_register_read_failures(
        self, client: MeltemModbusClient, unit: MockModbusUnit
    ) -> None:
        _seed(unit, mode=[MODE_MANUAL, 229, 0, 0, 0])
        previous = RoomState()
        for group in ("flow_control", "intensive"):
            previous = previous.with_read_health(
                group,
                ReadHealth(
                    last_attempt=dt_util.utcnow(),
                    consecutive_failures=3,
                    last_error="holding block read at address 41120 returned 0x05",
                ),
            )

        state = await _read_flow(client, previous)

        assert state.operation_mode == "manual"
        assert state.preset_mode == "medium"
        assert state.intensive_active is False
        for group in ("flow_control", "intensive"):
            health = state.read_health_for(group)
            assert health.consecutive_failures == 0
            assert health.last_successful_read == health.last_attempt
            assert health.last_error is None

    async def test_unbalanced_app_preset_targets_decode_to_airflow(
        self, client: MeltemModbusClient, unit: MockModbusUnit
    ) -> None:
        _seed(unit, flow=(30, 0), mode=[MODE_UNBALANCED, 0, 203, 0, 0])

        state = await _read_flow(client, RoomState(operation_mode="unbalanced"))

        assert state.target_level == 0
        assert state.extract_target_level == 30

    @pytest.mark.parametrize(
        ("room", "supply_target"),
        [
            pytest.param(_PLAIN, 34, id="m-wrg-ii"),
            pytest.param(_S_PLAIN, 33, id="m-wrg-s"),
        ],
    )
    async def test_unbalanced_mode_status_decodes_supply_and_extract(
        self,
        client: MeltemModbusClient,
        unit: MockModbusUnit,
        room: RoomConfig,
        supply_target: int,
    ) -> None:
        unit.holding[REGISTER_EXTRACT_AIR_FLOW] = [30, 34]
        unit.holding[REGISTER_MODE_STATUS] = [MODE_UNBALANCED, 68, 203]

        state = await _read_flow(client, RoomState(), room)

        assert state.operation_mode == "unbalanced"
        assert state.target_level == supply_target
        assert state.extract_target_level == 30
        assert state.preset_mode is None
        assert state.intensive_active is False

    async def test_quick_mode_code_is_not_decoded_as_an_unbalanced_target(
        self, client: MeltemModbusClient, unit: MockModbusUnit
    ) -> None:
        """227..230 share the >200 range but decode far above the rated airflow."""
        _seed(unit, flow=(30, 0), mode=[MODE_UNBALANCED, 229, 229, 0, 0])

        state = await _read_flow(client, RoomState(operation_mode="unbalanced"))

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
    @pytest.mark.parametrize("room", [_FC, _S_FC], ids=["m-wrg-ii", "m-wrg-s"])
    async def test_sensor_mode_selector_is_not_decoded_as_a_target(
        self,
        client: MeltemModbusClient,
        unit: MockModbusUnit,
        mode_value: int,
        expected_mode: str,
        room: RoomConfig,
    ) -> None:
        """41121 carries 112/144/16 there, which would scale to 56/72/8 m3/h."""
        _seed(unit, flow=(24, 24), mode=[MODE_SENSOR_CONTROL, mode_value, 0, 0, 0])

        state = await _read_flow(client, room=room)

        assert state.operation_mode == expected_mode
        # Derived from the measured airflow, not from the mode selector.
        assert state.target_level == 24

    async def test_unbalanced_unit_without_mode_reads_keeps_both_targets(
        self, client: MeltemModbusClient, unit: MockModbusUnit
    ) -> None:
        _seed(unit, flow=(40, 60))
        _reject_mode_status_read(unit)

        state = await _read_flow(
            client,
            RoomState(operation_mode="unbalanced", target_level=60, extract_target_level=40),
        )

        assert state.operation_mode == "unbalanced"
        assert state.target_level == 60
        assert state.extract_target_level == 40
        assert state.read_health_for("flow_control").last_error is not None


# ---------------------------------------------------------------------------
#  Optional mode reads and their backoff
# ---------------------------------------------------------------------------


class TestModeReadBackoff:
    async def test_failed_status_block_is_backed_off(
        self, client: MeltemModbusClient, unit: MockModbusUnit
    ) -> None:
        _seed(unit)
        _reject_mode_status_read(unit)

        first = await _read_flow(client)
        second = await _read_flow(client, first)

        assert first.target_level == 30
        assert second.target_level == 30
        assert _reads(unit).count((REGISTER_MODE_STATUS, 3)) == 1

    async def test_backed_off_status_is_retried_after_the_pause(
        self, client: MeltemModbusClient, unit: MockModbusUnit
    ) -> None:
        _seed(unit)
        _reject_mode_status_read(unit)
        first = await _read_flow(client)
        backoff = client._mode_backoff
        backoff.until = dict.fromkeys(backoff.until, time.monotonic() - 1)

        await _read_flow(client, first)

        assert _reads(unit).count((REGISTER_MODE_STATUS, 3)) == 2

    async def test_a_unit_falling_silent_ends_the_mode_reads(
        self, client: MeltemModbusClient, unit: MockModbusUnit
    ) -> None:
        """Only refused registers (HW-4) back off; a timeout ends the job."""
        _seed(unit, mode=[MODE_MANUAL, 60, 0, 0, 0])
        unit.fail_read(REGISTER_MODE_STATUS, ModbusTimeoutError("silent"))

        state = await _read_flow(client, RoomState(operation_mode="manual"))

        assert _reads(unit) == [
            (REGISTER_EXTRACT_AIR_FLOW, 2),
            (REGISTER_MODE_STATUS, 3),
            (REGISTER_MODE_STATUS, 3),
        ]
        assert state.operation_mode == "manual"
        assert state.read_health_for("flow").consecutive_failures == 0
        assert state.read_health_for("flow_control").consecutive_failures == 1
        assert not client._mode_backoff.is_active((2, "mode_status"))

    def test_backoff_caps_the_failure_counter(self, client: MeltemModbusClient) -> None:
        key = (2, "mode_status")
        client._mode_backoff.failures[key] = 1024

        client._mode_backoff.mark_failure(key, MeltemModbusError("read failed"))

        assert client._mode_backoff.failures[key] == 5
        assert client._mode_backoff.is_active(key)

    async def test_successful_write_clears_the_backoff(
        self, client: MeltemModbusClient
    ) -> None:
        key = (2, "mode_status")
        client._mode_backoff.mark_failure(key, MeltemModbusError("no"))

        await client.write_level(_PLAIN, 40)

        assert not client._mode_backoff.is_active(key)


# ---------------------------------------------------------------------------
#  Operating mode, presets, and intensive
# ---------------------------------------------------------------------------


class TestModeDecoding:
    @pytest.mark.parametrize(
        ("flow", "block", "previous", "expected_preset"),
        [
            pytest.param(
                (30, 30),
                [MODE_MANUAL, 229, 0, 0, 0],
                RoomState(operation_mode="manual"),
                "medium",
                id="app-code",
            ),
            pytest.param(
                (50, 0),
                [MODE_UNBALANCED, 0, 205, 0, 0],
                RoomState(operation_mode="unbalanced"),
                "extract_only",
                id="extract-only-from-the-unbalanced-code",
            ),
            pytest.param(
                (0, 50),
                [MODE_UNBALANCED, 205, 0, 0, 0],
                RoomState(operation_mode="unbalanced"),
                "supply_only",
                id="supply-only-from-the-unbalanced-code",
            ),
            pytest.param(
                (30, 30),
                [MODE_MANUAL, 227, 0, 0, 0],
                RoomState(preset_mode="medium"),
                "medium",
                id="intensive-code-in-a-full-read-keeps-the-base-preset",
            ),
            pytest.param(
                (30, 30),
                [MODE_MANUAL, 228, 0, 0, 0],
                RoomState(preset_mode="intensive"),
                "low",
                id="cleared-shadow-registers-give-the-base-preset",
            ),
        ],
    )
    async def test_preset_is_decoded_from_the_mode_block(
        self,
        client: MeltemModbusClient,
        unit: MockModbusUnit,
        flow: tuple[int, int],
        block: list[int],
        previous: RoomState,
        expected_preset: str,
    ) -> None:
        _seed(unit, flow=flow, mode=block)

        state = await _read_flow(client, previous)

        assert state.preset_mode == expected_preset

    async def test_raw_200_is_not_misdecoded_as_extract_only(
        self, client: MeltemModbusClient, unit: MockModbusUnit
    ) -> None:
        _seed(unit, flow=(100, 0), mode=[MODE_UNBALANCED, 0, 200, 0, 0])

        state = await _read_flow(
            client, RoomState(operation_mode="unbalanced", preset_mode="extract_only")
        )

        assert state.extract_target_level == 100
        assert state.preset_mode is None

    async def test_preset_clears_in_automatic_mode(
        self, client: MeltemModbusClient, unit: MockModbusUnit
    ) -> None:
        _seed(unit, mode=[MODE_SENSOR_CONTROL, MODE_AUTOMATIC_VALUE, 0, 0, 0])

        state = await _read_flow(
            client, RoomState(operation_mode="manual", preset_mode="low"), _FC
        )

        assert state.operation_mode == "automatic"
        assert state.preset_mode is None

    async def test_off_mode_is_decoded(
        self, client: MeltemModbusClient, unit: MockModbusUnit
    ) -> None:
        _seed(unit, flow=(0, 0), mode=[MODE_OFF, 0, 0, 0, 0])

        state = await _read_flow(client, RoomState(operation_mode="manual"))

        assert state.operation_mode == "off"
        assert state.preset_mode is None

    @pytest.mark.parametrize(
        "block",
        [
            pytest.param([MODE_MANUAL, 229, 0, MODE_MANUAL, 227], id="observed-0-0-0"),
            pytest.param([MODE_MANUAL, 227, 0, 0, 0], id="community-3-227"),
        ],
    )
    @pytest.mark.parametrize(
        "previous",
        [
            pytest.param(
                RoomState(
                    operation_mode="manual",
                    target_level=30,
                    balanced_target_readback=30,
                    preset_mode="medium",
                ),
                id="known-base",
            ),
            pytest.param(RoomState(), id="unknown-base"),
        ],
    )
    async def test_intensive_status_keeps_the_last_known_base(
        self,
        client: MeltemModbusClient,
        unit: MockModbusUnit,
        block: list[int],
        previous: RoomState,
    ) -> None:
        _seed(unit, flow=(100, 100), mode=block)

        state = await _read_flow(client, previous)

        base = {
            name: getattr(previous, name)
            for name in (
                "operation_mode",
                "target_level",
                "balanced_target_readback",
                "extract_target_level",
                "preset_mode",
            )
        }
        assert _values(state, base) == base
        assert state.intensive_active is True

    async def test_inactive_intensive_is_reported_as_false(
        self, client: MeltemModbusClient, unit: MockModbusUnit
    ) -> None:
        _seed(unit, mode=[MODE_MANUAL, 229, 0, 0, 0])

        state = await _read_flow(client, RoomState(intensive_active=True))

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
            state = await _read_flow(client, state)

        assert state.preset_mode == "medium"
