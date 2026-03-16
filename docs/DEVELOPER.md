# Developer Notes

This document captures the main implementation details, caveats, and lessons
learned while developing the `Meltem M-WRG` Home Assistant integration.

For the consolidated manufacturer reference we transcribed into Markdown, see
`docs/MELTEM.md`.

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

The integration currently targets Meltem ventilation units behind an
`M-WRG-GW` gateway over Modbus RTU via USB.

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
