# Meltem Integration TODO

Stand: 3.0.0

Diese Liste enthaelt nur noch offene Punkte. Alles, was frueher hier als
"erledigt" gefuehrt wurde, ist inzwischen in `CHANGELOG.md` beschrieben.
Hardware-Befunde und Reverse-Engineering-Notizen stehen in `docs/MELTEM.md`
und `docs/DEVELOPER.md`.

Offene Punkte, die vor einer Entscheidung eine Messung an echter Hardware
brauchen, stehen in `docs/HARDWARE_BACKLOG.md`.

## 1. Shortcut-Konfiguration in den Shadow-Bereichen `511xx` / `520xx`

Prioritaet: niedrig
Status: offen, reines Reverse-Engineering-Interesse

Befund:
- Lokale Panel-Wechsel auf `Abluft` / `Zuluft` aendern nicht `41120..41124`,
  sondern Shadow-/Meta-Bereiche in `511xx` / `520xx`.
- Wo die App die eigentliche Shortcut-Konfiguration, also die hinterlegten
  Volumenstroeme dieser Kurzmodi, ablegt, ist weiterhin unbekannt.

Einordnung:
- Kein Bug und kein Nutzerproblem: Zu- und Abluft sind seit 3.0.0 direkt ueber
  die beiden Fan-Entities steuerbar, der geratene Schreibpfad wurde entfernt.
- Der Lesepfad erkennt weiterhin, wenn Geraet oder App einen solchen Shortcut
  aktiviert haben.

Naechster Schritt:
- Nur bei Gelegenheit weiter untersuchen, siehe `docs/SETTING_RE_BACKLOG.md`.

## 2. `PRODUCT_ID` sprechend decodieren

Prioritaet: niedrig
Status: offen

Befund:
- `40002 PRODUCT_ID` wird gelesen und als Rohwert in den Geraeteinformationen
  angezeigt.
- Eine Zuordnung zu konkreten Modellbezeichnungen ist nicht bekannt.

Naechster Schritt:
- Werte weiterer Geraete sammeln, bevor eine Decodierung geraten wird.

