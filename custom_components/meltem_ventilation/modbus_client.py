"""Runtime Modbus client for Meltem Modbus ventilation units.

This module contains only the long-lived :class:`MeltemModbusClient` that the
coordinator uses for all reads and writes during normal operation.

Setup-time helpers (serial-settings builders, scans, profile probes, and pure
utility functions) live in ``modbus_helpers.py``.
"""

from __future__ import annotations

import math
import struct
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass, replace

from pymodbus.client import ModbusSerialClient
from pymodbus.exceptions import ConnectionException, ModbusIOException

from .const import (
    APP_UNBALANCED_PRESET_BASE,
    CO2_PROFILES,
    CONTROL_SETTING_LIMITS,
    CONTROL_SETTING_REGISTERS,
    HUMIDITY_PROFILES,
    MODE_MANUAL,
    MODE_OFF,
    MODE_SENSOR_CONTROL,
    MODE_UNBALANCED,
    OPERATION_MODE_MANUAL,
    OPERATION_MODE_OFF,
    OPERATION_MODE_UNBALANCED,
    PLAIN_PROFILES,
    PRESET_MODE_CODE_INTENSIVE,
    PRESET_MODE_EXTRACT_ONLY,
    PRESET_MODE_INTENSIVE,
    PRESET_MODE_SUPPLY_ONLY,
    PRESET_MODE_TO_RAW_CODE,
    RAW_CODE_TO_PRESET_MODE,
    RAW_VALUE_TO_SENSOR_MODE,
    REGISTER_APPLY,
    REGISTER_CO2_EXTRACT_AIR,
    REGISTER_CURRENT_LEVEL,
    REGISTER_DAYS_UNTIL_FILTER_CHANGE,
    REGISTER_ERROR_STATUS,
    REGISTER_EXHAUST_AIR_TEMPERATURE,
    REGISTER_EXTRACT_AIR_FLOW,
    REGISTER_EXTRACT_AIR_TARGET_LEVEL,
    REGISTER_EXTRACT_AIR_TEMPERATURE,
    REGISTER_FILTER_CHANGE_DUE,
    REGISTER_FROST_PROTECTION_ACTIVE,
    REGISTER_HUMIDITY_EXTRACT_AIR,
    REGISTER_HUMIDITY_STARTING_POINT,
    REGISTER_HUMIDITY_SUPPLY_AIR,
    REGISTER_MODE,
    REGISTER_OPERATING_HOURS,
    REGISTER_OUTDOOR_AIR_TEMPERATURE,
    REGISTER_PRESET_MODE,
    REGISTER_PRESET_VALUE,
    REGISTER_RF_COMM_STATUS,
    REGISTER_SOFTWARE_VERSION,
    REGISTER_SUPPLY_AIR_FLOW,
    REGISTER_SUPPLY_AIR_TEMPERATURE,
    REGISTER_VOC_SUPPLY_AIR,
    REQUEST_GAP_SECONDS,
    SENSOR_MODE_TO_RAW_VALUE,
    SENSOR_OPERATION_MODES,
    VOC_PROFILES,
    profile_max_airflow,
)
from .modbus_helpers import (
    MeltemConnectionError,
    MeltemModbusError,
    SerialSettings,
    build_client,
    derive_balanced_airflow,
    detect_slave_details_with_client,
    discover_gateway_nodes,
)
from .models import RefreshPlan, RoomConfig, RoomState

sync_sleep = time.sleep


def _to_optional_bool(value: int | bool | None) -> bool | None:
    """Coerce 0/1 register values to bool, preserving None."""
    if isinstance(value, bool):
        return value
    return bool(value) if value is not None else None


# pyserial raises SerialException (an OSError) for port lock/IO problems.
_RETRYABLE_EXCEPTIONS = (
    ConnectionException,
    ModbusIOException,
    OSError,
    TimeoutError,
)

# Fallback for libraries that raise plain exceptions with a descriptive message.
_RETRYABLE_MESSAGE_MARKERS = (
    "could not exclusively lock port",
    "connection reset",
    "broken pipe",
    "resource temporarily unavailable",
    "permission denied",
    "device or resource busy",
    "input/output error",
    "i/o error",
    "transport fail",
    "timed out",
    "timeout",
    "no response received",
)


@dataclass(slots=True, frozen=True)
class _ModeGroup:
    """Decoded mode and airflow-target state for one room."""

    operation_mode: str | None
    target_level: int | None
    extract_target_level: int | None
    preset_mode: str | None
    intensive_active: bool | None

    @classmethod
    def unchanged(cls, previous_state: RoomState) -> _ModeGroup:
        """Carry the previous values forward when the group is not due."""

        return cls(
            operation_mode=previous_state.operation_mode,
            target_level=previous_state.target_level,
            extract_target_level=previous_state.extract_target_level,
            preset_mode=previous_state.preset_mode,
            intensive_active=previous_state.intensive_active,
        )


class MeltemModbusClient:
    """Small synchronous wrapper around pymodbus.

    The client keeps one serial connection open for as long as it remains
    usable. Reads and writes are serialized by the coordinator, and this class
    adds one more thread-level lock so executor jobs cannot overlap either.
    """

    def __init__(self, settings: SerialSettings) -> None:
        self._settings = settings
        self._client: ModbusSerialClient | None = None
        self._lock = threading.RLock()
        self._optional_read_backoff_until: dict[tuple[int, int, int], float] = {}
        self._optional_read_failures: dict[tuple[int, int, int], int] = {}
        self._last_successful_read_by_slave: dict[int, float] = {}

    def seconds_since_successful_read(self, slave: int) -> float | None:
        """Return the age of the last answered register read for one unit.

        Every optional read swallows its error, so this is the only reliable
        signal that a unit behind the gateway went silent.
        """

        last_read = self._last_successful_read_by_slave.get(slave)
        if last_read is None:
            return None
        return time.monotonic() - last_read

    def close(self) -> None:
        """Close the underlying serial client."""

        with self._lock:
            if self._client is not None:
                self._client.close()
                self._client = None

    def ensure_connected(self) -> None:
        """Open the serial connection, raising ``MeltemModbusError`` on failure."""

        with self._lock:
            self._ensure_client()

    def reset_connection(self) -> None:
        """Drop the current serial connection so the next read reconnects cleanly."""

        self.close()

    @contextmanager
    def _gateway_operation(self, description: str):
        """Serialize one gateway operation and drop the connection on failure.

        Leaving a half-broken serial client behind makes every following
        operation fail, so any error closes it.
        """

        try:
            with self._lock:
                yield
        except MeltemModbusError:
            self.close()
            raise
        except Exception as err:
            self.close()
            raise MeltemModbusError(
                f"Unexpected error while {description}: {err!r}"
            ) from err

    def discover_gateway_units(self, start: int, end: int) -> list[int]:
        """Discover configured unit addresses using the current gateway connection."""

        with self._lock:
            client = self._ensure_client()
            return discover_gateway_nodes(
                client,
                self._settings.port,
                start=start,
                end=end,
            )

    def probe_slave_details(
        self,
        slave: int,
    ) -> tuple[str, str | None, list[str]]:
        """Probe one configured unit using the current gateway connection."""

        with self._lock:
            client = self._ensure_client()
            return detect_slave_details_with_client(client, slave)

    def read_room_state(
        self,
        room: RoomConfig,
        previous_state: RoomState | None = None,
        refresh_plan: RefreshPlan | None = None,
    ) -> RoomState:
        """Read all relevant state for one room.

        ``RefreshPlan`` decides which groups are due in the current scheduler
        tick. Values outside the plan are copied forward from ``previous_state``.
        """

        previous_state = previous_state or RoomState()
        refresh_plan = refresh_plan or RefreshPlan()

        with self._gateway_operation(f"reading room {room.key}"):
            # Fail fast on a dead transport: every read below is optional
            # and would otherwise silently report "nothing changed".
            self._ensure_client()

            if refresh_plan.refresh_airflow:
                # Airflow drives the UI and post-write confirmation, so it
                # gets its own fast path.
                extract_air_flow, supply_air_flow = self._read_airflow_pair(
                    room,
                    previous_state,
                )
            else:
                extract_air_flow = previous_state.extract_air_flow
                supply_air_flow = previous_state.supply_air_flow

            environment = self._read_profile_state(room, previous_state, refresh_plan)
            error, filter_due, frost = self._read_status_group(
                room, previous_state, refresh_plan
            )
            days, hours, software_version = self._read_maintenance_group(
                room, previous_state, refresh_plan
            )
            control_settings = self._read_control_settings_group(
                room, previous_state, refresh_plan
            )
            rf_comm_status = self._read_uint16_if_due(
                room,
                "rf_comm_status",
                REGISTER_RF_COMM_STATUS,
                previous_state.rf_comm_status,
                refresh_plan.refresh_status,
            )

            if refresh_plan.refresh_airflow:
                mode = self._read_mode_group(
                    room,
                    previous_state,
                    extract_air_flow,
                    supply_air_flow,
                )
            else:
                mode = _ModeGroup.unchanged(previous_state)

        # ``environment`` already carries the temperature and air-quality fields.
        return replace(
            environment,
            error_status=_to_optional_bool(error),
            filter_change_due=_to_optional_bool(filter_due),
            frost_protection_active=_to_optional_bool(frost),
            rf_comm_status=_to_optional_bool(rf_comm_status),
            extract_air_flow=extract_air_flow,
            supply_air_flow=supply_air_flow,
            operation_mode=mode.operation_mode,
            preset_mode=mode.preset_mode,
            intensive_active=mode.intensive_active,
            days_until_filter_change=days,
            operating_hours=hours,
            software_version=software_version,
            target_level=mode.target_level,
            extract_target_level=mode.extract_target_level,
            **control_settings,
        )

    def write_level(self, room: RoomConfig, level: int) -> None:
        """Write off/manual mode and target level for one room."""

        mode = MODE_OFF if level == 0 else MODE_MANUAL
        raw_level = self._scale_airflow_to_raw(room, level)

        with self._gateway_operation(f"writing level for room {room.key}"):
            self._write_uint16(room.slave, REGISTER_MODE, mode)
            self._write_uint16(room.slave, REGISTER_CURRENT_LEVEL, raw_level)
            self._write_uint16(room.slave, REGISTER_APPLY, 0)
            self._clear_optional_airflow_read_backoff(room.slave)

    def write_unbalanced_levels(
        self, room: RoomConfig, supply_level: int, extract_level: int
    ) -> None:
        """Write unbalanced mode with separate supply and extract levels."""

        raw_supply_level = self._scale_airflow_to_raw(room, supply_level)
        raw_extract_level = self._scale_airflow_to_raw(room, extract_level)

        with self._gateway_operation(
            f"writing unbalanced levels for room {room.key}"
        ):
            self._write_uint16(room.slave, REGISTER_MODE, MODE_UNBALANCED)
            self._write_uint16(room.slave, REGISTER_CURRENT_LEVEL, raw_supply_level)
            self._write_uint16(
                room.slave, REGISTER_EXTRACT_AIR_TARGET_LEVEL, raw_extract_level
            )
            self._write_uint16(room.slave, REGISTER_APPLY, 0)
            self._clear_optional_airflow_read_backoff(room.slave)

    def write_operating_mode(
        self,
        room: RoomConfig,
        operation_mode: str,
        balanced_level: int,
        extract_level: int,
    ) -> None:
        """Write one operating mode using the documented control registers."""

        with self._gateway_operation(f"writing operating mode for room {room.key}"):
            if operation_mode == OPERATION_MODE_OFF:
                self._write_uint16(room.slave, REGISTER_MODE, MODE_OFF)
                self._write_uint16(room.slave, REGISTER_CURRENT_LEVEL, 0)
            elif operation_mode == OPERATION_MODE_MANUAL:
                self._write_uint16(room.slave, REGISTER_MODE, MODE_MANUAL)
                self._write_uint16(
                    room.slave,
                    REGISTER_CURRENT_LEVEL,
                    self._scale_airflow_to_raw(room, balanced_level),
                )
            elif operation_mode == OPERATION_MODE_UNBALANCED:
                self._write_uint16(room.slave, REGISTER_MODE, MODE_UNBALANCED)
                self._write_uint16(
                    room.slave,
                    REGISTER_CURRENT_LEVEL,
                    self._scale_airflow_to_raw(room, balanced_level),
                )
                self._write_uint16(
                    room.slave,
                    REGISTER_EXTRACT_AIR_TARGET_LEVEL,
                    self._scale_airflow_to_raw(room, extract_level),
                )
            else:
                sensor_control_value = SENSOR_MODE_TO_RAW_VALUE.get(operation_mode)
                if sensor_control_value is None:
                    raise MeltemModbusError(
                        f"Unsupported operating mode {operation_mode!r} for room {room.key}"
                    )
                self._write_uint16(room.slave, REGISTER_MODE, MODE_SENSOR_CONTROL)
                self._write_uint16(
                    room.slave, REGISTER_CURRENT_LEVEL, sensor_control_value
                )
            self._write_uint16(room.slave, REGISTER_APPLY, 0)
            self._clear_optional_airflow_read_backoff(room.slave)

    def write_preset_mode(
        self,
        room: RoomConfig,
        preset_mode: str,
    ) -> None:
        """Write one confirmed app-style preset mode."""

        raw_code = PRESET_MODE_TO_RAW_CODE.get(preset_mode)
        if raw_code is None:
            raise MeltemModbusError(
                f"Unsupported preset mode {preset_mode!r} for room {room.key}"
            )

        with self._gateway_operation(f"writing preset mode for room {room.key}"):
            if preset_mode == PRESET_MODE_INTENSIVE:
                self._write_uint16(room.slave, REGISTER_PRESET_MODE, MODE_MANUAL)
                self._write_uint16(room.slave, REGISTER_PRESET_VALUE, raw_code)
            else:
                self._clear_secondary_preset_registers(room.slave)
                self._write_uint16(room.slave, REGISTER_MODE, MODE_MANUAL)
                self._write_uint16(room.slave, REGISTER_CURRENT_LEVEL, raw_code)
            self._write_uint16(room.slave, REGISTER_APPLY, 0)
            self._clear_optional_airflow_read_backoff(room.slave)

    def clear_intensive(self, room: RoomConfig) -> None:
        """Cancel a running intensive override.

        Only the dedicated shadow registers are touched, so the base quick mode
        and the airflow targets stay untouched.
        """

        with self._gateway_operation(f"clearing intensive mode for room {room.key}"):
            self._clear_secondary_preset_registers(room.slave)
            self._write_uint16(room.slave, REGISTER_APPLY, 0)
            self._clear_optional_airflow_read_backoff(room.slave)

    def write_control_setting(
        self,
        room: RoomConfig,
        setting_key: str,
        value: int,
    ) -> int:
        """Write one humidity/CO2 control setting register."""

        register = CONTROL_SETTING_REGISTERS.get(setting_key)
        limits = CONTROL_SETTING_LIMITS.get(setting_key)
        if register is None or limits is None:
            raise MeltemModbusError(
                f"Unsupported control setting {setting_key!r} for room {room.key}"
            )

        min_val, max_val, step = limits
        clamped = max(min_val, min(max_val, int(round(value))))
        stepped = min_val + ((clamped - min_val + step // 2) // step) * step

        with self._gateway_operation(f"writing control setting for room {room.key}"):
            self._write_uint16(room.slave, register, stepped)
        return stepped

    # ------------------------------------------------------------------
    #  Connection management
    # ------------------------------------------------------------------

    def _ensure_client(self) -> ModbusSerialClient:
        """Return a connected client, rebuilding it if needed."""
        last_error: Exception | None = None
        if self._client is not None:
            try:
                if self._client.is_socket_open():
                    return self._client
            except Exception as err:
                last_error = err

            # Socket not open — try to reconnect the existing client object.
            try:
                if self._client.connect():
                    return self._client
            except Exception as err:
                last_error = err

            # Discard the stale client before building a new one.
            try:
                self._client.close()
            except Exception:
                pass
            self._client = None

        # Create a fresh client with retries so the OS has time to release the
        # exclusive serial-port lock after a previous close().
        for connect_attempt in range(3):
            if connect_attempt > 0:
                sync_sleep(0.5)
            self._client = build_client(self._settings)
            try:
                if self._client.connect():
                    return self._client
            except Exception as err:
                last_error = err
            try:
                self._client.close()
            except Exception:
                pass
            self._client = None

        if last_error is not None:
            raise MeltemConnectionError(
                f"Could not connect to Meltem gateway on {self._settings.port}: {last_error}"
            ) from last_error
        raise MeltemConnectionError(
            f"Could not connect to Meltem gateway on {self._settings.port}"
        )

    # ------------------------------------------------------------------
    #  Low-level register access (runtime, with retry)
    # ------------------------------------------------------------------

    def _read_holding_registers_with_retry(
        self,
        slave: int,
        address: int,
        count: int,
        *,
        attempts: int = 2,
    ):
        """Read holding registers with one retry on transient failures.

        The connection is resolved per attempt, so a reconnect during the retry
        is picked up by every following read instead of reusing a dead client.
        Modbus error responses do not force a reconnect, because they still
        prove that the gateway link itself is alive.
        """

        last_error: Exception | None = None

        for attempt in range(1, attempts + 1):
            client = self._ensure_client()
            try:
                response = client.read_holding_registers(
                    address=address,
                    count=count,
                    device_id=slave,
                )
            except Exception as err:
                # Transport-/lock-level failure — close and reconnect for next try.
                last_error = err
                should_retry = self._is_retryable_transport_error(err)
                self.close()
                if should_retry and attempt < attempts:
                    sync_sleep(0.5)  # let OS release the serial port lock
                    continue
                raise MeltemModbusError(
                    f"Read raised {type(err).__name__} for slave {slave} register {address}: {err}"
                ) from err

            sync_sleep(REQUEST_GAP_SECONDS)

            if response is None:
                # No answer at all, so treat the transport as suspect.
                last_error = MeltemModbusError(
                    f"Read returned no response for slave {slave} register {address}"
                )
                self.close()
                if attempt < attempts:
                    sync_sleep(0.5)
                    continue
                raise last_error

            if response.isError():
                last_error = MeltemModbusError(
                    f"Read failed for slave {slave} register {address}: {response}"
                )
            elif not hasattr(response, "registers") or len(response.registers) < count:
                last_error = MeltemModbusError(
                    f"Read returned insufficient registers for slave {slave} register {address}"
                )
            else:
                self._last_successful_read_by_slave[slave] = time.monotonic()
                return response

            # The gateway answered, so keep the transport open and just back off.
            if attempt < attempts:
                sync_sleep(REQUEST_GAP_SECONDS)

        if isinstance(last_error, MeltemModbusError):
            raise last_error
        raise MeltemModbusError(
            f"Read failed for slave {slave} register {address}: {last_error!r}"
        )

    def _read_uint16(self, slave: int, address: int) -> int | None:
        response = self._read_holding_registers_with_retry(slave, address, 1)
        return response.registers[0]

    def _read_float32_word_swap(self, slave: int, address: int) -> float | None:
        response = self._read_holding_registers_with_retry(slave, address, 2)
        registers = response.registers
        # The gateway exposes these temperatures as float32 with swapped words.
        value = struct.unpack(">f", struct.pack(">HH", registers[1], registers[0]))[0]
        return value if math.isfinite(value) else None

    def _read_uint32_word_swap(self, slave: int, address: int) -> int | None:
        response = self._read_holding_registers_with_retry(slave, address, 2)
        registers = response.registers
        return struct.unpack(">I", struct.pack(">HH", registers[1], registers[0]))[0]

    def _read_uint16_block(self, slave: int, address: int, count: int) -> list[int]:
        response = self._read_holding_registers_with_retry(
            slave,
            address,
            count,
        )
        return list(response.registers[:count])

    @staticmethod
    def _decode_float32_from_block(
        block: list[int],
        *,
        start_address: int,
        address: int,
    ) -> float | None:
        """Decode one float32 value from a word-swapped register block."""

        index = address - start_address
        if index < 0 or index + 1 >= len(block):
            return None
        value = struct.unpack(">f", struct.pack(">HH", block[index + 1], block[index]))[0]
        return value if math.isfinite(value) else None

    @staticmethod
    def _decode_uint16_from_block(
        block: list[int],
        *,
        start_address: int,
        address: int,
    ) -> int | None:
        """Pick one register out of a block by its documented address."""

        index = address - start_address
        if index < 0 or index >= len(block):
            return None
        return block[index]

    # ------------------------------------------------------------------
    #  Optional reads (swallow errors, return None)
    # ------------------------------------------------------------------

    def _read_optional_uint16(self, slave: int, address: int) -> int | None:
        try:
            return self._read_uint16(slave, address)
        except MeltemConnectionError:
            raise
        except MeltemModbusError:
            return None

    def _read_optional_uint16_block(
        self, slave: int, address: int, count: int
    ) -> list[int] | None:
        try:
            return self._read_uint16_block(slave, address, count)
        except MeltemConnectionError:
            raise
        except MeltemModbusError:
            return None

    def _read_optional_airflow_uint16(self, slave: int, address: int) -> int | None:
        """Read one optional airflow-adjacent register with temporary backoff."""

        key = (slave, address, 1)
        if self._is_optional_read_backed_off(key):
            return None

        try:
            value = self._read_uint16(slave, address)
        except MeltemConnectionError:
            raise
        except MeltemModbusError:
            self._mark_optional_read_failure(key)
            return None

        self._clear_optional_read_failure(key)
        return value

    def _read_optional_airflow_uint16_block(
        self, slave: int, address: int, count: int
    ) -> list[int] | None:
        """Read one optional airflow-adjacent block with temporary backoff."""

        key = (slave, address, count)
        if self._is_optional_read_backed_off(key):
            return None

        try:
            value = self._read_uint16_block(slave, address, count)
        except MeltemConnectionError:
            raise
        except MeltemModbusError:
            self._mark_optional_read_failure(key)
            return None

        self._clear_optional_read_failure(key)
        return value

    def _is_optional_read_backed_off(self, key: tuple[int, int, int]) -> bool:
        """Return whether one optional register read is temporarily suppressed."""

        backoff_until = self._optional_read_backoff_until.get(key)
        if backoff_until is None:
            return False
        if time.monotonic() >= backoff_until:
            self._optional_read_backoff_until.pop(key, None)
            return False
        return True

    def _mark_optional_read_failure(self, key: tuple[int, int, int]) -> None:
        """Increase backoff after one optional register read failed."""

        failures = self._optional_read_failures.get(key, 0) + 1
        self._optional_read_failures[key] = failures
        delay_seconds = min(300.0, 30.0 * (2 ** (failures - 1)))
        self._optional_read_backoff_until[key] = time.monotonic() + delay_seconds

    def _clear_optional_read_failure(self, key: tuple[int, int, int]) -> None:
        """Clear any failure/backoff state after a successful optional read."""

        self._optional_read_failures.pop(key, None)
        self._optional_read_backoff_until.pop(key, None)

    def _clear_optional_airflow_read_backoff(self, slave: int) -> None:
        """Clear airflow-related optional read backoff after a successful write."""

        for key in (
            (slave, REGISTER_MODE, 2),
            (slave, REGISTER_MODE, 5),
            (slave, REGISTER_CURRENT_LEVEL, 1),
            (slave, REGISTER_EXTRACT_AIR_TARGET_LEVEL, 1),
        ):
            self._clear_optional_read_failure(key)

    def _read_optional_float32_word_swap(
        self, slave: int, address: int
    ) -> float | None:
        try:
            return self._read_float32_word_swap(slave, address)
        except MeltemConnectionError:
            raise
        except MeltemModbusError:
            return None

    def _read_optional_uint32_word_swap(
        self, slave: int, address: int
    ) -> int | None:
        try:
            return self._read_uint32_word_swap(slave, address)
        except MeltemConnectionError:
            raise
        except MeltemModbusError:
            return None

    # ------------------------------------------------------------------
    #  Conditional / grouped reads
    # ------------------------------------------------------------------

    def _read_uint16_if_due(
        self,
        room: RoomConfig,
        key: str,
        register: int,
        previous: int | None,
        should_refresh: bool,
    ) -> int | None:
        """Read one uint16 register if supported and due."""
        if not (self._supports(room, key) and should_refresh):
            return previous
        return self._coalesce(
            self._read_optional_uint16(room.slave, register),
            previous,
        )

    def _read_uint32_if_due(
        self,
        room: RoomConfig,
        key: str,
        register: int,
        previous: int | None,
        should_refresh: bool,
    ) -> int | None:
        """Read one uint32 register if supported and due."""
        if not (self._supports(room, key) and should_refresh):
            return previous
        return self._coalesce(
            self._read_optional_uint32_word_swap(room.slave, register),
            previous,
        )

    def _read_temperature_if_due(
        self,
        room: RoomConfig,
        key: str,
        register: int,
        previous: float | None,
        should_refresh: bool,
    ) -> float | None:
        """Read one float32 temperature register if supported and due."""
        if not (self._supports(room, key) and should_refresh):
            return previous
        return self._coalesce(
            self._read_optional_float32_word_swap(room.slave, register),
            previous,
        )

    def _read_airflow_pair(
        self,
        room: RoomConfig,
        previous_state: RoomState,
    ) -> tuple[int | None, int | None]:
        """Read extract/supply airflow."""

        supports_extract = self._supports(room, "extract_air_flow")
        supports_supply = self._supports(room, "supply_air_flow")

        extract_air_flow = previous_state.extract_air_flow
        supply_air_flow = previous_state.supply_air_flow

        block = None
        if supports_extract or supports_supply:
            # These registers are adjacent and benchmark well as a single read.
            block = self._read_optional_uint16_block(
                room.slave,
                REGISTER_EXTRACT_AIR_FLOW,
                REGISTER_SUPPLY_AIR_FLOW - REGISTER_EXTRACT_AIR_FLOW + 1,
            )

        if supports_extract and block is not None:
            extract_air_flow = self._coalesce(
                self._decode_uint16_from_block(
                    block,
                    start_address=REGISTER_EXTRACT_AIR_FLOW,
                    address=REGISTER_EXTRACT_AIR_FLOW,
                ),
                previous_state.extract_air_flow,
            )

        if supports_supply and block is not None:
            supply_air_flow = self._coalesce(
                self._decode_uint16_from_block(
                    block,
                    start_address=REGISTER_EXTRACT_AIR_FLOW,
                    address=REGISTER_SUPPLY_AIR_FLOW,
                ),
                previous_state.supply_air_flow,
            )

        return extract_air_flow, supply_air_flow

    def _read_status_group(
        self,
        room: RoomConfig,
        previous_state: RoomState,
        refresh_plan: RefreshPlan,
    ) -> tuple[int | bool | None, int | bool | None, int | bool | None]:
        """Read error/filter/frost as one compact status block when due."""

        supports_error = self._supports(room, "error_status")
        supports_filter = self._supports(room, "filter_change_due")
        supports_frost = self._supports(room, "frost_protection_active")

        should_refresh_error = supports_error and refresh_plan.refresh_status
        should_refresh_filter = supports_filter and refresh_plan.refresh_filter_change_due
        should_refresh_frost = supports_frost and refresh_plan.refresh_status

        error = previous_state.error_status
        filter_due = previous_state.filter_change_due
        frost = previous_state.frost_protection_active

        if should_refresh_error or should_refresh_filter or should_refresh_frost:
            block = self._read_optional_uint16_block(
                room.slave,
                REGISTER_ERROR_STATUS,
                REGISTER_FROST_PROTECTION_ACTIVE - REGISTER_ERROR_STATUS + 1,
            )
            if block is not None:
                if should_refresh_error:
                    error = self._coalesce(
                        self._decode_uint16_from_block(
                            block,
                            start_address=REGISTER_ERROR_STATUS,
                            address=REGISTER_ERROR_STATUS,
                        ),
                        error,
                    )
                if should_refresh_filter:
                    filter_due = self._coalesce(
                        self._decode_uint16_from_block(
                            block,
                            start_address=REGISTER_ERROR_STATUS,
                            address=REGISTER_FILTER_CHANGE_DUE,
                        ),
                        filter_due,
                    )
                if should_refresh_frost:
                    frost = self._coalesce(
                        self._decode_uint16_from_block(
                            block,
                            start_address=REGISTER_ERROR_STATUS,
                            address=REGISTER_FROST_PROTECTION_ACTIVE,
                        ),
                        frost,
                    )

        return error, filter_due, frost

    def _read_control_settings_group(
        self,
        room: RoomConfig,
        previous_state: RoomState,
        refresh_plan: RefreshPlan,
    ) -> dict[str, int | None]:
        """Read the contiguous humidity/CO2 control setting block when due."""

        previous_values = {
            key: getattr(previous_state, key) for key in CONTROL_SETTING_REGISTERS
        }
        should_refresh = refresh_plan.refresh_control_settings and any(
            self._supports(room, key) for key in CONTROL_SETTING_REGISTERS
        )
        if not should_refresh:
            return previous_values

        start_address = REGISTER_HUMIDITY_STARTING_POINT
        block = self._read_optional_uint16_block(
            room.slave,
            start_address,
            len(CONTROL_SETTING_REGISTERS),
        )
        if block is None:
            return previous_values

        return {
            key: (
                self._coalesce(
                    self._decode_uint16_from_block(
                        block,
                        start_address=start_address,
                        address=register,
                    ),
                    previous_values[key],
                )
                if self._supports(room, key)
                else previous_values[key]
            )
            for key, register in CONTROL_SETTING_REGISTERS.items()
        }

    def _read_maintenance_group(
        self,
        room: RoomConfig,
        previous_state: RoomState,
        refresh_plan: RefreshPlan,
    ) -> tuple[int | None, int | None, int | None]:
        """Read the slow-moving filter, runtime, and firmware values when due."""

        days = self._read_uint16_if_due(
            room, "days_until_filter_change",
            REGISTER_DAYS_UNTIL_FILTER_CHANGE,
            previous_state.days_until_filter_change,
            refresh_plan.refresh_filter_days,
        )
        hours = self._read_uint32_if_due(
            room, "operating_hours",
            REGISTER_OPERATING_HOURS,
            previous_state.operating_hours,
            refresh_plan.refresh_operating_hours,
        )
        software_version = (
            self._coalesce(
                self._read_optional_uint16(room.slave, REGISTER_SOFTWARE_VERSION),
                previous_state.software_version,
            )
            if refresh_plan.refresh_operating_hours
            else previous_state.software_version
        )
        return days, hours, software_version

    def _read_mode_block(
        self,
        room: RoomConfig,
    ) -> tuple[list[int] | None, bool]:
        """Read the mode register block, falling back to a shorter read.

        Returns the block and whether the full 5-register variant was readable;
        many units reject the longer read until a write has occurred.
        """

        needs_full_mode_block = (
            self._supports(room, "operation_mode")
            or self._supports(room, "preset_mode")
            or self._supports(room, "intensive")
        )
        if not needs_full_mode_block:
            return None, False

        mode_block = self._read_optional_airflow_uint16_block(
            room.slave,
            REGISTER_MODE,
            5,
        )
        if mode_block is not None and len(mode_block) >= 5:
            return mode_block, True

        if self._supports(room, "operation_mode") or self._supports(room, "preset_mode"):
            mode_block = self._read_optional_airflow_uint16_block(
                room.slave,
                REGISTER_MODE,
                2,
            )
        return mode_block, False

    def _read_mode_group(
        self,
        room: RoomConfig,
        previous_state: RoomState,
        extract_air_flow: int | None,
        supply_air_flow: int | None,
    ) -> _ModeGroup:
        """Read and decode operating mode, airflow targets, and preset state."""

        mode_block, full_mode_block_available = self._read_mode_block(room)

        operation_mode = (
            self._decode_operation_mode(mode_block[0], mode_block[1])
            if mode_block is not None and len(mode_block) >= 2
            else previous_state.operation_mode
        )
        raw_current_level = self._read_optional_airflow_uint16(
            room.slave,
            REGISTER_CURRENT_LEVEL,
        )

        raw_extract_target: int | None = None
        if operation_mode == OPERATION_MODE_UNBALANCED:
            target_level = self._decode_unbalanced_target_readback(
                room,
                raw_current_level,
            )
            raw_extract_target = self._read_optional_airflow_uint16(
                room.slave,
                REGISTER_EXTRACT_AIR_TARGET_LEVEL,
            )
            extract_target_level = (
                self._decode_unbalanced_target_readback(room, raw_extract_target)
                if raw_extract_target is not None
                else previous_state.extract_target_level
            )
        elif operation_mode in SENSOR_OPERATION_MODES:
            # Here 41121 holds the mode selector (112/144/16), not an airflow.
            # Decoding it as a level would yield plausible-looking nonsense.
            target_level = derive_balanced_airflow(extract_air_flow, supply_air_flow)
            extract_target_level = None
        else:
            # On the tested gateway, REGISTER_CURRENT_LEVEL behaves as a fast
            # target readback after balanced writes even though the vendor docs
            # describe it primarily as a write path. The airflow registers can
            # lag noticeably behind after a write, so use 41121 for target
            # confirmation when it looks like a valid balanced raw level and
            # fall back to derived airflow otherwise.
            target_level = self._decode_balanced_target_readback(
                room,
                raw_current_level,
                extract_air_flow,
                supply_air_flow,
            )
            extract_target_level = None

        return _ModeGroup(
            operation_mode=operation_mode,
            target_level=target_level,
            extract_target_level=extract_target_level,
            preset_mode=self._decode_preset_mode_with_fallback(
                mode_block=mode_block,
                full_mode_block_available=full_mode_block_available,
                operation_mode=operation_mode,
                raw_current_level=raw_current_level,
                raw_extract_target=raw_extract_target,
                previous_preset_mode=previous_state.preset_mode,
            ),
            intensive_active=self._decode_intensive_active(
                mode_block=mode_block,
                full_mode_block_available=full_mode_block_available,
                previous_intensive_active=previous_state.intensive_active,
            ),
        )

    def _read_profile_state(
        self,
        room: RoomConfig,
        previous_state: RoomState,
        refresh_plan: RefreshPlan,
    ) -> RoomState:
        prev = previous_state
        do_temp = refresh_plan.refresh_temperatures
        do_env = refresh_plan.refresh_environment

        if room.profile in PLAIN_PROFILES:
            # Plain units only expose the exhaust-air temperature according to
            # the Meltem unit matrix. They do not expose the other temperature
            # points or humidity/CO2/VOC values.
            exhaust = self._read_temperature_if_due(
                room,
                "exhaust_temperature",
                REGISTER_EXHAUST_AIR_TEMPERATURE,
                prev.exhaust_temperature,
                do_temp,
            )
            return RoomState(
                exhaust_temperature=exhaust,
                outdoor_air_temperature=prev.outdoor_air_temperature,
                extract_air_temperature=prev.extract_air_temperature,
                supply_air_temperature=prev.supply_air_temperature,
            )

        exhaust = prev.exhaust_temperature
        outdoor = prev.outdoor_air_temperature
        extract = prev.extract_air_temperature
        supply = prev.supply_air_temperature

        need_main_temp_block = any(
            (
                self._supports(room, "exhaust_temperature") and do_temp,
                self._supports(room, "outdoor_air_temperature") and do_env,
                self._supports(room, "extract_air_temperature") and do_temp,
            )
        )
        if need_main_temp_block:
            main_temp_block = self._read_optional_uint16_block(
                room.slave,
                REGISTER_EXTRACT_AIR_TEMPERATURE,
                6,
            )
            if main_temp_block is not None:
                if self._supports(room, "exhaust_temperature") and do_temp:
                    exhaust = self._coalesce(
                        self._decode_float32_from_block(
                            main_temp_block,
                            start_address=REGISTER_EXTRACT_AIR_TEMPERATURE,
                            address=REGISTER_EXHAUST_AIR_TEMPERATURE,
                        ),
                        prev.exhaust_temperature,
                    )
                if self._supports(room, "outdoor_air_temperature") and do_env:
                    outdoor = self._coalesce(
                        self._decode_float32_from_block(
                            main_temp_block,
                            start_address=REGISTER_EXTRACT_AIR_TEMPERATURE,
                            address=REGISTER_OUTDOOR_AIR_TEMPERATURE,
                        ),
                        prev.outdoor_air_temperature,
                    )
                if self._supports(room, "extract_air_temperature") and do_temp:
                    extract = self._coalesce(
                        self._decode_float32_from_block(
                            main_temp_block,
                            start_address=REGISTER_EXTRACT_AIR_TEMPERATURE,
                            address=REGISTER_EXTRACT_AIR_TEMPERATURE,
                        ),
                        prev.extract_air_temperature,
                    )

        if self._supports(room, "supply_air_temperature") and do_temp:
            supply = self._coalesce(
                self._read_optional_float32_word_swap(
                    room.slave,
                    REGISTER_SUPPLY_AIR_TEMPERATURE,
                ),
                prev.supply_air_temperature,
            )

        humidity_extract = prev.humidity_extract_air
        humidity_supply = prev.humidity_supply_air
        co2 = prev.co2_extract_air
        voc = prev.voc_supply_air

        need_extract_env_block = any(
            (
                room.profile in HUMIDITY_PROFILES and self._supports(room, "humidity_extract_air") and do_env,
                room.profile in CO2_PROFILES and self._supports(room, "co2_extract_air") and do_env,
            )
        )
        if need_extract_env_block:
            extract_env_block = self._read_optional_uint16_block(
                room.slave,
                REGISTER_HUMIDITY_EXTRACT_AIR,
                REGISTER_CO2_EXTRACT_AIR - REGISTER_HUMIDITY_EXTRACT_AIR + 1,
            )
            if extract_env_block is not None:
                if room.profile in HUMIDITY_PROFILES and self._supports(room, "humidity_extract_air") and do_env:
                    humidity_extract = self._coalesce(
                        self._decode_uint16_from_block(
                            extract_env_block,
                            start_address=REGISTER_HUMIDITY_EXTRACT_AIR,
                            address=REGISTER_HUMIDITY_EXTRACT_AIR,
                        ),
                        prev.humidity_extract_air,
                    )
                if room.profile in CO2_PROFILES and self._supports(room, "co2_extract_air") and do_env:
                    co2 = self._coalesce(
                        self._decode_uint16_from_block(
                            extract_env_block,
                            start_address=REGISTER_HUMIDITY_EXTRACT_AIR,
                            address=REGISTER_CO2_EXTRACT_AIR,
                        ),
                        prev.co2_extract_air,
                    )

        need_supply_env_block = any(
            (
                room.profile in HUMIDITY_PROFILES and self._supports(room, "humidity_supply_air") and do_env,
                room.profile in VOC_PROFILES and self._supports(room, "voc_supply_air") and do_env,
            )
        )
        if need_supply_env_block:
            supply_env_block = self._read_optional_uint16_block(
                room.slave,
                REGISTER_HUMIDITY_SUPPLY_AIR,
                REGISTER_VOC_SUPPLY_AIR - REGISTER_HUMIDITY_SUPPLY_AIR + 1,
            )
            if supply_env_block is not None:
                if room.profile in HUMIDITY_PROFILES and self._supports(room, "humidity_supply_air") and do_env:
                    humidity_supply = self._coalesce(
                        self._decode_uint16_from_block(
                            supply_env_block,
                            start_address=REGISTER_HUMIDITY_SUPPLY_AIR,
                            address=REGISTER_HUMIDITY_SUPPLY_AIR,
                        ),
                        prev.humidity_supply_air,
                    )
                if room.profile in VOC_PROFILES and self._supports(room, "voc_supply_air") and do_env:
                    voc = self._coalesce(
                        self._decode_uint16_from_block(
                            supply_env_block,
                            start_address=REGISTER_HUMIDITY_SUPPLY_AIR,
                            address=REGISTER_VOC_SUPPLY_AIR,
                        ),
                        prev.voc_supply_air,
                    )

        return RoomState(
            exhaust_temperature=exhaust,
            outdoor_air_temperature=outdoor,
            extract_air_temperature=extract,
            supply_air_temperature=supply,
            humidity_extract_air=humidity_extract,
            humidity_supply_air=humidity_supply,
            co2_extract_air=co2,
            voc_supply_air=voc,
        )

    # ------------------------------------------------------------------
    #  Write helpers
    # ------------------------------------------------------------------

    def _write_uint16(
        self,
        slave: int,
        address: int,
        value: int,
        *,
        attempts: int = 2,
    ) -> None:
        """Write one register, retrying once after a transient transport failure."""

        last_error: Exception | None = None

        for attempt in range(1, attempts + 1):
            client = self._ensure_client()
            try:
                response = client.write_register(
                    address=address, value=value, device_id=slave
                )
            except Exception as err:
                last_error = err
                should_retry = self._is_retryable_transport_error(err)
                self.close()
                if should_retry and attempt < attempts:
                    sync_sleep(0.5)
                    continue
                raise MeltemModbusError(
                    f"Write raised {type(err).__name__} for slave {slave} register {address}: {err}"
                ) from err

            sync_sleep(REQUEST_GAP_SECONDS)
            if response is None:
                last_error = MeltemModbusError(
                    f"Write returned no response for slave {slave} register {address}"
                )
                self.close()
                if attempt < attempts:
                    sync_sleep(0.5)
                    continue
                raise last_error

            if response.isError():
                raise MeltemModbusError(
                    f"Write failed for slave {slave} register {address}: {response}"
                )

            return

    def _clear_secondary_preset_registers(self, slave: int) -> None:
        """Clear the dedicated intensive-preset shadow registers before other presets."""

        self._write_uint16(slave, REGISTER_PRESET_MODE, 0)
        self._write_uint16(slave, REGISTER_PRESET_VALUE, 0)

    # ------------------------------------------------------------------
    #  Tiny helpers
    # ------------------------------------------------------------------

    def _coalesce(self, value, previous_value):
        return previous_value if value is None else value

    def _is_retryable_transport_error(self, err: Exception) -> bool:
        """Return whether an exception looks like a transient lock/transport issue."""

        if isinstance(err, _RETRYABLE_EXCEPTIONS):
            return True
        message = str(err).lower()
        return any(marker in message for marker in _RETRYABLE_MESSAGE_MARKERS)

    def _scale_airflow_to_raw(self, room: RoomConfig, level: int) -> int:
        max_airflow = profile_max_airflow(room.profile)
        return max(0, min(200, round(level * 200 / max_airflow)))

    def _scale_raw_level_to_airflow(
        self, room: RoomConfig, raw_level: int | None
    ) -> int | None:
        if raw_level is None:
            return None
        return round(raw_level * profile_max_airflow(room.profile) / 200)

    def _decode_balanced_target_readback(
        self,
        room: RoomConfig,
        raw_level: int | None,
        extract_air_flow: int | None,
        supply_air_flow: int | None,
    ) -> int | None:
        """Return a balanced target readback or fall back to measured airflow."""

        if raw_level is not None and 0 <= raw_level <= 200:
            return self._scale_raw_level_to_airflow(room, raw_level)

        # A shared "current level" only makes sense when both airflow
        # directions are effectively balanced.
        return derive_balanced_airflow(extract_air_flow, supply_air_flow)

    def _decode_unbalanced_target_readback(
        self,
        room: RoomConfig,
        raw_level: int | None,
    ) -> int | None:
        """Decode one unbalanced target, including app-side preset encodings."""

        if raw_level is None:
            return None
        if 0 <= raw_level <= 200:
            return self._scale_raw_level_to_airflow(room, raw_level)
        if raw_level > APP_UNBALANCED_PRESET_BASE:
            # The app encodes the shortcut airflow as 200 + m3/h per 10. Quick
            # mode codes land in the same range but decode far above the rated
            # airflow, so they must not be reported as a target.
            max_airflow = profile_max_airflow(room.profile)
            airflow = (raw_level - APP_UNBALANCED_PRESET_BASE) * 10
            return airflow if airflow <= max_airflow else None
        return None

    def _decode_operation_mode(
        self,
        mode_value: int | None,
        current_value: int | None,
    ) -> str | None:
        if mode_value == MODE_OFF:
            return OPERATION_MODE_OFF
        if mode_value == MODE_MANUAL:
            return OPERATION_MODE_MANUAL
        if mode_value == MODE_UNBALANCED:
            return OPERATION_MODE_UNBALANCED
        if mode_value != MODE_SENSOR_CONTROL:
            return None
        return RAW_VALUE_TO_SENSOR_MODE.get(current_value)

    def _decode_preset_mode(self, mode_block: list[int] | None) -> str | None:
        """Return one confirmed app-like preset mode from the optional mode block."""

        if mode_block is None:
            return None
        if len(mode_block) >= 5:
            if (
                mode_block[3] == MODE_MANUAL
                and mode_block[4] == PRESET_MODE_CODE_INTENSIVE
            ):
                return PRESET_MODE_INTENSIVE
        if len(mode_block) < 2:
            return None
        if mode_block[0] == MODE_UNBALANCED and len(mode_block) >= 3:
            if mode_block[1] == 0 and mode_block[2] > APP_UNBALANCED_PRESET_BASE:
                return PRESET_MODE_EXTRACT_ONLY
            if mode_block[2] == 0 and mode_block[1] > APP_UNBALANCED_PRESET_BASE:
                return PRESET_MODE_SUPPLY_ONLY
        if mode_block[0] != MODE_MANUAL:
            return None
        return RAW_CODE_TO_PRESET_MODE.get(mode_block[1])

    def _decode_preset_mode_with_fallback(
        self,
        *,
        mode_block: list[int] | None,
        full_mode_block_available: bool,
        operation_mode: str | None,
        raw_current_level: int | None,
        raw_extract_target: int | None,
        previous_preset_mode: str | None,
    ) -> str | None:
        """Decode preset mode without keeping stale values after known non-preset states."""

        if full_mode_block_available:
            decoded = self._decode_preset_mode(mode_block)
            if decoded == PRESET_MODE_INTENSIVE:
                return previous_preset_mode
            return decoded

        if mode_block is None:
            return previous_preset_mode

        if operation_mode == OPERATION_MODE_OFF or operation_mode in SENSOR_OPERATION_MODES:
            return None

        if operation_mode == OPERATION_MODE_MANUAL:
            return RAW_CODE_TO_PRESET_MODE.get(raw_current_level)

        if operation_mode == OPERATION_MODE_UNBALANCED:
            if (
                raw_current_level == 0
                and raw_extract_target is not None
                and raw_extract_target > APP_UNBALANCED_PRESET_BASE
            ):
                return PRESET_MODE_EXTRACT_ONLY
            if (
                raw_extract_target == 0
                and raw_current_level is not None
                and raw_current_level > APP_UNBALANCED_PRESET_BASE
            ):
                return PRESET_MODE_SUPPLY_ONLY
        return None

    def _decode_intensive_active(
        self,
        *,
        mode_block: list[int] | None,
        full_mode_block_available: bool,
        previous_intensive_active: bool | None,
    ) -> bool | None:
        """Return whether the temporary intensive override is currently active."""

        if full_mode_block_available and mode_block is not None:
            return (
                mode_block[3] == MODE_MANUAL
                and mode_block[4] == PRESET_MODE_CODE_INTENSIVE
            )
        if mode_block is None or len(mode_block) < 5:
            return previous_intensive_active
        return None

    def _supports(self, room: RoomConfig, entity_key: str) -> bool:
        if room.supported_entity_keys is None:
            return True
        return entity_key in room.supported_entity_keys
