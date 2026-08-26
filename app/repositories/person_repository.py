"""Repository fuer persons. Reine SQL-Zugriffsschicht (§ 8.1).
Enthält jetzt home_address — Stammadresse für neue Fahrten."""

from services.db_service import get_connection


def _ensure_home_address_col(conn) -> None:
    """Migration: home_address zu bestehenden DBs hinzufügen."""
    try:
        conn.execute("SELECT home_address FROM persons LIMIT 1")
    except Exception:
        conn.execute("ALTER TABLE persons ADD COLUMN home_address TEXT")
        conn.commit()


def insert_person(name: str, email: str | None, personalnummer: str | None,
                   kfz_kennzeichen: str | None, telefon: str | None,
                   home_address: str | None = None) -> int:
    conn = get_connection()
    try:
        _ensure_home_address_col(conn)
        cur = conn.execute(
            """INSERT INTO persons (name, email, personalnummer, kfz_kennzeichen, telefon, home_address)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (name, email, personalnummer, kfz_kennzeichen, telefon, home_address),
        )
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def list_persons() -> list[dict]:
    conn = get_connection()
    try:
        _ensure_home_address_col(conn)
        return [dict(r) for r in conn.execute("SELECT * FROM persons ORDER BY name").fetchall()]
    finally:
        conn.close()


def get_person(person_id: int) -> dict | None:
    conn = get_connection()
    try:
        _ensure_home_address_col(conn)
        row = conn.execute("SELECT * FROM persons WHERE id = ?", (person_id,)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def update_person(person_id: int, name: str, email: str | None, personalnummer: str | None,
                   kfz_kennzeichen: str | None, telefon: str | None,
                   home_address: str | None = None) -> None:
    conn = get_connection()
    try:
        _ensure_home_address_col(conn)
        conn.execute(
            """UPDATE persons SET name=?, email=?, personalnummer=?,
               kfz_kennzeichen=?, telefon=?, home_address=? WHERE id=?""",
            (name, email, personalnummer, kfz_kennzeichen, telefon, home_address, person_id),
        )
        conn.commit()
    finally:
        conn.close()


def delete_person(person_id: int) -> None:
    conn = get_connection()
    try:
        conn.execute("DELETE FROM persons WHERE id = ?", (person_id,))
        conn.commit()
    finally:
        conn.close()


def get_first_home_address() -> str | None:
    """Stammadresse der ersten Person — als Auto-Start für neue Fahrten."""
    conn = get_connection()
    try:
        _ensure_home_address_col(conn)
        row = conn.execute(
            "SELECT home_address FROM persons WHERE home_address IS NOT NULL AND home_address != '' LIMIT 1"
        ).fetchone()
        return row["home_address"] if row else None
    finally:
        conn.close()
