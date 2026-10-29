"""Tests for integration setup, unload, and data migration in __init__.py."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Iterator
from copy import deepcopy
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, State
from homeassistant.exceptions import (
    ConfigEntryError,
    ConfigEntryNotReady,
    HomeAssistantError,
)
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.typing import UNDEFINED
from modbus_connection import (
    AcknowledgeError,
    ClientClosedError,
    IllegalDataValueError,
    ModbusConnectionError,
    ModbusTimeoutError,
)
from modbus_connection.mock import MockModbusConnection, WriteEvent
from pytest_homeassistant.common import MockConfigEntry

from custom_components.meltem_ventilation import (
    async_migrate_entry,
    async_remove_entry,
    async_setup_entry,
    async_unload_entry,
)
from custom_components.meltem_ventilation.const import (
    BASE_SUPPORTED_ENTITY_KEYS,
    CONF_MAX_REQUESTS_PER_SECOND,
    CONF_PORT,
    CONF_ROOMS,
    DOMAIN,
    FIXED_TIMEOUT,
    MODE_MANUAL,
    MODE_UNBALANCED,
    PLATFORMS,
    PRESET_MODE_CODE_HIGH,
    PRESET_MODE_CODE_INTENSIVE,
    REGISTER_APPLY,
    REGISTER_CURRENT_LEVEL,
    REGISTER_EXTRACT_AIR_FLOW,
    REGISTER_EXTRACT_AIR_TARGET_LEVEL,
    REGISTER_HUMIDITY_STARTING_POINT,
    REGISTER_MODE,
    REGISTER_MODE_STATUS,
    REGISTER_PRESET_MODE,
)
from custom_components.meltem_ventilation.cumulative_diagnostics import CumulativeDiagnostics
from custom_components.meltem_ventilation.modbus_helpers import (
    MeltemConnectionError,
    MeltemModbusError,
    build_serial_params,
    supported_entity_keys_for_profile,
)
from custom_components.meltem_ventilation.models import (
    MeltemRuntimeData,
)

# ---------------------------------------------------------------------------
#  Helpers
# ---------------------------------------------------------------------------

_INIT = "custom_components.meltem_ventilation"
_PORT = "/dev/serial/by-id/test-device"
_TEST_GATEWAY_MAC = "02:00:00:00:00:01"
_ROOM = {
    "key": "unit_1",
    "name": "Unit 1",
    "slave": 2,
    "profile": "ii_plain",
    "preview": "ID 123 | basic",
    "supported_entity_keys": sorted(BASE_SUPPORTED_ENTITY_KEYS),
}


def _entry(
    hass: HomeAssistant,
    *,
    port: str = _PORT,
    rooms: list[dict[str, Any]] | None = None,
    options: dict[str, Any] | None = None,
    entry_id: str = "test-entry-id",
    version: int = 1,
    minor_version: int = 4,
) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Meltem",
        data={
            CONF_PORT: port,
            CONF_MAX_REQUESTS_PER_SECOND: 2.0,
            CONF_ROOMS: deepcopy(rooms or [_ROOM]),
        },
        options=options or {},
        entry_id=entry_id,
        version=version,
        minor_version=minor_version,
        source="user",
    )
    entry.add_to_hass(hass)
    return entry


def _register_entity(
    hass: HomeAssistant, entry: MockConfigEntry, platform: str, key: str
) -> er.RegistryEntry:
    return er.async_get(hass).async_get_or_create(
        platform, DOMAIN, f"{DOMAIN}_unit_1_{key}", config_entry=entry
    )


@dataclass(frozen=True, slots=True)
class _SetupMocks:
    resolve_port: MagicMock
    client_cls: MagicMock
    coordinator_cls: MagicMock
    forward: AsyncMock


@pytest.fixture
def setup_mocks(hass: HomeAssistant) -> Iterator[_SetupMocks]:
    """Run async_setup_entry without a gateway and without platforms."""
    with (
        patch(f"{_INIT}.resolve_preferred_port_path", side_effect=lambda port: port) as resolve,
        patch(f"{_INIT}.MeltemModbusClient", autospec=True) as client_cls,
        patch(f"{_INIT}.MeltemDataUpdateCoordinator", autospec=True) as coordinator_cls,
        patch.object(hass.config_entries, "async_forward_entry_setups", new=AsyncMock()) as forward,
    ):
        client_cls.return_value.async_read_gateway_mac.return_value = None

        async def _read_gateway_mac() -> str | None:
            return await client_cls.return_value.async_read_gateway_mac()

        coordinator_cls.return_value.async_read_gateway_mac.side_effect = _read_gateway_mac
        yield _SetupMocks(resolve, client_cls, coordinator_cls, forward)


# ---------------------------------------------------------------------------
#  async_setup_entry
# ---------------------------------------------------------------------------


class TestAsyncSetupEntry:
    async def test_setup_creates_coordinator_and_forwards_platforms(
        self,
        hass: HomeAssistant,
        setup_mocks: _SetupMocks,
    ) -> None:
        entry = _entry(hass)

        with patch.object(
            entry,
            "async_create_background_task",
            side_effect=lambda _hass, coro, _name, **_kwargs: coro.close(),
        ) as create_task:
            assert await async_setup_entry(hass, entry) is True

        setup_mocks.client_cls.assert_called_once()
        setup_mocks.coordinator_cls.assert_called_once()
        setup_mocks.forward.assert_awaited_once_with(entry, PLATFORMS)
        # The first refresh runs in the background so setup stays fast.
        create_task.assert_called_once()
        assert isinstance(entry.runtime_data, MeltemRuntimeData)

    @pytest.mark.parametrize("error", [RuntimeError("platform failed"), asyncio.CancelledError()])
    async def test_setup_failure_after_coordinator_removes_runtime_data(
        self,
        hass: HomeAssistant,
        setup_mocks: _SetupMocks,
        error: BaseException,
    ) -> None:
        setup_mocks.forward.side_effect = error
        entry = _entry(hass)

        with pytest.raises(type(error)) as raised:
            await async_setup_entry(hass, entry)

        assert raised.value is error
        assert not hasattr(entry, "runtime_data")
        setup_mocks.coordinator_cls.return_value.async_shutdown.assert_awaited_once()
        setup_mocks.client_cls.return_value.shutdown.assert_called_once()

    async def test_setup_cleanup_survives_coordinator_shutdown_failure(
        self, hass: HomeAssistant, setup_mocks: _SetupMocks
    ) -> None:
        setup_mocks.forward.side_effect = RuntimeError("platform failed")
        coordinator = setup_mocks.coordinator_cls.return_value
        coordinator.async_shutdown.side_effect = RuntimeError("shutdown failed")
        entry = _entry(hass)

        with pytest.raises(RuntimeError, match="shutdown failed") as raised:
            await async_setup_entry(hass, entry)

        assert isinstance(raised.value.__context__, RuntimeError)
        assert str(raised.value.__context__) == "platform failed"
        assert not hasattr(entry, "runtime_data")
        setup_mocks.client_cls.return_value.shutdown.assert_called_once()

    @pytest.mark.parametrize(
        ("error", "raised"),
        [
            (MeltemConnectionError("no answer"), ConfigEntryNotReady),
            (
                HomeAssistantError("already in use with different link settings"),
                ConfigEntryError,
            ),
        ],
        ids=("unreachable-gateway", "port-held-with-other-link-settings"),
    )
    async def test_failed_gateway_validation_shuts_the_client_down(
        self,
        hass: HomeAssistant,
        setup_mocks: _SetupMocks,
        error: Exception,
        raised: type[Exception],
    ) -> None:
        client = setup_mocks.client_cls.return_value
        client.async_validate_gateway.side_effect = error
        entry = _entry(hass)

        with pytest.raises(raised) as err:
            await async_setup_entry(hass, entry)

        client.shutdown.assert_called_once()
        if isinstance(err.value, ConfigEntryNotReady):
            assert err.value.translation_domain == DOMAIN
            assert err.value.translation_key == "gateway_unavailable"
            assert err.value.translation_placeholders == {"port": _PORT}
        else:
            assert isinstance(err.value, ConfigEntryError)
            assert err.value.translation_domain == DOMAIN
            assert err.value.translation_key == "port_in_use"
            assert not err.value.translation_placeholders

    async def test_client_takes_its_units_from_the_modbus_integration(
        self,
        hass: HomeAssistant,
        setup_mocks: _SetupMocks,
    ) -> None:
        entry = _entry(hass)

        with patch(f"{_INIT}.async_get_unit") as get_unit:
            await async_setup_entry(hass, entry)
            unit_factory = setup_mocks.client_cls.call_args.args[0]
            unit_factory(2)

        get_unit.assert_called_once_with(hass, entry, build_serial_params(_PORT), 2)

    async def test_setup_normalizes_port_path(
        self,
        hass: HomeAssistant,
        setup_mocks: _SetupMocks,
    ) -> None:
        setup_mocks.resolve_port.side_effect = lambda _port: "/dev/serial/by-id/normalized"
        entry = _entry(hass, port="/dev/ttyACM0")

        await async_setup_entry(hass, entry)

        assert entry.data[CONF_PORT] == entry.unique_id == "/dev/serial/by-id/normalized"

    async def test_setup_derives_missing_entity_keys_from_the_profile(
        self,
        hass: HomeAssistant,
        setup_mocks: _SetupMocks,
    ) -> None:
        """Legacy rooms without stored keys get their profile's entities, no probe."""
        entry = _entry(
            hass, rooms=[{"key": "unit_1", "name": "Unit 1", "slave": 2, "profile": "ii_fc"}]
        )

        await async_setup_entry(hass, entry)

        room = setup_mocks.coordinator_cls.call_args.kwargs["rooms"][0]
        assert room.supported_entity_keys == frozenset(supported_entity_keys_for_profile("ii_fc"))
        # The stored entry is not rewritten.
        assert "supported_entity_keys" not in entry.data[CONF_ROOMS][0]

    async def test_setup_ignores_entity_keys_stored_by_older_releases(
        self,
        hass: HomeAssistant,
        setup_mocks: _SetupMocks,
    ) -> None:
        """Probe keys beyond the profile would schedule reads the unit cannot answer."""
        stored_keys = ["extract_air_flow", "humidity_starting_point"]
        entry = _entry(
            hass,
            rooms=[{**_ROOM, "profile": "ii_plain", "supported_entity_keys": stored_keys}],
        )

        await async_setup_entry(hass, entry)

        room = setup_mocks.coordinator_cls.call_args.kwargs["rooms"][0]
        assert room.supported_entity_keys == frozenset(
            supported_entity_keys_for_profile("ii_plain")
        )
        assert entry.data[CONF_ROOMS][0]["supported_entity_keys"] == stored_keys

    @pytest.mark.parametrize(
        ("platform", "key"),
        [
            pytest.param("fan", "level", id="single-fan"),
            pytest.param("number", "supply_level", id="level-number"),
            pytest.param("button", "activate_intensive", id="intensive-button"),
            pytest.param("binary_sensor", "intensive_active", id="intensive-binary-sensor"),
            pytest.param("sensor", "co2_extract_air", id="previous-profile-sensor"),
            pytest.param("number", "co2_max_level", id="previous-profile-number"),
            pytest.param("select", "operation_mode", id="previous-profile-select"),
        ],
    )
    @pytest.mark.usefixtures("setup_mocks")
    async def test_setup_removes_obsolete_entities(
        self,
        hass: HomeAssistant,
        platform: str,
        key: str,
    ) -> None:
        entry = _entry(hass)
        obsolete = _register_entity(hass, entry, platform, key)
        kept = [
            _register_entity(hass, entry, "sensor", "operating_hours"),
            _register_entity(hass, entry, "sensor", "exhaust_temperature"),
        ]

        await async_setup_entry(hass, entry)

        registry = er.async_get(hass)
        assert registry.async_get(obsolete.entity_id) is None
        assert all(registry.async_get(entity.entity_id) for entity in kept)

    @pytest.mark.usefixtures("setup_mocks")
    async def test_setup_keeps_obsolete_entities_of_other_entries(
        self,
        hass: HomeAssistant,
    ) -> None:
        """Room keys are only unique per gateway."""
        entry = _entry(hass)
        foreign = _register_entity(hass, _entry(hass, entry_id="other-entry-id"), "fan", "level")

        await async_setup_entry(hass, entry)

        assert er.async_get(hass).async_get(foreign.entity_id) is not None

    async def test_setup_respects_option_max_request_rate(
        self,
        hass: HomeAssistant,
        setup_mocks: _SetupMocks,
    ) -> None:
        entry = _entry(hass, options={CONF_MAX_REQUESTS_PER_SECOND: 5.0})

        await async_setup_entry(hass, entry)

        assert setup_mocks.coordinator_cls.call_args.kwargs["max_requests_per_second"] == 5.0
        assert setup_mocks.client_cls.call_args.kwargs["max_requests_per_second"] == 5.0


# ---------------------------------------------------------------------------
#  async_unload_entry
# ---------------------------------------------------------------------------


class TestAsyncUnloadEntry:
    @pytest.mark.parametrize("unloaded", [True, False])
    async def test_coordinator_and_client_shut_down_only_after_the_platforms(
        self,
        hass: HomeAssistant,
        unloaded: bool,
    ) -> None:
        entry = _entry(hass)
        coordinator = MagicMock()
        coordinator.async_shutdown = AsyncMock()
        entry.runtime_data = MeltemRuntimeData(coordinator=coordinator)

        with patch.object(
            hass.config_entries,
            "async_unload_platforms",
            new=AsyncMock(return_value=unloaded),
        ):
            assert await async_unload_entry(hass, entry) is unloaded

        assert coordinator.async_shutdown.await_count == int(unloaded)
        assert coordinator.client.shutdown.call_count == int(unloaded)

    @pytest.mark.parametrize("error", [RuntimeError("shutdown failed"), asyncio.CancelledError()])
    async def test_client_is_shutdown_when_coordinator_shutdown_fails(
        self, hass: HomeAssistant, error: BaseException
    ) -> None:
        entry = _entry(hass)
        coordinator = MagicMock()
        coordinator.async_shutdown = AsyncMock(side_effect=error)
        entry.runtime_data = MeltemRuntimeData(coordinator=coordinator)

        with patch.object(
            hass.config_entries, "async_unload_platforms", new=AsyncMock(return_value=True)
        ):
            with pytest.raises(type(error)) as raised:
                await async_unload_entry(hass, entry)

        assert raised.value is error
        coordinator.client.shutdown.assert_called_once()


class TestAsyncRemoveEntry:
    async def test_removes_persisted_diagnostics(self, hass: HomeAssistant) -> None:
        entry = _entry(hass)
        diagnostics = await CumulativeDiagnostics.async_create(hass, entry.entry_id)
        diagnostics.record_request("success")
        await diagnostics.async_flush()

        await async_remove_entry(hass, entry)

        restored = await CumulativeDiagnostics.async_create(hass, entry.entry_id)
        assert restored.request_attempts == 0


# ---------------------------------------------------------------------------
#  async_migrate_entry
# ---------------------------------------------------------------------------


class TestAsyncMigrateEntry:
    async def test_migration_renames_the_airflow_health_entity(self, hass: HomeAssistant) -> None:
        entry = _entry(hass, minor_version=1)
        legacy = _register_entity(hass, entry, "binary_sensor", "airflow_data_stale")

        assert await async_migrate_entry(hass, entry) is True

        migrated = er.async_get(hass).async_get(legacy.entity_id)
        assert migrated is not None
        assert migrated.unique_id == f"{DOMAIN}_unit_1_data_health"
        assert entry.minor_version == 4
        assert CONF_MAX_REQUESTS_PER_SECOND not in entry.data
        assert entry.options[CONF_MAX_REQUESTS_PER_SECOND] == 2.0

    async def test_migration_keeps_an_existing_data_health_entity(
        self, hass: HomeAssistant
    ) -> None:
        entry = _entry(hass, minor_version=1)
        legacy = _register_entity(hass, entry, "binary_sensor", "airflow_data_stale")
        current = _register_entity(hass, entry, "binary_sensor", "data_health")

        assert await async_migrate_entry(hass, entry) is True

        registry = er.async_get(hass)
        migrated_legacy = registry.async_get(legacy.entity_id)
        migrated_current = registry.async_get(current.entity_id)
        assert migrated_legacy is not None
        assert migrated_current is not None
        assert migrated_legacy.unique_id == legacy.unique_id
        assert migrated_current.unique_id == current.unique_id
        assert entry.minor_version == 4

    async def test_migration_preserves_an_existing_request_rate_option(
        self, hass: HomeAssistant
    ) -> None:
        entry = _entry(
            hass,
            minor_version=2,
            options={CONF_MAX_REQUESTS_PER_SECOND: 5.0},
        )

        assert await async_migrate_entry(hass, entry) is True

        assert CONF_MAX_REQUESTS_PER_SECOND not in entry.data
        assert entry.options[CONF_MAX_REQUESTS_PER_SECOND] == 5.0
        assert entry.minor_version == 4

    async def test_migration_moves_identity_values_from_preview(self, hass: HomeAssistant) -> None:
        entry = _entry(
            hass,
            minor_version=3,
            rooms=[{**_ROOM, "preview": "R-Code R000001 | ID 116852 | basic"}],
        )

        assert await async_migrate_entry(hass, entry) is True

        room = entry.data[CONF_ROOMS][0]
        assert room["preview"] == "R-Code R000001 | ID 116852 | basic"
        assert room["product_id"] == 116852
        assert room["app_setup_code"] == "R000001"
        assert entry.minor_version == 4

    async def test_migration_rejects_a_newer_major_version(self, hass: HomeAssistant) -> None:
        entry = _entry(hass, version=2, minor_version=1)

        assert await async_migrate_entry(hass, entry) is False


# ---------------------------------------------------------------------------
#  Device registry
# ---------------------------------------------------------------------------


@pytest.mark.usefixtures("setup_mocks")
class TestDeviceRegistrySync:
    async def test_setup_registers_the_gateway_device(
        self, hass: HomeAssistant, setup_mocks: _SetupMocks
    ) -> None:
        entry = _entry(hass)
        registry = dr.async_get(hass)
        setup_mocks.client_cls.return_value.async_read_gateway_mac.return_value = _TEST_GATEWAY_MAC

        with patch.object(
            registry, "async_get_or_create_from_info", wraps=registry.async_get_or_create_from_info
        ) as register:
            await async_setup_entry(hass, entry)
            await hass.async_block_till_done(wait_background_tasks=True)

        assert register.call_count == 2
        first_info = register.call_args_list[0].kwargs["device_info"]
        mac_info = register.call_args_list[1].kwargs["device_info"]
        assert isinstance(first_info, dr.DeviceInfo)
        assert first_info.connections is UNDEFINED
        assert isinstance(mac_info, dr.DeviceInfo)
        assert mac_info.connections == {(dr.CONNECTION_NETWORK_MAC, _TEST_GATEWAY_MAC)}
        gateway = registry.async_get_device_by_identifier((DOMAIN, entry.entry_id), entry.entry_id)
        assert gateway is not None
        assert gateway.model == "M-WRG-GW"
        assert (dr.CONNECTION_NETWORK_MAC, _TEST_GATEWAY_MAC) in gateway.connections

    async def test_failed_optional_mac_read_keeps_existing_connection(
        self, hass: HomeAssistant, setup_mocks: _SetupMocks
    ) -> None:
        entry = _entry(hass)
        registry = dr.async_get(hass)
        mac_connection = (dr.CONNECTION_NETWORK_MAC, _TEST_GATEWAY_MAC)
        registry.async_get_or_create(
            config_entry_id=entry.entry_id,
            identifiers={(DOMAIN, entry.entry_id)},
            connections={mac_connection},
        )
        setup_mocks.client_cls.return_value.async_read_gateway_mac.side_effect = MeltemModbusError(
            "register unavailable"
        )

        assert await async_setup_entry(hass, entry) is True
        await hass.async_block_till_done(wait_background_tasks=True)

        gateway = registry.async_get_device_by_identifier((DOMAIN, entry.entry_id), entry.entry_id)
        assert gateway is not None
        assert mac_connection in gateway.connections

    async def test_setup_removes_devices_of_unconfigured_units(self, hass: HomeAssistant) -> None:
        entry = _entry(hass)
        registry = dr.async_get(hass)
        configured = registry.async_get_or_create(
            config_entry_id=entry.entry_id, identifiers={(DOMAIN, "unit_1")}
        )
        dropped = registry.async_get_or_create(
            config_entry_id=entry.entry_id, identifiers={(DOMAIN, "unit_9")}
        )

        await async_setup_entry(hass, entry)

        assert registry.async_get(configured.id) is not None
        assert registry.async_get(dropped.id) is None


# ---------------------------------------------------------------------------
#  Home Assistant's shared Modbus connection
# ---------------------------------------------------------------------------


class TestSharedModbusConnection:
    """Set up through the real modbus integration, on an in-memory link."""

    @staticmethod
    def _patch_link(
        links: list[MockModbusConnection], *, gateway_silent: bool = False, two_rooms: bool = False
    ):
        def _connection(params, **kwargs) -> MockModbusConnection:
            link = MockModbusConnection()
            gateway = link.for_unit(1)
            gateway.holding.update(
                {43901: 2 if two_rooms else 1, 43902: [2, 3] if two_rooms else [2]}
            )
            if gateway_silent:
                gateway.fail_requests(ModbusTimeoutError("silent"))
            link.for_unit(2).holding.update({41020: [30, 30], 41100: [3, 60, 0]})
            if two_rooms:
                link.for_unit(3).holding.update({41020: [45, 45], 41100: [3, 90, 0]})
            links.append(link)
            return link

        return patch("homeassistant.components.modbus.connection.ModbusConnection", _connection)

    async def test_rooms_recover_independently_after_link_loss_and_acknowledgements(
        self, hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        clock = SimpleNamespace(now=time.monotonic())
        for module in ("coordinator", "modbus_client", "device.transport"):
            monkeypatch.setattr(
                f"custom_components.meltem_ventilation.{module}.time",
                SimpleNamespace(monotonic=lambda: clock.now),
            )

        async def advance(seconds: float) -> None:
            clock.now += seconds
            await asyncio.sleep(0)

        monkeypatch.setattr(
            "custom_components.meltem_ventilation.device.transport.async_sleep", advance
        )
        links: list[MockModbusConnection] = []
        room_2 = {**_ROOM, "key": "unit_2", "name": "Unit 2", "slave": 3}
        entry = _entry(
            hass,
            rooms=[_ROOM, room_2],
            options={CONF_MAX_REQUESTS_PER_SECOND: 1000.0},
        )

        with self._patch_link(links, two_rooms=True):
            assert await hass.config_entries.async_setup(entry.entry_id)
            try:
                await hass.async_block_till_done(wait_background_tasks=True)
                coordinator = entry.runtime_data.coordinator
                first, second = links[0].for_unit(2), links[0].for_unit(3)

                async def poll(room_key: str) -> None:
                    clock.now += 60
                    coordinator._startup_jobs = []
                    for job in coordinator._jobs:
                        job.next_due = (
                            0.0 if job.room_key == room_key and job.key == "flow" else float("inf")
                        )
                    coordinator._last_read_started = None
                    await coordinator.async_refresh()
                    await hass.async_block_till_done()

                await poll("unit_1")
                await poll("unit_2")
                registry = er.async_get(hass)
                first_id = registry.async_get_entity_id(
                    Platform.FAN, DOMAIN, f"{DOMAIN}_unit_1_supply_level"
                )
                second_id = registry.async_get_entity_id(
                    Platform.FAN, DOMAIN, f"{DOMAIN}_unit_2_supply_level"
                )
                assert first_id is not None and second_id is not None

                def state_for(entity_id: str) -> State:
                    state = hass.states.get(entity_id)
                    assert state is not None
                    return state

                assert state_for(first_id).attributes["percentage"] == 30
                assert state_for(second_id).attributes["percentage"] == 45

                link = links[0]
                connect = link._connect_client
                monkeypatch.setattr(
                    link,
                    "_connect_client",
                    AsyncMock(side_effect=ModbusConnectionError("link lost")),
                )
                link.simulate_connection_lost()
                assert not link.connected
                for _ in range(3):
                    await poll("unit_1")
                    await poll("unit_2")
                assert not coordinator.room_available("unit_1")
                assert not coordinator.room_available("unit_2")
                assert state_for(first_id).state == "unavailable"
                assert state_for(second_id).state == "unavailable"

                monkeypatch.setattr(link, "_connect_client", connect)
                for unit in (first, second):
                    unit.fail_read(REGISTER_EXTRACT_AIR_FLOW, AcknowledgeError())
                recycles = coordinator.client.transport_diagnostics()["link_recycles"]
                for _ in range(3):
                    await poll("unit_1")
                    await poll("unit_2")
                assert link.connected
                assert coordinator.read_group_stale("unit_1", "flow") is True
                assert coordinator.read_group_stale("unit_2", "flow") is True
                assert coordinator.client.transport_diagnostics()["link_recycles"] == recycles
                assert coordinator.safe_data["unit_1"].supply_air_flow == 30
                assert coordinator.safe_data["unit_2"].supply_air_flow == 45

                first.fail_read(REGISTER_EXTRACT_AIR_FLOW, None)
                first.holding[REGISTER_EXTRACT_AIR_FLOW] = [50, 50]
                first.holding[REGISTER_MODE_STATUS] = [MODE_MANUAL, 100, 0]
                await poll("unit_1")
                assert coordinator.read_group_stale("unit_1", "flow") is False
                assert coordinator.read_group_stale("unit_2", "flow") is True
                assert state_for(first_id).attributes["percentage"] == 50
                assert state_for(second_id).state == "unavailable"
                assert coordinator.safe_data["unit_2"].supply_air_flow == 45

                second.fail_read(REGISTER_EXTRACT_AIR_FLOW, None)
                second.holding[REGISTER_EXTRACT_AIR_FLOW] = [55, 55]
                second.holding[REGISTER_MODE_STATUS] = [MODE_MANUAL, 110, 0]
                await poll("unit_2")
                assert coordinator.read_group_stale("unit_2", "flow") is False
                assert state_for(second_id).attributes["percentage"] == 55
            finally:
                assert await hass.config_entries.async_unload(entry.entry_id)

    @pytest.mark.parametrize("write_fails", [False, True], ids=("confirmed", "rejected"))
    async def test_fan_service_writes_and_reconciles_through_the_shared_link(
        self, hass: HomeAssistant, write_fails: bool
    ) -> None:
        links: list[MockModbusConnection] = []
        entry = _entry(hass, options={CONF_MAX_REQUESTS_PER_SECOND: 1000.0})

        with self._patch_link(links):
            assert await hass.config_entries.async_setup(entry.entry_id)
            await hass.async_block_till_done(wait_background_tasks=True)
            coordinator = entry.runtime_data.coordinator
            unit = links[0].for_unit(2)
            writes: list[WriteEvent] = []

            def _apply_write(event: WriteEvent) -> None:
                writes.append(event)
                if event.address == REGISTER_APPLY:
                    values = {write.address: int(write.values[0]) for write in writes}
                    unit.holding[REGISTER_MODE_STATUS] = [
                        values[REGISTER_MODE],
                        values[REGISTER_CURRENT_LEVEL],
                        values[REGISTER_EXTRACT_AIR_TARGET_LEVEL],
                    ]

            unit.on_write(_apply_write)
            if write_fails:
                unit.fail_write(REGISTER_CURRENT_LEVEL, IllegalDataValueError())

            registry = er.async_get(hass)
            supply_id = registry.async_get_entity_id(
                Platform.FAN, DOMAIN, f"{DOMAIN}_unit_1_supply_level"
            )
            extract_id = registry.async_get_entity_id(
                Platform.FAN, DOMAIN, f"{DOMAIN}_unit_1_extract_level"
            )
            assert supply_id is not None
            assert extract_id is not None
            initial = hass.states.get(supply_id)
            assert initial is not None
            assert initial.attributes["percentage"] == 30

            async def _set_supply() -> None:
                await hass.services.async_call(
                    "fan",
                    "set_percentage",
                    {"entity_id": supply_id, "percentage": 60},
                    blocking=True,
                )

            if write_fails:
                with pytest.raises(HomeAssistantError):
                    await _set_supply()
            else:
                await _set_supply()
            await hass.async_block_till_done()

            if write_fails:
                assert [(event.address, event.values[0]) for event in writes] == [
                    (REGISTER_MODE, MODE_UNBALANCED)
                ]
                assert coordinator._writes.by_room["unit_1"]["airflow_levels"].status == "failed"
                supply = hass.states.get(supply_id)
                assert supply is not None
                assert supply.attributes["percentage"] == 30
            else:
                assert [(event.address, event.values[0]) for event in writes] == [
                    (REGISTER_MODE, MODE_UNBALANCED),
                    (REGISTER_CURRENT_LEVEL, 120),
                    (REGISTER_EXTRACT_AIR_TARGET_LEVEL, 60),
                    (REGISTER_APPLY, 0),
                ]
                assert all(event.function_code == 0x06 for event in writes)
                assert coordinator._writes.by_room["unit_1"]["airflow_levels"].status == "pending"
                supply = hass.states.get(supply_id)
                extract = hass.states.get(extract_id)
                assert supply is not None and extract is not None
                assert supply.attributes["percentage"] == 60
                assert extract.attributes["percentage"] == 30
                assert supply.attributes["level_source"] == "pending"

                coordinator._startup_jobs = []
                for job in coordinator._jobs:
                    job.next_due = 0.0 if job.key == "flow" else float("inf")
                coordinator._last_read_started = None
                await coordinator.async_refresh()
                await hass.async_block_till_done()

                assert coordinator._writes.by_room["unit_1"]["airflow_levels"].status == "confirmed"
                supply = hass.states.get(supply_id)
                extract = hass.states.get(extract_id)
                assert supply is not None and extract is not None
                assert supply.attributes["percentage"] == 60
                assert extract.attributes["percentage"] == 30
                assert supply.attributes["level_source"] == "target"

            assert await hass.config_entries.async_unload(entry.entry_id)

    @pytest.mark.parametrize("lost_responses", [1, 2], ids=("retry-acknowledged", "both-lost"))
    async def test_applied_write_with_lost_response_is_reconciled(
        self, hass: HomeAssistant, lost_responses: int
    ) -> None:
        links: list[MockModbusConnection] = []
        entry = _entry(hass, options={CONF_MAX_REQUESTS_PER_SECOND: 1000.0})

        with self._patch_link(links):
            assert await hass.config_entries.async_setup(entry.entry_id)
            await hass.async_block_till_done(wait_background_tasks=True)
            coordinator = entry.runtime_data.coordinator
            unit = links[0].for_unit(2)
            writes: list[WriteEvent] = []
            write_register = unit.write_register
            apply_attempts = 0

            def _apply_write(event: WriteEvent) -> None:
                writes.append(event)
                if event.address == REGISTER_APPLY:
                    values = {write.address: int(write.values[0]) for write in writes}
                    unit.holding[REGISTER_MODE_STATUS] = [
                        values[REGISTER_MODE],
                        values[REGISTER_CURRENT_LEVEL],
                        values[REGISTER_EXTRACT_AIR_TARGET_LEVEL],
                    ]

            async def _lose_apply_response(address: int, value: int) -> None:
                nonlocal apply_attempts
                await write_register(address, value)
                if address == REGISTER_APPLY:
                    apply_attempts += 1
                    if apply_attempts <= lost_responses:
                        raise ModbusTimeoutError("response lost after apply")

            unit.on_write(_apply_write)
            unit.write_register = _lose_apply_response
            supply_id = er.async_get(hass).async_get_entity_id(
                Platform.FAN, DOMAIN, f"{DOMAIN}_unit_1_supply_level"
            )
            assert supply_id is not None

            async def _set_supply() -> None:
                await hass.services.async_call(
                    "fan",
                    "set_percentage",
                    {"entity_id": supply_id, "percentage": 60},
                    blocking=True,
                )

            if lost_responses == 2:
                with pytest.raises(HomeAssistantError):
                    await _set_supply()
            else:
                await _set_supply()
            await hass.async_block_till_done()

            assert apply_attempts == 2
            assert [event.address for event in writes] == [
                REGISTER_MODE,
                REGISTER_CURRENT_LEVEL,
                REGISTER_EXTRACT_AIR_TARGET_LEVEL,
                REGISTER_APPLY,
                REGISTER_APPLY,
            ]
            assert unit.holding[REGISTER_MODE_STATUS] == [MODE_UNBALANCED, 120, 60]
            if lost_responses == 1:
                coordinator._startup_jobs = []
                for job in coordinator._jobs:
                    job.next_due = 0.0 if job.key == "flow" else float("inf")
                coordinator._last_read_started = None
                await coordinator.async_refresh()
                await hass.async_block_till_done()
                assert coordinator._writes.by_room["unit_1"]["airflow_levels"].status == "confirmed"
            else:
                assert coordinator._writes.by_room["unit_1"]["airflow_levels"].status == "failed"
            supply = hass.states.get(supply_id)
            assert supply is not None
            assert supply.attributes["percentage"] == 60
            assert await hass.config_entries.async_unload(entry.entry_id)

    @pytest.mark.parametrize("kind", ["number", "select", "switch"])
    @pytest.mark.parametrize("write_fails", [False, True], ids=("confirmed", "rejected"))
    async def test_other_services_use_the_shared_link(
        self,
        hass: HomeAssistant,
        monkeypatch: pytest.MonkeyPatch,
        kind: str,
        write_fails: bool,
    ) -> None:
        monkeypatch.setattr(
            "custom_components.meltem_ventilation.coordinator.async_sleep", AsyncMock()
        )
        links: list[MockModbusConnection] = []
        room = {**_ROOM, "profile": "ii_f"}
        entry = _entry(hass, rooms=[room], options={CONF_MAX_REQUESTS_PER_SECOND: 1000.0})

        with self._patch_link(links):
            assert await hass.config_entries.async_setup(entry.entry_id)
            await hass.async_block_till_done(wait_background_tasks=True)
            coordinator = entry.runtime_data.coordinator
            unit = links[0].for_unit(2)
            unit.holding[REGISTER_HUMIDITY_STARTING_POINT] = [55, 20, 90]
            coordinator._startup_jobs = []
            for job in coordinator._jobs:
                job.next_due = 0.0 if job.key == "control_settings" else float("inf")
            coordinator._last_read_started = None
            await coordinator.async_refresh()
            await hass.async_block_till_done()

            writes: list[WriteEvent] = []

            def _apply_write(event: WriteEvent) -> None:
                writes.append(event)
                if event.address == REGISTER_APPLY:
                    value = (
                        PRESET_MODE_CODE_HIGH if kind == "select" else PRESET_MODE_CODE_INTENSIVE
                    )
                    unit.holding[REGISTER_MODE_STATUS] = [MODE_MANUAL, value, 0]

            unit.on_write(_apply_write)
            failed_register = (
                REGISTER_HUMIDITY_STARTING_POINT if kind == "number" else REGISTER_PRESET_MODE
            )
            if write_fails:
                unit.fail_write(failed_register, IllegalDataValueError())

            platform, key, service, service_data = {
                "number": (Platform.NUMBER, "humidity_starting_point", "set_value", {"value": 65}),
                "select": (Platform.SELECT, "preset_mode", "select_option", {"option": "high"}),
                "switch": (Platform.SWITCH, "intensive", "turn_on", {}),
            }[kind]
            entity_id = er.async_get(hass).async_get_entity_id(
                platform, DOMAIN, f"{DOMAIN}_unit_1_{key}"
            )
            assert entity_id is not None
            initial = hass.states.get(entity_id)
            assert initial is not None

            async def _call_service() -> None:
                await hass.services.async_call(
                    platform.value, service, {"entity_id": entity_id, **service_data}, blocking=True
                )

            if write_fails:
                with pytest.raises(HomeAssistantError):
                    await _call_service()
            else:
                await _call_service()
            await hass.async_block_till_done()

            state = hass.states.get(entity_id)
            assert state is not None
            if write_fails:
                assert writes == []
                assert state.state == initial.state
            elif kind == "number":
                assert [(event.address, event.values[0]) for event in writes] == [
                    (REGISTER_HUMIDITY_STARTING_POINT, 65)
                ]
                assert float(state.state) == 65.0
                assert (
                    coordinator._writes.by_room["unit_1"][
                        "control_setting:humidity_starting_point"
                    ].status
                    == "confirmed"
                )
            else:
                assert writes[-1].address == REGISTER_APPLY
                assert state.state == ("high" if kind == "select" else "on")
                key = "preset_mode" if kind == "select" else "intensive"
                assert coordinator._writes.by_room["unit_1"][key].status == "confirmed"

            assert await hass.config_entries.async_unload(entry.entry_id)

    async def test_invalid_control_range_is_rejected_before_writing(
        self, hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            "custom_components.meltem_ventilation.coordinator.async_sleep", AsyncMock()
        )
        links: list[MockModbusConnection] = []
        room = {**_ROOM, "profile": "ii_f"}
        entry = _entry(hass, rooms=[room], options={CONF_MAX_REQUESTS_PER_SECOND: 1000.0})

        with self._patch_link(links):
            assert await hass.config_entries.async_setup(entry.entry_id)
            await hass.async_block_till_done(wait_background_tasks=True)
            coordinator = entry.runtime_data.coordinator
            unit = links[0].for_unit(2)
            unit.holding[REGISTER_HUMIDITY_STARTING_POINT] = [55, 20, 60]
            coordinator._startup_jobs = []
            for job in coordinator._jobs:
                job.next_due = 0.0 if job.key == "control_settings" else float("inf")
            coordinator._last_read_started = None
            await coordinator.async_refresh()
            await hass.async_block_till_done()
            writes: list[WriteEvent] = []
            unit.on_write(writes.append)
            entity_id = er.async_get(hass).async_get_entity_id(
                Platform.NUMBER, DOMAIN, f"{DOMAIN}_unit_1_humidity_min_level"
            )
            assert entity_id is not None

            with pytest.raises(HomeAssistantError):
                await hass.services.async_call(
                    "number", "set_value", {"entity_id": entity_id, "value": 80}, blocking=True
                )

            assert writes == []
            assert unit.holding[REGISTER_HUMIDITY_STARTING_POINT] == [55, 20, 60]
            assert await hass.config_entries.async_unload(entry.entry_id)

    async def test_unload_releases_the_link(self, hass: HomeAssistant) -> None:
        links: list[MockModbusConnection] = []
        entry = _entry(hass)

        with self._patch_link(links):
            assert await hass.config_entries.async_setup(entry.entry_id)
            await hass.async_block_till_done(wait_background_tasks=True)
            (link,) = links
            assert link.for_unit(2).read_events
            assert link.for_unit(2).required_timeout == FIXED_TIMEOUT

            assert await hass.config_entries.async_unload(entry.entry_id)
            await hass.async_block_till_done()

        with pytest.raises(ClientClosedError):
            await link.for_unit(1).read_holding_registers(43901, 1)

    async def test_unload_stops_a_startup_read_waiting_for_a_rate_slot(
        self, hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        waiting = asyncio.Event()
        resume = asyncio.Event()

        async def _sleep(seconds: float) -> None:
            waiting.set()
            await resume.wait()

        monkeypatch.setattr(
            "custom_components.meltem_ventilation.device.transport.async_sleep", _sleep
        )
        links: list[MockModbusConnection] = []
        entry = _entry(hass)

        with self._patch_link(links):
            assert await hass.config_entries.async_setup(entry.entry_id)
            await asyncio.wait_for(waiting.wait(), timeout=5)
            (link,) = links
            assert not link.for_unit(2).read_events

            assert await hass.config_entries.async_unload(entry.entry_id)
            resume.set()
            await hass.async_block_till_done(wait_background_tasks=True)

            assert not link.for_unit(2).read_events
            with pytest.raises(ClientClosedError):
                await link.for_unit(1).read_holding_registers(43901, 1)

    @pytest.mark.parametrize("shutdown_fails", [False, True])
    async def test_unload_during_blocked_read_does_not_send_a_queued_write(
        self, hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch, shutdown_fails: bool
    ) -> None:
        links: list[MockModbusConnection] = []
        entry = _entry(hass, options={CONF_MAX_REQUESTS_PER_SECOND: 1000.0})
        blocked = asyncio.Event()
        resume = asyncio.Event()
        service_started = asyncio.Event()

        with self._patch_link(links):
            assert await hass.config_entries.async_setup(entry.entry_id)
            await hass.async_block_till_done(wait_background_tasks=True)
            coordinator = entry.runtime_data.coordinator
            unit = links[0].for_unit(2)
            original_read = unit.read_holding_registers
            writes: list[WriteEvent] = []
            unit.on_write(writes.append)

            async def _blocked_read(address: int, count: int) -> list[int]:
                blocked.set()
                await resume.wait()
                return await original_read(address, count)

            monkeypatch.setattr(unit, "read_holding_registers", _blocked_read)
            coordinator._startup_jobs = []
            for job in coordinator._jobs:
                job.next_due = 0.0 if job.key == "flow" else float("inf")
            coordinator._last_read_started = None
            read_task = asyncio.create_task(coordinator.async_refresh())
            await asyncio.wait_for(blocked.wait(), timeout=5)
            assert coordinator._gateway_lock.locked()
            supply_id = er.async_get(hass).async_get_entity_id(
                Platform.FAN, DOMAIN, f"{DOMAIN}_unit_1_supply_level"
            )
            assert supply_id is not None
            set_direction = coordinator.async_set_direction_level

            async def _queued_write(room_key: str, direction: str, level: int) -> None:
                service_started.set()
                await set_direction(room_key, direction, level)

            monkeypatch.setattr(coordinator, "async_set_direction_level", _queued_write)

            async def _set_supply() -> None:
                await hass.services.async_call(
                    "fan",
                    "set_percentage",
                    {"entity_id": supply_id, "percentage": 60},
                    blocking=True,
                )

            write_task = asyncio.create_task(_set_supply())
            await asyncio.wait_for(service_started.wait(), timeout=5)
            assert not write_task.done()
            assert writes == []
            try:
                if shutdown_fails:
                    cumulative = coordinator.cumulative_diagnostics
                    assert cumulative is not None
                    with (
                        patch.object(
                            hass.config_entries,
                            "async_unload_platforms",
                            new=AsyncMock(return_value=True),
                        ),
                        patch.object(
                            cumulative,
                            "async_flush",
                            new=AsyncMock(side_effect=RuntimeError("shutdown failed")),
                        ),
                        pytest.raises(RuntimeError, match="shutdown failed"),
                    ):
                        await asyncio.wait_for(async_unload_entry(hass, entry), timeout=5)
                else:
                    assert await asyncio.wait_for(
                        hass.config_entries.async_unload(entry.entry_id), timeout=5
                    )
            finally:
                resume.set()
            await read_task
            with pytest.raises(HomeAssistantError):
                await write_task
            assert writes == []
            assert not coordinator._gateway_lock.locked()

            if shutdown_fails:
                assert await hass.config_entries.async_unload(entry.entry_id)
            assert await hass.config_entries.async_setup(entry.entry_id)
            await hass.async_block_till_done(wait_background_tasks=True)
            assert len(links) == 2
            assert links[1].for_unit(2).read_events
            assert await hass.config_entries.async_unload(entry.entry_id)

    async def test_diagnostics_survive_unload_reload_and_are_removed_with_entry(
        self, hass: HomeAssistant
    ) -> None:
        links: list[MockModbusConnection] = []
        entry = _entry(hass, options={CONF_MAX_REQUESTS_PER_SECOND: 1000.0})

        with self._patch_link(links):
            assert await hass.config_entries.async_setup(entry.entry_id)
            await hass.async_block_till_done(wait_background_tasks=True)
            coordinator = entry.runtime_data.coordinator
            unit = links[0].for_unit(2)
            unit.fail_read(41020, IllegalDataValueError())
            coordinator._startup_jobs = []
            for job in coordinator._jobs:
                job.next_due = 0.0 if job.key == "flow" else float("inf")
            coordinator._last_read_started = None
            await coordinator.async_refresh()
            cumulative = coordinator.cumulative_diagnostics
            assert cumulative is not None
            attempts = cumulative.request_attempts
            failures = cumulative.request_failures
            failed_groups = cumulative.failed_read_groups
            period = cumulative.period_started_at
            assert attempts > 0 and failures > 0 and failed_groups > 0

            assert await hass.config_entries.async_unload(entry.entry_id)
            restored = await CumulativeDiagnostics.async_create(hass, entry.entry_id)
            assert restored.request_attempts == attempts
            assert restored.request_failures == failures
            assert restored.failed_read_groups == failed_groups
            assert restored.period_started_at == period

            assert await hass.config_entries.async_setup(entry.entry_id)
            await hass.async_block_till_done(wait_background_tasks=True)
            reloaded = entry.runtime_data.coordinator.cumulative_diagnostics
            assert reloaded is not None
            assert reloaded.request_attempts > attempts
            assert reloaded.request_failures == failures
            assert reloaded.failed_read_groups == failed_groups
            assert reloaded.period_started_at == period
            assert await hass.config_entries.async_remove(entry.entry_id)

        removed = await CumulativeDiagnostics.async_create(hass, entry.entry_id)
        assert removed.request_attempts == 0
        assert removed.request_failures == 0
        assert removed.failed_read_groups == 0

    async def test_a_silent_gateway_retries_setup_and_releases_the_link(
        self, hass: HomeAssistant
    ) -> None:
        links: list[MockModbusConnection] = []
        entry = _entry(hass)

        with self._patch_link(links, gateway_silent=True):
            await hass.config_entries.async_setup(entry.entry_id)
            await hass.async_block_till_done()

        assert entry.state is ConfigEntryState.SETUP_RETRY
        with pytest.raises(ClientClosedError):
            await links[0].for_unit(1).read_holding_registers(43901, 1)
