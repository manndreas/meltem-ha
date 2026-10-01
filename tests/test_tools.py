"""Tests for the gateway tools that do not need a live gateway."""

from __future__ import annotations

import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from unittest.mock import patch

import pytest
from modbus_connection import IllegalDataValueError
from modbus_connection.mock import MockModbusConnection, WriteEvent

from custom_components.meltem_ventilation.const import (
    DEFAULT_GATEWAY_DEVICE_ID,
    DEFAULT_PORT,
    FIXED_TIMEOUT,
    REGISTER_APPLY,
    REGISTER_CURRENT_LEVEL,
    REGISTER_GATEWAY_NODE_ADDRESS_1,
    REGISTER_GATEWAY_NUMBER_OF_NODES,
    REGISTER_MODE,
    REQUEST_GAP_SECONDS,
)
from custom_components.meltem_ventilation.modbus_helpers import build_serial_params
from tools import _link, write_registers

_MANUAL_80_SEQUENCE = ((REGISTER_MODE, 3), (REGISTER_CURRENT_LEVEL, 80), (REGISTER_APPLY, 0))


def test_link_settings_match_the_integration() -> None:
    """The tools keep their own copy, so it must not drift from the integration."""

    assert _link.serial_params("/dev/ttyX") == build_serial_params("/dev/ttyX")
    assert _link.TIMEOUT == FIXED_TIMEOUT
    assert _link.REQUEST_GAP_SECONDS == REQUEST_GAP_SECONDS
    assert _link.DEFAULT_PORT == DEFAULT_PORT
    assert _link.GATEWAY_DEVICE_ID == DEFAULT_GATEWAY_DEVICE_ID
    assert _link.REGISTER_GATEWAY_NUMBER_OF_NODES == REGISTER_GATEWAY_NUMBER_OF_NODES
    assert _link.REGISTER_GATEWAY_NODE_ADDRESS_1 == REGISTER_GATEWAY_NODE_ADDRESS_1


class TestWriteRegisters:
    @pytest.fixture(name="link")
    def link_fixture(self) -> MockModbusConnection:
        link = MockModbusConnection()

        @asynccontextmanager
        async def _open_link(_port: str) -> AsyncIterator[MockModbusConnection]:
            yield link

        with patch.object(write_registers, "open_link", _open_link):
            yield link

    async def _run(self, *args: str) -> int:
        argv = ["write_registers", "--slave", "2"]
        for address, value in _MANUAL_80_SEQUENCE:
            argv += ["--write", f"{address}={value}"]
        with patch.object(sys, "argv", [*argv, *args]):
            return await write_registers.main()

    async def test_a_failed_write_stops_before_the_apply(
        self, link: MockModbusConnection
    ) -> None:
        unit = link.for_unit(2)
        unit.fail_write(REGISTER_CURRENT_LEVEL, IllegalDataValueError())
        written: list[WriteEvent] = []
        unit.on_write(written.append)

        assert await self._run() == 1
        assert [event.address for event in written] == [REGISTER_MODE]

    async def test_keep_going_sends_the_rest_but_still_fails(
        self, link: MockModbusConnection
    ) -> None:
        unit = link.for_unit(2)
        unit.fail_write(REGISTER_CURRENT_LEVEL, IllegalDataValueError())
        written: list[WriteEvent] = []
        unit.on_write(written.append)

        assert await self._run("--keep-going") == 1
        assert [event.address for event in written] == [REGISTER_MODE, REGISTER_APPLY]
