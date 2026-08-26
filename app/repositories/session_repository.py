"""Repository fuer charging_sessions. Reine SQL-Zugriffsschicht (§ 8.1)."""

from services.db_service import get_connection


def insert_session(
    wallbox_id: int,
    user_id: int,
    source: str,
    start_timestamp: str,
    end_timestamp: str | None,
    meter_start_wh: int,
    meter_stop_wh: int | None,
    price_per_kwh: float,
    rfid_tag: str | None = None,
    classification: str | None = None,
    status: str = "closed",
    charging_location: str = "zuhause",
    charging_location_note: str | None = None,
) -> int:
    conn = get_connection()
    try:
        cur = conn.execute(
            """INSERT INTO charging_sessions
               (wallbox_id, user_id, source, start_timestamp, end_timestamp,
                meter_start_wh, meter_stop_wh, price_per_kwh, rfid_tag, classification, status,
                charging_location, charging_location_note)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (wallbox_id, user_id, source, start_timestamp, end_timestamp,
             meter_start_wh, meter_stop_wh, price_per_kwh, rfid_tag, classification, status,
             charging_location, charging_location_note),
        )
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def list_sessions(
    user_id: int,
    period_start: str | None = None,
    period_end: str | None = None,
    wallbox_id: int | None = None,
) -> list[dict]:
    query = """
        SELECT cs.*, wb.name AS wallbox_name, wb.location AS wallbox_location
        FROM charging_sessions cs
        JOIN wallboxes wb ON wb.id = cs.wallbox_id
        WHERE cs.user_id = ?
    """
    params: list = [user_id]
    if period_start:
        query += " AND date(cs.start_timestamp) >= date(?)"
        params.append(period_start)
    if period_end:
        query += " AND date(cs.start_timestamp) <= date(?)"
        params.append(period_end)
    if wallbox_id:
        query += " AND cs.wallbox_id = ?"
        params.append(wallbox_id)
    # datetime() normalisiert beide Formate ('2026-08-22T19:32:49Z' und
    # '2026-08-22 19:32:49') auf dieselbe SQLite-Darstellung bevor sortiert
    # wird — verhindert, dass ISO-Timestamps (OCPP) vor regulaeren Timestamps
    # (Loxone-API) erscheinen, weil 'T' > ' ' im ASCII-Vergleich.
    query += " ORDER BY datetime(cs.start_timestamp) DESC"

    conn = get_connection()
    try:
        return [dict(r) for r in conn.execute(query, params).fetchall()]
    finally:
        conn.close()


def get_session(session_id: int) -> dict | None:
    conn = get_connection()
    try:
        row = conn.execute("SELECT * FROM charging_sessions WHERE id = ?", (session_id,)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def get_open_session_for_wallbox(wallbox_id: int) -> dict | None:
    """Fuer polling-basierte Quellen (FA-LS-10): liefert die aktuell offene Session dieser Wallbox, falls vorhanden."""
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT * FROM charging_sessions WHERE wallbox_id = ? AND status = 'open' ORDER BY start_timestamp DESC LIMIT 1",
            (wallbox_id,),
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def get_any_open_session(user_id: int) -> dict | None:
    """Liefert die aktuell laufende Session ueber ALLE Wallboxen hinweg (egal ob
    OCPP oder direkte API) — fuer eine einheitliche "Aktuelle Ladesession"-Karte
    im Dashboard, unabhaengig vom Datenweg."""
    conn = get_connection()
    try:
        row = conn.execute(
            """SELECT cs.*, wb.name AS wallbox_name, wb.location AS wallbox_location
               FROM charging_sessions cs JOIN wallboxes wb ON wb.id = cs.wallbox_id
               WHERE cs.user_id = ? AND cs.status = 'open'
               ORDER BY cs.start_timestamp DESC LIMIT 1""",
            (user_id,),
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def session_exists_at_end(wallbox_id: int, end_timestamp: str) -> bool:
    """Fuer Log-Datei-Import (FA-LS-10): verhindert Duplikate bei wiederholtem
    Import derselben Datei — prueft, ob bereits eine Session mit exakt dieser
    Endzeit an dieser Wallbox existiert."""
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT 1 FROM charging_sessions WHERE wallbox_id = ? AND end_timestamp = ? LIMIT 1",
            (wallbox_id, end_timestamp),
        ).fetchone()
        return row is not None
    finally:
        conn.close()


def session_exists_near_start(wallbox_id: int, start_minute: str) -> bool:
    """Fuer den BMW-Ladehistorie-Import (services/cardata_service.py):
    BMW rundet Zeitstempel auf die Minute, unsere eigenen Quellen sind
    sekundengenau. Prueft deshalb per Minutenvergleich (Praefix-Match auf
    'YYYY-MM-DD HH:MM'), ob fuer diese Wallbox bereits eine Session mit
    Start in derselben Minute existiert — unabhaengig von der Quelle."""
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT 1 FROM charging_sessions WHERE wallbox_id = ? AND start_timestamp LIKE ? LIMIT 1",
            (wallbox_id, start_minute + "%"),
        ).fetchone()
        return row is not None
    finally:
        conn.close()


def list_closed_sessions_for_wallbox(wallbox_id: int) -> list[dict]:
    """Fuer den OCPP-Client-Relay (services/ocpp_client_service.py) — user-
    unabhaengige Abfrage aller ABGESCHLOSSENEN Sessions einer Wallbox,
    unabhaengig davon welchem Nutzer sie zugeordnet sind (Einzelnutzer-Tool,
    aber die bestehende list_sessions() verlangt zwingend eine user_id)."""
    conn = get_connection()
    try:
        rows = conn.execute(
            """SELECT * FROM charging_sessions
               WHERE wallbox_id = ? AND status = 'closed' AND meter_stop_wh IS NOT NULL
               ORDER BY id ASC""",
            (wallbox_id,),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def set_charging_location(session_id: int, new_value: str) -> bool:
    """Fuer manuelle Korrektur, falls die automatische Zuhause/Extern-
    Erkennung im Einzelfall falsch liegt."""
    if new_value not in ("zuhause", "extern"):
        raise ValueError(f"Ungueltiger Ladeort: {new_value!r}")
    conn = get_connection()
    try:
        cur = conn.execute(
            "UPDATE charging_sessions SET charging_location = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (new_value, session_id),
        )
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def update_classification(session_id: int, new_classification: str) -> None:
    conn = get_connection()
    try:
        conn.execute(
            "UPDATE charging_sessions SET classification = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (new_classification, session_id),
        )
        conn.commit()
    finally:
        conn.close()


def update_meter_stop_only(session_id: int, meter_stop_wh: int) -> None:
    """§ 6.1: MeterValues aktualisiert bei JEDEM Sample meter_stop_wh der offenen Session,
    damit ein Verbindungsabbruch vor StopTransaction nicht zu einer 0-kWh-Leiche fuehrt."""
    conn = get_connection()
    try:
        conn.execute(
            "UPDATE charging_sessions SET meter_stop_wh = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ? AND status = 'open'",
            (meter_stop_wh, session_id),
        )
        conn.commit()
    finally:
        conn.close()


def close_session(session_id: int, meter_stop_wh: int, end_timestamp: str) -> None:
    conn = get_connection()
    try:
        conn.execute(
            """UPDATE charging_sessions
               SET meter_stop_wh = ?, end_timestamp = ?, status = 'closed', updated_at = CURRENT_TIMESTAMP
               WHERE id = ?""",
            (meter_stop_wh, end_timestamp, session_id),
        )
        conn.commit()
    finally:
        conn.close()


def update_session(
    session_id: int,
    wallbox_id: int,
    start_timestamp: str,
    end_timestamp: str | None,
    meter_start_wh: int,
    meter_stop_wh: int | None,
    rfid_tag: str | None,
) -> None:
    conn = get_connection()
    try:
        conn.execute(
            """UPDATE charging_sessions
               SET wallbox_id = ?, start_timestamp = ?, end_timestamp = ?,
                   meter_start_wh = ?, meter_stop_wh = ?, rfid_tag = ?,
                   status = ?, updated_at = CURRENT_TIMESTAMP
               WHERE id = ?""",
            (wallbox_id, start_timestamp, end_timestamp, meter_start_wh, meter_stop_wh,
             rfid_tag, "closed" if end_timestamp else "open", session_id),
        )
        conn.commit()
    finally:
        conn.close()


def delete_session(session_id: int) -> None:
    conn = get_connection()
    try:
        conn.execute("DELETE FROM charging_sessions WHERE id = ?", (session_id,))
        conn.commit()
    finally:
        conn.close()


def get_last_meter_for_wallbox_name(wallbox_name: str) -> int | None:
    """Letzter bekannter Zaehlerstand (Ende, sonst Start) fuer eine Wallbox nach Name."""
    conn = get_connection()
    try:
        row = conn.execute(
            """SELECT cs.meter_stop_wh, cs.meter_start_wh
               FROM charging_sessions cs
               JOIN wallboxes wb ON wb.id = cs.wallbox_id
               WHERE wb.name = ?
               ORDER BY cs.start_timestamp DESC LIMIT 1""",
            (wallbox_name,),
        ).fetchone()
        if row is None:
            return None
        return row["meter_stop_wh"] if row["meter_stop_wh"] is not None else row["meter_start_wh"]
    finally:
        conn.close()
