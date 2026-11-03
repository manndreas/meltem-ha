# Meltem app catalog: static analysis

These are leads from a local, static inspection of the Android app bundle, not
manufacturer Modbus documentation or new hardware measurements. The app was
not installed or executed for this analysis.

Subsequent, separate hardware checks of selected leads are recorded in
[MELTEM.md](MELTEM.md#app-derived-register-survey-on-2026-11-03) and the
[app time-program comparison](MELTEM.md#app-time-program-comparison-on-2026-11-03).

## Source and limitations

- Package `com.meltem.cloudapp`, app version `1.17`, version code `117`.
- Source: the user-supplied APKPure XAPK, SHA-256
  `aab569135d6d1988e449026b138db12444788d8a053d44b8bbfada56c7141f1d`.
  This is a third-party distribution; its authenticity was not independently
  verified. The archive and app code are not included in this repository.
- The app uses a product ID returned in its cloud device list to look up
  entries in its bundled product catalog. The inspected path does not prove
  that this cloud ID always equals the unit's Modbus `40002` value.
- The app hides every unit whose cloud product ID is not in its catalog, and
  recognizes a unit's QR code by the catalog's product identifier. See
  [APK_ANALYSIS.md](APK_ANALYSIS.md#unit-qr-code).
- The catalog had two product entries in this bundle. This is not evidence
  that no other products or IDs exist.

## Product entries

| App catalog ID | Decimal | App series label | App product identifier | Local evidence |
| --- | ---: | --- | --- | --- |
| `0x0001C874` | 116852 | `M-WRG-II` | `VMD-22RPS44` | Six M-WRG-II units returned the same ID at `40002` and identifier at `40011`. |
| `0x0001C853` | 116819 | `M-WRG` | `VMD-22RES22` | No M-WRG-S unit was available to confirm these local registers. |

The M-WRG-II observations are recorded in
[MELTEM.md](MELTEM.md#product-registers-and-readable-islands); they support the
mapping for those units, not a general guarantee about all variants. Neither
entry identifies a specific exchanger, sensor equipment, or exact model
variant. In particular, tested P and E units shared the same product fields.

The integration shows the family from matching catalog entries during setup,
and warns when the selected profile belongs to a different family. The warning
can be confirmed because the older-family mapping is unverified. Unknown IDs
do not trigger a warning. The product identifier is also used as the Home
Assistant device's model identifier, retaining the raw numeric ID as hardware
version. The selected profile still determines the device model and supported
entities; even the series is not automatically selected from the catalog.

To confirm the M-WRG entry, compare `40002` and `40011` on an M-WRG-S unit
with its type plate, as described in
[R-1](LIVE_GATEWAY_TESTS.md#r-1--product-and-identity-registers).

## Sensor-capability detection

The app's `M-WRG` and `M-WRG-II` product entries use the same active-register
list. It includes the unit's exhaust humidity register `41006` and exhaust
CO2 register `41007`. The app requests those live values and checks them
against the per-register metadata. The range endpoints are not both inclusive:
the check uses strict comparisons for nonzero bounds, while a zero minimum is
treated as no lower bound:

| Register | App label | Values accepted by the app's check | Value that marks the feature disabled |
| --- | --- | --- | ---: |
| `41006` | Exhaust humidity | `2..99` | `239` |
| `41007` | Exhaust CO2 | `0..32765` | `32767` |

For both registers, the app first requires a numeric value and address, then
checks the configured range. If the range check fails and the raw value is
exactly the listed sentinel, it marks humidity or CO2 as disabled. Those flags
are passed from the live device screen to time-program screens, so this is
runtime sensor-value detection used to gate controls. It is not a product-ID
lookup for `-F` / `-FC` variants.

The flags disable the matching mode buttons in the overview and the schedule
editor. AUTO is disabled when **either** flag is set, so the app treats
automatic mode as needing both sensors; CO2 and RH depend only on their own
flag. The settings pages are not gated. See
[APK_ANALYSIS.md](APK_ANALYSIS.md#mode-buttons-and-displayed-state).

The classification is narrow: an unreadable value or a different
out-of-range value is not the same as the explicit sentinel. In particular,
the bundle does not provide a robust, separate “unknown” state for those
cases. Its limits also differ from this integration's setup probe; app
metadata should not be copied as a Modbus specification without hardware
confirmation.

## Relevance to this integration

The setup probe already checks local values from `41006` (humidity),
`41007` (CO2), `41011` (supply humidity), and `41013` (VOC) to suggest a
capability suffix. It does not derive or silently change the selected profile.
That is preferable to treating app sensor flags as proof of the exact `-F` or
`-FC` model. Its existing plausible-value bounds already reject the app's
`41006 = 239` and `41007 = 32767` sentinels; focused setup-probe regression
tests now preserve that behavior.

No further change to the detection thresholds is justified by the bundle: its
exclusive humidity limits differ from the integration's plausibility policy
and are not confirmed as manufacturer limits. Keep communication errors and
other implausible values distinct from a confirmed missing sensor, and retain
manual profile confirmation. A runtime capability UI or repair hint would be
a separate feature and should use fresh read-health data rather than changing
the configured profile or entity set from one poll.

## Register and mode leads

The app's table and code suggest the following unit-side readings. Labels,
access flags, and numerical mode codes are app metadata, not proof that local
Modbus reads or writes are supported or safe:

| Unit register | App-derived lead |
| --- | --- |
| `41007`, `41012` | App labels extract- and supply-air CO2 in ppm. The integration exposes `41007` on CO2-capable profiles and `41012`, disabled by default, only on the VOC profile; the manufacturer's unit matrix gives `-FC` units an extract-air CO2 sensor only. |
| `41008`, `41013` | App labels extract- and supply-air VOC in ppm. The integration exposes `41013` on the VOC profile and `41008` there as a disabled-by-default sensor. |
| `41015` / `41019` | Speed capability bits / actual speed mode; the table marks `41019` for `0x0001C853`. Neither is read by any app code. |
| `41100` | One table entry is keyed by `41100` but contains `41101` as its register field. The app looks entries up by key, so this has no visible effect, but it limits confidence in the table. |
| `41103..41106` | Temporary override mode and submodes, followed by scheduler state. The app falls back to `41103` / `41104` when `41100` / `41101` are `0`. |
| `41113` / `41133` | Legacy mode word: speed in bits `15..12`, mode in bits `11..8` (`1` continuous, `4` / `5` cross-air in / out, and others), `169` in the low byte of a write. Only the MANUAL button writes `41133`; the `41113` decode result is discarded. Local `425` (`0x01A9`) and `1449` (`0x05A9`) fit modes `1` and `5`, see [HW-12](HARDWARE_BACKLOG.md#hw-12--legacy-mode-word-41113). This does not establish that writing `41133` is active or safe through the local gateway. |
| `42006` | Priority override mode, marked read-write by the app; same name as service tag `95`. The surveyed local values `42006..42009 = 12, 60, 1, 15` equal the app defaults of tags `95`, `54`, `55` and `56`. No local write semantics have been established. |

The table associates night, interval, and temperature modes with submode
values `226`, `231`, and `224`, respectively. These remain unimplemented
commands; the app itself never sends the night value. Registers in the table
are unit-side leads: addresses such as `41012`, `41015`, and `41019` have
different meanings on the gateway.

The app labels `41000` as extract air (`Abluft`) and `41004` as exhaust air
(`Fortluft`), and computes extract absolute humidity from `41000` with
`41006`. This agrees with the
[observed local temperature swap](MELTEM.md#temperature-register-quirk)
rather than with the Modbus manual.

The 2026-11-03 survey on units without humidity, CO2 or VOC sensors returned
`32767` for `41007`, `41008`, `41012` and `41013`; it did not provide a
positive sensor reading. The integration follows the APK's ppm labels for
all four fields, but sensor-equipped hardware validation remains outstanding;
until then `41008` and `41012` are disabled by default, take no part in profile
detection, and raise no repair issue.
See [MELTEM.md](MELTEM.md#app-derived-register-survey-on-2026-11-03).

Do not add speculative reads, writes, or grouped register ranges from these
leads. Further investigation requires a reviewed, targeted test and the
existing live-test safety rules. The settings research backlog remains in
[SETTING_RE_BACKLOG.md](SETTING_RE_BACKLOG.md).
