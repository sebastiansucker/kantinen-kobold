"""
GFB Catering – automatische wöchentliche Mittagessen-Bestellung
=================================================================

Ablauf:
  1. Login auf https://bestellung-gfb-catering.de/
  2. Menüplan der kommenden Woche auslesen (pro Wochentag verfügbare Gerichte)
  3. Pro Kind: harte Regeln anwenden (Ausschlüsse), danach per Claude-API
     das passende Gericht aus den verbleibenden Optionen wählen lassen
  4. Auswahl für jeden Tag/jedes Kind eintragen und Bestellung abschicken

WICHTIG: Alle mit "TODO" markierten Selektoren/URLs sind Platzhalter.
Sie müssen anhand der echten Seite (z. B. über die Chrome-Entwicklertools,
Rechtsklick -> Untersuchen) geprüft und angepasst werden, da ich die Seite
noch nicht live einsehen konnte.

Aufruf: python order_lunch.py
Benötigte Umgebungsvariablen (siehe .env.example):
  GFB_USERNAME, GFB_PASSWORD, ANTHROPIC_API_KEY
"""

import os
import json
import logging
from dataclasses import dataclass, field
from typing import Optional

from playwright.sync_api import sync_playwright, Page, TimeoutError as PWTimeout
import httpx

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("gfb-lunch-order")

BASE_URL = "https://bestellung-gfb-catering.de/#/home"
ANTHROPIC_API_URL = "https://api.anthropic.com/v1/messages"
ANTHROPIC_MODEL = "claude-sonnet-4-6"  # ggf. auf aktuelles Modell anpassen


@dataclass
class Kind:
    name: str
    # Harte Ausschlussregeln, z. B. ["Fisch", "Schwein"]
    ausschluesse: list[str] = field(default_factory=list)
    # Weiche Vorlieben, die der KI als Kontext mitgegeben werden, z. B. "mag Nudelgerichte"
    vorlieben: str = ""


def lade_kinder(config_pfad: str) -> list[Kind]:
    """Lädt die Kinder-Konfiguration aus einer JSON-Datei (siehe config.json.example)."""
    with open(config_pfad, encoding="utf-8") as f:
        rohdaten = json.load(f)
    return [
        Kind(
            name=eintrag["name"],
            ausschluesse=eintrag.get("ausschluesse", []),
            vorlieben=eintrag.get("vorlieben", ""),
        )
        for eintrag in rohdaten
    ]


def login(page: Page, username: str, password: str) -> None:
    log.info("Öffne Startseite …")
    page.goto(BASE_URL, wait_until="networkidle")

    # TODO: Selektoren für Login-Formular prüfen. Übliche Kandidaten:
    # page.get_by_label("Benutzername") / page.get_by_placeholder("E-Mail")
    page.get_by_label("Benutzername").fill(username)  # TODO prüfen
    page.get_by_label("Passwort").fill(password)  # TODO prüfen
    page.get_by_role("button", name="Anmelden").click()  # TODO prüfen

    # Warten bis Login abgeschlossen ist (z. B. Dashboard-Element sichtbar)
    page.wait_for_selector("text=Menüplan", timeout=15000)  # TODO prüfen
    log.info("Login erfolgreich.")


def lese_menueplan(page: Page) -> dict:
    """
    Liest den Menüplan der aktuellen/kommenden Woche aus.

    Erwartetes Rückgabeformat:
    {
        "Montag": ["Gericht A (vegetarisch)", "Gericht B (Fisch)", ...],
        "Dienstag": [...],
        ...
    }
    """
    # TODO: Navigation zum Menüplan
    page.get_by_role("link", name="Menüplan").click()  # TODO prüfen
    page.wait_for_load_state("networkidle")

    # TODO: Diese Extraktion ist ein Platzhalter. Je nach DOM-Struktur
    # müssen hier die echten Selektoren für Tage/Gerichte rein, z. B.:
    # tage = page.locator(".menu-day").all()
    menueplan: dict[str, list[str]] = {}
    tage = page.locator(".menu-day").all()  # TODO prüfen
    for tag_el in tage:
        tag_name = tag_el.locator(".day-label").inner_text().strip()  # TODO prüfen
        gerichte = [g.inner_text().strip() for g in tag_el.locator(".dish-name").all()]  # TODO prüfen
        menueplan[tag_name] = gerichte

    log.info("Menüplan gelesen: %s", menueplan)
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
    """Trägt die Auswahl für einen Tag/ein Kind ein. Muss an echte UI angepasst werden."""
    # TODO: Navigation/Auswahl-Logik für die konkrete Bestell-UI.
    # Beispielhafter Ablauf (anzupassen):
    # page.get_by_text(kind.name).click()
    # page.locator(f".menu-day:has-text('{tag}')").get_by_text(gericht).click()
    log.info("Bestelle für %s am %s: %s", kind.name, tag, gericht)
    page.get_by_text(kind.name).click()  # TODO prüfen
    page.locator(f".menu-day:has-text('{tag}')").get_by_text(gericht, exact=False).click()  # TODO prüfen


def bestellung_abschliessen(page: Page) -> None:
    # TODO: finalen Bestätigungsbutton finden – Vorsicht, das ist der irreversible Schritt!
    page.get_by_role("button", name="Bestellung abschließen").click()  # TODO prüfen
    page.wait_for_selector("text=Bestellung erfolgreich", timeout=15000)  # TODO prüfen
    log.info("Bestellung abgeschlossen.")


def main() -> None:
    username = os.environ["GFB_USERNAME"]
    password = os.environ["GFB_PASSWORD"]
    api_key = os.environ["ANTHROPIC_API_KEY"]
    dry_run = os.environ.get("DRY_RUN", "true").lower() == "true"
    config_pfad = os.environ.get("KINDER_CONFIG", "config.json")
    kinder = lade_kinder(config_pfad)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        try:
            login(page, username, password)
            menueplan = lese_menueplan(page)

            for tag, gerichte in menueplan.items():
                for kind in kinder:
                    optionen = waehle_gericht_regelbasiert(gerichte, kind)
                    gericht = waehle_gericht_per_ki(tag, optionen, kind, api_key)
                    if dry_run:
                        log.info("[DRY RUN] Würde bestellen: %s / %s -> %s", tag, kind.name, gericht)
                    else:
                        bestelle_gericht(page, tag, kind, gericht)

            if not dry_run:
                bestellung_abschliessen(page)
            else:
                log.info("DRY_RUN=true – keine tatsächliche Bestellung ausgelöst.")

        except PWTimeout as e:
            log.error("Timeout beim Warten auf ein Element – vermutlich falscher Selektor: %s", e)
            page.screenshot(path="/data/error_screenshot.png")
            raise
        finally:
            browser.close()


if __name__ == "__main__":
    main()
