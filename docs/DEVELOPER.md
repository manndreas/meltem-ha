# Developer Notes

This document captures the main implementation details, caveats, and lessons
learned while developing the `Meltem M-WRG` Home Assistant integration.

For the consolidated manufacturer reference we transcribed into Markdown, see
`docs/MELTEM.md`.

For the separate mobile app cloud API reverse-engineering notes, see
`docs/CLOUD_API.md`.

For the local Modbus-side settings reverse-engineering backlog, see
`docs/SETTING_RE_BACKLOG.md`.

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
  removed for slider writes
- cached values remain visible in Home Assistant when the gateway disappears
  because failed polls preserve the previous state instead of clearing entities

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
- Retry transient read failures with a reconnect-based second attempt.
- Serialize all periodic reads and writes through one shared gateway lock.
- Use one scheduled read job at a time.
- Prefer grouped block reads over single-register reads.

## Pymodbus quirk in Home Assistant

In the Home Assistant environment used for this integration, `pymodbus`
expects `device_id=...`, not `slave=...`.

This is important for:

- discovery scan reads
- regular reads
- register writes

If `slave=` is used, Home Assistant may fail with errors such as:

- `TypeError(... got an unexpected keyword argument 'slave')`

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

The integration uses a serialized scheduler with an adjustable maximum request
rate.

Current design:

- each read operation is scheduled as one room-scoped job
- jobs use block reads where possible
- only one job runs at a time
- scheduler cadence is derived from `max_requests_per_second`
- slider writes are optimistic and rely on the normal scheduler for later
  convergence instead of forcing an immediate confirmation poll

Current job groups:

- airflow
- temperatures
- status
- filter data
- operating hours

Current target intervals:

- airflow: `10s`
- temperatures: `60s`
- status: `60s`
- filter data: `1h`
- operating hours: `1h`

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
- for Home Assistant writes, `Abluft` / `Zuluft` can already use the observed
  app-style `200 + airflow / 10` encoding, but the integration currently
  chooses that airflow from the room's live known target/flow because the
  dedicated app shortcut storage is still unknown

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
- if supply and extract airflow diverge, the single balanced value becomes
  unavailable by design

### Cached values after disconnect are expected with the current strategy

When the gateway is unplugged or otherwise unavailable, the integration keeps
the previous entity state in memory and logs read failures. This is intentional
for now:

- running entities do not disappear immediately on transient gateway outages
- the UI continues to show the last known values
- writes fail until communication is restored

If desired later, this behavior could be changed to mark entities unavailable
after a configurable number of consecutive failures.

### Retry strategy

Retries should be reserved for errors that look transient:

- serial-port lock conflicts
- temporary transport or timeout failures
- reconnectable I/O errors

Retries should not be used aggressively for normal Modbus exception responses:

- they often just mean "register unsupported in the current context"
- they add bus load without improving the result

Current implementation direction:

- retry lock/transport errors
- avoid repeated retries on plain `ExceptionResponse(...)`
- do not force immediate readback confirmation after normal slider writes

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

## Existing config entries vs clean setup

For major model/profile logic changes, a fully fresh setup is often more
reliable than trying to migrate partial intermediate states.

Recommended clean reset:

1. Remove the entire integration config entry in Home Assistant.
2. Restart Home Assistant.
3. Re-add the integration.

Removing only the gateway device is not enough.

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
- `tests/test_modbus_client.py`

## Tests

The current test coverage starts with pure-Python unit tests for the Modbus
helper layer plus scheduler/config helper tests. These tests intentionally avoid
requiring a full Home Assistant test environment and currently cover:

- gateway-backed discovery via `43901` / `43902..`
- minimal setup-time capability probing
- balanced airflow derivation
- target-level scaling during room-state reads
- `NaN` filtering for float temperature reads
- scheduler job construction and due-job selection
- coordinator passthrough methods for discovery/probing
- coordinator write-then-refresh behavior
- config-flow helper functions for defaults and room mapping

Run them with:

```bash
python3 -m unittest -v \
  tests/test_modbus_client.py \
  tests/test_coordinator_and_config_helpers.py
```
  setup, USB discovery, gateway-backed unit discovery, profile selection
- `custom_components/meltem_ventilation/modbus_client.py`
  low-level Modbus reads/writes and timing behavior
- `custom_components/meltem_ventilation/coordinator.py`
  rotating refresh plan
- `custom_components/meltem_ventilation/const.py`
  timing, profile metadata, register constants
- `custom_components/meltem_ventilation/number.py`
  airflow controls and per-series scaling

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

Useful local tools in this repo:

- `tools/probe_airios_bridge.py`
  confirms whether the bridge path on `device_id=1` responds and returns the
  configured unit list
- `tools/benchmark_gateway.py`
  compares different request patterns, gaps, and block-read candidates against
  a locally attached gateway

## Things to be careful with in future changes

- Do not remove request pacing unless you have tested the gateway thoroughly.
- Do not make polling user-configurable again without a strong reason.
- Be careful when touching scan timing; it can affect how many devices are found.
- Avoid relying on `41121` as a trustworthy current-state readback.
- Keep `M-WRG-S` and `M-WRG-II` airflow scaling separate.
