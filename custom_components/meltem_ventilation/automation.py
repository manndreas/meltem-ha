"""Shared helpers for Meltem automation platforms."""

from homeassistant.components.binary_sensor import DOMAIN as BINARY_SENSOR_DOMAIN
from homeassistant.components.binary_sensor import BinarySensorDeviceClass
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.automation import DomainSpec

from .const import DOMAIN

FILTER_CHANGE_DUE_DOMAIN_SPECS: dict[str, DomainSpec] = {
    BINARY_SENSOR_DOMAIN: DomainSpec(
        device_class=BinarySensorDeviceClass.PROBLEM,
    ),
}


def filter_change_due_entities(hass: HomeAssistant, entity_ids: set[str]) -> set[str]:
    """Keep only this integration's filter-change-due binary sensors."""

    registry = er.async_get(hass)
    return {
        entity_id
        for entity_id in entity_ids
        if (entity := registry.async_get(entity_id)) is not None
        and entity.platform == DOMAIN
        and entity.unique_id.endswith("_filter_change_due")
    }