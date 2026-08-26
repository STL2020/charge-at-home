"""Holt Ladevorgänge von einem externen OCPP-Dienst ab.

HINTERGRUND
-----------
Ein OCPP-Server muss laufen, wenn die Wallbox lädt — also auch nachts um zwei.
Auf einem Arbeitsplatzrechner ist das nicht gegeben: Er ist aus, wenn das Auto
lädt, und die Wallbox findet niemanden, dem sie ihre Daten melden könnte.

Deshalb übernimmt ein dauerhaft laufendes Gerät diese Aufgabe — ein LoxBerry,
ein NAS oder ein Raspberry Pi. Diese Anwendung holt die gesammelten Vorgänge
dort ab, wann immer sie gestartet wird.

Für Loxone-Wallboxen wird das gar nicht gebraucht: Deren Daten liest die
Anwendung direkt über die Miniserver-Schnittstelle.

UNTERSTÜTZTE GEGENSTELLEN
-------------------------
  eCharge@Home LoxBerry-Plugin   GET /api/sessions
  evcc                            GET /api/sessions
  SteVe                           GET /api/v1/transactions

Die Antworten unterscheiden sich im Aufbau; die Feldzuordnung unten gleicht
das aus. Weitere Dienste lassen sich ergänzen, ohne den übrigen Code zu
berühren.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta

from repositories import (settings_repository, session_repository,
                          wallbox_repository)
import services.event_log_service as event_log_service

# Feldzuordnung je Gegenstelle. Der erste passende Name gewinnt — so lassen
# sich Abweichungen zwischen Versionen auffangen, ohne Fallunterscheidungen
# im Ablauf.
FELDER = {
    "start":  ("start_time", "startTime", "created", "timestamp", "beginn"),
    "ende":   ("end_time", "endTime", "stopped", "ende"),
    "kwh":    ("energy_kwh", "energy", "chargedEnergy", "kwh", "meterTotal"),
    "start_wh": ("meter_start", "meterStart", "meter_start_wh"),
    "ende_wh":  ("meter_stop", "meterStop", "meter_stop_wh"),
    "punkt":  ("meter_id", "chargePointId", "charge_point_id", "connector", "loadpoint"),
    "preis":  ("price_per_kwh", "pricePerKwh", "tarif"),
}


def _hole(eintrag: dict, feld: str, standard=None):
    """Liest einen Wert anhand der bekannten Namensvarianten."""
    for name in FELDER.get(feld, ()):
        if name in eintrag and eintrag[name] not in (None, ""):
            return eintrag[name]
    return standard


def _als_zeit(wert) -> datetime | None:
    """Wandelt die verbreiteten Zeitformate um.

    Unterstützt Unix-Sekunden, Unix-Millisekunden und ISO-8601 — dieselbe
    Vielfalt, die uns schon bei BMW begegnet ist."""
    if wert in (None, ""):
        return None
    if isinstance(wert, (int, float)):
        zahl = int(wert)
        if zahl > 10_000_000_000:
            zahl //= 1000
        return datetime.fromtimestamp(zahl)
    text = str(wert).strip()
    if text.isdigit():
        zahl = int(text)
        if zahl > 10_000_000_000:
            zahl //= 1000
        return datetime.fromtimestamp(zahl)
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).replace(tzinfo=None)
    except ValueError:
        return None


def konfiguration() -> dict:
    """Aktuelle Einstellungen der Anbindung."""
    return {
        "aktiv": (settings_repository.get_setting("extern_ocpp_aktiv") or "0") == "1",
        "adresse": settings_repository.get_setting("extern_ocpp_adresse") or "",
        "pfad": settings_repository.get_setting("extern_ocpp_pfad") or "/api/sessions",
        "wallbox_name": (settings_repository.get_setting("extern_ocpp_wallbox")
                         or "Wallbox (extern)"),
        "preis": float(settings_repository.get_setting("contract_kwh_price")
                       or settings_repository.get_setting("default_kwh_price") or 0.34),
    }


def speichere_konfiguration(adresse: str, pfad: str = "", wallbox_name: str = "",
                            aktiv: bool = True) -> dict:
    """Legt die Verbindungsdaten ab.

    Die Adresse wird geduldig behandelt: Wer '192.168.1.50' eingibt, meint
    'http://192.168.1.50:8080' — ein Schema von Hand zu verlangen wäre
    unnötige Strenge."""
    adresse = (adresse or "").strip().rstrip("/")
    if adresse and not adresse.startswith(("http://", "https://")):
        adresse = "http://" + adresse
    settings_repository.set_setting("extern_ocpp_adresse", adresse)
    settings_repository.set_setting("extern_ocpp_pfad", (pfad or "/api/sessions").strip())
    settings_repository.set_setting("extern_ocpp_wallbox",
                                    (wallbox_name or "Wallbox (extern)").strip())
    settings_repository.set_setting("extern_ocpp_aktiv", "1" if aktiv else "0")
    return konfiguration()


def teste_verbindung() -> dict:
    """Prüft, ob die Gegenstelle antwortet und verwertbare Daten liefert."""
    k = konfiguration()
    if not k["adresse"]:
        return {"ok": False, "meldung": "Es ist keine Adresse hinterlegt."}
    try:
        eintraege = _abrufen(k["adresse"] + k["pfad"])
    except Exception as e:
        return {"ok": False, "meldung": _fehlertext(e, k["adresse"])}

    if not eintraege:
        return {"ok": True, "anzahl": 0,
                "meldung": ("Verbindung steht, es liegen aber keine Ladevorgänge vor. "
                            "Das ist normal, solange noch nicht geladen wurde.")}

    beispiel = eintraege[0]
    erkannt = sum(1 for f in ("start", "kwh") if _hole(beispiel, f) is not None)
    return {
        "ok": True,
        "anzahl": len(eintraege),
        "felder_erkannt": erkannt == 2,
        "meldung": (f"Verbindung steht. {len(eintraege)} Ladevorgänge gefunden."
                    if erkannt == 2 else
                    f"Verbindung steht, aber die Antwort enthält weder Zeitpunkt "
                    f"noch Energiemenge. Gefundene Felder: "
                    f"{', '.join(list(beispiel.keys())[:8])}"),
    }


def _abrufen(url: str, zeitlimit: int = 10) -> list:
    """Ruft die Liste ab und normalisiert die Hülle.

    Manche Dienste liefern die Liste unmittelbar, andere verpacken sie in
    ein Objekt — beides kommt vor."""
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=zeitlimit) as resp:
        daten = json.loads(resp.read().decode("utf-8"))
    if isinstance(daten, list):
        return daten
    for schluessel in ("sessions", "data", "transactions", "result", "items"):
        wert = daten.get(schluessel) if isinstance(daten, dict) else None
        if isinstance(wert, list):
            return wert
    return []


def _fehlertext(fehler: Exception, adresse: str) -> str:
    """Übersetzt Netzwerkfehler in etwas, mit dem man arbeiten kann."""
    if isinstance(fehler, urllib.error.HTTPError):
        if fehler.code == 404:
            return ("Der Dienst antwortet, kennt den Pfad aber nicht. "
                    "Stimmt die Angabe im Feld Pfad?")
        if fehler.code in (401, 403):
            return "Der Dienst verweigert den Zugriff."
        return f"Unerwartete Antwort (HTTP {fehler.code})."
    if isinstance(fehler, urllib.error.URLError):
        grund = str(getattr(fehler, "reason", ""))
        if "refused" in grund.lower():
            return (f"{adresse} ist erreichbar, aber auf diesem Port lauscht nichts. "
                    "Läuft der Dienst?")
        if "timed out" in grund.lower():
            return f"{adresse} antwortet nicht. Stimmt die Adresse?"
        return f"Keine Verbindung zu {adresse} ({grund})."
    if isinstance(fehler, json.JSONDecodeError):
        return "Die Antwort ist kein JSON — zeigt der Pfad auf eine Weboberfläche?"
    return f"Abruf fehlgeschlagen ({type(fehler).__name__})."


def importiere(user_id: int) -> dict:
    """Holt neue Ladevorgänge ab und legt sie an."""
    k = konfiguration()
    if not k["adresse"]:
        return {"ok": False, "meldung": "Es ist keine Adresse hinterlegt."}

    try:
        eintraege = _abrufen(k["adresse"] + k["pfad"])
    except Exception as e:
        meldung = _fehlertext(e, k["adresse"])
        event_log_service.log_event("extern_ocpp", "warning",
            f"Externer Dienst nicht abrufbar: {meldung}")
        return {"ok": False, "meldung": meldung}

    if not eintraege:
        return {"ok": True, "neu": 0, "gefunden": 0,
                "meldung": "Keine Ladevorgänge vorhanden."}

    wallbox_id = wallbox_repository.get_or_create_wallbox(
        k["wallbox_name"], source_type="manual")

    neu = uebersprungen = ohne_energie = 0
    for eintrag in eintraege:
        if not isinstance(eintrag, dict):
            continue
        start = _als_zeit(_hole(eintrag, "start"))
        if start is None:
            uebersprungen += 1
            continue
        ende = _als_zeit(_hole(eintrag, "ende")) or start

        # Zählerstände bevorzugen: Sie sind der belastbarere Nachweis.
        # Fehlen sie, genügt die gemeldete Menge.
        start_wh = _hole(eintrag, "start_wh")
        ende_wh = _hole(eintrag, "ende_wh")
        if start_wh is not None and ende_wh is not None:
            try:
                s_wh, e_wh = int(float(start_wh)), int(float(ende_wh))
                # Manche Dienste zählen in kWh statt Wh
                if e_wh - s_wh < 200 and float(ende_wh) - float(start_wh) > 0:
                    s_wh, e_wh = int(float(start_wh) * 1000), int(float(ende_wh) * 1000)
            except (TypeError, ValueError):
                s_wh = e_wh = 0
        else:
            try:
                kwh = float(_hole(eintrag, "kwh", 0) or 0)
            except (TypeError, ValueError):
                kwh = 0
            s_wh, e_wh = 0, int(kwh * 1000)

        if e_wh - s_wh <= 0:
            ohne_energie += 1
            continue

        start_ts = start.strftime("%Y-%m-%d %H:%M:%S")
        if session_repository.session_exists_near_start(wallbox_id, start_ts[:16]):
            uebersprungen += 1
            continue

        try:
            preis = float(_hole(eintrag, "preis") or k["preis"])
        except (TypeError, ValueError):
            preis = k["preis"]

        session_repository.insert_session(
            user_id=user_id, wallbox_id=wallbox_id, source="ocpp",
            start_timestamp=start_ts,
            end_timestamp=ende.strftime("%Y-%m-%d %H:%M:%S"),
            meter_start_wh=s_wh, meter_stop_wh=e_wh,
            price_per_kwh=preis, status="closed",
            charging_location="zuhause",
            charging_location_note=str(_hole(eintrag, "punkt") or ""))
        neu += 1

    event_log_service.log_event("extern_ocpp", "info",
        f"Externer Dienst: {len(eintraege)} Vorgänge geprüft, {neu} übernommen, "
        f"{uebersprungen} bereits bekannt, {ohne_energie} ohne Energiefluss.")
    return {"ok": True, "neu": neu, "gefunden": len(eintraege),
            "uebersprungen": uebersprungen, "ohne_energie": ohne_energie}
