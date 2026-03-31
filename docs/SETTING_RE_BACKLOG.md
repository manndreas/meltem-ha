# Meltem Setting Reverse-Engineering Backlog

This document is the local Modbus-side working backlog for device settings that
exist in the Meltem app but are not yet mapped to a confirmed local register
write path in this repository.

Purpose:

- keep the work scoped to concrete setting families
- separate confirmed local facts from plausible hypotheses
- avoid broad blind scans across the full register space
- make before/after captures reproducible

This document is intentionally about local Modbus-facing reverse engineering,
not the cloud API.

For cloud-side app traffic mapping, see `meltem-cloud-ha/docs/SETTING_MAPPING.md`.

## Current local conclusion

What is already clear:

- runtime control over USB / serial Modbus is well established
- app-side settings such as keypad airflow defaults and intensive ventilation
  parameters appear to remain effective offline later on the physical device
- therefore the effective configuration is very likely persisted locally
- however, the concrete local register write path for many app settings is not
  yet identified in the tested holding-register ranges

What is not yet justified:

- claiming direct Modbus support for app configuration pages such as intensive
  ventilation runtime or keypad preset airflow targets
- assuming that app-visible settings map cleanly onto one flat range of simple
  holding registers

Current project decision as of `2026-04-01`:

- pause deeper local settings reverse engineering for now
- treat this as a parked research topic, not an active product roadmap item
- only revisit it if a new register family, payload hint, or vendor-side clue
  appears

## Latest focused measurement

First targeted `intensive` family capture on `2026-03-31`:

- baseline: `tmp/setting-captures/intensive-slave2-20260331-225558-baseline.json`
- changed in app: one intensive ventilation setting
- follow-up: `tmp/setting-captures/intensive-slave2-20260331-225742-after-app-change.json`

Observed diff shape:

- `41120..41124` and `41132` did not expose a direct new plain-value readback
- `51100..51112` changed in lockstep from `4352` to `4354`
- `51113`, `51120..51133`, `51150..51151`, and `52006..52010` changed from
  `1` to `3`

Current interpretation:

- the app change clearly triggers a local state transition on the Modbus side
- however, the visible effect currently looks like a family-wide commit or
  version bump, not an obvious decoded parameter value
- this strengthens the case that app settings are persisted locally, but also
  suggests the important values may be packed, indirect, or only visible across
  repeated multi-value experiments

Second targeted `intensive` family capture on `2026-03-31`:

- pre-change: `tmp/setting-captures/intensive-slave2-20260331-230009-pre-airflow-40.json`
- changed in app: intensive ventilation airflow to `40`
- follow-up: `tmp/setting-captures/intensive-slave2-20260331-230043-after-airflow-40.json`

Observed diff shape:

- `41120..41124` and `41132` again showed no direct plain-value readback
- `51100..51112` changed in lockstep from `4356` to `4357`
- `51113`, `51120..51133`, `51150..51151`, and `52006..52010` changed from
  `5` to `6`
- `52000..52005` changed from `4353` to `4354`

Updated interpretation:

- the second run reproduced the same broad family-level change pattern rather
  than revealing a dedicated airflow register
- the observed changes look more like commit counters, version words, or
  payload sequence markers than direct decoded setting values
- because the pre-change snapshot already differed from the earlier follow-up
  snapshot, any future experiment should always use an immediate before/after
  pair and avoid comparing across wider app interaction gaps

Third targeted `all_known` family capture on `2026-03-31`:

- baseline: `tmp/setting-captures/all_known-slave2-20260331-230620-state-40-baseline.json`
- changed in app: intensive ventilation airflow to `60`
- follow-up: `tmp/setting-captures/all_known-slave2-20260331-230702-after-airflow-60.json`

Observed diff shape:

- `41120..41124`, `41132`, and the documented config range `42000..42009`
  still showed no direct plain-value readback
- `51100..51112` changed in lockstep from `4352` to `4353`
- `51113`, `51120..51133`, and `51150..51151` changed from `11` to `12`
- `52008..52010` changed from `11` to `12`
- `52007` jumped from `11` to `4352`, effectively mirroring the `52xxx`
  family base word rather than exposing a decoded airflow target

Current interpretation after the broader capture:

- widening the capture window still did not reveal a dedicated local register
  for the chosen intensive airflow value
- the broader run reinforces the idea that app-side writes are locally
  persisted through family-level commit/meta state, not as one obvious plain
  setting register in the currently known windows

Fourth targeted `all_known` family capture on `2026-03-31`:

- baseline: `tmp/setting-captures/all_known-slave2-20260331-230702-after-airflow-60.json`
- changed in app: intensive ventilation airflow back to `40`
- follow-up: `tmp/setting-captures/all_known-slave2-20260331-230851-back-airflow-40.json`

Observed diff shape:

- `41120..41124`, `41132`, and `42000..42009` still showed no direct plain-
  value readback
- `51100..51112` changed in lockstep from `4353` to `4355`
- `51113`, `51120..51133`, and `51150..51151` changed from `12` to `14`
- `52009..52010` changed from `12` to `14`
- `52008` jumped from `12` to `4352`
- compared to the original `40` baseline, no register in the captured window
  returned to a distinct earlier plain-value shape for the airflow target

Current interpretation after the A-B-A sequence:

- the `40 -> 60 -> 40` cycle still produced only monotonic family-level meta
  changes, not a decoded local value for the chosen airflow target
- this is strong evidence that the app write is persisted locally but exposed
  to us only through commit/version-like words in the currently known windows
- the intensive-family search should therefore be parked for now unless a new
  register family, payload path, or independent hardware hint appears

## Confirmed local evidence

- documented configuration writes exist for `42000..42009`
  - humidity control thresholds and airflow bounds
  - CO2 control thresholds and airflow bounds
  - external control input airflow and delays
- runtime preset writes exist in `41120..41124` plus commit register `41132`
- additional local shadow/meta ranges exist and do move when settings or panel
  actions change:
  - `51100..51113`
  - `51120..51133`
  - `51150..51151`
  - `52000..52010`
- those shadow changes do not currently decode like direct plain-text setting
  values; they look more like meta words, counters, family payloads, or commit
  state

## Working assumptions

- the Meltem app likely writes setting families, not isolated one-off values
- one app screen may trigger a structured local payload or a write sequence
- some values may only become visible through shadow/meta ranges rather than a
  directly readable holding register with the same semantic meaning
- a complete implementation should wait for either a new register family or a
  payload-level decoding hint before more time is spent on the intensive
  family specifically

## Priority families

### 1. Intensive ventilation

Target settings:

- intensive ventilation airflow
- intensive ventilation run-on time

Why this family matters:

- it is clearly user-visible
- it is already partially represented in the integration through the runtime
  intensive start path
- the configured defaults appear to persist offline

Known local ranges worth capturing:

- `41120..41124`
- `41132`
- `51100..51113`
- `51120..51133`
- `51150..51151`
- `52000..52010`

Recommended experiment:

1. capture baseline snapshot
2. change only the intensive airflow in the app
3. capture again and diff
4. repeat with a second distinct airflow value
5. reset to original value and capture once more
6. repeat the same sequence for intensive runtime

Success condition:

- one stable address or payload position changes in line with the chosen values
- or one stable family-level sequence can be observed repeatedly

Current status:

- the first A-B-A airflow experiment is complete
- result: local persistence looks real, but no directly usable setting register
  has been identified
- recommendation: pause this family and move effort to a better control group
  or a different setting family

First keypad/LOW airflow measurement on `2026-04-01`:

- baseline: `tmp/setting-captures/keypad-slave2-20260401-000404-baseline-presets-v2.json`
- changed in app: LOW airflow target
- follow-up: `tmp/setting-captures/keypad-slave2-20260401-000540-after-low-change.json`
- immediate stability check without another app change:
  `tmp/setting-captures/keypad-slave2-20260401-000623-stability-check.json`

Observed diff shape:

- no change was visible in `40000..40025`, `40200..40209`, `41000..41029`,
  `41100..41124`, or `42000..42009`
- `51120..51133` changed together from `69` to `71`
- `51150..51151` changed from `69` to `71`
- `52010` changed from `69` to `71`
- `52008` changed from `5376` to `4352`
- `52009` changed from `5376` to `5378`, then drifted back to `5376` during
  the immediate stability check
- the `71` values in `51120..51133`, `51150..51151`, and `52010` remained
  stable during the immediate no-change recapture

Current interpretation after the first keypad run:

- unlike the intensive-family experiments, the keypad-family write left one
  small stable value block instead of only a broad monotonic commit pattern
- that block is not decoded yet, but it is a much better candidate for a real
  persisted preset/default value or a closely related payload slot
- the next best test is one more LOW change to a clearly different target value
  to see whether `51120..51133` and `52010` move proportionally

Second keypad/LOW airflow measurement on `2026-04-01`:

- baseline: `tmp/setting-captures/keypad-slave2-20260401-000623-stability-check.json`
- changed in app: LOW airflow target to a second distinct value
- follow-up: `tmp/setting-captures/keypad-slave2-20260401-000822-after-low-55.json`
- immediate stability check without another app change:
  `tmp/setting-captures/keypad-slave2-20260401-000833-stability-check-2.json`

Observed diff shape:

- the readable islands in `40000..42009` again stayed unchanged, including the
  documented config block `42000..42009`
- `51120..51133`, `51150..51151`, and `52010` changed from `71` to `73`
- the immediate no-change recapture then changed the same block again from `73`
  to `74`
- `52007..52009` moved alongside the write, but also drifted again on the
  no-change recapture

Updated interpretation after the second keypad run:

- the keypad family still looks more promising than the intensive family,
  because it produces a compact repeatable block instead of only broad
  `51100..` / `52000..` family jumps
- however, the second run weakens the earlier hypothesis that `51120..51133`
  and `52010` are a directly readable LOW airflow value or one-to-one payload
  slot for that value
- the observed `69 -> 71 -> 71 -> 73 -> 74` series now looks more like family
  metadata, commit state, or a payload sequence marker that reacts to the
  keypad-default write path, not like a stable decoded airflow target
- recommendation: pause the LOW-default hypothesis as a direct register mapping
  and only revisit this family if a wider register window or independent clue
  reveals where the actual payload value lives

### 2. Keypad and preset defaults

Target settings:

- LOW airflow target
- MEDIUM airflow target
- HIGH airflow target
- extract-only default airflow
- supply-only default airflow

Why this family matters:

- these shortcuts are exposed in the UI already
- they are the clearest user-facing gap between runtime shortcuts and persisted
  app configuration

Known local ranges worth capturing:

- `41120..41124`
- `41132`
- `51100..51113`
- `51120..51133`
- `51150..51151`
- `52000..52010`

Recommended experiment:

1. pick one shortcut only, for example LOW airflow
2. baseline capture
3. change LOW to one distinct value
4. capture and diff
5. change LOW to a second distinct value
6. capture and diff
7. only after one setting is stable, repeat for MEDIUM and HIGH

Important caution:

- do not change several shortcut defaults in one run
- otherwise it becomes hard to separate payload slots inside one family

### 3. Cross-ventilation and one-sided airflow defaults

Target settings:

- supply-air mode airflow supply
- supply-air mode airflow extract
- extract-air mode airflow supply
- extract-air mode airflow extract

Why this family matters:

- it is a plausible source for the app-side `Abluft` and `Zuluft` shortcut
  defaults
- it may explain why runtime shortcut writes work but the stored defaults are
  still unreadable

Status:

- lower priority than intensive ventilation and keypad defaults
- likely worth pursuing only after one of the two higher-priority families has
  produced a reproducible pattern

### 4. Humidity and CO2 families

Target settings:

- humidity start/min/max
- CO2 start/min/max

Why this family matters:

- these already have documented writes in `42000..42005`
- they are useful as a control group for validating that the new capture tool
  sees known local configuration changes correctly

Recommended use:

- use one humidity or CO2 setting first as a sanity check capture
- then compare the shape of known documented changes against unknown families

Latest control result on `2026-04-01`:

- a direct local Modbus write on `slave 2` changed `42000` from `70 -> 71`
  and back to `70`, with exact plain-value readback in `42000`
- the matching snapshots only changed `42000` itself and one small side effect
  in `52008..52009` (`5377 -> 5376`) on the forward step; the restore step then
  changed only `42000`
- this is a useful contrast to the intensive-default experiments, where app-
  side changes only produced broad `511xx` / `520xx` meta-word jumps without a
  directly readable value slot

## Capture workflow

Use the family capture tool in `tools/capture_setting_family.py`.

Example:

```bash
python tools/capture_setting_family.py \
  --port /dev/ttyACM0 \
  --slave 2 \
  --family intensive \
  --label baseline \
  --output-dir tmp/setting-captures

python tools/capture_setting_family.py \
  --port /dev/ttyACM0 \
  --slave 2 \
  --family intensive \
  --label airflow-90 \
  --compare-latest \
  --output-dir tmp/setting-captures
```

Suggested workflow rules:

- keep one capture directory per device or test session
- use explicit labels that describe the user action
- repeat interesting changes at least twice with different values
- prefer one changed variable per run
- always take the baseline immediately before the app-side change
- keep screenshots or handwritten notes of the app-side values outside the JSON
  snapshot files

## Stop criteria

Stop pursuing a family for now if:

- repeated two-value tests show no reproducible local pattern
- only broad counter-like changes appear without value correlation
- the experiment starts requiring noisy full-register blind scans

The point is to find a stable local write model, not to spend unlimited time
on speculative search.

Current status after the latest keypad and intensive runs:

- the stop criteria have effectively been met for now
- local persistence is well supported, but a directly usable local register
  model for app-side defaults is still missing
- further effort should return to normal integration work unless materially new
  evidence appears