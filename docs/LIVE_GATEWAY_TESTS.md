# Live gateway test plan

Tests an AI agent can run on its own against a live `M-WRG-GW` gateway connected
over USB to the development machine. Command examples use Windows PowerShell;
the Python tools also run on Linux/POSIX from the repository root with
`.venv/bin/python` and a port such as `/dev/ttyACM0` or `/dev/serial/by-id/...`.
Adapt only the shell-specific loops, paths, and process-control commands. The
plan checks the hardware assumptions the integration relies on and works through
the open items in [HARDWARE_BACKLOG.md](HARDWARE_BACKLOG.md) (HW-1 to HW-7),
[SETTING_RE_BACKLOG.md](SETTING_RE_BACKLOG.md), and [TODO.md](TODO.md). HW-7
is the [post-release validation checklist](#hw-7--post-release-tmodbus-validation)
for the tmodbus transport introduced in `4.0.0`.

Tests that need a person (app, power switch, USB cable, keypad) are listed
separately in [H](#h--tests-that-need-a-person). The agent prepares them,
asks for the one physical step, and measures.

## How to use this plan

1. Work through the phases in order: [P](#p--preparation) →
   [G](#g--gateway-and-link-read-only) → [R](#r--register-map-and-decoding-read-only) →
   [T](#t--transport-and-timing) → [W](#w--writes-on-a-released-test-unit) →
   [H](#h--tests-that-need-a-person). Do not start W before G and R have passed.
2. Every test names the assumption it checks and its source, its type, the
   commands, a pass criterion, and where the result goes.
3. Save raw output under `tmp/live-tests/<date>/` (ignored by git) and keep the
   summary described in [Recording results](#recording-results).
4. Do not change code or constants during a run, and do not commit or push.
   Report the findings and the candidate solution they support; the decision
   is made afterwards.

| Type | Meaning | Runs without a person |
| --- | --- | --- |
| `R` | read-only | yes |
| `W` | writes runtime or configuration registers on a released unit and restores them | yes, once released in [P-4](#p-4--ask-the-person-once) |
| `S` | long run (hours) in the background | yes |
| `H` | needs a person for one physical or app step | the agent prepares, asks, and measures |

## Safety rules

### Hard limits

- **One process on the port.** Stop Home Assistant and every other tool that
  uses the gateway. The port is opened exclusively, and the tools in `tools/`
  cannot run at the same time; run them one after another.
- **Never write** `30000` (baud rate) or `30002` (slave address): a wrong value
  makes a unit unreachable on the bus. Never write `40xxx`, `41000..41099`,
  `42006..42009`, `43xxx`, the shadow ranges `511xx` / `520xx`, or anything on
  the gateway itself (slave `1`).
- **Allowed writes**, only on slaves released in [P-4](#p-4--ask-the-person-once):
  - `41120`, `41121`, `41122`, `41123`, `41124`, `41132` with values from the
    documented tables in [reference/modbus.md](reference/modbus.md#setting-the-ventilation-level)
    or the traced values in [MELTEM.md](MELTEM.md#reverse-engineered-app-preset-behavior)
  - `42000..42005` within the documented limits; [W-15](#w-15--control-settings)
    names the one exception
- **Mode writes end with `41132 = 0`.** Only [W-6](#w-6--41132-commit-latch)
  delays it on purpose, and still writes it at the end.
- **Function codes:** `0x03`, `0x04`, `0x06`. For [G-6](#g-6--other-function-codes)
  also `0x08` with sub-function `0` (return query data), `0x11` (report server
  ID) and `0x2B/0x0E` (read device identification). Never any other `0x08`
  sub-function: `1` restarts the communication, `4` forces listen-only mode and
  silences the unit until a power cycle. Never `0x10`; the manuals do not list
  it.
- **Pacing:** request gap at least `0.1 s`, except the sweep in
  [T-3](#t-3--request-gap-sweep). Never use the broadcast address `0`.
- **Write pacing:** at most one write sequence per unit every 10 s. Nonzero
  airflow targets stay between 10 m³/h and the profile maximum. Zero is only
  used where a documented off or one-sided mode explicitly requires it; do not
  use zero as a balanced running target. A unit stays off for at most 5 minutes.

### Before and after every W test

1. Snapshot:
   `& $py -m tools.capture_setting_family --port $port --slave $s --family all_known --label before-<ID> --output-dir $out\captures`
2. Run the test.
3. Restore the state from the snapshot, see [Appendix A](#appendix-a--restoring-a-unit).
4. Check:
   `& $py -m tools.capture_setting_family --port $port --slave $s --family all_known --label after-<ID> --output-dir $out\captures --compare-latest`.
   Expected differences: counters in `511xx` / `520xx` (see
   [R-11](#r-11--shadow-ranges-and-their-noise)) and `41020/41021` while the
   fans still settle. Recheck `41020/41021` after 2 minutes.

The integration-like write benchmarks attempt an airflow restore even after
an error or cancellation, and print restore errors separately. This cannot
restore an unreachable unit and does not restore its complete mode or preset
state. Always carry out the full snapshot restore and verification above.

### Abort criteria

Stop all writes, restore every touched unit, and report when:

- `41016` (error message) turns `1` on any unit
- the gateway does not answer `43901` for 60 s
- a write answers with an exception the test does not expect
- a unit is not back at its restored airflow after 2 minutes
- the person reports unexpected noise or behavior

## P — Preparation

### P-1 — Find the serial port

On Windows:
```powershell
Get-CimInstance Win32_PnPEntity |
    Where-Object { $_.PNPDeviceID -match 'VID_10AC&PID_010A' } |
    Select-Object Name, Manufacturer, PNPDeviceID
```

The `Name` ends in `(COMx)`. On Linux, run:

```bash
python3.14 -m serial.tools.list_ports -v
```

The Meltem adapter matches VID `10AC` / PID `010A`; use its `/dev/ttyACM*` or
`/dev/serial/by-id/...` path. If no matching port appears, ask the person.

### P-2 — Python environment

The test venv from [requirements-test.txt](../requirements-test.txt) has
everything needed, including `pymodbus` and `pyserial` for the baseline tools.

```powershell
$py   = "$env:TEMP\meltem-venv-2609\Scripts\python.exe"   # or any venv with requirements-test.txt
$port = "COM5"                                             # from P-1
$out  = Join-Path (Get-Location) "tmp\live-tests\$(Get-Date -Format yyyy-MM-dd)"
New-Item -ItemType Directory -Force "$out\captures", "$out\scripts" | Out-Null
$env:PYTHONPATH = (Get-Location).Path                      # lets scripts import tools.*
& $py -m pip show modbus-connection tmodbus pymodbus pyserial | Select-String '^(Name|Version)'
```

Run every command from the repository root. Pipe tool output into the results
folder, for example `... | Tee-Object "$out\G-2.txt"`. Start runs that take
longer than a few minutes in the background and redirect their output to a
file.

Linux setup equivalent:

```bash
python3.14 -m venv .venv
.venv/bin/python -m pip install -r requirements-test.txt
export PYTHONPATH="$PWD"
port=/dev/ttyACM0
out="tmp/live-tests/$(date +%F)"
mkdir -p "$out/captures" "$out/scripts"
```

Then invoke the same modules as `.venv/bin/python -m tools.<name> --port "$port"`.

### P-3 — Port is free and the gateway answers

```powershell
& $py -m tools.probe_airios_bridge --port $port | Tee-Object "$out\P-3.txt"
```

Pass: exit code `0`, `43901` and `43902..` answer. Exit code `2` means the port
is held by another process or missing.

### P-4 — Ask the person once

Ask in one round, then work without further questions until the H tests:

1. Which slave addresses may be written (W tests)? Default: none, skip W.
2. Is a person available now for H tests, and for which ones?
3. May a 24-hour run ([T-10](#t-10--24-hour-soak)) hold the port?
4. Model and variant of each unit from the type plate (`M-WRG-II` or `M-WRG-S`,
   `-F`, `-FC`, `O/VOC-AUL`), if known.
5. Which rooms are occupied, and at which times must the units not change?

### P-5 — Baseline worktree

The baseline measurement ([T-1](#t-1--baseline-with-the-pymodbus-tools)) must
use the pymodbus tools from before `4.0.0`. Commit `4009a32` is the last
commit with them; use the commit hash rather than the moving `main` branch.

```powershell
git worktree add --detach ..\meltem-ha-baseline 4009a32
```

Use the launcher from the current repository to load Home Assistant modules
when available (otherwise it installs a minimal benchmark-only stub) and adapt
the baseline runner to the installed pymodbus unit-keyword API:

```powershell
Push-Location ..\meltem-ha-baseline
& $py ..\meltem-ha\tools\run_pymodbus_baseline.py --help
Pop-Location
```

The launcher reports the pymodbus version and normalizes `slave` / `device_id`
for the pinned runner without editing the baseline worktree. Offline tests
cover both API signatures and fail explicitly on an unsupported signature.
The Home Assistant stub only supplies `Platform` and `utcnow` for the
integration modules loaded by this benchmark; it does not run or emulate
Home Assistant.
The 2026-10-09 attempt invoked the old runner directly with pymodbus `3.13.1`;
its compatibility shim passed an unsupported `slave` keyword during
discovery, so it sent no poll requests. That attempt is invalid; rerun it via
the launcher before recording a baseline result.

Remove the worktree when the run is finished: `git worktree remove ..\meltem-ha-baseline`.

### P-6 — Results summary

Create `$out\SUMMARY.md` with the table from
[Recording results](#recording-results) and fill it in after each test.

## G — Gateway and link (read-only)

### G-1 — USB identity

- **Checks:** USB matcher in [manifest.json](../custom_components/meltem_ventilation/manifest.json):
  vid `10AC`, pid `010A`, manufacturer `*honeywell*`, description `*modbus*`
  ([DEVELOPER.md](DEVELOPER.md#usb-discovery)).
- **Type:** R
- **Run:** P-1 command plus `& $py -m serial.tools.list_ports -v`.
- **Pass:** all four fields match the patterns (case-insensitive).

### G-2 — Serial settings and bridge registers

- **Checks:** 19200 baud, 8E1 ("Startbits 8" means 8 data bits,
  [reference/modbus.md](reference/modbus.md#serial-settings)); the gateway
  answers as bridge on `device_id=1`, including `41998..42001` and the uptime
  at `41019`.
- **Type:** R
- **Run:** the output of P-3; repeat once after 5 minutes.
- **Pass:** all bridge registers answer; uptime grows by about 300 s between
  the two runs. Record the values.

### G-3 — Discovery stability

- **Checks:** `43901` = unit count, `43902..` = addresses
  ([MELTEM.md](MELTEM.md#discovery-and-unit-list): `6`, `[3, 2, 4, 5, 7, 6, 0, ...]`);
  at most 15 units ([reference/gateway.md](reference/gateway.md#capacity-and-radio));
  `discover_gateway_nodes` ignores addresses outside `2..16`.
- **Type:** R
- **Run:** P-3 ten times in a row (`1..10 | ForEach-Object { ... }`).
- **Pass:** identical count and list every time; count ≤ 15; all addresses in
  `2..16`.

### G-4 — Gateway registers are not on the units

- **Checks:** `43900..43905` are unreadable on the units and readable on slave
  `1` ([MELTEM.md](MELTEM.md#product-registers-and-readable-islands)).
- **Type:** R
- **Run:** for slave `1` and two units:
  `& $py -m tools.scan_register_windows --port $port --slave <s> --start 43900 --end 43917 --window 1 --show-ok`
- **Pass:** slave `1` answers; the units do not.

### G-5 — What an unconfigured address returns

- **Checks:** whether the gateway answers a request for a unit it does not know
  with a timeout or with code 10/11. Autonomous stand-in for a powered-off unit
  (HW-7 measurement 2, candidate C); [H-1](#h-1--powered-off-unit) confirms it
  with a real unit.
- **Type:** R
- **Run:** pick the first address in `2..16` that is not in the G-3 list.
  `& $py -m tools.raw_requests --port $port --slave <address> --read 41020:2 --rounds 3`
  ([B.1](#b1--raw-requests-with-exception-class-and-latency)).
- **Pass:** informational. Record the exception class and latency:
  `ModbusTimeoutError` after about 0.8 s, `GatewayTargetError` (code 11), or
  `GatewayPathUnavailableError` (code 10).

### G-6 — Other function codes

- **Checks:** the manuals list `0x04`, `0x08`, `0x11`
  ([reference/modbus.md](reference/modbus.md#function-codes)); unknown whether
  the gateway forwards them.
- **Type:** R
- **Run:** on slave `1` and one unit:
  - `0x04`: `& $py -m tools.scan_register_windows --port $port --slave <s> --start 41020 --end 41021 --window 2 --function input --show-ok`
  - `0x08` sub-function `0`, `0x11`, `0x2B/0x0E`:
    `& $py -m tools.raw_requests --port $port --slave <s> --diagnostics --server-id --device-id`
- **Pass:** informational. Record per code: answer, exception class, or
  timeout. `0x08` must echo `0x1234`.

## R — Register map and decoding (read-only)

Run R on every unit unless the test says otherwise.

### R-1 — Product and identity registers

- **Checks:** `40002` PRODUCT_ID = `116852`, `40011` PRODUCT_NAME =
  `VMD-22RPS44`, `40021` = `40002`, `40004` software version `2326` / `2584`
  ([MELTEM.md](MELTEM.md#product-registers-and-readable-islands)). Raw material
  for decoding the product ID ([TODO.md](TODO.md) item 2) and for telling
  `M-WRG-S` from `M-WRG-II`.
- **Type:** R
- **Run:** per unit
  `& $py -m tools.capture_setting_family --port $port --slave $s --family all_known --label baseline --output-dir $out\captures`
- **Evaluate:** decode `40002/40003` and `40021/40022` as UINT32, low word
  first; decode `40011..40020` as ASCII in both byte orders.
- **Pass:** values match the documented ones, or the difference is recorded
  together with the type plate from P-4.

### R-2 — Readable register islands

- **Checks:** readable islands `40000..40022`, `40024..40025`, `40200..40209`,
  `41000..41029`, `41100..41113`, `42000..42009`; `41041..41051` unavailable
  ([MELTEM.md](MELTEM.md#product-registers-and-readable-islands)). Three
  places where the documentation contradicts itself or the integration:
  - `40101` / `40103` / `40104` are listed as stable, but lie outside the islands;
    the integration reads `40101` (`rf_comm_status`)
  - the integration reads operating hours at `41030/41031`, outside `41000..41029`
  - `41120..41124` are missing from the islands (HW-4)
- **Type:** R
- **Run:** single-register scans on two units, if possible with different
  software versions (R-1):

  ```powershell
  foreach ($range in @(@(40000, 40300), @(41000, 41140), @(42000, 42020))) {
      & $py -m tools.scan_register_windows --port $port --slave $s --start $range[0] --end $range[1] --window 1 --only-ok |
          Tee-Object -Append "$out\R-2-slave$s.txt"
  }
  ```

  On the other units scan the same ranges with `--window 10`.
- **Pass:** islands match. Record the readability of `40101`, `40103`,
  `40104`, `41030..41033`, `41120..41124` separately.

### R-3 — Word order of 32-bit values

- **Checks:** Float32 and UINT32 are stored low word first
  (`word_order="little"` in [components.py](../custom_components/meltem_ventilation/device/components.py));
  the manuals do not say.
- **Type:** R
- **Run:** evaluate the R-1 captures: `41000`, `41002`, `41004`, `41009` as
  Float32 and `41030`, `41032`, `40002` as UINT32, each in both word orders.
- **Pass:** low word first gives plausible values (temperatures −30..60 °C,
  hours below 200 000, product ID `116852`), high word first does not.

### R-4 — Temperature swap `41000` / `41004`

- **Checks:** `41000` behaves like extract air (room air) and `41004` like
  exhaust air ([MELTEM.md](MELTEM.md#temperature-register-quirk)).
- **Type:** R
- **Run:** read `41000..41011` from the captures on every unit with
  temperature sensors (`-F` and better).
- **Evaluate:** needs at least 8 K between outdoor (`41002`) and room
  temperature. Extract air is the value closest to room temperature (warmest in
  the heating season). Exhaust air lies between outdoor and extract air, closer
  to outdoor.
- **Pass:** `41000` is extract air and `41004` exhaust air on every unit.
  Inconclusive with less than 8 K; repeat at another time of day.

### R-5 — Sensor registers and profile detection

- **Checks:** `41006` humidity extract, `41007` CO2, `41011` humidity supply,
  `41013` VOC (not in any manufacturer document, [MELTEM.md](MELTEM.md#registers-not-covered-by-the-collected-manufacturer-documents));
  what units without a sensor return (undocumented); `detect_slave_details`
  derives the right variant.
- **Type:** R
- **Run:** `& $py -m tools.count_requests --port $port`: its `rooms:` output
  shows per unit the profile and preview that `detect_slave_details` derived
  ([B.2](#b2--integration-client-with-request-counter)); raw values from the
  R-1 captures; read `41012` and `41013` five times within 10 minutes:
  `& $py -m tools.raw_requests --port $port --slave $s --read 41012:2 --rounds 5 --interval 120`.
- **Pass:** the detected suffix matches the type plate (P-4). Record per
  variant what missing sensors return (`0`, `0xFFFF`, exception). VOC counts
  as confirmed only if a `O/VOC-AUL` unit shows plausible, changing values.

### R-6 — Status, filter, and operating hours

- **Checks:** `41016..41018` are flags; `41027` days until the filter change;
  `41030` unit hours include standby, `41032` counts only fan motor time
  ([reference/modbus.md](reference/modbus.md#status-and-measurement-registers-read-only)).
- **Type:** R
- **Run:** evaluate the R-1 captures; read `41030..41033` directly if R-2 found
  them unreadable in the scan.
- **Pass:** flags are `0` or `1`; `41017 = 1` only if `41027 = 0`;
  `41030 ≥ 41032`.

### R-7 — Control settings

- **Checks:** `42000..42005` within the limits of `CONTROL_SETTING_LIMITS`;
  `42006` is undocumented; `42007..42009` within the documented ranges.
- **Type:** R
- **Run:** evaluate the R-1 captures.
- **Pass:** all values are in range. Record `42006` per unit.

### R-8 — Setting registers `30000` / `30002`

- **Checks:** whether baud rate and slave address can be read over the bus
  ([reference/modbus.md](reference/modbus.md#setting-and-addressing-registers)).
  **Read only, never write.**
- **Type:** R
- **Run:** on one unit:
  `& $py -m tools.scan_register_windows --port $port --slave $s --start 30000 --end 30002 --window 1 --function both --show-ok`
- **Pass:** informational. If readable, `30000` should be `1` (19200) and
  `30002` the slave address.

### R-9 — Mode block before any write

- **Checks:** many units reject `41120..41124` until a write occurred (HW-4
  measurement 1, [MELTEM.md](MELTEM.md#target-readback-and-measured-airflow)).
- **Type:** R
- **Run:** before any W test touches the unit, run
  `& $py -m tools.raw_requests --port $port --slave $s` without request
  options ([B.1](#b1--raw-requests-with-exception-class-and-latency)): it
  reads `41120` ×5, `41120` ×2, `41121` ×1, `41122` ×1.
- **Pass:** informational. Record per unit: answer or exception class. Note
  whether Home Assistant or the app wrote to the unit since its last power-up;
  only [H-2](#h-2--freshly-powered-unit) gives a clean "never written" state.

### R-10 — Mode block equals single reads

- **Checks:** the flow job takes `41121/41122` from the five-register block
  instead of single reads (HW-6 measurement 1, read-only part).
- **Type:** R
- **Run:** on every unit where R-9 read the block: 20 rounds of `41120` ×5,
  then `41121` ×1 and `41122` ×1:
  `& $py -m tools.raw_requests --port $port --slave $s --read 41120:5 --read 41121:1 --read 41122:1 --rounds 20`.
- **Pass:** block and single values are identical in every round. The write
  variants follow in [W-10](#w-10--unbalanced-write-and-readback).

### R-11 — Shadow ranges and their noise

- **Checks:** `51100..51113`, `51120..51133`, `51150..51151`, `52000..52010`
  are readable ([MELTEM.md](MELTEM.md)); which registers drift without any
  change (`52009` drifted before). This is the noise floor for
  [H-7](#h-7--setting-families).
- **Type:** R
- **Run:** two captures 5 minutes apart with no app or keypad action:
  `capture_setting_family --family all_known --label idle-1`, then
  `--label idle-2 --compare-latest`.
- **Pass:** all ranges readable. List the registers that changed.

## T — Transport and timing

### T-1 — Baseline with the pymodbus tools

- **Checks:** reference measurement for HW-7 measurement 1.
- **Type:** R
- **Run:** in the baseline worktree from P-5:

  ```powershell
  Push-Location ..\meltem-ha-baseline
  foreach ($scenario in "airflow_single", "airflow_block", "status_single", "status_block", "temps_single", "temps_mixed_block") {
      & $py -m tools.benchmark_gateway --port $port --scenario $scenario --cycles 10 --gap 0.1 |
          Tee-Object "$out\T-1-$scenario.txt"
  }
  & $py -m tools.profile_register_reads --port $port --gap 0.1 --cycles 5 | Tee-Object "$out\T-1-profile.txt"
  & $py ..\meltem-ha\tools\run_pymodbus_baseline.py --port $port --gap 0.1 --mode full --cycles 3 | Tee-Object "$out\T-1-full.txt"
  & $py ..\meltem-ha\tools\run_pymodbus_baseline.py --port $port --gap 0.1 --mode scheduler --cycles 10 | Tee-Object "$out\T-1-scheduler.txt"
  Pop-Location
  ```

- **Record:** per scenario the success rate, average and p95 latency, and the
  error classes.

### T-2 — Same measurement with the current tools

- **Checks:** HW-7 measurement 1.
- **Type:** R
- **Run:** repeat the direct scenarios and profile command from T-1 in this
  repository. For the integration-like measurements, use
  `& $py -m tools.benchmark_integration_like --port $port --gap 0.1 --mode full --cycles 3`
  and
  `& $py -m tools.benchmark_integration_like --port $port --gap 0.1 --mode scheduler --cycles 10`
  instead of the baseline launcher.
  Record the integration version from `pyproject.toml`. Alternate baseline
  and current-version runs (A-B-A-B) so that radio conditions affect both
  equally.
- **Pass (proposed):** no scenario with a lower success rate, no new error
  classes, average latency at most 20 % and p95 at most 30 % above the
  baseline. Any miss points to HW-7 candidate B.

### T-3 — Request gap sweep

- **Checks:** `REQUEST_GAP_SECONDS = 0.1` is stable; a gap of `0` inflates
  latency ([DEVELOPER.md](DEVELOPER.md#current-state-summary)).
- **Type:** R
- **Run:**
  `foreach ($gap in 0, 0.05, 0.1, 0.2, 0.3) { & $py -m tools.benchmark_gateway --port $port --scenario airflow_block --cycles 10 --gap $gap | Tee-Object "$out\T-3-gap$gap.txt" }`
- **Pass:** `0.1` is the smallest gap without failures and without inflated
  latency.

### T-4 — Timeout headroom

- **Checks:** `FIXED_TIMEOUT = 0.8 s` is enough for the gateway to answer.
- **Type:** R
- **Run:** evaluate the T-2 outputs.
- **Pass:** p99 of successful requests below 0.5 s, no successful answer above
  0.7 s, no `ModbusProtocolError` or `ModbusDesyncError` (signs of late
  answers). Otherwise HW-7 candidate B.

### T-5 — Silent address in the integration client

- **Checks:** a silent unit costs one timeout plus one retry per job, the link
  is not recycled while other units answer (`link_recycles` stays `0`), and
  the other units are not slowed down (HW-7 measurement 2; per-job skip and
  quiet window in [DEVELOPER.md](DEVELOPER.md#transport-home-assistants-shared-modbus-connection)).
- **Type:** R, 10 minutes
- **Run:** the discovered rooms plus a ghost room on the G-5 address; every
  10 s an airflow plan for all rooms, every 60 s also the temperature and
  status plans ([B.2](#b2--integration-client-with-request-counter)):

  ```powershell
  & $py -m tools.count_requests --port $port --ghost <address from G-5> --plan airflow --plan temperatures/6 --plan status/6 --rounds 60 --interval 10 --diagnostics-every 6 |
      Tee-Object "$out\T-5.txt"
  ```

  Every job line shows room, requests, duration, and the failed read groups;
  `client.transport_diagnostics()` follows every minute.
- **Pass if G-5 showed a timeout:** at most 2 requests per ghost job,
  `link_recycles = 0`, 100 % success on the real rooms, one airflow round over
  all rooms finishes within 10 s.
- **If G-5 showed code 10/11:** record the requests per job (in-memory
  estimate: 8 per airflow job, 28 per full read) as evidence for HW-7
  candidate C.

### T-6 — Busy answers (code 6)

- **Checks:** whether the gateway ever answers `SERVER_DEVICE_BUSY`. tmodbus
  retries it for up to 60 s while the link is held (HW-7 measurement 5).
- **Type:** R
- **Run:**
  1. Search all T outputs for `ServerDeviceBusyError` and for single requests
     slower than 2 s.
  2. Do not run the old active stimulus with `--gap 0`: it conflicts with the
     hard pacing rule, which permits zero gap only in T-3. No safe Busy
     stimulus is currently documented, so active provocation is blocked pending
     a separately reviewed, paced method.
- **Pass:** informational. Record any occurrence with its duration. Code 6
  that blocks for seconds is evidence for HW-7 candidate D. A passive run with
  no occurrence does not establish that the gateway never returns code 6.

### T-7 — Port release and reopening

- **Checks:** the port is opened exclusively on the host OS; a second process
  fails cleanly (tools exit with code `2`); the port is free again right after
  a process dies. Autonomous part of HW-7 measurement 6.
- **Type:** R
- **Run:**
  1. Start `& $py -m tools.watch_register_changes --port $port --slave $s --range 41020:2`
     in the background.
  2. Run P-3: expect exit code `2` and "could not open serial connection".
  3. Stop the background process by PID (`Stop-Process` on Windows, `kill <PID>`
     on Linux), then run P-3 again.
  4. Run P-3 twenty times in a row.
- **Pass:** step 2 exits with `2`, steps 3 and 4 exit with `0`.

### T-8 — Wider block reads (optional)

- **Checks:** groundwork for wider block reads ([TODO.md](TODO.md) item 4).
  Not part of HW-7 post-release validation. The `register_ranges` stay
  unchanged until a benchmark backs a change.
- **Type:** R
- **Run:** [`raw_requests`](#b1--raw-requests-with-exception-class-and-latency)
  with `--rounds 30`, once per variant: `--read 41016:6` against
  `--read 41016:3 --read 41020:2`; `--read 41000:14` against the current blocks
  `--read 41000:6 --read 41006:2 --read 41009:2 --read 41011:3`.
- **Record:** success rate and total time per variant.

### T-9 — Live setup in the test harness (optional)

- **Checks:** setup and unload through Home Assistant's `modbus` integration
  on the real port; the port is free after unload (HW-7 measurement 6).
- **Type:** R
- **Run:** a throwaway test in `$out\scripts\` along the lines of
  `TestSharedModbusConnection` in [tests/test_init.py](../tests/test_init.py),
  without patching `ModbusConnection`, with the real port and the discovered
  rooms. After the unload, P-3 must succeed.
- **Limits:** the harness ships HA `2026.10.0b0` with
  `modbus-connection 4.12.3`, not a stable HA `2026.10.x` release. If the
  harness's checks for lingering
  tasks or threads fail on the serial backend, mark the test as blocked;
  [H-9](#h-9--real-home-assistant-instance) covers it.

### T-10 — 24-hour soak

- **Checks:** HW-7 measurement 7, HW-6 measurement 2; whether
  `TRANSPORT_LINK_QUIET_SECONDS = 10` fits (the longest pause between two
  answers in normal operation).
- **Type:** S, only if released in P-4
- **Run:** in the background, with the coordinator intervals from [const.py](../custom_components/meltem_ventilation/const.py):
  airflow every 10 s, temperatures and status every 60 s, filter, hours and
  control settings every 3600 s:

  ```powershell
  & $py -m tools.count_requests --port $port --plan airflow --plan temperatures/6 --plan status/6 --plan slow/360 --rounds 8640 --interval 10 --diagnostics-every 30 --csv "$out\T-10.csv" > "$out\T-10.txt"
  ```

  The CSV has one line per job (time, slave, plan, result, requests,
  latency); the log has `client.transport_diagnostics()` every 5 minutes.
  This is a long-running read-only client test, not a real Home Assistant
  coordinator soak; [H-9](#h-9--real-home-assistant-instance) covers the
  installed integration. Record any app/keypad/manual operation with its time
  and slave, because it can change mode-register readability or the mode being
  observed without a Modbus write.
- **Pass (proposed):** at least 99.5 % successful jobs per unit;
  `link_recycles = 0`, or every recycle explained; no busy wait above 2 s;
  longest pause between two answers below 5 s; no latency trend over the day.
  Also report the job-level result separately from transport diagnostics:
  known mode-register exceptions such as HW-4 code `0x05` remain failed
  groups/jobs and must not be silently removed from the total-job rate, but
  they are not timeouts or link failures. If they prevent the proposed
  threshold, report the soak as transport-stable but not a full pass.

## W — Writes on a released test unit

Only on slaves released in [P-4](#p-4--ask-the-person-once), one unit at a
time, with the snapshot and restore steps from the
[safety rules](#before-and-after-every-w-test).

Notes on the tools:

- `--room-index` of `benchmark_integration_like` is the 1-based position in
  its `rooms:` output, not the slave address. The write modes require it.
- The write modes of `benchmark_integration_like` restore with a balanced
  manual write. A unit that was in a preset, unbalanced, or sensor mode must be
  restored from the snapshot ([Appendix A](#appendix-a--restoring-a-unit)).
- The series cannot be probed, so `benchmark_integration_like` and
  `count_requests` treat every unit as `M-WRG-II` (max 100 m³/h). On an
  `M-WRG-S` pass `--profile <slave>=s_plain` (or `s_f`, `s_fc`); the `rooms:`
  output shows the profile in use.
- Client decode: `& $py -m tools.count_requests --port $port --slaves $s --show-state`
  reads an airflow plan and prints the decoded state (`operation_mode`,
  `preset_mode`, `target_level`, `intensive_active`, ...).
- `write_registers` keeps going after a failed write. Check that every line
  ends in `ok`.
- `--delta` is added to the current airflow. Use a negative delta when the
  unit runs within 20 m³/h of its maximum, so the target stays in range.
- Wait at least 10 s between runs on the same unit.

### W-1 — Balanced write: `41121` readback and airflow lag

- **Checks:** `41121` reflects a balanced write at once (60 m³/h ↔ `120`);
  `41020/41021` lag behind ([MELTEM.md](MELTEM.md#target-readback-and-measured-airflow));
  HW-2 measurement 3; HW-7 measurement 4.
- **Type:** W
- **Run:** choose from `--delta` values `-20`, `-4`, `4`, `20`, but run only
  deltas that keep the target within the hard limits of 10 m³/h and the
  profile maximum. For example, from a 20 m³/h baseline, skip `-20` because
  it would target 0; `-4`, `4`, and `20` are in range:
  `& $py -m tools.benchmark_integration_like --port $port --gap 0.1 --mode write_observe --room-index $ri --delta <d> --observe-seconds 60 --sample-interval 1`.
  Repeat one in-range delta with the baseline tool:
  `Push-Location ..\meltem-ha-baseline; & $py ..\meltem-ha\tools\run_pymodbus_baseline.py --port $port --gap 0.1 --mode write_observe --room-index $ri --delta <d> --observe-seconds 60 --sample-interval 1; Pop-Location`.
- **Pass:** `41121` shows the new raw value in the first sample. Record the
  time until `41020/41021` reach the target (±1) per delta.

### W-2 — Settle delay sweep

- **Checks:** `WRITE_SETTLE_SECONDS = 1.5` and `refresh_attempts=2`
  (HW-2 measurement 1, candidates A and C).
- **Type:** W
- **Run:**

  ```powershell
  foreach ($settle in 0, 0.5, 1.0, 1.5) {
      foreach ($i in 1..3) {
          & $py -m tools.benchmark_integration_like --port $port --gap 0.1 --mode write_refresh --room-index $ri --delta 10 --settle-seconds $settle --poll-interval 0.5 --max-polls 20 |
              Tee-Object "$out\W-2-settle$settle-$i.txt"
      }
  }
  ```

- **Pass:** record the smallest settle time at which the first `post_write`
  poll is `OK` in all three repetitions. If that already holds at `0`,
  candidate C (one refresh) is backed as well. This poll confirms the
  integration's target readback; separately record `41020/41021`, because a
  confirmed target does not prove the fans have already reached that airflow.

### W-3 — Settle delay with interleaved traffic

- **Checks:** whether traffic to another unit during the settle time delays
  the write or disturbs the other unit (HW-2 measurement 2, candidate B).
- **Type:** W on the test unit, R on a second unit
- **Run:** a script built from [B.2](#b2--integration-client-with-request-counter): write
  with `client.write_level(room, level)`, then poll with an airflow plan as in
  W-2, while a second task reads the airflow of another room every 0.5 s on
  the same link. Use the W-2 settle times; three repetitions each.
- **Pass:** the polls needed match W-2, and the second unit has no failures and
  no latency outliers during the write.

### W-4 — Target holds without polling

- **Checks:** a written target is still active after an idle phase with no
  reads.
- **Type:** W
- **Run:** `& $py -m tools.benchmark_integration_like --port $port --gap 0.1 --mode write_idle_check --room-index $ri --delta 10 --idle-seconds 12`
- **Pass:** `read_after_idle` and `read_after_restore` are `OK`.

### W-5 — Mode block after the first write

- **Checks:** whether a write unlocks the five-register read (HW-4
  measurements 2 and 3).
- **Type:** W
- **Run:** on a released unit where R-9 found the block unreadable, first take
  the required all-known snapshot and read `41020/41021`. These are extract
  and supply airflow, respectively; do not assume `41021` is a valid balanced
  target. If the baseline is one-sided or the chosen target would be zero,
  stop and obtain approval for an explicit nonzero balanced test target and
  restore sequence. Encode the approved target as
  `round(target × 200 / max airflow)`. Otherwise write
  `41120=3`, `41121=<approved raw target>`, `41132=0`, then run R-9 again
  after 1 s, 10 s, and 60 s. Restore and verify the full pre-test state using
  the safety rules above.
- **Pass:** informational. Readable afterwards points to HW-4 candidate A,
  still refused to candidate B. Stickiness after a power cycle is checked in
  [H-2](#h-2--freshly-powered-unit).

### W-6 — `41132` commit latch

- **Checks:** the unit takes over `41120..41132` only when `41132` is written
  ([reference/modbus.md](reference/modbus.md#write-order)); whether `41121`
  already reads back a value that is not applied yet. That matters because
  the integration confirms writes via `41121`.
- **Type:** W
- **Run:**
  1. Record `41020/41021` and `41121`.
  2. `write_registers --write 41120=3 --write 41121=<raw of current + 20 m³/h>`, without `41132`.
  3. Read `41020/41021` and `41120..41122` every 2 s for 20 s.
  4. `write_registers --write 41132=0`.
  5. Read every 2 s for 60 s.
  6. Restore.
- **Pass:** airflow changes only after step 4. Record whether `41121` showed
  the new value in step 3.

### W-7 — Quick presets `228` / `229` / `230`

- **Checks:** LOW / MED / HIGH as `41120=3`, `41121=228/229/230`, `41132=0`
  ([MELTEM.md](MELTEM.md#reverse-engineered-app-preset-behavior)); the
  integration decodes `preset_mode` from them.
- **Type:** W
- **Run:** per code: write the sequence; after 60 s read `41020/41021`,
  `41120..41124`, and the client decode (see the notes above).
- **Pass:** `41121` reads back the code; airflow settles at the LOW / MED /
  HIGH airflows stored on the unit; `preset_mode` is `low` / `medium` /
  `high`. LEDs: [H-6](#h-6--keypad-leds-and-physical-state).

### W-8 — Intensive and power-off

- **Checks:** HW-1: whether `41123/41124` survive a power-off write, and which
  candidate (A, C, D) fits.
- **Type:** W, needs the five-register read (R-9 or W-5)
- **Run:**
  - **Run A, current behavior:** write `41123=3`, `41124=227`, `41132=0`; after
    30 s read `41120..41124` and `41020/41021`. Then write `41120=1`,
    `41121=0`, `41132=0` (what `write_level(0)` does); read after 5, 30 and 60 s.
  - **Run B, candidate A:** intensive on again, then
    `41123=0`, `41124=0`, `41120=1`, `41121=0`, `41132=0` as one sequence;
    read the same way.
  - **Run C, manual write during intensive:** intensive on, then `41120=3`,
    `41121=<raw>`, `41132=0`; read the same way (relevant for candidate B).
  - After each run, run the client decode (`intensive_active`,
    `operation_mode`).
- **Pass:** informational, decides HW-1:
  - fans stop and `41123/41124` are `0` in run A: candidate D
  - fans stop but `41124` stays `227` in run A: candidate C, or A if run B
    clears it cleanly
  - fans do not stop in run A: candidate A is required, report immediately
- **Restore:** never leave intensive running; end with the snapshot state.

### W-9 — Intensive ends by itself

- **Checks:** intensive ends after the configured time (default 15 min,
  [reference/device-parameters.md](reference/device-parameters.md)); whether
  `41123/41124` are cleared then (HW-1, reason 1).
- **Type:** W, about 20 minutes
- **Run:** start intensive as in W-8, then
  `& $py -m tools.watch_register_changes --port $port --slave $s --range 41120:5 --range 41020:2 --interval 30`
  until 5 minutes after the expected end.
- **Pass:** informational. Record when the airflow drops and whether
  `41123/41124` change.

### W-10 — Unbalanced write and readback

- **Checks:** `41121` = supply, `41122` = extract; `41020` = extract and
  `41021` = supply airflow; block reads equal single reads after unbalanced
  writes (HW-6 measurement 1).
- **Type:** W
- **Run:** write `41120=4`, `41121=<raw 40 m³/h>`, `41122=<raw 60 m³/h>`,
  `41132=0`. After 60 s read five times: `41120` ×5, `41121` ×1, `41122` ×1,
  `41020` ×2. Run the client decode.
- **Pass:** `41021 ≈ 40` and `41020 ≈ 60`; block and single values equal;
  `operation_mode = unbalanced`, `target_level = 40`,
  `extract_target_level = 60`. Repeat the block/single comparison in W-1, W-7,
  and W-11.

### W-11 — Shortcut encoding `200 + n` on the unit

- **Checks:** HW-3, unit side: does the unit run `n × 10` m³/h for
  `41122 = 200 + n`, or its stored shortcut airflow whatever `n` is?
  ([MELTEM.md](MELTEM.md#reverse-engineered-app-preset-behavior) saw `201`
  and `205` change the airflow.)
- **Type:** W
- **Run:** for `n` in `3`, `5`, `7`: write `41120=4`, `41121=0`, `41122=200+n`,
  `41132=0`; read `41020/41021` after 60 s; run the client decode. Then once
  on the supply side (`41121=205`, `41122=0`).
- **Pass:**
  - `41020 ≈ n × 10` for each `n`: the linear decode is right, HW-3 candidate
    A or C
  - the same airflow for every `n`: the value only selects the stored shortcut,
    and the target the integration reports is wrong. Report it.
  - `preset_mode` decodes `extract_only` / `supply_only`

### W-12 — Single-direction start from off

- **Checks:** HW-5: does a stopped unit accept `41120 = 4` with one side at
  `0`?
- **Type:** W
- **Run:** write `41120=1`, `41121=0`, `41132=0`; wait until `41020/41021`
  read `0` (at most 60 s). Then write `41120=4`, `41121=0`,
  `41122=<raw 40 m³/h>`, `41132=0`; read `41020/41021` after 10, 30, 60 s.
- **Pass:** `41020 ≈ 40` and `41021 = 0` back HW-5 candidate B. Both fans
  running or none: keep candidate A.

### W-13 — Sensor control modes

- **Checks:** `41120=2` with `41121 = 112` (humidity, `-F`), `144` (CO2,
  `-FC`), `16` (automatic, `-FC`); read back and decode
  (`_decode_operation_mode`).
- **Type:** W, only on units with the matching sensors
- **Run:** per supported selector: write `41120=2`, `41121=<selector>`,
  `41132=0`; after 10 s read `41120/41121` and run the client decode.
- **Pass:** readback equals the written selector; `operation_mode` is
  `humidity_control`, `co2_control`, or `automatic`.

### W-14 — Airflow scaling

- **Checks:** `M-WRG-II`: raw `0..200` ↔ `0..100` m³/h (raw `70` → 35,
  `100` → 50). `M-WRG-S`: `raw / 2` or `raw × 97 / 200`? (open question in
  [reference/modbus.md](reference/modbus.md#open-questions-and-contradictions)).
- **Type:** W
- **Run:** balanced raw writes with `write_registers`: raw `70` and `100` on an
  `M-WRG-II`. On an `M-WRG-S`, if one exists (R-1, P-4): raw `80` and `200`.
  Read `41020/41021` after 60 s each.
- **Pass:** `M-WRG-II` shows 35 and 50. `M-WRG-S` shows 40 and 100
  (`raw / 2`) or 39 and 97 (`× 97 / 200`); the result decides
  `_scale_airflow_to_raw`.

### W-15 — Control settings

- **Checks:** exact readback of `42000..42005` without side effects (control
  experiment in [SETTING_RE_BACKLOG.md](SETTING_RE_BACKLOG.md)); CO2 starting
  point range `500..1200` (Modbus) against `400..1400` (remote control).
- **Type:** W
- **Run:**
  1. `42000`: original → original + 1 → original, with
     `capture_setting_family --family humidity --compare-latest` after each step.
  2. Optional, `-FC` units only: `42003 = 1300` (inside the remote control
     range), read back, restore at once. This is the one write outside the
     Modbus limits allowed by this plan.
- **Pass:** step 1 reads back exactly and changes nothing else. Step 2:
  record whether the unit accepts `1300` or answers `IllegalDataValueError`.

### W-16 — Integration write paths end to end

- **Checks:** the `4.0.0` write sequences and their decode against real
  hardware.
- **Type:** W
- **Run:** a script built from [B.2](#b2--integration-client-with-request-counter). Call in
  order, 15 s apart, and after each call `read_room_state` with an airflow
  plan (and a control settings plan for the last step):
  1. `client.write_level(room, 40)`: `operation_mode = manual`, `target_level = 40`
  2. `client.write_unbalanced_levels(room, 30, 50)`: `unbalanced`, `30` / `50`
  3. `client.write_preset_mode(room, "low")`: `preset_mode = low`
  4. `client.write_preset_mode(room, "intensive")`: `intensive_active = True`
  5. `client.clear_intensive(room)`: `intensive_active = False`
  6. `client.write_operating_mode(room, "off", 0, 0)`: `operation_mode = off`
  7. `client.write_control_setting(room, "humidity_starting_point", <original + 1>)`,
     then back to the original
- **Pass:** each decoded field matches within two refreshes. Restore from the
  snapshot.

## H — Tests that need a person

The agent prepares the measurement, asks for exactly one action, measures, and
repeats if needed.

### H-1 — Powered-off unit

- **Checks:** HW-7 measurement 2, HW-6 measurement 3: the
  answer for a really powered-off unit, its cost per job, and the effect on the
  other units.
- **Run:** start the T-5 scheduler without a ghost room in the background.
  Ask the person to switch one unit off (fuse or plug), wait 5 minutes, then
  switch it on again and wait 5 more minutes.
- **Pass:** as in T-5, with the real unit as the silent one. Also record how
  long the unit takes to answer again after power-up.

### H-2 — Freshly powered unit

- **Checks:** HW-4 measurements 1 and 4: whether the five-register read is
  refused after power-up and whether the unlock survives a power cycle.
- **Run:** right after the power-up in H-1, run R-9 before any write. Then run
  W-5. Ask for a second power cycle and run R-9 again.
- **Pass:** informational, decides HW-4 (A: unlocked by the first write;
  B: never readable on this unit).

### H-3 — USB unplug and replug

- **Checks:** HW-7 measurement 6: the client recovers after
  the link disappears.
- **Run:** with the T-10 scheduler running, ask the person to unplug the USB
  cable for 30 s and plug it back in.
- **Pass:** `ModbusConnectionError` while unplugged; the client reconnects on
  its own within one airflow round after the replug; the COM port number stays
  the same (P-1). Record the recovery time and `link_recycles`.
- **Note:** a standalone `count_requests` run exits when its serial connection
  is lost. It can confirm link-loss reporting, but cannot pass the automatic
  recovery criterion; use a client that remains alive and schedules another
  job after replug, ideally the Home Assistant coordinator in H-9.

### H-4 — App shortcut encoding

- **Checks:** HW-3, app side: `Abluft` / `Zuluft` shortcut airflow encoded as
  `200 + airflow / 10` over the whole range.
- **Run:** run `watch_register_changes --range 41120:5 --interval 2` in the
  background. Ask the person to set the `Abluft` shortcut in the app to 10, 20,
  40, 60, 80, 90, and 100 m³/h and to activate it each time; then two values
  for `Zuluft`. The app reaches the unit over radio, so the USB port stays
  free for the agent.
- **Pass:** `41122` (or `41121`) equals `200 + airflow / 10` for all seven
  values: HW-3 candidate A or C. Otherwise candidate B (code table).

### H-5 — Intensive from the app

- **Checks:** HW-1 measurement 5: the app writes the same `41123 = 3`,
  `41124 = 227`; then the Home Assistant power-off path.
- **Run:** ask the person to start intensive in the app; read `41120..41124`;
  then run W-8 run A from the power-off step.
- **Pass:** informational; compare with W-8.

### H-6 — Keypad LEDs and physical state

- **Checks:** the keypad LEDs for presets written over Modbus; `Abluft` /
  `Zuluft` LEDs stay dark, as with the app ([MELTEM.md](MELTEM.md)).
- **Run:** during W-7, W-8, and W-11 ask the person to check the LEDs within
  10 s of the write (they light only that long) and whether the fans run.
- **Pass:** informational.

### H-7 — Setting families

- **Checks:** [SETTING_RE_BACKLOG.md](SETTING_RE_BACKLOG.md) (families
  `intensive`, `keypad`, `cross_ventilation`, `humidity`, `co2`);
  [TODO.md](TODO.md) item 1 (shortcut configuration in `511xx` / `520xx`).
- **Run:** follow the capture workflow and stop criteria in
  SETTING_RE_BACKLOG.md: `capture_setting_family --family <f> --label baseline`;
  the person changes one app setting; capture with `--compare-latest`; the
  person reverts it; capture again. Subtract the noise from R-11.
- **Pass:** informational. A register that follows the setting value in both
  directions is a finding for MELTEM.md.

### H-8 — Unit of the control level registers

- **Checks:** `%` or m³/h for `42001`, `42002`, `42004`, `42005`, `42007`; on
  `M-WRG-S` whether `100 %` means 97 or 100 m³/h
  ([reference/modbus.md](reference/modbus.md#open-questions-and-contradictions)).
- **Run:** ask the person for the values shown in the app or on the remote
  control; compare with the R-7 values. Only conclusive on an `M-WRG-S` or
  with values that are not multiples of 10.
- **Pass:** informational.

### H-9 — Real Home Assistant instance

- **Checks:** HW-7 measurements 6 and 7 in a real installation; HW-6 measurement 2
  (log comparison with the previous release).
- **Run:** the person installs the branch on HA `2026.10` on a Linux host with
  the gateway attached and shares the logs and the diagnostics download. The
  agent checks: setup with USB discovery; `port_in_use` with a second Modbus
  hub on the same port with other settings; reload and unload release the port;
  `coordinator.transport` in the diagnostics; 24 hours of logs without
  "Recycling the Meltem gateway link", compared with logs of the previous
  release.
- **Startup follow-up:** for the progressive startup and telegram-level read
  limit, keep a fixed request rate (for example `3 req/s`) and capture three
  Core restarts with startup debug logging, including `tmodbus.raw_traffic`.
  Use only one process on the port and do not send test writes. For each run:
  1. Check successive function-`0x03` send timestamps, across all slaves and
     retry/fallback reads, against a minimum `1 / rate` interval (allow 1 ms
     for log timestamp precision). Write timing is intentionally not retuned.
  2. Record when each room's first airflow and subsequent groups appear,
     rather than waiting for a single full scan to publish all rooms.
  3. Export diagnostics and capture the data-health entity's history: groups
     not yet read must remain unknown; actual failures must remain visible.
     Compare code-5 register refusals separately from transport errors.
  4. Check that ongoing airflow polls and all slow first-pass jobs progress,
     with no new transport errors. Lower rates may lengthen the scan; record
     actual freshness rather than assuming the target intervals are met.
  The motivating observation is recorded once in
  [MELTEM.md](MELTEM.md#home-assistant-restart-on-2026-10-08). The revised
  behavior is covered by in-memory tests, not yet verified on the live host.
- **Pass:** no new transient read failures compared with the previous release;
  every recycle explained.

## Not covered

- Switching to 9600 baud via `30000` or changing the slave address via
  `30002`: changes the bus settings; never written by this plan.
- The gateway LAN interface and the cloud API.
- A gateway factory reset: removes all radio pairings.
- [TODO.md](TODO.md) item 3 (test harness for HA `2026.10`): no hardware
  needed.

## HW-7 — Post-release tmodbus validation

`4.0.0` has been released. The measurements below remain open post-release
validation for the tmodbus transport; they are not a release gate for that
version. Record each result as `pass`, `fail`, `inconclusive`, `blocked`, or
`skipped`, and keep HW-7 open while required observations remain incomplete or
incomparable.

| HW-7 measurement | Question | Tests |
| --- | --- | --- |
| 1. Benchmarks | latency and timeout rate against the pymodbus baseline | T-1, T-2, T-3, T-4 |
| 2. Powered-off unit | timeout or code 10/11, requests per job, unwanted `disconnect()` | G-5, T-5, H-1 |
| 3. Mode block fallback | five to two registers on a freshly powered unit (HW-4) | R-9, W-5, H-2 |
| 4. Writes | `41121` confirmation, settle time, lock duration (HW-2) | W-1, W-2, W-3, W-6 |
| 5. Busy | does the gateway answer code 6, and for how long | T-6 |
| 6. Link lifecycle | USB replug, reload, unload release and reopen the port | T-7, T-9, H-3, H-9 |
| 7. Soak | 24 hours of operation with all units | T-10, H-9 |

## Recording results

Raw output stays in `tmp/live-tests/<date>/` and is never committed. Keep the
summary in `$out\SUMMARY.md`:

| ID | Time | Units | Result | Key numbers | Raw file |
| --- | --- | --- | --- | --- | --- |
| G-5 | 2026-05-01 10:12 | 15 | pass | `ModbusTimeoutError`, 805 ms | `G-5.txt` |

Result is one of `pass`, `fail`, `inconclusive`, `blocked`, or `skipped`, with
a reason for every result other than `pass`.

Afterwards, move the findings into the documentation:

- **HW items:** add a "Measured on <date>" paragraph with the numbers to the
  item in [HARDWARE_BACKLOG.md](HARDWARE_BACKLOG.md), update its status, and
  name the candidate the numbers support. For resolved items the decision
  moves to [DEVELOPER.md](DEVELOPER.md) and the measured behavior to
  [MELTEM.md](MELTEM.md).
- **Hardware observations** (islands, encodings, product IDs, word order,
  swap): [MELTEM.md](MELTEM.md), with the date and the software version of the
  unit.
- **Answers to manufacturer open questions:** do not edit the manufacturer
  tables in `docs/reference/`. Record the observation in MELTEM.md and link it
  from the open question.
- **HW-7 validation:** note under HW-7 which of its seven measurements passed,
  see [HW-7 post-release validation](#hw-7--post-release-tmodbus-validation).

Report to the person: failed and inconclusive tests, the candidate solutions
the data supports, and the H tests still open.

## Appendix A — Restoring a unit

Restore from the `before-<ID>` snapshot. Write in the order shown, with
`write_registers --write ...`, and check that every line ends in `ok`.

| `41120` in the snapshot | Restore sequence |
| --- | --- |
| `1` (off) | `41120=1`, `41121=0`, `41132=0` |
| `2` (sensor control) | `41120=2`, `41121=<112, 144 or 16>`, `41132=0` |
| `3` (manual or preset) | `41120=3`, `41121=<raw or 228..230>`, `41132=0` |
| `4` (unbalanced or shortcut) | `41120=4`, `41121=<value>`, `41122=<value>`, `41132=0` |
| unreadable (HW-4) | Do not infer a restoration target from `41021`; stop unless an explicit nonzero balanced restore target and sequence were approved. |

- If the snapshot shows intensive (`41123=3`, `41124=227`), restore the base
  mode only; restarting intensive would restart its timer. Record it.
- If the mode is unreadable and either airflow direction is zero, do not use
  the unreadable-mode balanced fallback without an explicit restore target.
  A zero target is not a valid balanced running target; stop and clarify the
  restore sequence before any W test.
- Control settings: write back every changed `42000..42005` register.
- For nonzero balanced targets, raw is `round(target × 200 / max airflow)`;
  `max airflow` is 100 for `M-WRG-II` and 97 for `M-WRG-S`.

## Appendix B — Request tools and custom scripts

Custom scripts go to `$out\scripts\` and run from the repository root, for
example `& $py "$out\scripts\w16.py" $port 3`. `PYTHONPATH` from P-2 makes
`tools.*` importable.

### B.1 — Raw requests with exception class and latency

`tools/raw_requests.py` sends single requests to one unit and prints the
answer or the exception class with the latency. The summary counts answers,
error classes, and the total time per request.

- no request options: `41120` ×5, `41120` ×2, `41121` ×1, `41122` ×1 (R-9)
- `--read 41020:2` or `--read 41000-41013`: holding registers, repeatable,
  sent in this order; `--read input:41020:2` uses function `0x04`
- `--diagnostics`, `--server-id`, `--device-id`: `0x08` sub-function `0` with
  `0x1234`, `0x11`, `0x2B/0x0E`
- `--rounds 20 --interval 120`: repeat the requests; the interval runs from
  the start of one round to the next

### B.2 — Integration client with request counter

`tools/count_requests.py` runs the integration's own client, loaded by
`tools/benchmark_integration_like.py` with a small Home Assistant stub, and
counts the requests per slave, including retries.

- `--plan airflow|temperatures|status|slow|full`, repeatable; `NAME/EVERY`
  runs a plan only in every EVERY-th round, so `--plan temperatures/6` with
  `--interval 10` runs it every 60 s
- `--rounds`, `--interval`: number of rounds and seconds from one round to the
  next
- `--slaves 3,5`: poll only these units; `--ghost 15`: add an unconfigured
  address as an `ii_plain` room
- `--profile 3=s_plain`: the profile of an `M-WRG-S` (see the notes in [W](#w--writes-on-a-released-test-unit))
- `--show-state`: print the decoded room state after every job
- `--diagnostics-every N`: print `client.transport_diagnostics()` and the
  request counts every N rounds
- `--csv FILE`: append one line per job (time, slave, plan, result, requests,
  latency)

Tests with their own sequence (W-3, W-16) import the module in a script:

```python
"""Decode every room through the integration client and count the requests."""

import asyncio
import sys

import tools.count_requests as cr

bil = cr.bil


async def main(port: str) -> None:
    link = bil.open_connection(port)
    try:
        rooms = await bil.discover_rooms(link, port)
        client = cr.counting_client(link, port)
        for room in rooms:
            state = await client.read_room_state(room, bil.RoomState(), cr.PLANS["airflow"])
            print(room.slave, cr.describe(state))
        print(client.transport_diagnostics(), dict(cr.CountingUnit.counts))
    finally:
        await link.close()


asyncio.run(main(sys.argv[1]))
```

Building blocks for the tests:

- rooms: `await bil.discover_rooms(link, port, {slave: profile})`; ghost room:
  `bil.RoomConfig(key="ghost", name="Ghost", profile="ii_plain", slave=15)`
- plans: `cr.PLANS[name]`, `bil.RefreshPlan()` (everything) or
  `bil.RefreshPlan.only(...)` with
  `refresh_airflow`, `refresh_temperatures`, `refresh_environment`,
  `refresh_status`, `refresh_filter_change_due`, `refresh_filter_days`,
  `refresh_operating_hours`, `refresh_control_settings`
- probe: `await bil.detect_slave_details(bil.prepare_unit(link.for_unit(s), s, bil.new_transport_policy()))`
- writes: `client.write_level`, `write_unbalanced_levels`,
  `write_operating_mode`, `write_preset_mode`, `clear_intensive`,
  `write_control_setting`
- counters: `client.transport_diagnostics()`, `cr.CountingUnit.counts`
- tmodbus retries, including busy: `logging.basicConfig(level=logging.DEBUG)`
