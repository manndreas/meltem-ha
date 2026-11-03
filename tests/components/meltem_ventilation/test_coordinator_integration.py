"""Tests for coordinator update cycle, first refresh, and error handling."""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from functools import partial
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.core import CoreState, HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.event import async_call_later, async_track_time_interval
from homeassistant.helpers.update_coordinator import UpdateFailed
from homeassistant.util import dt as dt_util
from modbus_connection import (
    AcknowledgeError,
    GatewayPathUnavailableError,
    GatewayTargetError,
    IllegalDataAddressError,
    ModbusProtocolError,
    ModbusTimeoutError,
)
from modbus_connection.mock import MockModbusConnection, MockModbusUnit, WriteEvent
from pytest_homeassistant.common import MockConfigEntry, async_fire_time_changed

from custom_components.meltem_ventilation.binary_sensor import (
    BINARY_SENSOR_DESCRIPTIONS,
    MeltemBinarySensorEntity,
    MeltemDataHealthBinarySensor,
)
from custom_components.meltem_ventilation.const import (
    DOMAIN,
    REGISTER_CO2_EXTRACT_AIR,
    REGISTER_ERROR_STATUS,
    REGISTER_EXTRACT_AIR_FLOW,
    REGISTER_EXTRACT_AIR_TEMPERATURE,
    REGISTER_HUMIDITY_EXTRACT_AIR,
    REGISTER_HUMIDITY_SUPPLY_AIR,
    REGISTER_MODE_STATUS,
    REGISTER_RF_COMM_STATUS,
    REGISTER_RF_FAULT_STATUS,
    REGISTER_RF_LAST_SEEN_AGE,
    REGISTER_RF_SIGNAL_STRENGTH,
    REGISTER_SUPPLY_AIR_TEMPERATURE,
    REGISTER_VALUE_ERROR_STATUS,
    TRANSPORT_READ_RECOVERY_SECONDS,
    WRITE_HEALTH_RETENTION_SECONDS,
)
from custom_components.meltem_ventilation.coordinator import (
    LOCAL_DIAGNOSTIC_REFRESH_INTERVAL,
    TRANSPORT_BACKOFF_MAX_SECONDS,
    TRANSPORT_BACKOFF_START_SECONDS,
    UNREAD_GROUP_RETRY_SECONDS,
    MeltemDataUpdateCoordinator,
)
from custom_components.meltem_ventilation.cumulative_diagnostics import CumulativeDiagnostics
from custom_components.meltem_ventilation.missing_sensors import MISSING_SENSOR_ISSUE_AFTER_READS
from custom_components.meltem_ventilation.modbus_client import MeltemModbusClient
from custom_components.meltem_ventilation.modbus_helpers import (
    MeltemModbusError,
    supported_entity_keys_for_profile,
)
from custom_components.meltem_ventilation.models import (
    GatewayRfStatistics,
    ReadHealth,
    ReadJobOutcome,
    RefreshPlan,
    RoomConfig,
    RoomRead,
    RoomState,
    UnitProbeResult,
)
from custom_components.meltem_ventilation.new_units import (
    NEW_UNIT_CHECK_FIRST_DELAY_SECONDS,
    NEW_UNIT_CHECK_SECONDS,
)
from custom_components.meltem_ventilation.polling import (
    AIRFLOW_REFRESH_PLAN,
    FULL_REFRESH_PLAN,
    JOB_GROUPS,
    JobGroup,
    PollJob,
    room_needs_job,
)

from .common import allow_next_read, run_job, run_tick, skip_startup

# ---------------------------------------------------------------------------
#  Helpers
# ---------------------------------------------------------------------------

_ROOM_1 = RoomConfig(key="unit_1", name="Unit 1", profile="ii_plain", slave=2)
_ROOM_2 = RoomConfig(key="unit_2", name="Unit 2", profile="ii_fc", slave=3)
_TEST_GATEWAY_MAC = "02:00:00:00:00:01"


class _FakeClient:
    """Stand-in for MeltemModbusClient with no serial port."""

    def __init__(self) -> None:
        self.read_calls: list[tuple[str, RefreshPlan]] = []
        self.next_read_state = RoomState(target_level=42)
        self.fresh_read_groups: tuple[str, ...] = ()
        self.target_read_calls: list[str] = []
        self.next_balanced_target_readback: int | None = 42
        self.target_read_error: MeltemModbusError | None = None
        self.fail_rooms: set[str] = set()
        self.last_request_rate: float | None = None
        self.uptime_calls = 0
        self.uptime_error: MeltemModbusError | None = None
        self.gateway_mac_calls = 0
        self.utc_clock_calls = 0
        self.utc_clock_error: MeltemModbusError | None = None
        self.node_count_calls = 0
        self.node_count = 6
        self.node_count_error: MeltemModbusError | None = None
        self.rf_statistics_calls = 0
        self.rf_statistics_error: MeltemModbusError | None = None
        self.external_input_calls: list[tuple[str, str]] = []
        self.external_input_error: MeltemModbusError | None = None

    async def async_disconnect_gateway(self) -> None:
        raise AssertionError("Gateway disconnection is not expected in polling tests")

    async def async_read_gateway_uptime(self) -> int:
        self.uptime_calls += 1
        if self.uptime_error is not None:
            raise self.uptime_error
        return 123

    async def async_read_gateway_mac(self) -> str:
        self.gateway_mac_calls += 1
        return _TEST_GATEWAY_MAC

    async def async_read_gateway_utc_clock(self) -> int:
        self.utc_clock_calls += 1
        if self.utc_clock_error is not None:
            raise self.utc_clock_error
        return 1792580112

    async def async_read_gateway_node_count(self) -> int:
        self.node_count_calls += 1
        if self.node_count_error is not None:
            raise self.node_count_error
        return self.node_count

    def transport_diagnostics(self) -> dict[str, float | int | None]:
        raise AssertionError("Transport diagnostics are not expected in coordinator polling tests")

    async def async_read_gateway_rf_statistics(self) -> GatewayRfStatistics:
        self.rf_statistics_calls += 1
        if self.rf_statistics_error is not None:
            raise self.rf_statistics_error
        return GatewayRfStatistics(75, 147, 0.260866672, 0.035489265)

    async def async_read_room_app_setup_code(self, room: RoomConfig) -> str | None:
        raise AssertionError("App setup code reads are not expected in coordinator polling tests")

    async def async_read_external_input_setting(self, room: RoomConfig, key: str) -> int:
        self.external_input_calls.append((room.key, key))
        if self.external_input_error is not None:
            raise self.external_input_error
        return 60

    async def discover_gateway_units(self, start: int, end: int) -> list[int]:
        raise AssertionError("Gateway discovery is not expected in coordinator polling tests")

    async def probe_slave_details(self, slave: int) -> UnitProbeResult:
        raise AssertionError("Gateway probing is not expected in coordinator polling tests")

    async def read_room(
        self,
        room: RoomConfig,
        previous_state: RoomState,
        refresh_plan: RefreshPlan,
    ) -> RoomRead:
        self.read_calls.append((room.key, refresh_plan))
        if room.key in self.fail_rooms:
            raise MeltemModbusError(f"boom: {room.key}")
        state = self.next_read_state
        read_at = dt_util.utcnow()
        groups = [group for group in self.fresh_read_groups if group in refresh_plan.read_groups()]
        for group in groups:
            state = state.with_read_health(
                group, ReadHealth(last_attempt=read_at, last_successful_read=read_at)
            )
        return _read_result(state, groups)

    async def read_balanced_target_readback(self, room: RoomConfig) -> int | None:
        self.target_read_calls.append(room.key)
        if self.target_read_error is not None:
            raise self.target_read_error
        return self.next_balanced_target_readback

    def seconds_since_successful_read(self, slave: int) -> float | None:
        return None

    def read_quarantined(self, slave: int) -> bool:
        return False

    async def write_level(self, room: RoomConfig, level: int) -> None:
        raise AssertionError("Writes are not expected in coordinator polling tests")

    async def write_unbalanced_levels(
        self, room: RoomConfig, supply_level: int, extract_level: int
    ) -> None:
        raise AssertionError("Writes are not expected in coordinator polling tests")

    async def write_operating_mode(
        self, room: RoomConfig, operation_mode: str, balanced_level: int, extract_level: int
    ) -> None:
        raise AssertionError("Writes are not expected in coordinator polling tests")

    async def write_preset_mode(self, room: RoomConfig, preset_mode: str) -> None:
        raise AssertionError("Writes are not expected in coordinator polling tests")

    async def clear_intensive(self, room: RoomConfig) -> None:
        raise AssertionError("Writes are not expected in coordinator polling tests")

    async def write_control_setting(self, room: RoomConfig, setting_key: str, value: int) -> int:
        raise AssertionError("Writes are not expected in coordinator polling tests")

    def update_request_rate(self, max_requests_per_second: float) -> None:
        self.last_request_rate = max_requests_per_second

    def shutdown(self) -> None:
        pass


@pytest.fixture(autouse=True)
def _skip_settle_delays() -> Iterator[None]:
    with patch("custom_components.meltem_ventilation.coordinator.async_sleep", new=AsyncMock()):
        yield


def _build(
    hass: HomeAssistant,
    *rooms: RoomConfig,
    max_requests_per_second: float = 2.0,
) -> tuple[MeltemDataUpdateCoordinator, _FakeClient]:
    entry = MockConfigEntry(domain=DOMAIN, title="Meltem", version=1, source="user")
    entry.add_to_hass(hass)
    client = _FakeClient()
    coordinator = MeltemDataUpdateCoordinator(
        hass,
        config_entry=entry,
        client=client,
        rooms=list(rooms) or [_ROOM_1],
        max_requests_per_second=max_requests_per_second,
    )
    return coordinator, client


def _build_with_fresh_airflow(
    hass: HomeAssistant,
) -> tuple[MeltemDataUpdateCoordinator, _FakeClient]:
    coordinator, client = _build(hass)
    client.fresh_read_groups = ("flow", "flow_control")
    return coordinator, client


async def test_gateway_mac_read_holds_shared_lock(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    coordinator, client = _build(hass)
    original_read = client.async_read_gateway_mac

    async def assert_lock_held() -> str:
        assert coordinator._gateway_lock.locked()
        return await original_read()

    monkeypatch.setattr(client, "async_read_gateway_mac", assert_lock_held)

    assert await coordinator.async_read_gateway_mac() == _TEST_GATEWAY_MAC
    assert client.gateway_mac_calls == 1
    assert not coordinator._gateway_lock.locked()
    await coordinator.async_shutdown()


def _update_interval_seconds(coordinator: MeltemDataUpdateCoordinator) -> float:
    return coordinator.next_poll_delay


def _startup_job_count(coordinator: MeltemDataUpdateCoordinator) -> int:
    startup_jobs = coordinator._startup_jobs
    assert startup_jobs is not None
    return len(startup_jobs)


def _set_jobs_due(coordinator: MeltemDataUpdateCoordinator, next_due: float) -> None:
    for job in coordinator.poll_jobs:
        job.next_due = next_due


def _due_job(group_key: str, room_key: str = "unit_1") -> PollJob:
    group = next(group for group in JOB_GROUPS if group.key == group_key)
    return PollJob(group.key, room_key, group.refresh_plan, group.interval_seconds, 0.0)


def _read_ok(at: datetime) -> ReadHealth:
    return ReadHealth(last_attempt=at, last_successful_read=at)


def _read_result(
    state: RoomState,
    ok: Iterable[str] = (),
    *,
    failed: Mapping[str, str] | None = None,
    answered: bool = True,
) -> RoomRead:
    """Return a room read whose ``ok`` groups the unit answered and ``failed`` groups did not."""

    errors = dict(failed or {})
    recorded = frozenset(ok) | frozenset(errors)
    return RoomRead(
        state,
        ReadJobOutcome(
            attempted_groups=recorded,
            recorded_groups=recorded,
            errors=errors,
            answered=answered and bool(recorded),
            unit_success_groups=frozenset(ok),
        ),
    )


def _fresh_manual_state() -> RoomState:
    return RoomState(operation_mode="manual").with_read_health(
        "flow_control", _read_ok(dt_util.utcnow())
    )


def _with_health(state: RoomState, group_keys: Iterable[str], health: ReadHealth) -> RoomState:
    for group_key in group_keys:
        state = state.with_read_health(group_key, health)
    return state


# ---------------------------------------------------------------------------
#  Progressive startup
# ---------------------------------------------------------------------------


@pytest.fixture(name="clock")
def clock_fixture(monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    clock = SimpleNamespace(now=time.monotonic())
    for module in ("coordinator", "gateway_reads"):
        monkeypatch.setattr(
            f"custom_components.meltem_ventilation.{module}.time",
            SimpleNamespace(monotonic=lambda: clock.now),
        )
    return clock


class TestGatewayRfStatistics:
    async def test_the_gateway_unit_list_is_checked_for_units_not_set_up(
        self, hass: HomeAssistant, clock: SimpleNamespace
    ) -> None:
        coordinator, client = _build(hass)
        discover = AsyncMock(return_value=[2, 3])
        client.discover_gateway_units = discover  # type: ignore[method-assign]
        coordinator.data = {"unit_1": RoomState(target_level=30)}
        skip_startup(coordinator)
        _set_jobs_due(coordinator, clock.now + 99_999)
        issue_id = f"new_units_{coordinator.entry_id}"
        issues = ir.async_get(hass)

        await run_tick(coordinator)
        # The first check waits until the startup reads are through.
        discover.assert_not_awaited()

        clock.now += NEW_UNIT_CHECK_FIRST_DELAY_SECONDS
        await run_tick(coordinator)

        discover.assert_awaited_once_with(2, 16)
        issue = issues.async_get_issue(DOMAIN, issue_id)
        assert issue is not None
        assert issue.translation_placeholders == {"count": "1", "addresses": "3"}

        clock.now += NEW_UNIT_CHECK_SECONDS - 1
        await run_tick(coordinator)
        assert discover.await_count == 1

        await coordinator.async_shutdown()
        # Unloading keeps the issue, so a user's "ignore" survives a reload.
        assert issues.async_get_issue(DOMAIN, issue_id) is not None

    async def test_shared_optional_reads_and_staleness(
        self, hass: HomeAssistant, clock: SimpleNamespace
    ) -> None:
        coordinator, client = _build(hass)
        await coordinator.async_refresh()
        assert client.rf_statistics_calls == 0
        assert coordinator.gateway_rf_statistics is None
        coordinator.set_gateway_rf_statistics_enabled("messages", True)
        coordinator.set_gateway_rf_statistics_enabled("load", True)
        await coordinator.async_refresh()
        assert client.rf_statistics_calls == 1
        assert coordinator.gateway_rf_statistics == GatewayRfStatistics(
            75, 147, 0.260866672, 0.035489265
        )
        coordinator.set_gateway_rf_statistics_enabled("messages", False)
        clock.now += 60
        await coordinator.async_refresh()
        assert client.rf_statistics_calls == 2
        client.rf_statistics_error = MeltemModbusError("RF statistics unavailable")
        clock.now += 60
        await coordinator.async_refresh()
        assert coordinator.gateway_rf_statistics is not None
        assert coordinator.last_update_success
        assert coordinator.room_available("unit_1")
        clock.now += 121
        assert coordinator.gateway_rf_statistics is None
        coordinator.set_gateway_rf_statistics_enabled("load", False)
        await coordinator.async_refresh()
        assert client.rf_statistics_calls == 3
        await coordinator.async_shutdown()

    @pytest.mark.parametrize("shutdown", [False, True])
    async def test_disable_or_shutdown_cancels_read(
        self, hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch, shutdown: bool
    ) -> None:
        coordinator, client = _build(hass)
        started = asyncio.Event()

        async def wait_for_response() -> GatewayRfStatistics:
            started.set()
            await asyncio.Future()
            return GatewayRfStatistics(0, 0, 0.0, 0.0)

        monkeypatch.setattr(client, "async_read_gateway_rf_statistics", wait_for_response)
        coordinator.set_gateway_rf_statistics_enabled("messages", True)
        refresh = asyncio.create_task(coordinator._async_update_data())
        await started.wait()
        assert coordinator._gateway_lock.locked()
        if shutdown:
            await coordinator.async_shutdown()
        else:
            coordinator.set_gateway_rf_statistics_enabled("messages", False)
        await refresh
        assert not coordinator.optional_gateway_reads["rf_statistics"].consumers
        assert not coordinator.optional_gateway_reads["rf_statistics"].reading
        await coordinator.async_shutdown()


class TestExternalInputDiagnostics:
    async def test_only_enabled_settings_are_read_one_per_tick(
        self, hass: HomeAssistant, clock: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        coordinator, client = _build(hass, _ROOM_1, _ROOM_2)
        original_read = client.async_read_external_input_setting

        async def read_with_lock(room: RoomConfig, key: str) -> int:
            assert coordinator._gateway_lock.locked()
            return await original_read(room, key)

        monkeypatch.setattr(client, "async_read_external_input_setting", read_with_lock)
        coordinator.data = {
            "unit_1": RoomState(target_level=30),
            "unit_2": RoomState(target_level=30),
        }
        skip_startup(coordinator)
        _set_jobs_due(coordinator, clock.now + 99_999)
        coordinator._gateway_units.set_enabled("new_unit_issue", False)
        await coordinator.async_refresh()
        assert not client.external_input_calls
        assert coordinator.optional_room_reads == {}

        coordinator.set_external_input_enabled("unit_1", "external_input_level", True)
        coordinator.set_external_input_enabled("unit_1", "external_input_on_delay", True)
        coordinator.set_external_input_enabled("unit_2", "external_input_run_on", True)
        for expected_count in range(1, 4):
            clock.now += 1
            await coordinator.async_refresh()
            assert len(client.external_input_calls) == expected_count
        assert client.external_input_calls == [
            ("unit_1", "external_input_level"),
            ("unit_1", "external_input_on_delay"),
            ("unit_2", "external_input_run_on"),
        ]
        assert coordinator.external_input_value("unit_1", "external_input_level") == 60
        coordinator.set_external_input_enabled("unit_1", "external_input_level", False)
        assert coordinator.external_input_value("unit_1", "external_input_level") is None
        coordinator.set_external_input_enabled("unit_1", "external_input_level", True)
        assert coordinator.external_input_value("unit_1", "external_input_level") is None
        clock.now += 1
        await coordinator.async_refresh()
        assert client.external_input_calls[-1] == ("unit_1", "external_input_level")
        await coordinator.async_shutdown()

    async def test_failure_is_isolated_and_value_expires(
        self, hass: HomeAssistant, clock: SimpleNamespace
    ) -> None:
        coordinator, client = _build(hass)
        coordinator.data = {"unit_1": RoomState(target_level=30)}
        skip_startup(coordinator)
        _set_jobs_due(coordinator, clock.now + 99_999)
        coordinator._gateway_units.set_enabled("new_unit_issue", False)
        coordinator.set_external_input_enabled("unit_1", "external_input_level", True)
        await coordinator.async_refresh()
        assert coordinator.external_input_value("unit_1", "external_input_level") == 60

        client.external_input_error = MeltemModbusError("unsupported")
        clock.now += 3600
        await coordinator.async_refresh()
        assert coordinator.last_update_success
        assert coordinator.external_input_value("unit_1", "external_input_level") == 60
        clock.now += 7201
        assert coordinator.external_input_value("unit_1", "external_input_level") is None
        await coordinator.async_shutdown()

    async def test_optional_read_updates_its_room_alongside_another_rooms_job(
        self, hass: HomeAssistant, clock: SimpleNamespace
    ) -> None:
        coordinator, client = _build(hass, _ROOM_1, _ROOM_2)
        coordinator.data = {
            "unit_1": RoomState(target_level=30),
            "unit_2": RoomState(target_level=30),
        }
        client.fresh_read_groups = ("flow", "flow_control")
        skip_startup(coordinator)
        _set_jobs_due(coordinator, clock.now + 99_999)
        next(job for job in coordinator.poll_jobs if job.room_key == "unit_1").next_due = clock.now
        coordinator.set_external_input_enabled("unit_2", "external_input_level", True)
        seen: list[tuple[bool, bool]] = []
        coordinator.async_add_listener(
            lambda: seen.append(
                (coordinator.room_updated("unit_1"), coordinator.room_updated("unit_2"))
            )
        )

        await coordinator.async_refresh()

        assert seen == [(True, True)]
        assert client.external_input_calls == [("unit_2", "external_input_level")]
        await coordinator.async_shutdown()

    async def test_disabling_and_reenabling_during_a_read_retries_immediately(
        self, hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch, clock: SimpleNamespace
    ) -> None:
        coordinator, client = _build(hass)
        coordinator.data = {"unit_1": RoomState(target_level=30)}
        skip_startup(coordinator)
        _set_jobs_due(coordinator, clock.now + 99_999)
        started = asyncio.Event()
        calls = 0

        async def read(_room: RoomConfig, _key: str) -> int:
            nonlocal calls
            calls += 1
            if calls == 1:
                started.set()
                await asyncio.Future()
            return 60

        monkeypatch.setattr(client, "async_read_external_input_setting", read)
        coordinator.set_external_input_enabled("unit_1", "external_input_level", True)
        refresh = asyncio.create_task(coordinator.async_refresh())
        await started.wait()
        coordinator.set_external_input_enabled("unit_1", "external_input_level", False)
        coordinator.set_external_input_enabled("unit_1", "external_input_level", True)
        await refresh

        assert coordinator.last_update_success
        assert coordinator.external_input_value("unit_1", "external_input_level") is None
        clock.now += 1
        await coordinator.async_refresh()
        assert calls == 2
        assert coordinator.external_input_value("unit_1", "external_input_level") == 60
        await coordinator.async_shutdown()


class TestGatewayConnectedDevices:
    async def test_gateway_count_is_independent_of_configured_rooms(
        self, hass: HomeAssistant, clock: SimpleNamespace
    ) -> None:
        coordinator, client = _build(hass)
        await coordinator.async_refresh()
        assert client.node_count_calls == 0
        assert coordinator.gateway_connected_devices is None
        coordinator.set_gateway_connected_devices_enabled(True)
        await coordinator.async_refresh()
        assert len(coordinator.rooms) == 1
        assert coordinator.gateway_connected_devices == 6
        assert client.node_count_calls == 1
        clock.now += 1
        await coordinator.async_refresh()
        assert client.node_count_calls == 1
        clock.now += 60
        client.node_count = 0
        await coordinator.async_refresh()
        assert coordinator.gateway_connected_devices == 0
        assert client.node_count_calls == 2
        clock.now += 60
        client.node_count_error = MeltemModbusError("count unavailable")
        await coordinator.async_refresh()
        assert coordinator.gateway_connected_devices == 0
        assert coordinator.last_update_success
        assert coordinator.room_available("unit_1")
        clock.now += 121
        assert coordinator.gateway_connected_devices is None
        coordinator.set_gateway_connected_devices_enabled(False)
        await coordinator.async_refresh()
        assert client.node_count_calls == 3
        await coordinator.async_shutdown()

    @pytest.mark.parametrize("shutdown", [False, True])
    async def test_disable_or_shutdown_cancels_count_read(
        self, hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch, shutdown: bool
    ) -> None:
        coordinator, client = _build(hass)
        started = asyncio.Event()

        async def wait_for_response() -> int:
            started.set()
            await asyncio.Future()
            return 0

        monkeypatch.setattr(client, "async_read_gateway_node_count", wait_for_response)
        coordinator.set_gateway_connected_devices_enabled(True)
        refresh = asyncio.create_task(coordinator._async_update_data())
        await started.wait()
        assert coordinator._gateway_lock.locked()
        if shutdown:
            await coordinator.async_shutdown()
        else:
            coordinator.set_gateway_connected_devices_enabled(False)
        await refresh
        assert not coordinator.optional_gateway_reads["connected_devices"].enabled
        assert not coordinator.optional_gateway_reads["connected_devices"].reading
        await coordinator.async_shutdown()


class TestGatewayUptime:
    async def test_disable_cancels_pending_optional_read(
        self, hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        coordinator, client = _build(hass)
        started = asyncio.Event()

        async def wait_for_response() -> int:
            started.set()
            await asyncio.Future()
            return 123

        read = AsyncMock(side_effect=wait_for_response)
        monkeypatch.setattr(client, "async_read_gateway_uptime", read)
        coordinator.set_gateway_uptime_enabled(True)
        refresh = asyncio.create_task(coordinator._async_update_data())
        await started.wait()
        assert coordinator._gateway_lock.locked()
        coordinator.set_gateway_uptime_enabled(False)
        result = await refresh
        assert result["unit_1"].target_level == 42
        assert not coordinator.optional_gateway_reads["uptime"].reading
        assert coordinator.gateway_uptime is None
        await coordinator.async_shutdown()

    async def test_disabled_by_default(self, hass: HomeAssistant) -> None:
        coordinator, client = _build(hass)
        await coordinator.async_refresh()
        assert client.uptime_calls == 0
        assert coordinator.gateway_uptime is None

    async def test_due_reads_and_failure_isolation(
        self, hass: HomeAssistant, clock: SimpleNamespace
    ) -> None:
        coordinator, client = _build(hass)
        coordinator.set_gateway_uptime_enabled(True)
        await coordinator.async_refresh()
        assert coordinator.gateway_uptime == 123
        assert client.uptime_calls == 1

        clock.now += 1.0
        await coordinator.async_refresh()
        assert client.uptime_calls == 1
        clock.now += 60.0
        client.uptime_error = MeltemModbusError("unsupported uptime register")
        await coordinator.async_refresh()
        assert client.uptime_calls == 2
        assert coordinator.last_update_success
        assert coordinator.room_available("unit_1")
        assert coordinator.gateway_uptime == 123

        clock.now += 181.0
        assert coordinator.gateway_uptime is None
        coordinator.set_gateway_uptime_enabled(False)
        await coordinator.async_refresh()
        assert client.uptime_calls == 2


class TestGatewayUtcClock:
    async def test_disabled_by_default(self, hass: HomeAssistant) -> None:
        coordinator, client = _build(hass)
        await coordinator.async_refresh()
        assert client.utc_clock_calls == 0
        assert coordinator.gateway_utc_clock is None

    async def test_due_reads_are_independent_and_become_stale(
        self, hass: HomeAssistant, clock: SimpleNamespace
    ) -> None:
        coordinator, client = _build(hass)
        await hass.config.async_update(time_zone="Europe/Berlin")
        coordinator.set_gateway_utc_clock_enabled(True)
        await coordinator.async_refresh()
        expected = datetime.fromtimestamp(1792580112, tz=UTC)
        observed = coordinator.gateway_utc_clock
        assert observed is not None
        assert observed == expected
        assert observed.utcoffset() == timedelta(0)
        assert client.utc_clock_calls == 1
        assert client.uptime_calls == 0

        clock.now += 1
        await coordinator.async_refresh()
        assert client.utc_clock_calls == 1
        coordinator.set_gateway_uptime_enabled(True)
        client.utc_clock_error = MeltemModbusError("clock unavailable")
        clock.now += 60
        await coordinator.async_refresh()
        assert client.utc_clock_calls == 2
        assert coordinator.gateway_utc_clock == expected
        assert coordinator.gateway_uptime == 123
        assert coordinator.last_update_success
        assert coordinator.room_available("unit_1")

        clock.now += 120
        assert coordinator.gateway_utc_clock is None
        coordinator.set_gateway_utc_clock_enabled(False)
        await coordinator.async_refresh()
        assert client.utc_clock_calls == 2
        await coordinator.async_shutdown()

    async def test_invalid_clock_keeps_successful_room_updates(
        self, hass: HomeAssistant, clock: SimpleNamespace
    ) -> None:
        coordinator, _ = _build(hass)
        link = MockModbusConnection()
        gateway = link.for_unit(1)
        gateway.holding[41015] = [0xFFFF, 0xFFFF]
        link.for_unit(_ROOM_1.slave).holding.update({41020: [75, 75], 41100: [3, 150, 0]})
        client = MeltemModbusClient(link.for_unit, port="/dev/test")
        coordinator.client = client
        coordinator.data = {"unit_1": RoomState(supply_air_flow=30, extract_air_flow=30)}
        skip_startup(coordinator)
        coordinator.poll_jobs = [_due_job("flow")]
        coordinator.set_gateway_utc_clock_enabled(True)

        try:
            await coordinator.async_refresh()

            assert coordinator.last_update_success
            assert coordinator.room_available("unit_1")
            assert coordinator.safe_data["unit_1"].supply_air_flow == 75
            assert coordinator.safe_data["unit_1"].target_level == 75
            assert coordinator.gateway_utc_clock is None
            assert coordinator._consecutive_transport_failures == 0
            assert coordinator.backoff_seconds is None

            gateway.holding[41015] = [0x5678, 0x1234]
            clock.now += 61.0
            await coordinator.async_refresh()

            assert coordinator.last_update_success
            assert coordinator.gateway_utc_clock == datetime.fromtimestamp(0x12345678, tz=UTC)
        finally:
            await coordinator.async_shutdown()
            client.shutdown()

    async def test_uptime_failure_does_not_prevent_clock_read(self, hass: HomeAssistant) -> None:
        coordinator, client = _build(hass)
        client.uptime_error = MeltemModbusError("uptime unavailable")
        coordinator.set_gateway_uptime_enabled(True)
        coordinator.set_gateway_utc_clock_enabled(True)
        await coordinator.async_refresh()
        assert coordinator.gateway_utc_clock is not None
        assert coordinator.gateway_uptime is None
        assert client.utc_clock_calls == client.uptime_calls == 1
        assert coordinator.last_update_success
        await coordinator.async_shutdown()

    @pytest.mark.parametrize("shutdown", [False, True])
    async def test_disable_or_shutdown_cancels_pending_read(
        self, hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch, shutdown: bool
    ) -> None:
        coordinator, client = _build(hass)
        started = asyncio.Event()

        async def wait_for_response() -> int:
            started.set()
            await asyncio.Future()
            return 0

        monkeypatch.setattr(client, "async_read_gateway_utc_clock", wait_for_response)
        coordinator.set_gateway_utc_clock_enabled(True)
        refresh = asyncio.create_task(coordinator._async_update_data())
        await started.wait()
        assert coordinator._gateway_lock.locked()
        if shutdown:
            await coordinator.async_shutdown()
        else:
            coordinator.set_gateway_utc_clock_enabled(False)
        await refresh
        assert not coordinator.optional_gateway_reads["utc_clock"].reading
        assert coordinator.gateway_utc_clock is None
        assert not coordinator.optional_gateway_reads["utc_clock"].enabled
        await coordinator.async_shutdown()


@pytest.mark.parametrize("timed_out", [False, True])
async def test_rf_diagnostic_rejections_do_not_mark_room_unavailable(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch, timed_out: bool
) -> None:
    coordinator, client = _build(hass)
    state = RoomState(target_level=42)

    rf_groups = ("rf_last_seen", "rf_signal", "rf_fault", "value_error")

    async def fail_rf_reads(*_args: object) -> RoomRead:
        health = ReadHealth().failed(dt_util.utcnow(), "unsupported RF read", timed_out=timed_out)
        return _read_result(
            _with_health(state, rf_groups, health),
            failed=dict.fromkeys(rf_groups, "unsupported RF read"),
            answered=not timed_out,
        )

    monkeypatch.setattr(client, "read_room", fail_rf_reads)
    for _attempt in range(3):
        coordinator.async_set_updated_data(
            await run_job(coordinator, {"unit_1": state}, _due_job("rf"))
        )
    assert coordinator.room_available("unit_1") is (not timed_out)
    await coordinator.async_shutdown()


async def test_room_diagnostic_values_are_isolated(hass: HomeAssistant) -> None:
    coordinator, _ = _build(hass, _ROOM_1, _ROOM_2)
    assert coordinator.room_connectivity("unit_1") is None
    assert coordinator.last_successful_room_update("unit_1") is None
    now = dt_util.utcnow()
    failed = RoomState().with_read_health("flow", ReadHealth(last_attempt=now))
    coordinator._startup_states = {"unit_1": failed}
    assert coordinator.room_connectivity("unit_1") is False

    first = RoomState(target_level=30).with_read_health("flow", _read_ok(now))
    second = RoomState(target_level=40).with_read_health(
        "flow", _read_ok(now + timedelta(seconds=1))
    )
    coordinator.async_set_updated_data({"unit_1": first, "unit_2": second})
    assert coordinator.room_connectivity("unit_1") is True
    assert coordinator.last_successful_room_update("unit_1") == now
    assert coordinator.last_successful_room_update("unit_2") == now + timedelta(seconds=1)
    coordinator.room_runtime("unit_1").read_failures = 3
    assert coordinator.room_connectivity("unit_1") is False
    assert coordinator.last_successful_room_update("unit_1") == now


class TestFirstRefresh:
    async def test_first_refresh_publishes_one_room_before_reading_the_next(
        self,
        hass: HomeAssistant,
        clock: SimpleNamespace,
    ) -> None:
        coordinator, client = _build(hass, _ROOM_1, _ROOM_2)

        await coordinator.async_refresh()

        assert coordinator.data == {"unit_1": client.next_read_state}
        assert client.read_calls == [("unit_1", AIRFLOW_REFRESH_PLAN)]
        assert client.target_read_calls == []
        assert coordinator.data_health_stale("unit_2") is None
        assert not coordinator.room_available("unit_2")
        clock.now += _update_interval_seconds(coordinator)

        await coordinator.async_refresh()

        assert coordinator.data == {
            "unit_1": client.next_read_state,
            "unit_2": client.next_read_state,
        }
        assert client.read_calls == [
            ("unit_1", AIRFLOW_REFRESH_PLAN),
            ("unit_2", AIRFLOW_REFRESH_PLAN),
        ]

    async def test_startup_reads_every_supported_job_without_hour_long_delays(
        self,
        hass: HomeAssistant,
        clock: SimpleNamespace,
    ) -> None:
        coordinator, client = _build(hass, _ROOM_1, _ROOM_2)
        expected = {(job.room_key, job.refresh_plan) for job in coordinator.poll_jobs}
        await coordinator.async_refresh()
        attempts = 1
        while coordinator._startup_jobs:
            clock.now += _update_interval_seconds(coordinator)
            await coordinator.async_refresh()
            attempts += 1
            assert attempts <= len(expected) * 2

        assert set(client.read_calls) == expected
        assert all(plan != FULL_REFRESH_PLAN for _, plan in client.read_calls)
        assert len(coordinator.data) == 2

    async def test_startup_failure_does_not_prevent_the_next_room_from_recovering(
        self,
        hass: HomeAssistant,
        clock: SimpleNamespace,
    ) -> None:
        coordinator, client = _build(hass, _ROOM_1, _ROOM_2)
        client.fail_rooms = {"unit_1"}

        await coordinator.async_refresh()

        assert not coordinator.last_update_success
        assert _update_interval_seconds(coordinator) == TRANSPORT_BACKOFF_START_SECONDS
        clock.now += _update_interval_seconds(coordinator)

        await coordinator.async_refresh()

        failed_state = coordinator.data["unit_1"]
        assert not failed_state.has_data
        assert failed_state.read_health_for("flow").last_error == "boom: unit_1"
        assert failed_state.read_health_for("status").last_attempt is None
        assert coordinator.data["unit_2"] == client.next_read_state
        assert coordinator.last_update_success
        assert coordinator.backoff_seconds is None

    async def test_startup_keeps_the_job_rate_between_rooms(
        self,
        hass: HomeAssistant,
        clock: SimpleNamespace,
    ) -> None:
        coordinator, client = _build(hass, _ROOM_1, _ROOM_2, max_requests_per_second=2.0)

        await coordinator.async_refresh()
        await coordinator.async_refresh()

        assert len(client.read_calls) == 1
        clock.now += 0.5
        await coordinator.async_refresh()
        assert len(client.read_calls) == 2

    async def test_a_failed_first_refresh_is_retried_after_the_backoff_start(
        self,
        hass: HomeAssistant,
    ) -> None:
        """Retrying at the request rate would hammer a gateway that is down."""
        coordinator, client = _build(hass)
        client.fail_rooms = {"unit_1"}

        await coordinator.async_refresh()

        assert not coordinator.last_update_success
        assert coordinator.next_poll_delay == TRANSPORT_BACKOFF_START_SECONDS

    async def test_a_first_refresh_without_any_room_values_backs_off(
        self,
        hass: HomeAssistant,
    ) -> None:
        coordinator, client = _build(hass)
        client.next_read_state = RoomState().with_read_health(
            "flow",
            ReadHealth(last_attempt=dt_util.utcnow(), consecutive_failures=1, last_error="timeout"),
        )

        await coordinator.async_refresh()

        assert not coordinator.last_update_success
        assert coordinator.next_poll_delay == TRANSPORT_BACKOFF_START_SECONDS

    async def test_startup_skipped_job_preserves_transport_backoff(
        self, hass: HomeAssistant
    ) -> None:
        link = MockModbusConnection()
        unit = link.for_unit(_ROOM_1.slave)
        unit.fail_read(REGISTER_EXTRACT_AIR_FLOW, AcknowledgeError())
        unit.fail_read(REGISTER_MODE_STATUS, AcknowledgeError())
        client = MeltemModbusClient(link.for_unit, port="/dev/test", max_requests_per_second=1000)
        entry = MockConfigEntry(domain=DOMAIN, title="Meltem", source="user")
        entry.add_to_hass(hass)
        coordinator = MeltemDataUpdateCoordinator(
            hass, config_entry=entry, client=client, rooms=[_ROOM_1], max_requests_per_second=1000
        )
        skip_startup(coordinator)

        async def poll() -> None:
            for job in coordinator.poll_jobs:
                job.next_due = 0.0 if job.key == "flow" else float("inf")
            allow_next_read(coordinator)
            await run_tick(coordinator)

        try:
            with pytest.raises(UpdateFailed):
                await poll()
            assert coordinator._consecutive_transport_failures == 0
            reads_before = len(unit.read_events)

            for _ in range(3):
                coordinator._on_transport_failure()
            with pytest.raises(UpdateFailed):
                await poll()
            assert len(unit.read_events) == reads_before
            assert coordinator._consecutive_transport_failures == 3
            assert coordinator.backoff_seconds == TRANSPORT_BACKOFF_START_SECONDS

            client._component_backoff.until[(_ROOM_1.slave, "airflow")] = time.monotonic() - 1
            client._mode_backoff.until[(_ROOM_1.slave, "mode_status")] = time.monotonic() - 1
            unit.fail_read(REGISTER_EXTRACT_AIR_FLOW, None)
            unit.fail_read(REGISTER_MODE_STATUS, None)
            unit.holding[REGISTER_EXTRACT_AIR_FLOW] = [30, 30]
            unit.holding[REGISTER_MODE_STATUS] = [3, 60, 0]
            await poll()
            assert coordinator._consecutive_transport_failures == 0
            assert coordinator.backoff_seconds is None
        finally:
            await coordinator.async_shutdown()
            client.shutdown()

    async def test_first_refresh_keeps_working_room_when_another_has_no_values(
        self,
        hass: HomeAssistant,
        clock: SimpleNamespace,
    ) -> None:
        coordinator, client = _build(hass, _ROOM_1, _ROOM_2)
        unanswered = RoomState().with_read_health(
            "flow",
            ReadHealth(last_attempt=dt_util.utcnow(), consecutive_failures=1, last_error="timeout"),
        )
        working = RoomState(target_level=42)
        reads = [_read_result(unanswered, failed={"flow": "timeout"}), _read_result(working)]
        with patch.object(client, "read_room", side_effect=reads):
            await coordinator.async_refresh()
            clock.now += _update_interval_seconds(coordinator)
            await coordinator.async_refresh()

        assert coordinator.data == {"unit_1": unanswered, "unit_2": working}
        assert coordinator.last_update_success

    async def test_periodic_flow_jobs_continue_during_slow_startup(
        self,
        hass: HomeAssistant,
        clock: SimpleNamespace,
    ) -> None:
        coordinator, client = _build(hass, _ROOM_1, _ROOM_2)
        await coordinator.async_refresh()
        pending_count = _startup_job_count(coordinator)
        clock.now += 11.0

        await coordinator.async_refresh()
        clock.now += _update_interval_seconds(coordinator)
        await coordinator.async_refresh()

        assert client.read_calls == [
            ("unit_1", AIRFLOW_REFRESH_PLAN),
            ("unit_1", AIRFLOW_REFRESH_PLAN),
            ("unit_2", AIRFLOW_REFRESH_PLAN),
        ]
        assert _startup_job_count(coordinator) == pending_count - 1

    async def test_startup_job_is_rescheduled_from_completion(
        self,
        hass: HomeAssistant,
        clock: SimpleNamespace,
    ) -> None:
        coordinator, client = _build(hass)

        async def _slow_read(*args: object) -> RoomRead:
            clock.now += 15.0
            return _read_result(client.next_read_state)

        with patch.object(client, "read_room", side_effect=_slow_read):
            await coordinator.async_refresh()

        flow = next(job for job in coordinator.poll_jobs if job.key == "flow")
        assert flow.next_due == clock.now + flow.interval_seconds

    async def test_persistent_outage_keeps_backoff_and_failed_health(
        self,
        hass: HomeAssistant,
        clock: SimpleNamespace,
    ) -> None:
        coordinator, client = _build(hass)
        client.fail_rooms = {"unit_1"}
        for _ in range(20):
            await coordinator.async_refresh()
            assert not coordinator.last_update_success
            assert _update_interval_seconds(coordinator) >= TRANSPORT_BACKOFF_START_SECONDS
            clock.now += _update_interval_seconds(coordinator)
        assert not coordinator._startup_jobs
        assert (
            coordinator._startup_states["unit_1"].read_health_for("flow").consecutive_failures >= 3
        )

    async def test_group_unread_at_startup_is_retried_before_its_interval(
        self,
        hass: HomeAssistant,
        clock: SimpleNamespace,
    ) -> None:
        """Units can refuse every read for minutes after a gateway restart (H-3)."""
        coordinator, client = _build(hass)
        client.fail_rooms = {"unit_1"}
        started = clock.now
        hours = next(job for job in coordinator.poll_jobs if job.key == "hours")
        for _ in range(50):
            await coordinator.async_refresh()
            if ("unit_1", hours.refresh_plan) in client.read_calls:
                break
            clock.now += _update_interval_seconds(coordinator)

        retry = max(UNREAD_GROUP_RETRY_SECONDS, clock.now - started)
        assert retry < hours.interval_seconds
        assert hours.next_due == pytest.approx(clock.now + retry)

        async def _answer(room: RoomConfig, previous: RoomState, plan: RefreshPlan) -> RoomRead:
            client.read_calls.append((room.key, plan))
            at = dt_util.utcnow()
            for group_key in plan.read_groups():
                previous = previous.with_read_health(group_key, _read_ok(at))
            return _read_result(replace(previous, target_level=42), plan.read_groups())

        with patch.object(client, "read_room", side_effect=_answer):
            for _ in range(200):
                if client.read_calls.count(("unit_1", hours.refresh_plan)) >= 2:
                    break
                clock.now += _update_interval_seconds(coordinator)
                await coordinator.async_refresh()

        assert client.read_calls.count(("unit_1", hours.refresh_plan)) == 2
        assert hours.next_due == pytest.approx(clock.now + hours.interval_seconds)

    async def test_status_only_room_starts_without_an_airflow_read(
        self,
        hass: HomeAssistant,
        clock: SimpleNamespace,
    ) -> None:
        room = replace(_ROOM_1, supported_entity_keys=frozenset({"error_status"}))
        coordinator, client = _build(hass, room)

        await coordinator.async_refresh()

        assert client.read_calls == [("unit_1", RefreshPlan.only(refresh_status=True))]
        assert not coordinator._startup_jobs

    async def test_rate_limited_tick_during_failed_startup_stays_unsuccessful(
        self,
        hass: HomeAssistant,
        clock: SimpleNamespace,
    ) -> None:
        coordinator, client = _build(hass)
        client.fail_rooms = {"unit_1"}

        await coordinator.async_refresh()
        assert not coordinator.last_update_success
        assert coordinator.data is None
        assert coordinator._consecutive_transport_failures == 1
        assert client.uptime_calls == 0

        # A refresh inside the request-rate spacing skips room jobs, runs the
        # due gateway read, and keeps the startup failure without adding a
        # second transport failure.
        coordinator.set_gateway_uptime_enabled(True)
        await coordinator.async_refresh()

        assert not coordinator.last_update_success
        assert coordinator.data is None
        assert coordinator._consecutive_transport_failures == 1
        assert client.uptime_calls == 1
        assert coordinator.gateway_uptime == 123

    async def test_optional_gateway_read_updates_coordinator_read_spacing(
        self,
        hass: HomeAssistant,
        clock: SimpleNamespace,
    ) -> None:
        coordinator, client = _build(hass)
        await coordinator.async_refresh()
        skip_startup(coordinator)
        _set_jobs_due(coordinator, clock.now + 60.0)
        clock.now += 10.0

        coordinator.set_gateway_uptime_enabled(True)
        await coordinator.async_refresh()

        assert client.uptime_calls == 1
        assert coordinator._last_read_started == clock.now

        # A room job that becomes due before the read spacing expires waits for
        # the next slot.
        _set_jobs_due(coordinator, clock.now)
        reads_before = len(client.read_calls)
        await coordinator.async_refresh()
        assert len(client.read_calls) == reads_before


class TestStartupWithRealClient:
    @pytest.mark.parametrize("rate", [0.5, 3.0, 10.0])
    async def test_six_rooms_publish_incrementally_with_a_shared_telegram_limit(
        self,
        hass: HomeAssistant,
        monkeypatch: pytest.MonkeyPatch,
        clock: SimpleNamespace,
        rate: float,
    ) -> None:
        link = MockModbusConnection()
        rooms = [
            RoomConfig(
                key=f"unit_{slave}",
                name=f"Unit {slave}",
                profile="ii_plain",
                slave=slave,
                supported_entity_keys=frozenset(supported_entity_keys_for_profile("ii_plain")),
            )
            for slave in range(2, 8)
        ]
        writes: list[WriteEvent] = []
        for room in rooms:
            link.for_unit(room.slave).holding.update({41020: [30, 30], 41100: [3, 60, 0]})
            link.for_unit(room.slave).on_write(writes.append)
        slaves = {link.for_unit(slave): slave for slave in range(1, 8)}
        link.for_unit(1).holding[43901] = len(rooms)
        client = MeltemModbusClient(link.for_unit, port="/dev/test", max_requests_per_second=rate)
        entry = MockConfigEntry(domain=DOMAIN, title="Meltem", source="user")
        entry.add_to_hass(hass)
        coordinator = MeltemDataUpdateCoordinator(
            hass,
            config_entry=entry,
            client=client,
            rooms=rooms,
            max_requests_per_second=rate,
        )
        baseline = clock.now
        utc = dt_util.utcnow()
        logical_time = SimpleNamespace(monotonic=lambda: clock.now)
        monkeypatch.setattr(
            "custom_components.meltem_ventilation.device.transport.time", logical_time
        )
        monkeypatch.setattr("custom_components.meltem_ventilation.modbus_client.time", logical_time)
        utc_time = SimpleNamespace(utcnow=lambda: utc + timedelta(seconds=clock.now - baseline))
        monkeypatch.setattr("custom_components.meltem_ventilation.modbus_client.dt_util", utc_time)
        monkeypatch.setattr("custom_components.meltem_ventilation.read_health.dt_util", utc_time)

        async def _sleep(seconds: float) -> None:
            clock.now += seconds

        monkeypatch.setattr(
            "custom_components.meltem_ventilation.device.transport.async_sleep", _sleep
        )
        reads: list[tuple[float, int, int]] = []
        original_read = MockModbusUnit.read_holding_registers

        async def _read(unit: MockModbusUnit, address: int, count: int) -> list[int]:
            reads.append((clock.now, slaves[unit], address))
            return await original_read(unit, address, count)

        monkeypatch.setattr(MockModbusUnit, "read_holding_registers", _read)
        snapshots: list[dict[str, RoomState]] = []
        remove_listener = coordinator.async_add_listener(
            lambda: snapshots.append(coordinator.safe_data)
        )
        try:
            await client.async_validate_gateway()
            await coordinator.async_refresh()

            assert len(snapshots) == 1
            assert set(snapshots[0]) == {"unit_2"}
            assert snapshots[0]["unit_2"].supply_air_flow == 30
            assert snapshots[0]["unit_2"].error_status is None
            assert coordinator.read_group_stale("unit_2", "status") is None
            status_description = next(
                description
                for description in BINARY_SENSOR_DESCRIPTIONS
                if description.key == "error_status"
            )
            status_sensor = MeltemBinarySensorEntity(coordinator, rooms[0], status_description)
            assert not status_sensor.available
            health_sensor = MeltemDataHealthBinarySensor(coordinator, rooms[0])
            assert health_sensor.is_on is False
            status_attributes = health_sensor.extra_state_attributes["status"]
            assert isinstance(status_attributes, Mapping)
            assert status_attributes["stale"] is None

            steps = 0
            while coordinator._startup_jobs:
                clock.now += _update_interval_seconds(coordinator)
                await coordinator.async_refresh()
                steps += 1
                assert steps < 100

            assert coordinator.state_room_count == 6
            for job in coordinator.poll_jobs:
                job.next_due = (
                    0.0 if job.room_key == "unit_2" and job.key == "status" else float("inf")
                )
            clock.now += 1 / rate
            allow_next_read(coordinator)
            await coordinator.async_refresh()
            with patch.object(status_sensor, "async_write_ha_state"):
                status_sensor._handle_coordinator_update()
            assert status_sensor.available
            assert all(
                state.error_status is False and state.operating_hours == 0
                for state in coordinator.safe_data.values()
            )
            assert all(
                later[0] - earlier[0] >= 1 / rate - 1e-8 for earlier, later in zip(reads, reads[1:])
            )
            assert all(
                len(set(later) - set(earlier)) <= 1
                for earlier, later in zip(snapshots, snapshots[1:])
            )
            assert not writes
        finally:
            remove_listener()
            await coordinator.async_shutdown()
            client.shutdown()

    async def test_mode_refusal_is_not_recounted_during_startup_backoff(
        self, hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch, clock: SimpleNamespace
    ) -> None:
        link = MockModbusConnection()
        unit = link.for_unit(_ROOM_1.slave)
        unit.holding[41020] = [30, 30]
        original_read = unit.read_holding_registers

        async def _read(address: int, count: int) -> list[int]:
            if address == 41100:
                raise IllegalDataAddressError()
            return await original_read(address, count)

        monkeypatch.setattr(unit, "read_holding_registers", _read)
        client = MeltemModbusClient(link.for_unit, port="/dev/test")
        entry = MockConfigEntry(domain=DOMAIN, title="Meltem", source="user")
        entry.add_to_hass(hass)
        coordinator = MeltemDataUpdateCoordinator(
            hass,
            config_entry=entry,
            client=client,
            rooms=[_ROOM_1],
            max_requests_per_second=2.0,
        )

        for _ in range(3):
            await coordinator.async_refresh()
            clock.now += 11.0

        # The second refresh is periodic flow; the third makes startup progress.
        await coordinator.async_refresh()

        assert coordinator.last_update_success
        assert coordinator.safe_data["unit_1"].supply_air_flow == 30
        assert coordinator.read_group_available("unit_1", "flow")
        mode_health = coordinator.safe_data["unit_1"].read_health_for("flow_control")
        assert mode_health.consecutive_failures == 1
        assert mode_health.last_error is not None
        assert coordinator.read_group_stale("unit_1", "flow_control") is None
        assert MeltemDataHealthBinarySensor(coordinator, _ROOM_1).is_on is False
        client.shutdown()

    @pytest.mark.parametrize(
        ("error", "backoff_seconds"),
        [
            # Three unanswered jobs in a row slow down polling of the whole gateway.
            (ModbusTimeoutError("silent"), TRANSPORT_BACKOFF_START_SECONDS),
            (ModbusProtocolError("invalid response"), TRANSPORT_BACKOFF_START_SECONDS),
            # A unit that refuses reads still answers, so the link is healthy.
            (IllegalDataAddressError(), None),
            (GatewayPathUnavailableError(), None),
            (GatewayTargetError(), None),
        ],
    )
    async def test_real_client_failures_mark_room_unavailable_and_back_off_without_an_answer(
        self,
        hass: HomeAssistant,
        clock: SimpleNamespace,
        error: Exception,
        backoff_seconds: float | None,
    ) -> None:
        link = MockModbusConnection()
        unit = link.for_unit(_ROOM_1.slave)
        unit.holding.update({41020: [30, 30], 41100: [3, 60, 0]})
        client = MeltemModbusClient(link.for_unit, port="/dev/test")
        entry = MockConfigEntry(domain=DOMAIN, title="Meltem", source="user")
        entry.add_to_hass(hass)
        coordinator = MeltemDataUpdateCoordinator(
            hass,
            config_entry=entry,
            client=client,
            rooms=[_ROOM_1],
            max_requests_per_second=10.0,
        )

        await coordinator.async_refresh()
        assert coordinator.room_available("unit_1") is True

        unit.fail_requests(error)
        transport_clock = SimpleNamespace(now=time.monotonic())
        with patch(
            "custom_components.meltem_ventilation.device.transport.time",
            SimpleNamespace(monotonic=lambda: transport_clock.now),
        ):
            for attempt in range(3):
                assert coordinator.room_available("unit_1") is True, attempt
                for job in coordinator.poll_jobs:
                    job.next_due = 0.0
                allow_next_read(coordinator)
                await run_tick(coordinator)
                # The read pause after an unanswered request has to end first.
                transport_clock.now += TRANSPORT_READ_RECOVERY_SECONDS

        assert coordinator.room_available("unit_1") is False
        assert coordinator.last_job_error is not None
        assert client.read_quarantined(_ROOM_1.slave) is isinstance(
            error, (ModbusTimeoutError, ModbusProtocolError)
        )
        assert coordinator.backoff_seconds == backoff_seconds
        client.shutdown()

    async def test_balanced_target_readback_persists_across_coarse_status_polls(
        self, hass: HomeAssistant, clock: SimpleNamespace
    ) -> None:
        link = MockModbusConnection()
        unit = link.for_unit(_ROOM_1.slave)
        unit.holding.update({41020: [65, 65], 41100: [3, 206, 0], 41121: [130]})
        client = MeltemModbusClient(link.for_unit, port="/dev/test")
        entry = MockConfigEntry(domain=DOMAIN, title="Meltem", source="user")
        entry.add_to_hass(hass)
        coordinator = MeltemDataUpdateCoordinator(
            hass,
            config_entry=entry,
            client=client,
            rooms=[_ROOM_1],
            max_requests_per_second=10.0,
        )
        coordinator.write_confirmations.record_pending("unit_1", "airflow_levels", (65, 65))

        await coordinator.async_refresh()
        assert (
            coordinator.write_confirmations.by_room["unit_1"]["airflow_levels"].status
            == "confirmed"
        )
        assert coordinator.effective_levels("unit_1") == (65, 65)

        clock.now += 11.0
        skip_startup(coordinator)
        for job in coordinator.poll_jobs:
            if job.key == "flow":
                job.next_due = 0.0
        allow_next_read(coordinator)
        await run_tick(coordinator)

        assert coordinator.effective_levels("unit_1") == (65, 65)
        client.shutdown()


# ---------------------------------------------------------------------------
#  _async_update_data — first vs incremental
# ---------------------------------------------------------------------------


class TestAsyncUpdateData:
    @pytest.mark.parametrize("job_due", [False, True])
    @pytest.mark.parametrize("fail_at_clock", [False, True])
    async def test_optional_gateway_reads_run_in_order_under_lock(
        self,
        hass: HomeAssistant,
        monkeypatch: pytest.MonkeyPatch,
        job_due: bool,
        fail_at_clock: bool,
    ) -> None:
        coordinator, client = _build(hass)
        coordinator.data = {"unit_1": RoomState(target_level=10)}
        _set_jobs_due(coordinator, 0.0 if job_due else time.monotonic() + 9999)
        events: list[str] = []
        read_room = client.read_room

        async def record_room_read(
            room: RoomConfig, previous_state: RoomState, refresh_plan: RefreshPlan
        ) -> RoomRead:
            assert coordinator._gateway_lock.locked()
            events.append("room")
            return await read_room(room, previous_state, refresh_plan)

        async def record_gateway_read(name: str) -> None:
            assert coordinator._gateway_lock.locked()
            events.append(name)
            if name == "utc_clock" and fail_at_clock:
                raise RuntimeError("clock failed")

        monkeypatch.setattr(client, "read_room", record_room_read)
        for name, read in coordinator.optional_gateway_reads.items():
            monkeypatch.setattr(read, "async_read_if_due", partial(record_gateway_read, name))

        if fail_at_clock:
            with pytest.raises(RuntimeError, match="clock failed"):
                await run_tick(coordinator)
        else:
            await run_tick(coordinator)

        assert events == (["room"] if job_due else []) + (
            ["uptime", "utc_clock"]
            if fail_at_clock
            else ["uptime", "utc_clock", "connected_devices", "rf_statistics", "unit_list"]
        )
        assert not coordinator._gateway_lock.locked()
        await coordinator.async_shutdown()

    async def test_empty_data_triggers_one_startup_job(
        self,
        hass: HomeAssistant,
    ) -> None:
        coordinator, client = _build(hass)
        coordinator.data = {}

        data = await run_tick(coordinator)

        assert data == {"unit_1": client.next_read_state}
        assert client.read_calls == [("unit_1", AIRFLOW_REFRESH_PLAN)]

    async def test_existing_data_runs_one_incremental_job(
        self,
        hass: HomeAssistant,
    ) -> None:
        coordinator, client = _build(hass)
        coordinator.data = {"unit_1": RoomState(target_level=10)}
        _set_jobs_due(coordinator, 0.0)

        await run_tick(coordinator)

        assert len(client.read_calls) == 1
        assert client.read_calls[0][1] != FULL_REFRESH_PLAN

    async def test_no_due_job_returns_existing_data(
        self,
        hass: HomeAssistant,
    ) -> None:
        coordinator, client = _build(hass)
        existing = {"unit_1": RoomState(target_level=99)}
        coordinator.data = existing
        _set_jobs_due(coordinator, time.monotonic() + 9999)

        assert await run_tick(coordinator) is existing
        assert client.read_calls == []

    async def test_total_startup_outage_raises_update_failed(
        self,
        hass: HomeAssistant,
    ) -> None:
        coordinator, client = _build(hass, _ROOM_1, _ROOM_2)
        coordinator.data = {}
        client.fail_rooms = {"unit_1", "unit_2"}

        with pytest.raises(UpdateFailed) as err:
            await run_tick(coordinator)

        assert err.value.translation_domain == DOMAIN
        assert err.value.translation_key == "gateway_communication_failed"

    async def test_incremental_transport_failure_keeps_cached_state(
        self,
        hass: HomeAssistant,
    ) -> None:
        coordinator, client = _build(hass)
        coordinator.data = {"unit_1": RoomState(target_level=10)}
        client.fail_rooms = {"unit_1"}
        _set_jobs_due(coordinator, 0.0)

        data = await run_tick(coordinator)

        assert data["unit_1"].target_level == 10
        assert data["unit_1"].read_health_for("flow").last_error == "boom: unit_1"


# ---------------------------------------------------------------------------
#  Scheduler wakeups
# ---------------------------------------------------------------------------


class TestSchedulerWakeups:
    async def test_idle_tick_sleeps_until_the_next_job_is_due(
        self,
        hass: HomeAssistant,
    ) -> None:
        coordinator, _ = _build(hass)
        coordinator.data = {"unit_1": RoomState(target_level=30)}
        _set_jobs_due(coordinator, time.monotonic() + 30.0)

        await run_tick(coordinator)

        assert 29.0 < _update_interval_seconds(coordinator) <= 30.0

    async def test_next_tick_never_undercuts_the_request_rate(
        self,
        hass: HomeAssistant,
    ) -> None:
        coordinator, _ = _build(hass)
        coordinator.data = {"unit_1": RoomState(target_level=30)}
        _set_jobs_due(coordinator, time.monotonic() - 1.0)

        await run_tick(coordinator)

        assert _update_interval_seconds(coordinator) == coordinator._tick_seconds

    def test_next_tick_waits_exactly_until_the_next_due_job(self, hass: HomeAssistant) -> None:
        coordinator, _ = _build(hass)
        skip_startup(coordinator)
        due_in = 3.0
        _set_jobs_due(coordinator, time.monotonic() + due_in)

        coordinator._schedule_next_tick()

        assert due_in - 0.1 < _update_interval_seconds(coordinator) <= due_in

    async def test_jobs_respect_the_request_rate_cap(
        self,
        hass: HomeAssistant,
    ) -> None:
        coordinator, client = _build(hass)
        coordinator.data = {"unit_1": RoomState(target_level=10)}
        _set_jobs_due(coordinator, 0.0)

        await run_tick(coordinator)
        await run_tick(coordinator)

        assert len(client.read_calls) == 1

    async def test_post_write_reads_count_against_the_request_rate_cap(
        self,
        hass: HomeAssistant,
    ) -> None:
        coordinator, client = _build(hass)
        coordinator.data = {"unit_1": RoomState(target_level=10)}
        _set_jobs_due(coordinator, 0.0)

        await coordinator._async_refresh_room_after_write(_ROOM_1)
        await run_tick(coordinator)

        assert len(client.read_calls) == 1

    def test_backoff_interval_survives_rescheduling(self, hass: HomeAssistant) -> None:
        coordinator, _ = _build(hass)
        coordinator._backoff_seconds = TRANSPORT_BACKOFF_MAX_SECONDS
        coordinator._next_poll_delay = TRANSPORT_BACKOFF_MAX_SECONDS

        coordinator._schedule_next_tick()

        assert _update_interval_seconds(coordinator) == TRANSPORT_BACKOFF_MAX_SECONDS


# ---------------------------------------------------------------------------
#  Job scheduling
# ---------------------------------------------------------------------------


class TestJobScheduling:
    def test_jobs_of_one_group_are_staggered_across_rooms(
        self,
        hass: HomeAssistant,
    ) -> None:
        coordinator, _ = _build(hass, _ROOM_1, _ROOM_2)

        first, second = (job for job in coordinator.poll_jobs if job.key == "flow")

        assert second.next_due > first.next_due

    @pytest.mark.parametrize(
        ("supported_entity_keys", "needed_jobs"),
        [
            (None, {group.key for group in JOB_GROUPS}),
            (frozenset({"extract_air_flow"}), {"flow"}),
            (frozenset({"error_status"}), {"status"}),
        ],
        ids=("unfiltered", "airflow-only", "status-only"),
    )
    def test_room_only_needs_jobs_for_its_entities(
        self,
        supported_entity_keys: frozenset[str] | None,
        needed_jobs: set[str],
    ) -> None:
        room = replace(_ROOM_1, supported_entity_keys=supported_entity_keys)

        assert {group.key for group in JOB_GROUPS if room_needs_job(room, group)} == needed_jobs


# ---------------------------------------------------------------------------
#  Post-write refresh
# ---------------------------------------------------------------------------


class TestPostWriteRefresh:
    async def test_refresh_after_write_updates_data(
        self,
        hass: HomeAssistant,
    ) -> None:
        coordinator, client = _build(hass)
        coordinator.data = {"unit_1": RoomState(target_level=10)}
        client.next_read_state = RoomState(target_level=50, extract_air_flow=50, supply_air_flow=50)

        await coordinator._async_refresh_room_after_write(_ROOM_1)

        assert coordinator.data["unit_1"].target_level == 50

    async def test_refresh_after_write_waits_for_the_request_rate_slot(
        self,
        hass: HomeAssistant,
    ) -> None:
        coordinator, client = _build(hass, max_requests_per_second=0.5)
        coordinator.data = {"unit_1": RoomState(target_level=10)}
        coordinator._last_read_started = time.monotonic()

        waiting = asyncio.Event()
        resume = asyncio.Event()
        delays: list[float] = []

        async def _sleep(seconds: float) -> None:
            delays.append(seconds)
            waiting.set()
            await resume.wait()

        with patch("custom_components.meltem_ventilation.coordinator.async_sleep", new=_sleep):
            task = asyncio.create_task(coordinator._async_refresh_room_after_write(_ROOM_1))
            await waiting.wait()
            assert client.read_calls == []
            resume.set()
            await task

        assert len(delays) == 1
        assert 0 < delays[0] <= 2.0
        assert len(client.read_calls) == 1

    async def test_refresh_after_write_reads_once(
        self,
        hass: HomeAssistant,
    ) -> None:
        coordinator, client = _build(hass)
        coordinator.data = {"unit_1": RoomState(target_level=10)}
        stale = RoomState(target_level=10, extract_air_flow=10, supply_air_flow=10)

        with patch.object(client, "read_room", return_value=_read_result(stale)) as read_mock:
            await coordinator._async_refresh_room_after_write(_ROOM_1)

        assert read_mock.call_count == 1
        assert coordinator.data["unit_1"].target_level == 10

    async def test_refresh_after_write_failure_keeps_cached_data(
        self,
        hass: HomeAssistant,
    ) -> None:
        coordinator, client = _build(hass)
        coordinator.data = {"unit_1": RoomState(target_level=10)}
        client.fail_rooms = {"unit_1"}

        await coordinator._async_refresh_room_after_write(_ROOM_1)

        assert coordinator.data["unit_1"].target_level == 10
        assert coordinator.data["unit_1"].read_health_for("flow").consecutive_failures == 1


# ---------------------------------------------------------------------------
#  _read_one_job
# ---------------------------------------------------------------------------


class TestReadOneJob:
    @pytest.mark.parametrize(
        ("device_name", "unit_name"),
        [("Meltem Modbus Unit 2", "Meltem Modbus Unit 2"), (None, "Unit 2")],
        ids=("device-name", "no-device"),
    )
    async def test_sensor_placeholders_raise_a_repair_issue_until_a_value_arrives(
        self, hass: HomeAssistant, device_name: str | None, unit_name: str
    ) -> None:
        link = MockModbusConnection()
        unit = link.for_unit(_ROOM_2.slave)
        unit.holding.update(
            {
                REGISTER_EXTRACT_AIR_TEMPERATURE: [0, 0] * 3,
                REGISTER_SUPPLY_AIR_TEMPERATURE: [0, 0],
                REGISTER_HUMIDITY_EXTRACT_AIR: [45, 32767],
                REGISTER_HUMIDITY_SUPPLY_AIR: [47],
            }
        )
        client = MeltemModbusClient(link.for_unit, port="/dev/test", max_requests_per_second=1000)
        entry = MockConfigEntry(domain=DOMAIN, title="Meltem", source="user")
        entry.add_to_hass(hass)
        if device_name is not None:
            dr.async_get(hass).async_get_or_create(
                config_entry_id=entry.entry_id,
                identifiers={(DOMAIN, _ROOM_2.key)},
                name=device_name,
            )
        coordinator = MeltemDataUpdateCoordinator(
            hass, config_entry=entry, client=client, rooms=[_ROOM_2], max_requests_per_second=1000
        )
        issue_id = f"missing_sensor_{entry.entry_id}_unit_2_co2_extract_air"
        issues = ir.async_get(hass)
        try:
            for _ in range(MISSING_SENSOR_ISSUE_AFTER_READS):
                coordinator.data = await run_job(
                    coordinator, coordinator.safe_data, _due_job("temperature", "unit_2")
                )
            assert coordinator.safe_data["unit_2"].co2_extract_air is None
            assert coordinator.safe_data["unit_2"].humidity_extract_air == 45
            assert coordinator.room_available("unit_2")
            issue = issues.async_get_issue(DOMAIN, issue_id)
            assert issue is not None
            assert issue.translation_placeholders == {
                "unit": unit_name,
                "profile": "M-WRG-II (-FC)",
            }

            unit.holding[REGISTER_CO2_EXTRACT_AIR] = 800
            coordinator.data = await run_job(
                coordinator, coordinator.safe_data, _due_job("temperature", "unit_2")
            )
            assert coordinator.safe_data["unit_2"].co2_extract_air == 800
            assert issues.async_get_issue(DOMAIN, issue_id) is None

            unit.holding[REGISTER_CO2_EXTRACT_AIR] = 32767
            for _ in range(MISSING_SENSOR_ISSUE_AFTER_READS):
                coordinator.data = await run_job(
                    coordinator, coordinator.safe_data, _due_job("temperature", "unit_2")
                )
            assert issues.async_get_issue(DOMAIN, issue_id) is not None
        finally:
            await coordinator.async_shutdown()
            client.shutdown()
        # Unloading keeps the issue; the next setup drops it once the profile has no CO2.
        assert issues.async_get_issue(DOMAIN, issue_id) is not None
        reloaded = MeltemDataUpdateCoordinator(
            hass,
            config_entry=entry,
            client=client,
            rooms=[replace(_ROOM_2, profile="ii_f")],
            max_requests_per_second=1000,
        )
        await reloaded.async_shutdown()
        assert issues.async_get_issue(DOMAIN, issue_id) is None

    async def test_quarantined_room_recovers_after_the_pause(
        self, hass: HomeAssistant, clock: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            "custom_components.meltem_ventilation.device.transport.time",
            SimpleNamespace(monotonic=lambda: clock.now),
        )

        async def advance(seconds: float) -> None:
            clock.now += seconds

        monkeypatch.setattr(
            "custom_components.meltem_ventilation.device.transport.async_sleep", advance
        )
        link = MockModbusConnection()
        unit = link.for_unit(_ROOM_1.slave)
        unit.holding[REGISTER_EXTRACT_AIR_FLOW] = [30, 30]
        unit.holding[REGISTER_MODE_STATUS] = [3, 60, 0]
        client = MeltemModbusClient(link.for_unit, port="/dev/test", max_requests_per_second=1000)
        entry = MockConfigEntry(domain=DOMAIN, title="Meltem", source="user")
        entry.add_to_hass(hass)
        cumulative = CumulativeDiagnostics(MagicMock(), None)
        coordinator = MeltemDataUpdateCoordinator(
            hass,
            config_entry=entry,
            client=client,
            rooms=[_ROOM_1],
            max_requests_per_second=1000,
            cumulative_diagnostics=cumulative,
        )
        try:
            coordinator.data = await run_job(coordinator, {}, _due_job("flow"))
            unit.fail_read(REGISTER_EXTRACT_AIR_FLOW, ModbusTimeoutError("silent"))
            coordinator.data = await run_job(coordinator, coordinator.data, _due_job("flow"))
            # One lost telegram pauses the reads but keeps the valid cached values.
            assert client.read_quarantined(_ROOM_1.slave)
            assert coordinator.room_available("unit_1")
            health = coordinator.safe_data["unit_1"].group_read_health
            reads = len(unit.read_events)
            failures = cumulative.failed_read_groups
            coordinator._last_job_attempted = False
            coordinator.data = await run_job(coordinator, coordinator.data, _due_job("flow"))
            assert len(unit.read_events) == reads
            assert coordinator.safe_data["unit_1"].group_read_health == health
            assert cumulative.failed_read_groups == failures
            assert not coordinator._last_job_attempted

            clock.now += TRANSPORT_READ_RECOVERY_SECONDS
            assert coordinator.room_available("unit_1")
            unit.fail_read(REGISTER_EXTRACT_AIR_FLOW, None)
            coordinator.data = await run_job(coordinator, coordinator.data, _due_job("flow"))
            assert coordinator.room_available("unit_1")
            assert not client.read_quarantined(_ROOM_1.slave)
            assert coordinator.safe_data["unit_1"].read_health_for("flow").last_error is None
        finally:
            client.shutdown()

    async def test_only_gateway_rf_status_does_not_clear_a_room_failure_streak(
        self, hass: HomeAssistant
    ) -> None:
        link = MockModbusConnection()
        unit = link.for_unit(2)
        unit.holding[REGISTER_RF_COMM_STATUS] = 0
        room = replace(_ROOM_1, supported_entity_keys=frozenset({"rf_comm_status"}))
        client = MeltemModbusClient(link.for_unit, port="/dev/test", max_requests_per_second=1000)
        entry = MockConfigEntry(domain=DOMAIN, title="Meltem", source="user")
        entry.add_to_hass(hass)
        coordinator = MeltemDataUpdateCoordinator(
            hass, config_entry=entry, client=client, rooms=[room], max_requests_per_second=1000
        )
        coordinator.data = {"unit_1": RoomState(target_level=20)}
        coordinator.room_runtime("unit_1").read_failures = 2
        skip_startup(coordinator)
        for job in coordinator.poll_jobs:
            job.next_due = 0.0 if job.key == "status" else float("inf")
        allow_next_read(coordinator)

        try:
            coordinator.data = await run_tick(coordinator)

            assert [event.address for event in unit.read_events] == [REGISTER_RF_COMM_STATUS]
            assert coordinator.room_runtime("unit_1").read_failures == 2
            assert coordinator.backoff_seconds is None
        finally:
            await coordinator.async_shutdown()
            client.shutdown()

    async def test_gateway_rf_status_does_not_make_an_unreadable_room_reachable(
        self, hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            "custom_components.meltem_ventilation.device.transport.async_sleep", AsyncMock()
        )
        link = MockModbusConnection()
        unit = link.for_unit(2)
        unit.fail_read(REGISTER_ERROR_STATUS, IllegalDataAddressError())
        unit.holding[REGISTER_RF_COMM_STATUS] = 0
        client = MeltemModbusClient(link.for_unit, port="/dev/test", max_requests_per_second=1000)
        entry = MockConfigEntry(domain=DOMAIN, title="Meltem", source="user")
        entry.add_to_hass(hass)
        coordinator = MeltemDataUpdateCoordinator(
            hass, config_entry=entry, client=client, rooms=[_ROOM_1], max_requests_per_second=1000
        )
        coordinator.data = {"unit_1": RoomState(target_level=20)}
        skip_startup(coordinator)

        try:
            for _ in range(3):
                for job in coordinator.poll_jobs:
                    job.next_due = 0.0 if job.key == "status" else float("inf")
                allow_next_read(coordinator)
                coordinator.data = await run_tick(coordinator)

            assert not coordinator.room_available("unit_1")
            assert coordinator.backoff_seconds is None
            assert coordinator._consecutive_transport_failures == 0
        finally:
            await coordinator.async_shutdown()
            client.shutdown()

    async def test_bridge_rf_job_does_not_clear_room_failures_or_quarantine(
        self, hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            "custom_components.meltem_ventilation.device.transport.async_sleep", AsyncMock()
        )
        link = MockModbusConnection()
        unit = link.for_unit(2)
        unit.holding.update(
            {
                REGISTER_RF_LAST_SEEN_AGE: [25],
                REGISTER_RF_FAULT_STATUS: [0],
                REGISTER_VALUE_ERROR_STATUS: [0],
                REGISTER_RF_SIGNAL_STRENGTH: [65493],
            }
        )
        client = MeltemModbusClient(link.for_unit, port="/dev/test", max_requests_per_second=1000)
        entry = MockConfigEntry(domain=DOMAIN, title="Meltem", source="user")
        entry.add_to_hass(hass)
        coordinator = MeltemDataUpdateCoordinator(
            hass, config_entry=entry, client=client, rooms=[_ROOM_1], max_requests_per_second=1000
        )
        coordinator.data = {"unit_1": RoomState(target_level=20)}
        coordinator.room_runtime("unit_1").read_failures = 3
        skip_startup(coordinator)

        try:
            for job in coordinator.poll_jobs:
                job.next_due = 0.0 if job.key == "rf" else float("inf")
            allow_next_read(coordinator)
            coordinator.data = await run_tick(coordinator)

            assert coordinator.safe_data["unit_1"].rf_last_seen_age_seconds == 25
            assert coordinator.safe_data["unit_1"].rf_signal_strength == -43
            assert coordinator.room_runtime("unit_1").read_failures == 3
            assert not coordinator.room_available("unit_1")
            assert client.seconds_since_successful_read(2) is None
        finally:
            await coordinator.async_shutdown()
            client.shutdown()

    async def test_partial_group_skip_preserves_health_but_counts_actual_answers(
        self, hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            "custom_components.meltem_ventilation.device.transport.async_sleep", AsyncMock()
        )
        link = MockModbusConnection()
        unit = link.for_unit(2)
        unit.holding[41000] = [0, 0, 0, 0, 0, 0]
        unit.fail_read(REGISTER_SUPPLY_AIR_TEMPERATURE, AcknowledgeError())
        room = RoomConfig(
            key="unit_1",
            name="Unit 1",
            profile="ii_f",
            slave=2,
            supported_entity_keys=frozenset(supported_entity_keys_for_profile("ii_f")),
        )
        client = MeltemModbusClient(link.for_unit, port="/dev/test", max_requests_per_second=1000)
        entry = MockConfigEntry(domain=DOMAIN, title="Meltem", source="user")
        entry.add_to_hass(hass)
        coordinator = MeltemDataUpdateCoordinator(
            hass, config_entry=entry, client=client, rooms=[room], max_requests_per_second=1000
        )
        coordinator.data = {"unit_1": RoomState(target_level=20)}
        skip_startup(coordinator)

        async def _poll() -> None:
            for job in coordinator.poll_jobs:
                job.next_due = 0.0 if job.key == "temperature" else float("inf")
            allow_next_read(coordinator)
            coordinator.data = await run_tick(coordinator)

        try:
            await _poll()
            health = coordinator.safe_data["unit_1"].read_health_for("temperature")
            reads_before = len(unit.read_events)
            for _ in range(3):
                coordinator._on_transport_failure()
            coordinator.room_runtime("unit_1").read_failures = 2
            await _poll()

            assert REGISTER_SUPPLY_AIR_TEMPERATURE not in [
                event.address for event in unit.read_events[reads_before:]
            ]
            assert coordinator.safe_data["unit_1"].read_health_for("temperature") == health
            assert coordinator.backoff_seconds is None
            assert coordinator._consecutive_transport_failures == 0
            assert coordinator.room_runtime("unit_1").read_failures == 0
        finally:
            await coordinator.async_shutdown()
            client.shutdown()

    async def test_partial_temperature_read_proves_the_gateway_and_room_answered(
        self, hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch, clock: SimpleNamespace
    ) -> None:
        monkeypatch.setattr(
            "custom_components.meltem_ventilation.device.transport.async_sleep", AsyncMock()
        )
        link = MockModbusConnection()
        unit = link.for_unit(2)
        unit.holding[41000] = [0, 0, 0, 0, 0, 0]
        unit.fail_read(REGISTER_SUPPLY_AIR_TEMPERATURE, ModbusTimeoutError("silent"))
        outcomes: list[str] = []
        room = RoomConfig(
            key="unit_1",
            name="Unit 1",
            profile="ii_f",
            slave=2,
            supported_entity_keys=frozenset(supported_entity_keys_for_profile("ii_f")),
        )
        client = MeltemModbusClient(
            link.for_unit,
            port="/dev/test",
            max_requests_per_second=1000,
            on_request=outcomes.append,
        )
        entry = MockConfigEntry(domain=DOMAIN, title="Meltem", source="user")
        entry.add_to_hass(hass)
        coordinator = MeltemDataUpdateCoordinator(
            hass, config_entry=entry, client=client, rooms=[room], max_requests_per_second=1000
        )
        coordinator.data = {"unit_1": RoomState(target_level=20)}
        skip_startup(coordinator)

        try:
            for _ in range(3):
                for job in coordinator.poll_jobs:
                    job.next_due = 0.0 if job.key == "temperature" else float("inf")
                allow_next_read(coordinator)
                coordinator.data = await run_tick(coordinator)
                clock.now += 1

            assert outcomes.count("no_answer") == 1
            assert outcomes.count("success") == 1
            state = coordinator.safe_data["unit_1"]
            assert state.exhaust_temperature == 0.0
            assert state.read_health_for("temperature").last_error_timed_out
            assert state.read_health_for("temperature").consecutive_failures == 1
            assert coordinator._consecutive_transport_failures == 0
            assert coordinator.backoff_seconds is None
            # The answered blocks keep the unit reachable despite the paused reads.
            assert client.read_quarantined(_ROOM_1.slave)
            assert coordinator.room_available("unit_1")

            unit.fail_read(REGISTER_SUPPLY_AIR_TEMPERATURE, None)
            for job in coordinator.poll_jobs:
                job.next_due = 0.0 if job.key == "temperature" else float("inf")
            allow_next_read(coordinator)
            coordinator.data = await run_tick(coordinator)
            assert coordinator.safe_data["unit_1"].read_health_for("temperature") == (
                state.read_health_for("temperature")
            )
        finally:
            await coordinator.async_shutdown()
            client.shutdown()

    async def test_mixed_rf_failures_skips_outage_and_recovery(
        self, hass: HomeAssistant, clock: SimpleNamespace
    ) -> None:
        link = MockModbusConnection()
        unit = link.for_unit(_ROOM_1.slave)
        refused_registers = (
            REGISTER_RF_FAULT_STATUS,
            REGISTER_VALUE_ERROR_STATUS,
            REGISTER_RF_LAST_SEEN_AGE,
        )
        for register in refused_registers:
            unit.holding[register] = 0
            unit.fail_read(register, AcknowledgeError())
        unit.holding[REGISTER_RF_SIGNAL_STRENGTH] = 42
        unit.fail_read(REGISTER_RF_SIGNAL_STRENGTH, ModbusTimeoutError("silent"))
        client = MeltemModbusClient(link.for_unit, port="/dev/test", max_requests_per_second=1000)
        entry = MockConfigEntry(domain=DOMAIN, title="Meltem", version=1, source="user")
        entry.add_to_hass(hass)
        coordinator = MeltemDataUpdateCoordinator(
            hass,
            config_entry=entry,
            client=client,
            rooms=[_ROOM_1, _ROOM_2],
            max_requests_per_second=1000,
        )
        coordinator.data = {
            "unit_1": RoomState(target_level=30),
            "unit_2": RoomState(target_level=40),
        }
        coordinator.room_runtime("unit_1").read_failures = 2
        skip_startup(coordinator)
        for job in coordinator.poll_jobs:
            job.next_due = 0.0 if job.room_key == "unit_1" and job.key == "rf" else float("inf")

        async def _poll_rf() -> None:
            allow_next_read(coordinator)
            for job in coordinator.poll_jobs:
                if job.room_key == "unit_1" and job.key == "rf":
                    job.next_due = 0.0
            coordinator.data = await run_tick(coordinator)
            clock.now += 1

        await _poll_rf()
        first = coordinator.safe_data["unit_1"]
        assert not first.read_health_for("rf_last_seen").last_error_timed_out
        assert first.read_health_for("rf_signal").last_error_timed_out
        assert coordinator.room_runtime("unit_1").read_failures == 2
        # Bridge-local RF answers neither count as a unit answer nor end the streak.
        assert client.seconds_since_successful_read(_ROOM_1.slave) is None
        assert coordinator.room_available("unit_1")
        assert coordinator.room_available("unit_2")
        assert coordinator.backoff_seconds is None

        first_reads = [event.address for event in unit.read_events]
        await _poll_rf()
        second = coordinator.safe_data["unit_1"]
        assert second.read_health_for("rf_last_seen") == first.read_health_for("rf_last_seen")
        assert second.read_health_for("rf_fault") == first.read_health_for("rf_fault")
        assert second.read_health_for("value_error") == first.read_health_for("value_error")
        assert second.read_health_for("rf_signal").consecutive_failures == 1
        assert unit.read_events[len(first_reads) :] == []
        assert coordinator.room_runtime("unit_1").read_failures == 2
        assert coordinator.room_available("unit_1")
        assert coordinator._consecutive_transport_failures == 0

        for _ in range(2):
            await _poll_rf()
        assert coordinator.room_available("unit_1")
        assert coordinator.room_available("unit_2")
        assert coordinator.backoff_seconds is None

        for register in refused_registers:
            unit.fail_read(register, None)
        unit.fail_read(REGISTER_RF_SIGNAL_STRENGTH, None)
        for key in client._component_backoff.until:
            client._component_backoff.until[key] = time.monotonic() - 1
        await _poll_rf()
        recovered = coordinator.safe_data["unit_1"]
        assert recovered.group_read_health == second.group_read_health
        assert coordinator.room_runtime("unit_1").read_failures == 2
        assert client.seconds_since_successful_read(_ROOM_1.slave) is None
        assert coordinator.room_available("unit_1")
        assert coordinator.room_available("unit_2")
        assert coordinator.backoff_seconds is None
        client.shutdown()

    async def test_backed_off_flow_job_preserves_room_failure_streak_until_success(
        self, hass: HomeAssistant
    ) -> None:
        link = MockModbusConnection()
        unit = link.for_unit(_ROOM_1.slave)
        unit.holding.update(
            {
                REGISTER_EXTRACT_AIR_FLOW: [30, 30],
                REGISTER_MODE_STATUS: [3, 60, 0],
            }
        )
        unit.fail_read(REGISTER_EXTRACT_AIR_FLOW, AcknowledgeError())
        unit.fail_read(REGISTER_MODE_STATUS, AcknowledgeError())
        client = MeltemModbusClient(link.for_unit, port="/dev/test")
        entry = MockConfigEntry(domain=DOMAIN, title="Meltem", version=1, source="user")
        entry.add_to_hass(hass)
        coordinator = MeltemDataUpdateCoordinator(
            hass,
            config_entry=entry,
            client=client,
            rooms=[_ROOM_1],
            max_requests_per_second=2.0,
        )
        coordinator.data = {"unit_1": RoomState(target_level=30)}
        coordinator.room_runtime("unit_1").read_failures = 2
        job = _due_job("flow")

        first = await run_job(coordinator, coordinator.data, job)
        assert coordinator.room_runtime("unit_1").read_failures == 3
        assert not coordinator.room_available("unit_1")
        reads_after_failure = len(unit.read_events)

        second = await run_job(coordinator, first, job)

        assert len(unit.read_events) == reads_after_failure
        assert coordinator.room_runtime("unit_1").read_failures == 3
        assert not coordinator.room_available("unit_1")

        client._component_backoff.until[(_ROOM_1.slave, "airflow")] = time.monotonic() - 1
        client._mode_backoff.until[(_ROOM_1.slave, "mode_status")] = time.monotonic() - 1
        unit.fail_read(REGISTER_EXTRACT_AIR_FLOW, None)
        unit.fail_read(REGISTER_MODE_STATUS, None)

        recovered = await run_job(coordinator, second, job)

        assert recovered["unit_1"].read_health_for("flow").last_error is None
        assert coordinator.room_runtime("unit_1").read_failures == 0
        assert coordinator.room_available("unit_1")
        client.shutdown()

    async def test_fully_skipped_job_does_not_clear_gateway_backoff(
        self, hass: HomeAssistant
    ) -> None:
        link = MockModbusConnection()
        unit = link.for_unit(_ROOM_1.slave)
        unit.fail_read(REGISTER_EXTRACT_AIR_FLOW, AcknowledgeError())
        unit.fail_read(REGISTER_MODE_STATUS, AcknowledgeError())
        client = MeltemModbusClient(link.for_unit, port="/dev/test", max_requests_per_second=1000)
        entry = MockConfigEntry(domain=DOMAIN, title="Meltem", version=1, source="user")
        entry.add_to_hass(hass)
        coordinator = MeltemDataUpdateCoordinator(
            hass,
            config_entry=entry,
            client=client,
            rooms=[_ROOM_1],
            max_requests_per_second=1000,
        )
        coordinator.data = await run_job(
            coordinator,
            {"unit_1": RoomState(target_level=30, supply_air_flow=30)},
            _due_job("flow"),
        )
        reads_before = len(unit.read_events)
        for _ in range(3):
            coordinator._on_transport_failure()
        assert coordinator.backoff_seconds == TRANSPORT_BACKOFF_START_SECONDS
        skip_startup(coordinator)
        for job in coordinator.poll_jobs:
            job.next_due = 0.0 if job.key == "flow" else float("inf")
        allow_next_read(coordinator)

        coordinator.data = await run_tick(coordinator)

        assert len(unit.read_events) == reads_before
        assert coordinator._consecutive_transport_failures == 3
        assert coordinator.backoff_seconds == TRANSPORT_BACKOFF_START_SECONDS
        client.shutdown()

    @pytest.mark.parametrize("secondary_timeout", [False, True], ids=("answered", "timeout"))
    async def test_secondary_read_tracks_link_without_clearing_flow_failure(
        self, hass: HomeAssistant, secondary_timeout: bool
    ) -> None:
        link = MockModbusConnection()
        unit = link.for_unit(_ROOM_1.slave)
        unit.holding[REGISTER_MODE_STATUS] = [3, 60, 0]
        unit.fail_read(REGISTER_EXTRACT_AIR_FLOW, AcknowledgeError())
        client = MeltemModbusClient(link.for_unit, port="/dev/test", max_requests_per_second=1000)
        entry = MockConfigEntry(domain=DOMAIN, title="Meltem", version=1, source="user")
        entry.add_to_hass(hass)
        coordinator = MeltemDataUpdateCoordinator(
            hass,
            config_entry=entry,
            client=client,
            rooms=[_ROOM_1],
            max_requests_per_second=1000,
        )
        coordinator.data = await run_job(
            coordinator, {"unit_1": RoomState(supply_air_flow=30)}, _due_job("flow")
        )
        assert coordinator.room_runtime("unit_1").read_failures == 1
        if secondary_timeout:
            unit.fail_read(REGISTER_MODE_STATUS, ModbusTimeoutError("silent"))
        reads_before = len(unit.read_events)
        for _ in range(2 if secondary_timeout else 3):
            coordinator._on_transport_failure()
        skip_startup(coordinator)
        for job in coordinator.poll_jobs:
            job.next_due = 0.0 if job.key == "flow" else float("inf")
        allow_next_read(coordinator)

        coordinator.data = await run_tick(coordinator)

        assert [
            event.address
            for event in unit.read_events[reads_before:]
            if event.register_type == "holding"
        ] == [REGISTER_MODE_STATUS]
        assert coordinator.room_runtime("unit_1").read_failures == 1
        assert coordinator._consecutive_transport_failures == (3 if secondary_timeout else 0)
        assert coordinator.backoff_seconds == (
            TRANSPORT_BACKOFF_START_SECONDS if secondary_timeout else None
        )
        client.shutdown()

    async def test_backed_off_component_does_not_add_failed_group_counts(
        self, hass: HomeAssistant
    ) -> None:
        link = MockModbusConnection()
        unit = link.for_unit(_ROOM_1.slave)
        unit.holding[41020] = [30, 30]
        unit.fail_read(41020, AcknowledgeError())
        client = MeltemModbusClient(link.for_unit, port="/dev/test")
        entry = MockConfigEntry(domain=DOMAIN, title="Meltem", version=1, source="user")
        entry.add_to_hass(hass)
        cumulative = CumulativeDiagnostics(MagicMock(), None)
        coordinator = MeltemDataUpdateCoordinator(
            hass,
            config_entry=entry,
            client=client,
            rooms=[_ROOM_1],
            max_requests_per_second=2.0,
            cumulative_diagnostics=cumulative,
        )

        first = await run_job(coordinator, {}, _due_job("flow"))
        first_health = first["unit_1"].read_health_for("flow")
        assert cumulative.failed_read_groups == 1
        failures_after_attempt = cumulative.request_failures

        second = await run_job(coordinator, first, _due_job("flow"))

        assert cumulative.failed_read_groups == 1
        assert cumulative.request_failures == failures_after_attempt
        assert second["unit_1"].read_health_for("flow") == first_health
        client.shutdown()

    async def test_airflow_timeouts_do_not_recount_a_backed_off_mode(
        self, hass: HomeAssistant
    ) -> None:
        link = MockModbusConnection()
        unit = link.for_unit(_ROOM_1.slave)
        unit.holding[REGISTER_EXTRACT_AIR_FLOW] = [30, 30]
        unit.holding[REGISTER_MODE_STATUS] = [3, 60, 0]
        unit.fail_read(REGISTER_MODE_STATUS, AcknowledgeError())
        client = MeltemModbusClient(link.for_unit, port="/dev/test", max_requests_per_second=1000)
        entry = MockConfigEntry(domain=DOMAIN, title="Meltem", source="user")
        entry.add_to_hass(hass)
        cumulative = CumulativeDiagnostics(MagicMock(), None)
        coordinator = MeltemDataUpdateCoordinator(
            hass,
            config_entry=entry,
            client=client,
            rooms=[_ROOM_1],
            max_requests_per_second=1000,
            cumulative_diagnostics=cumulative,
        )
        try:
            state = await run_job(coordinator, {}, _due_job("flow"))
            original = cumulative.diagnostics()["read_failures"]
            assert original == {"unit_1": {"flow_control": 1, "intensive": 1}}
            unit.fail_read(REGISTER_EXTRACT_AIR_FLOW, ModbusTimeoutError("silent"))
            for _ in range(2):
                state = await run_job(coordinator, state, _due_job("flow"))
            assert cumulative.diagnostics()["read_failures"] == {
                "unit_1": {"flow": 1, "flow_control": 1, "intensive": 1}
            }
        finally:
            client.shutdown()

    async def test_successful_read_merges_state(
        self,
        hass: HomeAssistant,
    ) -> None:
        coordinator, client = _build(hass, _ROOM_1, _ROOM_2)
        client.next_read_state = RoomState(target_level=77)
        previous = {
            "unit_1": RoomState(target_level=10),
            "unit_2": RoomState(target_level=20),
        }

        result = await run_job(coordinator, previous, _due_job("flow"))

        assert result["unit_1"].target_level == 77
        assert result["unit_2"].target_level == 20

    async def test_pending_balanced_write_reads_exact_target_on_airflow_poll(
        self, hass: HomeAssistant
    ) -> None:
        coordinator, client = _build_with_fresh_airflow(hass)
        coordinator.write_confirmations.record_pending("unit_1", "airflow_levels", (65, 65))
        fresh = dt_util.utcnow()
        client.next_read_state = RoomState(operation_mode="manual").with_read_health(
            "flow_control",
            ReadHealth(last_attempt=fresh, last_successful_read=fresh),
        )
        client.next_balanced_target_readback = 65

        result = await run_job(coordinator, {}, _due_job("flow"))

        assert client.target_read_calls == ["unit_1"]
        assert result["unit_1"].balanced_target_readback == 65
        coordinator._confirm_pending_writes(result)
        assert (
            coordinator.write_confirmations.by_room["unit_1"]["airflow_levels"].status
            == "confirmed"
        )

    async def test_balanced_write_mismatch_can_recover_with_later_exact_readback(
        self, hass: HomeAssistant
    ) -> None:
        coordinator, client = _build_with_fresh_airflow(hass)
        coordinator.write_confirmations.record_pending("unit_1", "airflow_levels", (63, 63))
        first_read = dt_util.utcnow()
        client.next_read_state = RoomState(
            operation_mode="unbalanced",
            target_level=63,
            extract_target_level=60,
        ).with_read_health(
            "flow_control",
            ReadHealth(last_attempt=first_read, last_successful_read=first_read),
        )

        first_result = await run_job(coordinator, {}, _due_job("flow"))
        coordinator._confirm_pending_writes(first_result)

        confirmation = coordinator.write_confirmations.by_room["unit_1"]["airflow_levels"]
        assert confirmation.status == "mismatch"
        assert confirmation.actual_value == (63, 60)
        assert client.target_read_calls == []

        second_read = dt_util.utcnow()
        client.next_read_state = RoomState(operation_mode="manual").with_read_health(
            "flow_control",
            ReadHealth(last_attempt=second_read, last_successful_read=second_read),
        )
        client.next_balanced_target_readback = 63

        second_result = await run_job(coordinator, first_result, _due_job("flow"))
        coordinator._confirm_pending_writes(second_result)

        confirmation = coordinator.write_confirmations.by_room["unit_1"]["airflow_levels"]
        assert client.target_read_calls == ["unit_1"]
        assert second_result["unit_1"].balanced_target_readback == 63
        assert confirmation.status == "confirmed"
        assert confirmation.actual_value == (63, 63)

    async def test_poll_without_pending_balanced_write_skips_target_register(
        self, hass: HomeAssistant
    ) -> None:
        coordinator, client = _build_with_fresh_airflow(hass)
        client.next_read_state = _fresh_manual_state()

        with patch.object(
            coordinator,
            "_read_balanced_target_if_pending",
            wraps=coordinator._read_balanced_target_if_pending,
        ) as check_target:
            await run_job(coordinator, {}, _due_job("flow"))

        check_target.assert_awaited_once()
        assert client.target_read_calls == []

    async def test_unbalanced_write_skips_balanced_target_register(
        self, hass: HomeAssistant
    ) -> None:
        coordinator, client = _build_with_fresh_airflow(hass)
        coordinator.write_confirmations.record_pending("unit_1", "airflow_levels", (40, 60))
        fresh = dt_util.utcnow()
        client.next_read_state = RoomState(
            operation_mode="unbalanced",
            target_level=40,
            extract_target_level=60,
        ).with_read_health(
            "flow_control",
            ReadHealth(last_attempt=fresh, last_successful_read=fresh),
        )

        with patch.object(
            coordinator,
            "_read_balanced_target_if_pending",
            wraps=coordinator._read_balanced_target_if_pending,
        ) as check_target:
            await run_job(coordinator, {}, _due_job("flow"))

        check_target.assert_awaited_once()
        assert client.target_read_calls == []

    async def test_unreadable_target_register_does_not_confirm_from_status_or_measured_airflow(
        self, hass: HomeAssistant
    ) -> None:
        coordinator, client = _build_with_fresh_airflow(hass)
        fresh = dt_util.utcnow()
        client.next_read_state = RoomState(
            operation_mode="manual",
            target_level=65,
            balanced_target_readback=None,
        ).with_read_health(
            "flow_control",
            ReadHealth(last_attempt=fresh, last_successful_read=fresh),
        )
        client.target_read_error = MeltemModbusError("read 41121 refused")
        coordinator.write_confirmations.record_pending("unit_1", "airflow_levels", (65, 65))

        result = await run_job(coordinator, {}, _due_job("flow"))
        coordinator._confirm_pending_writes(result)

        assert client.target_read_calls == ["unit_1"]
        assert result["unit_1"].balanced_target_readback is None
        assert (
            coordinator.write_confirmations.by_room["unit_1"]["airflow_levels"].status == "pending"
        )

    async def test_target_register_is_not_read_after_the_health_retention(
        self, hass: HomeAssistant
    ) -> None:
        coordinator, client = _build_with_fresh_airflow(hass)
        client.next_read_state = _fresh_manual_state()
        coordinator.write_confirmations.record_pending("unit_1", "airflow_levels", (65, 65))
        coordinator.write_confirmations.set_status("unit_1", "airflow_levels", "mismatch")
        confirmation = coordinator.write_confirmations.by_room["unit_1"]["airflow_levels"]
        coordinator.write_confirmations.by_room["unit_1"]["airflow_levels"] = replace(
            confirmation,
            started_at=confirmation.started_at
            - timedelta(seconds=WRITE_HEALTH_RETENTION_SECONDS + 1),
        )

        with patch.object(
            coordinator,
            "_read_balanced_target_if_pending",
            wraps=coordinator._read_balanced_target_if_pending,
        ) as check_target:
            await run_job(coordinator, {}, _due_job("flow"))

        check_target.assert_awaited_once()
        assert client.target_read_calls == []

    async def test_failing_target_register_warns_once_per_failing_streak(
        self, hass: HomeAssistant
    ) -> None:
        coordinator, client = _build_with_fresh_airflow(hass)
        client.next_read_state = _fresh_manual_state()
        coordinator.write_confirmations.record_pending("unit_1", "airflow_levels", (65, 65))
        refused = MeltemModbusError("read 41121 refused")

        with patch("custom_components.meltem_ventilation.coordinator._LOGGER.log") as log:
            for error in (refused, refused, None, refused):
                client.target_read_error = error
                await run_job(coordinator, {}, _due_job("flow"))

        assert len(client.target_read_calls) == 4
        assert [call.args[0] for call in log.call_args_list] == [
            logging.WARNING,
            logging.DEBUG,
            logging.WARNING,
        ]

    async def test_quick_mode_code_in_target_register_leaves_the_write_pending(
        self, hass: HomeAssistant
    ) -> None:
        coordinator, client = _build_with_fresh_airflow(hass)
        client.next_read_state = _fresh_manual_state()
        client.next_balanced_target_readback = None
        coordinator.write_confirmations.record_pending("unit_1", "airflow_levels", (65, 65))

        result = await run_job(coordinator, {}, _due_job("flow"))
        coordinator._confirm_pending_writes(result)

        assert client.target_read_calls == ["unit_1"]
        assert result["unit_1"].balanced_target_readback is None
        assert (
            coordinator.write_confirmations.by_room["unit_1"]["airflow_levels"].status == "pending"
        )

    async def test_unverifiable_balanced_write_is_judged_after_intensive_ends(
        self, hass: HomeAssistant
    ) -> None:
        coordinator, client = _build_with_fresh_airflow(hass)
        coordinator.write_confirmations.record_pending("unit_1", "airflow_levels", (65, 65))
        client.next_read_state = replace(_fresh_manual_state(), intensive_active=True)

        during = await run_job(coordinator, {}, _due_job("flow"))
        coordinator._confirm_pending_writes(during)

        assert client.target_read_calls == []
        assert (
            coordinator.write_confirmations.by_room["unit_1"]["airflow_levels"].status
            == "unverifiable"
        )

        client.next_read_state = _fresh_manual_state()
        client.next_balanced_target_readback = 65
        after = await run_job(coordinator, during, _due_job("flow"))
        coordinator._confirm_pending_writes(after)

        assert client.target_read_calls == ["unit_1"]
        assert (
            coordinator.write_confirmations.by_room["unit_1"]["airflow_levels"].status
            == "confirmed"
        )

    async def test_failed_read_preserves_previous(
        self,
        hass: HomeAssistant,
    ) -> None:
        coordinator, client = _build(hass)
        client.fail_rooms = {"unit_1"}
        previous = {"unit_1": RoomState(target_level=55)}

        result = await run_job(coordinator, previous, _due_job("flow"))

        assert result["unit_1"].target_level == 55
        assert result["unit_1"].read_health_for("flow").consecutive_failures == 1

    async def test_failed_status_job_only_increments_status_health(
        self,
        hass: HomeAssistant,
    ) -> None:
        coordinator, client = _build(hass)
        client.fail_rooms = {"unit_1"}
        flow_health = replace(_read_ok(dt_util.utcnow()), consecutive_failures=1)
        previous = {"unit_1": RoomState().with_read_health("flow", flow_health)}

        state = (await run_job(coordinator, previous, _due_job("status")))["unit_1"]

        assert state.read_health_for("flow") == flow_health
        assert state.read_health_for("status").consecutive_failures == 1
        assert state.read_health_for("status").last_error == "boom: unit_1"

    @pytest.mark.parametrize("group", JOB_GROUPS, ids=lambda group: group.key)
    async def test_each_read_group_failure_is_isolated_and_recovers(
        self,
        hass: HomeAssistant,
        group: JobGroup,
    ) -> None:
        room = replace(_ROOM_1, profile="ii_fc_voc")
        coordinator, client = _build(hass, room)
        read_groups = group.refresh_plan.read_groups()
        neighbor_group = "flow" if "status" in read_groups else "status"
        previous_time = dt_util.utcnow()
        neighbor_health = ReadHealth(
            last_attempt=previous_time,
            last_successful_read=previous_time,
            consecutive_failures=1,
            last_error="neighbor group failed",
        )
        previous_state = _with_health(
            RoomState(target_level=55), read_groups, _read_ok(previous_time)
        ).with_read_health(neighbor_group, neighbor_health)
        job = _due_job(group.key, room.key)
        client.fail_rooms = {room.key}

        failed_states = await run_job(coordinator, {room.key: previous_state}, job)
        failed_state = failed_states[room.key]

        assert failed_state.target_level == 55
        for group_key in read_groups:
            health = failed_state.read_health_for(group_key)
            assert health.consecutive_failures == 1
            assert health.last_error == f"boom: {room.key}"
        assert failed_state.read_health_for(neighbor_group) == neighbor_health

        client.fail_rooms.clear()
        recovery_time = dt_util.utcnow()
        client.next_read_state = _with_health(failed_state, read_groups, _read_ok(recovery_time))

        recovered_state = (await run_job(coordinator, failed_states, job))[room.key]

        for group_key in read_groups:
            health = recovered_state.read_health_for(group_key)
            assert health.consecutive_failures == 0
            assert health.last_error is None
            assert health.last_successful_read == recovery_time
        assert recovered_state.read_health_for(neighbor_group) == neighbor_health
        assert recovered_state.target_level == 55

    async def test_repeated_failed_flow_reads_mark_airflow_stale(
        self,
        hass: HomeAssistant,
    ) -> None:
        coordinator, client = _build(hass)
        client.fail_rooms = {"unit_1"}
        state_map = {"unit_1": RoomState(target_level=55)}
        job = _due_job("flow")

        for _ in range(3):
            state_map = await run_job(coordinator, state_map, job)

        coordinator.data = state_map
        assert state_map["unit_1"].read_health_for("flow").consecutive_failures == 3
        assert coordinator.read_group_stale("unit_1", "flow") is True
        assert coordinator.read_group_available("unit_1", "flow") is False

    async def test_other_job_success_does_not_clear_airflow_failures(
        self,
        hass: HomeAssistant,
    ) -> None:
        coordinator, client = _build(hass)
        flow_health = ReadHealth(
            last_attempt=dt_util.utcnow(),
            consecutive_failures=2,
            last_error="airflow block read failed",
        )
        previous = RoomState().with_read_health("flow", flow_health)
        client.next_read_state = replace(previous, error_status=False)

        result = await run_job(coordinator, {"unit_1": previous}, _due_job("status"))

        assert result["unit_1"].read_health_for("flow").consecutive_failures == 2

    def test_airflow_data_becomes_stale_after_success_timestamp_expires(
        self,
        hass: HomeAssistant,
    ) -> None:
        coordinator, _ = _build(hass)
        expired = _read_ok(dt_util.utcnow() - timedelta(seconds=31))
        coordinator.data = {
            "unit_1": RoomState(supply_air_flow=30).with_read_health("flow", expired)
        }

        assert coordinator.read_group_stale("unit_1", "flow") is True
        assert coordinator.read_group_available("unit_1", "flow") is False

    def test_airflow_health_metadata_does_not_count_as_room_data(self) -> None:
        failed_read = ReadHealth(
            last_attempt=dt_util.utcnow(),
            consecutive_failures=1,
            last_error="read failed",
        )

        assert not RoomState().with_read_health("flow", failed_read).has_data

    def test_failed_but_never_read_group_is_not_reported_as_healthy(
        self,
        hass: HomeAssistant,
    ) -> None:
        coordinator, _ = _build(hass)
        health = ReadHealth(
            last_attempt=dt_util.utcnow(), consecutive_failures=1, last_error="read failed"
        )
        coordinator.data = {"unit_1": RoomState().with_read_health("flow", health)}
        coordinator.last_update_success = True
        sensor = MeltemDataHealthBinarySensor(coordinator, _ROOM_1)

        assert sensor.is_on is None
        flow_attributes = sensor.extra_state_attributes["flow"]
        assert isinstance(flow_attributes, Mapping)
        assert flow_attributes["stale"] is None

        coordinator.data = {
            "unit_1": RoomState().with_read_health("flow", replace(health, consecutive_failures=3))
        }
        with patch.object(sensor, "async_write_ha_state"):
            sensor._handle_coordinator_update()
        assert sensor.is_on is True


# ---------------------------------------------------------------------------
#  Tick interval
# ---------------------------------------------------------------------------


class TestTickInterval:
    @pytest.mark.parametrize(
        ("max_requests_per_second", "interval_seconds"), [(2.0, 0.5), (0.2, 5.0)]
    )
    def test_initial_interval_follows_the_request_rate(
        self,
        hass: HomeAssistant,
        max_requests_per_second: float,
        interval_seconds: float,
    ) -> None:
        coordinator, _ = _build(hass, max_requests_per_second=max_requests_per_second)

        assert coordinator.next_poll_delay == interval_seconds
        assert not coordinator.poll_scheduled


# ---------------------------------------------------------------------------
#  Listener updates per unit
# ---------------------------------------------------------------------------


class TestListenerScope:
    async def test_a_read_job_only_updates_the_entities_of_its_unit(
        self, hass: HomeAssistant, clock: SimpleNamespace
    ) -> None:
        coordinator, client = _build(hass, _ROOM_1, _ROOM_2)
        seen: list[tuple[bool, bool]] = []
        coordinator.async_add_listener(
            lambda: seen.append(
                (coordinator.room_updated("unit_1"), coordinator.room_updated("unit_2"))
            )
        )

        await coordinator.async_refresh()
        clock.now += _update_interval_seconds(coordinator)
        await coordinator.async_refresh()
        client.fresh_read_groups = ("flow",)
        await coordinator._async_refresh_room_after_write(_ROOM_1)

        assert client.read_calls[:2] == [
            ("unit_1", AIRFLOW_REFRESH_PLAN),
            ("unit_2", AIRFLOW_REFRESH_PLAN),
        ]
        assert seen[:3] == [(True, False), (False, True), (True, False)]
        # Outside a scoped update, every unit counts as updated.
        assert coordinator.room_updated("unit_1")
        assert coordinator.room_updated("unit_2")
        await coordinator.async_shutdown()

    async def test_an_update_after_a_failure_reaches_every_unit(
        self, hass: HomeAssistant, clock: SimpleNamespace
    ) -> None:
        coordinator, client = _build(hass, _ROOM_1, _ROOM_2)
        seen: list[tuple[bool, bool]] = []
        coordinator.async_add_listener(
            lambda: seen.append(
                (coordinator.room_updated("unit_1"), coordinator.room_updated("unit_2"))
            )
        )
        client.fail_rooms = {"unit_1"}

        await coordinator.async_refresh()
        assert not coordinator.last_update_success
        client.fail_rooms = set()
        clock.now += _update_interval_seconds(coordinator)
        await coordinator.async_refresh()

        assert coordinator.last_update_success
        assert seen == [(True, True), (True, True)]
        await coordinator.async_shutdown()

    async def test_time_dependent_diagnostics_share_one_timer(self, hass: HomeAssistant) -> None:
        coordinator, _ = _build(hass)
        calls: list[str] = []

        with patch(
            "custom_components.meltem_ventilation.coordinator.async_track_time_interval",
            wraps=async_track_time_interval,
        ) as track:
            remove_first = coordinator.async_add_diagnostic_listener(lambda: calls.append("a"))
            remove_second = coordinator.async_add_diagnostic_listener(lambda: calls.append("b"))
            assert track.call_count == 1

            async_fire_time_changed(hass, dt_util.utcnow() + LOCAL_DIAGNOSTIC_REFRESH_INTERVAL)
            await hass.async_block_till_done()
            assert calls == ["a", "b"]

            remove_first()
            async_fire_time_changed(hass, dt_util.utcnow() + 2 * LOCAL_DIAGNOSTIC_REFRESH_INTERVAL)
            await hass.async_block_till_done()
            assert calls == ["a", "b", "b"]

            remove_second()
            async_fire_time_changed(hass, dt_util.utcnow() + 3 * LOCAL_DIAGNOSTIC_REFRESH_INTERVAL)
            await hass.async_block_till_done()
            assert calls == ["a", "b", "b"]

            coordinator.async_add_diagnostic_listener(lambda: None)()
            assert track.call_count == 2
        await coordinator.async_shutdown()


# ---------------------------------------------------------------------------
#  Own poll scheduler
# ---------------------------------------------------------------------------


class TestPollScheduler:
    async def test_each_tick_schedules_the_next_one_after_the_planned_delay(
        self, hass: HomeAssistant, clock: SimpleNamespace
    ) -> None:
        coordinator, client = _build(hass, max_requests_per_second=0.2)

        await coordinator.async_poll()

        assert client.read_calls == [("unit_1", AIRFLOW_REFRESH_PLAN)]
        assert coordinator.poll_scheduled
        delay = coordinator.next_poll_delay
        assert delay == 5.0
        clock.now += delay
        async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=delay - 1.0))
        await hass.async_block_till_done()
        assert len(client.read_calls) == 1

        async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=delay + 0.1))
        await hass.async_block_till_done()
        assert len(client.read_calls) == 2
        assert coordinator.poll_scheduled
        await coordinator.async_shutdown()

    async def test_a_new_request_rate_moves_a_waiting_tick(self, hass: HomeAssistant) -> None:
        coordinator, _ = _build(hass, max_requests_per_second=0.5)
        coordinator.data = {"unit_1": RoomState(target_level=30)}
        skip_startup(coordinator)
        _set_jobs_due(coordinator, time.monotonic() - 1.0)
        await coordinator.async_poll()
        assert coordinator.next_poll_delay == 2.0

        with patch(
            "custom_components.meltem_ventilation.coordinator.async_call_later",
            wraps=async_call_later,
        ) as call_later:
            coordinator.update_request_rate(4.0)

        assert coordinator.next_poll_delay == 0.25
        assert call_later.call_args.args[1] == 0.25
        assert coordinator.poll_scheduled
        await coordinator.async_shutdown()

    async def test_an_unexpected_error_keeps_the_scheduler_running(
        self, hass: HomeAssistant, caplog: pytest.LogCaptureFixture
    ) -> None:
        coordinator, client = _build(hass)

        async def _crash(*_args: object) -> RoomRead:
            raise RuntimeError("bug")

        client.read_room = _crash  # type: ignore[method-assign]

        await coordinator.async_poll()

        assert not coordinator.last_update_success
        assert "Unexpected error fetching Meltem Modbus data" in caplog.text
        assert coordinator.poll_scheduled
        await coordinator.async_shutdown()

    async def test_home_assistant_stopping_ends_polling(
        self, hass: HomeAssistant, clock: SimpleNamespace
    ) -> None:
        """Home Assistant does not unload entries when it stops, so the ticks end themselves."""
        coordinator, client = _build(hass, max_requests_per_second=10.0)
        await coordinator.async_poll()
        assert coordinator.poll_scheduled

        hass.set_state(CoreState.stopping)
        clock.now += 60
        async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=60))
        await hass.async_block_till_done()
        await coordinator.async_poll()

        assert len(client.read_calls) == 1
        assert not coordinator.poll_scheduled
        hass.set_state(CoreState.running)
        await coordinator.async_shutdown()

    async def test_disabled_polling_leaves_refreshes_to_the_user(self, hass: HomeAssistant) -> None:
        coordinator, client = _build(hass)
        entry = coordinator.config_entry
        assert entry is not None
        hass.config_entries.async_update_entry(entry, pref_disable_polling=True)

        await coordinator.async_poll()

        assert len(client.read_calls) == 1
        assert not coordinator.poll_scheduled
        await coordinator.async_shutdown()

    async def test_shutdown_stops_the_scheduler(self, hass: HomeAssistant) -> None:
        coordinator, client = _build(hass)
        await coordinator.async_poll()
        assert coordinator.poll_scheduled

        await coordinator.async_shutdown()
        assert not coordinator.poll_scheduled

        await coordinator.async_poll()
        assert not coordinator.poll_scheduled
        async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=60))
        await hass.async_block_till_done()
        assert len(client.read_calls) == 1

    async def test_a_due_tick_runs_as_a_background_task_of_the_entry(
        self, hass: HomeAssistant
    ) -> None:
        coordinator, _ = _build(hass)
        entry = coordinator.config_entry
        assert entry is not None

        with patch.object(
            entry, "async_create_background_task", wraps=entry.async_create_background_task
        ) as create_task:
            coordinator._async_schedule_poll(0.0)
            async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=1))
            await hass.async_block_till_done()

        assert create_task.call_count == 1
        assert create_task.call_args.args[2] == "Meltem poll tick"
        await coordinator.async_shutdown()
