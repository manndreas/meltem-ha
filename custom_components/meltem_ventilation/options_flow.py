"""Options flow for Meltem gateways: request rate and unit rescans."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from types import MappingProxyType
from typing import Any

import probatio as vol
from homeassistant import config_entries
from homeassistant.config_entries import (
    ConfigFlowResult,
    ConfigSubentry,
)

from .const import (
    CONF_MAX_REQUESTS_PER_SECOND,
    CONF_PORT,
    CONF_SLAVE,
    CONF_UNIT_KEY,
    DEFAULT_MAX_REQUESTS_PER_SECOND,
)
from .coordinator import MeltemDataUpdateCoordinator
from .flow_helpers import (
    MAX_REQUEST_RATE_SELECTOR,
    async_probe_units,
    async_resolve_port,
    build_rooms_from_profiles,
    device_names_by_slave,
    match_existing_rooms,
    probe_previews,
    probe_profiles,
    profile_defaults,
    profiles_form,
    unit_details,
)
from .gateway_connection import SerialGatewayConnection, gateway_connection_from_data
from .modbus_helpers import MeltemModbusError
from .models import MeltemRuntimeData, UnitProbeResult
from .units import unit_data, unit_rooms, unit_subentries, unit_subentry_data

_LOGGER = logging.getLogger(__name__)


class MeltemVentilationOptionsFlow(config_entries.OptionsFlow):
    """Handle runtime options and gateway rescans.

    Rescans use the already running coordinator instead of opening a second
    serial connection. That keeps the gateway connection model identical during
    setup, runtime, and options changes.
    """

    def __init__(self) -> None:
        self._discovered_slaves: list[int] = []
        self._preview_by_slave: dict[int, str] = {}
        self._probe_results_by_slave: dict[int, UnitProbeResult] = {}
        self._detected_profile_by_slave: dict[int, str] = {}
        self._selected_profiles: dict[str, Any] = {}

    @property
    def _max_requests_per_second(self) -> float:
        """Return the currently stored scheduler request rate."""

        return float(
            self.config_entry.options.get(
                CONF_MAX_REQUESTS_PER_SECOND, DEFAULT_MAX_REQUESTS_PER_SECOND
            )
        )

    def _options_payload(self, request_rate: float | None = None) -> dict[str, object]:
        """Return the persisted options payload for finishing an options flow."""

        return {
            **self.config_entry.options,
            CONF_MAX_REQUESTS_PER_SECOND: (
                self._max_requests_per_second if request_rate is None else request_rate
            ),
        }

    @property
    def _existing_rooms(self) -> dict[int, dict[str, Any]]:
        return unit_rooms(self.config_entry)

    @property
    def _coordinator(self) -> MeltemDataUpdateCoordinator | None:
        """Return the running coordinator, or ``None`` if setup never completed.

        Home Assistant offers the options flow regardless of entry state, and
        deletes ``runtime_data`` whenever the entry is not loaded.
        """

        runtime_data: MeltemRuntimeData | None = getattr(self.config_entry, "runtime_data", None)
        return runtime_data.coordinator if runtime_data is not None else None

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Choose which configuration action to perform."""

        return self.async_show_menu(
            step_id="init",
            menu_options=[
                "edit_request_rate",
                "rescan_units",
            ],
        )

    async def async_step_edit_request_rate(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Change the maximum poll-job start rate."""

        if user_input is not None:
            request_rate = float(user_input[CONF_MAX_REQUESTS_PER_SECOND])
            options = self._options_payload(request_rate)
            if (coordinator := self._coordinator) is not None:
                coordinator.update_request_rate(request_rate)
            else:
                # An entry that never finished setup has no scheduler to retune;
                # its setup reads the rate, so store it before reloading.
                self.hass.config_entries.async_update_entry(self.config_entry, options=options)
                await self.hass.config_entries.async_reload(self.config_entry.entry_id)
            return self.async_create_entry(title="", data=options)

        return self.async_show_form(
            step_id="edit_request_rate",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_MAX_REQUESTS_PER_SECOND,
                        default=self._max_requests_per_second,
                    ): MAX_REQUEST_RATE_SELECTOR,
                }
            ),
        )

    def _show_profiles_form(
        self,
        step_id: str,
        slaves: list[int],
        defaults_by_slave: Mapping[int, str],
        existing_rooms: Mapping[int, Mapping[str, Any]],
    ) -> ConfigFlowResult:
        data_schema, placeholders = profiles_form(
            slaves,
            defaults_by_slave,
            self._preview_by_slave,
            device_names_by_slave(
                self.hass,
                self.config_entry.entry_id,
                existing_rooms.values(),
            ),
        )
        return self.async_show_form(
            step_id=step_id,
            data_schema=data_schema,
            description_placeholders=placeholders,
        )

    async def _async_save_profiles(
        self,
        slaves: list[int],
        user_input: Mapping[str, Any],
        existing_rooms: Mapping[int, Mapping[str, Any]],
        *,
        resolve_port: bool = False,
    ) -> ConfigFlowResult:
        """Store the rescanned units as subentries; the update listener reloads the entry.

        A unit the gateway no longer reports loses its subentry, and with it
        its device and entities.
        """

        entry = self.config_entry
        rooms = build_rooms_from_profiles(
            slaves, user_input, self._probe_results_by_slave, existing_rooms
        )
        subentries = {
            str(subentry.data[CONF_UNIT_KEY]): subentry for subentry in unit_subentries(entry)
        }
        kept = [(subentries.pop(str(room[CONF_UNIT_KEY]), None), room) for room in rooms]
        for stale in subentries.values():
            self.hass.config_entries.async_remove_subentry(entry, stale.subentry_id)
        for existing, room in kept:
            if existing is not None and existing.unique_id != str(room[CONF_SLAVE]):
                self.hass.config_entries.async_update_subentry(entry, existing, unique_id=None)
        for existing, room in kept:
            if existing is None:
                new = unit_subentry_data(room)
                self.hass.config_entries.async_add_subentry(
                    entry,
                    ConfigSubentry(
                        data=MappingProxyType(new["data"]),
                        subentry_type=new["subentry_type"],
                        title=new["title"],
                        unique_id=new["unique_id"],
                    ),
                )
            else:
                self.hass.config_entries.async_update_subentry(
                    entry,
                    existing,
                    data=unit_data(room),
                    unique_id=str(room[CONF_SLAVE]),
                )
        if resolve_port and isinstance(
            gateway_connection_from_data(entry.data), SerialGatewayConnection
        ):
            port = await async_resolve_port(self.hass, entry.data[CONF_PORT])
            self.hass.config_entries.async_update_entry(entry, data={**entry.data, CONF_PORT: port})
        return self.async_create_entry(title="", data=self._options_payload())

    async def async_step_rescan_units(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Rescan the gateway for configured units and update the integration."""

        errors: dict[str, str] = {}
        if user_input is not None:
            if (error := await self._async_rescan()) is None:
                return await self.async_step_profiles()
            errors["base"] = error

        return self.async_show_form(
            step_id="rescan_units",
            data_schema=vol.Schema({}),
            errors=errors,
        )

    async def _async_rescan(self) -> str | None:
        """Rescan and keep the units; return the form error on failure."""

        # Reuse the live client so a rescan does not race a second serial connection.
        if (coordinator := self._coordinator) is None:
            return "cannot_connect"
        port = gateway_connection_from_data(self.config_entry.data).unique_id
        try:
            slaves = await coordinator.async_discover_gateway_units()
        except MeltemModbusError:
            return "cannot_connect"

        _LOGGER.info("Rescanned Meltem gateway on %s and found slaves: %s", port, slaves)
        if not slaves:
            _LOGGER.warning(
                "No supported Meltem M-WRG units found on gateway at %s during rescan",
                port,
            )
            return "no_devices_found"

        self._probe_results_by_slave = await async_probe_units(
            slaves, coordinator.async_probe_slave_details
        )
        self._preview_by_slave = probe_previews(self._probe_results_by_slave)
        self._detected_profile_by_slave = probe_profiles(self._probe_results_by_slave)
        self._discovered_slaves = slaves
        return None

    async def async_step_profiles(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Update profiles after a rescan."""

        if not self._discovered_slaves:
            return await self.async_step_init()

        existing_rooms = match_existing_rooms(
            self._discovered_slaves,
            self._probe_results_by_slave,
            self._existing_rooms,
        )
        if user_input is not None:
            self._selected_profiles = dict(user_input)
            if self._stale_subentries(user_input, existing_rooms):
                return await self.async_step_confirm_remove()
            return await self._async_save_profiles(
                self._discovered_slaves,
                user_input,
                existing_rooms,
                resolve_port=True,
            )

        return self._show_profiles_form(
            "profiles",
            self._discovered_slaves,
            profile_defaults(
                self._discovered_slaves,
                self._detected_profile_by_slave,
                existing_rooms,
            ),
            existing_rooms,
        )

    async def async_step_confirm_remove(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Name the units the rescan no longer found before removing them."""

        existing_rooms = match_existing_rooms(
            self._discovered_slaves,
            self._probe_results_by_slave,
            self._existing_rooms,
        )
        if user_input is not None:
            return await self._async_save_profiles(
                self._discovered_slaves,
                self._selected_profiles,
                existing_rooms,
                resolve_port=True,
            )

        stale = self._stale_subentries(self._selected_profiles, existing_rooms)
        slaves = [int(subentry.data[CONF_SLAVE]) for subentry in stale]
        names = device_names_by_slave(
            self.hass, self.config_entry.entry_id, self._existing_rooms.values()
        )
        return self.async_show_form(
            step_id="confirm_remove",
            data_schema=vol.Schema({}),
            description_placeholders={
                "removed_count": str(len(stale)),
                "removed_units": unit_details(
                    slaves,
                    {
                        int(subentry.data[CONF_SLAVE]): str(subentry.data["preview"])
                        for subentry in stale
                        if subentry.data.get("preview")
                    },
                    {
                        int(subentry.data[CONF_SLAVE]): names.get(
                            int(subentry.data[CONF_SLAVE]), subentry.title
                        )
                        for subentry in stale
                    },
                ),
            },
        )

    def _stale_subentries(
        self,
        selected_profiles: Mapping[str, Any],
        existing_rooms: Mapping[int, Mapping[str, Any]],
    ) -> list[ConfigSubentry]:
        """Return the unit subentries that saving the rescan would remove."""

        rooms = build_rooms_from_profiles(
            self._discovered_slaves,
            selected_profiles,
            self._probe_results_by_slave,
            existing_rooms,
        )
        kept_keys = {str(room[CONF_UNIT_KEY]) for room in rooms}
        return [
            subentry
            for subentry in unit_subentries(self.config_entry)
            if str(subentry.data[CONF_UNIT_KEY]) not in kept_keys
        ]
