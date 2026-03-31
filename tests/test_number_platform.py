"""Tests for the number platform — entity creation, optimistic state, and writes."""

from __future__ import annotations

import asyncio
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.core import HomeAssistant

from custom_components.meltem_ventilation.coordinator import MeltemDataUpdateCoordinator
from custom_components.meltem_ventilation.models import RefreshPlan, RoomConfig, RoomState
from custom_components.meltem_ventilation.number import (
    MeltemBalancedLevelNumber,
    MeltemBaseLevelNumber,
    MeltemExtractLevelNumber,
    MeltemSupplyLevelNumber,
    PendingWriteCommand,
    _room_supports,
)


# ---------------------------------------------------------------------------
#  Helpers
# ---------------------------------------------------------------------------

_ROOM_ALL = RoomConfig(
    key="unit_1", name="Unit 1", profile="ii_plain", slave=2
)
_ROOM_CONSTRAINED = RoomConfig(
    key="unit_2",
    name="Unit 2",
    profile="ii_plain",
    slave=3,
    supported_entity_keys=frozenset({"level", "supply_level"}),
)
_ROOM_S = RoomConfig(
    key="unit_s", name="Unit S", profile="s_plain", slave=4
)


def _fake_coordinator(
    hass: HomeAssistant | None = None,
    rooms: list[RoomConfig] | None = None,
    data: dict[str, RoomState] | None = None,
) -> MagicMock:
    """Build a lightweight mock coordinator for number entity tests."""
    coordinator = MagicMock(spec=MeltemDataUpdateCoordinator)
    coordinator.data = data or {}
    type(coordinator).safe_data = property(lambda self: self.data if isinstance(self.data, dict) else {})
    coordinator.rooms = rooms or [_ROOM_ALL]
    coordinator.hass = hass
    coordinator.async_set_level = AsyncMock()
    coordinator.async_set_unbalanced_levels = AsyncMock()
    coordinator.optimistic_targets_by_room = {}
    coordinator.pending_writes_by_room = {}
    coordinator.write_tasks_by_room = {}
    coordinator.active_write_rooms = set()
    # CoordinatorEntity expects these
    coordinator.async_request_refresh = AsyncMock()
    coordinator.async_add_listener = MagicMock(return_value=lambda: None)
    coordinator.async_update_listeners = MagicMock()
    return coordinator


# ---------------------------------------------------------------------------
#  _room_supports filter — extended coverage
# ---------------------------------------------------------------------------


class TestRoomSupports:
    def test_all_keys_supported_when_none(self) -> None:
        room = RoomConfig(key="a", name="A", profile="ii_plain", slave=2)
        assert _room_supports(room, "level")
        assert _room_supports(room, "supply_level")
        assert _room_supports(room, "extract_level")

    def test_only_listed_keys_pass(self) -> None:
        room = RoomConfig(
            key="a",
            name="A",
            profile="ii_plain",
            slave=2,
            supported_entity_keys=frozenset({"level"}),
        )
        assert _room_supports(room, "level")
        assert not _room_supports(room, "supply_level")
        assert not _room_supports(room, "extract_level")


# ---------------------------------------------------------------------------
#  Balanced number — read state value
# ---------------------------------------------------------------------------


class TestBalancedLevelReadState:
    def test_ignores_stale_extract_target_in_manual_mode(self) -> None:
        coordinator = _fake_coordinator(
            data={
                "unit_1": RoomState(
                    target_level=20,
                    extract_target_level=50,
                    supply_air_flow=20,
                    extract_air_flow=20,
                    operation_mode="manual",
                )
            }
        )
        balanced = MeltemBalancedLevelNumber(coordinator, _ROOM_ALL)
        supply = MeltemSupplyLevelNumber(coordinator, _ROOM_ALL)
        extract = MeltemExtractLevelNumber(coordinator, _ROOM_ALL)

        assert balanced._read_state_value() == 20.0
        assert supply._read_state_value() == 20.0
        assert extract._read_state_value() == 20.0

    def test_reads_target_level(self) -> None:
        coordinator = _fake_coordinator(
            data={"unit_1": RoomState(target_level=42)}
        )
        entity = MeltemBalancedLevelNumber(coordinator, _ROOM_ALL)
        assert entity._read_state_value() == 42.0

    def test_fallback_average_of_flows(self) -> None:
        coordinator = _fake_coordinator(
            data={"unit_1": RoomState(extract_air_flow=40, supply_air_flow=50)}
        )
        entity = MeltemBalancedLevelNumber(coordinator, _ROOM_ALL)
        # Diverged flows (diff > 1) produce None for balanced target.
        assert entity._read_state_value() is None

    def test_fallback_single_flow(self) -> None:
        coordinator = _fake_coordinator(
            data={"unit_1": RoomState(supply_air_flow=60)}
        )
        entity = MeltemBalancedLevelNumber(coordinator, _ROOM_ALL)
        assert entity._read_state_value() == 60.0

    def test_fallback_extract_only(self) -> None:
        coordinator = _fake_coordinator(
            data={"unit_1": RoomState(extract_air_flow=70)}
        )
        entity = MeltemBalancedLevelNumber(coordinator, _ROOM_ALL)
        assert entity._read_state_value() == 70.0

    def test_returns_none_when_targets_diverge(self) -> None:
        coordinator = _fake_coordinator(
            data={
                "unit_1": RoomState(
                    target_level=55,
                    extract_target_level=40,
                    operation_mode="unbalanced",
                )
            }
        )
        entity = MeltemBalancedLevelNumber(coordinator, _ROOM_ALL)
        assert entity._read_state_value() == 0.0

    def test_returns_none_when_all_missing(self) -> None:
        coordinator = _fake_coordinator(data={"unit_1": RoomState()})
        entity = MeltemBalancedLevelNumber(coordinator, _ROOM_ALL)
        assert entity._read_state_value() is None


# ---------------------------------------------------------------------------
#  Supply number — read state value
# ---------------------------------------------------------------------------


class TestSupplyLevelReadState:
    def test_entity_is_disabled_by_default(self) -> None:
        entity = MeltemSupplyLevelNumber(_fake_coordinator(), _ROOM_ALL)
        assert entity.entity_registry_enabled_default is False

    def test_reads_target_level(self) -> None:
        coordinator = _fake_coordinator(
            data={"unit_1": RoomState(target_level=33)}
        )
        entity = MeltemSupplyLevelNumber(coordinator, _ROOM_ALL)
        assert entity._read_state_value() == 33.0

    def test_fallback_to_supply_flow(self) -> None:
        coordinator = _fake_coordinator(
            data={"unit_1": RoomState(supply_air_flow=44)}
        )
        entity = MeltemSupplyLevelNumber(coordinator, _ROOM_ALL)
        assert entity._read_state_value() == 44.0

    def test_reads_balanced_target_when_room_is_synchronized(self) -> None:
        coordinator = _fake_coordinator(
            data={
                "unit_1": RoomState(
                    target_level=48,
                    extract_target_level=48,
                )
            }
        )
        entity = MeltemSupplyLevelNumber(coordinator, _ROOM_ALL)
        assert entity._read_state_value() == 48.0

    def test_returns_none_when_both_missing(self) -> None:
        coordinator = _fake_coordinator(data={"unit_1": RoomState()})
        entity = MeltemSupplyLevelNumber(coordinator, _ROOM_ALL)
        assert entity._read_state_value() is None


# ---------------------------------------------------------------------------
#  Extract number — read state value
# ---------------------------------------------------------------------------


class TestExtractLevelReadState:
    def test_entity_is_disabled_by_default(self) -> None:
        entity = MeltemExtractLevelNumber(_fake_coordinator(), _ROOM_ALL)
        assert entity.entity_registry_enabled_default is False

    def test_reads_extract_target_level(self) -> None:
        coordinator = _fake_coordinator(
            data={
                "unit_1": RoomState(
                    target_level=60,
                    extract_target_level=50,
                    operation_mode="unbalanced",
                )
            }
        )
        entity = MeltemExtractLevelNumber(coordinator, _ROOM_ALL)
        assert entity._read_state_value() == 50.0

    def test_ignores_extract_target_when_mode_is_manual(self) -> None:
        coordinator = _fake_coordinator(
            data={
                "unit_1": RoomState(
                    target_level=60,
                    extract_target_level=50,
                    operation_mode="manual",
                )
            }
        )
        entity = MeltemExtractLevelNumber(coordinator, _ROOM_ALL)
        assert entity._read_state_value() == 60.0

    def test_reads_balanced_target_when_room_is_synchronized(self) -> None:
        coordinator = _fake_coordinator(
            data={
                "unit_1": RoomState(
                    target_level=52,
                    extract_target_level=52,
                )
            }
        )
        entity = MeltemExtractLevelNumber(coordinator, _ROOM_ALL)
        assert entity._read_state_value() == 52.0

    def test_fallback_to_extract_flow(self) -> None:
        coordinator = _fake_coordinator(
            data={"unit_1": RoomState(extract_air_flow=60)}
        )
        entity = MeltemExtractLevelNumber(coordinator, _ROOM_ALL)
        assert entity._read_state_value() == 60.0

    def test_returns_none_when_both_missing(self) -> None:
        coordinator = _fake_coordinator(data={"unit_1": RoomState()})
        entity = MeltemExtractLevelNumber(coordinator, _ROOM_ALL)
        assert entity._read_state_value() is None


# ---------------------------------------------------------------------------
#  Max value from profile
# ---------------------------------------------------------------------------


class TestMaxValueFromProfile:
    def test_ii_plain_max_is_100(self) -> None:
        coordinator = _fake_coordinator()
        entity = MeltemBalancedLevelNumber(coordinator, _ROOM_ALL)
        assert entity.native_max_value == 100.0

    def test_s_plain_max_is_97(self) -> None:
        coordinator = _fake_coordinator(rooms=[_ROOM_S])
        entity = MeltemBalancedLevelNumber(coordinator, _ROOM_S)
        assert entity.native_max_value == 97.0


# ---------------------------------------------------------------------------
#  Optimistic values
# ---------------------------------------------------------------------------


class TestOptimisticValue:
    def test_other_sliders_keep_optimistic_value_during_stale_gateway_read(self) -> None:
        coordinator = _fake_coordinator(
            data={"unit_1": RoomState(target_level=20, extract_target_level=20)}
        )
        balanced = MeltemBalancedLevelNumber(coordinator, _ROOM_ALL)
        supply = MeltemSupplyLevelNumber(coordinator, _ROOM_ALL)
        extract = MeltemExtractLevelNumber(coordinator, _ROOM_ALL)

        balanced._set_room_optimistic_targets(55)
        coordinator.data = {
            "unit_1": RoomState(
                target_level=20,
                extract_target_level=20,
                supply_air_flow=20,
                extract_air_flow=20,
            )
        }

        assert balanced._read_state_value() == 55.0
        assert supply._read_state_value() == 55.0
        assert extract._read_state_value() == 55.0

    def test_balanced_slider_shows_zero_immediately_for_optimistic_split_targets(self) -> None:
        coordinator = _fake_coordinator(
            data={"unit_1": RoomState(target_level=30, extract_target_level=40)}
        )
        balanced = MeltemBalancedLevelNumber(coordinator, _ROOM_ALL)
        supply = MeltemSupplyLevelNumber(coordinator, _ROOM_ALL)

        supply._set_room_optimistic_targets(55)

        assert balanced._read_state_value() == 0.0


# ---------------------------------------------------------------------------
#  Coordinator update clears optimistic overlay
# ---------------------------------------------------------------------------


class TestHandleCoordinatorUpdate:
    def test_clears_optimistic_targets_when_coordinator_matches(self) -> None:
        coordinator = _fake_coordinator(
            data={"unit_1": RoomState(target_level=50, extract_target_level=50)}
        )
        entity = MeltemBalancedLevelNumber(coordinator, _ROOM_ALL)
        entity._set_room_optimistic_targets(50)

        with patch.object(entity, "async_write_ha_state"):
            entity._handle_coordinator_update()
        assert "unit_1" not in coordinator.optimistic_targets_by_room

    def test_keeps_optimistic_targets_when_coordinator_diverges(self) -> None:
        coordinator = _fake_coordinator(
            data={"unit_1": RoomState(target_level=30, extract_target_level=30)}
        )
        entity = MeltemBalancedLevelNumber(coordinator, _ROOM_ALL)
        entity._set_room_optimistic_targets(50)

        with patch.object(entity, "async_write_ha_state"):
            entity._handle_coordinator_update()
        assert "unit_1" in coordinator.optimistic_targets_by_room


# ---------------------------------------------------------------------------
#  async_set_native_value (queued write worker creation)
# ---------------------------------------------------------------------------


class TestSetNativeValue:
    async def test_set_native_value_creates_shared_write_task(
        self, hass: HomeAssistant,
    ) -> None:
        coordinator = _fake_coordinator(
            hass=hass,
            data={"unit_1": RoomState(target_level=30)},
        )
        entity = MeltemBalancedLevelNumber(coordinator, _ROOM_ALL)
        entity.hass = hass

        with patch.object(entity, "async_write_ha_state"):
            await entity.async_set_native_value(55.0)

        pending = coordinator.pending_writes_by_room["unit_1"]
        assert pending.supply_level == 55
        assert pending.extract_level == 55
        assert coordinator.write_tasks_by_room["unit_1"] is not None
        coordinator.write_tasks_by_room["unit_1"].cancel()

    async def test_set_native_value_keeps_single_worker_and_updates_latest_command(
        self, hass: HomeAssistant,
    ) -> None:
        coordinator = _fake_coordinator(
            hass=hass,
            data={"unit_1": RoomState(target_level=30)},
        )
        entity = MeltemBalancedLevelNumber(coordinator, _ROOM_ALL)
        entity.hass = hass

        with patch.object(entity, "async_write_ha_state"):
            await entity.async_set_native_value(40.0)
            first_task = coordinator.write_tasks_by_room["unit_1"]
            await entity.async_set_native_value(60.0)

        await asyncio.sleep(0)
        assert coordinator.write_tasks_by_room["unit_1"] is first_task
        pending = coordinator.pending_writes_by_room["unit_1"]
        assert pending.supply_level == 60
        assert pending.extract_level == 60
        first_task.cancel()

    async def test_set_native_value_clamps_to_max(
        self, hass: HomeAssistant,
    ) -> None:
        coordinator = _fake_coordinator(
            hass=hass,
            data={"unit_1": RoomState(target_level=30)},
        )
        entity = MeltemBalancedLevelNumber(coordinator, _ROOM_ALL)
        entity.hass = hass

        with patch.object(entity, "async_write_ha_state"):
            await entity.async_set_native_value(999.0)

        pending = coordinator.pending_writes_by_room["unit_1"]
        assert pending.supply_level == 100
        assert pending.extract_level == 100
        coordinator.write_tasks_by_room["unit_1"].cancel()

    async def test_balanced_set_native_value_publishes_synced_targets(
        self, hass: HomeAssistant,
    ) -> None:
        coordinator = _fake_coordinator(
            hass=hass,
            data={"unit_1": RoomState(target_level=30, extract_target_level=20)},
        )
        entity = MeltemBalancedLevelNumber(coordinator, _ROOM_ALL)
        entity.hass = hass

        with patch.object(entity, "async_write_ha_state"):
            await entity.async_set_native_value(55.0)

        optimistic_targets = coordinator.optimistic_targets_by_room["unit_1"]
        assert optimistic_targets.supply_level == 55
        assert optimistic_targets.extract_level == 55
        coordinator.async_update_listeners.assert_called()
        coordinator.write_tasks_by_room["unit_1"].cancel()

    async def test_supply_set_native_value_keeps_extract_target(
        self, hass: HomeAssistant,
    ) -> None:
        coordinator = _fake_coordinator(
            hass=hass,
            data={
                "unit_1": RoomState(
                    target_level=30,
                    extract_target_level=40,
                    operation_mode="unbalanced",
                )
            },
        )
        entity = MeltemSupplyLevelNumber(coordinator, _ROOM_ALL)
        entity.hass = hass

        with patch.object(entity, "async_write_ha_state"):
            await entity.async_set_native_value(55.0)

        optimistic_targets = coordinator.optimistic_targets_by_room["unit_1"]
        assert optimistic_targets.supply_level == 55
        assert optimistic_targets.extract_level == 40
        coordinator.async_update_listeners.assert_called()
        coordinator.write_tasks_by_room["unit_1"].cancel()


# ---------------------------------------------------------------------------
#  Apply value — balanced vs supply vs extract
# ---------------------------------------------------------------------------


class TestApplyValue:
    async def test_balanced_calls_set_level(
        self, hass: HomeAssistant,
    ) -> None:
        coordinator = _fake_coordinator(
            hass=hass,
            data={"unit_1": RoomState(target_level=30)},
        )
        entity = MeltemBalancedLevelNumber(coordinator, _ROOM_ALL)
        entity.hass = hass

        await entity._async_apply_command(
            type("PendingWriteCommand", (), {"supply_level": 55, "extract_level": 55})()
        )
        coordinator.async_set_level.assert_awaited_once_with("unit_1", 55)

    async def test_supply_calls_unbalanced_keeping_extract(
        self, hass: HomeAssistant,
    ) -> None:
        coordinator = _fake_coordinator(
            hass=hass,
            data={"unit_1": RoomState(target_level=30, extract_target_level=40)},
        )
        entity = MeltemSupplyLevelNumber(coordinator, _ROOM_ALL)
        entity.hass = hass

        await entity._async_apply_command(
            type("PendingWriteCommand", (), {"supply_level": 50, "extract_level": 40})()
        )
        coordinator.async_set_unbalanced_levels.assert_awaited_once_with(
            "unit_1", 50, 40
        )

    async def test_supply_falls_back_to_own_level_when_no_extract(
        self, hass: HomeAssistant,
    ) -> None:
        coordinator = _fake_coordinator(
            hass=hass,
            data={"unit_1": RoomState()},
        )
        entity = MeltemSupplyLevelNumber(coordinator, _ROOM_ALL)
        entity.hass = hass

        await entity._async_apply_command(
            type("PendingWriteCommand", (), {"supply_level": 50, "extract_level": 50})()
        )
        # When extract_target is None, extract_level defaults to the supplied level.
        coordinator.async_set_level.assert_awaited_once_with("unit_1", 50)

    async def test_supply_uses_extract_airflow_when_target_missing(
        self, hass: HomeAssistant,
    ) -> None:
        coordinator = _fake_coordinator(
            hass=hass,
            data={"unit_1": RoomState(supply_air_flow=55, extract_air_flow=43)},
        )
        entity = MeltemSupplyLevelNumber(coordinator, _ROOM_ALL)
        entity.hass = hass

        await entity._async_apply_command(
            type("PendingWriteCommand", (), {"supply_level": 50, "extract_level": 43})()
        )
        coordinator.async_set_unbalanced_levels.assert_awaited_once_with(
            "unit_1", 50, 43
        )

    async def test_extract_calls_unbalanced_keeping_supply(
        self, hass: HomeAssistant,
    ) -> None:
        coordinator = _fake_coordinator(
            hass=hass,
            data={"unit_1": RoomState(target_level=60)},
        )
        entity = MeltemExtractLevelNumber(coordinator, _ROOM_ALL)
        entity.hass = hass

        await entity._async_apply_command(
            type("PendingWriteCommand", (), {"supply_level": 60, "extract_level": 35})()
        )
        coordinator.async_set_unbalanced_levels.assert_awaited_once_with(
            "unit_1", 60, 35
        )

    async def test_extract_uses_supply_airflow_when_target_level_missing(
        self, hass: HomeAssistant,
    ) -> None:
        coordinator = _fake_coordinator(
            hass=hass,
            data={"unit_1": RoomState(supply_air_flow=72, extract_air_flow=40)},
        )
        entity = MeltemExtractLevelNumber(coordinator, _ROOM_ALL)
        entity.hass = hass

        await entity._async_apply_command(
            type("PendingWriteCommand", (), {"supply_level": 72, "extract_level": 35})()
        )
        coordinator.async_set_unbalanced_levels.assert_awaited_once_with(
            "unit_1", 72, 35
        )

    def test_supply_prefers_target_level_before_measured_airflow(self) -> None:
        coordinator = _fake_coordinator(
            data={
                "unit_1": RoomState(
                    target_level=55,
                    supply_air_flow=70,
                    extract_air_flow=40,
                )
            }
        )
        entity = MeltemSupplyLevelNumber(coordinator, _ROOM_ALL)

        assert entity._read_state_value() == 55.0


# ---------------------------------------------------------------------------
#  Queued write worker — failure clears optimistic state
# ---------------------------------------------------------------------------


class TestQueuedWriteWorker:
    async def test_failure_clears_optimistic_targets(
        self, hass: HomeAssistant,
    ) -> None:
        coordinator = _fake_coordinator(
            hass=hass,
            data={"unit_1": RoomState(target_level=30)},
        )
        coordinator.async_set_level = AsyncMock(side_effect=Exception("boom"))
        entity = MeltemBalancedLevelNumber(coordinator, _ROOM_ALL)
        entity.hass = hass
        entity._pending_writes_by_room["unit_1"] = PendingWriteCommand(
            supply_level=50,
            extract_level=50,
            updated_at=time.monotonic() - 1,
        )
        entity._set_room_optimistic_targets(50)

        with (
            patch.object(entity, "async_write_ha_state"),
            patch("custom_components.meltem_ventilation.number.asyncio.sleep", new=AsyncMock()),
        ):
            await entity._async_process_room_writes()

        assert "unit_1" not in entity._optimistic_targets_by_room

    async def test_mixed_supply_and_extract_changes_keep_latest_target_pair(
        self, hass: HomeAssistant,
    ) -> None:
        coordinator = _fake_coordinator(
            hass=hass,
            data={"unit_1": RoomState(target_level=30, extract_target_level=40)},
        )
        supply = MeltemSupplyLevelNumber(coordinator, _ROOM_ALL)
        extract = MeltemExtractLevelNumber(coordinator, _ROOM_ALL)
        supply.hass = hass
        extract.hass = hass

        with (
            patch.object(supply, "async_write_ha_state"),
            patch.object(extract, "async_write_ha_state"),
        ):
            await supply.async_set_native_value(50.0)
            await extract.async_set_native_value(30.0)

        pending = coordinator.pending_writes_by_room["unit_1"]
        assert pending.supply_level == 50
        assert pending.extract_level == 30
        coordinator.write_tasks_by_room["unit_1"].cancel()


# ---------------------------------------------------------------------------
#  will_remove is a no-op for shared room workers
# ---------------------------------------------------------------------------


class TestRemoval:
    async def test_will_remove_from_hass_does_not_raise(
        self, hass: HomeAssistant,
    ) -> None:
        coordinator = _fake_coordinator(hass=hass)
        entity = MeltemBalancedLevelNumber(coordinator, _ROOM_ALL)
        entity.hass = hass

        await entity.async_will_remove_from_hass()
