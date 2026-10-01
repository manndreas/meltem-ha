# Support

If you open an issue, please include enough detail to reproduce the problem.

## Please include

- Home Assistant version
- integration version
- unit series (`M-WRG-S` or `M-WRG-II`) and the selected profile
- whether the issue happens during setup, discovery, reading, or writing
- whether another integration, for example a Modbus hub in
  `configuration.yaml`, uses the same serial port
- relevant Home Assistant logs, with debug logging enabled if possible (see
  [Logs in the README](./README.md#logs))
- the integration diagnostics download when the entry is available; the serial
  port is redacted automatically

## Helpful logs

In Home Assistant:

1. Open `Settings`
2. Open `System`
3. Open `Logs`

With the `Terminal & SSH` add-on:

```bash
ha core logs | grep meltem_ventilation
```

Download diagnostics from `Settings` -> `Devices & Services` ->
`Meltem Modbus` -> the three-dot menu -> `Download diagnostics`. It includes
the configured units, their current states and read health, availability,
scheduler status, counters of the serial link, and a best-effort gateway unit
list.

## Good issue reports

Good reports usually include:

- what you expected to happen
- what happened instead
- whether the problem is reproducible
- whether it started after a specific version or configuration change
