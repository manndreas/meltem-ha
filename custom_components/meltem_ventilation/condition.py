"""Purpose-specific automation conditions for Meltem units."""

from homeassistant.const import STATE_ON
from homeassistant.core import HomeAssistant
from homeassistant.helpers.condition import Condition, EntityStateConditionBase

from .automation import FILTER_CHANGE_DUE_DOMAIN_SPECS, filter_change_due_entities


class FilterChangeDueCondition(EntityStateConditionBase):
    """Check whether a selected Meltem unit reports a filter change due."""

    _domain_specs = FILTER_CHANGE_DUE_DOMAIN_SPECS
    _states = {STATE_ON}

    def entity_filter(self, entities: set[str]) -> set[str]:
        """Restrict targets to the Meltem filter status entity."""

        return filter_change_due_entities(self._hass, super().entity_filter(entities))


CONDITIONS: dict[str, type[Condition]] = {
    "filter_change_due": FilterChangeDueCondition,
}


async def async_get_conditions(hass: HomeAssistant) -> dict[str, type[Condition]]:
    """Return the conditions provided by the Meltem integration."""

    return CONDITIONS