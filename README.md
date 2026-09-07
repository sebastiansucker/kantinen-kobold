# GFB Lunch Order – automatische Mittagessen-Bestellung

Automatisiert die wöchentliche Bestellung auf `bestellung-gfb-catering.de`
für mehrere Kinder. Jedes Kind hat einen eigenen GFB-Catering-Account – das
Skript loggt sich für jedes Kind separat ein und durchläuft den kompletten
Ablauf einmal pro Account.

Die Gerichtsauswahl ist **rein regelbasiert möglich, ganz ohne KI**: harte
Ausschlüsse (z. B. "kein Fisch", "kein Fleisch" – erkannt über das
Kost-Kennzeichen, das die Seite jedem Gericht mitgibt) plus eine
Kategorie-Präferenz (z. B. "möglichst DGE, sonst Classic, sonst BIO-Veggie").
Optional kann stattdessen die Claude-API anhand freier Text-Vorlieben
entscheiden – pro Kind wählbar, siehe "Kinder & Regeln anpassen".

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
- Jedes Gericht trägt ein Kost-Kennzeichen (`K`/`F`/`G`) als ersten
  Buchstaben in der letzten Klammer der Beschreibung, live bestätigt anhand
  des Blatt-/Fisch-Icons neben dem Gericht sowie der Allergene-Legende der
  Seite (Fisch = Allergen IV). Wird für regelbasierte Fisch-/Fleisch-
  Ausschlüsse genutzt, siehe "Kinder & Regeln anpassen".
- `lese_menueplan()` wartet nach dem Öffnen des Speiseplans explizit auf die
  erste sichtbare Tag-Karte (nicht nur auf `networkidle`) – Angular rendert
  die Karten clientseitig, das kann nach Ende der Netzwerk-Requests noch
  etwas dauern (live als Race Condition beobachtet, sonst leerer Speiseplan).
- Der Schulferien-Filter (`NUR_AUSSERHALB_SCHULFERIEN`, siehe unten) wurde
  live gegen die echte DOM-Struktur verifiziert (ein Testtag wurde mit einem
  synthetischen Zeitraum korrekt übersprungen, alle anderen Tage blieben
  erhalten) – ein Testlauf mit den echten Brandenburg-2026-Terminen aus
  `schulferien.json` steht noch aus, da aktuell kein Ferientag im sichtbar/
  bestellbaren Zeitfenster der Seite liegt (nächster: Herbstferien ab 19.10.).

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

### Vorgebautes Image nutzen

Bei jedem Push auf `main` baut eine GitHub Action
(`.github/workflows/docker-publish.yml`) das Image automatisch und pusht es
nach `ghcr.io/sebastiansucker/kantinen-kobold:latest`. Statt lokal zu bauen,
kann das Image auch direkt gezogen werden:

```bash
docker pull ghcr.io/sebastiansucker/kantinen-kobold:latest
docker compose up -d
```

Das Package ist an dieses (private) Repository gekoppelt und daher ebenfalls
privat – Zugriff besteht nur mit einem GitHub-Account, der Lesezugriff auf
das Repo hat (vorher ggf. `docker login ghcr.io -u <github-user>` mit einem
Personal Access Token, das `read:packages` erlaubt).

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
| `PLAYWRIGHT_USER_AGENT`            | echter Desktop-Chrome-UA | Siehe "Bot-/Rate-Limit-Erkennung vermeiden" unten |
| `PLAYWRIGHT_TRACE`                 | `false`       | `true` = Playwright-Trace nach `trace.zip` aufzeichnen (mit `playwright show-trace trace.zip` auswertbar) |
| `DATA_DIR`                         | `/data`       | Zielverzeichnis für Screenshots/Trace/Logs bei Fehlern   |
| `STARTUP_JITTER_MAX_SECONDS`       | `900`         | Siehe "Bot-/Rate-Limit-Erkennung vermeiden" unten        |

**Tipp zum Debuggen:** `PLAYWRIGHT_HEADLESS=false` und `PLAYWRIGHT_TRACE=true`
setzen, Skript lokal (außerhalb des Containers) laufen lassen und den Ablauf
im sichtbaren Browser bzw. anschließend per Trace-Viewer nachvollziehen –
z. B. falls die Seite ihr Layout ändert und Selektoren angepasst werden
müssen.

### Bot-/Rate-Limit-Erkennung vermeiden

Bei einem Lauf pro Tag und Kind ist die reine Request-Frequenz gegenüber der
Seite ohnehin sehr niedrig – das eigentliche Risiko ist eher, dass
automatisierter Browser-Traffic als solcher erkannt (und z. B. von einer
Bot-/WAF-Lösung geblockt) statt klassisch "rate-gelimited" wird. Deshalb
setzt `erstelle_browser_context()` ein paar Standardmaßnahmen um, live
verifiziert (Login funktioniert weiterhin, `navigator.userAgent` zeigt einen
normalen Desktop-Chrome ohne "Headless", `navigator.webdriver` ist
`undefined`):

- Fester, realistischer Desktop-Chrome-`User-Agent`
  (`PLAYWRIGHT_USER_AGENT`) statt des von Chromium selbst gemeldeten.
- `navigator.webdriver` wird per Init-Script auf `undefined` gesetzt – das
  von WebDriver/CDP-gesteuerten Browsern gesetzte Flag ist einer der ersten
  Checks vieler Bot-Erkennungen.
- Zufällige Startverzögerung (`STARTUP_JITTER_MAX_SECONDS`, Default 15 min)
  am Anfang von `main()`, damit der tägliche Cronjob nicht jeden Tag exakt
  zur selben Sekunde bei der Seite aufschlägt.

Das ersetzt keine vollständige Browser-Fingerprint-Verschleierung (z. B.
Canvas-/WebGL-Fingerprinting bleibt unangetastet) – für den hier vorliegenden
Zweck (ein bis zwei legitime, mit echten Zugangsdaten eingeloggte Nutzer pro
Tag) ist das aber ausreichend und verhältnismäßig.

## Testen ohne echte Bestellung

Solange `DRY_RUN=true` in `.env` gesetzt ist, wird nur geloggt, welches
Gericht für welches Kind an welchem Tag gewählt würde – es wird **nichts**
tatsächlich abgeschickt. Empfehlung vor der ersten produktiven Nutzung mit
neuer/geänderter Konfiguration: Log-Ausgabe im Dry-Run prüfen, bevor auf
`DRY_RUN=false` umgestellt wird.

## Benachrichtigungen (ntfy.sh)

Optional lässt sich nach jedem Kind eine Push-Benachrichtigung über
[ntfy.sh](https://ntfy.sh) verschicken (Erfolg mit den neu bestellten
Gerichten pro Tag, oder Fehlschlag mit Kind-Name und Fehlergrund) – nützlich,
da das Ergebnis eines Cron-Laufs sonst nur im Log landet. Standardmäßig
deaktiviert (kein `NTFY_TOPIC` gesetzt = keine Nachrichten, keine Änderung am
bisherigen Verhalten).

Einrichtung:

1. In `.env` ein `NTFY_TOPIC` setzen, z. B. `gfb-bestellung-<zufallsstring>`
   (ein schwer erratbarer Name, da unauthentifizierte Topics auf ntfy.sh
   öffentlich lesbar sind für jeden, der den Topic-Namen kennt).
2. Zum Empfangen die [ntfy-App](https://ntfy.sh/#) (Android/iOS/Desktop) oder
   den Browser installieren/öffnen und dort das Topic abonnieren (App: "+"
   → Topic-Namen eingeben; Browser: `https://ntfy.sh/<topic>` öffnen, "Subscribe").
3. Optional `NTFY_URL` auf einen eigenen, selbstgehosteten ntfy-Server
   umstellen (Default: `https://ntfy.sh`).
4. Für ein auf ntfy.sh **reserviertes/geschütztes** Topic (mit ntfy.sh-Account
   unter *Settings → Reservations* anlegbar) zusätzlich unter *Settings →
   Access Tokens* einen Token erzeugen und als `NTFY_TOKEN` eintragen. Für ein
   öffentliches Topic (kein Account nötig) `NTFY_TOKEN` leer lassen.

Fehler beim Versand (z. B. ntfy nicht erreichbar) werden nur geloggt und
bringen den eigentlichen Bestell-Ablauf nicht zum Absturz.

### Home Assistant / Automationen: Lauf-Zusammenfassung (`NTFY_STATUS_TOPIC`)

Seit Home Assistant 2025.5 gibt es eine offizielle
[ntfy-Core-Integration](https://www.home-assistant.io/integrations/ntfy/):
Sie legt pro abonniertem Topic eine **Event-Entity** an, die bei jeder
eingehenden Nachricht feuert und deren komplette Attribute (`title`,
`message`, `tags`, `priority`) für Automationen/Dashboards zur Verfügung
stellt. Für zuverlässige Automationen auf `tags` matchen (z. B. `x` = Fehler,
`white_check_mark` = Erfolg, `information_source` = ok, nichts Neues) statt
den (deutschen) Freitext in `title`/`message` zu parsen.

Wichtig dabei: Auf `NTFY_TOPIC` wird **nur bei einer Änderung oder einem
Fehler** etwas verschickt (siehe oben) – an einem Tag, an dem für die Woche
bereits alles bestellt ist, passiert dort also nichts, und die zugehörige
HA-Event-Entity aktualisiert ihren Zeitstempel nicht. Wer in Home Assistant
(oder einer anderen Automation) den Zeitpunkt des **letzten Laufs**
unabhängig vom Bestellergebnis auswerten will, sollte zusätzlich
`NTFY_STATUS_TOPIC` auf ein zweites, separates Topic setzen: Dorthin geht bei
**jedem** Lauf genau eine kurze Zusammenfassung (Anzahl neu bestellter Tage
pro Kind, ggf. Fehlschläge), unabhängig vom Ergebnis – ohne dass dafür das
Handy-Topic (`NTFY_TOPIC`) mit einer täglichen Nachricht geflutet wird, an
dem ohnehin nichts passiert ist. Nutzt denselben `NTFY_URL`/`NTFY_TOKEN` wie
`NTFY_TOPIC`, muss also separat abonniert werden (Schritt 2 oben, mit dem
Topic-Namen aus `NTFY_STATUS_TOPIC`).

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
    "ausschluesse": ["Fisch", "Fleisch"],
    "bevorzugte_kategorien": ["DGE", "Classic", "BIO-Veggie"],
    "vorlieben": ""
  },
  {
    "name": "Kind 2",
    "benutzername": "kundennummer-oder-login-kind-2",
    "passwort": "passwort-kind-2",
    "ausschluesse": ["Fisch"],
    "bevorzugte_kategorien": [],
    "vorlieben": "mag lieber vegetarisch, keine scharfen Gerichte"
  }
]
```

- **`ausschluesse`**: "Fisch" und "Fleisch" werden über das Kost-Kennzeichen
  erkannt, das die Seite jedem Gericht als ersten Buchstaben in der letzten
  Klammer der Beschreibung mitgibt (`K` = vegetarisch, `F` = Fisch,
  `G` = Fleisch – z. B. sichtbar am Blatt-/Fisch-Icon neben dem Gericht).
  Das ist zuverlässiger als eine Textsuche, da Gerichtnamen wie
  "Lachswürfel" oder "Hähnchenragout" die Wörter "Fisch"/"Fleisch" gar
  nicht enthalten. Alle anderen Einträge (z. B. `"Nüsse"`) werden weiterhin
  als Textsuche in der Beschreibung geprüft.
- **`bevorzugte_kategorien`**: Priorisierte Liste, z. B.
  `["DGE", "Classic", "BIO-Veggie"]` für "möglichst gesund" (DGE = von der
  Deutschen Gesellschaft für Ernährung empfohlene Linie). Es gewinnt die
  erste Kategorie, die nach den Ausschlüssen noch übrig ist – **komplett
  regelbasiert, keine KI/API-Key nötig**. Leer lassen (`[]`), um
  stattdessen die Claude-API anhand von `vorlieben` entscheiden zu lassen
  (siehe Kind 2 im Beispiel oben).

Der Pfad zur Config-Datei kann über die Umgebungsvariable `KINDER_CONFIG`
gesetzt werden (Default: `config.json`). `config.json` liegt in `.gitignore`
und wird nicht mit ins Repo übernommen.

## Schulferien

Standardmäßig wird **nicht in den Schulferien bestellt**
(`NUR_AUSSERHALB_SCHULFERIEN=true`), da während der Ferien meist keine
Schulverpflegung stattfindet. Es wird davon ausgegangen, dass alle Kinder im
selben Bundesland zur Schule gehen – das Bundesland ist daher global über
`BUNDESLAND` (Default `BB`/Brandenburg) konfigurierbar, nicht pro Kind.

Die Ferientermine kommen standardmäßig **dynamisch** von der öffentlichen
[OpenHolidays API](https://www.openholidaysapi.org) (`SCHULFERIEN_QUELLE=api`,
Default) – das aktuelle Jahr und das Folgejahr werden bei jedem Lauf frisch
abgefragt, inklusive einzelner beweglicher Ferientage/Brückentage (z. B. der
26.05.2026 nach Christi Himmelfahrt), sodass die Liste **nicht mehr jährlich
von Hand gepflegt werden muss**. Schlägt die Abfrage fehl (kein
Netzwerkzugriff, Timeout, API nicht erreichbar), wird automatisch auf die
lokale Datei [`schulferien.json`](schulferien.json) zurückgefallen und eine
Warnung geloggt.

Mit `SCHULFERIEN_QUELLE=datei` lässt sich stattdessen ausschließlich die
lokale Datei verwenden (kein Netzwerkzugriff nötig). Sie ist im gleichen
Format wie die API-Antwort aufgebaut, pro Bundesland als Liste von
Zeiträumen (`von`/`bis`, inklusive) – einzelne bewegliche Ferientage werden
genauso als Eintrag mit `von == bis` abgebildet und zählen als vollwertiger
Ferientag:

```json
{
  "BB": [
    { "name": "Sommerferien 2026", "von": "2026-07-09", "bis": "2026-08-22" },
    { "name": "Beweglicher Ferientag 2026 (Brückentag nach Christi Himmelfahrt)", "von": "2026-05-26", "bis": "2026-05-26" }
  ]
}
```

Diese Datei liegt (anders als `config.json`) im Repo, da sie keine
Zugangsdaten enthält, sondern nur öffentliche Ferientermine – sie dient nur
noch als Fallback und muss daher nicht mehr aktuell gehalten werden, sofern
die API erreichbar ist. Aktuell ist nur Brandenburg (`BB`) für 2026
hinterlegt; für den Fallback-Fall bei anderen Bundesländern oder Jahren
müssten entsprechende Einträge ergänzt werden (Quelle z. B.
[schulferien.org](https://www.schulferien.org/deutschland/ferien/brandenburg/)).
Fehlt ein Bundesland in der Datei, wird das nur geloggt (keine Ferienprüfung
für dieses Bundesland) statt das Skript abzubrechen.

Auf `NUR_AUSSERHALB_SCHULFERIEN=false` setzen, um auch in den Ferien zu
bestellen (z. B. bei Ferienbetreuung mit Verpflegung).

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

In `crontab`: `0 6 * * *` = **täglich** um 06:00 Uhr. Format: Minute Stunde
Tag Monat Wochentag (0 = Sonntag … 6 = Samstag).

Bewusst täglich statt nur einmal pro Woche: Die Bestellfrist für die
Folgewoche kann schon Mittwoch/Donnerstag der Vorwoche ablaufen – ein
einzelner Wochenlauf (z. B. nur Montag) könnte das verpassen, falls der
Speiseplan für die Folgewoche zu diesem Zeitpunkt noch nicht (vollständig)
sichtbar war. Wiederholte Läufe sind sicher:

- `bestelle_gericht()` lässt einen Tag unangetastet, sobald für ihn
  **irgendeine** Option bereits bestellt ist (`check`-Status) – auch wenn
  eine andere Kategorie ausgewählt wäre. Ein erneuter Lauf dreht also weder
  eine frühere automatische noch eine manuell in der App geänderte
  Bestellung zurück.
- `bestellung_abschliessen()` erkennt einen leeren Warenkorb (Bestätigen-
  Button per `disabled` deaktiviert, live bestätigt) und kehrt ohne Aktion
  zurück, statt auf einen nicht klickbaren Button zu warten.

Beides wurde live gegen die echte Seite verifiziert (bereits bestellter Tag
blieb bei erneutem Aufruf mit anderer simulierter Präferenz unverändert;
leerer Warenkorb führte zu keiner Aktion statt einem Timeout).
