"""Run a stateful RTU-over-TCP mock and optional MBAP bridge for Meltem units.

The register model and the hardware behavior it follows are in
``tools/_mock_model.py``. This module adds the network servers, fault
injection, runtime commands, and the command line; see
``docs/dev/MOCK_GATEWAY.md``.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import random
import sys
import threading
import time
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from tools._mock_model import (
    MAX_REGISTER_COUNT,
    NO_HUMIDITY_VALUE,
    NO_SENSOR_VALUE,
    MockGatewayModel,
    RegisterRange,
    load_register_map,
    parse_register_range,
    validate_profile,
    validate_unit_address,
)

__all__ = [
    "DEFAULT_UNITS",
    "FAULT_ACTIONS",
    "MAX_REGISTER_COUNT",
    "MbapRtuTcpMockBridge",
    "NO_HUMIDITY_VALUE",
    "NO_SENSOR_VALUE",
    "RtuTcpMockGateway",
    "build_gateway",
    "main",
    "parse_args",
    "parse_units",
    "request_echo",
    "rtu_frame",
]

DEFAULT_UNITS = "2:ii_fc_voc,3:ii_f"
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 5020
FRAGMENT_PAUSE_SECONDS = 0.02

# Exception replies the integration maps to distinct errors.
EXCEPTION_FAULTS = {
    "illegal-address": 0x02,
    "ack": 0x05,
    "busy": 0x06,
    "gateway-path": 0x0A,
    "gateway-target": 0x0B,
}
FRAME_FAULTS = (
    "bad-crc",
    "short",
    "wrong-unit",
    "wrong-function",
    "wrong-length",
    "wrong-address",
    "wrong-value",
    "fragmented",
    "late",
)
FAULT_ACTIONS = ("timeout", "lost-reply", *EXCEPTION_FAULTS, *FRAME_FAULTS)

HELP = f"""Commands (ADDR 1 is the gateway):
  help                               show this list
  status                             show the simulated state
  pair ADDR PROFILE                  add or re-pair a unit and list it in the gateway
  unpair ADDR                        remove a unit from the gateway list; its requests time out
  silent ADDR on|off                 drop or answer requests to a unit
  set ADDR REG VALUE                 set a raw register
  get ADDR REG [COUNT]               show raw registers, ignoring the read rules
  refuse ADDR REG[-REG]              answer reads and writes there with 0x02
  allow ADDR REG[-REG]               undo refuse
  flag ADDR error|filter|frost on|off
                                     set a status flag of a unit
  app ADDR off|low|medium|high|intensive
  app ADDR SUPPLY [EXTRACT]          change a unit as the Meltem app would (airflow in m3/h)
  reboot [WARMUP_S]                  restart the gateway: uptime 0, warm-up, set registers lock
  warmup SECONDS                     start an RF warm-up now
  clock OFFSET_S                     shift the gateway's UTC clock
  settle MS                          delay status and airflow after each committed write
  delay MS | delay ADDR MS           delay all replies, or the replies of one unit
  drop-rate P | corrupt-rate P       drop or corrupt this share of replies (0..1)
  fault ADDR|* ACTION [COUNT]        script the next replies; ACTION is one of
                                     {", ".join(FAULT_ACTIONS)}
  fault clear                        remove all scripted faults
  dump FILE                          write the current registers as a register map"""


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
            address = validate_unit_address(int(address_text))
            validate_profile(profile)
        except ValueError as err:
            raise argparse.ArgumentTypeError(str(err)) from err
        if address in units:
            raise argparse.ArgumentTypeError(f"duplicate unit address {address}")
        units[address] = profile
    return units


def _split_address_option(value: str, expected: str) -> tuple[int, str]:
    address_text, separator, rest = value.partition(":")
    try:
        if not separator or not rest:
            raise ValueError
        return int(address_text), rest
    except ValueError as err:
        raise argparse.ArgumentTypeError(f"invalid {value!r}; expected {expected}") from err


def parse_refusal(value: str) -> tuple[int, RegisterRange]:
    """Parse ``ADDR:REG`` or ``ADDR:FIRST-LAST``."""

    address, registers = _split_address_option(value, "ADDR:REG or ADDR:FIRST-LAST")
    try:
        return address, parse_register_range(registers)
    except ValueError as err:
        raise argparse.ArgumentTypeError(str(err)) from err


def parse_unit_delay(value: str) -> tuple[int, float]:
    """Parse ``ADDR:MS``."""

    address, delay_text = _split_address_option(value, "ADDR:MS")
    try:
        delay = float(delay_text)
    except ValueError as err:
        raise argparse.ArgumentTypeError(f"invalid delay {delay_text!r}") from err
    if delay < 0:
        raise argparse.ArgumentTypeError("delays must be non-negative")
    return address, delay


def _validate_rate(rate: float, name: str) -> float:
    if not 0 <= rate <= 1:
        raise ValueError(f"the {name} must be between 0 and 1")
    return rate


def _validate_delay(seconds: float) -> float:
    if seconds < 0:
        raise ValueError("delays must be non-negative")
    return seconds


def request_echo(address: int, value: int) -> bytes:
    return address.to_bytes(2, "big") + value.to_bytes(2, "big")


def rtu_frame(payload: bytes) -> bytes:
    crc = 0xFFFF
    for byte in payload:
        crc ^= byte
        for _ in range(8):
            crc = (crc >> 1) ^ (0xA001 if crc & 1 else 0)
    return payload + crc.to_bytes(2, "little")


def _exception_frame(unit_id: int, function: int, code: int) -> bytes:
    return rtu_frame(bytes((unit_id, function | 0x80, code)))


def _distort(action: str | None, response: bytes, address: int, value: int) -> bytes:
    """Return ``response`` as broken by a frame fault."""

    if action == "bad-crc":
        return response[:-1] + bytes((response[-1] ^ 0xFF,))
    if action == "short":
        return response[:-2]
    if action == "wrong-unit":
        return rtu_frame(bytes(((response[0] % 247) + 1,)) + response[1:-2])
    if action == "wrong-function":
        return rtu_frame(response[:1] + bytes((4,)) + response[2:-2])
    if response[1] == 3 and action == "wrong-length":
        return rtu_frame(response[:2] + bytes((response[2] + 2,)) + response[3:-2] + b"\0\0")
    if response[1] == 6 and action == "wrong-address":
        return rtu_frame(response[:2] + request_echo((address + 1) & 0xFFFF, value))
    if response[1] == 6 and action == "wrong-value":
        return rtu_frame(response[:2] + request_echo(address, (value + 1) & 0xFFFF))
    return response


@dataclass(slots=True)
class _Fault:
    address: int | None
    action: str
    remaining: int


class RtuTcpMockGateway:
    """A stateful RTU-over-TCP server for Home Assistant smoke tests."""

    def __init__(
        self,
        units: Mapping[int, str],
        *,
        no_units: bool = False,
        silent_units: Sequence[int] = (),
        response_delay: float = 0,
        single_client: bool = False,
        warmup_seconds: float = 0,
        settle_seconds: float = 0,
        refused: Iterable[tuple[int, RegisterRange]] = (),
        unit_delays: Mapping[int, float] | None = None,
        drop_rate: float = 0,
        corrupt_rate: float = 0,
        seed: int | None = None,
        register_maps: Iterable[Mapping[str, Any]] = (),
        clock: Callable[[], float] = time.monotonic,
        wall_clock: Callable[[], float] = time.time,
    ) -> None:
        self.model = MockGatewayModel(
            units,
            no_units=no_units,
            silent_units=silent_units,
            warmup_seconds=warmup_seconds,
            settle_seconds=settle_seconds,
            clock=clock,
            wall_clock=wall_clock,
        )
        for data in register_maps:
            self.model.apply_register_map(data)
        for address, registers in refused:
            self.model.refuse(address, registers)
        self.response_delay = _validate_delay(response_delay)
        self.unit_delays: dict[int, float] = {}
        for address, delay in (unit_delays or {}).items():
            self.set_delay(delay, address)
        self.single_client = single_client
        self.drop_rate = _validate_rate(drop_rate, "drop rate")
        self.corrupt_rate = _validate_rate(corrupt_rate, "corrupt rate")
        self._random = random.Random(seed)
        self._faults: list[_Fault] = []
        self.requests = 0
        self._active_clients = 0
        self._writers: set[asyncio.StreamWriter] = set()
        self._server: asyncio.Server | None = None
        self._control_server: asyncio.Server | None = None

    @property
    def registers(self) -> dict[int, dict[int, int]]:
        return self.model.registers

    @property
    def writes(self) -> int:
        return self.model.writes

    # -- Faults and delays ------------------------------------------------

    def inject_fault(self, action: str, address: int | None = None, count: int = 1) -> None:
        """Apply ``action`` to the next ``count`` replies to ``address`` (any if ``None``)."""

        if action not in FAULT_ACTIONS:
            raise ValueError(f"unknown fault {action!r}; choose from {', '.join(FAULT_ACTIONS)}")
        if count < 1:
            raise ValueError("the fault count must be at least 1")
        self._faults.append(_Fault(address, action, count))

    def clear_faults(self) -> None:
        self._faults.clear()

    def set_delay(self, seconds: float, address: int | None = None) -> None:
        """Delay all replies, or with ``address`` additionally the replies of that unit."""

        _validate_delay(seconds)
        if address is None:
            self.response_delay = seconds
            return
        self.model.check_address(address)
        if seconds:
            self.unit_delays[address] = seconds
        else:
            self.unit_delays.pop(address, None)

    def _take_fault(self, address: int) -> str | None:
        for fault in self._faults:
            if fault.address in (None, address):
                fault.remaining -= 1
                if not fault.remaining:
                    self._faults.remove(fault)
                return fault.action
        if self.drop_rate and self._random.random() < self.drop_rate:
            return "timeout"
        if self.corrupt_rate and self._random.random() < self.corrupt_rate:
            return "bad-crc"
        return None

    # -- Server -----------------------------------------------------------

    async def start(self, host: str = DEFAULT_HOST, port: int = 0) -> int:
        """Start listening and return the bound port; port ``0`` picks a free one."""

        if self._server is not None:
            raise RuntimeError("mock gateway is already running")
        self._server = await asyncio.start_server(self._handle_client, host, port)
        return _bound_port(self._server)

    async def start_control(self, host: str = DEFAULT_HOST, port: int = 0) -> int:
        """Accept line-based runtime commands; return the bound port."""

        if self._control_server is not None:
            raise RuntimeError("the control port is already open")
        self._control_server = await asyncio.start_server(self._handle_control, host, port)
        return _bound_port(self._control_server)

    async def close(self) -> None:
        """Stop listening and close active client connections."""

        servers = [server for server in (self._server, self._control_server) if server]
        self._server = self._control_server = None
        for server in servers:
            server.close()
        # Waiting for the servers first would block while a client stays connected.
        writers = tuple(self._writers)
        for writer in writers:
            writer.close()
        if writers:
            await asyncio.gather(
                *(writer.wait_closed() for writer in writers), return_exceptions=True
            )
        for server in servers:
            await server.wait_closed()

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
        late_reply: bytes | None = None
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
                if rtu_frame(request[:-2]) != request:
                    print(f"Invalid RTU CRC from {peer}; closing client.", flush=True)
                    return
                self.requests += 1
                address = int.from_bytes(request[2:4], "big")
                value = int.from_bytes(request[4:6], "big")
                if not self.model.answers(unit_id):
                    print(f"DROP unit={unit_id} function=0x{function:02X} address={address}")
                    continue
                fault = self._take_fault(unit_id)
                if fault is not None:
                    print(
                        f"FAULT {fault} unit={unit_id} function=0x{function:02X} address={address}",
                        flush=True,
                    )
                if fault == "timeout":
                    continue
                if fault in EXCEPTION_FAULTS:
                    response = _exception_frame(unit_id, function, EXCEPTION_FAULTS[fault])
                else:
                    response = self._respond(unit_id, function, address, value)
                    if fault == "lost-reply":
                        continue
                    response = _distort(fault, response, address, value)
                delay = self.response_delay + self.unit_delays.get(unit_id, 0)
                if delay:
                    await asyncio.sleep(delay)
                if late_reply is not None:
                    writer.write(late_reply)
                    late_reply = None
                if fault == "late":
                    # Held back and sent just before the next reply, like a
                    # reply that arrives after the client gave up.
                    late_reply = response
                elif fault == "fragmented":
                    writer.write(response[:3])
                    await writer.drain()
                    await asyncio.sleep(FRAGMENT_PAUSE_SECONDS)
                    writer.write(response[3:])
                else:
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
        if function == 3:
            result = self.model.read(unit_id, address, value)
            if isinstance(result, int):
                print(f"READ unit={unit_id} {address} x{value} -> exception 0x{result:02X}")
                return _exception_frame(unit_id, function, result)
            print(f"READ unit={unit_id} {address} x{value} -> {result}", flush=True)
            payload = bytes((unit_id, function, value * 2)) + b"".join(
                register.to_bytes(2, "big") for register in result
            )
            return rtu_frame(payload)
        error = self.model.write(unit_id, address, value)
        if error is not None:
            print(f"WRITE unit={unit_id} {address} = {value} -> exception 0x{error:02X}")
            return _exception_frame(unit_id, function, error)
        print(f"WRITE unit={unit_id} {address} = {value}", flush=True)
        return rtu_frame(bytes((unit_id, function)) + request_echo(address, value))

    async def _handle_control(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        self._writers.add(writer)
        try:
            while line := await reader.readline():
                output = self.execute(line.decode(errors="replace"))
                writer.write((output + "\n").encode())
                await writer.drain()
        except ConnectionError, OSError:
            pass
        finally:
            self._writers.discard(writer)
            writer.close()
            await writer.wait_closed()

    # -- Runtime commands -------------------------------------------------

    def execute(self, line: str) -> str:
        """Run one runtime command and return its output."""

        words = line.split()
        if not words or words[0].startswith("#"):
            return ""
        command, args = words[0].lower(), words[1:]
        handler = self._commands().get(command)
        if handler is None:
            return f"error: unknown command {command!r}; type 'help' for the list"
        try:
            return handler(args)
        except ValueError as err:
            return f"error: {err}"

    def _commands(self) -> dict[str, Callable[[list[str]], str]]:
        return {
            "help": lambda args: HELP,
            "status": self._cmd_status,
            "pair": self._cmd_pair,
            "unpair": self._cmd_unpair,
            "silent": self._cmd_silent,
            "set": self._cmd_set,
            "get": self._cmd_get,
            "refuse": self._cmd_refuse,
            "allow": self._cmd_allow,
            "flag": self._cmd_flag,
            "app": self._cmd_app,
            "reboot": self._cmd_reboot,
            "warmup": self._cmd_warmup,
            "clock": self._cmd_clock,
            "settle": self._cmd_settle,
            "delay": self._cmd_delay,
            "drop-rate": self._cmd_drop_rate,
            "corrupt-rate": self._cmd_corrupt_rate,
            "fault": self._cmd_fault,
            "dump": self._cmd_dump,
        }

    def _cmd_status(self, args: list[str]) -> str:
        _expect(args, 0, 0, "status")
        unit_delays = ", ".join(
            f"{address}: {delay * 1000:.0f} ms"
            for address, delay in sorted(self.unit_delays.items())
        )
        faults = ", ".join(
            f"{'*' if fault.address is None else fault.address} {fault.action} x{fault.remaining}"
            for fault in self._faults
        )
        transport = (
            f"transport: delay {self.response_delay * 1000:.0f} ms"
            f"{f' (units {unit_delays})' if unit_delays else ''}, "
            f"drop rate {self.drop_rate:g}, corrupt rate {self.corrupt_rate:g}, "
            f"scripted faults [{faults}], requests {self.requests}, writes {self.writes}"
        )
        return f"{self.model.describe()}\n{transport}"

    def _cmd_pair(self, args: list[str]) -> str:
        _expect(args, 2, 2, "pair ADDR PROFILE")
        address = _int(args[0], "address")
        self.model.pair(address, args[1])
        return f"unit {address} paired as {args[1]}; the gateway lists {self.model.paired}"

    def _cmd_unpair(self, args: list[str]) -> str:
        _expect(args, 1, 1, "unpair ADDR")
        address = _int(args[0], "address")
        self.model.unpair(address)
        return f"unit {address} unpaired; the gateway lists {self.model.paired}"

    def _cmd_silent(self, args: list[str]) -> str:
        _expect(args, 2, 2, "silent ADDR on|off")
        address = _int(args[0], "address")
        silent = _on_off(args[1])
        self.model.set_silent(address, silent)
        return f"unit {address} {'drops' if silent else 'answers'} requests"

    def _cmd_set(self, args: list[str]) -> str:
        _expect(args, 3, 3, "set ADDR REG VALUE")
        address, register, value = (_int(arg, "number") for arg in args)
        self.model.set_register(address, register, value)
        return f"address {address} register {register} = {value}"

    def _cmd_get(self, args: list[str]) -> str:
        _expect(args, 2, 3, "get ADDR REG [COUNT]")
        address, register = _int(args[0], "address"), _int(args[1], "register")
        count = _int(args[2], "count") if len(args) == 3 else 1
        if not 1 <= count <= MAX_REGISTER_COUNT:
            raise ValueError(f"the count must be between 1 and {MAX_REGISTER_COUNT}")
        return f"{register}: {self.model.peek(address, register, count)}"

    def _cmd_refuse(self, args: list[str], refused: bool = True) -> str:
        _expect(args, 2, 2, f"{'refuse' if refused else 'allow'} ADDR REG[-REG]")
        address = _int(args[0], "address")
        self.model.refuse(address, parse_register_range(args[1]), refused)
        return f"address {address} {'refuses' if refused else 'allows'} {args[1]}"

    def _cmd_allow(self, args: list[str]) -> str:
        return self._cmd_refuse(args, refused=False)

    def _cmd_flag(self, args: list[str]) -> str:
        _expect(args, 3, 3, "flag ADDR error|filter|frost on|off")
        address = _int(args[0], "address")
        self.model.set_flag(address, args[1], _on_off(args[2]))
        return f"unit {address} {args[1]} {args[2]}"

    def _cmd_app(self, args: list[str]) -> str:
        _expect(args, 2, 3, "app ADDR off|low|medium|high|intensive|SUPPLY [EXTRACT]")
        address = _int(args[0], "address")
        self.model.app_change(address, args[1].lower(), args[2] if len(args) == 3 else None)
        return f"unit {address} changed as from the app"

    def _cmd_reboot(self, args: list[str]) -> str:
        _expect(args, 0, 1, "reboot [WARMUP_S]")
        self.model.reboot(_float(args[0], "warm-up") if args else None)
        return f"gateway restarted; warm-up {self.model.warmup_remaining:.0f} s"

    def _cmd_warmup(self, args: list[str]) -> str:
        _expect(args, 1, 1, "warmup SECONDS")
        self.model.start_warmup(_float(args[0], "warm-up"))
        return f"warm-up {self.model.warmup_remaining:.0f} s"

    def _cmd_clock(self, args: list[str]) -> str:
        _expect(args, 1, 1, "clock OFFSET_S")
        self.model.clock_offset = _int(args[0], "offset")
        return f"gateway clock offset {self.model.clock_offset} s"

    def _cmd_settle(self, args: list[str]) -> str:
        _expect(args, 1, 1, "settle MS")
        self.model.settle_seconds = _validate_delay(_float(args[0], "settle time") / 1000)
        return f"settle {self.model.settle_seconds * 1000:.0f} ms"

    def _cmd_delay(self, args: list[str]) -> str:
        _expect(args, 1, 2, "delay MS | delay ADDR MS")
        if len(args) == 1:
            self.set_delay(_float(args[0], "delay") / 1000)
            return f"reply delay {self.response_delay * 1000:.0f} ms"
        address = _int(args[0], "address")
        self.set_delay(_float(args[1], "delay") / 1000, address)
        return f"extra reply delay for address {address}: {args[1]} ms"

    def _cmd_drop_rate(self, args: list[str]) -> str:
        _expect(args, 1, 1, "drop-rate P")
        self.drop_rate = _validate_rate(_float(args[0], "rate"), "drop rate")
        return f"drop rate {self.drop_rate:g}"

    def _cmd_corrupt_rate(self, args: list[str]) -> str:
        _expect(args, 1, 1, "corrupt-rate P")
        self.corrupt_rate = _validate_rate(_float(args[0], "rate"), "corrupt rate")
        return f"corrupt rate {self.corrupt_rate:g}"

    def _cmd_fault(self, args: list[str]) -> str:
        if args == ["clear"]:
            self.clear_faults()
            return "scripted faults cleared"
        _expect(args, 2, 3, "fault ADDR|* ACTION [COUNT] | fault clear")
        address = None if args[0] == "*" else _int(args[0], "address")
        if address is not None:
            self.model.check_address(address)
        count = _int(args[2], "count") if len(args) == 3 else 1
        self.inject_fault(args[1], address, count)
        return f"next {count} repl{'y' if count == 1 else 'ies'} to {args[0]}: {args[1]}"

    def _cmd_dump(self, args: list[str]) -> str:
        _expect(args, 1, 1, "dump FILE")
        try:
            write_register_map(self, args[0])
        except OSError as err:
            raise ValueError(f"cannot write {args[0]}: {err}") from err
        return f"register map written to {args[0]}"


class MbapRtuTcpMockBridge:
    """Translate Modbus TCP/MBAP requests to a raw RTU-over-TCP mock gateway."""

    def __init__(
        self,
        backend_host: str,
        backend_port: int,
        *,
        backend_timeout: float = 6.0,
        single_client: bool = False,
    ) -> None:
        if not 1 <= backend_port <= 65535:
            raise ValueError("backend TCP port must be between 1 and 65535")
        if backend_timeout <= 0:
            raise ValueError("backend timeout must be positive")
        self.backend_host = backend_host
        self.backend_port = backend_port
        self.backend_timeout = backend_timeout
        self.single_client = single_client
        self._server: asyncio.Server | None = None
        self._writers: set[asyncio.StreamWriter] = set()
        self._active_clients = 0
        self._late_replies = 0

    def inject_late_reply(self, count: int = 1) -> None:
        """Hold the next MBAP response and send it before the following response."""

        if count < 1:
            raise ValueError("the late reply count must be at least 1")
        self._late_replies += count

    async def start(self, host: str = DEFAULT_HOST, port: int = 0) -> int:
        """Start the MBAP endpoint and return its bound port."""

        if self._server is not None:
            raise RuntimeError("MBAP bridge is already running")
        self._server = await asyncio.start_server(self._handle_client, host, port)
        return _bound_port(self._server)

    async def close(self) -> None:
        """Stop the MBAP endpoint and its active client connections."""

        server = self._server
        self._server = None
        if server is not None:
            server.close()
        writers = tuple(self._writers)
        for writer in writers:
            writer.close()
        if writers:
            await asyncio.gather(
                *(writer.wait_closed() for writer in writers), return_exceptions=True
            )
        if server is not None:
            await server.wait_closed()

    async def serve_forever(self) -> None:
        """Wait for clients until the server is cancelled or closed."""

        if self._server is None:
            raise RuntimeError("MBAP bridge is not running")
        await self._server.serve_forever()

    async def _handle_client(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        if self.single_client and self._active_clients:
            print("Rejecting a second MBAP client (--single-client).", flush=True)
            writer.close()
            await writer.wait_closed()
            return
        self._active_clients += 1
        self._writers.add(writer)
        peer = writer.get_extra_info("peername")
        print(f"MBAP client connected: {peer}", flush=True)
        late_response: bytes | None = None
        try:
            while True:
                try:
                    header = await reader.readexactly(7)
                except asyncio.IncompleteReadError:
                    return
                transaction_id = header[:2]
                protocol_id = int.from_bytes(header[2:4], "big")
                length = int.from_bytes(header[4:6], "big")
                unit_id = header[6]
                if protocol_id != 0 or not 2 <= length <= 254:
                    print(f"Closing malformed MBAP client {peer}.", flush=True)
                    return
                pdu = await reader.readexactly(length - 1)
                try:
                    response_pdu = await asyncio.wait_for(
                        self._exchange(unit_id, pdu), timeout=self.backend_timeout
                    )
                except TimeoutError:
                    continue
                response = (
                    transaction_id
                    + b"\x00\x00"
                    + (len(response_pdu) + 1).to_bytes(2, "big")
                    + bytes((unit_id,))
                    + response_pdu
                )
                if self._late_replies:
                    self._late_replies -= 1
                    late_response = response
                    continue
                if late_response is not None:
                    writer.write(late_response)
                    late_response = None
                writer.write(response)
                await writer.drain()
        except (ConnectionError, OSError, ValueError, asyncio.IncompleteReadError) as err:
            print(f"MBAP bridge request from {peer} failed: {err}", flush=True)
        finally:
            self._writers.discard(writer)
            self._active_clients -= 1
            writer.close()
            await writer.wait_closed()
            print(f"MBAP client disconnected: {peer}", flush=True)

    async def _exchange(self, unit_id: int, pdu: bytes) -> bytes:
        if not pdu or pdu[0] not in (3, 6):
            raise ValueError("mock MBAP bridge supports functions 0x03 and 0x06")
        reader, writer = await asyncio.open_connection(self.backend_host, self.backend_port)
        try:
            writer.write(rtu_frame(bytes((unit_id,)) + pdu))
            await writer.drain()
            response = await self._read_rtu_response(reader, unit_id, pdu[0])
            return response[1:-2]
        finally:
            writer.close()
            await writer.wait_closed()

    async def _read_rtu_response(
        self, reader: asyncio.StreamReader, unit_id: int, request_function: int
    ) -> bytes:
        response = await reader.readexactly(2)
        response_unit, response_function = response
        if response_unit != unit_id:
            raise ConnectionError("RTU response has the wrong unit address")
        if response_function == request_function | 0x80:
            response += await reader.readexactly(3)
        elif response_function == 3:
            byte_count = await reader.readexactly(1)
            response += byte_count + await reader.readexactly(byte_count[0] + 2)
        elif response_function == 6:
            response += await reader.readexactly(6)
        else:
            raise ConnectionError("RTU response has an unexpected function code")
        if rtu_frame(response[:-2]) != response:
            raise ConnectionError("RTU response has an invalid CRC")
        return response


def _expect(args: list[str], minimum: int, maximum: int, usage: str) -> None:
    if not minimum <= len(args) <= maximum:
        raise ValueError(f"usage: {usage}")


def _int(text: str, name: str) -> int:
    try:
        return int(text)
    except ValueError as err:
        raise ValueError(f"invalid {name} {text!r}") from err


def _float(text: str, name: str) -> float:
    try:
        return float(text)
    except ValueError as err:
        raise ValueError(f"invalid {name} {text!r}") from err


def _on_off(text: str) -> bool:
    if text.lower() not in ("on", "off"):
        raise ValueError(f"expected on or off, not {text!r}")
    return text.lower() == "on"


def _bound_port(server: asyncio.Server) -> int:
    if not server.sockets:
        raise RuntimeError("mock gateway has no listening socket")
    return int(server.sockets[0].getsockname()[1])


def write_register_map(gateway: RtuTcpMockGateway, path: str | Path) -> None:
    Path(path).write_text(
        json.dumps(gateway.model.to_register_map(), indent=2) + "\n", encoding="utf-8"
    )


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Run a stateful RTU-over-TCP mock and optional Modbus TCP/MBAP bridge "
            "for the Meltem integration."
        ),
        epilog="Type 'help' on standard input or the control port for the runtime commands.",
    )
    parser.add_argument("--host", default=DEFAULT_HOST, help="Bind address (default: loopback).")
    parser.add_argument(
        "--port", type=int, default=DEFAULT_PORT, help="TCP port to listen on (default: 5020)."
    )
    parser.add_argument(
        "--mbap-port",
        type=int,
        help="Also serve Modbus TCP/MBAP through a simulated RTU bridge on this TCP port.",
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
        help=(
            "Report zero configured units so the setup flow shows no_devices_found; "
            "requests to unit addresses then time out."
        ),
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
        "--unit-delay",
        type=parse_unit_delay,
        action="append",
        default=[],
        metavar="ADDR:MS",
        help="Additionally delay the replies of one unit; repeat for more.",
    )
    parser.add_argument(
        "--single-client",
        action="store_true",
        help="Reject another RTU or MBAP connection while a client is connected.",
    )
    parser.add_argument(
        "--warmup-seconds",
        type=float,
        default=0,
        help="Answer the sensor, status, and airflow blocks with Acknowledge for this long.",
    )
    parser.add_argument(
        "--settle-ms",
        type=float,
        default=0,
        help="Delay the status map and airflow after each committed write.",
    )
    parser.add_argument(
        "--refuse",
        type=parse_refusal,
        action="append",
        default=[],
        metavar="ADDR:REG[-REG]",
        help="Answer these registers with Illegal Data Address; repeat for more.",
    )
    parser.add_argument(
        "--drop-rate",
        type=float,
        default=0,
        help="Share of replies to drop, between 0 and 1.",
    )
    parser.add_argument(
        "--corrupt-rate",
        type=float,
        default=0,
        help="Share of replies to send with a broken CRC, between 0 and 1.",
    )
    parser.add_argument("--seed", type=int, help="Seed for the drop and corrupt rates.")
    parser.add_argument(
        "--register-map",
        action="append",
        default=[],
        metavar="FILE",
        help="Apply a register map or capture_setting_family capture; repeat for more.",
    )
    parser.add_argument(
        "--dump-register-map",
        metavar="FILE",
        help="Write the resulting registers as a register map and exit.",
    )
    parser.add_argument(
        "--control-port",
        type=int,
        help="Also accept runtime commands on this TCP port (0 picks a free one).",
    )
    args = parser.parse_args(argv)
    for name in ("port", "mbap_port", "control_port"):
        value = getattr(args, name)
        if value is not None and not 0 <= value <= 65535:
            parser.error(f"--{name.replace('_', '-')} must be between 0 and 65535")
    for name in ("response_delay_ms", "warmup_seconds", "settle_ms"):
        if getattr(args, name) < 0:
            parser.error(f"--{name.replace('_', '-')} must be non-negative")
    for name in ("drop_rate", "corrupt_rate"):
        if not 0 <= getattr(args, name) <= 1:
            parser.error(f"--{name.replace('_', '-')} must be between 0 and 1")
    return args


def build_gateway(args: argparse.Namespace) -> RtuTcpMockGateway:
    """Build the gateway from parsed arguments; raises ``ValueError`` on bad input."""

    return RtuTcpMockGateway(
        args.units,
        no_units=args.no_units,
        silent_units=args.silent_unit,
        response_delay=args.response_delay_ms / 1000,
        single_client=args.single_client,
        warmup_seconds=args.warmup_seconds,
        settle_seconds=args.settle_ms / 1000,
        refused=args.refuse,
        unit_delays={address: delay / 1000 for address, delay in args.unit_delay},
        drop_rate=args.drop_rate,
        corrupt_rate=args.corrupt_rate,
        seed=args.seed,
        register_maps=[load_register_map(path) for path in args.register_map],
    )


def _run_command(gateway: RtuTcpMockGateway, line: str) -> None:
    if output := gateway.execute(line):
        print(output, flush=True)


def _read_stdin_commands(loop: asyncio.AbstractEventLoop, gateway: RtuTcpMockGateway) -> None:
    stream = sys.stdin
    if stream is None:
        return

    def read() -> None:
        for line in stream:
            try:
                loop.call_soon_threadsafe(_run_command, gateway, line)
            except RuntimeError:
                return

    threading.Thread(target=read, name="mock-gateway-commands", daemon=True).start()


async def _serve(args: argparse.Namespace, gateway: RtuTcpMockGateway) -> None:
    port = await gateway.start(args.host, args.port)
    bridge: MbapRtuTcpMockBridge | None = None
    try:
        print(f"Mock Meltem gateway listening on {args.host}:{port}", flush=True)
        paired = gateway.model.paired
        print(
            "Reported units: "
            + (
                ", ".join(f"{address}={gateway.model.units[address]}" for address in paired)
                if paired
                else "none"
            ),
            flush=True,
        )
        if args.mbap_port is not None:
            bridge = MbapRtuTcpMockBridge(args.host, port, single_client=args.single_client)
            mbap_port = await bridge.start(args.host, args.mbap_port)
            print(
                f"Simulated Modbus TCP/MBAP bridge listening on {args.host}:{mbap_port}", flush=True
            )
        if args.control_port is not None:
            control_port = await gateway.start_control(args.host, args.control_port)
            print(f"Runtime commands on {args.host}:{control_port}", flush=True)
        _read_stdin_commands(asyncio.get_running_loop(), gateway)
        print("Type 'help' for runtime commands. Press Ctrl+C to stop.", flush=True)
        await gateway.serve_forever()
    finally:
        if bridge is not None:
            await bridge.close()
        await gateway.close()


def main(argv: Sequence[str] | None = None) -> None:
    args = parse_args(argv)
    try:
        gateway = build_gateway(args)
    except ValueError as err:
        raise SystemExit(f"Could not start mock gateway: {err}") from err
    if args.dump_register_map:
        try:
            write_register_map(gateway, args.dump_register_map)
        except OSError as err:
            raise SystemExit(f"Could not write {args.dump_register_map}: {err}") from err
        print(f"Register map written to {args.dump_register_map}", flush=True)
        return
    try:
        asyncio.run(_serve(args, gateway))
    except KeyboardInterrupt:
        print("Mock Meltem gateway stopped.", flush=True)
    except OSError as err:
        raise SystemExit(f"Could not start mock gateway: {err}") from err


if __name__ == "__main__":
    main()
