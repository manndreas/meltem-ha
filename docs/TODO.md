# Meltem Integration TODO

Stand: 2026-03-31

Diese Liste basiert auf einer Code-Analyse des aktuellen Integrationsstands.
Sie beschreibt bewusst zuerst den Ist-Zustand, damit wir die Themen geordnet
und mit klaren Hypothesen angehen koennen.

## 1. `operation_mode` und `preset_mode` sauber abgrenzen

Prioritaet: hoch
Status: teilweise umgesetzt am 2026-03-31

Problem:
- Aktuell gibt es zwei Select-Entitaeten:
  - `operation_mode` fuer dokumentierte Betriebsarten wie `off`, `manual`,
    `unbalanced`, `humidity_control`, `co2_control`, `automatic`
  - `preset_mode` fuer app-/keypad-aehnliche Kurzmodi wie `low`, `medium`,
    `high`, `intensive`, `extract_only`, `supply_only`

Analyse:
- Das ist keine reine Dublette.
- `operation_mode` beschreibt den grundlegenden Regelmodus.
- `preset_mode` beschreibt einen zweiten, reverse-engineerten Shortcut-Pfad
  mit eigenen Register-Codes und teils eigener LED-/Keypad-Semantik.
- Im aktuellen Code werden beide deshalb getrennt geschrieben und getrennt
  dekodiert.
- Eine naive Zusammenlegung in eine einzige Select-Entitaet waere
  wahrscheinlich eine schlechte Idee, weil dadurch zwei verschiedene
  Bedeutungen vermischt wuerden:
  - "Wie regelt das Geraet grundsaetzlich?"
  - "Welcher Bedienfolien-/App-Shortcut ist aktiv?"

Code-Stellen:
- `custom_components/meltem_ventilation/select.py`
- `custom_components/meltem_ventilation/modbus_client.py`
- `custom_components/meltem_ventilation/coordinator.py`

Naechster Schritt:
- Teilweise umgesetzt:
  - die Trennung bleibt intern bestehen
  - die sichtbaren Namen wurden geschaerft:
    - `operation_mode` -> `Regelmodus` / `Control mode`
    - `preset_mode` -> `Schnellmodus` / `Quick mode`
- Offen bleibt:
  - ob wir `operation_mode` zusaetzlich noch staerker als Experten-Entity
    behandeln wollen
  - ob `preset_mode` langfristig der primaere Nutzerpfad werden soll

## Zusatz: Slider-UX fuer Luftstrom

Prioritaet: mittel
Status: teilweise umgesetzt am 2026-03-31

Entscheidung:
- Der gemeinsame Slider `Volumenstrom` bleibt erhalten.
- Er ist der einfachste und passendste Bedienpfad fuer den haeufigen
  symmetrischen Fall `Zuluft = Abluft`.
- Die getrennten Slider `Zuluft-Sollwert` und `Abluft-Sollwert` bleiben als
  Experten-/Spezialwerkzeug vorhanden, sind fuer neue Installationen aber
  standardmaessig deaktiviert.

Begruendung:
- Anders als die entfernten abgeleiteten Sensoren ist der gemeinsame Slider
  nicht nur redundant, sondern hat eine eigene UX-Rolle.
- Ohne ihn wuerde der Standardfall unnötig komplizierter werden, weil stets
  zwei Slider bewegt werden muessten.

Offene UX-Punkte aus manuellem Test:
- Umgesetzt:
  - `Zuluft-Sollwert` / `Abluft-Sollwert` wurden in
    `Zuluftvolumenstrom` / `Abluftvolumenstrom` umbenannt
  - der gemeinsame Slider heisst nun `Gemeinsamer Volumenstrom`
- Umgesetzt:
  - wenn die beiden Richtungen per Slider auseinandergezogen werden, springt
    der gemeinsame Slider jetzt sofort auf `0`, statt erst nach spaeterem
    Refresh seinen synchronen Zustand zu verlieren

## 2. Redundante Luftstrom-Sensoren pruefen und vereinfachen

Prioritaet: hoch
Status: erledigt am 2026-03-31

Problem:
- `average_air_flow` und `current_level` wirken neben
  `extract_air_flow` und `supply_air_flow` potenziell ueberfluessig oder
  missverstaendlich.

Analyse:
- `extract_air_flow` und `supply_air_flow` spiegeln die tatsaechlich
  gemessenen Richtungen direkt wider und passen besser zur Meltem-App.
- `average_air_flow` ist nur ein abgeleiteter Mittelwert dieser beiden Werte.
- Der Sensor `current_level` ist besonders verwirrend:
  - Im `RoomState` war `current_level` eigentlich ein Zielwert-/Readback-Konzept
    aus `41121`.
  - Die Sensor-Entitaet `current_level` zeigt aber nicht diesen Readback an,
    sondern einen abgeleiteten "gemeinsamen Volumenstrom" aus den gemessenen
    Luftstroemen.
- Damit ist der Name aktuell semantisch unsauber.

Code-Stellen:
- `custom_components/meltem_ventilation/sensor.py`
- `custom_components/meltem_ventilation/models.py`
- `custom_components/meltem_ventilation/strings.json`
- `custom_components/meltem_ventilation/translations/de.json`

Naechster Schritt:
- Abgeschlossen:
  - `average_air_flow` wurde als Sensor entfernt
  - `current_level` wurde als Sensor entfernt
  - der interne Readback-Wert heisst jetzt konsistent `RoomState.target_level`
    und entspricht damit seiner echten Rolle in der Target-/Readback-Logik

## 3. Nach `preset_mode`-Aenderungen frueher und gezielter refreshen

Prioritaet: hoch
Status: erledigt am 2026-03-31

Problem:
- Nach einer Aenderung des `preset_mode` dauern neue Volumenstroeme oft zu
  lange, bis sie in Home Assistant sichtbar werden.

Analyse:
- `async_set_preset_mode()` wartet aktuell `WRITE_SETTLE_SECONDS` und fuehrt
  danach nur einen airflow-orientierten Post-Write-Refresh aus.
- Das Retry-System in `_async_refresh_room_after_write()` wird fuer
  `preset_mode` derzeit faktisch nicht genutzt, weil keine erwarteten Zielwerte
  mitgegeben werden.
- Dadurch endet der Refresh meist nach dem ersten, oft noch zu fruehen
  Readback.
- Gleichzeitig ist bekannt, dass `41020/41021` der effektive Luftstrom sind
  und einige Sekunden nachhaengen koennen.

Code-Stellen:
- `custom_components/meltem_ventilation/coordinator.py`
- `docs/DEVELOPER.md`

Naechster Schritt:
- Abgeschlossen:
  - Preset-Wechsel erzwingen jetzt mindestens einen zusaetzlichen fruehen
    Airflow-Refresh nach dem ersten Readback
  - dadurch wird der typische Nachlauf von `41020/41021` besser abgefangen,
    ohne fuer unbekannte Shortcut-Konfigurationen falsche Sollwerte zu raten

## 4. `preset_mode` bleibt nach dem Schreiben auf `unknown`

Prioritaet: sehr hoch
Status: teilweise erledigt am 2026-03-31

Problem:
- Nach dem Setzen eines `preset_mode` wird die Einstellung am Geraet zwar
  geaendert, in Home Assistant bleibt der Select aber auf `unknown`.

Wahrscheinliche Ursache:
- Der Readback fuer `operation_mode`/`preset_mode` nutzt einen optionalen
  Registerblock ab `41120` mit Laenge `5`.
- Wenn dieser Block einmal fehlschlaegt, wird ein Backoff auf dem Schluessel
  `(slave, 41120, 5)` gesetzt.
- Nach einem erfolgreichen Write wird dieser Backoff aktuell nicht fuer genau
  diesen Schluessel geloescht.
- Stattdessen werden andere Schluessel geloescht, unter anderem
  `(slave, 41120, 2)`.
- Folge: Der wichtige Mode-Block kann nach dem Write weiter unterdrueckt
  bleiben, und `preset_mode` faellt auf den alten Zustand oder `None` zurueck.

Code-Stellen:
- `custom_components/meltem_ventilation/modbus_client.py`
  - `_read_optional_airflow_uint16_block()`
  - `_clear_optional_airflow_read_backoff()`
  - `_decode_preset_mode()`

Naechster Schritt:
- Erledigt:
  - Backoff-Clearing fuer `(slave, 41120, 5)` wurde korrigiert
  - der Readback faellt jetzt defensiv von `41120..41124` auf
    `41120..41121` zurueck, wenn der 5er-Block nicht lesbar ist
  - bekannte Faelle mit altem festhaengendem `preset_mode` wurden per Tests
    abgesichert
- Offen bleibt nach manuellen Tests:
  - der Select `Schnellmodus` wirkt noch nicht in allen Faellen stabil
  - nach `mittel -> intensiv -> zurueck` blieb der sichtbare Wert einmal auf
    `hoch` haengen, obwohl das Geraet korrekt reagierte
  - das deutet auf einen weiteren Readback-/Zuordnungsfehler hin

## 4a. `Schnellmodus` sollte in der UI optimistisch reagieren

Prioritaet: mittel
Status: erledigt am 2026-03-31

Problem:
- Beim Wechsel des `Schnellmodus` springt der sichtbare Select-Wert aktuell
  erst nach dem Readback.
- Das fuehlt sich im UI traege an.

Analyse:
- Fuer bewusst vom Nutzer ausgeloeste Select-Aenderungen waere ein
  optimistisches UI-Verhalten wahrscheinlich angenehmer.
- Wichtig waere dann nur, bei Fehlversuchen oder widersprechendem Readback
  sauber wieder auf den echten Zustand zurueckzufallen.

Naechster Schritt:
- Abgeschlossen:
  - `Schnellmodus` reagiert jetzt optimistisch und faellt bei Fehlschlag oder
    bestaetigtem Readback wieder sauber auf den echten Zustand zurueck
  - bei manuellen Slider-Aenderungen geht `Schnellmodus` jetzt sofort auf
    `unknown`, statt bis zum spaeteren Readback auf dem alten Preset zu bleiben

## 4b. `Schnellmodus` bleibt nach `intensive` / Rueckwechsel auf falschem Wert stehen

Prioritaet: hoch
Status: erledigt am 2026-03-31

Problem:
- Im manuellen Test funktionierte `mittel -> intensiv -> zurueck` am Geraet
  korrekt.
- Danach blieb der sichtbare Wert im Dropdown aber auf `hoch` stehen.
- Weitere Aenderungen funktionierten am Geraet, der sichtbare Select-Wert
  blieb jedoch falsch.

Analyse:
- Das sieht nach einem verbleibenden Zuordnungsproblem im `preset_mode`-
  Readback aus, nicht nach einem Write-Problem.
- Besonders verdaechtig sind Mischzustande aus:
  - altem Preset-Code in `41121`
  - intensivbezogenem Shadow in `41123/41124`
  - spaeterem Fallback von vollem auf kurzen Mode-Block

Naechster Schritt:
- Abgeschlossen:
  - `intensive` wird nicht mehr als normaler Schnellmodus behandelt
  - stattdessen wurde `Intensivlueftung starten` als eigener Button
    modelliert
  - ein aktiver Intensiv-Override soll den eigentlichen Schnellmodus beim
    Readback nicht mehr ueberschreiben
  - der Select reagiert jetzt optimistisch in der UI
  - das Icon fuer `Schnellmodus` wurde auf ein schlichteres Symbol umgestellt
  - manueller HA-Test bestaetigt inzwischen, dass der `Intensiv`-Pfad und der
    anschliessende Rueckwechsel sauber funktionieren

## Zusatz: Kurze Transportfehler nicht sofort als globale Nichtverfuegbarkeit anzeigen

Prioritaet: mittel
Status: erledigt am 2026-03-31

Entscheidung:
- Kurzzeitige transportbedingte Coordinator-Fehler behalten jetzt zunaechst
  den letzten gueltigen Gesamtzustand, statt sofort alle Entities auf
  unavailable/unknown kippen zu lassen.

Begruendung:
- Ein einzelner kurzer serieller Aussetzer soll die UI nicht sofort komplett
  irritierend auf "unbekannt" setzen.
- Erst nach mehreren aufeinanderfolgenden groben Transportfehlern wird wieder
  ein echter Coordinator-Fehler nach oben gereicht.

## Zusatz: Diagnose-Flags `40103` / `40104`

Prioritaet: niedrig
Status: erledigt am 2026-03-31

Entscheidung:
- Die beiden bisher nur geraten benannten Diagnose-Flags `40103` und `40104`
  wurden wieder aus Home Assistant entfernt.

Begruendung:
- Solange ihre Bedeutung nicht belastbar dokumentiert ist, stiften sie in der
  UI mehr Verwirrung als Nutzen.
- Der normale, dokumentierte Fehlerpfad bleibt ueber `Fehlerstatus`
  unveraendert sichtbar.

## 5. Nach `intensive` lassen sich andere Presets zunaechst nicht sauber setzen

Prioritaet: sehr hoch
Status: erledigt am 2026-03-31

Problem:
- Wenn `preset_mode = intensive` gesetzt wurde, funktionieren weitere
  Preset-Aenderungen zunaechst nicht sauber.
- Spaeteres Setzen von z. B. `low` kann wieder `intensive` aktivieren.

Wahrscheinliche Ursache:
- `intensive` wird ueber einen separaten Registerpfad `41123/41124`
  geschrieben.
- `low`/`medium`/`high` dagegen ueber `41120/41121`.
- Beim Dekodieren wird `intensive` bevorzugt erkannt, sobald `41123/41124`
  entsprechend gesetzt sind.
- Aktuell gibt es keinen offensichtlichen Schritt, der diese
  `intensive`-Register beim Wechsel auf andere Presets explizit zuruecksetzt.
- Dadurch koennen Readback und eventuell auch Geraetezustand in einen
  widerspruechlichen Zwischenzustand geraten.

Code-Stellen:
- `custom_components/meltem_ventilation/modbus_client.py`
  - `write_preset_mode()`
  - `_decode_preset_mode()`

Naechster Schritt:
- Abgeschlossen:
  - Nicht-`intensive`-Presets raeumen jetzt vor dem eigentlichen Write den
    sekundaeren Preset-Pfad `41123/41124` aktiv auf
  - dadurch kann ein alter `intensive`-Shadow den anschliessenden
    Preset-Readback nicht mehr uebersteuern

## 6. `extract_only` / `supply_only` schalten, aber LED-Semantik stimmt nicht

Prioritaet: mittel bis hoch
Status: wahrscheinlich kein Bug, Befund geschaerft am 2026-03-31

Problem:
- `extract_only` und `supply_only` funktionieren funktional, aber die LEDs am
  Geraet leuchten nicht wie erwartet.

Analyse:
- Der Code nutzt bereits den reverse-engineerten unbalanced-Preset-Pfad mit
  `200 + airflow / 10`.
- Allerdings nimmt die Integration dafuer einen live abgeleiteten
  `preferred_level`, nicht den tatsaechlich im Geraet/App gespeicherten
  Shortcut-Wert.
- Laut Reverse-Engineering ist die eigentliche Shortcut-Konfiguration noch
  nicht bekannt.
- Hardware-Test am 2026-03-31 auf `slave 2`:
  - direkte Runtime-Writes wie `41120 = 4`, `41121 = 0`, `41122 = 201`
    bzw. `205`, dann `41132 = 0`, werden vom Geraet akzeptiert und aendern
    den Luftstrom funktional korrekt
  - die LEDs am Bedienpanel bleiben dabei dennoch aus
  - lokale Panel-Wechsel auf `Abluft` / `Zuluft` aendern dagegen nicht
    `41120..41124`, sondern Shadow-/Meta-Bereiche in `511xx` / `520xx`
- Spaetere Verifikation am gleichen Setup:
  - auch die offizielle Meltem-App schaltet bei `Abluft` / `Zuluft` die LEDs
    am Bedienpanel nicht ein
- Damit ist die wahrscheinlichste Einordnung inzwischen:
  - kein echter Integrations-Bug
  - sondern normales Geraeteverhalten fuer diese beiden Shortcuts

Code-Stellen:
- `custom_components/meltem_ventilation/modbus_client.py`
  - `write_preset_mode()`
  - `_encode_app_unbalanced_preset_level()`
- `docs/MELTEM.md`
- `docs/DEVELOPER.md`

Naechster Schritt:
- Teilweise umgesetzt:
  - die Integration merkt sich jetzt zuletzt beobachtete `extract_only`- und
    `supply_only`-Shortcut-Level pro Raum und verwendet diese bei spaeteren
    Writes bevorzugt wieder
- Hardware-Test hat gezeigt:
  - das allein loest die LED-Semantik nicht
- Aktuelle Empfehlung:
  - vorerst kein weiterer Fix in der Integration noetig
  - die Shadow-/Meta-Bereiche koennen spaeter weiter untersucht werden, aber
    eher aus Reverse-Engineering-Interesse als fuer einen akuten Bugfix

## 7. Entfernte Luftstrom-Sensoren erscheinen in HA noch

Prioritaet: niedrig bis mittel
Status: offen / wahrscheinlich kein Code-Bug

Problem:
- Nach dem Entfernen von `average_air_flow` und dem sichtbaren
  `current_level`-Sensor tauchen bei manuellen Tests offenbar weiterhin
  "durchschnittlicher" und "gemeinsamer" Volumenstrom in HA auf.

Analyse:
- Im aktuellen Code existieren diese Sensorbeschreibungen nicht mehr.
- Wenn sie weiterhin sichtbar sind, ist die wahrscheinlichste Ursache:
  - alter Entity-Registry-Bestand in Home Assistant
  - fehlender Reload / Neustart nach Codeaenderung

Naechster Schritt:
- bei Testnotizen klar festhalten:
  - Integration neu laden oder HA neu starten
  - alte Entities gegebenenfalls im Entity-Registry entfernen

## 8. Geraeteinformationen ausbauen

Prioritaet: mittel
Status: erledigt am 2026-03-31

Themen:
- `Software-Version` sollte auf der Geraeteinformationen-Karte sauber
  erscheinen
- `40002 PRODUCT_ID` sollte zusaetzlich als Geraeteinformation sichtbar
  werden, auch wenn wir ihn noch nicht sprechend decodieren koennen

Analyse:
- `sw_version` wird bereits in `device_info` gesetzt, sollte also grundsaetzlich
  auf der Geraetekarte erscheinen
- `PRODUCT_ID` lesen wir im Setup-/Probe-Pfad bereits, speichern ihn aber
  derzeit nicht im Runtime-/Device-Info-Pfad

Naechster Schritt:
- Abgeschlossen:
  - `sw_version` erscheint auf der Geraetekarte
  - die rohe `PRODUCT_ID` aus dem Setup-Preview wird als
    `Produkt-ID ...` in den Geraeteinformationen angezeigt
  - der separate Diagnosesensor `Software-Version` wurde entfernt, weil die
    Information nun sinnvoller an der Geraetekarte haengt

## 9. Interne Diagnoseflags `40103` / `40104` entfernen

Prioritaet: mittel
Status: erledigt am 2026-03-31

Problem:
- Die Register `40103` und `40104` sind zwar neutraler benannt, aber wir
  koennen ihre Bedeutung weiterhin nicht sinnvoll erklaeren.

Analyse:
- Solange diese Flags weder dokumentiert noch praktisch nuetzlich sind,
  erzeugen sie eher Verwirrung als Mehrwert.
- Dass sie standardmaessig deaktiviert sind, mildert das Problem, loest es
  aber nicht vollstaendig.

Naechster Schritt:
- Abgeschlossen:
  - die beiden Diagnoseflags wurden komplett entfernt

## 10. Icons und Bezeichnungen im Luftstrom-/Preset-UI verfeinern

Prioritaet: niedrig bis mittel
Status: erledigt am 2026-03-31

Offene UX-Punkte:
- Abgeschlossen:
  - die Icons fuer Abluft und Zuluft wurden vertauscht
  - das Icon fuer `Schnellmodus` wurde auf ein schlichteres Symbol umgestellt
  - `Fehlerstatus` wurde sprachlich zu `Geräte-Fehlerstatus` geschärft

## Empfohlene Reihenfolge

1. Geraeteinformations-Karte in HA pruefen (`Software-Version`, Hardware-ID)
2. Optionaler Aufraeumhinweis fuer alte entfernte Entities in HA dokumentieren

## Bereits geprueft

- Relevante lokale Tests fuer Select-, Sensor-, Modbus- und Coordinator-Pfade
  laufen aktuell gruen:
  - `tests/test_select_platform.py`
  - `tests/test_modbus_client.py`
  - `tests/test_coordinator_and_config_helpers.py`
  - `tests/test_coordinator_integration.py`
  - `tests/test_sensor_platform.py`
