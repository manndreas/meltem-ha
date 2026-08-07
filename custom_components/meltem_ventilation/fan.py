"""Fan entities for Meltem ventilation units.

Each unit exposes one fan per air direction. The Meltem hardware drives both
fans from a single register in balanced mode, so writing one direction always
sends both values and switches the unit to unbalanced mode.
"""

from __future__ import annotations

import math

from homeassistant.components.fan import FanEntity, FanEntityFeature
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.util.percentage import (
    percentage_to_ranged_value,
    ranged_value_to_percentage,
)
from homeassistant.util.scaling import int_states_in_range

from .const import (
    OPERATION_MODE_OFF,
    SENSOR_OPERATION_MODES,
    profile_max_airflow,
)
from .entity import MeltemEntity, room_supports_entity
from .models import MeltemRuntimeData, RoomConfig

DIRECTION_SUPPLY = "supply"
DIRECTION_EXTRACT = "extract"

DEFAULT_TURN_ON_PERCENTAGE = 50


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Meltem fan entities."""

    runtime_data: MeltemRuntimeData = entry.runtime_data
    coordinator = runtime_data.coordinator

    entities: list[FanEntity] = []
    for room in coordinator.rooms:
        if room_supports_entity(room, "supply_level"):
            entities.append(
                MeltemDirectionalFanEntity(coordinator, room, DIRECTION_SUPPLY)
            )
        if room_supports_entity(room, "extract_level"):
            entities.append(
                MeltemDirectionalFanEntity(coordinator, room, DIRECTION_EXTRACT)
            )
    async_add_entities(entities)


class MeltemDirectionalFanEntity(MeltemEntity, FanEntity):
    """Airflow target for one direction of a Meltem unit."""

    _attr_supported_features = (
        FanEntityFeature.SET_SPEED
        | FanEntityFeature.TURN_OFF
        | FanEntityFeature.TURN_ON
    )

    def __init__(self, coordinator, room: RoomConfig, direction: str) -> None:
        entity_key = f"{direction}_level"
        super().__init__(coordinator, room, entity_key, entity_key)
        self._direction = direction
        self._attr_icon = (
            "mdi:fan-chevron-up" if direction == DIRECTION_SUPPLY else "mdi:fan-chevron-down"
        )
        # One step per m3/h, so the slider cannot land between device values.
        self._attr_speed_count = int_states_in_range(_level_range(room.profile))

    @property
    def _levels(self) -> tuple[int | None, int | None]:
        return self.coordinator.effective_levels(self.room.key)

    @property
    def _own_level(self) -> int | None:
        supply, extract = self._levels
        return supply if self._direction == DIRECTION_SUPPLY else extract

    @property
    def _other_level(self) -> int | None:
        supply, extract = self._levels
        return extract if self._direction == DIRECTION_SUPPLY else supply

    @property  # type: ignore[override]
    def is_on(self) -> bool | None:
        percentage = self.percentage
        if percentage is None:
            return None
        return percentage > 0

    @property  # type: ignore[override]
    def percentage(self) -> int | None:  # type: ignore[override]
        level = self._own_level
        if level is None:
            return None
        return _normalize_level_to_percentage(level, self.room.profile)

    async def async_turn_on(
        self,
        percentage: int | None = None,
        preset_mode: str | None = None,
        **kwargs,
    ) -> None:
        if percentage is None:
            percentage = self.percentage or DEFAULT_TURN_ON_PERCENTAGE
        if percentage == 0:
            percentage = DEFAULT_TURN_ON_PERCENTAGE
        await self.async_set_percentage(percentage)

    async def async_turn_off(self, **kwargs) -> None:
        await self.async_set_percentage(0)

    async def async_set_percentage(self, percentage: int) -> None:
        normalized = max(0, min(100, int(percentage)))
        # Home Assistant works in percent, the coordinator in m3/h.
        own_level = _percentage_to_level(normalized, self.room.profile)
        other_level = self._other_level
        if other_level is None:
            # Writing always sends both directions, so guessing here would
            # silently stop the other fan.
            raise HomeAssistantError(
                f"Cannot change {self.entity_id}: the current airflow of the "
                "opposite direction is unknown"
            )

        operation_mode = self.room_state.operation_mode
        # Starting from a stopped unit, run both directions rather than
        # dropping straight into single-direction operation.
        starting_from_off = operation_mode == OPERATION_MODE_OFF
        # Under sensor control both fans only report fluctuating measurements,
        # so writing one direction would pin the other to a sampled value.
        # Turning a direction off still has to reach the unbalanced path.
        leaving_sensor_control = (
            own_level > 0 and operation_mode in SENSOR_OPERATION_MODES
        )

        if own_level == other_level or starting_from_off or leaving_sensor_control:
            # Equal levels are the balanced case; writing them as unbalanced
            # would leave the unit in a mode it cannot return from.
            await self.coordinator.async_set_level(self.room.key, own_level)
            return

        if self._direction == DIRECTION_SUPPLY:
            supply_level, extract_level = own_level, other_level
        else:
            supply_level, extract_level = other_level, own_level

        await self.coordinator.async_set_unbalanced_levels(
            self.room.key,
            supply_level,
            extract_level,
        )


def _level_range(profile: str) -> tuple[int, int]:
    return (1, profile_max_airflow(profile))


def _normalize_level_to_percentage(level: int, profile: str) -> int:
    if level <= 0:
        return 0
    # Measured airflow can exceed the rated maximum, e.g. during intensive
    # ventilation or when the configured profile understates the unit.
    return min(100, ranged_value_to_percentage(_level_range(profile), level))


def _percentage_to_level(percentage: int, profile: str) -> int:
    if percentage <= 0:
        return 0
    return math.ceil(percentage_to_ranged_value(_level_range(profile), percentage))
