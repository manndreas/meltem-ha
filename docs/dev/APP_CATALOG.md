# Meltem app catalog: static analysis

These are leads from a local, static inspection of the Android app bundle, not
manufacturer Modbus documentation or new hardware measurements. The app was
not installed or executed for this analysis.

## Source and limitations

- Package `com.meltem.cloudapp`, app version `1.17`, version code `117`.
- Source: the user-supplied APKPure XAPK, SHA-256
  `aab569135d6d1988e449026b138db12444788d8a053d44b8bbfada56c7141f1d`.
  This is a third-party distribution; its authenticity was not independently
  verified. The archive and app code are not included in this repository.
- The app uses a product ID returned in its cloud device list to look up
  entries in its bundled product catalog. The inspected path does not prove
  that this cloud ID always equals the unit's Modbus `40002` value.
- The catalog had two product entries in this bundle. This is not evidence
  that no other products or IDs exist.

## Product entries

| App catalog ID | Decimal | App series label | App product identifier | Local evidence |
| --- | ---: | --- | --- | --- |
| `0x0001C874` | 116852 | `M-WRG-II` | `VMD-22RPS44` | Six M-WRG-II units returned the same ID at `40002` and identifier at `40011`. |
| `0x0001C853` | 116819 | `M-WRG` | `VMD-22RES22` | No M-WRG-S unit was available to confirm these local registers. |

The M-WRG-II observations are recorded in
[MELTEM.md](MELTEM.md#product-registers-and-readable-islands); they support the
mapping for those units, not a general guarantee about all variants. Neither
entry identifies a specific exchanger, sensor equipment, or exact model
variant. In particular, tested P and E units shared the same product fields.

The integration uses matching catalog entries as setup hints and as the Home
Assistant device's model identifier, retaining the raw numeric ID as hardware
version. Unknown IDs remain raw. The selected profile still determines the
device model and supported entities; even the series is not automatically
selected from the catalog. Stored IDs also work for already configured units,
but an old stored setup preview is not rewritten until a fresh probe.

To confirm the M-WRG entry, compare `40002` and `40011` on an M-WRG-S unit
with its type plate, as described in
[R-1](LIVE_GATEWAY_TESTS.md#r-1--product-and-identity-registers).

## Register and mode leads

The app's table and code suggest the following unit-side leads. Labels,
access flags, and numerical mode codes are app metadata, not proof that local
Modbus reads or writes are supported or safe:

| Unit register | App-derived lead |
| --- | --- |
| `41008` / `41012` | VOC on the extract side / CO2 on the supply side. Previous reads of absent or unused sensors returned `32767`; see [MELTEM.md](MELTEM.md#identity-and-readable-registers). |
| `41015` / `41019` | Speed capability bits / actual speed mode; the table marks `41019` for `0x0001C853`. |
| `41100` | One table entry is keyed by `41100` but contains `41101` as its register field. This inconsistency limits confidence in the table. |
| `41103..41106` | Temporary override mode and submodes, followed by scheduler state. |
| `41113` / `41133` | Read speed / read-write speed metadata; an older app control path contains a write to `41133`. This does not establish that the path is active or safe through the local gateway. |
| `42006` | Priority override mode, marked read-write by the app. No local write semantics have been established. |

The table associates night, interval, and temperature modes with submode
values `226`, `231`, and `224`, respectively. These are candidates, not
implemented commands. Registers in the table are unit-side leads: addresses
such as `41012`, `41015`, and `41019` have different meanings on the gateway.

Do not add speculative reads, writes, or grouped register ranges from these
leads. Further investigation requires a reviewed, targeted test and the
existing live-test safety rules. The settings research backlog remains in
[SETTING_RE_BACKLOG.md](SETTING_RE_BACKLOG.md).
