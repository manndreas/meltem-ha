"""Subentry flow that adds one unit to a gateway or changes its profile."""

from __future__ import annotations

import logging
from typing import Any, Literal

import probatio as vol
from homeassistant import data_entry_flow
from homeassistant.config_entries import (
    ConfigEntryState,
    ConfigSubentryFlow,
    SubentryFlowResult,
)
from homeassistant.helpers import selector

from .const import (
    CONF_PROFILE,
    CONF_SLAVE,
)
from .coordinator import MeltemDataUpdateCoordinator
from .flow_helpers import (
    PROFILE_MISMATCH_SCHEMA,
    PROFILE_SELECTOR,
    async_probe_units,
    detected_profile_default,
    device_names_by_slave,
    new_unit,
    probe_profiles,
    profile_field_key,
    profile_mismatches,
    stored_unit_probe,
    unit_detail,
    unit_detail_placeholders,
    unit_details,
)
from .modbus_helpers import MeltemModbusError
from .models import MeltemRuntimeData, UnitProbeResult
from .units import unit_data, unit_rooms

_LOGGER = logging.getLogger(__name__)


class MeltemUnitSubentryFlow(ConfigSubentryFlow):
    """Add a unit the gateway reports, or change the profile of one unit."""

    def __init__(self) -> None:
        self._slaves: list[int] = []
        self._slave = 0
        self._probe_results_by_slave: dict[int, UnitProbeResult] = {}
        self._selected_profile: str | None = None
        self._profile_mismatches = ""
        self._profile_source: Literal["user", "profile", "reconfigure"] | None = None

    def _coordinator(self) -> MeltemDataUpdateCoordinator | None:
        """Return the running coordinator; scans go through its gateway link."""

        entry = self._get_entry()
        if entry.state is not ConfigEntryState.LOADED:
            return None
        runtime_data: MeltemRuntimeData | None = getattr(entry, "runtime_data", None)
        return runtime_data.coordinator if runtime_data is not None else None

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        """Choose an unconfigured unit and its profile."""

        if user_input is not None:
            slave = int(user_input[CONF_SLAVE])
            return await self._async_select_profile(
                slave, str(user_input[profile_field_key(slave)]), "user"
            )

        if (coordinator := self._coordinator()) is None:
            return self.async_abort(reason="entry_not_loaded")
        try:
            slaves = await coordinator.async_discover_gateway_units()
        except MeltemModbusError:
            return self.async_abort(reason="cannot_connect")
        configured = set(unit_rooms(self._get_entry()))
        self._slaves = [slave for slave in slaves if slave not in configured]
        if not self._slaves:
            return self.async_abort(reason="no_new_units")
        self._probe_results_by_slave = await async_probe_units(
            self._slaves, coordinator.async_probe_slave_details
        )
        if len(self._slaves) == 1:
            self._slave = self._slaves[0]
            return await self.async_step_profile()
        return self._show_user_form()

    def _show_user_form(self) -> SubentryFlowResult:
        detected_profiles = probe_profiles(self._probe_results_by_slave)
        slave_field = (
            vol.Required(CONF_SLAVE, default=str(self._slave))
            if self._slave
            else vol.Required(CONF_SLAVE)
        )
        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    slave_field: selector.SelectSelector(
                        selector.SelectSelectorConfig(
                            options=[str(slave) for slave in self._slaves],
                            mode=selector.SelectSelectorMode.LIST,
                        )
                    ),
                    **{
                        data_entry_flow.Required(
                            profile_field_key(slave),
                            default=(
                                self._selected_profile
                                if slave == self._slave and self._selected_profile is not None
                                else detected_profile_default(slave, detected_profiles)
                            ),
                            visible={"field": CONF_SLAVE, "value": str(slave)},
                        ): PROFILE_SELECTOR
                        for slave in self._slaves
                    },
                }
            ),
            description_placeholders={
                "device_count": str(len(self._slaves)),
                "unit_details": unit_details(self._slaves, self._probe_results_by_slave),
                **unit_detail_placeholders(self._slaves, self._probe_results_by_slave),
            },
        )

    async def async_step_profile(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Choose the profile of the unit to add."""

        slave = self._slave
        if user_input is not None:
            return await self._async_select_profile(slave, str(user_input[CONF_PROFILE]), "profile")

        return self.async_show_form(
            step_id="profile",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_PROFILE,
                        default=self._selected_profile
                        or detected_profile_default(
                            slave, probe_profiles(self._probe_results_by_slave)
                        ),
                    ): PROFILE_SELECTOR
                }
            ),
            description_placeholders={
                "slave": str(slave),
                "unit_details": unit_detail(self._probe_results_by_slave.get(slave)),
            },
        )

    async def _async_select_profile(
        self, slave: int, profile: str, source: Literal["user", "profile", "reconfigure"]
    ) -> SubentryFlowResult:
        self._slave = slave
        self._selected_profile = profile
        self._profile_source = source
        probes = self._probe_results_by_slave
        if source == "reconfigure":
            probes = {slave: stored_unit_probe(self._get_reconfigure_subentry().data)}
        self._profile_mismatches = profile_mismatches({slave: profile}, probes)
        if self._profile_mismatches:
            return await self.async_step_confirm_profile_mismatch()
        return self._finish_profile()

    def _finish_profile(self) -> SubentryFlowResult:
        if self._selected_profile is None or self._profile_source is None:
            raise RuntimeError("No profile selected")
        if self._profile_source == "reconfigure":
            entry = self._get_entry()
            subentry = self._get_reconfigure_subentry()
            data_updates = {CONF_PROFILE: self._selected_profile}
            if entry.update_listeners:
                return self.async_update_and_abort(entry, subentry, data_updates=data_updates)
            return self.async_update_reload_and_abort(
                entry,
                subentry,
                data_updates=data_updates,
                reload_even_if_entry_is_unchanged=False,
            )
        return self._create_unit(self._slave, self._selected_profile)

    async def async_step_confirm_profile_mismatch(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Allow the user to correct or explicitly confirm a mismatched profile."""

        if user_input is not None:
            if user_input["action"] == "correct":
                if self._profile_source == "user":
                    return self._show_user_form()
                if self._profile_source == "profile":
                    return await self.async_step_profile()
                if self._profile_source == "reconfigure":
                    return await self.async_step_reconfigure()
                raise RuntimeError("No profile selection to correct")
            return self._finish_profile()
        return self.async_show_form(
            step_id="confirm_profile_mismatch",
            data_schema=PROFILE_MISMATCH_SCHEMA,
            description_placeholders={"mismatches": self._profile_mismatches},
        )

    def _create_unit(self, slave: int, profile: str) -> SubentryFlowResult:
        """Create the selected unit using its probed details."""

        existing_rooms = unit_rooms(self._get_entry())
        if slave in existing_rooms:
            return self.async_abort(reason="already_configured")
        room = new_unit(
            slave,
            profile,
            self._probe_results_by_slave.get(slave),
            existing_rooms.values(),
        )
        return self.async_create_entry(
            title=str(room["name"]), data=unit_data(room), unique_id=str(slave)
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Change the profile of one unit."""

        entry = self._get_entry()
        subentry = self._get_reconfigure_subentry()
        slave = int(subentry.data[CONF_SLAVE])
        if user_input is not None:
            return await self._async_select_profile(
                slave, str(user_input[CONF_PROFILE]), "reconfigure"
            )

        names = device_names_by_slave(self.hass, entry.entry_id, unit_rooms(entry).values())
        return self.async_show_form(
            step_id="reconfigure",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_PROFILE,
                        default=self._selected_profile or str(subentry.data[CONF_PROFILE]),
                    ): PROFILE_SELECTOR
                }
            ),
            description_placeholders={
                "unit": names.get(slave, subentry.title),
                "slave": str(slave),
                "unit_details": unit_detail(stored_unit_probe(subentry.data), names.get(slave)),
            },
        )
