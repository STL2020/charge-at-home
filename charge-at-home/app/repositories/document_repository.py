"""Repository fuer generierte Belege (Tabelle documents, § 5.6).
PDF-Bytes werden als BLOB in der DB gespeichert damit Downloads auch nach
App-Neustart funktionieren — unabhaengig davon ob die Datei noch auf Disk liegt.
"""

import io
from services.db_service import get_connection


def save_document(doc_type: str, period_start: str, period_end: str, user_id: int,
                   file_path: str, checksum_sha256: str,
                   pdf_bytes: bytes | None = None) -> int:
    conn = get_connection()
    try:
        # pdf_data-Spalte existiert ggf. noch nicht in aelteren DBs — Migration
        try:
            conn.execute("SELECT pdf_data FROM documents LIMIT 1")
        except Exception:
            conn.execute("ALTER TABLE documents ADD COLUMN pdf_data BLOB")
            conn.commit()

        cur = conn.execute(
            """INSERT INTO documents
               (doc_type, period_start, period_end, user_id, file_path, checksum_sha256, pdf_data)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (doc_type, period_start, period_end, user_id, file_path, checksum_sha256, pdf_bytes),
        )
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def list_documents(user_id: int, year: str | None = None, month: str | None = None) -> list[dict]:
    conn = get_connection()
    try:
        query = "SELECT id, doc_type, period_start, period_end, user_id, file_path, checksum_sha256, generated_at FROM documents WHERE user_id = ?"
        params: list = [user_id]
        if year:
            query += " AND strftime('%Y', generated_at) = ?"
            params.append(year)
        if month:
            query += " AND strftime('%m', generated_at) = ?"
            params.append(month.zfill(2))
        query += " ORDER BY generated_at DESC"
        rows = conn.execute(query, params).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_document(document_id: int) -> dict | None:
    conn = get_connection()
    try:
        row = conn.execute("SELECT * FROM documents WHERE id = ?", (document_id,)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def delete_document(document_id: int) -> str | None:
    conn = get_connection()
    try:
        row = conn.execute("SELECT file_path FROM documents WHERE id = ?", (document_id,)).fetchone()
        if row is None:
            return None
        conn.execute("DELETE FROM documents WHERE id = ?", (document_id,))
        conn.commit()
        return row["file_path"]
    finally:
        conn.close()
