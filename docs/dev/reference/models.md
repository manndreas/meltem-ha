# Models, variants and technical data

Manufacturer facts about the unit series, the type key, article numbers,
sensor equipment, feature availability and technical data. Source IDs are
explained in [README.md](README.md).

## Series overview

| Series | Heat exchanger | Airflow | Notes |
| --- | --- | --- | --- |
| `M-WRG-II P` | cross-counterflow plate heat exchanger (Kreuzgegenstrom-Plattenwärmeübertrager), Alu/PET | 10-100 m³/h | current series |
| `M-WRG-II E` | cross-counterflow enthalpy heat exchanger with moisture recovery (Enthalpie-Wärmeübertrager), Alu/ABS polymer or Alu mesh with membrane | 10-100 m³/h | current series; practically no condensate in normal use |
| `M-WRG-S` (series `M-WRG`) | cross-flow plate heat exchanger (Kreuzstrom-Plattenwärmeübertrager), aluminium | 15-97 m³/h | older series; the remote control, app and gateway documents call it simply `M-WRG` |

Sources: `MB-II` 1.2, 1.7.5; `MB-S` 1.7.5; `SYS-II`, `SYS-S` page 1;
`TD-*`.

`M-WRG-K` is another variant of the older `M-WRG` series with an LCD display.
It appears only in the maintenance manual `WA-S` (see
[maintenance.md](maintenance.md)).

## Type key

The documents contain no formal type key. The meanings below are derived from
the manual titles, data sheets and feature tables.

| Part | Meaning | Evidence |
| --- | --- | --- |
| `M-WRG` | "Meltem Wärmerückgewinnung" (Meltem heat recovery) | `MB-II` 1.2 |
| `II` | second-generation series | all `M-WRG-II` documents |
| `P` | plate heat exchanger (cross-counterflow) | `BA-II` 1.2, `TD-II-P*` |
| `E` | enthalpy heat exchanger (moisture recovery) | `BA-II` 1.2, `TD-II-E*` |
| `S` | standard unit of the older `M-WRG` series | `BA-S` title, `FBH` 1.11 ("Standardgerät") |
| `S/Z` | used only as `M-WRG-S/Z-T`; the `Z` is not explained | `T-S` |
| no operating suffix | local operation only: 5-key membrane keypad on `M-WRG-II`, 4-position step switch on `M-WRG-S` | `BA-II` 7, `BA-S` 7 |
| `T` | operation via the 6-key touch sensor "Tastsensor InControl" or a three-position rotary switch provided on site; the unit itself also has its keypad or step switch | `T-II` 7, `T-S` 7 |
| `M` | Modbus RTU operating variant | `MB-II`, `MB-S` |
| `-F` | humidity control; adds humidity sensors and all four temperature sensors | `MB-II` 16.6 |
| `-FC` | humidity and CO2 control, automatic mode | `MB-II` 16.6 |

The order of the suffixes differs between the series. On `M-WRG-II` the
sensor suffix follows the operating suffix (`P-M-FC`); on `M-WRG-S` the
operating suffix is separated by a space (`M-WRG-S M-FC`, `M-WRG-S/Z-T-FC`).

## Article numbers

Current 6-digit article numbers from the data sheets (`TD-*`, title line):

| Variant | Art.-Nr. | Variant | Art.-Nr. |
| --- | --- | --- | --- |
| `M-WRG-II P` | `200117` | `M-WRG-II E` | `200309` |
| `M-WRG-II P-F` | `200351` | `M-WRG-II E-F` | `200399` |
| `M-WRG-II P-FC` | `200118` | `M-WRG-II E-FC` | `200239` |
| `M-WRG-II P-M` | `200441` | `M-WRG-II E-M` | `200310` |
| `M-WRG-II P-M-F` | `200120` | `M-WRG-II E-M-F` | `200249` |
| `M-WRG-II P-M-FC` | `200121` | `M-WRG-II E-M-FC` | `200400` |
| `M-WRG-II P-T` | `200308` | `M-WRG-II E-T` | `200113` |
| `M-WRG-II P-T-F` | `200398` | `M-WRG-II E-T-F` | `200123` |
| `M-WRG-II P-T-FC` | `200110` | `M-WRG-II E-T-FC` | `200124` |
| `M-WRG-S` | `200209` | `M-WRG-S/Z-T` | `200219` |
| `M-WRG-S M` | `200004` | `M-WRG-S/Z-T-F` | `200006` |
| `M-WRG-S M-F` | `200270` | `M-WRG-S/Z-T-FC` | `200007` |
| `M-WRG-S M-FC` | `200360` | | |

Accessories and spare parts:

| Art.-Nr. | Type | Description | Source |
| --- | --- | --- | --- |
| `200383` | `M-WRG-GW` | gateway for app operation | `GW`, `T-II` 1.2.1 |
| `200076` | `M-WRG-FBH` | radio remote control | `FBH` 1.4 |
| `200294` | `M-WRG-FT` | 4-way radio push button with LED feedback | `MB-II` 1.2.1 |
| `200443` | `M-WRG-II FSF` | external radio humidity sensor, battery powered | `T-II` 1.2.1 |
| `200144` | `M-WRG-II FSC` | external radio CO2 sensor, 230 V | `T-II` 1.2.1 |
| `200273` | `M-WRG-KNX-GW` | Modbus-KNX gateway, one per unit | `MB-II` 1.2.1 |
| `200111` | — | longer ventilation pipes for thicker walls | `SYS-II`, `SYS-S` |

Filter article numbers are listed in [maintenance.md](maintenance.md).

The older info sheet `INFO-MB` (2020) uses a previous numbering scheme:

- units: `5012` = `M-WRG-S M`, `5012-1` = `M-WRG-S M-F`,
  `5012-2` = `M-WRG-S M-FC`
- options: `5046-00` O/PARM, `5046-10` O/NOF, `5046-11` O/MVS,
  `5046-12` O/LFS, `5046-31` O/EST-1, `5046-32` O/EST-2
- accessories: `5478-10` FBH, `5478-20` FT, `733010` FSF, `733011` FSC

The `TECH-*` documents are earlier editions of the `SYS-*` overviews (2021).
They differ mainly in using these old 4-digit article numbers in the parts
lists, for example `5159` → `200042`.

None of these article numbers matches the `PRODUCT_ID` value `116852`
(`0x0001C874`) or the `PRODUCT_NAME` `VMD-22RPS44` read from register `40002`
/ `40011` on tested units. Those strings do not appear in any collected
document.

## Sensor equipment

| Sensor | German | base | `-F` | `-FC` |
| --- | --- | :--: | :--: | :--: |
| Exhaust air temperature | Fortlufttemperatur | X | X | X |
| Outdoor air temperature | Außenlufttemperatur | | X | X |
| Extract air temperature | Ablufttemperatur | | X | X |
| Supply air temperature | Zulufttemperatur | | X | X |
| Rel. humidity, extract air | Rel. Feuchte Abluft | | X | X |
| Rel. humidity, supply air | Rel. Feuchte Zuluft | | X | X |
| CO2, extract air | CO2 Abluft | | | X |

Sources: `MB-II` 16.6 and `MB-S` 16.6 contain the same matrix.

- A base unit has **only** the exhaust air temperature sensor. It is mounted
  on the exhaust side for frost protection (`T-S` 9.4, `MB-S` 9.3).
- All four temperatures and both humidity sensors come together with `-F`.
- Base units can still get humidity or CO2 control through the external radio
  sensors `M-WRG-II FSF` / `FSC` (`BA-S` 1.7.6, `SYS-S` page 1).
- No collected document mentions a VOC sensor variant. The app manual has
  settings for "CO2 control with supply-air VOC sensor", see
  [app.md](app.md).

## Operating variants and local controls

| Operating variant | Local control on the unit | Additional control | Levels |
| --- | --- | --- | --- |
| `M-WRG-II P` / `E` (`-F`, `-FC`) | membrane keypad, 5 keys, 5 LEDs | radio accessories, app | 5 at the unit, 10 with accessories |
| `M-WRG-II P-T` / `E-T` (`-F`, `-FC`) | membrane keypad, 5 keys, 5 LEDs | InControl touch sensor (6 keys) or site-provided three-position rotary switch, 24 V control voltage | 5 at the unit, 6 on InControl, 10 with accessories |
| `M-WRG-II P-M` / `E-M` (`-F`, `-FC`) | membrane keypad, 5 keys, 5 LEDs | Modbus RTU | 5 at the unit, 10 via Modbus |
| `M-WRG-S` | step switch (Stufenschalter), 4 programs, no LEDs | radio accessories, app | 4 at the unit, 10 with accessories |
| `M-WRG-S/Z-T` (`-F`, `-FC`) | step switch, 4 programs | InControl touch sensor or rotary switch, 24 V control voltage | 4 at the unit, 6 on InControl, 10 with accessories |
| `M-WRG-S M` (`-F`, `-FC`) | step switch, 4 programs | Modbus RTU | 4 at the unit, 10 via Modbus |

Sources: `MB-II` 1.7.6, `T-II` 1.7.6, `MB-S` 1.7.6, `T-S` 1.7.6,
`SYS-II` and `SYS-S` page 1, `TD-*`.

Key, switch and LED behaviour is described in [functions.md](functions.md).

## Technical data comparison

The values are identical across operating variants (plain, `-T`, `-M`) and
sensor variants (`-F`, `-FC`) of a heat-exchanger family unless a row says
otherwise.

| Property | `M-WRG-II P` | `M-WRG-II E` | `M-WRG-S` |
| --- | --- | --- | --- |
| System type | decentralised, recuperative | decentralised, recuperative | decentralised, recuperative |
| Airflow | 10-100 m³/h | 10-100 m³/h | 15-97 m³/h |
| Constant airflow control (Volumenstromkonstanz) | yes | yes | **no** (balanced: yes) |
| Fans | EC DC radial | EC DC radial | EC DC radial |
| Heat recovery η0 max. (DIN EN 13141-8) | 94 % | 91 % | 71 % |
| Heat recovery PHI, variant U² with duct | 82 % | 78 % | — |
| Moisture recovery ηx (PHI) | — | 55 % | no |
| Power consumption, free-blowing | 4.6-52.4 W | 4.5-51.2 W | 2.5-37 W |
| Standby power | 0.8 W | 0.8 W | approx. 1 W |
| Specific fan power, free-blowing | 0.33 W/(m³/h) at 70 m³/h | 0.31 W/(m³/h) at 70 m³/h | 0.14 at 40 m³/h, 0.24 at 69 m³/h (W/(m³/h)) |
| Max. current | 0.42 A | 0.42 A | 0.16 A |
| Supply | 230 V~ (85-265 V~), 50-60 Hz | same | same |
| Protection class | IPX4, IPX5 with U² | IPX4, IPX5 with U² | IPX1, IPX4 with switch cap `M-WRG-SN`, IPX5 with U² |
| Energy efficiency class (ErP) | B; A with `-F` / `-FC` or external radio sensor | B; A with `-F` / `-FC` or external radio sensor | B; A with `-F` / `-FC` or external radio sensor |
| Sound pressure, surface-mounted (LpA, 10 m²) | 11.6-48.1 dB(A) | 11.6-48.1 dB(A) (`SYS-II`: 11.6-46.7) | 19.0-46 dB(A) |
| Sound pressure, partially integrated | 12.3-47.5 dB(A); with extract duct 12.3-46.4 dB(A) | same | 15.5-46.5 dB(A) |
| Sound pressure, wall-integrated U² with duct | 8.4-42.6 dB(A) | 8.4-42.6 dB(A) (`SYS-II`: 6.5-35.3 with ABL + ZUL duct) | 12.4-41.9 dB(A) |
| Sound power LWA surface-mounted (`TD`) | 21.5-52.1 dB | 15.6-50.7 dB | 23.0-50.0 dB |
| Sound power LWA partially integrated (`TD`) | 17.0-51.5 dB | 16.3-50.1 dB | 19.5-50.5 dB |
| Sound power LWA part. integrated, extract duct (`TD`) | 16.6-50.4 dB | 16.3-48.6 dB | — |
| Sound power LWA U² with extract duct (`TD`) | 12.4-46.1 dB | 15.3-46.6 dB | 16.4-45.9 dB (U² 2-room) |
| Sound insulation Dn,e,w in operation | 51-70 dB | 51-70 dB | 50-56 dB |
| Outdoor air temperature in operation (room ≥ 20 °C) | -18 to +40 °C | -18 to +40 °C | -22 to +40 °C |
| Room humidity in operation | up to approx. 70 % rH | same | same |
| Dimensions without spigots (W × H × D) | 364 × 590 × 218 mm | 364 × 590 × 218 mm | 388 × 409 × 196 mm |
| Visible depth surface / partially integrated / U² | 218 / 58 / — mm | 218 / 58 / — mm | 196 / 66 / — mm |
| Outdoor / exhaust spigots | DN 100 | DN 100 | DN 100 |
| Weight | approx. 8.4 kg | approx. 9.4 kg | approx. 7.3 kg |
| Filters (outdoor / extract) | ISO ePM1 60 % (F7) / ISO Coarse 60 % (G4) | same | ISO ePM10 65 % (G4) / ISO ePM10 65 % (G4) |
| Filter monitoring | runtime based, acoustic and optical | same | time based, acoustic (optical via InControl) |
| Condensate | condensate connection required | not required in normal use | via exhaust pipe / facade termination or site condensate connection |
| Closure flaps (Verschlussklappen) | automatic on on/off, standby, power failure | same | same |
| Input "device off" (Gerät AUS) | optional | optional | no |
| Fault output (Störmeldeausgang) | optional | optional | — |
| Humidity setting range (`-F`, `-FC`) | 40-80 % rH, stepless | same | 40-80 % rH, 10 steps |
| CO2 setting range (`-FC`) | 400-1400 ppm, stepless | same | 400-1400 ppm, 10 steps |
| Control voltage | 24 V on `-T` variants (also printed on `E-M`) | same | 24 V on `-T` variants |
| External control input defaults | 60 m³/h, 1 min delay, 15 min run-on | same | same |
| Colour | white, similar to RAL 9010 | same | same |
| TÜV tested | yes | yes | yes |
| VDI 6022 sheet 1 conformity | W-377517-23-Zd | W-377517-23-Zd | W-377516-23-Zd (only with the optional F7 outdoor filter) |
| Passive House certificate (PHI) | 1327vs03 | 1328vs03 | no |
| DIBt approval | Z-51.3-431 | Z-51.3-431 | Z-51.3-138 |

Sources: `MB-II` 1.7, `MB-S` 1.7, `T-II` 1.7, `T-S` 1.7, `BA-II` 1.7,
`BA-S` 1.7, `SYS-II` / `SYS-S` page 1, all `TD-*` sheets.

Installation variants for both series: surface-mounted (Aufputz), partially
integrated (Unterputz / Teilintegriert) and wall-integrated (Wandintegriert,
`U²`). Duct connection is possible with accessories.

The `SYS-*` documents also contain pressure / airflow curves per level
(`ZUL/ABL LS10`..`LS100` for `M-WRG-II`, `LS1`..`LS10` for `M-WRG-S`) and
dimension drawings. These are not transcribed here.

## Feature availability by operating method

The overview documents (`SYS-II` page 3, `SYS-S` page 3) contain a matrix of
"Einstellbare Gerätefunktionen" (adjustable device functions). The tables
below restructure it: one column per operating method, and each cell lists
which sensor variants support the function. "all" means base, `-F` and `-FC`.

`SYS-II` shows only `M-WRG-II E` types. It is assumed that `P` types behave
the same.

### M-WRG-II

Column types: on the unit = `E`, `E-F`, `E-FC`; 4-way push button and remote
control = `E...` types; InControl and app = `E-T` types; Modbus = `E-M` types.

| Function | On the unit | 4-way push button | Remote control | InControl | App | Modbus |
| --- | --- | --- | --- | --- | --- | --- |
| 10 ventilation levels | — | — | all | — | all | all |
| 5 ventilation levels | base | — | — | — | — | — |
| 4 ventilation levels | F, FC | all | — | all | — | — |
| Extract-only operation (Abluftbetrieb) | — | — | — | base | all | — |
| Supply-only operation (Zuluftbetrieb) | — | — | — | base, F | all | — |
| Extract-only operation adjustable | — | — | all | — | all | all |
| Supply-only operation adjustable | — | — | all | — | all | all |
| Humidity control | F | F | — | F, FC | — | — |
| Humidity control adjustable | — | — | F, FC | — | F, FC | F, FC |
| CO2 control | — | — | — | FC | — | — |
| CO2 control adjustable | — | — | FC | — | FC | FC |
| Automatic mode rH + CO2 | FC | FC | — | — | — | — |
| Automatic mode adjustable | — | — | FC | — | FC | FC |
| Temporary intensive ventilation | all | all | — | all | — | — |
| Temporary intensive ventilation adjustable | — | — | all | — | all | all |
| Time program adjustable | — | — | — | — | all | all |
| Control input (Steuereingang) | all | all | all | all | all | all |
| Input "device off" (24 V; smoke detector, window contact), optional | all | all | all | all | all | all |
| Minimum ventilation DIN 18017-3, factory setting, cannot be switched off, optional | all | all | all | all | all | all |
| Moisture-protection ventilation with humidity control, cannot be switched off, optional | F, FC | F, FC | F, FC | F, FC | F, FC | F, FC |
| Filter change indicator, optical and acoustic | all | all | all | all | all | all |
| Read operating hours | — | — | all | — | all | all |
| Read operating hours with accessory | all | all | — | all | — | — |
| Display sensor values | — | — | all | — | all | all |
| Indication rH supply > rH extract | F, FC | — | — | F, FC | F, FC | F, FC |
| Fault indication, optical (LED / symbol) | all | all | all | all | all | all |
| Operating message (Betriebsmeldung) | — | — | all | — | all | all |
| Operating message via LED | all | all | — | all | — | — |
| Frost protection | all | all | all | all | all | all |

### M-WRG-S

Column types: on the unit = `M-WRG-S` only; 4-way push button, remote
control, InControl and app = `M-WRG-S/Z-T` types; Modbus = `M-WRG-S M` types.

| Function | On the unit | 4-way push button | Remote control | InControl | App | Modbus |
| --- | --- | --- | --- | --- | --- | --- |
| 10 ventilation levels | — | — | all | — | all | all |
| 4 ventilation levels | base | all | — | all | — | — |
| Extract-only operation | — | — | — | base | — | — |
| Supply-only operation | — | — | — | base, F | — | — |
| Extract-only operation adjustable | — | — | all | — | all | all |
| Supply-only operation adjustable | — | — | all | — | all | all |
| Humidity control | — | F | — | F, FC | — | — |
| Humidity control adjustable | — | — | F, FC | — | F, FC | F, FC |
| CO2 control | — | — | — | FC | — | — |
| CO2 control adjustable | — | — | FC | — | FC | FC |
| Automatic mode rH + CO2 | — | FC | — | — | — | — |
| Automatic mode adjustable | — | — | FC | — | FC | FC |
| Temporary intensive ventilation | base | all | — | all | — | — |
| Temporary intensive ventilation adjustable | — | — | all | — | all | all |
| Time program adjustable | — | — | — | — | all | all |
| Control input | all | all | all | all | all | all |
| Minimum ventilation DIN 18017-3, optional | all | all | all | all | all | all |
| Moisture-protection ventilation, optional | — | F, FC | F, FC | F, FC | F, FC | F, FC |
| Filter change indicator, acoustic / optical | all | all | all | all | all | all |
| Read operating hours | — | — | all | — | all | all |
| Read operating hours with accessory | base | all | — | all | — | — |
| Display sensor values | — | — | F, FC | — | all | all |
| Indication rH supply > rH extract | — | — | — | F, FC | F, FC | F, FC |
| Fault indication, optical | — | all | all | all | all | all |
| Operating message | — | — | all | — | all | all |
| Operating message via LED | — | all | — | all | — | — |
| Frost protection | all | all | all | all | all | all |

The `M-WRG-S` matrix has no "device off" input row; `SYS-S` page 1 states
that the input does not exist on this series.

Footnotes common to both overviews:

- KNX requires `M-WRG-KNX-GW` (Art.-Nr. `200273`). Loxone and other systems
  need the bus cable laid as Modbus RTU.
- A site-provided three-position rotary switch offers the same functions as
  the 4-way push button, except the LED display.
- The app works together with the controls on the unit, the 4-way push
  button, the remote control and InControl.

## Relevance for this integration

- `PROFILE_METADATA` distinguishes `s` and `ii` by maximum airflow (97 / 100)
  and capabilities (`humidity`, `co2`). This matches the documented series
  and sensor variants. `P` and `E` do not need to be distinguished at the
  Modbus level; they differ only in heat-exchanger specifications.
- A base unit exposes only the exhaust air temperature. The test expectations
  in `tests/test_platform_matrix.py` follow the sensor table above.
- `M-WRG-S` has no constant airflow control. Its actual airflow can deviate
  more from the target under pressure load than on `M-WRG-II`.
- "Display sensor values" and "read operating hours" are listed as Modbus
  capabilities for every variant.
- Supply-only and extract-only operation are "adjustable" via Modbus, but only
  the unbalanced write path (`41120 = 4`) is documented for them.

## Open questions and contradictions

- `TD-II-E-M` prints PHI certificate `1327vs03` (the P certificate) while all
  other E sheets print `1328vs03`.
- `TD-II-E-M` lists a 24 V control voltage, but `TD-II-P-M` does not. Both
  are Modbus variants with identical electronics according to the manuals.
- The `SYS-II` sound pressure values for `E` differ from the manuals' combined
  P/E ranges. The manuals give one range for both families.
- `O/EST-1` / `O/EST-2` (`INFO-MB`) are not explained in any document.
- The `O/VOC-AUL` option, used for an integration profile, is not mentioned in
  any collected document.
