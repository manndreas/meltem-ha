# Meltem Ventilation Home Assistant Custom Integration

[![HACS Custom](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://github.com/hacs/integration)
[![GitHub Release](https://img.shields.io/github/v/release/neon-vector/meltem-ha)](https://github.com/neon-vector/meltem-ha/releases)
[![License](https://img.shields.io/github/license/neon-vector/meltem-ha)](LICENSE)

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
- balanced and unbalanced airflow control
- operating mode selection
- writable humidity and CO2 control thresholds for supported profiles
- USB discovery for the Meltem gateway

## Installation

### Requirements

- a Meltem `M-WRG-GW` gateway
- supported `M-WRG-S` / `M-WRG-II` units already added in the Meltem app
- the gateway connected to the Home Assistant host via USB

### HACS

1. Open HACS in Home Assistant
2. Open the top-right menu -> `Custom repositories`
3. Add `https://github.com/neon-vector/meltem-ha` as type `Integration`
4. Search for `Meltem`
5. Install the integration
6. Restart Home Assistant

### Manual

Copy `custom_components/meltem_ventilation/` into your Home Assistant config
directory and restart Home Assistant.

## Setup

1. Open `Settings` -> `Devices & Services`
2. Click `Add Integration`
3. Search for `Meltem`
4. Select the serial port of the `M-WRG-GW` gateway
5. Let the integration read the configured unit list from the gateway
6. Assign the correct profile to each detected unit

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

Open the integration options via `Settings` -> `Devices & Services` -> `Meltem`
-> `Configure`.

- **Change serial connection** — update the serial port or the maximum request
  rate used by the scheduler
- **Change profiles for existing units** — reassign profiles without rescanning
  the gateway
- **Scan for new units** — discover units that were added to the gateway after
  the initial setup

## Diagnostic entities

Some diagnostic entities such as software version, RF communication status,
fault status and value error status are created but disabled by default. To
enable them, open the entity in Home Assistant and toggle the `Enabled` switch.

## Troubleshooting

### No units found

Check that:

- the gateway is powered and reachable over USB
- the units were already added in the Meltem app
- the units are fully configured in the `M-WRG-GW` gateway

### Values still show up after unplugging the gateway

Home Assistant can continue to show the last cached values for a while when the
gateway disappears. That does not mean the gateway is still connected. New
updates and writes will fail until the gateway is available again.

### Temperature names look odd

The `M-WRG-GW` gateway appears to expose the documented `41000` and `41004`
temperature values reversed on some setups. The integration compensates for
this gateway quirk so the Home Assistant entity names match the observed
airflow positions.

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
