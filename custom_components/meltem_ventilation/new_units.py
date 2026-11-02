"""Repair issue for units the gateway reports but Home Assistant does not have yet."""

from __future__ import annotations

from collections.abc import Iterable

from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir

from .const import DOMAIN

# The gateway's unit list changes only when units are paired in the Meltem app,
# so an hourly check is enough; the first one waits until startup reads settle.
NEW_UNIT_CHECK_SECONDS = 3600.0
NEW_UNIT_CHECK_FIRST_DELAY_SECONDS = 300.0


class NewUnitIssue:
    """Raise one repair issue while the gateway reports units that are not set up.

    The units cannot be added without the user, since the profile on the type
    plate decides their entities. The issue outlives a reload, since deleting
    it would also forget that the user ignored it; the first check after a
    start updates or removes it, and Home Assistant removes it together with
    the config entry.
    """

    def __init__(self, hass: HomeAssistant, entry_id: str) -> None:
        self._hass = hass
        self._entry_id = entry_id
        self._issue_id = f"new_units_{entry_id}"
        # ``None`` until the first check, which settles an issue of an earlier run.
        self._reported: tuple[int, ...] | None = None

    @property
    def new_units(self) -> tuple[int, ...]:
        """Return the addresses the current issue reports."""

        return self._reported or ()

    def update(self, reported: Iterable[int], configured: Iterable[int]) -> None:
        """Raise, update, or clear the issue for the latest unit list of the gateway."""

        new_units = tuple(sorted(set(reported) - set(configured)))
        if new_units == self._reported:
            return
        self._reported = new_units
        if not new_units:
            ir.async_delete_issue(self._hass, DOMAIN, self._issue_id)
            return
        ir.async_create_issue(
            self._hass,
            DOMAIN,
            self._issue_id,
            config_entry_id=self._entry_id,
            is_fixable=False,
            severity=ir.IssueSeverity.WARNING,
            translation_key="new_units_found",
            translation_placeholders={
                "count": str(len(new_units)),
                "addresses": ", ".join(str(address) for address in new_units),
            },
        )
