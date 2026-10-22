"""Persistent counters for cumulative Meltem communication diagnostics."""

from __future__ import annotations

import logging
import time
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

from .const import DOMAIN
from .device.transport import RequestOutcome

_LOGGER = logging.getLogger(__name__)

STORAGE_VERSION = 1
STORAGE_KEY_PREFIX = f"{DOMAIN}_cumulative_diagnostics"
SAVE_DELAY_SECONDS = 10.0
SAVE_INTERVAL_SECONDS = 60.0
REQUEST_FAILURE_CATEGORIES = ("no_answer", "modbus_exception", "other")
WRITE_ISSUE_CATEGORIES = {
    "failed": "send_failure",
    "mismatch": "readback_mismatch",
    "unconfirmed": "unconfirmed",
}


class CumulativeDiagnostics:
    """Track diagnostics for one config entry and persist them in HA storage."""

    def __init__(self, store: Store[dict[str, Any]], stored: Mapping[str, Any] | None) -> None:
        self._store = store
        self._last_save_scheduled_at: float | None = None
        data = stored if isinstance(stored, Mapping) else {}
        self._period_started_at = self._valid_timestamp(data.get("period_started_at"))

        transport = data.get("transport")
        if not isinstance(transport, Mapping):
            transport = {}
        self._request_attempts = _count(transport.get("request_attempts"))
        stored_failures = transport.get("request_failures")
        if not isinstance(stored_failures, Mapping):
            stored_failures = {}
        self._request_failures = {
            category: _count(stored_failures.get(category))
            for category in REQUEST_FAILURE_CATEGORIES
        }
        self._link_recycles = _count(transport.get("link_recycles"))
        self._read_failures = _nested_counters(data.get("read_failures"))
        self._write_issues = _nested_counters(data.get("write_issues"), depth=3)

    @classmethod
    async def async_create(cls, hass: HomeAssistant, entry_id: str) -> CumulativeDiagnostics:
        """Load persisted counters, tolerating missing or malformed stored data."""

        store: Store[dict[str, Any]] = Store(
            hass,
            STORAGE_VERSION,
            f"{STORAGE_KEY_PREFIX}_{entry_id}",
        )
        try:
            stored = await store.async_load()
        except Exception:
            _LOGGER.exception("Could not load cumulative Meltem diagnostics; starting a new period")
            stored = None
        if stored is not None and not isinstance(stored, Mapping):
            _LOGGER.warning("Ignoring malformed cumulative Meltem diagnostics")
            stored = None
        return cls(store, stored)

    @classmethod
    async def async_remove(cls, hass: HomeAssistant, entry_id: str) -> None:
        """Remove the persisted counters when their config entry is deleted."""

        store: Store[dict[str, Any]] = Store(
            hass,
            STORAGE_VERSION,
            f"{STORAGE_KEY_PREFIX}_{entry_id}",
        )
        await store.async_remove()

    @property
    def period_started_at(self) -> str:
        """Return the start of the persisted counter period in UTC."""

        return self._period_started_at

    @property
    def request_attempts(self) -> int:
        return self._request_attempts

    @property
    def request_failures(self) -> int:
        return sum(self._request_failures.values())

    @property
    def link_recycles(self) -> int:
        return self._link_recycles

    @property
    def failed_read_groups(self) -> int:
        return sum(
            count for room_groups in self._read_failures.values() for count in room_groups.values()
        )

    @property
    def write_issues(self) -> int:
        return sum(
            count
            for room_categories in self._write_issues.values()
            for write_keys in room_categories.values()
            for count in write_keys.values()
        )

    def record_request(self, outcome: RequestOutcome) -> None:
        """Record one completed transport attempt, including retries."""

        self._request_attempts += 1
        if outcome != "success":
            category = outcome if outcome in self._request_failures else "other"
            self._request_failures[category] += 1
        self._schedule_save()

    def record_link_recycle(self) -> None:
        """Record one forced transport-link recycle."""

        self._link_recycles += 1
        self._schedule_save()

    def record_read_failure(self, room_key: str, group_key: str) -> None:
        """Record one failed room read group after its retry policy completed."""

        self._increment_nested(self._read_failures, room_key, group_key)
        self._schedule_save()

    def record_write_issue(self, room_key: str, write_key: str, status: str) -> None:
        """Record one first-seen problematic command outcome."""

        category = WRITE_ISSUE_CATEGORIES.get(status)
        if category is None:
            return
        room_issues = self._write_issues.setdefault(room_key, {})
        category_issues = room_issues.setdefault(category, {})
        category_issues[write_key] = category_issues.get(write_key, 0) + 1
        self._schedule_save()

    def sensor_value(self, key: str) -> int:
        """Return a cumulative total for a diagnostic sensor key."""

        return {
            "gateway_modbus_attempts_total": self.request_attempts,
            "gateway_modbus_failures_total": self.request_failures,
            "gateway_link_recycles_total": self.link_recycles,
            "gateway_failed_read_groups_total": self.failed_read_groups,
            "gateway_write_issues_total": self.write_issues,
        }[key]

    def sensor_attributes(self, key: str) -> dict[str, object]:
        """Return the breakdown and period metadata for a diagnostic sensor."""

        attributes: dict[str, object] = {"period_started_at": self._period_started_at}
        if key in ("gateway_modbus_attempts_total", "gateway_modbus_failures_total"):
            attributes["failures_by_category"] = dict(self._request_failures)
            attributes["successful_attempts"] = self._request_attempts - self.request_failures
        elif key == "gateway_failed_read_groups_total":
            attributes["by_room_and_group"] = _copy_nested(self._read_failures)
        elif key == "gateway_write_issues_total":
            attributes["by_room_category_and_command"] = _copy_nested(self._write_issues)
        return attributes

    def diagnostics(self) -> dict[str, object]:
        """Return a detached snapshot for the diagnostics download."""

        return {
            "period_started_at": self._period_started_at,
            "transport": {
                "request_attempts": self._request_attempts,
                "request_failures": self.request_failures,
                "request_failures_by_category": dict(self._request_failures),
                "successful_attempts": self._request_attempts - self.request_failures,
                "link_recycles": self._link_recycles,
            },
            "read_failures": _copy_nested(self._read_failures),
            "write_issues": _copy_nested(self._write_issues),
        }

    async def async_flush(self) -> None:
        """Persist the latest snapshot during config-entry unload."""

        await self._store.async_save(self._stored_data())
        self._last_save_scheduled_at = time.monotonic()

    def _schedule_save(self) -> None:
        now = time.monotonic()
        if (
            self._last_save_scheduled_at is not None
            and now - self._last_save_scheduled_at < SAVE_INTERVAL_SECONDS
        ):
            return
        self._last_save_scheduled_at = now
        self._store.async_delay_save(self._stored_data, SAVE_DELAY_SECONDS)

    def _stored_data(self) -> dict[str, Any]:
        return {
            "period_started_at": self._period_started_at,
            "transport": {
                "request_attempts": self._request_attempts,
                "request_failures": dict(self._request_failures),
                "link_recycles": self._link_recycles,
            },
            "read_failures": _copy_nested(self._read_failures),
            "write_issues": _copy_nested(self._write_issues),
        }

    def _increment_nested(self, counters: dict[str, Any], *keys: str) -> None:
        current = counters
        for key in keys[:-1]:
            current = current.setdefault(key, {})
        current[keys[-1]] = current.get(keys[-1], 0) + 1

    @staticmethod
    def _valid_timestamp(value: object) -> str:
        if isinstance(value, str):
            try:
                parsed = datetime.fromisoformat(value)
            except ValueError:
                pass
            else:
                if parsed.tzinfo is not None:
                    return parsed.astimezone(UTC).isoformat()
        return datetime.now(UTC).isoformat()


def _count(value: object) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else 0


def _nested_counters(value: object, *, depth: int = 2) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        return {}
    counters: dict[str, Any] = {}
    for key, item in value.items():
        if not isinstance(key, str):
            continue
        if depth == 1:
            count = _count(item)
            if count:
                counters[key] = count
        else:
            nested = _nested_counters(item, depth=depth - 1)
            if nested:
                counters[key] = nested
    return counters


def _copy_nested(value: Mapping[str, Any]) -> dict[str, Any]:
    return {
        key: _copy_nested(item) if isinstance(item, Mapping) else item
        for key, item in value.items()
    }
