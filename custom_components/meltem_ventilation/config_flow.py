"""Config flow for Meltem gateways.

The flow uses the gateway bridge registers to discover configured units first,
then performs a small per-unit probe to preselect the most likely profile.
The heavier runtime reads happen only after the config entry is created.
"""

from __future__ import annotations

import logging
from functools import partial
from typing import Any

from homeassistant import config_entries
from homeassistant.config_entries import (
    ConfigEntryState,
    ConfigFlowResult,
    ConfigSubentryFlow,
)
from homeassistant.core import callback
from homeassistant.helpers.service_info.usb import UsbServiceInfo

from .const import (
    CONF_CONNECTION_TYPE,
    CONF_HOST,
    CONF_NETWORK_PORT,
    CONF_PORT,
    CONNECTION_RTU_OVER_TCP,
    DEFAULT_PORT,
    DOMAIN,
    GATEWAY_NAME,
    SUBENTRY_TYPE_UNIT,
)
from .flow_helpers import (
    PROFILE_MISMATCH_SCHEMA,
    async_discover_units,
    async_resolve_port,
    async_validate_port,
    build_rooms_from_profiles,
    connection_error,
    connection_schema,
    port_schema,
    probe_profiles,
    profile_defaults,
    profile_field_key,
    profile_mismatches,
    profiles_form,
)
from .gateway_connection import (
    GatewayConnection,
    RtuTcpGatewayConnection,
    SerialGatewayConnection,
    gateway_connection_from_data,
)
from .models import MeltemIntegrationConfigEntry, UnitProbeResult
from .options_flow import MeltemVentilationOptionsFlow
from .subentry_flow import MeltemUnitSubentryFlow
from .units import unit_subentry_data

_LOGGER = logging.getLogger(__name__)


class MeltemVentilationConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Meltem Modbus."""

    VERSION = 2
    MINOR_VERSION = 2

    def __init__(self) -> None:
        self._port = DEFAULT_PORT
        self._connection: GatewayConnection = SerialGatewayConnection(DEFAULT_PORT)
        self._discovered_slaves: list[int] = []
        self._probe_results_by_slave: dict[int, UnitProbeResult] = {}
        self._detected_profile_by_slave: dict[int, str] = {}
        self._selected_profiles: dict[str, Any] = {}
        self._profile_mismatches = ""
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
        """Collect the connection and scan for connected units."""

        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                connection = await self._async_connection(user_input)
            except ValueError:
                errors["base"] = "invalid_connection"
            else:
                self._connection = connection
                await self.async_set_unique_id(connection.unique_id)
                self._abort_if_unique_id_configured()
                endpoint = (
                    connection.port
                    if isinstance(connection, SerialGatewayConnection)
                    else connection
                )
                if (error := await self._async_scan(endpoint)) is None:
                    return await self.async_step_profiles()
                errors["base"] = error

        return self.async_show_form(
            step_id="user",
            data_schema=connection_schema(self._connection, user_input if errors else None),
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

    async def _async_connection(self, data: dict[str, Any]) -> GatewayConnection:
        """Validate network input or resolve a stable serial device path."""

        if data.get(CONF_CONNECTION_TYPE) == CONNECTION_RTU_OVER_TCP:
            return RtuTcpGatewayConnection(data.get(CONF_HOST, ""), data[CONF_NETWORK_PORT])
        port = data.get(CONF_PORT, "").strip()
        if not port:
            raise ValueError("Serial device path is required")
        return SerialGatewayConnection(await async_resolve_port(self.hass, port))

    async def _async_scan(self, port: str | RtuTcpGatewayConnection) -> str | None:
        """Scan the gateway and keep its units; return the form error on failure."""

        try:
            slaves, probe_results = await async_discover_units(self.hass, port)
        except Exception as err:
            return connection_error(
                err, "scanning", port if isinstance(port, str) else port.unique_id
            )

        _LOGGER.info(
            "Read configured Meltem units from gateway on %s and found addresses: %s",
            port,
            slaves,
        )
        if not slaves:
            _LOGGER.warning("No supported Meltem M-WRG units found on gateway at %s", port)
            return "no_devices_found"

        self._connection = SerialGatewayConnection(port) if isinstance(port, str) else port
        if isinstance(port, str):
            self._port = port
        self._discovered_slaves = slaves
        self._probe_results_by_slave = probe_results
        self._detected_profile_by_slave = probe_profiles(probe_results)
        return None

    async def async_step_profiles(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Collect the profile for each detected unit."""

        if not self._discovered_slaves:
            return await self.async_step_user()

        if user_input is not None:
            self._selected_profiles = dict(user_input)
            self._profile_mismatches = profile_mismatches(
                {
                    slave: str(user_input[profile_field_key(slave)])
                    for slave in self._discovered_slaves
                },
                self._probe_results_by_slave,
            )
            if self._profile_mismatches:
                return await self.async_step_confirm_profile_mismatch()
            return self._create_profiles()

        defaults = profile_defaults(self._discovered_slaves, self._detected_profile_by_slave)
        defaults.update(
            {
                slave: str(self._selected_profiles[profile_field_key(slave)])
                for slave in self._discovered_slaves
                if profile_field_key(slave) in self._selected_profiles
            }
        )
        data_schema, placeholders = profiles_form(
            self._discovered_slaves,
            defaults,
            self._probe_results_by_slave,
        )
        return self.async_show_form(
            step_id="profiles",
            data_schema=data_schema,
            description_placeholders=placeholders,
        )

    def _create_profiles(self) -> ConfigFlowResult:
        rooms = build_rooms_from_profiles(
            self._discovered_slaves, self._selected_profiles, self._probe_results_by_slave
        )
        return self.async_create_entry(
            title=GATEWAY_NAME,
            data=self._connection.as_data(),
            subentries=[unit_subentry_data(room) for room in rooms],
        )

    async def async_step_confirm_profile_mismatch(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Allow the user to correct or explicitly confirm mismatched profiles."""

        if user_input is not None:
            if user_input["action"] == "correct":
                return await self.async_step_profiles()
            return self._create_profiles()
        return self.async_show_form(
            step_id="confirm_profile_mismatch",
            data_schema=PROFILE_MISMATCH_SCHEMA,
            description_placeholders={"mismatches": self._profile_mismatches},
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Change the gateway connection without replacing the entry."""

        entry = self._get_reconfigure_entry()
        errors: dict[str, str] = {}
        current = gateway_connection_from_data(entry.data)

        if user_input is not None:
            try:
                connection = await self._async_connection(user_input)
            except ValueError:
                errors["base"] = "invalid_connection"
            else:
                if isinstance(current, SerialGatewayConnection):
                    current = SerialGatewayConnection(
                        await async_resolve_port(self.hass, current.port)
                    )
                existing = self.hass.config_entries.async_entry_for_domain_unique_id(
                    DOMAIN, connection.unique_id
                )
                if existing is not None and existing.entry_id != entry.entry_id:
                    return self.async_abort(reason="already_configured")
                if connection != current:
                    endpoint = (
                        connection.port
                        if isinstance(connection, SerialGatewayConnection)
                        else connection
                    )
                    try:
                        runtime = getattr(entry, "runtime_data", None)
                        if (
                            isinstance(current, RtuTcpGatewayConnection)
                            and isinstance(connection, RtuTcpGatewayConnection)
                            and entry.state is ConfigEntryState.LOADED
                            and runtime is not None
                        ):
                            await runtime.coordinator.async_validate_replacement(
                                partial(async_validate_port, self.hass, endpoint)
                            )
                        else:
                            await async_validate_port(self.hass, endpoint)
                    except Exception as err:
                        errors["base"] = connection_error(err, "opening", connection.unique_id)
                if not errors:
                    data = {
                        key: value
                        for key, value in entry.data.items()
                        if key
                        not in {CONF_PORT, CONF_CONNECTION_TYPE, CONF_HOST, CONF_NETWORK_PORT}
                    } | connection.as_data()
                    if entry.update_listeners:
                        return self.async_update_and_abort(
                            entry, unique_id=connection.unique_id, data=data
                        )
                    return self.async_update_reload_and_abort(
                        entry,
                        unique_id=connection.unique_id,
                        data=data,
                        reload_even_if_entry_is_unchanged=False,
                    )
                current = connection

        return self.async_show_form(
            step_id="reconfigure",
            data_schema=connection_schema(current, user_input if errors else None),
            errors=errors,
        )
