# Contributing

Thanks for helping improve the Meltem Home Assistant integration.

## Before you start

- This project talks to real Meltem Modbus hardware via the `M-WRG-GW` gateway.
- Please be conservative with write behavior, timing, retries, and register handling.
- Read [docs/DEVELOPER.md](./docs/DEVELOPER.md) for implementation notes.
- Read [docs/reference/](./docs/reference/README.md) for the manufacturer reference
  and [docs/MELTEM.md](./docs/MELTEM.md) for observed hardware behavior.
- Read [docs/HARDWARE_BACKLOG.md](./docs/HARDWARE_BACKLOG.md) before changing
  behavior that still needs verification on a live gateway, and
  [docs/LIVE_GATEWAY_TESTS.md](./docs/LIVE_GATEWAY_TESTS.md) before running
  anything against one.

## Development setup

The integration itself targets the Python version shipped with Home Assistant,
which is why `pyproject.toml` declares `requires-python = ">=3.13"`. The test
toolchain is stricter: `pytest-homeassistant-custom-component` currently
requires Python `>=3.14`, so use a 3.14 interpreter for the virtual
environment.

```bash
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\Activate.ps1
pip install -r requirements-test.txt
```

Do not install `homeassistant`, `pytest` or `pytest-asyncio` separately -
`pytest-homeassistant-custom-component` pins the matching versions.
The harness ships Home Assistant `2026.10` but not the requirements of its
Modbus integration, from which the integration gets its serial link, so
`requirements-test.txt` adds the `modbus-connection`, `tmodbus` and `pymodbus`
versions pinned in `homeassistant/components/modbus/manifest.json` (see
[docs/TODO.md](./docs/TODO.md) item 3). Tests never open a serial port; they
run against `modbus_connection.mock.MockModbusConnection`.

Run tests, lint, and the type check with:

```bash
pytest
ruff check custom_components tests tools
mypy
```

CI runs all three commands, plus hassfest and the HACS validation.

Useful focused test runs:

```bash
pytest tests/test_modbus_client.py
pytest tests/test_transport.py
pytest tests/test_config_flow.py
pytest tests/test_entity_descriptions.py
```

### Running the tests on Windows

The Home Assistant test harness assumes a POSIX host. Two extra steps are
needed, both local to your virtual environment and not part of the repository:

- Home Assistant imports the POSIX-only modules `fcntl` and `resource` during
  startup. Create `.venv/Lib/site-packages/fcntl.py` and
  `.venv/Lib/site-packages/resource.py` as small shims exposing the names that
  are actually used (`flock` and the `LOCK_*` constants for `fcntl`,
  `getrlimit`, `setrlimit` and `RLIMIT_NOFILE` for `resource`).
- `pytest-socket` blocks `socket.socketpair()` on Windows because it is
  implemented through a real loopback connection. `tests/conftest.py` already
  contains a narrow workaround for this; it only re-enables `socketpair` and
  still blocks outbound sockets, so do not widen it.

## Working with a real gateway

The scripts in `tools/` talk to a gateway on a local serial port without Home
Assistant. Run them as modules from the repository root:

```bash
python -m tools.raw_requests --help
```

- Use the port from one process at a time. Stop Home Assistant or anything
  else that uses the gateway first, and run the tools one after another.
- Some tools write registers. Follow the safety rules in
  [docs/LIVE_GATEWAY_TESTS.md](./docs/LIVE_GATEWAY_TESTS.md), and never write
  the baud rate or the Modbus address of a unit.
- The tools keep their own copy of the link settings in `tools/_link.py`;
  `tests/test_tools.py` keeps it in line with `const.py`.

## Contribution guidelines

- Keep user-visible terminology aligned with the Meltem manuals where practical.
- Prefer small, focused changes.
- Add or update tests for behavior changes.
- Do not remove documented hardware quirks unless you have confirmed different
  behavior on real hardware; they are listed in [docs/MELTEM.md](./docs/MELTEM.md).
- Put manufacturer facts into the matching file under `docs/reference/` with a
  source ID, and hardware observations into `docs/MELTEM.md`. Never commit the
  original Meltem PDFs.
- If you change release metadata, keep `custom_components/meltem_ventilation/manifest.json`, `pyproject.toml`, and `CHANGELOG.md` in sync.

## Pull requests

Please include:

- a short summary of the change
- why the change is needed
- any hardware assumptions or test setup details
- logs or screenshots if the change affects setup, discovery, or entities
- confirmation that `pytest`, ruff, and mypy pass
