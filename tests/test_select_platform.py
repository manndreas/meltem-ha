"""Tests for the Meltem operation-mode and preset select entities."""

from __future__ import annotations

from dataclasses import replace
from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.meltem_ventilation.const import PRESET_MODE_INACTIVE
from custom_components.meltem_ventilation.models import RoomConfig, RoomState
from custom_components.meltem_ventilation.select import (
    MeltemOperationModeSelect,
    MeltemPresetModeSelect,
)

_ROOM = RoomConfig(key="unit_1", name="Unit 1", profile="ii_fc", slave=2)


def _coordinator(state: RoomState) -> MagicMock:
    coordinator = MagicMock()
    coordinator.last_update_success = True
    coordinator.room_available.return_value = True
    coordinator.optimistic_preset_mode.return_value = None
    coordinator.async_set_operation_mode = AsyncMock()
    coordinator.async_set_preset_mode = AsyncMock()
    coordinator.async_clear_preset_mode = AsyncMock()
    coordinator.safe_data = {"unit_1": state}
    return coordinator


def _operation_mode_select(
    state: RoomState = RoomState(operation_mode="manual"), profile: str = "ii_fc"
) -> MeltemOperationModeSelect:
    return MeltemOperationModeSelect(_coordinator(state), replace(_ROOM, profile=profile))


def _preset_select(
    state: RoomState = RoomState(preset_mode="medium"),
) -> MeltemPresetModeSelect:
    return MeltemPresetModeSelect(_coordinator(state), _ROOM)


@pytest.mark.parametrize(
    "build", [_operation_mode_select, _preset_select], ids=("operation-mode", "preset")
)
def test_available_while_flow_control_is_stale(build) -> None:
    select = build()
    select.coordinator.read_group_available.return_value = False

    assert select.available is True
    select.coordinator.read_group_available.assert_not_called()


class TestMeltemOperationModeSelect:
    @pytest.mark.parametrize(
        ("profile", "options"),
        [
            ("ii_f", ["inactive", "humidity_control"]),
            ("ii_fc", ["inactive", "humidity_control", "co2_control", "automatic"]),
        ],
    )
    def test_options_follow_the_sensors_of_the_profile(
        self, profile: str, options: list[str]
    ) -> None:
        assert _operation_mode_select(profile=profile).options == options

    @pytest.mark.parametrize(
        ("operation_mode", "current_option"),
        [
            ("off", "inactive"),
            ("manual", "inactive"),
            ("unbalanced", "inactive"),
            ("co2_control", "co2_control"),
            (None, None),
        ],
    )
    def test_current_option(
        self, operation_mode: str | None, current_option: str | None
    ) -> None:
        select = _operation_mode_select(RoomState(operation_mode=operation_mode))

        assert select.current_option == current_option

    async def test_select_option_delegates_to_coordinator(self) -> None:
        select = _operation_mode_select(profile="ii_fc_voc")

        await select.async_select_option("automatic")

        select.coordinator.async_set_operation_mode.assert_awaited_once_with(
            "unit_1", "automatic"
        )

    async def test_selecting_inactive_leaves_sensor_control(self) -> None:
        select = _operation_mode_select(RoomState(operation_mode="co2_control"))

        await select.async_select_option("inactive")

        select.coordinator.async_set_operation_mode.assert_awaited_once_with(
            "unit_1", "manual"
        )

    async def test_selecting_inactive_keeps_an_unbalanced_setup(self) -> None:
        """Writing manual again would collapse both fans onto one airflow."""
        select = _operation_mode_select(RoomState(operation_mode="unbalanced"))

        await select.async_select_option("inactive")

        select.coordinator.async_set_operation_mode.assert_not_awaited()


class TestMeltemPresetModeSelect:
    def test_only_app_quick_modes_are_selectable(self) -> None:
        assert _preset_select().options == ["inactive", "low", "medium", "high"]

    @pytest.mark.parametrize(
        ("state", "optimistic", "current_option"),
        [
            pytest.param(RoomState(), None, None, id="no-readback"),
            pytest.param(RoomState(preset_mode="medium"), None, "medium", id="confirmed"),
            pytest.param(
                RoomState(operation_mode="unbalanced"),
                None,
                PRESET_MODE_INACTIVE,
                id="known-mode-without-quick-mode",
            ),
            pytest.param(
                RoomState(preset_mode="extract_only"),
                None,
                PRESET_MODE_INACTIVE,
                id="extract-only",
            ),
            pytest.param(
                RoomState(preset_mode="supply_only"),
                None,
                PRESET_MODE_INACTIVE,
                id="supply-only",
            ),
            pytest.param(RoomState(preset_mode="medium"), "low", "low", id="pending-selection"),
            pytest.param(
                RoomState(preset_mode="medium"),
                PRESET_MODE_INACTIVE,
                PRESET_MODE_INACTIVE,
                id="pending-clear",
            ),
        ],
    )
    def test_current_option(
        self, state: RoomState, optimistic: str | None, current_option: str | None
    ) -> None:
        select = _preset_select(state)
        select.coordinator.optimistic_preset_mode.return_value = optimistic

        assert select.current_option == current_option

    async def test_select_option_sets_preset_mode(self) -> None:
        select = _preset_select()

        await select.async_select_option("high")

        select.coordinator.async_set_preset_mode.assert_awaited_once_with("unit_1", "high")
        select.coordinator.async_clear_preset_mode.assert_not_awaited()

    async def test_select_option_can_clear_preset_mode(self) -> None:
        select = _preset_select()

        await select.async_select_option(PRESET_MODE_INACTIVE)

        select.coordinator.async_clear_preset_mode.assert_awaited_once_with("unit_1")
        select.coordinator.async_set_preset_mode.assert_not_awaited()
