"""Shared entity helpers for Meltem entities."""

from __future__ import annotations

import re

from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, GATEWAY_NAME, INTEGRATION_NAME, profile_label
from .coordinator import MeltemDataUpdateCoordinator
from .models import EMPTY_ROOM_STATE, RoomConfig, RoomState

_PREVIEW_PRODUCT_ID_RE = re.compile(r"\bID\s+(\d+)\b")


def gateway_device_info(entry_id: str) -> DeviceInfo:
    """Return the device of the gateway that all units of one entry hang off."""

    return DeviceInfo(
        identifiers={(DOMAIN, entry_id)},
        manufacturer="Meltem",
        model="M-WRG-GW",
        name=GATEWAY_NAME,
    )


def room_supports_entity(
    room: RoomConfig,
    entity_key: str,
    profiles: frozenset[str] | None = None,
) -> bool:
    """Return whether an entity should exist for one room.

    ``profiles`` additionally restricts the entity to the unit families that
    carry the underlying hardware.
    """

    if profiles is not None and room.profile not in profiles:
        return False
    return room.supports(entity_key)


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
        translation_key: str,
    ) -> None:
        super().__init__(coordinator)
        self.room = room
        self._entity_key = object_key
        self._attr_unique_id = f"{DOMAIN}_{room.key}_{object_key}"
        self._attr_translation_key = translation_key
        self._hw_version = _product_id_from_preview(room.preview)
        self._exported_versions: tuple[str | None, str | None] = (None, None)

    @property
    def device_info(self) -> DeviceInfo:
        info = DeviceInfo(
            identifiers={(DOMAIN, self.room.key)},
            manufacturer="Meltem",
            model=profile_label(self.room.profile),
            name=f"{INTEGRATION_NAME} {self.room.name}",
            hw_version=self._hw_version,
            sw_version=self._sw_version,
        )
        if (gateway_device_id := self.coordinator.gateway_device_id) is not None:
            info["via_device_id"] = gateway_device_id
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

    def _handle_coordinator_update(self) -> None:
        self._async_update_device_registry_versions()
        super()._handle_coordinator_update()

    def _async_update_device_registry_versions(self) -> None:
        """Push late-discovered version fields into the device registry."""

        device = self.device_entry
        if self.hass is None or device is None:
            return

        versions = (self._sw_version, self._hw_version)
        if versions == self._exported_versions:
            return

        dr.async_get(self.hass).async_update_device(
            device.id, sw_version=versions[0], hw_version=versions[1]
        )
        self._exported_versions = versions


def _product_id_from_preview(preview: str | None) -> str | None:
    """Extract the raw product ID from the setup preview string."""

    match = _PREVIEW_PRODUCT_ID_RE.search(preview or "")
    return match.group(1) if match else None
