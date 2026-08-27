"""Erzeugt einen vollständigen, realistischen Datenbestand für Vorführungen.

Zweck: Wer die Anwendung zum ersten Mal öffnet, sieht leere Tabellen und kann
nicht beurteilen, was sie leistet. Ein Klick füllt sie mit einem kompletten
Jahr — Wallbox, Ladevorgänge zuhause und unterwegs, Dienst- und Privatfahrten,
Fahrzeugkosten. Danach funktioniert jede Auswertung, jeder Beleg und jede
Kennzahl so, wie sie es mit echten Daten täte.

Die Daten sind bewusst plausibel statt zufällig: Ladungen folgen auf Fahrten,
der Kilometerstand wächst monoton, der Zählerstand ebenfalls, Schnellladungen
liegen unterwegs und kosten mehr. Eine Vorführung mit unmöglichen Werten
überzeugt niemanden.

Alle erzeugten Datensätze tragen eine Kennung im Verwendungszweck, damit sie
sich vollständig und ohne Risiko für echte Daten wieder entfernen lassen.
"""
from __future__ import annotations

import random
import sqlite3
from datetime import datetime, timedelta

from repositories import (person_repository, vehicle_repository,
                          wallbox_repository, session_repository,
                          trip_repository, settings_repository)
from services import db_service
import services.event_log_service as event_log_service

# Kennzeichnung der Demodaten. Steht im Anlass jeder Fahrt und erlaubt das
# gezielte Entfernen, ohne echte Erfassungen anzutasten.
MARKE = "[Demo]"

# Realistische Rahmenwerte eines Außendienstjahres
VERBRAUCH_KWH_100 = 20.8
AKKU_KWH = 80.7
WALLBOX_KW = 11.0
PREIS_HEIM = 0.28
PREIS_DC = 0.79
PREIS_AC_EXTERN = 0.59

KUNDEN = [
    ("Kundentermin Vertrieb", "Kundenzentrum Düsseldorf", "Kaiserswerther Str. 12, 40474 Düsseldorf", 221),
    ("Projektbesprechung", "Werk Köln-Niehl", "Niehler Gürtel 8, 50733 Köln", 96),
    ("Außendienstbesuch", "Standort Dortmund", "Rheinlanddamm 201, 44139 Dortmund", 189),
    ("Schulung / Training", "Akademie Frankfurt", "Mainzer Landstr. 50, 60325 Frankfurt", 178),
    ("Messebesuch", "Messe Essen", "Norbertstr. 2, 45131 Essen", 165),
    ("Partnergespräch", "Partnerbüro Bonn", "Adenauerallee 88, 53113 Bonn", 42),
    ("Lieferantenbesuch", "Zulieferer Aachen", "Jülicher Str. 191, 52070 Aachen", 148),
    ("Meeting intern", "Zentrale Wuppertal", "Friedrich-Engels-Allee 24, 42103 Wuppertal", 112),
]

PRIVATZIELE = [
    ("Privatfahrt", "Baumarkt", 18),
    ("Privatfahrt", "Wochenendausflug", 145),
    ("Privatfahrt", "Familienbesuch", 87),
    ("Privatfahrt", "Einkauf Innenstadt", 12),
    ("Privatfahrt", "Sportverein", 24),
]

SCHNELLLADER = [
    ("Autohof Wermelskirchen", "A1 Raststätte, 42929 Wermelskirchen", 152),
    ("Ladepark Velbert", "Friedrichstr. 299, 42551 Velbert", 178),
    ("Schnellladepark Gevelsberg", "Hagener Str. 130, 58285 Gevelsberg", 196),
    ("Ladehub Frankfurt Nord", "Mainzer Landstr. 50, 60325 Frankfurt", 143),
]


# Beispieldaten verwenden ausschliesslich erfundene Anschriften.
#
# Frueher wurde die Wohnanschrift der hinterlegten Person uebernommen, damit
# die Daten "realistisch" wirken. Das war falsch gedacht: Beispieldaten landen
# in Vorfuehrungen, Bildschirmfotos und Fehlerberichten. Die private Adresse
# des Anwenders hat dort nichts zu suchen.
DEMO_HEIMADRESSE = "Beispielweg 12, 40210 Musterstadt"


def _heimadresse() -> str:
    """Erfundene Wohnanschrift fuer die Beispieldaten."""
    return DEMO_HEIMADRESSE


def bestand() -> dict:
    """Zählt vorhandene Demodatensätze."""
    conn = db_service.get_connection()
    try:
        fahrten = conn.execute(
            "SELECT COUNT(*) c FROM trips WHERE purpose LIKE ?", (f"%{MARKE}%",)).fetchone()["c"]
        sessions = conn.execute(
            "SELECT COUNT(*) c FROM charging_sessions WHERE rfid_tag = ?",
            (MARKE,)).fetchone()["c"]
        wallboxen = conn.execute(
            "SELECT COUNT(*) c FROM wallboxes WHERE name LIKE ?",
            (f"%{MARKE}%",)).fetchone()["c"]
        return {"fahrten": fahrten, "sessions": sessions, "wallboxen": wallboxen,
                "vorhanden": bool(fahrten or sessions or wallboxen)}
    finally:
        conn.close()


def erzeuge(user_id: int = 1, monate: int = 12) -> dict:
    """Legt einen vollständigen Datenbestand an.

    Der Ablauf bildet ein echtes Jahr nach: Pro Woche mehrere Dienstfahrten,
    dazwischen Privatfahrten, nach jeder größeren Fahrt eine Ladung zuhause,
    bei langen Strecken zusätzlich unterwegs.
    """
    zufall = random.Random(20260824)   # feste Folge: Vorführungen bleiben vergleichbar
    heim = _heimadresse()

    # ── Stammdaten sicherstellen ───────────────────────────────────────────
    # Vorhandene Person weiterverwenden, aber NIE ihre Adresse in die
    # Beispieldaten uebernehmen — die Fahrten starten immer an der
    # erfundenen Anschrift.
    personen = person_repository.list_persons()
    if personen:
        person_id = personen[0]["id"]
    else:
        person_id = person_repository.insert_person(
            "Max Mustermann", "max.mustermann@example.com", "10042",
            "M-AB 1234", "", DEMO_HEIMADRESSE)

    fahrzeuge = vehicle_repository.list_vehicles()
    if fahrzeuge:
        vehicle_id = next((v["id"] for v in fahrzeuge if v.get("ist_standard")),
                          fahrzeuge[0]["id"])
    else:
        vehicle_id = vehicle_repository.insert_vehicle(
            person_id, "BMW i4 M50", "M-AB 1234E", "elektro", True)

    # Namen tragen die Kennzeichnung, damit die Wallboxen beim Entfernen
    # mitgehen. Ohne sie blieben nach jedem Durchlauf Karteileichen zurueck,
    # und ein zweiter Lauf legte weitere daneben.
    wb_heim = wallbox_repository.get_or_create_wallbox(
        f"Wallbox Garage {MARKE}", source_type="loxone_api")
    wb_extern = wallbox_repository.get_or_create_wallbox(
        f"Unterwegs (Ladekarte) {MARKE}", source_type="manual")

    # ── Zeitraum ───────────────────────────────────────────────────────────
    heute = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    start = (heute - timedelta(days=monate * 30)).replace(day=1)

    km_stand = 18_500          # Kilometerstand zu Beginn
    zaehler_wh = 412_000       # Wallbox-Zählerstand in Wh
    anz_fahrten = anz_sessions = 0
    km_dienst = km_privat = 0.0
    kwh_heim = kwh_extern = 0.0

    tag = start
    while tag < heute:
        wochentag = tag.weekday()

        # ── Dienstfahrten: zwei bis drei je Arbeitswoche ───────────────────
        if wochentag < 5 and zufall.random() < 0.45:
            anlass, ziel_name, ziel_adresse, entfernung = zufall.choice(KUNDEN)
            hin_zurueck = zufall.random() < 0.75
            strecke = entfernung * (2 if hin_zurueck else 1)
            # Leichte Streuung: Umleitungen, Parkplatzsuche
            strecke = round(strecke * zufall.uniform(0.97, 1.06), 1)

            trip_repository.insert_trip(
                user_id=user_id, trip_date=tag.strftime("%Y-%m-%d"),
                start_address=heim,
                end_address=f"{ziel_name}, {ziel_adresse}",
                distance_km=strecke,
                purpose=f"{anlass} {MARKE}",
                rate_chosen=0.15, vehicle_id=vehicle_id, fahrtart="dienstlich")
            km_stand += strecke
            km_dienst += strecke
            anz_fahrten += 1

            # Lange Strecken erfordern Nachladen unterwegs
            if strecke > 260 and zufall.random() < 0.7:
                name, adresse, leistung = zufall.choice(SCHNELLLADER)
                menge = round(zufall.uniform(24, 42), 2)
                beginn = tag.replace(hour=zufall.randint(11, 15),
                                     minute=zufall.choice([5, 17, 23, 41]))
                dauer_min = int(menge / (leistung / 60) * 1.35)
                session_repository.insert_session(
                    user_id=user_id, wallbox_id=wb_extern, source="bmw_app",
                    start_timestamp=beginn.strftime("%Y-%m-%d %H:%M:%S"),
                    end_timestamp=(beginn + timedelta(minutes=dauer_min)).strftime("%Y-%m-%d %H:%M:%S"),
                    meter_start_wh=0, meter_stop_wh=int(menge * 1000),
                    price_per_kwh=PREIS_DC, status="closed",
                    charging_location="extern", charging_location_note=adresse,
                    rfid_tag=MARKE)
                kwh_extern += menge
                anz_sessions += 1

        # ── Privatfahrten: eher am Wochenende ──────────────────────────────
        if (wochentag >= 5 and zufall.random() < 0.5) or zufall.random() < 0.12:
            anlass, ziel, entfernung = zufall.choice(PRIVATZIELE)
            strecke = round(entfernung * 2 * zufall.uniform(0.95, 1.1), 1)
            trip_repository.insert_trip(
                user_id=user_id, trip_date=tag.strftime("%Y-%m-%d"),
                start_address=heim, end_address=ziel,
                distance_km=strecke,
                purpose=f"{anlass} {MARKE}",
                rate_chosen=0.0, vehicle_id=vehicle_id, fahrtart="privat")
            km_stand += strecke
            km_privat += strecke
            anz_fahrten += 1

        # ── Heimladung, sobald genug verbraucht wurde ──────────────────────
        # Rund alle 300 km wird nachgeladen — das entspricht etwa 60 kWh.
        offen_km = (km_dienst + km_privat) * VERBRAUCH_KWH_100 / 100 \
                   - (kwh_heim + kwh_extern)
        if offen_km > zufall.uniform(28, 55):
            menge = round(min(offen_km, AKKU_KWH * 0.82), 2)
            beginn = tag.replace(hour=zufall.randint(17, 22),
                                 minute=zufall.choice([3, 12, 28, 47]))
            dauer_min = int(menge / WALLBOX_KW * 60 * 1.08)   # inkl. Ladeverlust
            session_repository.insert_session(
                user_id=user_id, wallbox_id=wb_heim, source="loxone_api",
                start_timestamp=beginn.strftime("%Y-%m-%d %H:%M:%S"),
                end_timestamp=(beginn + timedelta(minutes=dauer_min)).strftime("%Y-%m-%d %H:%M:%S"),
                meter_start_wh=zaehler_wh,
                meter_stop_wh=zaehler_wh + int(menge * 1000),
                price_per_kwh=PREIS_HEIM, status="closed",
                charging_location="zuhause", charging_location_note=heim,
                rfid_tag=MARKE)
            zaehler_wh += int(menge * 1000)
            kwh_heim += menge
            anz_sessions += 1

        tag += timedelta(days=1)

    # ── Fahrzeugkosten für die Vollkostenrechnung ──────────────────────────
    # person_id, nicht vehicle_id — die Kosten haengen laut Schema an der
    # Person, nicht am einzelnen Fahrzeug.
    _fahrzeugkosten(person_id, start, heute)

    # Vertragspreis setzen, damit die Auswertung sinnvolle Werte zeigt
    if not settings_repository.get_setting("contract_kwh_price"):
        settings_repository.set_setting("contract_kwh_price", str(PREIS_HEIM))

    event_log_service.log_event("system", "info",
        f"Demodaten erzeugt: {anz_fahrten} Fahrten, {anz_sessions} Ladevorgänge "
        f"über {monate} Monate.")

    return {
        "ok": True,
        "fahrten": anz_fahrten,
        "sessions": anz_sessions,
        "km_dienstlich": round(km_dienst),
        "km_privat": round(km_privat),
        "kwh_zuhause": round(kwh_heim, 1),
        "kwh_unterwegs": round(kwh_extern, 1),
        "zeitraum": f"{start.strftime('%m/%Y')} bis {heute.strftime('%m/%Y')}",
        "kilometerstand": round(km_stand),
    }


def _fahrzeugkosten(person_id: int, von: datetime, bis: datetime) -> None:
    """Legt laufende PKW-Kosten an, damit die Vollkostenrechnung greift.

    Die frühere Fassung schrieb in Spalten, die es nicht gibt (vehicle_id,
    kostenart, betrag_eur) und brach still ab — die Vollkosten-Bilanz blieb
    deshalb immer bei null. Maßgeblich ist das Schema: person_id, kategorie,
    bezeichnung, betrag, intervall."""
    conn = db_service.get_connection()
    try:
        spalten = {r["name"] for r in conn.execute("PRAGMA table_info(pkw_costs)")}
        if not {"person_id", "kategorie", "bezeichnung", "betrag"} <= spalten:
            return

        # Realistische Werte für einen geleasten BMW i4 M50
        posten = [
            ("leasing",      "Leasingrate",          649.00, "monatlich"),
            ("versicherung", "Vollkasko 300/150",     92.50, "monatlich"),
            ("wartung",      "Wartungspaket",         38.00, "monatlich"),
            ("reifen",       "Sommer- und Winterreifen", 940.00, "jaehrlich"),
            ("tuev",         "Hauptuntersuchung",    128.00, "jaehrlich"),
            ("sonstige",     "Stellplatzmiete",       45.00, "monatlich"),
        ]
        for kategorie, bezeichnung, betrag, intervall in posten:
            conn.execute(
                "INSERT INTO pkw_costs (person_id, kategorie, bezeichnung, betrag, "
                "intervall, aktiv, notiz) VALUES (?,?,?,?,?,1,?)",
                (person_id, kategorie, f"{bezeichnung} {MARKE}", betrag,
                 intervall, "Beispieldaten"))

        # Car Allowance: der monatliche Zuschuss des Arbeitgebers. Ohne ihn
        # bleibt die Kachel "Cash-Saldo" unvollständig.
        try:
            ca_spalten = {r["name"] for r in conn.execute("PRAGMA table_info(car_allowance)")}
            if {"person_id", "monatlicher_betrag"} <= ca_spalten:
                vorhanden = conn.execute(
                    "SELECT COUNT(*) c FROM car_allowance WHERE person_id = ?",
                    (person_id,)).fetchone()["c"]
                if not vorhanden:
                    conn.execute(
                        "INSERT INTO car_allowance (person_id, monatlicher_betrag, "
                        "lohnsteuerklasse, versteuert) VALUES (?,?,1,0)",
                        (person_id, 750.00))
        except sqlite3.Error:
            pass

        conn.commit()
    except sqlite3.Error:
        conn.rollback()
    finally:
        conn.close()


def entferne() -> dict:
    """Entfernt ausschließlich die Demodaten.

    Erkannt werden sie an der Kennzeichnung — selbst erfasste Datensätze
    bleiben unangetastet, auch wenn sie im selben Zeitraum liegen."""
    conn = db_service.get_connection()
    try:
        fahrten = conn.execute(
            "DELETE FROM trips WHERE purpose LIKE ?", (f"%{MARKE}%",)).rowcount
        sessions = conn.execute(
            "DELETE FROM charging_sessions WHERE rfid_tag = ?", (MARKE,)).rowcount
        kosten = 0
        try:
            kosten = conn.execute(
                "DELETE FROM pkw_costs WHERE kostenart LIKE ?", (f"%{MARKE}%",)).rowcount
        except Exception:
            pass

        # Abrechnungspositionen, deren Fahrt oder Ladung gerade entfernt
        # wurde. Bleiben sie stehen, erscheinen sie als Belegzeilen ohne
        # Beleg — und die Monatsabrechnung zeigt Betraege ohne Grundlage.
        for tabelle, spalte, quelle in (
                ("billing_entries", "trip_id", "trips"),
                ("billing_entries", "session_id", "charging_sessions")):
            try:
                conn.execute(
                    f"DELETE FROM {tabelle} WHERE {spalte} IS NOT NULL "
                    f"AND {spalte} NOT IN (SELECT id FROM {quelle})")
            except Exception:
                pass

        # Die Wallboxen der Beispieldaten. Nur solche mit Kennzeichnung im
        # Namen und ohne verbleibende Ladevorgaenge — selbst angelegte
        # Wallboxen bleiben in jedem Fall unberuehrt.
        wallboxen = 0
        try:
            wallboxen = conn.execute(
                "DELETE FROM wallboxes WHERE name LIKE ? "
                "AND id NOT IN (SELECT DISTINCT wallbox_id FROM charging_sessions "
                "               WHERE wallbox_id IS NOT NULL)",
                (f"%{MARKE}%",)).rowcount
        except Exception:
            pass
        conn.commit()
    finally:
        conn.close()

    event_log_service.log_event("system", "info",
        f"Demodaten entfernt: {fahrten} Fahrten, {sessions} Ladevorgänge.")
    return {"ok": True, "fahrten": fahrten, "sessions": sessions,
            "kosten": kosten, "wallboxen": wallboxen}
