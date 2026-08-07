# Contributing

Thanks for helping improve the Meltem Home Assistant integration.

## Before you start

- This project talks to real Meltem Modbus hardware via the `M-WRG-GW` gateway.
- Please be conservative with write behavior, timing, retries, and register handling.
- Read [docs/DEVELOPER.md](./docs/DEVELOPER.md) for implementation notes.
- Read [docs/MELTEM.md](./docs/MELTEM.md) for the transcribed manufacturer reference.
- Read [docs/HARDWARE_BACKLOG.md](./docs/HARDWARE_BACKLOG.md) before changing
  behavior that still needs verification on a live gateway.

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

Run tests and lint with:

```bash
pytest
ruff check custom_components tests
```

Useful focused test runs:

```bash
pytest tests/test_modbus_client.py
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
