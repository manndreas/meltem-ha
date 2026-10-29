# Changelog

## 4.7.0

- **Breaking:** Raise the minimum supported Home Assistant version to `2026.11.0`
  and use its dataclass-based `DeviceInfo` and device registry API.
- Add the fans and ventilation integration category for the Home Assistant
  community catalog.

- Back off repeated Modbus `0x05` responses per unit and register block to
  reduce redundant polling while a device recovers, then resume normal polling
  after a successful read.
- Keep cached values and read health unchanged while a block is backed off, so
  skipped polls do not inflate failed read-group diagnostics.
- Count timeouts from attempted register groups even when other groups are
  paused, and preserve gateway backoff when a whole polling job is skipped.
- Keep the gateway reachable when a read job combines responses with a timeout,
  and keep a room reachable when the job still yields usable values, including
  partially paused groups.

## 4.6.1

- Add persistent cumulative diagnostic counters for Modbus request attempts and
  failures, link recycles, failed read groups, and problematic write commands.
  The new gateway sensors are disabled by default and do not add Modbus reads.
- Include period metadata and per-category, per-room, and per-command counter
  breakdowns in the diagnostics download. Counters survive integration and Home
  Assistant restarts; a new config entry starts a new period, and deleting the
  entry removes its stored counters.

## 4.6.0

- Show each unit's App setup code in the initial setup and rescan profile lists
  so users can match Modbus addresses to the physical unit labels.
- Store product ID and App setup code as structured room identity fields, use
  the stable R-code as an additional Home Assistant device identifier, and
  migrate existing config entries without losing their known values.

## 4.5.1

- Display gateway uptime in days with one decimal place instead of raw seconds.
- Show RF load with two decimal places, and mean response time and successful
  Modbus requests with no decimal places.

## 4.5.0

- Raise the minimum supported Home Assistant version to `2026.10.2` and align
  the test harness and Modbus dependency pins with that release.
- Raise the minimum supported HACS version to `2.0.5`.
- **Breaking:** Remove the gateway available/unavailable unit counts, device-error
  and due-filter counts, consecutive timeout count, stale read-group count, and
  link-reconnection count. Replace the last-response-age entity with a new
  **Last Modbus response** timestamp entity. Update automations and dashboards
  that referenced these removed entities; their registry entries are cleaned up.
- Remove the per-unit **Last command status** entity. Failed or unconfirmed
  writes remain part of the internal confirmation logic and Read health diagnostics.
- Rename gateway **Reachable** to **Modbus connection** and detect an unanswered
  link after 30 seconds instead of 120 seconds. The optional response timestamp
  stays fixed during an outage rather than counting elapsed seconds.
- Add **Connected device count**, enabled by default, using gateway register
  `43901`. It counts registered gateway devices, not currently reachable RF
  devices or configured Home Assistant units. Reads stop when disabled.
- Rename the optional success-rate diagnostic to **Successful Modbus requests
  (5 min)** to make its transport scope and rolling window explicit.
- Register the gateway MAC address as a Home Assistant device connection.
  This best-effort background read does not block integration setup.
- Decode gateway uptime as low-word-first 32-bit seconds and remove its
  experimental label. The sensor remains disabled by default.
- Add disabled-by-default per-unit RF last-seen age, signed RF RSSI, and fan
  operating hours. The provisional RSSI unit is dBm; fan operating hours are
  separate from the existing device operating-hours counter.
- Add disabled-by-default per-unit **RF fault status** (`40103`) and **Value
  error** (`40104`) diagnostics with independent read-health groups. Unknown
  status values are not reported as healthy or faulty.
- Add optional gateway RF sent-message counts (`42100/42101`) and RF load
  percentages (`42102..42105`) for the current and previous hour. All four
  entities share one rate-limited read while any is enabled; percentages use
  the upstream scale without local rescaling.
- Add the enabled-by-default gateway **Total airflow delta**, calculated as
  total supply minus total extract airflow. Incomplete totals leave the delta
  unknown and identify all affected units.
- Add a disabled-by-default per-unit **App setup code** diagnostic. When enabled,
  it reads `40000..40001` once to expose the DataMatrix `R` code for manual app
  pairing; it adds no recurring polling load.
- Add an optional gateway **UTC clock** timestamp with independent, rate-limited
  reads. Stale clock and connected-device readings become unavailable without
  affecting room entities.
- Update English/German labels, icons, documentation, and regression tests for
  the gateway and unit diagnostics.

## 4.4.0

- Add gateway reachability, last-response age, timeout and link-recovery
  counters, available/unavailable unit counts, and stale read-group diagnostics.
- Add gateway error and due-filter counts with affected-unit attributes, plus
  measured supply/extract airflow totals. Incomplete totals stay unknown and
  identify their missing contributors rather than silently reporting partial sums.
- Add optional mean response time and request success rate over a rolling
  five-minute window, counting retries individually without extra bus requests.
- Add per-unit reachability, last successful update, latest command status and
  measured supply-minus-extract airflow balance. Local diagnostic timers keep
  outages and command timeouts visible without additional polling.
- Add disabled-by-default experimental gateway uptime via a single-register
  read every 60 seconds while enabled. Stale uptime becomes unavailable;
  restart and counter-wrap behavior still need hardware verification.
- Add English/German labels, status translations, icons, and documentation for
  the gateway overviews and device diagnostics, and preserve their registry
  settings across integration reloads.

## 4.3.1

- Simplify the README and move detailed control, polling, and availability
  guidance into focused user documentation.
- Add automation examples for scheduled quick modes and Read health alerts.

## 4.3.0

- Keep the other temperature and air-quality values of a unit available when
  one sensor reports a hardware sentinel such as `239` or `32767`: only that
  value keeps its last reading instead of failing the whole read group.
- Start the gateway polling backoff only after timeouts or lost connections,
  not when a unit answers a read with a Modbus exception response.
- Record the fallback of a fan command only after the gateway accepted the
  write, so a failed write leaves no `last_write_fallback` or log warning behind.
- Settle optimistic airflow, quick-mode, and intensive values only against a
  read that succeeded after the write, and keep the exact balanced airflow
  target across coarse status polls.
- Count a read job whose groups all failed without raising as a failed job, so
  units that stay silent or refuse reads become unavailable and show their last
  job error in diagnostics.
- Read only the registers a unit's profile has, so humidity-only units no
  longer read CO2 and VOC registers.
- Shut the coordinator down when a platform fails to set up.

## 4.2.0

- Confirm balanced airflow writes with a single read of the set register
  `41121`, because the status value `41101` reports balanced targets as
  `200 + airflow / 10` rather than the written raw value.
- Read `41121` only while an airflow write is at most 10 minutes old and still
  open, skip it during intensive ventilation, treat a quick-mode code there as
  no airflow target instead of an error, and warn once per failing streak
  instead of on every airflow poll.
- Judge a balanced airflow write made during intensive ventilation once the
  override has ended.
- Log a unit becoming unavailable and its recovery from the poll results only,
  so diagnostics and system health no longer trigger these messages and a
  silent unit no longer alternates between them; log single failed read jobs at
  debug level.
- Return to Home Assistant's standard coordinator entity with plain entity
  properties instead of the hand-invalidated cached properties from 4.1.1.
- Lint the Markdown files with Markdownlint and type-check the integration,
  tests, and tools with Pyright in CI.

## 4.1.1

- Migrate the configured maximum request rate from entry data to options while
  preserving the existing value.
- Log when a room becomes unavailable and when it recovers, while reducing
  repetitive gateway-backoff log messages.
- Report an invalid humidity or CO2 control range as a service validation
  error instead of a generic Home Assistant error.
- Add translated entity icons and English/German entity and config-flow
  strings.
- Document data updates, automation examples, the filter-change trigger and
  condition, and integration removal; move developer documentation under
  `docs/dev/`.
- Clarify fan feedback, quick-mode state, and unavailable-unit behavior in the
  README; clean up Markdown formatting throughout the documentation.
- Enable strict type checking, resolve Pylance diagnostics across the
  integration, tests, and tools, and use a runtime-data-typed config entry.

## 4.1.0

- Read current mode and airflow targets from the read-only status block
  `41100..41102` for all supported M-WRG-S and M-WRG-II profiles; stop polling
  the write-side mode registers. The M-WRG-S path follows the same map by
  assumption and still needs live hardware validation.
- Remove the legacy multi-register and single-register mode-read fallbacks.
- The data-read status no longer stays in the problem state on units that
  refuse the write-side mode registers until a first write.
- While intensive ventilation runs, the status block does not report the base
  mode. The operating mode, airflow targets, and quick mode keep their last
  known values; after a restart during intensive ventilation they stay unknown
  until it ends.
- An airflow or operating-mode change during intensive ventilation is recorded
  as `unverifiable` instead of a mismatch and no longer flags the data-read
  status; the first readback after intensive ventilation ends judges it.
- Read groups that failed at startup are retried after a minute, then at
  growing intervals, instead of only after their regular interval of up to an
  hour.
- Include the status block and its single registers in the gateway profiling
  tools, and document why earlier broad scans did not identify its meaning.

## 4.0.1

- Enforce the configured maximum request rate across individual Modbus read
  requests, retries, and all units sharing the gateway connection. Rate changes
  apply immediately; write-sequence timing is unchanged.
- Publish startup data progressively, reading airflow before slower groups and
  continuing normal polling while the remaining groups are initialized.
- Keep unread groups unknown during startup and preserve failed-read health
  until that group succeeds or reaches the existing failure threshold.
- Clear outdated health for the intensive group when the short mode-register
  fallback makes that read unavailable, so recovered data no longer leaves the
  data-read status in the problem state.

## 4.0.0

- Requires Home Assistant `2026.10` or newer. The integration now uses the
  serial connection of Home Assistant's built-in Modbus integration instead of
  its own `pymodbus` client. Entities are unchanged.
- The serial port is now changed with `Reconfigure` on the integration entry,
  which checks the new port first. The options keep the request rate, the
  unit profiles, and the rescan.
- All gateway communication is asynchronous and no longer occupies Home
  Assistant's executor threads.
- A serial port that another integration already uses with different settings
  is reported as a setup error instead of fighting over the port lock, and the
  setup dialog names that cause instead of a generic connection error.
- Every request is retried at most once, and plain Modbus exception responses
  from a unit are not retried at all. The link is only reopened after
  three timeouts in a row while no unit answered for 10 seconds, so a silent
  unit no longer resets the connection for all others.
- A unit that does not answer at all ends its poll job after the first
  unanswered block instead of timing out on every block; this also applies to
  the mode registers and to the setup probe.
- Setup now checks the gateway by reading its unit count.
- The system information page no longer shows the serial port path in the
  last scheduler error.
- A minimum level above the maximum level of the humidity or CO2 control is
  refused before it reaches the unit.
- Units added by a rescan no longer get the default name of a unit that is
  kept.
- A unit that falls silent in the middle of a poll job skips its mode
  registers instead of timing out on them as well.
- A gateway that does not answer at startup is retried every 5 seconds
  instead of at the request rate, and the first full read keeps the request
  rate between units. A first read where no unit returns any state values
  also uses this backoff.
- Post-write readbacks wait for the configured request-rate slot.
- An invalid sensor value keeps the last known value but marks its read group
  as failed instead of refreshing the timestamp of stale data.
- Only a valid target-register readback confirms an airflow write; a target
  derived from measured airflow cannot confirm it.
- A pending fan, quick-mode or intensive value disappears when its window
  ends, even if no poll follows.
- Entries from older releases no longer poll registers of sensors that the
  selected profile does not have.
- Setup probes follow the same retry rules as normal polling. The setup
  dialog opens the serial port once for reading the unit list and probing
  the units.
- `write_registers` stops at the first failed write unless `--keep-going`
  is given.
- The diagnostics download shows how often the serial link was reopened.
- In unbalanced mode, a failed read of the mode registers keeps the last
  supply target instead of clearing it, as it already did for the extract
  target.
- The integration loads Home Assistant's `modbus` integration, so its
  `modbus.*` actions now show up in Home Assistant.
- Debug logging for the serial link moved from `pymodbus` to the
  `modbus_connection` and `tmodbus` loggers.
- The developer tools in `tools/` use `modbus-connection` with tmodbus and
  share their link settings. `raw_requests` and `count_requests` replace the
  scripts in the live test plan, `capture_setting_family --range` replaces
  `diff_register_snapshot`, and `monitor_gateway` is gone.

## 3.1.0

- The minimum supported Home Assistant version is now `2026.8`, the release the
  integration is tested against. The automation triggers and conditions and
  the `pymodbus` 3.13 requirement need a much newer release than the
  previously declared `2025.1`.
- Added diagnostic sensors for the gateway device path and unit Modbus slave IDs.
- Added device automation triggers and conditions for filter changes.
- Track read freshness by register group and expose read and write outcomes in
  the data-health sensor. System Health lists stale read groups per unit.
- Improve fan control when airflow or operating-mode readings are incomplete,
  and reduce polling of silent units and unnecessary target-register reads.
- Unit devices are now linked to the gateway device, and devices of units that
  a rescan no longer finds are removed automatically.
- Airflow sensors now use the volume flow rate device class.
- Fixed the maximum request rate option not being honored for sub-second
  intervals.
- Fixed a reload failing because a late write readback reopened the serial port.
- Fixed the quick mode showing `Individual` after a restart during intensive
  ventilation.
- Fixed intensive commands flagging data health on units that cannot report
  the intensive state.
- Setup no longer probes the gateway for entity metadata of older entries.
- Fixed optional-read backoff growth after repeated failures.
- Fixed the data-health sensor not reporting stale read groups unless a write
  also failed.
- Redact the configured serial path from gateway, job, and read errors in
  diagnostic downloads.

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
