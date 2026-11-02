"""Set up the Meltem Modbus integration entry and runtime objects.

This module keeps the config-entry setup path intentionally small:
- normalize the selected serial port
- derive each configured room's entities from its profile
- create one shared Modbus client and one shared coordinator

The serial link itself belongs to Home Assistant's ``modbus`` integration,
which hands out units over a connection shared with any other integration.
All actual Modbus traffic stays in ``modbus_client.py`` and all polling
decisions stay in ``coordinator.py``.
"""

from __future__ import annotations

import logging
from collections import Counter
from dataclasses import replace
from functools import partial

from homeassistant.components.modbus import async_get_unit
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import (
    ConfigEntryError,
    ConfigEntryNotReady,
    HomeAssistantError,
)
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er

from .const import (
    CONF_MAX_REQUESTS_PER_SECOND,
    CONF_PORT,
    DEFAULT_MAX_REQUESTS_PER_SECOND,
    DOMAIN,
    GATEWAY_ENTITY_PLATFORM_BY_KEY,
    PLATFORMS,
)
from .coordinator import MeltemDataUpdateCoordinator
from .cumulative_diagnostics import CumulativeDiagnostics
from .entity import gateway_device_info, room_entity_platforms
from .gateway_connection import (
    SerialGatewayConnection,
    gateway_connection_from_data,
)
from .identifiers import (
    APP_SETUP_CODE_IDENTIFIER_PREFIX,
    gateway_entity_unique_id,
    gateway_identifiers,
    room_entity_unique_id,
    room_identifiers,
)
from .migration import async_migrate_entry
from .modbus_client import MeltemModbusClient
from .modbus_helpers import (
    MeltemModbusError,
    resolve_preferred_port_path,
)
from .models import MeltemIntegrationConfigEntry, MeltemRuntimeData, RoomConfig
from .units import room_config, unit_subentries

__all__ = ["async_migrate_entry"]

_LOGGER = logging.getLogger(__name__)


def _room_configs(entry: MeltemIntegrationConfigEntry) -> list[RoomConfig]:
    """Build the configured units; a code stored for several units identifies none of them."""

    rooms = [room_config(subentry) for subentry in unit_subentries(entry)]
    code_counts = Counter(room.app_setup_code for room in rooms if room.app_setup_code)
    ambiguous = [
        room.key for room in rooms if room.app_setup_code and code_counts[room.app_setup_code] > 1
    ]
    if not ambiguous:
        return rooms
    _LOGGER.warning(
        "Units %s share one stored app setup code; ignoring it until the next rescan",
        ", ".join(ambiguous),
    )
    return [replace(room, app_setup_code=None) if room.key in ambiguous else room for room in rooms]


def _max_requests_per_second(entry: MeltemIntegrationConfigEntry) -> float:
    return float(entry.options.get(CONF_MAX_REQUESTS_PER_SECOND, DEFAULT_MAX_REQUESTS_PER_SECOND))


async def _async_normalize_port(hass: HomeAssistant, entry: MeltemIntegrationConfigEntry) -> str:
    """Store and return the stable ``/dev/serial/by-id`` path of the gateway port.

    A by-id link can appear after setup, so this is a runtime fix-up, not a migration.
    """

    # Resolution walks /dev/serial/by-id, so it must not run in the event loop.
    port = await hass.async_add_executor_job(resolve_preferred_port_path, entry.data[CONF_PORT])
    if port != entry.data[CONF_PORT] or entry.unique_id != port:
        hass.config_entries.async_update_entry(
            entry, data={**entry.data, CONF_PORT: port}, unique_id=port
        )
    return port


def _async_remove_unsupported_entities(
    hass: HomeAssistant, entry: MeltemIntegrationConfigEntry, rooms: list[RoomConfig]
) -> None:
    """Drop registry entries that no configured room/profile creates anymore."""

    expected: dict[str, str] = {
        room_entity_unique_id(entry.entry_id, room.key, object_key): platform.value
        for room in rooms
        for object_key, platform in room_entity_platforms(room).items()
    }
    expected.update(
        (gateway_entity_unique_id(entry.entry_id, key), platform.value)
        for key, platform in GATEWAY_ENTITY_PLATFORM_BY_KEY.items()
    )

    registry = er.async_get(hass)
    for existing in er.async_entries_for_config_entry(registry, entry.entry_id):
        if (
            not existing.unique_id.startswith(f"{DOMAIN}_")
            or expected.get(existing.unique_id) == existing.domain
        ):
            continue
        _LOGGER.info("Removing unsupported Meltem entity %s", existing.entity_id)
        registry.async_remove(existing.entity_id)


def _async_register_gateway_device(
    hass: HomeAssistant,
    entry: MeltemIntegrationConfigEntry,
    gateway_mac: str | None = None,
) -> None:
    """Register the gateway, including any optional network identity."""

    registry = dr.async_get(hass)
    gateway = gateway_device_info(entry.entry_id, gateway_mac)
    registry.async_get_or_create_from_info(config_entry_id=entry.entry_id, device_info=gateway)


def _async_sync_devices(
    hass: HomeAssistant, entry: MeltemIntegrationConfigEntry, rooms: list[RoomConfig]
) -> None:
    """Register the gateway device and align the unit devices with the configured units.

    Devices of units no longer configured are removed. A unit device loses app
    setup codes its unit no longer reports, so the code can move to the device
    of the address that has it now without a device identifier collision.
    """

    registry = dr.async_get(hass)
    # Unit devices reference the gateway via ``via_device_id``, so it must exist first.
    _async_register_gateway_device(hass, entry)
    expected = {room.key: room_identifiers(room) for room in rooms}
    configured = {(DOMAIN, room_key) for room_key in expected} | gateway_identifiers(entry.entry_id)
    for device in dr.async_entries_for_config_entry(registry, entry.entry_id):
        if not device.identifiers & configured:
            _LOGGER.info("Removing device of unconfigured Meltem unit %s", device.name)
            registry.async_remove_device(device.id)
            continue
        room_key = next(
            (key for domain, key in device.identifiers if domain == DOMAIN and key in expected),
            None,
        )
        if room_key is None:
            continue
        stale = {
            identifier
            for identifier in device.identifiers
            if identifier[0] == DOMAIN
            and identifier[1].startswith(APP_SETUP_CODE_IDENTIFIER_PREFIX)
            and identifier not in expected[room_key]
        }
        if stale:
            registry.async_update_device(device.id, new_identifiers=device.identifiers - stale)


async def _async_initial_refresh_and_gateway_mac(
    hass: HomeAssistant,
    entry: MeltemIntegrationConfigEntry,
    coordinator: MeltemDataUpdateCoordinator,
) -> None:
    """Run the first poll tick, which starts polling, then add the gateway MAC."""

    await coordinator.async_poll()
    try:
        gateway_mac = await coordinator.async_read_gateway_mac()
    except MeltemModbusError as err:
        _LOGGER.debug("Unable to read optional gateway MAC address: %s", err)
    else:
        _async_register_gateway_device(hass, entry, gateway_mac)


async def async_setup_entry(hass: HomeAssistant, entry: MeltemIntegrationConfigEntry) -> bool:
    """Set up Meltem Modbus from a config entry."""

    connection = gateway_connection_from_data(entry.data)
    if isinstance(connection, SerialGatewayConnection):
        connection = SerialGatewayConnection(await _async_normalize_port(hass, entry))
    port = connection.unique_id
    rooms = _room_configs(entry)
    max_requests_per_second = _max_requests_per_second(entry)
    _LOGGER.info(
        "Using Meltem max request rate of %.1f req/s for %s configured unit(s)",
        max_requests_per_second,
        len(rooms),
    )

    _async_remove_unsupported_entities(hass, entry, rooms)
    _async_sync_devices(hass, entry, rooms)
    cumulative_diagnostics = await CumulativeDiagnostics.async_create(hass, entry.entry_id)

    # All rooms share one client, and the client asks Home Assistant's modbus
    # integration for its units, so the gateway only ever sees one connection.
    client = MeltemModbusClient(
        partial(async_get_unit, hass, entry, connection.modbus_params()),
        port=port,
        max_requests_per_second=max_requests_per_second,
        on_request=cumulative_diagnostics.record_request,
        on_link_recycle=cumulative_diagnostics.record_link_recycle,
    )
    try:
        await client.async_validate_gateway()
    except MeltemModbusError as err:
        client.shutdown()
        await cumulative_diagnostics.async_flush()
        _LOGGER.warning("Unable to connect to the Meltem gateway via %s: %s", port, err)
        raise ConfigEntryNotReady(
            translation_domain=DOMAIN,
            translation_key="gateway_unavailable",
            translation_placeholders={"port": port},
        ) from err
    except HomeAssistantError as err:
        # Another integration already uses this port with other link settings.
        client.shutdown()
        await cumulative_diagnostics.async_flush()
        _LOGGER.warning("Connection %s cannot be used by the Meltem gateway: %s", port, err)
        raise ConfigEntryError(
            translation_domain=DOMAIN,
            translation_key="port_in_use",
        ) from err

    coordinator = MeltemDataUpdateCoordinator(
        hass,
        config_entry=entry,
        client=client,
        rooms=rooms,
        max_requests_per_second=max_requests_per_second,
        cumulative_diagnostics=cumulative_diagnostics,
    )
    entry.runtime_data = MeltemRuntimeData(
        coordinator=coordinator, setup_snapshot=_setup_snapshot(entry)
    )
    forwarded = False
    try:
        await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
        forwarded = True
    finally:
        if not forwarded:
            try:
                await coordinator.async_shutdown()
            finally:
                client.shutdown()
                # Home Assistant only drops runtime_data on unload, not after a failed setup.
                del entry.runtime_data

    entry.async_on_unload(entry.add_update_listener(_async_reload_on_change))
    entry.async_create_background_task(
        hass,
        _async_initial_refresh_and_gateway_mac(hass, entry, coordinator),
        "meltem_first_refresh",
    )
    return True


def _setup_snapshot(entry: MeltemIntegrationConfigEntry) -> dict[str, object]:
    """Return what setup reads from the entry, apart from the live request rate."""

    return {
        **gateway_connection_from_data(entry.data).as_data(),
        "units": {
            subentry.subentry_id: (subentry.title, dict(subentry.data))
            for subentry in unit_subentries(entry)
        },
    }


async def _async_reload_on_change(hass: HomeAssistant, entry: MeltemIntegrationConfigEntry) -> None:
    """Reload once the port or the units changed.

    A new request rate applies without a reload, and a rescan changes several
    units at once, so a reload is scheduled only once per change.
    """

    runtime_data: MeltemRuntimeData | None = getattr(entry, "runtime_data", None)
    # A listener call queued before an unload can run after it.
    if runtime_data is None or runtime_data.reload_scheduled:
        return
    if _setup_snapshot(entry) == runtime_data.setup_snapshot:
        return
    runtime_data.reload_scheduled = True
    hass.config_entries.async_schedule_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: MeltemIntegrationConfigEntry) -> bool:
    """Unload a config entry."""

    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        runtime_data: MeltemRuntimeData = entry.runtime_data
        try:
            await runtime_data.coordinator.async_shutdown()
        finally:
            # A pending write readback must not reach the link after unloading;
            # the modbus integration closes the link once the entry releases it.
            runtime_data.coordinator.client.shutdown()

    return unload_ok


async def async_remove_entry(hass: HomeAssistant, entry: MeltemIntegrationConfigEntry) -> None:
    """Remove data that belongs to a permanently deleted config entry."""

    await CumulativeDiagnostics.async_remove(hass, entry.entry_id)
