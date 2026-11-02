"""Tests for the repair issues of sensors that report no value."""

from __future__ import annotations

from dataclasses import replace

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir
from pytest_homeassistant.common import MockConfigEntry

from custom_components.meltem_ventilation.const import DOMAIN
from custom_components.meltem_ventilation.missing_sensors import (
    MISSING_SENSOR_ISSUE_AFTER_READS,
    MissingSensorIssues,
)
from custom_components.meltem_ventilation.models import ReadJobOutcome, RoomConfig

_ROOM = RoomConfig(key="unit_1", name="Unit 1", profile="ii_fc", slave=2)
_OTHER = RoomConfig(key="unit_2", name="Unit 2", profile="ii_f", slave=3)
_MISSING_CO2 = ReadJobOutcome(
    missing_sensor_values=frozenset({"co2_extract_air"}),
    sensor_values=frozenset({"humidity_extract_air"}),
)
_CO2_BACK = ReadJobOutcome(sensor_values=frozenset({"co2_extract_air"}))
_MISSING_HUMIDITY = ReadJobOutcome(missing_sensor_values=frozenset({"humidity_supply_air"}))
_ENTRY_ID = "entry"


@pytest.fixture(autouse=True)
def _entry(hass: HomeAssistant) -> MockConfigEntry:
    entry = MockConfigEntry(domain=DOMAIN, title="Meltem", source="user", entry_id=_ENTRY_ID)
    entry.add_to_hass(hass)
    return entry


def _issue(hass: HomeAssistant, room_key: str, sensor: str) -> ir.IssueEntry | None:
    return ir.async_get(hass).async_get_issue(
        DOMAIN, f"missing_sensor_{_ENTRY_ID}_{room_key}_{sensor}"
    )


def _issues(hass: HomeAssistant) -> MissingSensorIssues:
    return MissingSensorIssues(hass, _ENTRY_ID, lambda room: f"Bathroom {room.slave}")


def _raise_both(issues: MissingSensorIssues) -> None:
    for _ in range(MISSING_SENSOR_ISSUE_AFTER_READS):
        issues.record(_ROOM, _MISSING_CO2)
        issues.record(_OTHER, _MISSING_HUMIDITY)


async def test_issue_is_raised_after_repeated_placeholders(hass: HomeAssistant) -> None:
    issues = _issues(hass)

    for _ in range(MISSING_SENSOR_ISSUE_AFTER_READS - 1):
        issues.record(_ROOM, _MISSING_CO2)
    assert _issue(hass, "unit_1", "co2_extract_air") is None

    issues.record(_ROOM, _MISSING_CO2)

    issue = _issue(hass, "unit_1", "co2_extract_air")
    assert issue is not None
    assert issue.translation_key == "missing_co2_extract_air"
    assert issue.translation_placeholders == {"unit": "Bathroom 2", "profile": "M-WRG-II (-FC)"}
    assert issue.severity is ir.IssueSeverity.WARNING
    assert issue.config_entry_id == _ENTRY_ID
    assert not issue.is_fixable
    assert not issue.is_persistent
    assert issues.raised == {("unit_1", "co2_extract_air")}
    assert _issue(hass, "unit_1", "humidity_extract_air") is None


async def test_a_value_resets_the_count_and_clears_the_issue(hass: HomeAssistant) -> None:
    issues = _issues(hass)
    for _ in range(MISSING_SENSOR_ISSUE_AFTER_READS - 1):
        issues.record(_ROOM, _MISSING_CO2)
    issues.record(_ROOM, _CO2_BACK)
    issues.record(_ROOM, _MISSING_CO2)
    assert _issue(hass, "unit_1", "co2_extract_air") is None

    for _ in range(MISSING_SENSOR_ISSUE_AFTER_READS):
        issues.record(_ROOM, _MISSING_CO2)
    assert _issue(hass, "unit_1", "co2_extract_air") is not None

    issues.record(_ROOM, _CO2_BACK)
    assert _issue(hass, "unit_1", "co2_extract_air") is None
    assert issues.raised == frozenset()


async def test_restore_keeps_issues_that_still_apply(hass: HomeAssistant) -> None:
    _raise_both(_issues(hass))
    ir.async_ignore_issue(hass, DOMAIN, f"missing_sensor_{_ENTRY_ID}_unit_1_co2_extract_air", True)

    # After a reload with the same units and profiles.
    issues = _issues(hass)
    issues.restore([_ROOM, _OTHER])

    assert issues.raised == {("unit_1", "co2_extract_air"), ("unit_2", "humidity_supply_air")}
    ignored = _issue(hass, "unit_1", "co2_extract_air")
    assert ignored is not None
    assert ignored.dismissed_version is not None
    # Another placeholder does not raise the issue again ...
    issues.record(_ROOM, _MISSING_CO2)
    assert _issue(hass, "unit_1", "co2_extract_air") == ignored
    # ... but a reading clears it.
    issues.record(_ROOM, _CO2_BACK)
    assert _issue(hass, "unit_1", "co2_extract_air") is None


async def test_restore_drops_issues_of_removed_units_and_sensors(hass: HomeAssistant) -> None:
    _raise_both(_issues(hass))
    unrelated = ("other_domain", "missing_sensor_entry_unit_1_co2_extract_air")
    ir.async_create_issue(
        hass,
        unrelated[0],
        unrelated[1],
        is_fixable=False,
        severity=ir.IssueSeverity.WARNING,
        translation_key="other",
    )

    # unit_1 now has a profile without CO2, and unit_2 was removed.
    issues = _issues(hass)
    issues.restore([RoomConfig(key="unit_1", name="Unit 1", profile="ii_f", slave=2)])

    assert issues.raised == frozenset()
    assert _issue(hass, "unit_1", "co2_extract_air") is None
    assert _issue(hass, "unit_2", "humidity_supply_air") is None
    assert ir.async_get(hass).async_get_issue(*unrelated) is not None


async def test_restore_leaves_inactive_issues_to_the_next_readings(hass: HomeAssistant) -> None:
    _raise_both(_issues(hass))
    registry = ir.async_get(hass)
    issue_id = f"missing_sensor_{_ENTRY_ID}_unit_1_co2_extract_air"
    # Home Assistant restores issues of an earlier start as inactive.
    registry.issues[(DOMAIN, issue_id)] = replace(registry.issues[(DOMAIN, issue_id)], active=False)

    issues = _issues(hass)
    issues.restore([_ROOM])

    assert issues.raised == frozenset()
    for _ in range(MISSING_SENSOR_ISSUE_AFTER_READS):
        issues.record(_ROOM, _MISSING_CO2)
    issue = _issue(hass, "unit_1", "co2_extract_air")
    assert issue is not None
    assert issue.active


async def test_removing_the_entry_removes_the_issues(
    hass: HomeAssistant, _entry: MockConfigEntry
) -> None:
    _raise_both(_issues(hass))

    await hass.config_entries.async_remove(_entry.entry_id)

    assert _issue(hass, "unit_1", "co2_extract_air") is None
    assert _issue(hass, "unit_2", "humidity_supply_air") is None
