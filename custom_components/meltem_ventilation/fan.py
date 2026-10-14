"""Fan entities for Meltem ventilation units.

Each unit exposes one fan per air direction. Equal levels run the unit
balanced from a single register; different levels switch it to unbalanced
mode with separate supply and extract targets.
"""

# Home Assistant types entity state as cached_property; like Home Assistant Core,
# these entities override it with plain properties computed from coordinator data.
# pyright: reportIncompatibleVariableOverride=false
from __future__ import annotations

import math
from typing import Any

from homeassistant.components.fan import FanEntity
from homeassistant.components.fan.const import FanEntityFeature
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
from .coordinator import MeltemDataUpdateCoordinator
from .entity import MeltemEntity, room_supports_entity
from .models import MeltemIntegrationConfigEntry, MeltemRuntimeData, RoomConfig

DEFAULT_TURN_ON_PERCENTAGE = 50

# The coordinator serializes all gateway access itself.
PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: MeltemIntegrationConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up Meltem fan entities."""

    runtime_data: MeltemRuntimeData = entry.runtime_data
    coordinator = runtime_data.coordinator
    async_add_entities(
        MeltemDirectionalFanEntity(coordinator, room, direction)
        for room in coordinator.rooms
        for direction in (DIRECTION_SUPPLY, DIRECTION_EXTRACT)
        if room_supports_entity(room, f"{direction}_level")
    )


class MeltemDirectionalFanEntity(MeltemEntity, FanEntity):
    """Airflow target for one direction of a Meltem unit."""

    _requires_fresh_read_group = False
    _attr_supported_features = (
        FanEntityFeature.SET_SPEED | FanEntityFeature.TURN_OFF | FanEntityFeature.TURN_ON
    )

    def __init__(
        self,
        coordinator: MeltemDataUpdateCoordinator,
        room: RoomConfig,
        direction: str,
    ) -> None:
        entity_key = f"{direction}_level"
        super().__init__(coordinator, room, entity_key, entity_key)
        self._direction = direction
        self._last_on_percentage: int | None = None
        self._level_range = (1, profile_max_airflow(room.profile))
        # One step per m3/h, so the slider cannot land between device values.
        self._attr_speed_count = int_states_in_range(self._level_range)

    @property
    def extra_state_attributes(self) -> dict[str, str | bool | int | None]:
        """Expose where the level comes from and when a write needs a fallback."""

        _own_level, other_level = self._directional_levels()
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

    def _directional_levels(self) -> tuple[int | None, int | None]:
        """Return this fan's level and the one of the opposite direction."""

        supply, extract = self.coordinator.effective_levels(self.room.key)
        if self._direction == DIRECTION_SUPPLY:
            return supply, extract
        return extract, supply

    @property
    def is_on(self) -> bool | None:
        percentage = self.percentage
        return None if percentage is None else percentage > 0

    @property
    def percentage(self) -> int | None:
        level, _other_level = self._directional_levels()
        if level is None:
            return None
        if level <= 0:
            return 0
        # Measured airflow can exceed the rated maximum, e.g. during intensive
        # ventilation or when the configured profile understates the unit.
        return min(100, ranged_value_to_percentage(self._level_range, level))

    async def async_turn_on(
        self,
        percentage: int | None = None,
        preset_mode: str | None = None,
        **kwargs: Any,
    ) -> None:
        if percentage is None:
            if self.is_on:
                # Re-sending the current level would end a running sensor mode.
                return
            percentage = self._last_on_percentage
        await self.async_set_percentage(percentage or DEFAULT_TURN_ON_PERCENTAGE)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.async_set_percentage(0)

    async def async_set_percentage(self, percentage: int) -> None:
        percentage = max(0, min(100, int(percentage)))
        # Home Assistant works in percent, the coordinator in m3/h.
        level = (
            math.ceil(percentage_to_ranged_value(self._level_range, percentage))
            if percentage > 0
            else 0
        )
        await self.coordinator.async_set_direction_level(self.room.key, self._direction, level)
