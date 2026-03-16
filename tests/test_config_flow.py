"""Tests for config flow and options flow via the HA flow engine."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry
try:
    from homeassistant.helpers.service_info import UsbServiceInfo
except ImportError:  # pragma: no cover - compatibility with older HA layouts
    @dataclass(slots=True)
    class UsbServiceInfo:
        device: str
        vid: str | None = None
        pid: str | None = None
        serial_number: str | None = None
        manufacturer: str | None = None
        description: str | None = None

from custom_components.meltem_ventilation.const import (
    CONF_MAX_REQUESTS_PER_SECOND,
    CONF_PORT,
    CONF_ROOMS,
    DEFAULT_MAX_REQUESTS_PER_SECOND,
    DOMAIN,
)

from custom_components.meltem_ventilation.modbus_helpers import MeltemModbusError


# ---------------------------------------------------------------------------
#  Helpers
# ---------------------------------------------------------------------------

_PATCHES_BASE = "custom_components.meltem_ventilation.config_flow"
_PATCHES_HELPERS = "custom_components.meltem_ventilation.modbus_helpers"


def _patch_validate_ok():
    return patch(f"{_PATCHES_BASE}.validate_serial_connection")


def _patch_scan(slaves: list[int]):
    return patch(f"{_PATCHES_BASE}.scan_available_slaves", return_value=slaves)


def _patch_detect(profile="plain", preview="ID 2 | basic", keys=None):
    keys = keys or ["level", "extract_air_flow", "supply_air_flow"]
    return patch(
        f"{_PATCHES_BASE}.detect_slave_details",
        return_value=(profile, preview, keys),
    )


def _patch_resolve(port="/dev/serial/by-id/test"):
    return patch(f"{_PATCHES_BASE}.resolve_preferred_port_path", return_value=port)


# ---------------------------------------------------------------------------
#  User config flow
# ---------------------------------------------------------------------------


class TestConfigFlowUser:
    async def test_user_step_shows_form(self, hass: HomeAssistant) -> None:
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
        assert result["type"] == FlowResultType.FORM
        assert result["step_id"] == "user"

    async def test_user_step_cannot_connect_shows_error(
        self, hass: HomeAssistant
    ) -> None:
        with (
            patch(
                f"{_PATCHES_BASE}.validate_serial_connection",
                side_effect=MeltemModbusError("fail"),
            ),
            _patch_resolve(),
        ):
            result = await hass.config_entries.flow.async_init(
                DOMAIN, context={"source": config_entries.SOURCE_USER}
            )
            result = await hass.config_entries.flow.async_configure(
                result["flow_id"],
                {
                    CONF_PORT: "/dev/ttyACM0",
                },
            )

        assert result["type"] == FlowResultType.FORM
        assert result["errors"] == {"base": "cannot_connect"}

    async def test_user_step_no_devices_found_shows_error(
        self, hass: HomeAssistant
    ) -> None:
        with (
            _patch_validate_ok(),
            _patch_scan([]),
            _patch_resolve(),
        ):
            result = await hass.config_entries.flow.async_init(
                DOMAIN, context={"source": config_entries.SOURCE_USER}
            )
            result = await hass.config_entries.flow.async_configure(
                result["flow_id"],
                {
                    CONF_PORT: "/dev/ttyACM0",
                },
            )

        assert result["type"] == FlowResultType.FORM
        assert result["errors"] == {"base": "no_devices_found"}

    async def test_user_step_success_proceeds_to_profiles(
        self, hass: HomeAssistant
    ) -> None:
        with (
            _patch_validate_ok(),
            _patch_scan([2]),
            _patch_detect(),
            _patch_resolve(),
        ):
            result = await hass.config_entries.flow.async_init(
                DOMAIN, context={"source": config_entries.SOURCE_USER}
            )
            result = await hass.config_entries.flow.async_configure(
                result["flow_id"],
                {
                    CONF_PORT: "/dev/ttyACM0",
                },
            )

        assert result["type"] == FlowResultType.FORM
        assert result["step_id"] == "profiles"


# ---------------------------------------------------------------------------
#  Profiles step
# ---------------------------------------------------------------------------


class TestConfigFlowProfiles:
    async def test_profiles_step_creates_entry(
        self, hass: HomeAssistant
    ) -> None:
        with (
            _patch_validate_ok(),
            _patch_scan([2]),
            _patch_detect("fc", "ID 2 | CO2", ["level", "co2_extract_air"]),
            _patch_resolve("/dev/serial/by-id/test"),
        ):
            result = await hass.config_entries.flow.async_init(
                DOMAIN, context={"source": config_entries.SOURCE_USER}
            )
            result = await hass.config_entries.flow.async_configure(
                result["flow_id"],
                {
                    CONF_PORT: "/dev/ttyACM0",
                },
            )
            # Now at profiles step — select a profile.
            result = await hass.config_entries.flow.async_configure(
                result["flow_id"],
                {"Unit 1 profile (ID 2 | CO2)": "ii_fc"},
            )

        assert result["type"] == FlowResultType.CREATE_ENTRY
        assert result["title"] == "Meltem"
        assert result["data"][CONF_PORT] == "/dev/serial/by-id/test"
        assert result["data"][CONF_MAX_REQUESTS_PER_SECOND] == DEFAULT_MAX_REQUESTS_PER_SECOND
        rooms = result["data"][CONF_ROOMS]
        assert len(rooms) == 1
        assert rooms[0]["profile"] == "ii_fc"
        assert rooms[0]["slave"] == 2

    async def test_profiles_step_multiple_units(
        self, hass: HomeAssistant
    ) -> None:
        with (
            _patch_validate_ok(),
            _patch_scan([2, 3]),
            _patch_detect("plain", "ID 2 | basic", ["level"]),
            _patch_resolve("/dev/serial/by-id/test"),
        ):
            result = await hass.config_entries.flow.async_init(
                DOMAIN, context={"source": config_entries.SOURCE_USER}
            )
            result = await hass.config_entries.flow.async_configure(
                result["flow_id"],
                {
                    CONF_PORT: "/dev/ttyACM0",
                },
            )
            # Two units discovered — both get the same detect result.
            result = await hass.config_entries.flow.async_configure(
                result["flow_id"],
                {
                    "Unit 1 profile (ID 2 | basic)": "ii_plain",
                    "Unit 2 profile (ID 2 | basic)": "ii_f",
                },
            )

        assert result["type"] == FlowResultType.CREATE_ENTRY
        rooms = result["data"][CONF_ROOMS]
        assert len(rooms) == 2
        assert rooms[0]["profile"] == "ii_plain"
        assert rooms[1]["profile"] == "ii_f"

    async def test_profiles_step_uses_english_label_regardless_of_language(
        self, hass: HomeAssistant
    ) -> None:
        hass.config.language = "de"

        with (
            _patch_validate_ok(),
            _patch_scan([2]),
            _patch_detect("fc", "ID 2 | CO2", ["level", "co2_extract_air"]),
            _patch_resolve("/dev/serial/by-id/test"),
        ):
            result = await hass.config_entries.flow.async_init(
                DOMAIN, context={"source": config_entries.SOURCE_USER}
            )
            result = await hass.config_entries.flow.async_configure(
                result["flow_id"],
                {
                    CONF_PORT: "/dev/ttyACM0",
                },
            )
            result = await hass.config_entries.flow.async_configure(
                result["flow_id"],
                {"Unit 1 profile (ID 2 | CO2)": "ii_fc"},
            )

        assert result["type"] == FlowResultType.CREATE_ENTRY
        assert result["data"][CONF_ROOMS][0]["profile"] == "ii_fc"

    async def test_usb_flow_populates_profile_preview_labels(
        self, hass: HomeAssistant
    ) -> None:
        discovery_info = UsbServiceInfo(
            device="/dev/ttyACM0",
            vid="10AC",
            pid="010A",
            serial_number="gw-1",
            manufacturer="Honeywell",
            description="Modbus",
        )

        with (
            _patch_validate_ok(),
            _patch_scan([2]),
            _patch_detect("fc", "ID 2 | CO2", ["level", "co2_extract_air"]),
            _patch_resolve("/dev/serial/by-id/test"),
        ):
            result = await hass.config_entries.flow.async_init(
                DOMAIN,
                context={"source": config_entries.SOURCE_USB},
                data=discovery_info,
            )
            assert result["step_id"] == "confirm_usb"

            result = await hass.config_entries.flow.async_configure(
                result["flow_id"],
                {
                    CONF_PORT: "/dev/ttyACM0",
                },
            )

        assert result["type"] == FlowResultType.FORM
        assert result["step_id"] == "profiles"
        schema_fields = list(result["data_schema"].schema.keys())
        assert any("ID 2 | CO2" in str(field) for field in schema_fields)

    async def test_usb_flow_returns_to_confirm_usb_on_scan_error(
        self, hass: HomeAssistant
    ) -> None:
        discovery_info = UsbServiceInfo(
            device="/dev/ttyACM0",
            vid="10AC",
            pid="010A",
            serial_number="gw-1",
            manufacturer="Honeywell",
            description="Modbus",
        )

        with patch(
            f"{_PATCHES_BASE}.validate_serial_connection",
            side_effect=MeltemModbusError("fail"),
        ):
            result = await hass.config_entries.flow.async_init(
                DOMAIN,
                context={"source": config_entries.SOURCE_USB},
                data=discovery_info,
            )
            result = await hass.config_entries.flow.async_configure(
                result["flow_id"],
                {
                    CONF_PORT: "/dev/ttyACM0",
                },
            )

        assert result["type"] == FlowResultType.FORM
        assert result["step_id"] == "confirm_usb"
        assert result["errors"] == {"base": "cannot_connect"}


# ---------------------------------------------------------------------------
#  Full end-to-end flow
# ---------------------------------------------------------------------------


class TestConfigFlowEndToEnd:
    async def test_full_flow_user_to_entry(
        self, hass: HomeAssistant
    ) -> None:
        """Walk through the entire user flow: user step → profiles → entry creation."""
        with (
            _patch_validate_ok(),
            _patch_scan([2, 3]),
            _patch_detect("fc_voc", "ID 2 | VOC", ["level", "voc_supply_air"]),
            _patch_resolve("/dev/serial/by-id/stable"),
        ):
            # Step 1: user
            result = await hass.config_entries.flow.async_init(
                DOMAIN, context={"source": config_entries.SOURCE_USER}
            )
            assert result["step_id"] == "user"

            # Step 2: submit port
            result = await hass.config_entries.flow.async_configure(
                result["flow_id"],
                {
                    CONF_PORT: "/dev/ttyACM0",
                },
            )
            assert result["step_id"] == "profiles"

            # Step 3: submit profiles
            result = await hass.config_entries.flow.async_configure(
                result["flow_id"],
                {
                    "Unit 1 profile (ID 2 | VOC)": "ii_fc_voc",
                    "Unit 2 profile (ID 2 | VOC)": "ii_fc",
                },
            )

        assert result["type"] == FlowResultType.CREATE_ENTRY
        data = result["data"]
        assert data[CONF_PORT] == "/dev/serial/by-id/stable"
        assert data[CONF_MAX_REQUESTS_PER_SECOND] == DEFAULT_MAX_REQUESTS_PER_SECOND
        assert len(data[CONF_ROOMS]) == 2
        assert data[CONF_ROOMS][0]["profile"] == "ii_fc_voc"
        assert data[CONF_ROOMS][1]["profile"] == "ii_fc"


# ---------------------------------------------------------------------------
#  Options flow
# ---------------------------------------------------------------------------


class TestOptionsFlow:
    @staticmethod
    def _setup_entry(hass: HomeAssistant) -> MockConfigEntry:
        entry = MockConfigEntry(
            domain=DOMAIN,
            title="Meltem",
            data={
                CONF_PORT: "/dev/serial/by-id/test",
                CONF_MAX_REQUESTS_PER_SECOND: 2.0,
                CONF_ROOMS: [
                    {
                        "key": "unit_1",
                        "name": "Unit 1",
                        "slave": 2,
                        "profile": "ii_plain",
                        "preview": "ID 2 | basic",
                        "supported_entity_keys": ["level"],
                    }
                ],
            },
            options={},
            version=1,
            source="user",
        )
        entry.add_to_hass(hass)
        return entry

    async def test_options_init_shows_menu(
        self, hass: HomeAssistant
    ) -> None:
        entry = self._setup_entry(hass)
        result = await hass.config_entries.options.async_init(entry.entry_id)
        assert result["type"] == FlowResultType.MENU
        assert result["step_id"] == "init"

    async def test_options_init_routes_to_edit_connection(
        self, hass: HomeAssistant
    ) -> None:
        entry = self._setup_entry(hass)

        result = await hass.config_entries.options.async_init(entry.entry_id)
        result = await hass.config_entries.options.async_configure(
            result["flow_id"],
            {"next_step_id": "edit_connection"},
        )

        assert result["type"] == FlowResultType.FORM
        assert result["step_id"] == "edit_connection"

    async def test_options_edit_connection_updates_entry_and_reloads_when_port_changes(
        self, hass: HomeAssistant
    ) -> None:
        entry = self._setup_entry(hass)
        entry.runtime_data = type(
            "RuntimeData",
            (),
            {"coordinator": type("Coordinator", (), {"update_request_rate": MagicMock()})()},
        )()

        with patch(
            "custom_components.meltem_ventilation.config_flow.validate_serial_connection"
        ) as validate_connection, patch(
            "custom_components.meltem_ventilation.config_flow.resolve_preferred_port_path",
            return_value="/dev/serial/by-id/new-port",
        ):
            result = await hass.config_entries.options.async_init(entry.entry_id)
            result = await hass.config_entries.options.async_configure(
                result["flow_id"],
                {"next_step_id": "edit_connection"},
            )
            result = await hass.config_entries.options.async_configure(
                result["flow_id"],
                {
                    CONF_PORT: "/dev/ttyACM1",
                    CONF_MAX_REQUESTS_PER_SECOND: 5.0,
                },
            )

        assert result["type"] == FlowResultType.CREATE_ENTRY
        assert entry.data[CONF_PORT] == "/dev/serial/by-id/new-port"
        assert entry.options[CONF_MAX_REQUESTS_PER_SECOND] == 5.0
        validate_connection.assert_called_once()

    async def test_options_edit_connection_updates_request_rate_without_reload(
        self, hass: HomeAssistant
    ) -> None:
        entry = self._setup_entry(hass)
        coordinator = type(
            "Coordinator",
            (),
            {"update_request_rate": MagicMock()},
        )()
        entry.runtime_data = type(
            "RuntimeData",
            (),
            {"coordinator": coordinator},
        )()

        result = await hass.config_entries.options.async_init(entry.entry_id)
        result = await hass.config_entries.options.async_configure(
            result["flow_id"],
            {"next_step_id": "edit_connection"},
        )
        result = await hass.config_entries.options.async_configure(
            result["flow_id"],
            {
                CONF_PORT: "/dev/serial/by-id/test",
                CONF_MAX_REQUESTS_PER_SECOND: 5.0,
            },
        )

        assert result["type"] == FlowResultType.CREATE_ENTRY
        assert result["data"][CONF_MAX_REQUESTS_PER_SECOND] == 5.0
        assert entry.options[CONF_MAX_REQUESTS_PER_SECOND] == 5.0
        coordinator.update_request_rate.assert_called_once_with(5.0)

    async def test_options_edit_profiles_updates_existing_rooms(
        self, hass: HomeAssistant
    ) -> None:
        entry = self._setup_entry(hass)
        entry.runtime_data = type(
            "RuntimeData",
            (),
            {
                "coordinator": type(
                    "Coordinator",
                    (),
                    {
                        "async_probe_slave_details": AsyncMock(
                            return_value=("fc", "ID 99 | CO2", ["level", "co2_extract_air"])
                        )
                    },
                )()
            },
        )()

        with patch.object(
            hass.config_entries, "async_reload", new=AsyncMock()
        ) as mock_reload:
            result = await hass.config_entries.options.async_init(entry.entry_id)
            result = await hass.config_entries.options.async_configure(
                result["flow_id"],
                {"next_step_id": "edit_profiles"},
            )
            result = await hass.config_entries.options.async_configure(
                result["flow_id"],
                {"Unit 1 profile (ID 99 | CO2)": "ii_fc"},
            )

        assert result["type"] == FlowResultType.CREATE_ENTRY
        mock_reload.assert_awaited_once_with(entry.entry_id)
        assert entry.data[CONF_ROOMS][0]["profile"] == "ii_fc"
        assert entry.data[CONF_ROOMS][0]["preview"] == "ID 99 | CO2"
        assert "co2_extract_air" in entry.data[CONF_ROOMS][0]["supported_entity_keys"]
        assert "humidity_extract_air" in entry.data[CONF_ROOMS][0]["supported_entity_keys"]

    async def test_options_edit_profiles_uses_rendered_field_mapping_on_submit(
        self, hass: HomeAssistant
    ) -> None:
        entry = self._setup_entry(hass)
        probe = AsyncMock(
            side_effect=[
                ("fc", "ID 99 | CO2", ["level", "co2_extract_air"]),
            ]
        )
        entry.runtime_data = type(
            "RuntimeData",
            (),
            {"coordinator": type("Coordinator", (), {"async_probe_slave_details": probe})()},
        )()

        with patch.object(
            hass.config_entries, "async_reload", new=AsyncMock()
        ):
            result = await hass.config_entries.options.async_init(entry.entry_id)
            result = await hass.config_entries.options.async_configure(
                result["flow_id"],
                {"next_step_id": "edit_profiles"},
            )
            result = await hass.config_entries.options.async_configure(
                result["flow_id"],
                {"Unit 1 profile (ID 99 | CO2)": "ii_fc"},
            )

        assert result["type"] == FlowResultType.CREATE_ENTRY
        assert probe.await_count == 1
