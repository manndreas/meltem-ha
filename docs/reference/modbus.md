# Modbus RTU interface

Manufacturer facts about the Modbus RTU interface of the Modbus operating
variants (`-M`). Source IDs are explained in [README.md](README.md).

Covered unit types:

- `M-WRG-II P-M`, `M-WRG-II E-M` and their `-F` / `-FC` variants
  (`MB-II` ch. 15-17)
- `M-WRG-S M`, `M-WRG-S M-F`, `M-WRG-S M-FC` (`MB-S` ch. 15-17, `INFO-MB`)

Both manuals describe the same register map. Where the series differ, both
values are given. Behaviour observed on real hardware (for example the
`41000` / `41004` swap, the preset codes `227..230` and the shadow registers
`511xx` / `520xx`) is **not** part of the manufacturer documentation. Those
notes are in [../MELTEM.md](../MELTEM.md).

## Physical layer and wiring

| Topic | Manufacturer statement | Source |
| --- | --- | --- |
| Board | Modbus board inside the unit with a 10-pole terminal block for the bus cable and a 3-pin header for the termination jumper | `MB-II` 15.1.1, `MB-S` 15.1.1 |
| Signals | Terminals labelled `Modbus +A`, `Modbus -B`, `GND/Schirm` (shield) | `MB-II` 15.5.1 (figure) |
| Termination | The jumper enables a 120 Ω termination resistor. Set it at both physical ends of a line bus. With star wiring, set it only on the unit farthest away from the master | `MB-II` 15.5.1, 15.6 |
| Cable | `J-Y (St) Y 2 x 2 x 0.6 mm` or `J-Y (St) Y 2 x 2 x 0.8 mm`, stripped 8 mm | `MB-II` 15.1.2 |
| Topology | Master to the first unit, then unit to unit. Example wiring per floor. Star wiring is allowed | `MB-II` 15.5 |
| Installation | Route data and mains cables separately (EN 50174-2). A wrongly connected bus cable can damage the unit and voids the warranty | `MB-II` 15.6 |
| Master | A Modbus RTU master is always required on site. The manuals do not name a maximum number of units per bus | `MB-II` 7.1, 15 |

### Addressing

- Every Modbus board ships with slave address `1`.
- The desired slave address should be given with the order. Meltem then sets
  it at the factory (`MB-II` 15.7). The info sheet recommends ordering
  `M-WRG-S M` units pre-addressed (`INFO-MB`).
- The address is held in register `30002` (see below). The manuals do not
  describe a field procedure for changing it.

### Other bus systems

- **KNX:** requires the Modbus-KNX gateway `M-WRG-KNX-GW` (Art.-Nr. `200273`),
  one gateway per unit. It acts as the Modbus master of that unit, can be
  built into the unit and is configured with an ETS application
  (`MB-II` 17.1). The older info sheet names a third-party gateway
  "Weinzierl 886 KNX-Modbus RTU" instead (`INFO-MB`).
- **Loxone:** wire the units as a normal Modbus bus. A Loxone Modbus Extension
  acts as master. Units must be ordered pre-addressed (`MB-II` 17.2).
- **Other systems:** need a Modbus RTU interface. Whether it works has to be
  clarified with the system vendor (`MB-II` 17.3).

## Serial settings

| Setting | Value | Note |
| --- | --- | --- |
| "Startbits" | `8` | Printed like this in both manuals. Almost certainly means 8 **data** bits (8E1) |
| Parity | `E` (even) | |
| Stop bits | `1` | |
| Baud rate | `19200` bps | `9600` selectable via register `30000` |
| Slave address | `1` | Specify the wanted address with the order |

Sources: `MB-II` 16.1, `MB-S` 16.1.

### Function codes

| Code | Function |
| --- | --- |
| `0x03` | Read Holding Register |
| `0x04` | Read Input Register |
| `0x06` | Write Single Holding Register |
| `0x08` | Diagnostics |
| `0x11` | Report ID |

`0x10` (Write Multiple Registers) is **not** listed. Multi-register writes
therefore have to be issued as sequential single writes, which matches the
write-sequence rules below. Source: `MB-II` 16.2.

### Frame requirements

- RTU encoding
- CRC16-ANSI, polynomial `0x8005` (reversed `0xA001`), initial value `0xFFFF`
- Maximum pause between characters: `1.5` character times
- Frame delimiter: `3.5` character times idle

Source: `MB-II` 16.3. The manuals give no further timing guidance, such as
response time or a minimum gap between requests.

## Setting and addressing registers

| Register | Name | Type | Values |
| --- | --- | --- | --- |
| `30000` | Baud rate | UINT8 | `0` = 9600 bps, `1` = 19200 bps |
| `30002` | Slave address (Slave-Adresse) | UINT8 | `1..247` |

Source: `MB-II` 16.4. The table does not say whether these registers can be
read or written over the bus, or which function code applies.

## Status and measurement registers (read only)

| Register | Name (English) | German | Type | Unit / values |
| --- | --- | --- | --- | --- |
| `41000`-`41001` | Exhaust air temperature | Fortlufttemperatur | Float32 | °C |
| `41002`-`41003` | Outdoor air temperature | Außenlufttemperatur | Float32 | °C |
| `41004`-`41005` | Extract air temperature | Ablufttemperatur | Float32 | °C |
| `41006` | Humidity, extract air | Feuchte Abluft | UINT16 | % |
| `41007` | CO2, extract air | CO2 Abluft | UINT16 | ppm |
| `41009`-`41010` | Supply air temperature | Zulufttemperatur | Float32 | °C |
| `41011` | Humidity, supply air | Feuchte Zuluft | UINT16 | % |
| `41016` | Error message | Fehlermeldung | UINT8 | `0` = device OK, `1` = error |
| `41017` | Air filter change indicator | Luftfilterwechsel-Anzeige | UINT8 | `0` = change time not elapsed, `1` = elapsed |
| `41018` | Frost protection function | Frostschutzfunktion | UINT8 | `0` = not active, `1` = active |
| `41020` | Ventilation level, extract air | Lüftungsstufe Abluft | UINT8 | m³/h |
| `41021` | Ventilation level, supply air | Lüftungsstufe Zuluft | UINT8 | m³/h |
| `41027` | Time until air filter change | Zeit bis Luftfilterwechsel | UINT16 | days |
| `41030`-`41031` | Operating hours, ventilation unit | Betriebsstunden Lüftungsgerät | UINT32 | h |
| `41032`-`41033` | Operating hours, fan motors | Betriebsstunden Lüftermotore | UINT32 | h |

Sources: `MB-II` 16.5, `MB-S` 16.5 (identical tables).

Notes:

- The register table lists every register for every variant. Which sensors
  actually exist depends on the variant: see
  [models.md](models.md#sensor-equipment).
- Neither manual specifies the word order of the Float32 and UINT32 pairs.
- The documents do not describe what a unit without the matching sensor
  returns.
- The meaning of "Betriebsstunden Lüftungsgerät" is explained in the remote
  control manual. It counts time the unit is switched on, including standby,
  while the fan-motor counter counts only motor runtime (`FBH` 9.4.3, IDs 83
  and 84).
- `41008`, `41012`..`41015`, `41019`, `41022`..`41026` and `41028`..`41029`
  are not documented. The VOC register `41013` used by this integration does
  not appear in any collected document.

## Configuration registers (read/write)

| Register | Name (English) | German | Min | Max | Step | Default | Type | Unit |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `42000` | Rel. humidity starting point | Rel. Feuchte Startpunkt | 40 | 80 | 1 | 60 | UINT8 | % |
| `42001` | Min. ventilation level, humidity control | Min. Lüftungsstufe Feuchteregelung | 0 | 100 | 10 | 10 | UINT8 | % |
| `42002` | Max. ventilation level, humidity control | Max. Lüftungsstufe Feuchteregelung | 10 | 100 | 10 | 60 | UINT8 | % |
| `42003` | CO2 starting point | CO2 Startpunkt | 500 | 1200 | 1 | II: 800, S: 600 | UINT16 | ppm |
| `42004` | Min. ventilation level, CO2 control | Min. Lüftungsstufe CO2-Regelung | 0 | 100 | 10 | 10 | UINT8 | % |
| `42005` | Max. ventilation level, CO2 control | Max. Lüftungsstufe CO2-Regelung | 10 | 100 | 10 | 60 | UINT8 | % |
| `42007` | Ventilation level, external control input | Lüftungsstufe Externer Steuereingang | 10 | 100 | 10 | 60 | UINT8 | % |
| `42008` | Switch-on delay, external control input | Einschaltverzögerung Externer Steuereingang | 0 | 240 | 1 | 1 | UINT8 | min |
| `42009` | Run-on time, external control input | Nachlaufzeit Externer Steuereingang | 0 | 240 | 1 | 15 | UINT8 | min |

Sources: `MB-II` 16.5, `MB-S` 16.5. `42006` is not documented.

The level registers are specified in `%`. The defaults (`10` / `60`) equal the
documented airflows on `M-WRG-II` in m³/h (10 and 60 m³/h). The remote control
exposes the same settings directly in m³/h; see
[device-parameters.md](device-parameters.md).

## Setting the ventilation level

### Write order

- **Balanced:** write `41120`, `41121`, then `41132`.
- **Unbalanced:** write `41120`, `41121`, `41122`, then `41132`.
- `41132` must always be written last. After `41132` is written, the unit
  takes over registers `41120` to `41132`.

Sources: `MB-II` 16.7.1, 16.7.2 (notes).

### Balanced

| Mode | `41120` (UINT8) | `41121` (UINT8), supply and exhaust fan | `41132` (UINT8) |
| --- | --- | --- | --- |
| Off (Aus) | `1` | not used | `0` |
| Ventilation level (Lüftungsstufe) | `3` | `0..200`, see scaling | `0` |
| Humidity control (Feuchteregelung), `-F` / `-FC` only | `2` | `112` | `0` |
| CO2 control (CO2-Regelung), `-FC` only | `2` | `144` | `0` |
| Automatic mode (Automatikbetrieb), `-FC` only | `2` | `16` | `0` |

### Unbalanced

| Mode | `41120` | `41121` (UINT8), supply fan | `41122` (UINT8), exhaust fan | `41132` |
| --- | --- | --- | --- | --- |
| Ventilation level | `4` | `0..200` | `0..200` | `0` |

### Scaling

| Series | Raw range | Airflow range | Printed examples |
| --- | --- | --- | --- |
| `M-WRG-II` | `0..200` | `0..100` m³/h | `70` → 35 m³/h, `100` → 50 m³/h |
| `M-WRG-S` | `0..200` | `0..97` m³/h | `80` → 40 m³/h, `100` → 50 m³/h |

The `M-WRG-S` examples follow `raw / 2` and not `raw × 97 / 200`, which would
give 38.8 m³/h for raw `80` and 48.5 m³/h for raw `100`. The manual does not
resolve which of the two is correct; see
[Open questions](#open-questions-and-contradictions).

### Not documented

The manuals do **not** document:

- reading back `41120..41132`, or any meaning of `41123` / `41124`
- intensive ventilation, standby, filter reset, supply-only or extract-only
  programs as Modbus commands. The data sheets of the `-M` variants advertise
  intensive ventilation, supply-only and extract-only operation and time
  programs (`TD-II-E-M`, `TD-S-M`); the Modbus mechanism for these is not
  described.
- access to the device parameters ("Kennzahlen") that the remote control and
  app can change, such as LOW/MEDIUM/HIGH airflows or the intensive duration
- the product, version and diagnostic registers in `40xxx` or the gateway
  registers `43901` / `43902..` that this integration reads

## Priority of inputs

Commands received via the external control input take precedence over
commands received via Modbus (`MB-II` 11.1.1). The external-input parameters
can be changed via Modbus (`42007..42009`).

## Relevance for this integration

- `PROFILE_METADATA.max_airflow` (`97` for `M-WRG-S`, `100` for `M-WRG-II`)
  matches the documented scaling ranges.
- `CONTROL_SETTING_LIMITS` in `const.py` matches the documented min / max /
  step of `42000..42005`.
- The register constants in `const.py` follow the documented numbers. The
  extract / exhaust temperature constants are deliberately mapped the other
  way round because of the hardware observation in
  [../MELTEM.md](../MELTEM.md).
- The "write `41132` last" rule is the only documented ordering constraint.
  Settle delays and request gaps are not covered by any document.

## Open questions and contradictions

- **"Startbits: 8"**: printed in both manuals. Read it as 8 data bits.
- **`M-WRG-S` scaling example**: the printed examples imply `raw / 2`.
  `modbus_client._scale_airflow_to_raw` uses `level × 200 / max_airflow`, which
  gives raw `82` for 40 m³/h instead of the printed `80`. Unverified on
  hardware.
- **CO2 starting point range**: Modbus `42003` allows `500..1200` ppm. The
  data sheets (`TD-*-FC`) and the remote control (`FBH` ID 39) give
  `400..1400` ppm.
- **Unit of `42001` / `42002` / `42004` / `42005` / `42007`**: the Modbus
  manual says `%`; the remote control lists the equivalent parameters in m³/h
  with step 1 (II) or 10 (S). On `M-WRG-S` it is unclear whether `100 %`
  means 97 m³/h or 100 m³/h.
- **`41013` (VOC)**: used by this integration for the `O/VOC-AUL` profile but
  absent from every collected document.
