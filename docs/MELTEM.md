# MELTEM interface documentation

This document collects the Meltem reference material we use in this repository
in handwritten Markdown form. It combines the relevant Modbus register and
configuration details for `M-WRG-S` and `M-WRG-II` units with a merged feature
matrix taken from the manufacturer documentation.

Where the manuals differ between product families, the differences are called
out inline. Where the tested gateway behavior differs from the documented
register mapping, that deviation is noted explicitly as an observed quirk.

## Modbus configuration

Sources:

- <https://www.meltem.com/fileadmin/downloads/documents/Meltem%20BA-IA_M-WRG-S_M.pdf>
- <https://www.meltem.com/fileadmin/downloads/documents/Meltem%20BA-IA_M-WRG-II_P-M_E-M.pdf>

### Modbus configuration

#### Default settings

- Start bits: 8
- Parity: E
- Stop bits: 1
- Baud rate: 19200 bps
- Slave address: 1, the required slave address should be specified in the purchase order

#### Function codes

Following function codes are supported:

- `0x03` Read Holding Register
- `0x04` Read Input Register
- `0x06` Write Single Holding Register
- `0x08` Diagnostics
- `0x11` Report ID

#### Frame requirements

- RTU encoded
- CRC16-ANSI Checksum, Polynomial `0x8005` / Reversed `0xA001`, Initialized `0xFFFF`
- Character Pauses Max `1.5 * Character Time`
- Frame Delimiter `3.5 * Character Time Idle`

#### Setting and addressing

| Register number | Function/name | Data type | Description |
| --- | --- | --- | --- |
| 30000 | Baud rate | UINT8 | `0 = 9600 bps`, `1 = 19200 bps` |
| 30002 | Slave address | UINT8 | Modbus slave address: `1 to 247` |

#### Registers

| Register number | Read/write | Function/name | German | Data type | Unit |
| --- | --- | --- | --- | --- | --- |
| 41016 | R | Error message: `0 = device OK; 1 = error` | Fehlermeldung: `0 = Gerät OK; 1 = Fehler` | UINT8 |  |
| 41018 | R | Frost protection function: `0 = not active; 1 = active` | Frostschutzfunktion: `0 = nicht aktiv; 1 = aktiv` | UINT8 |  |
| 41000, 41001 | R | Exhaust air temperature | Fortlufttemperatur | Float 32 bit | `°C` |
| 41002, 41003 | R | Outdoor air temperature | Außenlufttemperatur | Float 32 bit | `°C` |
| 41004, 41005 | R | Extract air temperature | Ablufttemperatur | Float 32 bit | `°C` |
| 41009, 41010 | R | Supply air temperature | Zulufttemperatur | Float 32 bit | `°C` |
| 41006 | R | Humidity, extract air | Feuchte Abluft | UINT16 | `%` |
| 41011 | R | Humidity, supply air | Feuchte Zuluft | UINT16 | `%` |
| 41007 | R | CO2, extract air | CO2 Abluft | UINT16 | `ppm` |
| 41013 | R | VOC, supply air |  | UINT16 | `ppm` |
| 41020 | R | Ventilation level for extract air | Lüftungsstufe Abluft | UINT8 | `m³/h` |
| 41021 | R | Ventilation level for supply air | Lüftungsstufe Zuluft | UINT8 | `m³/h` |
| 41017 | R | Air filter change indicator: `0 = air filter change time not elapsed; 1 = air filter change time elapsed` | Luftfilterwechsel-Anzeige: `0 = Luftfilterwechsel-Zeit nicht abgelaufen; 1 = Luftfilterwechsel-Zeit abgelaufen` | UINT8 |  |
| 41027 | R | Time until air filter change | Zeit bis Luftfilterwechsel | UINT16 | Days |
| 41030, 41031 | R | Ventilation unit operating hours | Betriebsstunden Lüftungsgerät | UINT32 | `h` |
| 41032, 41033 | R | Fan motors operating hours | Betriebsstunden Lüftermotore | UINT32 | `h` |

| Register | Read/write | Function/name | German | Min. | Max. | Step | Default | Data type | Unit |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 42000 | R/W | Rel. humidity starting point | Rel. Feuchte Startpunkt | 40 | 80 | 1 | 60 | UINT8 | `%` |
| 42001 | R/W | Min. ventilation level for humidity control | Min. Lüftungsstufe Feuchteregelung | 0 | 100 | 10 | 10 | UINT8 | `%` |
| 42002 | R/W | Max. ventilation level for humidity control | Max. Lüftungsstufe Feuchteregelung | 10 | 100 | 10 | 60 | UINT8 | `%` |
| 42003 | R/W | CO2 starting point | CO2 Startpunkt | 500 | 1200 | 1 | 800 (`M-WRG-S`: 600) | UINT16 | `ppm` |
| 42004 | R/W | Min. ventilation level for CO2 control | Min. Lüftungsstufe CO2-Regelung | 0 | 100 | 10 | 10 | UINT8 | `%` |
| 42005 | R/W | Max. ventilation level for CO2 control | Max. Lüftungsstufe CO2-Regelung | 10 | 100 | 10 | 60 | UINT8 | `%` |
| 42007 | R/W | Ventilation level for external control input | Lüftungsstufe Externer Steuereingang | 10 | 100 | 10 | 60 | UINT8 | `%` |
| 42008 | R/W | Switch-on delay for external control input | Einschaltverzögerung Externer Steuereingang | 0 | 240 | 1 | 1 | UINT8 | `min` |
| 42009 | R/W | Run-on time for external control input | Nachlaufzeit Externer Steuereingang | 0 | 240 | 1 | 15 | UINT8 | `min` |

Temperature register quirk observed on tested `M-WRG-GW` gateways:

- documented exhaust air temperature `41000/41001` behaved like extract air temperature
- documented extract air temperature `41004/41005` behaved like exhaust air temperature
- in other words: `41000` and `41004` appear effectively swapped on some setups

### Reverse-engineered app preset behavior

The published Modbus manuals do not document how the Meltem app drives keypad
LED states for `LOW` / `MED` / `HIGH`, temporary intensive ventilation, or the
app-side `Abluft` / `Zuluft` shortcuts.

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
- the Home Assistant integration currently exposes `Abluft` / `Zuluft` as
  app-like preset modes as well, but when those are triggered from HA it uses
  the room's current known airflow as the active-side target because the app's
  separately stored shortcut values are not yet readable

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
    `60/60 m3/h`
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
- instead the panel-side changes appeared in shadow/meta ranges:
  - `neutral -> Abluft`
    - `51120..51133`: broad `+1` increment pattern
    - `51150..51151`: `27 -> 28`
    - `52007: 27 -> 4352`
    - `52008..52010`: `27 -> 28`
  - `neutral -> Zuluft`
    - `51113: 4353 -> 4352`
    - `52008: 4352 -> 5376`
    - `52009: 31 -> 1055`
- direct raw Modbus runtime writes such as `41120 = 4`, `41121 = 0`,
  `41122 = 201` or `205`, then `41132 = 0`, still changed airflow on that
  unit but did not light the physical keypad LEDs
- this means the reverse-engineered `200 + airflow / 10` encoding is a valid
  runtime shortcut path, but not yet a complete model of the local panel LED
  semantics on all observed hardware
- later user verification on the same setup showed that the official Meltem
  app also leaves the physical keypad LEDs dark when switching to `Abluft` /
  `Zuluft`
- therefore missing LEDs for these two shortcuts should currently be treated
  as likely normal device behavior, not as proof that the HA integration is
  writing the wrong runtime preset registers

#### Sensors in the different ventilation unit types

| Sensor type | German | M-WRG-S M | M-WRG-S M-F | M-WRG-S M-FC | M-WRG-II P-M / M-WRG-II E-M | M-WRG-II P-M-F / M-WRG-II E-M-F | M-WRG-II P-M-FC / M-WRG-II E-M-FC | with option M-WRG-II O/VOC-AUL |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Exhaust air temperature | Fortlufttemperatur | X | X | X | X | X | X | X |
| Outdoor air temperature | Außenlufttemperatur |  | X | X |  | X | X | X |
| Extract air temperature | Ablufttemperatur |  | X | X |  | X | X | X |
| Supply air temperature | Zulufttemperatur |  | X | X |  | X | X | X |
| Rel. humidity, extract air | Rel. Feuchte Abluft |  | X | X |  | X | X | X |
| Rel. humidity, supply air | Rel. Feuchte Zuluft |  | X | X |  | X | X | X |
| CO2, extract air | CO2 Abluft |  |  | X |  |  | X | X |
| VOC, supply air | VOC Zuluft |  |  |  |  |  |  | X |

#### Setting the ventilation level

Write sequence:

- Balanced: write `41120`, `41121`, then `41132`.
- Unbalanced: write `41120`, `41121`, `41122`, then `41132`.
- `41132` must always be written last. Once `41132` has been written, the unit accepts registers `41120` to `41132`.

##### Balanced

| Mode | Register 41120 (UINT8) | Register 41121 (UINT8), supply air and exhaust air fan | Register 41132 (UINT8) |
| --- | --- | --- | --- |
| Off | 1 | Not used (`M-WRG-S`: `Nicht benutzt`) | 0 |
| Ventilation level | 3 | The range from `0` to `200` corresponds to an air flow of `0` to `100 m³/h` (`M-WRG-S`: `97 m³/h`).<br><br>Example:<br>A value of `70` corresponds to `35 m³/h`<br>A value of `100` corresponds to `50 m³/h` | 0 |
| Humidity control (*) | 2 | 112 | 0 |
| CO2 control (**) | 2 | 144 | 0 |
| Automatic mode (**) | 2 | 16 | 0 |

(*) On F and FC unit variants

(**) On FC unit variant

##### Unbalanced

| Mode | Register 41120 (UINT8) | Register 41121 (UINT8), supply air fan | Register 41122 (UINT8), exhaust air fan | Register 41132 (UINT8) |
| --- | --- | --- | --- | --- |
| Ventilation level | 4 | The range from `0` to `200` corresponds to an air flow of `0` to `100 m³/h` (`M-WRG-S`: `97 m³/h`).<br><br>Example:<br>`70` corresponds to `35 m³/h`<br>`100` corresponds to `50 m³/h` | The range from `0` to `200` corresponds to an air flow of `0` to `100 m³/h` (`M-WRG-S`: `97 m³/h`).<br><br>Example:<br>`70` corresponds to `35 m³/h`<br>`100` corresponds to `50 m³/h` | 0 |

## Feature matrix

Sources:

- <https://www.meltem.com/fileadmin/downloads/documents/Meltem%20Techn%20Daten%20M-WRG.pdf>
- <https://www.meltem.com/fileadmin/downloads/documents/Meltem%20TD_System_Ueberblick_M-WRG-II.pdf>

### Full matrix

| Feature | S | S/Z-T | S/Z-T-F | S/Z-T-FC | S/Z-T | S/Z-T-F | S/Z-T-FC | S/Z-T | S/Z-T-F | S/Z-T-FC | S/Z-T | S/Z-T-F | S/Z-T-FC | S M | S M-F | S M-FC | II E | II E-F | II E-FC | II E | II E-F | II E-FC | II E | II E-F | II E-FC | II E-T | II E-T-F | II E-T-FC | II E-T | II E-T-F | II E-T-FC | II E-M | II E-M-F | II E-M-FC |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 10 Lüftungsstufen |  |  |  |  |  |  |  | X | X | X | X | X | X | X | X | X |  |  |  |  |  |  |  |  |  | X | X | X | X | X | X | X | X | X |
| 5 Lüftungsstufen |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  | X |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |
| 4 Lüftungsstufen | X | X | X | X | X | X | X |  |  |  |  |  |  |  |  |  |  | X | X | X | X | X | X | X | X |  |  |  |  |  |  |  |  |  |
| Abluftbetrieb |  |  |  |  |  |  |  | X |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  | X | X | X | X |  |  |  |  |  |
| Zuluftbetrieb |  |  |  |  |  |  |  | X | X |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  | X | X | X | X | X |  |  |  |
| Abluftbetrieb einstellbar |  |  |  |  | X | X | X |  |  |  | X | X | X | X | X | X |  |  |  |  |  |  | X | X | X |  |  |  | X | X | X | X | X | X |
| Zuluftbetrieb einstellbar |  |  |  |  | X | X | X |  |  |  | X | X | X | X | X | X |  |  |  |  |  |  | X | X | X |  |  |  | X | X | X | X | X | X |
| Feuchte-Regelung (rF) |  |  | X | X |  |  |  |  | X | X |  |  |  |  |  |  |  |  |  |  | X | X |  |  |  |  | X | X |  |  |  |  |  |  |
| Feuchte-Regelung (rF) einstellbar |  |  |  |  |  | X | X |  |  |  |  | X | X |  | X | X |  |  |  |  |  |  |  | X | X |  |  |  |  | X | X |  | X | X |
| CO2-Regelung |  |  |  |  |  |  |  |  |  | X |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  | X |  |  |  |  |  |  |
| CO2-Regelung einstellbar |  |  |  |  |  |  | X |  |  |  |  |  | X |  |  | X |  |  |  |  |  |  |  |  | X |  |  | X |  |  | X |  |  | X |
| Automatik-Betrieb rF + CO2 |  |  |  |  |  |  | X |  |  |  |  |  |  |  |  |  |  |  |  |  |  | X |  |  |  |  |  | X |  |  |  |  |  |  |
| Automatik-Betrieb rF + CO2 einstellbar |  |  |  |  |  |  | X |  |  |  |  |  | X |  |  | X |  |  |  |  |  |  |  |  | X |  |  | X |  |  | X |  |  | X |
| Intensivlüftung temporär | X | X | X | X |  |  |  | X | X | X |  |  |  |  |  |  | X | X | X | X | X | X |  |  |  | X | X | X |  |  |  |  |  |  |
| Intensivlüftung temporär einstellbar |  |  |  |  | X | X | X |  |  |  | X | X | X | X | X | X |  |  |  |  |  |  | X | X | X |  |  |  | X | X | X | X | X | X |
| Zeitprogramm einstellbar |  |  |  |  |  |  |  |  |  |  | X | X | X | X | X | X |  |  |  |  |  |  |  |  |  |  |  |  | X | X | X | X | X | X |
| Steuereingang | X | X | X | X | X | X | X | X | X | X | X | X | X | X | X | X | X | X | X | X | X | X | X | X | X | X | X | X | X | X | X | X | X | X |
| Eingang Gerät AUS (Rauchmelder, Fensterkontakt 24V) optional | X | X | X | X | X | X | X | X | X | X | X | X | X | X | X | X | X | X | X | X | X | X | X | X | X | X | X | X | X | X | X | X | X | X |
| Programm Mindestlüftung nach DIN 18017-3 Werkseinstellung, NICHT ABSCHALTBAR! optional | X | X | X | X | X | X | X | X | X | X | X | X | X | X | X | X | X | X | X | X | X | X | X | X | X | X | X | X | X | X | X | X | X | X |
| Lüftung zum Feuchteschutz mit Feuchte-Regelung, NICHT ABSCHALTBAR! optional |  |  | X | X |  | X | X |  | X | X |  | X | X |  | X | X |  | X | X |  | X | X |  | X | X |  | X | X |  | X | X |  | X | X |
| Filterwechselanzeige optisch/akustisch | X | X | X | X | X | X | X | X | X | X | X | X | X | X | X | X | X | X | X | X | X | X | X | X | X | X | X | X | X | X | X | X | X | X |
| Betriebsstunden auslesen |  |  |  |  | X | X | X |  |  |  | X | X | X | X | X | X |  |  |  |  |  |  | X | X | X |  |  |  | X | X | X | X | X | X |
| Betriebsstunden auslesen mit Zubehör |  | X | X | X | X | X | X | X |  |  |  |  |  |  |  |  |  |  |  | X | X | X | X | X | X | X | X | X |  |  |  |  |  |  |
| Anzeige Sensorwerte |  |  |  |  |  |  |  | X | X | X | X | X | X |  | X | X |  |  |  |  |  |  |  |  |  | X | X | X | X | X | X | X | X | X |
| Anzeige rF ZUL > rF ABL |  |  |  |  |  |  |  |  | X | X |  | X | X |  | X | X |  |  |  |  | X | X |  |  |  | X | X | X | X | X | X |  |  |  |
| Störmeldung optisch LED / Zeichen |  | X | X | X | X | X | X | X | X | X | X | X | X | X | X | X | X | X | X | X | X | X | X | X | X | X | X | X | X | X | X | X | X | X |
| Betriebsmeldung |  |  |  |  | X | X | X |  |  |  | X | X | X | X | X | X |  |  |  |  |  |  | X | X | X |  |  |  | X | X | X | X | X | X |
| Betriebsmeldung LED |  | X | X | X |  |  |  | X | X | X |  |  |  |  |  |  |  |  |  | X | X | X |  |  |  | X | X | X | X | X | X |  |  |  |
| Frostschutzfunktion | X | X | X | X | X | X | X | X | X | X | X | X | X | X | X | X | X | X | X | X | X | X | X | X | X | X | X | X | X | X | X | X | X | X |
