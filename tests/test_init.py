"""Tests for integration setup, unload, and data migration in __init__.py."""

from __future__ import annotations

from copy import deepcopy
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.meltem_ventilation import (
    REQUIRED_ENTITY_KEYS,
    async_remove_config_entry_device,
    async_setup_entry,
    async_unload_entry,
)
from custom_components.meltem_ventilation.const import (
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
        mock_client_cls.return_value.close.assert_called_once()

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
        stored_keys = entry.data[CONF_ROOMS][0]["supported_entity_keys"]
        # The probe result is kept, but the profile decides the rest.
        assert "extract_air_flow" in stored_keys
        assert set(supported_entity_keys_for_profile("ii_plain")).issubset(stored_keys)

    async def test_setup_keeps_profile_entities_when_reprobing(
        self, hass: HomeAssistant
    ) -> None:
        """The probe only reports optional sensors, not thresholds or temperatures."""
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
                "custom_components.meltem_ventilation.resolve_preferred_port_path",
                side_effect=lambda port: port,
            ),
            patch(
                "custom_components.meltem_ventilation.MeltemModbusClient"
            ),
            patch(
                "custom_components.meltem_ventilation.MeltemDataUpdateCoordinator"
            ) as mock_coordinator_cls,
            patch(
                "custom_components.meltem_ventilation.detect_slave_details",
                return_value=(
                    "fc",
                    "ID 2 | CO2",
                    ["extract_air_flow", "humidity_extract_air", "co2_extract_air"],
                ),
            ),
            patch.object(
                hass.config_entries,
                "async_forward_entry_setups",
                new=AsyncMock(),
            ),
        ):
            mock_coordinator_cls.return_value.async_refresh = AsyncMock()
            await async_setup_entry(hass, entry)

        stored_keys = set(entry.data[CONF_ROOMS][0]["supported_entity_keys"])
        for key in (
            "humidity_starting_point",
            "co2_max_level",
            "outdoor_air_temperature",
            "supply_air_temperature",
        ):
            assert key in stored_keys

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
    async def test_setup_repairs_probe_only_keys_for_existing_profile(
        self,
        mock_coordinator_cls,
        mock_client_cls,
        _mock_resolve,
        hass: HomeAssistant,
    ) -> None:
        """Old probe-only metadata includes base keys but omits profile controls."""
        mock_coordinator_cls.return_value.async_refresh = AsyncMock()
        data = deepcopy(MINIMAL_ENTRY_DATA)
        data[CONF_ROOMS] = [
            {
                **MINIMAL_ROOM,
                "profile": "ii_fc",
                "supported_entity_keys": sorted(
                    REQUIRED_ENTITY_KEYS
                    | {"humidity_extract_air", "co2_extract_air"}
                ),
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
        assert set(supported_entity_keys_for_profile("ii_fc")).issubset(updated_keys)

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
        mock_client.close.assert_called_once()

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


class TestAsyncRemoveConfigEntryDevice:
    @staticmethod
    def _device(*room_keys: str) -> MagicMock:
        device = MagicMock()
        device.identifiers = {(DOMAIN, room_key) for room_key in room_keys}
        return device

    async def test_configured_unit_cannot_be_removed(self, hass: HomeAssistant) -> None:
        entry = _mock_config_entry()

        assert (
            await async_remove_config_entry_device(hass, entry, self._device("unit_1"))
            is False
        )

    async def test_orphaned_unit_can_be_removed(self, hass: HomeAssistant) -> None:
        entry = _mock_config_entry()

        assert (
            await async_remove_config_entry_device(hass, entry, self._device("unit_9"))
            is True
        )
