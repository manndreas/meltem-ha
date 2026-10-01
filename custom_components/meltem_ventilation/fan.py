"""Fan entities for Meltem ventilation units.

Each unit exposes one fan per air direction. Equal levels run the unit
balanced from a single register; different levels switch it to unbalanced
mode with separate supply and extract targets.
"""

from __future__ import annotations

import math

from homeassistant.components.fan import FanEntity, FanEntityFeature
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util.percentage import (
    percentage_to_ranged_value,
    ranged_value_to_percentage,
)
from homeassistant.util.scaling import int_states_in_range

from .const import (
    DIRECTION_EXTRACT,
    DIRECTION_SUPPLY,
    LEVEL_WRITE_FALLBACK_BALANCED,
    profile_max_airflow,
)
from .entity import MeltemEntity, room_supports_entity
from .models import MeltemRuntimeData, RoomConfig

DEFAULT_TURN_ON_PERCENTAGE = 50


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
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

    _requires_fresh_read_group = False
    _attr_supported_features = (
        FanEntityFeature.SET_SPEED
        | FanEntityFeature.TURN_OFF
        | FanEntityFeature.TURN_ON
    )

    def __init__(self, coordinator, room: RoomConfig, direction: str) -> None:
        entity_key = f"{direction}_level"
        super().__init__(coordinator, room, entity_key, entity_key)
        self._direction = direction
        self._last_on_percentage: int | None = None
        self._attr_icon = (
            "mdi:home-import-outline" if direction == DIRECTION_SUPPLY else "mdi:home-export-outline"
        )
        # One step per m3/h, so the slider cannot land between device values.
        self._attr_speed_count = int_states_in_range(_level_range(room.profile))

    @property
    def extra_state_attributes(self) -> dict[str, str | bool | int | None]:
        """Expose where the level comes from and when a write needs a fallback."""

        other_level = self._other_level
        attributes: dict[str, str | bool | int | None] = {
            "opposite_airflow": other_level,
            "opposite_airflow_known": other_level is not None,
            "level_source": self.coordinator.level_source(self.room.key),
        }
        fallback = self.coordinator.level_write_fallback(self.room.key)
        if fallback is not None:
            attributes["last_write_fallback"] = fallback
        elif other_level is None:
            attributes["next_write_fallback"] = LEVEL_WRITE_FALLBACK_BALANCED
        if self.room_state.operation_mode is None:
            attributes["operating_mode_known"] = False
            attributes["fan_write_may_override_mode"] = True
        return attributes

    def _handle_coordinator_update(self) -> None:
        if percentage := self.percentage:
            self._last_on_percentage = percentage
        super()._handle_coordinator_update()

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
            if self.is_on:
                # Re-sending the current level would end a running sensor mode.
                return
            percentage = self._last_on_percentage or DEFAULT_TURN_ON_PERCENTAGE
        if percentage == 0:
            percentage = DEFAULT_TURN_ON_PERCENTAGE
        await self.async_set_percentage(percentage)

    async def async_turn_off(self, **kwargs) -> None:
        await self.async_set_percentage(0)

    async def async_set_percentage(self, percentage: int) -> None:
        normalized = max(0, min(100, int(percentage)))
        # Home Assistant works in percent, the coordinator in m3/h.
        await self.coordinator.async_set_direction_level(
            self.room.key,
            self._direction,
            _percentage_to_level(normalized, self.room.profile),
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
