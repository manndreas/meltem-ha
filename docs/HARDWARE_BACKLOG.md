# Hardware Verification Backlog

Findings from code review that are **not** fixed in the repository because they
cannot be decided without a live `M-WRG-GW` gateway and at least one `M-WRG`
unit. Each entry states what was observed in the code, why a blind fix would be
irresponsible, what to measure, and which candidate solutions exist. How to
measure is in [LIVE_GATEWAY_TESTS.md](LIVE_GATEWAY_TESTS.md); every entry
names its tests there.

Related documents:

- `docs/reference/` — manufacturer reference extracted from the Meltem documents
- `docs/MELTEM.md` — observed register behaviour and traced register writes
- `docs/DEVELOPER.md` — implementation notes and the decisions behind them
- `docs/SETTING_RE_BACKLOG.md` — app-side settings reverse engineering
- `docs/TODO.md` — remaining non-hardware work
- `docs/LIVE_GATEWAY_TESTS.md` — test plan for an AI agent on the live gateway,
  covering every item below

Status legend: `open` (needs measurement), `parked` (measured, decision
deferred), `resolved` (move the decision into `docs/DEVELOPER.md` and the
measured behaviour into `docs/MELTEM.md`).

---

## HW-1 — Intensive shadow registers survive a power-off write

Priority: medium
Status: open
Affected code: `modbus_client.write_operating_mode`, `modbus_client.write_level`

### Observation

The intensive override uses a secondary write path, but the off/manual paths
never touch it:

| Action | Registers written |
| --- | --- |
| Intensive on | `41123 = 3`, `41124 = 227`, `41132 = 0` |
| Intensive off | `41123 = 0`, `41124 = 0`, `41132 = 0` |
| Unit off | `41120 = 1`, `41121 = 0`, `41132 = 0` |
| Manual airflow | `41120 = 3`, `41121 = <raw>`, `41132 = 0` |

If the unit does not clear `41123` / `41124` on its own, `41124 = 227` remains
set after switching off. `_decode_intensive_active` then reports
`intensive_active = True` for a unit that is not running, and the intensive
switch in Home Assistant stays on.

### Manufacturer documentation

Intensive ventilation ends by itself after a configurable duration (device
parameter 12, 0-240 min, default 15 min) and the previous program resumes.
How this appears in `41120..41124` is not documented. See
`docs/reference/functions.md` and `docs/reference/device-parameters.md`.

### Why this cannot be fixed blind

1. It is unknown whether the firmware clears the registers itself. The override
   also ends on its own after a runtime configured in the Meltem app, so
   self-clearing is plausible. If it self-clears, an extra write is pointless
   bus traffic; if it does not, the current behaviour is a real bug.
2. `docs/reference/modbus.md` states that `41132` must always be written last and that
   the unit accepts `41120..41132` only once `41132` has been written. That
   reads like a commit latch. A combined transaction
   (`41123 = 0`, `41124 = 0`, `41120 = 1`, `41121 = 0`, `41132 = 0`) has never
   been traced, so it is unknown whether the unit applies all of it, whether
   the last written mode wins, or whether clearing the shadow registers in the
   same commit cancels the off command.
3. Verification depends on a unit that answers the five-register read at
   `41120`. `_read_mode_block` falls back to a two-register read on units that
   reject it, and `_decode_intensive_active` then returns `None`. On such a
   unit a working fix and a broken fix look identical.
4. The failure mode is silent: no exception, no log entry. In the worst case a
   ventilation unit keeps running after the user pressed off.

### What to measure

1. Start intensive ventilation from Home Assistant.
2. Switch the unit off from Home Assistant.
3. Read `41120..41124` back and record the values.
4. Compare against the keypad LEDs and whether the fans actually stop.
5. Repeat with the Meltem app instead of Home Assistant in step 1, to separate
   integration behaviour from device behaviour.
6. Repeat the whole sequence with the candidate fix applied.

Tests: [W-8](LIVE_GATEWAY_TESTS.md#w-8--intensive-and-power-off),
[W-9](LIVE_GATEWAY_TESTS.md#w-9--intensive-ends-by-itself),
[H-5](LIVE_GATEWAY_TESTS.md#h-5--intensive-from-the-app),
[H-6](LIVE_GATEWAY_TESTS.md#h-6--keypad-leds-and-physical-state).

### Candidate solutions

- **A — clear on off only.** Prepend the `_CLEAR_INTENSIVE` writes to the
  `off` branch of `write_operating_mode` and to `write_level(0)`. Smallest
  change, but adds two register writes to every power-off.
- **B — clear on every direct airflow write.** Also covers manual and
  unbalanced writes. More consistent with `write_preset_mode`, which already
  clears the shadow registers for non-intensive presets. More bus traffic.
- **C — do not write, correct the decode.** Treat `intensive_active` as
  `False` whenever `operation_mode == "off"`, regardless of `41124`. Zero write
  risk, but leaves the device state itself inconsistent, which the Meltem app
  might still act on.
- **D — no change.** Correct if step 3 shows the firmware clears the registers
  itself. Record the result in `docs/DEVELOPER.md` so it is not investigated
  again.

Preference before measurement: **C** if only the Home Assistant state is wrong,
**A** if the device really keeps the override latched.

---

## HW-2 — Writes block the whole gateway for several seconds

Priority: medium
Status: open
Affected code: `coordinator.async_set_operation_mode`,
`coordinator.async_set_preset_mode`, `coordinator.async_activate_intensive`,
`coordinator.async_deactivate_intensive`

### Observation

```python
async with self._gateway_lock:                    # lock held from here
    await write_method(*write_args)               # 3-5 writes at 0.1 s gap  ~0.5 s
    await async_sleep(WRITE_SETTLE_SECONDS)       #                           1.5 s
    await self._async_refresh_room_after_write(   # read
        room,
        refresh_plan=readback_plan,
        min_refresh_attempts=refresh_attempts,    # 2: + 2.5 s + read
    )
```

The gateway lock is held for roughly five to six seconds. During that window no
poll job runs for any room, so the airflow sensors of every unit behind the
gateway can be up to six seconds stale right after a button press.

The obvious improvement is to release the lock during the pure settle sleep.

### Manufacturer documentation

The gateway reaches the units over 868.3 MHz radio. The Modbus manuals give
no timing guidance beyond the RTU frame rules. See
`docs/reference/gateway.md` and `docs/reference/modbus.md`.

### Why this cannot be fixed blind

1. The settle delay is probably not a pure wait. The gateway reaches the units
   over RF, which is why `rf_comm_status` exists at all. Letting another room's
   poll job into that window puts frames on the same RS-485 segment and the
   same RF bridge. Whether that delays propagation is a property of the
   `M-WRG-GW` firmware.
2. The apply latch described in HW-1 applies here too. If writing `41132`
   opens a commit window on the unit, it is unknown what foreign traffic in
   that window does.
3. The failure mode would be intermittent: a broken settle produces an
   occasional wrong readback, the optimistic overlay expires, and the UI jumps
   back to the previous value for a few seconds. Unit tests cannot see this.
4. It cannot be simulated. A mock client answers instantly and
   deterministically. A test can prove that the lock was released, which says
   nothing about how the gateway reacts.

### What to measure

1. Use `tools/benchmark_gateway.py` and `tools/benchmark_integration_like.py`
   to find the shortest settle delay that still yields a correct readback.
2. Run the same measurement while a second unit is polled concurrently, to see
   whether interleaved traffic changes the required delay.
3. Record how long `41121` and `41020/41021` actually need to reflect a write.
   `docs/MELTEM.md` already notes that `41020/41021` lag behind.

Tests: [W-1](LIVE_GATEWAY_TESTS.md#w-1--balanced-write-41121-readback-and-airflow-lag),
[W-2](LIVE_GATEWAY_TESTS.md#w-2--settle-delay-sweep),
[W-3](LIVE_GATEWAY_TESTS.md#w-3--settle-delay-with-interleaved-traffic),
[W-6](LIVE_GATEWAY_TESTS.md#w-6--41132-commit-latch).

### Candidate solutions

- **A — shorten `WRITE_SETTLE_SECONDS`.** If the measurement shows the unit
  settles in, say, 0.5 s, this alone removes most of the delay with no change
  to the locking model. Lowest risk, do this first.
- **B — release the lock during the settle sleep.** Largest gain, but depends
  on step 2 of the measurement showing that interleaved traffic is harmless.
- **C — reduce `refresh_attempts` from 2 to 1.** The second read exists
  because the first one was observed to be stale. If A shows the settle is
  reliable, the second read becomes unnecessary and saves ~2.5 s.
- **D — no change.** Six seconds of stale airflow readings after a manual
  action is arguably acceptable for a ventilation system.

Preference before measurement: **A**, then **C**, and only then consider **B**.

---

## HW-3 — Ambiguous encoding above `200` in the unbalanced target registers

Priority: low
Status: parked
Affected code: `modbus_client._decode_unbalanced_target_readback`

### Observation

`41121` / `41122` carry two different meanings in the same numeric range:

- app shortcut airflow, encoded as `200 + airflow / 10`
  (`203` = 30 m3/h, `205` = 50, `207` = 70 — traced, see `docs/MELTEM.md`)
- quick mode codes `227` / `228` / `229` / `230`
  (intensive / low / medium / high — also traced)

The decoder currently rejects any value whose decoded airflow exceeds the
profile maximum, which separates the two ranges for the tested profiles
(`227..230` decode to 270..300 m3/h, above both 97 and 100 m3/h).

### Manufacturer documentation

Supply-only and extract-only airflows are stored device parameters
(IDs 42-47; `M-WRG-II` defaults 50 / 0 m3/h). No raw encoding is documented.
See `docs/reference/device-parameters.md`.

### Why this is not fully settled

The separation is a heuristic that happens to work because the rated airflow of
both series is well below 200 m3/h. It is unknown whether the encoding is
really `200 + airflow / 10` for the whole range or whether the observed values
(`203`, `205`, `207`) are just three samples of something else. Only three
data points exist.

### What to measure

Configure the app `Abluft` / `Zuluft` shortcut to several airflows across the
full range (10, 20, 40, 60, 80, 90, 100 m3/h) and record `41121` / `41122` for
each. Seven data points would confirm or refute the linear encoding.

Tests: [W-11](LIVE_GATEWAY_TESTS.md#w-11--shortcut-encoding-200--n-on-the-unit),
[H-4](LIVE_GATEWAY_TESTS.md#h-4--app-shortcut-encoding).

### Candidate solutions

- **A — keep the current heuristic.** Correct for every value the tested
  profiles can produce.
- **B — explicit code table.** If the measurement shows the encoding is not
  linear, replace the arithmetic with a lookup table.
- **C — narrow the accepted window.** Accept only `200 + n` where the result is
  within the profile range *and* `n <= 20`, making the intent explicit.

---

## HW-4 — Units that reject the five-register mode block

Priority: low
Status: open
Affected code: `modbus_client._read_mode_block`,
`modbus_client._decode_intensive_active`

### Observation

`_read_mode_block` first tries to read five registers at `41120`. If that
fails, it falls back to two registers, and `_decode_intensive_active` then
returns `None`. On such units the intensive switch in Home Assistant shows
`unknown` permanently.

`docs/MELTEM.md` notes that many devices return Modbus exceptions for
`41120/41121/41122` until a write has occurred, so the fallback exists for a
reason. What is not known is whether the five-register read stays unavailable
forever on some units or only until the first write.

The `intensive` read group is no longer marked failed when the two-register
fallback succeeds, so these units do not keep `data_health` permanently on.
Intensive writes on these units are recorded as `unverifiable` rather than
`unconfirmed` for the same reason.

### Manufacturer documentation

Reading back `41120..41132` is not documented at all; the manuals only
describe writing these registers. See `docs/reference/modbus.md`.

### What to measure

1. On a freshly powered unit, attempt the five-register read at `41120` and
   record the exception.
2. Perform one airflow write.
3. Retry the five-register read.
4. Repeat after a power cycle to see whether the capability is sticky.

Tests: [R-9](LIVE_GATEWAY_TESTS.md#r-9--mode-block-before-any-write),
[W-5](LIVE_GATEWAY_TESTS.md#w-5--mode-block-after-the-first-write),
[H-2](LIVE_GATEWAY_TESTS.md#h-2--freshly-powered-unit).

### Candidate solutions

- **A — no change.** Correct if the capability returns after the first write;
  the switch is only `unknown` until the user does something.
- **B — probe once during setup.** Store the capability with the room and
  simply do not create the intensive switch on
  units that never answer.
- **C — derive intensive from `41121`.** If `41121` reads back `227` the
  override is active. Would work on units without the five-register read, but
  it conflicts with the quick mode codes in the same range (see HW-3).

---

## HW-5 — Turning a stopped unit on starts both directions

Priority: informational
Status: parked
Affected code: `coordinator.async_set_direction_level`

### Observation

Turning on a single fan while `operation_mode == "off"` writes a balanced
level, so both directions start. This is deliberate and documented in the
README, but it means the user cannot go directly from off into single-direction
operation.

### Why this is deliberate

Writing an unbalanced mode from a stopped unit was never traced. It is unknown
whether the unit accepts `41120 = 4` with one side at zero directly out of the
off state, or whether it needs to be running first.

### What to measure

From a stopped unit, write `41120 = 4`, `41121 = 0`, `41122 = <raw>`,
`41132 = 0` and check whether only the extract fan starts.

Tests: [W-12](LIVE_GATEWAY_TESTS.md#w-12--single-direction-start-from-off).

### Candidate solutions

- **A — keep the current behaviour.** Safe and documented.
- **B — allow direct single-direction start** if the measurement shows the unit
  accepts it.

---

## HW-6 — Verify the reduced read and retry pattern

Priority: medium
Status: open
Affected code: `modbus_client._read_mode_group`, `device/transport.py`,
`coordinator._job_interval`

### Observation

Three changes reduce bus time but were made without a live gateway:

1. The flow job takes `41121` / `41122` from the `41120` mode block instead of
   reading them again on their own, saving up to two requests per unit every
   10 s. The decoder already trusted the block values for quick-mode detection.
2. Every request is retried at most once (`TransportPolicy`). pymodbus'
   default `retries=3` had turned one silent register into about eight
   timeouts. This is measured as part of HW-7.
3. A unit that has been silent for `ROOM_SILENT_AFTER_SECONDS` is polled at
   most every `SILENT_ROOM_POLL_SECONDS`.

### What to measure

1. Compare `41121` / `41122` from the five-register block with single-register
   reads after balanced, unbalanced, preset, and app-shortcut writes.
2. Run the integration for a day with all units online and check the logs for
   new transient read failures compared to the previous release.
3. Power off one unit and confirm that the others still react to fan changes
   within a few seconds.

Tests: measurement 1 in [R-10](LIVE_GATEWAY_TESTS.md#r-10--mode-block-equals-single-reads)
and [W-10](LIVE_GATEWAY_TESTS.md#w-10--unbalanced-write-and-readback),
measurement 2 in [T-10](LIVE_GATEWAY_TESTS.md#t-10--24-hour-soak) and
[H-9](LIVE_GATEWAY_TESTS.md#h-9--real-home-assistant-instance),
measurement 3 in [T-5](LIVE_GATEWAY_TESTS.md#t-5--silent-address-in-the-integration-client)
and [H-1](LIVE_GATEWAY_TESTS.md#h-1--powered-off-unit).

### Candidate solutions

- **A — keep the changes** if all three measurements are clean.
- **B — a second retry** in `TransportPolicy` if step 2 shows more transient
  failures.
- **C — restore the single reads** if step 1 shows differing values.

---

## HW-7 — Validate the tmodbus transport on the real gateway

Priority: high (blocks the `4.0.0` release)
Status: open
Affected code: `device/transport.py`, `device/components.py`,
`modbus_helpers.prepare_unit`, `modbus_client._poll`,
`modbus_client._read_mode_component`

### Observation

`4.0.0` replaces the own pymodbus client with Home Assistant's shared Modbus
connection (tmodbus via `modbus-connection`). The request shapes and their
order are unchanged and covered by tests against an in-memory gateway, but
every timing and error finding in this backlog was measured with pymodbus.

Two behaviors found in review depend on how the gateway answers and are left
as they are until measured:

- **Code 10/11 for a powered-off unit.** A silent unit ends its job after the
  first timeout. If the gateway answers code 10/11 instead, the job goes on:
  in the in-memory gateway a full read costs 28 requests and a flow job 8,
  each retried once, and the mode reads go into backoff.
- **Busy (code 6).** tmodbus retries it internally for up to 60 s with
  growing waits, while the link and `_gateway_lock` stay held, so UI writes
  wait as well. `modbus-connection` does not make this configurable.

### Why this cannot be decided blind

1. tmodbus frames, waits, and times out differently from pymodbus; the gateway
   is known to be sensitive to pacing.
2. Whether a powered-off unit shows up as a timeout or as a gateway exception
   (code 10/11) decides whether the per-job skip applies. Skipping on code
   10/11 blind would also end jobs on a single RF hiccup the gateway reports
   that way.
3. Capping a job with a timeout cancels a busy request in the middle of
   tmodbus' retry; whether that leaves the gateway in a clean state is
   unknown.
4. `TRANSPORT_LINK_QUIET_SECONDS` (10 s) is a guess: long enough that a
   working link always gets an answer from some unit in that time, short
   enough that a wedged link is recycled quickly.

### What to measure

Each measurement is compared with a baseline taken with the pymodbus tools
from before `4.0.0`
([P-5](LIVE_GATEWAY_TESTS.md#p-5--baseline-worktree),
[T-1](LIVE_GATEWAY_TESTS.md#t-1--baseline-with-the-pymodbus-tools)):

1. Latency and timeout rate per scenario, compared with the baseline.
2. Power off one unit: timeout or code 10/11, requests per job, and whether
   `link_recycles` in the diagnostics stays at 0 while other units answer.
3. A freshly powered unit: the fallback from five to two mode registers (HW-4).
4. Write confirmation via `41121` and the settle time after writes (HW-2).
5. Whether the gateway ever answers with code 6, and for how long.
6. Unplug and replug the USB cable; reload and unload the entry: the port is
   released and the link comes back.
7. 24 hours of continuous operation with all units; note `link_recycles` and
   the info-level "Recycling the Meltem gateway link" log lines.

These seven measurements are the release gate for `4.0.0`; the tests per
measurement are listed under
[Release gate](LIVE_GATEWAY_TESTS.md#release-gate-for-400).

### Candidate solutions

- **A — release** if all measurements match the baseline.
- **B — tune** `REQUEST_GAP_SECONDS`, `FIXED_TIMEOUT`,
  `TRANSPORT_DISCONNECT_AFTER_TIMEOUTS`, or `TRANSPORT_LINK_QUIET_SECONDS`
  from the measurements.
- **C — skip the rest of a job on code 10/11** like on a timeout, in
  `MeltemModbusClient._poll` and `_read_mode_component`, and keep code 10/11
  out of the mode backoff, if a powered-off unit answers that way.
- **D — cap jobs and writes** with `asyncio.timeout` in the coordinator if the
  gateway sends code 6 and the 60 s retry blocks the UI.
