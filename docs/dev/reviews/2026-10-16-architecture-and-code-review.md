# Hypothesengetriebenes Architektur- und Code-Review: `meltem-ha` (v4.2.0)

Review-Datum: **2026-10-16**
Geprüfter Stand: Version `4.2.0`

Dieses Dokument enthält alle Befunde des Bottom-Up-Architektur- und Code-Reviews von den Rohdaten auf dem Draht ([`docs/dev/measurements/`](../measurements/) und [`tools/`](../../../tools/)) über die Protokoll- und Koordinationsschicht bis zu den Home-Assistant-Plattformen, der Test-Suite ([`tests/`](../../../tests/)) und der Dokumentation.

Die Befunde sind strikt nach Schweregrad priorisiert:

- **P1:** Kaputte Zustandsmaschinen, Race Conditions & Datenkorruption
- **P2:** Protokoll- und Hardware-Fehlabgleiche
- **P3:** Event-Loop-, Lock- und Lifecycle-Risiken
- **P4:** Mock-Tautologien & Scheinsicherheit in der Test-Suite
- **P5:** Home Assistant Quality Scale (IQS), Schema- und Dokumentations-Konsistenz

---

## 0. Synthese der Rohdaten auf dem Draht (`docs/dev/measurements/` & `tools/`)

Aus den realen Gateway-Captures in [`readable-register-scan-R-2.txt`](../measurements/2026-10-02/readable-register-scan-R-2.txt), [`pe-compare-slave4.txt`](../measurements/2026-10-09/pe-compare-slave4.txt), [`pe-compare-slave5.txt`](../measurements/2026-10-09/pe-compare-slave5.txt), [`pe-identity-slave4.txt`](../measurements/2026-10-09/pe-identity-slave4.txt), [`pe-identity-slave5.txt`](../measurements/2026-10-09/pe-identity-slave5.txt), [`pe-product-40009-40010-slaves2-3-6-7.txt`](../measurements/2026-10-09/pe-product-40009-40010-slaves2-3-6-7.txt) sowie den JSON-Captures ([`intensive-duration-slave2-before-15m.json`](../measurements/2026-10-09/intensive-duration-slave2-before-15m.json), [`intensive-duration-slave2-after-20m.json`](../measurements/2026-10-09/intensive-duration-slave2-after-20m.json), [`intensive-duration-slave2-restored-15m.json`](../measurements/2026-10-09/intensive-duration-slave2-restored-15m.json)) ergeben sich folgende Rahmenbedingungen der `M-WRG-GW`-Bridge (Honeywell/Airios 868,3 MHz RF-Bridge über USB-RTU `19200 8E1`):

1. **Antwortzeiten & Pacing:** Isolierte Register-Reads dauern `~15,5–16,9 ms`, aufeinanderfolgende Reads mit `REQUEST_GAP_SECONDS = 0.1` (`100 ms`) liegen konstant bei `~115,9–116,1 ms` ([`pe-compare-slave4.txt`](../measurements/2026-10-09/pe-compare-slave4.txt)). Bei parallelem Schreib-/Lese-Verkehr steigt die Read-Latenz auf bis zu `134,4 ms`.
2. **Block-Read-Atomarität:** Ein `0x03`-Block-Read schlägt vollständig mit Modbus Exception `0x02` (`IllegalDataAddressError`) oder `0x05` (`AcknowledgeError`) fehl, sobald **ein einziges** Register im Fenster nicht lesbar ist (weshalb `window=10` in [`readable-register-scan-R-2.txt`](../measurements/2026-10-02/readable-register-scan-R-2.txt) ab Zeile 203 fast alle Registerinseln verdeckt).
3. **Sentinel-Werte nicht verbauter Sensoren:** Auf Basisgeräten (`ii_plain`, Slaves 2–7 in [`readable-register-scan-R-2.txt`](../measurements/2026-10-02/readable-register-scan-R-2.txt)) antworten nicht bestückte Sensoren **nicht** mit Modbus-Exceptions, sondern mit festen Sentinel-Rohwerten:
   - Fehlende Float32-Temperatursensoren (`41000..41001`, `41002..41003`, `41009..41010`): `[0, 32704]` (`0x7FC00000` = IEEE-754 `NaN`).
   - Vorhandener Fortluftsensor (`41004..41005`): z. B. `[2622, 16785]` = `18,13 °C` (Little-Endian Word-Order).
   - Fehlende Feuchtesensoren (`41006`, `41011`): **`239`** (`0x00EF`).
   - Fehlende CO2-/VOC-Sensoren (`41007`, `41013`) sowie ungenutzte Register (`41008`, `41012`): **`32767`** (`0x7FFF`).
4. **Asymmetrie zwischen Status-Map (`41100..41102`) und Set-Registern (`41120..41124`):**
   - `41120..41124` und `41132` sind vor einem ersten Schreibvorgang unlesbar (`null` bzw. `0x05` in [`intensive-duration-slave2-before-15m.json`](../measurements/2026-10-09/intensive-duration-slave2-before-15m.json)).
   - `41100..41102` ist immer lesbar, liefert aber für balancierte Zielwerte in `41101` oft die grobe App-Kodierung `200 + airflow / 10` (10er-Schritte: `204` = 40 m³/h, `206` = 60 m³/h) oder Schnellmodus-Codes (`228..230`) und im unbalancierten Betrieb (`41100 = 4`) auf Slave 4 sogar `[4, 0, 228]` ([`pe-compare-slave4.txt`](../measurements/2026-10-09/pe-compare-slave4.txt)).

---

## 1. Priorisierte Befunde mit Beweisführung

### [P1-1] Kaputte Fehler- und Verfügbarkeits-Zustandsmaschine: `MeltemModbusClient.read_room_state` verschluckt alle Geräte-Timeouts und Modbus-Exceptions, wodurch `_room_failures`, `_last_job_error` und Transport-Backoff im Echtbetrieb tot sind

- **Schweregrad:** Kritisch (Kaputte Zustandsmaschine / Stale-State-Erkennung & Transport-Backoff außer Kraft)
- **(a) Exakte Datei- und Zeilenreferenzen:**
  - [`modbus_client.py`](../../../custom_components/meltem_ventilation/modbus_client.py): `MeltemModbusClient.read_room_state` (Zeilen 276–302), `MeltemModbusClient._poll` (Zeilen 321–351), `MeltemModbusClient._read_mode_status` (Zeilen 467–508)
  - [`coordinator.py`](../../../custom_components/meltem_ventilation/coordinator.py): `MeltemDataUpdateCoordinator._room_unavailable_reason` (Zeilen 261–273), `MeltemDataUpdateCoordinator._async_update_data` (Zeilen 365–406), `MeltemDataUpdateCoordinator._read_one_job` (Zeilen 1130–1166)
  - [`device/transport.py`](../../../custom_components/meltem_ventilation/device/transport.py): `TransportPolicy.run` (Zeilen 101–147)
- **(b) Auslösende Aufruf- und Ereignissequenz:**
  1. In `MeltemDataUpdateCoordinator._read_one_job` (Zeilen 1138–1155) erwartet der Coordinator, dass ein fehlgeschlagener Poll-Job `MeltemModbusError` wirft, um `self._last_job_error = err` zu setzen, `self._room_failures[room.key]` hochzuzählen (Schwelle `ROOM_UNAVAILABLE_AFTER_FAILURES = 3`) und in `MeltemDataUpdateCoordinator._async_update_data` (Zeilen 394–397) `self._on_transport_failure()` für den exponentiellen Gateway-Backoff (`5 s .. 60 s`) aufzurufen.
  2. Tatsächlich fängt `MeltemModbusClient._poll` jedoch `ModbusTimeoutError` ab (`except ModbusTimeoutError as err:` in Zeile 334), und `device.async_poll()` aus `modbus_connection` fängt alle übrigen `ModbusError`-Subklassen (`AcknowledgeError` `0x05`, `GatewayTargetError` `0x0B`, `GatewayPathUnavailableError` `0x0A`, `ModbusProtocolError`) ab und legt sie lediglich in `report.failed` ab. Ebenso fängt `MeltemModbusClient._read_mode_status` in Zeilen 495–505 `ModbusTimeoutError`, `ModbusExceptionError` und `ModbusError` vollständig ab.
  3. Dadurch kehrt `MeltemModbusClient.read_room_state` bei **jedem** Timeout, **jedem** CRC-/Protokollfehler und **jeder** Modbus-Exception (`0x05`, `0x0A`, `0x0B`) normal ohne Exception zurück (nur `ModbusConnectionError` beim physischen Abziehen des USB-Steckers wirft `MeltemConnectionError`).
  4. In `MeltemDataUpdateCoordinator._read_one_job` wird daher immer der `else:`-Zweig (Zeilen 1156–1160) durchlaufen:
     - `self._last_job_error` wird auf `None` gesetzt (Diagnostics und System Health zeigen `"last_job_error": "none"`, selbst wenn alle Geräte Timeouts oder `0x05` liefern).
     - `self._room_failures.pop(room.key, None)` löscht den Fehlerzähler bei jedem fehlgeschlagenen Job (`_room_failures` erreicht bei Timeouts/Modbus-Fehlern **niemals** `ROOM_UNAVAILABLE_AFTER_FAILURES = 3`).
     - In `MeltemDataUpdateCoordinator._async_update_data` (Zeile 394) ist `self._last_job_error is None`, sodass bei einem komplett schweigenden/hängenden Gateway nach dem Startup bei jedem Timeout-Job `self._on_transport_success()` statt `self._on_transport_failure()` aufgerufen wird – der Transport-Backoff greift nie!
  5. **Verschärfung durch Gateway-Register `40101` (`rf_comm_status`):** Wenn nach einem Gateway-Reconnect (wie im realen H-3-Test, dokumentiert in [`HARDWARE_BACKLOG.md`](../HARDWARE_BACKLOG.md) Zeilen 575–578 und 629–630) alle Geräte-Blöcke (`41020`, `41004`, `41016`) minutenlang Exception `0x05` liefern, das Bridge-Register `40101` (`rf_comm_status`) im 60-Sekunden-`status`-Job aber antwortet, setzt `TransportPolicy.run` (Zeile 145) alle 60 s `self._last_read_answer[unit_id] = time.monotonic()`. Da `60 s < ROOM_SILENT_AFTER_SECONDS (120 s)` und `_room_failures` stets `0` bleibt, wird das Gerät **niemals** als `unavailable` markiert, und Steuer-Entitäten (`MeltemDirectionalFanEntity`, `MeltemOperationModeSelect`, `MeltemPresetModeSelect`, `MeltemIntensiveSwitch` mit `_requires_fresh_read_group = False`) bleiben dauerhaft `available = True`.
- **(c) Reproduzierender `pytest`-Beweis (mit echtem `MeltemModbusClient` statt `_FakeClient`):**

```python
async def test_real_client_timeouts_never_trigger_room_failures_or_backoff(
    hass: HomeAssistant,
) -> None:
    link = MockModbusConnection()
    unit = link.for_unit(2)
    unit.holding.update({41020: [30, 30], 41100: [3, 60, 0]})
    room = RoomConfig(key="unit_1", name="Unit 1", profile="ii_plain", slave=2)
    client = MeltemModbusClient(link.for_unit, port="/dev/ttyACM0")
    entry = MockConfigEntry(domain=DOMAIN, title="Meltem", source="user")
    entry.add_to_hass(hass)
    coordinator = MeltemDataUpdateCoordinator(
        hass, config_entry=entry, client=client, rooms=[room], max_requests_per_second=10.0
    )

    # 1. Erster Poll erfolgreich -> Raum hat gecachte Daten
    await coordinator.async_refresh()
    assert coordinator.room_available("unit_1") is True

    # 2. Ab jetzt schlagen ALLE Reads auf Slave 2 mit Timeout (oder 0x05) fehl
    unit.fail_requests(ModbusTimeoutError("unit silent"))
    for _ in range(5):
        for job in coordinator._jobs:
            job.next_due = 0.0
        coordinator._last_read_started = None
        await coordinator._async_update_data()

    # BUG: Trotz 5 fehlgeschlagener Jobs in Folge:
    assert coordinator._room_failures.get("unit_1", 0) == 0  # Sollte >= 3 sein!
    assert coordinator.last_job_error is None                # Sollte Fehler enthalten!
    assert coordinator._backoff_seconds is None              # Kein Backoff trotz Totalausfall!
    assert coordinator.room_available("unit_1") is True      # Raum bleibt fälschlich available!
```

---

### [P1-2] `OptimisticOverlay` besitzt keinen Zeitstempel-Abgleich gegen `last_successful_read`: Vorab gecachte Zustände und Nachbar-Raum-Polls löschen frische Schreib-Overlays vorzeitig

- **Schweregrad:** Hoch (Race Condition / Ghost-Reverts in der UI)
- **(a) Exakte Datei- und Zeilenreferenzen:**
  - [`overlay.py`](../../../custom_components/meltem_ventilation/overlay.py): `OptimisticOverlay.set` (Zeilen 37–50), `OptimisticOverlay.settle` (Zeilen 57–65), `OptimisticOverlay.get` (Zeilen 67–78)
  - [`coordinator.py`](../../../custom_components/meltem_ventilation/coordinator.py): `MeltemDataUpdateCoordinator.optimistic_preset_mode` (Zeilen 295–306), `MeltemDataUpdateCoordinator.optimistic_intensive` (Zeilen 308–317), `MeltemDataUpdateCoordinator._resolve_levels` (Zeilen 333–341), `MeltemDataUpdateCoordinator._confirm_pending_writes` (Zeilen 1110–1128)
  - [`levels.py`](../../../custom_components/meltem_ventilation/levels.py): `LEVEL_CONFIRM_TOLERANCE = 2` (Zeile 13), `levels_reached` (Zeilen 23–29)
- **(b) Auslösende Aufruf- und Ereignissequenz:**
  Während `_checked_confirmation` in [`write_confirmation.py`](../../../custom_components/meltem_ventilation/write_confirmation.py) (Zeile 194) explizit prüft, ob der Read nach dem Schreibbefehl stattfand (`read_health.last_attempt < confirmation.started_at`), speichert `OptimisticOverlay` keinen `started_at`-Zeitstempel und vergleicht den ausstehenden Schreibwert blind gegen den aktuell in `self.safe_data[room_key]` liegenden (vor dem Write gelesenen!) Zustand. Das führt zu zwei konkreten Fehlern:
  1. **Sofortiger Verlust von ±1 % / ±2 %-Schreibbefehlen:**
     - Zum Zeitpunkt `t = 0 s` wurde `unit_1` mit `operation_mode="manual", target_level=60` gelesen (`flow_control` ist für 180 s „fresh").
     - Bei `t = 1 s` stellt der Nutzer den Lüfter in Home Assistant von `60 %` auf `62 %` (oder `61 %`, `59 %`, `58 %`).
     - `MeltemDataUpdateCoordinator.async_set_direction_level` schreibt `(62, 60)` ans Gateway (`refresh_attempts=0`, also kein sofortiger Readback) und setzt `self._optimistic_levels.set("unit_1", (62, 60))`.
     - Beim sofortigen `self.async_update_listeners()` ruft die Fan-Entität `MeltemDataUpdateCoordinator._resolve_levels` auf. Da `self.safe_data["unit_1"]` noch den alten Wert `(60, 60)` von `t = 0 s` enthält und `levels_reached` wegen `abs(60 - 62) <= LEVEL_CONFIRM_TOLERANCE (2)` `True` liefert, gibt `OptimisticOverlay.get` sofort `None` zurück! Die UI springt noch im selben Event-Loop-Tick auf `(60, 60)` und `level_source: "target"` zurück.
  2. **Schnelle A→B→A-Korrektur + Poll eines anderen Raums (Ghost-Update):**
     - `t = 0 s`: `unit_1` ist auf `60 %` (`RoomState` von `t = 0 s` hat `target_level=60`).
     - `t = 1 s`: Nutzer stellt `unit_1` auf `40 %` (`refresh_attempts=0`, `self.safe_data["unit_1"]` bleibt auf `target_level=60` von `t = 0 s`).
     - `t = 2 s`: Nutzer korrigiert `unit_1` zurück auf `60 %`.
     - `t = 2,25 s`: Ein regulärer Poll-Job für **`unit_2`** (oder ein `temperature`-Job für `unit_1`) läuft in `MeltemDataUpdateCoordinator._async_update_data` und ruft `self._confirm_pending_writes(updated_data)` für **alle** Räume auf (Zeile 1116).
     - `self._optimistic_levels.settle("unit_1", (60, 60))` sieht den Vor-Schreib-Cache `(60, 60)` von `t = 0 s` und **löscht das Overlay von `unit_1`**!
     - `t = 3 s`: Der erste echte `flow`-Poll für `unit_1` trifft ein, während das Gerät über RF erst den ersten Befehl (`40 %`) übernommen hat. Weil das Overlay bereits bei `t = 2,25 s` gelöscht wurde, springt die UI von `60 %` auf `40 %` zurück! Dasselbe passiert analog bei `_optimistic_presets` und `_optimistic_intensive`, weil `read_health.group_fresh()` nur prüft, ob der letzte Read jünger als 180 s ist, nicht ob er **nach** `overlay.set()` erfolgte.
- **(c) Reproduzierender `pytest`-Beweis:**

```python
async def test_small_step_and_aba_write_overlay_prematurely_settled_by_pre_write_cache(
    hass: HomeAssistant,
) -> None:
    coordinator, _ = _build_coordinator(hass, [_UNIT_1])
    coordinator.data = _fresh(RoomState(operation_mode="manual", target_level=60))

    # Fall 1: Änderung um 2 m³/h (60 -> 62) wird sofort vom alten Cache (60) geschluckt
    await coordinator.async_set_direction_level("unit_1", DIRECTION_SUPPLY, 62)
    assert coordinator.effective_levels("unit_1") == (60, 60)  # BUG: Sollte (62, 60) sein!
    assert coordinator.level_source("unit_1") == "target"      # BUG: Sollte "pending" sein!

    # Fall 2: A (60) -> B (40) -> A (60), gefolgt von Poll eines anderen Jobs
    await coordinator.async_set_level("unit_1", 40)
    await coordinator.async_set_level("unit_1", 60)
    coordinator._confirm_pending_writes(coordinator.safe_data)  # alter State von t=0!
    assert "unit_1" not in coordinator._optimistic_levels._pending  # BUG: Overlay gelöscht!
```

---

### [P1-3] `state.balanced_target_readback` (`41121`) wird von `levels.py` komplett ignoriert und beim nächsten Poll gelöscht – Verlust aller nicht durch 10 teilbaren Lüftungsstufen nach 15 Sekunden

- **Schweregrad:** Hoch (Falsche Zustandsanzeige & schleichende Sollwert-Verfälschung bei Folge-Writes)
- **(a) Exakte Datei- und Zeilenreferenzen:**
  - [`coordinator.py`](../../../custom_components/meltem_ventilation/coordinator.py): `MeltemDataUpdateCoordinator._read_balanced_target_if_pending` (Zeilen 999–1041)
  - [`modbus_client.py`](../../../custom_components/meltem_ventilation/modbus_client.py): `MeltemModbusClient._read_mode_group` (Zeilen 543–603, insb. Zeilen 579–590)
  - [`levels.py`](../../../custom_components/meltem_ventilation/levels.py): `target_levels` (Zeilen 44–78, insb. Zeilen 71–78)
  - [`write_confirmation.py`](../../../custom_components/meltem_ventilation/write_confirmation.py): `_readback_value` (Zeilen 220–240, insb. Zeilen 221–225)
- **(b) Auslösende Aufruf- und Ereignissequenz & Widerspruch zu den Hardware-Messungen:**
  1. Laut Live-Messung W-1 ([`MELTEM.md`](../MELTEM.md) Zeilen 273–276, [`HARDWARE_BACKLOG.md`](../HARDWARE_BACKLOG.md) Zeilen 214–239) meldet das Statusregister `41101` im balancierten Betrieb die App-Kodierung `200 + airflow / 10` (10er-Raster: `204` = 40 m³/h, `206` = 60 m³/h), während das Set-Register `41121` den exakten Rohwert (`0..200`, 1-m³/h-Auflösung) hält.
  2. Setzt der Nutzer beide Lüfter auf `65 %` (`65 m³/h`, `41121 = 130`), liefert der nächste `flow`-Poll auf `41100..41102` `[3, 206, 0]`. `MeltemModbusClient._read_mode_group` dekodiert `41101 = 206` über `_decode_balanced_target_readback` zu **`state.target_level = 60`** (und fällt wegen `target is not None` auch **nicht** auf `derive_balanced_airflow` zurück, selbst wenn `41020/41021` bereits `[65, 65]` melden!).
  3. `MeltemDataUpdateCoordinator._read_balanced_target_if_pending` liest nun `41121 = 130` und speichert `65` in `state.balanced_target_readback = 65`.
  4. `_readback_value` bestätigt damit den Write (`status = "confirmed"`).
  5. **Aber:** `target_levels` in [`levels.py`](../../../custom_components/meltem_ventilation/levels.py) ignoriert `state.balanced_target_readback` komplett und liest in Zeile 72 nur `common = state.target_level` (`60`). Zudem wird `balanced_target_readback` beim nächsten Poll in `MeltemModbusClient._read_mode_group` (Zeile 589) wieder mit `None` überschrieben, weil `self._writes.needs_confirmation(room.key, "airflow_levels")` nach `"confirmed"` `False` ist.
  6. Sobald `TARGET_OPTIMISTIC_SECONDS` (`15 s`) abläuft, springen beide Fan-Entitäten in Home Assistant von `65 %` auf `60 %` zurück – und wenn der Nutzer danach nur die Abluft auf `40 %` ändert, liest `MeltemDataUpdateCoordinator.async_set_direction_level` als Gegenrichtung `supply = 60` statt `65` und überschreibt die Zuluft ungewollt mit `60 m³/h`!
- **(c) Reproduzierender `pytest`-Beweis:**

```python
async def test_balanced_non_multiple_of_10_reverts_to_coarse_status_target(
    hass: HomeAssistant,
) -> None:
    link = MockModbusConnection()
    unit = link.for_unit(2)
    # Gerät läuft auf 65 m³/h (41121=130, 41020..41021=[65, 65]), Status 41101 meldet 206 (60 m³/h)
    unit.holding.update({41020: [65, 65], 41100: [3, 206, 0], 41121: [130]})
    client = MeltemModbusClient(link.for_unit, port="/dev/ttyACM0")
    coordinator = MeltemDataUpdateCoordinator(
        hass, config_entry=_mock_entry(hass), client=client, rooms=[_UNIT_1], max_requests_per_second=10.0
    )
    coordinator._writes.record_pending("unit_1", "airflow_levels", (65, 65))

    await coordinator.async_refresh()

    # WriteConfirmation ist "confirmed" (65, 65), aber effective_levels liefert (60, 60)!
    assert coordinator._writes.by_room["unit_1"]["airflow_levels"].status == "confirmed"
    assert coordinator.effective_levels("unit_1") == (60, 60)  # BUG: Sollte (65, 65) sein!
```

---

### [P1-4] Widerspruch zwischen `_decode_balanced_target_readback` und `levels.target_levels`: Falsche `level_source="target"` im Schnellmodus und Kopieren von `supply_air_flow` auf `extract_air_flow` bei Frostschutz / Asymmetrie

- **Schweregrad:** Hoch (Datenkorruption der angezeigten Lüfterwerte und `level_source`-Attribute)
- **(a) Exakte Datei- und Zeilenreferenzen:**
  - [`modbus_client.py`](../../../custom_components/meltem_ventilation/modbus_client.py): `_decode_balanced_target_readback` (Zeilen 750–764)
  - [`levels.py`](../../../custom_components/meltem_ventilation/levels.py): `target_levels` (Zeilen 44–78, insb. Zeilen 71–78), `reported_levels` (Zeilen 81–97, insb. Zeilen 91–95)
- **(b) Auslösende Aufruf- und Ereignissequenz:**
  1. **Verfälschung von `level_source`:** Wenn ein Gerät im Schnellmodus läuft (z. B. `[3, 229, 0]` = `medium`, wie auf Slaves 2, 3, 7 in [`readable-register-scan-R-2.txt`](../measurements/2026-10-02/readable-register-scan-R-2.txt)), ist der Zielwert über Modbus nicht lesbar (`229` ist der Preset-Code). `_decode_balanced_target_readback` fällt daher in Zeile 764 auf `derive_balanced_airflow(extract_air_flow, supply_air_flow)` zurück und speichert diesen **Messwert** in `state.target_level`!
     In `reported_levels` (Zeilen 92–94) wird `targets = target_levels(state, airflow_is_fresh=False)` aufgerufen, um zu prüfen, ob die Werte aus einem Zielregister (`"target"`) oder aus der Messung (`"measured"`) stammen. Weil `state.target_level` bereits mit dem Messwert überschrieben wurde, ist `levels == targets` immer `True`, und `reported_levels` meldet fälschlich `level_source = "target"` statt `"measured"`. Zudem bleibt dieser abgeleitete Wert selbst dann aktiv, wenn die `flow`-Gruppe veraltet (`airflow_is_fresh == False`) ist!
  2. **Überschreiben der Abluft mit der Zuluft bei `abs(extract - supply) > 1` (z. B. Frostschutz oder Rampenlauf):**
     Wenn im Modus `manual` (`41100 = 3`, z. B. Preset `229` oder ausgefallener Status-Read) `extract_air_flow = 40` und `supply_air_flow = 0` ist (wie im Frostschutzbetrieb laut [`functions.md`](../reference/functions.md) Zeile 181 dokumentiert!), gibt `derive_balanced_airflow` bewusst `None` zurück (`state.target_level = None`).
     Anschließend führt `target_levels` in Zeilen 72–77 jedoch folgenden Code aus:

     ```python
     common = state.target_level
     if common is None and airflow_is_fresh:
         common = state.supply_air_flow
     if common is None and airflow_is_fresh:
         common = state.extract_air_flow
     return common, common
     ```

     Da `common` auf `state.supply_air_flow` (`0`) gesetzt wird, wird `state.extract_air_flow` (`40`) komplett verworfen und `(0, 0)` für **beide** Lüfter zurückgegeben! Der Abluft-Lüfter wird in Home Assistant als `off` (`0 %`) angezeigt, obwohl er real mit `40 m³/h` läuft.
- **(c) Reproduzierender `pytest`-Beweis:**

```python
def test_target_levels_overwrites_extract_with_supply_when_diverging() -> None:
    now = dt_util.utcnow()
    health = ReadHealth(last_attempt=now, last_successful_read=now)
    # Im Modus "manual" (Preset 229 -> target_level=None) bei Frostschutz: Zuluft 0, Abluft 40
    state = RoomState(
        operation_mode="manual",
        preset_mode="medium",
        target_level=None,
        supply_air_flow=0,
        extract_air_flow=40,
        group_read_health=(("flow", health), ("flow_control", health)),
    )
    # BUG: gibt ((0, 0), "measured") statt ((0, 40), "measured") zurück!
    assert reported_levels(state) == ((0, 0), "measured")
```

---

### [P2-1] Ungefilterte Hardware-Sentinel-Werte (`239 %` rF, `32767 ppm` CO2/VOC) im Laufzeit-Pfad `_fresh_values`

- **Schweregrad:** Hoch (Protokoll-Abweichung / Korruption der Sensor-Zeitreihen und Langzeitstatistiken)
- **(a) Exakte Datei- und Zeilenreferenzen:**
  - [`modbus_helpers.py`](../../../custom_components/meltem_ventilation/modbus_helpers.py): `_PLAUSIBLE_RANGES` (Zeilen 55–60), `_is_plausible` (Zeilen 317–319)
  - [`modbus_client.py`](../../../custom_components/meltem_ventilation/modbus_client.py): `_fresh_values` (Zeilen 686–700, insb. Zeilen 695–698)
  - Messdaten: [`readable-register-scan-R-2.txt`](../measurements/2026-10-02/readable-register-scan-R-2.txt) (Zeilen 71–78, 167–174), [`pe-compare-slave4.txt`](../measurements/2026-10-09/pe-compare-slave4.txt) (Zeilen 13–14)
- **(b) Auslösende Eingabedaten & Widerspruch zu den Messdaten:**
  1. Die Hardware-Scans in [`readable-register-scan-R-2.txt`](../measurements/2026-10-02/readable-register-scan-R-2.txt) belegen für die UINT16-Sensorregister `41006` (`humidity_extract_air`), `41011` (`humidity_supply_air`), `41007` (`co2_extract_air`) und `41013` (`voc_supply_air`), dass das Gerät bei fehlendem oder nicht verfügbarem Sensor **keine** Modbus-Exception wirft, sondern Sentinel-Werte liefert:
     - `41006 = [239]`, `41011 = [239]` (`0x00EF`, außerhalb `0..100 %`)
     - `41007 = [32767]`, `41013 = [32767]` (`0x7FFF`, außerhalb `250..10000 ppm` bzw. `0..10000 ppm`)
  2. Während die Setup-Probe in `detect_slave_details` diese Sentinel-Werte über `_is_plausible` (`_PLAUSIBLE_RANGES`) korrekt verwirft, prüft `_fresh_values` (Zeile 695) im Laufzeitbetrieb ausschließlich:

     ```python
     if value is None or (isinstance(value, float) and not math.isfinite(value)):
     ```

  3. Da `239` und `32767` vom Typ `int` sind, passieren sie diese Prüfung ungehindert! Fällt ein externer Funksensor (`M-WRG-II FSF`/`FSC`, siehe [`models.md`](../reference/models.md) Zeile 83) aus, liefert ein Sensor kurzzeitig `0x7FFF`/`0xFFFF`/`239` oder wählt ein Nutzer im Profil-Dialog ein `-F`/`-FC`/`O/VOC-AUL`-Profil, werden `239 %` Luftfeuchtigkeit und `32767 ppm` CO2/VOC als gültige Messwerte in Home Assistant (inkl. Langzeit-Statistik `SensorStateClass.MEASUREMENT`) geschrieben.
- **(c) Reproduzierender `pytest`-Beweis anhand der realen Messdaten aus [`readable-register-scan-R-2.txt`](../measurements/2026-10-02/readable-register-scan-R-2.txt):**

```python
async def test_runtime_poll_accepts_hardware_sentinel_values_from_r2_scan(
    client: MeltemModbusClient, unit: MockModbusUnit
) -> None:
    # Exakte Registerwerte aus readable-register-scan-R-2.txt (Zeilen 69..78)
    unit.holding.update({
        41000: [0, 32704, 0, 32704, 2622, 16785],
        41006: [239, 32767],
        41009: [0, 32704],
        41011: [239, 32767, 32767],
    })
    state = await client.read_room_state(_FC_VOC, RoomState(), _TEMPERATURES)

    # BUG: 239% Feuchte und 32767 ppm CO2/VOC werden ungefiltert übernommen!
    assert state.humidity_extract_air == 239
    assert state.humidity_supply_air == 239
    assert state.co2_extract_air == 32767
    assert state.voc_supply_air == 32767
```

---

### [P2-2] `MeltemRoomDevice` beschränkt `ExtractAirQuality` (`41006..41007`) und `SupplyAirQuality` (`41011..41013`) nicht auf profil-unterstützte Felder und cached Devices nur nach `room.slave`

- **Schweregrad:** Hoch (Block-Read-Ausfall der gesamten `temperature`-Gruppe bei nicht unterstützten Nachbarregistern `41007`/`41012`/`41013`)
- **(a) Exakte Datei- und Zeilenreferenzen:**
  - [`device/device.py`](../../../custom_components/meltem_ventilation/device/device.py): `MeltemRoomDevice.__init__` (Zeilen 45–68)
  - [`device/components.py`](../../../custom_components/meltem_ventilation/device/components.py): `ExtractAirQuality` (Zeilen 127–133, `41006..41007`), `SupplyAirQuality` (Zeilen 136–142, `41011..41013`), `ControlSettings` (Zeilen 153–163, `42000..42005`)
  - [`modbus_client.py`](../../../custom_components/meltem_ventilation/modbus_client.py): `MeltemModbusClient._room_device` (Zeilen 217–224), `_due_fields` (Zeilen 611–672)
- **(b) Auslösende Aufruf- und Ereignissequenz:**
  1. In `MeltemRoomDevice.__init__` wird `_only(self.temperatures, "exhaust_temperature")` zwar für `PLAIN_PROFILES` aufgerufen, aber weder `ExtractAirQuality` noch `SupplyAirQuality` noch `ControlSettings` werden per `_only(...)` auf die Fähigkeiten des Profils eingeschränkt.
  2. Dadurch liest ein `-F`-Gerät (`ii_f`, `s_f`) bei jedem Temperatur-/Umwelt-Poll `41006..41007` (inkl. CO2 `41007`), und **jedes** `-F`- und `-FC`-Gerät (`ii_f`, `s_f`, `ii_fc`, `s_fc`) liest bei jedem Poll den 3er-Block `41011..41013` (inkl. des undokumentierten Registers `41012` und des unbestätigten VOC-Registers `41013`).
  3. Wie in [`MELTEM.md`](../MELTEM.md) Zeile 111 dokumentiert (`"a block read fails as a whole as soon as one register in it is unreadable"`) und in den eigenen Fixtures von [`test_config_flow.py`](../../../tests/components/meltem_ventilation/test_config_flow.py) (Zeile 211) sowie [`test_tools.py`](../../../tests/components/meltem_ventilation/test_tools.py) (Zeilen 138–139: `humidity_unit.fail_read(41007, IllegalDataAddressError()); humidity_unit.fail_read(41013, IllegalDataAddressError())`) modelliert: Sobald ein `-F`- oder `-FC`-Gerät Register `41007`, `41012` oder `41013` mit `IllegalDataAddressError` ablehnt, schlägt der gesamte Block-Read für `extract_air_quality` bzw. `supply_air_quality` fehl!
  4. Weil alle 8 Temperatur- und Luftgütesensoren an derselben Read-Health-Gruppe `"temperature"` hängen ([`const.py`](../../../custom_components/meltem_ventilation/const.py) Zeilen 242–253), führt der Fehler an `41013` dazu, dass `job.record_error(("temperature",), error)` gerufen wird und nach 3 Polls **sämtliche** Temperatur-, Feuchte- und CO2-Sensoren des Geräts `unavailable` werden!
  5. **Zusatzfehler im Device-Cache:** `MeltemModbusClient._room_device` cached `self._devices` nur unter `room.slave`. Wird derselbe Client erst mit einem Plain-Profil (`ii_plain`, `exhaust_temperature_only=True`) und danach für denselben Slave mit `ii_f` aufgerufen, bleibt `self.temperatures` dauerhaft auf `"exhaust_temperature"` beschnitten; `outdoor_air_temperature` und `extract_air_temperature` liefern `None`, was in `_fresh_values` (Zeile 696) als `"Invalid outdoor_air_temperature readback: None"` gewertet wird.
- **(c) Reproduzierender `pytest`-Beweis (anhand der `humidity_unit`-Konfiguration aus [`test_tools.py`](../../../tests/components/meltem_ventilation/test_tools.py) Zeilen 129–140):**

```python
async def test_ii_f_unit_rejecting_co2_and_voc_fails_entire_temperature_group(
    client: MeltemModbusClient, unit: MockModbusUnit
) -> None:
    # Exakt wie humidity_unit in test_tools.py:129-140 und co2_unit in test_config_flow.py:206-211
    unit.holding.update({
        41000: _float_words(21.0) + _float_words(10.0) + _float_words(18.0),
        41006: [45],
        41009: _float_words(19.0),
        41011: [50],
    })
    unit.fail_read(41007, IllegalDataAddressError())
    unit.fail_read(41013, IllegalDataAddressError())

    state = await client.read_room_state(_F, RoomState(), _TEMPERATURES)

    # BUG: Obwohl 41006 und 41011 gültige Werte (45%, 50%) haben, bleiben beide None
    # und die gesamte "temperature"-Gruppe wird als fehlerhaft markiert!
    assert state.humidity_extract_air is None
    assert state.humidity_supply_air is None
    assert state.read_health_for("temperature").consecutive_failures == 1
```

---

### [P2-3] Protokoll-Inkonsistenz bei unbalancierten Status-Codes (`[4, 0, 228]` auf Slave 4) und `M-WRG-S`-Skalierung

- **Schweregrad:** Mittel–Hoch (Protokoll-Abweichung gegenüber [`pe-compare-slave4.txt`](../measurements/2026-10-09/pe-compare-slave4.txt) und [`modbus.md`](../reference/modbus.md))
- **(a) Exakte Datei- und Zeilenreferenzen:**
  - [`modbus_client.py`](../../../custom_components/meltem_ventilation/modbus_client.py): `MeltemModbusClient._read_mode_group` (Zeilen 543–603, insb. Zeilen 564–575), `_scale_airflow_to_raw` (Zeilen 741–743), `_decode_app_encoded_airflow` (Zeilen 777–783), `_decode_preset_mode` (Zeilen 806–822, insb. Zeilen 813–816)
  - Messdaten: [`pe-compare-slave4.txt`](../measurements/2026-10-09/pe-compare-slave4.txt) (Zeile 19: `41100 x3 [4, 0, 228]`), [`modbus.md`](../reference/modbus.md) (Zeilen 122–131)
- **(b) Auslösende Eingabedaten & Widerspruch zu den Messdaten:**
  1. In [`pe-compare-slave4.txt`](../measurements/2026-10-09/pe-compare-slave4.txt) (Zeile 19) meldet Slave 4 real `41100..41102 = [4, 0, 228]`.
     - In `_decode_preset_mode` (Zeile 814) prüft der Code lediglich `raw_extract_target > APP_UNBALANCED_PRESET_BASE` (`228 > 200` ohne Obergrenze!) und dekodiert `preset_mode = "extract_only"`.
     - Gleichzeitig prüft `_decode_app_encoded_airflow` (Zeile 783) jedoch `airflow <= profile_max_airflow(room.profile)` (`(228 - 200) * 10 = 280 <= 100` → `False`) und gibt `None` zurück.
     - In `MeltemModbusClient._read_mode_group` (Zeilen 569–574) überschreibt `extract_target_level` dadurch einen zuvor bekannten `state.extract_target_level` mit `None`, weil `raw_extract_target is not None` (`228`) wahr ist, dessen Dekodierung aber `None` ergibt!
  2. Auf `M-WRG-S`-Profilen (`profile_max_airflow = 97`) lehnt `_decode_app_encoded_airflow` (Zeile 783) den App-Code `210` (`(210 - 200) * 10 = 100 > 97`) komplett als `None` ab, obwohl die App und Funkfernbedienung laut [`device-parameters.md`](../reference/device-parameters.md) (Zeilen 33–34, 52) auch auf der `M-WRG`-Serie Stufe 10 = `100` (bzw. `210`) verwenden.
- **(c) Beleg anhand [`pe-compare-slave4.txt`](../measurements/2026-10-09/pe-compare-slave4.txt) (Zeile 19):**
  - Eingabe `41100..41102 = [4, 0, 228]` bei `previous_state = RoomState(operation_mode="unbalanced", extract_target_level=50)`:
  - Ergebnis aus `MeltemModbusClient.read_room_state`: `state.preset_mode == "extract_only"`, `state.target_level == 0`, aber `state.extract_target_level` wird von `50` auf `None` gelöscht.

---

### [P3-1] `diagnostics.py` führt vor dem Auslesen von `transport_diagnostics()` einen Live-Discovery-Read aus und überschreibt damit genau die Diagnose-Zähler, die er exportieren soll

- **Schweregrad:** Mittel–Hoch (Verfälschung von Diagnosedaten & unbeabsichtigter Link-Recycle beim Diagnose-Download)
- **(a) Exakte Datei- und Zeilenreferenzen:**
  - [`diagnostics.py`](../../../custom_components/meltem_ventilation/diagnostics.py): `async_get_config_entry_diagnostics` (Zeilen 59–106, insb. Zeilen 68–84)
  - [`device/transport.py`](../../../custom_components/meltem_ventilation/device/transport.py): `TransportPolicy.diagnostics` (Zeilen 88–99), `TransportPolicy._link_answered` (Zeilen 149–151), `TransportPolicy._count_timeout` (Zeilen 153–171)
- **(b) Auslösende Aufruf- und Ereignissequenz:**
  1. In `async_get_config_entry_diagnostics` wird in Zeile 68 zuerst `gateway_units = await coordinator.async_discover_gateway_units()` aufgerufen und **erst danach** in Zeile 84 `"transport": coordinator.client.transport_diagnostics()`.
  2. `async_discover_gateway_units()` sendet zwei echte Modbus-Requests an Slave 1 (`43901` und `43902..`) über dieselbe `TransportPolicy`.
  3. Antwortet das Gateway (selbst wenn zuvor Raum-Units Timeouts hatten), ruft `TransportPolicy.run` `self._link_answered()` auf und setzt `self._consecutive_timeouts = 0` sowie `self._last_answer_at = time.monotonic()`. Dadurch zeigt der Diagnose-Export für `consecutive_timeouts` immer `0` und für `seconds_since_any_answer` immer `0.0`! (Genau dieses Artefakt trat im Live-Test am 2026-10-15 auf, siehe [`HARDWARE_BACKLOG.md`](../HARDWARE_BACKLOG.md) Zeilen 646–649: `seconds_since_any_answer=0` wegen des Diagnose-Abrufs!).
  4. Antwortet das Gateway hingegen nicht, blockiert der Diagnose-Download erst hinter `_gateway_lock`, erzeugt bis zu 2 Timeouts à `0,8 s` plus Rate-Slot-Wait und kann über `TransportPolicy._count_timeout` sogar `await unit.disconnect()` (einen Verbindungsabbruch des geteilten Modbus-Links!) als Seiteneffekt des Diagnose-Downloads auslösen.
- **(c) Reproduzierender `pytest`-Beweis:**

```python
async def test_diagnostics_download_wipes_transport_timeout_counters(
    hass: HomeAssistant,
) -> None:
    link = MockModbusConnection()
    link.for_unit(1).holding.update({43901: 1, 43902: [2]})
    client = MeltemModbusClient(link.for_unit, port="/dev/ttyACM0")
    client._policy._consecutive_timeouts = 2
    client._policy._last_answer_at = time.monotonic() - 42.0

    entry = _entry(hass, with_runtime=False)
    coordinator = MeltemDataUpdateCoordinator(
        hass, config_entry=entry, client=client, rooms=[_ROOM], max_requests_per_second=10.0
    )
    entry.runtime_data = MeltemRuntimeData(coordinator=coordinator)

    diag = await async_get_config_entry_diagnostics(hass, entry)
    # BUG: Der Diagnose-Abruf hat consecutive_timeouts=2 und seconds_since_any_answer=42.0 überschrieben!
    assert diag["coordinator"]["transport"]["consecutive_timeouts"] == 0
    assert diag["coordinator"]["transport"]["seconds_since_any_answer"] == 0.0
```

---

### [P3-2] Unnötige Haltezeit von `_gateway_lock` in `_async_refresh_room_after_write` und toter 3. Retry-Zweig (`POST_WRITE_REFRESH_RETRIES`)

- **Schweregrad:** Mittel–Hoch (Blockade aller anderen Räume für 2,5+ s nach bereits bestätigtem Write + toter Code)
- **(a) Exakte Datei- und Zeilenreferenzen:**
  - [`coordinator.py`](../../../custom_components/meltem_ventilation/coordinator.py): `MeltemDataUpdateCoordinator.async_set_preset_mode` (Zeile 736), `MeltemDataUpdateCoordinator.async_activate_intensive` (Zeile 769), `MeltemDataUpdateCoordinator.async_deactivate_intensive` (Zeile 782), `MeltemDataUpdateCoordinator._async_refresh_room_after_write` (Zeilen 952–997)
  - [`const.py`](../../../custom_components/meltem_ventilation/const.py): `POST_WRITE_REFRESH_RETRIES = 2`, `POST_WRITE_REFRESH_INTERVAL_SECONDS = 2.5` (Zeilen 63–64)
- **(b) Auslösende Aufruf- und Ereignissequenz:**
  1. Bei `async_set_preset_mode`, `async_activate_intensive` und `async_deactivate_intensive` wird `refresh_attempts=2` als `min_refresh_attempts=2` an `MeltemDataUpdateCoordinator._async_refresh_room_after_write` übergeben, während `self._gateway_lock` gehalten wird.
  2. Selbst wenn der **erste** Readback (`attempt == 0`, nach `1,5 s` `WRITE_SETTLE_SECONDS`) den neuen Zustand bereits erfolgreich liest und `self._confirm_pending_writes(updated_states)` den Write auf `"confirmed"` setzt, bricht die Schleife in Zeile 993 (`if failed or attempt + 1 >= max(1, min_refresh_attempts): return`) **nicht** ab, sondern schläft bei gehaltenem `_gateway_lock` weitere `2,5 s` (`POST_WRITE_REFRESH_INTERVAL_SECONDS`) und pollt das Gateway ein zweites Mal!
  3. Umgekehrt bricht die Schleife wegen `attempt + 1 >= max(1, min_refresh_attempts)` bei `min_refresh_attempts=1` immer nach 1 Versuch (`attempt=0`) und bei `min_refresh_attempts=2` immer nach 2 Versuchen (`attempt=1`) ab – selbst wenn der Write noch `"pending"` ist. Der 3. Schleifendurchlauf (`attempt=2` aus `range(POST_WRITE_REFRESH_RETRIES + 1)`) ist im gesamten Code unerreichbar.
- **(c) Beleg durch Branch-Coverage-Output (`pytest --cov=custom_components.meltem_ventilation --cov-branch`):**

  ```text
  Name                                                   Stmts   Miss Branch BrPart  Cover   Missing
  custom_components\meltem_ventilation\coordinator.py      491      8    154      8    98%   ..., 961->exit, 989->991, 996->961, ...
  ```

  Die Branches `961->exit` (reguläres Schleifenende nach `POST_WRITE_REFRESH_RETRIES + 1`) und `996->961` sind unerreicht, weil kein Aufrufer jemals `min_refresh_attempts > 2` übergibt.

---

### [P3-3] Options-Flow `async_step_edit_profiles` führt trotz gegenteiliger Dokumentation bis zu 75 serielle Modbus-Reads beim bloßen Öffnen des Formulars aus

- **Schweregrad:** Mittel–Hoch (UI-Blockade bis >60 s bei niedriger Request-Rate / Widerspruch zu Code-Docstring & [`README.md`](../../../README.md))
- **(a) Exakte Datei- und Zeilenreferenzen:**
  - [`config_flow.py`](../../../custom_components/meltem_ventilation/config_flow.py): `MeltemVentilationOptionsFlow.async_step_edit_profiles` (Zeilen 631–666, insb. Zeilen 651–659), `_async_probe_units` (Zeilen 295–314)
  - [`modbus_helpers.py`](../../../custom_components/meltem_ventilation/modbus_helpers.py): `_PROBE_COMPONENTS` (Zeilen 46–52), `detect_slave_details` (Zeilen 260–294)
  - [`README.md`](../../../README.md): Zeilen 190–191 (`"Change profiles for existing units — reassign profiles without rescanning the gateway"`)
- **(b) Auslösende Aufruf- und Ereignissequenz:**
  1. Öffnet der Nutzer im Options-Menü den Punkt `"Change profiles for existing units"` (`MeltemVentilationOptionsFlow.async_step_edit_profiles`, dessen Docstring lautet: `"""Edit the profiles for already known units without rescanning."""`), ruft Zeile 652 dennoch `probed, _ = await _async_probe_units(slaves, coordinator.async_probe_slave_details)` auf.
  2. Das erkannte Profil (`_`) wird weggeworfen; der Aufruf dient nur dazu, `preview` neu vom Bus zu lesen, obwohl `existing_rooms[slave].get("preview")` bereits in `ConfigEntry.data` gespeichert ist!
  3. Pro Gerät sendet `detect_slave_details` 5 Einzel-Reads (`40002`, `41006`, `41011`, `41007`, `41013`), die alle über die `TransportPolicy` des laufenden Coordinators laufen und somit dem Rate-Limit `max_requests_per_second` unterliegen.
  4. Bei 6 Geräten (30 Reads) und `max_requests_per_second = 0.5` (`2,0 s` pro Read) dauert allein das Öffnen des Formulars **`60 Sekunden`** (bei 15 Geräten **`150 Sekunden`**), bevor Home Assistant das Formular überhaupt anzeigt.

---

### [P3-4] Fehlerhaftes Lifecycle-Cleanup bei partiellem Setup-Fehler in `async_setup_entry` sowie fehlende Timer-Neuplanung in `update_request_rate`

- **Schweregrad:** Mittel (Hintergrund-Timer-Leak bei Setup-Abbruch / verzögerte Übernahme einer höheren Request-Rate)
- **(a) Exakte Datei- und Zeilenreferenzen:**
  - [`__init__.py`](../../../custom_components/meltem_ventilation/__init__.py): `async_setup_entry` (Zeilen 185–234, insb. Zeilen 223–230)
  - [`coordinator.py`](../../../custom_components/meltem_ventilation/coordinator.py): `MeltemDataUpdateCoordinator.update_request_rate` (Zeilen 926–932), `MeltemDataUpdateCoordinator._schedule_next_tick` (Zeilen 465–484)
- **(b) Auslösende Aufruf- und Ereignissequenz:**
  1. **Timer-Leak bei Setup-Fehler:** In `async_setup_entry` (Zeilen 223–230) wird `await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)` aufgerufen. Sobald die erste Plattform (`sensor`) ihre Entitäten hinzufügt, registrieren diese Listener am Coordinator, wodurch `DataUpdateCoordinator.async_add_listener` sofort den internen Refresh-Timer (`coordinator._unsub_refresh`) auf dem Event-Loop startet. Wirft eine nachfolgende Plattform in `async_forward_entry_setups` eine Exception, ruft der `except Exception:`-Block zwar `client.shutdown()` und `del entry.runtime_data` auf, **nicht aber `await coordinator.async_shutdown()`**! Der Refresh-Timer des verwaisten Coordinators bleibt aktiv.
  2. **Keine Timer-Neuplanung bei `update_request_rate`:** `MeltemDataUpdateCoordinator.update_request_rate` ruft `self._schedule_next_tick()` auf, welches lediglich `self.update_interval = timedelta(...)` setzt. Der Property-Setter `DataUpdateCoordinator.update_interval` in Home Assistant ändert jedoch nur `self._update_interval_seconds`, ohne `self._schedule_refresh()` aufzurufen; ein bereits laufender längerer Timer-Sleep wird nicht verkürzt.
- **(c) Reproduzierender `pytest`-Beweis (für den Setup-Abbruch-Leak):**

```python
async def test_partial_platform_setup_failure_leaks_coordinator_timer(
    hass: HomeAssistant,
) -> None:
    entry = _entry(hass)
    orig_forward = hass.config_entries.async_forward_entry_setups

    async def _fail_after_first_platform(entry, platforms):
        await orig_forward(entry, [Platform.SENSOR])
        raise RuntimeError("second platform failed")

    with (
        patch(f"{_INIT}.resolve_preferred_port_path", side_effect=lambda p: p),
        patch(f"{_INIT}.MeltemModbusClient.async_validate_gateway", new=AsyncMock()),
        patch.object(hass.config_entries, "async_forward_entry_setups", side_effect=_fail_after_first_platform),
        pytest.raises(RuntimeError, match="second platform failed"),
    ):
        await async_setup_entry(hass, entry)
    # Coordinator-Refresh-Timer wurde durch Platform.SENSOR gestartet, aber im except-Block nie per async_shutdown() gestoppt!
```

---

### [P3-5] Fehlende Zurücksetzung von `_level_fallbacks` bei nachfolgenden regulären Writes sowie redundante Device-Registry-Updates (`25×` pro Raum)

- **Schweregrad:** Niedrig–Mittel (Veraltete `last_write_fallback`-Attribute & unnötige Event-Loop-Arbeit)
- **(a) Exakte Datei- und Zeilenreferenzen:**
  - [`coordinator.py`](../../../custom_components/meltem_ventilation/coordinator.py): `MeltemDataUpdateCoordinator.level_write_fallback` (Zeilen 343–353), `MeltemDataUpdateCoordinator._record_level_fallback` (Zeilen 355–357), `MeltemDataUpdateCoordinator.async_set_direction_level` (Zeilen 592–668)
  - [`entity.py`](../../../custom_components/meltem_ventilation/entity.py): `MeltemEntity._async_update_device_registry_versions` (Zeilen 117–131)
- **(b) Auslösende Aufruf- und Ereignissequenz:**
  1. **`_level_fallbacks` wird bei Folge-Writes ohne Fallback nicht gelöscht:** Setzt ein Nutzer bei unbekannter Gegenrichtung (`other is None`) die Zuluft auf `60`, speichert `MeltemDataUpdateCoordinator._record_level_fallback` `self._level_fallbacks[room_key] = ("both_directions_balanced_manual", now)` (und feuert dabei ein zweites `async_update_listeners()` unmittelbar nach dem Write). Setzt der Nutzer 1 Sekunde später (vor dem nächsten `flow_control`-Poll) die Abluft auf `40`, nutzt `MeltemDataUpdateCoordinator.async_set_direction_level` das ausstehende Overlay `(60, 60)` (sodass `other = 60` und `operation_mode = "manual"` bekannt sind – **kein** Fallback!). Dennoch bleibt `self._level_fallbacks[room_key]` gesetzt, sodass `last_write_fallback` auf beiden Fan-Entitäten weiterhin `"both_directions_balanced_manual"` meldet.
  2. **`25×` `device_registry.async_update_device` pro Raum:** In `MeltemEntity` hält **jede einzelne Entität** ihr eigenes `self._exported_versions = (None, None)`. Beim ersten Coordinator-Update (wenn `_hw_version` gesetzt ist) und erneut beim ersten `hours`-Poll (wenn `_sw_version` hinzukommt) ruft jede der bis zu 25 Entitäten eines Raums in `MeltemEntity._async_update_device_registry_versions` `dr.async_get(self.hass).async_update_device(device.id, ...)` für dieselbe `device.id` auf (bei 6 Räumen bis zu 150 identische Registry-Aufrufe).

---

### [P4] Test-Audit auf Scheinsicherheit (Mock-Tautologien & ungetestete Pfade)

Die Ausführung von `pytest --cov=custom_components.meltem_ventilation --cov-branch --cov-report=term-missing`, `ruff check` und `mypy` zeigt:

- **Gesamtergebnis:** `699 passed, 1 skipped` (`98 %` kombinierte Statement-/Branch-Coverage; `30` ungetestete Statements, `25` ungetestete Branch-Übergänge).
- **Wichtiger Befund zur Test-Qualität:** Die hohe nominelle Coverage von `98 %` verdeckt vier gravierende **Mock-Tautologien**, bei denen Tests lediglich das Verhalten ihrer eigenen Test-Doubles prüfen:

| # | Testdatei & Stelle | Mock-Tautologie | Verdeckter Fehler im echten Code |
| --- | --- | --- | --- |
| 1 | [`test_coordinator_and_config_helpers.py`](../../../tests/components/meltem_ventilation/test_coordinator_and_config_helpers.py) (`_FakeClient.read_room_state`, Zeile 85) & [`test_coordinator_integration.py`](../../../tests/components/meltem_ventilation/test_coordinator_integration.py) (Zeile 85) | `_FakeClient.read_room_state` wirft `MeltemModbusError("boom")` für `room.key == "broken"`. | Der echte `MeltemModbusClient.read_room_state` wirft bei `ModbusTimeoutError`, `0x05 AcknowledgeError` oder `GatewayTargetError` **niemals** `MeltemModbusError`. Dadurch sind `_room_failures`, `last_job_error` und `_on_transport_failure` im Echtbetrieb bei Gerätefehlern wirkungslos (**[P1-1]**). |
| 2 | [`test_select_platform.py`](../../../tests/components/meltem_ventilation/test_select_platform.py) (Zeilen 109, 130–134) | Mockt `coordinator.optimistic_preset_mode` mit Rückgabewert `PRESET_MODE_INACTIVE` (`"inactive"`). | Der echte `MeltemDataUpdateCoordinator.async_clear_preset_mode` ruft `_optimistic_presets.clear()` statt `.set(..., PRESET_MODE_INACTIVE)` auf und setzt niemals ein `"inactive"`-Overlay; zudem fehlt für `operation_mode` jegliches Overlay. |
| 3 | [`test_diagnostics_and_system_health.py`](../../../tests/components/meltem_ventilation/test_diagnostics_and_system_health.py) (`_coordinator()`, Zeile 42) | Verwendet einen freien `MagicMock()` mit statischem `coordinator.last_job_error = None` und entkoppeltem `transport_diagnostics`-Mock. | Die echte Property `MeltemDataUpdateCoordinator.last_job_error` (Zeile 233) hat **0 % Coverage**, und der Seiteneffekt, dass `async_discover_gateway_units()` in [`diagnostics.py`](../../../custom_components/meltem_ventilation/diagnostics.py) die echten `transport_diagnostics()`-Zähler vor dem Auslesen überschreibt (**[P3-1]**), bleibt unentdeckt. |
| 4 | [`test_config_flow.py`](../../../tests/components/meltem_ventilation/test_config_flow.py) (Zeile 648) & [`test_coordinator_and_config_helpers.py`](../../../tests/components/meltem_ventilation/test_coordinator_and_config_helpers.py) (Zeilen 322, 553) | Mockt `coordinator.update_request_rate` im Options-Flow als `MagicMock()`; setzt in `test_fallback_is_cleared_by_the_next_successful_readback` `coordinator.data` direkt statt über `_confirm_pending_writes`. | Zeile 932 (`self._schedule_next_tick()` in `MeltemDataUpdateCoordinator.update_request_rate`) und Zeilen 1125–1126 (`del self._level_fallbacks[room_key]` in `MeltemDataUpdateCoordinator._confirm_pending_writes`) wurden in keinem einzigen Test der gesamten Suite ausgeführt (`Miss` im Coverage-Report). |

---

### [P5] Home Assistant Quality Scale (IQS), Schema- und Dokumentations-Konsistenz

1. **Diskrepanz zwischen [`triggers.yaml`](../../../custom_components/meltem_ventilation/triggers.yaml) / [`conditions.yaml`](../../../custom_components/meltem_ventilation/conditions.yaml) und der Automations-Dokumentation ([`TRIGGER_FILTER_CHANGE_DUE.md`](../../TRIGGER_FILTER_CHANGE_DUE.md), [`CONDITION_FILTER_CHANGE_DUE.md`](../../CONDITION_FILTER_CHANGE_DUE.md)):**
   - **(a) Referenzen:** [`triggers.yaml`](../../../custom_components/meltem_ventilation/triggers.yaml) (Zeilen 1–5), [`conditions.yaml`](../../../custom_components/meltem_ventilation/conditions.yaml) (Zeilen 1–5), [`TRIGGER_FILTER_CHANGE_DUE.md`](../../TRIGGER_FILTER_CHANGE_DUE.md) (Zeilen 17, 37–40), [`CONDITION_FILTER_CHANGE_DUE.md`](../../CONDITION_FILTER_CHANGE_DUE.md) (Zeilen 18, 36–39).
   - **(b)/(c) Befund:** In [`triggers.yaml`](../../../custom_components/meltem_ventilation/triggers.yaml) und [`conditions.yaml`](../../../custom_components/meltem_ventilation/conditions.yaml) ist unter `target:` ausschließlich `device: - integration: meltem_ventilation` definiert; ein `entity:`-Block (`domain: binary_sensor`, `device_class: problem`, `integration: meltem_ventilation`) fehlt. In der Home-Assistant-UI erlaubt der Target-Selector dadurch nur die Auswahl eines *Geräts* (inklusive des Gateway-Geräts `Meltem Gateway M-WRG-GW`, das gar keine `filter_change_due`-Entität besitzt!), während [`TRIGGER_FILTER_CHANGE_DUE.md`](../../TRIGGER_FILTER_CHANGE_DUE.md) (Zeile 17) und [`CONDITION_FILTER_CHANGE_DUE.md`](../../CONDITION_FILTER_CHANGE_DUE.md) (Zeile 18) den Nutzer anweisen: *"4. Select a Meltem device or its **Filter change due** entity as the target"*.
2. **Veraltete Aussagen in [`INTEGRATION_QUALITY_SCALE.md`](../INTEGRATION_QUALITY_SCALE.md):**
   - **(a) Referenzen:** [`INTEGRATION_QUALITY_SCALE.md`](../INTEGRATION_QUALITY_SCALE.md) (Zeilen 65, 92–94, 129–134, 151–153) vs. [`test_platform_matrix.py`](../../../tests/components/meltem_ventilation/test_platform_matrix.py) (Zeile 215).
   - **(b)/(c) Befund:**
     - [`INTEGRATION_QUALITY_SCALE.md`](../INTEGRATION_QUALITY_SCALE.md) behauptet an drei Stellen (Zeilen 92, 130, 152), `test_device_path_sensor_keeps_its_registry_entry_across_reloads` müsse beim Coverage-Lauf mit `--deselect` ausgeschlossen werden, weil dessen Teardown versuche, `/dev/ttyACM0` zu öffnen. Tatsächlich mockt `setup_profile_fixture` in [`test_platform_matrix.py`](../../../tests/components/meltem_ventilation/test_platform_matrix.py) (Zeilen 101–116) `async_validate_gateway` und `async_refresh`, sodass alle `700` Tests (`699 passed, 1 skipped`) ohne `--deselect` fehlerfrei durchlaufen.
     - Zudem behauptet [`INTEGRATION_QUALITY_SCALE.md`](../INTEGRATION_QUALITY_SCALE.md) in Zeile 138, [`config_flow.py`](../../../custom_components/meltem_ventilation/config_flow.py) habe `100 %` Coverage; mit Branch-Coverage (`--cov-branch`) liegt [`config_flow.py`](../../../custom_components/meltem_ventilation/config_flow.py) bei `99 %` (Branch `271->279` in `_temporary_units`: wiederholter Abruf desselben gecachten `slave` in `unit_for` wird in keinem Test durchlaufen, da jede Unit-ID im Setup-Flow nur einmal abgefragt wird).
3. **Widerspruch zwischen [`CHANGELOG.md`](../../../CHANGELOG.md) und dem tatsächlichen Verhalten von `write_control_setting`:**
   - **(a) Referenzen:** [`CHANGELOG.md`](../../../CHANGELOG.md) (Zeile 283) vs. [`coordinator.py`](../../../custom_components/meltem_ventilation/coordinator.py) (Zeilen 176–180, 785–804) und [`test_coordinator_and_config_helpers.py`](../../../tests/components/meltem_ventilation/test_coordinator_and_config_helpers.py) (Zeilen 916–971).
   - **(b)/(c) Befund:** [`CHANGELOG.md`](../../../CHANGELOG.md) (Zeile 283) dokumentiert unter 3.0.0: *"Humidity and CO2 settings are published optimistically after a successful write and stay visible if the immediate confirmation read fails."* Im aktuellen Code (4.2.0) existiert für `control_setting:*` jedoch kein `OptimisticOverlay` in `self._overlays`, und `test_control_setting_keeps_confirmed_value_when_refresh_fails` testet exakt das Gegenteil (nämlich dass der alte Wert `50` statt des geschriebenen Werts `70` sichtbar bleibt, wenn der Readback fehlschlägt), ohne dass diese Verhaltensänderung im Changelog vermerkt wurde.
4. **Schema-Konsistenz (`strings.json`, `translations/de.json`, `translations/en.json`, `icons.json`):**
   - Die Struktur und Schlüssel zwischen [`strings.json`](../../../custom_components/meltem_ventilation/strings.json), [`en.json`](../../../custom_components/meltem_ventilation/translations/en.json), [`de.json`](../../../custom_components/meltem_ventilation/translations/de.json) und [`icons.json`](../../../custom_components/meltem_ventilation/icons.json) sind vollständig synchron und decken alle Entitäts-, Ausnahme-, System-Health- und Flow-Schlüssel ab.

---

## 2. Zusammenfassung der empfohlenen Korrekturen (Roadmap)

1. **[P1-1] Fehler-Propagation zwischen `MeltemModbusClient` und `MeltemDataUpdateCoordinator` reparieren:**
   - In `MeltemDataUpdateCoordinator._read_one_job` auswerten, ob alle im `job.refresh_plan` fälligen Gruppen in diesem Durchlauf fehlgeschlagen sind (`last_attempt >= read_started_at` und `last_error is not None`). Falls ja, `_room_failures[room.key]` inkrementieren und `_last_job_error` setzen (analog zu [`benchmark_integration_like.py`](../../../tools/benchmark_integration_like.py) Zeilen 227–238).
   - Antworten auf das Gateway-lokale Register `40101` (`REGISTER_RF_COMM_STATUS`) nicht als Lebenszeichen des Endgeräts für `seconds_since_read_answer(unit_id)` werten, wenn sämtliche Geräte-Register (`41xxx`/`42xxx`) fehlschlagen.
2. **[P1-2] Zeitstempel-Schutz in `OptimisticOverlay` ergänzen:**
   - In `OptimisticOverlay` den `started_at`-Zeitpunkt (`dt_util.utcnow()`) speichern und in `get()` sowie `settle()` nur dann gegen `confirmed` abgleichen, wenn `read_health.last_successful_read >= pending.started_at` gilt.
3. **[P1-3 & P1-4] `balanced_target_readback` und `target_levels` konsolidieren:**
   - `state.balanced_target_readback` in `target_levels` vorrangig vor dem groben 10er-Raster-Statuswert `state.target_level` verwenden und über Folge-Polls beibehalten, solange sich `state.operation_mode` und `state.target_level` (`41101`) nicht ändern.
   - In `_decode_balanced_target_readback` `state.target_level` nicht mit `derive_balanced_airflow(...)` (Messwerten) vermischen, und in `target_levels` (Zeilen 72–77) bei `state.target_level is None` und `airflow_is_fresh` `(state.supply_air_flow, state.extract_air_flow)` statt `(common, common)` zurückgeben.
4. **[P2-1 & P2-2] Laufzeit-Plausibilitätsprüfung & profilgenaue Component-Restriktion:**
   - `_is_plausible` (`_PLAUSIBLE_RANGES`) auch in `_fresh_values` anwenden, damit Hardware-Sentinels (`239`, `32767`, `65535`) verworfen werden.
   - In `MeltemRoomDevice.__init__` `_only(...)` auch auf `self.extract_air_quality`, `self.supply_air_quality` und `self.control_settings` gemäß `room.profile` anwenden und `self._devices` nach `(room.slave, room.profile)` cachen.
5. **[P3-1 bis P3-5] Diagnostics, Post-Write-Refresh, Options-Flow & Lifecycle bereinigen:**
   - In `async_get_config_entry_diagnostics` `coordinator.client.transport_diagnostics()` **vor** `await coordinator.async_discover_gateway_units()` auslesen.
   - In `MeltemDataUpdateCoordinator._async_refresh_room_after_write` sofort zurückkehren, sobald der Write nicht mehr `"pending"` ist (`not self._writes.needs_confirmation(room.key, write_key)`).
   - In `MeltemVentilationOptionsFlow.async_step_edit_profiles` auf das erneute Modbus-Probing verzichten, wenn `existing_rooms[slave].get("preview")` bereits vorhanden ist.
   - In `async_setup_entry` im Fehlerpfad von `async_forward_entry_setups` `await coordinator.async_shutdown()` ergänzen und in `update_request_rate` den aktiven Refresh-Timer neu planen.
   - `_level_fallbacks` bei einem erfolgreichen Write ohne Fallback löschen und `_async_update_device_registry_versions` pro Raum statt pro Entität ausführen.
