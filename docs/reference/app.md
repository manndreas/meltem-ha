# Meltem app

Manufacturer facts about the Meltem smartphone app. The source is the
`APP` slide deck (2022, 55 slides, German). Many slides are screenshots with
little text, so labels are given as printed. Source IDs are explained in
[README.md](README.md).

## Prerequisites and setup

- The app is available for iOS (App Store) and Android (Play Store)
  (`APP` slide 3).
- An account is required before first use. Registration needs the required
  fields and acceptance of the privacy policy. A confirmation e-mail with an
  activation link follows (`APP` slides 4-8).
- One account can be logged in on several devices, mixing iOS and Android
  (`APP` slide 9).
- **Gateway first:** a gateway (`M-WRG-GW`) must be registered first. Gateway
  and phone must be in the same network for registration. Afterwards they
  need not be (`APP` slide 10).
- The app searches the local network for gateways. The MAC address on the
  back of the gateway identifies the right one. Registration uses the QR code
  on the back of the gateway, or the code printed below it. The gateway name
  can have up to 20 characters (`APP` slides 12-15).
- **Adding units** (`M-WRG-II` or `M-WRG` series) (`APP` slides 16-24):
  1. Tap "Gerät hinzufügen" (add device) and scan the QR code behind the
     unit cover, or enter the code manually.
  2. With manual entry, the unit type must also be selected manually; a wrong
     type prevents the connection. With a QR scan the type is detected
     automatically.
  3. Put the unit into connection mode, then tap "Verbinden" (connect).
  4. Name the unit, optionally grouped by "Ort" (location), "Bereich" (area)
     and "Gerätename" (unit name). Grouping can be changed later with
     "Geräte umbenennen".

Gateway prerequisites and LEDs are in [gateway.md](gateway.md). The remote
control manual states that a unit stays in connection mode for 5 min after
power-on (`FBH` 6.2).

## Unit view controls

| Control | Label | Behaviour | Source |
| --- | --- | --- | --- |
| Quick-select | `LOW`, `MED`, `HIGH` | fixed airflows that can be changed in the settings. The same values also apply to the unit's own keys and to InControl | `APP` slide 29 |
| Manual | "Manuell" | any level from 10 to 100 m³/h | `APP` slide 29 |
| Extract-only | "Abluftbetrieb" | extract and supply airflow can both be set; the values are stored and can be recalled | `APP` slide 29 |
| Supply-only | "Zuluftbetrieb" | supply and extract airflow can both be set; the values are stored | `APP` slide 29 |
| Automatic | `AUTO` | automatic program controlled by humidity (rH) and CO2 | `APP` slide 30 |
| CO2 | `CO2` | CO2 program | `APP` slide 30 |
| Humidity | "Feuchte" | humidity program controlled by relative humidity | `APP` slide 30 |
| Intensive | "Intensivlüftung" | time-limited intensive ventilation | `APP` slide 30 |
| Standby | "Standby" | the unit goes into standby, closes the flaps and waits for a new command. The command can come from a time program, the unit's own keys, InControl, the 4-way push button, the external radio sensors or the app | `APP` slide 30 |

Information shown per unit (`APP` slide 31):

- filter change indicator in days (time remaining until a filter change is
  needed)
- extract air sensor values and supply air sensor values
- other sensor values and operating hours. Operating hours are updated only
  once a day.
- activation of an existing time program

## Settings

Labels as printed (`APP` slides 33-36). Ranges and defaults are not given in
the app manual. Probable remote-control parameter IDs are listed in
[device-parameters.md](device-parameters.md#mapping-app--remote-control--modbus).

| Slide | Label (German) | English |
| --- | --- | --- |
| 33 | Startwert der Feuchte-Regelung | humidity control start value |
| 33 | Minimaler / Maximaler Volumenstrom der Feuchte-Regelung | min. / max. airflow of humidity control |
| 33 | Startwert der CO2 Regelung | CO2 control start value |
| 33 | Minimaler / Maximaler Volumenstrom der CO2 Regelung | min. / max. airflow of CO2 control |
| 34 | Volumenstrom der Intensivlüftung, Nachlaufzeit der Intensivlüftung | intensive ventilation airflow and run-on time (duration) |
| 34 | Taste I / II / III oder Geräteschalter I / II / III | airflow for key or switch position I / II / III (LOW / MED / HIGH) |
| 34-35 | Taste IIII (nur M-WRG-II) Abluft / Zuluft | key 4 extract / supply airflow (`M-WRG-II` only) |
| 35 | Zuluftbetrieb Volumenstrom Zuluft / Abluft | supply-only operation, supply / extract airflow; applies to InControl only |
| 35 | Abluftbetrieb Volumenstrom Zuluft / Abluft | extract-only operation, supply / extract airflow; applies to InControl only |
| 35 | CO2 Regelung mit Zuluft VOC Sensor EIN/AUS | CO2 control with supply-air VOC sensor on/off |
| 36 | CO2-Regelung mit Zuluft VOC-Sensor Startwert | start value of that control |
| 36 | CO2-Regelung mit Zuluft minimaler Volumenstrom Zuluft | min. supply airflow of that control |
| 36 | CO2-Regelung mit Zuluft Zeitverzögerung der Regelung | control delay of that control |
| 36 | Pausenzeit des Standby-Betriebs | standby (sniffing) pause time |
| 36 | Schnüffelzeit des Standby-Betriebs (mind. 5 Minuten) | sniffing time, at least 5 min |
| 36 | Lüftungsstufe des Standby-Betriebs | ventilation level during sniffing |
| 36 | Klappen schließen / Standby EIN/AUS | close flaps / standby on/off |
| 36 | Summer für Filterwechselanzeige EIN/AUS | buzzer for the filter indicator on/off |

The supply-only and extract-only airflows in the settings apply only to
InControl. The "Abluftbetrieb" / "Zuluftbetrieb" buttons in the unit view
store their own values (slide 29). The documents do not say whether these are
the same stored parameters.

## Programs

### Time program (Zeitprogramm)

- Up to 6 switching points per day and up to 42 per unit. Each switching
  point starts a program, which runs until the next switching point or a user
  action (`APP` slide 42).
- Actions can be set for single days, several days or all days. "Speichern"
  transfers the program to the unit (`APP` slide 39).
- The program is stored on the unit and runs even when the app or the
  internet is unavailable (`APP` slide 39).
- The time program must be activated in the unit view (`APP` slides 31, 42).
- Templates, for example summer and winter programs, are stored only in the
  app on the specific phone. They can be loaded into other units, and a
  program can be sent to several selected units at once (`APP` slides 41,
  42).

### Interval program (Intervall-Programm)

- Parameters: airflow during the pause, airflow during the run time, pause
  time and run time; started with a start button (`APP` slide 43).
- The program always starts with the run time. During the pause the unit goes
  into standby and closes the flaps. It repeats until the user changes
  something. The active interval program is shown in the unit status
  (`APP` slide 44).
- Example: run 60 min at 30 m³/h, pause 60 min at 0 m³/h.

### Temperature program (Temperatur-Programm)

- Only for unit types with humidity sensors, because it needs the supply air
  temperature (`APP` slides 45, 46).
- Parameters: airflow, "Temp. min Zuluft", "Temp. max Zuluft", start button.
- If the supply air temperature rises above the maximum, the unit goes into
  standby: levels at zero and flaps closed for 60 min. It then ventilates for
  5 min to check whether operation can resume. Below the minimum it also goes
  into standby, to avoid cooling the room down (`APP` slide 46).

## Multiple units

- Several units can be selected and controlled together. The combined view
  shows no values because the units may differ. All single-unit actions are
  available, including time program, intensive ventilation, temperature
  program and settings (`APP` slides 52-54).

## Relevance for this integration

- **Preset names:** the app uses `LOW` / `MED` / `HIGH`, `AUTO`, `CO2`,
  "Feuchte", "Intensivlüftung", "Abluftbetrieb", "Zuluftbetrieb" and
  "Standby". These are the labels users know. Keep integration terminology
  close to them.
- **The LOW / MED / HIGH airflows are shared** with the unit's own keys and
  InControl. They are device parameters (IDs 13-15), not app-local values.
- **Abluft / Zuluft shortcuts store their own airflows.** This matches the
  observation in [../MELTEM.md](../MELTEM.md) that the app sends a configured
  airflow for these shortcuts.
- **Operating hours update once a day** in the app. A slow polling interval
  for `41030` / `41032` loses nothing.
- **Time, interval and temperature programs** run on the unit and are
  configured through the cloud app. The Modbus documents describe no way to
  read or detect them. An active time program may change the operating mode
  without any Modbus write from this integration.
- **The supply-air VOC sensor** settings are the only mention of VOC in the
  collected documents. They support the existence of a VOC-equipped variant
  (`O/VOC-AUL` in this integration) but give no Modbus details.
