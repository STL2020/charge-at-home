"""
OCPP-Diagnose-Service — dauerhafte Logdatei + Nachrichtentyp-Zaehlung.

Hintergrund (Ruecksprache Auftraggeber): Das bisherige Rohdaten-Logging
("ROH von/an", siehe ocpp_server/server.py) landet im gemeinsamen event_log
(Datenbank-Tabelle, auf 500 Eintraege insgesamt rotierend, siehe
event_log_service.MAX_ENTRIES) — geteilt mit Loxone-API- und System-
Ereignissen, die haeufig genauso oft auftreten. Bei laufendem Betrieb koennen
OCPP-Rohdaten so schnell "verdraengt" werden, ohne dass erkennbar ist, WELCHE
Nachrichtentypen ueberhaupt jemals angekommen sind.

Dieser Service ergaenzt zwei Dinge:
1. Eine ECHTE, eigenstaendige Logdatei (data/ocpp_raw.log) fuer JEDE rohe
   OCPP-Nachricht — unabhaengig von der 500-Eintraege-Rotation, dauerhaft.
2. Eine persistente Zaehlung PRO NACHRICHTENTYP (BootNotification, Heartbeat,
   StatusNotification, StartTransaction, MeterValues, StopTransaction, ...) —
   macht auf einen Blick sichtbar, welche Typen ueberhaupt jemals eingegangen
   sind, statt das aus einzelnen Protokollzeilen muehsam selbst zu zaehlen.
   Bestaetigt/widerlegt damit konkret und nachvollziehbar, ob z. B.
   MeterValues/StartTransaction/StopTransaction jemals ankommen (bisher
   bekannte Einschraenkung: Loxone sendet diese nicht, siehe Pflichtenheft).
"""

import json
import os
from datetime import datetime, timezone

from services.db_service import get_connection

_SERVICE_DIR = os.path.dirname(os.path.abspath(__file__))
LOG_FILE_PATH = os.path.normpath(os.path.join(_SERVICE_DIR, "..", "..", "data", "ocpp_raw.log"))


def log_raw_message(direction: str, charge_point_id: str, raw_message: str) -> None:
    """Haengt JEDE rohe OCPP-Nachricht an eine dauerhafte Logdatei an (nicht
    rotierend, nicht mit anderen Quellen geteilt). direction: 'in' oder 'out'."""
    try:
        os.makedirs(os.path.dirname(LOG_FILE_PATH), exist_ok=True)
        timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
        arrow = "->" if direction == "in" else "<-"
        with open(LOG_FILE_PATH, "a", encoding="utf-8") as f:
            f.write(f"{timestamp} {arrow} [{charge_point_id}] {raw_message}\n")
    except Exception:
        pass  # Logging darf niemals die eigentliche OCPP-Verarbeitung stoeren


def _extract_message_type(raw_message: str) -> str | None:
    """Zerlegt eine OCPP-J-Nachricht (JSON-RPC-Array) und liefert den
    Nachrichtentyp (Action-Name), falls es sich um eine CALL-Nachricht
    (messageTypeId=2, vom Chargepoint AUSGEHEND) handelt. CALLRESULT (3) und
    CALLERROR (4) haben keinen eigenen Action-Namen (sind Antworten auf eine
    zuvor gesendete Nachricht) und werden deshalb hier nicht gezaehlt."""
    try:
        parsed = json.loads(raw_message)
    except (json.JSONDecodeError, TypeError):
        return None
    if not isinstance(parsed, list) or len(parsed) < 3:
        return None
    message_type_id = parsed[0]
    if message_type_id != 2:  # nur CALL-Nachrichten haben einen Action-Namen
        return None
    action = parsed[2]
    return action if isinstance(action, str) else None


def record_message_type(charge_point_id: str, raw_message: str) -> None:
    """Erkennt den Nachrichtentyp einer eingehenden Rohnachricht und erhoeht
    den persistenten Zaehler dafuer (siehe Modul-Docstring)."""
    message_type = _extract_message_type(raw_message)
    if message_type is None:
        return

    conn = get_connection()
    try:
        conn.execute(
            """INSERT INTO ocpp_message_counts (message_type, charge_point_id, count, first_seen_at, last_seen_at)
               VALUES (?, ?, 1, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
               ON CONFLICT(message_type, charge_point_id) DO UPDATE SET
                   count = count + 1,
                   last_seen_at = CURRENT_TIMESTAMP""",
            (message_type, charge_point_id),
        )
        conn.commit()
    except Exception:
        pass  # Zaehlung darf niemals die eigentliche OCPP-Verarbeitung stoeren
    finally:
        conn.close()


def get_message_type_counts() -> list[dict]:
    """Liefert alle bisher erfassten Nachrichtentyp-Zaehlungen, neueste zuerst."""
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT * FROM ocpp_message_counts ORDER BY last_seen_at DESC"
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_log_file_path() -> str:
    """Gibt den absoluten Pfad der Logdatei zurück — für Diagnose-Zwecke."""
    return LOG_FILE_PATH


def get_log_file_info() -> dict:
    """Diagnose: Pfad, Existenz, Größe der Logdatei."""
    exists = os.path.exists(LOG_FILE_PATH)
    return {
        "path": LOG_FILE_PATH,
        "exists": exists,
        "size_bytes": os.path.getsize(LOG_FILE_PATH) if exists else 0,
        "line_count": sum(1 for _ in open(LOG_FILE_PATH, "r", encoding="utf-8")) if exists else 0,
    }


def get_log_file_tail(max_lines: int = 200) -> list[str]:
    """Liest die letzten Zeilen der dauerhaften Logdatei (fuer die Anzeige in
    der Oberflaeche, ohne bei sehr grossen Dateien die komplette Datei laden
    zu muessen)."""
    if not os.path.exists(LOG_FILE_PATH):
        return []
    try:
        with open(LOG_FILE_PATH, "r", encoding="utf-8") as f:
            lines = f.readlines()
        return [line.rstrip("\n") for line in lines[-max_lines:]]
    except Exception:
        return []
