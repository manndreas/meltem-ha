"""Shared entity helpers for Meltem entities."""

from __future__ import annotations

import re

from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, INTEGRATION_NAME, profile_label
from .coordinator import MeltemDataUpdateCoordinator
from .models import EMPTY_ROOM_STATE, RoomConfig, RoomState

_PREVIEW_PRODUCT_ID_RE = re.compile(r"\bID\s+(\d+)\b")


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
    if room.supported_entity_keys is None:
        return True
    return entity_key in room.supported_entity_keys


class MeltemEntity(CoordinatorEntity[MeltemDataUpdateCoordinator]):
    """Base entity for all Meltem room entities.

    Every room discovered during setup becomes one HA device. Individual
    sensors, binary sensors, and numbers attach to that device via this base
    class.
    """

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: MeltemDataUpdateCoordinator,
        room: RoomConfig,
        object_key: str,
        translation_key: str,
    ) -> None:
        super().__init__(coordinator)
        self.room = room
        self._attr_unique_id = f"{DOMAIN}_{room.key}_{object_key}"
        self._attr_translation_key = translation_key
        self._hw_version = _product_id_from_preview(room.preview)
        self._last_exported_sw_version: str | None = None
        self._last_exported_hw_version: str | None = None

    @property
    def device_info(self) -> DeviceInfo:
        sw_version = self.room_state.software_version
        return DeviceInfo(
            identifiers={(DOMAIN, self.room.key)},
            manufacturer="Meltem",
            model=profile_label(self.room.profile),
            name=f"{INTEGRATION_NAME} {self.room.name}",
            hw_version=self._hw_version,
            sw_version=str(sw_version) if sw_version is not None else None,
        )

    @property
    def room_state(self) -> RoomState:
        return self.coordinator.safe_data.get(self.room.key, EMPTY_ROOM_STATE)

    @property
    def available(self) -> bool:
        return super().available and self.coordinator.room_available(self.room.key)

    def _handle_coordinator_update(self) -> None:
        self._async_update_device_registry_versions()
        super()._handle_coordinator_update()

    def _async_update_device_registry_versions(self) -> None:
        """Push late-discovered version fields into the device registry."""

        if self.hass is None:
            return

        sw_version = (
            str(self.room_state.software_version)
            if self.room_state.software_version is not None
            else None
        )
        if (
            sw_version == self._last_exported_sw_version
            and self._hw_version == self._last_exported_hw_version
        ):
            return

        device_registry = dr.async_get(self.hass)
        device = device_registry.async_get_device(identifiers={(DOMAIN, self.room.key)})
        if device is None:
            return

        device_registry.async_update_device(
            device.id,
            sw_version=sw_version,
            hw_version=self._hw_version,
        )
        self._last_exported_sw_version = sw_version
        self._last_exported_hw_version = self._hw_version


def _product_id_from_preview(preview: str | None) -> str | None:
    """Extract the raw product ID from the setup preview string."""

    if not preview:
        return None
    match = _PREVIEW_PRODUCT_ID_RE.search(preview)
    if match is None:
        return None
    return match.group(1)
