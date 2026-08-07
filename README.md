# Meltem Modbus Home Assistant Custom Integration

[![HACS Custom](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://github.com/hacs/integration)
[![GitHub Release](https://img.shields.io/github/v/release/manndreas/meltem-ha)](https://github.com/manndreas/meltem-ha/releases)
[![License](https://img.shields.io/github/license/manndreas/meltem-ha)](https://github.com/manndreas/meltem-ha/blob/main/LICENSE)

Home Assistant custom integration for Meltem `M-WRG-S` and `M-WRG-II`
ventilation units via the Meltem `M-WRG-GW` gateway and Modbus RTU over USB.

Use this integration at your own risk. It is an unofficial project and comes
with no warranty. The authors are not liable for damage to Meltem devices,
gateways, Home Assistant hosts, or other connected equipment.

## Features

- support for Meltem `M-WRG-S` and `M-WRG-II` unit families
- automatic discovery of configured units via the `M-WRG-GW` gateway
- per-unit profile selection during setup
- temperature, airflow, filter and operating-hour sensors
- separate supply air and extract air fan entities per unit
- app-style quick modes and sensor-driven control modes
- writable humidity and CO2 control thresholds for supported profiles
- USB discovery for the Meltem gateway

## Controlling a unit

Each unit is exposed as two `fan` entities:

- **Supply air** — target airflow of the supply fan
- **Extract air** — target airflow of the extract fan

Setting both to the same value runs the unit balanced. Setting them to
different values switches it to unbalanced operation, and setting one to zero
reproduces the extract-only or supply-only shortcuts of the Meltem app. When
both are zero, the unit is switched off; turning it back on starts both
directions again.

Two further controls complement the fans:

- **Quick mode** — the app shortcuts `Low`, `Medium` and `High`. The airflow
  behind these is configured in the Meltem app and cannot be read over Modbus.
  `Individual` means no shortcut is active, which also covers the extract-only
  and supply-only states set from the Meltem app.
- **Sensor control** — humidity, CO2 or automatic regulation, where supported.
  Selecting `Off` ends a running sensor mode and returns the unit to manual
  airflow; it does nothing when no sensor mode is active. While a sensor mode
  is active the unit chooses the airflow itself, so the fans show measured
  values instead of targets. Setting a non-zero fan speed leaves sensor control
  with a balanced airflow; setting one direction to zero selects one-sided
  operation without stopping the whole unit.

Intensive ventilation stays a separate control because it is a temporary
override that does not replace the active quick mode. It is exposed as a
switch, so it can be cancelled before the runtime configured in the Meltem app
has elapsed.

## Installation

### Requirements

- Home Assistant `2025.1` or newer
- a Meltem `M-WRG-GW` gateway
- supported `M-WRG-S` / `M-WRG-II` units already added in the Meltem app
- the gateway connected to the Home Assistant host via USB
  > **Note:** Use a USB cable that is fully wired for data — charge-only cables will not work.

### HACS

1. Open HACS in Home Assistant
2. Open the top-right menu -> `Custom repositories`
3. Add `https://github.com/manndreas/meltem-ha` as type `Integration`
4. Search for `Meltem Modbus`
5. Install the integration
6. Restart Home Assistant

### Manual

Copy `custom_components/meltem_ventilation/` into your Home Assistant config
directory and restart Home Assistant.

## Setup

1. Open `Settings` -> `Devices & Services`
2. Click `Add Integration`
3. Search for `Meltem Modbus`
4. Select the serial port of the `M-WRG-GW` gateway
5. Let the integration read the configured unit list from the gateway
6. Assign the correct profile to each detected unit

Units are listed by their Modbus address and initially named `Unit 1`, `Unit 2`
and so on. Rename them like any other Home Assistant device; the new name is
also shown when you edit the profiles later.

## Supported profiles

- `M-WRG-S`
- `M-WRG-S (-F)`
- `M-WRG-S (-FC)`
- `M-WRG-II`
- `M-WRG-II (-F)`
- `M-WRG-II (-FC)`
- `M-WRG-II (O/VOC-AUL)`

The integration can detect optional sensors such as humidity, CO2 and VOC, but
it cannot yet reliably distinguish `M-WRG-S` from `M-WRG-II` automatically.
During setup, choose the exact profile manually.

## Options

Open the integration options via `Settings` -> `Devices & Services` -> `Meltem Modbus`
-> `Configure`.

- **Change serial connection** — update the serial port or the maximum poll-job
  start rate used by the scheduler. One job can contain several serialized
  Modbus requests; the option is not a wire-level request limit.
- **Change profiles for existing units** — reassign profiles without rescanning
  the gateway
- **Scan for new units** — discover units that were added to the gateway after
  the initial setup

## Diagnostic entities

Some diagnostic entities such as operating hours remain disabled by default.
Firmware and hardware details are shown on the device card instead of as
separate version sensors.

## Troubleshooting

### No units found

Check that:

- the gateway is powered and reachable over USB
- the units were already added in the Meltem app
- the units are fully configured in the `M-WRG-GW` gateway

### Logs

In Home Assistant:

1. Open `Settings`
2. Open `System`
3. Open `Logs`

With the `Terminal & SSH` add-on:

```bash
ha core logs | grep meltem_ventilation
```

## Support

Please include Home Assistant logs and a short description of your unit/gateway
setup when opening an issue.

Additional project docs:

- [CHANGELOG.md](./CHANGELOG.md)
- [CONTRIBUTING.md](./CONTRIBUTING.md)
- [SUPPORT.md](./SUPPORT.md)
- [docs/MELTEM.md](./docs/MELTEM.md)
- [docs/DEVELOPER.md](./docs/DEVELOPER.md)
- [docs/HARDWARE_BACKLOG.md](./docs/HARDWARE_BACKLOG.md)
- [docs/SETTING_RE_BACKLOG.md](./docs/SETTING_RE_BACKLOG.md)
- [docs/TODO.md](./docs/TODO.md)
