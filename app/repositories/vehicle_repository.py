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
    conn.commit()


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
