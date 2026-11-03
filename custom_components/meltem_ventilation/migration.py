"""Migrate Meltem config entries from older schemas.

- 1.2 renamed the airflow-only diagnostic to the data-health entity
- 1.3 moved the request rate from ``data`` to ``options``
- 1.4 moved identity values out of the display preview
- 2.1 moved every unit into a config subentry and scoped entity unique IDs by the entry
- 2.2 renamed the unit entity key ``rf_fault_status`` to ``device_fault``
"""

from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType
from typing import Any

from homeassistant.config_entries import ConfigSubentry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er

from .const import (
    CONF_MAX_REQUESTS_PER_SECOND,
    CONF_ROOMS,
    CONF_UNIT_KEY,
    DOMAIN,
    ENTITY_PLATFORM_BY_KEY,
)
from .identifiers import legacy_room_entity_unique_id, room_entity_unique_id
from .models import MeltemIntegrationConfigEntry
from .units import unit_subentry_data

# Unit entity keys renamed in 2.2, mapped to their new key.
RENAMED_ENTITY_KEYS: dict[str, str] = {"rf_fault_status": "device_fault"}


async def async_migrate_entry(hass: HomeAssistant, entry: MeltemIntegrationConfigEntry) -> bool:
    """Migrate a config entry from an older schema."""

    if entry.version > 2:
        return False

    if entry.version == 1:
        if entry.minor_version < 2:
            _async_migrate_data_health_entities(hass, entry)

        if entry.minor_version < 3:
            data = dict(entry.data)
            options = dict(entry.options)
            if CONF_MAX_REQUESTS_PER_SECOND in data:
                options.setdefault(
                    CONF_MAX_REQUESTS_PER_SECOND,
                    data.pop(CONF_MAX_REQUESTS_PER_SECOND),
                )
            hass.config_entries.async_update_entry(
                entry,
                data=data,
                options=options,
                minor_version=3,
            )

        if entry.minor_version < 4:
            data = dict(entry.data)
            data[CONF_ROOMS] = [_migrate_room_identity_fields(room) for room in data[CONF_ROOMS]]
            hass.config_entries.async_update_entry(entry, data=data, minor_version=4)

        _async_migrate_units_to_subentries(hass, entry)

    if entry.minor_version < 2:
        _async_migrate_renamed_entity_keys(hass, entry)

    return True


def _async_migrate_units_to_subentries(
    hass: HomeAssistant, entry: MeltemIntegrationConfigEntry
) -> None:
    """Move every unit into a config subentry and scope its entities by the entry.

    The unit keeps its room key, so its device keeps its identifiers, which
    Home Assistant already keeps unique per config entry. Entity unique IDs are
    unique per integration instead, so they gain the entry ID. The device and
    the entities move to the unit's subentry and keep all user settings.
    """

    entity_registry = er.async_get(hass)
    device_registry = dr.async_get(hass)
    entities = {
        entity.unique_id: entity
        for entity in er.async_entries_for_config_entry(entity_registry, entry.entry_id)
    }
    used_unique_ids: set[str | None] = set()
    for room in entry.data.get(CONF_ROOMS, []):
        room_key = str(room[CONF_UNIT_KEY])
        subentry_data = unit_subentry_data(room)
        if subentry_data["unique_id"] in used_unique_ids:
            subentry_data["unique_id"] = None
        used_unique_ids.add(subentry_data["unique_id"])
        subentry = ConfigSubentry(
            data=MappingProxyType(subentry_data["data"]),
            subentry_type=subentry_data["subentry_type"],
            title=subentry_data["title"],
            unique_id=subentry_data["unique_id"],
        )
        hass.config_entries.async_add_subentry(entry, subentry)
        # Renamed keys keep their old name here; the 2.2 step renames them.
        for entity_key in (*ENTITY_PLATFORM_BY_KEY, *RENAMED_ENTITY_KEYS):
            entity = entities.get(legacy_room_entity_unique_id(room_key, entity_key))
            if entity is not None:
                entity_registry.async_update_entity(
                    entity.entity_id,
                    new_unique_id=room_entity_unique_id(entry.entry_id, room_key, entity_key),
                    config_subentry_id=subentry.subentry_id,
                )
        device = device_registry.async_get_device_by_identifier((DOMAIN, room_key), entry.entry_id)
        if device is not None:
            device_registry.async_update_device(
                device.id,
                new_config_entry_id=entry.entry_id,
                new_config_subentry_id=subentry.subentry_id,
            )
    data = {key: value for key, value in entry.data.items() if key != CONF_ROOMS}
    hass.config_entries.async_update_entry(entry, data=data, version=2, minor_version=1)


def _migrate_room_identity_fields(room: Mapping[str, Any]) -> dict[str, Any]:
    """Move identity values from the legacy display preview to dedicated fields."""

    migrated = dict(room)
    preview = room.get("preview")
    if not isinstance(preview, str):
        return migrated

    for part in preview.split(" | "):
        if part.startswith("ID ") and "product_id" not in migrated:
            try:
                migrated["product_id"] = int(part.removeprefix("ID "))
            except ValueError:
                pass
        elif part.startswith("R-Code ") and "app_setup_code" not in migrated:
            code = part.removeprefix("R-Code ")
            if (
                len(code) == 7
                and code.startswith("R")
                and all(character in "0123456789ABCDEF" for character in code[1:])
            ):
                migrated["app_setup_code"] = code
    return migrated


def _async_migrate_data_health_entities(
    hass: HomeAssistant, entry: MeltemIntegrationConfigEntry
) -> None:
    """Rename the airflow-only diagnostic while preserving registry settings."""

    registry = er.async_get(hass)
    entry_entities = {
        entity.unique_id: entity
        for entity in er.async_entries_for_config_entry(registry, entry.entry_id)
    }
    for room in entry.data[CONF_ROOMS]:
        old_entity = entry_entities.get(f"{DOMAIN}_{room['key']}_airflow_data_stale")
        new_unique_id = f"{DOMAIN}_{room['key']}_data_health"
        if old_entity is None or new_unique_id in entry_entities:
            continue
        registry.async_update_entity(old_entity.entity_id, new_unique_id=new_unique_id)
        entry_entities[new_unique_id] = old_entity


def _async_migrate_renamed_entity_keys(
    hass: HomeAssistant, entry: MeltemIntegrationConfigEntry
) -> None:
    """Give unit entities with a renamed key their new unique ID.

    The entity keeps its entity ID, its history, and all user settings. If an
    entity with the new unique ID already exists, the old one is left for the
    setup cleanup to remove.
    """

    registry = er.async_get(hass)
    entities = er.async_entries_for_config_entry(registry, entry.entry_id)
    unique_ids = {entity.unique_id for entity in entities}
    for entity in entities:
        for old_key, new_key in RENAMED_ENTITY_KEYS.items():
            old_suffix = f"_{old_key}"
            if not entity.unique_id.endswith(old_suffix):
                continue
            new_unique_id = f"{entity.unique_id.removesuffix(old_suffix)}_{new_key}"
            if new_unique_id in unique_ids:
                continue
            registry.async_update_entity(entity.entity_id, new_unique_id=new_unique_id)
            unique_ids.add(new_unique_id)
    hass.config_entries.async_update_entry(entry, minor_version=2)
