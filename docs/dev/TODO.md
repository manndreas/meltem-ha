# Meltem Integration TODO

Status: 5.0.0

This list only holds open items. Everything that used to be listed here as
done is described in `CHANGELOG.md`. Hardware observations and reverse
engineering notes are in `docs/dev/MELTEM.md`, implementation notes in
`docs/dev/DEVELOPER.md`, manufacturer facts in `docs/dev/reference/`.

Open items that need a measurement on real hardware before a decision are in
`docs/dev/HARDWARE_BACKLOG.md`.

Item IDs are permanent, like the `HW-n` IDs: a finished item is removed, and
its ID is not reused, so references such as `TODO-4` stay valid. Link to an
item by its ID, never by its position in the list.

## TODO-1 — Shortcut configuration in the shadow ranges `511xx` / `520xx`

Priority: low
Status: open, reverse engineering interest only

Finding:

- Switching the local panel to `Abluft` / `Zuluft` does not change
  `41120..41124`, but shadow/meta ranges in `511xx` / `520xx`.
- Where the unit stores its own shortcut configuration, that is the airflows
  of these shortcuts, is still unknown. The Meltem app does not use it: the
  static app analysis shows that the app keeps the airflows of its
  `Abluft` / `Zuluft` buttons on the phone and sends them with every command
  ([APK_ANALYSIS.md](APK_ANALYSIS.md#extract-only-and-supply-only-airflows)).

Assessment:

- Not a bug and not a user problem: since 3.0.0 supply and extract air are
  controlled directly through the two fan entities, and the guessed write path
  was removed.
- The read path still detects when the unit or the app activated such a
  shortcut.

Next step:

- Only investigate further when convenient, see `docs/dev/SETTING_RE_BACKLOG.md`.
- The manufacturer documents the supply/extract airflows as device parameters
  (IDs 42-47) without a Modbus mapping, see
  `docs/dev/reference/device-parameters.md`.

## TODO-2 — Decode `PRODUCT_ID`

Priority: low
Status: open

Finding:

- `40002 PRODUCT_ID` is read and shown as a raw value in the device
  information. The app catalog suggests series and product identifiers for two
  values; the M-WRG-II entry also matches the observed product name.
- The older-series entry has not been checked on hardware, and a mapping to
  concrete model variants is not known. See [APP_CATALOG.md](APP_CATALOG.md).

Next step:

- Collect values and type-plate details from more units, especially M-WRG-S,
  before inferring series or variants automatically.
- The manufacturer documents contain neither `116852` nor `VMD-22RPS44`; the
  article numbers do not match, see `docs/dev/reference/models.md`.

## TODO-5 — Raw register diagnostics

Priority: low
Status: open

Finding:

- The device-modelling framework of `modbus-connection` offers
  `async_read_raw()`, so raw register diagnostics need almost no own code.

Next step:

- Decide which ranges may be read and how often, so the diagnostics cannot
  flood the gateway.
- Exclude gateway (unit ID `1`) registers `41030..41098`: the Meltem app's
  setup writes the gateway credential and its cloud binding there
  ([APK_ANALYSIS.md](APK_ANALYSIS.md#gateway-modbus-tcp-frames)).

## TODO-7 — Discover RTU-over-TCP bridges via mDNS

Priority: low
Status: open, follow-up feature request

Finding:

- The ESP bridge can advertise an `_modbus._tcp` service, but the first
  RTU-over-TCP connection uses manual host and port entry to keep setup
  straightforward.

Next step:

- Assess discovering and selecting these services from the config flow.
- Verify Home Assistant can resolve the advertised service and hostname
  reliably before depending on mDNS for setup.

## TODO-8 — Name the app programs and the time-program state

Priority: low
Status: open, waits for
[H-13](LIVE_GATEWAY_TESTS.md#h-13--override-and-program-status)

Finding:

- The Meltem app starts its interval and temperature programs with `41120`
  arrays beginning `[3, 231]` and `[2, 224]`, and shows night ventilation,
  interval and temperature program as active when `41100/41101` read `3/226`,
  `3/231` or `2/224` ([APK_ANALYSIS.md](APK_ANALYSIS.md#mode-buttons-and-displayed-state)).
  The integration shows the interval and night programs as manual operation
  with the measured airflow, and the temperature program as an unknown
  operating mode.
- The app reads `41106` and treats `1`, `2` and `4` as an active time program.
  Local captures showed `1` for an active program, `3` for a stored but
  inactive one and `0` without a program
  ([MELTEM.md](MELTEM.md#app-time-program-comparison-on-2026-11-03)). An
  active time program changes the mode at its switch points without any
  command from Home Assistant.

Next step:

- Once H-13 confirms the codes, decode them as read-only states with
  translations and updated snapshots.
- A "time program active" sensor needs `41106`, which lies outside the
  current `ModeStatus` block. Add it together with
  [HW-13](HARDWARE_BACKLOG.md#hw-13--temporary-override-status-4110341105)
  candidate B (block `41100..41106`, benchmarked in T-13) instead of adding a
  separate request.
