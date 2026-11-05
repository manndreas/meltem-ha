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

- **Device support:** Connect Meltem `M-WRG-S` and `M-WRG-II` units through
  their `M-WRG-GW` gateway via USB or a network bridge.
- **Simple setup:** Find units already configured in the Meltem app and manage
  each one as a separate Home Assistant device.
- **Ventilation control:** Adjust supply and extract air separately, use quick
  and sensor-based modes, and switch on intensive ventilation. Supported units
  also allow you to adjust humidity and CO2 thresholds.
- **Air and unit status:** See temperatures, airflow and filter status, plus
  humidity, CO2 and VOC readings where supported by your unit.
- **Maintenance and automations:** Use filter-change triggers and conditions
  to create reminders and automations.
- **Diagnostics:** Check connection and unit health, view optional information
  such as operating hours, and receive repair notices for newly detected units
  or sensors that report no value.

See the [control guide](./docs/CONTROLS.md) and
[diagnostics guide](./docs/DIAGNOSTICS.md) for details.

## Controls

Each unit has separate supply-air and extract-air `fan` entities. Quick mode,
sensor control, and intensive ventilation controls complement the fans. See
the [control guide](./docs/CONTROLS.md) for how these controls interact,
including balanced and one-sided airflow and sensor modes.

## Automations

Use the **Filter change due** trigger and condition to create filter-change
automations. See the [trigger guide](./docs/TRIGGER_FILTER_CHANGE_DUE.md),
[condition guide](./docs/CONDITION_FILTER_CHANGE_DUE.md), and
[ventilation automation example](./docs/AUTOMATION_EXAMPLES.md).

## Installation

### Requirements

- Home Assistant `2026.11.0` or newer
- a Meltem `M-WRG-GW` gateway
- supported `M-WRG-S` / `M-WRG-II` units paired with the gateway using the
  Meltem app
- either the gateway connected to the Home Assistant host via USB, or a
  network bridge that transports raw Modbus RTU frames over TCP
  > **Note:** Use a USB cable that is fully wired for data — charge-only cables will not work.

> **Pairing and internet access:** Pair each unit with the gateway in the Meltem
> app; pairing requires an internet connection. This integration cannot pair
> units. After pairing, it communicates locally with the gateway, so ongoing
> use of the integration does not require internet access or the Meltem cloud.

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

When available, the App setup code helps you match a detected unit to its
label. Choose its profile using the model on the type plate. Units initially
have names such as `Unit 1` and can be renamed in Home Assistant. See the
[setup guide](./docs/SETUP.md) for more on identifying units.

### Units

- **Add unit** on the integration page adds a unit that was paired in the
  Meltem app after the initial setup. Home Assistant shows a repair notice when
  the gateway reports such a unit.
- **Reconfigure** on a unit changes its profile. The integration reloads
  afterwards.
- Deleting a unit removes its device and entities. A later **Scan for new
  units** adds it again if the gateway still reports it.

## Supported profiles

- `M-WRG-S (-F, -FC)`
- `M-WRG-II (-F, -FC, O/VOC-AUL)`

The `M-WRG-II` profiles support both `P` units with a plate heat exchanger
and `E` units with an enthalpy heat exchanger that also recovers moisture.
Choose the profile based on the unit's sensors; no separate `P` or `E`
profile is needed.

The integration can detect optional sensors such as humidity, CO2 and VOC, but
it cannot yet reliably distinguish `M-WRG-S` from `M-WRG-II` automatically.
During setup, choose the exact profile manually.

The integration has only been tested and validated with `M-WRG-II` devices.
Other hardware should work, but has not been verified. Feedback from users with
different hardware is very welcome.

## Options

Open the integration options via `Settings` -> `Devices & Services` -> `Meltem Modbus`
-> `Configure`.

- **Change request rate** — adjust how often the integration queries the
  gateway.
- **Scan for new units** — find changes to units registered with the gateway.
  You are asked before units that are no longer reported are removed.

To change the gateway connection, choose `Reconfigure` in the integration
menu. Existing units and entity IDs remain. See the
[setup guide](./docs/SETUP.md) for details about these options and network
connections.

## Polling and diagnostics

Readings update periodically and may take a little time to appear after
startup. The [diagnostics guide](./docs/DIAGNOSTICS.md) covers refresh times,
connection health, optional sensors, and collecting logs.

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
