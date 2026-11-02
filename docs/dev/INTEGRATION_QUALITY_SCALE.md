# Home Assistant Integration Quality Scale

Repository review date: **November 2, 2026**.

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
| ✅ | [`config-flow-test-coverage`](https://developers.home-assistant.io/docs/core/integration-quality-scale/rules/config-flow-test-coverage) | The full test suite covers [config_flow.py](../../custom_components/meltem_ventilation/config_flow.py) and [subentry_flow.py](../../custom_components/meltem_ventilation/subentry_flow.py) at 100%, [options_flow.py](../../custom_components/meltem_ventilation/options_flow.py) at 99%, and [flow_helpers.py](../../custom_components/meltem_ventilation/flow_helpers.py) at 99%. [test_config_flow.py](../../tests/components/meltem_ventilation/test_config_flow.py) tests recovery from errors in the user, USB, options, and reconfigure flows, as well as preventing the same gateway from being configured twice. |
| ✅ | [`config-flow`](https://developers.home-assistant.io/docs/core/integration-quality-scale/rules/config-flow) | UI setup, field-level `data_description` text for every config and options form field, and English and German translations are provided. The gateway connection (legacy serial path or transport, host, and TCP port) is stored in `ConfigEntry.data`, each unit with its profile in a config subentry, and the request rate in `ConfigEntry.options`. The migration moves the existing rate from `data` to `options` without overwriting an existing options value. [strings.json](../../custom_components/meltem_ventilation/strings.json), [config_flow.py](../../custom_components/meltem_ventilation/config_flow.py), [options_flow.py](../../custom_components/meltem_ventilation/options_flow.py), [migration.py](../../custom_components/meltem_ventilation/migration.py) |
| ✅ | [`dependency-transparency`](https://developers.home-assistant.io/docs/core/integration-quality-scale/rules/dependency-transparency) | Dependencies are declared in [manifest.json](../../custom_components/meltem_ventilation/manifest.json). The reviewed runtime dependency `modbus-connection` is available on PyPI and in a public repository with an OSI license, release tag, and CI. |
| ➖ | [`docs-actions`](https://developers.home-assistant.io/docs/core/integration-quality-scale/rules/docs-actions) | No service actions are provided. |
| ✅ | [`docs-triggers`](https://developers.home-assistant.io/docs/core/integration-quality-scale/rules/docs-triggers) | The [trigger guide](../TRIGGER_FILTER_CHANGE_DUE.md) explains its purpose and target selection, describes `behavior` and `for`, and includes UI steps and a usable YAML example. The [README.md](../../README.md) links to it. |
| ✅ | [`docs-conditions`](https://developers.home-assistant.io/docs/core/integration-quality-scale/rules/docs-conditions) | The [condition guide](../CONDITION_FILTER_CHANGE_DUE.md) explains its purpose and parameters (`behavior`, `for`), and includes UI steps and a YAML example. The [README.md](../../README.md) links to it. |
| ✅ | [`docs-high-level-description`](https://developers.home-assistant.io/docs/core/integration-quality-scale/rules/docs-high-level-description) | The [README.md](../../README.md) explains the integration's purpose, manufacturer, gateway, and product families. |
| ✅ | [`docs-installation-instructions`](https://developers.home-assistant.io/docs/core/integration-quality-scale/rules/docs-installation-instructions) | The [README.md](../../README.md) explains the requirements and HACS and manual installation. |
| ✅ | [`docs-removal-instructions`](https://developers.home-assistant.io/docs/core/integration-quality-scale/rules/docs-removal-instructions) | The [removal guide](../REMOVE_INTEGRATION.md) explains how to remove the config entry and uninstall through HACS or manually. It is linked from the [README.md](../../README.md). |
| ➖ | [`entity-event-setup`](https://developers.home-assistant.io/docs/core/integration-quality-scale/rules/entity-event-setup) | The integration does not subscribe to entity events that require lifecycle registration. |
| ✅ | [`entity-unique-id`](https://developers.home-assistant.io/docs/core/integration-quality-scale/rules/entity-unique-id) | Unit and gateway entities receive stable unique IDs that include the config entry ID ([identifiers.py](../../custom_components/meltem_ventilation/identifiers.py)); the migration to config entry `2.1` moved existing entities to them. |
| ✅ | [`has-entity-name`](https://developers.home-assistant.io/docs/core/integration-quality-scale/rules/has-entity-name) | The shared entity base class sets `has_entity_name`; the additional sensor sets it as well. [entity.py](../../custom_components/meltem_ventilation/entity.py), [sensor.py](../../custom_components/meltem_ventilation/sensor.py) |
| ✅ | [`runtime-data`](https://developers.home-assistant.io/docs/core/integration-quality-scale/rules/runtime-data) | The coordinator and runtime data are managed through `ConfigEntry.runtime_data`. [`__init__.py`](../../custom_components/meltem_ventilation/__init__.py), [models.py](../../custom_components/meltem_ventilation/models.py) |
| ✅ | [`test-before-configure`](https://developers.home-assistant.io/docs/core/integration-quality-scale/rules/test-before-configure) | The config flow validates the port and scans and probes the gateway before creating the entry. [config_flow.py](../../custom_components/meltem_ventilation/config_flow.py), [flow_helpers.py](../../custom_components/meltem_ventilation/flow_helpers.py) |
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
| ✅ | [`test-coverage`](https://developers.home-assistant.io/docs/core/integration-quality-scale/rules/test-coverage) | The CI test run fails below 98% statement and branch coverage ([ci.yml](../../.github/workflows/ci.yml), threshold in [pyproject.toml](../../pyproject.toml)). On Windows, where the POSIX tests are skipped, overall coverage is 98.5% (99% of statements); [config_flow.py](../../custom_components/meltem_ventilation/config_flow.py) is at 100% statement and branch coverage. |

## Gold

| Status | Rule | Assessment and evidence |
| --- | --- | --- |
| ✅ | [`devices`](https://developers.home-assistant.io/docs/core/integration-quality-scale/rules/devices) | The gateway and configured ventilation units are registered as devices. [entity.py](../../custom_components/meltem_ventilation/entity.py) |
| ✅ | [`diagnostics`](https://developers.home-assistant.io/docs/core/integration-quality-scale/rules/diagnostics) | Diagnostics are implemented; the serial device path, network hostname, and app setup codes of the units are redacted from the output. [diagnostics.py](../../custom_components/meltem_ventilation/diagnostics.py) |
| ➖ | [`discovery-update-info`](https://developers.home-assistant.io/docs/core/integration-quality-scale/rules/discovery-update-info) | Serial devices can be discovered over USB; RTU-over-TCP endpoints are entered manually and can be changed through Reconfigure. The integration does not discover gateway software updates. |
| ✅ | [`discovery`](https://developers.home-assistant.io/docs/core/integration-quality-scale/rules/discovery) | USB discovery and gateway-based unit scanning are implemented. [manifest.json](../../custom_components/meltem_ventilation/manifest.json), [config_flow.py](../../custom_components/meltem_ventilation/config_flow.py), [flow_helpers.py](../../custom_components/meltem_ventilation/flow_helpers.py) |
| ✅ | [`docs-data-update`](https://developers.home-assistant.io/docs/core/integration-quality-scale/rules/docs-data-update) | The [README.md](../../README.md) specifies the default rate of 4 requests/s and its range, documents the normal intervals for each data group and the staggered scheduler, and explains the effects of more units, a lower rate, and transport backoff. [const.py](../../custom_components/meltem_ventilation/const.py), [polling.py](../../custom_components/meltem_ventilation/polling.py), [coordinator.py](../../custom_components/meltem_ventilation/coordinator.py) |
| ✅ | [`docs-examples`](https://developers.home-assistant.io/docs/core/integration-quality-scale/rules/docs-examples) | The [README.md](../../README.md) contains an executable YAML automation for intensive ventilation while a room is occupied; the filter-change [trigger](../TRIGGER_FILTER_CHANGE_DUE.md) and [condition](../CONDITION_FILTER_CHANGE_DUE.md) guides include additional reminder examples. |
| ✅ | [`docs-known-limitations`](https://developers.home-assistant.io/docs/core/integration-quality-scale/rules/docs-known-limitations) | The [README.md](../../README.md) identifies unverified hardware profiles and the need to select profiles manually as limitations. |
| ✅ | [`docs-supported-devices`](https://developers.home-assistant.io/docs/core/integration-quality-scale/rules/docs-supported-devices) | The [README.md](../../README.md) lists the supported profiles and specifies which hardware has been tested. |
| ✅ | [`docs-supported-functions`](https://developers.home-assistant.io/docs/core/integration-quality-scale/rules/docs-supported-functions) | The [README.md](../../README.md) describes sensors, fan entities, modes, switches, thresholds, and options. |
| ✅ | [`docs-troubleshooting`](https://developers.home-assistant.io/docs/core/integration-quality-scale/rules/docs-troubleshooting) | The [README.md](../../README.md) provides help for missing or unreachable units, port conflicts, and logs. |
| ✅ | [`docs-use-cases`](https://developers.home-assistant.io/docs/core/integration-quality-scale/rules/docs-use-cases) | The usage description in the [README.md](../../README.md) explains practical cases such as balanced or unbalanced operation, sensor control, and intensive ventilation. |
| ⚠️ | [`dynamic-devices`](https://developers.home-assistant.io/docs/core/integration-quality-scale/rules/dynamic-devices) | The gateway's unit list is checked hourly, and a unit that is not set up raises a repair issue; **Add unit** adds it as a subentry. Units are not added without the user, because only the type plate settles the profile, and a wrong profile creates wrong entities and airflow scaling. [new_units.py](../../custom_components/meltem_ventilation/new_units.py), [subentry_flow.py](../../custom_components/meltem_ventilation/subentry_flow.py) |
| ✅ | [`entity-category`](https://developers.home-assistant.io/docs/core/integration-quality-scale/rules/entity-category) | Diagnostic and configuration entities are categorized appropriately. [binary_sensor.py](../../custom_components/meltem_ventilation/binary_sensor.py), [number.py](../../custom_components/meltem_ventilation/number.py) |
| ✅ | [`entity-device-class`](https://developers.home-assistant.io/docs/core/integration-quality-scale/rules/entity-device-class) | Appropriate device classes are used for sensors and binary sensors. [sensor.py](../../custom_components/meltem_ventilation/sensor.py), [binary_sensor.py](../../custom_components/meltem_ventilation/binary_sensor.py) |
| ✅ | [`entity-disabled-by-default`](https://developers.home-assistant.io/docs/core/integration-quality-scale/rules/entity-disabled-by-default) | Less central diagnostic and operating-hours entities are disabled by default. [sensor.py](../../custom_components/meltem_ventilation/sensor.py) |
| ✅ | [`entity-translations`](https://developers.home-assistant.io/docs/core/integration-quality-scale/rules/entity-translations) | Entity names are localized in English and German. [en.json](../../custom_components/meltem_ventilation/translations/en.json), [de.json](../../custom_components/meltem_ventilation/translations/de.json) |
| ✅ | [`exception-translations`](https://developers.home-assistant.io/docs/core/integration-quality-scale/rules/exception-translations) | Integration-specific exception messages are included in the translations. [strings.json](../../custom_components/meltem_ventilation/strings.json), [de.json](../../custom_components/meltem_ventilation/translations/de.json) |
| ✅ | [`icon-translations`](https://developers.home-assistant.io/docs/core/integration-quality-scale/rules/icon-translations) | All integration-specific icons are defined in [icons.json](../../custom_components/meltem_ventilation/icons.json) using the entity translation keys; device-class icons are left to Home Assistant defaults. |
| ✅ | [`reconfiguration-flow`](https://developers.home-assistant.io/docs/core/integration-quality-scale/rules/reconfiguration-flow) | The gateway connection can be changed or switched between serial and RTU over TCP through Reconfigure; each unit profile can be changed through its subentry. [config_flow.py](../../custom_components/meltem_ventilation/config_flow.py), [subentry_flow.py](../../custom_components/meltem_ventilation/subentry_flow.py) |
| ✅ | [`repair-issues`](https://developers.home-assistant.io/docs/core/integration-quality-scale/rules/repair-issues) | Two user-fixable cases raise translated repair issues: units the gateway reports but that are not set up ([new_units.py](../../custom_components/meltem_ventilation/new_units.py)), and a sensor of the selected profile that keeps reporting no value, which usually means a wrong profile ([missing_sensors.py](../../custom_components/meltem_ventilation/missing_sensors.py)). Both disappear once the cause is gone. Connection problems are reported during setup and through Read health. |
| ✅ | [`stale-devices`](https://developers.home-assistant.io/docs/core/integration-quality-scale/rules/stale-devices) | Deleting a unit subentry removes its device; a rescan removes the subentries of units the gateway no longer reports after the user confirms them, and setup removes devices of units that are no longer configured. [`__init__.py`](../../custom_components/meltem_ventilation/__init__.py), [options_flow.py](../../custom_components/meltem_ventilation/options_flow.py) |

## Platinum

| Status | Rule | Assessment and evidence |
| --- | --- | --- |
| ✅ | [`async-dependency`](https://developers.home-assistant.io/docs/core/integration-quality-scale/rules/async-dependency) | Modbus communication uses asynchronous APIs; blocking path resolution is moved to an executor. [modbus_client.py](../../custom_components/meltem_ventilation/modbus_client.py), [`__init__.py`](../../custom_components/meltem_ventilation/__init__.py) |
| ➖ | [`inject-websession`](https://developers.home-assistant.io/docs/core/integration-quality-scale/rules/inject-websession) | The integration does not use an HTTP dependency or web session. |
| ✅ | [`strict-typing`](https://developers.home-assistant.io/docs/core/integration-quality-scale/rules/strict-typing) | [pyproject.toml](../../pyproject.toml) enables mypy strict mode; the integration uses the runtime-data-typed `MeltemIntegrationConfigEntry`. Unneeded `type: ignore[override]` directives were removed; the executor return value is narrowed to `str` at the Home Assistant boundary based on the typed worker. [models.py](../../custom_components/meltem_ventilation/models.py) |

## Verification

Tests and static checks were last run on November 2, 2026, on Windows with
Python 3.14 and the test dependencies pinned by the repository:

- **Coverage run:** 1,133 passed and 25 skipped. The skipped tests are the 24
  POSIX pseudo-terminal RTU protocol tests in `test_serial_protocol.py` and the
  `/dev/serial/by-id` symlink test, which run on Linux. The run includes
  snapshot tests of every entity and of the diagnostics download.
- **Overall test coverage:** 98.5% of statements and branches; CI enforces at
  least 98%. Modules below 98% on Windows: `modbus_helpers.py` (90%, the
  skipped symlink test leaves the by-id resolution uncovered),
  `cumulative_diagnostics.py` (94%), `device/device.py` (95%), `sensor.py`
  (96%), and `binary_sensor.py` (97%).
- **Flows:** `config_flow.py`, `subentry_flow.py`, and `migration.py` at 100%
  of statements and branches, `options_flow.py` and `flow_helpers.py` at 99%.
- **Ruff:** `ruff format --check` and `ruff check` passed for
  `custom_components`, `tests`, and `tools`.
- **mypy --strict:** no findings in 40 source files; the Home Assistant
  executor's `Any` return value is treated as `str` at the call boundary based
  on the annotated worker result.
- **Pyright 1.1.414 (standard mode):** not run locally after the last changes
  of this review, since npm could not fetch it; the CI job runs it on
  `custom_components`, `tests`, and `tools`. The entity modules disable only
  `reportIncompatibleVariableOverride`, because Home Assistant types entity
  state as `cached_property` while the integration overrides it with plain
  properties.
- **Markdownlint, Hassfest, and HACS validation:** not run locally for this
  audit; all three jobs are configured in
  [ci.yml](../../.github/workflows/ci.yml).

The coverage figures come from `pytest --cov --cov-report=term`, the command
CI runs; the measured source and the threshold are configured in
[pyproject.toml](../../pyproject.toml).

## Recommended next steps

1. **Complete Bronze for a Core candidacy:** provide brand assets that meet
   the requirements in the central `core_integrations/meltem_ventilation`
   directory.
2. **Improve Gold:** decide whether a unit the gateway reports may be added
   automatically with a provisional profile, which would complete
   `dynamic-devices`; today a repair issue asks the user to add it.
