"""Tests for integration setup, unload, and data migration in __init__.py."""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from copy import deepcopy
from dataclasses import dataclass
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import (
    ConfigEntryError,
    ConfigEntryNotReady,
    HomeAssistantError,
)
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.typing import UNDEFINED
from modbus_connection import ClientClosedError, IllegalDataValueError, ModbusTimeoutError
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
    MODE_UNBALANCED,
    PLATFORMS,
    REGISTER_APPLY,
    REGISTER_CURRENT_LEVEL,
    REGISTER_EXTRACT_AIR_TARGET_LEVEL,
    REGISTER_MODE,
    REGISTER_MODE_STATUS,
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

    async def test_setup_failure_after_coordinator_removes_runtime_data(
        self,
        hass: HomeAssistant,
        setup_mocks: _SetupMocks,
    ) -> None:
        setup_mocks.forward.side_effect = RuntimeError("platform failed")
        entry = _entry(hass)

        with pytest.raises(RuntimeError, match="platform failed"):
            await async_setup_entry(hass, entry)

        assert not hasattr(entry, "runtime_data")
        setup_mocks.coordinator_cls.return_value.async_shutdown.assert_awaited_once()
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
    def _patch_link(links: list[MockModbusConnection], *, gateway_silent: bool = False):
        def _connection(params, **kwargs) -> MockModbusConnection:
            link = MockModbusConnection()
            gateway = link.for_unit(1)
            gateway.holding.update({43901: 1, 43902: [2]})
            if gateway_silent:
                gateway.fail_requests(ModbusTimeoutError("silent"))
            link.for_unit(2).holding.update({41020: [30, 30], 41100: [3, 60, 0]})
            links.append(link)
            return link

        return patch("homeassistant.components.modbus.connection.ModbusConnection", _connection)

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
