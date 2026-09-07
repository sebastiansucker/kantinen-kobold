# GFB Lunch Order – automatische Mittagessen-Bestellung

Automatisiert die wöchentliche Bestellung auf `bestellung-gfb-catering.de`
für mehrere Kinder, mit regelbasierter Vorfilterung (Ausschlüsse wie "kein Fisch")
und KI-gestützter Auswahl (Claude API) unter den verbleibenden Optionen.

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

**Noch nicht verifiziert:** Der Ablauf zum Umschalten zwischen mehreren
Kindern im selben Account (der getestete Account hatte nur ein Kind
hinterlegt) – siehe TODO in `bestelle_gericht()`.

## Setup

```bash
cp .env.example .env
# .env mit echten Werten füllen (Login, Anthropic API-Key)
# DRY_RUN=true lassen, bis die Selektoren geprüft sind!

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

Dann `config.json` anpassen (Name, Ausschlüsse, Vorlieben pro Kind):

```json
[
  { "name": "Kind 1", "ausschluesse": ["Fisch"], "vorlieben": "mag lieber vegetarisch" },
  { "name": "Kind 2", "ausschluesse": ["Fisch", "Schweinefleisch"], "vorlieben": "" }
]
```

Der Pfad zur Config-Datei kann über die Umgebungsvariable `KINDER_CONFIG`
gesetzt werden (Default: `config.json`). `config.json` liegt in `.gitignore`
und wird nicht mit ins Repo übernommen.

## Sicherheit

- Zugangsdaten liegen nur in `.env` (nicht ins Git-Repo committen, `.env` in
  `.gitignore` aufnehmen).
- Für produktiven Einsatz auf Unraid: `.env` idealerweise über Unraid-Secrets
  oder eine restriktive Datei-Berechtigung absichern statt Klartext im Share.
- Bei Fehlern wird automatisch ein Screenshot nach `/data/error_screenshot.png`
  geschrieben – hilfreich zum Debuggen fehlgeschlagener Selektoren.

## Cron-Zeitpunkt ändern

In `crontab`: `0 6 * * 1` = Montag 06:00 Uhr. Format: Minute Stunde Tag Monat
Wochentag (0 = Sonntag … 6 = Samstag).
