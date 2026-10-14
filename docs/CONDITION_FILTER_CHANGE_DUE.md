# Filter change due condition

The **Filter change due** condition (`meltem_ventilation.filter_change_due`)
passes when the selected Meltem **Filter change due** binary sensor is `on`.
That state means the unit reports that its filter needs replacing.

A condition does not start an automation. Home Assistant evaluates it after an
automation trigger fires; use this condition to allow an automation to continue
only when a filter change is due. The target is restricted to this
integration's filter-change sensors, not other problem sensors such as the
device error sensor.

## Use the condition in the UI

1. Go to **Settings** > **Automations & scenes**.
2. Open an automation or create one.
3. Under **And if**, select **Add condition**.
4. Select a Meltem device or its **Filter change due** entity as the target.
5. Choose **Filter change due**.
6. Set **Condition passes if** and **For at least** as needed, then save the
   automation.

## Condition options

| Option | Meaning |
| --- | --- |
| `behavior` | How results are combined when multiple filter sensors are targeted. `any` (default) passes if at least one selected sensor is `on`; `all` passes only if every selected sensor is `on`. |
| `for` | The minimum time a selected sensor must have been `on` for it to count. The default `00:00:00` checks the current state without an additional wait. |

## YAML example

This automation checks the condition every morning and creates a persistent
notification only if the selected Meltem unit reports that a filter change is
due. Replace the example entity with the **Filter change due** entity from
your installation.

```yaml
alias: Check Meltem filter in the morning
description: Notify me if a filter change is due.
triggers:
  - trigger: time
    at: "08:00:00"
conditions:
  - condition: meltem_ventilation.filter_change_due
    target:
      entity_id:
        - binary_sensor.living_room_filter_change_due
    options:
      behavior: any
      for: "00:00:00"
actions:
  - action: persistent_notification.create
    data:
      title: Meltem filter change
      message: "A Meltem unit reports that a filter change is due."
mode: single
```

If the automation should run immediately when the sensor changes to `on`, use
the [Filter change due trigger](./TRIGGER_FILTER_CHANGE_DUE.md) instead.
