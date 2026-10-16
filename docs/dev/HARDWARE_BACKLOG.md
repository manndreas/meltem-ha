# Hardware Verification Backlog

Findings from code review that remain unresolved or need live validation on an
`M-WRG-GW` gateway and at least one `M-WRG` unit. Each entry states what was
observed in the code, why a blind fix would be irresponsible, what to measure,
and which candidate solutions exist. How to measure is in
[LIVE_GATEWAY_TESTS.md](LIVE_GATEWAY_TESTS.md); every entry names its tests
there.

Every live run for an item is written up in detail here, in a dated subsection
of that item. Other documents only summarise it and link here; the behavior a
run establishes goes as a short finding into `docs/dev/MELTEM.md`.

Related documents:

- `docs/dev/reference/` — manufacturer reference extracted from the Meltem documents
- `docs/dev/MELTEM.md` — observed register behaviour and traced register writes
- `docs/dev/DEVELOPER.md` — implementation notes and the decisions behind them
- `docs/dev/SETTING_RE_BACKLOG.md` — app-side settings reverse engineering
- `docs/dev/TODO.md` — remaining non-hardware work
- `docs/dev/LIVE_GATEWAY_TESTS.md` — test plan for an AI agent on the live gateway,
  covering every item below

Status legend: `open` (needs measurement), `in progress` (code changed, live
validation pending), `parked` (measured, decision deferred), `resolved` (move
the decision into `docs/dev/DEVELOPER.md` and the measured behaviour into
`docs/dev/MELTEM.md`).

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
`docs/dev/reference/functions.md` and `docs/dev/reference/device-parameters.md`.

### Why this cannot be fixed blind

1. It is unknown whether the firmware clears the registers itself. The override
   also ends on its own after a runtime configured in the Meltem app, so
   self-clearing is plausible. If it self-clears, an extra write is pointless
   bus traffic; if it does not, the current behaviour is a real bug.
2. `docs/dev/reference/modbus.md` states that `41132` must always be written last and that
   the unit accepts `41120..41132` only once `41132` has been written. That
   reads like a commit latch. A combined transaction
   (`41123 = 0`, `41124 = 0`, `41120 = 1`, `41121 = 0`, `41132 = 0`) has never
   been traced, so it is unknown whether the unit applies all of it, whether
   the last written mode wins, or whether clearing the shadow registers in the
   same commit cancels the off command.
3. The old reader depended on `41120..41124` to observe the intensive state,
   and could not distinguish a correct off transition when that set-register
   block returned an exception. The read-only status map at `41100..41102`
   now provides that observation without changing the write sequence.
4. The failure mode is silent: no exception, no log entry. In the worst case a
   ventilation unit keeps running after the user pressed off.

### What to measure

1. Start intensive ventilation from Home Assistant.
2. Switch the unit off from Home Assistant.
3. Read `41100..41102` for current mode state; capture `41123/41124` only as
   separate diagnostics, not as the source for the client decode.
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
  itself. Record the result in `docs/dev/DEVELOPER.md` so it is not investigated
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

### Observation (HW-2)

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

### Manufacturer documentation (HW-2)

The gateway reaches the units over 868.3 MHz radio. The Modbus manuals give
no timing guidance beyond the RTU frame rules. See
`docs/dev/reference/gateway.md` and `docs/dev/reference/modbus.md`.

### Why this cannot be fixed blind (HW-2)

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

### What to measure (HW-2)

1. Use `tools/benchmark_gateway.py` and `tools/benchmark_integration_like.py`
   to find the shortest settle delay that still yields a correct readback.
2. Run the same measurement while a second unit is polled concurrently, to see
   whether interleaved traffic changes the required delay.
3. Record how long current status `41101` and measured airflow `41020/41021`
   need to reflect a write. `docs/dev/MELTEM.md` already notes that the measured
   airflow can lag behind.

Tests: [W-1](LIVE_GATEWAY_TESTS.md#w-1--balanced-write-status-readback-and-airflow-lag),
[W-2](LIVE_GATEWAY_TESTS.md#w-2--settle-delay-sweep),
[W-3](LIVE_GATEWAY_TESTS.md#w-3--settle-delay-with-interleaved-traffic),
[W-6](LIVE_GATEWAY_TESTS.md#w-6--41132-commit-latch).

### Partial measurement on 2026-10-02

On slave `4`, the measured baseline was `20/20 m3/h`. W-1 was run at target
airflows `16`, `24`, and `40 m3/h`, observing each for 60 seconds. The written
raw targets (`41121=32`, `48`, and `80`) appeared in the first post-write
sample, about one second after each write; each sample read itself took about
0.48 seconds. At 16 and 24, measured airflow stayed at `20/20` throughout the
observation and did not reach either target within 60 seconds. At 40, the
measured-airflow block changed from `20/20` to `40/40` by the first sample and
stayed there for the 60-second observation. All three runs restored raw
target `40`; the final readback and integration state were manual balanced
`20/20`. No request failures occurred. This tested the set-side `41121`
readback; it did not record status register `41101`, so it does not validate
the current read path.

The measured response thus differs by target delta: the 20 m3/h change took
effect by the first sample, but changes of only 4 m3/h in either direction
were not reflected in measured airflow within 60 seconds. This does not
establish the shortest reliable settle delay for other target values.

W-2 then tested target `30 m3/h` from the same `20/20` baseline at configured
settle delays of `0`, `0.5`, `1.0`, and `1.5 s`, three repetitions each. All
12 writes, first post-write target polls, restore writes, and post-restore
polls succeeded. The first post-write poll reported target `30` even at
`0 s` settle. The direct measured-airflow block immediately after each write
was still `20/20`; W-2 therefore confirms immediate target readback, not that
the physical airflow had already changed. A zero settle supports removing the
second target refresh attempt in this test path, but the correct physical-flow
settle delay remains open.

W-3 then ran all 12 target-30/restore-20 trials while a second task read
`41020..41021` from slave `3` every `0.5 s`. All 12 first target polls and
restores succeeded, and all 30 concurrent reads succeeded with values
`[30, 30]`; there were no timeouts or link recycles. In idle, five reads on
slave `3` took `15.4..16.7 ms` (average `16.2 ms`). During W-3, concurrent
reads took up to `134.4 ms`, and the primary target poll was about `116 ms`
slower than the equivalent W-2 run. This is a noticeable latency increase
without failures; the interleaved-traffic test is not a clean pass for
releasing the gateway lock during settle.

W-6 staged target `40 m3/h` (`41120=3`, `41121=80`) without writing the apply
register. Measured airflow stayed at `20/20` for the full 20-second
pre-commit period. After writing `41132=0`, airflow was still `20/20` at the
first approximately two-second sample and reached `40/40` by the next sample,
within about five seconds of the commit. Restoring target `20` with a final
commit returned measured airflow to `20/20` by the next approximately
two-second sample after it remained at 40 at one second. Status/error flags
stayed `0`.

### Follow-up W-1 on 2026-10-14

On slave `4` (M-WRG-II, software `2326`, integration `4.1.1`), the measured
baseline was `60/60 m3/h` with status `[3, 230, 0]` (HIGH). The pre-test
snapshot could not read `41120..41124` or `41132`. W-1 wrote a balanced target
of `40 m3/h` (`41121=80`) and observed for 60 seconds at one-second intervals.
The current-state map returned `[3, 204, 0]` throughout; `41101=204`, not the
written raw target `80`. The measured airflow stayed at `60/60` for the first
five observations, reached `40/40` by observation six (about six seconds), and
stayed there through observation 60. All 62 timed operations/snapshots
succeeded; mean duration was `587.3 ms`, p95 `595.8 ms`.

The helper restored balanced `60/60` with raw target `41121=120`; status was
`[3, 206, 0]`. The explicitly approved final restore wrote HIGH
(`41120=3`, `41121=230`, `41132=0`). Immediately and two minutes later, status
was `[3, 230, 0]`, measured airflow was `60/60`, and `41016=0`. After the first
write, `41120×2` read `[3, 230]`, while `41120×5` still returned exception
`0x05`. Registers `42001..42009` also became readable although none was written;
the corresponding pre-test values were unreadable. Shadow-register values
changed as well, and their cause is not established.

This W-1 disproved the assumption that `41101` echoes the raw balanced target:
the observed values `204` and `206` fit `200 + airflow / 10` for targets 40
and 60. The client now decodes this app encoding for balanced status when the
result is within the profile maximum; preset codes `228..230` remain distinct.
This run used the integration-like client rather than the live Home Assistant
coordinator; the coordinator confirmation was validated separately in
[W-17](#w-17-home-assistant-write-confirmation-on-2026-10-14).

### W-17 Home Assistant write confirmation on 2026-10-14

Real Home Assistant `2026.10.0` drove slave `4` (M-WRG-II, software `2326`,
mapped as unit 3), starting from HIGH at measured `60/60 m3/h` with status
`[3,230,0]`.

- **First attempt, mismatch:** the supply `63%` write was confirmed at
  `63/60`. After at least 10 seconds, the extract `63%` write remained
  `mismatch` for more than 60 seconds (`expected=63/63`, `actual=63/60`,
  `last_error=null`). With HA stopped, raw reads returned
  `41100..41102=[3,126,0]`, `41120=3`, `41121=126`, `41122=120`, `41132=0`,
  `41016=0`, and measured airflow `60/60`. The stored `41121` target was
  therefore `63 m3/h`, but these values did not show what the coordinator had
  read when it reported the mismatch. The approved HIGH restore (`41120=3`,
  `41121=230`, `41132=0`) was verified at status `[3,230,0]`, `41122=120`,
  `41016=0`, and `60/60`.
- **Cause and fix:** offline tracing found the cause in the retry gate:
  `mismatch` remained eligible for recovery in `confirm()`, but
  `needs_confirmation()` excluded it, so no later exact `41121` read was
  made. The coordinator now continues exact reads for mismatched writes; a
  regression test covers an initial unbalanced `63/60` read followed by a
  manual-mode exact `63` readback.
- **Retest, pass:** again from HIGH, measured `60/60`, and fresh read-health
  groups, supply `63%` was confirmed at `63/60`; after at least 10 seconds,
  extract `63%` was confirmed at `63/63` with `last_error=null`. With HA
  stopped, raw reads returned the same values as after the first attempt. The
  measured airflow had not yet converged, which is a separate settle-time
  observation (see below). The approved HIGH restore was verified at
  `[3,230,0]`, `41121=230`, `41122=120`, `41132=0`, `41016=0`, and `60/60`.
  The live mapping was `63% -> 63 m3/h`, not the previously assumed
  `64 m3/h`. Local captures, not archived:
  `all_known-slave4-20261014-145009-W17-confirmed-before-restore.json` and
  `all_known-slave4-20261014-145023-W17-confirmed-restored-baseline.json`.

This validates the coordinator's balanced-write confirmation on the tested
M-WRG-II, not on M-WRG-S.

### Follow-up airflow lag through Home Assistant on 2026-10-14

Five controlled cycles on slave `4` started from fresh HIGH at measured
`60/60 m3/h`: supply fan `40%`, after more than 10 seconds extract fan `40%`,
then the HIGH preset. Measured airflow comes from HA's flow-group reads about
every 10 seconds, so the times below are polling bounds, not physical response
times.

- **Cycle 1:** HA confirmed supply `[40,60]`; measured supply was still `60`
  at about 5 s and `40` by 11.4 s, while extract stayed at `60`. HA confirmed
  extract `[40,40]`; measured airflow was still `40/60` at about 5 s and
  `40/40` by 9.2 s. HIGH was confirmed at the preset-register readback, but
  measured airflow stayed `40/40` through about 16 s and read `60/60` by 17 s
  and on the following fresh read. The read-health groups were fresh and both
  write statuses were confirmed.
- **Cycle 2:** measured `40/60` and then `40/40` within 9.1 s and 7.9 s of the
  respective write starts. After the confirmed HIGH preset, fresh reads still
  showed `40/40` at 27 s and showed `60/60` by 47.5 s and on the next fresh
  poll. With HA stopped, single-register reads verified status `[3,230,0]`,
  `41120=3`, `41121=230`, `41122=120`, `41123=0`, `41124=0`, `41132=0`,
  `41016=0`, and measured `60/60`. Local capture, not archived:
  `all_known-slave4-20261014-151659-HA-airflow-lag-HIGH-restored.json`.
- **Cycle 3:** supply airflow reached `40` by 12.7 s. The first read after
  the extract write mismatched at `[40,60]`; the next fresh flow poll
  confirmed `[40,40]` and measured `40/40` about 12 s after that write. HIGH
  was confirmed at `13:18:43.476 UTC`; a flow read at `13:18:45.262`, about
  1.8 s later, already showed `60/60`, and the next fresh poll at
  `13:18:53.116` still did.
- **Cycle 4:** the supply and extract `40%` service calls returned at
  `13:42:18.746` and `13:42:19.097 UTC`; these are service completion times,
  not readback confirmations. The fresh flow read at `13:42:23.672` still
  showed `60/60` and a `[40,60]` mismatch; the next at `13:42:34.170` showed
  `40/40` and confirmed `[40,40]`. The HIGH request started at
  `13:42:41.104`; its readback at `13:42:46.164` confirmed HIGH while flow was
  still `40/40`, and the next read at `13:42:48.262` showed `60/60`, about
  2.1 s later. Reads were fresh and error-free throughout.
- **Cycle 5:** the two `40%` service calls returned at `13:46:21.847` and
  `13:46:22.311 UTC`. The fresh read at `13:46:26.560` confirmed target
  `[40,40]` while airflow was still `60/60`; the next at `13:46:37.054`
  measured `40/40`. HIGH was requested at `13:46:37.373`; its readback at
  `13:46:42.426` confirmed HIGH with airflow still `40/40`, and the next read
  at `13:46:47.594` showed `60/60`, about 5.2 s later. Reads were fresh and
  error-free.

Target confirmation always came before measured convergence, and the
service-call response, the register readback, and the measured airflow are
distinct events. The observed return to HIGH ranged from under 2 s through
about 2.1 s, 5.2 s, and 16–17 s to 27–47.5 s, so there is no single settle
interval. These are bounds from one M-WRG-II unit and HA's poll cadence, not
a response-time guarantee. Do not change timing from these five cycles;
gather more controlled samples first.

### One-second raw airflow polls on 2026-10-14

To separate physical response from HA's 10-second flow polling, HA was stopped
and slave `4` was polled directly once per second after each raw write sequence.
From verified HIGH, `41120=3`, `41121=80`, `41132=0` produced `60/60` for
polls 1–6 and `40/40` at poll 7; `40/40` held through all 60 polls. Restoring
`41120=3`, `41121=230`, `41132=0` returned `60/60` already at poll 1 and held
through all 60 polls. All 120 reads succeeded. Final single-register reads
verified `[3,230,0]`, `41120=3`, `41121=230`, `41122=120`, `41123=0`,
`41124=0`, `41132=0`, `41016=0`, and `60/60`. Local capture, not archived:
`all_known-slave4-20261014-152255-W17-HA-airflow-lag-raw-timing-HIGH-restored.json`.

This uses direct current-level register writes, not HA's quick-mode service.
It demonstrates that the 10-second HA poll cadence masks the physical
transition time, but does not explain the wide spread in measured return times
after HA's preset service. Keep timing changes parked until that path has more
controlled repetitions.

### Raw replay of the HA HIGH preset sequence

The HA `write_preset_mode("high")` register order was replayed directly after
measured airflow had reached `40/40`: clear preset mode/value
(`41123=0`, `41124=0`), write mode/current target (`41120=3`, `41121=230`),
then apply (`41132=0`). The first one-second airflow poll still read `40/40`;
poll 2 read `60/60`, which held through poll 60. All 60 reads succeeded. The
manual `40 m3/h` setup before this replay reached measured `40/40` by poll 3.
Final readback was `[3,230,0]`, `41120..41124=[3,230,120,0,0]`,
`41132=0`, and measured `60/60`. This is the same register order over the
raw tool transport, not the HA tmodbus transport; it does not explain the
slower HA observations. Local capture, not archived:
`all_known-slave4-20261014-152627-W17-HA-preset-sequence-raw-HIGH-restored.json`.

### Six-slave read load during raw airflow observation

This run defined test
[W-18](LIVE_GATEWAY_TESTS.md#w-18--measured-airflow-under-the-full-polling-load).
With HA stopped and a fresh all-known snapshot saved, one serial client ran
the integration's real `build_jobs` schedule for all six configured slaves,
at the same 4 requests/s limit and 0.1 s request spacing. An additional raw
`41020..41021` read sampled slave 4 about once per second. The manual 40 m3/h
target was first measured at `14:01:59.134 UTC`, 6.9 s after the write call
returned. After the HIGH preset write returned at `14:02:04.062`, the first
raw `60/60` sample arrived at `14:02:09.134` (5.1 s later). Over the
60-second HIGH observation, four initial samples were `40/40`, then 53 were
`60/60`; the maximum sample gap was 1.8 s. All scheduled reads succeeded;
transport diagnostics showed zero timeouts and zero link recycles. The
before/after all-known captures differed only in shadow/meta counters, with
the unit's runtime and documented settings restored. This one loaded run did
not reproduce the long HA return outlier; it does not exclude an intermittent
delay. Local raw log and captures, not archived:
`w18-six-slave-interleaved-airflow.jsonl`,
`all_known-slave4-20261014-155722-before-W18-six-slave-load.json`, and
`all_known-slave4-20261014-160332-after-W18-six-slave-load.json`.

### Candidate solutions (HW-2)

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

### Observation (HW-3)

`41121` / `41122` carry two different meanings in the same numeric range:

- app shortcut airflow, encoded as `200 + airflow / 10`
  (`203` = 30 m3/h, `205` = 50, `207` = 70 — traced, see `docs/dev/MELTEM.md`)
- quick mode codes `227` / `228` / `229` / `230`
  (intensive / low / medium / high — also traced)

The decoder currently rejects any value whose decoded airflow exceeds the
profile maximum, which separates the two ranges for the tested profiles
(`227..230` decode to 270..300 m3/h, above both 97 and 100 m3/h).

The same range appears in the read-only status map `41100..41102`, which the
integration decodes with the same rules. On 2026-10-09, slave `4` reported
`[4, 0, 208]` in one read and `[4, 0, 228]` in a later identity comparison
([raw read](measurements/2026-10-09/pe-compare-slave4.txt)). `208` decodes
to an 80 m3/h extract shortcut. `228` decodes to 280 m3/h, so the extract
target becomes unknown while the quick mode is reported as `extract_only`.
Whether `228` is the LOW code applied to the extract direction, another app
encoding, or something else is unknown; the measured airflow and app state at
that time were not recorded.

### Manufacturer documentation (HW-3)

Supply-only and extract-only airflows are stored device parameters
(IDs 42-47; `M-WRG-II` defaults 50 / 0 m3/h). No raw encoding is documented.
See `docs/dev/reference/device-parameters.md`.

### Why this is not fully settled

The separation is a heuristic that happens to work because the rated airflow of
both series is well below 200 m3/h. It is unknown whether the encoding is
really `200 + airflow / 10` for the whole range or whether the observed values
(`203`, `205`, `207`) are just three samples of something else. Only three
data points exist.

### What to measure (HW-3)

Configure the app `Abluft` / `Zuluft` shortcut to several airflows across the
full range (10, 20, 40, 60, 80, 90, 100 m3/h) and record `41121` / `41122` for
each. Seven data points would confirm or refute the linear encoding. Record
`41100..41102` and `41020/41021` alongside, and capture the app state whenever
an unbalanced status slot reports a value above `210`, such as `228`.

Tests: [W-11](LIVE_GATEWAY_TESTS.md#w-11--shortcut-encoding-200--n-on-the-unit),
[H-4](LIVE_GATEWAY_TESTS.md#h-4--app-shortcut-encoding).

### Candidate solutions (HW-3)

- **A — keep the current heuristic.** Correct for every value the tested
  profiles can produce.
- **B — explicit code table.** If the measurement shows the encoding is not
  linear, replace the arithmetic with a lookup table.
- **C — narrow the accepted window.** Accept only `200 + n` where the result is
  within the profile range *and* `n <= 20`, making the intent explicit.

---

## HW-4 — Units that reject the five-register mode block

Priority: low
Status: in progress
Affected code: `const.MODE_STATUS_SENSOR_MODE_TO_RAW_VALUE`,
`device/components.ModeStatus`, `modbus_client._read_mode_block`

### Prior behavior

The old `_read_mode_block` read five set registers at `41120`; on failure it
fell back to two, then sometimes to single set-register reads. If those reads
were refused, the intensive state stayed unknown and the mode health group
could remain stale.

### Current implementation

All supported M-WRG-S and M-WRG-II profiles now read current state from
`41100..41102`; `41120..41124` are retained for writes only. This removes the
runtime set-register fallback. Applying the same map to M-WRG-S follows the
user's explicit assumption; direct live evidence is currently from M-WRG-II
only. Home Assistant deployment and M-WRG-S hardware validation remain
pending.

### Manufacturer documentation (HW-4)

Reading back `41120..41132` is not documented; the manuals only describe
writing these registers. The `41100..41102` status map is community/live
evidence, not a manufacturer guarantee. See `docs/dev/reference/modbus.md` and
the observations below.

### What to measure (HW-4)

1. On a freshly powered unit, attempt the five-register read at `41120` and
   record the exception.
2. Perform one airflow write.
3. Retry the five-register read.
4. Repeat after a power cycle to see whether the capability is sticky.

Tests: [R-9](LIVE_GATEWAY_TESTS.md#r-9--mode-block-before-any-write),
[W-5](LIVE_GATEWAY_TESTS.md#w-5--mode-block-after-the-first-write),
[H-2](LIVE_GATEWAY_TESTS.md#h-2--freshly-powered-unit),
[R-12](LIVE_GATEWAY_TESTS.md#r-12--read-only-mode-status-map).

### Measurements on 2026-10-02

Before any write in the session, all six discovered units returned
`AcknowledgeError` (Modbus exception `0x05`) for the five-register and
two-register reads at `41120`, and for single reads at `41121` / `41122`.
They were all detected as `ii_plain`; five reported software `2326` and slave
`5` reported `2584`. Their write and power-cycle history before this session is
unknown, so this does not establish the freshly-powered behavior or decide
whether a write unlocks the block.

Slave `4` was then power-cycled for about five minutes. Immediately after
power-up, the five-register read, two-register read, and single reads at
`41121` / `41122` still returned exception code `0x05`. The unit therefore
does not become readable from a power cycle alone. Whether a first write
unlocks it remained unknown at that point. The airflow registers
`41020..41021` answered on the first post-power-up read and on five
consecutive samples with `20/20 m3/h`, matching the Meltem app; five more
minutes of polling changed neither the reading nor produced transport
timeouts. No Modbus writes were made up to this point.

After an explicitly authorized write of `41120=3`, `41121=40`, and
`41132=0`, the two-register read at `41120` became readable as `[3, 40]` and
the single read at `41121` returned `40`. The five-register read at `41120`
and single read at `41122` continued to return code `0x05` at 1, 10, and 60
seconds. The integration decoded manual balanced operation at `20/20 m3/h`.
This supports the short-read fallback for the basic mode and target, but
leaves intensive state unavailable on this unit. Whether the five-register
read is permanently unsupported, or behaves differently after another power
cycle, remains open. A second five-minute power cycle after W-5 left the
two-register read `[3, 40]` and single read `41121=40` available; the
five-register read and `41122` still returned code `0x05`. The integration
continued to decode manual balanced operation at `20/20 m3/h`. On this unit,
short-read availability and manual airflow readback therefore persisted
through one power cycle, while the intensive block stayed unavailable.

#### Controlled W-5 follow-up on 2026-10-09

After moving the gateway to the test computer, the first read of slave `4`'s
airflow registers also returned `0x05`. After about eight minutes, the read
returned `[80, 0]` (`41020` extract, `41021` supply) and `41016=0`, while all
R-9 mode reads still returned `0x05`.

Since `41021` was zero, the W-5 procedure's suggested source could not safely
be used as a balanced target. With explicit approval, the test used
`41120=3`, `41121=40`, `41132=0` (manual balanced `20/20 m3/h`). At 1, 10,
and 60 seconds, the short read returned `[3, 40]` and `41121` returned `40`;
airflow remained `20/20 m3/h` and error register `41016` remained `0`. The
five-register read at `41120` and single read at `41122` continued returning
exception `0x05`. This confirms that the write unlocked the short basic-mode
and target read, not the five-register block or `41122`.

The unit was restored to the pre-test extract/supply airflow with
`41120=4`, `41121=0`, `41122=160`, `41132=0`. After two minutes,
`41020..41021` returned `[80, 0]`, `41016=0`, the short mode read returned
`[4, 0]`, `41121=0`, and `41122=160`; the five-register read still returned
`0x05`. An initial restore used the opposite supply/extract order and briefly
produced `[0, 80]`; the direction was corrected and the final `[80, 0]`
readback verified. The before/after all-known snapshots show changes in
`511xx` and `52008..52010`. These shadow values were not written or restored;
their cause remains undetermined.

A read-only function-`0x04` probe on slave `4` returned exception `0x01`
for `41120×5`, `41120×2`, `41121`, and `41122`. The known airflow block
`41020×2` also returned `0x01` using function `0x04`; no alternate
input-register map was found on this unit. Single-register FC03 reads of
`41120×1` returned `0x05` on all six units, so reducing the read length did
not find a read-only unlock path.

The ioBroker community template identifies `41100..41102` as polled
read-only mode/supply/extract status. A direct FC03 read of `41100×3`
succeeded on all six slaves without writes, with values that match the
observed preset and unbalanced states; the values and later checks are in
[MELTEM.md](MELTEM.md#read-only-mode-status-at-4110041102). This status map is
what the integration now reads, see
[Current implementation](#current-implementation).

A cross-check against [pyairios](https://github.com/scabrero/pyairios) found
no generic login or read-unlock sequence. Its
[BRDG-02EM23 class](https://github.com/scabrero/pyairios/blob/main/src/pyairios/models/brdg_02em23.py)
is a bridge wrapper; the library lists other RF controllers, not Meltem
M-WRG-II. Its only `41120` model is for the different
[VMD-07RPS13](https://github.com/scabrero/pyairios/blob/main/src/pyairios/models/vmd_07rps13.py)
and marks that register READ/WRITE. The
[client](https://github.com/scabrero/pyairios/blob/main/src/pyairios/client.py)
treats Modbus exception `0x05` as Acknowledge; it has no unlock exchange.
This is not evidence that the Meltem node should behave the same way.

The person confirmed the same BRDG-02EM23 is connected to Ethernet and USB.
HA's `tmodbus` log and the host USB descriptor show an RTU serial interface,
although the collected bridge manual documents micro-USB only for power.
`pyairios` describes BRDG-02EM23 as Modbus-TCP and BRDG-02R13 as serial
Modbus-RTU, so its TCP bridge path is not the same transport as the observed
USB serial path. A [community setup guide](https://github.com/baked95/siber-vmc-ha/blob/main/docs/BRIDGE_SETUP.md)
reports that local TCP port `502` is closed in cloud mode and enabling local
Modbus-TCP requires factory reset/rebinding without cloud configuration,
which disables the mobile-app connection. This is community evidence, not an
official vendor procedure; do not switch modes or reset the bridge as an
unlock experiment.

T-10 provided a separate, uncontrolled clue on slave `5` (firmware `2584`).
No Modbus writes were made, but the user reported operating its wall button
several times; the times and actions were not recorded. Its airflow jobs
reported the mode-register code `0x05` for 6,228 rounds, then succeeded for
2,412 rounds. A post-soak R-9 read returned the full block
`41120..41124 = [0, 0, 0, 3, 227]`; the integration decoded
`intensive_active=True`, and `41020..41021` read `75/75 m3/h`. Because the
button actions were not timestamped or controlled, this is a correlation, not
evidence that a wall-button action unlocked the block or caused the intensive
state. Repeat this observation under controlled conditions before changing
HW-4 behavior.

### H-2 read-only follow-up on 2026-10-15

On slave `4`, the pre-cycle `41100×3` read returned `[3, 230, 0]`; the
`41120×5`, `41120×2`, `41121`, and `41122` reads returned exception `0x05`.
The airflow read `41020×2` returned `[60, 60]`. After the user-reported
power-off/on at 00:45/00:46 CEST (minute precision), a read-only observer ran
120 R-9 rounds at five-second intervals. `41100×3` succeeded in all 120
rounds with `[3, 230, 0]`; every five-register, two-register, and
single-register set-side read returned `0x05`. The post-cycle airflow read
remained `[60, 60]`.

The power cycle alone did not make the set-side mode block readable. At the
end of this read-only phase, no writes had been made: the pre-test set-side
mode was unreadable and an explicit restore target and sequence had not yet
been established. W-5 and the second post-write power cycle were subsequently
performed as described below.
See the [H-2 result](LIVE_GATEWAY_TESTS.md#h-2--freshly-powered-unit).

### H-2 W-5 follow-up on 2026-10-15

The fresh pre-write snapshot at 00:59 CEST still had the set-side mode
registers unreadable. The independent status read was `[3, 230, 0]`, airflow
was `[60, 60]`, and error register `41016` was `0`. With the approved
balanced target, the sequence `41120=3`, `41121=120`, `41132=0` was written
at 00:59:51 CEST. The first R-9 follow-up landed at 01:00:13, about 21 s
after the write: status was `[3, 206, 0]`, `41120×2` returned `[3, 120]`,
and `41121` returned `120`. The five-register read and `41122` still
returned exception `0x05`. The 60 s sample at 01:00:52 repeated those
results. The planned 1 s and 10 s samples were not captured, so this run
does not establish the unlock's exact onset time. Airflow remained
`[60, 60]` and `41016` remained `0`.

Slave `4` was restored to its prior HIGH state with
`41120=3`, `41121=230`, `41132=0`. The post-restore status was `[3, 230, 0]`,
the short read `[3, 230]`, and `41121=230`; `41120×5` and `41122` remained
unreadable. After the two-minute settling interval, airflow was still
`[60, 60]` and the error register was `0`. The after-snapshot showed
`41120=3`, `41121=230`, and `41132=0` where the before-snapshot had `null`;
the documented-config registers were unchanged.

The all-known comparison also recorded these shadow/meta changes:
`51113: 4365->4352`; `51120`, `51121`, and `51132`: `31->24832`;
`51122..51131`, `51133`, `51150`, `51151`, and `52010`: `31->32`;
`52008: 5381->4352`; `52009: 5381->5377`. They are recorded as observed
write-associated changes, not assigned a semantic meaning here. W-5 showed
that the write enables the two-register and single-target reads on this unit,
but not the five-register block or `41122`. The second post-write power cycle
to check whether the short-read availability is sticky is described below.

The first follow-up observer ran 120 read-only R-9 rounds from 01:08:15 to
01:18:11 CEST, but no power cycle occurred in that window. A repeat observer
ran 120 rounds at five-second intervals from 01:23:20 to 01:33:16 CEST; the
user reported Slave `4` off/on at 01:25/01:26 CEST (minute precision). In all
120 rounds, `41100×3` returned `[3, 230, 0]`, `41120×2` returned `[3, 230]`,
and `41121` returned `230`; `41120×5` and `41122` returned exception `0x05`.
The immediate post-observer read at 01:33:31 showed the same values, with
airflow `[60, 60]` and error `0`.

At the gateway-observed level, the short-read availability survived the
reported power cycle, while the five-register block and `41122` remained
unreadable. The unchanged replies while the unit was off are compatible with
gateway caching, so this does not prove the unit itself served every read.
The cycle therefore supports, but does not conclusively establish, sticky
short-read availability after power loss.

### Remaining validation

- **HA deployment/read health:** verified on 2026-10-14 with isolated HA Core
  `2026.10.0` and all six configured units. Slave `4`'s `flow_control` and
  `intensive` groups were fresh without a mode change.
- **Status-block/single-register parity (R-12):** matched on all six
  M-WRG-II units on 2026-10-14, see
  [MELTEM.md](MELTEM.md#read-only-mode-status-at-4110041102).
- Validate the assumed same register map on an M-WRG-S unit when available.
  Since `4.1.0` there is no fallback to the set registers: a unit that refuses
  `41100` keeps `flow_control` and `intensive` stale and its mode, quick mode,
  and intensive state unknown.
- **HA coordinator write confirmation (W-17):** validated on the tested
  M-WRG-II after a coordinator fix; the first run had ended in a mismatch.
  M-WRG-S is still open. See
  [W-17 under HW-2](#w-17-home-assistant-write-confirmation-on-2026-10-14).
- **Sensor-mode subcodes:** the read-side codes `48` (automatic), `112`
  (humidity control), and `176` (CO2 control) come only from the ioBroker
  script's decode; none of the six units was in sensor control during the
  captures. They fit read value = write value with bit `32` set (`16 -> 48`,
  `144 -> 176`; `112` already has it). The integration also maps `144` to CO2
  control as an unverified fallback that ioBroker does not list. Unknown
  subcodes decode to an unknown operating mode without a log message, and
  diagnostics only contain the decoded state. Verify with
  [W-13](LIVE_GATEWAY_TESTS.md#w-13--sensor-control-modes) on units with the
  matching sensors.
- **Intensive status:** `0/0` was observed once for an app-started override
  on slave `4`; `3/227` is only the community decode. Confirm the status for
  an override started by the integration's own `41123/41124` write in W-8.

---

## HW-5 — Turning a stopped unit on starts both directions

Priority: informational
Status: parked
Affected code: `coordinator.async_set_direction_level`

### Observation (HW-5)

Turning on a single fan while `operation_mode == "off"` writes a balanced
level, so both directions start. This is deliberate and documented in the
README, but it means the user cannot go directly from off into single-direction
operation.

### Why this is deliberate

Writing an unbalanced mode from a stopped unit was never traced. It is unknown
whether the unit accepts `41120 = 4` with one side at zero directly out of the
off state, or whether it needs to be running first.

### What to measure (HW-5)

From a stopped unit, write `41120 = 4`, `41121 = 0`, `41122 = <raw>`,
`41132 = 0` and check whether only the extract fan starts.

Tests: [W-12](LIVE_GATEWAY_TESTS.md#w-12--single-direction-start-from-off).

### Candidate solutions (HW-5)

- **A — keep the current behaviour.** Safe and documented.
- **B — allow direct single-direction start** if the measurement shows the unit
  accepts it.

---

## HW-6 — Verify the reduced read and retry pattern

Priority: medium
Status: open
Affected code: `modbus_client._read_mode_group`, `device/transport.py`,
`coordinator._job_interval`

### Observation (HW-6)

Three changes reduce bus time but were made without a live gateway:

1. The original flow job polled the `41120` set-register block. It has been
   replaced by one `41100..41102` current-state read per due flow job; the
   separate status-map scan and single-register comparisons are in R-12.
2. Every request is retried at most once (`TransportPolicy`). pymodbus'
   default `retries=3` had turned one silent register into about eight
   timeouts. This is measured as part of HW-7.
3. A unit that has been silent for `ROOM_SILENT_AFTER_SECONDS` is polled at
   most every `SILENT_ROOM_POLL_SECONDS`.

### What to measure (HW-6)

1. Validate that the status block and its single-register reads agree after
   balanced, unbalanced, and preset changes; do not treat set-register
   readbacks as the integration's state source.
2. Run the integration for a day with all units online and check the logs for
   new transient read failures compared to the previous release.
3. Power off one unit and confirm that the others still react to fan changes
   within a few seconds.

Tests: measurement 1 in [R-12](LIVE_GATEWAY_TESTS.md#r-12--read-only-mode-status-map)
and [W-10](LIVE_GATEWAY_TESTS.md#w-10--unbalanced-write-and-readback),
measurement 2 in [T-10](LIVE_GATEWAY_TESTS.md#t-10--24-hour-soak) and
[H-9](LIVE_GATEWAY_TESTS.md#h-9--real-home-assistant-instance),
measurement 3 in [T-5](LIVE_GATEWAY_TESTS.md#t-5--silent-address-in-the-integration-client)
and [H-1](LIVE_GATEWAY_TESTS.md#h-1--powered-off-unit).

### H-1 measurement on 2026-10-14

The controlled H-1 repeat completed 60 airflow rounds and 10 temperature and
status rounds per unit while slave `4` was physically powered off. All 480
jobs for all six units succeeded; slave `4` continued returning `60/60 m3/h`
in two requests per airflow job, with zero transport timeouts or link
recycles. The other five units were not observably affected. Because the
powered-off unit's unchanged values were still reported as successful reads,
the gateway's retained-value behavior and the actual recovery time remain
unknown. See [MELTEM.md](MELTEM.md#reads-while-a-unit-is-powered-off).

### Candidate solutions (HW-6)

- **A — keep the changes** if all three measurements are clean.
- **B — a second retry** in `TransportPolicy` if step 2 shows more transient
  failures.
- **C — investigate the status map** if step 1 shows differing values.

---

## HW-7 — Validate the tmodbus transport on the real gateway

Priority: high (post-release validation)
Status: open
Affected code: `device/transport.py`, `device/components.py`,
`modbus_helpers.prepare_unit`, `modbus_client._poll`,
`modbus_client._read_mode_status`

### Observation (HW-7)

`4.0.0` replaces the own pymodbus client with Home Assistant's shared Modbus
connection (tmodbus via `modbus-connection`). The request shapes and their
order are unchanged and covered by tests against an in-memory gateway, but
the original timing and error findings in this backlog were measured with
pymodbus. A first live comparison with tmodbus was run on 2026-10-02; results
and the remaining post-release items are below.

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

### What to measure (HW-7)

Each measurement is compared with a baseline taken with the pymodbus tools
from before `4.0.0`
([P-5](LIVE_GATEWAY_TESTS.md#p-5--baseline-worktree),
[T-1](LIVE_GATEWAY_TESTS.md#t-1--baseline-with-the-pymodbus-tools)):

1. Latency and timeout rate per scenario, compared with the baseline.
2. Power off one unit: timeout or code 10/11, requests per job, and whether
   `link_recycles` in the diagnostics stays at 0 while other units answer.
3. A freshly powered unit: read-only mode-status reads for the supported
   M-WRG-S and M-WRG-II profiles (HW-4 / R-12).
4. Home Assistant write confirmation via the exact `41121` set-register
   readback, plus the settle time after writes (HW-2 / W-17).
5. Whether the gateway ever answers with code 6, and for how long.
6. Unplug and replug the USB cable; reload and unload the entry: the port is
   released and the link comes back.
7. 24 hours of continuous operation with all units; note `link_recycles` and
   the info-level "Recycling the Meltem gateway link" log lines.

These seven measurements are the post-release validation checklist for the
tmodbus transport introduced in `4.0.0`. They are no longer a release gate for
`4.0.0`; the tests per measurement are listed under
[HW-7 post-release validation](LIVE_GATEWAY_TESTS.md#hw-7--post-release-tmodbus-validation).

### Partial measurement: HW-7 on 2026-10-02

- **Measurement 1:** The six read-only benchmark scenarios completed 1,440
  requests per transport, alternating A-B-A-B, with no failures and no
  protocol or desynchronization errors. The pymodbus baseline had average
  latencies of `18.6..19.5 ms` and p95 `19.6..19.8 ms`; tmodbus averaged
  about `15.8 ms`, with p95 `15.9..16.8 ms`, p99 `16.7 ms`, and maximum
  `16.9 ms`. tmodbus was therefore not slower in those simple request
  patterns. The gap-0 T-3 run timed out while reading the discovery list at
  `43902`; the tested gaps `0.05..0.3 s` passed 60/60 requests once each.
- **Measurements 2 and 5:** Unconfigured slave `8` timed out after about
  `0.8 s` in G-5. During the ten-minute T-5 run, each of its 60 airflow jobs
  used two requests and took about `1.8 s`; `link_recycles` stayed at `0`.
  No Busy/code-6, protocol, or desynchronization errors appeared in the
  recorded read-only runs. T-6's active gap-0 stimulus was not run because it
  conflicts with the hard pacing rule, which permits gap `0` only for T-3.
- **Measurement 2, real-unit part:** In the final H-1 run, slave `4` was
  physically off for about five minutes while all six rooms were polled.
  During that interval, 35 airflow jobs for slave `4` completed successfully
  with 2-3 requests each; no timeout or gateway-path exception surfaced to the
  integration client. `link_recycles` and consecutive transport timeouts
  stayed at `0`. The other five rooms continued to answer, and status and
  temperature plans succeeded 10/10 per room. After power-up, slave `4`
  continued to return successful airflow jobs. A separate final read reported
  measured airflow `80/0 m3/h` while the short mode read remained manual target
  `20 m3/h`; status flags were `0`, and five repeat reads agreed. The person
  confirmed that the app and physical unit also showed `80/0`, with no
  unexpected behavior. The poller did not print state values during the off
  interval, so cached data during that interval cannot be ruled out.
- **Measurement 3:** Partial; the pre-write, post-write, and power-cycle
  readbacks are recorded under
  [HW-4](#hw-4--units-that-reject-the-five-register-mode-block). The short
  read and `41121` persisted through a post-write power cycle on slave `4`,
  but the five-register block and `41122` remained unavailable.
- **Measurement 4:** Partial; the write, target-confirmation, airflow-lag,
  interleaved-read, and commit-latch measurements are recorded under
  [HW-2](#hw-2--writes-block-the-whole-gateway-for-several-seconds). W-5/W-1/
  W-2/W-3/W-6 used the integration-like tool or a helper script, not the real
  Home Assistant coordinator, so its lock duration and write path remain
  unverified on the live installation.
- **Measurement 6:** On Linux, a second process failed to open the held serial
  port with exit code `2`; after stopping the watcher, P-3 reopened it 20/20
  times. The optional T-9 Home Assistant setup/unload test passed, and P-3
  reopened the port afterwards. USB unplug/replug and a real HA installation
  remain untested.
- **Measurement 7:** T-10 completed 8,640 rounds over 24 h 1 min with six
  units. There were no transport timeouts, protocol/desynchronization errors,
  Busy responses, or link recycles; all 288 diagnostics showed
  `consecutive_timeouts=0` and `link_recycles=0`. Temperature, status, and
  slow plans succeeded for every unit. The proposed 99.5% total-job threshold
  was not met because mode-group exception `0x05` made airflow jobs fail on
  slaves `2`, `3`, `6`, and `7` throughout, and on slave `5` for 6,228 jobs;
  slave `5` airflow jobs then succeeded for 2,412 jobs. Slave `4` airflow jobs
  succeeded throughout. The mode-read transition on slave `5` is detailed
  under [HW-4](#hw-4--units-that-reject-the-five-register-mode-block). Thus
  T-10 supports transport stability, but does not pass the proposed whole-job
  criterion. Per-round CSV and text logs were local-only; the aggregate counts
  above are the retained record.

The T-1 integration-like baseline runner counts a refresh as successful when
`read_room_state` does not raise; the current T-2 runner also counts optional
read-group health errors. Consequently, the baseline reported 18/18 full
refreshes and 240/240 scheduler jobs successful, while tmodbus reported
0/18 and 180/240 respectively, with the difference consisting of the known
mode-register exception code `5`. These success counts are not directly
comparable. The raw register-profile tests showed the same mode-register
exceptions on both transports. HW-7 remains open: active Busy behavior and
the USB lifecycle are still unmeasured. T-10 was transport-stable but did not
meet the proposed whole-job success threshold.
The powered-off-unit test saw successful integration jobs, but did not log
their returned state. The integration-like success/error comparison still
needs a like-for-like interpretation.

The HA `2026.10.0` restart export from 2026-10-08 provides partial real-host
evidence, summarized in
[MELTEM.md](MELTEM.md#home-assistant-restart-on-2026-10-08). It motivated shared
read-telegram pacing and progressive startup, but does not close measurements
6/7 or prove the reported transient data-health indication was caused by the
startup traffic. Validate the revised startup under H-9 before marking that
behavior resolved.

### Follow-up measurements on 2026-10-09

The live gateway was discovered on `/dev/ttyACM0` with six nodes
`[3, 2, 4, 5, 7, 6]`. Selected raw evidence is preserved under
[`docs/dev/measurements/2026-10-09/`](measurements/2026-10-09/); other working
logs and snapshots were local-only and are not part of the repository.

- **1 — Benchmarks:** The first tmodbus run completed six direct request
  scenarios (720 requests) without errors, with mean latency about
  `15.6..15.8 ms` and p95 about `15.7..15.9 ms`. In the initial baseline
  airflow-single scenario, 80/120 reads succeeded; the 40 failures were
  exception `0x05` from slaves `6` and `7`. Later, after USB reconnect, direct
  airflow reads returned `0x05` on all six slaves in both baseline and current
  runs. The comparison is therefore incomplete and sensitive to gateway
  lifecycle. The baseline integration-like full/scheduler benchmark failed
  during discovery with an unsupported `slave` keyword and sent no poll
  requests; its zero-request result is not a benchmark outcome.
- **1 — Request gaps:** T-3 completed 60/60 requests at each tested positive
  gap (`0.05`, `0.1`, `0.2`, and `0.3 s`). Gap `0` was not tested.
- **2 / 5 — Unconfigured and powered-off units:** G-5 timed out on slave `8`
  in all three probes after about `0.8..0.9 s`. During the ten-minute T-5
  run, the ghost unit used two requests per airflow job and `link_recycles`
  remained `0`; real-unit airflow jobs still reported known mode-group
  exception `0x05`. In H-1, slave `4` was off for 6 min 5 s, exceeding the
  five-minute safety limit. The helper reported completed jobs without
  transport timeouts, but did not record values; this is inconclusive and
  must not be counted as a pass or repeated.
- **3 / 4 — Mode reads and write confirmation:** On slave `4`, W-1 read back
  target `41121=40` after requesting `20 m3/h`; measured airflow changed from
  `80/0` to `20/20` by observation six. After the helper restored its own
  target to off, the person manually restored `80/0`, which a later read
  confirmed. The
  post-restore snapshot contained multiple shadow-register differences whose
  cause is unknown. The test used a helper, not the Home Assistant
  coordinator; the freshly powered-unit mode-read case remains open.
- **5 — Busy behavior:** No Busy response appeared in the passive runs. The
  active stimulus and gap-0 request were not run under the pacing safety
  rules.
- **6 — Link lifecycle:** T-7 passed: a competing process could not open the
  held port and reopened it 20/20 times after release. The standalone H-3
  poller only detected the unplugged adapter. In the H-3 follow-up, the
  running HA integration reopened the same serial path automatically about
  80 s after the loss; airflow reads first returned `0x05` on all six units
  and recovered over up to about eight minutes, so the one-airflow-round
  criterion was not demonstrated. The replug time, a diagnostics export, and
  `link_recycles` were not captured. The old set-register mode reads kept
  `flow_control` and `intensive` stale on every room; that problem predated
  the disconnect and does not test the `41100..41102` path. A later return
  to the HA host showed the same pattern and one acknowledged LOW-preset write
  from the bathroom automation. Timeline and snapshots:
  [MELTEM.md](MELTEM.md#usb-disconnect-and-port-recovery-on-2026-10-09).
- **6 — H-9 startup and lifecycle checks:** with `4.0.1` at `3 req/s`, three
  Core restarts kept every function-`0x03` send at least `348 ms` apart,
  completed first airflow, filter, and operating-hours reads for all six
  units, and showed no transport timeouts or link recycles. Reload, unload,
  and re-enable released and reacquired the link cleanly. Mode-group `0x05`
  from the old set-register path still flagged data health on five rooms.
  The competing-hub check was skipped and the previous-release comparison is
  outstanding. Details:
  [MELTEM.md](MELTEM.md#home-assistant-startup-on-2026-10-09).
- **7 — Soak:** The 24-hour run was not repeated, per the person's instruction.
  The 2026-10-02 T-10 result remains the only soak evidence.

### Follow-up measurements on 2026-10-14

- **2 — Powered-off unit:** the controlled H-1 repeat recorded successful
  replies and unchanged `60/60 m3/h` values from physically powered-off
  slave `4`; all other units' jobs also succeeded. This gives the per-job
  cost but does not establish whether values are retained by the gateway or
  how quickly the unit becomes observable again. See the HW-6 H-1 measurement
  and [MELTEM.md](MELTEM.md#reads-while-a-unit-is-powered-off).
- **3 — Mode status map:** A read-only comparison of `41100×3` with reads of
  `41100`, `41101`, and `41102` singly matched on all six connected nodes.
  Slave `4` was confirmed as M-WRG-II; M-WRG-S validation remains open.
- **4 — Writes:** W-1 on slave `4` changed measured airflow from `60/60` to
  `40/40` after about six seconds, with no failed timed operations. However,
  the status map returned `41101=204` for raw target `41121=80`, and
  `41101=206` for raw target `41121=120` on restore. This contradicts the raw
  `0..200` echo assumption. The final HIGH restore was verified after two
  minutes with `41016=0`; the full five-register set block still returned
  exception `0x05`. See
  [Follow-up W-1 on 2026-10-14](#follow-up-w-1-on-2026-10-14) under HW-2.
  The real Home Assistant coordinator confirmed balanced writes in W-17 after
  a fix, see [W-17](#w-17-home-assistant-write-confirmation-on-2026-10-14).
- **6 — USB replug (H-3):** on 2026-10-14, kernel events recorded USB
  disconnect at 19:49:13 CEST and re-enumeration at 19:50:39; the adapter
  serial `480782E24` returned as `/dev/ttyACM0`. The actual cable gap was
  about 86 seconds, not the planned 30. The running HA integration resumed
  airflow reads, but not within one airflow round: unit `3` was fresh by
  about 19:51:35, unit `1` by about 19:52:45, units `4` and `5` by 19:54:10,
  and unit `6` by 19:55:47. At the 19:55:52 snapshot, unit `2` alone remained
  stale, with three failures on `41020×2` returning exception `0x05`; its last
  successful airflow read was at 19:49:07, before the disconnect. A follow-up
  state check at about 20:01:46 found all six units fresh; unit `2` had zero
  failures and its latest successful airflow read was at 20:01:43. Its first
  recovery time was not captured. Thus the client recovered the link and all
  units eventually, but H-3's one-round criterion failed. `link_recycles` was
  not captured. See the
  [H-3 result](LIVE_GATEWAY_TESTS.md#h-3--usb-unplug-and-replug) and
  [MELTEM.md](MELTEM.md#usb-disconnect-and-port-recovery-on-2026-10-09).

### H-3 USB reconnect retest on 2026-10-15

With the Home Assistant coordinator left running, the kernel recorded USB
disconnect at `01:44:07 CEST` and re-enumerated adapter serial `480782E24` as
`/dev/ttyACM0` at `01:45:08`, a 61-second gap. Home Assistant logged the serial
connection loss at `01:44:07.909`. The first post-replug health capture showed
all six `flow` groups stale with three failures on `41020×2` (exception
`0x05`).

The 12-minute state observer started at `01:43:13` and completed at `01:55:14`.
Its first captured successful `flow.last_successful_read` timestamps after
re-enumeration were:

| Unit | First captured success (CEST) | Delay after re-enumeration |
|------|-------------------------------|----------------------------|
| 3    | 01:46:56                      | 1 min 49 s                 |
| 2    | 01:48:06                      | 2 min 59 s                 |
| 1    | 01:52:45                      | 7 min 38 s                 |
| 5    | 01:54:05                      | 8 min 57 s                 |
| 4    | 01:54:34                      | 9 min 27 s                 |
| 6    | 01:54:58                      | 9 min 51 s                 |

The UI observer reported 206 parse errors, so these are first captured
successes, not guaranteed exact first-recovery times. Direct state snapshots
showed units `2` and `3` fresh while `1`, `4`, `5`, and `6` remained stale at
`01:48:30` and `01:50:51`; at `01:53:25`, units `1` through `3` were fresh and
`4` through `6` were still stale. A final `01:55:51` snapshot showed every
read-health group fresh with zero failures and all six device-error entities
off. The port number and adapter serial were unchanged. Thus automatic
recovery completed for every unit, but the one-airflow-round criterion failed.
`link_recycles` was not captured.

### H-3 USB reconnect follow-up on 2026-10-15 at 11:19 CEST

The kernel recorded USB disconnect at `11:19:19 CEST` and re-enumerated the
same adapter (serial `480782E24`) on `/dev/ttyACM0` at `11:21:49`, a
2-minute-30-second gap. Home Assistant logged serial loss at `11:19:19.459`.
The port name and adapter serial were unchanged.

At the `11:27:01` state snapshot, units `1`, `4`, `5`, and `6` had fresh flow
data; units `2` and `3` were still stale. Unit `3` had a successful flow read
by `11:28:24.425`. Unit `2` was still stale at `11:28:32`, with its last
successful flow read at `11:19:12.523` and three `0x05` failures on
`41020×2`; it had a successful flow read stamped `11:29:10.866` and was
fresh with zero failures at the `11:29:17` observer baseline. Thus the one-
airflow-round recovery criterion failed; two rooms' flow groups remained
unrecovered for more than five minutes after re-enumeration.

The coordinator logged units `2` and `3` as reachable at `11:22:46` and
`11:22:47`, respectively, before their flow groups recovered. Those
room-level messages reflect a successful read of some group, not necessarily
the airflow group. The direct state observer ran from `11:29:17` to `11:34:18`;
its browser WebSocket disconnected at `11:32:10`, so the final state was
rechecked after a page reload. At `11:35:06`, all six read-health entities
and device-error entities were clear, with every flow group fresh and at zero
failures. The earlier long observer result had expired before retrieval, so
the exact first-success times for units `1`, `4`, `5`, and `6` were not
captured.

After the final state check, the integration diagnostics reported
`coordinator.transport` as `consecutive_timeouts=0`, `link_recycles=0`, and
`seconds_since_any_answer=0`, with `last_update_success=true`. No
`Recycling the Meltem gateway link` message appeared in the HA log after this
HA process started at `11:06:52`. The diagnostics request includes a gateway
discovery read and was made only after the observation was complete.

#### Passive read-health observation after reconnect

From `11:42:18` to `11:52:19 CEST`, a direct state observer watched the normal
Home Assistant scheduler at `4 req/s` without issuing manual Modbus reads. Its
frontend connection stayed live throughout the interval. Each unit recorded
`56–57` successful `flow` refreshes and `9–10` each for `status` and
`temperature`; none of the 459 observed group updates had a failure or stale
flag. The HA log capture was empty. A diagnostics request after the window
reported `consecutive_timeouts=0`, `link_recycles=0`, and
`last_update_success=true`; that request included a gateway discovery read.

This supports that the earlier `0x05` failures were not recurring during
steady polling after recovery. It does not establish why they occurred during
the reconnect period.

### H-3 raw reconnect trace on 2026-10-15 at 12:41 CEST

Kernel events recorded USB disconnect at `12:41:34.176 CEST` and
re-enumeration at `12:42:05.099 CEST`, a `30.923 s` gap. The same adapter
(serial `480782E24`) returned on `/dev/ttyACM0`. This was a read-only
reconnect; no writes or competing serial client were used.

Initial airflow reads from all room RTU slaves `2–7` returned Modbus
exception `0x05` on `41020×2`. The first successful `41020×2` responses were:
slave `3` at `12:43:30.842`, slave `5` at `12:45:29.861`, slave `7` at
`12:45:32.551`, slave `6` at `12:46:16.644`, slave `4` at `12:48:04.082`,
and slave `2` at `12:49:21.336 CEST`. The observer ended at `12:49:34.562`
after all six flow groups had been fresh with zero failures for 12 seconds.
The one-airflow-round criterion (about 10 seconds) failed; the slowest first
success was 7 minutes 16 seconds after re-enumeration.

#### RTU request and response correlation

The raw logger split some `RTU recv` frames across adjacent lines. After
reassembling those chunks, all `1,676` request/response pairs in the capture
passed CRC, byte-count, function, and slave checks; there were no incomplete
pairs or orphan chunks.

From re-enumeration through the `12:49:34.562` airflow checkpoint, `627`
transactions comprised `434` normal responses and `193` Modbus exception
`0x05` responses, with no other exception code. The exceptions affected more
than the airflow block:

- `41020×2` (extract/supply airflow): `137` exceptions; counts by slaves
  `2–7` were `40, 7, 32, 18, 22, 18`. These all preceded each slave's first
  successful airflow response listed above.
- `41004×2` (exhaust temperature): `25` exceptions, with counts by slaves
  `2–7` of `7, 2, 6, 3, 4, 3`. First successful responses were slave `2` at
  `12:49:36.895`, `3` at `12:44:30.264`, `4` at `12:48:38.551`, `5` at
  `12:45:37.095`, `6` at `12:46:15.926`, and `7` at `12:45:45.705`.
- `41016×3` (error, filter-due, and frost status): `26` exceptions, with
  counts by slaves `2–7` of `7, 2, 6, 3, 4, 4`. First successful responses
  were at `12:49:22.770`, `12:44:16.320`, `12:48:24.459`, `12:45:25.923`,
  `12:46:31.296`, and `12:46:28.344`, respectively.
- `41100×3` (current mode status): five exceptions, one each on slaves
  `2–5` and `7`, none on `6`. First successful responses were at
  `12:42:48.233`, `12:42:47.251`, `12:42:56.986`, `12:42:49.216`,
  `12:42:20.642`, and `12:42:50.198`, respectively.
- `40101×1` (RF communication status): no exceptions. First successful
  responses for slaves `2–7` were between `12:42:14.737` and `12:42:23.590`.

The temperature read for slave `2` first succeeded at `12:49:36.895`, just
after the airflow checkpoint; that checkpoint establishes recovery of the
airflow groups, not every read group. No further `0x05` response occurred for
a block/slave after its first success. From `12:49:34.562` to the end of the
raw capture at `12:58:04.200`, all `730` transactions were exception-free.

Three diagnostics endpoint fetches were made after the observation window
while inspecting the response shape; each includes a gateway-discovery read.
The final response at `13:01:44 CEST` reported `consecutive_timeouts=0`,
`link_recycles=0`, `last_update_success=true`, and no gateway-probe or
last-job error. Thus `seconds_since_any_answer=0` reflects a post-window
probe, not reconnect-only activity. Gateway discovery found
`[3, 2, 4, 5, 7, 6]`.

The `tmodbus.raw_traffic` logger was restored to `WARNING` after the
measurement. The main HA log's last raw-traffic line was at `12:58:04.169`;
no later raw frames appeared through `13:02:09`, while polling and diagnostics
remained successful.

#### Passive read-health observation after 12:41 raw trace

An initial event-subscription attempt was discarded after the frontend
WebSocket stopped receiving updates. The valid observer then sampled Home
Assistant's `/api/states` every 5 seconds from `13:29:16` to `13:39:16 CEST`
(`601 s`; `122` snapshots). These snapshots did not query the gateway. The
observer tracked `flow`, `status`, and `temperature` groups for all six units.
Units `1–5` each had
`57` successful flow updates and unit `6` had `56`; status had `10` updates
per unit, and temperature had `9–10`. Across all `458` observed group updates
there were no failures or stale transitions. All snapshots succeeded, and the
frontend WebSocket was connected at the start and end.

### H-3 `v4.1.1` tag comparison on 2026-10-15 at 14:38 CEST

The isolated Home Assistant configuration loaded the archived `v4.1.1` tag
instead of `HEAD 09ac937`; both sources report integration version `4.1.1`.
This was a read-only run with one Home Assistant process on `/dev/ttyACM0`.

The kernel recorded USB disconnect at `14:38:22 CEST` and re-enumeration of
the same adapter (serial `480782E24`) at `14:38:54`, a `32 s` gap. At
`14:39:11`, all six units had failing/stale airflow reads.

After reassembling fragmented `RTU recv` log entries, `1,239` complete
request/response pairs passed CRC, length, function, and slave checks;
`1,201` of these were after re-enumeration. One request at the time-capped
capture's end had no response in the captured file. No exception code other
than `0x05` appeared.

| Block | `HEAD 09ac937` `0x05` counts by slaves 2–7 | Tag `v4.1.1` `0x05` counts by slaves 2–7 |
| --- | --- | --- |
| `41020×2` airflow | `40, 7, 32, 18, 22, 18` (`137`) | `3, 27, 53, 38, 43, 40` (`204`) |
| `41004×2` exhaust temperature | `7, 2, 6, 3, 4, 3` (`25`) | `1, 5, 9, 7, 7, 7` (`36`) |
| `41016×3` status | `7, 2, 6, 3, 4, 4` (`26`) | `1, 5, 9, 7, 8, 7` (`37`) |
| `41100×3` current mode | `1, 1, 1, 1, 0, 1` (`5`) | `1, 1, 1, 1, 1, 1` (`6`) |
| `40101×1` RF status | `0` | `0` |

For each block/slave in the tag trace, every `0x05` preceded its first normal
response; there were no later `0x05` responses for that pair. First
successful responses, in slave order `2–7`, were:

| Block | First successful response times (CEST) |
| --- | --- |
| `41020×2` airflow | `14:39:36.754, 14:43:44.425, 14:48:14.860, 14:45:42.118, 14:46:27.566, 14:45:55.025` |
| `41004×2` exhaust temperature | `14:40:14.433, 14:44:17.702, 14:48:20.182, 14:46:20.243, 14:46:22.652, 14:46:21.447` |
| `41016×3` status | `14:40:08.091, 14:44:07.428, 14:48:12.655, 14:46:13.719, 14:47:16.835, 14:46:15.161` |

The slowest first airflow response was `9 min 21 s` after re-enumeration.
Current-mode reads each returned one `0x05` before succeeding within
`41–45 s`; RF-status reads had no exceptions and first succeeded within
`13–17 s`.

A passive `/api/states` observer ran from `14:40:15` to `14:50:20 CEST`
(`605 s`; `103` snapshots) and did not query the gateway. At the end, all six
read-health entities were healthy, with no current stale flags or errors;
all `writes` attributes remained empty. A final state check at `14:55:21`
confirmed the same.

The tag capture contained `283` `0x05` responses in `1,201` post-reconnect
transactions, versus `193` in the earlier HEAD trace through its airflow
recovery checkpoint (`627` transactions). These windows are not equal, and
there was only one reconnect per source, so the count difference does not
establish a code regression or improvement. The observed recovery pattern
was otherwise the same: register-specific `0x05` responses, staggered first
successes, and no further exception after each block/slave recovered.

The raw capture continued for at least `13 min 52 s` after re-enumeration,
exceeding the `10 min` recovery-capture limit in
[LIVE_GATEWAY_TESTS.md](LIVE_GATEWAY_TESTS.md#h-3--usb-unplug-and-replug).
Keep future H-3 captures within that documented limit unless the procedure
is explicitly revised.

After the tag run, the logger was restored to `WARNING`, the tag process was
stopped, and the isolated configuration was switched back to `HEAD 09ac937`.
The port was released before restart. A `131 s` HEAD baseline from
`14:56:36` to `14:58:47 CEST` recorded `249` successful group updates across
`flow`, `flow_control`, `intensive`, `status`, and `temperature`, with no
failures, stale transitions, or writes.

### Current disposition

- **1 — Benchmarks:** partial. The initial direct tmodbus scenarios passed,
  but the later `0x05` results after USB reconnect and the baseline runner's
  zero-request failure prevent a comparable full result.
- **2 — Powered-off unit:** partial. The 2026-10-09 H-1 run exceeded its
  five-minute limit. The controlled 2026-10-14 repeat stayed within the limit
  and logged values, but slave `4` kept returning unchanged successful
  `60/60` reads while physically off. Retained-value behavior and recovery
  time remain unresolved.
- **3 — Mode status map:** partial. Block/single parity succeeded on all six
  connected nodes on 2026-10-14, and the HA deployment read the status map
  with fresh read-health groups; M-WRG-S validation remains open (HW-4 /
  R-12).
- **4 — Writes:** partial. W-17 confirmed the exact Home Assistant coordinator
  readback on M-WRG-II, and five HA repetitions recorded variable airflow
  settle times. W-18 then ran the six-slave scheduler under load without
  timeouts or link recycles; it did not reproduce the long HIGH-return
  outlier. The lock/settle behavior is not characterized enough to justify a
  timing change.
- **5 — Busy:** inconclusive. No Busy response appeared in passive runs, and
  the active stimulus was not run under the current pacing safety rules.
- **6 — Link lifecycle:** partial. Port locking and new-process reopening
  passed. H-9 confirmed startup pacing and initial room progress at `3 req/s`,
  plus clean connection release and reacquisition on reload, unload, and
  re-enable. The 2026-10-09 HA H-3 follow-up reopened the serial link automatically after
  USB loss, and all six units eventually returned successful airflow reads.
  The later HA-host return again showed airflow reads succeeding on all six by
  log end, but the old set-register mode reads remained `0x05` on all six and
  short fallback reads remained `0x05` on five. Those runs predate the
  `41100..41102` status-path change. An acknowledged LOW-preset write sequence
  from the bathroom humidity-control automation appeared on slave `5`;
  there was no manual action. Recovery took up to about eight minutes after the transport
  reopened, while the mode failures kept several `Datenlesestatus` entities in
  `Problem`. The exact replug time and diagnostics are unavailable, so the
  one-airflow-round criterion and `link_recycles` counter cannot be confirmed.
  The controlled 2026-10-14 H-3 run recovered five of six units but did not
  meet the one-airflow-round criterion; unit `2` remained stale at the
  19:55:52 snapshot, but all six units were fresh by the follow-up check at
  about 20:01:46. See the detailed measurement above.
  The 2026-10-15 01:44 CEST retest again failed the one-round criterion,
  although all six units eventually recovered within about ten minutes after
  re-enumeration; see the [H-3 retest](#h-3-usb-reconnect-retest-on-2026-10-15).
  The 11:19 CEST follow-up also failed: units `2` and `3` had delayed flow
  recovery, and `link_recycles` was `0`; see the
  [11:19 H-3 follow-up](#h-3-usb-reconnect-follow-up-on-2026-10-15-at-1119-cest).
  A subsequent 10-minute passive read-health window found no repeated `0x05`
  errors or stale groups; see the
  [passive observation](#passive-read-health-observation-after-reconnect).
  The 12:41 CEST raw-trace retest also failed the one-round criterion: first
  successful airflow reads came 1 minute 26 seconds to 7 minutes 16 seconds
  after re-enumeration, while `link_recycles` and `consecutive_timeouts` stayed
  at zero. See the
  [12:41 H-3 raw trace](#h-3-raw-reconnect-trace-on-2026-10-15-at-1241-cest).
  Offline RTU correlation found `0x05` on the temperature and status blocks
  as well as airflow; mode-status exceptions were brief, and RF status reads
  succeeded early. No exception responses appeared after recovery. See the
  [RTU correlation](#rtu-request-and-response-correlation).
  A subsequent 10-minute passive window recorded no read failures or stale
  transitions; see the
  [passive observation after the 12:41 trace](#passive-read-health-observation-after-1241-raw-trace).
  A read-only comparison against the archived `v4.1.1` tag also failed the
  one-round criterion: first airflow responses ranged from `43 s` to
  `9 min 21 s` after re-enumeration. The single-run comparison did not isolate
  a code effect; see the
  [tag comparison](#h-3-v411-tag-comparison-on-2026-10-15-at-1438-cest).
  The H-3 unplug-test campaign is closed for this round. The observed delay is
  accepted operationally for the rare manual USB unplug/replug scenario; the
  one-airflow-round criterion remains unmet and the cause is unresolved. This
  does not establish behavior for other transport faults. The competing-hub
  port-conflict check remains untested.
- **7 — Soak:** partial. The 24-hour run showed no transport timeouts or link
  recycles, but did not meet the proposed whole-job success threshold because
  of mode-register exceptions; no new 24-hour run was authorized. Do not
  treat this as a pass for whole-job reliability.

The evidence supports transport stability only in the measured scenarios. The
post-replug `0x05` observations remain unexplained; do not treat them as either
a persistent airflow fault or proof that the transport is unstable. They do
not close HW-7 or justify changing retry, timeout, or locking behavior. Keep
the item open until the remaining observations are recorded with comparable
success criteria.

### Candidate solutions (HW-7)

- **A — leave the transport unchanged** if the remaining measurements show no
  consequential regression; document the evidence and close HW-7.
- **B — tune** `REQUEST_GAP_SECONDS`, `FIXED_TIMEOUT`,
  `TRANSPORT_DISCONNECT_AFTER_TIMEOUTS`, or `TRANSPORT_LINK_QUIET_SECONDS`
  from the measurements.
- **C — skip the rest of a job on code 10/11** like on a timeout, in
  `MeltemModbusClient._poll` and `_read_mode_status`, and keep code 10/11
  out of the mode backoff, if a powered-off unit answers that way.
- **D — cap jobs and writes** with `asyncio.timeout` in the coordinator if the
  gateway sends code 6 and the 60 s retry blocks the UI.
