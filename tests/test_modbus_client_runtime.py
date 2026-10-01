"""Tests for the MeltemModbusClient lifecycle, error handling, and writes."""

from __future__ import annotations

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


def _writes(unit: MockModbusUnit) -> list[WriteEvent]:
    events: list[WriteEvent] = []
    unit.on_write(events.append)
    return events


def _written(events: list[WriteEvent]) -> list[tuple[int, int]]:
    return [(event.address, event.values[0]) for event in events]


# ---------------------------------------------------------------------------
#  Units and link timing
# ---------------------------------------------------------------------------


class TestUnits:
    async def test_every_unit_asks_for_the_gateway_timing(
        self, client: MeltemModbusClient, link: MockModbusConnection
    ) -> None:
        await client.read_room_state(_ROOM, RoomState(), RefreshPlan.only(refresh_airflow=True))

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
        plan = RefreshPlan.only(refresh_airflow=True)
        for _ in range(2):
            await client.read_room_state(_ROOM, RoomState(), plan)
            await client.read_room_state(_ROOM_S, RoomState(), plan)

        assert requested == [2, 3]

    async def test_answered_reads_update_the_silence_timer(
        self, client: MeltemModbusClient
    ) -> None:
        assert client.seconds_since_successful_read(2) is None

        await client.read_room_state(_ROOM, RoomState(), RefreshPlan.only(refresh_airflow=True))

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

        assert [(e.address, e.count) for e in gateway.read_events] == [
            (REGISTER_GATEWAY_NUMBER_OF_NODES, 1)
        ]

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

        _profile, preview, _keys = await client.probe_slave_details(4)

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

        assert [(e.address, e.count) for e in unit.read_events] == [
            (REGISTER_EXTRACT_AIR_FLOW, 2)
        ] * 2
        assert state.supply_air_flow == 30
        assert state.operation_mode == "manual"
        for group in ("flow", "flow_control", "status", "temperature", "hours"):
            assert state.read_health_for(group).consecutive_failures == 1

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
        plan = RefreshPlan.only(refresh_airflow=True)

        await client.read_room_state(_ROOM, RoomState(), plan)
        state = await client.read_room_state(_ROOM_S, RoomState(), plan)

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
    async def test_zero_level_switches_the_unit_off(
        self, client: MeltemModbusClient, link: MockModbusConnection
    ) -> None:
        events = _writes(link.for_unit(2))

        await client.write_level(_ROOM, 0)

        assert _written(events) == [
            (REGISTER_MODE, MODE_OFF),
            (REGISTER_CURRENT_LEVEL, 0),
            (REGISTER_APPLY, 0),
        ]
        assert {event.function_code for event in events} == {0x06}

    async def test_level_is_scaled_to_the_raw_range(
        self, client: MeltemModbusClient, link: MockModbusConnection
    ) -> None:
        events = _writes(link.for_unit(2))

        await client.write_level(_ROOM, 50)

        assert _written(events) == [
            (REGISTER_MODE, MODE_MANUAL),
            (REGISTER_CURRENT_LEVEL, 100),
            (REGISTER_APPLY, 0),
        ]

    async def test_s_profile_scales_to_its_own_maximum(
        self, client: MeltemModbusClient, link: MockModbusConnection
    ) -> None:
        events = _writes(link.for_unit(3))

        await client.write_level(_ROOM_S, 97)

        assert _written(events)[1] == (REGISTER_CURRENT_LEVEL, 200)

    async def test_a_rejected_write_is_raised(
        self, client: MeltemModbusClient, link: MockModbusConnection
    ) -> None:
        unit = link.for_unit(2)
        events = _writes(unit)
        unit.fail_write(REGISTER_CURRENT_LEVEL, IllegalDataValueError())

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
        unit = link.for_unit(2)
        unit.fail_write(REGISTER_APPLY, ModbusTimeoutError("silent"))

        with pytest.raises(MeltemModbusError):
            await client.write_level(_ROOM, 50)


class TestWriteUnbalancedLevels:
    async def test_writes_mode_and_both_levels(
        self, client: MeltemModbusClient, link: MockModbusConnection
    ) -> None:
        events = _writes(link.for_unit(2))

        await client.write_unbalanced_levels(_ROOM, 60, 40)

        assert _written(events) == [
            (REGISTER_MODE, MODE_UNBALANCED),
            (REGISTER_CURRENT_LEVEL, 120),
            (REGISTER_EXTRACT_AIR_TARGET_LEVEL, 80),
            (REGISTER_APPLY, 0),
        ]

    async def test_raw_levels_are_clamped(
        self, client: MeltemModbusClient, link: MockModbusConnection
    ) -> None:
        events = _writes(link.for_unit(2))

        await client.write_unbalanced_levels(_ROOM, 999, -10)

        assert _written(events)[1:3] == [
            (REGISTER_CURRENT_LEVEL, 200),
            (REGISTER_EXTRACT_AIR_TARGET_LEVEL, 0),
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
        events = _writes(link.for_unit(4))

        await client.write_operating_mode(_ROOM_FC_VOC, operation_mode, 45, 45)

        assert _written(events) == [
            (REGISTER_MODE, MODE_SENSOR_CONTROL),
            (REGISTER_CURRENT_LEVEL, selector),
            (REGISTER_APPLY, 0),
        ]

    async def test_unbalanced_mode_keeps_both_levels(
        self, client: MeltemModbusClient, link: MockModbusConnection
    ) -> None:
        events = _writes(link.for_unit(2))

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
        events = _writes(link.for_unit(2))

        await client.write_operating_mode(_ROOM, "off", 60, 40)

        assert _written(events) == [
            (REGISTER_MODE, MODE_OFF),
            (REGISTER_CURRENT_LEVEL, 0),
            (REGISTER_APPLY, 0),
        ]

    async def test_unknown_mode_is_rejected_before_writing(
        self, client: MeltemModbusClient, link: MockModbusConnection
    ) -> None:
        events = _writes(link.for_unit(2))

        with pytest.raises(MeltemModbusError, match="Unsupported operating mode"):
            await client.write_operating_mode(_ROOM, "turbo", 60, 40)

        assert events == []


class TestWritePresetMode:
    async def test_quick_mode_clears_the_shadow_registers_first(
        self, client: MeltemModbusClient, link: MockModbusConnection
    ) -> None:
        events = _writes(link.for_unit(2))

        await client.write_preset_mode(_ROOM, "medium")

        assert _written(events) == [
            (REGISTER_PRESET_MODE, 0),
            (REGISTER_PRESET_VALUE, 0),
            (REGISTER_MODE, MODE_MANUAL),
            (REGISTER_CURRENT_LEVEL, PRESET_MODE_CODE_MEDIUM),
            (REGISTER_APPLY, 0),
        ]

    async def test_intensive_writes_only_the_shadow_registers(
        self, client: MeltemModbusClient, link: MockModbusConnection
    ) -> None:
        events = _writes(link.for_unit(2))

        await client.write_preset_mode(_ROOM, "intensive")

        assert _written(events) == [
            (REGISTER_PRESET_MODE, MODE_MANUAL),
            (REGISTER_PRESET_VALUE, PRESET_MODE_CODE_INTENSIVE),
            (REGISTER_APPLY, 0),
        ]

    async def test_clearing_intensive_leaves_the_base_mode_alone(
        self, client: MeltemModbusClient, link: MockModbusConnection
    ) -> None:
        events = _writes(link.for_unit(2))

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
        events = _writes(link.for_unit(4))

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
    def test_returns_true_when_no_constraints(self, client: MeltemModbusClient) -> None:
        assert client._supports(_ROOM, "anything")

    def test_returns_true_only_for_listed_keys(self, client: MeltemModbusClient) -> None:
        room = RoomConfig(
            key="r",
            name="R",
            profile="ii_plain",
            slave=2,
            supported_entity_keys=frozenset({"exhaust_temperature"}),
        )
        assert client._supports(room, "exhaust_temperature")
        assert not client._supports(room, "humidity_extract_air")
