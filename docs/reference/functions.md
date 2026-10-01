# Ventilation programs and device behaviour

Manufacturer facts about what the units do: ventilation programs, local
controls and LEDs, frost protection, filter monitoring, inputs and factory
options. Source IDs are explained in [README.md](README.md). The configurable
parameters behind these functions are listed in
[device-parameters.md](device-parameters.md).

## Terminology

| German | English used here | Meaning |
| --- | --- | --- |
| Lüftungsstufe | ventilation level | airflow setting in m³/h, or a level number on accessories |
| Lüftungsprogramm | ventilation program | operating program such as humidity control or intensive ventilation |
| Lüftung bei Abwesenheit (reduzierte Lüftung) | reduced ventilation / LOW | lowest fixed level |
| Lüftung bei Anwesenheit (Nennlüftung) | nominal ventilation / MEDIUM | normal operation |
| Erhöhte Lüftung | increased ventilation / HIGH | higher level for load peaks |
| Intensivlüftung | intensive ventilation | maximum level, time-limited |
| Zuluftbetrieb (Sommerbetrieb) | supply-only operation (summer mode) | supply fan only (II) or mostly supply (S) |
| Abluftbetrieb | extract-only operation | extract fan only (II) or mostly extract (S) |
| Feuchteregelung | humidity control | demand control by relative humidity |
| CO2-Regelung | CO2 control | demand control by CO2 |
| Automatikbetrieb | automatic mode | humidity and CO2 control combined |
| Dauerbetrieb | continuous operation | fixed level, used by the remote control |
| Standby-Modus | standby | fans stopped, unit powered, flaps closed |
| Schnüffelbetrieb | sniffing mode | periodic measurement while the minimum level is 0 |
| Frostschutzbetrieb | frost protection mode | unbalanced operation to protect the heat exchanger |
| Querlüftung | cross ventilation | one unit on supply-only, another on extract-only |

The remote control and app use the labels `LOW`, `MEDIUM` (`MED`) and `HIGH`
for the first three programs (`FBH` 9.4.3, `APP` slide 29).

## Ventilation programs and default airflows

| Program | `M-WRG-II` | `M-WRG-S` / `M-WRG` | Sources |
| --- | --- | --- | --- |
| Reduced ventilation (LOW) | 10 m³/h | 15 m³/h | `MB-II` 10.1, `T-S` 10.1 |
| Nominal ventilation (MEDIUM) | 30 m³/h | 30 m³/h | `MB-II` 10.2, `T-S` 10.2 |
| Increased ventilation (HIGH) | 50 m³/h; key 4 on base units: 70 m³/h ("HIGH I") | 60 m³/h | `MB-II` 10.3, `T-S` 10.3, `FBH` 9.4.3 |
| Intensive ventilation | 100 m³/h for approx. 15 min | 97 m³/h for approx. 15 min | `MB-II` 10.4, `T-S` 10.4 |
| Supply-only (summer) | supply 50, extract 0 m³/h | supply 50, extract 15 m³/h | `T-II` 10.5, `T-S` 10.5 |
| Extract-only | extract 50, supply 0 m³/h | extract 50, supply 15 m³/h | `T-II` 10.6, `T-S` 10.6 |
| Humidity control | min. level (10), rises steplessly up to 60 m³/h above 60 % rH | min. level (15), up to 60 m³/h above 60 % rH | `MB-II` 10.5, `T-S` 10.7 |
| CO2 control | min. level (10), 10-60 m³/h above 800 ppm | min. level (15), 15-60 m³/h above 600 ppm | `T-II` 10.8, `T-S` 10.8 |
| Automatic mode | humidity and CO2 control in parallel; the higher proposed level wins | same | `MB-II` 10.6, `T-S` 10.8 |
| Continuous operation (remote control) | 30 m³/h supply and extract | level 3 (30 m³/h) | `FBH` 8.1.6, 8.2.6 |
| Standby | 0 m³/h, flaps closed, unit stays powered | same | `T-II` 9.2, `FBH` 10.1 |

Details:

- **Reduced ventilation** is meant for absence, for example holidays, and
  already includes moisture-protection ventilation (`MB-II` 10.1).
- **Intensive ventilation** ends after approx. 15 min, or earlier when
  another program is chosen. The previously active program then resumes
  (`MB-II` 10.4). Duration and airflow are device parameters (IDs 12 and 11,
  defaults 15 min and 100 m³/h; see
  [device-parameters.md](device-parameters.md)). On the remote control, a
  long press (> 3 s) on the up key starts or stops it (`FBH` 10.2).
- **Supply-only on `M-WRG-II`** switches the exhaust fan off. On `M-WRG-S`
  the exhaust fan drops to the lowest level, so heat recovery continues in a
  limited way (`MB-II` 6.2.2, `T-S` 6.2.2). Both series recommend avoiding
  supply-only operation and cross ventilation in the cold season. Otherwise
  the unit keeps triggering frost protection or switches off completely
  (`T-II` 10.5, 10.6). The Modbus manual adds that pure supply or extract
  operation is not advisable in winter because the unit is then often in
  unbalanced frost-protection operation (`MB-II` 6.2.1).
- On Modbus units, supply-only or extract-only operation can be realised
  "via Modbus or by factory setting" (`MB-II` 6.2.2).
- **Humidity control** compares the calculated absolute humidity of supply
  and extract air. If the supply (outdoor) air is more humid than the extract
  air, dehumidification is impossible. `M-WRG-II` indicates this by blinking
  LED 3. `M-WRG` units then ventilate at the lowest level (`MB-II` 10.5,
  `FBH` 8.2.3).
- **CO2 control as a separate program** exists on `-T-FC` (InControl key)
  and in the remote control, app and Modbus (`41121 = 144`). On the keypad of
  `-FC` units, key 4 is automatic mode (`MB-II` 7.3.3).
- On `M-WRG-S/Z-T-FC` and `M-WRG-II P-T-FC` / `E-T-FC`, the InControl
  "CO2" key can be reassigned to automatic mode at the factory or with the
  remote control (ID 50) (`T-S` 10.8, `FBH` 9.4.3 footnote 3).

### Sniffing mode

- With a minimum level of 0 m³/h for humidity or CO2 control, which can be set
  at the factory, via Modbus, app or remote control, the unit enters sniffing
  mode (`MB-II` 10.6 footnote, `T-II` 10.8 footnote).
- It pauses for the configured pause time (factory 60 min). It then runs for
  5 min to measure humidity and CO2. If a threshold is exceeded, it returns to
  ventilation.
- Pause time, measurement time and the airflow during measurement are device
  parameters 7, 8 and 9 (defaults 60 min, 5 min, 20 m³/h) (`FBH` 9.4.3).

## Local controls

### M-WRG-II membrane keypad (Folientastatur)

Five keys with one LED each on the left side of the unit (`MB-II` 7.2). The
same keypad exists on the plain, `-T` and `-M` variants.

| Key | Base unit | `-F` | `-FC` |
| --- | --- | --- | --- |
| 1 | reduced ventilation, 10 m³/h | same | same |
| 2 | nominal ventilation, 30 m³/h | same | same |
| 3 | increased ventilation, 50 m³/h | same | same |
| 4 | increased ventilation, 70 m³/h | humidity control, 10-60 m³/h stepless | automatic mode, 10-60 m³/h stepless |
| 5 | intensive ventilation (15 min), 100 m³/h | same | same |

Source: `MB-II` 7.3, `BA-II` 7.1, `T-II` 7.1.1. Key assignments are device
parameters 16, 17, 18, 119 and 120 (`FBH` 9.4.3).

LED behaviour (`MB-II` 7.4):

- After a key is pressed, its LED lights for 10 s and then goes out. The
  active program is **not** shown permanently.
- Status indications:

| LED | State | Meaning |
| --- | --- | --- |
| LED 1 | steady | air filter change required |
| LED 2 | steady | device fault (for example sensor or motor defective); see Modbus `41016` |
| LED 3 | blinks 10 s | absolute humidity of supply air is higher than that of extract air |
| LED 5 | blinks 10 s | unit in frost protection mode |

The mains switch sits behind the cover (`MB-II` 7.2).

### M-WRG-S step switch (Stufenschalter)

Mains switch (`O` / `I`) and a step switch on the unit. There are no LEDs
(`BA-S` 7, `MB-S` 7.2, `T-S` 7.1).

| Position | Program | Airflow |
| --- | --- | --- |
| I | reduced ventilation | 15 m³/h |
| II | nominal ventilation | 30 m³/h |
| III | increased ventilation | 60 m³/h |
| sequence I-II-I within 2 s | intensive ventilation (15 min) | 97 m³/h |

- Repeating I-II-I cancels a running intensive ventilation (`T-S` 7.1).
- On `M-WRG-S/Z-T`, the position chosen at the step switch is also shown by
  the matching LED on InControl (`T-S` 7.1).

### InControl touch sensor (`-T` variants)

Six keys with LEDs; each LED shows the selected program (`T-II` 7.2,
`T-S` 7.2).

| Key position | Base `-T` | `-T-F` | `-T-FC` |
| --- | --- | --- | --- |
| top left | reduced ventilation / standby | same | same |
| top right | nominal ventilation | same | same |
| middle left | increased ventilation | same | same |
| middle right | intensive ventilation (15 min) | same | same |
| bottom left | supply-only (summer) | supply-only (summer) | humidity control |
| bottom right | extract-only | humidity control | CO2 control **or** automatic mode (option) |

Layout from `T-II` figures 15-17; `T-S` figures 12-14 use the same layout.

Status indications (blinking LED, `T-II` 7.2.4):

| LED | Meaning |
| --- | --- |
| "reduced ventilation" blinks | device fault |
| "nominal ventilation" blinks | air filter change required |
| "humidity control" blinks | absolute humidity of supply air higher than extract air |

Further behaviour:

- Pressing "reduced ventilation" for more than 3 s activates standby. This is
  not possible with option `O/NOF` (`T-II` 9.2.1). Device parameter 96 can
  enable or disable this standby function (`FBH` 9.4.3).
- Up to 5 units of the same type can share one InControl. All of them get the
  same program; units with sensors regulate on their own measurements
  (`T-II` 9.1).
- "The unit always executes the last choice made", whichever control it came
  from (`T-II` 9.1).
- Wiring: J-Y(St)Y cable, star topology, max. 15 m. 24 V comes from unit 1
  only, which also drives the LEDs (`T-II` ch. 15).

### Three-position rotary switch (site-provided, `-T` variants)

| Position | `M-WRG-II` | `M-WRG-S` |
| --- | --- | --- |
| 0 | standby, 0 m³/h | standby, 0 m³/h |
| 1 | reduced, 10 m³/h | reduced, 15 m³/h |
| 2 | nominal, 30 m³/h | nominal, 30 m³/h |
| 3 | base: increased 50 m³/h; `-F`: humidity control 10-60; `-FC`: automatic mode 10-60 | base: increased 60 m³/h; `-F`: humidity control 15-60; `-FC`: CO2 control or automatic (option) 15-60 |

- An optional additional push button starts 15 min of intensive ventilation
  (100 / 97 m³/h). Choosing a level on the rotary switch cancels it
  (`T-II` 9.3, `T-S` 9.3).
- A DIP switch on the unit must be changed from "Taster" (button) to
  "Schalter" (switch). Up to 5 units, max. 50 m cable (`T-II` ch. 16).

### Radio accessories

- **Remote control `M-WRG-FBH`**: see
  [device-parameters.md](device-parameters.md).
- **4-way radio push button `M-WRG-FT`**: keys 1-3 select LOW / MEDIUM / HIGH
  by default. Device parameter 101 can switch them to the programs stored in
  IDs 168-170 (`FBH` 9.4.3 footnote 4).
- **External radio sensors** `M-WRG-II FSF` (humidity, battery) and
  `M-WRG-II FSC` (CO2, 230 V) add demand control to units without built-in
  sensors (`T-II` 1.2.1).

## Standby and closure flaps

- Standby ends ventilation; the unit stays powered and the flaps close
  (`T-II` 9.2). Leave standby by selecting a program again. With the remote
  control, the unit otherwise starts in the preset program "Dauerbetrieb"
  (`FBH` 10.1.2).
- Whether the flaps close in standby is parameter 10 (default: closed)
  (`FBH` 9.4.3).
- Leaving the unit in standby for long periods is not advisable (`T-II` 9.2).
- Power-on: the flaps open after approx. 1 s on `M-WRG-II` and approx. 10 s
  on `M-WRG-S` (`MB-II` 8.2, `T-S` 8.2). If the flaps do not open fully
  after first power-on or a long standstill, switch off, wait at least 15 s
  and switch on again (`T-S` 8.3).
- The flaps are fully automatic on power on/off, standby and power failure
  (`MB-II` 1.7.6).

## Frost protection

The exhaust air temperature (Fortlufttemperatur) is monitored continuously.
This sensor exists on every variant.

| Step | `M-WRG-II P` | `M-WRG-II E` | `M-WRG-S` |
| --- | --- | --- | --- |
| Trigger | exhaust air < -1.5 °C | < -2.2 °C ("A1") or < -2.7 °C ("A2") | < 2 °C |
| Action | change supply and/or extract airflow stepwise so the extract share rises | same | same |
| Return | 3-min average > 5.5 °C, step by step back to the previous state | 2-min average > 7.0 °C | 3-min average ≥ 4 °C |
| Shut-down | if > 5.5 °C is not reached within the control range (for example because the room cools down), both fans stop | same with 7.0 °C | if 2 °C is not reached, both fans stop |
| Retry | after 1 h, run for 6 min and check; resume the previous state if > 5.5 °C, otherwise repeat | same with 7.0 °C | resume as soon as the sensor shows 4 °C |
| End | when exhaust air stays above 5.5 °C and both fans run balanced | same with 7.0 °C | — |
| Additional trigger | if the exhaust fan speed rises considerably within 2 h while exhaust air < 2 °C | same | not documented |

Sources: `MB-II` 9.3, `T-II` 9.4, `BA-II` 9.2, `T-S` 9.4, `MB-S` 9.3. The
manuals do not explain the meaning of "A1" and "A2".

Usage rules from the manuals (`MB-II` 6.2.1, `MB-S` 6.2):

- Do not switch the unit off in winter.
- Keep bedrooms at 16-18 °C minimum. Do not run the unit with room
  temperatures below 15 °C, especially with outdoor temperatures below 0 °C
  (`M-WRG-II`) or -5 °C (`M-WRG-S`). Otherwise frost protection is permanently
  active or the unit switches off.
- Run intensive ventilation regularly at high indoor humidity and before
  switching the unit off, to remove condensate.
- Frequent frost-protection activation and rising noise are listed as
  symptoms of clogged filters (`MB-II` 14).

## Filter monitoring

- Runtime based: after more than one year since the last filter change, the
  filter indicator activates (`MB-II` 12).
- The acoustic warning interval shortens over about 2-3 weeks. The filter
  must be changed when the warning sounds hourly for 1 s each time.
- `M-WRG-II`: LED 1 steady. `M-WRG-S`: acoustic only; optical via InControl
  on `S/Z-T`. `-T` variants: "nominal ventilation" LED blinks.
- The buzzer can be switched off in the app ("Summer für
  Filterwechselanzeige", `APP` slide 36).

Reset after a filter change:

| Control | Procedure | Source |
| --- | --- | --- |
| `M-WRG-II` keypad | hold key 1 for approx. 5 s; three beeps confirm | `MB-II` 12.3.7 |
| `M-WRG-S` step switch | switch I-II-III-II-I within 3 s; while it beeps, repeat within 3 s; three beeps confirm | `MB-S` 12.3.5, `WA-S` 7.14 |
| Remote control | hold the menu key for more than 3 s | `FBH` 10.3 |
| Modbus | not documented | — |

Older `M-WRG` units built up to 05/2017 monitored the filter soiling instead
of the runtime (`WA-S` 4.1.1). Details are in [maintenance.md](maintenance.md).

## External control input (Externer Steuereingang)

- Fitted as standard: an extra 230 V~ input (85-265 V~, 50-60 Hz) for a
  switch, timer, motion detector or similar (`MB-II` 11.1.1).
- It has a run-on relay with a switch-on delay (the unit starts only after the
  delay) and a run-on time (the unit returns to the previous program only after
  the run-on time).
- Commands from the external input have **higher priority than Modbus**
  (`MB-II` 11.1.1).
- With option `O/NOF`, windowless rooms can be ventilated according to
  DIN 18017-3.

| Parameter | Default | Range `M-WRG-II` | Range `M-WRG-S` |
| --- | --- | --- | --- |
| Airflow | 60 m³/h | 10-100 m³/h | 15-97 m³/h |
| Switch-on delay | 1 min | 0-240 min | 0-240 min |
| Run-on time | 15 min | 0-240 min | 0-240 min |

Sources: `MB-II` 11.1.2, `T-S` 11.1.2. These parameters can be changed via
Modbus (`42007..42009`), at the factory or with the remote control
(IDs 54-56). Remote-control parameter 95 selects which program the input
triggers (default 12 = "ventilation level external control input").

## Factory options

| Option | Function | Series | Source |
| --- | --- | --- | --- |
| `O/PARM` | parameterisation for all `M-WRG-II` and `M-WRG` units; factory fitted | both | `MB-II` 11.2 |
| `O/MVS` | minimum ventilation per DIN 18017-3: 40 m³/h from 08:00 to 20:00, 20 m³/h from 20:00 to 08:00; overrides all other settings; keys or switch positions become 20 / 40 / 60 (/ 80) m³/h; only together with `O/NOF` | both | `MB-II` 11.3, `T-S` 11.3 |
| `O/EGG-AUS` | input "device off" (for example smoke detector or window contact) plus a potential-free fault output; factory fitted | `M-WRG-II` only | `MB-II` 11.4 |
| `O/NOF` | mains switch without function; switching off must be possible elsewhere (fuse box) | both | `MB-II` 11.5 |
| `O/LFS` | moisture-protection ventilation: fixed minimum levels for day and night (factory default 20 m³/h from 08:00 to 20:00 and 20 m³/h from 20:00 to 08:00) with a humidity program running in the background; keys become 20 / 40 / 60 (/ 80) m³/h; only higher levels can be chosen; only with humidity / CO2 control and `O/NOF` | both | `MB-II` 11.6 |
| `O/Filter-FA` | allergy filter `M-WRG-FA` instead of the standard outdoor filter | `M-WRG-S` | `MB-S` 11.5 |
| `M-WRG-SN` | protective cap for the mains switch, IPX4 for protection zone 2 | `M-WRG-S` | `MB-S` 11.6 |

Remote-control parameters 133 and 134 configure the smoke-detector input
(default mode 18 = "device off"; contact type normally open or normally
closed) on `M-WRG-II` (`FBH` 9.4.3).

## Troubleshooting (manufacturer table)

| Symptom | Cause | Remedy |
| --- | --- | --- |
| Unit does not run | protective mode after an EMC disturbance | switch off, wait 15 s, switch on |
| Unit does not run | installation error, or defective switch, motor or control | have an electrician check it |
| LED 2 steady (`M-WRG-II`) | device fault | check Modbus register `41016`; contact Meltem |
| Flaps do not open (`M-WRG-S`) | actuator without power after a long standstill, or foreign objects | switch off, wait at least 15 s, switch on; remove debris |
| Unit beeps at intervals | filter change interval exceeded | change filters |
| Frequent frost protection, rising noise | filters overdue or heavily soiled | change filters |

Sources: `MB-II` 14, `MB-S` 14, `T-S` 14.

## Relevance for this integration

- **Intensive ventilation ends on its own** after the configured duration
  (default 15 min, configurable 0-240 min); the previous program then resumes.
  This supports the self-clearing hypothesis in
  [../HARDWARE_BACKLOG.md](../HARDWARE_BACKLOG.md) (HW-1). The documents do not
  say how this shows up in `41120..41124`.
- **"Off" via Modbus vs. standby**: the Modbus manual only has "Aus"
  (`41120 = 1`). The other controls describe "Standby" (unit powered, flaps
  closed). The documents do not say whether Modbus "Aus" equals standby.
- **Keypad LEDs** show the selected program only for 10 s, so dark keypad LEDs
  are normal. This fits the observations on `Abluft` / `Zuluft` LEDs in
  [../MELTEM.md](../MELTEM.md).
- **Frost protection** deliberately unbalances the airflows and can stop both
  fans for an hour at a time. During `41018 = 1`, the readings of
  `41020` / `41021` can therefore differ from the target or be 0 without a
  fault.
- **CO2 threshold defaults differ by series** (800 vs. 600 ppm); see `42003`
  in [modbus.md](modbus.md).
- **The external control input overrides Modbus.** A write that seems
  ignored may be caused by an active external input.
- **LOW / MEDIUM / HIGH** are the manufacturer's names for the first three
  fixed programs. They match the preset names used by the app and by this
  integration.
- **`M-WRG-II` supply-only stops the extract fan completely** (0 m³/h), while
  `M-WRG-S` keeps 15 m³/h on the other side.
