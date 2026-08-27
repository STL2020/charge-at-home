"""Import des BMW-CarData-Datenarchivs (ZIP aus dem MyBMW-Portal).

Das Archiv laesst sich im Portal unter "Historisches Datenarchiv" anfordern und
enthaelt unter anderem die Ladehistorie der letzten Wochen. Diese Datei ist fuer
die Fahrtenerfassung wertvoller, als es zunaechst scheint: Jeder Ladevorgang
traegt den KILOMETERSTAND und den LADEORT MIT ADRESSE.

FAHRTEN-REKONSTRUKTION
----------------------
Zwischen zwei aufeinanderfolgenden Ladevorgaengen liegt zwangslaeufig die
gefahrene Strecke:

    Ladung A:  24.442 km, Ort A
    Ladung B:  24.549 km, Ort B
    ────────────────────────────────────
    → 107 km von Ort A nach Ort B

Das ergaenzt die Live-Erfassung um die Vergangenheit: Der laufende Abruf ueber
die CarData-API kennt nur den aktuellen Stand, das Archiv reicht dagegen Wochen
zurueck.

WICHTIGE EINSCHRAENKUNG
-----------------------
Eine so rekonstruierte "Fahrt" ist die Summe aller Fahrten zwischen zwei
Ladevorgaengen. Wer zu einem Kunden und zurueck faehrt und erst danach
laedt, erhaelt einen Eintrag ueber die Gesamtstrecke mit gleichem Start und
Ziel. Die Kilometer stimmen exakt, die Route ist aber unvollstaendig. Solche
Faelle werden gekennzeichnet, damit sie beim Zuordnen auffallen und ergaenzt
werden koennen — fuer ein Fahrtenbuch nach R 9.5 LStR ist das unverzichtbar.
"""
from __future__ import annotations

import json
import zipfile
from datetime import datetime

from repositories import bmw_trip_repository
import services.event_log_service as event_log_service

# Fahrten unterhalb dieser Distanz gelten als Rangieren und werden verworfen.
MIN_DISTANZ_KM = 1.0


def _kennzeichnung(start_adr: str, ziel_adr: str) -> str:
    """Hinweis, wenn Start und Ziel identisch sind — dann fehlt die Route."""
    if start_adr and start_adr == ziel_adr:
        return " (Rundfahrt — Zwischenziele bitte ergänzen)"
    return ""


def lese_ladehistorie(zip_pfad: str) -> dict:
    """Zieht die Ladehistorie aus dem Archiv.

    Der Dateiname enthaelt Fahrgestellnummer und Datum und variiert daher;
    gesucht wird nach dem Bestandteil 'Ladehistorie' bzw. 'ChargingHistory'."""
    try:
        with zipfile.ZipFile(zip_pfad) as z:
            treffer = [n for n in z.namelist()
                       if n.lower().endswith(".json")
                       and ("ladehistorie" in n.lower() or "charginghistory" in n.lower())]
            if not treffer:
                return {"ok": False,
                        "meldung": ("Im Archiv wurde keine Ladehistorie gefunden. "
                                    "Enthält das ZIP die Datei "
                                    "'BMW-CarData-Ladehistorie…json'?")}
            with z.open(treffer[0]) as f:
                daten = json.loads(f.read().decode("utf-8"))
        if not isinstance(daten, list):
            return {"ok": False, "meldung": "Unerwartetes Format der Ladehistorie."}
        return {"ok": True, "eintraege": daten, "datei": treffer[0]}
    except zipfile.BadZipFile:
        return {"ok": False, "meldung": "Die Datei ist kein gültiges ZIP-Archiv."}
    except Exception as e:
        return {"ok": False, "meldung": f"Archiv nicht lesbar ({type(e).__name__})."}


def _ladepunkte(eintraege: list) -> list[dict]:
    """Verdichtet die Ladehistorie zu Haltepunkten.

    Mehrere Ladevorgaenge am selben Ort mit gleichem Kilometerstand — etwa
    unterbrochenes Laden ueber Nacht — werden zu EINEM Punkt zusammengefasst.
    Sonst entstuenden Dutzende Fahrten mit null Kilometern."""
    sortiert = sorted(eintraege, key=lambda e: e.get("startTime") or 0)
    punkte: list[dict] = []
    for e in sortiert:
        km = e.get("mileage")
        if km is None:
            continue
        ort = e.get("chargingLocation") or {}
        adresse = (ort.get("formattedAddress") or ort.get("municipality") or "").strip()
        zeit = e.get("startTime") or 0
        dauer = e.get("totalChargingDurationSec") or 0

        if punkte and punkte[-1]["km"] == km and punkte[-1]["adresse"] == adresse:
            # Gleicher Ort, gleicher Kilometerstand → derselbe Halt
            punkte[-1]["zeit_ende"] = zeit + dauer
            continue
        punkte.append({
            "km": km,
            "zeitzone": e.get("timeZone") or "Europe/Berlin",
            "adresse": adresse,
            "ort": ort.get("municipality") or "",
            "lat": ort.get("mapMatchedLatitude"),
            "lon": ort.get("mapMatchedLongitude"),
            "zeit_start": zeit,
            "zeit_ende": zeit + dauer,
        })
    return punkte


def _zeit(ts: int, zeitzone: str = "Europe/Berlin") -> str:
    """Unix-Zeitstempel als Ortszeit.

    Ohne Zeitzone wuerde die des Servers gelten — im Docker-Container UTC.
    Eine Fahrt, die um 00:30 endet, waere dann auf 22:30 des Vortags datiert
    und liefe am Monatsersten in die falsche Abrechnung."""
    from zoneinfo import ZoneInfo
    try:
        zone = ZoneInfo(zeitzone or "Europe/Berlin")
    except Exception:
        zone = ZoneInfo("Europe/Berlin")
    try:
        return datetime.fromtimestamp(int(ts), zone).strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return datetime.now(zone).strftime("%Y-%m-%d %H:%M:%S")


def _zieltext(start: str, ziel: str, km: float) -> str:
    """Zieladresse — oder ein Hinweis, wenn es keine gibt.

    Faehrt jemand von zuhause weg und kommt zurueck, ohne unterwegs zu
    laden, meldet BMW zweimal dieselbe Adresse. Das im Fahrtenbuch so
    stehen zu lassen waere irrefuehrend: Es sieht aus, als sei man
    200 km im Kreis gefahren.
    """
    s = (start or "").split(",")[0].strip().lower()
    z = (ziel or "").split(",")[0].strip().lower()
    if s and s == z:
        return f"Ziel unbekannt (Rundfahrt, {km:.0f} km)"
    return ziel or "—"


# ENTFERNT (28.08.): rekonstruiere_fahrten bildete aus Ladepunkten
# vermeintliche Fahrten. Zwischen zwei Ladevorgaengen kann beliebig viel
# passiert sein: 100 km zum Kunden und danach 20 km einkaufen ergeben hier
# eine einzige "Fahrt" ueber 120 km. Die Ladehistorie kennt nur Ladeorte,
# keine Fahrten. Fahrten entstehen jetzt ausschliesslich aus dem MQTT-Stream
# (services/cardata_stream_service.py). Mit ihr entfernt: _standard_fahrzeug
# und importiere_archiv, die nur fuer diesen Zweck existierten.


def lies_fahrzeugdaten(zip_pfad: str) -> dict:
    """Liest Fahrzeugdaten aus dem BMW-Archiv.

    Das Archiv enthaelt neben der Ladehistorie eine KeyList mit den
    Wartungsterminen (Condition Based Service) und eine Reifendiagnose.
    Beides ist fuer die Fahrzeugverwaltung nuetzlich — bisher blieb es
    ungenutzt liegen.
    """
    import zipfile, re as _re
    import xml.etree.ElementTree as ET

    daten: dict = {}
    try:
        with zipfile.ZipFile(zip_pfad) as z:
            namen = z.namelist()

            # Fahrgestellnummer steht im Dateinamen
            for n in namen:
                m = _re.search(r"_([A-HJ-NPR-Z0-9]{17})_", n)
                if m:
                    daten["vin"] = m.group(1)
                    break

            # Wartungstermine aus der KeyList
            keylist = next((n for n in namen
                            if "KeyList" in n and n.endswith(".xml")), None)
            if keylist:
                with z.open(keylist) as f:
                    wurzel = ET.parse(f).getroot()
                for msg in wurzel.iter("cbsMessage"):
                    eintrag = {k.tag: (k.text or "").strip() for k in msg}
                    titel = eintrag.get("title", "")
                    datum = eintrag.get("date", "")[:10]   # TT.MM.JJJJ
                    if not datum or datum == "-":
                        continue
                    # In ISO wandeln, damit sich sortieren laesst
                    m = _re.match(r"(\d{2})\.(\d{2})\.(\d{4})", datum)
                    iso = f"{m.group(3)}-{m.group(2)}-{m.group(1)}" if m else datum
                    if "untersuchung" in titel.lower():
                        daten["hu_faellig"] = iso
                    elif "bremsfl" in titel.lower():
                        daten["bremsfluessigkeit"] = iso
                    elif "check" in titel.lower() or "service" in titel.lower():
                        daten["service_faellig"] = iso

            # Reifen aus der Diagnose
            reifen = next((n for n in namen if "Reifendiagnose" in n
                           and n.endswith(".json")), None)
            if reifen:
                with z.open(reifen) as f:
                    r = json.loads(f.read().decode("utf-8"))
                montiert = ((r.get("passengerCar") or {}).get("mountedTyres") or {})
                for seite, feld in [("frontLeft", "reifen_vorne"),
                                    ("rearLeft", "reifen_hinten")]:
                    dim = ((montiert.get(seite) or {}).get("dimension") or {})
                    if dim.get("value"):
                        daten[feld] = dim["value"]

            # Kilometerstand aus der juengsten Ladung
            hist = next((n for n in namen if "Ladehistorie" in n
                         and n.endswith(".json")), None)
            if hist:
                with z.open(hist) as f:
                    eintraege = json.loads(f.read().decode("utf-8"))
                mit_km = [e for e in eintraege if e.get("mileage")]
                if mit_km:
                    juengste = max(mit_km, key=lambda e: e.get("startTime", 0))
                    daten["km_stand"] = int(juengste["mileage"])
                    from datetime import datetime as _dt
                    daten["km_stand_datum"] = _dt.fromtimestamp(
                        juengste["startTime"]).strftime("%Y-%m-%d")
    except Exception:
        pass   # Fahrzeugdaten sind Beiwerk — der Fahrten-Import laeuft trotzdem

    return daten


def ladevorgaenge_uebersicht(eintraege: list) -> dict:
    """Kennzahlen der Ladehistorie — informativ fuer die Oberflaeche.

    Die Unterscheidung nach Ladeort ist steuerlich relevant: Nur zuhause
    geladener Strom faellt unter den Auslagenersatz nach § 3 Nr. 50 EStG."""
    orte: dict[str, int] = {}
    for e in eintraege:
        ort = (e.get("chargingLocation") or {}).get("formattedAddress") or "unbekannt"
        orte[ort] = orte.get(ort, 0) + 1
    return {
        "anzahl": len(eintraege),
        "orte": sorted(orte.items(), key=lambda x: -x[1]),
    }


def importiere_ladehistorie_datei(vehicle_id: int, zip_pfad: str, user_id: int,
                                  vin: str = "") -> dict:
    """Einmaliger Import: Ladehistorie aus dem BMW-Datenarchiv (ZIP) als
    Ladesessions dieses Fahrzeugs uebernehmen — fuer Zeitraeume vor den
    letzten 30 Tagen, die die laufende Verbindung (Stream) nicht mehr
    erreicht.

    Nutzt bewusst dieselbe Verarbeitung wie der laufende API-Import
    (Heimladung-Erkennung, Preise je Ladeart, Duplikatschutz, Energie aus
    Ladebloecken/SoC) — nur die Quelle der Rohdaten unterscheidet sich.
    Die fruehere Variante hat aus denselben Daten stattdessen Fahrten
    rekonstruiert; das ist entfernt (siehe rekonstruiere_fahrten-Docstring),
    weil zwischen zwei Ladungen beliebig viel liegen kann."""
    gelesen = lese_ladehistorie(zip_pfad)
    if not gelesen["ok"]:
        return gelesen

    from services import cardata_service
    return cardata_service.importiere_ladesessions(
        vehicle_id, vin, user_id, sessions_liste=gelesen["eintraege"])
