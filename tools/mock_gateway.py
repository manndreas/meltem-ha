"""Run a stateful RTU-over-TCP mock of a Meltem M-WRG-GW and its units."""

from __future__ import annotations

import argparse
import asyncio
import struct
import time
from collections.abc import Mapping, Sequence

from custom_components.meltem_ventilation.const import (
    MODE_MANUAL,
    MODE_SENSOR_CONTROL,
    MODE_UNBALANCED,
    PRESET_MODE_CODE_INTENSIVE,
    PRODUCT_ID_M_WRG,
    PRODUCT_ID_M_WRG_II,
    PROFILE_METADATA,
    REGISTER_APP_SETUP_CODE,
    REGISTER_APPLY,
    REGISTER_CO2_EXTRACT_AIR,
    REGISTER_CO2_MAX_LEVEL,
    REGISTER_CO2_MIN_LEVEL,
    REGISTER_CO2_STARTING_POINT,
    REGISTER_CURRENT_LEVEL,
    REGISTER_DAYS_UNTIL_FILTER_CHANGE,
    REGISTER_ERROR_STATUS,
    REGISTER_EXHAUST_AIR_TEMPERATURE,
    REGISTER_EXTRACT_AIR_FLOW,
    REGISTER_EXTRACT_AIR_TARGET_LEVEL,
    REGISTER_EXTRACT_AIR_TEMPERATURE,
    REGISTER_EXTRACT_MODE_STATUS,
    REGISTER_FAN_MOTOR_HOURS,
    REGISTER_FILTER_CHANGE_DUE,
    REGISTER_FROST_PROTECTION_ACTIVE,
    REGISTER_GATEWAY_MAC_START,
    REGISTER_GATEWAY_NODE_ADDRESS_1,
    REGISTER_GATEWAY_NUMBER_OF_NODES,
    REGISTER_GATEWAY_RF_LOAD_CURRENT_HOUR,
    REGISTER_GATEWAY_RF_LOAD_PREVIOUS_HOUR,
    REGISTER_GATEWAY_RF_MESSAGES_CURRENT_HOUR,
    REGISTER_GATEWAY_RF_MESSAGES_PREVIOUS_HOUR,
    REGISTER_GATEWAY_UPTIME,
    REGISTER_GATEWAY_UTC_CLOCK,
    REGISTER_HUMIDITY_EXTRACT_AIR,
    REGISTER_HUMIDITY_MAX_LEVEL,
    REGISTER_HUMIDITY_MIN_LEVEL,
    REGISTER_HUMIDITY_STARTING_POINT,
    REGISTER_HUMIDITY_SUPPLY_AIR,
    REGISTER_MODE,
    REGISTER_MODE_STATUS,
    REGISTER_OPERATING_HOURS,
    REGISTER_OUTDOOR_AIR_TEMPERATURE,
    REGISTER_PRESET_MODE,
    REGISTER_PRESET_VALUE,
    REGISTER_PRODUCT_ID,
    REGISTER_SUPPLY_AIR_FLOW,
    REGISTER_SUPPLY_AIR_TEMPERATURE,
    REGISTER_SUPPLY_MODE_STATUS,
    REGISTER_VOC_SUPPLY_AIR,
)

DEFAULT_UNITS = "2:ii_fc_voc,3:ii_f"
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 5020
MAX_REGISTER_COUNT = 125


def parse_units(value: str) -> dict[int, str]:
    """Parse comma-separated ``address:profile`` pairs."""

    units: dict[int, str] = {}
    if not value.strip():
        raise argparse.ArgumentTypeError("provide at least one address:profile pair")
    for item in value.split(","):
        address_text, separator, profile = item.strip().partition(":")
        if not separator or not address_text or not profile:
            raise argparse.ArgumentTypeError(f"invalid unit {item!r}; expected address:profile")
        try:
            address = int(address_text)
        except ValueError as err:
            raise argparse.ArgumentTypeError(f"invalid Modbus address {address_text!r}") from err
        if not 2 <= address <= 16:
            raise argparse.ArgumentTypeError("unit addresses must be between 2 and 16")
        if address in units:
            raise argparse.ArgumentTypeError(f"duplicate unit address {address}")
        if profile not in PROFILE_METADATA:
            available = ", ".join(PROFILE_METADATA)
            raise argparse.ArgumentTypeError(
                f"unknown profile {profile!r}; choose from {available}"
            )
        units[address] = profile
    return units


def _word_swapped_u32(address: int, value: int) -> dict[int, int]:
    return {address: value & 0xFFFF, address + 1: (value >> 16) & 0xFFFF}


def _word_swapped_float(address: int, value: float) -> dict[int, int]:
    high_word, low_word = struct.unpack(">HH", struct.pack(">f", value))
    return {address: low_word, address + 1: high_word}


def _unit_registers(profile: str, app_setup_code: int) -> dict[int, int]:
    """Build readable registers with sensor values matching the selected profile."""

    registers = {
        address: 0
        for start, end in (
            (40000, 40004),
            (40100, 40109),
            (41000, 41033),
            (41100, 41132),
            (42000, 42005),
        )
        for address in range(start, end + 1)
    }
    capabilities = PROFILE_METADATA[profile].capabilities
    product_id = PRODUCT_ID_M_WRG if profile.startswith("s_") else PRODUCT_ID_M_WRG_II
    registers.update(_word_swapped_u32(REGISTER_PRODUCT_ID, product_id))
    registers[REGISTER_APP_SETUP_CODE] = app_setup_code
    registers[REGISTER_APP_SETUP_CODE + 1] = 0
    registers[REGISTER_HUMIDITY_EXTRACT_AIR] = 48 if "humidity" in capabilities else 0xFFFF
    registers[REGISTER_HUMIDITY_SUPPLY_AIR] = 44 if "humidity" in capabilities else 0xFFFF
    registers[REGISTER_CO2_EXTRACT_AIR] = 720 if "co2" in capabilities else 0xFFFF
    registers[REGISTER_VOC_SUPPLY_AIR] = 360 if "voc" in capabilities else 0xFFFF
    for address, temperature in (
        (REGISTER_EXTRACT_AIR_TEMPERATURE, 21.5),
        (REGISTER_OUTDOOR_AIR_TEMPERATURE, 8.0),
        (REGISTER_EXHAUST_AIR_TEMPERATURE, 19.0),
        (REGISTER_SUPPLY_AIR_TEMPERATURE, 17.5),
    ):
        registers.update(_word_swapped_float(address, temperature))
    registers.update(_word_swapped_u32(REGISTER_OPERATING_HOURS, 12345))
    registers.update(_word_swapped_u32(REGISTER_FAN_MOTOR_HOURS, 6789))
    registers[REGISTER_DAYS_UNTIL_FILTER_CHANGE] = 120
    registers[REGISTER_EXTRACT_AIR_FLOW] = 40
    registers[REGISTER_SUPPLY_AIR_FLOW] = 40
    registers[REGISTER_ERROR_STATUS] = 0
    registers[REGISTER_FILTER_CHANGE_DUE] = 0
    registers[REGISTER_FROST_PROTECTION_ACTIVE] = 0
    registers[REGISTER_HUMIDITY_STARTING_POINT] = 55
    registers[REGISTER_HUMIDITY_MIN_LEVEL] = 20
    registers[REGISTER_HUMIDITY_MAX_LEVEL] = 60
    registers[REGISTER_CO2_STARTING_POINT] = 800
    registers[REGISTER_CO2_MIN_LEVEL] = 30
    registers[REGISTER_CO2_MAX_LEVEL] = 50
    registers[REGISTER_MODE] = MODE_MANUAL
    registers[REGISTER_CURRENT_LEVEL] = 80
    registers[REGISTER_EXTRACT_AIR_TARGET_LEVEL] = 80
    registers[REGISTER_MODE_STATUS] = MODE_MANUAL
    registers[REGISTER_SUPPLY_MODE_STATUS] = 80
    registers[REGISTER_EXTRACT_MODE_STATUS] = 80
    return registers


def _gateway_registers(units: Mapping[int, str], no_units: bool) -> dict[int, int]:
    registers = {
        REGISTER_GATEWAY_NUMBER_OF_NODES: 0 if no_units else len(units),
        REGISTER_GATEWAY_MAC_START: 0x0200,
        REGISTER_GATEWAY_MAC_START + 1: 0,
        REGISTER_GATEWAY_MAC_START + 2: 1,
        REGISTER_GATEWAY_RF_MESSAGES_CURRENT_HOUR: 75,
        REGISTER_GATEWAY_RF_MESSAGES_PREVIOUS_HOUR: 147,
    }
    if not no_units:
        registers.update(
            {
                REGISTER_GATEWAY_NODE_ADDRESS_1 + index: address
                for index, address in enumerate(sorted(units))
            }
        )
    timestamp = int(time.time())
    registers.update(_word_swapped_u32(REGISTER_GATEWAY_UPTIME, 12345))
    registers.update(_word_swapped_u32(REGISTER_GATEWAY_UTC_CLOCK, timestamp))
    registers.update(_word_swapped_float(REGISTER_GATEWAY_RF_LOAD_CURRENT_HOUR, 18.5))
    registers.update(_word_swapped_float(REGISTER_GATEWAY_RF_LOAD_PREVIOUS_HOUR, 12.25))
    return registers


class RtuTcpMockGateway:
    """A small stateful RTU-over-TCP server for Home Assistant smoke tests."""

    def __init__(
        self,
        units: Mapping[int, str],
        *,
        no_units: bool = False,
        silent_units: Sequence[int] = (),
        response_delay: float = 0,
        single_client: bool = False,
    ) -> None:
        if response_delay < 0:
            raise ValueError("response_delay must be non-negative")
        unknown_silent_units = set(silent_units) - set(units)
        if unknown_silent_units:
            raise ValueError(f"silent units are not configured: {sorted(unknown_silent_units)}")
        self.units = dict(units)
        self.no_units = no_units
        self.silent_units = frozenset(silent_units)
        self.response_delay = response_delay
        self.single_client = single_client
        self.registers = {
            1: _gateway_registers(self.units, no_units),
            **{
                address: _unit_registers(profile, 0xA000 + address)
                for address, profile in self.units.items()
            },
        }
        self.requests = 0
        self.writes = 0
        self._active_clients = 0
        self._writers: set[asyncio.StreamWriter] = set()
        self._server: asyncio.Server | None = None

    async def start(self, host: str = DEFAULT_HOST, port: int = DEFAULT_PORT) -> int:
        """Start listening and return the bound port."""

        if self._server is not None:
            raise RuntimeError("mock gateway is already running")
        self._server = await asyncio.start_server(self._handle_client, host, port)
        if not self._server.sockets:
            raise RuntimeError("mock gateway has no listening socket")
        return int(self._server.sockets[0].getsockname()[1])

    async def close(self) -> None:
        """Stop listening and close active client connections."""

        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
            self._server = None
        writers = tuple(self._writers)
        for writer in writers:
            writer.close()
        if writers:
            await asyncio.gather(*(writer.wait_closed() for writer in writers))

    async def serve_forever(self) -> None:
        """Wait for clients until the server is cancelled or closed."""

        if self._server is None:
            raise RuntimeError("mock gateway is not running")
        await self._server.serve_forever()

    async def _handle_client(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        if self.single_client and self._active_clients:
            print("Rejecting a second client (--single-client).", flush=True)
            writer.close()
            await writer.wait_closed()
            return
        self._active_clients += 1
        self._writers.add(writer)
        peer = writer.get_extra_info("peername")
        print(f"Client connected: {peer}", flush=True)
        try:
            while True:
                try:
                    prefix = await reader.readexactly(2)
                except asyncio.IncompleteReadError:
                    return
                unit_id, function = prefix
                if function not in (3, 6):
                    print(
                        f"Closing client after unsupported function 0x{function:02X}.",
                        flush=True,
                    )
                    return
                request = prefix + await reader.readexactly(6)
                if _rtu_frame(request[:-2]) != request:
                    print(f"Invalid RTU CRC from {peer}; closing client.", flush=True)
                    return
                self.requests += 1
                address = int.from_bytes(request[2:4], "big")
                value = int.from_bytes(request[4:6], "big")
                if unit_id in self.silent_units:
                    print(f"DROP unit={unit_id} function=0x{function:02X} address={address}")
                    continue
                response = self._respond(unit_id, function, address, value)
                if self.response_delay:
                    await asyncio.sleep(self.response_delay)
                writer.write(response)
                await writer.drain()
        except (ConnectionError, OSError) as err:
            print(f"Client {peer} disconnected: {err}", flush=True)
        finally:
            self._writers.discard(writer)
            self._active_clients -= 1
            writer.close()
            await writer.wait_closed()
            print(f"Client disconnected: {peer}", flush=True)

    def _respond(self, unit_id: int, function: int, address: int, value: int) -> bytes:
        registers = self.registers.get(unit_id)
        if registers is None:
            return _exception_frame(unit_id, function, 11)
        if function == 3:
            count = value
            if not 1 <= count <= MAX_REGISTER_COUNT or address + count > 0x10000:
                return _exception_frame(unit_id, function, 3)
            values = [registers.get(register, 0) for register in range(address, address + count)]
            print(f"READ unit={unit_id} {address} x{count} -> {values}", flush=True)
            payload = bytes((unit_id, function, count * 2)) + b"".join(
                register.to_bytes(2, "big") for register in values
            )
            return _rtu_frame(payload)
        if unit_id == 1:
            print("Rejecting writes to gateway unit 1.", flush=True)
            return _exception_frame(unit_id, function, 2)
        registers[address] = value
        self.writes += 1
        print(f"WRITE unit={unit_id} {address} = {value}", flush=True)
        if address == REGISTER_APPLY and value == 0:
            self._commit_mode(registers)
        return _rtu_frame(bytes((unit_id, function)) + request_echo(address, value))

    @staticmethod
    def _commit_mode(registers: dict[int, int]) -> None:
        mode = registers.get(REGISTER_MODE, MODE_MANUAL)
        current_level = registers.get(REGISTER_CURRENT_LEVEL, 0)
        extract_level = registers.get(REGISTER_EXTRACT_AIR_TARGET_LEVEL, current_level)
        if (
            mode == MODE_MANUAL
            and registers.get(REGISTER_PRESET_MODE) == MODE_MANUAL
            and registers.get(REGISTER_PRESET_VALUE) == PRESET_MODE_CODE_INTENSIVE
        ):
            current_level = PRESET_MODE_CODE_INTENSIVE
        registers[REGISTER_MODE_STATUS] = mode
        registers[REGISTER_SUPPLY_MODE_STATUS] = current_level
        registers[REGISTER_EXTRACT_MODE_STATUS] = (
            extract_level if mode == MODE_UNBALANCED else current_level
        )
        if mode == MODE_SENSOR_CONTROL:
            registers[REGISTER_EXTRACT_MODE_STATUS] = current_level


def request_echo(address: int, value: int) -> bytes:
    return address.to_bytes(2, "big") + value.to_bytes(2, "big")


def _rtu_frame(payload: bytes) -> bytes:
    crc = 0xFFFF
    for byte in payload:
        crc ^= byte
        for _ in range(8):
            crc = (crc >> 1) ^ (0xA001 if crc & 1 else 0)
    return payload + crc.to_bytes(2, "little")


def _exception_frame(unit_id: int, function: int, code: int) -> bytes:
    return _rtu_frame(bytes((unit_id, function | 0x80, code)))


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run a local, stateful RTU-over-TCP mock for the Meltem integration."
    )
    parser.add_argument("--host", default=DEFAULT_HOST, help="Bind address (default: loopback).")
    parser.add_argument(
        "--port", type=int, default=DEFAULT_PORT, help="TCP port to listen on (default: 5020)."
    )
    parser.add_argument(
        "--units",
        type=parse_units,
        default=parse_units(DEFAULT_UNITS),
        help=f"Comma-separated address:profile pairs (default: {DEFAULT_UNITS}).",
    )
    parser.add_argument(
        "--no-units",
        action="store_true",
        help="Report zero configured units so the setup flow shows no_devices_found.",
    )
    parser.add_argument(
        "--silent-unit",
        type=int,
        action="append",
        default=[],
        metavar="ADDRESS",
        help="Drop requests to this configured unit; repeat for more than one.",
    )
    parser.add_argument(
        "--response-delay-ms",
        type=float,
        default=0,
        help="Delay every Modbus response by this many milliseconds.",
    )
    parser.add_argument(
        "--single-client",
        action="store_true",
        help="Reject another connection while one client is connected.",
    )
    args = parser.parse_args(argv)
    if not 0 <= args.port <= 65535:
        parser.error("--port must be between 0 and 65535")
    if args.response_delay_ms < 0:
        parser.error("--response-delay-ms must be non-negative")
    if set(args.silent_unit) - set(args.units):
        parser.error("--silent-unit must name an address from --units")
    return args


async def _serve(args: argparse.Namespace) -> None:
    gateway = RtuTcpMockGateway(
        args.units,
        no_units=args.no_units,
        silent_units=args.silent_unit,
        response_delay=args.response_delay_ms / 1000,
        single_client=args.single_client,
    )
    port = await gateway.start(args.host, args.port)
    print(f"Mock Meltem gateway listening on {args.host}:{port}", flush=True)
    print(
        "Reported units: "
        + (
            "none"
            if args.no_units
            else ", ".join(f"{key}={value}" for key, value in args.units.items())
        ),
        flush=True,
    )
    print("Press Ctrl+C to stop.", flush=True)
    try:
        await gateway.serve_forever()
    finally:
        await gateway.close()


def main() -> None:
    args = parse_args()
    try:
        asyncio.run(_serve(args))
    except KeyboardInterrupt:
        print("Mock Meltem gateway stopped.", flush=True)
    except OSError as err:
        raise SystemExit(f"Could not start mock gateway: {err}") from err


if __name__ == "__main__":
    main()
