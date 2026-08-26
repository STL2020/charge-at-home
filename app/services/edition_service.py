"""Unterscheidet Demo- und Vollversion.

WARUM ZWEI PAKETE STATT LIZENZPRUEFUNG
--------------------------------------
Ein Lizenzschluessel, den die Anwendung selbst prueft, braucht Zugangsdaten
zum Lizenzdienst — und die sind in einer Anwendung, die beim Kunden laeuft,
immer auslesbar. Wer sie findet, kann fremde Lizenzen manipulieren.

Deshalb: Zwei getrennte Pakete. Die Demo kann frei verteilt werden, die
Vollversion erhaelt nur, wer bezahlt hat. Keine Zugangsdaten im Umlauf,
keine Netzwerkabhaengigkeit, kein Ausfall bei Serverproblemen.

DER OFFENSICHTLICHE EINWAND
---------------------------
Die Vollversion laesst sich weitergeben. Das stimmt — technisch verhindern
liesse sich das nur mit einer Pruefung, die wiederum Zugangsdaten braucht.
Stattdessen wird jedes Exemplar personalisiert: Der Name des Kaeufers steht
in jedem erzeugten Beleg. Wer die Datei weitergibt, verteilt seinen eigenen
Namen mit — auf Dokumenten, die beim Arbeitgeber und beim Finanzamt landen.
Das wirkt in der Praxis staerker als jede technische Sperre.

ERKENNUNG
---------
Die Datei edition.json neben der Anwendung entscheidet. Fehlt sie, gilt
Demo — ein versehentlich unvollstaendiges Paket ist damit eingeschraenkt,
nicht versehentlich frei.
"""
from __future__ import annotations

import json
import os
from functools import lru_cache

# ── Umfang der Demo-Fassung ────────────────────────────────────────────────
#
# Erfassen ist unbegrenzt: Wer die Software beurteilen will, muss sie mit
# echten Daten füllen können. Eine Zählgrenze bei fünf Ladevorgängen bricht
# den Test genau dann ab, wenn es interessant wird.
#
# Eingeschränkt sind stattdessen die Dinge, die den laufenden Betrieb
# ausmachen: verwertbare Belege, mehrere Wallboxen, die BMW-Anbindung und
# der OCPP-Server. Damit lässt sich alles ansehen und nichts produktiv nutzen.
DEMO_SESSIONS_PRO_MONAT = None      # unbegrenzt
DEMO_FAHRTEN_PRO_MONAT = None       # unbegrenzt
DEMO_WASSERZEICHEN = "DEMOVERSION"
DEMO_MAX_WALLBOXEN = 1

# Funktionen, die der Vollversion vorbehalten sind
NUR_VOLL = {
    "bmw": ("BMW CarData",
            "Fahrten und Ladevorgänge automatisch aus dem Fahrzeug übernehmen"),
    "ocpp_server": ("OCPP-Server",
                    "Wallboxen anderer Hersteller direkt anbinden"),
    "mehrere_wallboxen": ("Mehrere Wallboxen",
                          "Mehr als eine Ladestation gleichzeitig verwalten"),
}

_EDITION_DATEI = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "edition.json")


@lru_cache(maxsize=1)
def edition() -> dict:   # nur die Paketdatei wird zwischengespeichert
    """Liest die Ausgabekennung. Ohne Datei gilt Demo."""
    try:
        with open(_EDITION_DATEI, encoding="utf-8") as f:
            daten = json.load(f)
        if daten.get("edition") == "voll":
            return {
                "voll": True,
                "bezeichnung": "Vollversion",
                "kaeufer": (daten.get("kaeufer") or "").strip(),
                "bestellnummer": (daten.get("bestellnummer") or "").strip(),
                "ausgestellt": (daten.get("ausgestellt") or "").strip(),
            }
    except Exception:
        pass
    return {"voll": False, "bezeichnung": "Demoversion",
            "kaeufer": "", "bestellnummer": "", "ausgestellt": ""}


def ist_vollversion() -> bool:
    """Vollversion durch Paket ODER durch eingegebenen Lizenzschluessel.

    Beide Wege stehen gleichberechtigt nebeneinander:
      * Das personalisierte Vollpaket braucht keine Verbindung — es laeuft
        auch dort, wo kein Internet erreichbar ist.
      * Der Lizenzschluessel erlaubt es, aus der frei verteilten Demo
        heraus freizuschalten, ohne ein neues Paket zu verschicken.
    """
    if edition()["voll"]:
        return True
    try:
        from services import payhip_service
        return bool(payhip_service.status().get("lizenziert"))
    except Exception:
        # Eine Stoerung bei der Pruefung darf die Anwendung nicht lahmlegen
        return False


def wasserzeichen() -> str | None:
    """Text fuer das PDF-Wasserzeichen, oder None bei der Vollversion."""
    return None if ist_vollversion() else DEMO_WASSERZEICHEN


def _lizenz_kaeufer() -> str:
    """Name bzw. E-Mail aus einer eingegebenen Lizenz."""
    try:
        from services import payhip_service
        st = payhip_service.status()
        return st.get("kaeufer", "") if st.get("lizenziert") else ""
    except Exception:
        return ""


def lizenzvermerk() -> str:
    """Fusszeile fuer erzeugte Belege.

    Bei der Vollversion mit Namen des Kaeufers — das ist die Personalisierung,
    die eine Weitergabe unattraktiv macht."""
    e = edition()
    if e["voll"] and e["kaeufer"]:
        return f"eCharge@Home - Lizenziert für {e['kaeufer']}"
    kaeufer = _lizenz_kaeufer()
    if kaeufer:
        return f"eCharge@Home - Lizenziert für {kaeufer}"
    if ist_vollversion():
        return "eCharge@Home - Vollversion"
    return "Erstellt mit eCharge@Home (Demoversion) - nicht zur Vorlage geeignet"


def monats_limit_erreicht(art: str, anzahl_im_monat: int) -> bool:
    """Erfassen ist in beiden Fassungen unbegrenzt.

    Beibehalten, damit bestehende Aufrufe weiterlaufen."""
    return False


def funktion_verfuegbar(name: str) -> bool:
    """Prueft, ob eine der Vollversion vorbehaltene Funktion nutzbar ist."""
    if ist_vollversion():
        return True
    return name not in NUR_VOLL


def gesperrt_hinweis(name: str) -> dict:
    """Einheitliche Rueckmeldung fuer gesperrte Funktionen."""
    titel, beschreibung = NUR_VOLL.get(name, ("Diese Funktion", ""))
    return {
        "ok": False,
        "gesperrt": True,
        "funktion": titel,
        "meldung": f"{titel} ist der Vollversion vorbehalten.",
        "beschreibung": beschreibung,
        "produktlink": "https://payhip.com/b/smMFv",
    }


def wallbox_limit_erreicht(anzahl_vorhanden: int) -> bool:
    """In der Demo laesst sich genau eine Wallbox einrichten."""
    if ist_vollversion():
        return False
    return anzahl_vorhanden >= DEMO_MAX_WALLBOXEN


def limit_info(sessions_im_monat: int = 0, fahrten_im_monat: int = 0) -> dict:
    """Uebersicht fuer die Oberflaeche."""
    e = edition()
    voll = e["voll"]
    return {
        "voll": voll,
        "bezeichnung": e["bezeichnung"],
        "kaeufer": e["kaeufer"],
        # Erfassen ist unbegrenzt — die Felder bleiben fuer aeltere Aufrufe
        "sessions": {"genutzt": sessions_im_monat, "limit": None, "erreicht": False},
        "fahrten": {"genutzt": fahrten_im_monat, "limit": None, "erreicht": False},
        "wasserzeichen": wasserzeichen(),
        "max_wallboxen": None if voll else DEMO_MAX_WALLBOXEN,
        "gesperrt": [] if voll else [
            {"schluessel": k, "titel": t, "beschreibung": b}
            for k, (t, b) in NUR_VOLL.items()
        ],
    }
