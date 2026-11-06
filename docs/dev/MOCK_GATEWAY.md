# Local mock gateway

Use this stateful RTU-over-TCP simulator and its optional MBAP bridge to
smoke-test Home Assistant setup and runtime flows without a physical gateway.
The simulator serves raw RTU frames; the bridge presents Modbus TCP/MBAP and
translates requests to the simulator. Neither is a substitute for the live
hardware checks in
[LIVE_GATEWAY_TESTS.md](LIVE_GATEWAY_TESTS.md).

The register model is in `tools/_mock_model.py`; `tools/mock_gateway.py` adds
the network side, fault injection, runtime commands, and the command line.

## Start the simulator

Run from the repository root with the project test environment:

```bash
.venv/bin/python -m tools.mock_gateway
```

On Windows, use:

```powershell
.\.venv\Scripts\python.exe -m tools.mock_gateway
```

By default, it listens on `127.0.0.1:5020` and reports two units:

| Address | Profile | Simulated sensors |
| --- | --- | --- |
| 2 | `ii_fc_voc` | humidity, CO2, VOC |
| 3 | `ii_f` | humidity |

Sensor registers of a unit without that sensor return what sensorless units
returned on hardware: `239` for humidity and `32767` for CO2 and VOC. The
app-only registers `41008` (extract VOC) and `41012` (supply CO2) carry values
only on `ii_fc_voc`; real units have not confirmed them yet.

Use `--help` for all options. For example, choose different models or a free
port. Add `--mbap-port` to expose a simulated Modbus TCP/MBAP bridge that
forwards requests to the raw RTU mock:

```bash
.venv/bin/python -m tools.mock_gateway \
  --units 2:ii_plain,3:s_fc --port 55020 --mbap-port 1502
```

The raw RTU endpoint listens on port `55020`; the simulated MBAP endpoint
listens on port `1502`. In Home Assistant, choose **Modbus TCP (MBAP)** and
connect to the latter. This exercises the integration's MBAP transport and a
software RTU-to-MBAP bridge, but does not validate any physical bridge firmware.

By default, the bridge leaves a request unanswered when the RTU side does not
reply, so the integration sees a timeout. Most real MBAP-to-RTU bridges answer
with an exception instead; `--mbap-bridge-exceptions` imitates them with
`0x0B` for a missing or invalid RTU reply and `0x0A` when the RTU side cannot
be reached. The bridge waits `--mbap-response-timeout` seconds for the RTU
reply: by default `6` seconds, longer than the integration's 5-second network
timeout, and `1` second with `--mbap-bridge-exceptions`, as recommended for a
real bridge, so the exception arrives before the integration gives up.

The simulated product IDs follow the app catalog: `116852` for `ii_*` and
`116819` for `s_*`. The latter remains unconfirmed on real hardware; the
simulation is not evidence that an M-WRG-S unit reports that value.

In Home Assistant, add the integration with **RTU over TCP**, host
`127.0.0.1`, and the raw endpoint port. With `--mbap-port`, you can instead
choose **Modbus TCP (MBAP)** and use the MBAP endpoint port. Select the matching
profile for each unit. The simulator prints each Modbus read, write, and
injected fault; `Ctrl+C` stops it.

If Home Assistant runs in a container or on another host, bind to a reachable
interface, for example `--host 0.0.0.0`, and connect to that host's address.
With wildcard bindings (`0.0.0.0` or `::`), the MBAP bridge connects to the
local RTU mock through `127.0.0.1` or `::1`; both listeners keep the requested
bind address.
Modbus has no authentication or encryption: expose the simulator only on a
trusted network.

## Modeled behavior

Each rule below follows an observation in [MELTEM.md](MELTEM.md). Values that
no observation backs are marked as mock choices.

| Behavior | Source |
| --- | --- |
| A unit answers only its readable register islands (`40000..40022`, `40024..40033`, `40041`, `40050..40051`, `40071`, `40100..40104`, `40109..40110`, `40120`, `40200..40209`, `41000..41034`, `41100..41113`, `42000..42009`). A block read that touches any other register fails as a whole with `0x02`. | [Product registers and readable islands](MELTEM.md#product-registers-and-readable-islands) |
| The gateway (address `1`) answers only its MAC, clock, uptime, RF statistics, and unit list; other registers, such as `41998..42001`, return `0x02`. Writes to the gateway return `0x02`. | [Gateway registers and function codes](MELTEM.md#gateway-registers-and-function-codes) |
| An address without a paired unit gets no reply, so its requests time out. | [Discovery and unit list](MELTEM.md#discovery-and-unit-list) |
| The set registers `41120..41124` answer reads with `0x05` until they are written in the current gateway session; written ones read back. `41132` is never readable. | [Set-register readback](MELTEM.md#set-register-readback-at-4112041124) |
| During an RF warm-up (`--warmup-seconds`, `reboot`, `warmup`), units answer `41000..41006`, `41009..41011`, and `41016..41021` with `0x05` while other blocks answer. | [Request pacing and block reads](MELTEM.md#request-pacing-and-block-reads), T-8 |
| Writing `41132 = 0` commits the set registers to the status map `41100..41102` and the airflow `41020..41021`, optionally after `--settle-ms`. | [Operating mode and set registers](MELTEM.md#operating-mode-and-set-registers) |
| Intensive (`41123 = 3`, `41124 = 227`) keeps running, also after switching off, until both registers are cleared; its status reads `3/227/227`. An intensive start from the app (`app ADDR intensive`) reads `0/0/0`. | [Read-only mode status](MELTEM.md#read-only-mode-status-at-4110041102), W-8 |
| Sensor control reports the written value with bit `32` set (`112`, `176`, `48`). | Community decode, see [Status values without hardware evidence](MELTEM.md#status-values-without-hardware-evidence) |
| Uptime counts up from `12345` s and restarts at `0` after `reboot`; the UTC clock follows the host clock plus `clock OFFSET_S`. | Mock choice |
| Quick-mode airflow: LOW 20, MEDIUM 30, HIGH 60, intensive 80 m³/h (capped at the profile maximum). | Mock choice; intensive ran at 80/80 m³/h in W-8 |
| `41015 = 49144`, `41019 = 31`, `41113 = 425`, and `42006 = 12` on every unit; external input level 60 %, on-delay 1 min, run-on 15 min. | [App-derived register survey](MELTEM.md#app-derived-register-survey-on-2026-11-03), [reference](reference/modbus.md) |

The RTU simulator does not model request pacing: by default it answers as fast
as it can, including to several clients at once. `--single-client` rejects a
second connection on both the RTU endpoint and the optional MBAP bridge. How
the real gateway degrades under too many requests is not characterized well
enough to imitate.

## Suggested smoke sequence

1. Select both units during setup and finish the profile form. Confirm the
   integration loads and creates sensor, binary sensor, fan, number, select,
   and switch entities. Unit 2 has the CO2 and VOC entities, with supply-air
   CO2 and extract-air VOC disabled by default; unit 3 has humidity entities.
2. Set a fan level or select an operating mode. Confirm the terminal shows
   writes to `41120..41124` ending with `41132 = 0`, and the readback of
   `41121`. The mock commits the mode to `41100..41102` and the airflow to
   `41020..41021` when it receives that final write.
3. Change a humidity or CO2 setting on unit 2. Confirm its `42000..42005`
   register write appears in the terminal.
4. Download diagnostics. The configured host should be redacted. Check the
   integration's System Health page and the gateway and unit entities.
5. Change the request rate in the options. To test **Add unit**, start the
   mock with only unit 2, set it up, then type `pair 3 ii_f` in the simulator's
   terminal and add the unit in Home Assistant. Also try a rescan or profile
   change.
6. To exercise bridge replacement, start the simulator with `--single-client`
   and reconfigure the loaded entry from `127.0.0.1` to `localhost` using the
   same port. The old connection must be released before the new endpoint is
   checked.

## Runtime commands

While the simulator runs, type commands on its standard input. With
`--control-port PORT` it also accepts them line by line on that TCP port, for
example from a script or `nc 127.0.0.1 PORT`; port `0` picks a free one. Each
command prints a one-line confirmation or `error: ...`. Address `1` is the
gateway.

| Command | Effect |
| --- | --- |
| `help`, `status` | List the commands; show units, pairing, overrides, delays, and scripted faults. |
| `pair ADDR PROFILE`, `unpair ADDR` | Add or re-pair a unit and list it in the gateway, or remove it from the list so its requests time out. |
| `silent ADDR on\|off` | Drop or answer the requests of a unit that stays listed. |
| `set ADDR REG VALUE`, `get ADDR REG [COUNT]` | Change or show raw registers. `set` makes the register readable; `get` ignores the read rules. |
| `refuse ADDR REG[-REG]`, `allow ADDR REG[-REG]` | Answer reads and writes of these registers with `0x02`, or undo it. |
| `flag ADDR error\|filter\|frost on\|off` | Set a status flag in `41016..41018`. |
| `app ADDR off\|low\|medium\|high\|intensive`, `app ADDR SUPPLY [EXTRACT]` | Change the unit as the Meltem app would: status map and airflow change, the set registers do not. |
| `reboot [WARMUP_S]` | Restart the gateway: uptime starts at `0`, a warm-up begins, and the set registers are unreadable again. |
| `warmup SECONDS`, `clock OFFSET_S`, `settle MS` | Start a warm-up, shift the gateway clock, or delay status and airflow after each commit. |
| `delay MS`, `delay ADDR MS` | Delay all replies, or additionally the replies of one unit. |
| `drop-rate P`, `corrupt-rate P` | Drop the replies, or break their CRC, with probability `P` (`0..1`). |
| `fault ADDR\|* ACTION [COUNT]`, `fault clear` | Apply a fault to the next `COUNT` replies to a unit or to any address. |
| `dump FILE` | Write the current registers as a register map. |

Fault actions:

| Action | Reply |
| --- | --- |
| `timeout` | The request is lost and not applied; no reply. |
| `lost-reply` | The request is applied, but no reply is sent. |
| `late` | The reply is held back and sent just before the next reply on that connection. |
| `fragmented` | The reply arrives in two parts, 20 ms apart. |
| `bad-crc`, `short` | The reply has a broken CRC, or lacks its CRC. |
| `wrong-unit`, `wrong-function` | A valid frame from another address, or with function `0x04`. |
| `wrong-length` | A read reply with one register more than requested. |
| `wrong-address`, `wrong-value` | A write echo with the next address or value. |
| `illegal-address`, `ack`, `busy`, `gateway-path`, `gateway-target` | Exception `0x02`, `0x05`, `0x06`, `0x0A`, or `0x0B` instead of the reply. |

## Failure scenarios

- `--no-units`: the gateway responds but reports no units; setup should show
  `no_devices_found`. Requests to unit addresses then time out, as they would
  on a gateway without paired units. Type `pair 2 ii_fc_voc` and retry setup
  to verify recovery.
- `--silent-unit 3`: drop all requests to unit 3 while the gateway remains
  online. Requests to that unit will time out; the gateway and unit 2 remain
  reachable. Setup probes can take up to the configured network timeout.
- `--warmup-seconds 120`: the sensor, status, and airflow blocks return
  Acknowledge for two minutes after the start, as after connecting a real
  gateway. Use `reboot 120` later for the same after a gateway restart.
- `--refuse 3:41007`: unit 3 (`ii_f`) refuses the CO2 register, so a raw
  read of the merged block `41006×6` fails as a whole, as an
  `M-WRG-S/Z-T-F` did in
  [HW-10](HARDWARE_BACKLOG.md#hw-10--merged-air-quality-block-on-sensor-equipped-units).
  The integration does not read `41007` on `-F` units, so its humidity
  sensors must keep working. Which register the real unit refused is not
  known.
- `--refuse 1:41019-41020`: the gateway refuses its uptime, so the optional
  uptime read keeps failing and retries with a growing delay.
- `--settle-ms 3000`: status and airflow change three seconds after each
  command, so pending values and write confirmation can be watched.
- `--response-delay-ms 300` and `--unit-delay 3:500`: delay every reply, or
  additionally those of unit 3.
- `--drop-rate 0.05 --corrupt-rate 0.02 --seed 1`: lose or break a share of
  the replies for a soak test of the retry and recovery paths; `--seed` makes
  a run repeatable.
- `MbapRtuTcpMockBridge.inject_late_reply()` in a Python test holds one MBAP
  response until the next request, then sends it with its original transaction
  ID. This exercises late-response handling without changing the RTU mock's
  existing raw-frame fault behavior.
- `--mbap-bridge-exceptions --silent-unit 3` with `--mbap-port`: the bridge
  answers requests to unit 3 with `0x0B` after one second instead of staying
  silent, so the integration sees an answered request rather than a timeout,
  see [HW-15](HARDWARE_BACKLOG.md#exception-replies-from-a-bridge-hw-15).
- Stop the simulator with `Ctrl+C` to test an unavailable gateway. Restart it
  to check recovery.

The simulator handles the integration's holding-register reads (`0x03`) and
single-register writes (`0x06`). Other function codes close the client
connection.

## Register maps

`--register-map FILE` (repeatable) applies a JSON file on top of the generated
registers; `--dump-register-map FILE` writes the resulting registers and exits,
and the `dump FILE` command does the same at runtime. A register map looks like
this:

```json
{
  "format": 1,
  "gateway": {"registers": {"42100": 75}},
  "units": {
    "2": {"profile": "ii_plain", "paired": true, "registers": {"41006": 239, "41007": null}}
  }
}
```

Units in the map are added or re-paired with the given profile; `"paired":
false` keeps a unit configured but unlisted. A value overrides the register and
makes it readable, even outside the islands above. `null` makes a register
refuse with `0x02`. The unit list on the gateway always follows the paired
units.

Captures written by `tools.capture_setting_family`
(`{"metadata": {"slave": N, ...}, "values": {...}}`) load the same way into the
unit `N`, which must already be set with `--units`. A `null` for a set register
or `41132` in a capture only means it was not readable yet, which the model
already covers, so it is ignored.

## Limits and tests

The defaults are intended for integration smoke tests, not for validating
manufacturer behavior, timing, or unsupported Modbus operations. The rules in
[Modeled behavior](#modeled-behavior) only repeat what the cited measurements
showed on M-WRG-II units; they say nothing about M-WRG-S units or other
firmware.

In tests, `RtuTcpMockGateway.start()` binds a free port unless one is given,
so a simulator left running on `5020` does not collide with the test suite.
`RtuTcpMockGateway.execute()` runs the runtime commands directly, and
`MockGatewayModel` takes injectable clocks to step through warm-ups and settle
delays.

The POSIX-only RTU protocol tests in `test_serial_protocol.py` keep their own
pseudo-terminal simulator: they test the serial transport, which the TCP mock
does not exercise. The fault actions above cover the same reply defects, so
they can also be reproduced over TCP on Windows.
