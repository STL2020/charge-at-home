"""BMW-CarData-Verbindung je Fahrzeug.

Ersetzt die bisherigen globalen 'cardata_*'-Einstellungen: Client-ID, GCID,
Token, Stream-Zustand und Fahrzeugdaten hingen bislang an einer einzigen,
anwendungsweiten Verbindung — es passte also nur ein BMW-Fahrzeug. Wer
mehrere Fahrzeuge mit eigenem CarData-Zugang hat (unterschiedliche Autos,
ggf. unterschiedliche BMW-Konten), braucht je Fahrzeug eine eigene, komplett
unabhaengige Verbindung samt eigener Tokens und eigenem Stream.

Jede Zeile hier gehoert zu genau einem Eintrag in 'vehicles' (vehicle_id).
"""
from __future__ import annotations

from services.db_service import get_connection


def _ensure_table(conn) -> None:
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS vehicle_bmw_connections (
            vehicle_id INTEGER PRIMARY KEY,
            client_id TEXT,
            gcid TEXT,
            access_token TEXT,
            id_token TEXT,
            refresh_token TEXT,
            token_gueltig_bis TEXT,
            refresh_erneuert_am TEXT,
            device_code TEXT,
            code_verifier TEXT,
            container_id TEXT,
            stream_aktiv INTEGER NOT NULL DEFAULT 0,
            stream_host TEXT,
            stream_port TEXT,
            letzter_abruf TEXT,
            fzg_daten TEXT,
            stream_puffer TEXT,
            stream_letzter_stand TEXT,
            created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
    """)
    conn.commit()
    # Migration fuer bereits bestehende Datenbanken: 'CREATE TABLE IF NOT
    # EXISTS' legt bei schon vorhandener Tabelle keine neue Spalte an.
    # BUG-Hintergrund: der Container merkte sich nie, mit welchen
    # Deskriptoren er angelegt wurde — kamen spaeter neue hinzu (wie
    # 'maxEnergy' oder 'isPlugged' heute), blieb der alte, unvollstaendige
    # Container bestehen und BMW lieferte die neuen Felder nie aus, auch
    # nicht ueber den manuellen Abruf.
    spalten = {r["name"] for r in conn.execute("PRAGMA table_info(vehicle_bmw_connections)")}
    if "container_deskriptoren" not in spalten:
        conn.execute("ALTER TABLE vehicle_bmw_connections ADD COLUMN container_deskriptoren TEXT")
        conn.commit()


FELDER = ("client_id", "gcid", "access_token", "id_token", "refresh_token",
          "token_gueltig_bis", "refresh_erneuert_am", "device_code",
          "code_verifier", "container_id", "container_deskriptoren",
          "stream_aktiv", "stream_host",
          "stream_port", "letzter_abruf", "fzg_daten",
          "stream_puffer", "stream_letzter_stand")


def get(vehicle_id: int) -> dict:
    """Liefert die Verbindungsdaten eines Fahrzeugs — nie None, sondern
    leere Werte, damit Aufrufer nicht jedes Mal auf None pruefen muessen."""
    conn = get_connection()
    try:
        _ensure_table(conn)
        row = conn.execute(
            "SELECT * FROM vehicle_bmw_connections WHERE vehicle_id = ?",
            (vehicle_id,)).fetchone()
        if row:
            d = dict(row)
        else:
            d = {f: None for f in FELDER}
            d["vehicle_id"] = vehicle_id
        # Immer als String/Bool nutzbar, egal ob die Zeile existiert.
        d["stream_aktiv"] = bool(d.get("stream_aktiv"))
        for f in FELDER:
            if f != "stream_aktiv" and d.get(f) is None:
                d[f] = ""
        return d
    finally:
        conn.close()


def set_felder(vehicle_id: int, **werte) -> None:
    """Setzt einzelne Felder, legt die Zeile bei Bedarf an.

    Nur bekannte Felder werden angenommen, Tippfehler im Aufruf fallen also
    sofort auf statt still ignoriert zu werden."""
    unbekannt = set(werte) - set(FELDER)
    if unbekannt:
        raise ValueError(f"Unbekannte Felder: {unbekannt}")
    if not werte:
        return
    conn = get_connection()
    try:
        _ensure_table(conn)
        vorhanden = conn.execute(
            "SELECT 1 FROM vehicle_bmw_connections WHERE vehicle_id = ?",
            (vehicle_id,)).fetchone()
        if vorhanden:
            setz = ", ".join(f"{k} = ?" for k in werte)
            conn.execute(
                f"UPDATE vehicle_bmw_connections SET {setz} WHERE vehicle_id = ?",
                list(werte.values()) + [vehicle_id])
        else:
            spalten = ["vehicle_id"] + list(werte.keys())
            platz = ", ".join("?" * len(spalten))
            conn.execute(
                f"INSERT INTO vehicle_bmw_connections ({', '.join(spalten)}) "
                f"VALUES ({platz})", [vehicle_id] + list(werte.values()))
        conn.commit()
    finally:
        conn.close()


def loeschen(vehicle_id: int) -> None:
    """Trennt die Verbindung vollstaendig — fuer 'Verbindung trennen' und
    beim Loeschen eines Fahrzeugs."""
    conn = get_connection()
    try:
        _ensure_table(conn)
        conn.execute(
            "DELETE FROM vehicle_bmw_connections WHERE vehicle_id = ?",
            (vehicle_id,))
        conn.commit()
    finally:
        conn.close()


def liste_verbundener_fahrzeuge() -> list[int]:
    """Vehicle-IDs mit einer bestehenden BMW-Anmeldung (Refresh-Token
    vorhanden) — zum Wiederaufnehmen von Automatik/Stream nach einem
    Neustart, ohne jedes Fahrzeug einzeln abfragen zu muessen."""
    conn = get_connection()
    try:
        _ensure_table(conn)
        rows = conn.execute(
            """SELECT vehicle_id FROM vehicle_bmw_connections
               WHERE refresh_token IS NOT NULL AND refresh_token != ''"""
        ).fetchall()
        return [r["vehicle_id"] for r in rows]
    finally:
        conn.close()


def liste_mit_aktivem_stream() -> list[int]:
    """Vehicle-IDs, deren Stream eingeschaltet ist — fuer die
    Wiederaufnahme nach einem Neustart."""
    conn = get_connection()
    try:
        _ensure_table(conn)
        rows = conn.execute(
            "SELECT vehicle_id FROM vehicle_bmw_connections WHERE stream_aktiv = 1"
        ).fetchall()
        return [r["vehicle_id"] for r in rows]
    finally:
        conn.close()
