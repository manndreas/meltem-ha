"""Writable number entities for Meltem units.

Each write is read back from the gateway before the call returns, so the
entity shows the value the unit actually stored after step normalization.
"""

# Home Assistant types entity state as cached_property; like Home Assistant Core,
# these entities override it with plain properties computed from coordinator data.
# pyright: reportIncompatibleVariableOverride=false
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from operator import attrgetter

from homeassistant.components.number import (
    NumberEntity,
    NumberEntityDescription,
    NumberMode,
)
from homeassistant.const import (
    PERCENTAGE,
    EntityCategory,
    UnitOfRatio,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import CONTROL_SETTING_LIMITS
from .coordinator import MeltemDataUpdateCoordinator
from .entity import MeltemEntity, async_add_room_entities, room_has_entity
from .models import (
    MeltemIntegrationConfigEntry,
    RoomConfig,
    RoomState,
)


@dataclass(frozen=True, kw_only=True)
class MeltemControlSettingNumberDescription(NumberEntityDescription):
    """Describe one writable humidity/CO2 control setting."""

    value_fn: Callable[[RoomState], int | None]


def _control_setting(key: str, unit: str = PERCENTAGE) -> MeltemControlSettingNumberDescription:
    """Describe one control setting within the manufacturer's limits."""

    minimum, maximum, step = CONTROL_SETTING_LIMITS[key]
    return MeltemControlSettingNumberDescription(
        key=key,
        native_min_value=minimum,
        native_max_value=maximum,
        native_step=step,
        native_unit_of_measurement=unit,
        # Setting keys double as RoomState field names.
        value_fn=attrgetter(key),
    )


CONTROL_SETTING_DESCRIPTIONS: tuple[MeltemControlSettingNumberDescription, ...] = (
    _control_setting("humidity_starting_point"),
    _control_setting("humidity_min_level"),
    _control_setting("humidity_max_level"),
    _control_setting("co2_starting_point", UnitOfRatio.PARTS_PER_MILLION),
    _control_setting("co2_min_level"),
    _control_setting("co2_max_level"),
)


# The coordinator serializes all gateway access itself.
PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: MeltemIntegrationConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up Meltem number entities."""

    coordinator = entry.runtime_data.coordinator
    async_add_room_entities(
        coordinator,
        async_add_entities,
        lambda room: (
            MeltemControlSettingNumber(coordinator, room, description)
            for description in CONTROL_SETTING_DESCRIPTIONS
            if room_has_entity(room, description.key)
        ),
    )


class MeltemControlSettingNumber(MeltemEntity, NumberEntity):
    """Writable config number for humidity/CO2 automation thresholds."""

    entity_description: MeltemControlSettingNumberDescription
    _attr_entity_category = EntityCategory.CONFIG
    _attr_mode = NumberMode.BOX

    def __init__(
        self,
        coordinator: MeltemDataUpdateCoordinator,
        room: RoomConfig,
        description: MeltemControlSettingNumberDescription,
    ) -> None:
        super().__init__(coordinator, room, description.key)
        self.entity_description = description

    @property
    def native_value(self) -> float | None:
        value = self.entity_description.value_fn(self.room_state)
        return float(value) if value is not None else None

    async def async_set_native_value(self, value: float) -> None:
        await self.coordinator.async_set_control_setting(
            self.room.key, self.entity_description.key, round(value)
        )
