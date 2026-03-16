"""Tests for MeltemModbusClient runtime — connection, retry, read, write paths."""

from __future__ import annotations

import struct
import time
from unittest.mock import MagicMock, call, patch

import pytest

from custom_components.meltem_ventilation.const import (
    MODE_AUTOMATIC_VALUE,
    MODE_CO2_CONTROL_VALUE,
    MODE_HUMIDITY_CONTROL_VALUE,
    MODE_MANUAL,
    MODE_OFF,
    MODE_SENSOR_CONTROL,
    MODE_UNBALANCED,
    REGISTER_APPLY,
    REGISTER_CO2_STARTING_POINT,
    REGISTER_CURRENT_LEVEL,
    REGISTER_EXTRACT_AIR_TARGET_LEVEL,
    REGISTER_HUMIDITY_STARTING_POINT,
    REGISTER_MODE,
)
from custom_components.meltem_ventilation.modbus_client import MeltemModbusClient
from custom_components.meltem_ventilation.modbus_helpers import (
    MeltemModbusError,
    SerialSettings,
)
from custom_components.meltem_ventilation.models import RefreshPlan, RoomConfig, RoomState


# ---------------------------------------------------------------------------
#  Helpers
# ---------------------------------------------------------------------------

_SETTINGS = SerialSettings(
    port="/dev/ttyACM0",
    baudrate=19200,
    bytesize=8,
    parity="E",
    stopbits=1,
    timeout=0.8,
)

_ROOM = RoomConfig(key="unit_1", name="Unit 1", profile="ii_plain", slave=2)
_ROOM_S = RoomConfig(key="unit_s", name="Unit S", profile="s_plain", slave=3)
_ROOM_FC_VOC = RoomConfig(key="unit_v", name="Unit V", profile="ii_fc_voc", slave=4)


class _FakeResponse:
    def __init__(self, registers=None, error=False):
        self.registers = registers or []
        self._error = error

    def isError(self) -> bool:
        return self._error


class _FakeWriteResponse:
    def __init__(self, error=False):
        self._error = error

    def isError(self) -> bool:
        return self._error


def _failing_client():
    """Return a MagicMock whose connect() always returns False."""
    m = MagicMock()
    m.connect.return_value = False
    return m


# ---------------------------------------------------------------------------
#  Connection management — _ensure_client
# ---------------------------------------------------------------------------


class TestEnsureClient:
    def test_returns_existing_client_when_socket_open(self) -> None:
        client = MeltemModbusClient(_SETTINGS)
        mock_pymodbus = MagicMock()
        mock_pymodbus.is_socket_open.return_value = True
        client._client = mock_pymodbus

        result = client._ensure_client()
        assert result is mock_pymodbus

    def test_reconnects_when_socket_not_open(self) -> None:
        client = MeltemModbusClient(_SETTINGS)
        mock_pymodbus = MagicMock()
        mock_pymodbus.is_socket_open.return_value = False
        mock_pymodbus.connect.return_value = True
        client._client = mock_pymodbus

        result = client._ensure_client()
        assert result is mock_pymodbus
        mock_pymodbus.connect.assert_called_once()

    def test_builds_new_client_after_stale_one_fails(self) -> None:
        client = MeltemModbusClient(_SETTINGS)
        stale = MagicMock()
        stale.is_socket_open.return_value = False
        stale.connect.return_value = False
        client._client = stale

        fresh = MagicMock()
        fresh.connect.return_value = True

        with (
            patch(
                "custom_components.meltem_ventilation.modbus_client.build_client",
                return_value=fresh,
            ),
            patch(
                "custom_components.meltem_ventilation.modbus_client.time.sleep",
            ),
        ):
            result = client._ensure_client()

        assert result is fresh
        stale.close.assert_called()

    def test_retries_up_to_three_times(self) -> None:
        client = MeltemModbusClient(_SETTINGS)
        client._client = None

        mock1 = MagicMock()
        mock1.connect.return_value = False
        mock2 = MagicMock()
        mock2.connect.return_value = False
        mock3 = MagicMock()
        mock3.connect.return_value = True

        with (
            patch(
                "custom_components.meltem_ventilation.modbus_client.build_client",
                side_effect=[mock1, mock2, mock3],
            ),
            patch("custom_components.meltem_ventilation.modbus_client.time.sleep"),
        ):
            result = client._ensure_client()

        assert result is mock3

    def test_raises_after_all_retries_fail(self) -> None:
        client = MeltemModbusClient(_SETTINGS)
        client._client = None

        mock_fail = MagicMock()
        mock_fail.connect.return_value = False

        with (
            patch(
                "custom_components.meltem_ventilation.modbus_client.build_client",
                return_value=mock_fail,
            ),
            patch("custom_components.meltem_ventilation.modbus_client.time.sleep"),
            pytest.raises(MeltemModbusError, match="Could not connect"),
        ):
            client._ensure_client()

    def test_normalizes_connect_exceptions(self) -> None:
        client = MeltemModbusClient(_SETTINGS)
        client._client = None

        broken = MagicMock()
        broken.connect.side_effect = OSError("serial busy")

        with (
            patch(
                "custom_components.meltem_ventilation.modbus_client.build_client",
                return_value=broken,
            ),
            patch("custom_components.meltem_ventilation.modbus_client.time.sleep"),
            pytest.raises(MeltemModbusError, match="serial busy"),
        ):
            client._ensure_client()


# ---------------------------------------------------------------------------
#  close / reset_connection
# ---------------------------------------------------------------------------


class TestCloseAndReset:
    def test_close_closes_and_clears_client(self) -> None:
        client = MeltemModbusClient(_SETTINGS)
        mock_pymodbus = MagicMock()
        client._client = mock_pymodbus

        client.close()
        mock_pymodbus.close.assert_called_once()
        assert client._client is None

    def test_close_noop_when_no_client(self) -> None:
        client = MeltemModbusClient(_SETTINGS)
        client._client = None
        client.close()  # Should not raise.

    def test_reset_connection_delegates_to_close(self) -> None:
        client = MeltemModbusClient(_SETTINGS)
        mock_pymodbus = MagicMock()
        client._client = mock_pymodbus

        client.reset_connection()
        mock_pymodbus.close.assert_called_once()
        assert client._client is None


# ---------------------------------------------------------------------------
#  _read_holding_registers_with_retry
# ---------------------------------------------------------------------------


class TestReadHoldingRegistersWithRetry:
    def test_returns_response_on_success(self) -> None:
        client = MeltemModbusClient(_SETTINGS)
        mock_pymodbus = MagicMock()
        expected = _FakeResponse(registers=[42])
        mock_pymodbus.read_holding_registers.return_value = expected
        mock_pymodbus.is_socket_open.return_value = True
        client._client = mock_pymodbus

        with patch("custom_components.meltem_ventilation.modbus_client.time.sleep"):
            result = client._read_holding_registers_with_retry(
                mock_pymodbus, 2, 41000, 1
            )

        assert result.registers == [42]

    def test_retries_on_transport_exception(self) -> None:
        client = MeltemModbusClient(_SETTINGS)
        mock_pymodbus = MagicMock()
        mock_pymodbus.read_holding_registers.side_effect = [
            ConnectionError("transport fail"),
            _FakeResponse(registers=[99]),
        ]
        mock_pymodbus.is_socket_open.return_value = True
        client._client = mock_pymodbus

        # After transport error, close() clears _client.  _ensure_client()
        # then calls build_client() — patch it to return a fresh mock whose
        # read_holding_registers returns the second side_effect value.
        rebuilt = MagicMock()
        rebuilt.connect.return_value = True
        rebuilt.is_socket_open.return_value = True
        rebuilt.read_holding_registers.return_value = _FakeResponse(registers=[99])

        with (
            patch("custom_components.meltem_ventilation.modbus_client.time.sleep"),
            patch(
                "custom_components.meltem_ventilation.modbus_client.build_client",
                return_value=rebuilt,
            ),
        ):
            result = client._read_holding_registers_with_retry(
                mock_pymodbus, 2, 41000, 1
            )

        assert result.registers == [99]

    def test_raises_on_persistent_transport_error(self) -> None:
        client = MeltemModbusClient(_SETTINGS)
        mock_pymodbus = MagicMock()
        mock_pymodbus.read_holding_registers.side_effect = ConnectionError("fail")
        mock_pymodbus.is_socket_open.return_value = True
        client._client = mock_pymodbus

        # After first transport error, close() + _ensure_client() rebuilds.
        # The rebuilt client also raises, so the second attempt fails too.
        rebuilt = MagicMock()
        rebuilt.connect.return_value = True
        rebuilt.is_socket_open.return_value = True
        rebuilt.read_holding_registers.side_effect = ConnectionError("fail again")

        with (
            patch("custom_components.meltem_ventilation.modbus_client.time.sleep"),
            patch(
                "custom_components.meltem_ventilation.modbus_client.build_client",
                return_value=rebuilt,
            ),
            pytest.raises(MeltemModbusError, match="Read raised"),
        ):
            client._read_holding_registers_with_retry(mock_pymodbus, 2, 41000, 1)

    def test_raises_on_error_response(self) -> None:
        client = MeltemModbusClient(_SETTINGS)
        mock_pymodbus = MagicMock()
        mock_pymodbus.read_holding_registers.return_value = _FakeResponse(error=True)

        with (
            patch("custom_components.meltem_ventilation.modbus_client.time.sleep"),
            pytest.raises(MeltemModbusError, match="Read failed"),
        ):
            client._read_holding_registers_with_retry(mock_pymodbus, 2, 41000, 1)

    def test_raises_on_none_response(self) -> None:
        client = MeltemModbusClient(_SETTINGS)
        mock_pymodbus = MagicMock()
        mock_pymodbus.read_holding_registers.return_value = None

        with (
            patch("custom_components.meltem_ventilation.modbus_client.time.sleep"),
            pytest.raises(MeltemModbusError, match="no response|Read failed"),
        ):
            client._read_holding_registers_with_retry(mock_pymodbus, 2, 41000, 1)

    def test_raises_on_insufficient_registers(self) -> None:
        client = MeltemModbusClient(_SETTINGS)
        mock_pymodbus = MagicMock()
        mock_pymodbus.read_holding_registers.return_value = _FakeResponse(
            registers=[1]
        )

        with (
            patch("custom_components.meltem_ventilation.modbus_client.time.sleep"),
            pytest.raises(MeltemModbusError, match="insufficient|Read failed"),
        ):
            client._read_holding_registers_with_retry(
                mock_pymodbus, 2, 41000, 3
            )


# ---------------------------------------------------------------------------
#  write_level
# ---------------------------------------------------------------------------


class TestWriteLevel:
    def test_zero_level_sends_mode_off(self) -> None:
        client = MeltemModbusClient(_SETTINGS)
        mock_pymodbus = MagicMock()
        mock_pymodbus.is_socket_open.return_value = True
        mock_pymodbus.write_register.return_value = _FakeWriteResponse()
        client._client = mock_pymodbus

        with patch("custom_components.meltem_ventilation.modbus_client.time.sleep"):
            client.write_level(_ROOM, 0)

        calls = mock_pymodbus.write_register.call_args_list
        # First call: MODE
        assert calls[0] == call(address=REGISTER_MODE, value=MODE_OFF, device_id=2)
        # Second call: CURRENT_LEVEL = 0
        assert calls[1] == call(
            address=REGISTER_CURRENT_LEVEL, value=0, device_id=2
        )
        # Third call: APPLY
        assert calls[2] == call(address=REGISTER_APPLY, value=0, device_id=2)

    def test_nonzero_level_sends_mode_manual(self) -> None:
        client = MeltemModbusClient(_SETTINGS)
        mock_pymodbus = MagicMock()
        mock_pymodbus.is_socket_open.return_value = True
        mock_pymodbus.write_register.return_value = _FakeWriteResponse()
        client._client = mock_pymodbus

        with patch("custom_components.meltem_ventilation.modbus_client.time.sleep"):
            client.write_level(_ROOM, 50)

        calls = mock_pymodbus.write_register.call_args_list
        assert calls[0] == call(address=REGISTER_MODE, value=MODE_MANUAL, device_id=2)

    def test_level_scales_to_raw_200_range(self) -> None:
        """For ii_plain (max=100), level 50 → raw 100."""
        client = MeltemModbusClient(_SETTINGS)
        mock_pymodbus = MagicMock()
        mock_pymodbus.is_socket_open.return_value = True
        mock_pymodbus.write_register.return_value = _FakeWriteResponse()
        client._client = mock_pymodbus

        with patch("custom_components.meltem_ventilation.modbus_client.time.sleep"):
            client.write_level(_ROOM, 50)

        # raw = round(50 * 200 / 100) = 100
        level_call = mock_pymodbus.write_register.call_args_list[1]
        assert level_call == call(
            address=REGISTER_CURRENT_LEVEL, value=100, device_id=2
        )

    def test_level_scaling_for_s_profile(self) -> None:
        """For s_plain (max=97), level 97 → raw 200."""
        client = MeltemModbusClient(_SETTINGS)
        mock_pymodbus = MagicMock()
        mock_pymodbus.is_socket_open.return_value = True
        mock_pymodbus.write_register.return_value = _FakeWriteResponse()
        client._client = mock_pymodbus

        with patch("custom_components.meltem_ventilation.modbus_client.time.sleep"):
            client.write_level(_ROOM_S, 97)

        # raw = round(97 * 200 / 97) = 200
        level_call = mock_pymodbus.write_register.call_args_list[1]
        assert level_call == call(
            address=REGISTER_CURRENT_LEVEL, value=200, device_id=3
        )

    def test_write_error_raises(self) -> None:
        client = MeltemModbusClient(_SETTINGS)
        mock_pymodbus = MagicMock()
        mock_pymodbus.is_socket_open.return_value = True
        mock_pymodbus.write_register.return_value = _FakeWriteResponse(error=True)
        client._client = mock_pymodbus

        with (
            patch("custom_components.meltem_ventilation.modbus_client.time.sleep"),
            pytest.raises(MeltemModbusError, match="Write failed"),
        ):
            client.write_level(_ROOM, 50)


# ---------------------------------------------------------------------------
#  write_unbalanced_levels
# ---------------------------------------------------------------------------


class TestWriteUnbalancedLevels:
    def test_sends_mode_unbalanced_and_both_levels(self) -> None:
        client = MeltemModbusClient(_SETTINGS)
        mock_pymodbus = MagicMock()
        mock_pymodbus.is_socket_open.return_value = True
        mock_pymodbus.write_register.return_value = _FakeWriteResponse()
        client._client = mock_pymodbus

        with patch("custom_components.meltem_ventilation.modbus_client.time.sleep"):
            client.write_unbalanced_levels(_ROOM, 60, 40)

        calls = mock_pymodbus.write_register.call_args_list
        assert calls[0] == call(
            address=REGISTER_MODE, value=MODE_UNBALANCED, device_id=2
        )
        # Supply: raw = round(60 * 200 / 100) = 120
        assert calls[1] == call(
            address=REGISTER_CURRENT_LEVEL, value=120, device_id=2
        )
        # Extract: raw = round(40 * 200 / 100) = 80
        assert calls[2] == call(
            address=REGISTER_EXTRACT_AIR_TARGET_LEVEL, value=80, device_id=2
        )
        assert calls[3] == call(address=REGISTER_APPLY, value=0, device_id=2)

    def test_raw_levels_clamped_to_0_200(self) -> None:
        client = MeltemModbusClient(_SETTINGS)
        mock_pymodbus = MagicMock()
        mock_pymodbus.is_socket_open.return_value = True
        mock_pymodbus.write_register.return_value = _FakeWriteResponse()
        client._client = mock_pymodbus

        with patch("custom_components.meltem_ventilation.modbus_client.time.sleep"):
            client.write_unbalanced_levels(_ROOM, 999, -10)

        calls = mock_pymodbus.write_register.call_args_list
        # Supply 999 → min(200, round(999*200/100)) → 200
        assert calls[1].kwargs["value"] == 200 or calls[1][1]["value"] == 200
        # Extract -10 → max(0, round(-10*200/100)) → 0
        assert calls[2].kwargs["value"] == 0 or calls[2][1]["value"] == 0


class TestWriteOperatingMode:
    def test_humidity_control_writes_sensor_control_magic_value(self) -> None:
        client = MeltemModbusClient(_SETTINGS)
        mock_pymodbus = MagicMock()
        mock_pymodbus.is_socket_open.return_value = True
        mock_pymodbus.write_register.return_value = _FakeWriteResponse()
        client._client = mock_pymodbus

        with patch("custom_components.meltem_ventilation.modbus_client.time.sleep"):
            client.write_operating_mode(_ROOM_FC_VOC, "humidity_control", 45, 45)

        assert mock_pymodbus.write_register.call_args_list == [
            call(address=REGISTER_MODE, value=MODE_SENSOR_CONTROL, device_id=4),
            call(
                address=REGISTER_CURRENT_LEVEL,
                value=MODE_HUMIDITY_CONTROL_VALUE,
                device_id=4,
            ),
            call(address=REGISTER_APPLY, value=0, device_id=4),
        ]

    def test_co2_control_writes_sensor_control_magic_value(self) -> None:
        client = MeltemModbusClient(_SETTINGS)
        mock_pymodbus = MagicMock()
        mock_pymodbus.is_socket_open.return_value = True
        mock_pymodbus.write_register.return_value = _FakeWriteResponse()
        client._client = mock_pymodbus

        with patch("custom_components.meltem_ventilation.modbus_client.time.sleep"):
            client.write_operating_mode(_ROOM_FC_VOC, "co2_control", 45, 45)

        assert mock_pymodbus.write_register.call_args_list[0] == call(
            address=REGISTER_MODE,
            value=MODE_SENSOR_CONTROL,
            device_id=4,
        )
        assert mock_pymodbus.write_register.call_args_list[1] == call(
            address=REGISTER_CURRENT_LEVEL,
            value=MODE_CO2_CONTROL_VALUE,
            device_id=4,
        )

    def test_automatic_writes_sensor_control_magic_value(self) -> None:
        client = MeltemModbusClient(_SETTINGS)
        mock_pymodbus = MagicMock()
        mock_pymodbus.is_socket_open.return_value = True
        mock_pymodbus.write_register.return_value = _FakeWriteResponse()
        client._client = mock_pymodbus

        with patch("custom_components.meltem_ventilation.modbus_client.time.sleep"):
            client.write_operating_mode(_ROOM_FC_VOC, "automatic", 45, 45)

        assert mock_pymodbus.write_register.call_args_list[1] == call(
            address=REGISTER_CURRENT_LEVEL,
            value=MODE_AUTOMATIC_VALUE,
            device_id=4,
        )

    def test_unbalanced_mode_preserves_both_levels(self) -> None:
        client = MeltemModbusClient(_SETTINGS)
        mock_pymodbus = MagicMock()
        mock_pymodbus.is_socket_open.return_value = True
        mock_pymodbus.write_register.return_value = _FakeWriteResponse()
        client._client = mock_pymodbus

        with patch("custom_components.meltem_ventilation.modbus_client.time.sleep"):
            client.write_operating_mode(_ROOM, "unbalanced", 60, 40)

        assert mock_pymodbus.write_register.call_args_list == [
            call(address=REGISTER_MODE, value=MODE_UNBALANCED, device_id=2),
            call(address=REGISTER_CURRENT_LEVEL, value=120, device_id=2),
            call(address=REGISTER_EXTRACT_AIR_TARGET_LEVEL, value=80, device_id=2),
            call(address=REGISTER_APPLY, value=0, device_id=2),
        ]


class TestWriteControlSetting:
    def test_humidity_setting_writes_expected_register(self) -> None:
        client = MeltemModbusClient(_SETTINGS)
        mock_pymodbus = MagicMock()
        mock_pymodbus.is_socket_open.return_value = True
        mock_pymodbus.write_register.return_value = _FakeWriteResponse()
        client._client = mock_pymodbus

        with patch("custom_components.meltem_ventilation.modbus_client.time.sleep"):
            client.write_control_setting(_ROOM_FC_VOC, "humidity_starting_point", 50)

        mock_pymodbus.write_register.assert_called_once_with(
            address=REGISTER_HUMIDITY_STARTING_POINT,
            value=50,
            device_id=4,
        )

    def test_co2_setting_writes_expected_register(self) -> None:
        client = MeltemModbusClient(_SETTINGS)
        mock_pymodbus = MagicMock()
        mock_pymodbus.is_socket_open.return_value = True
        mock_pymodbus.write_register.return_value = _FakeWriteResponse()
        client._client = mock_pymodbus

        with patch("custom_components.meltem_ventilation.modbus_client.time.sleep"):
            client.write_control_setting(_ROOM_FC_VOC, "co2_starting_point", 850)

        mock_pymodbus.write_register.assert_called_once_with(
            address=REGISTER_CO2_STARTING_POINT,
            value=850,
            device_id=4,
        )


# ---------------------------------------------------------------------------
#  _write_uint16
# ---------------------------------------------------------------------------


class TestWriteUint16:
    def test_raises_on_error_response(self) -> None:
        client = MeltemModbusClient(_SETTINGS)
        mock_pymodbus = MagicMock()
        mock_pymodbus.write_register.return_value = _FakeWriteResponse(error=True)

        with (
            patch("custom_components.meltem_ventilation.modbus_client.time.sleep"),
            pytest.raises(MeltemModbusError, match="Write failed"),
        ):
            client._write_uint16(mock_pymodbus, 2, 41120, 3)

    def test_raises_on_none_response(self) -> None:
        client = MeltemModbusClient(_SETTINGS)
        mock_pymodbus = MagicMock()
        mock_pymodbus.write_register.return_value = None
        client._client = mock_pymodbus

        rebuilt = MagicMock()
        rebuilt.connect.return_value = True
        rebuilt.is_socket_open.return_value = True
        rebuilt.write_register.return_value = None

        with (
            patch("custom_components.meltem_ventilation.modbus_client.time.sleep"),
            patch(
                "custom_components.meltem_ventilation.modbus_client.build_client",
                return_value=rebuilt,
            ),
            pytest.raises(MeltemModbusError, match="no response"),
        ):
            client._write_uint16(mock_pymodbus, 2, 41120, 3)

        assert client._client is None

    def test_raises_on_transport_exception(self) -> None:
        client = MeltemModbusClient(_SETTINGS)
        mock_pymodbus = MagicMock()
        mock_pymodbus.write_register.side_effect = OSError("write failed")
        client._client = mock_pymodbus

        with pytest.raises(MeltemModbusError, match="write failed"):
            client._write_uint16(mock_pymodbus, 2, 41120, 3)

        assert client._client is None


# ---------------------------------------------------------------------------
#  read_room_state end-to-end (light integration)
# ---------------------------------------------------------------------------


class TestReadRoomStateEndToEnd:
    def test_closes_connection_on_unexpected_error(self) -> None:
        client = MeltemModbusClient(_SETTINGS)
        mock_pymodbus = MagicMock()
        mock_pymodbus.is_socket_open.return_value = True
        mock_pymodbus.read_holding_registers.side_effect = RuntimeError("unexpected")
        client._client = mock_pymodbus

        with (
            patch("custom_components.meltem_ventilation.modbus_client.time.sleep"),
            pytest.raises(MeltemModbusError, match="Unexpected error"),
        ):
            client.read_room_state(_ROOM, RoomState(), RefreshPlan())

        assert client._client is None  # Connection was closed.

    def test_reraises_modbus_error_as_is(self) -> None:
        """MeltemModbusError from _ensure_client propagates through read_room_state.

        Note: error *responses* are swallowed by _read_optional_* helpers and
        do not propagate.  The realistic scenario for MeltemModbusError
        reaching the caller is a connection-level failure in _ensure_client.
        """
        client = MeltemModbusClient(_SETTINGS)
        # No client set — _ensure_client will call build_client which we make fail.
        with (
            patch("custom_components.meltem_ventilation.modbus_client.time.sleep"),
            patch(
                "custom_components.meltem_ventilation.modbus_client.build_client",
                side_effect=lambda s: _failing_client(),
            ),
            pytest.raises(MeltemModbusError, match="Could not connect"),
        ):
            client.read_room_state(_ROOM, RoomState(), RefreshPlan())


# ---------------------------------------------------------------------------
#  _supports helper
# ---------------------------------------------------------------------------


class TestSupportsHelper:
    def test_returns_true_when_no_constraints(self) -> None:
        client = MeltemModbusClient(_SETTINGS)
        assert client._supports(_ROOM, "anything")

    def test_returns_true_for_listed_key(self) -> None:
        client = MeltemModbusClient(_SETTINGS)
        room = RoomConfig(
            key="r",
            name="R",
            profile="ii_plain",
            slave=2,
            supported_entity_keys=frozenset({"exhaust_temperature"}),
        )
        assert client._supports(room, "exhaust_temperature")
        assert not client._supports(room, "humidity_extract_air")


# ---------------------------------------------------------------------------
#  _coalesce
# ---------------------------------------------------------------------------


class TestCoalesce:
    def test_returns_value_when_not_none(self) -> None:
        client = MeltemModbusClient(_SETTINGS)
        assert client._coalesce(42, 99) == 42

    def test_returns_previous_when_none(self) -> None:
        client = MeltemModbusClient(_SETTINGS)
        assert client._coalesce(None, 99) == 99
