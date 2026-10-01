"""Tests for the MeltemModbusClient lifecycle, error handling, and writes."""

from __future__ import annotations

from dataclasses import replace

import pytest
from homeassistant.exceptions import HomeAssistantError
from modbus_connection import (
    IllegalDataValueError,
    ModbusConnectionError,
    ModbusTimeoutError,
)
from modbus_connection.mock import MockModbusConnection, MockModbusUnit, WriteEvent

from custom_components.meltem_ventilation.const import (
    DEFAULT_GATEWAY_DEVICE_ID,
    MODE_AUTOMATIC_VALUE,
    MODE_CO2_CONTROL_VALUE,
    MODE_HUMIDITY_CONTROL_VALUE,
    MODE_MANUAL,
    MODE_OFF,
    MODE_SENSOR_CONTROL,
    MODE_UNBALANCED,
    PRESET_MODE_CODE_INTENSIVE,
    PRESET_MODE_CODE_MEDIUM,
    REGISTER_APPLY,
    REGISTER_CO2_MAX_LEVEL,
    REGISTER_CO2_STARTING_POINT,
    REGISTER_CURRENT_LEVEL,
    REGISTER_EXHAUST_AIR_TEMPERATURE,
    REGISTER_EXTRACT_AIR_FLOW,
    REGISTER_EXTRACT_AIR_TARGET_LEVEL,
    REGISTER_GATEWAY_NODE_ADDRESS_1,
    REGISTER_GATEWAY_NUMBER_OF_NODES,
    REGISTER_HUMIDITY_MAX_LEVEL,
    REGISTER_HUMIDITY_MIN_LEVEL,
    REGISTER_HUMIDITY_STARTING_POINT,
    REGISTER_MODE,
    REGISTER_PRESET_MODE,
    REGISTER_PRESET_VALUE,
    REGISTER_PRODUCT_ID,
)
from custom_components.meltem_ventilation.modbus_client import MeltemModbusClient
from custom_components.meltem_ventilation.modbus_helpers import (
    MeltemConnectionError,
    MeltemModbusError,
)
from custom_components.meltem_ventilation.models import RefreshPlan, RoomConfig, RoomState

_PORT = "/dev/ttyACM0"
_ROOM = RoomConfig(key="unit_1", name="Unit 1", profile="ii_plain", slave=2)
_ROOM_S = RoomConfig(key="unit_s", name="Unit S", profile="s_plain", slave=3)
_ROOM_FC_VOC = RoomConfig(key="unit_v", name="Unit V", profile="ii_fc_voc", slave=4)
_AIRFLOW = RefreshPlan.only(refresh_airflow=True)


@pytest.fixture(name="sleeps")
def sleeps_fixture(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    sleeps: list[float] = []

    async def _sleep(seconds: float) -> None:
        sleeps.append(seconds)

    monkeypatch.setattr(
        "custom_components.meltem_ventilation.device.transport.async_sleep", _sleep
    )
    return sleeps


@pytest.fixture(name="link")
def link_fixture(sleeps: list[float]) -> MockModbusConnection:
    return MockModbusConnection()


@pytest.fixture(name="client")
def client_fixture(link: MockModbusConnection) -> MeltemModbusClient:
    return MeltemModbusClient(link.for_unit, port=_PORT)


def _writes(link: MockModbusConnection, room: RoomConfig) -> list[WriteEvent]:
    events: list[WriteEvent] = []
    link.for_unit(room.slave).on_write(events.append)
    return events


def _written(events: list[WriteEvent]) -> list[tuple[int, int]]:
    return [(event.address, event.values[0]) for event in events]


def _reads(unit: MockModbusUnit) -> list[tuple[int, int]]:
    return [(event.address, event.count) for event in unit.read_events]


# ---------------------------------------------------------------------------
#  Units and link timing
# ---------------------------------------------------------------------------


class TestUnits:
    async def test_every_unit_asks_for_the_gateway_timing(
        self, client: MeltemModbusClient, link: MockModbusConnection
    ) -> None:
        await client.read_room_state(_ROOM, RoomState(), _AIRFLOW)

        unit = link.for_unit(2)
        assert unit.message_spacing == 0.1
        assert unit.required_timeout == 0.8

    async def test_each_unit_is_requested_once(
        self, link: MockModbusConnection
    ) -> None:
        requested: list[int] = []

        def _factory(unit_id: int) -> MockModbusUnit:
            requested.append(unit_id)
            return link.for_unit(unit_id)

        client = MeltemModbusClient(_factory, port=_PORT)
        for _ in range(2):
            await client.read_room_state(_ROOM, RoomState(), _AIRFLOW)
            await client.read_room_state(_ROOM_S, RoomState(), _AIRFLOW)

        assert requested == [2, 3]

    async def test_answered_reads_update_the_silence_timer(
        self, client: MeltemModbusClient
    ) -> None:
        assert client.seconds_since_successful_read(2) is None

        await client.read_room_state(_ROOM, RoomState(), _AIRFLOW)

        assert client.seconds_since_successful_read(2) is not None
        assert client.seconds_since_successful_read(3) is None


# ---------------------------------------------------------------------------
#  Gateway operations
# ---------------------------------------------------------------------------


class TestGatewayOperations:
    async def test_validate_reads_the_gateway_node_count(
        self, client: MeltemModbusClient, link: MockModbusConnection
    ) -> None:
        gateway = link.for_unit(DEFAULT_GATEWAY_DEVICE_ID)
        gateway.holding[REGISTER_GATEWAY_NUMBER_OF_NODES] = 1

        await client.async_validate_gateway()

        assert _reads(gateway) == [(REGISTER_GATEWAY_NUMBER_OF_NODES, 1)]

    async def test_validate_reports_a_silent_gateway(
        self, client: MeltemModbusClient, link: MockModbusConnection
    ) -> None:
        link.for_unit(DEFAULT_GATEWAY_DEVICE_ID).fail_requests(ModbusTimeoutError("silent"))

        with pytest.raises(MeltemModbusError):
            await client.async_validate_gateway()

    async def test_validate_passes_a_link_conflict_through(self) -> None:
        def _conflicting(_unit_id: int) -> MockModbusUnit:
            raise HomeAssistantError("already in use with different link settings")

        client = MeltemModbusClient(_conflicting, port=_PORT)

        with pytest.raises(HomeAssistantError) as err:
            await client.async_validate_gateway()

        assert not isinstance(err.value, MeltemModbusError)

    async def test_discovery_uses_the_gateway_unit(
        self, client: MeltemModbusClient, link: MockModbusConnection
    ) -> None:
        link.for_unit(DEFAULT_GATEWAY_DEVICE_ID).holding.update(
            {REGISTER_GATEWAY_NUMBER_OF_NODES: 2, REGISTER_GATEWAY_NODE_ADDRESS_1: [2, 3]}
        )

        assert await client.discover_gateway_units(2, 16) == [2, 3]

    async def test_probe_uses_the_room_unit(
        self, client: MeltemModbusClient, link: MockModbusConnection
    ) -> None:
        link.for_unit(4).holding[REGISTER_PRODUCT_ID] = [0xC874, 0x0001]

        _profile, preview = await client.probe_slave_details(4)

        assert preview is not None
        assert preview.startswith("ID 116852 |")


# ---------------------------------------------------------------------------
#  Failures while reading
# ---------------------------------------------------------------------------


class TestReadFailures:
    async def test_a_lost_link_is_raised_after_one_paused_retry(
        self,
        client: MeltemModbusClient,
        link: MockModbusConnection,
        sleeps: list[float],
    ) -> None:
        unit = link.for_unit(2)
        unit.fail_requests(ModbusConnectionError("unplugged"))

        with pytest.raises(MeltemConnectionError, match=_PORT):
            await client.read_room_state(_ROOM, RoomState(), RefreshPlan())

        assert len(unit.read_events) == 2
        assert sleeps == [0.5]

    async def test_a_silent_unit_ends_the_job_after_its_first_block(
        self, client: MeltemModbusClient, link: MockModbusConnection
    ) -> None:
        unit = link.for_unit(2)
        unit.fail_requests(ModbusTimeoutError("silent"))
        previous = RoomState(supply_air_flow=30, operation_mode="manual")

        state = await client.read_room_state(_ROOM, previous, RefreshPlan())

        assert _reads(unit) == [(REGISTER_EXTRACT_AIR_FLOW, 2)] * 2
        assert state.supply_air_flow == 30
        assert state.operation_mode == "manual"
        for group in ("flow", "flow_control", "status", "temperature", "hours"):
            assert state.read_health_for(group).consecutive_failures == 1

    async def test_a_unit_falling_silent_mid_job_skips_the_mode_reads(
        self, client: MeltemModbusClient, link: MockModbusConnection
    ) -> None:
        unit = link.for_unit(2)
        unit.holding[REGISTER_EXTRACT_AIR_FLOW] = [40, 40]
        unit.fail_read(REGISTER_EXHAUST_AIR_TEMPERATURE, ModbusTimeoutError("silent"))

        state = await client.read_room_state(_ROOM, RoomState(), RefreshPlan())

        assert state.supply_air_flow == 40
        assert all(address != REGISTER_MODE for address, _count in _reads(unit))
        assert state.read_health_for("flow_control").consecutive_failures == 1

    async def test_silent_units_do_not_recycle_the_link(
        self, client: MeltemModbusClient, link: MockModbusConnection
    ) -> None:
        for slave in (2, 3):
            link.for_unit(slave).fail_requests(ModbusTimeoutError("silent"))

        for room in (_ROOM, _ROOM_S):
            await client.read_room_state(room, RoomState(), RefreshPlan())

        assert client.transport_diagnostics()["link_recycles"] == 0

    async def test_a_silent_unit_does_not_affect_its_neighbours(
        self, client: MeltemModbusClient, link: MockModbusConnection
    ) -> None:
        link.for_unit(2).fail_requests(ModbusTimeoutError("silent"))
        link.for_unit(3).holding[REGISTER_EXTRACT_AIR_FLOW] = [40, 40]

        await client.read_room_state(_ROOM, RoomState(), _AIRFLOW)
        state = await client.read_room_state(_ROOM_S, RoomState(), _AIRFLOW)

        assert state.supply_air_flow == 40
        assert state.read_health_for("flow").consecutive_failures == 0

    async def test_shutdown_rejects_later_operations(
        self, client: MeltemModbusClient, link: MockModbusConnection
    ) -> None:
        client.shutdown()

        with pytest.raises(MeltemConnectionError, match="shut down"):
            await client.read_room_state(_ROOM, RoomState(), RefreshPlan())
        with pytest.raises(MeltemConnectionError, match="shut down"):
            await client.write_level(_ROOM, 40)

        assert link.for_unit(2).read_events == []


# ---------------------------------------------------------------------------
#  Writes
# ---------------------------------------------------------------------------


class TestWriteLevel:
    @pytest.mark.parametrize(
        ("room", "level", "expected"),
        [
            pytest.param(
                _ROOM,
                0,
                [(REGISTER_MODE, MODE_OFF), (REGISTER_CURRENT_LEVEL, 0), (REGISTER_APPLY, 0)],
                id="zero-switches-the-unit-off",
            ),
            pytest.param(
                _ROOM,
                50,
                [(REGISTER_MODE, MODE_MANUAL), (REGISTER_CURRENT_LEVEL, 100), (REGISTER_APPLY, 0)],
                id="scaled-to-the-raw-range",
            ),
            pytest.param(
                _ROOM_S,
                97,
                [(REGISTER_MODE, MODE_MANUAL), (REGISTER_CURRENT_LEVEL, 200), (REGISTER_APPLY, 0)],
                id="s-profile-scales-to-its-own-maximum",
            ),
        ],
    )
    async def test_writes_mode_level_and_apply(
        self,
        client: MeltemModbusClient,
        link: MockModbusConnection,
        room: RoomConfig,
        level: int,
        expected: list[tuple[int, int]],
    ) -> None:
        events = _writes(link, room)

        await client.write_level(room, level)

        assert _written(events) == expected
        # The manuals only list single-register writes (0x06).
        assert {event.function_code for event in events} == {0x06}

    async def test_a_rejected_write_is_raised(
        self, client: MeltemModbusClient, link: MockModbusConnection
    ) -> None:
        events = _writes(link, _ROOM)
        link.for_unit(2).fail_write(REGISTER_CURRENT_LEVEL, IllegalDataValueError())

        with pytest.raises(MeltemModbusError):
            await client.write_level(_ROOM, 50)

        # The APPLY latch must not fire after a half-written request.
        assert _written(events) == [(REGISTER_MODE, MODE_MANUAL)]

    async def test_an_unanswered_write_is_retried_once(
        self, client: MeltemModbusClient, link: MockModbusConnection
    ) -> None:
        unit = link.for_unit(2)
        attempts: list[int] = []
        write_register = unit.write_register

        async def _lose_the_first_apply(address: int, value: int) -> None:
            attempts.append(address)
            if address == REGISTER_APPLY and attempts.count(REGISTER_APPLY) == 1:
                raise ModbusTimeoutError("lost")
            await write_register(address, value)

        unit.write_register = _lose_the_first_apply

        await client.write_level(_ROOM, 50)

        assert attempts == [
            REGISTER_MODE,
            REGISTER_CURRENT_LEVEL,
            REGISTER_APPLY,
            REGISTER_APPLY,
        ]

    async def test_a_write_unanswered_twice_is_raised(
        self, client: MeltemModbusClient, link: MockModbusConnection
    ) -> None:
        link.for_unit(2).fail_write(REGISTER_APPLY, ModbusTimeoutError("silent"))

        with pytest.raises(MeltemModbusError):
            await client.write_level(_ROOM, 50)


class TestWriteUnbalancedLevels:
    @pytest.mark.parametrize(
        ("supply", "extract", "raw_supply", "raw_extract"),
        [(60, 40, 120, 80), (999, -10, 200, 0)],
        ids=("scaled", "clamped"),
    )
    async def test_writes_mode_and_both_levels(
        self,
        client: MeltemModbusClient,
        link: MockModbusConnection,
        supply: int,
        extract: int,
        raw_supply: int,
        raw_extract: int,
    ) -> None:
        events = _writes(link, _ROOM)

        await client.write_unbalanced_levels(_ROOM, supply, extract)

        assert _written(events) == [
            (REGISTER_MODE, MODE_UNBALANCED),
            (REGISTER_CURRENT_LEVEL, raw_supply),
            (REGISTER_EXTRACT_AIR_TARGET_LEVEL, raw_extract),
            (REGISTER_APPLY, 0),
        ]


class TestWriteOperatingMode:
    @pytest.mark.parametrize(
        ("operation_mode", "selector"),
        [
            ("humidity_control", MODE_HUMIDITY_CONTROL_VALUE),
            ("co2_control", MODE_CO2_CONTROL_VALUE),
            ("automatic", MODE_AUTOMATIC_VALUE),
        ],
    )
    async def test_sensor_modes_write_the_selector(
        self,
        client: MeltemModbusClient,
        link: MockModbusConnection,
        operation_mode: str,
        selector: int,
    ) -> None:
        events = _writes(link, _ROOM_FC_VOC)

        await client.write_operating_mode(_ROOM_FC_VOC, operation_mode, 45, 45)

        assert _written(events) == [
            (REGISTER_MODE, MODE_SENSOR_CONTROL),
            (REGISTER_CURRENT_LEVEL, selector),
            (REGISTER_APPLY, 0),
        ]

    async def test_unbalanced_mode_keeps_both_levels(
        self, client: MeltemModbusClient, link: MockModbusConnection
    ) -> None:
        events = _writes(link, _ROOM)

        await client.write_operating_mode(_ROOM, "unbalanced", 60, 40)

        assert _written(events) == [
            (REGISTER_MODE, MODE_UNBALANCED),
            (REGISTER_CURRENT_LEVEL, 120),
            (REGISTER_EXTRACT_AIR_TARGET_LEVEL, 80),
            (REGISTER_APPLY, 0),
        ]

    async def test_off_mode_clears_the_level(
        self, client: MeltemModbusClient, link: MockModbusConnection
    ) -> None:
        events = _writes(link, _ROOM)

        await client.write_operating_mode(_ROOM, "off", 60, 40)

        assert _written(events) == [
            (REGISTER_MODE, MODE_OFF),
            (REGISTER_CURRENT_LEVEL, 0),
            (REGISTER_APPLY, 0),
        ]

    async def test_manual_mode_writes_the_balanced_level(
        self, client: MeltemModbusClient, link: MockModbusConnection
    ) -> None:
        events = _writes(link, _ROOM)

        await client.write_operating_mode(_ROOM, "manual", 60, 40)

        assert _written(events) == [
            (REGISTER_MODE, MODE_MANUAL),
            (REGISTER_CURRENT_LEVEL, 120),
            (REGISTER_APPLY, 0),
        ]

    async def test_unknown_mode_is_rejected_before_writing(
        self, client: MeltemModbusClient, link: MockModbusConnection
    ) -> None:
        events = _writes(link, _ROOM)

        with pytest.raises(MeltemModbusError, match="Unsupported operating mode"):
            await client.write_operating_mode(_ROOM, "turbo", 60, 40)

        assert events == []


class TestWritePresetMode:
    @pytest.mark.parametrize(
        ("preset_mode", "expected"),
        [
            pytest.param(
                "medium",
                [
                    (REGISTER_PRESET_MODE, 0),
                    (REGISTER_PRESET_VALUE, 0),
                    (REGISTER_MODE, MODE_MANUAL),
                    (REGISTER_CURRENT_LEVEL, PRESET_MODE_CODE_MEDIUM),
                    (REGISTER_APPLY, 0),
                ],
                id="quick-mode-clears-the-shadow-registers-first",
            ),
            pytest.param(
                "intensive",
                [
                    (REGISTER_PRESET_MODE, MODE_MANUAL),
                    (REGISTER_PRESET_VALUE, PRESET_MODE_CODE_INTENSIVE),
                    (REGISTER_APPLY, 0),
                ],
                id="intensive-writes-only-the-shadow-registers",
            ),
        ],
    )
    async def test_writes_the_preset_sequence(
        self,
        client: MeltemModbusClient,
        link: MockModbusConnection,
        preset_mode: str,
        expected: list[tuple[int, int]],
    ) -> None:
        events = _writes(link, _ROOM)

        await client.write_preset_mode(_ROOM, preset_mode)

        assert _written(events) == expected

    async def test_clearing_intensive_leaves_the_base_mode_alone(
        self, client: MeltemModbusClient, link: MockModbusConnection
    ) -> None:
        events = _writes(link, _ROOM)

        await client.clear_intensive(_ROOM)

        assert _written(events) == [
            (REGISTER_PRESET_MODE, 0),
            (REGISTER_PRESET_VALUE, 0),
            (REGISTER_APPLY, 0),
        ]

    async def test_unknown_preset_is_rejected(self, client: MeltemModbusClient) -> None:
        with pytest.raises(MeltemModbusError, match="Unsupported preset mode"):
            await client.write_preset_mode(_ROOM, "turbo")


class TestWriteControlSetting:
    @pytest.mark.parametrize(
        ("setting_key", "value", "register", "expected"),
        [
            ("humidity_starting_point", 50, REGISTER_HUMIDITY_STARTING_POINT, 50),
            ("co2_starting_point", 850, REGISTER_CO2_STARTING_POINT, 850),
            ("humidity_starting_point", 99, REGISTER_HUMIDITY_STARTING_POINT, 80),
            ("humidity_max_level", 0, REGISTER_HUMIDITY_MAX_LEVEL, 10),
            ("co2_starting_point", 1800, REGISTER_CO2_STARTING_POINT, 1200),
            ("co2_max_level", 0, REGISTER_CO2_MAX_LEVEL, 10),
            ("humidity_min_level", 15, REGISTER_HUMIDITY_MIN_LEVEL, 20),
        ],
        ids=[
            "humidity",
            "co2",
            "humidity-upper-bound",
            "humidity-lower-bound",
            "co2-upper-bound",
            "co2-lower-bound",
            "step",
        ],
    )
    async def test_writes_the_clamped_and_stepped_value(
        self,
        client: MeltemModbusClient,
        link: MockModbusConnection,
        setting_key: str,
        value: int,
        register: int,
        expected: int,
    ) -> None:
        events = _writes(link, _ROOM_FC_VOC)

        written = await client.write_control_setting(_ROOM_FC_VOC, setting_key, value)

        assert written == expected
        assert _written(events) == [(register, expected)]

    async def test_unknown_setting_is_rejected(self, client: MeltemModbusClient) -> None:
        with pytest.raises(MeltemModbusError, match="Unsupported control setting"):
            await client.write_control_setting(_ROOM_FC_VOC, "turbo", 1)


# ---------------------------------------------------------------------------
#  Small helpers
# ---------------------------------------------------------------------------


class TestSupportsHelper:
    @pytest.mark.parametrize(
        ("supported", "key", "expected"),
        [
            (None, "anything", True),
            (frozenset({"exhaust_temperature"}), "exhaust_temperature", True),
            (frozenset({"exhaust_temperature"}), "humidity_extract_air", False),
        ],
        ids=("unconstrained", "listed", "not-listed"),
    )
    def test_only_listed_keys_are_supported(
        self, supported: frozenset[str] | None, key: str, expected: bool
    ) -> None:
        room = replace(_ROOM, supported_entity_keys=supported)

        assert room.supports(key) is expected
