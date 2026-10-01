# Meltem manufacturer reference

This folder collects the manufacturer facts from the original Meltem
documents that matter for developing this integration, now or later. The
original PDFs are **not** part of the repository (`docs/meltem/` is
gitignored). Every document listed below can be downloaded from the
manufacturer's website.

Observed hardware behaviour, reverse-engineering results and gateway quirks
are kept separate:

- [../MELTEM.md](../MELTEM.md): observed register behaviour and traced writes
- [../DEVELOPER.md](../DEVELOPER.md): implementation notes and hardware
  findings
- [../HARDWARE_BACKLOG.md](../HARDWARE_BACKLOG.md),
  [../SETTING_RE_BACKLOG.md](../SETTING_RE_BACKLOG.md): open measurements

## Contents

| File | Topic |
| --- | --- |
| [modbus.md](modbus.md) | Modbus RTU wiring, serial settings, register map, write sequences, scaling |
| [gateway.md](gateway.md) | `M-WRG-GW` gateway: purpose, radio, LEDs, reset |
| [models.md](models.md) | series, type key, article numbers, sensor equipment, technical data, feature matrix |
| [functions.md](functions.md) | ventilation programs, local controls and LEDs, frost protection, filter monitoring, inputs, options |
| [device-parameters.md](device-parameters.md) | persistent device parameters ("Kennzahlen") and mode list from the remote control manual, mapping to app and Modbus |
| [app.md](app.md) | Meltem app: setup, controls, settings, time / interval / temperature programs |
| [maintenance.md](maintenance.md) | filters, change intervals, filter indicator, maintenance plans |

## Conventions

- The text is written in our own words. Numbers, ranges and register tables
  are reproduced as facts. Short German labels are quoted where the exact
  wording matters, for example for UI terminology. No figures or drawings are
  copied.
- German terms appear in parentheses where they help match the manuals,
  for example "supply air (Zuluft)".
- Every fact names its source as `<source ID> <section>`, for example
  `MB-II 16.5`. For slide decks and data sheets the slide or page is given.
- Each topic file ends with **Relevance for this integration** and **Open
  questions and contradictions**. Mismatches with the code are only
  documented there; the code was not changed.
- Statements marked as inference are not written in any document.

## Source index

All documents were collected as `docs/meltem/<file name>`. The public URL is
`https://www.meltem.com/fileadmin/downloads/documents/<file name>`, URL
encoded. All 41 URLs answered `HTTP 200` on 2026-10-01.

### Manuals and info sheets

| ID | File (link) | Content | Dok.-Nr. / edition | Pages |
| --- | --- | --- | --- | --- |
| `MB-II` | [Meltem BA-IA_M-WRG-II_P-M_E-M.pdf](https://www.meltem.com/fileadmin/downloads/documents/Meltem%20BA-IA_M-WRG-II_P-M_E-M.pdf) | operating and installation manual `M-WRG-II P-M` / `E-M` (`-F`, `-FC`), Modbus | `2400030`, 9th ed. 2026-02-23 | 48 |
| `MB-S` | [Meltem BA-IA_M-WRG-S_M.pdf](https://www.meltem.com/fileadmin/downloads/documents/Meltem%20BA-IA_M-WRG-S_M.pdf) | operating and installation manual `M-WRG-S M` (`-F`, `-FC`), Modbus | `2400035`, 6th ed. 2025-01-01 | 44 |
| `T-II` | [Meltem BA-IA_M-WRG-II_P-T_E-T.pdf](https://www.meltem.com/fileadmin/downloads/documents/Meltem%20BA-IA_M-WRG-II_P-T_E-T.pdf) | operating and installation manual `M-WRG-II P-T` / `E-T` (`-F`, `-FC`), InControl | `2400032`, 9th ed. 2026-02-23 | 60 |
| `T-S` | [Meltem BA-IA_M-WRG-S_Z-T.pdf](https://www.meltem.com/fileadmin/downloads/documents/Meltem%20BA-IA_M-WRG-S_Z-T.pdf) | operating and installation manual `M-WRG-S/Z-T` (`-F`, `-FC`), InControl | `2400036`, 11th ed. 2026-01-15 | 56 |
| `BA-II` | [Meltem BA_M-WRG-II_P_E.pdf](https://www.meltem.com/fileadmin/downloads/documents/Meltem%20BA_M-WRG-II_P_E.pdf) | operating manual `M-WRG-II P` / `E` (`-F`, `-FC`), keypad only | `2400023`, 9th ed. 2026-02-23 | 36 |
| `BA-S` | [Meltem BA_M-WRG-S.pdf](https://www.meltem.com/fileadmin/downloads/documents/Meltem%20BA_M-WRG-S.pdf) | operating manual `M-WRG-S`, step switch | `2400026`, 10th ed. 2025-01-01 | 32 |
| `FBH` | [Meltem BA_M-WRG-FBH.pdf](https://www.meltem.com/fileadmin/downloads/documents/Meltem%20BA_M-WRG-FBH.pdf) | operating manual radio remote control `M-WRG-FBH` | `2400021`, 6th ed. 2025-01-01 | 44 |
| `GW` | [Meltem IA-BA_M-WRG-GW.pdf](https://www.meltem.com/fileadmin/downloads/documents/Meltem%20IA-BA_M-WRG-GW.pdf) | installation and operating manual gateway `M-WRG-GW` | `2400001`, 2025-01-01 | 2 |
| `APP` | [Meltem BA-IA_Meltem_App DE.pdf](https://www.meltem.com/fileadmin/downloads/documents/Meltem%20BA-IA_Meltem_App%20DE.pdf) | Meltem app, step by step (slide deck) | © 2022 | 55 |
| `INFO-MB` | [Meltem Infoblatt Modbus M-WRG.pdf](https://www.meltem.com/fileadmin/downloads/documents/Meltem%20Infoblatt%20Modbus%20M-WRG.pdf) | info sheet `M-WRG-S M` with Modbus (RTU) interface | Art.-Nr. `744386`, KW 51/2020 | 1 |
| `WA-II` | [Meltem WA_M-WRG-II.pdf](https://www.meltem.com/fileadmin/downloads/documents/Meltem%20WA_M-WRG-II.pdf) | maintenance manual `M-WRG-II P...` / `E...` | `2400379`, 3rd ed. 2025-01-01 | 28 |
| `WA-S` | [Meltem WA_M-WRG-S_K.pdf](https://www.meltem.com/fileadmin/downloads/documents/Meltem%20WA_M-WRG-S_K.pdf) | maintenance manual `M-WRG-S...` / `M-WRG-K...` | `2400380`, 3rd ed. 2025-01-01 | 20 |

### System overviews

| ID | File (link) | Content | Date | Pages |
| --- | --- | --- | --- | --- |
| `SYS-II` | [Meltem TD_System_Ueberblick_M-WRG-II.pdf](https://www.meltem.com/fileadmin/downloads/documents/Meltem%20TD_System_Ueberblick_M-WRG-II.pdf) | technical data, pressure curves, feature matrix, dimensions, parts lists (`M-WRG-II E` types) | KW 47/2025 | 8 |
| `SYS-S` | [Meltem TD_System_Ueberblick_M-WRG-S.pdf](https://www.meltem.com/fileadmin/downloads/documents/Meltem%20TD_System_Ueberblick_M-WRG-S.pdf) | same for `M-WRG-S` and `M-WRG-S/Z-T` | PDF 2025-11-24 | 10 |
| `TECH-II` | [Meltem Techn Daten M-WRG-II.pdf](https://www.meltem.com/fileadmin/downloads/documents/Meltem%20Techn%20Daten%20M-WRG-II.pdf) | earlier edition of `SYS-II` with old article numbers | KW 43/2021 | 8 |
| `TECH-S` | [Meltem Techn Daten M-WRG.pdf](https://www.meltem.com/fileadmin/downloads/documents/Meltem%20Techn%20Daten%20M-WRG.pdf) | earlier edition of `SYS-S` with old article numbers | PDF 2025-09-03 | 10 |

### Data sheets

ID scheme: `TD-II-<variant>` and `TD-S-<variant>`; `TD-*` means all of them.
Each sheet has 1-2 pages. PDF dates: `M-WRG-II E*` 2025-10-29, `M-WRG-II P*`
and `M-WRG-S*` 2025-02-05.

| ID | File (link) | Art.-Nr. |
| --- | --- | --- |
| `TD-II-P` | [Meltem TD_M-WRG-II P.pdf](https://www.meltem.com/fileadmin/downloads/documents/Meltem%20TD_M-WRG-II%20P.pdf) | `200117` |
| `TD-II-P-F` | [Meltem TD_M-WRG-II P-F.pdf](https://www.meltem.com/fileadmin/downloads/documents/Meltem%20TD_M-WRG-II%20P-F.pdf) | `200351` |
| `TD-II-P-FC` | [Meltem TD_M-WRG-II P-FC.pdf](https://www.meltem.com/fileadmin/downloads/documents/Meltem%20TD_M-WRG-II%20P-FC.pdf) | `200118` |
| `TD-II-P-M` | [Meltem TD_M-WRG-II P-M.pdf](https://www.meltem.com/fileadmin/downloads/documents/Meltem%20TD_M-WRG-II%20P-M.pdf) | `200441` |
| `TD-II-P-M-F` | [Meltem TD_M-WRG-II P-M-F.pdf](https://www.meltem.com/fileadmin/downloads/documents/Meltem%20TD_M-WRG-II%20P-M-F.pdf) | `200120` |
| `TD-II-P-M-FC` | [Meltem TD_M-WRG-II P-M-FC.pdf](https://www.meltem.com/fileadmin/downloads/documents/Meltem%20TD_M-WRG-II%20P-M-FC.pdf) | `200121` |
| `TD-II-P-T` | [Meltem TD_M-WRG-II P-T.pdf](https://www.meltem.com/fileadmin/downloads/documents/Meltem%20TD_M-WRG-II%20P-T.pdf) | `200308` |
| `TD-II-P-T-F` | [Meltem TD_M-WRG-II P-T-F.pdf](https://www.meltem.com/fileadmin/downloads/documents/Meltem%20TD_M-WRG-II%20P-T-F.pdf) | `200398` |
| `TD-II-P-T-FC` | [Meltem TD_M-WRG-II P-T-FC.pdf](https://www.meltem.com/fileadmin/downloads/documents/Meltem%20TD_M-WRG-II%20P-T-FC.pdf) | `200110` |
| `TD-II-E` | [Meltem TD_M-WRG-II E.pdf](https://www.meltem.com/fileadmin/downloads/documents/Meltem%20TD_M-WRG-II%20E.pdf) | `200309` |
| `TD-II-E-F` | [Meltem TD_M-WRG-II E-F.pdf](https://www.meltem.com/fileadmin/downloads/documents/Meltem%20TD_M-WRG-II%20E-F.pdf) | `200399` |
| `TD-II-E-FC` | [Meltem TD_M-WRG-II E-FC.pdf](https://www.meltem.com/fileadmin/downloads/documents/Meltem%20TD_M-WRG-II%20E-FC.pdf) | `200239` |
| `TD-II-E-M` | [Meltem TD_M-WRG-II E-M.pdf](https://www.meltem.com/fileadmin/downloads/documents/Meltem%20TD_M-WRG-II%20E-M.pdf) | `200310` |
| `TD-II-E-M-F` | [Meltem TD_M-WRG-II E-M-F.pdf](https://www.meltem.com/fileadmin/downloads/documents/Meltem%20TD_M-WRG-II%20E-M-F.pdf) | `200249` |
| `TD-II-E-M-FC` | [Meltem TD_M-WRG-II E-M-FC.pdf](https://www.meltem.com/fileadmin/downloads/documents/Meltem%20TD_M-WRG-II%20E-M-FC.pdf) | `200400` |
| `TD-II-E-T` | [Meltem TD_M-WRG-II E-T.pdf](https://www.meltem.com/fileadmin/downloads/documents/Meltem%20TD_M-WRG-II%20E-T.pdf) | `200113` |
| `TD-II-E-T-F` | [Meltem TD_M-WRG-II E-T-F.pdf](https://www.meltem.com/fileadmin/downloads/documents/Meltem%20TD_M-WRG-II%20E-T-F.pdf) | `200123` |
| `TD-II-E-T-FC` | [Meltem TD_M-WRG-II E-T-FC.pdf](https://www.meltem.com/fileadmin/downloads/documents/Meltem%20TD_M-WRG-II%20E-T-FC.pdf) | `200124` |
| `TD-S` | [Meltem TD_M-WRG-S.pdf](https://www.meltem.com/fileadmin/downloads/documents/Meltem%20TD_M-WRG-S.pdf) | `200209` |
| `TD-S-M` | [Meltem TD_M-WRG-S M.pdf](https://www.meltem.com/fileadmin/downloads/documents/Meltem%20TD_M-WRG-S%20M.pdf) | `200004` |
| `TD-S-M-F` | [Meltem TD_M-WRG-S M-F.pdf](https://www.meltem.com/fileadmin/downloads/documents/Meltem%20TD_M-WRG-S%20M-F.pdf) | `200270` |
| `TD-S-M-FC` | [Meltem TD_M-WRG-S M-FC.pdf](https://www.meltem.com/fileadmin/downloads/documents/Meltem%20TD_M-WRG-S%20M-FC.pdf) | `200360` |
| `TD-S-Z-T` | [Meltem TD_M-WRG-S Z-T.pdf](https://www.meltem.com/fileadmin/downloads/documents/Meltem%20TD_M-WRG-S%20Z-T.pdf) | `200219` |
| `TD-S-Z-T-F` | [Meltem TD_M-WRG-S Z-T-F.pdf](https://www.meltem.com/fileadmin/downloads/documents/Meltem%20TD_M-WRG-S%20Z-T-F.pdf) | `200006` |
| `TD-S-Z-T-FC` | [Meltem TD_M-WRG-S Z-T-FC.pdf](https://www.meltem.com/fileadmin/downloads/documents/Meltem%20TD_M-WRG-S%20Z-T-FC.pdf) | `200007` |

### Documents referenced but not collected

The manuals refer to further documents that are not in `docs/meltem/`:
installation manuals `2400253` (`M-WRG-II`) and `2400269` (`M-WRG-S`),
`M-WRG-FT` manual `2400214`, external humidity / CO2 sensor manuals
`2400216` / `2400215`, `M-WRG-KNX-GW` manual `2400217`, and the app manual
`2400388`. The Modbus-KNX communication-object document mentioned in
[../MELTEM.md](../MELTEM.md) is also not part of this collection.

## Glossary

| German | Abbr. | English |
| --- | --- | --- |
| Außenluft | AUL | outdoor air |
| Zuluft | ZUL | supply air |
| Abluft | ABL | extract air |
| Fortluft | FOL | exhaust air |
| Wärmerückgewinnung | WRG | heat recovery |
| Wärmebereitstellungsgrad | η0 | heat recovery efficiency |
| Feuchterückgewinnung | ηx | moisture recovery |
| Kreuzgegenstrom- / Kreuzstrom-Plattenwärmeübertrager | | cross-counterflow / cross-flow plate heat exchanger |
| Enthalpie-Wärmeübertrager | | enthalpy heat exchanger |
| Luftleistung, Volumenstrom | | airflow (m³/h) |
| Lüftungsstufe | LS | ventilation level |
| Lüftungsprogramm | | ventilation program |
| balanciert / unbalanciert | | balanced / unbalanced (supply = extract or not) |
| Volumenstromkonstanz | | constant airflow control |
| Folientastatur, Bedienfolie | | membrane keypad on the unit |
| Stufenschalter | | step switch on the unit (`M-WRG-S`) |
| Tastsensor InControl | | wired 6-key touch sensor (`-T` variants) |
| Dreistufen-Drehschalter mit Nullstellung | | three-position rotary switch with zero position |
| Funkfernbedienung | FBH | radio remote control |
| 4-fach Funktaster | FT | 4-way radio push button |
| Externer Funksensor Feuchte / CO2 | FSF / FSC | external radio humidity / CO2 sensor |
| Externer Steuereingang | | external control input (230 V) |
| Einschaltverzögerung / Nachlaufzeit | | switch-on delay / run-on time |
| Eingang „Gerät AUS" | | device-off input |
| Störmeldeausgang | | fault output |
| Verschlussklappen, Luftklappen | | closure flaps |
| Netzschalter | | mains switch |
| Frostschutzfunktion | | frost protection |
| Schnüffelbetrieb | | sniffing mode |
| Filterwechselanzeige | | filter change indicator |
| Störmeldung / Betriebsmeldung | | fault indication / operating message |
| Aufputz / Unterputz (Teilintegriert) / Wandintegriert (U²) | AP / UP / U² | surface-mounted / partially integrated / wall-integrated |
| Kennzahl | | parameter ID (remote control) |
| Werkseinstellung | | factory setting |

## Findings relevant to open backlog items

These are cross-references only. Decisions remain with the next hardware
session.

| Item | What the documents say | Details |
| --- | --- | --- |
| HW-1 intensive registers after power-off | Intensive ventilation ends on its own after a configurable duration (ID 12, 0-240 min, default 15) and resumes the previous program. Register behaviour is not documented. | [functions.md](functions.md#ventilation-programs-and-default-airflows), [device-parameters.md](device-parameters.md) |
| HW-2 write settle time | Gateway and units communicate over 868.3 MHz radio. No timing guidance beyond the RTU frame rules. | [gateway.md](gateway.md), [modbus.md](modbus.md#frame-requirements) |
| HW-3 encoding above 200 | Supply-only and extract-only airflows are stored device parameters (IDs 42-47; `M-WRG-II` defaults 50 / 0). No raw encoding is documented. | [device-parameters.md](device-parameters.md) |
| HW-4 five-register mode block | Reading `41120..41124` is not documented at all. | [modbus.md](modbus.md#not-documented) |
| TODO 1 shortcut configuration | The app stores airflows for "Abluftbetrieb" / "Zuluftbetrieb"; InControl uses IDs 42-47. | [app.md](app.md), [device-parameters.md](device-parameters.md) |
| TODO 2 `PRODUCT_ID` decoding | `116852` / `VMD-22RPS44` match no article number or text in any document. | [models.md](models.md#article-numbers) |
| Setting RE: intensive and keypad families | Documented as persistent device parameters 11-15 and 44-45, without a Modbus mapping. | [device-parameters.md](device-parameters.md#relevance-for-this-integration) |

## Discrepancies between documents and the integration

Documented only; no code was changed.

- `M-WRG-S` scaling examples imply `raw / 2` (raw `80` = 40 m³/h). The
  integration scales with `× 200 / 97`. See [modbus.md](modbus.md#scaling).
- The CO2 starting point is `500..1200` ppm in the Modbus manual and
  `400..1400` ppm in the data sheets and the remote control. The integration
  uses the Modbus range.
- Register `41013` (VOC) and the `O/VOC-AUL` option are not in any collected
  document.
- `M-WRG-S` has no constant airflow control (`SYS-S`). Actual airflow may
  deviate more from the target than on `M-WRG-II`.
