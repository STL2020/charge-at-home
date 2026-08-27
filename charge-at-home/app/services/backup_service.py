"""Datensicherung, Export und Zuruecksetzen.

Pflichtenheft: FA-COMP-04 (Backup), FA-COMP-05 (Datenexport).

Drei Aufgaben, die im Betrieb zusammengehoeren:

  Sicherung   Ein vollstaendiges Abbild als ZIP — Datenbank, Belege und
              Einstellungen. Wiederherstellbar auf einem anderen Rechner.
  Export      Die Nutzdaten als CSV fuer Steuerberater, Tabellenkalkulation
              oder als Nachweis. Lesbar ohne diese Software.
  Zuruecksetzen  Der Auslieferungszustand fuer eine Neuinstallation.

Grundsatz: Vor jedem zerstoerenden Schritt wird automatisch gesichert. Wer
zuruecksetzt, soll das rueckgaengig machen koennen — auch wenn er im Moment
des Klickens sicher war.
"""
from __future__ import annotations

import csv
import io
import json
import os
import shutil
import sqlite3
import zipfile
from datetime import datetime

from services import db_service
import services.event_log_service as event_log_service

# Tabellen, die in den CSV-Export gehoeren. Protokoll- und Systemtabellen
# bleiben aussen vor: Sie sind fuer die Nachvollziehbarkeit im Betrieb da,
# nicht fuer die Weitergabe.
EXPORT_TABELLEN = [
    ("charging_sessions", "Ladevorgaenge"),
    ("trips", "Fahrten"),
    ("wallboxes", "Wallboxen"),
    ("vehicles", "Fahrzeuge"),
    ("persons", "Personen"),
    ("pkw_costs", "Fahrzeugkosten"),
    ("car_allowance", "Arbeitgeberzuschuesse"),
    ("bmw_trips", "BMW-Importe"),
]


def _datenverzeichnis() -> str:
    return os.path.dirname(db_service.get_db_path())


def _zeitstempel() -> str:
    return datetime.now().strftime("%Y-%m-%d_%H%M")


# ── Sicherung ──────────────────────────────────────────────────────────────

def erstelle_sicherung(mit_belegen: bool = True) -> tuple[bytes, str]:
    """Vollstaendiges Abbild als ZIP.

    Die Datenbank wird ueber die SQLite-Backup-Schnittstelle kopiert statt
    ueber das Dateisystem: Nur so ist sie auch dann konsistent, wenn gerade
    geschrieben wird (etwa waehrend eines laufenden Ladevorgangs)."""
    puffer = io.BytesIO()
    verzeichnis = _datenverzeichnis()

    with zipfile.ZipFile(puffer, "w", zipfile.ZIP_DEFLATED) as z:
        # Datenbank konsistent kopieren
        temp_db = os.path.join(verzeichnis, f"_sicherung_{os.getpid()}.db")
        try:
            quelle = sqlite3.connect(db_service.get_db_path())
            ziel = sqlite3.connect(temp_db)
            with ziel:
                quelle.backup(ziel)
            ziel.close()
            quelle.close()
            z.write(temp_db, "charging.db")
        finally:
            if os.path.exists(temp_db):
                os.remove(temp_db)

        # Erzeugte Belege
        belege = os.path.join(verzeichnis, "documents")
        if mit_belegen and os.path.isdir(belege):
            for wurzel, _, dateien in os.walk(belege):
                for name in dateien:
                    voll = os.path.join(wurzel, name)
                    z.write(voll, os.path.join("documents",
                                               os.path.relpath(voll, belege)))

        # Begleitzettel: erklaert im Zweifel Monate spaeter, was das ZIP ist
        z.writestr("SICHERUNG.txt", _begleitzettel())

    daten = puffer.getvalue()
    name = f"eChargeHome_Sicherung_{_zeitstempel()}.zip"
    event_log_service.log_event("system", "info",
        f"Sicherung erstellt ({len(daten) // 1024} KB).")
    return daten, name


def _begleitzettel() -> str:
    kennzahlen = zaehle_datensaetze()
    zeilen = [f"  {name:<26} {anzahl}" for name, anzahl in kennzahlen.items()]
    return (
        "eCharge@Home — Datensicherung\n"
        "=" * 46 + "\n\n"
        f"Erstellt am:  {datetime.now().strftime('%d.%m.%Y um %H:%M Uhr')}\n\n"
        "Inhalt\n"
        "------\n"
        "  charging.db    Vollstaendige Datenbank\n"
        "  documents/     Erzeugte PDF-Belege\n\n"
        "Enthaltene Datensaetze\n"
        "----------------------\n" + "\n".join(zeilen) + "\n\n"
        "Wiederherstellen\n"
        "----------------\n"
        "  In der Anwendung unter Einstellungen -> System -> Sicherung\n"
        "  einspielen. Alternativ charging.db von Hand in den Ordner data/\n"
        "  kopieren, waehrend die Anwendung beendet ist.\n\n"
        "Hinweis zum Verschluesselungs-Schluessel\n"
        "----------------------------------------\n"
        "  Gespeicherte Zugangsdaten (Loxone, BMW) sind verschluesselt. Der\n"
        "  Schluessel liegt AUSSERHALB dieses Archivs im Benutzerverzeichnis.\n"
        "  Auf einem anderen Rechner muessen diese Zugangsdaten deshalb neu\n"
        "  eingegeben werden — alle uebrigen Daten bleiben vollstaendig.\n"
    )


def zaehle_datensaetze() -> dict:
    """Anzahl je Tabelle — fuer Begleitzettel und Anzeige."""
    conn = db_service.get_connection()
    ergebnis = {}
    try:
        for tabelle, name in EXPORT_TABELLEN:
            try:
                anzahl = conn.execute(f"SELECT COUNT(*) c FROM {tabelle}").fetchone()["c"]
                ergebnis[name] = anzahl
            except sqlite3.OperationalError:
                continue
    finally:
        try:
            conn.execute("PRAGMA foreign_keys = ON")
        except Exception:
            pass
        conn.close()
    return ergebnis


def spiele_sicherung_ein(zip_daten: bytes) -> dict:
    """Stellt eine Sicherung wieder her.

    Der bestehende Stand wird vorher automatisch gesichert — ein
    versehentliches Einspielen soll nicht das Ende der Daten bedeuten."""
    verzeichnis = _datenverzeichnis()
    try:
        with zipfile.ZipFile(io.BytesIO(zip_daten)) as z:
            namen = z.namelist()
            if "charging.db" not in namen:
                return {"ok": False,
                        "meldung": "Das Archiv enthält keine charging.db — "
                                   "ist es eine Sicherung dieser Anwendung?"}

            # Sicherheitskopie des aktuellen Stands
            vorher, _ = erstelle_sicherung(mit_belegen=False)
            notfall = os.path.join(verzeichnis,
                                   f"vor_wiederherstellung_{_zeitstempel()}.zip")
            with open(notfall, "wb") as f:
                f.write(vorher)

            # Datenbank pruefen, bevor sie die bestehende ersetzt
            temp = os.path.join(verzeichnis, "_pruefung.db")
            with open(temp, "wb") as f:
                f.write(z.read("charging.db"))
            try:
                pruef = sqlite3.connect(temp)
                tabellen = {r[0] for r in pruef.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'")}
                pruef.close()
                if "charging_sessions" not in tabellen:
                    os.remove(temp)
                    return {"ok": False,
                            "meldung": "Die Datenbank im Archiv ist unvollständig."}
            except sqlite3.DatabaseError:
                os.remove(temp)
                return {"ok": False, "meldung": "Die Datenbank im Archiv ist beschädigt."}

            # Erst jetzt ersetzen
            db_service.close_all()
            ziel = db_service.get_db_path()
            for endung in ("", "-wal", "-shm"):
                pfad = ziel + endung
                if os.path.exists(pfad):
                    os.remove(pfad)
            shutil.move(temp, ziel)

            # Belege zurückspielen
            wieder = 0
            for name in namen:
                if name.startswith("documents/") and not name.endswith("/"):
                    zielpfad = os.path.join(verzeichnis, name)
                    os.makedirs(os.path.dirname(zielpfad), exist_ok=True)
                    with open(zielpfad, "wb") as f:
                        f.write(z.read(name))
                    wieder += 1

        db_service.init_db()
        event_log_service.log_event("system", "info",
            f"Sicherung eingespielt ({wieder} Belege). "
            f"Vorheriger Stand gesichert unter {os.path.basename(notfall)}.")
        return {"ok": True, "belege": wieder,
                "notfallkopie": os.path.basename(notfall),
                "datensaetze": zaehle_datensaetze()}
    except zipfile.BadZipFile:
        return {"ok": False, "meldung": "Die Datei ist kein gültiges ZIP-Archiv."}
    except Exception as e:
        return {"ok": False, "meldung": f"Wiederherstellung fehlgeschlagen ({type(e).__name__})."}


# ── Export ─────────────────────────────────────────────────────────────────

def exportiere_csv() -> tuple[bytes, str]:
    """Alle Nutzdaten als CSV-Dateien in einem ZIP.

    Mit Semikolon getrennt und BOM versehen, damit Excel die Datei ohne
    Nachfragen korrekt oeffnet — der haeufigste Stolperstein beim Weitergeben
    an Steuerberater."""
    puffer = io.BytesIO()
    conn = db_service.get_connection()
    try:
        with zipfile.ZipFile(puffer, "w", zipfile.ZIP_DEFLATED) as z:
            for tabelle, name in EXPORT_TABELLEN:
                try:
                    zeilen = conn.execute(f"SELECT * FROM {tabelle}").fetchall()
                except sqlite3.OperationalError:
                    continue
                if not zeilen:
                    continue
                text = io.StringIO()
                schreiber = csv.writer(text, delimiter=";", quoting=csv.QUOTE_MINIMAL)
                schreiber.writerow(zeilen[0].keys())
                for zeile in zeilen:
                    schreiber.writerow(list(zeile))
                z.writestr(f"{name}.csv", "\ufeff" + text.getvalue())
            z.writestr("LIESMICH.txt", _export_hinweis())
    finally:
        conn.close()

    daten = puffer.getvalue()
    event_log_service.log_event("system", "info", "Datenexport als CSV erstellt.")
    return daten, f"eChargeHome_Export_{_zeitstempel()}.zip"


def _export_hinweis() -> str:
    return (
        "eCharge@Home — Datenexport\n"
        "=" * 46 + "\n\n"
        f"Erstellt am: {datetime.now().strftime('%d.%m.%Y um %H:%M Uhr')}\n\n"
        "Format\n"
        "------\n"
        "  Semikolon als Trennzeichen, UTF-8 mit BOM.\n"
        "  Oeffnet sich in Excel und LibreOffice ohne Nachfrage.\n\n"
        "Hinweise zu den Werten\n"
        "----------------------\n"
        "  Energiemengen stehen in Wattstunden (Wh): 13260 = 13,26 kWh.\n"
        "  Bei Ladevorgaengen ergibt sich die Menge aus\n"
        "  meter_stop_wh minus meter_start_wh.\n"
        "  Beginnt meter_start_wh bei 0, liegt kein absoluter Zaehlerstand\n"
        "  vor — die Menge stimmt, ein Zaehlernachweis fehlt jedoch.\n\n"
        "  Bei Fahrten unterscheidet die Spalte 'fahrtart' zwischen\n"
        "  dienstlich, privat und offen. Nur dienstliche Fahrten sind\n"
        "  abrechnungsrelevant.\n"
    )


# ── Zuruecksetzen ──────────────────────────────────────────────────────────

def setze_zurueck(behalte_stammdaten: bool = True, bereiche: dict | None = None) -> dict:
    """Versetzt ausgewaehlte Bereiche in den Auslieferungszustand.

    `bereiche` benennt einzeln, was entfernt werden soll:

        bewegungsdaten  Ladevorgaenge, Fahrten, Belege, Protokolle
        wallboxen       Wallboxen samt Zugangsdaten und Messwerten
        fahrzeuge       Fahrzeuge und Fahrzeugkosten
        personen        Personen, Arbeitgeber, Car Allowance
        einstellungen   Tarife, Adressen, Anbindungen, Lizenz

    Ohne Angabe entscheidet `behalte_stammdaten` wie bisher — so bleiben
    aeltere Aufrufe gueltig.

    Vor dem Loeschen wird immer gesichert."""
    verzeichnis = _datenverzeichnis()
    sicherung, _ = erstelle_sicherung()
    pfad = os.path.join(verzeichnis, f"vor_zuruecksetzen_{_zeitstempel()}.zip")
    with open(pfad, "wb") as f:
        f.write(sicherung)

    gruppen = {
        "bewegungsdaten": ["charging_sessions", "trips", "bmw_trips", "documents",
                           "event_log", "audit_log", "billing_entries",
                           "ocpp_message_counts", "loxone_last_charge_log",
                           "loxone_log_reconcile_state"],
        "wallboxen":     ["wallbox_live_metrics", "wallbox_status",
                          "loxone_wallbox_config", "ocpp_client_config",
                          "loxone_poll_state", "wallboxes"],
        "fahrzeuge":     ["pkw_costs", "vehicles"],
        "personen":      ["car_allowance", "employers", "persons"],
        "einstellungen": ["tariffs"],
    }

    if bereiche is None:
        # Alter Aufrufweg: Bewegungsdaten immer, Stammdaten je nach Schalter
        bereiche = {"bewegungsdaten": True}
        if not behalte_stammdaten:
            bereiche.update({"wallboxen": True, "fahrzeuge": True,
                             "personen": True, "einstellungen": True})

    # Vollstaendig heisst: alles angehakt. Nur dann wird die Einrichtung
    # zurueckgesetzt und die Anwendung startet wie beim ersten Mal.
    vollstaendig = all(bereiche.get(k) for k in gruppen)

    conn = db_service.get_connection()
    geloescht = {}
    probleme = []
    try:
        tabellen = {r["name"] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        zu_leeren = []
        for name, liste in gruppen.items():
            if bereiche.get(name):
                zu_leeren += liste

        # Fremdschluesselpruefung waehrend des Loeschens aus: Sonst haengt der
        # Erfolg an der Reihenfolge — eine Tabelle mit Verweis auf 'wallboxes'
        # laesst sich nicht leeren, solange dort noch Zeilen stehen. Genau
        # daran scheiterten loxone_poll_state und ocpp_client_config.
        conn.execute("PRAGMA foreign_keys = OFF")

        # Jede Tabelle einzeln: Scheitert eine — etwa weil ein Hintergrunddienst
        # gerade schreibt —, sollen die uebrigen trotzdem geleert werden. Ein
        # halb ausgefuehrtes Zuruecksetzen mit klarer Meldung ist besser als
        # eine Fehlermeldung, nach der alles unveraendert dasteht.
        for tabelle in zu_leeren:
            if tabelle not in tabellen:
                continue
            try:
                anzahl = conn.execute(f"SELECT COUNT(*) c FROM {tabelle}").fetchone()["c"]
                conn.execute(f"DELETE FROM {tabelle}")
                conn.commit()
                if anzahl:
                    geloescht[tabelle] = anzahl
            except Exception:
                # Zweiter Versuch nach kurzer Pause: Ein Hintergrunddienst kann
                # die Tabelle gerade gesperrt haben.
                try:
                    import time as _t
                    _t.sleep(0.4)
                    anzahl = conn.execute(f"SELECT COUNT(*) c FROM {tabelle}").fetchone()["c"]
                    conn.execute(f"DELETE FROM {tabelle}")
                    conn.commit()
                    if anzahl:
                        geloescht[tabelle] = anzahl
                except Exception as e2:
                    conn.rollback()
                    probleme.append(f"{tabelle} ({type(e2).__name__})")

        try:
            conn.execute("DELETE FROM app_settings WHERE key LIKE 'cardata_stand_%'")
            if vollstaendig:
                # Vollstaendiges Zuruecksetzen heisst: Auslieferungszustand.
                # Dazu gehoert, dass die Einrichtung erneut durchlaufen wird —
                # Haftungshinweis, Name, Fahrzeug, Datenquelle. Sonst stuende
                # der naechste Nutzer vor einer leeren Anwendung ohne Hinweis,
                # was zuerst zu tun ist.
                conn.execute("DELETE FROM app_settings WHERE key LIKE 'cardata_%'")
                conn.execute("UPDATE users_config SET name = '' WHERE id = 1")
                for schluessel in ("setup_complete", "disclaimer_accepted",
                                   "heim_adresse", "contract_kwh_price",
                                   "lizenz_key", "lizenz_geprueft_am",
                                   "lizenz_kaeufer", "lizenz_gekauft_am"):
                    conn.execute("DELETE FROM app_settings WHERE key = ?", (schluessel,))
            conn.commit()
        except Exception as e:
            probleme.append(f"Einstellungen ({type(e).__name__})")
    finally:
        conn.close()

    # VACUUM verkleinert die Datei, ist aber verzichtbar. Es scheitert, sobald
    # ein Hintergrunddienst eine Verbindung offen haelt — das darf den Vorgang
    # nicht zum Fehlschlag machen.
    try:
        conn2 = db_service.get_connection()
        conn2.execute("VACUUM")
        conn2.close()
    except Exception:
        pass

    # Erzeugte Belege entfernen. Einzelne gesperrte Dateien (etwa eine im
    # Betrachter geoeffnete PDF) sollen den Rest nicht blockieren.
    belege = os.path.join(verzeichnis, "documents")
    if os.path.isdir(belege):
        offen = 0
        for wurzel, _, dateien in os.walk(belege):
            for name in dateien:
                try:
                    os.remove(os.path.join(wurzel, name))
                except Exception:
                    offen += 1
        if offen:
            probleme.append(f"{offen} Beleg(e) waren geöffnet")

    anzahl_gesamt = sum(geloescht.values())
    meldung = (f"Anwendung zurückgesetzt "
               f"({'Stammdaten behalten' if behalte_stammdaten else 'vollständig'}): "
               f"{anzahl_gesamt} Datensätze entfernt. Sicherung: {os.path.basename(pfad)}")
    if probleme:
        meldung += " — nicht möglich bei: " + ", ".join(probleme)
    event_log_service.log_event("system", "warning", meldung)

    return {"ok": True, "geloescht": geloescht, "anzahl": anzahl_gesamt,
            "sicherung": os.path.basename(pfad),
            "stammdaten_behalten": behalte_stammdaten,
            # Sagt der Oberflaeche, dass die Einrichtung neu zu durchlaufen ist
            "setup_erforderlich": vollstaendig,
            "probleme": probleme}
