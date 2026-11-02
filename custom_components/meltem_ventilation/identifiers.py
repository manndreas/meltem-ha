"""Stable identifiers of the gateway, the units, and their entities."""

from __future__ import annotations

from .const import DOMAIN
from .models import RoomConfig

APP_SETUP_CODE_IDENTIFIER_PREFIX = "app_setup_code:"


def gateway_identifiers(entry_id: str) -> set[tuple[str, str]]:
    """Return the stable identifier set for one gateway."""

    return {(DOMAIN, entry_id)}


def room_identifiers(room: RoomConfig) -> set[tuple[str, str]]:
    """Return the device identifiers of one unit: its address key and its app setup code.

    Home Assistant keeps device identifiers unique per config entry, so they
    need no entry prefix.
    """

    identifiers = {(DOMAIN, room.key)}
    if room.app_setup_code is not None:
        identifiers.add((DOMAIN, f"{APP_SETUP_CODE_IDENTIFIER_PREFIX}{room.app_setup_code}"))
    return identifiers


def gateway_entity_unique_id(entry_id: str, entity_key: str) -> str:
    """Return the unique ID of one gateway entity."""

    return f"{DOMAIN}_{entry_id}_{entity_key}"


def room_entity_unique_id(entry_id: str, room_key: str, entity_key: str) -> str:
    """Return the unique ID of one unit entity.

    Entity unique IDs are unique per integration, not per config entry, so the
    entry ID keeps the units of two gateways apart.
    """

    return f"{DOMAIN}_{entry_id}_{room_key}_{entity_key}"


def legacy_room_entity_unique_id(room_key: str, entity_key: str) -> str:
    """Return the unique ID a unit entity had before config entry version 2."""

    return f"{DOMAIN}_{room_key}_{entity_key}"
