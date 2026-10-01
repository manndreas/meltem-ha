# Developer Notes

This document captures the main implementation details, caveats, and lessons
learned while developing the `Meltem M-WRG` Home Assistant integration.

For the manufacturer reference extracted from the original Meltem documents,
see `docs/reference/`. Observed register behaviour and traced app writes are
in `docs/MELTEM.md`.

For the local Modbus-side settings reverse-engineering backlog, see
`docs/SETTING_RE_BACKLOG.md`.

For review findings that need a live gateway before they can be decided, see
`docs/HARDWARE_BACKLOG.md`.

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

Useful helper for local settings work:

- `tools/capture_setting_family.py` captures focused before/after snapshots for
  setting families such as `intensive`, `keypad`, `humidity`, and `co2`

First focused local settings measurement so far:

- changing one app-side intensive ventilation setting caused a broad local
  shadow/meta update in `51100..51113`, `51120..51133`, `51150..51151`, and
  `52006..52010`
- the known runtime write/readback registers `41120..41124` and `41132` still
  did not reveal a direct decoded configuration value for that app change
- current working interpretation: the setting is very likely persisted locally,
  but the observable Modbus effect looks more like a family-level commit or
  payload version change than a simple one-register plain value
- a second immediate before/after test for intensive airflow reproduced the
  same pattern and additionally bumped `52000..52005`, which reinforces the
  commit-sequence hypothesis rather than a direct plain-value mapping
- a first keypad-default experiment was more promising: changing the LOW
  airflow target left `51120..51133`, `51150..51151`, and `52010` at a new
  stable value (`69 -> 71`) while the broader readable islands stayed unchanged
- `52009` drifted back on an immediate no-change recapture, so it looks more
  like family metadata; the stable `51120..`/`51150..`/`52010` block is the
  better candidate for a persisted preset/default payload slot
- a second LOW-default change weakened that interpretation again: the same
  block moved from `71 -> 73` and then `73 -> 74` on an immediate no-change
  recapture while the readable `40000..42009` islands still did not reveal a
  direct preset target value
- current working interpretation for keypad defaults: the `51120..`/`51150..`
  / `52010` block is still family-relevant, but it behaves more like commit or
  payload-sequence metadata than a stable decoded LOW/MED/HIGH airflow value
- current project decision: deeper reverse engineering of app-side persisted
  settings is parked for now and should only be revisited if a new register
  family, payload hint, or external documentation changes the picture

Supported series/profiles:

- `M-WRG-S`
- `M-WRG-S (-F)`
- `M-WRG-S (-FC)`
- `M-WRG-II`
- `M-WRG-II (-F)`
- `M-WRG-II (-FC)`
- `M-WRG-II (O/VOC-AUL)`

## Gateway and Modbus behavior

The gateway is slow and should be treated conservatively.

Important observations:

- Requests sent too quickly can cause partial reads, stale data, or complete
  temporary loss of values.
- Setup scans are sensitive to aggressive timeouts.
- The gateway appears to answer sequentially, similar to how the Meltem app
  fetches values "one after another".

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

Open until measured on the gateway (HW-7):

- If the gateway answers code 10/11 for a powered-off unit instead of timing
  out, the per-job skip does not apply, because `Device.async_poll` only
  raises timeouts. In the in-memory gateway such a unit costs 28 requests for
  a full read and 8 for a flow job, each retried once, and the mode reads go
  into backoff.
- tmodbus retries `SERVER_DEVICE_BUSY` (code 6) internally for up to 60 s with
  growing waits. The link and `_gateway_lock` stay held that long, so writes
  from the UI wait too. `modbus-connection` does not make this configurable.

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

Confirmed local observation:

- on the tested Meltem gateway, `43901 -> 6`
- `43902..43917 -> [3, 2, 4, 5, 7, 6, 0, ...]`

## Product and diagnostic registers

The tested `M-WRG-II` units exposed stable generic product registers.

Confirmed local reads on all six units:

- `40002 PRODUCT_ID -> 116852 (0x0001C874)`
- `40011 PRODUCT_NAME -> VMD-22RPS44`
- `40021 RECEIVED_PRODUCT_ID -> 116852 (0x0001C874)`

These registers look like the most promising basis for future model/profile
auto-detection.

Additional diagnostic registers that returned stable values:

- `40004 SOFTWARE_VERSION`
- `40101 RF_COMM_STATUS`
- `40103 FAULT_STATUS`
- `40104 VALUE_ERROR_STATUS`

Observed software versions on the tested setup:

- units `1, 2, 3, 4, 6`: `2326`
- unit `5`: `2584`

The different software version on unit `5` matches the user's note that this
unit likely had a replacement board.

Additional holding-register sweep on `2026-03-31` for unit `slave 2`:

- coarse scan over `40000..49999` with `10`-register windows only found
  readable windows in these areas:
  - `40000..40019`
  - `40200..40209`
  - `41000..41029`
  - `41100..41109`
- single-register follow-up refined that result to these readable islands:
  - `40000..40022`, `40024..40025`
  - `40200..40209`
  - `41000..41029`
  - `41100..41113`
  - `42000..42009`
- `43900..43905` remained unreadable on the unit itself, which is consistent
  with `43901` / `43902..` being a gateway-side discovery path on `slave 1`

Registers probed locally but not available on the tested setup:

- `41041 FILTER_DURATION`
- `41042 FILTER_REMAINING_PERCENT`
- `41043 FAN_RPM_EXHAUST`
- `41044 FAN_RPM_SUPPLY`
- `41050 BYPASS_MODE`
- `41051 BYPASS_STATUS`

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

Local benchmark results on the tested gateway so far:

- airflow block read `41020..41021`: stable
- status block read `41016..41018`: stable
- plain temperature mixed block `41002..41005` plus separate `41009`: stable

Confirmed local live test:

- a direct write on unit address `4` changed the airflow from `60 -> 65 m³/h`
- the gateway exposed the new airflow in `41020..41021` after roughly `4s`
- after restoring the unit back to `60 m³/h`, the same registers returned to
  the original value after a few seconds
- the same behavior was observed while polling all six configured units in one
  loop

## Register and state caveats

### App presets are only partially mappable with published Modbus docs

The vendor app clearly exposes additional user-facing presets and settings
such as:

- `LOW`, `MED`, `HIGH`
- temporary intensive ventilation airflow and runtime
- app-configurable supply-only / extract-only defaults

However, the published Modbus manuals currently used by this repository only
document writable configuration registers up to `42009` plus the runtime write
sequence `41120` / `41121` / `41122` / `41132`.

Current interpretation:

- the app uses extra preset encodings beyond the published `0..200` airflow
  scaling path
- plain airflow writes should not be assumed to update the same internal
  keypad / LED state as the Meltem app or local folientastatur
- claiming support for the app's preset configuration registers is not yet
  justified

Additional vendor context from a separate Meltem Modbus-KNX document:

- the KNX object model also separates
  - normal ventilation level
  - unbalanced supply/extract operation
  - automatic mode
  - humidity mode
  - CO2 mode
  - intensive ventilation
- that supports the architectural assumption that these are distinct control
  concepts in Meltem's product logic rather than one flat shared mode list
- however, the KNX values from that document must not be mapped directly onto
  the USB gateway's holding registers without independent proof

Confirmed local write tracing on `2026-03-30` for one `M-WRG-II` plain unit:

- `LOW` -> `41120 = 3`, `41121 = 228`, then `41132 = 0`
- `MED` -> `41120 = 3`, `41121 = 229`, then `41132 = 0`
- `HIGH` -> `41120 = 3`, `41121 = 230`, then `41132 = 0`
- temporary intensive ventilation -> `41123 = 3`, `41124 = 227`, then
  `41132 = 0`
- app `Abluft` / `Zuluft` shortcuts use `41120 = 4` and encode the configured
  airflow on the active side as `200 + airflow_in_m3h / 10`

Focused local setting-diff runs on `2026-03-31` for intensive airflow defaults
did confirm that app-side changes leave local Modbus-visible traces, but only
as family-level jumps in `511xx` / `520xx` shadow and meta words. A direct
plain-value readback did not appear in `41120..41124`, `41132`, or
`42000..42009`, and an immediate `40 -> 60 -> 40` A-B-A sequence still only
produced monotonic version/commit-like changes. Until a new register family or
payload decoding hint appears, deeper local support for intensive default
settings should be treated as unconfirmed.

A separate control write on `2026-04-01` used one documented local config
register directly: `42000` (`humidity starting point`) was changed on
`slave 2` from `70 -> 71` and then restored to `70`. That value echoed back
exactly in `42000`, while the snapshot diff only showed a small side effect in
`52008..52009` on the forward step. This is a useful reference pattern: known
local config writes look like direct plain-value changes, unlike the intensive
default experiments that only moved broad `511xx` / `520xx` meta blocks.

Examples:

- `Abluft 30` -> `41122 = 203`
- `Abluft 50` -> `41122 = 205`
- `Zuluft 50` -> `41121 = 205`

On a later hardware check on `2026-03-31`, the same runtime write family
successfully changed airflow on another tested unit (`slave 2`), but did not
reproduce the physical keypad LEDs for `Abluft` / `Zuluft`.

Observed raw Modbus writes and outcomes on `slave 2`:

- `Abluft 10` written as `41120 = 4`, `41121 = 0`, `41122 = 201`,
  then `41132 = 0`
  - runtime readback stayed stable at `41120..41124 = [4, 0, 201, 0, 0]`
  - the unit accepted the airflow change path
  - the physical `Abluft` LED on the keypad did not light
- `Zuluft 10` written as `41120 = 4`, `41121 = 201`, `41122 = 0`,
  then `41132 = 0`
  - runtime readback stayed stable at `41120..41124 = [4, 201, 0, 0, 0]`
  - the physical keypad LEDs remained off
- `Abluft 50` written as `41122 = 205`
  - runtime readback stayed stable at `41120..41124 = [4, 0, 205, 0, 0]`
  - airflow eventually converged to `50/0`
  - the physical `Abluft` LED still did not light

Current interpretation:

- the reverse-engineered `200 + airflow_in_m3h / 10` encoding is still valid
  as a runtime airflow control path
- however, on at least one tested unit it is not sufficient on its own to
  reproduce the keypad LED semantics for `Abluft` / `Zuluft`
- the integration should therefore treat these writes as high-confidence
  runtime airflow shortcuts, not yet as a complete reproduction of the local
  panel state machine
- later user verification on the same setup showed that the official Meltem
  app also does not light the physical keypad LEDs when switching to
  `Abluft` / `Zuluft`
- this strongly suggests that missing LEDs for these two shortcuts may be
  normal device behavior rather than a Home Assistant integration defect

Additional local negative findings from the same reverse-engineering session:

- changing app-side configuration values such as intensive airflow and then
  leaving the settings page via `Zurueck` produced no visible register changes
  in these searched ranges
  - unit `slave 5`: `42000..42560`
  - gateway `slave 1`: `41980..42540`
- that suggests these deeper app settings are either
  - stored outside the tested holding-register windows
  - not written immediately
  - or not exposed through the same direct Modbus path that runtime controls
    use

Additional system-level observations from the same setup:

- the vendor app did not function without Internet connectivity
- the gateway also appeared cloud-dependent in normal operation

Current working hypothesis:

- the Meltem app likely sends these deeper configuration changes to Meltem
  backend services first
- the gateway then synchronizes at least part of that configuration from the
  cloud instead of exposing a simple local Modbus write path for every app
  setting

Important caveat:

- this does not mean the values are "cloud only" at runtime
- the user observed that changed keypad shortcut values remain usable from the
  local folientastatur later even when the cloud path is unavailable
- therefore the effective shortcut and intensive defaults must still end up
  stored somewhere locally in the gateway, in the unit, or in another
  internal memory path that was not visible in our tested holding-register
  windows
- the unresolved question is not whether local persistence exists, but through
  which local interface or register space it is exposed

Confirmed local persistence checks on `2026-03-30`:

- after changing `Bedienfolie LOW` to `60 m3/h` in the app and then removing
  cloud access, pressing `LOW` on the physical keypad still drove the unit at
  `60/60 m3/h`
- the same offline check still showed the app-style preset code path
  `41120/41121/41122 = [3, 228, 0]`
- after changing temporary intensive airflow to `90 m3/h` in the app and then
  removing cloud access, activating intensive mode still drove the unit at
  `90/90 m3/h`

These checks confirm that at least some app-configured shortcut values are
persisted locally, even though their storage writes were not visible in the
tested holding-register windows.

Additional local reverse-engineering findings on `2026-03-30`:

- single-register scans uncovered additional readable shadow ranges on
  `slave 5` that broad block reads had hidden because mixed valid/invalid
  windows failed as a whole
- confirmed readable shadow/meta ranges:
  - `51100..51113`
  - `51120..51133`
  - `51150..51151`
  - `52000..52010`
- these ranges do not expose the human-readable configured values directly;
  instead they behaved like local meta/state/commit words

Observed diff patterns:

- changing `Intensivluftung` airflow changed `51100..51112`
- changing `Intensivluftung` run-on time produced a broad `+1` increment
  across `51100..51113`, `51120..51132`, `51133`, `51150..51151`,
  and `52000..52010`
- changing `Bedienfolie LOW` produced the same broad `+1` style increment
  pattern
- changing `Bedienfolie HIGH` also produced the same broad `+1` style
  increment pattern
- one tested `Bedienfolie MED` change produced no diff in these shadow ranges

Current interpretation:

- these `51xxx` / `52xxx` registers are likely not the configured airflow or
  runtime values themselves
- they look more like local change counters, commit markers, version words,
  or status bitfields associated with persisted configuration
- the actual stored shortcut/configuration payload still has not been
  identified

Additional broader capture on `2026-03-31` using the combined known ranges
(`41120..41124`, `41132`, `42000..42009`, `51100..51113`, `51120..51133`,
`51150..51151`, `52000..52010`) for `slave 2`:

- changing intensive airflow from `40` to `60` still produced no direct
  readback in `411xx` or `420xx`
- `51100..51112` bumped together from `4352` to `4353`
- `51113`, `51120..51133`, and `51150..51151` bumped from `11` to `12`
- `52008..52010` bumped from `11` to `12`
- `52007` changed from `11` to `4352`, which again looks like a meta/payload
  marker rather than a decoded target airflow

This broader capture strengthens the current assumption that the relevant app
settings are persisted locally, but not as one plain writable/readable holding
register in the currently known windows.

Practical implication:

- treat keypad/runtime presets (`LOW` / `MED` / `HIGH` / `Intensiv`,
  `Abluft`, `Zuluft`) as the current high-confidence reverse-engineered
  surface
- document deeper app settings first unless a concrete register mapping has
  been reproduced locally
- for Home Assistant writes, `Abluft` / `Zuluft` are no longer written as
  app-style shortcuts at all: since the dedicated app shortcut storage is
  still unknown, the integration would have had to guess the airflow. Both
  states are now expressed by setting one of the two fan entities to zero,
  which uses the documented unbalanced write sequence. The observed
  `200 + airflow / 10` encoding is still decoded on read so a shortcut
  activated by the device or app remains visible.

Additional panel-side hardware findings on `2026-03-31` for `slave 2`:

- switching the local panel to `Abluft` changed runtime airflow, but did not
  change `41120..41124`
- observed diff for `neutral -> Abluft`:
  - `41020: 20 -> 40`
  - `41021: 20 -> 28`
  - `51120..51133`: broad `+1` increment pattern
  - `51150..51151`: `27 -> 28`
  - `52007: 27 -> 4352`
  - `52008..52010`: `27 -> 28`
- switching the local panel to `Zuluft` also did not change `41120..41124`
- observed diff for `neutral -> Zuluft`:
  - `41020: 10 -> 0`
  - `51113: 4353 -> 4352`
  - `52008: 4352 -> 5376`
  - `52009: 31 -> 1055`

Implication of the panel diffs:

- local `Abluft` / `Zuluft` keypresses on this unit are not represented only
  by the direct runtime write block `41120..41124`
- the physical panel appears to drive an additional local shadow/commit state
  machine in the `51xxx` / `52xxx` ranges
- this strongly explains why raw runtime writes can reproduce the functional
  airflow change while still missing the keypad LED state
- however, because the user later confirmed that the vendor app also leaves
  those LEDs dark for `Abluft` / `Zuluft`, the missing LED state should no
  longer be treated as clear evidence of an incomplete HA write sequence

### Research plan for unresolved app settings

The deeper settings pages in the Meltem app remain unresolved, especially for:

- intensive airflow and run-on time
- keypad configuration values
- standby settings
- acoustic signals
- VOC / CO2 special configuration pages

Current status:

- no visible writes were found in the tested holding-register windows on the
  unit slave or the gateway slave during single-setting changes
- that makes blind linear scanning increasingly expensive
- the current evidence now points more strongly toward a cloud-facing config
  workflow plus a separate local persistence layer

Recommended next steps for future sessions:

1. Prefer before/after snapshot diffs over live watching.
   Use `tools/diff_register_snapshot.py` with one setting change per run.

2. Keep the test action minimal and stable.
   For each run:
   - capture baseline
   - change exactly one app setting
   - leave the page in a known way such as `Zurueck`
   - capture diff

3. Search one hypothesis at a time.
   Suggested order:
   - higher holding-register windows on gateway `slave 1`
   - higher holding-register windows on the concrete unit slave
   - alternate trigger moments: save, back navigation, activating the related
     runtime mode, app restart

4. Do not mix multiple setting changes in one trace.
   Single-setting traces are much easier to reason about later.

5. Record negative findings explicitly.
   Each excluded range should be written down in this document so the same
   search space is not repeated later.

When to stop scanning and reassess:

- after several consecutive wide no-hit ranges on both gateway and unit
  levels, treat it as a sign that the storage path may not be exposed through
  the same direct holding-register surface
- at that point, prioritize consolidating confirmed runtime behavior and look
  for new external evidence before continuing with more wide scans

### `REGISTER_CURRENT_LEVEL` is a useful target readback, not a current-airflow readback

The vendor documentation describes `41121` as part of the write sequence in
section `16.7`, not as a normal read register. On the tested gateway, however,
reading it after a balanced write returned the last written raw target value
consistently.

Confirmed local behavior:

- baseline `60 m3/h` corresponded to `41121 = 120`
- writing `64 m3/h` changed `41121` to `128` immediately
- writing `10 m3/h` changed `41121` accordingly as well
- restoring the target changed `41121` back immediately

Interpretation:

- `41121` is useful as a fast confirmation of the requested balanced target
- it should not be treated as the authoritative current airflow
- if `41121` is missing or implausible, the integration should still fall back
  to airflow-derived state

Older observations of implausible values such as `230` are still relevant, so
the integration only accepts `41121` when it looks like a valid raw level in
the documented `0..200` range.

Additional local finding:

- on several units, `41121` returned a Modbus exception until the first write
  was sent to that unit
- after a write, the same register became readable and reflected the latest
  written raw balanced target consistently

### Airflow is the most trustworthy "current state"

For current runtime state, these registers are the most useful:

- `41020` extract airflow
- `41021` supply airflow

Confirmed local behavior after write testing:

- `41020/41021` did not always change immediately after small balanced writes
- a larger change such as `60 -> 10 m3/h` became visible clearly on
  `41020/41021`
- that suggests these registers behave like the effective/actual airflow, not
  the fast target confirmation path

Current approach:

- use `41121` to confirm a newly written balanced target when available
- keep `41020/41021` as the authoritative current/actual airflow view
- if supply and extract airflow diverge, the unit is driven in unbalanced mode
  and both directions are read back from their own target registers

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

### Retry strategy

Retries should be reserved for errors that look transient:

- serial-port lock conflicts
- temporary transport or timeout failures
- reconnectable I/O errors

Retries should not be used aggressively for normal Modbus exception responses:

- they often just mean "register unsupported in the current context"
- they add bus load without improving the result

Current implementation direction:

- retry timeouts, garbled frames, and a lost link once; never retry a plain
  Modbus exception response
- reopen the link only after several timeouts in a row with no answer in
  between, so one silent unit does not reset the link for the others
- do not force immediate readback confirmation after normal airflow writes
- the setup probes use the same retry rules as the runtime client

### Temperatures are not identical conceptually

Even if values may sometimes look the same on specific models:

- exhaust air temperature
- extract air temperature
- supply air temperature
- outdoor air temperature

represent different airflow positions in the system.

Do not assume identical semantics just because some models expose identical
values in practice.

### Confirmed special case: registers `41000` and `41004`

- on the tested `M-WRG-GW` gateway, the documented exhaust/extract
  temperature registers behaved reversed
- in practice, the gateway exposed the expected exhaust-air temperature on
  `41004`
- correspondingly, `41000` behaved like the extract-air temperature register
- the integration maps the logical sensor names to the values actually seen on
  the gateway
- this should be treated as a gateway-specific quirk, not as a correction of
  the unit manual itself

## Profile detection

The integration can infer sensor capabilities reasonably well:

- humidity present -> `-F` or better
- CO2 present -> `-FC` or better
- VOC present -> `O/VOC-AUL`

Current limitation:

- the integration cannot reliably distinguish `M-WRG-S` from `M-WRG-II`
  automatically

The generic product registers above are the best current path to improve this.

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

- `custom_components/meltem_ventilation/config_flow.py`
  setup, USB discovery, gateway-backed unit discovery, profile selection
- `custom_components/meltem_ventilation/modbus_client.py`
  async room reads, mode decoding, and write sequences
- `custom_components/meltem_ventilation/modbus_helpers.py`
  link parameters, unit preparation, discovery, and setup probes
- `custom_components/meltem_ventilation/device/components.py`
  one `Component` per register block, pinned to the established requests
- `custom_components/meltem_ventilation/device/device.py`
  room, probe, and gateway devices built from those components
- `custom_components/meltem_ventilation/device/transport.py`
  retry policy shared by all units on the gateway link
- `custom_components/meltem_ventilation/coordinator.py`
  rotating refresh plan, optimistic state, write orchestration
- `custom_components/meltem_ventilation/const.py`
  timing, profile metadata, register constants, entity-key groupings
- `custom_components/meltem_ventilation/fan.py`
  the two directional airflow controls, which are the primary control path
- `custom_components/meltem_ventilation/number.py`
  per-series control settings such as humidity and CO2 thresholds

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

Run the whole suite with:

```bash
pytest
```

Focused runs are usually enough while iterating:

```bash
pytest tests/test_modbus_client.py
pytest tests/test_config_flow.py
pytest tests/test_entity_descriptions.py
```

See `CONTRIBUTING.md` for environment setup, including the Windows-specific
workarounds.

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

## Things to be careful with in future changes

- Do not remove request pacing unless you have tested the gateway thoroughly.
- Do not make polling user-configurable again without a strong reason.
- Be careful when touching scan timing; it can affect how many devices are found.
- Avoid relying on `41121` as a trustworthy current-state readback.
- Keep `M-WRG-S` and `M-WRG-II` airflow scaling separate.
