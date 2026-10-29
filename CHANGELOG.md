# Changelog

## 4.7.0

### Breaking Changes (4.7.0)

- **Breaking:** Requires Home Assistant `2026.11.0` or newer.

### Improvements (4.7.0)

- Reduce repeated requests when a unit returns a temporary error. Normal polling
  resumes when the affected readings succeed again.
- Keep diagnostic counts and gateway availability accurate when some readings
  are delayed or skipped.
- After a unit does not answer or a read is cancelled, wait five seconds before
  reading it again. The unit remains unavailable until a read succeeds. This
  reduces, but does not eliminate, the risk of delayed replies being mistaken
  for current readings.

### Fixes (4.7.0)

- Improve recovery from a disconnected USB gateway by allowing more time for
  the connection to return before retrying.
- After an interrupted airflow change, keep the target unknown and prevent
  changes that depend on the previous airflow until the unit reports its state
  again.
- Hide app setup codes in diagnostics downloads, including codes in error
  messages and unit details.
- Close the gateway connection reliably when setup is interrupted or unloading
  encounters an error.

### Other Changes (4.7.0)

- List the integration in the fans and ventilation category in the Home
  Assistant community catalog.

## 4.6.1

### New Features (4.6.1)

- Add optional gateway sensors counting requests, communication failures,
  reconnections, and read and command problems. They are disabled by default and
  do not generate additional gateway requests.
- Include detailed counts by unit and command in the diagnostics download.
  Counts survive restarts, start fresh for a new integration entry, and are
  deleted when that entry is removed.

### Fixes (4.6.1)

- Keep successful unit updates when optional gateway diagnostics return an
  invalid value, and improve recovery from invalid communication responses.

## 4.6.0

### Improvements (4.6.0)

- Show each unit's app setup code during setup and rescans, making it easier to
  match the listed units to their physical labels.
- Improve how Home Assistant identifies units without losing existing details.

## 4.5.1

### Improvements (4.5.1)

- Display gateway uptime in days with one decimal place instead of raw seconds.
- Show RF load with two decimal places, and mean response time and successful
  Modbus requests with no decimal places.

## 4.5.0

### Requirements (4.5.0)

- Requires Home Assistant `2026.10.2` and HACS `2.0.5` or newer.

### Breaking Changes (4.5.0)

- Remove the gateway available/unavailable unit counts, device-error
  and due-filter counts, consecutive timeout count, stale read-group count, and
  link-reconnection count, as well as each unit's **Last command status**.
  Replace the last-response-age entity with **Last Modbus response**. Update
  automations and dashboards that use the removed entities; their registry
  entries are cleaned up automatically.
- Rename gateway **Reachable** to **Modbus connection** and detect a connection
  outage after 30 seconds instead of 120 seconds.
- Rename the optional success-rate sensor to **Successful Modbus requests
  (5 min)**.

### New Features (4.5.0)

- Add **Connected device count**, enabled by default. It counts devices
  registered with the gateway, not necessarily devices that are currently
  reachable or configured in Home Assistant.
- Make gateway uptime a regular, optional diagnostic.
- Add optional diagnostics for each unit's last radio contact, signal strength,
  fan operating hours, radio fault status, and value errors. Fan hours are
  separate from device operating hours; the signal strength unit is provisional.
  These values are read during normal polling even when their entities are
  disabled.
- Add optional gateway diagnostics for radio messages and load during the
  current and previous hour.
- Add the enabled-by-default gateway **Total airflow delta**, calculated as
  supply minus extract airflow. If readings are missing, the delta stays unknown
  and identifies the affected units.
- Add an optional **App setup code** sensor for manual app pairing and an
  optional gateway **UTC clock** sensor.

### Improvements (4.5.0)

- Failed or unconfirmed commands still appear in Read health diagnostics.
- Prevent overlapping gateway requests when reading its identity at startup.
- Add the gateway MAC address to its Home Assistant device information and
  update diagnostic labels, icons, and documentation.

## 4.4.0

### New Features (4.4.0)

- Add gateway diagnostics for connection problems, unit availability, device
  errors, and filters due for replacement.
- Show total measured supply and extract airflow. If readings are missing, the
  totals stay unknown and identify the affected units.
- Add optional gateway response-time, request-success-rate, and experimental
  uptime sensors. Uptime behavior after a restart still needs hardware testing.
- Add per-unit diagnostics for availability, the last successful update,
  command status, and the difference between supply and extract airflow.

### Documentation and Translations (4.4.0)

- Add English and German labels and documentation for the new diagnostics.

Some of these diagnostics were removed or renamed in 4.5.0; see its migration
notes above.

## 4.3.1

### Documentation (4.3.1)

- Simplify the README and move detailed control, polling, and availability
  guidance into focused user documentation.

### New Features (4.3.1)

- Add automation examples for scheduled quick modes and Read health alerts.

## 4.3.0

### Fixes (4.3.0)

- Keep other temperature and air-quality readings up to date when a humidity,
  CO2, or VOC sensor reports an invalid value; the affected sensor keeps its
  last valid reading.
- Mark units that repeatedly fail to provide readings unavailable and show the
  latest error in diagnostics.
- Stop background polling cleanly if the integration fails to finish setup.
- Avoid reporting a fan-command fallback when the command was not accepted.
- Preserve distinct supply and extract readings when they differ and no shared
  target is known.
- Capture connection counters before the diagnostics download probes the gateway,
  so the probe does not overwrite the exported counters.
- Back off gateway polling after timeouts or lost connections, but not when a
  unit responds with an error.

### Improvements (4.3.0)

- Improve airflow, quick-mode, and intensive-ventilation feedback after a
  command, including when a confirmation is delayed.
- Reduce unnecessary requests for sensors a unit does not have and improve
  recovery from connection and setup failures.
- Show clearer, translated messages for gateway connection and command errors.

## 4.2.0

### Fixes (4.2.0)

- Improve confirmation of balanced airflow changes. Changes made during
  intensive ventilation can be confirmed after it ends, within ten minutes of
  the command.
- Report unit outages and recoveries more consistently and reduce repeated
  messages in the logs.

## 4.1.1

### Improvements (4.1.1)

- Preserve the configured maximum request rate when moving it into integration
  options.
- Log when a unit becomes unavailable or recovers, with fewer repetitive
  connection messages.

### Fixes (4.1.1)

- Report invalid humidity or CO2 control ranges as input validation errors.

### Documentation and Translations (4.1.1)

- Add English and German descriptions to setup and options fields, and clarify
  how the shared request-rate limit works.
- Expand the documentation for controls, updates, automations, filter changes,
  and removing the integration.

## 4.1.0

### Improvements (4.1.0)

- Improve operating-mode and airflow feedback, including on units that do not
  report their settings until after a change. The change still needs testing
  with M-WRG-S hardware.
- During intensive ventilation, the operating mode, airflow targets, and quick
  mode keep their last known values. After a restart during intensive
  ventilation, they remain unknown until it ends.
- Check airflow and mode changes made during intensive ventilation after the
  override ends, instead of incorrectly reporting a mismatch.
- Retry readings that fail at startup sooner.

### Fixes (4.1.0)

- Clear previous read-health errors when operating-mode status reads succeed.

## 4.0.1

### Improvements (4.0.1)

- Apply the configured request-rate limit across all units and read retries.
  Changes to the limit take effect immediately.
- Show available startup data progressively, starting with airflow, instead of
  waiting for every reading. Values not yet read remain unknown.
- Improve read-health reporting during startup and after recovery.

### Fixes (4.0.1)

- Clear an outdated intensive-ventilation read error when the shorter
  operating-mode read succeeds but intensive status is unavailable.

## 4.0.0

### Requirements (4.0.0)

- Requires Home Assistant `2026.10` or newer.

### New Features (4.0.0)

- Add filter-change triggers and conditions for device automations.
- Link unit devices to the gateway in Home Assistant and remove devices that are
  no longer found during a rescan.
- Gateway communication now uses Home Assistant's built-in Modbus integration;
  existing entities are unchanged.
- Change the serial port using **Reconfigure** on the integration entry. The
  options still contain the request rate, unit profiles, and rescan.
- Report conflicting serial-port settings clearly during setup, and check that
  the gateway responds before completing setup. Home Assistant's `modbus.*`
  actions are now available.
- Include link reconnection counts in the diagnostics download.

### Improvements (4.0.0)

- Improve recovery when the gateway or a unit stops responding. A silent unit
  is less likely to interrupt other units, and an unavailable gateway is retried
  at a reduced rate.
- Keep airflow, operating-mode, quick-mode, and intensive controls available
  when the unit is reachable but individual status readings are outdated.
- Keep the last valid sensor reading when the device reports an invalid value,
  while showing the reading problem in diagnostics.
- Avoid polling readings outside the selected device profile on installations
  upgraded from older releases.
- Improve confirmation and expiry of airflow, quick-mode, and intensive
  ventilation changes. A failed mode read no longer clears the last known supply
  target in unbalanced mode.
- Hide the serial port path from scheduler errors on the system information page.
- Show outdated readings and command problems in data-health diagnostics, and
  outdated readings in System Health. Hide the serial port path from errors in
  diagnostic downloads.

### Fixes (4.0.0)

- Keep sensor-controlled operation when turning on an already running fan
  without specifying a speed, and restore the last speed when restarting it.
- Preserve custom settings for the gateway port sensor when the integration
  reloads.
- Avoid reporting a command problem when a unit cannot confirm the intensive
  ventilation status, and detect an already configured gateway before scanning.
- Reject a humidity or CO2 minimum above its maximum before sending it to the
  unit, and fix duplicate default names for units added by a rescan.
- Preserve the first airflow target when changing both directions in quick
  succession, and avoid false warnings for commands replaced by newer ones.
- Show operating-mode and intensive-ventilation states as unknown when their
  readings are outdated, while keeping pending changes visible.
- Fix request-rate limits below one second, reloads after writes, and quick-mode
  feedback after a restart during intensive ventilation.

## 3.1.0

### New Features (3.1.0)

- Add optional diagnostic sensors for the gateway port and unit addresses.

### Fixes (3.1.0)

- Prevent an error after repeated failures to read optional device information.

## 3.0.2

### Improvements (3.0.2)

- Update the airflow and operating-hours icons.

## 3.0.1

### Compatibility (3.0.1)

- Allow an earlier version of the Modbus library to be installed.

## 3.0.0

### Migration

Every airflow control entity was replaced. Automations referencing the old
entities stop working and have to be updated. The examples below use English
names; actual entity IDs may differ, especially after renaming or upgrading:

| Removed | Replacement |
| --- | --- |
| `number.<unit>_common_airflow` (2.x) / `number.<unit>_airflow` (1.x) | `fan.<unit>_supply_air` **and** `fan.<unit>_extract_air` |
| `number.<unit>_supply_air_flow` (2.x) / `number.<unit>_supply_setpoint` (1.x) | `fan.<unit>_supply_air` |
| `number.<unit>_extract_air_flow` (2.x) / `number.<unit>_extract_setpoint` (1.x) | `fan.<unit>_extract_air` |
| `button.<unit>_start_intensive_ventilation` | `switch.<unit>_intensive_ventilation` |
| `binary_sensor.<unit>_intensive_ventilation_active` | the same switch |
| `select.<unit>_quick_mode` options `Extract` / `Supply` | set one fan to `0` |
| `select.<unit>_control_mode` options `Off` / `Manual` / `Unbalanced` | the fan entities |

`select.<unit>_control_mode` is now named `Sensor control` and keeps only the
sensor-driven modes. Selecting its `Off` option ends a running sensor mode and
returns the unit to manual airflow; it does not switch the unit off, and it does
nothing when no sensor mode is running.

All removed entities are deleted from the entity registry automatically on the
first start after the update.

### New Features (3.0.0)

- Select **Individual** in quick mode to leave a running quick mode.

### Breaking Changes (3.0.0)

- Requires Home Assistant `2025.1` and `pymodbus` `3.14` or newer.
- Control airflow with separate **Supply air** and **Extract air** fans. Equal
  settings run the unit balanced; different settings run it unbalanced. Set
  both to zero to switch the unit off. Turning it back on starts both directions,
  as does the first fan adjustment after a sensor-driven mode.
- **Quick mode** no longer offers `Extract` and `Supply`; set one fan to zero
  instead. When such a mode is selected in the Meltem app, quick mode shows
  `Individual` and the fans show the airflow.
- **Sensor control** offers sensor-driven modes and `Off`; units without a
  humidity or CO2 sensor no longer have this entity. Its `Off` option ends
  sensor control without switching the unit off.
- Intensive ventilation is now a switch, so it can also be stopped. The old
  button and status sensor are removed automatically.
- Units without humidity or CO2 sensors no longer offer outdoor, extract, or
  supply air temperature sensors; they retain the exhaust air temperature.

### Fixes (3.0.0)

- Fix reloads leaving the serial port locked, improve recovery from connection
  losses, and keep the options dialog usable after a failed setup.
- Keep unit entities unavailable until their first successful reading, rather
  than accepting commands with unknown values.
- Fix fan and quick-mode feedback, including readings above 100%, unknown fans
  appearing off, and incorrect targets in unbalanced mode.
- Fix switching one fan off during sensor control, starting intensive
  ventilation, and immediately clearing a quick mode.
- Make humidity and CO2 threshold controls available with the correct ranges
  and steps, including the full 40-80% humidity starting-point range. Successful
  changes remain visible even if immediate confirmation fails.
- Repair missing unit information and remove unsupported entities when a unit's
  profile changes.

### Improvements (3.0.0)

- Identify units in setup and options by address, hardware details, and their
  Home Assistant device name. Rename units on their device pages.
- Allow devices no longer found during a rescan to be removed from their device
  pages. Also clean up the remaining registry entries for sensors removed in
  2.0.0.
- Show fan and intensive-ventilation changes immediately while they await
  confirmation. Fans show measured airflow during humidity, CO2, or automatic
  control.
- Refuse a fan adjustment when the opposite airflow is unknown, because both
  directions must be sent together.
- Mark an unreachable unit unavailable while other units continue working. If
  the gateway cannot be reached at setup, Home Assistant retries automatically.
- Allow diagnostics downloads after an incomplete setup, and avoid an extra
  gateway request when viewing system health.
- Avoid adding the same gateway twice through automatic and manual discovery.
- Include English translations for the integration's entities and setup.

## 2.0.0

### New Features (2.0.0)

- Add quick modes for low, medium, high, supply-only, and extract-only
  ventilation.
- Add a button to start intensive ventilation and a sensor showing whether it
  is active.

### Breaking Changes (2.0.0)

- Remove two diagnostic status sensors whose meaning was not yet clear.

### Improvements (2.0.0)

- Improve quick-mode feedback after changes. Simplify airflow controls, remove
  redundant sensors, and improve labels and icons. The shared airflow slider
  remains the main control for balanced use.
- Keep the last valid values during short communication interruptions instead
  of immediately marking entities unavailable.
- Show firmware and hardware information on the device card and remove the
  separate software-version sensor.
- Show the configured device name when editing a unit's profile and clarify
  diagnostic names.
- Rename the integration to **Meltem Modbus**.
- Hide the serial port path in Home Assistant's System Health information.

Airflow and intensive-ventilation controls changed again in 3.0.0; see its
migration notes above.

## 1.1.0

### Improvements (1.1.0)

- Reduce gateway traffic by combining sensor readings and avoid unnecessary
  Home Assistant updates when values have not changed.

## 1.0.0 - Initial version

### Features (1.0.0)

- Initial release of the Meltem Home Assistant integration, with USB and
  gateway-backed discovery for Meltem `M-WRG-GW`.
- Support for `M-WRG-S` and `M-WRG-II` profile variants.
- Sensors, airflow controls, and operating modes including humidity and CO2
  control on supported units.
- Config and options flows, diagnostics, and System Health.
- Polling and write behavior tuned against real gateway hardware.
