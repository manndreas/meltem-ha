"""Shared entity helpers for Meltem entities."""

from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, INTEGRATION_NAME, profile_label
from .coordinator import MeltemDataUpdateCoordinator
from .models import RoomConfig, RoomState


def room_supports_entity(room: RoomConfig, entity_key: str) -> bool:
    """Return whether an entity key is supported for one room."""
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

    @property
    def device_info(self) -> DeviceInfo:
        sw_version = self.room_state.software_version
        return DeviceInfo(
            identifiers={(DOMAIN, self.room.key)},
            manufacturer="Meltem",
            model=profile_label(self.room.profile),
            name=f"{INTEGRATION_NAME} {self.room.name}",
            sw_version=str(sw_version) if sw_version is not None else None,
        )

    @property
    def room_state(self) -> RoomState:
        return self.coordinator.safe_data.get(self.room.key, RoomState())
