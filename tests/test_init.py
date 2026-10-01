"""Tests for integration setup, unload, and data migration in __init__.py."""

from __future__ import annotations

from copy import deepcopy
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.meltem_ventilation import (
    async_migrate_entry,
    async_setup_entry,
    async_unload_entry,
)
from custom_components.meltem_ventilation.const import (
    BASE_SUPPORTED_ENTITY_KEYS,
    CONF_MAX_REQUESTS_PER_SECOND,
    CONF_PORT,
    CONF_ROOMS,
    DOMAIN,
    PLATFORMS,
)
from custom_components.meltem_ventilation.modbus_helpers import (
    supported_entity_keys_for_profile,
)
from custom_components.meltem_ventilation.models import (
    MeltemRuntimeData,
)

# ---------------------------------------------------------------------------
#  Helpers
# ---------------------------------------------------------------------------

MINIMAL_ROOM = {
    "key": "unit_1",
    "name": "Unit 1",
    "slave": 2,
    "profile": "ii_plain",
    "preview": "ID 123 | basic",
    "supported_entity_keys": sorted(BASE_SUPPORTED_ENTITY_KEYS),
}

MINIMAL_ENTRY_DATA = {
    CONF_PORT: "/dev/serial/by-id/test-device",
    CONF_MAX_REQUESTS_PER_SECOND: 2.0,
    CONF_ROOMS: [MINIMAL_ROOM],
}


def _mock_config_entry(**overrides) -> MockConfigEntry:
    return MockConfigEntry(
        domain=DOMAIN,
        title="Meltem",
        data=overrides.get("data", deepcopy(MINIMAL_ENTRY_DATA)),
        options=overrides.get("options", {}),
        entry_id=overrides.get("entry_id", "test-entry-id"),
        version=overrides.get("version", 1),
        minor_version=overrides.get("minor_version", 2),
        source="user",
    )


# ---------------------------------------------------------------------------
#  async_setup_entry
# ---------------------------------------------------------------------------


class TestAsyncSetupEntry:
    @patch(
        "custom_components.meltem_ventilation.resolve_preferred_port_path",
        side_effect=lambda p: p,
    )
    @patch(
        "custom_components.meltem_ventilation.MeltemModbusClient",
        autospec=True,
    )
    @patch(
        "custom_components.meltem_ventilation.MeltemDataUpdateCoordinator",
        autospec=True,
    )
    async def test_setup_creates_coordinator_and_forwards_platforms(
        self,
        mock_coordinator_cls,
        mock_client_cls,
        _mock_resolve,
        hass: HomeAssistant,
    ) -> None:
        """async_setup_entry should create client + coordinator, do first refresh,
        store runtime data, and forward platforms."""
        mock_coordinator = mock_coordinator_cls.return_value
        mock_coordinator.async_refresh = AsyncMock()

        entry = _mock_config_entry()
        entry.add_to_hass(hass)

        created_tasks = []

        def _create_background_task(_hass, coro, _name, **kwargs):
            created_tasks.append(coro)
            coro.close()
            return MagicMock()

        with patch.object(
            hass.config_entries,
            "async_forward_entry_setups",
            new=AsyncMock(),
        ) as mock_forward, patch.object(
            entry, "async_create_background_task", side_effect=_create_background_task
        ):
            result = await async_setup_entry(hass, entry)

        assert result is True
        mock_client_cls.assert_called_once()
        mock_coordinator_cls.assert_called_once()
        mock_forward.assert_awaited_once_with(entry, PLATFORMS)
        # The first refresh runs in the background so setup stays fast.
        assert len(created_tasks) == 1
        assert hasattr(entry, "runtime_data")

    @patch(
        "custom_components.meltem_ventilation.resolve_preferred_port_path",
        side_effect=lambda port: port,
    )
    @patch(
        "custom_components.meltem_ventilation.MeltemModbusClient",
        autospec=True,
    )
    @patch(
        "custom_components.meltem_ventilation.MeltemDataUpdateCoordinator",
        autospec=True,
    )
    async def test_setup_failure_after_coordinator_removes_runtime_data(
        self,
        mock_coordinator_cls,
        mock_client_cls,
        _mock_resolve,
        hass: HomeAssistant,
    ) -> None:
        entry = _mock_config_entry()
        entry.add_to_hass(hass)

        with (
            patch.object(
                hass.config_entries,
                "async_forward_entry_setups",
                new=AsyncMock(side_effect=RuntimeError("platform failed")),
            ),
            pytest.raises(RuntimeError, match="platform failed"),
        ):
            await async_setup_entry(hass, entry)

        assert not hasattr(entry, "runtime_data")
        mock_client_cls.return_value.shutdown.assert_called_once()

    @patch(
        "custom_components.meltem_ventilation.MeltemModbusClient",
        autospec=True,
    )
    @patch(
        "custom_components.meltem_ventilation.MeltemDataUpdateCoordinator",
        autospec=True,
    )
    async def test_setup_normalizes_port_path(
        self,
        mock_coordinator_cls,
        mock_client_cls,
        hass: HomeAssistant,
    ) -> None:
        mock_coordinator = mock_coordinator_cls.return_value
        mock_coordinator.async_refresh = AsyncMock()

        data = deepcopy(MINIMAL_ENTRY_DATA)
        data[CONF_PORT] = "/dev/ttyACM0"
        entry = _mock_config_entry(data=data)
        entry.add_to_hass(hass)

        with (
            patch(
                "custom_components.meltem_ventilation.resolve_preferred_port_path",
                return_value="/dev/serial/by-id/normalized",
            ),
            patch.object(
                hass.config_entries,
                "async_forward_entry_setups",
                new=AsyncMock(),
            ),
        ):
            await async_setup_entry(hass, entry)

        # The entry data should have been updated.
        assert entry.data[CONF_PORT] == "/dev/serial/by-id/normalized"
        assert entry.unique_id == "/dev/serial/by-id/normalized"

    @patch(
        "custom_components.meltem_ventilation.resolve_preferred_port_path",
        side_effect=lambda p: p,
    )
    @patch(
        "custom_components.meltem_ventilation.MeltemModbusClient",
        autospec=True,
    )
    @patch(
        "custom_components.meltem_ventilation.MeltemDataUpdateCoordinator",
        autospec=True,
    )
    async def test_setup_derives_missing_entity_keys_from_the_profile(
        self,
        mock_coordinator_cls,
        mock_client_cls,
        _mock_resolve,
        hass: HomeAssistant,
    ) -> None:
        """Legacy rooms without stored keys get their profile's entities, no probe."""
        mock_coordinator_cls.return_value.async_refresh = AsyncMock()

        data = deepcopy(MINIMAL_ENTRY_DATA)
        data[CONF_ROOMS] = [
            {"key": "unit_1", "name": "Unit 1", "slave": 2, "profile": "ii_fc"}
        ]
        entry = _mock_config_entry(data=data)
        entry.add_to_hass(hass)

        with patch.object(
            hass.config_entries,
            "async_forward_entry_setups",
            new=AsyncMock(),
        ):
            await async_setup_entry(hass, entry)

        room = mock_coordinator_cls.call_args.kwargs["rooms"][0]
        assert room.supported_entity_keys == frozenset(
            supported_entity_keys_for_profile("ii_fc")
        )
        # The stored entry is not rewritten.
        assert "supported_entity_keys" not in entry.data[CONF_ROOMS][0]

    @patch(
        "custom_components.meltem_ventilation.resolve_preferred_port_path",
        side_effect=lambda port: port,
    )
    @patch(
        "custom_components.meltem_ventilation.MeltemModbusClient",
        autospec=True,
    )
    @patch(
        "custom_components.meltem_ventilation.MeltemDataUpdateCoordinator",
        autospec=True,
    )
    async def test_setup_completes_stale_entity_keys_with_the_profile(
        self,
        mock_coordinator_cls,
        mock_client_cls,
        _mock_resolve,
        hass: HomeAssistant,
    ) -> None:
        """Keys stored by an older release still get entities added later."""
        mock_coordinator_cls.return_value.async_refresh = AsyncMock()
        data = deepcopy(MINIMAL_ENTRY_DATA)
        data[CONF_ROOMS] = [
            {
                **MINIMAL_ROOM,
                "profile": "ii_fc",
                "supported_entity_keys": ["extract_air_flow", "humidity_extract_air"],
            }
        ]
        entry = _mock_config_entry(data=data)
        entry.add_to_hass(hass)

        with patch.object(
            hass.config_entries,
            "async_forward_entry_setups",
            new=AsyncMock(),
        ):
            await async_setup_entry(hass, entry)

        room = mock_coordinator_cls.call_args.kwargs["rooms"][0]
        assert set(supported_entity_keys_for_profile("ii_fc")).issubset(
            room.supported_entity_keys
        )
        assert entry.data[CONF_ROOMS][0]["supported_entity_keys"] == [
            "extract_air_flow",
            "humidity_extract_air",
        ]

    @patch(
        "custom_components.meltem_ventilation.resolve_preferred_port_path",
        side_effect=lambda p: p,
    )
    @patch(
        "custom_components.meltem_ventilation.MeltemModbusClient",
        autospec=True,
    )
    @patch(
        "custom_components.meltem_ventilation.MeltemDataUpdateCoordinator",
        autospec=True,
    )
    async def test_setup_removes_entities_replaced_by_the_directional_fans(
        self,
        mock_coordinator_cls,
        mock_client_cls,
        _mock_resolve,
        hass: HomeAssistant,
    ) -> None:
        mock_coordinator = mock_coordinator_cls.return_value
        mock_coordinator.async_refresh = AsyncMock()

        entry = _mock_config_entry()
        entry.add_to_hass(hass)

        registry = er.async_get(hass)
        obsolete = registry.async_get_or_create(
            "fan", DOMAIN, f"{DOMAIN}_unit_1_level", config_entry=entry
        )
        obsolete_number = registry.async_get_or_create(
            "number", DOMAIN, f"{DOMAIN}_unit_1_supply_level", config_entry=entry
        )
        obsolete_button = registry.async_get_or_create(
            "button", DOMAIN, f"{DOMAIN}_unit_1_activate_intensive", config_entry=entry
        )
        obsolete_binary_sensor = registry.async_get_or_create(
            "binary_sensor", DOMAIN, f"{DOMAIN}_unit_1_intensive_active", config_entry=entry
        )
        kept = registry.async_get_or_create(
            "sensor", DOMAIN, f"{DOMAIN}_unit_1_operating_hours", config_entry=entry
        )

        with patch.object(
            hass.config_entries,
            "async_forward_entry_setups",
            new=AsyncMock(),
        ):
            await async_setup_entry(hass, entry)

        assert registry.async_get(obsolete.entity_id) is None
        assert registry.async_get(obsolete_number.entity_id) is None
        assert registry.async_get(obsolete_button.entity_id) is None
        assert registry.async_get(obsolete_binary_sensor.entity_id) is None
        assert registry.async_get(kept.entity_id) is not None

    @patch(
        "custom_components.meltem_ventilation.resolve_preferred_port_path",
        side_effect=lambda port: port,
    )
    @patch(
        "custom_components.meltem_ventilation.MeltemModbusClient",
        autospec=True,
    )
    @patch(
        "custom_components.meltem_ventilation.MeltemDataUpdateCoordinator",
        autospec=True,
    )
    async def test_setup_removes_entities_from_the_previous_profile(
        self,
        mock_coordinator_cls,
        mock_client_cls,
        _mock_resolve,
        hass: HomeAssistant,
    ) -> None:
        mock_coordinator_cls.return_value.async_refresh = AsyncMock()
        entry = _mock_config_entry()
        entry.add_to_hass(hass)
        registry = er.async_get(hass)
        old_co2 = registry.async_get_or_create(
            "sensor", DOMAIN, f"{DOMAIN}_unit_1_co2_extract_air", config_entry=entry
        )
        old_threshold = registry.async_get_or_create(
            "number", DOMAIN, f"{DOMAIN}_unit_1_co2_max_level", config_entry=entry
        )
        old_sensor_mode = registry.async_get_or_create(
            "select", DOMAIN, f"{DOMAIN}_unit_1_operation_mode", config_entry=entry
        )
        kept = registry.async_get_or_create(
            "sensor", DOMAIN, f"{DOMAIN}_unit_1_exhaust_temperature", config_entry=entry
        )

        with patch.object(
            hass.config_entries,
            "async_forward_entry_setups",
            new=AsyncMock(),
        ):
            await async_setup_entry(hass, entry)

        assert registry.async_get(old_co2.entity_id) is None
        assert registry.async_get(old_threshold.entity_id) is None
        assert registry.async_get(old_sensor_mode.entity_id) is None
        assert registry.async_get(kept.entity_id) is not None

    @patch(
        "custom_components.meltem_ventilation.resolve_preferred_port_path",
        side_effect=lambda p: p,
    )
    @patch(
        "custom_components.meltem_ventilation.MeltemModbusClient",
        autospec=True,
    )
    @patch(
        "custom_components.meltem_ventilation.MeltemDataUpdateCoordinator",
        autospec=True,
    )
    async def test_setup_keeps_obsolete_entities_of_other_entries(
        self,
        mock_coordinator_cls,
        mock_client_cls,
        _mock_resolve,
        hass: HomeAssistant,
    ) -> None:
        """Room keys are only unique per gateway."""
        mock_coordinator = mock_coordinator_cls.return_value
        mock_coordinator.async_refresh = AsyncMock()

        entry = _mock_config_entry()
        entry.add_to_hass(hass)
        other_entry = _mock_config_entry(entry_id="other-entry-id")
        other_entry.add_to_hass(hass)

        registry = er.async_get(hass)
        foreign = registry.async_get_or_create(
            "fan", DOMAIN, f"{DOMAIN}_unit_1_level", config_entry=other_entry
        )

        with patch.object(
            hass.config_entries,
            "async_forward_entry_setups",
            new=AsyncMock(),
        ):
            await async_setup_entry(hass, entry)

        assert registry.async_get(foreign.entity_id) is not None

    @patch(
        "custom_components.meltem_ventilation.resolve_preferred_port_path",
        side_effect=lambda p: p,
    )
    @patch(
        "custom_components.meltem_ventilation.MeltemModbusClient",
        autospec=True,
    )
    @patch(
        "custom_components.meltem_ventilation.MeltemDataUpdateCoordinator",
        autospec=True,
    )
    async def test_setup_respects_option_max_request_rate(
        self,
        mock_coordinator_cls,
        mock_client_cls,
        _mock_resolve,
        hass: HomeAssistant,
    ) -> None:
        mock_coordinator = mock_coordinator_cls.return_value
        mock_coordinator.async_refresh = AsyncMock()

        entry = _mock_config_entry(
            options={CONF_MAX_REQUESTS_PER_SECOND: 5.0}
        )
        entry.add_to_hass(hass)

        with patch.object(
            hass.config_entries,
            "async_forward_entry_setups",
            new=AsyncMock(),
        ):
            await async_setup_entry(hass, entry)

        call_kwargs = mock_coordinator_cls.call_args
        assert call_kwargs.kwargs["max_requests_per_second"] == 5.0


# ---------------------------------------------------------------------------
#  async_unload_entry
# ---------------------------------------------------------------------------


class TestAsyncUnloadEntry:
    async def test_unload_removes_runtime_data_and_closes_client(
        self, hass: HomeAssistant,
    ) -> None:
        entry = _mock_config_entry()
        entry.add_to_hass(hass)

        mock_client = MagicMock()
        mock_coordinator = MagicMock()
        mock_coordinator.client = mock_client
        mock_coordinator.async_shutdown = AsyncMock()
        entry.runtime_data = MeltemRuntimeData(coordinator=mock_coordinator)

        with patch.object(
            hass.config_entries,
            "async_unload_platforms",
            new=AsyncMock(return_value=True),
        ):
            result = await async_unload_entry(hass, entry)

        assert result is True
        mock_client.shutdown.assert_called_once()
        mock_client.close.assert_not_called()

    async def test_unload_returns_false_on_platform_failure(
        self, hass: HomeAssistant,
    ) -> None:
        entry = _mock_config_entry()
        entry.add_to_hass(hass)

        mock_client = MagicMock()
        mock_coordinator = MagicMock()
        mock_coordinator.client = mock_client
        entry.runtime_data = MeltemRuntimeData(coordinator=mock_coordinator)

        with patch.object(
            hass.config_entries,
            "async_unload_platforms",
            new=AsyncMock(return_value=False),
        ):
            result = await async_unload_entry(hass, entry)

        assert result is False


class TestAsyncMigrateEntry:
    async def test_migration_renames_the_airflow_health_entity(
        self, hass: HomeAssistant
    ) -> None:
        entry = _mock_config_entry(minor_version=1)
        entry.add_to_hass(hass)
        registry = er.async_get(hass)
        legacy = registry.async_get_or_create(
            "binary_sensor",
            DOMAIN,
            f"{DOMAIN}_unit_1_airflow_data_stale",
            config_entry=entry,
        )

        assert await async_migrate_entry(hass, entry) is True

        migrated = registry.async_get(legacy.entity_id)
        assert migrated is not None
        assert migrated.unique_id == f"{DOMAIN}_unit_1_data_health"
        assert entry.minor_version == 2

    async def test_migration_keeps_an_existing_data_health_entity(
        self, hass: HomeAssistant
    ) -> None:
        entry = _mock_config_entry(minor_version=1)
        entry.add_to_hass(hass)
        registry = er.async_get(hass)
        legacy = registry.async_get_or_create(
            "binary_sensor",
            DOMAIN,
            f"{DOMAIN}_unit_1_airflow_data_stale",
            config_entry=entry,
        )
        current = registry.async_get_or_create(
            "binary_sensor",
            DOMAIN,
            f"{DOMAIN}_unit_1_data_health",
            config_entry=entry,
        )

        assert await async_migrate_entry(hass, entry) is True

        assert registry.async_get(legacy.entity_id).unique_id == (
            f"{DOMAIN}_unit_1_airflow_data_stale"
        )
        assert registry.async_get(current.entity_id).unique_id == (
            f"{DOMAIN}_unit_1_data_health"
        )

    async def test_migration_rejects_a_newer_major_version(
        self, hass: HomeAssistant
    ) -> None:
        entry = _mock_config_entry(version=2, minor_version=1)
        entry.add_to_hass(hass)

        assert await async_migrate_entry(hass, entry) is False


@patch(
    "custom_components.meltem_ventilation.resolve_preferred_port_path",
    side_effect=lambda port: port,
)
@patch("custom_components.meltem_ventilation.MeltemModbusClient", autospec=True)
@patch(
    "custom_components.meltem_ventilation.MeltemDataUpdateCoordinator", autospec=True
)
class TestDeviceRegistrySync:
    @staticmethod
    async def _setup(hass: HomeAssistant, entry: MockConfigEntry) -> None:
        with patch.object(
            hass.config_entries,
            "async_forward_entry_setups",
            new=AsyncMock(),
        ):
            await async_setup_entry(hass, entry)

    async def test_setup_registers_the_gateway_device(
        self, mock_coordinator_cls, _mock_client_cls, _mock_resolve, hass: HomeAssistant
    ) -> None:
        mock_coordinator_cls.return_value.async_refresh = AsyncMock()
        entry = _mock_config_entry()
        entry.add_to_hass(hass)

        await self._setup(hass, entry)

        gateway = dr.async_get(hass).async_get_device(
            identifiers={(DOMAIN, entry.entry_id)}
        )
        assert gateway is not None
        assert gateway.model == "M-WRG-GW"

    async def test_setup_removes_devices_of_unconfigured_units(
        self, mock_coordinator_cls, _mock_client_cls, _mock_resolve, hass: HomeAssistant
    ) -> None:
        mock_coordinator_cls.return_value.async_refresh = AsyncMock()
        entry = _mock_config_entry()
        entry.add_to_hass(hass)
        registry = dr.async_get(hass)
        configured = registry.async_get_or_create(
            config_entry_id=entry.entry_id, identifiers={(DOMAIN, "unit_1")}
        )
        dropped = registry.async_get_or_create(
            config_entry_id=entry.entry_id, identifiers={(DOMAIN, "unit_9")}
        )

        await self._setup(hass, entry)

        assert registry.async_get(configured.id) is not None
        assert registry.async_get(dropped.id) is None
