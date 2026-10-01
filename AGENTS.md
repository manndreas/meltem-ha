# AGENTS.md

## Project

- Home Assistant custom integration for Meltem `M-WRG` ventilation units via the `M-WRG-GW` gateway
- Integration domain: `meltem_ventilation`

## Important files

- `custom_components/meltem_ventilation/manifest.json`: Home Assistant metadata and integration version
- `custom_components/meltem_ventilation/const.py`: profile metadata, register constants, entity-key groupings
- `custom_components/meltem_ventilation/modbus_client.py`: async room reads, mode decoding, write sequences
- `custom_components/meltem_ventilation/modbus_helpers.py`: link parameters, unit preparation, discovery, setup probes
- `custom_components/meltem_ventilation/device/`: register blocks as `modbus-connection` components (`components.py`), room/probe/gateway devices (`device.py`), and the shared retry policy (`transport.py`)
- `custom_components/meltem_ventilation/coordinator.py`: polling scheduler and write orchestration
- `custom_components/meltem_ventilation/strings.json`: English entity/config strings
- `custom_components/meltem_ventilation/translations/de.json`: German translations
- `docs/reference/`: manufacturer reference extracted from the original Meltem documents (originals in gitignored `docs/meltem/`)
- `docs/MELTEM.md`: observed register behavior, gateway quirks, and traced app writes
- `docs/DEVELOPER.md`: implementation notes, caveats, and hardware findings
- `docs/HARDWARE_BACKLOG.md`: open findings that require live-gateway testing
- `CHANGELOG.md`: release history

## Working rules

- Keep user-visible terminology aligned with the Meltem manuals where practical
- Put new manufacturer facts into the matching file under `docs/reference/` with a source ID; put hardware observations into `docs/MELTEM.md`
- Never commit the original Meltem PDFs; summarize facts in own words instead of copying text or figures
- Be conservative with Modbus timing and grouped reads; gateway behavior is sensitive
- Keep the `register_ranges` of the components unchanged unless a gateway benchmark backs the change; the parity test in `tests/test_modbus_client.py` guards them
- Do not remove or rewrite observed hardware quirks without checking `docs/DEVELOPER.md`
- When changing versioned release metadata, update both `manifest.json` and `pyproject.toml`

## Validation

- Run tests with `pytest`
- Focused runs are usually enough while iterating, for example:
  - `pytest tests/test_modbus_client.py`
  - `pytest tests/test_transport.py`
  - `pytest tests/test_config_flow.py`
  - `pytest tests/test_entity_descriptions.py`
