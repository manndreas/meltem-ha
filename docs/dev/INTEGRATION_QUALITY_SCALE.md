# Home Assistant Integration Quality Scale

Repository review date: **October 16, 2026**.

This document records an internal assessment against the official
[Quality Scale](https://developers.home-assistant.io/docs/core/integration-quality-scale/),
[checklist](https://developers.home-assistant.io/docs/core/integration-quality-scale/checklist),
and [rules](https://developers.home-assistant.io/docs/core/integration-quality-scale/rules).
It is not an official assessment by the Home Assistant Core team.

## Result

The integration is distributed through HACS as a community integration and
therefore formally belongs to the special **Custom** category. The Bronze,
Silver, Gold, and Platinum tiers are awarded to Core integrations. No
`quality_scale.yaml` defining a target or achieved tier is present.

If assessed as a candidate for Home Assistant Core, this review finds that the
integration **does not yet meet Bronze**. All other applicable Bronze rules are
met; the remaining gap is publishing the brand assets in the central
repository for Core integrations. The HACS integration includes the assets
locally, as intended for Custom integrations.

If `brands` is excluded from the theoretical assessment, all other applicable
Bronze rules and all applicable Silver rules are met. This would make the
integration theoretically Silver; formally, it remains a Custom integration
and therefore does not receive an official Silver rating.

Some higher-tier rules also fail independently of whether Bronze has been
achieved. The Quality Scale is cumulative: a tier is met only when all
applicable rules for that tier and every lower tier are met.

## Status legend

- ✅ **Met** — demonstrated in the repository by code, tests, or documentation.
- ⚠️ **Partial** — relevant implementation exists, but the rule is not yet
  fully met.
- ❌ **Open** — a requirement is missing or a measurable threshold is not met.
- ➖ **Not applicable** — the rule does not apply to this integration; the
  reason is given.

Rule names link to their official descriptions. File paths link to the
reviewed locations in the repository.

## Bronze

| Status | Rule | Assessment and evidence |
| --- | --- | --- |
| ➖ | [`action-setup`](https://developers.home-assistant.io/docs/core/integration-quality-scale/rules/action-setup) | No service actions are provided that would need to be registered in `async_setup`. |
| ✅ | [`appropriate-polling`](https://developers.home-assistant.io/docs/core/integration-quality-scale/rules/appropriate-polling) | The coordinator limits the request rate, staggers read jobs, and uses backoff on transport failures. [coordinator.py](../../custom_components/meltem_ventilation/coordinator.py), [polling.py](../../custom_components/meltem_ventilation/polling.py) |
| ❌ | [`brands`](https://developers.home-assistant.io/docs/core/integration-quality-scale/rules/brands) | Local [brand assets](../../custom_components/meltem_ventilation/brand/icon.png) and [logo files](../../custom_components/meltem_ventilation/brand/logo.png) are present for the HACS custom integration. A Core candidate still needs an entry under [`core_integrations/meltem_ventilation`](https://github.com/home-assistant/brands/tree/master/core_integrations) in the central [brands repository](https://github.com/home-assistant/brands). |
| ✅ | [`common-modules`](https://developers.home-assistant.io/docs/core/integration-quality-scale/rules/common-modules) | Shared responsibilities are split across modules such as [coordinator.py](../../custom_components/meltem_ventilation/coordinator.py), [polling.py](../../custom_components/meltem_ventilation/polling.py), and [read_health.py](../../custom_components/meltem_ventilation/read_health.py). |
| ✅ | [`config-flow-test-coverage`](https://developers.home-assistant.io/docs/core/integration-quality-scale/rules/config-flow-test-coverage) | The full test suite covers [config_flow.py](../../custom_components/meltem_ventilation/config_flow.py) at 100%. [test_config_flow.py](../../tests/components/meltem_ventilation/test_config_flow.py) tests recovery from errors in the user, USB, options, and reconfigure flows, as well as preventing the same gateway from being configured twice. |
| ✅ | [`config-flow`](https://developers.home-assistant.io/docs/core/integration-quality-scale/rules/config-flow) | UI setup, field-level `data_description` text for every config and options form field, and English and German translations are provided. Connection and device-profile settings are stored in `ConfigEntry.data`; the request rate is stored in `ConfigEntry.options`. The migration moves the existing rate from `data` to `options` without overwriting an existing options value. [strings.json](../../custom_components/meltem_ventilation/strings.json), [config_flow.py](../../custom_components/meltem_ventilation/config_flow.py), [`__init__.py`](../../custom_components/meltem_ventilation/__init__.py) |
| ✅ | [`dependency-transparency`](https://developers.home-assistant.io/docs/core/integration-quality-scale/rules/dependency-transparency) | Dependencies are declared in [manifest.json](../../custom_components/meltem_ventilation/manifest.json). The reviewed runtime dependency `modbus-connection` is available on PyPI and in a public repository with an OSI license, release tag, and CI. |
| ➖ | [`docs-actions`](https://developers.home-assistant.io/docs/core/integration-quality-scale/rules/docs-actions) | No service actions are provided. |
| ✅ | [`docs-triggers`](https://developers.home-assistant.io/docs/core/integration-quality-scale/rules/docs-triggers) | The [trigger guide](../TRIGGER_FILTER_CHANGE_DUE.md) explains its purpose and target selection, describes `behavior` and `for`, and includes UI steps and a usable YAML example. The [README.md](../../README.md) links to it. |
| ✅ | [`docs-conditions`](https://developers.home-assistant.io/docs/core/integration-quality-scale/rules/docs-conditions) | The [condition guide](../CONDITION_FILTER_CHANGE_DUE.md) explains its purpose and parameters (`behavior`, `for`), and includes UI steps and a YAML example. The [README.md](../../README.md) links to it. |
| ✅ | [`docs-high-level-description`](https://developers.home-assistant.io/docs/core/integration-quality-scale/rules/docs-high-level-description) | The [README.md](../../README.md) explains the integration's purpose, manufacturer, gateway, and product families. |
| ✅ | [`docs-installation-instructions`](https://developers.home-assistant.io/docs/core/integration-quality-scale/rules/docs-installation-instructions) | The [README.md](../../README.md) explains the requirements and HACS and manual installation. |
| ✅ | [`docs-removal-instructions`](https://developers.home-assistant.io/docs/core/integration-quality-scale/rules/docs-removal-instructions) | The [removal guide](../REMOVE_INTEGRATION.md) explains how to remove the config entry and uninstall through HACS or manually. It is linked from the [README.md](../../README.md). |
| ➖ | [`entity-event-setup`](https://developers.home-assistant.io/docs/core/integration-quality-scale/rules/entity-event-setup) | The integration does not subscribe to entity events that require lifecycle registration. |
| ✅ | [`entity-unique-id`](https://developers.home-assistant.io/docs/core/integration-quality-scale/rules/entity-unique-id) | Room entities receive stable IDs through the shared [entity.py](../../custom_components/meltem_ventilation/entity.py) base class. |
| ✅ | [`has-entity-name`](https://developers.home-assistant.io/docs/core/integration-quality-scale/rules/has-entity-name) | The shared entity base class sets `has_entity_name`; the additional sensor sets it as well. [entity.py](../../custom_components/meltem_ventilation/entity.py), [sensor.py](../../custom_components/meltem_ventilation/sensor.py) |
| ✅ | [`runtime-data`](https://developers.home-assistant.io/docs/core/integration-quality-scale/rules/runtime-data) | The coordinator and runtime data are managed through `ConfigEntry.runtime_data`. [`__init__.py`](../../custom_components/meltem_ventilation/__init__.py), [models.py](../../custom_components/meltem_ventilation/models.py) |
| ✅ | [`test-before-configure`](https://developers.home-assistant.io/docs/core/integration-quality-scale/rules/test-before-configure) | The config flow validates the port and scans and probes the gateway before creating the entry. [config_flow.py](../../custom_components/meltem_ventilation/config_flow.py) |
| ✅ | [`test-before-setup`](https://developers.home-assistant.io/docs/core/integration-quality-scale/rules/test-before-setup) | The gateway is validated during setup, and the initial reads are started. [`__init__.py`](../../custom_components/meltem_ventilation/__init__.py) |
| ✅ | [`unique-config-entry`](https://developers.home-assistant.io/docs/core/integration-quality-scale/rules/unique-config-entry) | The port is used as the unique ID, and `single_config_entry` is set in [manifest.json](../../custom_components/meltem_ventilation/manifest.json). |

The `config-flow` rule also includes the requirements to provide context for
input fields through `data_description` and to use `ConfigEntry.data` and
`ConfigEntry.options` appropriately. Both are met; the test
[`test_all_config_flow_fields_have_descriptions`](../../tests/components/meltem_ventilation/test_entity_descriptions.py)
checks the descriptions for every config and options flow field.

## Silver

| Status | Rule | Assessment and evidence |
| --- | --- | --- |
| ➖ | [`action-exceptions`](https://developers.home-assistant.io/docs/core/integration-quality-scale/rules/action-exceptions) | No service actions are provided. |
| ✅ | [`config-entry-unloading`](https://developers.home-assistant.io/docs/core/integration-quality-scale/rules/config-entry-unloading) | Platforms are unloaded and the coordinator and client are shut down when the entry is unloaded. [`__init__.py`](../../custom_components/meltem_ventilation/__init__.py) |
| ✅ | [`docs-configuration-parameters`](https://developers.home-assistant.io/docs/core/integration-quality-scale/rules/docs-configuration-parameters) | The [README.md](../../README.md) explains how to change the port, edit profiles, change the request rate, and rescan the gateway. |
| ✅ | [`docs-installation-parameters`](https://developers.home-assistant.io/docs/core/integration-quality-scale/rules/docs-installation-parameters) | The [README.md](../../README.md) explains the gateway, USB connection, requirements, port, and profile selection. |
| ✅ | [`entity-unavailable`](https://developers.home-assistant.io/docs/core/integration-quality-scale/rules/entity-unavailable) | Entities are marked unavailable when rooms are unavailable or read groups are stale. [entity.py](../../custom_components/meltem_ventilation/entity.py) |
| ✅ | [`integration-owner`](https://developers.home-assistant.io/docs/core/integration-quality-scale/rules/integration-owner) | `codeowners` is set in [manifest.json](../../custom_components/meltem_ventilation/manifest.json). |
| ✅ | [`log-when-unavailable`](https://developers.home-assistant.io/docs/core/integration-quality-scale/rules/log-when-unavailable) | Each room is logged once at Info level when it reaches the failure threshold or has been silent too long, and once when it recovers. The check runs after every read job of the room, so availability queries from entities, diagnostics, and system health have no side effects. Single failed read jobs and gateway backoff changes are logged at Debug level. [coordinator.py](../../custom_components/meltem_ventilation/coordinator.py), [test_coordinator_and_config_helpers.py](../../tests/components/meltem_ventilation/test_coordinator_and_config_helpers.py) |
| ✅ | [`parallel-updates`](https://developers.home-assistant.io/docs/core/integration-quality-scale/rules/parallel-updates) | Entity platforms set `PARALLEL_UPDATES = 0`; serialization is handled centrally by the coordinator. Examples: [fan.py](../../custom_components/meltem_ventilation/fan.py), [sensor.py](../../custom_components/meltem_ventilation/sensor.py). |
| ➖ | [`reauthentication-flow`](https://developers.home-assistant.io/docs/core/integration-quality-scale/rules/reauthentication-flow) | There are no credentials or authentication to renew. |
| ✅ | [`test-coverage`](https://developers.home-assistant.io/docs/core/integration-quality-scale/rules/test-coverage) | In the full coverage run, overall coverage is 99%: every integration module except `modbus_helpers.py` (86% on Windows where the POSIX `/dev/serial/by-id` symlink test is skipped; 96% on Linux) is at 98% or above, and [config_flow.py](../../custom_components/meltem_ventilation/config_flow.py), [write_confirmation.py](../../custom_components/meltem_ventilation/write_confirmation.py), and [device/transport.py](../../custom_components/meltem_ventilation/device/transport.py) are at 100% statement and branch coverage. |

## Gold

| Status | Rule | Assessment and evidence |
| --- | --- | --- |
| ✅ | [`devices`](https://developers.home-assistant.io/docs/core/integration-quality-scale/rules/devices) | The gateway and configured ventilation units are registered as devices. [entity.py](../../custom_components/meltem_ventilation/entity.py) |
| ✅ | [`diagnostics`](https://developers.home-assistant.io/docs/core/integration-quality-scale/rules/diagnostics) | Diagnostics are implemented; the serial port is redacted from the output. [diagnostics.py](../../custom_components/meltem_ventilation/diagnostics.py) |
| ➖ | [`discovery-update-info`](https://developers.home-assistant.io/docs/core/integration-quality-scale/rules/discovery-update-info) | The connection is local over USB/serial, so there is no dynamic IP or network address to update. |
| ✅ | [`discovery`](https://developers.home-assistant.io/docs/core/integration-quality-scale/rules/discovery) | USB discovery and gateway-based unit scanning are implemented. [manifest.json](../../custom_components/meltem_ventilation/manifest.json), [config_flow.py](../../custom_components/meltem_ventilation/config_flow.py) |
| ✅ | [`docs-data-update`](https://developers.home-assistant.io/docs/core/integration-quality-scale/rules/docs-data-update) | The [README.md](../../README.md) specifies the default rate of 4 requests/s and its range, documents the normal intervals for each data group and the staggered scheduler, and explains the effects of more units, a lower rate, and transport backoff. [const.py](../../custom_components/meltem_ventilation/const.py), [polling.py](../../custom_components/meltem_ventilation/polling.py), [coordinator.py](../../custom_components/meltem_ventilation/coordinator.py) |
| ✅ | [`docs-examples`](https://developers.home-assistant.io/docs/core/integration-quality-scale/rules/docs-examples) | The [README.md](../../README.md) contains an executable YAML automation for intensive ventilation while a room is occupied; the filter-change [trigger](../TRIGGER_FILTER_CHANGE_DUE.md) and [condition](../CONDITION_FILTER_CHANGE_DUE.md) guides include additional reminder examples. |
| ✅ | [`docs-known-limitations`](https://developers.home-assistant.io/docs/core/integration-quality-scale/rules/docs-known-limitations) | The [README.md](../../README.md) identifies unverified hardware profiles and the need to select profiles manually as limitations. |
| ✅ | [`docs-supported-devices`](https://developers.home-assistant.io/docs/core/integration-quality-scale/rules/docs-supported-devices) | The [README.md](../../README.md) lists the supported profiles and specifies which hardware has been tested. |
| ✅ | [`docs-supported-functions`](https://developers.home-assistant.io/docs/core/integration-quality-scale/rules/docs-supported-functions) | The [README.md](../../README.md) describes sensors, fan entities, modes, switches, thresholds, and options. |
| ✅ | [`docs-troubleshooting`](https://developers.home-assistant.io/docs/core/integration-quality-scale/rules/docs-troubleshooting) | The [README.md](../../README.md) provides help for missing or unreachable units, port conflicts, and logs. |
| ✅ | [`docs-use-cases`](https://developers.home-assistant.io/docs/core/integration-quality-scale/rules/docs-use-cases) | The usage description in the [README.md](../../README.md) explains practical cases such as balanced or unbalanced operation, sensor control, and intensive ventilation. |
| ❌ | [`dynamic-devices`](https://developers.home-assistant.io/docs/core/integration-quality-scale/rules/dynamic-devices) | Units added later do not appear automatically during operation. Users must run “Scan for new units” and select profiles. [README.md](../../README.md), [config_flow.py](../../custom_components/meltem_ventilation/config_flow.py) |
| ✅ | [`entity-category`](https://developers.home-assistant.io/docs/core/integration-quality-scale/rules/entity-category) | Diagnostic and configuration entities are categorized appropriately. [binary_sensor.py](../../custom_components/meltem_ventilation/binary_sensor.py), [number.py](../../custom_components/meltem_ventilation/number.py) |
| ✅ | [`entity-device-class`](https://developers.home-assistant.io/docs/core/integration-quality-scale/rules/entity-device-class) | Appropriate device classes are used for sensors and binary sensors. [sensor.py](../../custom_components/meltem_ventilation/sensor.py), [binary_sensor.py](../../custom_components/meltem_ventilation/binary_sensor.py) |
| ✅ | [`entity-disabled-by-default`](https://developers.home-assistant.io/docs/core/integration-quality-scale/rules/entity-disabled-by-default) | Less central diagnostic and operating-hours entities are disabled by default. [sensor.py](../../custom_components/meltem_ventilation/sensor.py) |
| ✅ | [`entity-translations`](https://developers.home-assistant.io/docs/core/integration-quality-scale/rules/entity-translations) | Entity names are localized in English and German. [en.json](../../custom_components/meltem_ventilation/translations/en.json), [de.json](../../custom_components/meltem_ventilation/translations/de.json) |
| ✅ | [`exception-translations`](https://developers.home-assistant.io/docs/core/integration-quality-scale/rules/exception-translations) | Integration-specific exception messages are included in the translations. [strings.json](../../custom_components/meltem_ventilation/strings.json), [de.json](../../custom_components/meltem_ventilation/translations/de.json) |
| ✅ | [`icon-translations`](https://developers.home-assistant.io/docs/core/integration-quality-scale/rules/icon-translations) | All integration-specific icons are defined in [icons.json](../../custom_components/meltem_ventilation/icons.json) using the entity translation keys; device-class icons are left to Home Assistant defaults. |
| ✅ | [`reconfiguration-flow`](https://developers.home-assistant.io/docs/core/integration-quality-scale/rules/reconfiguration-flow) | The serial port can be changed through the reconfigure flow; profiles and new units can be managed through the options flow. [config_flow.py](../../custom_components/meltem_ventilation/config_flow.py) |
| ➖ | [`repair-issues`](https://developers.home-assistant.io/docs/core/integration-quality-scale/rules/repair-issues) | The review found no concrete case requiring a persistent repair issue for a user-fixable problem. Connection problems are reported during setup or in the options flow and through Read health. |
| ✅ | [`stale-devices`](https://developers.home-assistant.io/docs/core/integration-quality-scale/rules/stale-devices) | Devices for units that are no longer configured are removed during setup. [`__init__.py`](../../custom_components/meltem_ventilation/__init__.py) |

## Platinum

| Status | Rule | Assessment and evidence |
| --- | --- | --- |
| ✅ | [`async-dependency`](https://developers.home-assistant.io/docs/core/integration-quality-scale/rules/async-dependency) | Modbus communication uses asynchronous APIs; blocking path resolution is moved to an executor. [modbus_client.py](../../custom_components/meltem_ventilation/modbus_client.py), [`__init__.py`](../../custom_components/meltem_ventilation/__init__.py) |
| ➖ | [`inject-websession`](https://developers.home-assistant.io/docs/core/integration-quality-scale/rules/inject-websession) | The integration does not use an HTTP dependency or web session. |
| ✅ | [`strict-typing`](https://developers.home-assistant.io/docs/core/integration-quality-scale/rules/strict-typing) | [pyproject.toml](../../pyproject.toml) enables mypy strict mode; the integration uses the runtime-data-typed `MeltemIntegrationConfigEntry`. Unneeded `type: ignore[override]` directives were removed; the executor return value is narrowed to `str` at the Home Assistant boundary based on the typed worker. [models.py](../../custom_components/meltem_ventilation/models.py) |

## Verification

Tests and static checks were last run on October 16, 2026, on Windows with
Python 3.14.7 and the test dependencies pinned by the repository:

- **Coverage run:** 712 passed and one skipped. The skipped
  test resolves a `/dev/serial/by-id` symlink, which Windows cannot create
  without elevated rights; it runs on Linux.
- **Overall test coverage:** 99%. Every integration module except
  `modbus_helpers.py` is at 98% or above; that module reaches 86% only on
  Windows, where the skipped symlink test leaves the by-id resolution
  uncovered. The October 13 Linux run reported 96% for it.
- **`config_flow.py`, `write_confirmation.py`, and `device/transport.py`:**
  100% statement and 100% branch coverage.
- **Ruff:** `ruff format --check` and `ruff check` passed for
  `custom_components`, `tests`, and `tools`.
- **mypy --strict:** no findings in 28 source files; the Home Assistant
  executor's `Any` return value is treated as `str` at the call boundary based
  on the annotated worker result.
- **Pyright 1.1.414 (standard mode):** no findings in `custom_components`,
  `tests`, and `tools`; the entity modules disable only
  `reportIncompatibleVariableOverride`, because Home Assistant types entity
  state as `cached_property` while the integration overrides it with plain
  properties.
- **Hassfest and HACS validation:** not run locally for this audit; both jobs
  are configured in [ci.yml](../../.github/workflows/ci.yml).

The coverage figures come from
`pytest --cov=custom_components.meltem_ventilation --cov-report=term-missing`.

## Recommended next steps

1. **Complete Bronze for a Core candidacy:** provide brand assets that meet
   the requirements in the central `core_integrations/meltem_ventilation`
   directory.
2. **Improve Gold:** implement dynamic unit discovery so units added during
   operation appear automatically without a rescan and reload.
