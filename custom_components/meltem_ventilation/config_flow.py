"""Config flow and options flow for Meltem gateways.

The flow uses the gateway bridge registers to discover configured units first,
then performs a small per-unit probe to preselect the most likely profile.
The heavier runtime reads happen only after the config entry is created.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from contextlib import AsyncExitStack, asynccontextmanager
from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.components.modbus import async_get_temporary_unit
from homeassistant.config_entries import ConfigEntry, ConfigFlowResult
from homeassistant.core import callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import selector
from homeassistant.helpers.service_info.usb import UsbServiceInfo
from modbus_connection import ModbusUnit

from .const import (
    CONF_MAX_REQUESTS_PER_SECOND,
    CONF_PORT,
    CONF_ROOMS,
    DEFAULT_GATEWAY_DEVICE_ID,
    DEFAULT_MAX_REQUESTS_PER_SECOND,
    DEFAULT_PORT,
    DEFAULT_SCAN_SLAVE_END,
    DEFAULT_SCAN_SLAVE_START,
    DOMAIN,
    GATEWAY_NAME,
    MAX_MAX_REQUESTS_PER_SECOND,
    MIN_MAX_REQUESTS_PER_SECOND,
    MODEL_PROFILE_LABELS,
)
from .coordinator import MeltemDataUpdateCoordinator
from .modbus_helpers import (
    MeltemConnectionError,
    MeltemModbusError,
    build_serial_params,
    detect_slave_details,
    discover_gateway_nodes,
    new_transport_policy,
    prepare_unit,
    read_gateway_node_count,
    resolve_preferred_port_path,
    supported_entity_keys_for_profile,
)
from .models import MeltemRuntimeData

_LOGGER = logging.getLogger(__name__)



def _build_options_result_data(
    config_entry: ConfigEntry, request_rate: float
) -> dict[str, object]:
    """Return the persisted options payload for finishing an options flow."""

    return {
        **config_entry.options,
        CONF_MAX_REQUESTS_PER_SECOND: request_rate,
    }


def _profiles_form(
    slaves: list[int],
    defaults_by_slave: Mapping[int, str],
    previews_by_slave: Mapping[int, str],
    names_by_slave: Mapping[int, str] | None = None,
) -> tuple[vol.Schema, dict[str, str]]:
    """Build the schema and description placeholders for one profile step."""

    profile_selector = _build_profile_selector()
    data_schema = vol.Schema(
        {
            vol.Required(
                _profile_field_key(slave),
                default=defaults_by_slave[slave],
            ): profile_selector
            for slave in slaves
        }
    )
    placeholders = {
        "device_count": str(len(slaves)),
        "unit_details": _unit_details(slaves, previews_by_slave, names_by_slave),
    }
    return data_schema, placeholders


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


def _profile_field_key(slave: int) -> str:
    """Build the stable schema key for one detected unit.

    The key is derived from the Modbus address so it survives rescans and can
    be translated in ``strings.json``.
    """

    return f"slave_{slave}"


def _default_room_name(index: int) -> str:
    """Build a default room/device name."""

    return f"Unit {index}"


def _unit_details(
    slaves: list[int],
    previews_by_slave: Mapping[int, str],
    names_by_slave: Mapping[int, str] | None = None,
) -> str:
    """Build the markdown list that identifies each unit in the step description."""

    names_by_slave = names_by_slave or {}
    lines: list[str] = []
    for slave in slaves:
        details: list[str] = []
        name = names_by_slave.get(slave)
        if name:
            details.append(name)
        preview = previews_by_slave.get(slave)
        if preview:
            details.append(preview.replace("ID ", "Hardware ID "))
        suffix = f": {', '.join(details)}" if details else ""
        lines.append(f"- **{slave}**{suffix}")
    return "\n".join(lines)


@callback
def _device_names_by_slave(
    hass, config_entry_id: str, rooms: list[Mapping[str, Any]]
) -> dict[int, str]:
    """Map unit addresses to the device names the user set in Home Assistant.

    Naming belongs to the device registry, so the stored room name is only ever
    the initial value and is not shown here.
    """

    registry = dr.async_get(hass)
    names: dict[int, str] = {}
    for room in rooms:
        device = registry.async_get_device_by_identifier(
            (DOMAIN, str(room["key"])), config_entry_id
        )
        if device is not None and device.name_by_user:
            names[int(room["slave"])] = device.name_by_user
    return names


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
) -> list[dict[str, object]]:
    """Build room config entries from selected per-device profiles."""

    rooms: list[dict[str, object]] = []
    previews_by_slave = previews_by_slave or {}
    existing_rooms_by_slave = existing_rooms_by_slave or {}
    used_room_keys: set[str] = set()

    for index, slave in enumerate(slaves, start=1):
        existing_room = existing_rooms_by_slave.get(slave, {})
        selected_profile = str(selected_profiles[_profile_field_key(slave)])
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
                # Only the initial device name; renaming happens in the device registry.
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


class _PortInUseError(MeltemConnectionError):
    """Another integration holds the serial port with other link settings."""


@asynccontextmanager
async def _temporary_units(
    hass, port: str
) -> AsyncIterator[Callable[[int], Awaitable[ModbusUnit]]]:
    """Hold units on the gateway link for one flow step.

    The link closes again on exit unless a loaded entry also holds it.
    """

    params = build_serial_params(port)
    policy = new_transport_policy()
    async with AsyncExitStack() as stack:
        units: dict[int, ModbusUnit] = {}

        async def unit_for(slave: int) -> ModbusUnit:
            if slave not in units:
                try:
                    unit = await stack.enter_async_context(
                        async_get_temporary_unit(hass, params, slave)
                    )
                except HomeAssistantError as err:
                    raise _PortInUseError(str(err)) from err
                units[slave] = prepare_unit(unit, slave, policy)
            return units[slave]

        yield unit_for


async def _async_discover_units(
    hass, port: str
) -> tuple[list[int], dict[int, str], dict[int, str]]:
    """Read the unit list from the gateway and probe every unit on one link.

    Returns the unit addresses, their previews, and their detected profiles.
    The probed entity keys are intentionally not returned: the stored keys are
    always derived from the profile the user picks afterwards.
    """

    preview_by_slave: dict[int, str] = {}
    detected_profile_by_slave: dict[int, str] = {}

    async with _temporary_units(hass, port) as unit_for:
        _LOGGER.info("Starting Meltem gateway-backed unit discovery on %s", port)
        discovered_slaves = await discover_gateway_nodes(
            await unit_for(DEFAULT_GATEWAY_DEVICE_ID),
            port,
            start=DEFAULT_SCAN_SLAVE_START,
            end=DEFAULT_SCAN_SLAVE_END,
        )
        for slave in discovered_slaves:
            try:
                (
                    detected_profile,
                    preview,
                    _supported_entity_keys,
                ) = await detect_slave_details(await unit_for(slave))
            except MeltemModbusError as err:
                _LOGGER.warning(
                    "Setup probe failed for Meltem unit at slave %s: %s",
                    slave,
                    err,
                )
                detected_profile = "plain"
                preview = None
            detected_profile_by_slave[slave] = detected_profile
            if preview:
                preview_by_slave[slave] = preview

    return discovered_slaves, preview_by_slave, detected_profile_by_slave


async def _async_validate_port(hass, port: str) -> None:
    """Check that a gateway answers on the given serial port."""

    async with _temporary_units(hass, port) as unit_for:
        await read_gateway_node_count(await unit_for(DEFAULT_GATEWAY_DEVICE_ID))


async def _async_resolve_port(hass, port: str) -> str:
    """Resolve the stable serial path off the event loop.

    Resolution walks ``/dev/serial/by-id``, which is blocking filesystem I/O.
    """

    return await hass.async_add_executor_job(resolve_preferred_port_path, port)


class MeltemVentilationConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Meltem Modbus."""

    VERSION = 1
    MINOR_VERSION = 2

    def __init__(self) -> None:
        self._port = DEFAULT_PORT
        self._max_requests_per_second = DEFAULT_MAX_REQUESTS_PER_SECOND
        self._discovered_slaves: list[int] = []
        self._preview_by_slave: dict[int, str] = {}
        self._detected_profile_by_slave: dict[int, str] = {}
        self._usb_title_placeholders: dict[str, str] | None = None

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: ConfigEntry,
    ) -> MeltemVentilationOptionsFlow:
        """Return the options flow handler."""

        return MeltemVentilationOptionsFlow()

    async def async_step_user(
        self, user_input: dict | None = None
    ) -> ConfigFlowResult:
        """Collect the serial port and scan for connected units."""

        errors: dict[str, str] = {}

        if user_input is not None:
            selected_port = user_input[CONF_PORT]
            normalized_port = await _async_resolve_port(self.hass, selected_port)
            # Abort before the slow scan when this gateway is already set up.
            await self.async_set_unique_id(normalized_port)
            self._abort_if_unique_id_configured()

            try:
                (
                    discovered_slaves,
                    preview_by_slave,
                    detected_profile_by_slave,
                ) = await _async_discover_units(self.hass, normalized_port)
            except _PortInUseError:
                errors["base"] = "port_in_use"
            except MeltemModbusError:
                errors["base"] = "cannot_connect"
            except Exception:
                _LOGGER.exception("Unexpected error while scanning %s", selected_port)
                errors["base"] = "unknown"
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
                    self._preview_by_slave = preview_by_slave
                    self._detected_profile_by_slave = detected_profile_by_slave
                    self._port = normalized_port
                    self._discovered_slaves = discovered_slaves

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

    async def async_step_usb(self, discovery_info: UsbServiceInfo) -> ConfigFlowResult:
        """Handle USB discovery for a Meltem gateway."""

        port = discovery_info.device
        normalized_port = await _async_resolve_port(self.hass, port)

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
        self, user_input: dict | None = None
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

    async def async_step_scan(self) -> ConfigFlowResult:
        """Scan the gateway for configured units."""

        self._port = await _async_resolve_port(self.hass, self._port)

        try:
            (
                discovered_slaves,
                self._preview_by_slave,
                self._detected_profile_by_slave,
            ) = await _async_discover_units(self.hass, self._port)
        except _PortInUseError:
            return self._show_confirm_usb_form(errors={"base": "port_in_use"})
        except MeltemModbusError:
            return self._show_confirm_usb_form(
                errors={"base": "cannot_connect"},
            )
        except Exception:
            _LOGGER.exception("Unexpected error while scanning %s", self._port)
            return self._show_confirm_usb_form(errors={"base": "unknown"})

        if not discovered_slaves:
            _LOGGER.info(
                "Read configured Meltem units from gateway on %s and found no configured addresses",
                self._port,
            )
            return self._show_confirm_usb_form(
                errors={"base": "no_devices_found"},
            )

        self._discovered_slaves = discovered_slaves
        return await self.async_step_profiles()

    async def async_step_profiles(
        self, user_input: dict | None = None
    ) -> ConfigFlowResult:
        """Collect the profile for each detected unit."""

        if not self._discovered_slaves:
            return await self.async_step_user()

        data_schema, placeholders = _profiles_form(
            self._discovered_slaves,
            {
                slave: _detected_profile_default(slave, self._detected_profile_by_slave)
                for slave in self._discovered_slaves
            },
            self._preview_by_slave,
        )

        if user_input is not None:
            return self.async_create_entry(
                title=GATEWAY_NAME,
                data={
                    CONF_PORT: self._port,
                    CONF_MAX_REQUESTS_PER_SECOND: self._max_requests_per_second,
                    CONF_ROOMS: _build_rooms_from_profiles(
                        self._discovered_slaves,
                        user_input,
                        self._preview_by_slave,
                    ),
                },
            )

        return self.async_show_form(
            step_id="profiles",
            data_schema=data_schema,
            description_placeholders=placeholders,
        )


class MeltemVentilationOptionsFlow(config_entries.OptionsFlow):
    """Handle runtime options and gateway rescans.

    Rescans use the already running coordinator instead of opening a second
    serial connection. That keeps the gateway connection model identical during
    setup, runtime, and options changes.
    """

    def __init__(self) -> None:
        self._request_rate_override: float | None = None
        self._discovered_slaves: list[int] = []
        self._preview_by_slave: dict[int, str] = {}
        self._detected_profile_by_slave: dict[int, str] = {}

    @property
    def _max_requests_per_second(self) -> float:
        """Return the pending or currently stored scheduler request rate."""

        if self._request_rate_override is not None:
            return self._request_rate_override
        return float(
            self.config_entry.options.get(
                CONF_MAX_REQUESTS_PER_SECOND,
                self.config_entry.data.get(
                    CONF_MAX_REQUESTS_PER_SECOND,
                    DEFAULT_MAX_REQUESTS_PER_SECOND,
                ),
            )
        )

    @property
    def _coordinator(self) -> MeltemDataUpdateCoordinator | None:
        """Return the running coordinator, or ``None`` if setup never completed.

        Home Assistant offers the options flow regardless of entry state, and
        deletes ``runtime_data`` whenever the entry is not loaded.
        """

        runtime_data: MeltemRuntimeData | None = getattr(
            self.config_entry, "runtime_data", None
        )
        return runtime_data.coordinator if runtime_data is not None else None

    async def async_step_init(
        self, user_input: dict | None = None
    ) -> ConfigFlowResult:
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
    ) -> ConfigFlowResult:
        """Change serial connection settings used by the integration."""

        errors: dict[str, str] = {}
        current_port = str(self.config_entry.data.get(CONF_PORT, DEFAULT_PORT))
        current_request_rate = self._max_requests_per_second

        if user_input is not None:
            selected_port = str(user_input[CONF_PORT])
            normalized_port = await _async_resolve_port(self.hass, selected_port)
            selected_request_rate = float(user_input[CONF_MAX_REQUESTS_PER_SECOND])
            # The stored path may predate a /dev/serial/by-id symlink, so it has
            # to be normalized too before deciding that the port changed.
            current_normalized_port = await _async_resolve_port(
                self.hass, current_port
            )

            if normalized_port != current_normalized_port:
                try:
                    await _async_validate_port(self.hass, normalized_port)
                except _PortInUseError:
                    errors["base"] = "port_in_use"
                except MeltemModbusError:
                    errors["base"] = "cannot_connect"
                except Exception:
                    _LOGGER.exception("Unexpected error while opening %s", selected_port)
                    errors["base"] = "unknown"
                else:
                    self.hass.config_entries.async_update_entry(
                        self.config_entry,
                        data={
                            **self.config_entry.data,
                            CONF_PORT: normalized_port,
                        },
                        unique_id=normalized_port,
                    )

            if not errors:
                self.hass.config_entries.async_update_entry(
                    self.config_entry,
                    options={
                        **self.config_entry.options,
                        CONF_MAX_REQUESTS_PER_SECOND: selected_request_rate,
                    },
                )
                self._request_rate_override = selected_request_rate

                if normalized_port != current_normalized_port:
                    await self.hass.config_entries.async_reload(self.config_entry.entry_id)
                elif (coordinator := self._coordinator) is not None:
                    coordinator.update_request_rate(selected_request_rate)
                else:
                    # Entry never finished setup, so there is no scheduler to retune.
                    await self.hass.config_entries.async_reload(self.config_entry.entry_id)

                return self.async_create_entry(
                    title="",
                    data=_build_options_result_data(
                        self.config_entry, self._max_requests_per_second
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
    ) -> ConfigFlowResult:
        """Edit the profiles for already known units without rescanning."""

        coordinator = self._coordinator
        existing_rooms = {
            int(room["slave"]): room for room in self.config_entry.data[CONF_ROOMS]
        }
        slaves = sorted(existing_rooms)

        if not slaves:
            return await self.async_step_rescan_units()

        if user_input is None:
            self._preview_by_slave = {}
            for slave in slaves:
                preview = existing_rooms[slave].get("preview")
                if coordinator is not None:
                    try:
                        _detected_profile, preview, _keys = (
                            await coordinator.async_probe_slave_details(slave)
                        )
                    except MeltemModbusError:
                        preview = existing_rooms[slave].get("preview")
                if preview:
                    self._preview_by_slave[slave] = str(preview)

        if user_input is not None:
            return await self._async_apply_profiles(
                {
                    **self.config_entry.data,
                    CONF_ROOMS: _build_rooms_from_profiles(
                        slaves,
                        user_input,
                        self._preview_by_slave,
                        existing_rooms,
                    ),
                }
            )

        data_schema, placeholders = _profiles_form(
            slaves,
            {slave: str(existing_rooms[slave]["profile"]) for slave in slaves},
            self._preview_by_slave,
            _device_names_by_slave(
                self.hass,
                self.config_entry.entry_id,
                self.config_entry.data[CONF_ROOMS],
            ),
        )
        return self.async_show_form(
            step_id="edit_profiles",
            data_schema=data_schema,
            description_placeholders=placeholders,
        )

    async def _async_apply_profiles(self, updated_data: dict) -> ConfigFlowResult:
        """Persist changed room profiles and reload the entry."""

        self.hass.config_entries.async_update_entry(
            self.config_entry,
            data=updated_data,
            options=_build_options_result_data(
                self.config_entry, self._max_requests_per_second
            ),
        )
        await self.hass.config_entries.async_reload(self.config_entry.entry_id)
        return self.async_create_entry(
            title="",
            data=_build_options_result_data(
                self.config_entry, self._max_requests_per_second
            ),
        )

    async def async_step_rescan_units(
        self, user_input: dict | None = None
    ) -> ConfigFlowResult:
        """Rescan the gateway for configured units and update the integration."""

        errors: dict[str, str] = {}

        if user_input is not None:
            # Reuse the live coordinator/client so options changes do not race a
            # second serial connection against the running one.
            coordinator = self._coordinator
            if coordinator is None:
                errors["base"] = "cannot_connect"
                return self.async_show_form(
                    step_id="rescan_units",
                    data_schema=vol.Schema({}),
                    errors=errors,
                )
            try:
                discovered_slaves = await coordinator.async_discover_gateway_units()
            except MeltemModbusError:
                errors["base"] = "cannot_connect"
            else:
                _LOGGER.info(
                    "Rescanned Meltem gateway on %s and found slaves: %s",
                    self.config_entry.data[CONF_PORT],
                    discovered_slaves,
                )
                if not discovered_slaves:
                    _LOGGER.warning(
                        "No supported Meltem M-WRG units found on gateway at %s during rescan",
                        self.config_entry.data[CONF_PORT],
                    )
                    errors["base"] = "no_devices_found"
                else:
                    self._preview_by_slave = {}
                    self._detected_profile_by_slave = {}
                    for slave in discovered_slaves:
                        try:
                            detected_profile, preview, _keys = (
                                await coordinator.async_probe_slave_details(slave)
                            )
                        except MeltemModbusError as err:
                            _LOGGER.warning(
                                "Options rescan probe failed for Meltem unit at slave %s: %s",
                                slave,
                                err,
                            )
                            detected_profile = "plain"
                            preview = None
                        self._detected_profile_by_slave[slave] = detected_profile
                        if preview:
                            self._preview_by_slave[slave] = preview
                    self._discovered_slaves = discovered_slaves
                    return await self.async_step_profiles()

        return self.async_show_form(
            step_id="rescan_units",
            data_schema=vol.Schema({}),
            errors=errors,
        )

    async def async_step_profiles(
        self, user_input: dict | None = None
    ) -> ConfigFlowResult:
        """Update profiles after a rescan."""

        if not self._discovered_slaves:
            return await self.async_step_init()

        existing_rooms = {
            int(room["slave"]): room for room in self.config_entry.data[CONF_ROOMS]
        }

        if user_input is not None:
            return await self._async_apply_profiles(
                {
                    **self.config_entry.data,
                    CONF_PORT: await _async_resolve_port(
                        self.hass, self.config_entry.data[CONF_PORT]
                    ),
                    CONF_ROOMS: _build_rooms_from_profiles(
                        self._discovered_slaves,
                        user_input,
                        self._preview_by_slave,
                        existing_rooms,
                    ),
                }
            )

        data_schema, placeholders = _profiles_form(
            self._discovered_slaves,
            {
                slave: str(
                    existing_rooms.get(slave, {}).get(
                        "profile",
                        _detected_profile_default(
                            slave, self._detected_profile_by_slave
                        ),
                    )
                )
                for slave in self._discovered_slaves
            },
            self._preview_by_slave,
            _device_names_by_slave(
                self.hass,
                self.config_entry.entry_id,
                self.config_entry.data[CONF_ROOMS],
            ),
        )
        return self.async_show_form(
            step_id="profiles",
            data_schema=data_schema,
            description_placeholders=placeholders,
        )
