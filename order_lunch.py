"""
GFB Catering – automatische wöchentliche Mittagessen-Bestellung
=================================================================

Jedes Kind hat einen eigenen GFB-Catering-Account (kein gemeinsamer Account
mit Kind-Auswahl). Der komplette Ablauf läuft daher separat pro Kind, jeweils
in einer eigenen Playwright-Session:
  1. Login auf https://bestellung-gfb-catering.de/ mit den Zugangsdaten des Kindes
  2. Speiseplan der kommenden Woche auslesen (pro Wochentag verfügbare
     Gerichte), Tage in den Schulferien werden dabei standardmäßig
     übersprungen (siehe NUR_AUSSERHALB_SCHULFERIEN, schulferien.json)
  3. Harte Regeln anwenden (Ausschlüsse wie Fisch/Fleisch), danach das
     passende Gericht wählen – regelbasiert per Kategorie-Präferenz
     (bevorzugte_kategorien, z. B. "möglichst DGE"), oder falls für ein Kind
     keine gesetzt ist, per Claude-API anhand freier Vorlieben
  4. Auswahl für jeden Tag eintragen und Bestellung abschicken

WICHTIG: Der komplette Ablauf (login(), lese_menueplan(), bestelle_gericht(),
bestellung_abschliessen()) wurde per Playwright live gegen die echte Seite
geprüft und mit mehreren echten Testbestellungen end-to-end bestätigt,
inklusive der regelbasierten Fisch-/Fleisch-Ausschlüsse und Kategorie-
Präferenz (siehe Kind.bevorzugte_kategorien, waehle_gericht()).

Aufruf: python order_lunch.py
Benötigte Umgebungsvariablen (siehe .env.example):
  ANTHROPIC_API_KEY (optional – nur nötig, wenn mindestens ein Kind in der
  Konfiguration keine bevorzugte_kategorien gesetzt hat)
  NTFY_TOPIC (optional – nur gesetzt, wenn Push-Benachrichtigungen über
  ntfy.sh/einen selbstgehosteten ntfy-Server gewünscht sind, siehe NtfyConfig)
Zugangsdaten pro Kind stehen in der Kinder-Konfiguration, siehe
config.json.example (Feld KINDER_CONFIG, Default config.json).
"""

import os
import json
import logging
import random
import re
import time
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Optional

from playwright.sync_api import sync_playwright, Browser, BrowserContext, Page, TimeoutError as PWTimeout
import httpx

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("gfb-lunch-order")

BASE_URL = "https://bestellung-gfb-catering.de/#/home"
ANTHROPIC_API_URL = "https://api.anthropic.com/v1/messages"
ANTHROPIC_MODEL = "claude-sonnet-4-6"  # ggf. auf aktuelles Modell anpassen


def _env_bool(name: str, default: bool) -> bool:
    return os.environ.get(name, str(default)).strip().lower() in ("1", "true", "yes")


# Realistischer Desktop-Chrome-UA statt des von Playwright/Chromium selbst
# gemeldeten (kann je nach Chromium-Version verräterische Automations-Marker
# enthalten). Versionsnummer grob an die im Docker-Image gebündelte
# Chromium-Version angelehnt, muss nicht exakt stimmen.
STANDARD_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36"
)


@dataclass
class PlaywrightConfig:
    """Browser-/Kontext-Einstellungen für Playwright, per Umgebungsvariable steuerbar.

    PLAYWRIGHT_HEADLESS=false (Browser sichtbar) und PLAYWRIGHT_TRACE=true
    (playwright show-trace) helfen beim Debuggen, falls die Seite ihr Layout
    ändert und Selektoren angepasst werden müssen.
    """

    headless: bool = True
    slow_mo_ms: int = 0
    action_timeout_ms: int = 15000
    navigation_timeout_ms: int = 30000
    locale: str = "de-DE"
    timezone_id: str = "Europe/Berlin"
    viewport_width: int = 1280
    viewport_height: int = 900
    user_agent: str = STANDARD_USER_AGENT
    trace: bool = False
    data_dir: str = "/data"

    @classmethod
    def from_env(cls) -> "PlaywrightConfig":
        return cls(
            headless=_env_bool("PLAYWRIGHT_HEADLESS", True),
            slow_mo_ms=int(os.environ.get("PLAYWRIGHT_SLOWMO_MS", "0")),
            action_timeout_ms=int(os.environ.get("PLAYWRIGHT_ACTION_TIMEOUT_MS", "15000")),
            navigation_timeout_ms=int(os.environ.get("PLAYWRIGHT_NAVIGATION_TIMEOUT_MS", "30000")),
            locale=os.environ.get("PLAYWRIGHT_LOCALE", "de-DE"),
            timezone_id=os.environ.get("PLAYWRIGHT_TIMEZONE", "Europe/Berlin"),
            viewport_width=int(os.environ.get("PLAYWRIGHT_VIEWPORT_WIDTH", "1280")),
            viewport_height=int(os.environ.get("PLAYWRIGHT_VIEWPORT_HEIGHT", "900")),
            user_agent=os.environ.get("PLAYWRIGHT_USER_AGENT", STANDARD_USER_AGENT),
            trace=_env_bool("PLAYWRIGHT_TRACE", False),
            data_dir=os.environ.get("DATA_DIR", "/data"),
        )


def erstelle_browser_context(p, cfg: PlaywrightConfig) -> tuple[Browser, BrowserContext]:
    """Startet Chromium und einen Kontext gemäß PlaywrightConfig.

    Ein paar Standardmaßnahmen, damit die Sitzung wie ein normaler
    Browser-Nutzer aussieht statt wie offensichtliche Automatisierung
    (relevant für Bot-/Rate-Limit-Erkennung, nicht für reine Requestfrequenz –
    bei einem Lauf pro Tag und Kind ist die ohnehin sehr niedrig):
      - Fester, realistischer Desktop-Chrome-User-Agent (siehe
        STANDARD_USER_AGENT) statt des von Chromium selbst gemeldeten.
      - `navigator.webdriver` wird per Init-Script auf `undefined` gesetzt –
        das von WebDriver/CDP-gesteuerten Browsern gesetzte Flag ist einer
        der ersten Checks vieler Bot-Erkennungen.
    """
    browser = p.chromium.launch(headless=cfg.headless, slow_mo=cfg.slow_mo_ms)
    context = browser.new_context(
        locale=cfg.locale,
        timezone_id=cfg.timezone_id,
        viewport={"width": cfg.viewport_width, "height": cfg.viewport_height},
        user_agent=cfg.user_agent,
    )
    context.add_init_script(
        "Object.defineProperty(navigator, 'webdriver', { get: () => undefined });"
    )
    context.set_default_timeout(cfg.action_timeout_ms)
    context.set_default_navigation_timeout(cfg.navigation_timeout_ms)
    if cfg.trace:
        context.tracing.start(screenshots=True, snapshots=True, sources=True)
    return browser, context


@dataclass
class NtfyConfig:
    """Konfiguration für Push-Benachrichtigungen über ntfy.sh (oder einen
    selbstgehosteten ntfy-Server), siehe https://ntfy.sh.

    Ohne gesetztes `topic` sind Benachrichtigungen komplett deaktiviert
    (Default-Verhalten unverändert, siehe sende_ntfy_nachricht()).

    `status_topic` ist ein optionales zweites, separates Topic nur für die
    Lauf-Zusammenfassung am Ende von main() (immer genau eine Nachricht pro
    Skript-Ausführung, unabhängig davon, ob etwas bestellt wurde) – gedacht
    für Auswertung/Automationen (z. B. Home Assistant), damit "letzter Lauf"
    dort auch an Tagen ohne Bestelländerung sichtbar ist, ohne das normale
    `topic` (für Push-Benachrichtigungen aufs Handy) mit einer Nachricht pro
    Tag zu fluten, an dem ohnehin nichts passiert ist.
    """

    url: str = "https://ntfy.sh"
    topic: str = ""
    status_topic: str = ""
    token: str = ""

    @classmethod
    def from_env(cls) -> "NtfyConfig":
        return cls(
            url=os.environ.get("NTFY_URL", "https://ntfy.sh").rstrip("/"),
            topic=os.environ.get("NTFY_TOPIC", "").strip(),
            status_topic=os.environ.get("NTFY_STATUS_TOPIC", "").strip(),
            token=os.environ.get("NTFY_TOKEN", "").strip(),
        )


def sende_ntfy_nachricht(
    cfg: NtfyConfig,
    titel: str,
    nachricht: str,
    tags: Optional[list[str]] = None,
    topic: Optional[str] = None,
) -> None:
    """Schickt eine Push-Benachrichtigung per ntfy.sh-Publish-API (einfacher
    HTTP-POST an <url>/<topic>, siehe https://docs.ntfy.sh/publish/).

    Verwendet standardmäßig cfg.topic; `topic` überschreiben, um stattdessen
    z. B. an cfg.status_topic zu senden (siehe NtfyConfig). Ohne (effektives)
    Topic ein No-Op. Versand-Fehler (z. B. ntfy nicht erreichbar) dürfen den
    eigentlichen Bestell-Ablauf nicht zum Absturz bringen und werden daher
    nur geloggt.
    """
    ziel_topic = topic if topic is not None else cfg.topic
    if not ziel_topic:
        return

    headers = {"Title": titel.encode("utf-8")}
    if tags:
        headers["Tags"] = ",".join(tags)
    if cfg.token:
        headers["Authorization"] = f"Bearer {cfg.token}"

    try:
        resp = httpx.post(
            f"{cfg.url}/{ziel_topic}",
            content=nachricht.encode("utf-8"),
            headers=headers,
            timeout=10,
        )
        resp.raise_for_status()
    except Exception as e:
        log.warning("ntfy-Benachrichtigung konnte nicht gesendet werden: %s", e)


@dataclass
class Kind:
    name: str
    # Zugangsdaten des eigenen GFB-Catering-Accounts dieses Kindes.
    benutzername: str
    passwort: str
    # Harte Ausschlussregeln. "Fisch"/"Fleisch" (Groß-/Kleinschreibung egal)
    # werden über das Kost-Kennzeichen der Seite erkannt (siehe
    # _kost_kennzeichen()), alles andere per Textsuche in der Beschreibung
    # (z. B. ["Nüsse"]).
    ausschluesse: list[str] = field(default_factory=list)
    # Priorisierte Liste bevorzugter Kategorien, z. B. ["DGE", "Classic",
    # "BIO-Veggie"] für "möglichst gesund". Wird rein regelbasiert
    # ausgewertet (keine KI nötig): Es gewinnt die erste Kategorie aus dieser
    # Liste, die nach den Ausschlüssen noch verfügbar ist. Leer lassen, um
    # stattdessen per Claude-API anhand von `vorlieben` auswählen zu lassen.
    bevorzugte_kategorien: list[str] = field(default_factory=list)
    # Weiche Vorlieben, die der KI als Kontext mitgegeben werden, z. B. "mag
    # Nudelgerichte". Wird nur verwendet, wenn bevorzugte_kategorien leer ist.
    vorlieben: str = ""


def lade_kinder(config_pfad: str) -> list[Kind]:
    """Lädt die Kinder-Konfiguration aus einer JSON-Datei (siehe config.json.example).

    Jeder Eintrag braucht eigene Zugangsdaten (benutzername/passwort), da
    jedes Kind einen eigenen GFB-Catering-Account hat.
    """
    with open(config_pfad, encoding="utf-8") as f:
        rohdaten = json.load(f)
    return [
        Kind(
            name=eintrag["name"],
            benutzername=eintrag["benutzername"],
            passwort=eintrag["passwort"],
            ausschluesse=eintrag.get("ausschluesse", []),
            bevorzugte_kategorien=eintrag.get("bevorzugte_kategorien", []),
            vorlieben=eintrag.get("vorlieben", ""),
        )
        for eintrag in rohdaten
    ]


def lade_schulferien(config_pfad: str, bundesland: str) -> list[tuple[date, date]]:
    """Lädt die Schulferien-Zeiträume für ein Bundesland aus schulferien.json.

    Format (siehe schulferien.json): {"<Bundesland-Kürzel>": [{"name": ...,
    "von": "YYYY-MM-DD", "bis": "YYYY-MM-DD"}, ...]}. Einzelne bewegliche
    Ferientage (z. B. Brückentage) werden als Eintrag mit von == bis
    abgebildet und zählen genauso als Ferientag wie ein mehrtägiger
    Ferienblock.
    """
    with open(config_pfad, encoding="utf-8") as f:
        rohdaten = json.load(f)
    eintraege = rohdaten.get(bundesland)
    if not eintraege:
        log.warning(
            "Keine Schulferien-Daten für Bundesland '%s' in %s gefunden – "
            "es wird nicht auf Schulferien geprüft.", bundesland, config_pfad,
        )
        return []
    return [
        (date.fromisoformat(eintrag["von"]), date.fromisoformat(eintrag["bis"]))
        for eintrag in eintraege
    ]


def ist_schulferientag(datum: date, schulferien: list[tuple[date, date]]) -> bool:
    """Prüft, ob datum in einen der Schulferien-Zeiträume fällt (inklusive)."""
    return any(von <= datum <= bis for von, bis in schulferien)


def _parse_tag_datum(tag: str) -> date:
    """Parst das Datum aus dem Tag-Label von lese_menueplan(), z. B. "22.09.26 - Dienstag"."""
    datum_teil = tag.split(" - ", 1)[0]
    return datetime.strptime(datum_teil, "%d.%m.%y").date()


def login(page: Page, username: str, password: str) -> None:
    log.info("Öffne Startseite …")
    page.goto(BASE_URL, wait_until="networkidle")

    # Login ist eine eigene Route (#/login), kein Formular auf der Startseite.
    # Per Live-Inspektion bestätigt (Chromium/Playwright gegen die echte Seite):
    #   Benutzername: <input id="benutzername" formcontrolname="login">
    #   Passwort:     <input id="passwort" formcontrolname="password">
    #   Button:       <button>Anmelden</button>
    page.get_by_text("Anmelden", exact=False).first.click()
    page.wait_for_url("**/#/login")

    page.locator("#benutzername").fill(username)
    page.locator("#passwort").fill(password)
    page.get_by_role("button", name="Anmelden").click()

    page.wait_for_selector("text=Speiseplan", timeout=15000)
    log.info("Login erfolgreich.")

    # Nach dem Login erscheint manchmal ein Mitteilungen-Dialog (Angular CDK
    # Overlay), der die komplette Seite mit einem Backdrop blockiert und
    # jeden weiteren Klick (z. B. auf "Speiseplan") verhindert - live als
    # echter Fehlschlag beobachtet. Schließen, falls vorhanden.
    try:
        page.get_by_text("Mitteilungen schließen", exact=False).first.click(timeout=3000)
        log.info("Mitteilungen-Dialog geschlossen.")
    except PWTimeout:
        pass  # Kein Dialog vorhanden


def lese_menueplan(page: Page, schulferien: Optional[list[tuple[date, date]]] = None) -> dict:
    """
    Liest den Speiseplan aus und gibt nur noch änderbare Tage zurück (Tage,
    deren Bestellfrist bereits abgelaufen ist, werden übersprungen). Ist
    `schulferien` gesetzt (siehe lade_schulferien()), werden zusätzlich Tage
    übersprungen, die in die Schulferien fallen.

    Per Live-Inspektion bestätigte DOM-Struktur (Angular/Material-App):
      div.speiseplan-tagWbp                          – ein Tag
        speiseplantaglabel .speiseplanTagLabelNormal strong  – z. B. "07.09.26 - Montag"
        .speiseplanMenu (mehrere pro Tag, i. d. R. DGE/Classic/BIO-Veggie)
          .speiseplan-menu-titel strong (1.)          – Kategoriename, z. B. "DGE"
          #speiseplanMenuBeschreibung                 – Gerichtbeschreibung
          [data-testid="order-einzeln"]               – Bestell-Icon-Button
            Klasse "disabled"                         – Frist abgelaufen, nicht mehr änderbar
            mat-icon-Text "check"                     – aktuell für diesen Tag bestellt
            mat-icon-Text "add"                       – nicht bestellt, klickbar zum Bestellen
            mat-icon-Text "remove_shopping_cart"       – für vergangene/gesperrte Tage (informativ)

    Rückgabeformat (Schlüssel ist das volle Datum+Wochentag-Label, da ein
    Monat mehrere gleichnamige Wochentage enthält):
    {
        "07.09.26 - Montag": ["DGE: ...", "Classic: ...", "BIO-Veggie: ..."],
        ...
    }
    """
    page.get_by_text("Speiseplan", exact=False).first.click()
    page.wait_for_load_state("networkidle")
    # networkidle sagt nur, dass keine Requests mehr laufen - Angular braucht
    # danach noch etwas Zeit, um die Tag-Karten clientseitig zu rendern.
    # Ohne dieses Warten kann tage weiter unten leer sein (live beobachtet).
    page.locator(".speiseplan-tagWbp").first.wait_for(state="visible", timeout=15000)

    menueplan: dict[str, list[str]] = {}
    tage = page.locator(".speiseplan-tagWbp").all()
    for tag_el in tage:
        tag_name = tag_el.locator(".speiseplanTagLabelNormal strong").inner_text().strip()

        if schulferien and ist_schulferientag(_parse_tag_datum(tag_name), schulferien):
            log.info("Überspringe %s – Schulferien.", tag_name)
            continue

        menu_els = tag_el.locator(".speiseplanMenu").all()
        aenderbar = any(
            "disabled" not in (m.locator("[data-testid='order-einzeln']").get_attribute("class") or "")
            for m in menu_els
        )
        if not aenderbar:
            log.info("Überspringe %s – Bestellfrist bereits abgelaufen.", tag_name)
            continue

        gerichte = []
        for menu_el in menu_els:
            kategorie = menu_el.locator(".speiseplan-menu-titel strong").first.inner_text().strip()
            beschreibung = menu_el.locator("#speiseplanMenuBeschreibung").inner_text().strip()
            gerichte.append(f"{kategorie}: {beschreibung}")
        menueplan[tag_name] = gerichte

    log.info("Speiseplan gelesen (nur änderbare Tage): %s", menueplan)
    return menueplan


# Kost-Kennzeichen, das die Seite jedem Gericht mitgibt (letzte Klammer der
# Beschreibung, z. B. "... (F, Ia, IV, VII)"): K = vegetarisch, F = Fisch,
# G = Fleisch. Per Live-Inspektion bestätigt (u. a. sichtbar als Blatt-/
# Fisch-Icon neben dem Gericht). Zuverlässiger als Textsuche, da Gerichtnamen
# das Wort "Fisch"/"Fleisch" oft gar nicht enthalten (z. B. "Lachswürfel",
# "Hähnchenragout").
KOST_KENNZEICHEN_FUER_AUSSCHLUSS = {
    "fisch": "F",
    "fleisch": "G",
}


def _kost_kennzeichen(beschreibung: str) -> Optional[str]:
    """Extrahiert K/F/G aus der letzten Klammer der Gerichtbeschreibung."""
    treffer = re.findall(r"\(([A-Z])[,)]", beschreibung)
    return treffer[-1] if treffer else None


def waehle_gericht_regelbasiert(gerichte: list[str], kind: Kind) -> list[str]:
    """Filtert harte Ausschlüsse heraus. Gibt die verbleibenden Optionen zurück.

    "Fisch" und "Fleisch" in kind.ausschluesse werden über das Kost-
    Kennzeichen der Seite erkannt (siehe _kost_kennzeichen()), alle anderen
    Ausschlüsse per Textsuche in der Beschreibung.
    """
    kennzeichen_ausschluesse = {
        KOST_KENNZEICHEN_FUER_AUSSCHLUSS[a.lower()]
        for a in kind.ausschluesse
        if a.lower() in KOST_KENNZEICHEN_FUER_AUSSCHLUSS
    }
    text_ausschluesse = [a for a in kind.ausschluesse if a.lower() not in KOST_KENNZEICHEN_FUER_AUSSCHLUSS]

    gefiltert = [
        g for g in gerichte
        if _kost_kennzeichen(g) not in kennzeichen_ausschluesse
        and not any(ausschluss.lower() in g.lower() for ausschluss in text_ausschluesse)
    ]
    return gefiltert or gerichte  # Fallback: falls alles ausgeschlossen ist, alle anzeigen


def waehle_gericht_nach_kategorie(optionen: list[str], bevorzugte_kategorien: list[str]) -> Optional[str]:
    """Wählt rein regelbasiert die erste verfügbare bevorzugte Kategorie
    (z. B. ["DGE", "Classic", "BIO-Veggie"] für "möglichst gesund").

    Gibt None zurück, wenn keine der bevorzugten Kategorien unter den
    (bereits ausschlussgefilterten) Optionen vorhanden ist.
    """
    kategorie_zu_gericht = {g.split(":", 1)[0].strip(): g for g in optionen}
    for kategorie in bevorzugte_kategorien:
        if kategorie in kategorie_zu_gericht:
            return kategorie_zu_gericht[kategorie]
    return None


def waehle_gericht_per_ki(tag: str, optionen: list[str], kind: Kind, api_key: Optional[str]) -> str:
    """Lässt Claude aus den (bereits regelgefilterten) Optionen das passende Gericht wählen."""
    if len(optionen) == 1:
        return optionen[0]

    if not api_key:
        raise RuntimeError(
            f"Keine bevorzugte Kategorie für {kind.name} verfügbar und kein "
            "ANTHROPIC_API_KEY gesetzt – kann kein Gericht auswählen."
        )

    prompt = f"""Wähle für {kind.name} das passende Mittagessen für {tag} aus folgenden Optionen:
{json.dumps(optionen, ensure_ascii=False)}

Zusätzliche Vorlieben: {kind.vorlieben or "keine besonderen Vorlieben"}

Antworte NUR mit dem exakten Gerichtnamen aus der Liste, ohne weitere Erklärung."""

    resp = httpx.post(
        ANTHROPIC_API_URL,
        headers={
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
        json={
            "model": ANTHROPIC_MODEL,
            "max_tokens": 50,
            "messages": [{"role": "user", "content": prompt}],
        },
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json()
    text = "".join(block["text"] for block in data["content"] if block["type"] == "text").strip()

    # Absicherung: Antwort muss eine der Optionen sein, sonst Fallback auf erste Option
    if text not in optionen:
        log.warning("KI-Antwort '%s' nicht in Optionen, nehme erste Option als Fallback.", text)
        return optionen[0]
    return text


def waehle_gericht(tag: str, optionen: list[str], kind: Kind, api_key: Optional[str]) -> str:
    """Wählt aus den (bereits ausschlussgefilterten) Optionen ein Gericht.

    Ist kind.bevorzugte_kategorien gesetzt, entscheidet das rein regelbasiert
    (keine KI nötig). Nur wenn keine der bevorzugten Kategorien verfügbar ist
    – oder gar keine gesetzt sind –, wird auf die Claude-API zurückgegriffen.
    """
    if kind.bevorzugte_kategorien:
        gericht = waehle_gericht_nach_kategorie(optionen, kind.bevorzugte_kategorien)
        if gericht is not None:
            return gericht
        log.warning(
            "Keine der bevorzugten Kategorien %s für %s am %s verfügbar, weiche auf KI-Auswahl aus.",
            kind.bevorzugte_kategorien, kind.name, tag,
        )
    return waehle_gericht_per_ki(tag, optionen, kind, api_key)


def bestelle_gericht(page: Page, tag: str, kind: Kind, gericht: str) -> bool:
    """Wählt für einen Tag das per KI/Regeln bestimmte Gericht aus.

    Gibt zurück, ob dabei tatsächlich eine neue Bestellung ausgelöst wurde
    (False, falls bereits etwas bestellt war, die Frist abgelaufen ist oder
    das Gericht nicht gefunden wurde) – wird für die ntfy-Erfolgsmeldung in
    bestelle_fuer_kind() gebraucht, damit dort nur tatsächlich geänderte
    Tage aufgelistet werden.

    `page` ist bereits im eigenen Account von `kind` eingeloggt (jedes Kind
    hat einen eigenen GFB-Catering-Account, kein Umschalten innerhalb einer
    Session nötig). `tag` ist das volle Label aus lese_menueplan() (z. B.
    "07.09.26 - Montag"), `gericht` das kombinierte "Kategorie: Beschreibung"
    aus derselben Funktion.

    Wichtig, da das Skript wiederholt laufen soll (z. B. mehrmals pro Woche,
    um Bestellfristen sicher vor Ablauf zu erwischen): Ist für diesen Tag
    bereits IRGENDEINE Option bestellt (mat-icon-Text "check") – egal ob aus
    einem früheren automatischen Lauf oder manuell in der App geändert –,
    wird nichts angefasst, auch wenn eine andere Kategorie als `gericht`
    ausgewählt ist. Ein erneuter Lauf darf eine bestehende Auswahl nicht
    zurückdrehen.
    """
    tag_el = page.locator(".speiseplan-tagWbp").filter(has_text=tag)
    menu_els = tag_el.locator(".speiseplanMenu").all()

    for menu_el in menu_els:
        bestell_control = menu_el.locator("[data-testid='order-einzeln']")
        if bestell_control.locator("mat-icon").inner_text().strip() == "check":
            kategorie = menu_el.locator(".speiseplan-menu-titel strong").first.inner_text().strip()
            log.info("Für %s am %s bereits eine Bestellung vorhanden (%s) – unverändert gelassen.", kind.name, tag, kategorie)
            return False

    log.info("Bestelle für %s am %s: %s", kind.name, tag, gericht)
    for menu_el in menu_els:
        kategorie = menu_el.locator(".speiseplan-menu-titel strong").first.inner_text().strip()
        beschreibung = menu_el.locator("#speiseplanMenuBeschreibung").inner_text().strip()
        if f"{kategorie}: {beschreibung}" != gericht:
            continue

        bestell_control = menu_el.locator("[data-testid='order-einzeln']")
        ist_gesperrt = "disabled" in (bestell_control.get_attribute("class") or "")
        if ist_gesperrt:
            log.warning("Bestellfrist für %s (%s) bereits abgelaufen – überspringe.", tag, gericht)
            return False

        bestell_control.click()
        return True

    log.warning("Gericht '%s' am %s nicht im Speiseplan gefunden.", gericht, tag)
    return False


def bestellung_abschliessen(page: Page) -> None:
    """Bestätigt die im Warenkorb gesammelten Bestelländerungen – irreversibler Schritt!

    Per Live-Inspektion mit einer echten Testbestellung vollständig bestätigt.
    Wichtig: Der Warenkorb wird nur clientseitig in der laufenden Browser-
    Sitzung gehalten – Login, Gerichtsauswahl und Bestätigung müssen daher
    in EINER durchgehenden Playwright-Session laufen (wie in main() der
    Fall), sonst ist der Warenkorb beim Aufruf von bestellung_abschliessen()
    wieder leer.

    Ablauf: Warenkorb (#/warenkorb) öffnen, optionalen Hinweisdialog
    ("Bestätigung der Bestelländerung erforderlich" mit "OK"-Button –
    erscheint nicht bei jedem Aufruf) schließen, dann auf
    "Zum genannten Preis bestätigen" klicken. Bestätigter Erfolgstext:
    "Vielen Dank. Die Bestellung für den angegebenen Zeitraum wurde
    erfolgreich im System hinterlegt."

    Wichtig, da das Skript wiederholt laufen soll: Ist der Warenkorb leer
    (keine Bestelländerung vorgenommen, z. B. weil an allen Tagen bereits
    bestellt war), ist der Bestätigen-Button per <button disabled> deaktiviert
    – live bestätigt. Playwright würde beim Klick auf ein deaktiviertes
    Element bis zum Timeout warten, daher hier vorab prüfen und überspringen.
    """
    page.get_by_text("Warenkorb", exact=False).first.click()
    page.wait_for_load_state("networkidle")

    try:
        page.get_by_role("button", name="OK").click(timeout=3000)
    except PWTimeout:
        pass  # Hinweisdialog erscheint nicht immer

    bestaetigen_button = page.get_by_role("button", name="Zum genannten Preis bestätigen")
    if bestaetigen_button.is_disabled():
        log.info("Keine offenen Bestelländerungen im Warenkorb – nichts zu bestätigen.")
        return

    bestaetigen_button.click()
    page.wait_for_selector("text=Vielen Dank", timeout=15000)
    log.info("Bestellung abgeschlossen.")


def bestelle_fuer_kind(
    p,
    pw_cfg: PlaywrightConfig,
    kind: Kind,
    api_key: Optional[str],
    dry_run: bool,
    schulferien: Optional[list[tuple[date, date]]] = None,
    ntfy_cfg: Optional[NtfyConfig] = None,
) -> list[tuple[str, str]]:
    """Führt den kompletten Ablauf (Login, Auswahl, Bestätigung) für ein Kind
    in einer eigenen Browser-Session gegen dessen eigenen Account aus.

    Ist ntfy_cfg gesetzt (siehe NtfyConfig), wird bei Erfolg eine Übersicht
    der neu bestellten Tage per ntfy verschickt (nur für Tage, an denen
    tatsächlich eine neue Bestellung ausgelöst wurde, siehe bestelle_gericht()).
    Fehlschläge werden von main() gemeldet, da dort über alle Kinder hinweg
    entschieden wird, ob zusätzlich eine Sammel-Nachricht sinnvoll ist.

    Gibt die Liste der neu bestellten (Tag, Gericht)-Paare zurück – main()
    braucht das zusätzlich für die Lauf-Zusammenfassung auf NTFY_STATUS_TOPIC.
    """
    browser, context = erstelle_browser_context(p, pw_cfg)
    page = context.new_page()
    try:
        login(page, kind.benutzername, kind.passwort)
        menueplan = lese_menueplan(page, schulferien)

        neue_bestellungen: list[tuple[str, str]] = []
        for tag, gerichte in menueplan.items():
            optionen = waehle_gericht_regelbasiert(gerichte, kind)
            gericht = waehle_gericht(tag, optionen, kind, api_key)
            if dry_run:
                log.info("[DRY RUN] Würde bestellen: %s / %s -> %s", tag, kind.name, gericht)
                neue_bestellungen.append((tag, gericht))
            elif bestelle_gericht(page, tag, kind, gericht):
                neue_bestellungen.append((tag, gericht))

        if not dry_run:
            bestellung_abschliessen(page)
        else:
            log.info("DRY_RUN=true – für %s keine tatsächliche Bestellung ausgelöst.", kind.name)

        if ntfy_cfg and neue_bestellungen:
            uebersicht = "\n".join(f"{tag}: {gericht}" for tag, gericht in neue_bestellungen)
            if dry_run:
                titel = f"[DRY RUN] Bestellt: {kind.name}"
                uebersicht = (
                    "DRY_RUN=true – es wurde NICHTS tatsächlich bestellt, "
                    "nur eine Vorschau:\n\n" + uebersicht
                )
            else:
                titel = f"Bestellt: {kind.name}"
            sende_ntfy_nachricht(ntfy_cfg, titel, uebersicht, tags=["white_check_mark"])

        return neue_bestellungen

    except PWTimeout as e:
        log.error("Timeout beim Warten auf ein Element für %s – vermutlich falscher Selektor: %s", kind.name, e)
        page.screenshot(path=os.path.join(pw_cfg.data_dir, f"error_screenshot_{kind.name}.png"))
        raise
    finally:
        if pw_cfg.trace:
            context.tracing.stop(path=os.path.join(pw_cfg.data_dir, f"trace_{kind.name}.zip"))
        browser.close()


def main() -> None:
    # Zufällige Startverzögerung, damit der tägliche Cronjob nicht jeden Tag
    # exakt zur selben Sekunde bei der Seite aufschlägt (ein klares
    # Automatisierungsmerkmal). 0 = deaktiviert.
    jitter_max_s = int(os.environ.get("STARTUP_JITTER_MAX_SECONDS", "900"))
    if jitter_max_s > 0:
        verzoegerung = random.uniform(0, jitter_max_s)
        log.info("Warte %.0f s (zufällige Startverzögerung) …", verzoegerung)
        time.sleep(verzoegerung)

    # Nur nötig als Fallback für Kinder ohne bevorzugte_kategorien (siehe
    # Kind/waehle_gericht) – wer für alle Kinder eine Kategorie-Präferenz
    # setzt, braucht keinen ANTHROPIC_API_KEY.
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    dry_run = os.environ.get("DRY_RUN", "true").lower() == "true"
    config_pfad = os.environ.get("KINDER_CONFIG", "config.json")
    kinder = lade_kinder(config_pfad)
    pw_cfg = PlaywrightConfig.from_env()
    ntfy_cfg = NtfyConfig.from_env()
    os.makedirs(pw_cfg.data_dir, exist_ok=True)

    # Standardmäßig wird in den Schulferien nicht bestellt (alle Kinder gehen
    # von der gleichen Annahme aus: gleiches Bundesland für alle Kinder).
    nur_ausserhalb_schulferien = _env_bool("NUR_AUSSERHALB_SCHULFERIEN", True)
    schulferien: Optional[list[tuple[date, date]]] = None
    if nur_ausserhalb_schulferien:
        bundesland = os.environ.get("BUNDESLAND", "BB")
        schulferien_pfad = os.environ.get("SCHULFERIEN_CONFIG", "schulferien.json")
        schulferien = lade_schulferien(schulferien_pfad, bundesland)

    # Jedes Kind hat einen eigenen Account -> eigener Login/eigene Session pro
    # Kind. Ein Fehler bei einem Account soll den Lauf für die anderen Kinder
    # nicht verhindern; am Ende wird trotzdem ein Fehler gemeldet (wichtig für
    # Cron-Benachrichtigungen).
    fehlgeschlagen: list[str] = []
    anzahl_neue_bestellungen: dict[str, int] = {}
    with sync_playwright() as p:
        for kind in kinder:
            try:
                neue_bestellungen = bestelle_fuer_kind(p, pw_cfg, kind, api_key, dry_run, schulferien, ntfy_cfg)
                anzahl_neue_bestellungen[kind.name] = len(neue_bestellungen)
            except Exception as e:
                log.exception("Ablauf für %s fehlgeschlagen.", kind.name)
                fehlgeschlagen.append(kind.name)
                sende_ntfy_nachricht(
                    ntfy_cfg,
                    f"Fehlgeschlagen: {kind.name}",
                    f"Ablauf für {kind.name} fehlgeschlagen: {e}",
                    tags=["x"],
                )

    # Lauf-Zusammenfassung auf NTFY_STATUS_TOPIC (falls konfiguriert): anders
    # als die Nachrichten oben auf NTFY_TOPIC wird diese bei JEDEM Lauf genau
    # einmal verschickt, unabhängig davon, ob etwas neu bestellt wurde – z. B.
    # für eine Home-Assistant-Automation, die den Zeitpunkt des letzten Laufs
    # unabhängig vom Bestellergebnis auswerten will.
    if ntfy_cfg.status_topic:
        zeilen = [f"{name}: {n} neu bestellt" for name, n in anzahl_neue_bestellungen.items()]
        zeilen += [f"{name}: fehlgeschlagen" for name in fehlgeschlagen]
        status_text = "\n".join(zeilen) if zeilen else "Keine Kinder konfiguriert."
        if dry_run:
            status_text = "[DRY RUN]\n" + status_text
        if fehlgeschlagen:
            tag = "x"
        elif any(anzahl_neue_bestellungen.values()):
            tag = "white_check_mark"
        else:
            tag = "information_source"
        sende_ntfy_nachricht(
            ntfy_cfg, "GFB Lunch Order: Lauf abgeschlossen", status_text,
            tags=[tag], topic=ntfy_cfg.status_topic,
        )

    if fehlgeschlagen:
        if len(kinder) > 1:
            sende_ntfy_nachricht(
                ntfy_cfg,
                "Bestellung: Fehler im Gesamtlauf",
                f"Fehlgeschlagen für {len(fehlgeschlagen)} von {len(kinder)} Kindern: {', '.join(fehlgeschlagen)}",
                tags=["warning"],
            )
        raise RuntimeError(f"Fehlgeschlagen für: {', '.join(fehlgeschlagen)}")


if __name__ == "__main__":
    main()
