"""Fahrzeug-Repository – mehrere Autos je Person, E oder Verbrenner."""
from services.db_service import get_connection


def _ensure_tables(conn) -> None:
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS vehicles (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            person_id INTEGER,
            bezeichnung TEXT NOT NULL,
            kennzeichen TEXT,
            antrieb TEXT NOT NULL DEFAULT 'elektro',   -- 'elektro' | 'verbrenner'
            ist_standard INTEGER NOT NULL DEFAULT 0,
            aktiv INTEGER NOT NULL DEFAULT 1,
            created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
    """)
    # pkw_costs: vehicle_id-Spalte nachrüsten (Migration)
    cols = [r["name"] for r in conn.execute("PRAGMA table_info(pkw_costs)").fetchall()]
    if "vehicle_id" not in cols:
        try:
            conn.execute("ALTER TABLE pkw_costs ADD COLUMN vehicle_id INTEGER")
        except Exception:
            pass
    # car_allowance: vehicle_id-Spalte nachrüsten
    cols_ca = [r["name"] for r in conn.execute("PRAGMA table_info(car_allowance)").fetchall()]
    if "vehicle_id" not in cols_ca:
        try:
            conn.execute("ALTER TABLE car_allowance ADD COLUMN vehicle_id INTEGER")
        except Exception:
            pass
    # trips: vehicle_id-Spalte nachrüsten
    cols_t = [r["name"] for r in conn.execute("PRAGMA table_info(trips)").fetchall()]
    if "vehicle_id" not in cols_t:
        try:
            conn.execute("ALTER TABLE trips ADD COLUMN vehicle_id INTEGER")
        except Exception:
            pass
    # Fahrzeugdaten aus dem BMW-Archiv: Fahrgestellnummer, Kilometerstand,
    # Wartungstermine. Nachgeruestet, damit bestehende Datenbanken
    # weiterlaufen.
    cols_v = [r["name"] for r in conn.execute("PRAGMA table_info(vehicles)").fetchall()]
    for spalte, typ in [("vin", "TEXT"), ("km_stand", "INTEGER"),
                        # Nutzungsart: entscheidet, welche Anforderungen gelten.
                        #   'fahrtenbuch'  — lueckenlos nach § 6 Abs. 1 Nr. 4 EStG,
                        #                    Alternative zur 1-%-Regel beim Firmenwagen.
                        #                    Jede Fahrt zaehlt, auch private.
                        #   'reisekosten'  — nur dienstliche Fahrten als Nachweis fuer
                        #                    Kilometerpauschale oder Arbeitgeber-
                        #                    erstattung. Luecken sind unerheblich.
                        ("nutzungsart", "TEXT DEFAULT 'reisekosten'"),
                        ("km_stand_datum", "TEXT"),
                        ("hu_faellig", "TEXT"),        # Hauptuntersuchung
                        ("service_faellig", "TEXT"),   # naechster Service
                        ("bremsfluessigkeit", "TEXT"),
                        ("reifen_vorne", "TEXT"), ("reifen_hinten", "TEXT")]:
        if spalte not in cols_v:
            try:
                conn.execute(f"ALTER TABLE vehicles ADD COLUMN {spalte} {typ}")
            except Exception:
                pass
    conn.commit()


def anlegen_aus_bmw(daten: dict, person_id: int | None = None) -> int:
    """Legt ein Fahrzeug aus BMW-Archivdaten an oder aktualisiert es.

    Die Fahrgestellnummer ist der Schluessel: Gibt es den Wagen schon,
    werden nur die Werte aufgefrischt — sonst entstuende bei jedem Import
    ein neuer Eintrag.
    """
    vin = (daten.get("vin") or "").strip()
    conn = get_connection()
    try:
        _ensure_tables(conn)
        vorhanden = None
        if vin:
            vorhanden = conn.execute(
                "SELECT id FROM vehicles WHERE vin = ? LIMIT 1", (vin,)).fetchone()

        felder = {
            "vin": vin or None,
            "km_stand": daten.get("km_stand"),
            "km_stand_datum": daten.get("km_stand_datum"),
            "hu_faellig": daten.get("hu_faellig"),
            "service_faellig": daten.get("service_faellig"),
            "bremsfluessigkeit": daten.get("bremsfluessigkeit"),
            "reifen_vorne": daten.get("reifen_vorne"),
            "reifen_hinten": daten.get("reifen_hinten"),
        }

        if vorhanden:
            setz = ", ".join(f"{k} = ?" for k, v in felder.items() if v is not None)
            werte = [v for v in felder.values() if v is not None]
            if setz:
                conn.execute(f"UPDATE vehicles SET {setz} WHERE id = ?",
                             werte + [vorhanden["id"]])
                conn.commit()
            return vorhanden["id"]

        # Erstes Fahrzeug wird automatisch zum Standard
        anzahl = conn.execute("SELECT COUNT(*) c FROM vehicles").fetchone()["c"]
        spalten = ["person_id", "bezeichnung", "antrieb", "ist_standard"] + \
                  [k for k, v in felder.items() if v is not None]
        werte = [person_id, daten.get("bezeichnung") or "BMW", "elektro",
                 1 if anzahl == 0 else 0] + \
                [v for v in felder.values() if v is not None]
        platz = ", ".join("?" * len(spalten))
        cur = conn.execute(
            f"INSERT INTO vehicles ({', '.join(spalten)}) VALUES ({platz})", werte)
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def setze_stammdaten(vehicle_id: int, werte: dict) -> None:
    """Setzt Fahrgestellnummer, Kilometerstand und Termine.

    Nur die uebergebenen Felder werden geaendert — was nicht dabei ist,
    bleibt stehen. So ueberschreibt ein Teilupdate keine Angaben, die
    aus einer anderen Quelle stammen.
    """
    erlaubt = {"vin", "km_stand", "km_stand_datum", "hu_faellig", "nutzungsart",
               "service_faellig", "bremsfluessigkeit",
               "reifen_vorne", "reifen_hinten"}
    felder = {k: v for k, v in werte.items() if k in erlaubt and v not in (None, "")}
    if not felder:
        return
    conn = get_connection()
    try:
        _ensure_tables(conn)
        setz = ", ".join(f"{k} = ?" for k in felder)
        conn.execute(f"UPDATE vehicles SET {setz} WHERE id = ?",
                     list(felder.values()) + [vehicle_id])
        conn.commit()
    finally:
        conn.close()


def list_vehicles(person_id: int = None) -> list[dict]:
    conn = get_connection()
    try:
        _ensure_tables(conn)
        if person_id:
            rows = conn.execute(
                "SELECT * FROM vehicles WHERE aktiv=1 AND person_id=? ORDER BY ist_standard DESC, id",
                (person_id,)
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM vehicles WHERE aktiv=1 ORDER BY ist_standard DESC, id"
            ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_vehicle(vehicle_id: int) -> dict | None:
    conn = get_connection()
    try:
        _ensure_tables(conn)
        row = conn.execute("SELECT * FROM vehicles WHERE id=?", (vehicle_id,)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def insert_vehicle(person_id: int, bezeichnung: str, kennzeichen: str,
                    antrieb: str, ist_standard: bool = False) -> int:
    conn = get_connection()
    try:
        _ensure_tables(conn)
        if ist_standard:
            conn.execute("UPDATE vehicles SET ist_standard=0 WHERE person_id=?", (person_id,))
        cur = conn.execute(
            """INSERT INTO vehicles (person_id,bezeichnung,kennzeichen,antrieb,ist_standard)
               VALUES (?,?,?,?,?)""",
            (person_id, bezeichnung, kennzeichen or "", antrieb, 1 if ist_standard else 0)
        )
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def update_vehicle(vehicle_id: int, bezeichnung: str, kennzeichen: str,
                   antrieb: str, ist_standard: bool = False) -> None:
    conn = get_connection()
    try:
        _ensure_tables(conn)
        v = conn.execute("SELECT person_id FROM vehicles WHERE id=?", (vehicle_id,)).fetchone()
        if ist_standard and v:
            conn.execute("UPDATE vehicles SET ist_standard=0 WHERE person_id=?", (v["person_id"],))
        conn.execute(
            "UPDATE vehicles SET bezeichnung=?,kennzeichen=?,antrieb=?,ist_standard=? WHERE id=?",
            (bezeichnung, kennzeichen or "", antrieb, 1 if ist_standard else 0, vehicle_id)
        )
        conn.commit()
    finally:
        conn.close()


def delete_vehicle(vehicle_id: int) -> None:
    conn = get_connection()
    try:
        _ensure_tables(conn)
        conn.execute("UPDATE vehicles SET aktiv=0 WHERE id=?", (vehicle_id,))
        conn.commit()
    finally:
        conn.close()
