"""Repository fuer wallboxes. Reine SQL-Zugriffsschicht, keine Geschaeftslogik (§ 8.1)."""

import sqlite3

from services.db_service import get_connection


def get_or_create_wallbox(name: str, source_type: str = "manual") -> int:
    """Liefert die ID einer Wallbox mit diesem Namen, legt sie bei Bedarf an.

    Wird von CSV-Import und manueller Erfassung genutzt, solange die echte
    Wallbox-Verwaltung (Sprint 3, NFA-10) noch nicht existiert.
    """
    conn = get_connection()
    try:
        row = conn.execute("SELECT id FROM wallboxes WHERE name = ?", (name,)).fetchone()
        if row:
            return row["id"]
        cur = conn.execute(
            "INSERT INTO wallboxes (name, source_type) VALUES (?, ?)",
            (name, source_type),
        )
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def list_wallboxes() -> list[dict]:
    conn = get_connection()
    try:
        return [dict(r) for r in conn.execute("SELECT * FROM wallboxes ORDER BY name").fetchall()]
    finally:
        conn.close()


def get_wallbox(wallbox_id: int) -> dict | None:
    conn = get_connection()
    try:
        row = conn.execute("SELECT * FROM wallboxes WHERE id = ?", (wallbox_id,)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def get_by_ocpp_id(charge_point_id: str) -> dict | None:
    """NFA-10: Zugriffskontrolle — nur bekannte charge_point_id werden akzeptiert."""
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT * FROM wallboxes WHERE ocpp_charge_point_id = ?", (charge_point_id,)
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def create_wallbox(
    name: str, source_type: str, ocpp_charge_point_id: str | None = None,
    loxone_host: str | None = None, loxone_username: str | None = None,
    loxone_password_encrypted: str | None = None, location: str | None = None,
) -> int:
    conn = get_connection()
    try:
        cur = conn.execute(
            """INSERT INTO wallboxes
               (name, source_type, ocpp_charge_point_id, loxone_host, loxone_username,
                loxone_password_encrypted, location)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (name, source_type, ocpp_charge_point_id, loxone_host, loxone_username,
             loxone_password_encrypted, location),
        )
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def update_wallbox(
    wallbox_id: int, name: str, source_type: str, ocpp_charge_point_id: str | None,
    loxone_host: str | None, loxone_username: str | None,
    loxone_password_encrypted: str | None, location: str | None = None,
) -> None:
    conn = get_connection()
    try:
        if location is not None:
            conn.execute(
                """UPDATE wallboxes SET name = ?, source_type = ?, ocpp_charge_point_id = ?,
                   loxone_host = ?, loxone_username = ?, loxone_password_encrypted = ?, location = ?
                   WHERE id = ?""",
                (name, source_type, ocpp_charge_point_id, loxone_host, loxone_username,
                 loxone_password_encrypted, location, wallbox_id),
            )
        else:
            conn.execute(
                """UPDATE wallboxes SET name = ?, source_type = ?, ocpp_charge_point_id = ?,
                   loxone_host = ?, loxone_username = ?, loxone_password_encrypted = ?
                   WHERE id = ?""",
                (name, source_type, ocpp_charge_point_id, loxone_host, loxone_username,
                 loxone_password_encrypted, wallbox_id),
            )
        conn.commit()
    finally:
        conn.close()


def _tables_referencing_wallbox() -> list[str]:
    """Findet automatisch ALLE Tabellen mit einer wallbox_id-Spalte (ausser
    charging_sessions und wallboxes selbst, die gesondert behandelt werden).

    Ersetzt eine fest eingetragene Tabellenliste, die in der Vergangenheit
    wiederholt vergessen wurde zu aktualisieren, wenn eine neue Tabelle mit
    Bezug auf wallboxes hinzukam (zuletzt: wallbox_live_metrics) — das fuehrte
    jedes Mal zu genau derselben FOREIGN-KEY-Fehlermeldung beim Loeschen.
    """
    conn = get_connection()
    try:
        tables = [row[0] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()]
        result = []
        for t in tables:
            if t in ("wallboxes", "charging_sessions"):
                continue
            cols = [c[1] for c in conn.execute(f"PRAGMA table_info({t})").fetchall()]
            if "wallbox_id" in cols:
                result.append(t)
        return result
    finally:
        conn.close()


def delete_wallbox(wallbox_id: int, force: bool = False) -> tuple[bool, str]:
    """Loescht eine Wallbox samt aller abhaengigen Konfigurationsdaten.

    Blockiert die Loeschung standardmaessig, falls bereits Ladesessions
    zugeordnet sind — das waere ein Verlust von Abrechnungsdaten, kein reines
    Aufraeumen. Mit force=True werden auch die Sessions mitgeloescht (z. B.
    zum Entfernen von Testdaten waehrend der Einrichtung).
    """
    conn = get_connection()
    try:
        session_count = conn.execute(
            "SELECT COUNT(*) AS c FROM charging_sessions WHERE wallbox_id = ?", (wallbox_id,)
        ).fetchone()["c"]
        if session_count > 0 and not force:
            return False, (
                f"Wallbox kann nicht gelöscht werden: {session_count} Ladesession(s) sind "
                f"bereits zugeordnet. Sessions zuerst löschen oder Wallbox umbenennen statt entfernen."
            )

        # Fremdschluesselpruefung waehrend des Loeschens aus. Sonst haengt der
        # Erfolg an der Reihenfolge, in der die abhaengigen Tabellen geleert
        # werden — und eine einzige, die aus einer aelteren Fassung stammt,
        # laesst den ganzen Vorgang mit HTTP 500 scheitern.
        conn.execute("PRAGMA foreign_keys = OFF")

        if force:
            conn.execute("DELETE FROM charging_sessions WHERE wallbox_id = ?", (wallbox_id,))

        # Jede abhaengige Tabelle einzeln, Fehler einsammeln statt abbrechen:
        # Ein Rest in einer Nebentabelle darf nicht verhindern, dass die
        # Wallbox verschwindet.
        reste = []
        for table in _tables_referencing_wallbox():
            try:
                conn.execute(f"DELETE FROM {table} WHERE wallbox_id = ?", (wallbox_id,))
            except sqlite3.Error:
                reste.append(table)

        conn.execute("DELETE FROM wallboxes WHERE id = ?", (wallbox_id,))
        conn.commit()

        msg = "Wallbox gelöscht." if not force else \
              f"Wallbox samt {session_count} Session(s) gelöscht."
        if reste:
            msg += (f" Hinweis: Reste in {', '.join(reste)} konnten nicht "
                    f"entfernt werden — sie stören den Betrieb nicht.")
        return True, msg
    except sqlite3.Error as e:
        conn.rollback()
        return False, f"Löschen fehlgeschlagen: {e}"
    finally:
        try:
            conn.execute("PRAGMA foreign_keys = ON")
        except sqlite3.Error:
            pass
        conn.close()


def set_status(wallbox_id: int, status: str) -> None:
    """FA-LS-09: Live-Status, geschrieben vom OCPP-Prozess, gelesen von Flask (WAL-Modus)."""
    conn = get_connection()
    try:
        conn.execute(
            """INSERT INTO wallbox_status (wallbox_id, status, updated_at)
               VALUES (?, ?, CURRENT_TIMESTAMP)
               ON CONFLICT(wallbox_id) DO UPDATE SET status = excluded.status, updated_at = CURRENT_TIMESTAMP""",
            (wallbox_id, status),
        )
        conn.commit()
    finally:
        conn.close()


def get_status(wallbox_id: int) -> dict | None:
    conn = get_connection()
    try:
        row = conn.execute("SELECT * FROM wallbox_status WHERE wallbox_id = ?", (wallbox_id,)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def list_wallboxes_with_status() -> list[dict]:
    conn = get_connection()
    try:
        rows = conn.execute(
            """SELECT wb.*, ws.status as live_status, ws.updated_at as status_updated_at,
                      lwc.uuid as loxone_uuid,
                      (SELECT COUNT(*) FROM charging_sessions cs WHERE cs.wallbox_id = wb.id) as session_count
               FROM wallboxes wb
               LEFT JOIN wallbox_status ws ON ws.wallbox_id = wb.id
               LEFT JOIN loxone_wallbox_config lwc ON lwc.wallbox_id = wb.id
               ORDER BY wb.name"""
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()
