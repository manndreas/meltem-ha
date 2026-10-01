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
- `custom_components/meltem_ventilation/coordinator.py`: gateway lock, request-rate cap, backoff, and write orchestration
- `custom_components/meltem_ventilation/polling.py`, `read_health.py`, `levels.py`, `overlay.py`, `write_confirmation.py`: job planning, read freshness, airflow targets, pending values, and write outcomes used by the coordinator
- `custom_components/meltem_ventilation/config_flow.py`: setup, USB discovery, reconfigure (serial port), gateway-backed unit discovery, profile selection
- `custom_components/meltem_ventilation/fan.py`: the two directional airflow controls, the primary control path
- `custom_components/meltem_ventilation/number.py`: per-series control settings such as humidity and CO2 thresholds
- `custom_components/meltem_ventilation/strings.json`: English entity/config strings
- `custom_components/meltem_ventilation/translations/de.json`: German translations
- `tools/`: scripts for a live gateway, run as modules from the repository root (`python -m tools.<name>`); their link settings are in `tools/_link.py`
- `docs/reference/`: manufacturer reference extracted from the original Meltem documents (originals in gitignored `docs/meltem/`)
- `docs/MELTEM.md`: observed register behavior, gateway quirks, and traced app writes
- `docs/DEVELOPER.md`: implementation notes and the decisions behind them
- `docs/HARDWARE_BACKLOG.md`: open findings that require live-gateway testing
- `docs/LIVE_GATEWAY_TESTS.md`: test plan for running those checks on the live gateway; follow its safety rules
- `docs/SETTING_RE_BACKLOG.md`: parked reverse engineering of app settings, with its measurement log
- `CHANGELOG.md`: release history

## Working rules

- Keep user-visible terminology aligned with the Meltem manuals where practical
- Put new manufacturer facts into the matching file under `docs/reference/` with a source ID; put hardware observations into `docs/MELTEM.md`
- Never commit the original Meltem PDFs; summarize facts in own words instead of copying text or figures
- Be conservative with Modbus timing and grouped reads; gateway behavior is sensitive
- Keep the `register_ranges` of the components unchanged unless a gateway benchmark backs the change; the parity test in `tests/test_modbus_client.py` guards them
- Do not remove or rewrite observed hardware quirks without checking `docs/MELTEM.md` and `docs/DEVELOPER.md`
- Keep the link settings in `tools/_link.py` in line with `const.py`; `tests/test_tools.py` checks them
- When changing versioned release metadata, update `manifest.json`, `pyproject.toml`, and `CHANGELOG.md`; the release workflow rejects a tag that differs from the first two

## Validation

- Tests need Python 3.14; see `CONTRIBUTING.md` for the setup and the Windows workarounds
- Run tests with `pytest`, lint with `ruff check custom_components tests tools`, and type-check with `mypy`; CI runs all three, plus hassfest and the HACS validation
- Focused runs are usually enough while iterating, for example:
  - `pytest tests/test_modbus_client.py`
  - `pytest tests/test_transport.py`
  - `pytest tests/test_config_flow.py`
  - `pytest tests/test_entity_descriptions.py`
