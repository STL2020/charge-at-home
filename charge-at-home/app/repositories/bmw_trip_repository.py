"""Datenzugriff fuer importierte BMW-ConnectedDrive-Fahrten (Sprint 6).

Die Rohfahrten liegen bewusst in einer eigenen Tabelle `bmw_trips` und nicht
direkt in `trips`: Der Import holt ALLE Fahrten des Fahrzeugs — auch private.
Erst wenn der Nutzer eine Fahrt als dienstlich einstuft, entsteht daraus ein
abrechnungsrelevanter Eintrag in `trips`. So bleibt der Gesamtkilometer-Nachweis
vollstaendig, ohne dass private Fahrten in die Abrechnung geraten.
"""
from __future__ import annotations

from services.db_service import get_connection


def _ensure_table(conn) -> None:
    """Legt Tabelle und Index an, falls die Installation aelter ist als
    Sprint 6 (schema.sql wird nur bei Neuanlage vollstaendig ausgefuehrt)."""
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS bmw_trips (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            vehicle_id INTEGER,
            bmw_trip_id TEXT NOT NULL UNIQUE,
            start_time DATETIME,
            end_time DATETIME,
            start_mileage INTEGER,
            end_mileage INTEGER,
            distance_km REAL NOT NULL DEFAULT 0,
            start_address TEXT,
            end_address TEXT,
            category TEXT NOT NULL DEFAULT 'UNVERARBEITET'
                CHECK (category IN ('UNVERARBEITET', 'DIENSTLICH', 'PRIVAT')),
            trip_id INTEGER,
            imported_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE INDEX IF NOT EXISTS idx_bmw_trips_category ON bmw_trips(category);
    """)
    conn.commit()


def bekannte_trip_ids(user_id: int) -> set[str]:
    """IDs bereits importierter Fahrten — Grundlage der Duplikatspruefung.

    Beruecksichtigt nur Eintraege, deren Fahrt noch in der Fahrtenliste steht.
    Wurde sie dort geloescht, gilt sie als nicht mehr vorhanden und darf erneut
    importiert werden — sonst waere ein versehentlich geloeschter Bestand
    dauerhaft verloren, obwohl die Quelldaten noch existieren."""
    conn = get_connection()
    try:
        _ensure_table(conn)
        rows = conn.execute(
            """SELECT b.bmw_trip_id FROM bmw_trips b
               WHERE b.user_id = ?
                 AND (b.trip_id IS NULL
                      OR EXISTS (SELECT 1 FROM trips t WHERE t.id = b.trip_id))""",
            (user_id,)).fetchall()
        return {r["bmw_trip_id"] for r in rows}
    finally:
        conn.close()


def raeume_verwaiste_auf(user_id: int) -> int:
    """Entfernt Referenzen auf inzwischen geloeschte Fahrten.

    Zwei Faelle:
      * Die verknuepfte Fahrt wurde geloescht.
      * Es besteht gar keine Verknuepfung (Altbestand aus frueheren Versionen)
        und der Nutzer hat ueberhaupt keine Fahrten mehr — dann kann die
        Referenz nur verwaist sein.

    Ohne diese Bereinigung liesse sich ein geloeschter Bestand nie wieder
    importieren: Jede Fahrt gaelte dauerhaft als 'bereits bekannt'."""
    conn = get_connection()
    try:
        _ensure_table(conn)
        entfernt = conn.execute(
            """DELETE FROM bmw_trips
               WHERE user_id = ? AND trip_id IS NOT NULL
                 AND NOT EXISTS (SELECT 1 FROM trips t WHERE t.id = bmw_trips.trip_id)""",
            (user_id,)).rowcount

        # Altbestand ohne Verknuepfung: nur bereinigen, wenn gar keine Fahrt
        # mehr existiert — sonst koennten noch gueltige Referenzen dabei sein.
        rest = conn.execute("SELECT COUNT(*) c FROM trips WHERE user_id = ?",
                            (user_id,)).fetchone()["c"]
        if rest == 0:
            entfernt += conn.execute(
                "DELETE FROM bmw_trips WHERE user_id = ? AND trip_id IS NULL",
                (user_id,)).rowcount
        conn.commit()
        return entfernt
    finally:
        conn.close()


def insert_trips(user_id: int, trips: list[dict], vehicle_id: int | None = None) -> int:
    """Speichert neue Fahrten. Bereits vorhandene bmw_trip_id werden dank
    UNIQUE-Constraint uebersprungen (INSERT OR IGNORE), damit ein erneuter
    Sync keine Doubletten erzeugt. Rueckgabe: Anzahl tatsaechlich neuer Zeilen."""
    if not trips:
        return 0
    conn = get_connection()
    try:
        _ensure_table(conn)
        vorher = conn.execute(
            "SELECT COUNT(*) c FROM bmw_trips WHERE user_id = ?", (user_id,)
        ).fetchone()["c"]
        conn.executemany(
            """INSERT OR IGNORE INTO bmw_trips
               (user_id, vehicle_id, bmw_trip_id, start_time, end_time,
                start_mileage, end_mileage, distance_km, start_address, end_address)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            [(user_id, vehicle_id, t["trip_id"], t.get("start_time"), t.get("end_time"),
              t.get("start_mileage"), t.get("end_mileage"), t.get("distance_km", 0),
              t.get("start_address"), t.get("end_address")) for t in trips])
        conn.commit()
        nachher = conn.execute(
            "SELECT COUNT(*) c FROM bmw_trips WHERE user_id = ?", (user_id,)
        ).fetchone()["c"]
        return nachher - vorher
    finally:
        conn.close()


def list_trips(user_id: int, category: str | None = None, limit: int = 200) -> list[dict]:
    conn = get_connection()
    try:
        _ensure_table(conn)
        if category:
            rows = conn.execute(
                """SELECT * FROM bmw_trips WHERE user_id = ? AND category = ?
                   ORDER BY start_time DESC LIMIT ?""",
                (user_id, category, limit)).fetchall()
        else:
            rows = conn.execute(
                """SELECT * FROM bmw_trips WHERE user_id = ?
                   ORDER BY start_time DESC LIMIT ?""",
                (user_id, limit)).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_trip(trip_id: int) -> dict | None:
    conn = get_connection()
    try:
        _ensure_table(conn)
        row = conn.execute("SELECT * FROM bmw_trips WHERE id = ?", (trip_id,)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def set_category(bmw_id: int, category: str, trip_id: int | None = None) -> None:
    """Klassifizierung setzen. Bei DIENSTLICH wird zusaetzlich die id des
    erzeugten trips-Eintrags vermerkt, damit der Bezug nachvollziehbar bleibt."""
    conn = get_connection()
    try:
        _ensure_table(conn)
        conn.execute(
            "UPDATE bmw_trips SET category = ?, trip_id = ? WHERE id = ?",
            (category, trip_id, bmw_id))
        conn.commit()
    finally:
        conn.close()


def zaehle_offen(user_id: int) -> int:
    """Anzahl unverarbeiteter Fahrten — fuer die Hinweiskarte im Dashboard."""
    conn = get_connection()
    try:
        _ensure_table(conn)
        return conn.execute(
            "SELECT COUNT(*) c FROM bmw_trips WHERE user_id = ? AND category = 'UNVERARBEITET'",
            (user_id,)).fetchone()["c"]
    finally:
        conn.close()


def statistik(user_id: int) -> dict:
    """Kilometer je Kategorie — Grundlage fuer den Gesamtkilometer-Nachweis:
    private Fahrten zaehlen fuer die Jahresfahrleistung, auch wenn sie nicht
    abgerechnet werden."""
    conn = get_connection()
    try:
        _ensure_table(conn)
        rows = conn.execute(
            """SELECT category, COUNT(*) AS anzahl,
                      COALESCE(SUM(distance_km), 0) AS km
               FROM bmw_trips WHERE user_id = ? GROUP BY category""",
            (user_id,)).fetchall()
        d = {r["category"]: {"anzahl": r["anzahl"], "km": round(r["km"], 1)} for r in rows}
        for k in ("UNVERARBEITET", "DIENSTLICH", "PRIVAT"):
            d.setdefault(k, {"anzahl": 0, "km": 0.0})
        d["gesamt_km"] = round(sum(v["km"] for k, v in d.items() if k != "gesamt_km"), 1)
        return d
    finally:
        conn.close()


def km_bereich(user_id: int, von: str | None = None, bis: str | None = None) -> dict:
    """Kilometerstaende am Anfang und Ende eines Zeitraums.

    Grundlage sind die vom Fahrzeug gemeldeten Tachostaende der importierten
    Fahrten. Fuer das Fahrtenbuch ist das ein belastbarer Nachweis: Die Werte
    stammen aus dem Bordcomputer, nicht aus einer Schaetzung."""
    conn = get_connection()
    try:
        _ensure_table(conn)
        sql = """SELECT MIN(start_mileage) AS km_min, MAX(end_mileage) AS km_max,
                        COUNT(*) AS anzahl,
                        MIN(date(start_time)) AS erste, MAX(date(end_time)) AS letzte
                 FROM bmw_trips
                 WHERE user_id = ? AND start_mileage IS NOT NULL
                   AND end_mileage IS NOT NULL"""
        params: list = [user_id]
        if von:
            sql += " AND date(start_time) >= date(?)"
            params.append(von)
        if bis:
            sql += " AND date(end_time) <= date(?)"
            params.append(bis)
        r = conn.execute(sql, params).fetchone()
        if not r or r["anzahl"] == 0:
            return {"vorhanden": False}
        return {
            "vorhanden": True,
            "km_start": r["km_min"],
            "km_ende": r["km_max"],
            "gefahren": (r["km_max"] or 0) - (r["km_min"] or 0),
            "anzahl_fahrten": r["anzahl"],
            "erste_fahrt": r["erste"],
            "letzte_fahrt": r["letzte"],
        }
    finally:
        conn.close()



def insert_trip_ref(user_id: int, trip: dict, trip_id: int,
                    vehicle_id: int | None = None) -> bool:
    """Legt EINE Referenz an und verknuepft sie direkt mit der Fahrt.

    Ersetzt den frueheren Umweg ueber insert_trips() + nachtraegliches Suchen:
    Dabei wurde die zuletzt eingefuegte Zeile ueber die Sortierung gesucht und
    haeufig die falsche erwischt, sodass die Verknuepfung leer blieb. Ohne sie
    liess sich nach dem Loeschen einer Fahrt nichts mehr neu importieren."""
    conn = get_connection()
    try:
        _ensure_table(conn)
        cur = conn.execute(
            """INSERT OR IGNORE INTO bmw_trips
               (user_id, vehicle_id, bmw_trip_id, start_time, end_time,
                start_mileage, end_mileage, distance_km, start_address, end_address,
                category, trip_id)
               VALUES (?,?,?,?,?,?,?,?,?,?,'UNVERARBEITET',?)""",
            (user_id, vehicle_id, trip["trip_id"], trip.get("start_time"),
             trip.get("end_time"), trip.get("start_mileage"), trip.get("end_mileage"),
             trip.get("distance_km", 0), trip.get("start_address"),
             trip.get("end_address"), trip_id))
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()
