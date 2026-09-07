# GFB Lunch Order – automatische Mittagessen-Bestellung

Automatisiert die wöchentliche Bestellung auf `bestellung-gfb-catering.de`
für mehrere Kinder, mit regelbasierter Vorfilterung (Ausschlüsse wie "kein Fisch")
und KI-gestützter Auswahl (Claude API) unter den verbleibenden Optionen. Jedes
Kind hat einen eigenen GFB-Catering-Account – das Skript loggt sich für jedes
Kind separat ein und durchläuft den kompletten Ablauf einmal pro Account.

> **Hinweis zur Wiederverwendbarkeit:** Die Selektoren sind spezifisch für das
> Bestellportal `bestellung-gfb-catering.de`. Das Projekt eignet sich daher
> primär für andere Familien/Schulen, die dieselbe GFB-Catering-Plattform
> nutzen – nicht als generische Lösung für beliebige Bestellportale.
>
> **Disclaimer:** Nutzung auf eigene Verantwortung, ohne Gewähr. Bitte vor
> dem Einsatz die Nutzungsbedingungen des jeweiligen Bestellportals prüfen –
> manche Anbieter untersagen automatisierte Zugriffe explizit.

## Status: Kompletter Ablauf end-to-end bestätigt

Login, Speiseplan-Auslesen, Gerichtsauswahl und Bestellbestätigung wurden
per Playwright live gegen die echte Seite geprüft und die Selektoren in
`order_lunch.py` entsprechend aktualisiert:

- Benutzername: `input#benutzername` (Angular `formcontrolname="login"`)
- Passwort: `input#passwort` (Angular `formcontrolname="password"`)
- Login-Button: `<button>Anmelden</button>`
- Der Bestellbereich der App heißt durchgängig **"Speiseplan"**, nicht
  "Menüplan" wie ursprünglich vermutet.
- Ein Tag im Speiseplan ist `div.speiseplan-tagWbp`, darin mehrere
  `.speiseplanMenu`-Karten (Kategorie z. B. "DGE"/"Classic"/"BIO-Veggie" +
  Beschreibung) mit je einem Bestell-Icon-Button
  `[data-testid="order-einzeln"]` (Zustände: `add` = bestellbar,
  `check` = bereits bestellt, Klasse `disabled` = Frist abgelaufen).
- Der Warenkorb (`#/warenkorb`) zeigt ausstehende Änderungen und einen
  Button "Zum genannten Preis bestätigen" zum endgültigen Abschicken.
  **Wichtig:** Der Warenkorb wird nur clientseitig in der laufenden
  Browser-Sitzung gehalten – Login, Auswahl und Bestätigung müssen in
  einer durchgehenden Playwright-Session laufen (wie in `main()`).
- Mit einer echten Testbestellung (ein Gericht, ein Tag) end-to-end
  verifiziert; Erfolgstext nach dem Bestätigen: "Vielen Dank. Die
  Bestellung für den angegebenen Zeitraum wurde erfolgreich im System
  hinterlegt."

Da jedes Kind einen eigenen Account hat (kein gemeinsamer Account mit
Kind-Umschaltung), läuft `main()` den kompletten Ablauf separat pro Kind in
einer eigenen Playwright-Session (`bestelle_fuer_kind()`). Schlägt ein
Account fehl, werden die übrigen Kinder trotzdem weiterverarbeitet; am Ende
des Laufs wird ein Fehler gemeldet, falls mindestens ein Account
fehlgeschlagen ist (wichtig für Cron-Benachrichtigungen).

## Setup

```bash
cp .env.example .env
# .env mit echten Werten füllen (Anthropic API-Key)
cp config.json.example config.json
# config.json mit den echten Zugangsdaten pro Kind füllen (siehe unten)
# DRY_RUN=true lassen, bis der erste Testlauf geprüft ist!

docker compose build
docker compose up -d
```

## Playwright-Konfiguration

Das Browser-Verhalten wird zentral in `PlaywrightConfig` (`order_lunch.py`)
gebündelt und lässt sich über Umgebungsvariablen (siehe `.env.example`)
steuern, ohne den Code anzufassen:

| Variable                          | Default        | Bedeutung                                              |
|------------------------------------|---------------|---------------------------------------------------------|
| `PLAYWRIGHT_HEADLESS`              | `true`        | `false` = Browser sichtbar starten (Selektoren prüfen)   |
| `PLAYWRIGHT_SLOWMO_MS`             | `0`           | Aktionen künstlich verlangsamen (ms)                     |
| `PLAYWRIGHT_ACTION_TIMEOUT_MS`     | `15000`       | Timeout für einzelne Aktionen (Klick, Fill, …)           |
| `PLAYWRIGHT_NAVIGATION_TIMEOUT_MS` | `30000`       | Timeout für Seitennavigationen                           |
| `PLAYWRIGHT_LOCALE`                | `de-DE`       | Browser-Locale                                           |
| `PLAYWRIGHT_TIMEZONE`              | `Europe/Berlin` | Browser-Zeitzone                                       |
| `PLAYWRIGHT_VIEWPORT_WIDTH/HEIGHT` | `1280x900`    | Viewport-Größe                                           |
| `PLAYWRIGHT_TRACE`                 | `false`       | `true` = Playwright-Trace nach `trace.zip` aufzeichnen (mit `playwright show-trace trace.zip` auswertbar) |
| `DATA_DIR`                         | `/data`       | Zielverzeichnis für Screenshots/Trace/Logs bei Fehlern   |

**Tipp beim Prüfen der TODO-Selektoren:** `PLAYWRIGHT_HEADLESS=false` und
`PLAYWRIGHT_TRACE=true` setzen, Skript lokal (außerhalb des Containers)
laufen lassen und den Ablauf im sichtbaren Browser bzw. anschließend per
Trace-Viewer nachvollziehen.

## Testen ohne echte Bestellung

Solange `DRY_RUN=true` in `.env` gesetzt ist, wird nur geloggt, welches
Gericht für welches Kind an welchem Tag gewählt würde – es wird **nichts**
tatsächlich abgeschickt. Erst wenn die Selektoren geprüft sind und die
Log-Ausgabe plausibel aussieht, auf `DRY_RUN=false` umstellen.

## Kinder & Regeln anpassen

```bash
cp config.json.example config.json
```

Dann `config.json` anpassen (Zugangsdaten, Ausschlüsse, Vorlieben pro Kind).
Jedes Kind hat einen eigenen GFB-Catering-Account:

```json
[
  {
    "name": "Kind 1",
    "benutzername": "kundennummer-oder-login-kind-1",
    "passwort": "passwort-kind-1",
    "ausschluesse": ["Fisch"],
    "vorlieben": "mag lieber vegetarisch"
  },
  {
    "name": "Kind 2",
    "benutzername": "kundennummer-oder-login-kind-2",
    "passwort": "passwort-kind-2",
    "ausschluesse": ["Fisch", "Schweinefleisch"],
    "vorlieben": ""
  }
]
```

Der Pfad zur Config-Datei kann über die Umgebungsvariable `KINDER_CONFIG`
gesetzt werden (Default: `config.json`). `config.json` liegt in `.gitignore`
und wird nicht mit ins Repo übernommen.

## Sicherheit

- **`config.json` enthält Klartext-Zugangsdaten für zwei echte Accounts** (ein
  Account pro Kind) und darf niemals committet werden – liegt bereits in
  `.gitignore`, trotzdem vor jedem `git add`/Commit gegenprüfen.
- Für produktiven Einsatz auf Unraid: `config.json` idealerweise über
  Unraid-Secrets oder eine restriktive Datei-Berechtigung absichern statt
  Klartext im Share.
- Bei Fehlern wird automatisch ein Screenshot nach
  `/data/error_screenshot_<Kindname>.png` geschrieben – hilfreich zum
  Debuggen fehlgeschlagener Selektoren, enthält aber ggf. personenbezogene
  Bestelldaten und sollte entsprechend behandelt werden.

## Cron-Zeitpunkt ändern

In `crontab`: `0 6 * * 1` = Montag 06:00 Uhr. Format: Minute Stunde Tag Monat
Wochentag (0 = Sonntag … 6 = Samstag).
