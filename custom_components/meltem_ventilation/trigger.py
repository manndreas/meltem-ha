"""Purpose-specific automation triggers for Meltem units."""

from homeassistant.const import STATE_ON
from homeassistant.core import HomeAssistant
from homeassistant.helpers.trigger import EntityTargetStateTriggerBase, Trigger

from .automation import FILTER_CHANGE_DUE_DOMAIN_SPECS, filter_change_due_entities


class FilterChangeDueTrigger(EntityTargetStateTriggerBase):
    """Trigger when a selected Meltem unit reports a filter change due."""

    _domain_specs = FILTER_CHANGE_DUE_DOMAIN_SPECS
    _to_states = {STATE_ON}

    def entity_filter(self, entities: set[str]) -> set[str]:
        """Restrict targets to the Meltem filter status entity."""

        return filter_change_due_entities(self._hass, super().entity_filter(entities))


TRIGGERS: dict[str, type[Trigger]] = {
    "filter_change_due": FilterChangeDueTrigger,
}


async def async_get_triggers(hass: HomeAssistant) -> dict[str, type[Trigger]]:
    """Return the triggers provided by the Meltem integration."""

    return TRIGGERS