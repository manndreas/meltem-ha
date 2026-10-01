# Plan: Migrating to the Modbus support of Home Assistant

- Target version: `4.0.0`
- Minimum Home Assistant version: `2026.10.0` (the shared connection arrived
  in `2026.9`, but that release pins a `modbus-connection` without the APIs
  used here; see [Implementation status](#implementation-status))
- Status: 2026-10-01, implemented on branch `feature/ha-modbus-connection`;
  phases 0 and 8 (real gateway) are still open

## Summary

The integration will get its Modbus connection from the Home Assistant
`modbus` integration via `homeassistant.components.modbus.async_get_unit`.
Underneath, that is always tmodbus via `modbus-connection`. Registers are
modelled with the device-modelling framework of `modbus-connection`
(`Component`, `Device`).

Every block that is read as one request today becomes its own `Component`,
pinned with `register_ranges`. That way the integration initially sends exactly
the same requests to the gateway as it does today. The coordinator,
`RoomState`, and the entities stay unchanged. The layer below becomes async and
uses the framework.

## State before the migration

Line references in this section and in the phases below point to commit
`4009a32`, before the implementation.

- Transport: synchronous `pymodbus.ModbusSerialClient` (serial RTU, 19200 8E1)
  over USB to the `M-WRG-GW`, run via `hass.async_add_executor_job`.
- [modbus_client.py](../custom_components/meltem_ventilation/modbus_client.py):
  `MeltemModbusClient` with a `threading.RLock` ([L163](../custom_components/meltem_ventilation/modbus_client.py#L163)),
  its own reconnect ([`_ensure_client`, L511](../custom_components/meltem_ventilation/modbus_client.py#L511)),
  its own retry logic ([`_read_holding_registers_with_retry`, L568](../custom_components/meltem_ventilation/modbus_client.py#L568),
  [`_write_uint16`, L1445](../custom_components/meltem_ventilation/modbus_client.py#L1445)),
  and `_PYMODBUS_RETRIES = 0` ([L126](../custom_components/meltem_ventilation/modbus_client.py#L126)).
- [modbus_helpers.py](../custom_components/meltem_ventilation/modbus_helpers.py):
  `SerialSettings` ([L68](../custom_components/meltem_ventilation/modbus_helpers.py#L68)),
  setup-time detection ([`detect_slave_details_with_client`, L187](../custom_components/meltem_ventilation/modbus_helpers.py#L187)),
  and gateway discovery ([`discover_gateway_nodes`, L336](../custom_components/meltem_ventilation/modbus_helpers.py#L336)).
- [coordinator.py](../custom_components/meltem_ventilation/coordinator.py):
  one `_gateway_lock` ([L245](../custom_components/meltem_ventilation/coordinator.py#L245)),
  executor calls at L497, L517, L892, L958, L971, and L989, `sync_sleep(0.5)` at L1052.
- Timing in [const.py](../custom_components/meltem_ventilation/const.py#L45-L48):
  `FIXED_TIMEOUT`, `SCAN_TIMEOUT`, and `SETUP_PROBE_TIMEOUT` are all `0.8`,
  `REQUEST_GAP_SECONDS = 0.1`.

## Decisions

| Topic | Decision |
|---|---|
| Transport | Full switch to `async_get_unit` and `async_get_temporary_unit`. The backend is always tmodbus. |
| Modelling | Introduce the device-modelling framework (`Component`, fields, `Device`). |
| Location of device logic | A `device/` subpackage inside the integration, with no Home Assistant imports. No separate PyPI library. |
| Retry | Timeouts and protocol errors are retried once without disconnecting. `disconnect()` only after 3 consecutive timeouts with no response in between. |
| `tools/` | All 11 scripts move to `modbus-connection` with tmodbus. |
| Version | `4.0.0`, minimum version in `hacs.json` is `2026.10.0`. |
| Config entries | Data stays the same, no migration needed. |

## Verified API facts

Sources: core source `homeassistant/components/modbus/connection.py` (dev branch)
and the `modbus-connection` documentation.

- `async_get_unit(hass, entry, params, unit_id) -> ModbusUnit`
  - Multiple unit IDs per config entry are supported (internally `units: dict[entry_id, set[int]]`).
  - Release runs through `entry.async_on_unload`. Once the last holder is gone, the connection is closed.
  - The call itself does no I/O. The connection is opened on the first request.
- `async_get_temporary_unit(hass, params, unit_id)` is an async context manager for config flows.
- The same endpoint with different link settings raises `HomeAssistantError`.
  For serial links the endpoint is `("serial", device)`, and the path is
  compared verbatim. `/dev/serial/by-id/...` and `/dev/ttyUSB0` therefore count
  as different.
- With no requirements set, the connection uses a 10 s timeout.
  - `unit.require_timeout(s)` is a floor; the largest value wins.
  - `unit.set_message_spacing(s)` keeps the line quiet before and after every request to that unit.
- Exceptions:
  - `ModbusConnectionError`, with subclass `ClientClosedError`
  - `ModbusTimeoutError`
  - `ModbusProtocolError`, with subclass `ModbusDesyncError`. On desync the library disconnects by itself.
  - `ModbusExceptionError` with typed subclasses, for example
    `IllegalDataAddressError` (2), `ServerDeviceBusyError` (6), and `GatewayTargetError` (11).
- tmodbus retries `SERVER_DEVICE_BUSY` internally for up to 60 s. It does not retry timeouts.
- `Component.async_update()` is atomic: one refused block fails the whole update.
  - `register_ranges` pins the blocks; reads are never merged across range boundaries.
  - `restrict_fields()` must be called before the first update.
- `Device.async_poll(names)` returns an `UpdateReport` with `updated` and `failed`.
  - `ModbusConnectionError` propagates immediately.
  - `ModbusTimeoutError` propagates only while nothing has answered in that poll yet.
- Multi-register values support `word_order="little"`. That matches today's word swap.
- The pytest plugin provides `mock_modbus_connection` and `mock_modbus_unit`, with:
  - `holding`, `fail_read`, `fail_write`, and `fail_requests`
  - `read_events` for request shapes
  - `message_spacing` and `required_timeout` as attributes
- The pins in the core manifest (dev branch) are `pymodbus==3.13.1`,
  `modbus-connection[tmodbus]==4.12.3`, and `tmodbus==0.6.2`.
  - HA `2026.9.x` pins `modbus-connection[tmodbus]==4.10.0`. That version has
    no `require_timeout`, no `Device`, a fixed 10 s timeout, and spacing per
    unit only.
  - HA `2026.10.0b0` pins `modbus-connection==4.12.3` and `tmodbus==0.6.2`.
    This is why the minimum is `2026.10.0`.

## Mapping today's blocks to components

The goal is request parity. Every row becomes one `Component` with exactly this
`register_ranges`.

| Component | Registers | Count | Type / decoding | Read group(s) | When read |
|---|---|---|---|---|---|
| Airflow | 41020–41021 | 2 | uint16 (extract, supply) | `flow` | `refresh_airflow` |
| Mode, full | 41120–41124 | 5 | uint16, fields 41120–41124 writable | `flow_control`, `intensive` | `refresh_airflow`, with backoff |
| Mode, short (`restrict_fields`) | 41120–41121 | 2 | uint16 | `flow_control` | fallback when the full block fails (HW-4) |
| Current level, single | 41121 | 1 | uint16 | `flow_control` | only when no mode block was delivered |
| Extract target, single | 41122 | 1 | uint16 | `flow_control` | unbalanced without the full block |
| Status | 41016–41018 | 3 | uint16 → bool | `status`, `filter` | `refresh_status` or `refresh_filter_change_due` |
| RF status | 40101 | 1 | uint16 → bool | `status` | `refresh_status` |
| Temperatures | 41000–41005 | 6 | float32 little (extract 41000, outdoor 41002, exhaust 41004) | `temperature` | `refresh_temperatures`, outdoor via `refresh_environment` |
| Temperatures, plain (`restrict_fields`) | 41004–41005 | 2 | float32 little | `temperature` | plain profiles |
| Supply air temperature | 41009–41010 | 2 | float32 little | `temperature` | `refresh_temperatures` |
| Extract air quality | 41006–41007 | 2 | uint16 (humidity, CO2) | `temperature` | `refresh_environment`, profiles f/fc |
| Supply air quality | 41011–41013 | 3 | uint16 (humidity 41011, VOC 41013) | `temperature` | `refresh_environment`, profiles f/fc_voc |
| Filter days | 41027 | 1 | uint16 | `filter` | `refresh_filter_days` |
| Operating hours | 41030–41031 | 2 | uint32 little | `hours` | `refresh_operating_hours` |
| Software version | 40004 | 1 | uint16 | `hours` | `refresh_operating_hours` |
| Control settings | 42000–42005 | 6 | uint16, writable | `control_settings` | `refresh_control_settings` |
| Command (APPLY) | 41132 | – | write only | – | never polled |
| Detection | 40002–40003, 41006, 41007, 41011, 41013 | single | uint32 little / uint16 | setup | config flow, options flow |
| Gateway (unit 1) | 43901, 43902+n (n ≤ 32) | 1, n | uint16 | discovery | config flow, options flow, diagnostics |

The order within a job stays as today: airflow, profile values, status,
maintenance, control settings, RF, mode.

The optional-read backoff (30 s to 300 s, at most 5 failures) still applies
only to the mode family, as today, and is reset after every successful write.
Its keys become `(slave, component name)` instead of `(slave, address, count)`.

### Write sequences (unchanged)

| Method | Registers in order |
|---|---|
| `write_level` | 41120 = 1 (for 0) or 3, 41121 = scaled, 41132 = 0 |
| `write_unbalanced_levels` | 41120 = 4, 41121 = supply scaled, 41122 = extract scaled, 41132 = 0 |
| `write_operating_mode` | Off: 41120 = 1, 41121 = 0. Manual: 41120 = 3, 41121. Unbalanced: 41120 = 4, 41121, 41122. Sensor control: 41120 = 2, 41121 = 112/144/16. Then 41132 = 0 in every case |
| `write_preset_mode` | Intensive: 41123 = 3, 41124 = 227. Otherwise: 41123 = 0, 41124 = 0, 41120 = 3, 41121 = 228/229/230. Then 41132 = 0 |
| `clear_intensive` | 41123 = 0, 41124 = 0, 41132 = 0 |
| `write_control_setting` | 42000–42005, clamped and rounded to the step size, no APPLY. Returns the written value |

## Transport and retry rules

Implemented in a wrapper around the `ModbusUnit` (phase 1, `transport.py`). It
applies to reads and writes; writes set absolute values and can therefore be
retried.

| Error | Behavior |
|---|---|
| `ModbusTimeoutError` | retry once, do not disconnect; counts toward the timeout counter |
| `ModbusProtocolError` (for example CRC) | same as timeout |
| `ModbusDesyncError` | the library disconnects by itself; retry once |
| `GatewayTargetError`, `GatewayPathUnavailableError` | retry once; resets the timeout counter, because the gateway itself answered |
| `ModbusConnectionError` (except `ClientClosedError`) | wait 0.5 s, retry once; the library reconnects |
| `ClientClosedError` | immediately `MeltemConnectionError` |
| other `ModbusExceptionError` | no retry, becomes `MeltemModbusError` |
| 3 consecutive timeouts with no response in between (across all units) | `disconnect()` and reset the counter |
| `ServerDeviceBusyError` | tmodbus retries internally for up to 60 s; check on the gateway |

Further points:

- Per unit: `set_message_spacing(REQUEST_GAP_SECONDS)` and `require_timeout(FIXED_TIMEOUT)`.
- The time of the last response is tracked per slave. `seconds_since_successful_read` builds on it.
- Error mapping in the client: `ModbusConnectionError` becomes `MeltemConnectionError`, every other `ModbusError` becomes `MeltemModbusError`.

## Phases

### Phase 0: Preparation (must happen before everything else)

1. Take a reference measurement on the real gateway with today's pymodbus tools
   and save the results:
   - [benchmark_gateway.py](../tools/benchmark_gateway.py)
   - [benchmark_integration_like.py](../tools/benchmark_integration_like.py)
   - [profile_register_reads.py](../tools/profile_register_reads.py)

   This has to happen before phase 6, because the tools are migrated there.
2. Check whether the tmodbus serial link works on Windows with a COM port, for
   example with a read of 43901 on unit 1.
3. Pin versions:
   - Which versions of `modbus-connection`, `tmodbus`, and `pymodbus` ship with HA `2026.9.0`?
   - Do `set_message_spacing`, `require_timeout`, `restrict_fields`, `Device.async_poll`, and `register_ranges` already exist there?
   - Does `pytest-homeassistant-custom-component==0.13.367` belong to HA 2026.9.x?

### Phase 1: `device/` subpackage without Home Assistant dependencies

Path: `custom_components/meltem_ventilation/device/`. Depends on phase 0.

1. `components.py`: all components from the table above.
   - Each with `register_ranges` set to exactly its block.
   - Writable fields for the mode block, the control settings, and APPLY.
2. `codec.py`: move the pure functions out of `modbus_client.py`.
   - Airflow scaling
   - Decoding of operation mode, preset, and intensive
   - Target readback (balanced and unbalanced)
   - Clamping and rounding of control settings
   - Bool coercion
3. `device.py`:
   - `MeltemRoomDevice(Device)` per room. For plain profiles the temperature component is restricted to exhaust air with `restrict_fields`.
   - `MeltemGatewayDevice(Device)` for unit 1.
   - Async functions for detection and discovery.
4. `transport.py`: the wrapper from [Transport and retry rules](#transport-and-retry-rules), with shared state across all units.

### Phase 2: Rebuild the client as async

File: [modbus_client.py](../custom_components/meltem_ventilation/modbus_client.py). Depends on phase 1.

1. The constructor receives a function that returns a unit for a slave ID.
   Units and room devices are cached per slave.
2. Remove: `threading.RLock`, `sync_sleep`, the pymodbus imports,
   `_RETRYABLE_EXCEPTIONS`, `_RETRYABLE_MESSAGE_MARKERS`, `_PYMODBUS_RETRIES`,
   and `_ensure_client`.
3. `read_room_state` ([L247](../custom_components/meltem_ventilation/modbus_client.py#L247)) becomes async.
   - For each `RefreshPlan`, the due components are read via `Device.async_poll`.
   - The `UpdateReport` is mapped onto the per-group `ReadHealth`. `_updated_read_health` keeps its meaning.
   - The mode family gets its own logic. It reproduces what
     `_read_mode_block` ([L1143](../custom_components/meltem_ventilation/modbus_client.py#L1143)) and
     `_read_mode_group` ([L1185](../custom_components/meltem_ventilation/modbus_client.py#L1185)) do today:
     fallback from 5 to 2 registers, single reads of 41121 and 41122,
     reuse of the mode block (HW-6), and backoff.
   - Values that are not due are still carried forward from the previous state (`_coalesce`).
   - If a unit does not answer at all (timeout before anything else has answered), the rest of the job is skipped. The due groups are marked as failed; no exception is raised.
4. The write methods starting at [L372](../custom_components/meltem_ventilation/modbus_client.py#L372) become async.
   The sequences stay as in the table above; writes go through `Component.write`.
5. `discover_gateway_units`, `probe_slave_details`, and `seconds_since_successful_read` become async or stay as they are.
   `shutdown()` only sets a flag that rejects late operations.

### Phase 3: Helper functions

File: [modbus_helpers.py](../custom_components/meltem_ventilation/modbus_helpers.py). Runs in parallel with phase 2.

1. Remove: `SerialSettings`, `build_client`, `build_scan_settings`,
   `build_setup_probe_settings`, `validate_serial_connection`, `scan_available_slaves`,
   `detect_slave_details`, and the synchronous `_safe_read_*` and `_read_gateway_registers`.
2. New: `build_serial_params(port) -> ModbusSerialParams` (19200, 8, E, 1, RTU).
3. Keep: `resolve_preferred_port_path` (still runs in the executor), the
   plausibility checks, and `supported_entity_keys_for_profile`.

### Phase 4: Home Assistant wiring

Depends on phases 2 and 3.

1. [manifest.json](../custom_components/meltem_ventilation/manifest.json):
   - `"dependencies": ["modbus"]`
   - `"requirements": ["modbus-connection>=<version from phase 0>"]`, `pymodbus` is dropped
   - `"loggers": ["modbus_connection", "tmodbus"]`
   - `"version": "4.0.0"`
2. [__init__.py](../custom_components/meltem_ventilation/__init__.py#L156-L250):
   - `async_get_unit` for unit 1 and all room slaves.
   - One read of 43901 during setup. If it fails with `ModbusError`, raise `ConfigEntryNotReady`.
   - `HomeAssistantError` (conflicting link settings) becomes `ConfigEntryError`.
   - The executor calls of `ensure_connected` and `shutdown` are removed (L212, L225, L230, L249).
3. [coordinator.py](../custom_components/meltem_ventilation/coordinator.py):
   - The executor calls at L497, L517, L892, L958, L971, and L989 are replaced by `await`.
   - `_read_all_rooms_full` and `_read_one_job` become async.
   - `sync_sleep(0.5)` at L1052 becomes `async_sleep(0.5)`; the `sync_sleep` alias is removed.
   - The `write_method` in `_async_write_with_confirmation` is awaited.
   - `_gateway_lock` stays.
4. [config_flow.py](../custom_components/meltem_ventilation/config_flow.py):
   - `_async_scan_slaves` and `_async_probe_discovered_slaves` (L241–L316) get unit 1 and the slaves as temporary units via `async_get_temporary_unit`, held together with `AsyncExitStack`.
   - Use the normalized by-id path everywhere.
   - `async_step_edit_connection` (L588) validates a new port with a read of 43901 on a temporary unit.
   - `HomeAssistantError` becomes `cannot_connect`.
5. [const.py](../custom_components/meltem_ventilation/const.py#L45-L48):
   - `SCAN_TIMEOUT` and `SETUP_PROBE_TIMEOUT` are removed.
   - New: `TRANSPORT_DISCONNECT_AFTER_TIMEOUTS = 3`.

### Phase 5: Tests

Runs per module in parallel with phases 1 to 4.

1. [requirements-test.txt](../requirements-test.txt):
   - `pytest-homeassistant-custom-component` matching 2026.9.x.
   - `modbus-connection[tmodbus]` and `tmodbus` with the versions HA ships.
   - `pymodbus` stays, because the core `modbus` integration imports it.
2. New tests for the device layer with `mock_modbus_unit`. A **parity test**
   uses `read_events` to check that the request shapes (address and count) per
   `RefreshPlan` and profile match the table above.
3. Port the scenarios from these files to the mock:
   - [test_modbus_client.py](../tests/test_modbus_client.py)
   - [test_modbus_client_runtime.py](../tests/test_modbus_client_runtime.py)
   - [test_modbus_helpers.py](../tests/test_modbus_helpers.py)

   Useful tools:
   - Reproduce HW-4: `fail_read(41123, IllegalDataAddressError())`. This only hits the 5-register block.
   - Reproduce a silent unit: `fail_requests(ModbusTimeoutError())`.
   - Check timing: `message_spacing == 0.1` and `required_timeout == 0.8`.

   Tests that only cover pymodbus internals (port lock, `isError()`, `device_id=`) are dropped.
4. New tests for the retry rules: one retry, disconnect after 3 timeouts, counter reset after a response.
5. The `_FakeClient` classes in the coordinator tests become async; patches of `sync_sleep` become `async_sleep`.
6. In [test_config_flow.py](../tests/test_config_flow.py) and [test_init.py](../tests/test_init.py)
   the patches target `async_get_temporary_unit` and `async_get_unit`
   in the integration's namespace and return units of a `MockModbusConnection`.

### Phase 6: Scripts in `tools/`

Depends on the reference measurement from phase 0; otherwise in parallel.

1. Move all 11 scripts to `asyncio` and
   `modbus_connection.tmodbus.ModbusConnection(ModbusSerialParams(...), timeout=..., message_spacing=...)`.
2. [benchmark_integration_like.py](../tools/benchmark_integration_like.py) and
   [test_41121_after_write.py](../tools/test_41121_after_write.py) use the new async client with `connection.for_unit`.

### Phase 7: Documentation and release metadata

1. [hacs.json](../hacs.json): `"homeassistant": "2026.10.0"`.
2. [pyproject.toml](../pyproject.toml) and `manifest.json`: `4.0.0`.
3. [CHANGELOG.md](../CHANGELOG.md): a `4.0.0` entry with the behavior changes below.
4. [README.md](../README.md): requirements and logger example (`modbus_connection`, `tmodbus`).
5. [CONTRIBUTING.md](../CONTRIBUTING.md): test dependencies.
6. [DEVELOPER.md](DEVELOPER.md): replace the sections on gateway timing and pymodbus with
   the new transport rules, plus a table of how each hardware finding is implemented.
7. [HARDWARE_BACKLOG.md](HARDWARE_BACKLOG.md): new item HW-7 for validating tmodbus (see phase 8).
8. [AGENTS.md](../AGENTS.md): add the `device/` subpackage to the important files.

### Phase 8: Validation on the real gateway (must pass before release)

1. Compare benchmarks with the reference measurement from phase 0: latency and timeout rate.
2. Power off a unit:
   - Does a timeout or a `GatewayTargetError` (code 11) come back?
   - How many timeouts does a job cost?
   - Does the counter trigger a `disconnect()` when it should not?
3. Fallback from 5 to 2 registers on a freshly powered unit (HW-4).
4. Confirmation via 41121 after writes, settle time after writing, and lock duration (HW-2).
5. Does the gateway report "busy" (code 6)? tmodbus then retries for up to 60 s, and `_gateway_lock` stays held that long.
6. Unplug and replug USB, reload and unload: is the port released and cleanly re-established afterwards?
7. 24 hours of continuous operation with all units.

## Intentional behavior changes

- Exception responses from the device are no longer retried. Exception: gateway errors (code 10/11) are retried once.
- Timeouts no longer disconnect the serial link immediately, only after 3 consecutive timeouts.
- A silent unit now costs about one timeout plus one retry per job instead of one timeout per block.
- Setup and `edit_connection` validate the connection with a read of 43901 instead of only opening the port.
- Setup-time detection no longer retries three times like pymodbus did by default; it follows the retry rules above.
- The integration loads the core `modbus` integration. Its actions (`modbus.*`) therefore show up in Home Assistant.

## Implementation status

Phases 1 to 7 are implemented on `feature/ha-modbus-connection`. Phase 0
(reference measurement) has to be taken from `main`, whose tools still use
pymodbus. Phase 8 needs the real gateway. The release waits for HA `2026.10.0`.

Deviations from the plan:

- Minimum HA version `2026.10.0` instead of `2026.9.0`, see
  [Verified API facts](#verified-api-facts).
- `codec.py` was not created. The decode helpers stay in `modbus_client.py`,
  where their tests already target them; moving them would only add churn.
- The `device/` subpackage imports the register constants from `../const.py`.
  It has no other Home Assistant dependency, but `const.py` imports
  `homeassistant.const.Platform`.
- `GatewayTargetError` and `GatewayPathUnavailableError` are retried once but
  reset the timeout counter instead of counting toward it: the gateway
  answered, so the serial link is alive.
- The test harness `pytest-homeassistant-custom-component==0.13.367` ships HA
  `2026.9.4`; [requirements-test.txt](../requirements-test.txt) installs
  `modbus-connection` `4.12.3` on top. Move to the first harness for `2026.10`
  once it exists.
- HA `2026.9` deprecated `via_device`, `async_get_device`, and removing a
  config entry from a device via `async_update_device`. The tests turn these
  into errors, so unit devices now link to the gateway with `via_device_id`,
  and devices of dropped units are removed with `async_remove_device`.
- The timing tools ([benchmark_gateway.py](../tools/benchmark_gateway.py),
  [profile_register_reads.py](../tools/profile_register_reads.py)) keep their
  explicit gap outside the measured latency, so their numbers stay comparable
  with the pymodbus baseline.

## Verification

1. `pytest` and `ruff check custom_components tests`; in CI also hassfest and the HACS check.
2. The parity test via `read_events` passes: identical request shapes as today.
3. All items from phase 8 are done and documented in [HARDWARE_BACKLOG.md](HARDWARE_BACKLOG.md).

## Out of scope

- Raw register diagnostics via `async_read_raw()`
- Wider merged block reads; only after phase 8 and with benchmarks
- Separate PyPI library and submission to HA Core
- TCP and `socket://` in the config flow
- ESPHome serial proxy

## Risks and open points

- The `homeassistant.components.modbus` API is very new; `get_hub` is already
  deprecated. Keep the lower bound in the manifest tight and check the breaking
  changes of every HA release.
- The `modbus-connection` version in HA `2026.9` turned out to be too old
  (`4.10.0`). Resolved by requiring HA `2026.10.0`.
- How tmodbus behaves with the gateway is untested. All hardware findings so far
  come from measurements with pymodbus.
- The tmodbus busy retry can block the bus for up to 60 s.
- Whether the scripts run on Windows depends on tmodbus serial support.
- About 120 tests need to be ported to the mock.

## Later steps

1. Raw register diagnostics via `async_read_raw()`: almost no effort with the framework.
2. Wider merges, for example 41016–41021 in one request, only after phase 8 and with benchmarks.

## Sources

- [Home Assistant 2026.9 release notes](https://www.home-assistant.io/blog/2026/09/02/release-20269/)
- [Modbus integration documentation](https://www.home-assistant.io/integrations/modbus/)
- [Developer blog: Modernizing Modbus](https://developers.home-assistant.io/blog/2026/07/05/modernizing-modbus)
- [Developer documentation: Modbus](https://developers.home-assistant.io/docs/modbus/introduction)
- [modbus-connection documentation](https://home-assistant-libs.github.io/modbus-connection/)
- [Core: modbus/connection.py](https://github.com/home-assistant/core/blob/dev/homeassistant/components/modbus/connection.py)
