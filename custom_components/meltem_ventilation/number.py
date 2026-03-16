"""Writable number entities for Meltem units.

These entities are optimistic on purpose: Home Assistant updates the slider
immediately, then waits for the coordinator to confirm the new value from the
gateway. This keeps the UI responsive even though writes settle slowly.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from dataclasses import replace
import logging
import time
from typing import Callable

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import CO2_PROFILES, DEBOUNCE_SECONDS, HUMIDITY_PROFILES, profile_max_airflow
from .entity import MeltemEntity, room_supports_entity
from .models import MeltemRuntimeData, RoomConfig, RoomState

_LOGGER = logging.getLogger(__name__)


@dataclass(slots=True, frozen=True)
class OptimisticTargets:
    """Shared optimistic target pair for one room."""

    supply_level: int
    extract_level: int
    expires_at: float


@dataclass(slots=True, frozen=True)
class PendingWriteCommand:
    """Latest queued write command for one room."""

    supply_level: int
    extract_level: int
    updated_at: float


@dataclass(slots=True, frozen=True)
class MeltemControlSettingNumberDescription:
    """Describe one writable humidity/CO2 control setting."""

    key: str
    native_min_value: float
    native_max_value: float
    native_step: float
    native_unit_of_measurement: str | None
    icon: str
    supported_profiles: frozenset[str]
    value_fn: Callable[[RoomState], int | None]


CONTROL_SETTING_DESCRIPTIONS: tuple[MeltemControlSettingNumberDescription, ...] = (
    MeltemControlSettingNumberDescription(
        key="humidity_starting_point",
        native_min_value=40,
        native_max_value=60,
        native_step=10,
        native_unit_of_measurement="%",
        icon="mdi:water-percent",
        supported_profiles=HUMIDITY_PROFILES,
        value_fn=lambda state: state.humidity_starting_point,
    ),
    MeltemControlSettingNumberDescription(
        key="humidity_min_level",
        native_min_value=0,
        native_max_value=100,
        native_step=10,
        native_unit_of_measurement="%",
        icon="mdi:fan-minus",
        supported_profiles=HUMIDITY_PROFILES,
        value_fn=lambda state: state.humidity_min_level,
    ),
    MeltemControlSettingNumberDescription(
        key="humidity_max_level",
        native_min_value=0,
        native_max_value=100,
        native_step=10,
        native_unit_of_measurement="%",
        icon="mdi:fan-plus",
        supported_profiles=HUMIDITY_PROFILES,
        value_fn=lambda state: state.humidity_max_level,
    ),
    MeltemControlSettingNumberDescription(
        key="co2_starting_point",
        native_min_value=500,
        native_max_value=1200,
        native_step=1,
        native_unit_of_measurement="ppm",
        icon="mdi:molecule-co2",
        supported_profiles=CO2_PROFILES,
        value_fn=lambda state: state.co2_starting_point,
    ),
    MeltemControlSettingNumberDescription(
        key="co2_min_level",
        native_min_value=0,
        native_max_value=100,
        native_step=10,
        native_unit_of_measurement="%",
        icon="mdi:fan-minus",
        supported_profiles=CO2_PROFILES,
        value_fn=lambda state: state.co2_min_level,
    ),
    MeltemControlSettingNumberDescription(
        key="co2_max_level",
        native_min_value=0,
        native_max_value=100,
        native_step=10,
        native_unit_of_measurement="%",
        icon="mdi:fan-plus",
        supported_profiles=CO2_PROFILES,
        value_fn=lambda state: state.co2_max_level,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Meltem number entities."""

    runtime_data: MeltemRuntimeData = entry.runtime_data
    coordinator = runtime_data.coordinator

    entities: list[NumberEntity] = []
    for room in coordinator.rooms:
        if _room_supports(room, "level"):
            entities.append(MeltemBalancedLevelNumber(coordinator, room))
        if _room_supports(room, "supply_level"):
            entities.append(MeltemSupplyLevelNumber(coordinator, room))
        if _room_supports(room, "extract_level"):
            entities.append(MeltemExtractLevelNumber(coordinator, room))
        for description in CONTROL_SETTING_DESCRIPTIONS:
            if _supports_control_setting(room, description):
                entities.append(
                    MeltemControlSettingNumber(coordinator, room, description)
                )
    async_add_entities(entities)


def _room_supports(room: RoomConfig, entity_key: str) -> bool:
    """Return whether one number entity should exist for one room."""
    return room_supports_entity(room, entity_key)


def _supports_control_setting(
    room: RoomConfig,
    description: MeltemControlSettingNumberDescription,
) -> bool:
    """Return whether one control-setting number should exist for one room."""

    if room.profile not in description.supported_profiles:
        return False
    return _room_supports(room, description.key)


def _targets_are_synchronized(state: RoomState) -> bool:
    """Return whether the room currently behaves like a coupled target state."""

    if state.operation_mode == "unbalanced":
        if (
            state.current_level is not None
            and state.extract_target_level is not None
            and abs(state.current_level - state.extract_target_level) > 1
        ):
            return False

    return True


def _balanced_target_value(state: RoomState) -> float | None:
    """Return the coupled target value when the room is synchronized."""

    if not _targets_are_synchronized(state):
        return None

    if state.current_level is not None:
        return float(state.current_level)

    if state.supply_air_flow is not None and state.extract_air_flow is not None:
        if abs(state.supply_air_flow - state.extract_air_flow) > 1:
            return None
        return float(round((state.supply_air_flow + state.extract_air_flow) / 2))

    if state.supply_air_flow is not None:
        return float(state.supply_air_flow)

    if state.extract_air_flow is not None:
        return float(state.extract_air_flow)

    return None


def _supply_target_value(state: RoomState) -> float | None:
    """Return the UI value for the supply slider."""

    balanced_target = _balanced_target_value(state)
    if balanced_target is not None:
        return balanced_target

    if state.current_level is not None:
        return float(state.current_level)

    if state.supply_air_flow is not None:
        return float(state.supply_air_flow)
    return None


def _extract_target_candidate(state: RoomState) -> int | None:
    """Return the extract-side target that is currently active."""

    if state.operation_mode == "unbalanced" and state.extract_target_level is not None:
        return state.extract_target_level
    return state.current_level


def _extract_target_value(state: RoomState) -> float | None:
    """Return the UI value for the extract slider."""

    balanced_target = _balanced_target_value(state)
    if balanced_target is not None:
        return balanced_target

    extract_target = _extract_target_candidate(state)
    if extract_target is not None:
        return float(extract_target)

    if state.extract_air_flow is not None:
        return float(state.extract_air_flow)
    return None


class MeltemBaseLevelNumber(MeltemEntity, NumberEntity):
    """Base writable number entity for ventilation levels."""

    _attr_icon = "mdi:fan"
    _attr_mode = NumberMode.SLIDER
    _attr_native_min_value = 0
    _attr_native_step = 1
    _attr_native_unit_of_measurement = "m³/h"

    def __init__(
        self, coordinator, room, object_key: str, translation_key: str
    ) -> None:
        super().__init__(coordinator, room, object_key, translation_key)
        self._optimistic_targets_by_room: dict[str, OptimisticTargets] = (
            coordinator.optimistic_targets_by_room
        )
        self._pending_writes_by_room: dict[str, PendingWriteCommand] = (
            coordinator.pending_writes_by_room
        )
        self._write_tasks_by_room: dict[str, asyncio.Task[None]] = (
            coordinator.write_tasks_by_room
        )
        self._attr_native_max_value = float(profile_max_airflow(room.profile))

    @property
    def native_value(self) -> float | None:
        return self._read_state_value()

    async def async_set_native_value(self, value: float) -> None:
        normalized = max(
            0,
            min(int(self.native_max_value or 100), int(round(value))),
        )

        # Show the requested value immediately and let the coordinator replace
        # it once the gateway reports the same target again.
        self._set_room_optimistic_targets(normalized)
        optimistic_targets = self._optimistic_targets_by_room[self.room.key]
        self._pending_writes_by_room[self.room.key] = PendingWriteCommand(
            supply_level=optimistic_targets.supply_level,
            extract_level=optimistic_targets.extract_level,
            updated_at=time.monotonic(),
        )
        self.async_write_ha_state()
        self.coordinator.async_update_listeners()
        self._ensure_room_write_task()

    def _read_state_value(self) -> float | None:
        raise NotImplementedError

    def _build_optimistic_targets(
        self, level: int, state: RoomState
    ) -> tuple[int, int]:
        raise NotImplementedError

    @property
    def _raw_room_state(self) -> RoomState:
        return self.coordinator.safe_data.get(self.room.key, RoomState())

    @property
    def _effective_room_state(self) -> RoomState:
        optimistic = self._get_room_optimistic_targets()
        if optimistic is None:
            return self._raw_room_state

        return replace(
            self._raw_room_state,
            current_level=optimistic.supply_level,
            extract_target_level=optimistic.extract_level,
        )

    def _set_room_optimistic_targets(self, level: int) -> None:
        """Store one shared optimistic target pair for all room sliders."""

        supply_level, extract_level = self._build_optimistic_targets(
            level,
            self._effective_room_state,
        )
        self._optimistic_targets_by_room[self.room.key] = OptimisticTargets(
            supply_level=int(supply_level),
            extract_level=int(extract_level),
            expires_at=time.monotonic() + 30.0,
        )

    def _get_room_optimistic_targets(self) -> OptimisticTargets | None:
        """Return the shared optimistic targets if they have not expired."""

        optimistic = self._optimistic_targets_by_room.get(self.room.key)
        if optimistic is None:
            return None

        if time.monotonic() >= optimistic.expires_at:
            self._optimistic_targets_by_room.pop(self.room.key, None)
            return None

        return optimistic

    def _clear_room_optimistic_targets(self) -> None:
        """Remove shared optimistic targets and refresh all slider entities."""

        if self._optimistic_targets_by_room.pop(self.room.key, None) is not None:
            self.coordinator.async_update_listeners()

    def _optimistic_targets_confirmed(self, state: RoomState) -> bool:
        """Return whether the raw coordinator state confirms the optimistic targets."""

        optimistic = self._get_room_optimistic_targets()
        if optimistic is None:
            return True

        supply_value = _supply_target_value(state)
        if supply_value is None or abs(supply_value - optimistic.supply_level) > 3:
            return False

        extract_value = _extract_target_value(state)
        if extract_value is None or abs(extract_value - optimistic.extract_level) > 3:
            return False

        if optimistic.supply_level == optimistic.extract_level:
            balanced_value = _balanced_target_value(state)
            if balanced_value is None or abs(balanced_value - optimistic.supply_level) > 3:
                return False

        return True

    def _ensure_room_write_task(self) -> None:
        """Ensure one shared last-write-wins worker exists for this room."""

        if self.room.key not in self.coordinator.active_write_rooms:
            self.coordinator.active_write_rooms.add(self.room.key)
            self._write_tasks_by_room[self.room.key] = self.hass.async_create_task(
                self._async_process_room_writes()
            )

    async def _async_process_room_writes(self) -> None:
        """Serialize rapid slider updates into one stable per-room write worker."""

        try:
            while True:
                command = self._pending_writes_by_room.get(self.room.key)
                if command is None:
                    return

                remaining = DEBOUNCE_SECONDS - (time.monotonic() - command.updated_at)
                if remaining > 0:
                    await asyncio.sleep(remaining)
                    latest_command = self._pending_writes_by_room.get(self.room.key)
                    if latest_command is None:
                        return
                    if latest_command.updated_at != command.updated_at:
                        continue
                    command = latest_command

                await self._async_apply_command(command)

                latest_after_apply = self._pending_writes_by_room.get(self.room.key)
                if latest_after_apply is None:
                    return
                if latest_after_apply.updated_at == command.updated_at:
                    self._pending_writes_by_room.pop(self.room.key, None)
                    return
        except asyncio.CancelledError:
            raise
        except Exception:
            _LOGGER.exception(
                "Failed to process queued write for room %s", self.room.key
            )
            self._pending_writes_by_room.pop(self.room.key, None)
            self._clear_room_optimistic_targets()
            self.async_write_ha_state()
        finally:
            self.coordinator.active_write_rooms.discard(self.room.key)
            self._write_tasks_by_room.pop(self.room.key, None)

    async def _async_apply_command(self, command: PendingWriteCommand) -> None:
        """Apply one queued write command for this room."""

        if command.supply_level == command.extract_level:
            await self.coordinator.async_set_level(self.room.key, command.supply_level)
            return
        await self.coordinator.async_set_unbalanced_levels(
            self.room.key,
            command.supply_level,
            command.extract_level,
        )

    def _handle_coordinator_update(self) -> None:
        """Clear optimistic value once coordinator reports a matching value."""
        if self._optimistic_targets_confirmed(self._raw_room_state):
            self._optimistic_targets_by_room.pop(self.room.key, None)
        super()._handle_coordinator_update()

class MeltemBalancedLevelNumber(MeltemBaseLevelNumber):
    """Writable number entity for balanced ventilation level."""

    def __init__(self, coordinator, room) -> None:
        super().__init__(coordinator, room, "level", "level")
        self._attr_icon = "mdi:fan"

    def _read_state_value(self) -> float | None:
        return _balanced_target_value(self._effective_room_state)

    def _build_optimistic_targets(
        self, level: int, state: RoomState
    ) -> tuple[int, int]:
        return level, level


class MeltemSupplyLevelNumber(MeltemBaseLevelNumber):
    """Writable number entity for unbalanced supply air level."""

    def __init__(self, coordinator, room) -> None:
        super().__init__(coordinator, room, "supply_level", "supply_level")
        self._attr_icon = "mdi:fan-chevron-down"

    def _read_state_value(self) -> float | None:
        return _supply_target_value(self._effective_room_state)

    def _build_optimistic_targets(
        self, level: int, state: RoomState
    ) -> tuple[int, int]:
        extract_level = _extract_target_candidate(state)
        if extract_level is None:
            extract_level = _balanced_target_value(state)
        if extract_level is None:
            extract_level = level
        return level, int(extract_level)


class MeltemExtractLevelNumber(MeltemBaseLevelNumber):
    """Writable number entity for unbalanced extract air level."""

    def __init__(self, coordinator, room) -> None:
        super().__init__(coordinator, room, "extract_level", "extract_level")
        self._attr_icon = "mdi:fan-chevron-up"

    def _read_state_value(self) -> float | None:
        return _extract_target_value(self._effective_room_state)

    def _build_optimistic_targets(
        self, level: int, state: RoomState
    ) -> tuple[int, int]:
        supply_level = state.current_level
        if supply_level is None:
            supply_level = _balanced_target_value(state)
        if supply_level is None:
            supply_level = level
        return int(supply_level), level


class MeltemControlSettingNumber(MeltemEntity, NumberEntity):
    """Writable config number for humidity/CO2 automation thresholds."""

    entity_description: MeltemControlSettingNumberDescription
    _attr_entity_category = EntityCategory.CONFIG
    _attr_mode = NumberMode.BOX

    def __init__(
        self,
        coordinator,
        room,
        description: MeltemControlSettingNumberDescription,
    ) -> None:
        super().__init__(coordinator, room, description.key, description.key)
        self.entity_description = description
        self._attr_icon = description.icon
        self._attr_native_min_value = description.native_min_value
        self._attr_native_max_value = description.native_max_value
        self._attr_native_step = description.native_step
        self._attr_native_unit_of_measurement = description.native_unit_of_measurement

    @property
    def native_value(self) -> float | None:
        value = self.entity_description.value_fn(self.room_state)
        return float(value) if value is not None else None

    async def async_set_native_value(self, value: float) -> None:
        await self.coordinator.async_set_control_setting(
            self.room.key,
            self.entity_description.key,
            int(round(value)),
        )
