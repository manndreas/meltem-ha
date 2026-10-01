# Developer Notes

This document captures the implementation details of the `Meltem M-WRG`
Home Assistant integration and the decisions behind them.

Related documents:

- `docs/reference/`: manufacturer reference extracted from the original Meltem
  documents
- `docs/MELTEM.md`: observed register behaviour, gateway quirks, and traced
  app writes
- `docs/HARDWARE_BACKLOG.md`: review findings that need a live gateway before
  they can be decided
- `docs/LIVE_GATEWAY_TESTS.md`: how to measure them
- `docs/SETTING_RE_BACKLOG.md`: parked reverse engineering of app settings

## Safety and liability

This is an unofficial community project. It is provided without warranty, and
the authors do not accept liability for damage to Meltem units, gateways,
building systems, Home Assistant hosts, or other connected equipment.

When changing write behavior, timing, or register usage, prefer conservative
defaults and validate against real hardware carefully.

## Current state summary

These are the most important practical findings from the latest hardware tests:

- gateway-backed discovery via `43901` / `43902..` is stable
- a small positive request gap works better than no gap at all
- `REQUEST_GAP_SECONDS = 0.1` was stable on the tested setup
- removing the gap entirely (`0.0`) caused huge latency inflation without
  improving reliability
- `41121` behaves like a fast balanced target readback after writes
- `41020/41021` behave like the effective/current airflow and may lag behind
- many devices return Modbus exceptions for `41120/41121/41122` until a write
  has occurred
- immediate write confirmation polling created unnecessary bus load and was
  removed for airflow writes
- short read failures preserve the previous state, but a unit that keeps
  failing or stays silent is marked unavailable instead of showing frozen
  values
- room entities stay unavailable until the first poll returns at least one
  state value
- unloading shuts the client down for good, so a late post-write readback
  cannot reach the link; Home Assistant's `modbus` integration closes the
  serial port once the entry has released its units
- since `4.0.0` all of this runs over Home Assistant's shared Modbus
  connection (tmodbus) instead of an own pymodbus client; the hardware
  findings above were measured with pymodbus and are re-checked in HW-7

## Scope

The integration currently targets Meltem M-WRG units behind an
`M-WRG-GW` gateway over Modbus RTU via USB.

Supported series/profiles:

- `M-WRG-S`
- `M-WRG-S (-F)`
- `M-WRG-S (-FC)`
- `M-WRG-II`
- `M-WRG-II (-F)`
- `M-WRG-II (-FC)`
- `M-WRG-II (O/VOC-AUL)`

## Gateway network interface

Current working assumption:

- the `M-WRG-GW` gateway is not meaningfully controllable via a local LAN API
- the Meltem mobile app communicates with Meltem's internet services rather
  than directly with a local gateway API for normal control flows
- local Home Assistant integration should therefore continue to treat USB /
  serial Modbus as the primary supported path

Observed basis for that assumption:

- previous app traffic captures showed internet communication rather than a
  usable local control channel
- direct probing of the gateway IP on the tested setup did not reveal any
  useful local TCP service, SSDP response, mDNS service, or other obvious LAN
  control surface

Practical consequence:

- further reverse engineering effort is better spent on USB / serial Modbus
  behavior or, if ever needed, on the app's cloud traffic rather than on the
  gateway's local network interface

## Gateway and Modbus behavior

The gateway is slow and should be treated conservatively, see
[MELTEM.md](MELTEM.md#request-pacing-and-block-reads) for the measurements.

Implementation rules:

- Keep a pause between Modbus requests.
  Current implementation: `REQUEST_GAP_SECONDS = 0.1`
- Retry a request at most once, see [Transport](#transport-home-assistants-shared-modbus-connection).
- Serialize all periodic reads and writes through one shared gateway lock.
- Use one scheduled read job at a time.
- Prefer grouped block reads over single-register reads.

## Transport: Home Assistant's shared Modbus connection

Since `4.0.0` the integration has no serial client of its own. It asks Home
Assistant's `modbus` integration for one `ModbusUnit` per Modbus address:

- `async_get_unit(hass, entry, params, unit_id)` at runtime; the units are
  released when the entry unloads, and the link closes after the last holder
- `async_get_temporary_unit(hass, params, unit_id)` in the config flow
- the backend is always tmodbus via `modbus-connection`; HA `2026.10` pins
  `modbus-connection` `4.12.3`, the first version with `require_timeout` and
  the device-modelling framework used here, hence the minimum HA version
- link parameters are fixed in `build_serial_params`: 19200 baud, 8E1, RTU
- another integration that uses the same port with other link settings makes
  setup fail with `ConfigEntryError` (config flow: `port_in_use`)
- every unit asks for `set_message_spacing(REQUEST_GAP_SECONDS)` and
  `require_timeout(FIXED_TIMEOUT)` before its first request (`prepare_unit`);
  both are floors, so another consumer of the same port can only raise them.
  A YAML hub with `timeout: 3` on the same port would make every silent unit
  cost 3 s per attempt instead of 0.8 s
- the config flow reads the unit list and probes every unit on one temporary
  link, so the port is opened once per step

Register blocks are `Component`s in `device/components.py`. Each one pins
`register_ranges` to exactly the block the pymodbus client read, so the gateway
sees the same requests in the same order. `tests/test_modbus_client.py`
checks this with the mock's `read_events`. Do not merge blocks without a
benchmark on the real gateway.

The retry rules live in `device/transport.py` (`TransportPolicy`, one shared
instance per gateway link):

| Error | Behavior |
|---|---|
| `ModbusTimeoutError`, `ModbusProtocolError` | retry once; counts toward the timeout counter |
| `GatewayTargetError`, `GatewayPathUnavailableError` | retry once; resets the counter, the gateway answered |
| `ModbusConnectionError` | wait `TRANSPORT_RETRY_DELAY_SECONDS`, retry once; the library reconnects |
| `ClientClosedError` | no retry |
| other `ModbusExceptionError` | no retry; resets the counter |
| `TRANSPORT_DISCONNECT_AFTER_TIMEOUTS` timeouts in a row, across all units, and no answer at all for `TRANSPORT_LINK_QUIET_SECONDS` | `disconnect()`; the next request reopens the link, and a new quiet window starts |

An exception response usually means that the register is not available in
the unit's current state, so retrying it would only add bus load. The setup
probes follow the same rules as the runtime client.

The quiet window keeps silent units from recycling a link their neighbours
still answer on, also when two silent units or two jobs of one silent unit
follow each other. Any answer counts, including exception responses. The
diagnostics download shows `link_recycles`, `consecutive_timeouts`, and
`seconds_since_any_answer` under `coordinator.transport`; a recycle is logged
at info level.

The client maps `ModbusConnectionError` to `MeltemConnectionError` and every
other `ModbusError` to `MeltemModbusError`. When a unit times out before any
of its blocks answered in a job, the rest of that job is skipped and all of
its groups are marked failed, so a silent unit costs one timeout plus one
retry per job instead of one per block. The same holds when a unit falls
silent during the mode reads: the first timeout there ends the job. Only an
exception response starts the mode backoff, because only that is the HW-4
refusal; a timeout is retried on the next flow job. The setup probe also stops
at the first timeout of a unit.

Code 10/11 for a powered-off unit and busy answers (code 6) are left as they
are until measured; see HW-7 in [HARDWARE_BACKLOG.md](HARDWARE_BACKLOG.md).

The `homeassistant.components.modbus` API is new and still changing
(`get_hub` is already deprecated). Keep the lower bound in the manifest tight
and check the breaking changes of every Home Assistant release.

How the hardware findings are implemented now:

| Finding | Implementation |
|---|---|
| The gateway needs a pause between requests | `REQUEST_GAP_SECONDS` via `set_message_spacing` in `prepare_unit` |
| 0.8 s is enough for the gateway to answer | `FIXED_TIMEOUT` via `require_timeout` in `prepare_unit` |
| pymodbus' default resend turned one unanswered register into about eight timeouts | `TransportPolicy` retries once, without disconnecting |
| Units reject `41120..41124` until a first write (HW-4) | `mode` → `mode_short` → single reads in `MeltemModbusClient._read_mode_block`, backoff per `(slave, component)` on exception responses only |
| `41121`/`41122` come with the mode block | `_read_mode_group` only reads them on their own without a block |
| `41121` holds the sensor-mode selector in sensor modes | `_read_mode_group` derives the target from the measured airflow there |
| `41000` and `41004` are swapped on the gateway | field mapping of `Temperatures` in `device/components.py` |
| 32-bit values are word-swapped | `word_order="little"` on `float32` and `uint32` fields |
| Mode writes only take effect after `41132` | `Command` component, written last in every mode and preset sequence |
| Discovery runs on the gateway's own unit 1 | `MeltemGateway` in `device/device.py` |
| A silent unit must not drag down the others | per-job skip in `MeltemModbusClient._poll` and `_read_mode_component`, quiet window in `TransportPolicy`, `SILENT_ROOM_POLL_SECONDS` in the coordinator |

## Discovery model

There is no true Modbus auto-discovery. The integration performs a best-effort
gateway-backed discovery on the Meltem bridge path.

Current assumptions:

- the gateway exposes its configured unit list on bridge `device_id=1`
- `43901` returns the number of configured units
- `43902..` returns the configured unit addresses
- USB discovery finds the gateway, not the individual ventilation units

The values seen on the tested gateway are in
[MELTEM.md](MELTEM.md#discovery-and-unit-list).

## Polling strategy

The integration uses a serialized scheduler with an adjustable maximum
poll-job start rate.

Current design:

- each read operation is scheduled as one room-scoped job
- jobs use block reads where possible
- only one job runs at a time
- scheduler cadence is derived from `max_requests_per_second`; despite the
  legacy option name, this limits job starts, not individual wire requests
- post-write readbacks count as job starts for that cap, and a job that would
  start too early is skipped and rescheduled
- Home Assistant's `DataUpdateCoordinator` schedules the next refresh from
  `int(loop.time())`, so sub-second intervals would fire up to a second early;
  the coordinator adds the current fractional loop second to compensate
- one job can perform several grouped or optional Modbus reads, each still
  separated by `REQUEST_GAP_SECONDS`
- airflow-level writes rely on the normal scheduler for later readback instead
  of forcing an immediate confirmation poll
- the flow job takes `41121`/`41122` from the `41120` mode block and only
  reads them on their own when the block is unavailable
- a unit that has not answered for `ROOM_SILENT_AFTER_SECONDS` is polled at
  most every `SILENT_ROOM_POLL_SECONDS`, because every unanswered read costs
  timeouts on the shared bus
- every request is retried at most once (see
  [Transport](#transport-home-assistants-shared-modbus-connection)); the
  pymodbus client's default `retries=3` had turned one unanswered register
  into about eight timeouts

Current job groups:

- `flow`: airflow values
- `flow_control`: mode and airflow controls
- `status`: fault, frost, and RF status
- `temperature`: temperature and environment values
- `filter`: filter values
- `hours`: operating hours and software version
- `control_settings`: humidity and CO2 settings

Current target intervals:

- `flow` and `flow_control`: `10s`
- `temperature` and `status`: `60s`
- `filter`, `hours`, and `control_settings`: `1h`

## Read health and write confirmation

Each supported read group tracks its own last attempt, last successful read,
consecutive failures, and most recent error. Optional Modbus failures preserve
the last cached value, but only affect the health of the group that performed
the read. A successful read in another group does not clear that failure.
Expected groups are derived from each room's supported entities, so a register
that a device profile does not expose is not reported as a failed read. The
group-to-entity mapping lives in `const.READ_GROUP_ENTITY_KEYS` and
`RefreshPlan.read_groups()`; the client and the coordinator both use it.

Read-only entities are available only while their own group's last successful
read is fresh. Airflow uses a 30-second freshness limit; other groups use three
times their polling interval. Control entities (fans, selects, the intensive
switch) stay available while the unit answers and report `unknown` values
instead, so a command can still be sent. The `data_health` diagnostic binary
sensor exposes per-group read status and write confirmation details without
issuing extra gateway requests; its attributes are excluded from the recorder
because they change on every poll. The Home Assistant system-health page lists
stale groups per unit as plain text, because its dialog does not render nested
values.

A successful Modbus write call confirms only that the write operation was
accepted at the transport/protocol layer. Control entities show the written
value as pending (`level_source: pending` on the fans) until the associated
group has been read after the write, or until the pending window expires.
Only target registers confirm an airflow write; the measured airflow lags
behind and never does. A matching readback confirms the write; a different
value is reported as a mismatch, and a failed readback remains unconfirmed.
Cached values from before the write cannot confirm it. A failed write drops any
pending value. Failed, mismatched, or unconfirmed writes flag `data_health` for
`WRITE_HEALTH_RETENTION_SECONDS` and then only remain visible as attributes.

Units that answer the two-register mode read but reject the five-register one
(HW-4) do not record an `intensive` read failure; the intensive state is simply
unknown there. An intensive write on such a unit is recorded as `unverifiable`
instead of `unconfirmed`, so it does not flag `data_health`.

The base quick mode is decoded from `41120..41122` only. The intensive override
in `41123`/`41124` no longer masks it, so the quick mode stays correct after a
restart during intensive ventilation.

Write errors reach the UI as translated `HomeAssistantError`s (`exceptions` in
`strings.json`) instead of an "Unknown error".

## Directional fan writes

Both fans write through `coordinator.async_set_direction_level`, which resolves
the opposite direction and writes under one per-unit lock. Two quick or
concurrent commands (for example a scene setting both fans) therefore build on
each other's pending value instead of on the stale cache.

Decision rules, in order:

- opposite direction unknown: a non-zero value is written balanced to both
  directions and reported as `last_write_fallback`; switching a single
  direction off is refused with `opposite_airflow_unknown`
- measured opposite airflow within `LEVEL_CONFIRM_TOLERANCE` of the own
  measured airflow counts as one balanced value, so jitter does not force
  unbalanced operation
- levels at most `BALANCED_LEVEL_TOLERANCE` apart, a stopped unit, or leaving
  sensor control with a non-zero value: balanced write
- otherwise: unbalanced write with the opposite direction kept

`fan.turn_on` without a percentage does nothing on a running fan, because
re-sending the measured level would end a running sensor mode.

## Register and state caveats

### App presets and settings

The quick mode and intensive codes the integration writes were traced from the
Meltem app, see [MELTEM.md](MELTEM.md#reverse-engineered-app-preset-behavior):

- `LOW` / `MED` / `HIGH`: clear `41123`/`41124`, then `41120 = 3`,
  `41121 = 228` / `229` / `230`, then `41132 = 0`
- intensive ventilation: `41123 = 3`, `41124 = 227`, then `41132 = 0`

Decisions:

- `Abluft` / `Zuluft` are not written as app shortcuts. The airflow the app
  stores for them is not readable, so the integration would have to guess it.
  Both states are set by turning one of the two fans to zero, which uses the
  documented unbalanced write sequence. The shortcut encoding
  `200 + airflow / 10` is still decoded on read, so a shortcut started on the
  unit or in the app stays visible.
- Missing keypad LEDs for `Abluft` / `Zuluft` are not an integration defect;
  the Meltem app leaves them dark as well.
- Settings the app stores on the unit (LOW / MED / HIGH airflows, intensive
  airflow and duration, shortcut airflows) persist locally, but no register
  for them has been found. They are not supported; the reverse engineering is
  parked, see [SETTING_RE_BACKLOG.md](SETTING_RE_BACKLOG.md).
- The values in Meltem's Modbus-KNX document describe KNX objects, not the
  holding registers of the USB gateway, and are not used.

### Target readback and measured airflow

`41121` reads back the last written balanced target at once, while
`41020/41021` show the effective airflow and lag behind, see
[MELTEM.md](MELTEM.md#target-readback-and-measured-airflow). Therefore:

- `41121` confirms a newly written balanced target; it is accepted only within
  the raw range `0..200`, because the quick mode codes share the register
- `41020/41021` stay the authoritative current airflow and never confirm a
  write
- if `41121` is missing or implausible, the state falls back to the measured
  airflow
- in unbalanced mode both directions are read back from their own target
  registers and keep their last value when that read fails

### Per-unit availability on read failures

When the gateway is unplugged or otherwise unavailable, the integration keeps
the previous entity state in memory for a short while and logs read failures.
This avoids flapping on transient outages.

Beyond that grace period the entities of an affected unit are marked
unavailable rather than showing frozen values:

- before the first successful state read, room entities are unavailable rather
  than accepting commands built from unknown fallback values
- a unit is considered unavailable after `ROOM_UNAVAILABLE_AFTER_FAILURES`
  consecutive failures, or after `ROOM_SILENT_AFTER_SECONDS` without any
  successful read, even if the gateway itself still answers
- silent units are polled less often so they do not starve the others
- units that still respond keep working independently
- polling backs off progressively while the gateway stays unreachable
- writes fail until communication is restored

### Temperatures are not identical conceptually

Even if values may sometimes look the same on specific models:

- exhaust air temperature
- extract air temperature
- supply air temperature
- outdoor air temperature

represent different airflow positions in the system.

Do not assume identical semantics just because some models expose identical
values in practice. The gateway swaps `41000` and `41004`
([MELTEM.md](MELTEM.md#temperature-register-quirk)); the field mapping of
`Temperatures` in `device/components.py` maps the logical sensors to the
values actually seen.

## Profile detection

The integration can infer sensor capabilities reasonably well:

- humidity present -> `-F` or better
- CO2 present -> `-FC` or better
- VOC present -> `O/VOC-AUL`

Current limitation:

- the integration cannot reliably distinguish `M-WRG-S` from `M-WRG-II`
  automatically

The product registers `40002` / `40011` / `40021`
([MELTEM.md](MELTEM.md#product-registers-and-readable-islands)) are the best
current path to improve this.

That means:

- capability auto-detection is possible
- exact series selection still requires user confirmation

## Why the series is not auto-detected by forcing max airflow

A theoretical idea is:

- drive the fan to maximum
- observe whether it tops out at `97 m3/h` or `100 m3/h`

This is intentionally not implemented automatically.

Reasons:

- it would be intrusive and noisy
- it would visibly change the user's ventilation state during setup
- it is not a safe or polite default behavior

If implemented in the future, it should be:

- opt-in
- clearly labeled
- treated as a diagnostic action

## Model-specific airflow scaling

The UI works in `m³/h`, not in raw Modbus level units.

Current mapping:

- `M-WRG-II`: max airflow `100 m³/h`
- `M-WRG-S`: max airflow `97 m³/h`

Writes convert from UI airflow to raw Modbus values:

- raw register range is `0..200`
- conversion is series-dependent

## Setup flow notes

Important setup behavior:

- the serial port must remain editable even if USB discovery pre-fills it
- users need to select the exact model profile manually
- setup previews are best-effort only
- the setup probe is intentionally minimal and reads product/capability
  registers only

Known UX caveat:

- previews may be missing if the setup probe times out or a register does not
  answer quickly enough

## Existing config entries and profile changes

Setup never probes the gateway for entity metadata:

- each room's entities are the stored keys plus everything its profile implies,
  derived on load, so entries from older releases gain new entities without a
  rewrite
- one-time schema changes run in `async_migrate_entry` (config entry `1.2`
  renamed the airflow-only `airflow_data_stale` entity to `data_health`)
- changing to a profile with fewer capabilities removes registry entities that
  the new profile no longer creates
- globally removed entities from older releases are handled by the same
  platform-aware registry cleanup
- devices of units that a rescan no longer reports are removed on setup; unit
  devices are linked to the gateway device via `via_device_id`

A fresh setup should therefore not be needed for normal profile changes or
upgrades. Remove and re-add the config entry only when its stored data is
actually corrupt or when debugging a setup problem that cannot be reproduced
otherwise. Removing only the gateway device does not reset the config entry.

## USB discovery

The current USB matcher is based on real hardware observations:

- `vid`: `10AC`
- `pid`: `010A`
- `manufacturer`: `Honeywell`
- `description`: `Modbus`

This is good enough for development, but it may still match a generic USB/Modbus
bridge in some environments.

## Important files

The file overview is in `AGENTS.md` under "Important files".

## Tests

The test suite runs against a real Home Assistant test environment provided by
`pytest-homeassistant-custom-component`, which requires Python 3.14. Modbus
traffic runs against `modbus_connection.mock.MockModbusConnection`, an
in-memory register bank, so the request shapes, the retry policy, and the
decoding are tested together. It covers the Modbus layer, the coordinator,
and every entity platform, including:

- request parity with the blocks the pymodbus client read
- the retry policy: one retry, the quiet window before recycling the link,
  gateway errors
- setup and unload through Home Assistant's real `modbus` integration on an
  in-memory link, including the release of the port
- gateway-backed discovery via `43901` / `43902..`
- minimal setup-time capability probing
- balanced airflow derivation and per-profile scaling
- target-level scaling during room-state reads
- `NaN` filtering for float temperature reads
- scheduler job construction and due-job selection
- coordinator write-then-refresh behavior, optimistic state, transport backoff
- setup metadata repair, profile-aware entity cleanup, and serial unload
  synchronization
- control-setting range and manufacturer-step normalization
- config-flow helper functions for defaults and room mapping
- entity creation per model profile across all platforms

See `CONTRIBUTING.md` for environment setup, the test commands, and the
Windows-specific workarounds.

## Suggested debugging workflow

If something breaks:

1. Verify the integration loads at all.
2. Check logs:

```bash
ha core logs | grep meltem_ventilation
```

3. Confirm scan results.
4. Confirm the correct serial port is used.
5. Confirm the correct model profiles were chosen.
6. Only then investigate individual register behavior.

Useful local tools in this repo (async, on `modbus-connection` with tmodbus
since `4.0.0`; any earlier commit has the pymodbus versions):

- `tools/probe_airios_bridge.py`
  confirms whether the bridge path on `device_id=1` responds and returns the
  configured unit list
- `tools/benchmark_gateway.py`
  compares different request patterns, gaps, and block-read candidates against
  a locally attached gateway; the gap is slept outside the measured latency
- `tools/benchmark_integration_like.py`
  drives the integration's own client through scheduler-like loops and write
  experiments
- `tools/raw_requests.py`
  sends single requests to one unit and prints the answer or exception class
  with its latency
- `tools/count_requests.py`
  runs the integration's client with refresh plans and counts the requests per
  unit, including retries

The tools run without Home Assistant and keep their own copy of the link
settings in `tools/_link.py`; keep it in line with `const.py`. Run them as
modules from the repository root, e.g. `python -m tools.raw_requests --help`.

## Things to be careful with in future changes

- Do not remove request pacing unless you have tested the gateway thoroughly.
- Do not make polling user-configurable again without a strong reason.
- Be careful when touching scan timing; it can affect how many devices are found.
- Avoid relying on `41121` as a trustworthy current-state readback.
- Keep `M-WRG-S` and `M-WRG-II` airflow scaling separate.
