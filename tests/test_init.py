"""Tests for integration setup, unload, and data migration in __init__.py."""

from __future__ import annotations

import asyncio
from copy import deepcopy
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.meltem_ventilation import (
    REQUIRED_ENTITY_KEYS,
    async_setup_entry,
    async_unload_entry,
)
from custom_components.meltem_ventilation.const import (
    CONF_MAX_REQUESTS_PER_SECOND,
    CONF_PORT,
    CONF_ROOMS,
    DEFAULT_MAX_REQUESTS_PER_SECOND,
    DOMAIN,
    PLATFORMS,
)
from custom_components.meltem_ventilation.models import (
    MeltemRuntimeData,
    RoomConfig,
    RoomState,
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
    "supported_entity_keys": sorted(REQUIRED_ENTITY_KEYS),
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
        version=1,
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

        def _create_task(coro):
            created_tasks.append(coro)
            coro.close()
            return MagicMock()

        with patch.object(
            hass.config_entries,
            "async_forward_entry_setups",
            new=AsyncMock(),
        ) as mock_forward, patch.object(hass, "async_create_task", side_effect=_create_task):
            result = await async_setup_entry(hass, entry)

        assert result is True
        mock_client_cls.assert_called_once()
        mock_coordinator_cls.assert_called_once()
        mock_forward.assert_awaited_once_with(entry, PLATFORMS)
        assert len(created_tasks) == 1
        assert hasattr(entry, "runtime_data")

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
    async def test_setup_reprobes_when_metadata_missing(
        self,
        mock_coordinator_cls,
        mock_client_cls,
        _mock_resolve,
        hass: HomeAssistant,
    ) -> None:
        """When a room lacks supported_entity_keys, setup should re-probe."""
        mock_coordinator = mock_coordinator_cls.return_value
        mock_coordinator.async_refresh = AsyncMock()

        data = deepcopy(MINIMAL_ENTRY_DATA)
        data[CONF_ROOMS] = [
            {
                "key": "unit_1",
                "name": "Unit 1",
                "slave": 2,
                "profile": "ii_plain",
            }
        ]
        entry = _mock_config_entry(data=data)
        entry.add_to_hass(hass)

        with (
            patch(
                "custom_components.meltem_ventilation.detect_slave_details",
                return_value=("plain", "ID 2 | basic", ["level", "extract_air_flow"]),
            ) as mock_detect,
            patch.object(
                hass.config_entries,
                "async_forward_entry_setups",
                new=AsyncMock(),
            ),
        ):
            await async_setup_entry(hass, entry)

        mock_detect.assert_called_once()
        # Entry data should now have supported_entity_keys.
        assert entry.data[CONF_ROOMS][0]["supported_entity_keys"] == [
            "level",
            "extract_air_flow",
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
    async def test_setup_falls_back_to_profile_defaults_when_reprobe_fails(
        self,
        mock_coordinator_cls,
        mock_client_cls,
        _mock_resolve,
        hass: HomeAssistant,
    ) -> None:
        mock_coordinator = mock_coordinator_cls.return_value
        mock_coordinator.async_refresh = AsyncMock()

        data = deepcopy(MINIMAL_ENTRY_DATA)
        data[CONF_ROOMS] = [
            {
                "key": "unit_1",
                "name": "Unit 1",
                "slave": 2,
                "profile": "ii_fc",
            }
        ]
        entry = _mock_config_entry(data=data)
        entry.add_to_hass(hass)

        with (
            patch(
                "custom_components.meltem_ventilation.detect_slave_details",
                side_effect=Exception("boom"),
            ),
            patch.object(
                hass.config_entries,
                "async_forward_entry_setups",
                new=AsyncMock(),
            ),
        ):
            await async_setup_entry(hass, entry)

        assert "co2_extract_air" in entry.data[CONF_ROOMS][0]["supported_entity_keys"]
        assert "humidity_extract_air" in entry.data[CONF_ROOMS][0]["supported_entity_keys"]

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
    async def test_setup_augments_stale_supported_keys(
        self,
        mock_coordinator_cls,
        mock_client_cls,
        _mock_resolve,
        hass: HomeAssistant,
    ) -> None:
        """When supported_entity_keys is present but incomplete, setup should merge REQUIRED_ENTITY_KEYS."""
        mock_coordinator = mock_coordinator_cls.return_value
        mock_coordinator.async_refresh = AsyncMock()

        data = deepcopy(MINIMAL_ENTRY_DATA)
        # Provide an incomplete set that's missing some required keys.
        data[CONF_ROOMS] = [
            {
                **MINIMAL_ROOM,
                "supported_entity_keys": ["level", "extract_air_flow"],
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

        updated_keys = set(entry.data[CONF_ROOMS][0]["supported_entity_keys"])
        assert REQUIRED_ENTITY_KEYS.issubset(updated_keys)

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
        mock_coordinator.async_cancel_room_write_tasks = AsyncMock()
        entry.runtime_data = MeltemRuntimeData(coordinator=mock_coordinator)

        with patch.object(
            hass.config_entries,
            "async_unload_platforms",
            new=AsyncMock(return_value=True),
        ):
            result = await async_unload_entry(hass, entry)

        assert result is True
        mock_client.close.assert_called_once()

    async def test_unload_cancels_pending_room_write_tasks(
        self, hass: HomeAssistant,
    ) -> None:
        entry = _mock_config_entry()
        entry.add_to_hass(hass)

        async def _sleep_forever():
            try:
                await asyncio.sleep(3600)
            except asyncio.CancelledError:
                raise

        task = hass.async_create_task(_sleep_forever())
        mock_client = MagicMock()
        mock_coordinator = MagicMock()
        mock_coordinator.client = mock_client
        mock_coordinator.async_cancel_room_write_tasks = AsyncMock(side_effect=lambda: task.cancel())
        entry.runtime_data = MeltemRuntimeData(coordinator=mock_coordinator)

        with patch.object(
            hass.config_entries,
            "async_unload_platforms",
            new=AsyncMock(return_value=True),
        ):
            result = await async_unload_entry(hass, entry)

        await asyncio.sleep(0)

        assert result is True
        mock_coordinator.async_cancel_room_write_tasks.assert_awaited_once()
        assert task.cancelled()

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
