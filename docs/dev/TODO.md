# Meltem Integration TODO

Status: 4.2.0

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
- Where the app stores the actual shortcut configuration, that is the
  airflows of these shortcuts, is still unknown.

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
  information.
- A mapping to concrete model names is not known.

Next step:

- Collect values from more units before guessing a decoding.
- The manufacturer documents contain neither `116852` nor `VMD-22RPS44`; the
  article numbers do not match, see `docs/dev/reference/models.md`.

## TODO-4 — Wider block reads

Priority: low
Status: open, after the HW-7 post-release validation

Finding:

- The `register_ranges` of the components reproduce the blocks of the
  pymodbus client one to one. Merging neighbouring blocks, for example
  `41016..41021` in one request, would save requests per job.

Next step:

- Only with a benchmark on the real gateway, see test T-8 in
  `docs/dev/LIVE_GATEWAY_TESTS.md`. Update the parity test in
  `tests/components/meltem_ventilation/test_modbus_client.py` together with the ranges.

## TODO-5 — Raw register diagnostics

Priority: low
Status: open

Finding:

- The device-modelling framework of `modbus-connection` offers
  `async_read_raw()`, so raw register diagnostics need almost no own code.

Next step:

- Decide which ranges may be read and how often, so the diagnostics cannot
  flood the gateway.
