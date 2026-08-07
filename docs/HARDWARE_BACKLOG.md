# Hardware Verification Backlog

Findings from code review that are **not** fixed in the repository because they
cannot be decided without a live `M-WRG-GW` gateway and at least one `M-WRG`
unit. Each entry states what was observed in the code, why a blind fix would be
irresponsible, what to measure, and which candidate solutions exist.

Related documents:

- `docs/MELTEM.md` — manufacturer reference and traced register writes
- `docs/DEVELOPER.md` — implementation notes and hardware findings
- `docs/SETTING_RE_BACKLOG.md` — app-side settings reverse engineering
- `docs/TODO.md` — remaining non-hardware work

Status legend: `open` (needs measurement), `parked` (measured, decision
deferred), `resolved` (move the outcome into `docs/DEVELOPER.md`).

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

### Why this cannot be fixed blind

1. It is unknown whether the firmware clears the registers itself. The override
   also ends on its own after a runtime configured in the Meltem app, so
   self-clearing is plausible. If it self-clears, an extra write is pointless
   bus traffic; if it does not, the current behaviour is a real bug.
2. `docs/MELTEM.md` states that `41132` must always be written last and that
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

`tools/watch_register_changes.py` and `tools/write_registers.py` cover most of
this; a dedicated script would only need to sequence the steps.

### Candidate solutions

- **A — clear on off only.** Add `_clear_secondary_preset_registers` to the
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
    await executor(write_...)                     # 3-5 writes at 0.1 s gap  ~0.5 s
  await async_sleep(WRITE_SETTLE_SECONDS)       #                           1.5 s
    await self._async_refresh_room_after_write(   # read
        room, min_refresh_attempts=2              # + 2.5 s
    )                                             # + read
```

The gateway lock is held for roughly five to six seconds. During that window no
poll job runs for any room, so the airflow sensors of every unit behind the
gateway can be up to six seconds stale right after a button press.

The obvious improvement is to release the lock during the pure settle sleep.

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
   `docs/DEVELOPER.md` already notes that `41020/41021` lag behind.

### Candidate solutions

- **A — shorten `WRITE_SETTLE_SECONDS`.** If the measurement shows the unit
  settles in, say, 0.5 s, this alone removes most of the delay with no change
  to the locking model. Lowest risk, do this first.
- **B — release the lock during the settle sleep.** Largest gain, but depends
  on step 2 of the measurement showing that interleaved traffic is harmless.
- **C — reduce `min_refresh_attempts` from 2 to 1.** The second read exists
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

`docs/DEVELOPER.md` notes that many devices return Modbus exceptions for
`41120/41121/41122` until a write has occurred, so the fallback exists for a
reason. What is not known is whether the five-register read stays unavailable
forever on some units or only until the first write.

### What to measure

1. On a freshly powered unit, attempt the five-register read at `41120` and
   record the exception.
2. Perform one airflow write.
3. Retry the five-register read.
4. Repeat after a power cycle to see whether the capability is sticky.

### Candidate solutions

- **A — no change.** Correct if the capability returns after the first write;
  the switch is only `unknown` until the user does something.
- **B — probe once during setup.** Store the capability in
  `supported_entity_keys` and simply do not create the intensive switch on
  units that never answer.
- **C — derive intensive from `41121`.** If `41121` reads back `227` the
  override is active. Would work on units without the five-register read, but
  it conflicts with the quick mode codes in the same range (see HW-3).

---

## HW-5 — Turning a stopped unit on starts both directions

Priority: informational
Status: parked
Affected code: `fan.MeltemDirectionalFanEntity.async_set_percentage`

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

### Candidate solutions

- **A — keep the current behaviour.** Safe and documented.
- **B — allow direct single-direction start** if the measurement shows the unit
  accepts it.
