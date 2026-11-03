"""The units of a gateway, each stored as a config subentry."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from homeassistant.config_entries import ConfigEntry, ConfigSubentry, ConfigSubentryData

from .const import (
    CONF_NAME_IS_DEVICE_NAME,
    CONF_PROFILE,
    CONF_SLAVE,
    CONF_UNIT_KEY,
    SUBENTRY_TYPE_UNIT,
)
from .modbus_helpers import supported_entity_keys_for_profile
from .models import RoomConfig

# Unit facts kept in a subentry's data; the name is the subentry title.
UNIT_DATA_KEYS = (
    CONF_UNIT_KEY,
    CONF_SLAVE,
    CONF_PROFILE,
    "preview",
    "product_id",
    "app_setup_code",
    CONF_NAME_IS_DEVICE_NAME,
)


def unit_data(room: Mapping[str, Any]) -> dict[str, Any]:
    """Return the subentry data of one unit, without its name or unset values.

    Older releases stored more keys per unit, such as its entity keys; the
    entities now follow from the profile, so those are dropped.
    """

    return {key: room[key] for key in UNIT_DATA_KEYS if room.get(key) is not None}


def unit_subentry_data(room: Mapping[str, Any]) -> ConfigSubentryData:
    """Return a new unit subentry; the Modbus address is unique per gateway."""

    return ConfigSubentryData(
        data=unit_data(room),
        subentry_type=SUBENTRY_TYPE_UNIT,
        title=str(room["name"]),
        unique_id=str(room[CONF_SLAVE]),
    )


def unit_subentries(entry: ConfigEntry[Any]) -> list[ConfigSubentry]:
    """Return the unit subentries of an entry, ordered by Modbus address."""

    return sorted(
        entry.get_subentries_of_type(SUBENTRY_TYPE_UNIT),
        key=lambda subentry: int(subentry.data[CONF_SLAVE]),
    )


def unit_rooms(entry: ConfigEntry[Any]) -> dict[int, dict[str, Any]]:
    """Return every unit's data and name by Modbus address."""

    return {
        int(subentry.data[CONF_SLAVE]): {**subentry.data, "name": subentry.title}
        for subentry in unit_subentries(entry)
    }


def room_config(subentry: ConfigSubentry) -> RoomConfig:
    """Build one unit whose entities follow from its profile.

    Deriving them on load keeps older entries complete when a release adds
    entities, without probing the gateway or rewriting the entry.
    """

    data = subentry.data
    profile = str(data[CONF_PROFILE])
    return RoomConfig(
        key=str(data[CONF_UNIT_KEY]),
        name=subentry.title,
        profile=profile,
        slave=int(data[CONF_SLAVE]),
        preview=data.get("preview"),
        product_id=int(data["product_id"]) if data.get("product_id") is not None else None,
        app_setup_code=(
            str(data["app_setup_code"]) if data.get("app_setup_code") is not None else None
        ),
        supported_entity_keys=frozenset(supported_entity_keys_for_profile(profile)),
        subentry_id=subentry.subentry_id,
        name_is_device_name=data.get(CONF_NAME_IS_DEVICE_NAME) is True,
    )
