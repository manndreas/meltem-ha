# AGENTS.md

## Project

- Home Assistant custom integration for Meltem `M-WRG` ventilation units via the `M-WRG-GW` gateway
- Integration domain: `meltem_ventilation`

## Important files

- `custom_components/meltem_ventilation/manifest.json`: Home Assistant metadata and integration version
- `custom_components/meltem_ventilation/const.py`: profile metadata, register constants, entity-key groupings
- `custom_components/meltem_ventilation/modbus_client.py`: Modbus reads, writes, scaling, and mode handling
- `custom_components/meltem_ventilation/modbus_helpers.py`: capability probing and helper logic
- `custom_components/meltem_ventilation/coordinator.py`: polling scheduler and write orchestration
- `custom_components/meltem_ventilation/strings.json`: English entity/config strings
- `custom_components/meltem_ventilation/translations/de.json`: German translations
- `docs/MELTEM.md`: handwritten manufacturer reference extracted into Markdown
- `docs/DEVELOPER.md`: implementation notes, caveats, and hardware findings
- `CHANGELOG.md`: release history

## Working rules

- Keep user-visible terminology aligned with the Meltem manuals where practical
- Prefer updating `docs/MELTEM.md` when new manufacturer facts are discovered
- Be conservative with Modbus timing and grouped reads; gateway behavior is sensitive
- Do not remove or rewrite observed hardware quirks without checking `docs/DEVELOPER.md`
- When changing versioned release metadata, update both `manifest.json` and `pyproject.toml`

## Validation

- Run tests with `pytest`
- Focused runs are usually enough while iterating, for example:
  - `pytest tests/test_modbus_client.py`
  - `pytest tests/test_config_flow.py`
  - `pytest tests/test_entity_descriptions.py`
