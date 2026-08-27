"""
Event-Log-Service — FA-LOG-01.
Timestamps werden in lokaler Zeit gespeichert (datetime.now() statt
SQLite CURRENT_TIMESTAMP, das immer UTC liefert — Deutschland UTC+2
im Sommer würde sonst 2 Stunden zu früh erscheinen).
"""

from datetime import datetime
from services.db_service import get_connection

MAX_ENTRIES = 500


def log_event(source: str, level: str, message: str) -> None:
    # Lokale Zeit verwenden statt SQLite CURRENT_TIMESTAMP (= immer UTC)
    local_ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    conn = get_connection()
    try:
        conn.execute(
            "INSERT INTO event_log (source, level, message, created_at) VALUES (?, ?, ?, ?)",
            (source, level, message, local_ts),
        )
        conn.execute(
            """DELETE FROM event_log WHERE id NOT IN (
                   SELECT id FROM event_log ORDER BY created_at DESC LIMIT ?
               )""",
            (MAX_ENTRIES,),
        )
        conn.commit()
    except Exception:
        pass
    finally:
        conn.close()


def list_events(limit: int = 150, source: str | None = None, level: str | None = None) -> list[dict]:
    conn = get_connection()
    try:
        query = "SELECT * FROM event_log WHERE 1=1"
        params: list = []
        if source:
            query += " AND source = ?"
            params.append(source)
        if level:
            query += " AND level = ?"
            params.append(level)
        query += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)
        rows = conn.execute(query, params).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()
