# Filters and maintenance

Manufacturer facts about filters, filter monitoring and maintenance plans.
Source IDs are explained in [README.md](README.md).

## Filters

### M-WRG-II

| Art.-Nr. | Type | Use | Class | End disc colour |
| --- | --- | --- | --- | --- |
| `200335` | `M-WRG-II FA` | outdoor air filter (standard) | ISO ePM1 60 % (F7) | turquoise |
| `200191` | `M-WRG-II FK` | activated carbon filter, optional for outdoor air; also binds odours and gases such as fuels, nitrogen oxides, ozone and solvents | ISO ePM2.5 55 % (F7) | turquoise |
| `200267` | `M-WRG-II FS` | extract air filter (standard) | ISO Coarse 60 % (G4) | yellow |

Only these filters are permitted in the respective position. Article number
and filter class are embossed on the end disc (`MB-II` 12.1, 12.3.4,
12.3.5; `WA-II`).

### M-WRG-S / M-WRG-K

| Art.-Nr. | Type | Use | Class |
| --- | --- | --- | --- |
| `200095` | `M-WRG-FS` | standard filter for outdoor and extract air | ISO ePM10 65 % (G4) |
| `200350` | `M-WRG-FA` | allergy filter, outdoor air only | ISO ePM1 60 % (F7) |
| `200106` | `M-WRG-FK` | activated carbon filter, outdoor air only | ISO ePM10 60 % (M6) |

Sources: `MB-S` 1.7.7, 12.1; `WA-S`.

Both series use round filter cartridges (Rundfilterpatronen) for outdoor and
extract air. Filters can be changed without tools. Switch the unit off at the
mains switch first. On `M-WRG-S` the open flaps otherwise block removal of the
cartridges (`WA-S` 4.1). With option `O/NOF` the unit must be isolated at the
fuse instead (`MB-II` 12.3.1).

### Hygiene requirement

VDI 6022 and DIN 1946-6 (category H) require an outdoor air filter of class
ISO ePM1 ≥ 50 % (F7). It is standard on every `M-WRG-II` and optional on
`M-WRG-S` (`MB-II` 6.3, `T-S` 6.3).

## Change intervals

- DIN 1946-6 recommends changing filters every six months.
- For hygiene reasons, change outdoor and extract filters after one year at
  the latest, ideally before the heating season.
- Change every six months with heavy air pollution (traffic, industry, dusty
  rooms).
- Always change both filters together. The permeability of both filters
  affects efficiency and energy consumption.

Source: `MB-II` 6.3, `WA-II` 4.1, `WA-S` 4.1.

## Filter change indicator

| Series / build | Monitoring | Indication | Source |
| --- | --- | --- | --- |
| `M-WRG-II` | runtime since the last filter change; active after more than one year | LED 1 steady, plus acoustic warning | `MB-II` 12 |
| `M-WRG-S` / `M-WRG-K` from build 06/2017 | runtime since the last filter change; active after more than one year | acoustic; optical via InControl on `S/Z-T` | `WA-S` 4.1.1, `SYS-S` |
| `M-WRG-S` / `M-WRG-K` up to build 05/2017 | soiling level of the filter cartridges, monitored automatically | acoustic; on `M-WRG-K` also "!" in the upper right corner of the LCD | `WA-S` 4.1.1 |

- The acoustic warning interval shortens over about 2-3 weeks. The change is
  due when the warning sounds hourly for 1 s. The long warning period is meant
  to leave time to order filters (`MB-II` 12).
- On the remote control the filter symbol is steady when the one-year interval
  is exceeded. It blinks every 2 s when the filter is soiled; that state
  exists only on `M-WRG` series units (`FBH` 12).
- The app shows the remaining days until a filter change and can switch the
  buzzer off (`APP` slides 31, 36).

Reset procedures are listed in
[functions.md](functions.md#filter-monitoring). No Modbus reset is
documented.

## Maintenance plans

### M-WRG-II (`WA-II`)

| Plan | Interval | Who | Content |
| --- | --- | --- | --- |
| A | yearly | user | clean cover, filter cover and filter ring; check and clean the outdoor air pipe; clean the filter base, air grilles or duct adapters and the intermediate plate; visually check the heat exchanger and vacuum it with a soft brush; change both filters; function test of supply and exhaust fans; reset the filter indicator; log the maintenance |
| B | every 5 years, earlier if the yearly check finds heavy soiling | qualified specialists only | remove, wash and dry the heat exchanger (warm water, mild soap if needed, dry upright); grease the sealing lips thinly with Vaseline; clean flat ducts or flexible pipes if present; function test of the fans |

### M-WRG-S / M-WRG-K (`WA-S`)

| Plan | Interval | Who | Content |
| --- | --- | --- | --- |
| A | yearly | user | as for `M-WRG-II`, plus removing and cleaning both flap frames, cleaning the flaps in place and checking the flap mechanism; on `M-WRG-K`, check that the display reacts to the remote control. Do **not** vacuum the heat exchanger (risk of damaging the fins) |
| B | every 5 years | user | disinfect the unit: spray disinfectant into the supply opening, mainly onto the heat exchanger, and run the unit for about 5 min without the outdoor filter; clean flat ducts or flexible pipes if present; function test of the fans |

Further maintenance facts:

- The warranty expires if the unit is not maintained according to the
  maintenance manual (`WA-II`).
- After maintenance, switch the unit on and check that the flaps open audibly
  and that air flows at the extract filter and the supply opening (`WA-II`
  8.12, `WA-S` 7.13).
- Outer surfaces: wipe with a damp cloth and mild soap solution. Never use
  high-pressure or steam cleaners or acidic or abrasive cleaners (`MB-II` 13).

## Relevance for this integration

- `41017` switches to `1` after one year of runtime since the last reset;
  `41027` counts down the remaining days. On `M-WRG-S` units built up to
  05/2017 the indicator may instead reflect soiling. How such units map this
  to `41017` / `41027` is not documented.
- A filter reset exists only on the local controls, the remote control and
  probably the app. Exposing a "reset filter" action in Home Assistant would
  need an undocumented Modbus path.
- A permanently set `41017` together with frequent `41018 = 1` matches the
  manufacturer's troubleshooting pattern "filters overdue, frost protection
  activates often".
