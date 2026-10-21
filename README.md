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
- temperature, airflow, filter and operating-hour sensors, plus humidity, CO2
  and VOC sensors depending on the profile
- separate supply air and extract air fan entities per unit
- app-style quick modes, sensor-driven control modes and an intensive
  ventilation switch
- writable humidity and CO2 control thresholds for supported profiles
- gateway fault and filter-change overviews, measured airflow totals, connection
  diagnostics and optional communication-quality sensors
- per-unit reachability, last command status, last successful update and measured
  airflow balance; optional experimental gateway uptime
- automation triggers and conditions for a due filter change
- USB discovery for the Meltem gateway

## Controls

Each unit has separate supply-air and extract-air `fan` entities. Quick mode,
sensor control, and intensive ventilation controls complement the fans. See
the [control guide](./docs/CONTROLS.md) for how these controls interact,
including balanced and one-sided airflow and sensor modes.

## Automations

The integration provides a **Filter change due** trigger for automations. It
fires when a Meltem unit reports that its filter needs replacing. See the
[filter change due trigger guide](./docs/TRIGGER_FILTER_CHANGE_DUE.md) for the
UI steps, trigger options, and a YAML example.

The **Filter change due** condition lets an automation check that state after
another trigger has fired. See the
[filter change due condition guide](./docs/CONDITION_FILTER_CHANGE_DUE.md) for
UI instructions, options, and a YAML example.

See the [automation example](./docs/AUTOMATION_EXAMPLES.md) for a YAML
automation that runs intensive ventilation while a room is occupied.

## Installation

### Requirements

- Home Assistant `2026.10.2` or newer
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

To remove the config entry and uninstall the integration, follow the
[removal instructions](./docs/REMOVE_INTEGRATION.md).

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

The integration has only been tested and validated with `M-WRG-II` devices.
Other hardware should work, but has not been verified. Feedback from users with
different hardware is very welcome.

## Options

Open the integration options via `Settings` -> `Devices & Services` -> `Meltem Modbus`
-> `Configure`.

- **Change request rate** — adjust the shared read-request cap (default
  **4 requests per second**, configurable from **0.5 to 10**). See
  [polling and diagnostics](./docs/DIAGNOSTICS.md) for details.
- **Change profiles for existing units** — reassign profiles without rescanning
  the gateway
- **Scan for new units** — discover units that were added to the gateway after
  the initial setup

To move the gateway to another serial port, choose `Reconfigure` in the menu
of the integration entry. The new port is checked before it is saved.

## Polling and diagnostics

The integration polls the gateway rather than receiving pushed updates.
Values appear progressively at startup, depending on the request rate and
number of configured units. See the
[diagnostics guide](./docs/DIAGNOSTICS.md) for refresh intervals, read health,
availability, and log collection.

The gateway device groups fault and filter-change counts and total measured
supply/extract airflow across configured units. Each unit also exposes its
reachability, last command status, and airflow balance. Communication quality,
the last successful update timestamp, and experimental gateway uptime can be
enabled in the entity settings. Uptime adds optional register reads and is not
yet suitable for restart detection; other new diagnostics reuse existing data.

## Troubleshooting

### No units found

Check that:

- the gateway is powered and reachable over USB
- the units were already added in the Meltem app
- the units are fully configured in the `M-WRG-GW` gateway

### A single unit is unavailable

Check the **Read health** attributes and download diagnostics. See
[unit availability troubleshooting](./docs/DIAGNOSTICS.md#a-single-unit-is-unavailable)
for details.

### Serial port shared with another integration

See the
[serial-port troubleshooting guide](./docs/DIAGNOSTICS.md#serial-port-shared-with-another-integration)
for shared-port settings and timeout behavior.

### Logs

Follow the [log collection steps](./docs/DIAGNOSTICS.md#logs).

## Support

Please include Home Assistant logs and a short description of your unit/gateway
setup when opening an issue; [SUPPORT.md](./SUPPORT.md) lists what helps.

For release notes and development guidance, see [CHANGELOG.md](./CHANGELOG.md)
and [CONTRIBUTING.md](./CONTRIBUTING.md).
