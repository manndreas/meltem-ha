"""Config flow for Meltem gateways.

The flow uses the gateway bridge registers to discover configured units first,
then performs a small per-unit probe to preselect the most likely profile.
The heavier runtime reads happen only after the config entry is created.
"""

from __future__ import annotations

import logging
from typing import Any

from homeassistant import config_entries
from homeassistant.config_entries import (
    ConfigFlowResult,
    ConfigSubentryFlow,
)
from homeassistant.core import callback
from homeassistant.helpers.service_info.usb import UsbServiceInfo

from .const import (
    CONF_PORT,
    DEFAULT_PORT,
    DOMAIN,
    GATEWAY_NAME,
    SUBENTRY_TYPE_UNIT,
)
from .flow_helpers import (
    async_discover_units,
    async_resolve_port,
    async_validate_port,
    build_rooms_from_profiles,
    connection_error,
    port_schema,
    probe_previews,
    probe_profiles,
    profile_defaults,
    profiles_form,
)
from .models import MeltemIntegrationConfigEntry, UnitProbeResult
from .options_flow import MeltemVentilationOptionsFlow
from .subentry_flow import MeltemUnitSubentryFlow
from .units import unit_subentry_data

_LOGGER = logging.getLogger(__name__)


class MeltemVentilationConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Meltem Modbus."""

    VERSION = 2
    MINOR_VERSION = 1

    def __init__(self) -> None:
        self._port = DEFAULT_PORT
        self._discovered_slaves: list[int] = []
        self._preview_by_slave: dict[int, str] = {}
        self._probe_results_by_slave: dict[int, UnitProbeResult] = {}
        self._detected_profile_by_slave: dict[int, str] = {}
        self._usb_title_placeholders: dict[str, str] | None = None

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: MeltemIntegrationConfigEntry,
    ) -> MeltemVentilationOptionsFlow:
        """Return the options flow handler."""

        return MeltemVentilationOptionsFlow()

    @classmethod
    @callback
    def async_get_supported_subentry_types(
        cls, config_entry: MeltemIntegrationConfigEntry
    ) -> dict[str, type[ConfigSubentryFlow]]:
        """Return the subentry types; every unit is one subentry."""

        return {SUBENTRY_TYPE_UNIT: MeltemUnitSubentryFlow}

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Collect the serial port and scan for connected units."""

        errors: dict[str, str] = {}
        if user_input is not None:
            port = await async_resolve_port(self.hass, user_input[CONF_PORT])
            # Abort before the slow scan when this gateway is already set up.
            await self.async_set_unique_id(port)
            self._abort_if_unique_id_configured()
            if (error := await self._async_scan(port)) is None:
                return await self.async_step_profiles()
            errors["base"] = error
            # Offer the entered port again, not the default.
            self._port = str(user_input[CONF_PORT])

        return self.async_show_form(
            step_id="user",
            data_schema=port_schema(self._port),
            errors=errors,
        )

    async def async_step_usb(self, discovery_info: UsbServiceInfo) -> ConfigFlowResult:
        """Handle USB discovery for a Meltem gateway."""

        port = discovery_info.device
        normalized_port = await async_resolve_port(self.hass, port)

        # Same unique ID scheme as the manual step so both paths deduplicate.
        await self.async_set_unique_id(normalized_port)
        self._abort_if_unique_id_configured(updates={CONF_PORT: normalized_port})

        self._port = normalized_port
        self._usb_title_placeholders = {
            "port": normalized_port,
            "manufacturer": discovery_info.manufacturer or "Unknown",
            "description": discovery_info.description or "Unknown USB device",
        }

        return await self.async_step_confirm_usb()

    async def async_step_confirm_usb(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Confirm a discovered USB device before scanning units."""

        if user_input is not None:
            self._port = str(user_input[CONF_PORT])
            return await self.async_step_scan()

        return self._show_confirm_usb_form()

    def _show_confirm_usb_form(
        self,
        *,
        errors: dict[str, str] | None = None,
    ) -> ConfigFlowResult:
        """Render the USB confirmation step."""

        return self.async_show_form(
            step_id="confirm_usb",
            data_schema=port_schema(self._port),
            errors=errors,
            description_placeholders=self._usb_title_placeholders
            or {
                "port": self._port,
                "manufacturer": "Unknown",
                "description": "Unknown USB device",
            },
        )

    async def async_step_scan(self) -> ConfigFlowResult:
        """Scan the gateway for configured units."""

        self._port = await async_resolve_port(self.hass, self._port)
        # The port may have been edited after USB discovery set the unique ID.
        await self.async_set_unique_id(self._port)
        self._abort_if_unique_id_configured()
        if (error := await self._async_scan(self._port)) is not None:
            return self._show_confirm_usb_form(errors={"base": error})
        return await self.async_step_profiles()

    async def _async_scan(self, port: str) -> str | None:
        """Scan the gateway and keep its units; return the form error on failure."""

        try:
            slaves, probe_results = await async_discover_units(self.hass, port)
        except Exception as err:
            return connection_error(err, "scanning", port)

        _LOGGER.info(
            "Read configured Meltem units from gateway on %s and found addresses: %s",
            port,
            slaves,
        )
        if not slaves:
            _LOGGER.warning("No supported Meltem M-WRG units found on gateway at %s", port)
            return "no_devices_found"

        self._port = port
        self._discovered_slaves = slaves
        self._probe_results_by_slave = probe_results
        self._preview_by_slave = probe_previews(probe_results)
        self._detected_profile_by_slave = probe_profiles(probe_results)
        return None

    async def async_step_profiles(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Collect the profile for each detected unit."""

        if not self._discovered_slaves:
            return await self.async_step_user()

        if user_input is not None:
            rooms = build_rooms_from_profiles(
                self._discovered_slaves,
                user_input,
                self._probe_results_by_slave,
            )
            return self.async_create_entry(
                title=GATEWAY_NAME,
                data={CONF_PORT: self._port},
                subentries=[unit_subentry_data(room) for room in rooms],
            )

        data_schema, placeholders = profiles_form(
            self._discovered_slaves,
            profile_defaults(self._discovered_slaves, self._detected_profile_by_slave),
            self._preview_by_slave,
        )
        return self.async_show_form(
            step_id="profiles",
            data_schema=data_schema,
            description_placeholders=placeholders,
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Change the serial port of the gateway."""

        entry = self._get_reconfigure_entry()
        errors: dict[str, str] = {}
        current_port = str(entry.data[CONF_PORT])

        if user_input is not None:
            port = await async_resolve_port(self.hass, user_input[CONF_PORT])
            # The stored path may predate a /dev/serial/by-id symlink, so it has
            # to be normalized too before deciding that the port changed.
            if port != await async_resolve_port(self.hass, current_port):
                try:
                    await async_validate_port(self.hass, port)
                except Exception as err:
                    errors["base"] = connection_error(err, "opening", port)
            if not errors:
                if entry.update_listeners:
                    # The loaded entry's update listener reloads it on a new port.
                    return self.async_update_and_abort(
                        entry, unique_id=port, data_updates={CONF_PORT: port}
                    )
                return self.async_update_reload_and_abort(
                    entry,
                    unique_id=port,
                    data_updates={CONF_PORT: port},
                    reload_even_if_entry_is_unchanged=False,
                )
            current_port = str(user_input[CONF_PORT])

        return self.async_show_form(
            step_id="reconfigure",
            data_schema=port_schema(current_port),
            errors=errors,
        )
