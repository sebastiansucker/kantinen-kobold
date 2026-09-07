"""
GFB Catering – automatische wöchentliche Mittagessen-Bestellung
=================================================================

Jedes Kind hat einen eigenen GFB-Catering-Account (kein gemeinsamer Account
mit Kind-Auswahl). Der komplette Ablauf läuft daher separat pro Kind, jeweils
in einer eigenen Playwright-Session:
  1. Login auf https://bestellung-gfb-catering.de/ mit den Zugangsdaten des Kindes
  2. Speiseplan der kommenden Woche auslesen (pro Wochentag verfügbare Gerichte)
  3. Harte Regeln anwenden (Ausschlüsse), danach per Claude-API das passende
     Gericht aus den verbleibenden Optionen wählen lassen
  4. Auswahl für jeden Tag eintragen und Bestellung abschicken

WICHTIG: Der komplette Ablauf (login(), lese_menueplan(), bestelle_gericht(),
bestellung_abschliessen()) wurde per Playwright live gegen die echte Seite
geprüft und mit einer echten Testbestellung (ein Gericht, ein Tag) end-to-end
bestätigt.

Aufruf: python order_lunch.py
Benötigte Umgebungsvariablen (siehe .env.example):
  ANTHROPIC_API_KEY
Zugangsdaten pro Kind stehen in der Kinder-Konfiguration, siehe
config.json.example (Feld KINDER_CONFIG, Default config.json).
"""

import os
import json
import logging
from dataclasses import dataclass, field
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


@dataclass
class PlaywrightConfig:
    """Browser-/Kontext-Einstellungen für Playwright, per Umgebungsvariable steuerbar.

    Solange die TODO-Selektoren in diesem Skript noch nicht gegen die echte
    Seite geprüft sind, hilft vor allem PLAYWRIGHT_HEADLESS=false (Browser
    sichtbar) und PLAYWRIGHT_TRACE=true (playwright show-trace zum Debuggen).
    """

    headless: bool = True
    slow_mo_ms: int = 0
    action_timeout_ms: int = 15000
    navigation_timeout_ms: int = 30000
    locale: str = "de-DE"
    timezone_id: str = "Europe/Berlin"
    viewport_width: int = 1280
    viewport_height: int = 900
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
            trace=_env_bool("PLAYWRIGHT_TRACE", False),
            data_dir=os.environ.get("DATA_DIR", "/data"),
        )


def erstelle_browser_context(p, cfg: PlaywrightConfig) -> tuple[Browser, BrowserContext]:
    """Startet Chromium und einen Kontext gemäß PlaywrightConfig."""
    browser = p.chromium.launch(headless=cfg.headless, slow_mo=cfg.slow_mo_ms)
    context = browser.new_context(
        locale=cfg.locale,
        timezone_id=cfg.timezone_id,
        viewport={"width": cfg.viewport_width, "height": cfg.viewport_height},
    )
    context.set_default_timeout(cfg.action_timeout_ms)
    context.set_default_navigation_timeout(cfg.navigation_timeout_ms)
    if cfg.trace:
        context.tracing.start(screenshots=True, snapshots=True, sources=True)
    return browser, context


@dataclass
class Kind:
    name: str
    # Zugangsdaten des eigenen GFB-Catering-Accounts dieses Kindes.
    benutzername: str
    passwort: str
    # Harte Ausschlussregeln, z. B. ["Fisch", "Schwein"]
    ausschluesse: list[str] = field(default_factory=list)
    # Weiche Vorlieben, die der KI als Kontext mitgegeben werden, z. B. "mag Nudelgerichte"
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
            vorlieben=eintrag.get("vorlieben", ""),
        )
        for eintrag in rohdaten
    ]


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

    # TODO prüfen: Ohne echte Zugangsdaten konnte der Zustand nach dem Login
    # nicht live eingesehen werden. Die App nennt den Speiseplan durchgehend
    # "Speiseplan" (nicht "Menüplan" wie ursprünglich vermutet) – das
    # folgende Warten muss anhand des echten Post-Login-Screens geprüft werden.
    page.wait_for_selector("text=Speiseplan", timeout=15000)  # TODO prüfen
    log.info("Login erfolgreich.")


def lese_menueplan(page: Page) -> dict:
    """
    Liest den Speiseplan aus und gibt nur noch änderbare Tage zurück (Tage,
    deren Bestellfrist bereits abgelaufen ist, werden übersprungen).

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

    menueplan: dict[str, list[str]] = {}
    tage = page.locator(".speiseplan-tagWbp").all()
    for tag_el in tage:
        tag_name = tag_el.locator(".speiseplanTagLabelNormal strong").inner_text().strip()

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


def waehle_gericht_regelbasiert(gerichte: list[str], kind: Kind) -> list[str]:
    """Filtert harte Ausschlüsse heraus. Gibt die verbleibenden Optionen zurück."""
    gefiltert = [
        g for g in gerichte
        if not any(ausschluss.lower() in g.lower() for ausschluss in kind.ausschluesse)
    ]
    return gefiltert or gerichte  # Fallback: falls alles ausgeschlossen ist, alle anzeigen


def waehle_gericht_per_ki(tag: str, optionen: list[str], kind: Kind, api_key: str) -> str:
    """Lässt Claude aus den (bereits regelgefilterten) Optionen das passende Gericht wählen."""
    if len(optionen) == 1:
        return optionen[0]

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


def bestelle_gericht(page: Page, tag: str, kind: Kind, gericht: str) -> None:
    """Wählt für einen Tag das per KI/Regeln bestimmte Gericht aus.

    `page` ist bereits im eigenen Account von `kind` eingeloggt (jedes Kind
    hat einen eigenen GFB-Catering-Account, kein Umschalten innerhalb einer
    Session nötig). `tag` ist das volle Label aus lese_menueplan() (z. B.
    "07.09.26 - Montag"), `gericht` das kombinierte "Kategorie: Beschreibung"
    aus derselben Funktion.
    """
    log.info("Bestelle für %s am %s: %s", kind.name, tag, gericht)

    tag_el = page.locator(".speiseplan-tagWbp").filter(has_text=tag)
    for menu_el in tag_el.locator(".speiseplanMenu").all():
        kategorie = menu_el.locator(".speiseplan-menu-titel strong").first.inner_text().strip()
        beschreibung = menu_el.locator("#speiseplanMenuBeschreibung").inner_text().strip()
        if f"{kategorie}: {beschreibung}" != gericht:
            continue

        bestell_control = menu_el.locator("[data-testid='order-einzeln']")
        status = bestell_control.locator("mat-icon").inner_text().strip()
        ist_gesperrt = "disabled" in (bestell_control.get_attribute("class") or "")

        if status == "check":
            log.info("Für %s am %s bereits bestellt: %s", kind.name, tag, gericht)
            return
        if ist_gesperrt:
            log.warning("Bestellfrist für %s (%s) bereits abgelaufen – überspringe.", tag, gericht)
            return

        bestell_control.click()
        return

    log.warning("Gericht '%s' am %s nicht im Speiseplan gefunden.", gericht, tag)


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
    """
    page.get_by_text("Warenkorb", exact=False).first.click()
    page.wait_for_load_state("networkidle")

    try:
        page.get_by_role("button", name="OK").click(timeout=3000)
    except PWTimeout:
        pass  # Hinweisdialog erscheint nicht immer

    page.get_by_role("button", name="Zum genannten Preis bestätigen").click()
    page.wait_for_selector("text=Vielen Dank", timeout=15000)
    log.info("Bestellung abgeschlossen.")


def bestelle_fuer_kind(p, pw_cfg: PlaywrightConfig, kind: Kind, api_key: str, dry_run: bool) -> None:
    """Führt den kompletten Ablauf (Login, Auswahl, Bestätigung) für ein Kind
    in einer eigenen Browser-Session gegen dessen eigenen Account aus."""
    browser, context = erstelle_browser_context(p, pw_cfg)
    page = context.new_page()
    try:
        login(page, kind.benutzername, kind.passwort)
        menueplan = lese_menueplan(page)

        for tag, gerichte in menueplan.items():
            optionen = waehle_gericht_regelbasiert(gerichte, kind)
            gericht = waehle_gericht_per_ki(tag, optionen, kind, api_key)
            if dry_run:
                log.info("[DRY RUN] Würde bestellen: %s / %s -> %s", tag, kind.name, gericht)
            else:
                bestelle_gericht(page, tag, kind, gericht)

        if not dry_run:
            bestellung_abschliessen(page)
        else:
            log.info("DRY_RUN=true – für %s keine tatsächliche Bestellung ausgelöst.", kind.name)

    except PWTimeout as e:
        log.error("Timeout beim Warten auf ein Element für %s – vermutlich falscher Selektor: %s", kind.name, e)
        page.screenshot(path=os.path.join(pw_cfg.data_dir, f"error_screenshot_{kind.name}.png"))
        raise
    finally:
        if pw_cfg.trace:
            context.tracing.stop(path=os.path.join(pw_cfg.data_dir, f"trace_{kind.name}.zip"))
        browser.close()


def main() -> None:
    api_key = os.environ["ANTHROPIC_API_KEY"]
    dry_run = os.environ.get("DRY_RUN", "true").lower() == "true"
    config_pfad = os.environ.get("KINDER_CONFIG", "config.json")
    kinder = lade_kinder(config_pfad)
    pw_cfg = PlaywrightConfig.from_env()
    os.makedirs(pw_cfg.data_dir, exist_ok=True)

    # Jedes Kind hat einen eigenen Account -> eigener Login/eigene Session pro
    # Kind. Ein Fehler bei einem Account soll den Lauf für die anderen Kinder
    # nicht verhindern; am Ende wird trotzdem ein Fehler gemeldet (wichtig für
    # Cron-Benachrichtigungen).
    fehlgeschlagen: list[str] = []
    with sync_playwright() as p:
        for kind in kinder:
            try:
                bestelle_fuer_kind(p, pw_cfg, kind, api_key, dry_run)
            except Exception:
                log.exception("Ablauf für %s fehlgeschlagen.", kind.name)
                fehlgeschlagen.append(kind.name)

    if fehlgeschlagen:
        raise RuntimeError(f"Fehlgeschlagen für: {', '.join(fehlgeschlagen)}")


if __name__ == "__main__":
    main()
