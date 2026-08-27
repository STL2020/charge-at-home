"""Repository fuer ocpp_client_config (§ 5.18) — FA-OCPP-CLIENT-01."""

from services.db_service import get_connection


def get_config(wallbox_id: int) -> dict | None:
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT * FROM ocpp_client_config WHERE wallbox_id = ?", (wallbox_id,)
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def list_enabled_configs() -> list[dict]:
    conn = get_connection()
    try:
        rows = conn.execute(
            """SELECT occ.*, w.name AS wallbox_name FROM ocpp_client_config occ
               JOIN wallboxes w ON w.id = occ.wallbox_id
               WHERE occ.enabled = 1"""
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def set_config(wallbox_id: int, remote_url: str, remote_charge_point_id: str, enabled: bool) -> None:
    conn = get_connection()
    try:
        conn.execute(
            """INSERT INTO ocpp_client_config (wallbox_id, remote_url, remote_charge_point_id, enabled)
               VALUES (?, ?, ?, ?)
               ON CONFLICT(wallbox_id) DO UPDATE SET
                   remote_url = excluded.remote_url,
                   remote_charge_point_id = excluded.remote_charge_point_id,
                   enabled = excluded.enabled""",
            (wallbox_id, remote_url, remote_charge_point_id, 1 if enabled else 0),
        )
        conn.commit()
    finally:
        conn.close()


def delete_config(wallbox_id: int) -> None:
    conn = get_connection()
    try:
        conn.execute("DELETE FROM ocpp_client_config WHERE wallbox_id = ?", (wallbox_id,))
        conn.commit()
    finally:
        conn.close()


def record_connect_attempt(wallbox_id: int, success: bool, error: str | None = None) -> None:
    conn = get_connection()
    try:
        if success:
            conn.execute(
                """UPDATE ocpp_client_config SET last_connect_attempt_at = CURRENT_TIMESTAMP,
                   last_connect_success_at = CURRENT_TIMESTAMP, last_error = NULL WHERE wallbox_id = ?""",
                (wallbox_id,),
            )
        else:
            conn.execute(
                """UPDATE ocpp_client_config SET last_connect_attempt_at = CURRENT_TIMESTAMP,
                   last_error = ? WHERE wallbox_id = ?""",
                (error, wallbox_id),
            )
        conn.commit()
    finally:
        conn.close()


def set_last_relayed_session(wallbox_id: int, session_id: int) -> None:
    conn = get_connection()
    try:
        conn.execute(
            "UPDATE ocpp_client_config SET last_relayed_session_id = ? WHERE wallbox_id = ?",
            (session_id, wallbox_id),
        )
        conn.commit()
    finally:
        conn.close()
