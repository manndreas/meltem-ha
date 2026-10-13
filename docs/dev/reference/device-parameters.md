# Device parameters ("Kennzahlen")

The radio remote control `M-WRG-FBH` documents the most complete public list
of persistent device parameters. Each parameter has a numeric ID
("Kennzahl"). The parameters are stored permanently in the unit
(`FBH` 9.4), which is the same kind of setting that the Meltem app edits; see
[app.md](app.md). Source IDs are explained in [README.md](README.md).

None of these parameters is documented as a Modbus register. The only
overlap with the Modbus map is the subset that also appears as `42000..42009`
(see [Mapping](#mapping-app--remote-control--modbus)).

## Remote control M-WRG-FBH

| Item | Value | Source |
| --- | --- | --- |
| Art.-Nr. | `200076` | `FBH` 1.4 |
| Compatible units | `M-WRG-II` and `M-WRG` series from build year 2018 | `FBH` 1.2 |
| Units per remote | up to 6, all of the same type | `FBH` 11 |
| Radio | 868.3 MHz, at least 0 dBm | `FBH` 1.6.1 |
| Power | 2 × 1.5 V AA alkaline | `FBH` 1.6.1 |
| Display | LCD, switches off after 20 s without input | `FBH` 7.1 |

Operation:

- **Pairing:** after power-on, a unit stays in connection mode for 5 min.
  Holding two keys of the remote together for more than 3 s opens the
  connection menu, where units are paired or unpaired. A failed attempt after
  the 5 min window requires power-cycling the unit (`FBH` 6.2, 6.3, 7.7, 12).
- **Several units:** the remote talks to the unit with the best radio link,
  which is not necessarily the nearest one. Humidity and CO2 values come from
  that unit. All units get the same program; units with sensors regulate on
  their own measurements (`FBH` 11).
- **Level scale on the display:**
  - `M-WRG-II`: level 10 = 10 m³/h, ..., level 90 = 90 m³/h, level 99 =
    100 m³/h. The display shows 10..99 (`FBH` 8.1).
  - `M-WRG`: level 1 = 15 m³/h, level 2 = 20 m³/h, ..., level 9 = 90 m³/h,
    level 10 = 100 m³/h. The display shows 1..10 (`FBH` 8.2).
- **Manual mode:** the up and down keys change the current levels only
  temporarily. In supply-only operation only the supply level changes, in
  extract-only only the extract level, otherwise both together. The values are
  lost when the program changes (`FBH` 9.2).
- **Active program menu:** the main parameters of the active program can be
  changed and saved permanently (`FBH` 9.3.3):
  - supply-only and extract-only: supply and extract level, set separately
  - humidity, CO2 and automatic mode: min. level, max. level, threshold
  - continuous operation and intensive ventilation: supply and extract level,
    set together
- **Special keys:** on > 3 s = standby; up > 3 s = intensive ventilation
  on/off; menu > 3 s = reset filter indicator (`FBH` 10).
- **Display symbols** include: filter, frost protection, device fault,
  radio, battery, supply / extract level, rH %, CO2 ppm, manual mode,
  continuous operation, CO2 control, intensive ventilation, automatic mode,
  and "gateway" (shown when the unit is controlled via a gateway or runs a
  program the remote does not support) (`FBH` 5.2).

## Parameter list

`II` = `M-WRG-II` (`FBH` 9.4.3, table 7); `M-WRG` = older series
(`FBH` 9.4.4, table 8). Range format: min-max / step / default. "—" = the
parameter does not exist for that series.

| ID | Parameter (German) | English | II | M-WRG | Unit | Notes |
| --- | --- | --- | --- | --- | --- | --- |
| 84 | Betriebsstunden Lüftermotore | operating hours, fan motors | read only | read only | h | runtime of the fan motors |
| 83 | Betriebsstunden Lüftungsgerät | operating hours, unit | read only | read only | h | unit switched on, including standby |
| 13 | Luftleistung Modus LOW | airflow LOW | 0-100 / 1 / 10 | 0-100 / 10 / 10 | m³/h | LOW = reduced ventilation |
| 14 | Luftleistung Modus MEDIUM | airflow MEDIUM | 0-100 / 1 / 30 | 0-100 / 10 / 30 | m³/h | MEDIUM = nominal ventilation |
| 15 | Luftleistung Modus HIGH | airflow HIGH | 0-100 / 1 / 50 | 0-100 / 10 / 60 | m³/h | HIGH = increased ventilation |
| 44 | Luftleistung Abluft im Modus HIGH I | extract airflow in HIGH I | 0-100 / 1 / 70 | — | m³/h | key 4 on the keypad |
| 45 | Luftleistung Zuluft im Modus HIGH I | supply airflow in HIGH I | 0-100 / 1 / 70 | — | m³/h | key 4 on the keypad |
| 11 | Luftleistung Intensivlüftung | intensive ventilation airflow | 0-100 / 1 / 100 | 0-100 / 10 / 100 | m³/h | key 5 on the keypad (II) |
| 12 | Dauer der Intensivlüftung | intensive ventilation duration | 0-240 / 1 / 15 | 0-240 / 1 / 15 | min | key 5 on the keypad (II) |
| 55 | Einschaltverzögerung externer Steuereingang | switch-on delay, external input | 0-240 / 1 / 1 | 0-240 / 1 / 1 | min | |
| 56 | Nachlaufzeit externer Steuereingang | run-on time, external input | 0-240 / 1 / 15 | 0-240 / 1 / 15 | min | |
| 54 | Luftleistung Abluft/Zuluft bei externem Steuereingang | airflow with external input | 0-100 / 1 / 60 | 0-100 / 10 / **70** | m³/h | M-WRG default differs from the manuals (60) |
| 95 | Modus externer Steuereingang | program for the external input | 0-18 / 1 / 12 | 0-18 / 1 / 12 | mode | see mode list |
| 36 | Startwert Feuchteregelung | humidity control threshold | 40-80 / 1 / 60 | 40-80 / 1 / 60 | % | |
| 37 | Min. Luftleistung Feuchteregelung | min. airflow, humidity control | 0-100 / 1 / 10 | 0-100 / 10 / 10 | m³/h | 0 = sniffing mode |
| 38 | Max. Luftleistung Feuchteregelung | max. airflow, humidity control | 0-100 / 1 / 60 | 0-100 / 10 / 60 | m³/h | |
| 39 | Startwert CO2-Regelung | CO2 control threshold | 400-1400 / 10 / 800 | 400-1400 / 1 / 600 | ppm | step as printed |
| 40 | Min. Luftleistung CO2-Regelung | min. airflow, CO2 control | 0-100 / 1 / 10 | 0-100 / 10 / 10 | m³/h | 0 = sniffing mode |
| 41 | Max. Luftleistung CO2-Regelung | max. airflow, CO2 control | 0-100 / 1 / 60 | 0-100 / 10 / 60 | m³/h | |
| 16 | Modus Taste 1 / Stufenschalter Stellung I | program for key 1 / switch position I | 0-18 / 1 / 0 | 0-18 / 1 / 0 | mode | LOW |
| 17 | Modus Taste 2 / Stellung II | program for key 2 / position II | 0-18 / 1 / 1 | 0-18 / 1 / 1 | mode | MEDIUM |
| 18 | Modus Taste 3 / Stellung III | program for key 3 / position III | 0-18 / 1 / 2 | 0-18 / 1 / 2 | mode | HIGH |
| 119 | Modus Taste 4 auf Folientastatur | program for key 4 | 0-18 / 1 / 11 | — | mode | HIGH 1 |
| 120 | Modus Taste 5 auf Folientastatur | program for key 5 | 0-18 / 1 / 14 | — | mode | intensive ventilation |
| 7 | Pausenzeit | sniffing pause time | 1-255 / 1 / 60 | 1-255 / 1 / 60 | min | |
| 8 | Schnüffelzeit | sniffing measurement time | 5-255 / 1 / 5 | 5-255 / 1 / 5 | min | |
| 9 | Luftleistung im Schnüffelbetrieb | airflow while measuring | 10-100 / 1 / 20 | 10-100 / 10 / 20 | m³/h | |
| 10 | Stellung der Luftklappen im Standby-Modus | flap position in standby | 0-1 / 1 / 1 | 0-1 / 1 / 1 | — | 0 = open, 1 = closed |
| 42 | Luftleistung Abluft im Programm Zuluftbetrieb | extract airflow in supply-only | 0-100 / 1 / 0 | 0-100 / 10 / 10 | m³/h | InControl and remote control |
| 43 | Luftleistung Zuluft im Programm Zuluftbetrieb | supply airflow in supply-only | 0-100 / 1 / 50 | 0-100 / 10 / 50 | m³/h | InControl and remote control |
| 46 | Luftleistung Abluft im Programm Abluftbetrieb | extract airflow in extract-only | 0-100 / 1 / 50 | 0-100 / 10 / 50 | m³/h | InControl and remote control |
| 47 | Luftleistung Zuluft im Programm Abluftbetrieb | supply airflow in extract-only | 0-100 / 1 / 0 | 0-100 / 10 / 10 | m³/h | InControl and remote control |
| 196 | Laufzeit Querlüftung | cross-ventilation runtime | 0-1440 / 1 / 120 | — | min | |
| 57 | Lüftungsstufe Dauerbetrieb | continuous operation level | 0-100 / 1 / 30 | 0-100 / 10 / 30 | m³/h | supply and extract set together |
| 50 | Tastsensor InControl: CO2-Regelung oder Automatikbetrieb | InControl CO2 key function | 0-1 / 1 / 0 | 0-1 / 1 / 0 | — | 0 = CO2 control, 1 = automatic mode |
| 101 | Modus externer Schalter | external switch mode | 0-2 / 1 / 1 | 0-3 / 1 / 1 | — | 0 = off, 1 = airflow, 2 = mode, 3 = unused |
| 131 | Umschaltung Sommer-/Winterzeit | daylight saving time | 0-1 / 1 / 1 | 0-1 / 1 / 1 | — | 0 = winter time, 1 = summer time |
| 123 | Zeitzone | time zone | -720-840 / 1 / 60 | same | min | offset to UTC; +60 = Germany |
| 96 | Tastsensor InControl Standby EIN/AUS | InControl standby | 0-1 / 1 / 1 | 0-1 / 1 / 1 | — | 0 = standby off, 1 = on |
| 133 | Modus Rauchmeldereingang | smoke detector input program | 0-18 / 1 / 18 | — | mode | 18 = device off |
| 134 | Kontakttyp Rauchmelder | smoke detector contact type | 0-1 / 1 / 0 | — | — | 0 = normally open (Schließer), 1 = normally closed (Öffner) |
| 151-154 | N/A | reserved | N/A | — | | |
| 168 | Modus externer Schalter I | program for external switch I | 0-18 / 1 / 0 | 0-18 / 1 / 0 | mode | |
| 169 | Modus externer Schalter II | program for external switch II | 0-18 / 1 / 1 | 0-18 / 1 / 1 | mode | |
| 170 | Modus externer Schalter III | program for external switch III | 0-18 / 1 / 2 | 0-18 / 1 / 2 | mode | |
| 93 | Werkseinstellungen wiederherstellen | restore factory settings | 0-1 / 1 / 0 | 0-1 / 1 / 0 | — | 1 = restore |

Notes on the table:

- ID 101 configures keys 1-3 of the 4-way push button `M-WRG-FT` and the
  radio sensors. Value 1 means keys 1-3 select LOW / MEDIUM / HIGH; value 2
  means the programs in IDs 168-170 are used (`FBH` footnote 4).
- ID 96 is only needed for InControl (`FBH` footnote 5).
- IDs 123 and 131 show that the unit keeps a local clock. This matches the
  app statement that time programs run on the unit without app or internet
  (`APP` slide 39).

## Mode list

Used by IDs 16-18, 95, 119, 120, 133 and 168-170 (`FBH` 9.4.5).

| Mode | `M-WRG-II` | `M-WRG` |
| --- | --- | --- |
| 0 | LOW | LOW |
| 1 | MEDIUM | MEDIUM |
| 2 | HIGH | HIGH |
| 3 | humidity control | humidity control |
| 4 | CO2 control | CO2 control |
| 5 | automatic mode | automatic mode |
| 6 | not used | not used |
| 7 | supply-only | supply-only |
| 8, 9 | not used | not used |
| 10 | extract-only | extract-only |
| 11 | HIGH 1 | not used |
| 12 | ventilation level of the external control input | same |
| 13 | not used | not used |
| 14 | intensive ventilation | intensive ventilation |
| 15-17 | not used | not used |
| 18 | device off (Gerät AUS) | device off |

## Mapping: app ↔ remote control ↔ Modbus

This mapping combines labels from three documents. It is an inference from
matching names and descriptions; none of the documents states it.

| App setting (`APP` slides 33-36) | Remote-control ID | Documented Modbus register |
| --- | --- | --- |
| Humidity control start value | 36 | `42000` |
| Min. / max. airflow, humidity control | 37 / 38 | `42001` / `42002` (in %) |
| CO2 control start value | 39 | `42003` |
| Min. / max. airflow, CO2 control | 40 / 41 | `42004` / `42005` (in %) |
| Intensive ventilation airflow / run-on time | 11 / 12 | — |
| Key I / II / III or device switch I / II / III (LOW / MED / HIGH airflows) | 13 / 14 / 15 | — |
| Key IIII (M-WRG-II only), extract / supply | 44 / 45 | — |
| Supply-only: supply / extract airflow (InControl) | 43 / 42 | — |
| Extract-only: supply / extract airflow (InControl) | 47 / 46 | — |
| Standby pause time / sniffing time / standby ventilation level | 7 / 8 / 9 | — |
| Close flaps / standby on/off | 10, possibly 96 | — |
| Filter indicator buzzer on/off | — | — |
| CO2 control with supply VOC sensor (on/off, start value, min. supply airflow, delay) | — | — |
| — (external input) | 54 / 55 / 56 | `42007` / `42008` / `42009` |
| — (operating hours) | 83 / 84 | `41030` / `41032` |

## Relevance for this integration

- **App-side settings are device parameters.** The settings that
  [../SETTING_RE_BACKLOG.md](../SETTING_RE_BACKLOG.md) is looking for are
  documented here as permanently stored device parameters: LOW / MEDIUM / HIGH
  airflows, intensive airflow and duration, and the supply-only / extract-only
  airflows (IDs 11-15, 42-47). This explains why they stay effective offline.
  No document describes a Modbus path to them.
- **LOW / MEDIUM / HIGH naming** matches the traced preset codes
  `228` / `229` / `230` and intensive `227` in [../MELTEM.md](../MELTEM.md).
  The mode list numbers (0, 1, 2, 14) are not those raw values and show no
  simple offset. The raw codes therefore look like a different encoding.
- **Supply-only / extract-only defaults** on `M-WRG-II` are 50 / 0 m³/h. They
  are configurable per side and stored in the unit. The traced app encoding
  `200 + airflow / 10` (HW-3 in
  [../HARDWARE_BACKLOG.md](../HARDWARE_BACKLOG.md)) may correspond to these
  stored values, but the documents say nothing about an encoding.
- **Cross-ventilation runtime** (ID 196, default 120 min) suggests that
  supply-only / extract-only operation may be time-limited on `M-WRG-II` in
  some contexts. The documents do not explain when ID 196 applies.
- **Intensive duration** is configurable from 0 to 240 min (ID 12). Any
  self-clearing of the intensive override (HW-1) therefore depends on this
  parameter, not on a fixed 15 min.
- **Units for Modbus `42001` / `42002` / `42004` / `42005`**: the remote
  control lists them in m³/h. On `M-WRG-II` the step is 1 m³/h, while the
  Modbus manual specifies `%` with step 10.
