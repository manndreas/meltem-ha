"""Runtime Modbus client for Meltem ventilation units.

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
from typing import cast

from pymodbus.client import ModbusSerialClient

from .const import (
    MODE_AUTOMATIC_VALUE,
    MODE_CO2_CONTROL_VALUE,
    MODE_HUMIDITY_CONTROL_VALUE,
    MODE_MANUAL,
    MODE_OFF,
    MODE_SENSOR_CONTROL,
    MODE_UNBALANCED,
    REGISTER_CO2_MAX_LEVEL,
    REGISTER_CO2_MIN_LEVEL,
    REGISTER_CO2_STARTING_POINT,
    REQUEST_GAP_SECONDS,
    REGISTER_CO2_EXTRACT_AIR,
    REGISTER_CURRENT_LEVEL,
    REGISTER_DAYS_UNTIL_FILTER_CHANGE,
    REGISTER_ERROR_STATUS,
    REGISTER_EXHAUST_AIR_TEMPERATURE,
    REGISTER_EXTRACT_AIR_TEMPERATURE,
    REGISTER_EXTRACT_AIR_FLOW,
    REGISTER_EXTRACT_AIR_TARGET_LEVEL,
    REGISTER_FILTER_CHANGE_DUE,
    REGISTER_FROST_PROTECTION_ACTIVE,
    REGISTER_FAULT_STATUS,
    REGISTER_HUMIDITY_EXTRACT_AIR,
    REGISTER_HUMIDITY_MAX_LEVEL,
    REGISTER_HUMIDITY_MIN_LEVEL,
    REGISTER_HUMIDITY_SUPPLY_AIR,
    REGISTER_HUMIDITY_STARTING_POINT,
    REGISTER_MODE,
    REGISTER_OUTDOOR_AIR_TEMPERATURE,
    REGISTER_OPERATING_HOURS,
    REGISTER_APPLY,
    REGISTER_RF_COMM_STATUS,
    REGISTER_SOFTWARE_VERSION,
    REGISTER_SUPPLY_AIR_TEMPERATURE,
    REGISTER_SUPPLY_AIR_FLOW,
    REGISTER_VALUE_ERROR_STATUS,
    REGISTER_VOC_SUPPLY_AIR,
    CO2_PROFILES,
    HUMIDITY_PROFILES,
    PLAIN_PROFILES,
    VOC_PROFILES,
    profile_max_airflow,
)
from .modbus_helpers import (
    MeltemModbusError,
    SerialSettings,
    build_client,
    derive_balanced_airflow,
    detect_slave_details_with_client,
    discover_gateway_nodes,
)
from .models import RefreshPlan, RoomConfig, RoomState


def _to_optional_bool(value: int | bool | None) -> bool | None:
    """Coerce 0/1 register values to bool, preserving None."""
    if isinstance(value, bool):
        return value
    return bool(value) if value is not None else None


class MeltemModbusClient:
    """Small synchronous wrapper around pymodbus.

    The client keeps one serial connection open for as long as it remains
    usable. Reads and writes are serialized by the coordinator, and this class
    adds one more thread-level lock so executor jobs cannot overlap either.
    """

    def __init__(self, settings: SerialSettings) -> None:
        self._settings = settings
        self._client: ModbusSerialClient | None = None
        self._lock = threading.Lock()
        self._optional_read_backoff_until: dict[tuple[int, int, int], float] = {}
        self._optional_read_failures: dict[tuple[int, int, int], int] = {}

    def close(self) -> None:
        """Close the underlying serial client."""

        if self._client is not None:
            self._client.close()
            self._client = None

    def reset_connection(self) -> None:
        """Drop the current serial connection so the next read reconnects cleanly."""

        self.close()

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

        try:
            with self._lock:
                client = self._ensure_client()
                if refresh_plan.refresh_airflow:
                    # Airflow drives the UI and post-write confirmation, so it
                    # gets its own fast path.
                    (
                        extract_air_flow,
                        supply_air_flow,
                    ) = self._read_airflow_pair(
                        client,
                        room,
                        previous_state,
                    )
                else:
                    extract_air_flow = previous_state.extract_air_flow
                    supply_air_flow = previous_state.supply_air_flow

                # Re-acquire the client after each block so retries can replace
                # a broken transport transparently.
                client = self._ensure_client()

                state = self._read_profile_state(
                    client,
                    room,
                    previous_state,
                    refresh_plan,
                )

                client = self._ensure_client()

                error, filter_due, frost = self._read_status_group(
                    client,
                    room,
                    previous_state,
                    refresh_plan,
                )

                client = self._ensure_client()

                days = self._read_uint16_if_due(
                    client, room, "days_until_filter_change",
                    REGISTER_DAYS_UNTIL_FILTER_CHANGE,
                    previous_state.days_until_filter_change,
                    refresh_plan.refresh_filter_days,
                )
                hours = self._read_uint32_if_due(
                    client, room, "operating_hours",
                    REGISTER_OPERATING_HOURS,
                    previous_state.operating_hours,
                    refresh_plan.refresh_operating_hours,
                )
                software_version = self._read_uint16_if_due(
                    client, room, "software_version",
                    REGISTER_SOFTWARE_VERSION,
                    previous_state.software_version,
                    refresh_plan.refresh_operating_hours,
                )
                (
                    humidity_starting_point,
                    humidity_min_level,
                    humidity_max_level,
                    co2_starting_point,
                    co2_min_level,
                    co2_max_level,
                ) = self._read_control_settings_group(
                    client,
                    room,
                    previous_state,
                    refresh_plan,
                )
                rf_comm_status = self._read_uint16_if_due(
                    client, room, "rf_comm_status",
                    REGISTER_RF_COMM_STATUS,
                    previous_state.rf_comm_status,
                    refresh_plan.refresh_status,
                )
                fault_status = self._read_uint16_if_due(
                    client, room, "fault_status",
                    REGISTER_FAULT_STATUS,
                    previous_state.fault_status,
                    refresh_plan.refresh_status,
                )
                value_error_status = self._read_uint16_if_due(
                    client, room, "value_error_status",
                    REGISTER_VALUE_ERROR_STATUS,
                    previous_state.value_error_status,
                    refresh_plan.refresh_status,
                )
                if refresh_plan.refresh_airflow:
                    client = self._ensure_client()
                    mode_block = None
                    if self._supports(room, "operation_mode"):
                        mode_block = self._read_optional_airflow_uint16_block(
                            client,
                            room.slave,
                            REGISTER_MODE,
                            2,
                        )
                    raw_current_level = self._read_optional_airflow_uint16(
                        client,
                        room.slave,
                        REGISTER_CURRENT_LEVEL,
                    )
                    # On the tested gateway, REGISTER_CURRENT_LEVEL behaves as
                    # a fast target readback after balanced writes even though
                    # the vendor docs describe it primarily as a write path.
                    # The airflow registers can lag noticeably behind after a
                    # write, so use 41121 for target confirmation when it looks
                    # like a valid raw level and fall back to derived airflow
                    # otherwise.
                    current_level = self._decode_balanced_target_readback(
                        room,
                        raw_current_level,
                        extract_air_flow,
                        supply_air_flow,
                    )
                    operation_mode = (
                        self._decode_operation_mode(mode_block[0], mode_block[1])
                        if mode_block is not None and len(mode_block) >= 2
                        else previous_state.operation_mode
                    )
                    if operation_mode == "unbalanced":
                        raw_extract_target = self._read_optional_airflow_uint16(
                            client,
                            room.slave,
                            REGISTER_EXTRACT_AIR_TARGET_LEVEL,
                        )
                        extract_target_level = (
                            self._scale_raw_level_to_airflow(room, raw_extract_target)
                            if raw_extract_target is not None
                            else previous_state.extract_target_level
                        )
                    else:
                        extract_target_level = None
                else:
                    current_level = previous_state.current_level
                    extract_target_level = previous_state.extract_target_level
                    operation_mode = previous_state.operation_mode
        except MeltemModbusError:
            self.close()
            raise
        except Exception as err:
            self.close()
            raise MeltemModbusError(
                f"Unexpected error while reading room {room.key}: {err!r}"
            ) from err

        return RoomState(
            exhaust_temperature=state.exhaust_temperature,
            outdoor_air_temperature=state.outdoor_air_temperature,
            extract_air_temperature=state.extract_air_temperature,
            supply_air_temperature=state.supply_air_temperature,
            error_status=_to_optional_bool(error),
            filter_change_due=_to_optional_bool(filter_due),
            frost_protection_active=_to_optional_bool(frost),
            rf_comm_status=_to_optional_bool(rf_comm_status),
            fault_status=_to_optional_bool(fault_status),
            value_error_status=_to_optional_bool(value_error_status),
            humidity_extract_air=state.humidity_extract_air,
            humidity_supply_air=state.humidity_supply_air,
            co2_extract_air=state.co2_extract_air,
            voc_supply_air=state.voc_supply_air,
            extract_air_flow=extract_air_flow,
            supply_air_flow=supply_air_flow,
            operation_mode=operation_mode,
            days_until_filter_change=days,
            operating_hours=hours,
            software_version=software_version,
            current_level=current_level,
            extract_target_level=extract_target_level,
            humidity_starting_point=humidity_starting_point,
            humidity_min_level=humidity_min_level,
            humidity_max_level=humidity_max_level,
            co2_starting_point=co2_starting_point,
            co2_min_level=co2_min_level,
            co2_max_level=co2_max_level,
        )

    def write_level(self, room: RoomConfig, level: int) -> None:
        """Write off/manual mode and target level for one room."""

        mode = MODE_OFF if level == 0 else MODE_MANUAL
        max_airflow = profile_max_airflow(room.profile)
        raw_level = max(0, min(200, round(level * 200 / max_airflow)))

        try:
            with self._lock:
                client = self._ensure_client()
                self._write_uint16(client, room.slave, REGISTER_MODE, mode)
                self._write_uint16(client, room.slave, REGISTER_CURRENT_LEVEL, raw_level)
                self._write_uint16(client, room.slave, REGISTER_APPLY, 0)
                self._clear_optional_airflow_read_backoff(room.slave)
        except MeltemModbusError:
            self.close()
            raise
        except Exception as err:
            self.close()
            raise MeltemModbusError(
                f"Unexpected error while writing level for room {room.key}: {err!r}"
            ) from err

    def write_unbalanced_levels(
        self, room: RoomConfig, supply_level: int, extract_level: int
    ) -> None:
        """Write unbalanced mode with separate supply and extract levels."""

        raw_supply_level = self._scale_airflow_to_raw(room, supply_level)
        raw_extract_level = self._scale_airflow_to_raw(room, extract_level)

        try:
            with self._lock:
                client = self._ensure_client()
                self._write_uint16(client, room.slave, REGISTER_MODE, MODE_UNBALANCED)
                self._write_uint16(
                    client, room.slave, REGISTER_CURRENT_LEVEL, raw_supply_level
                )
                self._write_uint16(
                    client, room.slave, REGISTER_EXTRACT_AIR_TARGET_LEVEL, raw_extract_level
                )
                self._write_uint16(client, room.slave, REGISTER_APPLY, 0)
                self._clear_optional_airflow_read_backoff(room.slave)
        except MeltemModbusError:
            self.close()
            raise
        except Exception as err:
            self.close()
            raise MeltemModbusError(
                f"Unexpected error while writing unbalanced levels for room {room.key}: {err!r}"
            ) from err

    def write_operating_mode(
        self,
        room: RoomConfig,
        operation_mode: str,
        balanced_level: int,
        extract_level: int,
    ) -> None:
        """Write one operating mode using the documented control registers."""

        try:
            with self._lock:
                client = self._ensure_client()
                if operation_mode == "off":
                    self._write_uint16(client, room.slave, REGISTER_MODE, MODE_OFF)
                    self._write_uint16(client, room.slave, REGISTER_CURRENT_LEVEL, 0)
                elif operation_mode == "manual":
                    self._write_uint16(client, room.slave, REGISTER_MODE, MODE_MANUAL)
                    self._write_uint16(
                        client,
                        room.slave,
                        REGISTER_CURRENT_LEVEL,
                        self._scale_airflow_to_raw(room, balanced_level),
                    )
                elif operation_mode == "unbalanced":
                    self._write_uint16(client, room.slave, REGISTER_MODE, MODE_UNBALANCED)
                    self._write_uint16(
                        client,
                        room.slave,
                        REGISTER_CURRENT_LEVEL,
                        self._scale_airflow_to_raw(room, balanced_level),
                    )
                    self._write_uint16(
                        client,
                        room.slave,
                        REGISTER_EXTRACT_AIR_TARGET_LEVEL,
                        self._scale_airflow_to_raw(room, extract_level),
                    )
                else:
                    sensor_control_value = {
                        "humidity_control": MODE_HUMIDITY_CONTROL_VALUE,
                        "co2_control": MODE_CO2_CONTROL_VALUE,
                        "automatic": MODE_AUTOMATIC_VALUE,
                    }.get(operation_mode)
                    if sensor_control_value is None:
                        raise MeltemModbusError(
                            f"Unsupported operating mode {operation_mode!r} for room {room.key}"
                        )
                    self._write_uint16(
                        client,
                        room.slave,
                        REGISTER_MODE,
                        MODE_SENSOR_CONTROL,
                    )
                    self._write_uint16(
                        client,
                        room.slave,
                        REGISTER_CURRENT_LEVEL,
                        sensor_control_value,
                    )
                self._write_uint16(client, room.slave, REGISTER_APPLY, 0)
                self._clear_optional_airflow_read_backoff(room.slave)
        except MeltemModbusError:
            self.close()
            raise
        except Exception as err:
            self.close()
            raise MeltemModbusError(
                f"Unexpected error while writing operating mode for room {room.key}: {err!r}"
            ) from err

    def write_control_setting(
        self,
        room: RoomConfig,
        setting_key: str,
        value: int,
    ) -> None:
        """Write one humidity/CO2 control setting register."""

        register_bounds: dict[str, tuple[int, int, int]] = {
            "humidity_starting_point": (REGISTER_HUMIDITY_STARTING_POINT, 0, 100),
            "humidity_min_level": (REGISTER_HUMIDITY_MIN_LEVEL, 0, 100),
            "humidity_max_level": (REGISTER_HUMIDITY_MAX_LEVEL, 0, 100),
            "co2_starting_point": (REGISTER_CO2_STARTING_POINT, 0, 2000),
            "co2_min_level": (REGISTER_CO2_MIN_LEVEL, 0, 100),
            "co2_max_level": (REGISTER_CO2_MAX_LEVEL, 0, 100),
        }
        entry = register_bounds.get(setting_key)
        if entry is None:
            raise MeltemModbusError(
                f"Unsupported control setting {setting_key!r} for room {room.key}"
            )

        register, min_val, max_val = entry
        clamped = max(min_val, min(max_val, int(round(value))))

        try:
            with self._lock:
                client = self._ensure_client()
                self._write_uint16(client, room.slave, register, clamped)
        except MeltemModbusError:
            self.close()
            raise
        except Exception as err:
            self.close()
            raise MeltemModbusError(
                f"Unexpected error while writing control setting for room {room.key}: {err!r}"
            ) from err

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
                time.sleep(0.5)
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
            raise MeltemModbusError(
                f"Could not connect to Meltem gateway on {self._settings.port}: {last_error}"
            ) from last_error
        raise MeltemModbusError(
            f"Could not connect to Meltem gateway on {self._settings.port}"
        )

    # ------------------------------------------------------------------
    #  Low-level register access (runtime, with retry)
    # ------------------------------------------------------------------

    def _read_holding_registers_with_retry(
        self,
        client: ModbusSerialClient,
        slave: int,
        address: int,
        count: int,
        *,
        attempts: int = 2,
    ):
        """Read holding registers with one retry on transient failures.

        Transport failures trigger a reconnect for the next attempt. Modbus
        error responses do not, because they still prove that the gateway link
        itself is alive.
        """

        last_error: Exception | None = None

        for attempt in range(1, attempts + 1):
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
                    time.sleep(0.5)  # let OS release the serial port lock
                    client = self._ensure_client()
                    continue
                raise MeltemModbusError(
                    f"Read raised {type(err).__name__} for slave {slave} register {address}: {err}"
                ) from err

            time.sleep(REQUEST_GAP_SECONDS)

            if response is None:
                last_error = MeltemModbusError(
                    f"Read returned no response for slave {slave} register {address}"
                )
                if attempt >= attempts:
                    self.close()
            elif response.isError():
                last_error = MeltemModbusError(
                    f"Read failed for slave {slave} register {address}: {response}"
                )
            elif not hasattr(response, "registers") or len(response.registers) < count:
                last_error = MeltemModbusError(
                    f"Read returned insufficient registers for slave {slave} register {address}"
                )
            else:
                return response

            # The gateway answered, so keep the transport open and just back off.
            if attempt < attempts:
                time.sleep(REQUEST_GAP_SECONDS)

        if isinstance(last_error, MeltemModbusError):
            raise last_error
        raise MeltemModbusError(
            f"Read failed for slave {slave} register {address}: {last_error!r}"
        )

    def _read_uint16(
        self, client: ModbusSerialClient, slave: int, address: int
    ) -> int | None:
        response = self._read_holding_registers_with_retry(client, slave, address, 1)
        return response.registers[0]

    def _read_float32_word_swap(
        self, client: ModbusSerialClient, slave: int, address: int
    ) -> float | None:
        response = self._read_holding_registers_with_retry(client, slave, address, 2)
        registers = response.registers
        # The gateway exposes these temperatures as float32 with swapped words.
        value = struct.unpack(">f", struct.pack(">HH", registers[1], registers[0]))[0]
        return value if math.isfinite(value) else None

    def _read_uint32_word_swap(
        self, client: ModbusSerialClient, slave: int, address: int
    ) -> int | None:
        response = self._read_holding_registers_with_retry(client, slave, address, 2)
        registers = response.registers
        return struct.unpack(">I", struct.pack(">HH", registers[1], registers[0]))[0]

    def _read_uint16_block(
        self, client: ModbusSerialClient, slave: int, address: int, count: int
    ) -> list[int]:
        response = self._read_holding_registers_with_retry(
            client,
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

    # ------------------------------------------------------------------
    #  Optional reads (swallow errors, return None)
    # ------------------------------------------------------------------

    def _read_optional_uint16(
        self, client: ModbusSerialClient, slave: int, address: int
    ) -> int | None:
        try:
            return self._read_uint16(client, slave, address)
        except MeltemModbusError:
            return None

    def _read_optional_uint16_block(
        self, client: ModbusSerialClient, slave: int, address: int, count: int
    ) -> list[int] | None:
        try:
            return self._read_uint16_block(client, slave, address, count)
        except MeltemModbusError:
            return None

    def _read_optional_airflow_uint16(
        self, client: ModbusSerialClient, slave: int, address: int
    ) -> int | None:
        """Read one optional airflow-adjacent register with temporary backoff."""

        key = (slave, address, 1)
        if self._is_optional_read_backed_off(key):
            return None

        try:
            value = self._read_uint16(client, slave, address)
        except MeltemModbusError:
            self._mark_optional_read_failure(key)
            return None

        self._clear_optional_read_failure(key)
        return value

    def _read_optional_airflow_uint16_block(
        self, client: ModbusSerialClient, slave: int, address: int, count: int
    ) -> list[int] | None:
        """Read one optional airflow-adjacent block with temporary backoff."""

        key = (slave, address, count)
        if self._is_optional_read_backed_off(key):
            return None

        try:
            value = self._read_uint16_block(client, slave, address, count)
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
            (slave, REGISTER_CURRENT_LEVEL, 1),
            (slave, REGISTER_EXTRACT_AIR_TARGET_LEVEL, 1),
        ):
            self._clear_optional_read_failure(key)

    def _read_optional_float32_word_swap(
        self, client: ModbusSerialClient, slave: int, address: int
    ) -> float | None:
        try:
            return self._read_float32_word_swap(client, slave, address)
        except MeltemModbusError:
            return None

    def _read_optional_uint32_word_swap(
        self, client: ModbusSerialClient, slave: int, address: int
    ) -> int | None:
        try:
            return self._read_uint32_word_swap(client, slave, address)
        except MeltemModbusError:
            return None

    # ------------------------------------------------------------------
    #  Conditional / grouped reads
    # ------------------------------------------------------------------

    def _read_uint16_if_due(
        self,
        client: ModbusSerialClient,
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
            self._read_optional_uint16(client, room.slave, register),
            previous,
        )

    def _read_uint32_if_due(
        self,
        client: ModbusSerialClient,
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
            self._read_optional_uint32_word_swap(client, room.slave, register),
            previous,
        )

    def _read_temperature_if_due(
        self,
        client: ModbusSerialClient,
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
            self._read_optional_float32_word_swap(client, room.slave, register),
            previous,
        )

    def _read_airflow_pair(
        self,
        client: ModbusSerialClient,
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
                client,
                room.slave,
                REGISTER_EXTRACT_AIR_FLOW,
                2,
            )

        if supports_extract and block is not None:
            extract_air_flow = self._coalesce(block[0], previous_state.extract_air_flow)

        if supports_supply and block is not None:
            supply_air_flow = self._coalesce(block[1], previous_state.supply_air_flow)

        return extract_air_flow, supply_air_flow

    def _read_status_group(
        self,
        client: ModbusSerialClient,
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
                client,
                room.slave,
                REGISTER_ERROR_STATUS,
                3,
            )
            if block is not None:
                if should_refresh_error:
                    error = self._coalesce(block[0], error)
                if should_refresh_filter:
                    filter_due = self._coalesce(block[1], filter_due)
                if should_refresh_frost:
                    frost = self._coalesce(block[2], frost)

        return error, filter_due, frost

    def _read_control_settings_group(
        self,
        client: ModbusSerialClient,
        room: RoomConfig,
        previous_state: RoomState,
        refresh_plan: RefreshPlan,
    ) -> tuple[int | None, int | None, int | None, int | None, int | None, int | None]:
        """Read the contiguous humidity/CO2 control setting block when due."""

        keys = (
            "humidity_starting_point",
            "humidity_min_level",
            "humidity_max_level",
            "co2_starting_point",
            "co2_min_level",
            "co2_max_level",
        )
        previous_values = (
            previous_state.humidity_starting_point,
            previous_state.humidity_min_level,
            previous_state.humidity_max_level,
            previous_state.co2_starting_point,
            previous_state.co2_min_level,
            previous_state.co2_max_level,
        )
        should_refresh = refresh_plan.refresh_control_settings and any(
            self._supports(room, key) for key in keys
        )
        if not should_refresh:
            return previous_values

        block = self._read_optional_uint16_block(
            client,
            room.slave,
            REGISTER_HUMIDITY_STARTING_POINT,
            6,
        )
        if block is None:
            return previous_values

        return tuple(
            self._coalesce(block[index], previous_values[index])
            if self._supports(room, keys[index])
            else previous_values[index]
            for index in range(6)
        )

    def _read_profile_state(
        self,
        client: ModbusSerialClient,
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
                client,
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
                client,
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
                    client,
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
                client,
                room.slave,
                REGISTER_HUMIDITY_EXTRACT_AIR,
                2,
            )
            if extract_env_block is not None:
                if room.profile in HUMIDITY_PROFILES and self._supports(room, "humidity_extract_air") and do_env:
                    humidity_extract = self._coalesce(
                        cast(int | None, extract_env_block[0]),
                        prev.humidity_extract_air,
                    )
                if room.profile in CO2_PROFILES and self._supports(room, "co2_extract_air") and do_env:
                    co2 = self._coalesce(
                        cast(int | None, extract_env_block[1]),
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
                client,
                room.slave,
                REGISTER_HUMIDITY_SUPPLY_AIR,
                3,
            )
            if supply_env_block is not None:
                if room.profile in HUMIDITY_PROFILES and self._supports(room, "humidity_supply_air") and do_env:
                    humidity_supply = self._coalesce(
                        cast(int | None, supply_env_block[0]),
                        prev.humidity_supply_air,
                    )
                if room.profile in VOC_PROFILES and self._supports(room, "voc_supply_air") and do_env:
                    voc = self._coalesce(
                        cast(int | None, supply_env_block[2]),
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
        client: ModbusSerialClient,
        slave: int,
        address: int,
        value: int,
        *,
        attempts: int = 2,
    ) -> None:
        last_error: Exception | None = None

        for attempt in range(1, attempts + 1):
            try:
                response = client.write_register(
                    address=address, value=value, device_id=slave
                )
            except Exception as err:
                last_error = err
                should_retry = self._is_retryable_transport_error(err)
                self.close()
                if should_retry and attempt < attempts:
                    time.sleep(0.5)
                    client = self._ensure_client()
                    continue
                raise MeltemModbusError(
                    f"Write raised {type(err).__name__} for slave {slave} register {address}: {err}"
                ) from err

            time.sleep(REQUEST_GAP_SECONDS)
            if response is None:
                last_error = MeltemModbusError(
                    f"Write returned no response for slave {slave} register {address}"
                )
                if attempt < attempts:
                    self.close()
                    time.sleep(0.5)
                    client = self._ensure_client()
                    continue
                self.close()
                raise last_error

            if response.isError():
                raise MeltemModbusError(
                    f"Write failed for slave {slave} register {address}: {response}"
                )

            return

        if isinstance(last_error, MeltemModbusError):
            raise last_error
        raise MeltemModbusError(
            f"Write failed for slave {slave} register {address}: {last_error!r}"
        )

    # ------------------------------------------------------------------
    #  Tiny helpers
    # ------------------------------------------------------------------

    def _coalesce(self, value, previous_value):
        return previous_value if value is None else value

    def _is_retryable_transport_error(self, err: Exception) -> bool:
        """Return whether an exception looks like a transient lock/transport issue."""

        message = str(err).lower()
        retry_markers = (
            "could not exclusively lock port",
            "connectionerror",
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
        return any(marker in message for marker in retry_markers)

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

    def _decode_operation_mode(
        self,
        mode_value: int | None,
        current_value: int | None,
    ) -> str | None:
        if mode_value == MODE_OFF:
            return "off"
        if mode_value == MODE_MANUAL:
            return "manual"
        if mode_value == MODE_UNBALANCED:
            return "unbalanced"
        if mode_value != MODE_SENSOR_CONTROL:
            return None
        if current_value == MODE_HUMIDITY_CONTROL_VALUE:
            return "humidity_control"
        if current_value == MODE_CO2_CONTROL_VALUE:
            return "co2_control"
        if current_value == MODE_AUTOMATIC_VALUE:
            return "automatic"
        return None

    def _supports(self, room: RoomConfig, entity_key: str) -> bool:
        if room.supported_entity_keys is None:
            return True
        return entity_key in room.supported_entity_keys
