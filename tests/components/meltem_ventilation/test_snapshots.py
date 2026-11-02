"""Snapshot tests of every entity and of the diagnostics download."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from unittest.mock import AsyncMock, patch

import pytest
from freezegun.api import FrozenDateTimeFactory
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util
from pytest_homeassistant.common import MockConfigEntry, snapshot_platform
from syrupy.assertion import SnapshotAssertion

from custom_components.meltem_ventilation.const import CONF_PORT, DOMAIN, PLATFORMS
from custom_components.meltem_ventilation.diagnostics import (
    async_get_config_entry_diagnostics,
)
from custom_components.meltem_ventilation.models import (
    GatewayRfStatistics,
    ReadHealth,
    ReadJobOutcome,
    RoomRead,
    RoomState,
)
from custom_components.meltem_ventilation.units import unit_subentry_data

_INIT = "custom_components.meltem_ventilation"
_CLIENT = f"{_INIT}.MeltemModbusClient"
_NOW = "2026-11-02T12:00:00+00:00"
_GROUPS = (
    "flow",
    "flow_control",
    "intensive",
    "status",
    "temperature",
    "filter",
    "hours",
    "fan_hours",
    "rf_last_seen",
    "rf_signal",
    "rf_fault",
    "value_error",
    "control_settings",
)

type SetupEntry = Callable[[list[Platform]], Awaitable[MockConfigEntry]]


def _full_state() -> RoomState:
    """Return a unit that reported every value just now."""

    now = dt_util.utcnow()
    state = RoomState(
        exhaust_temperature=8.5,
        outdoor_air_temperature=4.0,
        extract_air_temperature=21.5,
        supply_air_temperature=19.0,
        humidity_extract_air=48,
        humidity_supply_air=41,
        co2_extract_air=780,
        voc_supply_air=120,
        extract_air_flow=40,
        supply_air_flow=42,
        error_status=False,
        filter_change_due=False,
        frost_protection_active=False,
        rf_comm_status=False,
        rf_fault_status=False,
        value_error_status=False,
        operation_mode="manual",
        preset_mode=None,
        intensive_active=False,
        target_level=40,
        days_until_filter_change=120,
        operating_hours=12_345,
        fan_motor_hours=11_000,
        rf_last_seen_age_seconds=12,
        rf_signal_strength=-43,
        software_version=2326,
        humidity_starting_point=60,
        humidity_min_level=10,
        humidity_max_level=60,
        co2_starting_point=800,
        co2_min_level=10,
        co2_max_level=60,
    )
    for group in _GROUPS:
        state = state.with_read_health(
            group, ReadHealth(last_attempt=now, last_successful_read=now)
        )
    return state


@pytest.fixture(name="setup_entry")
def setup_entry_fixture(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, monkeypatch: pytest.MonkeyPatch
) -> SetupEntry:
    """Set up one M-WRG-II (O/VOC-AUL) unit without a gateway, at a fixed time."""

    freezer.move_to(_NOW)
    for name, value in {
        "async_validate_gateway": None,
        "async_read_gateway_uptime": 1_296_000,
        "async_read_gateway_mac": "02:00:00:00:00:01",
        "async_read_gateway_utc_clock": 1_793_620_800,
        "async_read_gateway_node_count": 1,
        "async_read_gateway_rf_statistics": GatewayRfStatistics(75, 147, 0.26, 0.04),
        "async_read_room_app_setup_code": "R000001",
        "discover_gateway_units": [2],
        "read_room": RoomRead(RoomState(), ReadJobOutcome()),
    }.items():
        monkeypatch.setattr(f"{_CLIENT}.{name}", AsyncMock(return_value=value))
    monkeypatch.setattr(
        f"{_INIT}.coordinator.MeltemDataUpdateCoordinator.async_refresh", AsyncMock()
    )

    async def _setup(platforms: list[Platform]) -> MockConfigEntry:
        entry = MockConfigEntry(
            domain=DOMAIN,
            title="Meltem Gateway M-WRG-GW",
            entry_id="01JMELTEMSNAPSHOTENTRY0000",
            unique_id="/dev/serial/by-id/usb-meltem",
            data={CONF_PORT: "/dev/serial/by-id/usb-meltem"},
            subentries_data=[
                {
                    **unit_subentry_data(
                        {
                            "key": "slave_2",
                            "name": "Unit 1",
                            "slave": 2,
                            "profile": "ii_fc_voc",
                            "preview": "R-Code R000001 | ID 116852 | VOC",
                            "product_id": 116852,
                            "app_setup_code": "R000001",
                        }
                    ),
                    "subentry_id": "01JMELTEMSNAPSHOTUNIT00000",
                }
            ],
            version=2,
            source="user",
        )
        entry.add_to_hass(hass)
        with (
            patch(f"{_INIT}.PLATFORMS", platforms),
            patch(f"{_INIT}.resolve_preferred_port_path", side_effect=lambda port: port),
        ):
            assert await hass.config_entries.async_setup(entry.entry_id)
            await hass.async_block_till_done()
        coordinator = entry.runtime_data.coordinator
        coordinator.async_set_updated_data({"slave_2": _full_state()})
        # A poll tick would read the optional gateway values the enabled entities need.
        await coordinator._async_read_optional_gateway_values_due()
        coordinator.async_update_listeners()
        await hass.async_block_till_done()
        return entry

    return _setup


@pytest.mark.usefixtures("entity_registry_enabled_by_default")
@pytest.mark.parametrize("platform", PLATFORMS)
async def test_entities(
    hass: HomeAssistant,
    entity_registry: er.EntityRegistry,
    snapshot: SnapshotAssertion,
    setup_entry: SetupEntry,
    platform: Platform,
) -> None:
    entry = await setup_entry([platform])

    await snapshot_platform(hass, entity_registry, snapshot, entry.entry_id)
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_diagnostics(
    hass: HomeAssistant, snapshot: SnapshotAssertion, setup_entry: SetupEntry
) -> None:
    entry = await setup_entry([Platform.SENSOR])

    assert await async_get_config_entry_diagnostics(hass, entry) == snapshot
    assert await hass.config_entries.async_unload(entry.entry_id)
