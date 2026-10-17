# Control behavior

## Directional fans

Each unit is exposed as two `fan` entities:

- **Supply air** — target airflow of the supply fan
- **Extract air** — target airflow of the extract fan

Setting both to the same value runs the unit balanced. Setting them to
different values switches it to unbalanced operation. Setting one to zero
runs the unit extract-only or supply-only, like the `Abluft` / `Zuluft`
shortcuts of the Meltem app but with the airflow you set. When both are zero,
the unit is switched off; turning it back on starts both directions again.

A new fan value is shown immediately. The `level_source` attribute identifies
whether the displayed airflow is a `pending` write, a confirmed `target`, or
`measured` airflow. Turning on a fan that is already running changes nothing,
so it does not end a running sensor mode. Turning on a stopped direction
restores its last speed. If the opposite direction cannot be read, a non-zero
value is applied to both directions, and switching off a single direction is
refused instead of stopping the whole unit.

## Quick mode

The **Quick mode** selector provides the Meltem app shortcuts `Low`, `Medium`
and `High`. The airflow behind these modes is configured in the Meltem app and
cannot be read over Modbus. `Individual` means no selectable `Low`, `Medium`
or `High` quick mode is active. Extract-only and supply-only operation also
appears as `Individual` in this selector and is shown through the two fan
values.

## Sensor control

Where supported, sensor control provides humidity, CO2 or automatic
regulation. Selecting `Off` ends a running sensor mode and returns the unit to
manual airflow; it does nothing when no sensor mode is active. While a sensor
mode is active, the unit chooses the airflow itself, so the fans show measured
values instead of targets. Setting a non-zero fan speed leaves sensor control
with a balanced airflow; setting one direction to zero selects one-sided
operation without stopping the whole unit.

## Intensive ventilation

Intensive ventilation is a separate switch because it is a temporary override
that does not replace the active quick mode. It can be cancelled before the
runtime configured in the Meltem app has elapsed.
