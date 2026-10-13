# Home Assistant Integration Quality Scale

Stand der Repository-Prüfung: **13. Oktober 2026**.

Dieses Dokument hält eine interne Einschätzung anhand der offiziellen
[Quality Scale](https://developers.home-assistant.io/docs/core/integration-quality-scale/),
[Checkliste](https://developers.home-assistant.io/docs/core/integration-quality-scale/checklist)
und [Regeln](https://developers.home-assistant.io/docs/core/integration-quality-scale/rules)
fest. Es ist keine offizielle Bewertung durch das Home-Assistant-Core-Team.

## Ergebnis

Die Integration wird als Community-Integration über HACS verteilt und ist
deshalb formal in der Sonderkategorie **Custom**. Die Skalenstufen Bronze,
Silver, Gold und Platinum werden für Core-Integrationen vergeben. Eine
`quality_scale.yaml` mit einem angestrebten oder erreichten Tier liegt nicht
vor.

Würde die Integration als Kandidat für Home Assistant Core bewertet, wäre sie
nach dieser Prüfung **noch nicht Bronze-konform**. Der wesentliche offene
Bronze-Punkt ist eine Anleitung zum Entfernen der Integration.

Darüber hinaus scheitern einzelne Regeln höherer Stufen auch unabhängig davon,
ob Bronze bereits erreicht ist. Die Quality Scale ist kumulativ: Eine Stufe
gilt erst als erfüllt, wenn alle anwendbaren Regeln dieser und aller
niedrigeren Stufen erfüllt sind.

## Bewertungslegende

- ✅ **Erfüllt** – im Repository durch Code, Tests oder Dokumentation belegt.
- ⚠️ **Teilweise** – relevante Umsetzung vorhanden, aber die Regel ist noch
  nicht vollständig erfüllt.
- ❌ **Offen** – Anforderung fehlt oder ein messbarer Grenzwert wird verfehlt.
- ➖ **Nicht anwendbar** – die Regel greift für diese Integration nicht; Grund
  ist angegeben.

Die Regelbezeichnungen verlinken jeweils auf die offizielle Erläuterung.
Dateipfade verlinken auf die geprüften Stellen im Repository.

## Bronze

| Status | Regel | Ergebnis und Beleg |
|---|---|---|
| ➖ | [`action-setup`](https://developers.home-assistant.io/docs/core/integration-quality-scale/rules/action-setup) | Es werden keine Service-Actions angeboten, die in `async_setup` registriert werden müssten. |
| ✅ | [`appropriate-polling`](https://developers.home-assistant.io/docs/core/integration-quality-scale/rules/appropriate-polling) | Der Coordinator begrenzt die Request-Rate, staffelt Lesejobs und verwendet bei Transportfehlern Backoff. [coordinator.py](../../custom_components/meltem_ventilation/coordinator.py), [polling.py](../../custom_components/meltem_ventilation/polling.py) |
| ✅ | [`brands`](https://developers.home-assistant.io/docs/core/integration-quality-scale/rules/brands) | Branding-Dateien sind vorhanden: [icon.png](../../custom_components/meltem_ventilation/brand/icon.png) und [logo.png](../../custom_components/meltem_ventilation/brand/logo.png). |
| ✅ | [`common-modules`](https://developers.home-assistant.io/docs/core/integration-quality-scale/rules/common-modules) | Gemeinsame Verantwortlichkeiten sind in Module wie [coordinator.py](../../custom_components/meltem_ventilation/coordinator.py), [polling.py](../../custom_components/meltem_ventilation/polling.py) und [read_health.py](../../custom_components/meltem_ventilation/read_health.py) ausgelagert. |
| ✅ | [`config-flow-test-coverage`](https://developers.home-assistant.io/docs/core/integration-quality-scale/rules/config-flow-test-coverage) | Die Gesamtsuite deckt [config_flow.py](../../custom_components/meltem_ventilation/config_flow.py) zu 100 % ab. [test_config_flow.py](../../tests/test_config_flow.py) testet die Wiederherstellung nach Fehlern in User-, USB-, Options- und Reconfigure-Flows sowie, dass ein Gateway nicht doppelt eingerichtet werden kann. |
| ✅ | [`config-flow`](https://developers.home-assistant.io/docs/core/integration-quality-scale/rules/config-flow) | UI-Einrichtung, feldbezogene `data_description`-Texte für alle Config- und Options-Formularfelder sowie englische und deutsche Übersetzungen sind vorhanden. Verbindungs- und Geräteprofil-Konfiguration liegt in `ConfigEntry.data`; die Request-Rate wird als `ConfigEntry.options` behandelt. Die Migration übernimmt die bisher in `data` gespeicherte Rate nach `options`, ohne einen vorhandenen Optionswert zu überschreiben. [strings.json](../../custom_components/meltem_ventilation/strings.json), [config_flow.py](../../custom_components/meltem_ventilation/config_flow.py), [__init__.py](../../custom_components/meltem_ventilation/__init__.py) |
| ✅ | [`dependency-transparency`](https://developers.home-assistant.io/docs/core/integration-quality-scale/rules/dependency-transparency) | Abhängigkeiten sind in [manifest.json](../../custom_components/meltem_ventilation/manifest.json) deklariert. Die geprüfte Laufzeitabhängigkeit `modbus-connection` ist auf PyPI und in einem öffentlichen Repository mit OSI-Lizenz, Release-Tag und CI verfügbar. |
| ➖ | [`docs-actions`](https://developers.home-assistant.io/docs/core/integration-quality-scale/rules/docs-actions) | Es werden keine Service-Actions angeboten. |
| ✅ | [`docs-triggers`](https://developers.home-assistant.io/docs/core/integration-quality-scale/rules/docs-triggers) | Die eigene [Trigger-Anleitung](../TRIGGER_FILTER_CHANGE_DUE.md) erklärt Zweck und Zielauswahl, beschreibt `behavior` und `for`, enthält UI-Schritte und ein verwendbares YAML-Beispiel. Die [README.md](../../README.md) verlinkt die Seite. |
| ✅ | [`docs-conditions`](https://developers.home-assistant.io/docs/core/integration-quality-scale/rules/docs-conditions) | Die eigene [Condition-Anleitung](../CONDITION_FILTER_CHANGE_DUE.md) erklärt Zweck und Parameter (`behavior`, `for`), enthält UI-Schritte und ein YAML-Beispiel. Die [README.md](../../README.md) verlinkt die Seite. |
| ✅ | [`docs-high-level-description`](https://developers.home-assistant.io/docs/core/integration-quality-scale/rules/docs-high-level-description) | Die [README.md](../../README.md) erklärt Zweck, Hersteller, Gateway und Produktfamilien. |
| ✅ | [`docs-installation-instructions`](https://developers.home-assistant.io/docs/core/integration-quality-scale/rules/docs-installation-instructions) | Die [README.md](../../README.md) erklärt Voraussetzungen sowie HACS- und manuelle Installation. |
| ❌ | [`docs-removal-instructions`](https://developers.home-assistant.io/docs/core/integration-quality-scale/rules/docs-removal-instructions) | Eine Anleitung zum Entfernen der Integration ist in der [README.md](../../README.md) nicht vorhanden. |
| ➖ | [`entity-event-setup`](https://developers.home-assistant.io/docs/core/integration-quality-scale/rules/entity-event-setup) | Die Integration abonniert keine Entity-Events, für die Lifecycle-Registrierung erforderlich wäre. |
| ✅ | [`entity-unique-id`](https://developers.home-assistant.io/docs/core/integration-quality-scale/rules/entity-unique-id) | Room-Entities erhalten stabile IDs über die gemeinsame [entity.py](../../custom_components/meltem_ventilation/entity.py)-Basisklasse. |
| ✅ | [`has-entity-name`](https://developers.home-assistant.io/docs/core/integration-quality-scale/rules/has-entity-name) | Die gemeinsame Entity-Basisklasse setzt `has_entity_name`; der zusätzliche Sensor setzt es ebenfalls. [entity.py](../../custom_components/meltem_ventilation/entity.py), [sensor.py](../../custom_components/meltem_ventilation/sensor.py) |
| ✅ | [`runtime-data`](https://developers.home-assistant.io/docs/core/integration-quality-scale/rules/runtime-data) | Coordinator und Laufzeitdaten werden über `ConfigEntry.runtime_data` verwaltet. [__init__.py](../../custom_components/meltem_ventilation/__init__.py), [models.py](../../custom_components/meltem_ventilation/models.py) |
| ✅ | [`test-before-configure`](https://developers.home-assistant.io/docs/core/integration-quality-scale/rules/test-before-configure) | Der Config Flow prüft den Port und scannt/probt das Gateway vor dem Erstellen des Eintrags. [config_flow.py](../../custom_components/meltem_ventilation/config_flow.py) |
| ✅ | [`test-before-setup`](https://developers.home-assistant.io/docs/core/integration-quality-scale/rules/test-before-setup) | Das Gateway wird beim Setup validiert; die ersten Lesevorgänge werden gestartet. [__init__.py](../../custom_components/meltem_ventilation/__init__.py) |
| ✅ | [`unique-config-entry`](https://developers.home-assistant.io/docs/core/integration-quality-scale/rules/unique-config-entry) | Der Port dient als eindeutige ID; außerdem ist `single_config_entry` im [manifest.json](../../custom_components/meltem_ventilation/manifest.json) gesetzt. |

Bei `config-flow` gehören auch die Teilanforderungen dazu: Kontext für
Eingabefelder über `data_description` bereitzustellen und `ConfigEntry.data`
und `ConfigEntry.options` zweckgemäß zu verwenden. Die erste Teilanforderung
ist offen, die zweite ist erfüllt.

## Silver

| Status | Regel | Ergebnis und Beleg |
|---|---|---|
| ➖ | [`action-exceptions`](https://developers.home-assistant.io/docs/core/integration-quality-scale/rules/action-exceptions) | Es werden keine Service-Actions angeboten. |
| ✅ | [`config-entry-unloading`](https://developers.home-assistant.io/docs/core/integration-quality-scale/rules/config-entry-unloading) | Beim Unload werden Plattformen entladen und Coordinator sowie Client heruntergefahren. [__init__.py](../../custom_components/meltem_ventilation/__init__.py) |
| ✅ | [`docs-configuration-parameters`](https://developers.home-assistant.io/docs/core/integration-quality-scale/rules/docs-configuration-parameters) | Die [README.md](../../README.md) beschreibt Portwechsel, Profilbearbeitung, Request-Rate und Gateway-Rescan. |
| ✅ | [`docs-installation-parameters`](https://developers.home-assistant.io/docs/core/integration-quality-scale/rules/docs-installation-parameters) | Gateway, USB-Verbindung, Voraussetzungen, Port und Profilwahl werden in der [README.md](../../README.md) erklärt. |
| ✅ | [`entity-unavailable`](https://developers.home-assistant.io/docs/core/integration-quality-scale/rules/entity-unavailable) | Entities werden bei nicht verfügbaren Räumen oder veralteten Lesegruppen als nicht verfügbar markiert. [entity.py](../../custom_components/meltem_ventilation/entity.py) |
| ✅ | [`integration-owner`](https://developers.home-assistant.io/docs/core/integration-quality-scale/rules/integration-owner) | `codeowners` ist im [manifest.json](../../custom_components/meltem_ventilation/manifest.json) gesetzt. |
| ⚠️ | [`log-when-unavailable`](https://developers.home-assistant.io/docs/core/integration-quality-scale/rules/log-when-unavailable) | Wiederherstellung wird einmalig auf Info-Level protokolliert. Fehlgeschlagene Lesejobs werden dagegen als Warnings geloggt; ein einmaliges Info-Log beim Übergang in den nicht erreichbaren Zustand fehlt. [coordinator.py](../../custom_components/meltem_ventilation/coordinator.py) |
| ✅ | [`parallel-updates`](https://developers.home-assistant.io/docs/core/integration-quality-scale/rules/parallel-updates) | Die Entity-Plattformen setzen `PARALLEL_UPDATES = 0`; die Serialisierung erfolgt zentral im Coordinator. Beispiele: [fan.py](../../custom_components/meltem_ventilation/fan.py), [sensor.py](../../custom_components/meltem_ventilation/sensor.py). |
| ➖ | [`reauthentication-flow`](https://developers.home-assistant.io/docs/core/integration-quality-scale/rules/reauthentication-flow) | Es gibt keine Zugangsdaten oder Authentifizierung, die erneuert werden könnten. |
| ❌ | [`test-coverage`](https://developers.home-assistant.io/docs/core/integration-quality-scale/rules/test-coverage) | Die Regel verlangt über 95 % Abdeckung für jedes Integrationsmodul. Die Gesamtdeckung ist zwar 99 %, [write_confirmation.py](../../custom_components/meltem_ventilation/write_confirmation.py) liegt aber bei 94 %. |

## Gold

| Status | Regel | Ergebnis und Beleg |
|---|---|---|
| ✅ | [`devices`](https://developers.home-assistant.io/docs/core/integration-quality-scale/rules/devices) | Gateway und konfigurierte Lüftungsgeräte werden als Geräte registriert. [entity.py](../../custom_components/meltem_ventilation/entity.py) |
| ✅ | [`diagnostics`](https://developers.home-assistant.io/docs/core/integration-quality-scale/rules/diagnostics) | Diagnostics sind implementiert; der serielle Port wird aus den Ausgaben redigiert. [diagnostics.py](../../custom_components/meltem_ventilation/diagnostics.py) |
| ➖ | [`discovery-update-info`](https://developers.home-assistant.io/docs/core/integration-quality-scale/rules/discovery-update-info) | Die Verbindung erfolgt lokal über USB/seriellen Port; eine dynamische IP- oder Netzwerkadresse gibt es nicht zu aktualisieren. |
| ✅ | [`discovery`](https://developers.home-assistant.io/docs/core/integration-quality-scale/rules/discovery) | USB-Discovery und Scan der Units über das Gateway sind implementiert. [manifest.json](../../custom_components/meltem_ventilation/manifest.json), [config_flow.py](../../custom_components/meltem_ventilation/config_flow.py) |
| ⚠️ | [`docs-data-update`](https://developers.home-assistant.io/docs/core/integration-quality-scale/rules/docs-data-update) | Die [README.md](../../README.md) erklärt die konfigurierbare Request-Rate und den gestaffelten Start. Der Default von 4 Requests/s und die konkrete Update-Strategie bzw. deren Grenzen sind nicht vollständig beschrieben. [const.py](../../custom_components/meltem_ventilation/const.py), [coordinator.py](../../custom_components/meltem_ventilation/coordinator.py) |
| ❌ | [`docs-examples`](https://developers.home-assistant.io/docs/core/integration-quality-scale/rules/docs-examples) | Es sind keine verwendbaren Automations-YAML-Beispiele oder Blueprints dokumentiert. |
| ✅ | [`docs-known-limitations`](https://developers.home-assistant.io/docs/core/integration-quality-scale/rules/docs-known-limitations) | Nicht verifizierte Hardwareprofile und die erforderliche manuelle Profilwahl werden in der [README.md](../../README.md) als Einschränkungen benannt. |
| ✅ | [`docs-supported-devices`](https://developers.home-assistant.io/docs/core/integration-quality-scale/rules/docs-supported-devices) | Die [README.md](../../README.md) listet die unterstützten Profile und grenzt die Hardware ab, mit der getestet wurde. |
| ✅ | [`docs-supported-functions`](https://developers.home-assistant.io/docs/core/integration-quality-scale/rules/docs-supported-functions) | Die [README.md](../../README.md) beschreibt Sensoren, Fan-Entities, Modi, Schalter, Schwellenwerte und Optionen. |
| ✅ | [`docs-troubleshooting`](https://developers.home-assistant.io/docs/core/integration-quality-scale/rules/docs-troubleshooting) | Die [README.md](../../README.md) enthält Hilfen zu fehlenden bzw. nicht erreichbaren Units, Portkonflikten und Logs. |
| ✅ | [`docs-use-cases`](https://developers.home-assistant.io/docs/core/integration-quality-scale/rules/docs-use-cases) | Die Bedienungsbeschreibung in der [README.md](../../README.md) erläutert praktische Fälle wie balancierten/einseitigen Betrieb, Sensorsteuerung und Intensivlüftung. |
| ❌ | [`dynamic-devices`](https://developers.home-assistant.io/docs/core/integration-quality-scale/rules/dynamic-devices) | Nachträglich konfigurierte Units erscheinen nicht automatisch während des Betriebs. Nutzer müssen „Scan for new units“ ausführen und Profile auswählen. [README.md](../../README.md), [config_flow.py](../../custom_components/meltem_ventilation/config_flow.py) |
| ✅ | [`entity-category`](https://developers.home-assistant.io/docs/core/integration-quality-scale/rules/entity-category) | Diagnose- und Konfigurations-Entities sind entsprechend kategorisiert. [binary_sensor.py](../../custom_components/meltem_ventilation/binary_sensor.py), [number.py](../../custom_components/meltem_ventilation/number.py) |
| ✅ | [`entity-device-class`](https://developers.home-assistant.io/docs/core/integration-quality-scale/rules/entity-device-class) | Für Sensoren und Binary-Sensoren werden passende Device-Classes verwendet. [sensor.py](../../custom_components/meltem_ventilation/sensor.py), [binary_sensor.py](../../custom_components/meltem_ventilation/binary_sensor.py) |
| ✅ | [`entity-disabled-by-default`](https://developers.home-assistant.io/docs/core/integration-quality-scale/rules/entity-disabled-by-default) | Weniger zentrale Diagnose- und Betriebsstunden-Entities sind standardmäßig deaktiviert. [sensor.py](../../custom_components/meltem_ventilation/sensor.py) |
| ✅ | [`entity-translations`](https://developers.home-assistant.io/docs/core/integration-quality-scale/rules/entity-translations) | Entity-Namen sind über englische und deutsche Übersetzungen lokalisiert. [en.json](../../custom_components/meltem_ventilation/translations/en.json), [de.json](../../custom_components/meltem_ventilation/translations/de.json) |
| ✅ | [`exception-translations`](https://developers.home-assistant.io/docs/core/integration-quality-scale/rules/exception-translations) | Integrationsspezifische Exception-Meldungen sind in den Übersetzungen vorhanden. [strings.json](../../custom_components/meltem_ventilation/strings.json), [de.json](../../custom_components/meltem_ventilation/translations/de.json) |
| ❌ | [`icon-translations`](https://developers.home-assistant.io/docs/core/integration-quality-scale/rules/icon-translations) | Eigene Icons werden im Code gesetzt, aber eine `icons.json` mit Icon-Übersetzungen fehlt. Beispiele: [fan.py](../../custom_components/meltem_ventilation/fan.py), [binary_sensor.py](../../custom_components/meltem_ventilation/binary_sensor.py). |
| ✅ | [`reconfiguration-flow`](https://developers.home-assistant.io/docs/core/integration-quality-scale/rules/reconfiguration-flow) | Der serielle Port kann per Reconfigure-Flow geändert werden; Profile und neue Units sind über den Options-Flow bearbeitbar. [config_flow.py](../../custom_components/meltem_ventilation/config_flow.py) |
| ➖ | [`repair-issues`](https://developers.home-assistant.io/docs/core/integration-quality-scale/rules/repair-issues) | Bei der Prüfung wurde kein konkreter Fall gefunden, in dem ein persistentes Repair-Issue für eine vom Nutzer zu behebende Störung erforderlich ist. Verbindungsprobleme werden im Setup/Options-Flow und über Read Health gemeldet. |
| ✅ | [`stale-devices`](https://developers.home-assistant.io/docs/core/integration-quality-scale/rules/stale-devices) | Geräte nicht mehr konfigurierter Units werden beim Setup entfernt. [__init__.py](../../custom_components/meltem_ventilation/__init__.py) |

## Platinum

| Status | Regel | Ergebnis und Beleg |
|---|---|---|
| ✅ | [`async-dependency`](https://developers.home-assistant.io/docs/core/integration-quality-scale/rules/async-dependency) | Die Modbus-Kommunikation wird über asynchrone APIs ausgeführt; blockierende Pfadauflösung wird in einen Executor ausgelagert. [modbus_client.py](../../custom_components/meltem_ventilation/modbus_client.py), [__init__.py](../../custom_components/meltem_ventilation/__init__.py) |
| ➖ | [`inject-websession`](https://developers.home-assistant.io/docs/core/integration-quality-scale/rules/inject-websession) | Die Integration verwendet keine HTTP-Abhängigkeit bzw. Websession. |
| ❌ | [`strict-typing`](https://developers.home-assistant.io/docs/core/integration-quality-scale/rules/strict-typing) | Der vorhandene mypy-Lauf ist erfolgreich, aber [pyproject.toml](../../pyproject.toml) aktiviert keinen Strict-Modus. `check_untyped_defs = true` allein erfüllt die Regel nicht. |

## Verifikation

Die Tests und statischen Prüfungen wurden am 13. Oktober 2026 in einer
isolierten Umgebung mit Python 3.14.7 und den im Repository festgelegten
Testabhängigkeiten ausgeführt:

- **Pytest:** 661 bestanden.
- **Gesamte Testabdeckung:** 99 %.
- **`config_flow.py`:** 100 %.
- **`write_confirmation.py`:** 94 %; verfehlt die Silver-Anforderung von über
  95 % je Modul.
- **Ruff:** alle Prüfungen bestanden.
- **mypy:** keine Befunde in 28 Quelldateien.
- **Hassfest und HACS-Validierung:** in diesem Audit nicht lokal ausgeführt;
  beide Jobs sind in [ci.yml](../../.github/workflows/ci.yml) konfiguriert.

Die Coverage-Zahlen stammen aus `pytest --cov=custom_components.meltem_ventilation
--cov-report=term-missing:skip-covered`. Für den Audit wurden Abhängigkeiten in
einer temporären Umgebung installiert und diese anschließend entfernt; es
wurden keine Projektdateien verändert.

## Empfohlene nächste Schritte

1. **Bronze vervollständigen:** `data_description`-Texte ergänzen, die
   ungetesteten Flow-Zweige abdecken sowie eine Anleitung zum Entfernen
   hinzufügen.
2. **Silver absichern:** `write_confirmation.py` auf über 95 % bringen und
   beim Übergang in den Ausfallzustand ein einmaliges Info-Log ergänzen.
3. **Gold verbessern:** nutzbare Automationsbeispiele, präzise
   Polling-/Update-Angaben und `icons.json` ergänzen. Für automatische
   Geräteaufnahme müsste außerdem das Hinzufügen neuer Units während des
   Betriebs umgesetzt werden.
4. **Platinum vorbereiten:** striktes Typing schrittweise einführen und die
   verbleibenden Typisierungsbefunde beheben.
