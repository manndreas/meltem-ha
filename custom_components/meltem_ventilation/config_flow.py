"""Config flow and options flow for Meltem gateways.

The flow uses the gateway bridge registers to discover configured units first,
then performs a small per-unit probe to preselect the most likely profile.
The heavier runtime reads happen only after the config entry is created.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from functools import partial
import logging
from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import callback
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers import selector
try:
    from homeassistant.helpers.service_info import UsbServiceInfo
except ImportError:  # pragma: no cover - compatibility with older HA layouts
    @dataclass(slots=True)
    class UsbServiceInfo:
        """Fallback USB discovery payload for test/runtime compatibility."""

        device: str
        vid: str | None = None
        pid: str | None = None
        serial_number: str | None = None
        manufacturer: str | None = None
        description: str | None = None

from .const import (
    CONF_MAX_REQUESTS_PER_SECOND,
    CONF_PORT,
    CONF_ROOMS,
    DEFAULT_PORT,
    DEFAULT_MAX_REQUESTS_PER_SECOND,
    DEFAULT_SCAN_SLAVE_END,
    DEFAULT_SCAN_SLAVE_START,
    DOMAIN,
    FIXED_BAUDRATE,
    FIXED_BYTESIZE,
    FIXED_PARITY,
    FIXED_STOPBITS,
    FIXED_TIMEOUT,
    INTEGRATION_NAME,
    MAX_MAX_REQUESTS_PER_SECOND,
    MIN_MAX_REQUESTS_PER_SECOND,
    MODEL_PROFILE_LABELS,
)
from .modbus_helpers import (
    MeltemModbusError,
    SerialSettings,
    build_scan_settings,
    build_setup_probe_settings,
    detect_slave_details,
    resolve_preferred_port_path,
    scan_available_slaves,
    supported_entity_keys_for_profile,
    validate_serial_connection,
)
from .models import MeltemRuntimeData

_LOGGER = logging.getLogger(__name__)



def _build_options_result_data(config_entry: ConfigEntry, **updates: object) -> dict[str, object]:
    """Return the persisted options payload for finishing an options flow."""

    return {
        **config_entry.options,
        **updates,
    }


def _build_profile_selector() -> selector.SelectSelector:
    """Build the selector used for per-device profile selection."""

    return selector.SelectSelector(
        selector.SelectSelectorConfig(
            mode=selector.SelectSelectorMode.DROPDOWN,
            options=[
                selector.SelectOptionDict(value=key, label=label)
                for key, label in MODEL_PROFILE_LABELS.items()
            ],
        )
    )


def _build_max_request_rate_selector() -> selector.NumberSelector:
    """Build the selector used for the maximum scheduler request rate."""

    return selector.NumberSelector(
        selector.NumberSelectorConfig(
            min=MIN_MAX_REQUESTS_PER_SECOND,
            max=MAX_MAX_REQUESTS_PER_SECOND,
            step=0.5,
            mode=selector.NumberSelectorMode.BOX,
        )
    )


def _profile_field_key(index: int) -> str:
    """Build a human-friendly field label for one detected unit."""

    return f"Unit {index} profile"


def _default_room_name(index: int) -> str:
    """Build a default room/device name."""

    return f"Unit {index}"


def _profile_label(
    index: int,
    slave: int,
    previews_by_slave: Mapping[int, str],
) -> str:
    """Build a user-facing profile label with an optional preview."""

    base = _profile_field_key(index)
    preview = previews_by_slave.get(slave)
    if not preview:
        return base
    return f"{base} ({preview})"


def _detected_profile_default(
    slave: int, detected_profiles_by_slave: Mapping[int, str]
) -> str:
    """Return the default profile selection for a detected unit.

    The setup probe determines the suffix part from available sensors.
    The series default stays on M-WRG-II unless a more specific mapping exists.
    """

    detected_profile = detected_profiles_by_slave.get(slave)
    capability_defaults = {
        "plain": "ii_plain",
        "f": "ii_f",
        "fc": "ii_fc",
        "fc_voc": "ii_fc_voc",
    }
    return capability_defaults.get(detected_profile or "", "ii_plain")


def _build_rooms_from_profiles(
    slaves: list[int],
    selected_profiles: Mapping[str, Any],
    previews_by_slave: Mapping[int, str] | None = None,
    existing_rooms_by_slave: Mapping[int, Mapping[str, Any]] | None = None,
    *,
    profile_fields_by_slave: Mapping[int, str] | None = None,
) -> list[dict[str, object]]:
    """Build room config entries from selected per-device profiles."""

    rooms: list[dict[str, object]] = []
    previews_by_slave = previews_by_slave or {}
    existing_rooms_by_slave = existing_rooms_by_slave or {}
    profile_fields_by_slave = profile_fields_by_slave or {}
    used_room_keys: set[str] = set()

    for index, slave in enumerate(slaves, start=1):
        existing_room = existing_rooms_by_slave.get(slave, {})
        field_key = profile_fields_by_slave.get(
            slave,
            _profile_label(index, slave, previews_by_slave),
        )
        selected_profile = str(selected_profiles[field_key])
        preferred_room_key = str(existing_room.get("key") or f"slave_{slave}")
        room_key = preferred_room_key
        suffix = 2
        while room_key in used_room_keys:
            room_key = f"{preferred_room_key}_{suffix}"
            suffix += 1
        used_room_keys.add(room_key)
        rooms.append(
            {
                "key": room_key,
                "name": existing_room.get("name", _default_room_name(index)),
                "slave": slave,
                "profile": selected_profile,
                "preview": previews_by_slave.get(slave) or existing_room.get("preview"),
                "supported_entity_keys": supported_entity_keys_for_profile(
                    selected_profile
                ),
            }
        )

    return rooms


async def _async_scan_slaves(hass, settings: SerialSettings) -> list[int]:
    """Validate the serial connection and discover configured units via the gateway."""

    await hass.async_add_executor_job(validate_serial_connection, settings)
    return await hass.async_add_executor_job(
        partial(
            scan_available_slaves,
            build_scan_settings(settings),
            start=DEFAULT_SCAN_SLAVE_START,
            end=DEFAULT_SCAN_SLAVE_END,
        )
    )


def _build_serial_settings(port: str) -> SerialSettings:
    """Build the fixed serial settings for the given port."""

    return SerialSettings(
        port=port,
        baudrate=FIXED_BAUDRATE,
        bytesize=FIXED_BYTESIZE,
        parity=FIXED_PARITY,
        stopbits=FIXED_STOPBITS,
        timeout=float(FIXED_TIMEOUT),
    )


async def _async_probe_discovered_slaves(
    hass,
    settings: SerialSettings,
    discovered_slaves: list[int],
) -> tuple[dict[int, str], dict[int, str], dict[int, list[str]]]:
    """Probe discovered units for previews, detected profiles, and entity keys."""

    probe_settings = build_setup_probe_settings(settings)
    preview_by_slave: dict[int, str] = {}
    detected_profile_by_slave: dict[int, str] = {}
    supported_entity_keys_by_slave: dict[int, list[str]] = {}

    for slave in discovered_slaves:
        try:
            (
                detected_profile,
                preview,
                supported_entity_keys,
            ) = await hass.async_add_executor_job(
                detect_slave_details,
                probe_settings,
                slave,
            )
        except MeltemModbusError as err:
            _LOGGER.warning(
                "Setup probe failed for Meltem unit at slave %s: %s",
                slave,
                err,
            )
            detected_profile = "plain"
            preview = None
            supported_entity_keys = supported_entity_keys_for_profile("ii_plain")
        detected_profile_by_slave[slave] = detected_profile
        if preview:
            preview_by_slave[slave] = preview
        supported_entity_keys_by_slave[slave] = supported_entity_keys

    return (
        preview_by_slave,
        detected_profile_by_slave,
        supported_entity_keys_by_slave,
    )


class MeltemVentilationConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Meltem ventilation."""

    VERSION = 1

    def __init__(self) -> None:
        self._port = DEFAULT_PORT
        self._max_requests_per_second = DEFAULT_MAX_REQUESTS_PER_SECOND
        self._discovered_slaves: list[int] = []
        self._preview_by_slave: dict[int, str] = {}
        self._detected_profile_by_slave: dict[int, str] = {}
        self._supported_entity_keys_by_slave: dict[int, list[str]] = {}
        self._profile_fields_by_slave: dict[int, str] = {}
        self._usb_title_placeholders: dict[str, str] | None = None

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: ConfigEntry,
    ) -> MeltemVentilationOptionsFlow:
        """Return the options flow handler."""

        return MeltemVentilationOptionsFlow(config_entry)

    async def async_step_user(self, user_input: dict | None = None) -> FlowResult:
        """Collect the serial port and scan for connected units."""

        errors: dict[str, str] = {}

        if user_input is not None:
            selected_port = user_input[CONF_PORT]
            normalized_port = resolve_preferred_port_path(selected_port)
            settings = _build_serial_settings(selected_port)

            try:
                discovered_slaves = await _async_scan_slaves(self.hass, settings)
            except MeltemModbusError:
                errors["base"] = "cannot_connect"
            else:
                _LOGGER.info(
                    "Read configured Meltem units from gateway on %s and found addresses: %s",
                    selected_port,
                    discovered_slaves,
                )
                if not discovered_slaves:
                    _LOGGER.warning(
                        "No supported Meltem M-WRG units found on gateway at %s",
                        selected_port,
                    )
                    errors["base"] = "no_devices_found"
                else:
                    (
                        self._preview_by_slave,
                        self._detected_profile_by_slave,
                        self._supported_entity_keys_by_slave,
                    ) = await _async_probe_discovered_slaves(
                        self.hass,
                        settings,
                        discovered_slaves,
                    )
                    self._port = normalized_port
                    self._discovered_slaves = discovered_slaves

                    await self.async_set_unique_id(normalized_port)
                    self._abort_if_unique_id_configured()

                    return await self.async_step_profiles()

        data_schema = vol.Schema(
            {
                vol.Required(CONF_PORT, default=self._port): str,
            }
        )

        return self.async_show_form(
            step_id="user",
            data_schema=data_schema,
            errors=errors,
        )

    async def async_step_usb(self, discovery_info: UsbServiceInfo) -> FlowResult:
        """Handle USB discovery for a Meltem gateway."""

        port = discovery_info.device
        serial_number = discovery_info.serial_number or port

        await self.async_set_unique_id(serial_number)
        self._abort_if_unique_id_configured(updates={CONF_PORT: port})

        self._port = port
        self._usb_title_placeholders = {
            "port": port,
            "manufacturer": discovery_info.manufacturer or "Unknown",
            "description": discovery_info.description or "Unknown USB device",
        }

        return await self.async_step_confirm_usb()

    async def async_step_confirm_usb(
        self, user_input: dict | None = None
    ) -> FlowResult:
        """Confirm a discovered USB device before scanning units."""

        if user_input is not None:
            self._port = str(user_input[CONF_PORT])
            return await self.async_step_scan()

        return self._show_confirm_usb_form()

    def _show_confirm_usb_form(
        self,
        *,
        errors: dict[str, str] | None = None,
    ) -> FlowResult:
        """Render the USB confirmation step."""

        data_schema = vol.Schema(
            {
                vol.Required(CONF_PORT, default=self._port): str,
            }
        )

        return self.async_show_form(
            step_id="confirm_usb",
            data_schema=data_schema,
            errors=errors,
            description_placeholders=self._usb_title_placeholders
            or {
                "port": self._port,
                "manufacturer": "Unknown",
                "description": "Unknown USB device",
            },
        )

    async def async_step_scan(self) -> FlowResult:
        """Scan the gateway for configured units."""

        settings = _build_serial_settings(self._port)

        try:
            discovered_slaves = await _async_scan_slaves(self.hass, settings)
        except MeltemModbusError:
            return self._show_confirm_usb_form(
                errors={"base": "cannot_connect"},
            )

        if not discovered_slaves:
            _LOGGER.info(
                "Read configured Meltem units from gateway on %s and found no configured addresses",
                self._port,
            )
            self._port = resolve_preferred_port_path(self._port)
            return self._show_confirm_usb_form(
                errors={"base": "no_devices_found"},
            )

        (
            self._preview_by_slave,
            self._detected_profile_by_slave,
            self._supported_entity_keys_by_slave,
        ) = await _async_probe_discovered_slaves(
            self.hass,
            settings,
            discovered_slaves,
        )
        self._discovered_slaves = discovered_slaves
        return await self.async_step_profiles()

    async def async_step_profiles(
        self, user_input: dict | None = None
    ) -> FlowResult:
        """Collect the profile for each detected unit."""

        if not self._discovered_slaves:
            return await self.async_step_user()

        profile_selector = _build_profile_selector()
        self._profile_fields_by_slave = {
            slave: _profile_label(
                index,
                slave,
                self._preview_by_slave,
            )
            for index, slave in enumerate(self._discovered_slaves, start=1)
        }
        data_schema = vol.Schema(
            {
                vol.Required(
                    self._profile_fields_by_slave[slave],
                    default=_detected_profile_default(
                        slave, self._detected_profile_by_slave
                    ),
                ): profile_selector
                for index, slave in enumerate(self._discovered_slaves, start=1)
            }
        )

        if user_input is not None:
            return self.async_create_entry(
                title=INTEGRATION_NAME,
                data={
                    CONF_PORT: resolve_preferred_port_path(self._port),
                    CONF_MAX_REQUESTS_PER_SECOND: self._max_requests_per_second,
                    CONF_ROOMS: _build_rooms_from_profiles(
                        self._discovered_slaves,
                        user_input,
                        self._preview_by_slave,
                        {
                            slave: {}
                            for slave in self._discovered_slaves
                        },
                        profile_fields_by_slave=self._profile_fields_by_slave,
                    ),
                },
            )

        return self.async_show_form(
            step_id="profiles",
            data_schema=data_schema,
            description_placeholders={
                "device_count": str(len(self._discovered_slaves)),
            },
        )


class MeltemVentilationOptionsFlow(config_entries.OptionsFlow):
    """Handle runtime options and gateway rescans.

    Rescans use the already running coordinator instead of opening a second
    serial connection. That keeps the gateway connection model identical during
    setup, runtime, and options changes.
    """

    def __init__(self, config_entry: ConfigEntry) -> None:
        self._config_entry = config_entry
        self._max_requests_per_second = float(
            config_entry.options.get(
                CONF_MAX_REQUESTS_PER_SECOND,
                config_entry.data.get(
                    CONF_MAX_REQUESTS_PER_SECOND,
                    DEFAULT_MAX_REQUESTS_PER_SECOND,
                ),
            )
        )
        self._discovered_slaves: list[int] = []
        self._preview_by_slave: dict[int, str] = {}
        self._detected_profile_by_slave: dict[int, str] = {}
        self._supported_entity_keys_by_slave: dict[int, list[str]] = {}
        self._profile_fields_by_slave: dict[int, str] = {}

    async def async_step_init(self, user_input: dict | None = None) -> FlowResult:
        """Choose which configuration action to perform."""

        return self.async_show_menu(
            step_id="init",
            menu_options=[
                "edit_connection",
                "edit_profiles",
                "rescan_units",
            ],
        )

    async def async_step_edit_connection(
        self, user_input: dict | None = None
    ) -> FlowResult:
        """Change serial connection settings used by the integration."""

        errors: dict[str, str] = {}
        current_port = str(self._config_entry.data.get(CONF_PORT, DEFAULT_PORT))
        current_request_rate = self._max_requests_per_second

        if user_input is not None:
            selected_port = str(user_input[CONF_PORT])
            normalized_port = resolve_preferred_port_path(selected_port)
            selected_request_rate = float(user_input[CONF_MAX_REQUESTS_PER_SECOND])
            current_normalized_port = current_port

            if normalized_port != current_normalized_port:
                settings = _build_serial_settings(selected_port)

                try:
                    await self.hass.async_add_executor_job(
                        validate_serial_connection,
                        settings,
                    )
                except MeltemModbusError:
                    errors["base"] = "cannot_connect"
                else:
                    self.hass.config_entries.async_update_entry(
                        self._config_entry,
                        data={
                            **self._config_entry.data,
                            CONF_PORT: normalized_port,
                        },
                    )

            if not errors:
                self.hass.config_entries.async_update_entry(
                    self._config_entry,
                    options={
                        **self._config_entry.options,
                        CONF_MAX_REQUESTS_PER_SECOND: selected_request_rate,
                    },
                )
                self._max_requests_per_second = selected_request_rate

                if normalized_port != current_normalized_port:
                    await self.hass.config_entries.async_reload(self._config_entry.entry_id)
                else:
                    runtime_data: MeltemRuntimeData = self._config_entry.runtime_data
                    runtime_data.coordinator.update_request_rate(selected_request_rate)

                return self.async_create_entry(
                    title="",
                    data=_build_options_result_data(
                        self._config_entry,
                        **{CONF_MAX_REQUESTS_PER_SECOND: self._max_requests_per_second},
                    ),
                )

            current_port = selected_port
            current_request_rate = selected_request_rate

        return self.async_show_form(
            step_id="edit_connection",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_PORT, default=current_port): str,
                    vol.Required(
                        CONF_MAX_REQUESTS_PER_SECOND,
                        default=current_request_rate,
                    ): _build_max_request_rate_selector(),
                }
            ),
            errors=errors,
        )

    async def async_step_edit_profiles(
        self, user_input: dict | None = None
    ) -> FlowResult:
        """Edit the profiles for already known units without rescanning."""

        runtime_data: MeltemRuntimeData = self._config_entry.runtime_data
        existing_rooms = {
            int(room["slave"]): room for room in self._config_entry.data[CONF_ROOMS]
        }
        slaves = sorted(existing_rooms)

        if not slaves:
            return await self.async_step_rescan_units()

        if user_input is None:
            self._preview_by_slave = {}
            self._detected_profile_by_slave = {}
            self._supported_entity_keys_by_slave = {}
            for slave in slaves:
                try:
                    detected_profile, preview, supported_entity_keys = (
                        await runtime_data.coordinator.async_probe_slave_details(slave)
                    )
                except MeltemModbusError:
                    preview = existing_rooms[slave].get("preview")
                    supported_entity_keys = supported_entity_keys_for_profile(
                        str(existing_rooms[slave].get("profile", "ii_plain"))
                    )
                    detected_profile = str(existing_rooms[slave].get("profile", "ii_plain"))
                self._detected_profile_by_slave[slave] = detected_profile
                if preview:
                    self._preview_by_slave[slave] = str(preview)
                self._supported_entity_keys_by_slave[slave] = list(supported_entity_keys)

        if user_input is not None:
            updated_data = {
                **self._config_entry.data,
                CONF_ROOMS: _build_rooms_from_profiles(
                    slaves,
                    user_input,
                    self._preview_by_slave,
                    {
                        slave: {
                            **existing_rooms[slave],
                        }
                        for slave in slaves
                    },
                    profile_fields_by_slave=self._profile_fields_by_slave,
                ),
            }
            self.hass.config_entries.async_update_entry(
                self._config_entry,
                data=updated_data,
            )
            await self.hass.config_entries.async_reload(self._config_entry.entry_id)
            return self.async_create_entry(
                title="",
                data=_build_options_result_data(
                    self._config_entry,
                    **{CONF_MAX_REQUESTS_PER_SECOND: self._max_requests_per_second},
                ),
            )

        profile_selector = _build_profile_selector()
        self._profile_fields_by_slave = {
            slave: _profile_label(
                index,
                slave,
                self._preview_by_slave,
            )
            for index, slave in enumerate(slaves, start=1)
        }
        return self.async_show_form(
            step_id="edit_profiles",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        self._profile_fields_by_slave[slave],
                        default=str(existing_rooms[slave]["profile"]),
                    ): profile_selector
                    for index, slave in enumerate(slaves, start=1)
                }
            ),
            description_placeholders={
                "device_count": str(len(slaves)),
            },
        )

    async def async_step_rescan_units(
        self, user_input: dict | None = None
    ) -> FlowResult:
        """Rescan the gateway for configured units and update the integration."""

        errors: dict[str, str] = {}

        if user_input is not None:
            try:
                runtime_data: MeltemRuntimeData = self._config_entry.runtime_data
                # Reuse the live coordinator/client so options changes do not
                # race a second serial connection against the running one.
                discovered_slaves = await runtime_data.coordinator.async_discover_gateway_units()
            except MeltemModbusError:
                errors["base"] = "cannot_connect"
            else:
                _LOGGER.info(
                    "Rescanned Meltem gateway on %s and found slaves: %s",
                    self._config_entry.data[CONF_PORT],
                    discovered_slaves,
                )
                if not discovered_slaves:
                    _LOGGER.warning(
                        "No supported Meltem M-WRG units found on gateway at %s during rescan",
                        self._config_entry.data[CONF_PORT],
                    )
                    errors["base"] = "no_devices_found"
                else:
                    self._preview_by_slave = {}
                    self._detected_profile_by_slave = {}
                    self._supported_entity_keys_by_slave = {}
                    for slave in discovered_slaves:
                        try:
                            detected_profile, preview, supported_entity_keys = (
                                await runtime_data.coordinator.async_probe_slave_details(slave)
                            )
                        except MeltemModbusError as err:
                            _LOGGER.warning(
                                "Options rescan probe failed for Meltem unit at slave %s: %s",
                                slave,
                                err,
                            )
                            detected_profile = "plain"
                            preview = None
                            supported_entity_keys = supported_entity_keys_for_profile(
                                "ii_plain"
                            )
                        self._detected_profile_by_slave[slave] = detected_profile
                        if preview:
                            self._preview_by_slave[slave] = preview
                        self._supported_entity_keys_by_slave[slave] = supported_entity_keys
                    self._discovered_slaves = discovered_slaves
                    return await self.async_step_profiles()

        return self.async_show_form(
            step_id="rescan_units",
            data_schema=vol.Schema({}),
            errors=errors,
        )

    async def async_step_profiles(
        self, user_input: dict | None = None
    ) -> FlowResult:
        """Update profiles after a rescan."""

        if not self._discovered_slaves:
            return await self.async_step_init()

        existing_rooms = {
            int(room["slave"]): room for room in self._config_entry.data[CONF_ROOMS]
        }
        profile_selector = _build_profile_selector()
        self._profile_fields_by_slave = {
            slave: _profile_label(
                index,
                slave,
                self._preview_by_slave,
            )
            for index, slave in enumerate(self._discovered_slaves, start=1)
        }
        data_schema = vol.Schema(
            {
                vol.Required(
                    self._profile_fields_by_slave[slave],
                    default=str(
                        existing_rooms.get(slave, {}).get(
                            "profile",
                            _detected_profile_default(
                                slave, self._detected_profile_by_slave
                            ),
                        )
                    ),
                ): profile_selector
                for index, slave in enumerate(self._discovered_slaves, start=1)
            }
        )

        if user_input is not None:
            updated_data = {
                **self._config_entry.data,
                CONF_PORT: resolve_preferred_port_path(self._config_entry.data[CONF_PORT]),
                CONF_ROOMS: _build_rooms_from_profiles(
                    self._discovered_slaves,
                    user_input,
                    self._preview_by_slave,
                    {
                        slave: {
                            **existing_rooms.get(slave, {}),
                        }
                        for slave in self._discovered_slaves
                    },
                    profile_fields_by_slave=self._profile_fields_by_slave,
                ),
            }
            self.hass.config_entries.async_update_entry(
                self._config_entry,
                data=updated_data,
                options={
                    **self._config_entry.options,
                    CONF_MAX_REQUESTS_PER_SECOND: self._max_requests_per_second,
                },
            )
            await self.hass.config_entries.async_reload(self._config_entry.entry_id)
            return self.async_create_entry(
                title="",
                data=_build_options_result_data(
                    self._config_entry,
                    **{CONF_MAX_REQUESTS_PER_SECOND: self._max_requests_per_second},
                ),
            )

        return self.async_show_form(
            step_id="profiles",
            data_schema=data_schema,
            description_placeholders={
                "device_count": str(len(self._discovered_slaves)),
            },
        )
