# Contributing

Thanks for helping improve the Meltem Home Assistant integration.

## Before you start

- This project talks to real Meltem ventilation hardware via the `M-WRG-GW` gateway.
- Please be conservative with write behavior, timing, retries, and register handling.
- Read [docs/DEVELOPER.md](/Users/andreas/Code/meltem-ha/docs/DEVELOPER.md) for implementation notes.
- Read [docs/MELTEM.md](/Users/andreas/Code/meltem-ha/docs/MELTEM.md) for the transcribed manufacturer reference.

## Development setup

- Python: `>=3.12`
- Install dependencies in your preferred environment.
- Run tests with:

```bash
pytest
```

Useful focused test runs:

```bash
pytest tests/test_modbus_client.py
pytest tests/test_config_flow.py
pytest tests/test_entity_descriptions.py
```

## Contribution guidelines

- Keep user-visible terminology aligned with the Meltem manuals where practical.
- Prefer small, focused changes.
- Add or update tests for behavior changes.
- Do not remove documented hardware quirks unless you have confirmed different behavior on real hardware.
- If you change release metadata, keep `custom_components/meltem_ventilation/manifest.json`, `pyproject.toml`, and `CHANGELOG.md` in sync.

## Pull requests

Please include:

- a short summary of the change
- why the change is needed
- any hardware assumptions or test setup details
- logs or screenshots if the change affects setup, discovery, or entities
