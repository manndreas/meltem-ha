"""Shared entity helpers for Meltem entities."""

# Home Assistant types entity state as cached_property; like Home Assistant Core,
# these entities override it with plain properties computed from coordinator data.
# pyright: reportIncompatibleVariableOverride=false
from __future__ import annotations

from collections.abc import Callable, Iterable

from homeassistant.const import Platform
from homeassistant.core import callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity import Entity
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    DATA_HEALTH_ENTITY_KEY,
    ENTITY_PLATFORM_BY_KEY,
    ENTITY_PROFILES,
    GATEWAY_NAME,
    INTEGRATION_NAME,
    profile_label,
)
from .coordinator import MeltemDataUpdateCoordinator
from .identifiers import (
    gateway_entity_unique_id,
    gateway_identifiers,
    room_entity_unique_id,
    room_identifiers,
)
from .models import EMPTY_ROOM_STATE, RoomConfig, RoomState


def async_add_room_entities(
    coordinator: MeltemDataUpdateCoordinator,
    async_add_entities: AddConfigEntryEntitiesCallback,
    entities_of: Callable[[RoomConfig], Iterable[Entity]],
) -> None:
    """Add the entities of every unit to the config subentry the unit is stored in."""

    for room in coordinator.rooms:
        if entities := list(entities_of(room)):
            async_add_entities(entities, config_subentry_id=room.subentry_id)


def gateway_device_info(entry_id: str, gateway_mac: str | None = None) -> DeviceInfo:
    """Return the device of the gateway that all units of one entry hang off."""

    info = DeviceInfo(
        identifiers=gateway_identifiers(entry_id),
        manufacturer="Meltem",
        model="M-WRG-GW",
        name=GATEWAY_NAME,
    )
    if gateway_mac is not None:
        info.connections = {(dr.CONNECTION_NETWORK_MAC, gateway_mac)}
    return info


def room_has_entity(room: RoomConfig, entity_key: str) -> bool:
    """Return whether an entity exists for one room.

    This is the only rule for it: the platforms create exactly these entities,
    and setup removes registry entries of every other one.
    """

    if entity_key == DATA_HEALTH_ENTITY_KEY:
        return room.supports_any(("supply_air_flow", "extract_air_flow"))
    profiles = ENTITY_PROFILES.get(entity_key)
    if profiles is not None and room.profile not in profiles:
        return False
    return room.supports(entity_key)


def room_entity_platforms(room: RoomConfig) -> dict[str, Platform]:
    """Return the platform of every entity one room has."""

    return {
        key: platform
        for key, platform in ENTITY_PLATFORM_BY_KEY.items()
        if room_has_entity(room, key)
    }


class MeltemLocalDiagnosticEntity(CoordinatorEntity[MeltemDataUpdateCoordinator]):
    """Refresh time-dependent diagnostics locally, including during outages."""

    @property
    def available(self) -> bool:
        return True

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        # One shared timer for all these entities instead of one per entity.
        self.async_on_remove(
            self.coordinator.async_add_diagnostic_listener(self.async_write_ha_state)
        )


class MeltemGatewayBaseEntity(Entity):
    """Base entity attached to the Meltem gateway device."""

    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(self, entry_id: str, key: str) -> None:
        super().__init__()
        self._attr_unique_id = gateway_entity_unique_id(entry_id, key)
        self._attr_translation_key = key
        self._attr_device_info = gateway_device_info(entry_id)


class MeltemGatewayEntity(MeltemLocalDiagnosticEntity, MeltemGatewayBaseEntity):
    """Gateway diagnostics that remain visible when communication fails."""

    def __init__(self, coordinator: MeltemDataUpdateCoordinator, entry_id: str, key: str) -> None:
        MeltemLocalDiagnosticEntity.__init__(self, coordinator)
        MeltemGatewayBaseEntity.__init__(self, entry_id, key)


class MeltemEntity(CoordinatorEntity[MeltemDataUpdateCoordinator]):
    """Base entity for all Meltem room entities.

    Every room discovered during setup becomes one HA device, and every entity
    attaches to its room's device through this base class.
    """

    _attr_has_entity_name = True
    _requires_fresh_read_group = True

    def __init__(
        self,
        coordinator: MeltemDataUpdateCoordinator,
        room: RoomConfig,
        object_key: str,
        translation_key: str | None = None,
    ) -> None:
        super().__init__(coordinator)
        self.room = room
        self._entity_key = object_key
        self._attr_unique_id = room_entity_unique_id(coordinator.entry_id, room.key, object_key)
        self._attr_translation_key = translation_key or object_key
        self._hw_version = str(room.product_id) if room.product_id is not None else None
        self._exported_versions: tuple[str | None, str | None] = (None, None)

    @property
    def device_info(self) -> DeviceInfo:
        info = DeviceInfo(
            identifiers=room_identifiers(self.room),
            manufacturer="Meltem",
            model=profile_label(self.room.profile),
            name=f"{INTEGRATION_NAME} {self.room.name}",
            hw_version=self._hw_version,
            sw_version=self._sw_version,
        )
        if (gateway_device_id := self.coordinator.gateway_device_id) is not None:
            info.via_device_id = gateway_device_id
        return info

    @property
    def room_state(self) -> RoomState:
        return self.coordinator.safe_data.get(self.room.key, EMPTY_ROOM_STATE)

    @property
    def _sw_version(self) -> str | None:
        sw_version = self.room_state.software_version
        return str(sw_version) if sw_version is not None else None

    @property
    def available(self) -> bool:
        if not (super().available and self.coordinator.room_available(self.room.key)):
            return False
        if not self._requires_fresh_read_group:
            return True
        read_group = self.coordinator.read_group_for_entity(self._entity_key)
        return read_group is None or self.coordinator.read_group_available(
            self.room.key, read_group
        )

    @callback
    def _handle_coordinator_update(self) -> None:
        # A read job of another unit leaves this unit's values unchanged.
        if not self.coordinator.room_updated(self.room.key):
            return
        self._async_update_device_registry_versions()
        super()._handle_coordinator_update()

    def _async_update_device_registry_versions(self) -> None:
        """Push late-discovered version fields into the device registry."""

        sw_version, hw_version = versions = (self._sw_version, self._hw_version)
        # Checked first, since every entity of a unit runs this on every update of the unit.
        if versions == self._exported_versions or self.hass is None:
            return
        device = self.device_entry
        if device is None:
            return

        self._exported_versions = versions
        if versions == (getattr(device, "sw_version", None), getattr(device, "hw_version", None)):
            return
        dr.async_get(self.hass).async_update_device(
            device.id, sw_version=sw_version, hw_version=hw_version
        )


class MeltemRoomDiagnosticEntity(MeltemEntity, MeltemLocalDiagnosticEntity):
    """Keep one room's local diagnostics visible when its data goes stale."""

    @property
    def available(self) -> bool:
        return True
