# Filter change due trigger

The **Filter change due** trigger (`meltem_ventilation.filter_change_due`)
fires when a selected Meltem unit's **Filter change due** binary sensor
transitions to `on`. It is intended for automations such as sending a
maintenance reminder.

The target is restricted to this integration's filter-change sensors. Other
problem sensors, such as the device error sensor, do not trigger this event.
The trigger reacts to a state change; it does not fire just because the sensor
is already `on` when the automation is loaded.

## Use the trigger in the UI

1. Go to **Settings** > **Automations & scenes**.
2. Open an automation or create one.
3. Under **When**, select **Add trigger**.
4. Select a Meltem device as the target.
5. Choose **Filter change due**.
6. Set **Trigger when** and **For at least** as needed, then save the automation.

## Trigger options

| Option | Meaning |
| --- | --- |
| `behavior` | How to handle the selected filter sensors. `each` (default) fires for every selected sensor that transitions to `on`. `first` fires on the transition that results in exactly one selected sensor being `on`. `all` fires when all selected sensors are `on`. |
| `for` | How long to wait after the trigger condition is met before firing. The default `00:00:00` fires immediately. If the matching target state is no longer valid during the wait, the pending trigger is cancelled. |

For example, with multiple selected units, `each` can start one run per unit
as its filter becomes due. `all` waits until every selected unit reports a due
filter; `first` starts on the transition that takes the number of due units
from zero to one.

## YAML example

This automation creates a persistent notification as soon as a selected
Meltem unit reports that its filter change is due. Replace the example entity
with the **Filter change due** entity from your installation.

```yaml
alias: Notify when a Meltem filter change is due
description: Remind me when a Meltem unit reports a filter change.
triggers:
  - trigger: meltem_ventilation.filter_change_due
    target:
      entity_id:
        - binary_sensor.living_room_filter_change_due
    options:
      behavior: each
      for: "00:00:00"
conditions: []
actions:
  - action: persistent_notification.create
    data:
      title: Meltem filter change
      message: "Filter change due for {{ trigger.entity_id }}."
mode: single
```
