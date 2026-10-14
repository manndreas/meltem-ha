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

from .const import CO2_PROFILES, CONTROL_SETTING_LIMITS, HUMIDITY_PROFILES
from .coordinator import MeltemDataUpdateCoordinator
from .entity import MeltemEntity, room_supports_entity
from .models import (
    MeltemIntegrationConfigEntry,
    MeltemRuntimeData,
    RoomConfig,
    RoomState,
)


@dataclass(frozen=True, kw_only=True)
class MeltemControlSettingNumberDescription(NumberEntityDescription):
    """Describe one writable humidity/CO2 control setting."""

    supported_profiles: frozenset[str]
    value_fn: Callable[[RoomState], int | None]


def _control_setting(
    key: str,
    supported_profiles: frozenset[str],
    unit: str = PERCENTAGE,
) -> MeltemControlSettingNumberDescription:
    """Describe one control setting within the manufacturer's limits."""

    minimum, maximum, step = CONTROL_SETTING_LIMITS[key]
    return MeltemControlSettingNumberDescription(
        key=key,
        native_min_value=minimum,
        native_max_value=maximum,
        native_step=step,
        native_unit_of_measurement=unit,
        supported_profiles=supported_profiles,
        # Setting keys double as RoomState field names.
        value_fn=attrgetter(key),
    )


CONTROL_SETTING_DESCRIPTIONS: tuple[MeltemControlSettingNumberDescription, ...] = (
    _control_setting("humidity_starting_point", HUMIDITY_PROFILES),
    _control_setting("humidity_min_level", HUMIDITY_PROFILES),
    _control_setting("humidity_max_level", HUMIDITY_PROFILES),
    _control_setting("co2_starting_point", CO2_PROFILES, UnitOfRatio.PARTS_PER_MILLION),
    _control_setting("co2_min_level", CO2_PROFILES),
    _control_setting("co2_max_level", CO2_PROFILES),
)


# The coordinator serializes all gateway access itself.
PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: MeltemIntegrationConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up Meltem number entities."""

    runtime_data: MeltemRuntimeData = entry.runtime_data
    coordinator = runtime_data.coordinator
    async_add_entities(
        MeltemControlSettingNumber(coordinator, room, description)
        for room in coordinator.rooms
        for description in CONTROL_SETTING_DESCRIPTIONS
        if room_supports_entity(room, description.key, description.supported_profiles)
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
        super().__init__(coordinator, room, description.key, description.key)
        self.entity_description = description

    @property
    def native_value(self) -> float | None:
        value = self.entity_description.value_fn(self.room_state)
        return float(value) if value is not None else None

    async def async_set_native_value(self, value: float) -> None:
        await self.coordinator.async_set_control_setting(
            self.room.key, self.entity_description.key, round(value)
        )
