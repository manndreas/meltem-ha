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

## TODO-5 — Raw register diagnostics

Priority: low
Status: open

Finding:

- The device-modelling framework of `modbus-connection` offers
  `async_read_raw()`, so raw register diagnostics need almost no own code.

Next step:

- Decide which ranges may be read and how often, so the diagnostics cannot
  flood the gateway.

## TODO-6 — Support RTU over TCP gateways

Priority: low
Status: open, feature request ([GitHub issue #7](https://github.com/manndreas/meltem-ha/issues/7))

Request:

- Support network gateways such as the ESP32 bridge in the issue, which exposes
  the Meltem gateway over RTU over TCP. This would help installations where
  Home Assistant cannot access the gateway directly over USB, for example in
  some Docker setups.

Transport spike (2026-11-02):

- On Home Assistant `2026.11.0b0`, `modbus-connection 4.12.7`, and `tmodbus
  1.1.0`, the shared Home Assistant connection handled three RTU reads over a
  single TCP connection using `ModbusSerialParams` with a `socket://` device
  and RTU framing.
- The test used the integration's `prepare_unit` and a local TCP server that
  delimited frames after the ESP bridge's 40 ms idle gap. All three requests
  carried valid RTU CRCs and returned the expected values.
- This was a software-only test: no ESP32, Meltem gateway, or ventilation unit
  was connected. The installed transport appears sufficient; end-to-end
  behavior with the bridge remains unverified.

Agreed first-version scope:

- Support generic raw RTU over TCP, compatible with the ESP bridge; do not add
  Modbus TCP/MBAP or UDP.
- Enter the host and port manually, defaulting to port `5020`; mDNS discovery
  is a separate follow-up.
- Allow changing between serial and network transports in Reconfigure.
- Document that the ESP bridge uses unauthenticated, unencrypted TCP and should
  only be used on a trusted local network.
- Mark the feature as not validated against real ESP32/Meltem hardware.

Next step:

- Generalize the gateway connection configuration and setup/reconfigure flow
  to support either the existing serial device or an RTU-over-TCP host and
  port, while preserving existing serial entries.
- Validate the integration against an ESP32 bridge and Meltem gateway before
  advertising support.

## TODO-7 — Discover RTU-over-TCP bridges via mDNS

Priority: low
Status: open, follow-up feature request

Finding:

- The ESP bridge can advertise an `_modbus._tcp` service, but the first
  RTU-over-TCP implementation will use manual host and port entry to keep its
  setup scope small.

Next step:

- After manual RTU-over-TCP setup is supported, assess discovering and
  selecting these services from the config flow.
- Verify Home Assistant can resolve the advertised service and hostname
  reliably before depending on mDNS for setup.
