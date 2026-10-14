# MELTEM observed behaviour

This document collects Meltem behaviour that was **observed or reverse
engineered** on real hardware and is not covered by the manufacturer
documentation. Examples are register quirks of the tested `M-WRG-GW` gateway
and traced app writes.

Each topic starts with the established behaviour, followed by its evidence.
A live run for an open item in [HARDWARE_BACKLOG.md](HARDWARE_BACKLOG.md) is
written up in detail there; this file states what the run established and
links to it.

Topics:

- [Gateway and request pacing](#gateway-and-request-pacing)
- [Identity and readable registers](#identity-and-readable-registers)
- [Temperature register quirk](#temperature-register-quirk)
- [Operating mode and set registers](#operating-mode-and-set-registers)
- [Target readback and measured airflow](#target-readback-and-measured-airflow)
- [Reverse-engineered app preset behavior](#reverse-engineered-app-preset-behavior)
- [Unit and link lifecycle](#unit-and-link-lifecycle)

Manufacturer facts have moved to [reference/](reference/README.md):

- Modbus settings, register map, write sequences and scaling:
  [reference/modbus.md](reference/modbus.md)
- sensor equipment per variant, type key, feature matrix and technical data:
  [reference/models.md](reference/models.md)
- ventilation programs, LEDs, frost protection and filter monitoring:
  [reference/functions.md](reference/functions.md)
- persistent device parameters (LOW / MEDIUM / HIGH, intensive, ...):
  [reference/device-parameters.md](reference/device-parameters.md)

## Gateway and request pacing

### Discovery and unit list

The gateway answers on its own Modbus address `1` and lists the units it
knows there. On the tested gateway:

- `43901 -> 6` (number of configured units)
- `43902..43917 -> [3, 2, 4, 5, 7, 6, 0, ...]` (their addresses)
- `43900..43905` are unreadable on the units themselves

On 2026-10-02 the same count and address list appeared in ten consecutive
probes. The gateway uptime increased from `2422` to `2895` seconds across a
later interval of more than five minutes.

### Gateway registers and function codes

Read-only probes on 2026-10-02 found that gateway address `1` rejected holding
registers `41998..42001` with Modbus exception `0x02` (`Illegal Data Address`).
The same gateway still answered uptime at `41019` and discovery at `43901` /
`43902..`. Function `0x04` for `41020..41021`, diagnostics `0x08` subfunction
`0`, report-server-ID `0x11`, and device-identification `0x2B/0x0E` returned
Modbus exception `0x01` on the gateway and unit `2`.

### Request pacing and block reads

- requests sent too quickly cause partial reads, stale data, or a temporary
  loss of all values; the gateway answers one request after the other
- a request gap of `0.1 s` was stable; no gap at all inflated latency heavily
  without improving reliability
- setup scans are sensitive to short timeouts
- stable block reads: airflow `41020..41021`, status `41016..41018`, and
  temperatures `41002..41005` plus a separate `41009`
- over tmodbus (since `4.0.0`), simple reads took about `16 ms` on average,
  slightly faster than with the earlier pymodbus client (`18.6..19.5 ms`)
- in the one request-gap sweep, gaps of `0.05..0.3 s` passed, while gap `0`
  timed out reading the discovery list; this is not enough evidence to reduce
  the established `0.1 s` gap
- a 24-hour read-only soak with six units saw no transport timeouts, protocol
  errors, Busy answers, or link recycles
- reading another unit while a write settles caused no failures, but raised
  read latency from about `16 ms` to up to `134 ms`

The benchmark, gap-sweep, and soak runs are written up under
[HW-7](HARDWARE_BACKLOG.md#hw-7--validate-the-tmodbus-transport-on-the-real-gateway),
the interleaved-traffic run (W-3) under
[HW-2](HARDWARE_BACKLOG.md#partial-measurement-on-2026-10-02).

## Identity and readable registers

### Product registers and readable islands

All six tested `M-WRG-II` units returned the same product registers:

- `40002 PRODUCT_ID -> 116852 (0x0001C874)`
- `40011 PRODUCT_NAME -> VMD-22RPS44`
- `40021 RECEIVED_PRODUCT_ID -> 116852 (0x0001C874)`

These registers are the most promising basis for telling the series apart.

Other diagnostic registers that returned stable values:

- `40004 SOFTWARE_VERSION`
- `40101 RF_COMM_STATUS`
- `40103 FAULT_STATUS`
- `40104 VALUE_ERROR_STATUS`

Observed software versions:

- units `1, 2, 3, 4, 6`: `2326`
- unit `5`: `2584`, probably a replacement board

The original note does not say how the units were numbered. Address `1` is
the gateway and the units answer on `2..7`, so these are probably positions
in the setup, not Modbus addresses.

The 2026-10-02 follow-up on slaves `2..7` again returned product ID `116852`;
the integration detected `ii_plain` / `basic` for all six. Software versions
were `2326` on five units and `2584` on slave `5`. Single-register scans also
confirmed readable `40101`, `40103`, `40104`, and `41030..41033`; the mode
registers `41120..41124` did not return values before any write in that
session.

On the same date, all six units returned the same current holding-register
values for `42000..42009`: `[60, 10, 60, 600, 10, 60, 12, 60, 1, 15]`.
These are a read-only snapshot, not claimed defaults. `42006=12` remains
undocumented.

Holding-register sweep on `2026-03-31` on unit `slave 2`:

- a coarse scan over `40000..49999` with windows of 10 registers found
  readable windows only in `40000..40019`, `40200..40209`, `41000..41029`, and
  `41100..41109`
- single-register reads refined this to the islands `40000..40022`,
  `40024..40025`, `40200..40209`, `41000..41029`, `41100..41113`, and
  `42000..42009`
- a block read fails as a whole as soon as one register in it is unreadable,
  which is why coarse windows hide islands

An expanded R-2 single-register scan on 2026-10-02 covered slaves `3` and `5`
(the latter is the bathroom). Both reported the same readable islands:
`40000..40022`, `40024..40033`, `40041`, `40050..40051`, `40071`,
`40100..40104`, `40109..40110`, `40120`, `40200..40209`, `41000..41034`,
`41100..41112`, and `42000..42009`. This adds readable addresses omitted from
the 2026-03-31 summary, notably `40026..40033`, `40041`, `40050..40051`,
`40071`, `40100`, `40102`, `40109..40110`, `40120`, and `41034`. Register
`41113` was not readable in that scan. The detailed output is preserved in
[`readable-register-scan-R-2.txt`](measurements/2026-10-02/readable-register-scan-R-2.txt).

Registers that did not answer on the tested setup:

- `41041 FILTER_DURATION`
- `41042 FILTER_REMAINING_PERCENT`
- `41043 FAN_RPM_EXHAUST`
- `41044 FAN_RPM_SUPPLY`
- `41050 BYPASS_MODE`
- `41051 BYPASS_STATUS`

### Readable areas with incomplete meanings

The 2026-03-31 single-register scan was on slave `2`; it establishes
readability on that unit, not identical behavior across every profile.
Potentially useful but still unclassified areas are:

- `40024..40033`, `40041`, `40050..40051`, `40071`, `40100..40104`,
  `40109..40110`, `40120`, and `40200..40209`: readable islands, mostly with
  no assigned meaning in the collected manufacturer references
- `41103..41112`: readable tail after the now-identified current-mode status
  registers `41100..41102`; its fields have not been interpreted. `41113`
  has returned both `0x05` exceptions and successful values in different
  probes.
- within the reported `41000..41029` island: `41008`, `41012..41015`,
  `41022..41026`, and `41028..41029` have no confirmed semantics. `41013` is
  used as VOC for one selected profile, but that mapping remains unverified.
- `41019` behaved as gateway uptime on address `1`; that meaning should not be
  assumed for room-unit addresses.
- `40103` and `40104` returned stable values in separate probes, but are not
  decoded or exposed by the integration
- `41032..41033` are documented fan-motor operating hours and were readable
  in the captured `41030×4` response on slave `3` (`[38083, 0, 38014, 0]`).
  They are not yet exposed by the integration; R-6 checks them against unit
  operating hours before any entity is added.

The broad scan covered `40000..49999`; it did not exhaustively scan
`50000..65535`. Later reads of `51100..51113`, `51120..51133`,
`51150..51151`, and `52000..52010` were targeted setting captures, and those
words showed substantial drift. They should not be treated as direct settings
or expanded into a blind full-range scan without a specific hypothesis.
Registers `30000` and `30002` appear in the manufacturer documentation, but
their bus readability and function code have not been verified. The planned
read-only probe is [R-8](LIVE_GATEWAY_TESTS.md); that plan explicitly forbids
writing either register.

### Registers not covered by the collected manufacturer documents

The integration uses `41013` as "VOC, supply air" (UINT16, `ppm`) for the
`O/VOC-AUL` profile. An earlier version of this file listed the register and
an `O/VOC-AUL` column (all temperatures, both humidities, CO2 and VOC) in the
sensor matrix. None of the documents collected in `docs/meltem/` contains the
register or the option. Their origin is unknown; treat both as unverified.

### Slave 5 (bathroom) compared with the other units

The user identified slave `5` as the bathroom unit and said it is the only
unit without moisture recovery. In the saved R-1 capture from 2026-10-02, the
six units shared `PRODUCT_ID=116852`, `PRODUCT_NAME=VMD-22RPS44`,
`RECEIVED_PRODUCT_ID=116852`, and identical `42000..42009` values. Slave `5`
was the one difference in software version (`2584`; the other five were
`2326`), which could reflect a replacement board but does not identify the
heat exchanger. Its operating-hour counters were lower than the other rooms;
that is usage information, not a variant code.

The paired R-2 scans on slaves `3` and `5` found identical readable address
islands, but some raw values differed. A current read-only comparison with
slave `4` and one read of the same identity fields across all six showed:

- `40009` matches the manufacturing year: the user confirmed slave `5` (the
  bathroom) was built in 2022, while the other units were built in 2021.
  `40010` is `1804` on slave `5` and `3847` on the other five in the current
  sweep; its meaning remains unknown and could be build or board metadata.
  Neither field currently proves the P/E exchanger type. `40000/40001`,
  `40100`, and `40109` also differ, with no confirmed semantics.
- `41106` is `3` on slaves `5`, `6`, and `7`, and `0` on `2`, `3`, and `4`.
  The same `0`/`3` split appeared for slaves `3`/`5` in R-2. The value is
  repeatable per-unit across the captures, but is not unique to the bathroom,
  so it does not by itself identify moisture recovery.
- `41113` returned `425` on slave `5` and `1449` on slave `4` in five repeat
  reads each (difference `1024`); the other four slaves returned exception
  `0x05` in the same sweep. It was also unavailable on slaves `3` and `5` in
  the 2026-10-02 scan, so its availability is inconsistent and its meaning
  remains unknown despite the repeatable pair values.
- `52010` was `118` on both `4` and `5` in this latest comparison. Its earlier
  per-unit offset in the idle captures was not reproduced, so it is not a
  reliable model marker.
- The current status values also differed (`41100..41102`: slave `5`
  `[3,228,0]`, slave `4` `[4,0,228]`); these are operating-state differences,
  not evidence of different exchanger hardware. The integration cannot decode
  the extract value `228` on slave `4`: it decodes to 280 m3/h, so the
  extract target is unknown while the quick mode is reported as
  `extract_only` (HW-3 in [HARDWARE_BACKLOG.md](HARDWARE_BACKLOG.md)).

The manufacturer type plate or article number remains the reliable
discriminator: `M-WRG-II P` has no moisture recovery, while `M-WRG-II E` has
an enthalpy exchanger. The known Modbus product ID/name did not distinguish
them, and the integration's `ii_*` profile records sensor capabilities, not
P/E; see [models.md](reference/models.md). Raw comparison outputs are preserved
in [the slave 5 read](measurements/2026-10-09/pe-compare-slave5.txt),
[the slave 4 read](measurements/2026-10-09/pe-compare-slave4.txt), and the
[six-slave identity read](measurements/2026-10-09/pe-product-40009-40010-slaves2-3-6-7.txt).
The wider identity reads are preserved for
[slave 5](measurements/2026-10-09/pe-identity-slave5.txt) and
[slave 4](measurements/2026-10-09/pe-identity-slave4.txt).

## Temperature register quirk

Observed on tested `M-WRG-GW` gateways:

- documented exhaust air temperature `41000/41001` behaved like extract air temperature
- documented extract air temperature `41004/41005` behaved like exhaust air temperature
- in other words: `41000` and `41004` appear effectively swapped on some setups
- treat this as a quirk of the gateway, not as a correction of the unit manual

The 2026-10-02 scan could not re-evaluate the swap: all six discovered units
were `ii_plain`; `41000` and `41002` decoded as NaN, while `41004` was a
plausible room temperature. Repeat the swap test on a sensor-equipped variant.

## Operating mode and set registers

The current operating mode and targets are read from the status map at
`41100..41102`; the set registers `41120..41124` and the apply register
`41132` are the write interface and cannot be relied on for reading.

### Read-only mode status at `41100..41102`

The ioBroker community template in
[ioBroker/modbus-templates#57](https://github.com/ioBroker/modbus-templates/pull/57)
separates pollable status registers `41100..41102` from set registers
`41120..41122` and commit `41132`. A direct FC03 read of `41100×3` succeeded
on all six M-WRG-II slaves without any writes:

| Slave | `41100` mode | `41101` supply/status subtype | `41102` extract |
| --- | ---: | ---: | ---: |
| 2 | 3 | 229 | 0 |
| 3 | 3 | 229 | 0 |
| 4 | 4 | 0 | 208 |
| 5 | 3 | 228 | 0 |
| 6 | 4 | 68 | 203 |
| 7 | 3 | 229 | 0 |

The values match the current preset/unbalanced states seen during testing.
The ioBroker template decodes mode `3` subcodes `228/229/230` as LOW/MED/HIGH,
mode `4` as unbalanced, and sensor-mode status subcodes separately from the
write values. This gives the integration a read-only source for current mode
and targets; `41120..41122` remain the write interface. The implementation
now uses this map for every supported M-WRG-S and M-WRG-II profile, based on
the user's explicit assumption that the two series behave alike. Direct
hardware validation is currently only from M-WRG-II units; the M-WRG-S path
still needs confirmation when such a unit is available. The manufacturer
manual does not document this status map, so treat it as observed/community
evidence rather than a manufacturer guarantee.

A later read-only FC03 check on 2026-10-09 returned the same three-register
values from all six slaves in about 15-17 ms each. No slave had the intensive
status pattern used by the client decoder (`mode=3, submode=227` or
`mode=0, submode=0`) during that scan. This used the locally exposed serial
interface and does not establish that USB access is manufacturer supported.

A further read-only check on 2026-10-14 compared `41100×3` with single reads
of `41100`, `41101`, and `41102` on all six connected nodes. Each block matched
its three single-register values:

| Slave | Software | `41100..41102` | `41020/41021` |
| --- | ---: | --- | --- |
| 2 | 2326 | `[3, 229, 0]` | `[30, 30]` |
| 3 | 2326 | `[3, 229, 0]` | `[30, 30]` |
| 4 | 2326 | `[3, 230, 0]` | `[60, 60]` |
| 5 | 2584 | `[3, 228, 0]` | `[30, 30]` |
| 6 | 2326 | `[3, 203, 0]` | `[30, 30]` |
| 7 | 2326 | `[3, 229, 0]` | `[30, 30]` |

The integration-like discovery reported profile `ii_plain` for all six; only
slave `4` was confirmed as M-WRG-II by the person during this run. This does
not validate M-WRG-S. The gateway product ID was `116852` on each node.

In a separate check while the user had intensive ventilation active on slave
`4`, FC03 `41100×3` returned `[0, 0, 0]` in 16.6 ms. This matches the active
pattern decoded by the integration and confirms intensive-state detection on
this M-WRG-II unit for that operating state. It does not validate M-WRG-S or
expose the configured intensive airflow/duration. The separate T-10
observation on slave `5` used the set-side `41120..41124` block and was
uncontrolled; it is not needed as evidence for this status-map result.

#### Status values without hardware evidence

The ioBroker script decodes sensor control from `41100=2` with subcodes `112`
(humidity), `176` (CO2), and `48` (automatic), and intensive from `3/227` as
well as `0/0`. None of the six units was in sensor control during the captures,
and only `0/0/0` was seen for intensive. The sensor subcodes fit the write
values `112`, `144`, and `16` with bit `32` set. The integration additionally
accepts `144` as CO2 control, which ioBroker does not list. Any other subcode
decodes to an unknown operating mode; the raw status is neither logged nor
included in the diagnostics. Open checks are listed under HW-4 in
[HARDWARE_BACKLOG.md](HARDWARE_BACKLOG.md#remaining-validation).

A user-authorized app test on slave `2` changed only the intensive run-on time
from 15 to 20 minutes and back to 15. The read-only capture found no readable
value in `41120..41124` or `41132`; only shadow/meta counters changed, and the
restore capture had no further raw differences. This did not identify a
duration register; the detailed captures and noise caveat are in
[SETTING_RE_BACKLOG.md](SETTING_RE_BACKLOG.md).

#### Why earlier hardware scans did not identify it

The register range was not missing from the scans. The 2026-03-31 holding
register sweep already found readable windows at `41100..41109`, then
single-register reads narrowed this to `41100..41113`. The capture tool
classified this only as `mode_and_runtime_state`; the raw values had no
documented meanings in the manufacturer material gathered for the project.
Meanwhile, the runtime profiler and `raw_requests` defaults focused on the
known set-register block at `41120+`, and the profiler did not request
`41100`. The ioBroker template supplied the missing semantic clue that
`41100..41102` are the current-state map and `41120+` are set registers.
This was a classification/tool-coverage gap, not a failure of the broad scan
to see the range.

### Set-register readback at `41120..41124`

The manuals only describe writing `41120..41124`. Reading them back is
unreliable on the tested M-WRG-II units, which is why the integration reads the
current state from `41100..41102` and uses the set registers for writes only:

- before any write in a session, all six units answered the five-register and
  two-register reads at `41120` and single reads of `41121` / `41122` with
  exception `0x05`; a five-minute power cycle did not change that on slave `4`
- after a balanced write (`41120=3`, `41121=40`, `41132=0`) on slave `4`, the
  two-register read at `41120` and the single read of `41121` returned the
  written values and stayed readable through one later power cycle; the
  five-register read and `41122` still returned `0x05`
- a week later, after the gateway had moved to another computer, the reads
  were refused again until the next write (W-5 on 2026-10-09); the same held
  before W-1 on 2026-10-14
- after an unbalanced restore on the same unit (`41120=4`, `41121=0`,
  `41122=160`), `41121` and `41122` both read back, while the five-register
  read still returned `0x05`
- neither function `0x04` nor a single-register FC03 read of `41120` offers a
  read-only path: FC04 returned `0x01`, even for the airflow block
  `41020×2`, and FC03 `41120×1` returned `0x05` on all six units
- after the first write of W-1 on 2026-10-14, `41120×2` read `[3, 230]` on
  slave `4` while `41120×5` still returned `0x05`; `42001..42009`, which the
  pre-test snapshot could not read, also became readable although none was
  written
- during the 24-hour T-10 soak, the set registers of slave `5` became
  readable without any Modbus write, after uncontrolled wall-button
  operations, and then read `[0, 0, 0, 3, 227]` (intensive)
- after a USB reconnect, Home Assistant's five-register read failed on all six
  units and its two-register fallback succeeded only on slave `5`, see
  [HA-host reconnect at 12:33 CEST](#ha-host-reconnect-at-1233-cest)

The individual runs, including the restore steps and the shadow-register side
effects, are written up under
[HW-4](HARDWARE_BACKLOG.md#hw-4--units-that-reject-the-five-register-mode-block).

## Target readback and measured airflow

The manuals describe `41121` only as part of the write sequence. On the tested
gateway it reads back the last written raw balanced target, once the unit
accepts the read at all (see
[Set-register readback](#set-register-readback-at-4112041124)):

- a baseline of `60 m3/h` corresponded to `41121 = 120`
- writing `64 m3/h` changed `41121` to `128` at once; `10 m3/h` and the
  restore were reflected at once as well
- on several units `41121` returned a Modbus exception until the first write
  to that unit; afterwards it was readable and followed every write
- older readings such as `230` lie outside the raw range `0..200`; they match
  the HIGH quick mode code from the
  [traced app writes](#reverse-engineered-app-preset-behavior)
- through Home Assistant, the fan percentage `63%` on an M-WRG-II was stored
  as `41121=126`, that is `63 m3/h`, not the previously assumed `64 m3/h`
  (W-17)

The status register `41101` does not echo this raw value. For a balanced
target it reports the app encoding `200 + airflow / 10`: raw targets `80` and
`120` (40 and 60 m3/h) appeared as `204` and `206`, and HIGH as `230`
(W-1 on 2026-10-14).

`41020` (extract) and `41021` (supply) show the effective airflow and lag
behind the target:

- a staged target has no effect until `41132=0` is written; in W-6 the
  airflow stayed unchanged for 20 s with `41121` already at the new value, and
  changed within about 5 s of the commit
- small balanced writes did not always show up at once; a large change such
  as `60 -> 10 m3/h` did. From a `20/20` baseline, targets 16 and 24 were not
  reached within 60 s, while 40 was reached by the first sample (W-1 on
  2026-10-02)
- a write on unit `4` from `60` to `65 m3/h` showed up in `41020..41021`
  after about 4 s, and the restore to `60 m3/h` after a few seconds; the same
  held while all six units were polled in one loop
- with one-second polling, a manual step from HIGH down to `40/40` took about
  3 to 7 s; the return to HIGH took 1 to 2 s, both with the direct
  current-level writes and with the integration's preset sequence. Under the
  full six-unit polling load (W-18), the two steps took about 7 s and 5 s
- through Home Assistant's 10-second flow polling, the return to HIGH was
  observed after anywhere from under 2 s to 27–47.5 s across five cycles; the
  long outliers are not explained
- the target readback therefore comes before measured convergence and never
  proves that the fans have reached the new airflow; the integration does not
  confirm writes from `41020/41021`

The individual runs are written up under
[HW-2](HARDWARE_BACKLOG.md#hw-2--writes-block-the-whole-gateway-for-several-seconds),
including [W-17](HARDWARE_BACKLOG.md#w-17-home-assistant-write-confirmation-on-2026-10-14)
and the
[Home Assistant airflow-lag cycles](HARDWARE_BACKLOG.md#follow-up-airflow-lag-through-home-assistant-on-2026-10-14);
the 2026-10-09 W-1 run is under
[HW-7](HARDWARE_BACKLOG.md#follow-up-measurements-on-2026-10-09).

## Reverse-engineered app preset behavior

The published Modbus manuals do not document how the Meltem app drives keypad
LED states for `LOW` / `MED` / `HIGH`, temporary intensive ventilation, or the
app-side `Abluft` / `Zuluft` shortcuts.

Manufacturer context: `LOW` / `MEDIUM` / `HIGH`, the intensive airflow and
duration, and the supply-only / extract-only airflows are documented as
persistent device parameters of the remote control and app, without any
Modbus mapping. The keypad LEDs light only for 10 s after a key press. See
[reference/device-parameters.md](reference/device-parameters.md) and
[reference/functions.md](reference/functions.md#local-controls).

Related vendor evidence from a separate Meltem Modbus-KNX document:

- the KNX communication-object model also distinguishes between
  - normal ventilation level
  - unbalanced supply air
  - unbalanced extract air
  - automatic mode
  - humidity mode
  - CO2 mode
  - intensive ventilation
- in that KNX mapping, communication object `0` carries the main mode /
  supply-side value, while object `26` carries the extract-side value for
  unbalanced operation
- documented KNX values there include:
  - `202 = automatic`
  - `203 = humidity`
  - `204 = CO2`
  - `205 = intensive ventilation`

Why this matters here:

- it supports the general Meltem concept that regulation modes, intensive
  ventilation, and unbalanced one-sided airflow are distinct control concepts
- it therefore aligns well with the integration's separation between
  `operation_mode` and `preset_mode`

Important limitation:

- this is a KNX communication-object abstraction, not the runtime Modbus
  register map of the tested USB gateway
- the KNX value encoding must therefore not be assumed to map directly onto
  holding registers such as `41120..41124`
- treat it as supporting product semantics, not as direct register evidence

Local write tracing against one `M-WRG-II` plain unit on `2026-03-30` found:

- `LOW`
  - `41120 = 3`
  - `41121 = 228`
  - `41132 = 0`
- `MED`
  - `41120 = 3`
  - `41121 = 229`
  - `41132 = 0`
- `HIGH`
  - `41120 = 3`
  - `41121 = 230`
  - `41132 = 0`
- temporary intensive ventilation
  - `41123 = 3`
  - `41124 = 227`
  - then `41132 = 0`
- app `Abluft` mode with configured airflow `30 m3/h`
  - `41120 = 4`
  - `41121 = 0`
  - `41122 = 203`
  - then `41132 = 0`
- app `Abluft` mode with configured airflow `50 m3/h`
  - `41122 = 205`
- app `Abluft` mode with configured airflow `70 m3/h`
  - `41122 = 207`
- app `Zuluft` mode with configured airflow `50 m3/h`
  - `41120 = 4`
  - `41121 = 205`
  - `41122 = 0`
  - then `41132 = 0`
- app `Zuluft` mode with configured airflow `70 m3/h`
  - `41121 = 207`

Current interpretation:

- `LOW` / `MED` / `HIGH` use fixed preset codes `228` / `229` / `230`
- temporary intensive ventilation uses a separate secondary write path on
  `41123` / `41124`
- app `Abluft` / `Zuluft` shortcuts appear to encode the configured airflow as
  `200 + airflow_in_m3h / 10` on the active side
- plain airflow writes such as `30/30` through the documented `0..200`
  scaling path do not necessarily update the same keypad LED state as the app
- the Home Assistant integration decodes these shortcuts on read but does not
  write them; since 3.0.0 it expresses supply-only and extract-only operation
  by setting one fan to zero, because the airflow stored for the shortcut is
  not readable

Important limitation:

- these mappings are observed behavior on one locally tested unit and gateway,
  not vendor-published documentation
- the official Modbus documentation available to this repository still only
  documents writable holding registers up to `42009`
- local snapshot and change-watch tests on `2026-03-30` found no visible
  writes for app-side configuration pages such as intensive-airflow settings
  in these searched ranges:
  - unit `slave 5`: `42000..42560`
  - gateway `slave 1`: `41980..42540`
- the vendor app and the tested gateway both appeared cloud-dependent in
  normal operation, which makes a cloud-mediated settings workflow plausible
- however, changed shortcut values still remain usable later from the local
  keypad, so the effective configuration must still be persisted somewhere
  locally even if that storage path is not visible in the tested holding
  registers
- confirmed local examples on the tested setup:
  - `Bedienfolie LOW = 60 m3/h` remained effective offline and still drove
    `60/60 m3/h`, with `41120..41122 = [3, 228, 0]`
  - temporary intensive airflow `= 90 m3/h` remained effective offline and
    still drove `90/90 m3/h`
- later single-register scans also found additional readable local shadow
  ranges on the tested unit:
  - `51100..51113`
  - `51120..51133`
  - `51150..51151`
  - `52000..52010`
- app configuration changes affected these shadow ranges reproducibly, but the
  values behaved like meta/commit counters or status words rather than direct
  configured airflow/runtime values
- an additional panel-side hardware check on `2026-03-31` showed that local
  `Abluft` / `Zuluft` button presses on another tested unit (`slave 2`) did
  not change `41120..41124` at all
- instead the panel-side changes appeared in the airflow and in shadow/meta
  ranges:
  - `neutral -> Abluft`
    - `41020: 20 -> 40`, `41021: 20 -> 28`
    - `51120..51133`: broad `+1` increment pattern
    - `51150..51151`: `27 -> 28`
    - `52007: 27 -> 4352`
    - `52008..52010`: `27 -> 28`
  - `neutral -> Zuluft`
    - `41020: 10 -> 0`
    - `51113: 4353 -> 4352`
    - `52008: 4352 -> 5376`
    - `52009: 31 -> 1055`
- direct raw Modbus runtime writes such as `41120 = 4`, `41121 = 0`,
  `41122 = 201` or `205`, then `41132 = 0`, still changed airflow on that
  unit but did not light the physical keypad LEDs
  - `41120..41124` read back as written: `[4, 0, 201, 0, 0]` for `Abluft 10`,
    `[4, 201, 0, 0, 0]` for `Zuluft 10`, `[4, 0, 205, 0, 0]` for `Abluft 50`
  - `Abluft 50` converged to `50/0 m3/h`
- this means the reverse-engineered `200 + airflow / 10` encoding is a valid
  runtime shortcut path, but not yet a complete model of the local panel LED
  semantics on all observed hardware
- later user verification on the same setup showed that the official Meltem
  app also leaves the physical keypad LEDs dark when switching to `Abluft` /
  `Zuluft`
- therefore missing LEDs for these two shortcuts should currently be treated
  as likely normal device behavior, not as proof that the HA integration is
  writing the wrong runtime preset registers

## Unit and link lifecycle

How the gateway and the integration behave when a unit loses power, Home
Assistant restarts, or the USB link drops:

- a physically powered-off unit did not cause timeouts: the gateway kept
  answering its airflow jobs, possibly from cached data
- opening the serial port once timed out at startup before the first request;
  the connection came up about a second later (2026-10-08)
- with the shared read-telegram pacing, Home Assistant restarts kept every
  read at least the configured interval apart, and reload, unload, and
  re-enable released and reacquired the link cleanly (2026-10-09)
- after a USB loss, the running integration reopened the same port by itself
  after about 80 s; airflow reads first returned exception `0x05` on all
  units and recovered over up to about eight minutes (2026-10-09)

The dated sections below are the evidence. What they contribute to the
transport validation is summarised under
[HW-7](HARDWARE_BACKLOG.md#hw-7--validate-the-tmodbus-transport-on-the-real-gateway).

### Reads while a unit is powered off

In two H-1 runs, slave `4` was switched off while the integration client
polled all units. Its airflow jobs kept completing in 2-3 requests (35 jobs in
the first run), without transport timeouts or link recycles. Neither run
logged the returned values, so whether the gateway served cached data is
unknown. The 2026-10-09 run also exceeded the five-minute limit (6 min 5 s)
and is not a valid pass; it must not be repeated that way.

After power-up in the first run, `41020..41021` read `[80, 0]`, matching the
Meltem app and the physical airflow, while the then-used set-register read
still decoded manual target `20`. This differs from the `20/20` measured after
earlier W-6 restores; no writes were made after that final power cycle. The
runs are written up as HW-7 measurement 2 under
[HW-7](HARDWARE_BACKLOG.md#partial-measurement-hw-7-on-2026-10-02).

### Home Assistant restart on 2026-10-08

The Core debug export `home-assistant_2026-10-08T18-44-34.916Z.log` (local,
not archived) captured a restart of HA `2026.10.0` with integration `4.0.0`, six units, and
the configured rate `3 req/s`. Log timestamps below are local CEST (UTC+2).

- At `20:38:11.133`, opening the serial connection timed out while `serialx`
  was waiting for the executor to reconfigure the port. The connection was
  established at `20:38:12.048`, before the first RTU request. The log does
  not establish whether executor contention, USB readiness, or another cause
  delayed the open; this is not an unanswered register read.
- Gateway validation and the initial full room scan sent 58 RTU requests
  between `20:38:12.079` and `20:38:25.360`, including up to eight sends in a
  calendar second. The first coordinator refresh completed at `20:38:25.477`
  in `12.788 s`, with `success: True`. The old rate cap limited room/job
  starts rather than individual read telegrams.
- Every room's first airflow read succeeded. All 288 recorded coordinator
  refreshes through `20:44:34.517` reported success, but mode-register reads
  still returned exception `0x05`: full/short/single fallbacks on slaves
  `2`, `3`, `6`, and `7`; the full block on slave `5`, whose short read worked.
  This is consistent with the separate [set-register findings](#set-register-readback-at-4112041124),
  not evidence of a transport timeout or recovery of every read group.

The person reported a transient "Datenlesestatus" problem on each restart.
The export does not contain the entity's exact on/off transition, so neither
the opening timeout nor the startup traffic is proven to cause that indication.
The progressive startup and shared read pacing described in
[DEVELOPER.md](DEVELOPER.md#polling-strategy) address the observed burst; their
effect on the real restart still needs the H-9 check.

### Home Assistant startup on 2026-10-09

Three H-9 startup runs captured HA `2026.10.0` with integration `4.0.1`, six
units, and an effective request rate of `3 req/s`. Local log timestamps are
CEST (UTC+2).

- The effective rate appeared at `10:39:23.827`, `10:50:40.714`, and
  `10:59:30.944` for runs R1, R2, and R3. The first airflow reads for all six
  units completed in the ranges `10:39:26..10:39:40`,
  `10:50:43..10:50:52`, and `10:59:33..10:59:45`, respectively.
- The first filter reads for all units completed by `10:40:21`, `10:51:12`,
  and `11:00:06`; the first operating-hours reads completed by `10:40:32`,
  `10:51:19`, and `11:00:19`.
- The full logs contain 369, 378, and 367 function-`0x03` sends for R1/R2/R3.
  The shortest intervals between successive sends were `350`, `349`, and
  `348 ms`; none was shorter than the `333 ms` interval required at the
  configured rate.
- Each diagnostics snapshot showed six rooms with state, successful
  coordinator updates, no unavailable rooms, a null `last_job_error`, and
  zero consecutive transport timeouts and link recycles.
- The mode-register exception `0x05` persisted on slaves `2`, `3`, `4`, `6`,
  and `7`; slave `5` had successful mode-group reads at each snapshot. The
  activity export shows the aggregate data-health entity changing from off
  to on for five rooms shortly after startup in R1 and R2; the one that
  remained off corresponds to the successful mode-group reads. These
  exceptions are answered Modbus responses, not transport timeouts. The R3
  activity export was not included, so its aggregate entity history cannot be
  checked.

The follow-up reload/unload capture (test4) also showed a clean link
lifecycle. On reload, the serial connection closed at `11:13:19.374` and
reopened at `11:13:19.481`. On deactivation, the final RTU request was at
`11:16:16.835`; the serial link closed by `11:16:18.100`. No RTU requests
were logged while the integration was unloaded. After re-enabling, the link
reopened at `11:17:08.929`, the first request followed at `11:17:08.932`, and
all six units had successful first airflow-read responses by `11:17:18.266`.
The 112 function-`0x03` requests after re-enable had a minimum spacing of
`349 ms`. No warnings, errors, or link-recycle markers appeared in the
captured logs. Mode-group reads still received exception `0x05` responses;
these are Modbus replies rather than transport timeouts. A diagnostics export
was not supplied for this run, so coordinator counters cannot be confirmed.

Together, the startup and reload/unload captures support observed pacing,
first-pass read progress, and clean release/reacquisition during HA lifecycle
changes. H-9 remains partial: the competing-hub port-conflict check (skipped)
and comparison with the previous release were not performed. No 24-hour soak
was performed.

### USB disconnect and port recovery on 2026-10-09

The serial port was confirmed as `/dev/ttyACM0`; P-3 discovered six nodes
`[3, 2, 4, 5, 7, 6]`. T-7 confirmed exclusive access: a competing P-3 process
exited with code `2` while the watcher held the port, and P-3 reopened it
successfully 20/20 times after the watcher stopped.

During H-3, the standalone polling helper reported a lost serial connection
and exited with code `2` after the USB adapter was unplugged. Following
reconnection, the port name remained `/dev/ttyACM0` and a new P-3 probe
succeeded. The first post-reconnect probe reported gateway uptime of 7 seconds,
and immediate airflow reads returned exception `0x05` for all six slaves. The
person confirmed normal fan operation at that time.

A later H-3 follow-up used the running Home Assistant integration; its logs
were provided locally and are not archived in this repository. The USB loss
was reported at `11:25:08.732`.
After six consecutive port-open failures and backoffs up to 40 seconds, the
same serial path reopened automatically at `11:26:28.475`, and the coordinator
logged the gateway reachable again. This confirms transport-level
reconnection by the running client. The later capture continues through
`11:35:36.041` and shows gradual airflow-read recovery after initial
`0x05` responses: successful reads from register `41020` first returned for
slave `6` at `11:28:22.805`, slave `3` at `11:30:22.989`, slave `2` at
`11:31:07.709`, slave `5` at `11:32:39.926`, slave `4` at `11:32:47.958`,
and slave `7` at `11:34:38.787`. At the end of the capture, the latest
airflow read for each of the six slaves had succeeded.

No successful mode-block responses were observed after reconnect: recorded
reads at `41120` / `41121` returned exception `0x05` across all six units.
The latest logged attempts vary by unit because of mode-read backoff. A
per-group `Datenlesestatus` state snapshot at `11:56:06 CEST` confirms this
for one room: `flow_control` and `intensive` each had three consecutive
failures, no successful read in the current coordinator state, and
`stale=true`, with the last error identifying the five-register read at
`41120` returning exception code `5`. In that same snapshot, `flow`, `status`,
`temperature`, `filter`, and `hours` were fresh and had zero failures; no
write outcome was pending. A six-room snapshot at `12:00 CEST` showed the same
pattern on every room: `flow_control` and `intensive` had three consecutive
failures, were stale, and reported the `41120` count-5 exception; all other
reported read groups had zero failures and were not stale. Five rooms had no
successful `flow_control` read in the current coordinator state; one room's
last successful read was at `11:25:04 CEST`, just before the USB loss. The
previously shared room state had already changed to `Problem` at `11:17:39
CEST`, about seven minutes before the disconnect, so that problem state did
not originate with this disconnect. The precise USB replug time was not
recorded; airflow recovery took up to about eight minutes after the transport
reopened, so the H-3 one-airflow-round recovery criterion was not demonstrated.
The `link_recycles` counter cannot be confirmed.

#### HA-host reconnect at 12:33 CEST

The next core log shows RTU traffic resuming at `12:34:12.479`; by its end at
`12:51:50.849`, the latest airflow read had succeeded for all six units.
Several earlier airflow reads in this window still returned `0x05` on slaves
`2`, `3`, `4`, `5`, and `6`; slave `7` had no such failures in the window.
The latest temperature and status-block reads also succeeded. The five-register
mode read at `41120` returned `0x05` on all six units. Its two-register
fallback continued returning `0x05` on slaves `2`, `3`, `4`, `6`, and `7`;
slave `5` returned `[3, 228]` successfully on 42 of 44 attempts. The direct
W-5 fallback previously observed on slave `4` was therefore not observed by HA
after this reconnect.

At `12:44:38`, the HA core log records five acknowledged function-`0x06`
writes to slave `5`: `41123=0`, `41124=0`, `41120=3`, `41121=228`, and
`41132=0`. This exactly matches the integration's LOW-preset write sequence.
The person identified the write as coming from the bathroom humidity-control
automation and confirmed no manual bus write or button press. The write was
followed by successful short mode reads on slave `5`, but the full
five-register read remained unavailable.

Per-room state attributes captured at `13:01 CEST` show the current
`Datenlesestatus` split: five rooms are `on`, each with three consecutive
`flow_control` and `intensive` failures and `stale=true` from the
`41120` count-5 exception. Their `flow`, `status`, `temperature`, `filter`,
and `hours` groups are not stale. The bathroom is `off`: its `flow_control`
read succeeded with zero failures after the LOW preset write, and `intensive`
has no attempt because the short-read fallback marks that optional group
skipped. The bathroom's `writes.preset_mode` outcome is confirmed with both
expected and actual value `low`. A single earlier filter-block `0x05` on each
room has only one consecutive failure and is not stale.
