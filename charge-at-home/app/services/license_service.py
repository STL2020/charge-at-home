"""Ausgabe-abhaengige Einschraenkungen.

Seit v11.79 entscheidet das ausgelieferte Paket ueber den Funktionsumfang,
nicht mehr ein Lizenzschluessel:

  Demo         frei verteilbar, 5 Ladevorgaenge und 3 Fahrten je Monat,
               Belege mit Wasserzeichen
  Vollversion  personalisiertes Paket, unbegrenzt, Belege ohne Wasserzeichen
               und mit dem Namen des Kaeufers in der Fusszeile

WARUM KEIN LIZENZSCHLUESSEL MEHR
--------------------------------
Eine Pruefung in der Anwendung braucht Zugangsdaten zum Lizenzdienst — und
die sind in Software, die beim Kunden laeuft, immer auslesbar. Wer sie
findet, kann fremde Lizenzen manipulieren. Dazu kaeme laufender Aufwand:
Schluessel ausstellen, zurueckziehen, Rueckfragen beantworten, wenn der
Dienst gerade nicht erreichbar ist.

Das Zwei-Pakete-Modell vermeidet all das. Es laesst sich technisch umgehen,
indem jemand die Vollversion weitergibt — dagegen steht die Personalisierung:
Der Name des Kaeufers erscheint auf jedem erzeugten Beleg.

Dieses Modul bleibt als schmale Fassade bestehen, damit bestehende Aufrufe
weiterlaufen. Die Logik liegt in edition_service.
"""
from __future__ import annotations

from services import edition_service

# Rueckwaertskompatible Konstanten
DEMO_SESSION_LIMIT = 20
DEMO_WATERMARK_TEXT = edition_service.DEMO_WASSERZEICHEN
FREE_SESSIONS_PRO_MONAT = edition_service.DEMO_SESSIONS_PRO_MONAT
FREE_FAHRTEN_PRO_MONAT = edition_service.DEMO_FAHRTEN_PRO_MONAT


def is_demo(license_status: str = "") -> bool:
    """True, wenn Einschraenkungen gelten."""
    return not edition_service.ist_vollversion()


def watermark_for(license_status: str = "") -> str | None:
    """Text fuer das PDF-Wasserzeichen, oder None bei der Vollversion."""
    return edition_service.wasserzeichen()


def monats_limit_erreicht(art: str, anzahl_im_monat: int,
                          license_status: str = "") -> bool:
    """Prueft die Demo-Grenze fuer den laufenden Monat."""
    return edition_service.monats_limit_erreicht(art, anzahl_im_monat)


def session_limit_reached(current_session_count: int,
                          license_status: str = "") -> bool:
    """Beibehalten fuer aeltere Aufrufe (Gesamtzahl statt Monatsgrenze)."""
    if edition_service.ist_vollversion():
        return False
    return current_session_count >= DEMO_SESSION_LIMIT


def limit_info(license_status: str = "", sessions_im_monat: int = 0,
               fahrten_im_monat: int = 0) -> dict:
    """Uebersicht fuer die Oberflaeche."""
    info = edition_service.limit_info(sessions_im_monat, fahrten_im_monat)
    info["pro"] = info["voll"]          # aeltere Feldbezeichnung
    return info


def activate_license(key: str = "") -> bool:
    """Entfaellt — der Funktionsumfang haengt am ausgelieferten Paket."""
    return False


def aktiviere_lizenz(key: str = "") -> dict:
    return {"ok": False,
            "fehler": ("Diese Fassung nutzt keine Lizenzschlüssel. "
                       "Die Vollversion wird als eigenes Paket ausgeliefert.")}


def pruefe_lizenz_periodisch() -> dict:
    e = edition_service.edition()
    return {"status": "voll" if e["voll"] else "demo",
            "bezeichnung": e["bezeichnung"], "kaeufer": e["kaeufer"]}
