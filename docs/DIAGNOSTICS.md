# Polling, read health, and diagnostics

## Polling and refreshes

The integration polls the gateway; it does not receive pushed updates. The
request-rate setting is a shared cap on individual Modbus read requests across
all configured units. Retries and post-write readbacks count toward it.
Poll-job starts are also spaced at the configured rate, so this is not an
update interval for every entity. It defaults to **4 read requests per second**
and can be set from **0.5 to 10 requests per second** in steps of 0.5. Write
sequences retain their separate hardware timing.

Each unit's values are refreshed in groups:

| Data group | Normal refresh interval |
| --- | ---: |
| Airflow and operating mode | 10 seconds |
| Status flags (error, frost protection, RF communication) | 60 seconds |
| Temperatures and environment sensors | 60 seconds |
| Filter status and days until filter change | 1 hour |
| Operating hours and firmware version | 1 hour |
| Humidity and CO2 control settings | 1 hour |

The scheduler staggers jobs across units and processes due jobs sequentially.
At startup, values appear progressively rather than in one snapshot. More
configured units, a lower request-rate cap, or transport retries can delay the
initial scan and later refreshes; the intervals above are targets, not
guaranteed delivery times.

## Read health and availability

After three consecutive transport failures (timeouts or lost connections),
polling backs off from 5 seconds and increases up to 60 seconds. A successful
read resumes the normal schedule.

Airflow data is considered stale after 30 seconds without a successful read;
other groups become stale after three times their normal interval. Read-only
sensors and control-threshold numbers become unavailable when their own read
group is stale or has failed three times in a row. Fans, mode selectors, and
the intensive switch stay available as long as the unit itself answers, so
commands can still be sent. A unit as a whole is marked unavailable after
three consecutive failed read jobs or more than 120 seconds without a
successful unit response.

## Diagnostic entities

**Days until filter change** and **Read health** are enabled by default.
**Operating hours**, **RF communication status**, **Modbus slave ID**, and
**Modbus device path** remain disabled by default. Firmware and hardware
details are shown on the device card instead of as separate version sensors.

Each unit also provides **Reachable** and **Last command status**, both enabled
by default, and an optional **Last successful update** timestamp. These
diagnostics remain visible during an outage and refresh locally every 10
seconds without extra Modbus requests. **Reachable** is unknown before the
first read attempt, then follows the unit availability rules above. **Last
successful update** is the latest successful supported read group for that
unit, not the time another unit was polled. It stays at the last successful
time during an outage; it does not promise that every group is current.

**Last command status** is unknown before the first recorded write. It follows
the most recently started command, including transport failures, and exposes
`write_key`, `started_at`, `expected_value`, and `actual_value` as attributes.
Commands rejected by input validation before reaching the transport are not
recorded. The states are:

| State | Meaning |
| --- | --- |
| Pending | Accepted by the transport; waiting for readback |
| Confirmed | A successful readback confirmed the requested value |
| Readback differs | A successful readback differs from the requested value |
| Not confirmed | No confirmation within 30 seconds |
| Not verifiable | The current state, such as an intensive override, prevents verification |
| Failed | Sending the command failed |
| Superseded | Replaced by a newer command |

The last outcome stays visible until another command or an integration reload;
it is not cleared after the ten-minute Read health retention window. A local
refresh can take up to 10 seconds to show the 30-second confirmation timeout.

**Read health** stays available even when a unit or individual read group is
unavailable. It turns on when a read group is stale (three consecutive failed
read jobs or its stale interval has elapsed) or while a recent command has
failed, mismatched, or remained unconfirmed. Its attributes show details per
group and for recent commands.

### Gateway diagnostics

The gateway device provides these overview and diagnostic entities:

| Entity | Meaning | Enabled by default |
| --- | --- | --- |
| Reachable | The gateway replied within the last 30 seconds | Yes |
| Time since last response | Seconds since any valid Modbus response | No |
| Consecutive timeouts | Consecutive unanswered or invalid requests | No |
| Link reconnections | Automatic link recoveries since this integration entry was loaded | No |
| Available units | Configured units currently providing usable data | No |
| Unavailable units | Configured units currently unavailable | No |
| Stale read groups | Supported read groups with stale data or three consecutive failures | No |
| Units with errors | Count of configured units reporting a device error | Yes |
| Units needing filter changes | Count of configured units reporting a due filter change | Yes |
| Total supply air flow | Sum of measured supply airflow in m³/h | Yes |
| Total extract air flow | Sum of measured extract airflow in m³/h | Yes |
| Mean response time | Mean successful request duration in milliseconds over the last 5 minutes | No |
| Request success rate | Successful attempts as a percentage of all attempts over the last 5 minutes | No |
| Uptime (experimental) | Raw gateway uptime register, in seconds | No |

Except for the optional uptime read described below, these entities use
existing runtime data and do not send additional Modbus requests or discover
units. Enabled entities refresh locally every 10 seconds as well as on
coordinator updates, including during polling backoff. Diagnostics remain
visible when communication fails; uptime becomes unavailable when stale.

**Reachable** detects recent gateway responses, not whether every unit behind
it is reachable. A valid Modbus error response also proves that the link
answered. Before the first response, this entity and **Time since last
response** are unknown. Once a response has been received, **Reachable** turns
off after more than 30 seconds without another response, with up to 10 seconds
of local refresh delay.

The unit counts follow the same availability rules as the unit entities and
always sum to the number of configured units. Cached state alone does not make
a unit available. **Stale read groups** excludes unsupported and still-unknown
groups, as well as write confirmations. It stays unknown until at least one
group's health is known. Its `affected_units` attribute maps unit keys to their
stale groups; this detail is excluded from the recorder.

**Link reconnections** counts automatic serial-link recovery attempts, not
gateway hardware restarts. Both transport counters reset when the integration
entry is reloaded; a valid answer also clears **Consecutive timeouts**.

### Faults, maintenance, and airflow totals

The error and filter counts include an `affected_units` attribute mapping unit
keys to configured names. All four overview sensors expose `complete` and
`unknown_units`. A missing, unavailable, or stale required value makes the
total unknown, not zero or a partial sum. Known affected units remain listed
even when the overall count is unknown. A known zero airflow is included.
These attributes are excluded from the recorder.

The totals use only configured units and their supported, available read
groups; they do not discover other gateway nodes. They combine the most recent
usable measured readings, not fan targets or optimistic writes, and are not
a simultaneous measurement of every unit. **Airflow balance** on each unit is
measured supply minus extract airflow in m³/h: positive means more supply,
negative means more extract. Both directions must be known and the flow group
must be available. This is an ordinary measurement, not a command or diagnostic.

### Communication quality

The rolling five-minute window counts actual Modbus request attempts, including
retries and writes. A timeout followed by a successful retry counts as one
failed and one successful attempt. A valid Modbus error reply proves gateway
connectivity but counts as a failed operation. Cancellation and attempts refused
because the client is closed are excluded.

The mean response time uses successful attempts only and excludes waiting for
the gateway lock, read-rate limit, and retry delays. Both sensors expose
`sample_count` and `window_seconds`. With no samples the success rate is
unknown; with no successful samples the mean response time is unknown. Samples
expire even without further traffic. The window resets on integration reload
and is bounded to 10,000 attempts.

### Experimental gateway uptime

Enabling **Uptime (experimental)** adds one read of register `41019` on gateway
address `1` about every 60 seconds. The read shares the gateway lock and read
rate limit; scheduling, retries, or backoff may delay it. Disabling the entity
stops these reads, and an uptime failure does not make room data unavailable.
After more than 180 seconds without a successful uptime read the sensor becomes
unavailable.

This is one raw 16-bit register, not a locally extrapolated timer or cumulative
counter. Restart behavior, wraparound, and whether another register extends the
counter still need hardware verification. Do not use it to detect restarts yet.
It is deliberately disabled by default and uses a measurement state class,
not `total_increasing`.

## A single unit is unavailable

After 120 seconds without a successful read, airflow polling for that unit is
spaced to 60 seconds to reduce its load on the shared link; other units
continue to be polled. The unit becomes available automatically after a
successful read. Check its power supply and radio link to the gateway. The
**Read health** attributes and diagnostics download show which reads failed.

## Serial port shared with another integration

The serial link is handled by Home Assistant's Modbus integration, so other
integrations can use the same port. They must use the same serial settings
(19200 baud, 8E1); otherwise setup reports that the port is already in use.
The longest timeout any of them asks for applies to all requests on that
port, so a long timeout elsewhere slows down this integration when a unit
does not answer.

## Logs

In Home Assistant:

1. Open **Settings**.
2. Open **System**.
3. Open **Logs**.

With the `Terminal & SSH` add-on:

```bash
ha core logs | grep meltem_ventilation
```

The serial link itself is handled by Home Assistant's Modbus integration. To
see its traffic, enable debug logging in `configuration.yaml`:

```yaml
logger:
  logs:
    custom_components.meltem_ventilation: debug
    modbus_connection: debug
    tmodbus: debug
```
