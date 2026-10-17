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

**Read health** stays available even when a unit or individual read group is
unavailable. It turns on when a read group is stale (three consecutive failed
read jobs or its stale interval has elapsed) or while a recent command has
failed, mismatched, or remained unconfirmed. Its attributes show details per
group and for recent commands.

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
