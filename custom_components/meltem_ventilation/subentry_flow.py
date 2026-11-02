"""Subentry flow that adds one unit to a gateway or changes its profile."""

from __future__ import annotations

import logging
from typing import Any

import probatio as vol
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
    PROFILE_SELECTOR,
    async_probe_units,
    detected_profile_default,
    device_names_by_slave,
    new_unit,
    probe_previews,
    probe_profiles,
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

    def _coordinator(self) -> MeltemDataUpdateCoordinator | None:
        """Return the running coordinator; scans go through its gateway link."""

        entry = self._get_entry()
        if entry.state is not ConfigEntryState.LOADED:
            return None
        runtime_data: MeltemRuntimeData | None = getattr(entry, "runtime_data", None)
        return runtime_data.coordinator if runtime_data is not None else None

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        """Choose one of the units the gateway reports that is not set up yet."""

        if user_input is not None:
            self._slave = int(user_input[CONF_SLAVE])
            return await self.async_step_profile()

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

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_SLAVE): selector.SelectSelector(
                        selector.SelectSelectorConfig(
                            options=[str(slave) for slave in self._slaves],
                            mode=selector.SelectSelectorMode.LIST,
                        )
                    )
                }
            ),
            description_placeholders={
                "device_count": str(len(self._slaves)),
                "unit_details": unit_details(
                    self._slaves, probe_previews(self._probe_results_by_slave)
                ),
            },
        )

    async def async_step_profile(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Choose the profile of the unit to add."""

        slave = self._slave
        if user_input is not None:
            room = new_unit(
                slave,
                str(user_input[CONF_PROFILE]),
                self._probe_results_by_slave.get(slave),
                unit_rooms(self._get_entry()).values(),
            )
            return self.async_create_entry(
                title=str(room["name"]), data=unit_data(room), unique_id=str(slave)
            )

        return self.async_show_form(
            step_id="profile",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_PROFILE,
                        default=detected_profile_default(
                            slave, probe_profiles(self._probe_results_by_slave)
                        ),
                    ): PROFILE_SELECTOR
                }
            ),
            description_placeholders={
                "slave": str(slave),
                "unit_details": unit_details([slave], probe_previews(self._probe_results_by_slave)),
            },
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Change the profile of one unit."""

        entry = self._get_entry()
        subentry = self._get_reconfigure_subentry()
        if user_input is not None:
            data_updates = {CONF_PROFILE: str(user_input[CONF_PROFILE])}
            if entry.update_listeners:
                # The loaded entry's update listener reloads it with the new profile.
                return self.async_update_and_abort(entry, subentry, data_updates=data_updates)
            return self.async_update_reload_and_abort(
                entry,
                subentry,
                data_updates=data_updates,
                reload_even_if_entry_is_unchanged=False,
            )

        slave = int(subentry.data[CONF_SLAVE])
        names = device_names_by_slave(self.hass, entry.entry_id, unit_rooms(entry).values())
        preview = subentry.data.get("preview")
        return self.async_show_form(
            step_id="reconfigure",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_PROFILE, default=str(subentry.data[CONF_PROFILE])
                    ): PROFILE_SELECTOR
                }
            ),
            description_placeholders={
                "unit": names.get(slave, subentry.title),
                "unit_details": unit_details(
                    [slave], {slave: str(preview)} if preview else {}, names
                ),
            },
        )
