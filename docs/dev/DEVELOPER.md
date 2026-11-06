# Developer Notes

This document captures the implementation details of the `Meltem M-WRG`
Home Assistant integration and the decisions behind them.

Related documents:

- `docs/dev/reference/`: manufacturer reference extracted from the original Meltem
  documents
- `docs/dev/MELTEM.md`: observed register behaviour, gateway quirks, and traced
  app writes
- `docs/dev/HARDWARE_BACKLOG.md`: review findings that need a live gateway before
  they can be decided
- `docs/dev/LIVE_GATEWAY_TESTS.md`: how to measure them
- `docs/dev/MOCK_GATEWAY.md`: how to run software-only RTU-over-TCP and simulated
  Modbus TCP/MBAP smoke tests
- `docs/dev/SETTING_RE_BACKLOG.md`: parked reverse engineering of app settings
- `docs/dev/APK_ANALYSIS.md`: static Android app and cloud-protocol reference

## Safety and liability

This is an unofficial community project. It is provided without warranty, and
the authors do not accept liability for damage to Meltem units, gateways,
building systems, Home Assistant hosts, or other connected equipment.

When changing write behavior, timing, or register usage, prefer conservative
defaults and validate against real hardware carefully.

## Current state summary

These are the most important practical findings from the latest hardware tests:

- gateway-backed discovery via `43901` / `43902..` is stable
- a small positive request gap works better than no gap at all
- `REQUEST_GAP_SECONDS = 0.1` was stable on the tested setup
- a zero request gap was unstable in the pymodbus measurements and timed out
  during the 2026 tmodbus gap sweep; see HW-7 in
  [HARDWARE_BACKLOG.md](HARDWARE_BACKLOG.md)
- read-only current mode and target state come from `41100..41102` for all
  supported M-WRG-S and M-WRG-II profiles; this is directly measured on
  M-WRG-II, and assumed for M-WRG-S
- `41120..41124` are set registers and are not used for routine polling
- `41020/41021` behave like the effective/current airflow and may lag behind
- many devices refuse reads of the set registers, the five-register block even
  after a write; routine polling therefore never reads them, and only a recent
  balanced airflow write is confirmed by a single read of `41121` (see HW-4)
- immediate write confirmation polling created unnecessary bus load and was
  removed for airflow writes
- short read failures preserve the previous state, but a unit that keeps
  failing or stays silent is marked unavailable instead of showing frozen
  values; a single unanswered read only pauses the unit's reads
- room entities stay unavailable until the first poll returns at least one
  state value
- unloading shuts the client down for good, so a late post-write readback
  cannot reach the link; Home Assistant's `modbus` integration releases the
  serial or TCP connection once the entry has released its units
- since `4.0.0` all of this runs over Home Assistant's shared Modbus
  connection (tmodbus) instead of an own pymodbus client; partial live
  tmodbus-vs-pymodbus results and remaining post-release validation are tracked
  in HW-7

## Runtime overview entities

Gateway and room diagnostics, including Read health, use cached coordinator
state and one shared ten-second timer per coordinator. Entities unregister
their callbacks on unload; the last one removes the timer. Their availability
does not follow a failed coordinator update: a disconnected state and the last
read timestamp must remain visible. Room reachability is unknown until its
first read attempt. The timestamp is the latest successful supported group for
that room only.

Absolute humidity is computed only when the matching temperature and humidity
arrive in the same room read; a partial or invalid pair clears the derived
value instead of combining fresh and stale inputs. Raw `41100..41102` values
are retained before mode normalization for the diagnostics download.
External-input diagnostics use independent single-register reads only while
enabled, with the shared gateway lock and transport rate limit. At most one
such room read is added per scheduler tick; disabled entities trigger none.
The CO2/VOC measurements use ppm and normal air-quality polling. Supply-air
CO2 (`41012`) and extract-air VOC (`41008`) exist only on the VOC profile,
where they are decoded from the `41006..41013` block it reads anyway; the
`-FC` profiles carry only extract-air CO2 (unit matrix in
[reference/models.md](reference/models.md)) and do not read `41012`.
Component register ranges remain unchanged.

`WriteConfirmations` keeps outcomes per room and write key for internal
read-health evaluation. Confirmation deadlines and recent failed or
unconfirmed writes remain available through Read health without an additional
entity or poll. Raw transport errors are not exposed as entity attributes.

Everything else the coordinator tracks for one unit between reads and writes
is a `RoomRuntime` (`room_runtime.py`, `coordinator.room_runtime(key)`): the
run of failed read jobs, the last read error, whether its unavailability was
logged, the target-read failure streak, the mode-group health of an interrupted
flow write, and the fallback of the last fan write. Pending display values stay
in the `OptimisticOverlay` instances and write outcomes in
`WriteConfirmations`.

Gateway measured flow totals use room and read-group
availability. Missing contributors make the native total unknown, with
`complete` and `unknown_units` attributes. They
must not silently show partial totals or substitute optimistic fan values.
Per-room balance belongs to the existing flow health group and adds no read.
All new IDs belong in the platform maps in `const.py`, and hardware-specific
ones in `ENTITY_PROFILES`, so `entity.room_has_entity` creates them and
registry pruning keeps user customizations on reload.

`TransportPolicy` keeps a bounded rolling 300-second deque of individual
request attempts. Each retry is a separate sample. Response-time measurements
start after locking and rate-limit waits; only successful operations enter the
latency mean. Modbus exception responses are failed operations even though they
reset the link timeout counter. Cancellation and closed-client refusals are
excluded. Reading the transport diagnostics snapshot also expires old samples
without bus activity. A config-entry diagnostics export additionally performs
a gateway discovery through the shared coordinator lock; it reads the node
count and, when available, the node address list, so it can briefly delay
polling.

The gateway MAC (`41012..41014`) is read once after the initial coordinator
refresh, through the shared coordinator lock and transport policy; failure is
best-effort and leaves the gateway device without that connection. Gateway
uptime (`41019..41020`, low-word-first U32 seconds), UTC clock
(`41015..41016`), registered-device count (`43901`), and RF statistics
(`42100..42105`) use optional 60-second reads on gateway unit `1` through the
same lock and policy. Their errors are isolated from room availability/backoff
counters. Cached observations expire after 180 seconds. Uptime, clock, and RF
entities are disabled by default; the registered count is enabled by default.
The four RF entities register consumers of one shared read, cancelled only
when the last consumer disables it or the entry unloads. Existing component
ranges remain unchanged; RF statistics have their own six-register component,
matching the live read shape.

RF message buckets use `measurement`, not `total_increasing`. RF loads decode
low-word-first Float32 percentages without rescaling, following pyairios;
physical calibration remains unverified on Meltem. The room RF job also reads
`40103` and `40104` as individual `Register` components, each with independent
health. Unsupported optional RF reads do not count as room failures, unlike
timeouts. `40103 = 0xFFFF` and unrecognised `40104` values clear the affected
field to unknown. Hardware observations are recorded in [MELTEM.md](MELTEM.md).

## Scope

The integration targets Meltem M-WRG units behind an `M-WRG-GW` gateway over
Modbus RTU via USB, a raw RTU-over-TCP bridge, or a Modbus TCP/MBAP endpoint.

Supported series/profiles:

- `M-WRG-S`
- `M-WRG-S (-F)`
- `M-WRG-S (-FC)`
- `M-WRG-II`
- `M-WRG-II (-F)`
- `M-WRG-II (-FC)`
- `M-WRG-II (O/VOC-AUL)`

## Gateway network interface

Previous app traffic captures showed cloud communication for normal control,
and earlier LAN probes did not find a useful local service. A later
[read-only comparison](MELTEM.md#read-only-modbus-tcp-comparison-on-2026-11-03)
did obtain matching gateway and unit values over native Modbus TCP, but the
port subsequently refused new connections. The reason and the conditions for
reliable access remain unknown.

The integration now also has a Modbus TCP/MBAP connection type, tested through
the software MBAP-to-RTU bridge in [MOCK_GATEWAY.md](MOCK_GATEWAY.md). The
brief hardware comparison did not validate sustained use of the gateway's
native TCP endpoint, writes, or coexistence with the app. Do not reset or
reconfigure the gateway merely to make its LAN port accessible; see
[HW-15](HARDWARE_BACKLOG.md#hw-15--verify-reliable-modbus-tcpmbap-operation).

## Gateway and Modbus behavior

The gateway is slow and should be treated conservatively, see
[MELTEM.md](MELTEM.md#request-pacing-and-block-reads) for the measurements.

Implementation rules:

- Keep a pause between Modbus requests.
  Current implementation: `REQUEST_GAP_SECONDS = 0.1`
- Retry a request at most once, see [Transport](#transport-home-assistants-shared-modbus-connection).
- Serialize all periodic reads and writes through one shared gateway lock.
- Use one scheduled read job at a time.
- Prefer grouped block reads over single-register reads.

## Transport: Home Assistant's shared Modbus connection

Since `4.0.0` the integration has no serial client of its own. It asks Home
Assistant's `modbus` integration for one `ModbusUnit` per Modbus address:

- `async_get_unit(hass, entry, params, unit_id)` at runtime; the units are
  released when the entry unloads, and the link closes after the last holder
- `async_get_temporary_unit(hass, params, unit_id)` in the config flow
- `gateway_connection.py` interprets existing entries without a connection
  type as serial and builds parameters for serial, raw RTU-over-TCP, or
  Modbus TCP/MBAP links
- the backend is always tmodbus via `modbus-connection`; HA `2026.11.0` pins
  `modbus-connection` `4.12.7`. Version `4.12.3` first added `require_timeout`
  and the device-modelling framework used here; the integration's minimum
  dependency is aligned with the supported HA baseline
- serial parameters from `build_serial_params` are fixed at 19200 baud, 8E1,
  RTU; raw RTU-over-TCP uses `socket://host:port` with RTU framing; MBAP uses
  `ModbusTcpParams` with the host and TCP port
- another integration that uses the same endpoint with incompatible link
  settings makes setup fail with `ConfigEntryError` (config flow:
  `port_in_use`)
- every unit asks for `set_message_spacing(REQUEST_GAP_SECONDS)` and a
  transport-specific timeout before its first request (`prepare_unit`);
  both are floors, so another consumer of the same endpoint can only raise
  them. USB uses `FIXED_TIMEOUT` (0.8 s); both network protocols use
  `NETWORK_TIMEOUT` (5 s), including DNS and connection establishment. The
  shared Modbus API does not separate connect and response timeouts, so a
  silent unit over TCP can take 5 s per attempt
- the config flow reads the unit list and probes every unit on one temporary
  link, so the endpoint is opened once per step
- when changing a loaded network connection, the coordinator holds its gateway
  lock while disconnecting the old link and checking the replacement. This
  lets a single-client bridge accept the check; on failure the unchanged old
  connection is reopened by the next request. The link can be shared with
  other integrations, which can reconnect while the check is running; their
  requests cannot be serialized by this coordinator

Register blocks are `Component`s in `device/components.py`. Each one pins
`register_ranges` to a block read on the gateway. `Temperatures` reads
`41000..41005` and `SupplyTemperature` reads `41009..41010`. `-F` and `-FC`
units read their air-quality sensors with `ExtractAirQuality` (`41006`, or
`41006..41007` with CO2) and `SupplyAirQuality` (`41011`). Only `O/VOC-AUL`
reads the merged `AirQuality` block `41006..41013`. 5.0.0 had merged the `-F`
and `-FC` reads into `41006..41011` as well, until an `M-WRG-S/Z-T-F` refused
that block, see
[HW-10](HARDWARE_BACKLOG.md#hw-10--merged-air-quality-block-on-sensor-equipped-units).
T-8 benchmarked the merged blocks only on sensorless `M-WRG-II` units, which
never send them at runtime.
`tests/components/meltem_ventilation/test_modbus_client.py` checks the request
shapes with the mock's `read_events`. Do not merge blocks without a benchmark
on the real gateway, and on a sensor-equipped unit for the air-quality
registers: a block read fails as a whole as soon as one register in it is
refused.

The retry rules live in `device/transport.py` (`TransportPolicy`, one shared
instance per gateway link):

| Error | Behavior |
| --- | --- |
| `ModbusTimeoutError`, `ModbusProtocolError` | pause unanswered reads for 5 s; retry writes once; counts toward the timeout counter |
| `GatewayTargetError`, `GatewayPathUnavailableError` | retry once; resets the counter, the gateway answered |
| `ModbusConnectionError` | pause reads for 5 s; for writes wait `TRANSPORT_RETRY_DELAY_SECONDS` (5 s), then retry once |
| `ClientClosedError` | no retry |
| other `ModbusExceptionError` | no retry; resets the counter |
| `TRANSPORT_DISCONNECT_AFTER_TIMEOUTS` timeouts in a row, across all units, and no answer at all for `TRANSPORT_LINK_QUIET_SECONDS` | `disconnect()`; the next request reopens the link, and a new quiet window starts |

Over Modbus TCP through a separate bridge, `0x0A` and `0x0B` usually come from
the bridge rather than the gateway, so a silent unit or an unplugged gateway
behind it still counts as an answer of the link. Whether to treat them like a
timeout there is open as
[HW-15](HARDWARE_BACKLOG.md#exception-replies-from-a-bridge-hw-15).

After an unanswered, malformed, disconnected or cancelled holding-register read,
the policy pauses further reads of that unit for `TRANSPORT_READ_RECOVERY_SECONDS`
(5 s). It does not immediately retry or send a synchronization request. During
the pause, skipped jobs do not change read health or failure counters. After
the pause, the next scheduled read is attempted; a further failure starts a new
pause. The pause only affects scheduling: the values cached before the
unanswered read were valid, so the coordinator does not make the room
unavailable for it. Availability follows the failure threshold and the silence
limit described in [Per-unit availability on read failures](#per-unit-availability-on-read-failures),
so one lost telegram does not flap every entity of the unit. Writes and other unit IDs are not
blocked. A gateway-unit timeout also delays its diagnostic and discovery reads;
Home Assistant retries a failed initial setup through its normal backoff.

Five seconds is a conservative recovery delay, not a measured or guaranteed
maximum response time. A stale RTU response may still arrive after the pause and
be mistaken for a later response to the same unit and length. This is not a
bus-wide quarantine: other holders of the Home Assistant connection can send
requests without this policy's lock. Reload or setup retry creates a new policy;
disconnecting or reopening the port is not a verified reset of the gateway.
Without an exclusive shared-link quarantine and a proven release condition,
stale answers remain possible. See HW-7.

An exception response is not retried immediately by the transport. For a
polled component, `AcknowledgeError` (`0x05`) starts a per-slave/component
backoff of 10, 20, 40, then at most 60 seconds. Optional room diagnostics
(`fan_motor_hours` and the `401xx` RF registers) apply the same component
backoff on any `ModbusExceptionError`, including `IllegalDataAddressError`
(`0x02`), and do not mark `data_health` stale while they have never succeeded.
The next due read retries the
same register block; a successful block read clears its backoff. Other
exception codes do not start this component backoff. The separate current-mode
status read retains its existing 30-to-300-second exception backoff. While a
component is backed off, its cached values and read health are left unchanged;
the skipped poll is not counted as another failed read group. Other components
and rooms continue polling. The setup probe does not use the runtime component
backoff. It gives up on a unit after a timeout before the unit answered
anything, since every further read would only time out as well. Once the unit
has answered, one lost or garbled reply, or a read refused by the recovery
pause, is read again after `TRANSPORT_READ_RECOVERY_SECONDS`; without that
retry a single lost telegram would turn a sensor unit into a `plain` proposal.
During a rescan the runtime client probes under the gateway lock, so that rare
wait also delays polling by up to five seconds.
A timeout while the link is not connected is a failed connect, for example to
an unreachable bridge, and raises `MeltemConnectionError` like discovery does.

The quiet window keeps silent units from recycling a link their neighbours
still answer on, also when two silent units or two jobs of one silent unit
follow each other. Any answer counts, including exception responses. The
diagnostics download shows `link_recycles`, `consecutive_timeouts`, and
`seconds_since_any_answer` under `coordinator.transport`; a recycle is logged
at info level.

`ModbusUnit.disconnect()` drops the whole shared connection, not just the
Meltem units: every other holder of the port reconnects on its next request,
and a request that is still in flight is cut after a short grace period. This
is accepted because the port is the gateway's own USB adapter, so any other
holder talks to the same gateway, which is not answering either. The quiet
window only counts answers to Meltem requests, so a recycle can still hit
another integration that keeps the gateway busy with its own traffic.

The client maps `ModbusConnectionError` to `MeltemConnectionError` and every
other `ModbusError` to `MeltemModbusError`. Gateway readers also report missing
or invalid payloads as `ModbusProtocolError`, so optional gateway reads cannot
discard successful room updates because of an invalid value. Transport and
read health share `NO_ANSWER_ERRORS` for timeouts and protocol errors; both
count as missing valid answers for the coordinator's transport backoff and
skip subsequent mode reads in the job.

When a unit times out before any of its blocks answered in a job, the rest of
that job is skipped and its groups are marked failed. Jobs during the recovery
pause are skipped without another request or failure count. If a timeout occurs
after another block answered, later blocked components are skipped. Only an
exception response starts the mode backoff.

So far an unconfigured address timed out after about 0.8 s, while a
physically powered-off unit caused no timeout at all: the gateway kept
answering its airflow jobs, possibly from cached data. No Busy/code-6 response
has been observed. The per-job skip therefore stays tied to timeouts until
HW-7 shows how the gateway reports a powered-off unit; the measurements are in
[HARDWARE_BACKLOG.md](HARDWARE_BACKLOG.md#hw-7--validate-the-tmodbus-transport-on-the-real-gateway)
and [MELTEM.md](MELTEM.md#reads-while-a-unit-is-powered-off).

The `homeassistant.components.modbus` API is new and still changing
(`get_hub` is already deprecated). Keep the lower bound in the manifest tight
and check the breaking changes of every Home Assistant release.

How the hardware findings are implemented now:

| Finding | Implementation |
| --- | --- |
| The gateway needs a pause between requests | `REQUEST_GAP_SECONDS` via `set_message_spacing` in `prepare_unit` |
| 0.8 s is enough for the gateway to answer | `FIXED_TIMEOUT` via `require_timeout` in `prepare_unit` |
| pymodbus' default resend turned one unanswered register into about eight timeouts | `TransportPolicy` pauses reads after the first unanswered attempt |
| M-WRG-S and M-WRG-II status is read separately from the set registers (HW-4) | Every supported profile reads `41100..41102`; writes remain on `41120..41122` plus `41132`. M-WRG-S uses the same map by explicit assumption; live data is from M-WRG-II. |
| Read-side sensor subcodes differ from write values | `_read_mode_block` normalizes `41101` status codes before decoding sensor modes; the codes come from the ioBroker decode and are not yet observed on hardware (HW-4) |
| `41000` and `41004` are swapped on the gateway | field mapping of `Temperatures` in `device/components.py` |
| 32-bit values are word-swapped | `word_order="little"` on `float32` and `uint32` fields |
| Mode writes only take effect after `41132` | `Command` component, written last in every mode and preset sequence |
| Discovery runs on the gateway's own unit 1 | `MeltemGateway` in `device/device.py` |
| A silent unit must not drag down the others | per-job skip in `MeltemModbusClient._poll` and `_read_mode_block`, quiet window in `TransportPolicy`, `SILENT_ROOM_POLL_SECONDS` in the coordinator |

## Discovery model

There is no true Modbus auto-discovery. The integration performs a best-effort
gateway-backed discovery on the Meltem bridge path.

Current assumptions:

- the gateway exposes its configured unit list on bridge `device_id=1`
- `43901` returns the number of configured units
- `43902..` returns the configured unit addresses
- USB discovery finds the gateway, not the individual ventilation units

The values seen on the tested gateway are in
[MELTEM.md](MELTEM.md#discovery-and-unit-list).

## Polling strategy

The integration uses a serialized scheduler with an adjustable maximum
read-request rate. The shared `TransportPolicy` enforces the limit across
all units; the coordinator owns the gateway lock, job-start pacing, backoff,
and write sequences. The parts without Home Assistant state live in their
own modules:

| Module | Content |
| --- | --- |
| `polling.py` | job groups, their intervals, job planning and selection |
| `read_health.py` | freshness, availability, and staleness of a read group |
| `levels.py` | supply/extract targets of a unit and their tolerances |
| `overlay.py` | pending values shown until a readback confirms them |
| `write_confirmation.py` | outcome of each write against its readback |

Current design:

- each read operation is scheduled as one room-scoped job
- jobs use block reads where possible
- only one job runs at a time
- `max_requests_per_second` limits both job starts and individual read
  attempts, including optional mode-status reads and retries; the policy waits
  at least `1 / rate` after the previous read attempt finishes, not merely
  after it was submitted, so a slow connect or link queue cannot cause a burst
- this read limit is configured before gateway validation and is shared by
  discovery, probes, polling, and post-write readbacks on the runtime client;
  option changes update the policy as well as the scheduler without a reload
- post-write readbacks still wait for a job-start slot; writes do not consume
  read slots or receive extra pacing, and the read limit leaves
  `REQUEST_GAP_SECONDS`, register ranges, retry counts, and write order as
  they were
- setup starts a background refresh as before; startup uses the existing
  compact jobs instead of a full scan of all rooms in one coordinator refresh
- the first pass reads airflow for each supported room first, followed by
  status, temperature, filter, hours, and control settings; each job publishes
  its state immediately, and unattempted groups remain unknown
- pending first-pass jobs alternate with overdue jobs of already-read groups
  when both are available, so slower startup groups progress without blocking
  all periodic airflow reads; first-pass jobs are rescheduled from completion
- before any room has state values, an unsuccessful job reports `UpdateFailed`
  and retries after `TRANSPORT_BACKOFF_START_SECONDS`; failed group health is
  retained internally and published alongside subsequent successful reads
- a job whose group has never been read successfully is retried after
  `UNREAD_GROUP_RETRY_SECONDS` or the current uptime, whichever is longer,
  capped at its normal interval; units can refuse every read for minutes after
  a gateway restart (H-3), and the hourly groups would otherwise stay unknown
  for an hour. The growing delay bounds the extra reads per group to a few.
- shutdown rejects reads still waiting for a rate slot; cancellation releases
  the policy lock without sending the waiting request
- the coordinator plans its own poll ticks: it passes no `update_interval` to
  Home Assistant's `DataUpdateCoordinator`, and every tick (`async_poll`)
  schedules the next one with `async_call_later` from `next_poll_delay`. The
  wake-up time is therefore exact, and a new request rate, a newly enabled
  optional read, or the end of a write readback moves a waiting tick at once
  instead of after the old interval. Earlier releases tuned `update_interval`
  and compensated Home Assistant's whole-second alignment, which relied on an
  internal detail of Home Assistant. The gateway-facing pace is unchanged: the
  transport's read slot and `REQUEST_GAP_SECONDS` still space every request,
  so a tick can only start a request when the request-rate cap allows it.
  Because the integration schedules itself, it also honours what Home
  Assistant's own scheduling did: no tick starts once Home Assistant is
  stopping (entries are not unloaded on stop), and with "Enable polling for
  updates" turned off only the first tick and user-requested refreshes run.
  [HW-9](HARDWARE_BACKLOG.md#t-11-harness-run-on-2026-11-02) checked the
  resulting timing on the gateway: no read broke `1 / rate`, also across live
  rate changes and a reload, so the wake-up needs no extra margin
- the optional gateway values (uptime, UTC clock, device count, RF statistics,
  and the unit list for the new-unit repair issue) are `OptionalGatewayRead`
  instances (`gateway_reads.py`): read in the poll tick under the gateway lock while at least one
  consumer needs them, and cancelled when the last consumer goes away; the
  unit list has an internal consumer and waits five minutes before its first
  read; `coordinator.optional_gateway_reads` exposes them. A failed read is
  retried after `OPTIONAL_READ_RETRY_SECONDS` (60 s), doubling with each
  further failure up to its regular interval, so an hourly value is not lost
  for an hour after one refused request; the 60-second reads are unaffected
- one job can perform several grouped or optional Modbus reads, each subject
  to the shared read limit and the link's minimum `REQUEST_GAP_SECONDS`
- airflow-level writes rely on the normal scheduler for later readback instead
  of forcing an immediate confirmation poll
- M-WRG-S and M-WRG-II flow jobs read current mode/targets from `41100..41102`;
  mode writes use `41120..41122` and commit at `41132`
- a unit that has not answered for `ROOM_SILENT_AFTER_SECONDS` is polled at
  most every `SILENT_ROOM_POLL_SECONDS`, because every unanswered read costs
  timeouts on the shared bus
- every request is retried at most once (see
  [Transport](#transport-home-assistants-shared-modbus-connection)); the
  pymodbus client's default `retries=3` had turned one unanswered register
  into about eight timeouts

Current job groups:

- `flow`: airflow values
- `flow_control`: mode and airflow controls
- `status`: fault, frost, and RF status
- `temperature`: temperatures, plus humidity, CO2, and VOC values in their
  own read-health group `air_quality`, so that a refused air-quality read
  leaves the temperatures available
- `filter`: filter values
- `hours`: operating hours and software version
- `control_settings`: humidity and CO2 settings

Current target intervals:

- `flow` and `flow_control`: `10s`
- `temperature` and `status`: `60s`
- `filter`, `hours`, and `control_settings`: `1h`

These are target intervals, not throughput guarantees: at a low request rate
or with many fallback reads, the serialized link can take longer. Freshness
limits are not relaxed to hide that overload. The startup log motivating this
change is summarized once in
[MELTEM.md](MELTEM.md#home-assistant-restart-on-2026-10-08); the live check for
the revised behavior is part of H-9 in [LIVE_GATEWAY_TESTS.md](LIVE_GATEWAY_TESTS.md).

## Read health and write confirmation

Each supported read group tracks its own last attempt, last successful read,
consecutive failures, and most recent error. Optional Modbus failures preserve
the last cached value, but only affect the health of the group that performed
the read. Non-finite decoded values (`NaN`/`Inf` floats) also preserve the last
cached value and mark their read group as failed. A successful read in another
group does not clear that failure. Out-of-range hardware sentinels such as
`239` or `32767` on humidity, CO2, and VOC registers (`PLAUSIBLE_RANGES`) only
mean that one sensor reports nothing: that field becomes unknown instead of
keeping a value that no longer tracks the room, it is logged at debug level,
and the rest of the group stays healthy. When a sensor of the selected profile
reports such a placeholder `MISSING_SENSOR_ISSUE_AFTER_READS` times in a row
(about three minutes), `missing_sensors.py` raises a non-persistent repair
issue for that unit and sensor; it suggests checking the profile and
disappears with the next real value, on reload, or on restart. Only the
sensors that define a profile (`modbus_client.PROFILE_SENSORS`: both
humidities, extract-air CO2, supply-air VOC) raise one. Supply-air CO2
(`41012`) and extract-air VOC (`41008`) come only from the app's register map
and are offered, disabled by default, on the `O/VOC-AUL` profile, where they
are decoded from the `41006..41013` block that profile reads anyway. A
placeholder there says nothing about the profile, so they neither raise an
issue nor take part in the setup probe's profile detection.
`test_missing_sensors.py` checks that every issue sensor has a translation.
Expected groups are derived from each room's supported entities, so a register
that a device profile does not expose is not reported as a failed read. The
group-to-entity mapping lives in `const.READ_GROUP_ENTITY_KEYS` and
`RefreshPlan.read_groups()`; the client and the coordinator both use it.
Groups with no successful read remain unknown until the existing failure
threshold is reached, unless the unit refused the group with an exception
response: such a group is only retried after a backoff while its entities are
already unavailable, so `read_health.group_stale` reports it at once. Optional
diagnostic groups are the exception to that; a refusal there means the unit
lacks the register. The aggregate `data_health` status is also unknown when
there is only inconclusive read health and no write outcome; a failed attempt
alone is not a healthy reading. Existing confirmed failures and stale values
continue to flag a problem during startup.

Read-only entities are available only while their own group's last successful
read is fresh. Airflow uses a 30-second freshness limit; other groups use three
times their polling interval. Control entities (fans, selects, the intensive
switch) stay available while the unit answers and report `unknown` values
instead, so a command can still be sent. The `data_health` diagnostic binary
sensor exposes per-group read status and write confirmation details without
issuing extra gateway requests; its attributes are excluded from the recorder
because they change on every poll. The Home Assistant system-health page lists
stale groups per unit as plain text, because its dialog does not render nested
values.

A successful Modbus write call confirms only that the write operation was
accepted at the transport/protocol layer. Control entities show the written
value as pending (`level_source: pending` on the fans) until the associated
group has been read after the write, or until the pending window expires.
Only target registers confirm an airflow write; the measured airflow lags
behind and never does. A target derived from measured airflow is only used for
display, not confirmation. A matching readback confirms the write; a different
value is reported as a mismatch, and a failed readback remains unconfirmed.
Cached values from before the write cannot confirm it. A failed write drops any
pending value. Failed, mismatched, or unconfirmed writes flag `data_health` for
`WRITE_HEALTH_RETENTION_SECONDS` and then only remain visible as attributes.

An accepted airflow, operating-mode, or quick-mode command marks earlier
unsettled commands of the other two kinds as `superseded` and clears their
pending displays. This outcome is final and does not flag `data_health`.
Failed replacements do not supersede earlier commands. Intensive and control
setting writes remain independent.

Cancellation while sending is different from a definite failure: the device
may already have applied some or all of the command. The new outcome is
`unconfirmed`, and affected pending overlays are cleared without doing I/O
during cancellation. After an interrupted flow-control write, directional and
operating-mode changes that depend on the old state are refused until a later
successful read of the group the unit's levels come from: the flow-control
read, or, while the mode status map is not fresh, the airflow read
(`coordinator._levels_reported_since`, following `levels.reported_levels`).
Without that fallback a unit that refuses `41100..41102` would stay
uncontrollable until a reload. An explicit balanced or unbalanced command can
still be sent. Cancellation while merely waiting for the gateway lock leaves
the previous state unchanged.

Pending values are dropped when a readback confirms them
(`_confirm_pending_writes`), never as a side effect of reading an entity
state. A pending value that is not confirmed expires through a timer, which
also updates the entities. All overlays (airflow levels, quick mode, and
intensive) only settle against a read of their own group that succeeded after
the write; a matching cached value from before the write leaves them pending.

"After the write" is decided without the wall clock. Each write, overlay,
interrupted flow write, and fan-write fallback keeps the read health its group
had when the write was sent. Reads and writes share the gateway lock, so only
a later read changes that health; a read counts as later exactly when the
group's health differs from that baseline (`read_health.read_since`,
`mode_read_since`, `WriteConfirmation.baseline`). Comparing timestamps instead
refused fan changes after the system clock was set back, until it caught up
again. The freshness limits of the read groups use the monotonic clock as well
(`ReadHealth.last_success_monotonic`, `read_health.read_too_old`), so a forward
jump, such as the first NTP sync of a host without a real-time clock, does not
make every group stale until its next read. Wall-clock times are still used for
the confirmation timeout, the health retention, and the displayed times.

`MeltemModbusClient.read_room` returns the new state together with a
`ReadJobOutcome`: the groups it attempted and recorded, their errors, whether
any request got an answer, and which groups yielded values from the unit
itself. The coordinator judges a job only by that outcome; `RoomState` holds
values and read health only. `read_room_state` returns just the state for
callers that do not need the outcome, such as the gateway tools.

Selecting inactive sensor control is decided under the gateway lock. It is a
no-op only when a fresh mode read already reports off, manual, or unbalanced
operation. Otherwise it writes manual mode using the known airflow, so a stale
manual-mode cache cannot silently discard the command.

The minimum level of a sensor control must not exceed its maximum level;
`async_set_control_setting` refuses such a write with a
`ServiceValidationError` (`control_level_range`) before it reaches the unit. Validation runs under the same gateway lock as
the write, so concurrent changes cannot both pass against an old range.
Accepted settings without a successful readback are used instead of cached
values for the opposite bound. Both values are compared after rounding to
the register step.

M-WRG-S and M-WRG-II mode state is decoded from `41100..41102`. The M-WRG-S
path follows the same map by explicit assumption; live validation is currently
from M-WRG-II. The status block reports intensive mode through its read-side
mode/submode values, so routine polling does not read the write-side
`41120..41124` block.

An intensive status (`0/0`, observed, or `3/227`, community decode) hides the
base mode. The client then keeps the last known operating mode, targets, and
quick mode and only sets `intensive_active`. After a restart during intensive
ventilation these values stay unknown until the override ends. A non-zero
airflow or operating-mode write does not clear the intensive registers, so a
readback that still reports intensive cannot show it: the write is recorded as
`unverifiable`, which does not flag `data_health`. Unlike `confirmed` or
`superseded` it is not final; the first readback after the override ends
confirms or mismatches it. A balanced airflow write needs the `41121` read for
that, so it is only judged within `WRITE_HEALTH_RETENTION_SECONDS` of the
write. Power-off writes (`write_level(0)` and `write_operating_mode("off")`)
and quick-mode writes clear `41123/41124` in the same commit so an active
intensive override cannot keep the fans running after the unit is switched off
(see [HW-1](HARDWARE_BACKLOG.md#w-8-measurement-on-2026-11-02)).

The set-register block `41120..41124` is used for writes; the only read is
the single-register confirmation read of `41121` described under
[Target readback and measured airflow](#target-readback-and-measured-airflow).
`41132` is still written last as the apply latch. Registers written before an
interrupted command stay staged until the next `41132`; the client remembers
them per unit and logs a warning when a later command's commit would also
apply some it does not write again. A register whose write timed out or was
cancelled counts as staged, because the unit may have stored it; one the unit
refused with an exception response does not. Whether that commit really applies them is
open ([HW-8](HARDWARE_BACKLOG.md#hw-8--an-interrupted-mode-command-leaves-staged-registers-for-the-next-commit)).

Write errors reach the UI as translated `HomeAssistantError`s (`exceptions` in
`strings.json`) instead of an "Unknown error". Only Modbus errors and Home
Assistant errors, such as a port conflict, count as a failed write; any other
exception is a programming error and propagates unchanged.

## Directional fan writes

Both fans write through `coordinator.async_set_direction_level`, which resolves
the opposite direction and writes under the gateway lock shared by polls and
all mode writes. Two quick or concurrent commands (for example a scene setting
both fans) therefore build on each other's pending value instead of on the
stale cache. A fan command queued behind a preset or operating-mode change
resolves the opposite airflow after that change and its readback finish, not
before it waits for the bus. Internal `_locked` helpers require the caller to
hold this lock; the write sequences remain unchanged.

While an airflow pair is pending, it also determines whether the unit is off,
balanced, or unbalanced. Starting from off or leaving sensor control therefore
only forces the first command to be balanced; subsequent commands keep the
other pending direction. Restarting after a pending off command still starts
both directions.

Decision rules, in order:

- opposite direction unknown: a non-zero value is written balanced to both
  directions and reported as `last_write_fallback`; switching a single
  direction off is refused with `opposite_airflow_unknown`
- measured opposite airflow within `LEVEL_CONFIRM_TOLERANCE` of the own
  measured airflow counts as one balanced value, so jitter does not force
  unbalanced operation
- levels at most `BALANCED_LEVEL_TOLERANCE` apart, a stopped unit, or leaving
  sensor control with a non-zero value: balanced write
- otherwise: unbalanced write with the opposite direction kept

`fan.turn_on` without a percentage does nothing on a running fan, because
re-sending the measured level would end a running sensor mode.

## Integration-like gateway benchmarks

`tools/benchmark_integration_like.py` and `tools/count_requests.py` share the
setup probe's two-value result. Supported entity keys follow the selected
profile, including a `--profile` override, just as they do in the integration.

An integration-like polling sample fails when a due group reports an error
from that read, even when the runtime client swallowed the Modbus exception.
The errors come from the read's own `ReadJobOutcome`, so errors left over from
an earlier, skipped group do not count toward the sample.

`benchmark_integration_like` uses the integration's request gap and request
rate unless `--gap` or `--rate` says otherwise, and reads its raw register
snapshots through `MeltemModbusClient.unit_for`, so they share the client's
rate limit and read quarantine and cannot pick up a late reply to an
unanswered request. The tool loads the integration modules without Home
Assistant through a small stub; `test_benchmark_runs_without_home_assistant`
runs it in a separate process with Home Assistant blocked, because the test
session itself always has the real package loaded.

Every write experiment attempts its airflow restore on normal completion,
exceptions, or cancellation, including a failed initial write that may have
partially reached the unit. A restore error is reported separately and does
not replace an earlier experiment error. The link stays open until restoration
finishes. This is best-effort airflow restoration, not a replacement for the
full before/after snapshot procedure in `LIVE_GATEWAY_TESTS.md`.

## Register and state caveats

### App presets and settings

The quick mode and intensive codes the integration writes were traced from the
Meltem app, see [MELTEM.md](MELTEM.md#reverse-engineered-app-preset-behavior):

- `LOW` / `MED` / `HIGH`: clear `41123`/`41124`, then `41120 = 3`,
  `41121 = 228` / `229` / `230`, then `41132 = 0`
- intensive ventilation: `41123 = 3`, `41124 = 227`, then `41132 = 0`

Decisions:

- `Abluft` / `Zuluft` are not written as app shortcuts. The app keeps the
  airflows for these buttons on the phone and sends them with every command
  ([APK_ANALYSIS.md](APK_ANALYSIS.md#extract-only-and-supply-only-airflows));
  nothing on the unit exposes them, so the integration would have to guess.
  Both states are set by turning one of the two fans to zero, which uses the
  documented unbalanced write sequence. The shortcut encoding
  `200 + airflow / 10` is still decoded on read, so a shortcut started on the
  unit or in the app stays visible.
- Missing keypad LEDs for `Abluft` / `Zuluft` are not an integration defect;
  the Meltem app leaves them dark as well.
- Settings the app stores on the unit (LOW / MED / HIGH airflows, intensive
  airflow and duration, the supply-only / extract-only airflows of parameters
  42..47) persist locally, but no register for them has been found. They are
  not supported; the reverse engineering is parked, see
  [SETTING_RE_BACKLOG.md](SETTING_RE_BACKLOG.md).
- The values in Meltem's Modbus-KNX document describe KNX objects, not the
  holding registers of the USB gateway, and are not used.

### Target readback and measured airflow

The current-state map at `41100..41102` reports the operating mode and targets;
`41020/41021` show the effective airflow and can lag behind, see
[MELTEM.md](MELTEM.md). Therefore:

- `41101` can provide a status target for display; values are scaled using the
  selected profile's airflow range
- `41020/41021` stay the authoritative current airflow and never confirm a
  write
- balanced airflow writes are confirmed by a single-register read of the exact
  set-side target at `41121` while the write remains eligible for confirmation
- in unbalanced mode the status values `41101/41102` provide supply/extract
  targets; the previous target is kept if the status read fails

W-1 on 2026-10-14 disproved the earlier assumption that `41101` echoes the
raw `41121` target: it reports balanced targets as `200 + airflow / 10`, see
[MELTEM.md](MELTEM.md#target-readback-and-measured-airflow).

The client decodes `41101` as either the raw `0..200` value or the app encoding
`200 + airflow / 10` when the resulting airflow is within the selected profile's
maximum, for display only. It does not use this coarse status value or measured
airflow to confirm a balanced write. While a write is pending, unconfirmed,
mismatched, or unverifiable, and at most for `WRITE_HEALTH_RETENTION_SECONDS`
after it started, the coordinator reads `41121` separately on airflow polls
and scales its exact raw value with the profile range. Keeping mismatches eligible lets a later exact read recover from a transient
stale mode/target read; the time limit stops the extra request once the write
could no longer flag `data_health`. Once `41121` has been read, its exact
value is carried forward across subsequent airflow polls as long as the unit
remains in the same manual or off mode without an active preset and the coarse
status target `41101` does not change, so the displayed fan target does not
jump back to a 10 m³/h step after the confirmation window ends. While
intensive ventilation hides the base mode, the read is skipped, and an
`unverifiable` write is judged by the first exact read after the override
ends, provided that happens within the same window; otherwise it stays
`unverifiable`, which does not flag `data_health`. A value outside
`0..200`, such as a quick-mode code written by the app or keypad, is no
airflow target and leaves the write unconfirmed.
If the set-side read fails, the write is not confirmed and a later airflow
poll may retry it rather than falling back to another source; the first
failure of a streak is logged as a warning, repeats at debug level. Preset
codes `227..230` keep their separate mode meanings. W-17 confirmed this path
on an M-WRG-II unit; see
[W-17 in HARDWARE_BACKLOG.md](HARDWARE_BACKLOG.md#w-17-home-assistant-write-confirmation-on-2026-10-14).

### Readback after mode writes

Operating-mode, quick-mode, and intensive writes read their group back once,
`WRITE_SETTLE_SECONDS` (`0.5 s`) after the commit, while the coordinator still
holds the gateway lock. Control settings wait `CONTROL_SETTING_SETTLE_SECONDS`
(`1.5 s`). A write this readback does not confirm stays `mismatch` or
`unconfirmed`, and the regular polls of its group can still confirm it.
Balanced and unbalanced airflow writes have no immediate readback.

Until `5.0.0` mode writes waited `1.5 s` and read back up to twice, 2.5 s
apart, so a write held the lock for up to about 5.5 s and several quick-mode
commands in a row could leave every unit unpolled for longer than the 30 s
freshness limit. HW-2 measured on six M-WRG-II units that the status map
`41100..41102` shows the committed state at the first read, 36 of 36 times,
and that reads of other units during the settle window change nothing. The
delay was therefore shortened and the second readback dropped (candidates A
and C); `0.5 s` is a margin for units and radio conditions that were not
measured. Releasing the lock during the wait (candidate B) is backed by the
same data but buys at most that half second. Control settings exist only on
`-F` and `-FC` units, none of which was available, so their delay is
unchanged. See
[HW-2](HARDWARE_BACKLOG.md#decision-and-validation-on-2026-11-02).

### Per-unit availability on read failures

When the gateway is unplugged or otherwise unavailable, the integration keeps
the previous entity state in memory for a short while and logs each failed
read job at debug level. This avoids flapping on transient outages.

Beyond that grace period the entities of an affected unit are marked
unavailable rather than showing frozen values:

- before the first successful state read, room entities are unavailable rather
  than accepting commands built from unknown fallback values
- a unit is considered unavailable after `ROOM_UNAVAILABLE_AFTER_FAILURES`
  consecutive failures, or after `ROOM_SILENT_AFTER_SECONDS` without any
  successful read, even if the gateway itself still answers
- silent units are polled less often so they do not starve the others
- units that still respond keep working independently
- polling backs off progressively while the gateway stays unreachable, that is
  after timeouts or lost connections; a unit that refuses reads with Modbus
  exception responses still proves the link works and does not start a backoff
- writes fail until communication is restored

A unit's change to unavailable and its recovery are each logged once at info
level. The check runs after every read job of that unit, so availability
queries from entities, diagnostics, or system health have no side effects. A
job whose attempted primary read groups all swallowed their Modbus errors
still records a failed group (`read_health.job_read_failure`); groups skipped
by component backoff do not mask failures from the groups actually attempted.
A partially failed group still keeps the room reachable if it delivered a usable
value. The job outcome separately tracks actual attempts and valid responses,
including exception responses before a retry. This avoids classifying the whole
gateway as unanswered merely because another block in the same group timed out.
It also recognizes successful reads when a different block in that group is
paused, without rewriting the group's cached read health. Bridge-local RF
registers (`40100`, `40101`, `40103`, `40104`, `40109`) alone do not clear a
room's failure streak.
Once any room has data, a job with no attempted groups neither increments nor
clears the transport failure streak. With no room data at all, the startup
failure still backs off. Bridge-local `401xx` (`BRIDGE_RF_REGISTERS`)
responses do not count as a unit read answer in `TransportPolicy`, so a silent
unit cannot appear reachable through gateway status or RF diagnostic reads.

The most recent failed job of each unit is kept with its time
(`coordinator.last_room_errors`) and shown in the diagnostics download and on
the system-health page. `last_job_error` only reflects the most recent job of
any unit, so with several units a unit that keeps failing would show there
only now and then.

### Listener updates per unit

A read job changes the state of one unit only, so the listener update after it
skips the entities of every other unit (`coordinator.room_updated`); gateway
entities always update. An update after a failed coordinator update, and
updates from overlays and write outcomes, still reach every entity. Diagnostics
that age without a read, such as connectivity and the last Modbus answer, share
one 10-second timer (`async_add_diagnostic_listener`) instead of one per
entity.

### Temperatures are not identical conceptually

Even if values may sometimes look the same on specific models:

- exhaust air temperature
- extract air temperature
- supply air temperature
- outdoor air temperature

represent different airflow positions in the system.

Do not assume identical semantics just because some models expose identical
values in practice. The gateway swaps `41000` and `41004`
([MELTEM.md](MELTEM.md#temperature-register-quirk)); the field mapping of
`Temperatures` in `device/components.py` maps the logical sensors to the
values actually seen.

## Profile detection

The integration can infer sensor capabilities reasonably well:

- humidity present -> `-F` or better
- CO2 present -> `-FC` or better
- VOC present -> `O/VOC-AUL`

Current limitation:

- the integration cannot reliably distinguish `M-WRG-S` from `M-WRG-II`
  automatically

The product registers `40002` / `40011` / `40021`
([MELTEM.md](MELTEM.md#product-registers-and-readable-islands)) are the best
current path to improve this. The [app catalog](APP_CATALOG.md#product-entries)
provides a matching M-WRG-II identifier and an unverified M-WRG candidate.
The integration displays these as hints and device model IDs but does not use
them to select a profile.

That means:

- capability auto-detection is possible
- exact series selection still requires user confirmation

## Why the series is not auto-detected by forcing max airflow

A theoretical idea is:

- drive the fan to maximum
- observe whether it tops out at `97 m3/h` or `100 m3/h`

This is intentionally not implemented automatically.

Reasons:

- it would be intrusive and noisy
- it would visibly change the user's ventilation state during setup
- it is not a safe or polite default behavior

If implemented in the future, it should be:

- opt-in
- clearly labeled
- treated as a diagnostic action

## Model-specific airflow scaling

The UI works in `m³/h`, not in raw Modbus level units.

Current mapping:

- `M-WRG-II`: max airflow `100 m³/h`
- `M-WRG-S`: max airflow `97 m³/h`

Writes convert from UI airflow to raw Modbus values:

- raw register range is `0..200`
- conversion is series-dependent

## Setup flow notes

Important setup behavior:

- the connection type and endpoint are editable; USB discovery still
  pre-fills the serial device path
- users need to select the exact model profile manually
- setup previews are best-effort only
- the setup probe is intentionally minimal and reads product/capability
  registers only
- the config, options, and subentry flows share discovery, probing, and the
  profile form in `flow_helpers.py`

Known UX caveat:

- previews may be missing if the setup probe times out or a register does not
  answer quickly enough

## Config entries, units, and profile changes

Since config entry version `2.1` (current: `2.2`), the entry data holds the gateway connection
(a serial port for existing entries, or a transport type, host and TCP port);
every unit is a config subentry of type `unit` (`units.py`). Its title is the
unit's initial name, its unique ID is the Modbus address, and its data holds
the room key, address, profile, and the identity values of the setup probe.
Home Assistant shows each unit as its own entry under the gateway, with
**Add unit** (`subentry_flow.MeltemUnitSubentryFlow.async_step_user`) and
**Reconfigure** for the profile (`async_step_reconfigure`). The options flow
(`options_flow.py`) keeps the request rate and **Scan for new units**, which adds, updates, and removes unit subentries in one
step. Before it removes units the gateway no longer reports, the
`confirm_remove` step names them; closing the dialog keeps them. Deleting a
unit subentry lets Home Assistant remove its device and
entities.

The entities and the device of a unit belong to its subentry: the platforms
add them per unit with `config_subentry_id` (`entity.async_add_room_entities`).
Entity unique IDs are unique per integration, not per entry, so they include
the entry ID (`identifiers.room_entity_unique_id`); gateway entities already
did. Device identifiers stay `(DOMAIN, room key)` plus the app setup code,
because Home Assistant `2026.11` keeps device identifiers unique per config
entry. Units of a second gateway therefore no longer collide with the first;
lifting `single_config_entry` is a separate product decision.

An update listener reloads the entry when the gateway connection or unit subentries
differ from what setup used (`_setup_snapshot`). A new request rate applies
without a reload, and a rescan that changes several units schedules one reload.
Flows that change a loaded entry therefore finish with `async_update_and_abort`
and leave the reload to the listener; for an entry that is not loaded they
reload directly.

Setup never probes the gateway for entity metadata:

- each unit's entities follow from its profile alone, derived on load, so
  entries from older releases gain new entities without a rewrite
- `entity.room_has_entity` is the only rule for which entity a unit has; the
  platforms create exactly those, and setup removes registry entries of every
  other one (`_async_remove_unsupported_entities`), so a profile with fewer
  capabilities loses its extra entities and user settings of the remaining
  ones survive reloads
- one-time schema changes run in `async_migrate_entry` (`migration.py`): `1.2` renamed the
  airflow-only `airflow_data_stale` entity to `data_health`, `1.3` moved the
  request rate to the options, `1.4` moved identity values out of the preview,
  and `2.1` moved every unit into a subentry, gave unit entities entry-scoped
  unique IDs, and moved the entities and devices to their subentries with all
  user settings; entity keys that releases before `4.0.0` stored with a unit
  are dropped then. `2.2` renamed the unit entity key `rf_fault_status` to
  `device_fault` (`40103`, named after the Meltem app) and moves existing
  entities to the new unique ID with their entity ID and settings
- devices of units no longer configured are removed on setup; unit devices are
  linked to the gateway device via `via_device_id`

A fresh setup should therefore not be needed for normal profile changes or
upgrades. Remove and re-add the config entry only when its stored data is
actually corrupt or when debugging a setup problem that cannot be reproduced
otherwise. Removing only the gateway device does not reset the config entry.

The gateway connection is changed with the reconfigure flow, which checks the
new endpoint before saving it. Network endpoint identity uses the normalized
hostname or IP address and port; switching transports preserves the entry ID.

A unit device also carries its app setup code as an identifier
(`identifiers.room_identifiers`). When units change addresses, for example
after being paired to the gateway again, a rescan stores each code with its new
address. Setup then removes codes a device no longer reports before the
platforms load, because Home Assistant rejects device info whose identifiers
belong to another device and drops every entity of that unit. A rescan also
drops a stored code of a unit it could not probe when another unit now reports
that code, and a code stored for several units identifies none of them until
the next rescan. A rescan reserves the room keys of all matched units before it
names new ones: a new unit at a moved unit's old address gets a suffixed key
instead of taking over the moved unit's subentry, device, and entities.

### Repair issues

- `new_units.py`: the coordinator reads the gateway's unit list about five
  minutes after startup and then hourly, as an `OptionalGatewayRead` without an
  entity consumer, and raises `new_units_found` while the gateway reports
  addresses that have no unit subentry. The profile cannot be detected
  reliably, so units are never added without the user. This read uses
  `discover_gateway_nodes(strict=True)`: a check the gateway does not answer
  raises instead of returning an empty list, which would delete the issue and
  with it the user's "Ignore".
- `missing_sensors.py`: see
  [Read health and write confirmation](#read-health-and-write-confirmation).

Both are non-persistent and linked to the config entry, so Home Assistant
removes them with the entry. Unloading the entry keeps them: deleting an issue
would also delete the user's "Ignore", and reloads are frequent (every unit
change or rescan). Instead, the first unit-list check after a start updates or
removes the new-unit issue, and `MissingSensorIssues.restore` deletes
missing-sensor issues of units or sensors that are no longer configured and
takes over the active ones until a reading decides about them.

## USB discovery

The current USB matcher is based on real hardware observations:

- `vid`: `10AC`
- `pid`: `010A`
- `manufacturer`: `Honeywell`
- `description`: `Modbus`

This is good enough for development, but it may still match a generic USB/Modbus
bridge in some environments.

## Important files

The file overview is in `AGENTS.md` under "Important files".

## Tests

The test suite runs against a real Home Assistant test environment provided by
`pytest-homeassistant`, which requires Python 3.14. Modbus
traffic runs against `modbus_connection.mock.MockModbusConnection`, an
in-memory register bank, so the request shapes, the retry policy, and the
decoding are tested together. It covers the Modbus layer, the coordinator,
and every entity platform, including:

- request parity with the blocks the pymodbus client read
- the retry policy: one retry, the quiet window before recycling the link,
  gateway errors
- setup and unload through Home Assistant's real `modbus` integration on an
  in-memory link, including the release of the port
- Home Assistant fan, number, select, and switch services through the entity,
  coordinator, and in-memory link, including successful readback and rejected
  writes, plus a lost response after an applied write
- gateway-backed discovery via `43901` / `43902..`
- minimal setup-time capability probing
- balanced airflow derivation and per-profile scaling
- target-level scaling during room-state reads
- `NaN` and out-of-range sentinel filtering for temperature and air-quality reads
- scheduler job construction and due-job selection
- coordinator write-then-refresh behavior, optimistic state, transport backoff
- mixed read failures and responses within and across groups, skipped backed-off
  registers, recovery, and read-health freshness and availability boundaries
- a shared-link outage followed by per-room `0x05` responses and staggered
  recovery, using a controlled clock and Home Assistant entity states
- setup metadata repair, profile-aware entity cleanup, cancellation during
  setup, and serial unload synchronization, including a queued write, shutdown
  errors, and persisted diagnostics
- interrupted multi-register writes, including lost responses after the write
  was applied, cancellation, and a complete subsequent write sequence
- control-setting range and manufacturer-step normalization
- config-flow helper functions for defaults and room mapping
- the unit subentry flows, the migration to subentries, and the reload on unit
  changes
- entity creation per model profile across all platforms, and that every unit
  entity keeps its registry entry across reloads
- snapshots of every entity and of the diagnostics download
  (`test_snapshots.py`; regenerate with `pytest --snapshot-update` after an
  intended change and review the diff)
- the link settings in `tools/_link.py` against `const.py`

On POSIX, the separate RTU protocol tests connect the real tmodbus client to a
local pseudo-terminal simulator with separate per-unit register maps. They
exercise reads, writes, unknown registers, exception frames, fragmented,
missing, truncated, corrupted, delayed, and mismatched responses, retries,
and reconnection. Home Assistant fan service tests also run through its shared
Modbus integration and the real RTU client to the simulated serial device.
The tests exercise the local read quarantine: a read during the pause sends no
request, and late replies that arrive during the pause are discarded before
the next request. A late reply that arrives only after the pause, while the
next request of the same length is open, would still be accepted; the tests do
not cover that case, which the pause can make less likely but not prevent.
They do not prove safety across reloads or for other holders of the
shared connection, as described above.
A write whose reply was lost may likewise have been applied more than once.
These tests do not establish that repeated writes are harmless on physical
hardware or that the simulated post-write readback models the physical latch.
They are skipped on Windows. Neither simulator establishes physical gateway
behavior; live validation and its safety rules remain in
[HARDWARE_BACKLOG.md](HARDWARE_BACKLOG.md) and
[LIVE_GATEWAY_TESTS.md](LIVE_GATEWAY_TESTS.md).

The platform tests use `common.mock_coordinator()`, a `create_autospec` of
the coordinator, so a renamed or removed coordinator member fails them. The
scheduler tests drive single steps of a real coordinator through the helpers in
`common.py` (`run_job`, `run_tick`, `skip_startup`, `allow_next_read`), the one
place that reaches into its internals for that.

CI also runs `mypy` (configured in `pyproject.toml`) against the Home
Assistant version the test harness installs, Pyright on `custom_components`, `tests` and `tools`,
and Markdownlint.

See `CONTRIBUTING.md` for environment setup, the test commands, and the
Windows-specific workarounds.

## Suggested debugging workflow

If something breaks:

1. Verify the integration loads at all.
2. Check logs:

   ```bash
   ha core logs | grep meltem_ventilation
   ```

3. Confirm scan results.
4. Confirm the correct connection type and endpoint are configured.
5. Confirm the correct model profiles were chosen.
6. Only then investigate individual register behavior.

Useful local tools in this repo (async, on `modbus-connection` with tmodbus
since `4.0.0`; any earlier commit has the pymodbus versions):

- `tools/probe_airios_bridge.py`
  confirms whether the bridge path on `device_id=1` responds and returns the
  configured unit list
- `tools/benchmark_gateway.py`
  compares different request patterns, gaps, and block-read candidates against
  a locally attached gateway; the gap is slept outside the measured latency
- `tools/benchmark_integration_like.py`
  drives the integration's own client through scheduler-like loops and write
  experiments
- `tools/raw_requests.py`
  sends single requests to one unit and prints the answer or exception class
  with its latency
- `tools/count_requests.py`
  runs the integration's client with refresh plans and counts the requests per
  unit, including retries

The tools run without Home Assistant and keep their own copy of the link
settings in `tools/_link.py`; `tests/components/meltem_ventilation/test_tools.py` keeps it in line with
`const.py`. Run them as modules from the repository root, e.g.
`python -m tools.raw_requests --help`. `tools/write_registers.py` stops at the
first failed write unless `--keep-going` is given, so a trailing apply never
activates a half-written sequence.

## Things to be careful with in future changes

- Do not remove request pacing unless you have tested the gateway thoroughly.
- Do not make polling user-configurable again without a strong reason.
- Be careful when touching scan timing; it can affect how many devices are found.
- Use `41100..41102`, not the write-side `41120..41122`, for current mode state.
- Keep `M-WRG-S` and `M-WRG-II` airflow scaling separate.
- Compute entity state in plain `@property` overrides, as Home Assistant Core
  does. Home Assistant types these attributes as `cached_property`, so the
  entity modules disable Pyright's `reportIncompatibleVariableOverride`; a
  `cached_property` override would freeze the value between coordinator
  updates.
