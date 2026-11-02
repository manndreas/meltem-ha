"""Repair issues for sensors that a unit's profile expects but the unit never reports."""

from __future__ import annotations

from collections.abc import Callable, Iterable

from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir

from .const import DOMAIN, profile_label
from .modbus_client import placeholder_sensors
from .models import ReadJobOutcome, RoomConfig

# Consecutive reads with a "no sensor" placeholder before an issue is raised;
# the environment values are read once a minute, so this is about three minutes.
MISSING_SENSOR_ISSUE_AFTER_READS = 3


class MissingSensorIssues:
    """Raise one repair issue per unit and sensor that keeps reporting no value.

    The issues outlive a reload, since deleting one would also forget that the
    user ignored it; Home Assistant removes them together with the config entry.
    """

    def __init__(
        self, hass: HomeAssistant, entry_id: str, unit_name: Callable[[RoomConfig], str]
    ) -> None:
        self._hass = hass
        self._entry_id = entry_id
        self._unit_name = unit_name
        self._missing_reads: dict[tuple[str, str], int] = {}
        self._raised: set[tuple[str, str]] = set()

    @property
    def raised(self) -> frozenset[tuple[str, str]]:
        """Return the ``(room key, sensor)`` pairs that currently have an issue."""

        return frozenset(self._raised)

    def restore(self, rooms: Iterable[RoomConfig]) -> None:
        """Take over the issues of an earlier run and drop the ones that no longer apply.

        An issue of a unit or sensor that is no longer configured, for example
        after a profile change, is deleted. A shown issue whose sensor the unit
        still has stays until a reading decides about it.
        """

        expected = {
            self._issue_id((room.key, sensor)): (room.key, sensor)
            for room in rooms
            for sensor in placeholder_sensors(room)
        }
        prefix = f"missing_sensor_{self._entry_id}_"
        registry = ir.async_get(self._hass)
        for (domain, issue_id), issue in tuple(registry.issues.items()):
            if domain != DOMAIN or not issue_id.startswith(prefix):
                continue
            if (key := expected.get(issue_id)) is None:
                ir.async_delete_issue(self._hass, DOMAIN, issue_id)
            elif issue.active:
                self._raised.add(key)
                self._missing_reads[key] = MISSING_SENSOR_ISSUE_AFTER_READS

    def record(self, room: RoomConfig, outcome: ReadJobOutcome) -> None:
        """Count placeholder readings and raise or clear the matching issues."""

        for sensor in outcome.sensor_values:
            key = (room.key, sensor)
            self._missing_reads.pop(key, None)
            if key in self._raised:
                self._raised.discard(key)
                ir.async_delete_issue(self._hass, DOMAIN, self._issue_id(key))
        for sensor in outcome.missing_sensor_values:
            key = (room.key, sensor)
            count = self._missing_reads[key] = self._missing_reads.get(key, 0) + 1
            if count >= MISSING_SENSOR_ISSUE_AFTER_READS and key not in self._raised:
                self._raised.add(key)
                ir.async_create_issue(
                    self._hass,
                    DOMAIN,
                    self._issue_id(key),
                    config_entry_id=self._entry_id,
                    is_fixable=False,
                    severity=ir.IssueSeverity.WARNING,
                    translation_key=f"missing_{sensor}",
                    translation_placeholders={
                        "unit": self._unit_name(room),
                        "profile": profile_label(room.profile),
                    },
                )

    def _issue_id(self, key: tuple[str, str]) -> str:
        room_key, sensor = key
        return f"missing_sensor_{self._entry_id}_{room_key}_{sensor}"
