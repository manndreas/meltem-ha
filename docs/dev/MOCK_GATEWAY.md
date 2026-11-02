# Local mock gateway

Use this stateful RTU-over-TCP simulator to smoke-test Home Assistant setup and
runtime flows without a physical gateway. It serves raw RTU frames, not Modbus
TCP frames, and is not a substitute for the live hardware checks in
[LIVE_GATEWAY_TESTS.md](LIVE_GATEWAY_TESTS.md).

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

Use `--help` for all options. For example, choose different models or a free
port:

```bash
.venv/bin/python -m tools.mock_gateway --units 2:ii_plain,3:s_fc --port 55020
```

In Home Assistant, add the integration with **RTU over TCP**, host
`127.0.0.1`, and the selected port. Select the matching profile for each unit.
The simulator prints each Modbus read and write; `Ctrl+C` stops it.

If Home Assistant runs in a container or on another host, bind to a reachable
interface, for example `--host 0.0.0.0`, and connect to that host's address.
Modbus has no authentication or encryption: expose the simulator only on a
trusted network.

## Suggested smoke sequence

1. Select both units during setup and finish the profile form. Confirm the
   integration loads and creates sensor, binary sensor, fan, number, select,
   and switch entities. Unit 2 has the CO2 and VOC entities; unit 3 has
   humidity entities.
2. Set a fan level or select an operating mode. Confirm the terminal shows
   writes to `41120..41124` ending with `41132 = 0`. The mock commits the mode
   to `41100..41102` when it receives that final write.
3. Change a humidity or CO2 setting on unit 2. Confirm its `42000..42005`
   register write appears in the terminal.
4. Download diagnostics. The configured host should be redacted. Check the
   integration's System Health page and the gateway and unit entities.
5. Change the request rate in the options. Try **Add unit** by selecting only
   unit 2 in the initial setup, then add unit 3 afterwards. Also try a rescan
   or profile change.
6. To exercise bridge replacement, start the simulator with `--single-client`
   and reconfigure the loaded entry from `127.0.0.1` to `localhost` using the
   same port. The old connection must be released before the new endpoint is
   checked.

## Failure scenarios

- `--no-units`: the gateway responds but reports no units; setup should show
  `no_devices_found`.
- `--silent-unit 3`: drop all requests to unit 3 while the gateway remains
  online. Requests to that unit will time out; the gateway and unit 2 remain
  reachable. Setup probes can take up to the configured network timeout.
- `--response-delay-ms 300`: delay every response to exercise slower requests.
- Stop the simulator with `Ctrl+C` to test an unavailable gateway. Restart it
  to check recovery.

The simulator handles the integration's holding-register reads (`0x03`) and
single-register writes (`0x06`). Other function codes close the client
connection. Registers not explicitly modeled read as zero. These defaults are
intended for integration smoke tests, not for validating manufacturer
behavior, timing, or unsupported Modbus operations.
