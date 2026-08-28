"""Repository fuer loxone_wallbox_config und loxone_poll_state (§ 8.1)."""

from services.db_service import get_connection


def set_uuid(wallbox_id: int, uuid: str) -> None:
    conn = get_connection()
    try:
        conn.execute(
            """INSERT INTO loxone_wallbox_config (wallbox_id, uuid) VALUES (?, ?)
               ON CONFLICT(wallbox_id) DO UPDATE SET uuid = excluded.uuid""",
            (wallbox_id, uuid),
        )
        conn.commit()
    finally:
        conn.close()


def get_uuid(wallbox_id: int) -> str | None:
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT uuid FROM loxone_wallbox_config WHERE wallbox_id = ?", (wallbox_id,)
        ).fetchone()
        return row["uuid"] if row else None
    finally:
        conn.close()


def list_loxone_api_wallboxes_with_config() -> list[dict]:
    """Alle Wallboxen mit source_type='loxone_api', inkl. UUID (falls konfiguriert)."""
    conn = get_connection()
    try:
        rows = conn.execute(
            """SELECT wb.*, lwc.uuid AS loxone_uuid
               FROM wallboxes wb
               LEFT JOIN loxone_wallbox_config lwc ON lwc.wallbox_id = wb.id
               WHERE wb.source_type = 'loxone_api'"""
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_poll_state(wallbox_id: int) -> dict | None:
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT * FROM loxone_poll_state WHERE wallbox_id = ?", (wallbox_id,)
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def set_poll_state(wallbox_id: int, last_meter_wh: int, unchanged_count: int) -> None:
    conn = get_connection()
    try:
        conn.execute(
            """INSERT INTO loxone_poll_state (wallbox_id, last_meter_wh, unchanged_count, updated_at)
               VALUES (?, ?, ?, CURRENT_TIMESTAMP)
               ON CONFLICT(wallbox_id) DO UPDATE SET
                   last_meter_wh = excluded.last_meter_wh,
                   unchanged_count = excluded.unchanged_count,
                   updated_at = CURRENT_TIMESTAMP""",
            (wallbox_id, last_meter_wh, unchanged_count),
        )
        conn.commit()
    finally:
        conn.close()


def get_last_lcl(wallbox_id: int) -> str | None:
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT lcl_text FROM loxone_last_charge_log WHERE wallbox_id = ?", (wallbox_id,)
        ).fetchone()
        return row["lcl_text"] if row else None
    finally:
        conn.close()


def set_poll_state_lcl(wallbox_id: int, lcl_text: str) -> None:
    """FA-LS-10 (Wallbox2-Log-Weg): merkt sich den zuletzt gesehenen Lcl-Text,
    um Aenderungen (= neue abgeschlossene Session) zu erkennen."""
    conn = get_connection()
    try:
        conn.execute(
            """INSERT INTO loxone_last_charge_log (wallbox_id, lcl_text, updated_at)
               VALUES (?, ?, CURRENT_TIMESTAMP)
               ON CONFLICT(wallbox_id) DO UPDATE SET
                   lcl_text = excluded.lcl_text, updated_at = CURRENT_TIMESTAMP""",
            (wallbox_id, lcl_text),
        )
        conn.commit()
    finally:
        conn.close()


def set_live_metrics(wallbox_id: int, current_power_kw: float | None, connected: bool | None,
                      raw_snapshot: str | None = None) -> None:
    """FA-LS-10: Live-Momentaufnahme fuer die Live-Ansicht — zeigt sichtbar,
    DASS und WANN zuletzt synchronisiert wurde (Antwort auf die Rückmeldung,
    dass der Hintergrund-Abgleich sonst voellig unsichtbar ablaeuft).

    Schreibt zusaetzlich die Peak-Leistung fort, damit Loxone-Wallboxen in der
    Oberflaeche dieselben Kennzahlen liefern wie OCPP-Wallboxen (Sprint 5)."""
    conn = get_connection()
    try:
        conn.execute(
            """INSERT INTO wallbox_live_metrics (wallbox_id, current_power_kw, connected, raw_snapshot, peak_power_kw, last_sync_at)
               VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
               ON CONFLICT(wallbox_id) DO UPDATE SET
                   current_power_kw = excluded.current_power_kw,
                   connected = excluded.connected,
                   raw_snapshot = excluded.raw_snapshot,
                   peak_power_kw = MAX(COALESCE(wallbox_live_metrics.peak_power_kw, 0),
                                       COALESCE(excluded.current_power_kw, 0)),
                   last_sync_at = CURRENT_TIMESTAMP""",
            (wallbox_id, current_power_kw, int(connected) if connected is not None else None,
             raw_snapshot, current_power_kw or 0),
        )
        conn.commit()
    finally:
        conn.close()


def get_live_metrics(wallbox_id: int) -> dict | None:
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT * FROM wallbox_live_metrics WHERE wallbox_id = ?", (wallbox_id,)
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


# ---------- Auth-Backoff (Ausfallsicherheit, siehe loxone/poller.py) ----------

def get_auth_backoff_state(wallbox_id: int) -> dict | None:
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT * FROM loxone_auth_backoff WHERE wallbox_id = ?", (wallbox_id,)
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def set_polling_paused(wallbox_id: int, paused: bool) -> None:
    """Manueller Not-Aus (siehe schema.sql § 5.16): stoppt sofort jeden
    weiteren Verbindungsversuch zu dieser Wallbox, unabhaengig vom
    automatischen Backoff-Fehlversuchs-Zaehler."""
    conn = get_connection()
    try:
        conn.execute(
            """INSERT INTO loxone_auth_backoff (wallbox_id, manually_paused)
               VALUES (?, ?)
               ON CONFLICT(wallbox_id) DO UPDATE SET manually_paused = ?""",
            (wallbox_id, 1 if paused else 0, 1 if paused else 0),
        )
        conn.commit()
    finally:
        conn.close()


def record_auth_failure(wallbox_id: int) -> int:
    """Erhoeht den Fehlversuch-Zaehler und liefert den neuen Stand zurueck."""
    conn = get_connection()
    try:
        conn.execute(
            """INSERT INTO loxone_auth_backoff (wallbox_id, consecutive_failures, last_attempt_at)
               VALUES (?, 1, CURRENT_TIMESTAMP)
               ON CONFLICT(wallbox_id) DO UPDATE SET
                   consecutive_failures = consecutive_failures + 1,
                   last_attempt_at = CURRENT_TIMESTAMP""",
            (wallbox_id,),
        )
        conn.commit()
        row = conn.execute(
            "SELECT consecutive_failures FROM loxone_auth_backoff WHERE wallbox_id = ?", (wallbox_id,)
        ).fetchone()
        return row["consecutive_failures"] if row else 1
    finally:
        conn.close()


def record_auth_success(wallbox_id: int) -> None:
    """Setzt den Fehlversuch-Zaehler zurueck, sobald eine Anfrage wieder klappt."""
    conn = get_connection()
    try:
        conn.execute(
            """INSERT INTO loxone_auth_backoff (wallbox_id, consecutive_failures, last_attempt_at, last_success_at)
               VALUES (?, 0, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
               ON CONFLICT(wallbox_id) DO UPDATE SET
                   consecutive_failures = 0,
                   last_attempt_at = CURRENT_TIMESTAMP,
                   last_success_at = CURRENT_TIMESTAMP""",
            (wallbox_id,),
        )
        conn.commit()
    finally:
        conn.close()


# ---------- Log-Abgleich (Ausfallsicherheit, siehe loxone_log_import_service.py) ----------

def get_log_reconcile_state(wallbox_id: int) -> dict | None:
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT * FROM loxone_log_reconcile_state WHERE wallbox_id = ?", (wallbox_id,)
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def ensure_log_reconcile_row(wallbox_id: int, default_log_path: str = "/dev/fsget/log/wallbox.log") -> None:
    """Legt bei Bedarf eine Standard-Zeile an (fuer neu angelegte Wallboxen)."""
    conn = get_connection()
    try:
        conn.execute(
            """INSERT INTO loxone_log_reconcile_state (wallbox_id, log_path)
               VALUES (?, ?)
               ON CONFLICT(wallbox_id) DO NOTHING""",
            (wallbox_id, default_log_path),
        )
        conn.commit()
    finally:
        conn.close()


def set_log_path(wallbox_id: int, log_path: str) -> None:
    ensure_log_reconcile_row(wallbox_id)
    conn = get_connection()
    try:
        conn.execute(
            "UPDATE loxone_log_reconcile_state SET log_path = ? WHERE wallbox_id = ?",
            (log_path, wallbox_id),
        )
        conn.commit()
    finally:
        conn.close()


def mark_log_reconciled(wallbox_id: int, imported_count: int) -> None:
    ensure_log_reconcile_row(wallbox_id)
    conn = get_connection()
    try:
        conn.execute(
            """UPDATE loxone_log_reconcile_state
               SET last_reconciled_at = CURRENT_TIMESTAMP, last_imported_count = ?
               WHERE wallbox_id = ?""",
            (imported_count, wallbox_id),
        )
        conn.commit()
    finally:
        conn.close()

