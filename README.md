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

## Status: Gerüst – noch nicht einsatzbereit

Ich konnte die Zielseite noch nicht live einsehen (kein verbundener Browser).
Alle mit `# TODO` markierten Stellen in `order_lunch.py` sind Platzhalter für
CSS-Selektoren / Texte, die zur echten Seite passen müssen. So findest du sie:

1. Seite im Chrome öffnen, einloggen.
2. Rechtsklick auf das jeweilige Element (Login-Feld, Menüplan-Tag, Gericht,
   Bestätigungs-Button) → **Untersuchen**.
3. Den passenden Selektor (id, class, text) in `order_lunch.py` eintragen.

**Alternative:** Wenn du die Claude-in-Chrome-Erweiterung installierst und
verbindest, kann ich die Seite (ohne deine Zugangsdaten selbst einzugeben)
inspizieren und die Selektoren direkt für dich ausfüllen.

## Setup

```bash
cp .env.example .env
# .env mit echten Werten füllen (Login, Anthropic API-Key)
# DRY_RUN=true lassen, bis die Selektoren geprüft sind!

docker compose build
docker compose up -d
```

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
