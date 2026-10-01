"""Tests for config flow and options flow via the HA flow engine."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant import config_entries
from homeassistant.config_entries import ConfigFlowResult
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.service_info.usb import UsbServiceInfo
from modbus_connection import IllegalDataAddressError, ModbusTimeoutError
from modbus_connection.mock import MockModbusConnection
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.meltem_ventilation.const import (
    CONF_MAX_REQUESTS_PER_SECOND,
    CONF_PORT,
    CONF_ROOMS,
    DEFAULT_GATEWAY_DEVICE_ID,
    DEFAULT_MAX_REQUESTS_PER_SECOND,
    DOMAIN,
    FIXED_TIMEOUT,
    REQUEST_GAP_SECONDS,
)
from custom_components.meltem_ventilation.modbus_helpers import (
    MeltemConnectionError,
)

# ---------------------------------------------------------------------------
#  Helpers
# ---------------------------------------------------------------------------

_PATCHES_BASE = "custom_components.meltem_ventilation.config_flow"
_PORT = "/dev/ttyACM0"
_STABLE_PORT = "/dev/serial/by-id/test"
_USB_DISCOVERY = UsbServiceInfo(
    device=_PORT,
    vid="10AC",
    pid="010A",
    serial_number="gw-1",
    manufacturer="Honeywell",
    description="Modbus",
)


@pytest.fixture(autouse=True, name="gateway_link")
def gateway_link_fixture() -> Iterator[MockModbusConnection]:
    """Serve the flow's temporary units from memory instead of a serial port."""

    connection = MockModbusConnection()

    @asynccontextmanager
    async def _temporary_unit(hass, params, unit_id):
        yield connection.for_unit(unit_id)

    with patch(f"{_PATCHES_BASE}.async_get_temporary_unit", new=_temporary_unit):
        yield connection


@asynccontextmanager
async def _conflicting_unit(hass, params, unit_id):
    raise HomeAssistantError("already in use with different link settings")
    yield  # pragma: no cover


def _patch_validate_ok():
    return patch(
        f"{_PATCHES_BASE}.read_gateway_node_count", new=AsyncMock(return_value=1)
    )


def _patch_scan(slaves: list[int]):
    return patch(
        f"{_PATCHES_BASE}.discover_gateway_nodes",
        new=AsyncMock(return_value=slaves),
    )


def _patch_scan_error(error: Exception):
    return patch(
        f"{_PATCHES_BASE}.discover_gateway_nodes",
        new=AsyncMock(side_effect=error),
    )


def _patch_detect(profile: str = "plain", preview: str = "ID 2 | basic"):
    return patch(
        f"{_PATCHES_BASE}.detect_slave_details",
        new=AsyncMock(return_value=(profile, preview)),
    )


def _patch_resolve(port: str = _STABLE_PORT):
    return patch(f"{_PATCHES_BASE}.resolve_preferred_port_path", return_value=port)


def _patch_resolve_unchanged():
    return patch(
        f"{_PATCHES_BASE}.resolve_preferred_port_path", side_effect=lambda port: port
    )


async def _async_submit_port(hass: HomeAssistant, port: str = _PORT) -> ConfigFlowResult:
    """Start the user flow and submit the serial port."""

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    return await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_PORT: port}
    )


async def _async_submit_usb_port(hass: HomeAssistant) -> ConfigFlowResult:
    """Start the USB discovery flow and confirm the discovered port."""

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USB}, data=_USB_DISCOVERY
    )
    assert result["step_id"] == "confirm_usb"
    return await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_PORT: _PORT}
    )


async def _async_open_option(
    hass: HomeAssistant, entry: MockConfigEntry, step_id: str
) -> ConfigFlowResult:
    result = await hass.config_entries.options.async_init(entry.entry_id)
    return await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": step_id}
    )


async def _async_submit_request_rate(
    hass: HomeAssistant, entry: MockConfigEntry
) -> ConfigFlowResult:
    """Open the request-rate options and submit a rate of 5."""

    result = await _async_open_option(hass, entry, "edit_request_rate")
    return await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_MAX_REQUESTS_PER_SECOND: 5.0}
    )


async def _async_reconfigure(
    hass: HomeAssistant, entry: MockConfigEntry, port: str
) -> ConfigFlowResult:
    """Start the reconfigure flow and submit ``port``."""

    result = await entry.start_reconfigure_flow(hass)
    assert result["step_id"] == "reconfigure"
    return await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_PORT: port}
    )


async def _async_submit_profiles(
    hass: HomeAssistant, result: ConfigFlowResult, profiles: dict[str, str]
) -> ConfigFlowResult:
    return await hass.config_entries.flow.async_configure(result["flow_id"], profiles)


def _attach_coordinator(entry: MockConfigEntry, **methods: Any) -> SimpleNamespace:
    """Give the entry a running coordinator that offers only ``methods``."""

    coordinator = SimpleNamespace(**methods)
    entry.runtime_data = SimpleNamespace(coordinator=coordinator)
    return coordinator


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

    @pytest.mark.parametrize(
        ("error", "expected"),
        [
            (MeltemConnectionError("fail"), "cannot_connect"),
            (ValueError("invalid port"), "unknown"),
        ],
    )
    async def test_scan_errors_are_shown_on_the_form(
        self, hass: HomeAssistant, error: Exception, expected: str
    ) -> None:
        with _patch_scan_error(error), _patch_resolve():
            result = await _async_submit_port(hass)

        assert result["type"] == FlowResultType.FORM
        assert result["errors"] == {"base": expected}

    async def test_user_step_no_devices_found_shows_error(
        self, hass: HomeAssistant
    ) -> None:
        with _patch_scan([]), _patch_resolve():
            result = await _async_submit_port(hass)

        assert result["type"] == FlowResultType.FORM
        assert result["errors"] == {"base": "no_devices_found"}

    async def test_user_step_success_proceeds_to_profiles(
        self, hass: HomeAssistant
    ) -> None:
        with _patch_scan([2]), _patch_detect(), _patch_resolve():
            result = await _async_submit_port(hass)

        assert result["type"] == FlowResultType.FORM
        assert result["step_id"] == "profiles"

    async def test_user_step_reads_the_gateway_and_probes_each_unit(
        self, hass: HomeAssistant, gateway_link: MockModbusConnection
    ) -> None:
        gateway = gateway_link.for_unit(DEFAULT_GATEWAY_DEVICE_ID)
        gateway.holding.update({43901: 2, 43902: [2, 3]})
        co2_unit = gateway_link.for_unit(2)
        # Product ID 116852 as a little-endian uint32, then humidity and CO2.
        co2_unit.holding.update(
            {40002: [116852 & 0xFFFF, 116852 >> 16], 41006: 45, 41007: 800, 41011: 50}
        )
        co2_unit.fail_read(41013, IllegalDataAddressError())
        gateway_link.for_unit(3).fail_requests(ModbusTimeoutError("silent"))

        with _patch_resolve():
            result = await _async_submit_port(hass)

        assert result["type"] == FlowResultType.FORM
        assert result["step_id"] == "profiles"
        assert list(result["data_schema"].schema.keys()) == ["slave_2", "slave_3"]
        details = result["description_placeholders"]["unit_details"]
        assert "ID 116852 | CO2" in details
        for unit in (gateway, co2_unit):
            assert unit.required_timeout == FIXED_TIMEOUT
            assert unit.message_spacing == REQUEST_GAP_SECONDS
        # The silent unit is given up after its first probe and one retry.
        assert len(gateway_link.for_unit(3).read_events) == 2

    async def test_user_step_scans_and_probes_on_one_link(
        self, hass: HomeAssistant, gateway_link: MockModbusConnection
    ) -> None:
        gateway_link.for_unit(DEFAULT_GATEWAY_DEVICE_ID).holding.update(
            {43901: 2, 43902: [2, 3]}
        )
        holds: list[str] = []

        @asynccontextmanager
        async def _recording_unit(hass, params, unit_id):
            holds.append(f"take {unit_id}")
            try:
                yield gateway_link.for_unit(unit_id)
            finally:
                holds.append(f"release {unit_id}")

        with (
            patch(f"{_PATCHES_BASE}.async_get_temporary_unit", new=_recording_unit),
            _patch_resolve(),
        ):
            await _async_submit_port(hass)

        # Releasing the last hold would close the port in between.
        assert holds[:3] == ["take 1", "take 2", "take 3"]
        assert sorted(holds[3:]) == ["release 1", "release 2", "release 3"]

    async def test_user_step_port_held_with_other_settings_shows_port_in_use(
        self, hass: HomeAssistant
    ) -> None:
        with (
            patch(f"{_PATCHES_BASE}.async_get_temporary_unit", new=_conflicting_unit),
            _patch_resolve(),
        ):
            result = await _async_submit_port(hass)

        assert result["type"] == FlowResultType.FORM
        assert result["errors"] == {"base": "port_in_use"}


# ---------------------------------------------------------------------------
#  Profiles step
# ---------------------------------------------------------------------------


class TestConfigFlowProfiles:
    async def test_profiles_step_creates_entry(
        self, hass: HomeAssistant
    ) -> None:
        with _patch_scan([2]), _patch_detect("fc", "ID 2 | CO2"), _patch_resolve():
            result = await _async_submit_port(hass)
            result = await _async_submit_profiles(hass, result, {"slave_2": "ii_fc"})

        assert result["type"] == FlowResultType.CREATE_ENTRY
        assert result["title"] == "Meltem Gateway M-WRG-GW"
        assert result["data"][CONF_PORT] == _STABLE_PORT
        assert result["data"][CONF_MAX_REQUESTS_PER_SECOND] == DEFAULT_MAX_REQUESTS_PER_SECOND
        rooms = result["data"][CONF_ROOMS]
        assert len(rooms) == 1
        assert rooms[0]["profile"] == "ii_fc"
        assert rooms[0]["slave"] == 2
        entry = result["result"]
        assert (entry.version, entry.minor_version) == (1, 2)
        assert entry.unique_id == _STABLE_PORT

    async def test_profiles_step_multiple_units(
        self, hass: HomeAssistant
    ) -> None:
        with _patch_scan([2, 3]), _patch_detect(), _patch_resolve():
            result = await _async_submit_port(hass)
            result = await _async_submit_profiles(
                hass, result, {"slave_2": "ii_plain", "slave_3": "ii_f"}
            )

        assert result["type"] == FlowResultType.CREATE_ENTRY
        rooms = result["data"][CONF_ROOMS]
        assert len(rooms) == 2
        assert rooms[0]["profile"] == "ii_plain"
        assert rooms[1]["profile"] == "ii_f"

    async def test_profiles_step_uses_stable_field_keys_regardless_of_language(
        self, hass: HomeAssistant
    ) -> None:
        hass.config.language = "de"

        with _patch_scan([2]), _patch_detect("fc", "ID 2 | CO2"), _patch_resolve():
            result = await _async_submit_port(hass)
            result = await _async_submit_profiles(hass, result, {"slave_2": "ii_fc"})

        assert result["type"] == FlowResultType.CREATE_ENTRY
        assert result["data"][CONF_ROOMS][0]["profile"] == "ii_fc"

    async def test_usb_flow_shows_unit_previews_in_the_description(
        self, hass: HomeAssistant
    ) -> None:
        with _patch_scan([2]), _patch_detect("fc", "ID 2 | CO2"), _patch_resolve():
            result = await _async_submit_usb_port(hass)

        assert result["type"] == FlowResultType.FORM
        assert result["step_id"] == "profiles"
        assert list(result["data_schema"].schema.keys()) == ["slave_2"]
        assert "Hardware ID 2 | CO2" in result["description_placeholders"]["unit_details"]

    async def test_usb_flow_returns_to_confirm_usb_on_scan_error(
        self, hass: HomeAssistant
    ) -> None:
        with _patch_scan_error(MeltemConnectionError("fail")):
            result = await _async_submit_usb_port(hass)

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
            _patch_scan([2, 3]),
            _patch_detect("fc_voc", "ID 2 | VOC"),
            _patch_resolve("/dev/serial/by-id/stable"),
        ):
            result = await hass.config_entries.flow.async_init(
                DOMAIN, context={"source": config_entries.SOURCE_USER}
            )
            assert result["step_id"] == "user"

            result = await hass.config_entries.flow.async_configure(
                result["flow_id"], {CONF_PORT: _PORT}
            )
            assert result["step_id"] == "profiles"

            result = await _async_submit_profiles(
                hass, result, {"slave_2": "ii_fc_voc", "slave_3": "ii_fc"}
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


@pytest.fixture(name="entry")
def entry_fixture(hass: HomeAssistant) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Meltem",
        data={
            CONF_PORT: _STABLE_PORT,
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


class TestOptionsFlow:
    async def test_options_init_shows_menu(
        self, hass: HomeAssistant, entry: MockConfigEntry
    ) -> None:
        result = await hass.config_entries.options.async_init(entry.entry_id)
        assert result["type"] == FlowResultType.MENU
        assert result["step_id"] == "init"

    async def test_options_survive_an_entry_that_never_finished_setup(
        self, hass: HomeAssistant, entry: MockConfigEntry
    ) -> None:
        """A retrying entry has no runtime_data, but options must stay usable."""
        assert not hasattr(entry, "runtime_data")

        edit_profiles = await _async_open_option(hass, entry, "edit_profiles")
        assert edit_profiles["step_id"] == "edit_profiles"
        assert "Hardware ID 2 | basic" in (
            edit_profiles["description_placeholders"]["unit_details"]
        )

        result = await _async_open_option(hass, entry, "rescan_units")
        rescan = await hass.config_entries.options.async_configure(
            result["flow_id"], {}
        )
        assert rescan["step_id"] == "rescan_units"
        assert rescan["errors"] == {"base": "cannot_connect"}

    async def test_options_request_rate_reloads_without_a_coordinator(
        self, hass: HomeAssistant, entry: MockConfigEntry
    ) -> None:
        with patch.object(
            hass.config_entries, "async_reload", new=AsyncMock()
        ) as mock_reload:
            result = await _async_submit_request_rate(hass, entry)

        assert result["type"] == FlowResultType.CREATE_ENTRY
        assert entry.options[CONF_MAX_REQUESTS_PER_SECOND] == 5.0
        mock_reload.assert_awaited_once_with(entry.entry_id)

    async def test_options_init_routes_to_edit_request_rate(
        self, hass: HomeAssistant, entry: MockConfigEntry
    ) -> None:
        result = await _async_open_option(hass, entry, "edit_request_rate")

        assert result["type"] == FlowResultType.FORM
        assert result["step_id"] == "edit_request_rate"
        assert list(result["data_schema"].schema) == [CONF_MAX_REQUESTS_PER_SECOND]

    async def test_options_request_rate_updates_without_reload(
        self, hass: HomeAssistant, entry: MockConfigEntry
    ) -> None:
        coordinator = _attach_coordinator(entry, update_request_rate=MagicMock())

        with patch.object(
            hass.config_entries, "async_reload", new=AsyncMock()
        ) as mock_reload:
            result = await _async_submit_request_rate(hass, entry)

        assert result["type"] == FlowResultType.CREATE_ENTRY
        assert result["data"][CONF_MAX_REQUESTS_PER_SECOND] == 5.0
        assert entry.options[CONF_MAX_REQUESTS_PER_SECOND] == 5.0
        coordinator.update_request_rate.assert_called_once_with(5.0)
        mock_reload.assert_not_awaited()

    async def test_options_edit_profiles_updates_existing_rooms(
        self, hass: HomeAssistant, entry: MockConfigEntry
    ) -> None:
        probe = AsyncMock(return_value=("fc", "ID 99 | CO2"))
        _attach_coordinator(entry, async_probe_slave_details=probe)

        with patch.object(
            hass.config_entries, "async_reload", new=AsyncMock()
        ) as mock_reload:
            result = await _async_open_option(hass, entry, "edit_profiles")
            result = await hass.config_entries.options.async_configure(
                result["flow_id"], {"slave_2": "ii_fc"}
            )

        assert result["type"] == FlowResultType.CREATE_ENTRY
        mock_reload.assert_awaited_once_with(entry.entry_id)
        # Showing and submitting the form must not probe the unit twice.
        assert probe.await_count == 1
        assert entry.data[CONF_ROOMS][0]["profile"] == "ii_fc"
        assert entry.data[CONF_ROOMS][0]["preview"] == "ID 99 | CO2"
        # Entities follow from the profile on load, so stale stored keys are dropped.
        assert "supported_entity_keys" not in entry.data[CONF_ROOMS][0]

    async def test_options_edit_profiles_shows_the_device_name_from_the_registry(
        self, hass: HomeAssistant, entry: MockConfigEntry
    ) -> None:
        _attach_coordinator(
            entry,
            async_probe_slave_details=AsyncMock(return_value=("fc", "ID 99 | CO2")),
        )

        registry = dr.async_get(hass)
        device = registry.async_get_or_create(
            config_entry_id=entry.entry_id,
            identifiers={(DOMAIN, "unit_1")},
            name="Meltem Modbus Unit 1",
        )
        registry.async_update_device(device.id, name_by_user="Bad")

        result = await _async_open_option(hass, entry, "edit_profiles")

        assert result["step_id"] == "edit_profiles"
        assert result["description_placeholders"]["unit_details"] == (
            "- **2**: Bad, Hardware ID 99 | CO2"
        )


# ---------------------------------------------------------------------------
#  Reconfigure flow
# ---------------------------------------------------------------------------


class TestReconfigureFlow:
    async def test_reconfigure_updates_the_port_and_reloads(
        self, hass: HomeAssistant, entry: MockConfigEntry
    ) -> None:
        with (
            _patch_validate_ok() as validate_connection,
            patch(
                f"{_PATCHES_BASE}.resolve_preferred_port_path",
                side_effect=lambda port: (
                    "/dev/serial/by-id/new-port" if port == "/dev/ttyACM1" else port
                ),
            ),
            patch.object(hass.config_entries, "async_schedule_reload") as mock_reload,
        ):
            result = await _async_reconfigure(hass, entry, "/dev/ttyACM1")

        assert result["type"] == FlowResultType.ABORT
        assert result["reason"] == "reconfigure_successful"
        assert entry.data[CONF_PORT] == "/dev/serial/by-id/new-port"
        assert entry.unique_id == "/dev/serial/by-id/new-port"
        validate_connection.assert_awaited_once()
        mock_reload.assert_called_once_with(entry.entry_id)

    async def test_reconfigure_reports_a_port_held_with_other_settings(
        self, hass: HomeAssistant, entry: MockConfigEntry
    ) -> None:
        with (
            patch(f"{_PATCHES_BASE}.async_get_temporary_unit", new=_conflicting_unit),
            _patch_resolve_unchanged(),
        ):
            result = await _async_reconfigure(hass, entry, "/dev/ttyACM1")

        assert result["type"] == FlowResultType.FORM
        assert result["errors"] == {"base": "port_in_use"}
        assert entry.data[CONF_PORT] == _STABLE_PORT

    async def test_reconfigure_ignores_an_equivalent_port_path(
        self, hass: HomeAssistant, entry: MockConfigEntry
    ) -> None:
        """A raw path that resolves to the stored by-id path is no change."""
        hass.config_entries.async_update_entry(entry, unique_id=_STABLE_PORT)
        with (
            _patch_validate_ok() as validate_connection,
            _patch_resolve(),
            patch.object(hass.config_entries, "async_schedule_reload") as mock_reload,
        ):
            result = await _async_reconfigure(hass, entry, _PORT)

        assert result["type"] == FlowResultType.ABORT
        validate_connection.assert_not_called()
        mock_reload.assert_not_called()
        assert entry.data[CONF_PORT] == _STABLE_PORT


class TestUsbPortEdit:
    async def test_editing_the_discovered_port_updates_the_unique_id(
        self, hass: HomeAssistant
    ) -> None:
        with (
            _patch_scan([2]),
            _patch_detect(),
            patch(
                f"{_PATCHES_BASE}.resolve_preferred_port_path",
                side_effect=lambda port: f"/dev/serial/by-id/{port.rsplit('/', 1)[-1]}",
            ),
        ):
            result = await hass.config_entries.flow.async_init(
                DOMAIN, context={"source": config_entries.SOURCE_USB}, data=_USB_DISCOVERY
            )
            result = await hass.config_entries.flow.async_configure(
                result["flow_id"], {CONF_PORT: "/dev/ttyACM1"}
            )
            result = await _async_submit_profiles(hass, result, {"slave_2": "ii_plain"})

        assert result["type"] == FlowResultType.CREATE_ENTRY
        assert result["result"].unique_id == "/dev/serial/by-id/ttyACM1"
        assert result["data"][CONF_PORT] == "/dev/serial/by-id/ttyACM1"
