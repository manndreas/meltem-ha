# Meltem Integration TODO

Status: 4.1.0

This list only holds open items. Everything that used to be listed here as
done is described in `CHANGELOG.md`. Hardware observations and reverse
engineering notes are in `docs/MELTEM.md`, implementation notes in
`docs/DEVELOPER.md`, manufacturer facts in `docs/reference/`.

Open items that need a measurement on real hardware before a decision are in
`docs/HARDWARE_BACKLOG.md`.

## 1. Shortcut configuration in the shadow ranges `511xx` / `520xx`

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
- Only investigate further when convenient, see `docs/SETTING_RE_BACKLOG.md`.
- The manufacturer documents the supply/extract airflows as device parameters
  (IDs 42-47) without a Modbus mapping, see
  `docs/reference/device-parameters.md`.

## 2. Decode `PRODUCT_ID`

Priority: low
Status: open

Finding:
- `40002 PRODUCT_ID` is read and shown as a raw value in the device
  information.
- A mapping to concrete model names is not known.

Next step:
- Collect values from more units before guessing a decoding.
- The manufacturer documents contain neither `116852` nor `VMD-22RPS44`; the
  article numbers do not match, see `docs/reference/models.md`.

## 3. Move the test harness to the final Home Assistant 2026.10

Priority: low
Status: open, waiting for the stable release

Finding:
- `pytest-homeassistant-custom-component==0.13.368` ships Home Assistant
  `2026.10.0b0`, the first harness with the `modbus` integration users run
  against; the whole suite, including
  `tests/test_init.py::TestSharedModbusConnection`, passes with it.
- The harness does not install the requirements of the `modbus` integration,
  so `requirements-test.txt` pins them in line with its manifest.
- `2026.10` moved Home Assistant's own flows from `voluptuous` to `probatio`;
  the config flow follows, so mypy accepts its form schemas.

Next step:
- Once a harness for the stable `2026.10.x` exists, raise the pin and compare
  the extra pins with `homeassistant/components/modbus/manifest.json` again.

## 4. Wider block reads

Priority: low
Status: open, after the HW-7 post-release validation

Finding:
- The `register_ranges` of the components reproduce the blocks of the
  pymodbus client one to one. Merging neighbouring blocks, for example
  `41016..41021` in one request, would save requests per job.

Next step:
- Only with a benchmark on the real gateway, see test T-8 in
  `docs/LIVE_GATEWAY_TESTS.md`. Update the parity test in
  `tests/test_modbus_client.py` together with the ranges.

## 5. Raw register diagnostics

Priority: low
Status: open

Finding:
- The device-modelling framework of `modbus-connection` offers
  `async_read_raw()`, so raw register diagnostics need almost no own code.

Next step:
- Decide which ranges may be read and how often, so the diagnostics cannot
  flood the gateway.
