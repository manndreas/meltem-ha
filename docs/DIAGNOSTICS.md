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
| RF last-seen age, RSSI, fault status, and value-error status | 60 seconds |
| Temperatures and environment sensors | 60 seconds |
| Filter status and days until filter change | 1 hour |
| Device and fan operating hours, firmware version | 1 hour |
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

**Fan operating hours**, **RF last-seen age**, and **RF RSSI** are also disabled
by default. Device operating hours and fan operating hours are separate
counters, both in hours. RF last-seen age reports the gateway's age value in
seconds since the last RF reception, sampled about once a minute; it is not
extrapolated locally. RSSI is decoded as a signed 16-bit value. Its displayed
unit **dBm is provisional**: the upstream register map specifies the signed
encoding but does not establish the physical unit for this register. See the
[hardware observations](dev/MELTEM.md) for the measured values.

These room diagnostics use separate register reads and read-health groups;
an unsupported diagnostic does not invalidate the existing measurements or
controls. The room reads follow the normal supported-profile polling even
when their entities are disabled; hiding them does not stop those reads.

**RF fault status** (`40103`) and **Value error** (`40104`) are disabled by
default. They use the upstream Airios interpretation: fault status is off for
`0`, on for nonzero values except `0xFFFF`, and unknown for `0xFFFF`. Value
error is off for `0`, on for `1`, and unknown for other values. A later unknown
value clears the previous binary state instead of retaining a healthy value.
RF fault status is distinct from the existing **Device error** (`41016`);
it is not specifically labelled as a fan failure. Value error indicates one
or more invalid values, not which sensor failed. These meanings have not been
independently confirmed by inducing faults on Meltem hardware.

Each unit also provides **Reachable**, enabled by default, and an optional
**Last successful update** timestamp. These diagnostics remain visible during
an outage and refresh locally every 10 seconds without extra Modbus requests.
**Reachable** is unknown before the
first read attempt, then follows the unit availability rules above. **Last
successful update** is the latest successful supported read group for that
unit, not the time another unit was polled. It stays at the last successful
time during an outage; it does not promise that every group is current.

**Read health** stays available even when a unit or individual read group is
unavailable. It turns on when a read group is stale (three consecutive failed
read jobs or its stale interval has elapsed) or while a recent command has
failed, mismatched, or remained unconfirmed. Its attributes show details per
group and for recent commands.

### Gateway diagnostics

The gateway device provides these overview and diagnostic entities:

| Entity | Meaning | Enabled by default |
| --- | --- | --- |
| Modbus connection | The gateway replied within the last 30 seconds | Yes |
| Last Modbus response | Time of the most recent valid Modbus response | No |
| Connected device count | Number of devices registered with the gateway | Yes |
| RF messages sent this hour | Gateway RF messages sent during the current hour | No |
| RF messages sent previous hour | Gateway RF messages sent during the previous hour | No |
| RF load this hour | Gateway-reported current-hour RF load in percent | No |
| RF load previous hour | Gateway-reported previous-hour RF load in percent | No |
| Total supply air flow | Sum of measured supply airflow in m³/h | Yes |
| Total extract air flow | Sum of measured extract airflow in m³/h | Yes |
| Total airflow delta | Total supply minus total extract airflow in m³/h | Yes |
| Mean response time | Mean successful request duration in milliseconds over the last 5 minutes | No |
| Successful Modbus requests (5 min) | Successful attempts as a percentage of all attempts over the last 5 minutes | No |
| Uptime | Gateway uptime, in seconds | No |
| UTC clock | Gateway UTC time as a timestamp | No |

Except for the connected-device count, RF statistics, uptime, and UTC clock reads described
below, these entities use existing runtime data and do not send additional
Modbus requests or discover units. Runtime diagnostics refresh locally every
10 seconds as well as on coordinator updates, including during polling backoff.
They remain visible when communication fails; the separately read count,
RF statistics, uptime, and UTC clock become unavailable when stale.

**Modbus connection** detects recent gateway responses, not whether every unit behind
it is reachable. A valid Modbus error response also proves that the link
answered. Before the first response, this entity and **Last Modbus response**
are unknown. Once a response has been received, **Modbus connection** turns
off after more than 30 seconds without another response, with up to 10 seconds
of local refresh delay.

**Last Modbus response** records the Home Assistant host's UTC time when any
valid read or write response arrives, including Modbus error replies. It stays
unchanged through timeouts and outages, and resets to unknown on integration
reload until another reply arrives. This is not the gateway's own **UTC clock**.
Home Assistant displays the timestamp in the user's time zone.

Transport timeout and recovery counters remain available in the diagnostics
download, but no longer have their own gateway entities. Per-unit device-error
and filter-change entities remain unchanged.

### Airflow totals

The three gateway airflow aggregates expose `complete` and `unknown_units`. A missing,
unavailable, or stale required value makes the total unknown, not zero or a
partial sum. A known zero airflow is included. These attributes are excluded
from the recorder.

The totals use only configured units and their supported, available read
groups; they do not discover other gateway nodes. They combine the most recent
usable measured readings, not fan targets or optimistic writes, and are not
a simultaneous measurement of every unit. **Airflow balance** on each unit is
measured supply minus extract airflow in m³/h: positive means more supply,
negative means more extract. Both directions must be known and the flow group
must be available. This is an ordinary measurement, not a command or diagnostic.
The gateway **Total airflow delta** is calculated from its two complete airflow
totals using the same sign convention; positive means net supply airflow.

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

### Connected device count

**Connected device count** reads gateway register `43901` on address `1` about
every 60 seconds while enabled. It counts devices registered with the gateway,
including ones not configured in Home Assistant. It is not an instantaneous
count of reachable RF devices and does not indicate individual room health.
Zero is a valid count.

The sensor is enabled by default. Its reads share the gateway lock and
read-rate limit; scheduling, retries, or backoff may delay them. Disabling the
entity stops the reads. A failed read preserves the last count until it becomes
unavailable after more than 180 seconds without a successful read. A count-read
failure does not make room entities unavailable.

### Gateway RF statistics

Enabling any of the four RF statistics entities adds one shared read of
`42100..42105` on gateway address `1` about every 60 seconds. Disabling the
last enabled entity stops these reads. The read shares the gateway lock and
request-rate cap and cannot make room entities unavailable. A read failure
preserves the previous observation until it expires after more than 180
seconds without a successful RF statistics read.

The sent-message counts are unsigned 16-bit hourly buckets, not lifetime
counters or counts of Home Assistant Modbus requests. Both use `measurement`,
not `total_increasing`; the previous-hour bucket can fall as well as rise.
The exact bucket-rollover timing has not been isolated on Meltem hardware.

RF load uses low-word-first Float32 values displayed directly as percentages,
following pyairios without multiplying by 100. Four decimal places keep small
loads visible. Non-finite or out-of-range percentages become unavailable
without invalidating the counters or the other load value. The physical
percentage calibration has not been independently verified for this Meltem
gateway. Additional temperature/mode register reads correlated with more RF
traffic in the live tests, whereas repeated airflow-only reads did not reliably
do so. These values do not prove that every room measurement is fresh; see the
[hardware observations](dev/MELTEM.md).

### Gateway uptime

Enabling **Uptime** adds one read of registers `41019..41020` on gateway address
`1` about every 60 seconds. The 32-bit value is in seconds, with the low word at
`41019`. The read shares the gateway lock and read-rate limit; scheduling,
retries, or backoff may delay it. Disabling the entity stops these reads, and an
uptime failure does not make room data unavailable. After more than 180 seconds
without a successful uptime read the sensor becomes unavailable.

The sensor reports the gateway's uptime value without local extrapolation. It
is deliberately disabled by default and uses a measurement state class, not
`total_increasing`.

### Gateway UTC clock

Enabling **UTC clock** adds one read of registers `41015..41016` on gateway
address `1` about every 60 seconds. The value is a low-word-first 32-bit UTC
Unix timestamp, exposed as a normal Home Assistant timestamp. Home Assistant
displays it in the user's time zone; no separate gateway local-time sensor is
created. It reports the sampled gateway time, not the computer's current time,
and does not advance locally between reads.

The clock is disabled by default, shares the gateway lock and read-rate limit,
and is independent of uptime and room polling. Disabling the entity stops its
reads. A failed read preserves the last observation until it becomes unavailable
after more than 180 seconds without a successful clock read. It cannot make
room entities unavailable. The upstream unknown value `0xFFFFFFFF` is rejected.

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
