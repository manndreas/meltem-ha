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
| Intensivlüftung temporaer | X | X | X | X |  |  |  | X | X | X |  |  |  |  |  |  | X | X | X | X | X | X |  |  |  | X | X | X |  |  |  |  |  |  |
| Intensivlüftung temporaer einstellbar |  |  |  |  | X | X | X |  |  |  | X | X | X | X | X | X |  |  |  |  |  |  | X | X | X |  |  |  | X | X | X | X | X | X |
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
| Stoermeldung optisch LED / Zeichen |  | X | X | X | X | X | X | X | X | X | X | X | X | X | X | X | X | X | X | X | X | X | X | X | X | X | X | X | X | X | X | X | X | X |
| Betriebsmeldung |  |  |  |  | X | X | X |  |  |  | X | X | X | X | X | X |  |  |  |  |  |  | X | X | X |  |  |  | X | X | X | X | X | X |
| Betriebsmeldung LED |  | X | X | X |  |  |  | X | X | X |  |  |  |  |  |  |  |  |  | X | X | X |  |  |  | X | X | X | X | X | X |  |  |  |
| Frostschutzfunktion | X | X | X | X | X | X | X | X | X | X | X | X | X | X | X | X | X | X | X | X | X | X | X | X | X | X | X | X | X | X | X | X | X | X |
