"""Tests for persistent cumulative diagnostics."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

from custom_components.meltem_ventilation.const import DOMAIN
from custom_components.meltem_ventilation.cumulative_diagnostics import (
    STORAGE_KEY_PREFIX,
    STORAGE_VERSION,
    CumulativeDiagnostics,
)


async def test_counters_are_restored_after_recreation(hass: HomeAssistant) -> None:
    diagnostics = await CumulativeDiagnostics.async_create(hass, "entry")
    period_started_at = diagnostics.period_started_at
    diagnostics.record_request("success")
    diagnostics.record_request("no_answer")
    diagnostics.record_link_recycle()
    diagnostics.record_read_failure("room", "flow")
    diagnostics.record_write_issue("room", "airflow_levels", "mismatch")
    await diagnostics.async_flush()

    restored = await CumulativeDiagnostics.async_create(hass, "entry")

    assert restored.period_started_at == period_started_at
    assert restored.request_attempts == 2
    assert restored.request_failures == 1
    assert restored.link_recycles == 1
    assert restored.failed_read_groups == 1
    assert restored.write_issues == 1


async def test_invalid_counter_fields_default_to_zero(hass: HomeAssistant) -> None:
    store: Store[dict[str, object]] = Store(
        hass,
        STORAGE_VERSION,
        f"{STORAGE_KEY_PREFIX}_invalid",
    )
    await store.async_save(
        {
            "period_started_at": "not-a-timestamp",
            "transport": {
                "request_attempts": "many",
                "request_failures": {"no_answer": -1, "other": True},
                "link_recycles": 2,
            },
            "read_failures": {"room": {"flow": "bad"}},
            "write_issues": "bad",
        }
    )

    diagnostics = await CumulativeDiagnostics.async_create(hass, "invalid")

    assert diagnostics.request_attempts == 0
    assert diagnostics.request_failures == 0
    assert diagnostics.link_recycles == 2
    assert diagnostics.failed_read_groups == 0
    assert diagnostics.write_issues == 0


async def test_snapshot_has_nested_breakdowns(hass: HomeAssistant) -> None:
    diagnostics = await CumulativeDiagnostics.async_create(hass, DOMAIN)
    diagnostics.record_request("modbus_exception")
    diagnostics.record_read_failure("room", "temperature")
    diagnostics.record_write_issue("room", "operation_mode", "unconfirmed")

    snapshot = diagnostics.diagnostics()

    transport = snapshot.get("transport")
    assert isinstance(transport, dict)
    assert transport["request_failures_by_category"] == {
        "no_answer": 0,
        "modbus_exception": 1,
        "other": 0,
    }
    assert snapshot["read_failures"] == {"room": {"temperature": 1}}
    assert snapshot["write_issues"] == {"room": {"unconfirmed": {"operation_mode": 1}}}


def test_save_requests_are_rate_limited(monkeypatch) -> None:
    clock = SimpleNamespace(now=100.0)
    monkeypatch.setattr(
        "custom_components.meltem_ventilation.cumulative_diagnostics.time",
        SimpleNamespace(monotonic=lambda: clock.now),
    )
    store = MagicMock()
    diagnostics = CumulativeDiagnostics(store, None)

    diagnostics.record_request("success")
    diagnostics.record_request("no_answer")
    assert store.async_delay_save.call_count == 1

    clock.now += 60.0
    diagnostics.record_request("success")
    assert store.async_delay_save.call_count == 2
