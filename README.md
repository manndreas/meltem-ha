# Meltem Modbus Home Assistant Custom Integration

[![HACS Custom](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://github.com/hacs/integration)
[![CI](https://github.com/manndreas/meltem-ha/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/manndreas/meltem-ha/actions/workflows/ci.yml)
[![GitHub Release](https://img.shields.io/github/v/release/manndreas/meltem-ha)](https://github.com/manndreas/meltem-ha/releases)
[![License](https://img.shields.io/github/license/manndreas/meltem-ha)](https://github.com/manndreas/meltem-ha/blob/main/LICENSE)

**Unofficial** Home Assistant custom integration for Meltem `M-WRG-S` and `M-WRG-II`
ventilation units via the Meltem `M-WRG-GW` gateway and Modbus RTU over USB or
a raw RTU-over-TCP bridge.

Use this integration at your own risk. It is an unofficial project and comes
with no warranty. The authors are not liable for damage to Meltem devices,
gateways, Home Assistant hosts, or other connected equipment.

## Features

- support for Meltem `M-WRG-S` and `M-WRG-II` unit families
- automatic discovery of configured units via the `M-WRG-GW` gateway
- per-unit profile selection during setup
- temperature, airflow, filter and operating-hour sensors, plus humidity, CO2
  and VOC sensors depending on the profile; optional absolute-humidity sensors
  are calculated from the existing temperature and humidity readings
- separate supply air and extract air fan entities per unit
- app-style quick modes, sensor-driven control modes and an intensive
  ventilation switch
- writable humidity and CO2 control thresholds for supported profiles
- optional, read-only absolute-humidity estimates and external-control-input
  settings; these diagnostics are disabled by default and do not write to the unit
- CO2 measurement for extract air on the `-FC` profiles and VOC measurement for
  supply air on the `O/VOC-AUL` profile, reported in ppm and enabled by default.
  The `O/VOC-AUL` profile also offers supply-air CO2 and extract-air VOC, which
  only the Meltem app's register map describes; these two are disabled by
  default until a unit with those sensors confirms them
- gateway connected-device count, measured airflow totals and delta, connection
  diagnostics, and optional RF, uptime, UTC clock, and communication-quality sensors
- per-unit reachability, read health, last successful update, measured airflow
  balance, and optional RF, fan-hour, app-setup-code and external-input diagnostics
- automation triggers and conditions for a due filter change
- each unit as its own entry under the gateway, with **Add unit** and a
  per-unit profile change
- repair notices for units the gateway reports but Home Assistant does not have
  yet, and for sensors of the selected profile that report no value
- USB discovery for the Meltem gateway
- manual setup of raw RTU-over-TCP bridges (not yet tested with a real bridge)

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

- Home Assistant `2026.11.0` or newer
- a Meltem `M-WRG-GW` gateway
- supported `M-WRG-S` / `M-WRG-II` units already added in the Meltem app
- either the gateway connected to the Home Assistant host via USB, or a
  network bridge that transports raw Modbus RTU frames over TCP
  > **Note:** Use a USB cable that is fully wired for data — charge-only cables will not work.

RTU-over-TCP uses raw RTU frames, not Modbus TCP/MBAP. Network connections have
no encryption or authentication: use them only on a trusted local network.
RTU over TCP has so far only been tested against software simulators, not with
a real network bridge and gateway. Reports from users who try it are welcome.

### Marketplace

1. Open `Settings` -> `Marketplace`
2. Select `Add from link` and enter `https://github.com/manndreas/meltem-ha`
3. Connect GitHub when prompted for the custom repository
4. Install `Meltem Modbus` and continue to setup

Updates still require a Home Assistant restart.

### Manual

Copy `custom_components/meltem_ventilation/` into your Home Assistant config
directory and restart Home Assistant.

To remove the config entry and uninstall the integration, follow the
[removal instructions](./docs/REMOVE_INTEGRATION.md).

## Setup

1. Open `Settings` -> `Devices & Services`
2. Click `Add Integration`
3. Search for `Meltem Modbus`
4. Choose USB serial and enter the gateway's serial port, or choose RTU over TCP
   and enter the bridge hostname or IP address and TCP port (default `5020`)
5. Let the integration read the configured unit list from the gateway
6. Assign the correct profile to each detected unit

The profile list shows each unit's App setup code when available. Match it to
the code below the DataMatrix on the unit label to identify which Modbus
address belongs to each physical unit. Profile selection still uses the model
information on the type plate. For known product IDs, the setup preview also
shows a series and product identifier from the app catalog; the older-series
mapping has not yet been verified on hardware. Existing units with a known ID
show the identifier in their device details.

Units are listed by their Modbus address and initially named `Unit 1`, `Unit 2`
and so on. Each unit appears as its own entry under the gateway on the
integration page. Rename them like any other Home Assistant device; the new
name is also shown when you change a profile later.

### Units

- **Add unit** on the integration page adds a unit that was paired in the
  Meltem app after the initial setup. Home Assistant shows a repair notice when
  the gateway reports such a unit.
- **Reconfigure** on a unit changes its profile. The integration reloads
  afterwards.
- Deleting a unit removes its device and entities. A later **Scan for new
  units** adds it again if the gateway still reports it.

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
- **Scan for new units** — read the unit list from the gateway again: new units
  are added and moved units are updated; the profiles of all units can be
  changed in the same step. Units the gateway no longer reports are listed for
  confirmation before they are removed together with their devices

To change the gateway connection or switch between USB serial and RTU over
TCP, choose `Reconfigure` in the menu of the integration entry. The new
connection is checked before it is saved; existing units and entity IDs remain.
Hostnames, including `.local` names, must be resolvable by the Home Assistant
host. Automatic network discovery is not supported.

## Polling and diagnostics

The integration polls the gateway rather than receiving pushed updates.
Values appear progressively at startup, depending on the request rate and
number of configured units. See the
[diagnostics guide](./docs/DIAGNOSTICS.md) for refresh intervals, read health,
availability, and log collection.

The gateway device exposes the connected-device count, Modbus connection status,
and total measured supply, extract, and delta airflow across configured units.
Each unit also exposes its reachability, read health, and airflow balance.
Communication quality, cumulative Modbus counters, RF diagnostics, the last
successful update timestamp, the app setup code, gateway uptime, and the
gateway UTC clock can be enabled in the entity settings.

## Troubleshooting

### No units found

Check that:

- the gateway is powered and reachable over the configured connection
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
