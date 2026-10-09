# MELTEM observed behaviour

This document collects Meltem behaviour that was **observed or reverse
engineered** on real hardware and is not covered by the manufacturer
documentation. Examples are register quirks of the tested `M-WRG-GW` gateway
and traced app writes.

Manufacturer facts have moved to [reference/](reference/README.md):

- Modbus settings, register map, write sequences and scaling:
  [reference/modbus.md](reference/modbus.md)
- sensor equipment per variant, type key, feature matrix and technical data:
  [reference/models.md](reference/models.md)
- ventilation programs, LEDs, frost protection and filter monitoring:
  [reference/functions.md](reference/functions.md)
- persistent device parameters (LOW / MEDIUM / HIGH, intensive, ...):
  [reference/device-parameters.md](reference/device-parameters.md)

## Registers not covered by the collected manufacturer documents

The integration uses `41013` as "VOC, supply air" (UINT16, `ppm`) for the
`O/VOC-AUL` profile. An earlier version of this file listed the register and
an `O/VOC-AUL` column (all temperatures, both humidities, CO2 and VOC) in the
sensor matrix. None of the documents collected in `docs/meltem/` contains the
register or the option. Their origin is unknown; treat both as unverified.

Read-only probes on 2026-10-02 found that gateway address `1` rejected holding
registers `41998..42001` with Modbus exception `0x02` (`Illegal Data Address`).
The same gateway still answered uptime at `41019` and discovery at `43901` /
`43902..`. Function `0x04` for `41020..41021`, diagnostics `0x08` subfunction
`0`, report-server-ID `0x11`, and device-identification `0x2B/0x0E` returned
Modbus exception `0x01` on the gateway and unit `2`.

## Discovery and unit list

The gateway answers on its own Modbus address `1` and lists the units it
knows there. On the tested gateway:

- `43901 -> 6` (number of configured units)
- `43902..43917 -> [3, 2, 4, 5, 7, 6, 0, ...]` (their addresses)
- `43900..43905` are unreadable on the units themselves

On 2026-10-02 the same count and address list appeared in ten consecutive
probes. The gateway uptime increased from `2422` to `2895` seconds across a
later interval of more than five minutes.

## Mode reads after a power cycle

On 2026-10-02, slave `4` was powered off for about five minutes, then read
immediately after power-up. The five-register read at `41120`, the two-register
fallback, and single-register reads at `41121` / `41122` all still returned
Modbus exception `0x05`. No Modbus writes were made.

The unit answered `41020..41021` on the first post-power-up read and on five
consecutive samples. They reported `20/20 m3/h`, matching the Meltem app.
Five minutes of further polling did not change the airflow reading or produce
transport timeouts. This is an observation for this one unit, not a conclusion
that all units behave the same after power loss.

After an explicitly authorized write sequence on that unit
(`41120=3`, `41121=40`, `41132=0`), the short read at `41120` returned
`[3, 40]` and `41121` returned `40`. The five-register read at `41120` and
single read at `41122` still returned exception `0x05` at 1, 10, and 60
seconds. The integration decoded manual balanced operation at `20/20 m3/h`.
This does not establish whether the five-register block is unsupported on all
units; the post-write behavior has only been checked on slave `4`.

After a second five-minute power cycle, the short read still returned
`[3, 40]`, `41121` still returned `40`, and `41020..41021` remained at
`20/20 m3/h`. The five-register read and `41122` still returned exception
`0x05`. The integration continued to decode manual balanced operation.

## W-5 follow-up on 2026-10-09

On slave `4`, the pre-write airflow read returned `[80, 0]` (`41020` extract,
`41021` supply) and `41016=0`. All R-9 mode reads returned exception `0x05`.
Because `41021` was zero, it could not safely serve as the balanced test
target; with approval, W-5 instead wrote `41120=3`, `41121=40`,
`41132=0` (manual balanced `20/20 m3/h`).

At 1, 10, and 60 seconds, the two-register read returned `[3, 40]` and
`41121` returned `40`; airflow was `20/20 m3/h` and `41016` remained `0`.
The five-register read at `41120` and single read at `41122` still returned
exception `0x05`. This confirms that the write unlocked the short basic mode
and target read, but not the five-register block or `41122`.

The unit was restored to the pre-test extract/supply state with
`41120=4`, `41121=0`, `41122=160`, `41132=0`. After two minutes,
`41020..41021` returned `[80, 0]` and `41016=0`; the short read returned
`[4, 0]`, `41121=0`, and `41122=160`, while the five-register read still
returned `0x05`. The first restore attempt had the supply/extract directions
reversed and briefly produced `[0, 80]`; it was corrected before the final
verification. The before/after all-known snapshots also show changes in
`511xx` and `52008..52010`; those shadow values were not written or restored,
and their cause is not established.

A read-only FC04 probe on slave `4` returned exception `0x01` for
`41120×5`, `41120×2`, `41121`, and `41122`. A control read of known airflow
registers `41020×2` also returned `0x01`, so this unit does not expose these
values through a separate input-register map. A single-register FC03 read of
`41120×1` returned exception `0x05` on all six slaves; reducing the read
length does not provide a read-only unlock path.

## Read-only mode status at `41100..41102`

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

In a separate check while the user had intensive ventilation active on slave
`4`, FC03 `41100×3` returned `[0, 0, 0]` in 16.6 ms. This matches the active
pattern decoded by the integration and confirms intensive-state detection on
this M-WRG-II unit for that operating state. It does not validate M-WRG-S or
expose the configured intensive airflow/duration. The separate T-10
observation on slave `5` used the set-side `41120..41124` block and was
uncontrolled; it is not needed as evidence for this status-map result.

### Why earlier hardware scans did not identify it

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

## Reads while a unit is powered off

In the H-1 follow-up, the integration client completed 35 airflow jobs for
slave `4` while it was physically off, using 2-3 requests per job and reporting
no read-group error. The run did not log returned airflow values, so whether
the gateway served cached data remains unknown.

After power-up, `41020..41021` repeatedly returned `[80, 0]`, while
`41120..41121` still decoded manual target `20` and the status flags were `0`.
The person confirmed the Meltem app and physical airflow also showed `80/0`,
with no unexpected behavior. This differs from the `20/20` measured airflow
after earlier W-6 restores; no writes were made after this final power cycle.

In the 2026-10-09 H-1 follow-up, slave `4` was again switched off while the
polling helper ran. The unit remained off for 6 min 5 s, exceeding the test
plan's five-minute maximum; this deviation must not be treated as a valid H-1
pass or repeated. The helper reported completed airflow jobs without
transport timeouts or link recycles, but did not record the returned values.
Whether the gateway served cached data is therefore still unknown.

## Mode-read change during T-10

During the 2026-10-02/03 T-10 soak, slave `5`'s airflow jobs first reported
mode-group exception `0x05` and later began succeeding. After the soak, five
full reads of `41120..41124` returned `[0, 0, 0, 3, 227]`; `41020..41021`
returned `75/75 m3/h`, and the integration decoded `intensive_active=True`.
The user reported several wall-button operations on slave `5` during the run.
No Modbus writes were sent to it, but the button actions were not timestamped,
so their relation to the read change and intensive state is unknown. The
24-hour job counts and transport outcome are summarized under
[HW-7](HARDWARE_BACKLOG.md#hw-7--validate-the-tmodbus-transport-on-the-real-gateway).

## Home Assistant restart on 2026-10-08

The Core debug export `home-assistant_2026-10-08T18-44-34.916Z.log`
captured a restart of HA `2026.10.0` with integration `4.0.0`, six units, and
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
  This is consistent with the separate [mode-read findings](#mode-reads-after-a-power-cycle),
  not evidence of a transport timeout or recovery of every read group.

The person reported a transient "Datenlesestatus" problem on each restart.
The export does not contain the entity's exact on/off transition, so neither
the opening timeout nor the startup traffic is proven to cause that indication.
The progressive startup and shared read pacing described in
[DEVELOPER.md](DEVELOPER.md#polling-strategy) address the observed burst; their
effect on the real restart still needs the H-9 check.

## Home Assistant startup on 2026-10-09

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

## USB disconnect and port recovery on 2026-10-09

The serial port was confirmed as `/dev/ttyACM0`; P-3 discovered six nodes
`[3, 2, 4, 5, 7, 6]`. T-7 confirmed exclusive access: a competing P-3 process
exited with code `2` while the watcher held the port, and P-3 reopened it
successfully 20/20 times after the watcher stopped.

During H-3, the standalone polling helper reported a lost serial connection
and exited with code `2` after the USB adapter was unplugged. Following
reconnection, the port name remained `/dev/ttyACM0` and a new P-3 probe
succeeded. The first post-reconnect probe reported gateway uptime of 7 seconds,
and immediate airflow reads returned exception `0x05` for all six slaves.

A later H-3 follow-up used the running Home Assistant integration; its logs
are in `tmp/tests/abziehtest/`. The USB loss was reported at `11:25:08.732`.
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

### HA-host reconnect at 12:33 CEST

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

## Product registers and readable islands

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

Registers that did not answer on the tested setup:

- `41041 FILTER_DURATION`
- `41042 FILTER_REMAINING_PERCENT`
- `41043 FAN_RPM_EXHAUST`
- `41044 FAN_RPM_SUPPLY`
- `41050 BYPASS_MODE`
- `41051 BYPASS_STATUS`

## Readable areas with incomplete meanings

The 2026-03-31 single-register scan was on slave `2`; it establishes
readability on that unit, not identical behavior across every profile.
Potentially useful but still unclassified areas are:

- `40024..40025` and `40200..40209`: readable islands with no assigned
  meaning in the collected manufacturer references
- `41103..41113`: readable tail after the now-identified current-mode status
  registers `41100..41102`; its fields have not been interpreted
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

## Slave 5 (bathroom) compared with the other units

The user identified slave `5` as the bathroom unit and said it is the only
unit without moisture recovery. In the saved R-1 capture from 2026-10-02, the
six units shared `PRODUCT_ID=116852`, `PRODUCT_NAME=VMD-22RPS44`,
`RECEIVED_PRODUCT_ID=116852`, and identical `42000..42009` values. Slave `5`
was the one difference in software version (`2584`; the other five were
`2326`), which could reflect a replacement board but does not identify the
heat exchanger. Its operating-hour counters were lower than the other rooms;
that is usage information, not a variant code.

The all-known baseline/idle captures do show differences in shadow/meta
registers, including `52010`: slave `5` returned `4353` then `4366` in the two
idle captures, while the other slaves returned `48` then `61`. Both groups
advanced by `13`; this stable offset is worth testing against another
confirmed unit, but the range also drifts and has no known feature semantics.
It is not enough to label the bathroom's exchanger type.

The saved single-register R-2 sweep across `40000..40300` was on slave `3`,
not the bathroom; the bathroom therefore has no comparable raw scan saved
for `40024..40025`, `40200..40209`, or other unmapped fields. The observed
Modbus product ID/name did not distinguish the P/E exchanger type. The
manufacturer type plate or article number is the reliable discriminator:
`M-WRG-II P` has no moisture recovery, while `M-WRG-II E` has an enthalpy
exchanger. The integration's `ii_*` profile only records sensor capabilities,
not P/E; see [models.md](reference/models.md).

## Request pacing and block reads

- requests sent too quickly cause partial reads, stale data, or a temporary
  loss of all values; the gateway answers one request after the other
- a request gap of `0.1 s` was stable; no gap at all inflated latency heavily
  without improving reliability
- setup scans are sensitive to short timeouts
- stable block reads: airflow `41020..41021`, status `41016..41018`, and
  temperatures `41002..41005` plus a separate `41009`

The 2026-10-02 A-B-A-B comparison covered six simple read scenarios with 1,440
requests per transport. The pre-4.0.0 pymodbus runs had 100% successful
requests, average latencies from `18.6` to `19.5 ms`, and p95 from `19.6` to
`19.8 ms`. The current tmodbus runs also had 100% successful requests, average
latencies around `15.8 ms`, p95 from `15.9` to `16.8 ms`, p99 `16.7 ms`, and
maximum `16.9 ms`. No protocol or desynchronization errors appeared in those
scenarios.

In the request-gap sweep, gap `0` timed out while reading the gateway's
discovery list at `43902`; gaps `0.05..0.3 s` each completed 60/60 requests.
This was one sweep, so it is not enough evidence to reduce the established
`0.1 s` request gap.

On 2026-10-02, W-1 on slave `4` changed the balanced target from `20` to
`16`, `24`, and `40 m3/h` (`41121=32`, `48`, and `80`). Each target appeared in
the first post-write sample about one second later; each sample read itself
took about `0.48 s`. For targets 16 and 24, measured airflow `41020..41021`
stayed at `20/20 m3/h` for the full 60-second observations. At target 40, the
measured airflow changed to `40/40` by the first sample and stayed there for
the 60-second observation. All runs restored the target to `20`; the
integration decoded manual balanced `20/20` after restoration.

W-2 then applied a target of `30 m3/h` from a `20/20` baseline. Across three
repetitions each at configured settle delays of `0`, `0.5`, `1.0`, and `1.5 s`,
the first integration target poll returned `30` every time, including at
`0 s`. The immediate `41020..41021` measured-airflow block still read `20/20`;
the target readback is not evidence that the fans have already reached the new
airflow.

During W-3, a second task read slave `3`'s airflow registers every `0.5 s`
while slave `4` cycled between target `30` and restored target `20`. The
primary target confirmation and restores succeeded in all 12 trials, and all
30 reads on slave `3` returned `[30, 30]`. The idle baseline was
`15.4..16.7 ms`; during interleaved traffic reads took up to `134.4 ms`.
There were no read failures or link recycles.

The T-10 soak ran read-only for 8,640 rounds over 24 h 1 min with six units.
All 288 transport diagnostics showed zero consecutive timeouts and link
recycles; no timeout, protocol/desynchronization, or Busy errors were logged.
Temperature, status, and slow plans succeeded on every unit. Airflow jobs
reported the known mode-group exception `0x05` on slaves `2`, `3`, `6`, and
`7` for the full run and on slave `5` for 6,228 jobs; on slave `5`, 2,412
airflow jobs later succeeded. The user reported operating slave `5`'s wall
button several times during the soak, so that transition cannot be attributed
to polling alone. Slave `4` airflow jobs succeeded throughout. The T-10
transport findings are strong, but the proposed 99.5% total-job threshold was
not met because the mode-group failures remain in the job results.

The simple request-scenario and request-gap baselines above were measured with
pymodbus. HW-7 in [HARDWARE_BACKLOG.md](HARDWARE_BACKLOG.md) repeats those
benchmarks with the shared Modbus connection.

In the 2026-10-09 follow-up, the first tmodbus run completed all six direct
request scenarios (720 requests total) without errors; mean latency was about
`15.6..15.8 ms` and p95 about `15.7..15.9 ms`. This was not reproducible
after a later USB reconnect: direct airflow reads then returned exception
`0x05` across all six slaves on both the baseline and current runs. The
baseline integration-like full/scheduler benchmark also failed during
discovery because the pinned pymodbus API did not accept its `slave` keyword,
and it sent no poll requests. These results do not form a valid full
transport comparison.

## Target readback and measured airflow

The manuals describe `41121` only as part of the write sequence. On the tested
gateway it reads back the last written raw balanced target:

- a baseline of `60 m3/h` corresponded to `41121 = 120`
- writing `64 m3/h` changed `41121` to `128` at once; `10 m3/h` and the
  restore were reflected at once as well
- on several units `41121` returned a Modbus exception until the first write
  to that unit; afterwards it was readable and followed every write
- older readings such as `230` lie outside the raw range `0..200`; they match
  the HIGH quick mode code from the
  [traced app writes](#reverse-engineered-app-preset-behavior)

`41020` (extract) and `41021` (supply) show the effective airflow and lag
behind the target:

- small balanced writes did not always show up at once; a large change such
  as `60 -> 10 m3/h` did
- a write on unit `4` from `60` to `65 m3/h` showed up in `41020..41021` after
  about 4 s, and the restore to `60 m3/h` after a few seconds; the same held
  while all six units were polled in one loop
- in W-6 on `2026-10-02`, unit `4` stayed at `20/20 m3/h` for 20 s while
  `41120=3` and `41121=80` were staged; after `41132=0`, it reached `40/40`
  within about 5 s. Restoring `41121=40` and writing `41132=0` returned it to
  `20/20` within a few seconds; status/error flags stayed clear
- this W-6 sequence showed no airflow change before the apply write even
  though `41121` already read `80`; target-register readback alone does not
  mean the requested airflow has been applied
- in W-1 on `2026-10-09`, slave `4` started at measured `80/0 m3/h`. The
  requested `20 m3/h` target read back as `41121=40` by the first observation;
  measured airflow remained `80/0` for the first five observations and was
  `20/20` by observation six. The helper then restored its own target to off.
  The person manually restored `80/0`, which a later read confirmed. The
  post-restore snapshot had multiple shadow-register differences, whose cause
  was not isolated.

## Temperature register quirk

Observed on tested `M-WRG-GW` gateways:

- documented exhaust air temperature `41000/41001` behaved like extract air temperature
- documented extract air temperature `41004/41005` behaved like exhaust air temperature
- in other words: `41000` and `41004` appear effectively swapped on some setups
- treat this as a quirk of the gateway, not as a correction of the unit manual

The 2026-10-02 scan could not re-evaluate the swap: all six discovered units
were `ii_plain`; `41000` and `41002` decoded as NaN, while `41004` was a
plausible room temperature. Repeat the swap test on a sensor-equipped variant.

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
