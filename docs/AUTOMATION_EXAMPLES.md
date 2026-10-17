# Automation examples

Replace example entity IDs with the IDs from your Home Assistant instance.

## Run intensive ventilation while a room is occupied

This starts the Meltem **Intensive ventilation** switch when occupancy is
detected and turns it off after the room has been clear for 10 minutes.

```yaml
alias: Ventilate while the bathroom is occupied
description: Run Meltem intensive ventilation while occupancy is detected.
triggers:
  - trigger: state
    entity_id: binary_sensor.bathroom_occupancy
    to: "on"
    id: occupied
  - trigger: state
    entity_id: binary_sensor.bathroom_occupancy
    to: "off"
    for: "00:10:00"
    id: unoccupied
conditions: []
actions:
  - choose:
      - conditions:
          - condition: trigger
            id: occupied
        sequence:
          - action: switch.turn_on
            target:
              entity_id: switch.meltem_bathroom_intensive
      - conditions:
          - condition: trigger
            id: unoccupied
        sequence:
          - action: switch.turn_off
            target:
              entity_id: switch.meltem_bathroom_intensive
mode: single
```

The unit's intensive runtime configured in the Meltem app still applies, so it
may end the override before the occupancy sensor has been clear for 10 minutes.

## Set quick mode on a schedule

This example uses the `Low` quick mode overnight and `Medium` during the day.
Set the airflow values for these quick modes in the Meltem app; the integration
cannot read those values over Modbus.

```yaml
alias: Set Meltem quick mode by time
description: Use Low overnight and Medium during the day.
triggers:
  - trigger: time
    at: "22:00:00"
    id: night
  - trigger: time
    at: "07:00:00"
    id: day
conditions: []
actions:
  - choose:
      - conditions:
          - condition: trigger
            id: night
        sequence:
          - action: select.select_option
            target:
              entity_id: select.meltem_bathroom_quick_mode
            data:
              option: low
      - conditions:
          - condition: trigger
            id: day
        sequence:
          - action: select.select_option
            target:
              entity_id: select.meltem_bathroom_quick_mode
            data:
              option: medium
mode: single
```

The option values in YAML are lowercase (`low`, `medium`); they correspond to
the `Low` and `Medium` choices shown in Home Assistant.

## Notify when Read health stays on

The **Read health** binary sensor turns on when a read group is stale or a
recent command has failed, mismatched, or remained unconfirmed. This example
waits five minutes before creating a persistent notification to avoid
notifying on brief problems.

```yaml
alias: Notify when Meltem read health needs attention
description: Alert if Meltem read health stays on for five minutes.
triggers:
  - trigger: state
    entity_id: binary_sensor.meltem_bathroom_read_health
    to: "on"
    for: "00:05:00"
conditions: []
actions:
  - action: persistent_notification.create
    data:
      title: Meltem read health
      message: >-
        Read health has been on for five minutes. Check the entity attributes
        and download the integration diagnostics.
mode: single
```

## Filter change reminders

For examples using the integration's **Filter change due** trigger or
condition, see the [trigger guide](./TRIGGER_FILTER_CHANGE_DUE.md) and
[condition guide](./CONDITION_FILTER_CHANGE_DUE.md).
