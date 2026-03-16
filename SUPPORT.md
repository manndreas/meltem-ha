# Support

If you open an issue, please include enough detail to reproduce the problem.

## Please include

- Home Assistant version
- integration version
- Meltem unit family and selected profile
- whether you use `M-WRG-S` or `M-WRG-II`
- whether the issue happens during setup, discovery, reading, or writing
- relevant Home Assistant logs

## Helpful logs

In Home Assistant:

1. Open `Settings`
2. Open `System`
3. Open `Logs`

With the `Terminal & SSH` add-on:

```bash
ha core logs | grep meltem_ventilation
```

## Good issue reports

Good reports usually include:

- what you expected to happen
- what happened instead
- whether the problem is reproducible
- whether it started after a specific version or configuration change
