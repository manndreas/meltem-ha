"""Tests for the repair issue about units that are not set up."""

from __future__ import annotations

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir
from pytest_homeassistant.common import MockConfigEntry

from custom_components.meltem_ventilation.const import DOMAIN
from custom_components.meltem_ventilation.new_units import NewUnitIssue


@pytest.fixture(name="entry")
def entry_fixture(hass: HomeAssistant) -> MockConfigEntry:
    entry = MockConfigEntry(domain=DOMAIN, title="Meltem", source="user")
    entry.add_to_hass(hass)
    return entry


def _issue(hass: HomeAssistant, entry: MockConfigEntry) -> ir.IssueEntry | None:
    return ir.async_get(hass).async_get_issue(DOMAIN, f"new_units_{entry.entry_id}")


async def test_units_that_are_not_set_up_raise_one_issue(
    hass: HomeAssistant, entry: MockConfigEntry
) -> None:
    issue = NewUnitIssue(hass, entry.entry_id)

    issue.update([5, 2, 3], configured=[2])

    raised = _issue(hass, entry)
    assert raised is not None
    assert raised.translation_key == "new_units_found"
    assert raised.translation_placeholders == {"count": "2", "addresses": "3, 5"}
    assert raised.severity is ir.IssueSeverity.WARNING
    assert raised.config_entry_id == entry.entry_id
    assert not raised.is_fixable
    assert not raised.is_persistent
    assert issue.new_units == (3, 5)


async def test_the_issue_follows_the_latest_unit_list(
    hass: HomeAssistant, entry: MockConfigEntry
) -> None:
    issue = NewUnitIssue(hass, entry.entry_id)
    assert issue.new_units == ()
    issue.update([2, 3], configured=[2])
    issue.update([2, 3], configured=[2])

    issue.update([2, 3, 4], configured=[2, 3])
    raised = _issue(hass, entry)
    assert raised is not None
    assert raised.translation_placeholders == {"count": "1", "addresses": "4"}

    issue.update([2, 3, 4], configured=[2, 3, 4])
    assert _issue(hass, entry) is None
    assert issue.new_units == ()


async def test_the_first_check_removes_an_issue_of_an_earlier_run(
    hass: HomeAssistant, entry: MockConfigEntry
) -> None:
    NewUnitIssue(hass, entry.entry_id).update([3], configured=[])

    # After a reload, for example once the unit was added.
    NewUnitIssue(hass, entry.entry_id).update([3], configured=[3])

    assert _issue(hass, entry) is None


async def test_an_ignored_issue_stays_ignored_after_a_reload(
    hass: HomeAssistant, entry: MockConfigEntry
) -> None:
    NewUnitIssue(hass, entry.entry_id).update([3], configured=[])
    ir.async_ignore_issue(hass, DOMAIN, f"new_units_{entry.entry_id}", True)

    NewUnitIssue(hass, entry.entry_id).update([3], configured=[])

    raised = _issue(hass, entry)
    assert raised is not None
    assert raised.dismissed_version is not None


async def test_restore_drops_units_that_were_configured_before_reload(
    hass: HomeAssistant, entry: MockConfigEntry
) -> None:
    NewUnitIssue(hass, entry.entry_id).restore([2])
    assert _issue(hass, entry) is None

    NewUnitIssue(hass, entry.entry_id).update([3, 5], configured=[2])
    ir.async_ignore_issue(hass, DOMAIN, f"new_units_{entry.entry_id}", True)

    restored = NewUnitIssue(hass, entry.entry_id)
    restored.restore([2, 3])
    raised = _issue(hass, entry)
    assert raised is not None
    assert raised.translation_placeholders == {"count": "1", "addresses": "5"}
    assert raised.dismissed_version is not None
    assert restored.new_units == (5,)

    NewUnitIssue(hass, entry.entry_id).restore([2, 3, 5])
    assert _issue(hass, entry) is None


async def test_removing_the_entry_removes_the_issue(
    hass: HomeAssistant, entry: MockConfigEntry
) -> None:
    NewUnitIssue(hass, entry.entry_id).update([3], configured=[])

    await hass.config_entries.async_remove(entry.entry_id)

    assert _issue(hass, entry) is None
