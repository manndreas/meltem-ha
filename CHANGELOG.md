# Changelog

## 3.0.2

- Changed some icons.

## 3.0.1

- Changed python version requirement to be compatible with Home Assistant.

## 3.0.0

### Migrating your automations

Every airflow control entity was replaced. Automations referencing the old
entities stop working and have to be updated:

| Removed | Replacement |
| --- | --- |
| `fan.<unit>_ventilation_level` | `fan.<unit>_supply_air` **and** `fan.<unit>_extract_air` |
| `number.<unit>_common_airflow` (1.x) | same two fan entities |
| `number.<unit>_supply_air_flow` (1.x) | `fan.<unit>_supply_air` |
| `number.<unit>_extract_air_flow` (1.x) | `fan.<unit>_extract_air` |
| `button.<unit>_start_intensive_ventilation` | `switch.<unit>_intensive_ventilation` |
| `binary_sensor.<unit>_intensive_ventilation_active` | the same switch |
| `select.<unit>_quick_mode` options `Extract` / `Supply` | set one fan to `0` |
| `select.<unit>_control_mode` options `Off` / `Manual` / `Unbalanced` | the fan entities |

`select.<unit>_control_mode` is now named `Sensor control` and keeps only the
sensor-driven modes. Selecting its `Off` option ends a running sensor mode and
returns the unit to manual airflow; it does not switch the unit off, and it
does nothing when no sensor mode is running.

All removed entities are deleted from the entity registry automatically on the
first start after the update.

### Breaking changes

- The minimum supported Home Assistant version is now `2025.1`, and `pymodbus`
  `3.13` or newer is required.
- Airflow control now uses two dedicated `fan` entities per unit, `Supply air`
  and `Extract air`, instead of a single shared ventilation slider.
  - The previous combined fan entity is removed. Its registry entry is cleaned
    up automatically on upgrade.
  - Setting both directions to the same value runs the unit balanced, different
    values switch it to unbalanced operation.
  - Turning both directions to zero switches the unit off. Turning a stopped
    unit back on starts both directions rather than a single one, and so does
    the first change after a sensor-driven mode.
- `Quick mode` no longer offers `Extract` and `Supply`. Both states are now
  expressed directly by setting one of the two fans to zero. If the unit reports
  one of them (for example after using the Meltem app), the select shows
  `Individual`; the fan entities show the actual airflow.
- The control-mode select is now `Sensor control` and only offers the
  sensor-driven modes plus `Off`. Manual airflow, unbalanced operation and
  switching the unit off are reachable through the fan entities.
  - Units without a humidity or CO2 sensor no longer get this entity at all.
  - Selecting `Off` only ends a running sensor mode. If the unit already runs
    on a manually set airflow, the selection does nothing, so an unbalanced
    setup is no longer collapsed onto a single value.
- Intensive ventilation is now a `switch` instead of a button plus a separate
  binary sensor, so a running override can also be cancelled again.
  - The old button and binary sensor are removed and cleaned up automatically.

### Fixes

- Unloading or reloading the integration now waits for an active serial
  operation before closing the client, so a background read can no longer
  reconnect afterwards and leave the port locked.
- Missing or incomplete stored unit metadata is repaired on startup. The
  best-effort sensor probe is merged with profile-derived temperatures and
  thresholds, including for entries affected by earlier probe-only metadata.
- A setup failure after coordinator creation no longer leaves stale runtime
  data behind for the options flow to mistake for a running integration.
- Room entities stay unavailable until their first successful state read
  instead of briefly accepting commands with unknown device values at startup.
- Humidity and CO2 control values sent through services are now snapped to the
  manufacturer-defined register step before they are written and published.
- Changing a unit to a profile with fewer capabilities now removes entity
  registry entries that the new profile no longer supports, instead of leaving
  them permanently unavailable.
- System Health and diagnostics now count only units with at least one live
  state value, not placeholder states created after a failed read.
- The options dialog no longer crashes when the entry never finished setting up.
  Changing the profiles, rescanning or adjusting the request rate raised an
  error on exactly the screen you need to fix an unreachable gateway.
- Switching a single fan off while a sensor-driven mode is running no longer
  stops the whole unit.
- `pymodbus` is now required in a version that actually provides the API the
  integration calls. An older but still matching version already present in the
  Home Assistant environment made every register read and write fail.
- Resolving the serial port performed blocking filesystem access inside the
  Home Assistant event loop, on every setup and every options change.
- A lost serial connection in the middle of a poll is no longer swallowed.
  Optional register reads still hide ordinary Modbus errors, but a dead
  connection now reaches the coordinator, which marks the units unavailable and
  backs off instead of reporting unchanged values.
- After a reconnect, the remaining reads of the same poll no longer run against
  the closed connection and silently keep the previous values.
- A read or write that received no answer at all now reconnects before
  retrying, instead of repeating the attempt on the same dead connection.
- A fan could report more than 100 %, for example during intensive ventilation
  or after selecting an `M-WRG-S` profile for an `M-WRG-II` unit.
- A fan without data reports `unknown` instead of `off`.
- Changing one fan while a sensor-driven mode is active now writes a balanced
  airflow. Both fans only report fluctuating measurements in that state, so the
  opposite direction was previously pinned to a sampled value.
- Quick-mode codes are no longer decoded as an unbalanced airflow target. A
  unit running `Low`, `Medium`, `High` or intensive ventilation in unbalanced
  mode reported full airflow instead of no target.
- Saving the connection options no longer treats an unchanged port as a port
  change when the stored path predates a `/dev/serial/by-id` symlink, which
  caused an unnecessary reload every time.
- Turning on intensive ventilation no longer fails because the switch used an
  obsolete Modbus-client call signature.
- Clearing a quick mode immediately after selecting it now still sends the
  manual-mode write, even when the gateway readback has not caught up yet.
- Humidity and CO2 setting ranges now match the manufacturer register table.
  This notably exposes the full `40..80%` humidity starting-point range in
  single-percent steps and prevents out-of-range backend writes.
- Humidity and CO2 settings are published optimistically after a successful
  write and stay visible if the immediate confirmation read fails.
- Changing or normalizing the serial port now keeps the config-entry unique ID
  synchronized, so later USB discovery can still match the existing entry.
- The humidity and CO2 threshold numbers were never created: their entity
  description did not derive from `NumberEntityDescription`, so Home Assistant
  rejected them with an `AttributeError`.
- Units without humidity or CO2 sensors no longer offer outdoor, extract and
  supply air temperature sensors. Per the manufacturer sensor matrix these
  units only have an exhaust air temperature sensor.

### Other changes

- Setup and the options flow now identify each unit by its Modbus address. The
  profile field labels are translated and no longer change when the hardware
  preview changes.
- The profile steps list every detected unit with its hardware details and, if
  you renamed the device in Home Assistant, with that name. Units are named in
  Home Assistant like any other device; there is no separate name field.
- Devices of units that a rescan no longer finds can be deleted from the device
  page instead of lingering as unavailable.
- The intensive switch shows a pending change immediately instead of jumping
  back until the gateway confirms it.
- The scheduler now sleeps until the next poll job is due instead of waking up
  on every request-rate tick.
- The sensors removed in 2.0.0 (`average_air_flow`, `current_level` and the
  separate `software_version` diagnostic) are now also deleted from the entity
  registry automatically. Previously they lingered as unavailable entities.
- Setup now fails with a retryable error when the gateway cannot be reached,
  so Home Assistant retries instead of loading an entry without data.
- Entities of a single unreachable unit become unavailable instead of showing
  frozen values while the other units keep working. This now also covers units
  that stay silent while the gateway itself still answers.
- Polling backs off progressively when the gateway stays unreachable.
- Pending airflow writes are shown optimistically and shared between both fans,
  so adjusting them in quick succession no longer reverts a value.
- During humidity, CO2 or automatic control the fans now show the measured
  airflow. Previously the mode selector was misread as a ventilation level and
  displayed a fixed, plausible-looking value.
- Changing a fan is refused while the airflow of the opposite direction is
  unknown, because every write sends both directions at once.
- The `pyserial` dependency is now requested explicitly, which is required for
  the serial gateway connection.

## 2.0.0

- Reworked quick-mode handling to better match real device behavior.
  - `Intensive ventilation` is now a separate action button instead of a normal quick-mode entry.
  - Added `Intensive ventilation active` as a dedicated status entity.
  - Improved quick-mode readback, fallback handling, and post-write refreshes.
- Simplified and cleaned up the airflow UI.
  - Removed redundant derived airflow sensors.
  - Kept the shared airflow slider as the main control for the common balanced case.
  - Improved labels, icons, and quick-mode naming for better day-to-day usability.
- Improved stability with real hardware.
  - Short transient communication failures now keep the last valid state instead of immediately flipping everything to unavailable.
  - Added more defensive handling when optional mode registers are only partially readable.
- Improved device information.
  - Firmware and hardware information are now shown more cleanly on the device card.
  - Removed the separate software-version sensor after moving that information into device info.
- Cleaned up diagnostics and terminology.
  - Removed undocumented diagnostic flags that were not meaningfully interpretable.
  - Clarified user-facing names for modes, airflow values, and error state.
- Improved setup and options UX.
  - The profile-editing flow now shows the configured room/device name so existing units are easier to identify.
- Expanded reverse-engineering notes, helper tools, and automated test coverage.

## 1.1.0

- Some minor resource optimizations

## 1.0.0 - Initial version

- Initial release of the Meltem Home Assistant integration
- USB and gateway-backed discovery for Meltem `M-WRG-GW`
- Support for `M-WRG-S` and `M-WRG-II` profile variants
- Sensors, binary sensors, airflow controls, operation mode selection, and supported humidity/CO2 settings
- Config flow, options flow, diagnostics, and system health support
- Polling and write behavior tuned against real gateway hardware
