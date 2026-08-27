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


def rekonstruiere_fahrten(eintraege: list, vin: str = "") -> list[dict]:
    """Bildet aus den Ladepunkten Fahrten.

    Die Fahrt-ID enthaelt beide Kilometerstaende und ist damit stabil: Ein
    erneuter Import derselben Daten erzeugt keine Doppeleintraege."""
    punkte = _ladepunkte(eintraege)
    fahrten = []
    for a, b in zip(punkte, punkte[1:]):
        distanz = (b["km"] or 0) - (a["km"] or 0)
        if distanz < MIN_DISTANZ_KM:
            continue
        fahrten.append({
            "trip_id": f"ARCH-{vin or 'BMW'}-{a['km']}-{b['km']}",
            # Abfahrt: nach Ende des Ladevorgangs am Startpunkt
            "start_time": _zeit(a["zeit_ende"] or a["zeit_start"], a.get("zeitzone")),
            "end_time": _zeit(b["zeit_start"], b.get("zeitzone")),
            "start_mileage": a["km"],
            "end_mileage": b["km"],
            "distance_km": round(float(distanz), 1),
            "start_address": a["adresse"] or "—",
            # Start und Ziel gleich: Der Wagen ist weggefahren und
            # zurueckgekommen, ohne unterwegs zu laden. BMW kennt nur die
            # beiden Ladepunkte — wo der Termin war, steht nirgends. Statt
            # zweimal derselben Adresse (was aussieht, als waere man im
            # Kreis gefahren) wird das offen benannt.
            "end_address": _zieltext(a["adresse"], b["adresse"], distanz),
        })
    return fahrten


def _standard_fahrzeug(user_id: int, vin: str = "") -> int | None:
    """Fahrzeug fuer den Import ermitteln.

    Zuerst ueber die Fahrgestellnummer — sie steht im Archivnamen und
    identifiziert den Wagen eindeutig. Sonst: Gibt es genau ein Fahrzeug,
    kann es nur dieses sein.
    """
    from services import db_service
    conn = db_service.get_connection()
    try:
        spalten = [r["name"] for r in conn.execute("PRAGMA table_info(vehicles)")]
        if not spalten:
            return None
        if vin and "vin" in spalten:
            z = conn.execute("SELECT id FROM vehicles WHERE vin = ? LIMIT 1",
                             (vin,)).fetchone()
            if z:
                return z["id"]
        zeilen = conn.execute("SELECT id FROM vehicles LIMIT 2").fetchall()
        if len(zeilen) == 1:
            return zeilen[0]["id"]
    except Exception:
        pass
    finally:
        conn.close()
    return None


def importiere_archiv(zip_pfad: str, user_id: int, vin: str = "",
                      vehicle_id: int | None = None) -> dict:
    """Kompletter Durchlauf: Archiv lesen, Fahrten bilden, speichern."""
    gelesen = lese_ladehistorie(zip_pfad)
    if not gelesen["ok"]:
        return gelesen

    eintraege = gelesen["eintraege"]
    fahrten = rekonstruiere_fahrten(eintraege, vin)
    if not fahrten:
        return {"ok": True, "neu": 0, "gefunden": 0,
                "ladevorgaenge": len(eintraege),
                "meldung": ("Keine Fahrten ableitbar — im Archiv fehlen "
                            "Kilometerstände oder es gab keine Ortswechsel.")}

    # Verwaiste Referenzen entfernen: Wurde eine Fahrt geloescht, soll sie
    # erneut importiert werden koennen.
    bmw_trip_repository.raeume_verwaiste_auf(user_id)
    bekannt = bmw_trip_repository.bekannte_trip_ids(user_id)
    neue = [f for f in fahrten if f["trip_id"] not in bekannt]

    # Die Fahrten landen direkt in der normalen Fahrtenliste. Eine zweite
    # Oberflaeche zum Zuordnen waere ueberfluessig: Dort stehen ohnehin alle
    # Fahrten, dort werden neue erfasst, dort wird bearbeitet. `bmw_trips`
    # dient nur noch als technische Referenz fuer den Duplikatschutz.
    from repositories import trip_repository
    # Fahrzeug bestimmen, falls keines uebergeben wurde: Gibt es nur eines,
    # ist die Sache eindeutig. Sonst bleibt das Feld leer und der Anwender
    # waehlt selbst — raten waere hier schlechter als offen lassen.
    if vehicle_id is None:
        vehicle_id = _standard_fahrzeug(user_id, vin)

    gespeichert = 0
    for f in neue:
        trip_id = trip_repository.insert_trip(
            user_id=user_id,
            trip_date=(f.get("start_time") or "")[:10],
            start_address=f.get("start_address") or "—",
            end_address=f.get("end_address") or "—",
            distance_km=f.get("distance_km") or 0,
            purpose="",
            rate_chosen=0.0, vehicle_id=vehicle_id, fahrtart="offen")
        # Referenz mit direkter Verknuepfung — nur so laesst sich spaeter
        # erkennen, ob die Fahrt noch existiert.
        bmw_trip_repository.insert_trip_ref(user_id, f, trip_id, vehicle_id=vehicle_id)
        gespeichert += 1

    km_gesamt = round(sum(f["distance_km"] for f in fahrten), 1)
    zeitraum = ""
    if fahrten:
        zeitraum = f"{fahrten[0]['start_time'][:10]} bis {fahrten[-1]['end_time'][:10]}"

    event_log_service.log_event("bmw", "info",
        f"CarData-Archiv importiert: {gespeichert} neue Fahrten "
        f"({km_gesamt} km) aus {len(eintraege)} Ladevorgängen.")
    return {
        "ok": True,
        "neu": gespeichert,
        "gefunden": len(fahrten),
        "uebersprungen": len(fahrten) - len(neue),
        "ladevorgaenge": len(eintraege),
        "km_gesamt": km_gesamt,
        "zeitraum": zeitraum,
        "vorschau": fahrten[:5],
    }


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
