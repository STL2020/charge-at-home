"""
DB-Service — zentraler SQLite-Verbindungsaufbau.

Pflichtenheft-Referenzen:
- NFA-02: SQLite im WAL-Modus, keine automatische Loeschung.
- NFA-08: Jede Verbindung mit timeout=10.0s (Busy-Timeout), um
  'database is locked'-Fehler bei gleichzeitigem Zugriff abzufangen.

Alle Repositories (Sprint 1+) verwenden ausschliesslich get_connection(),
nicht sqlite3.connect() direkt, damit WAL-Modus und Timeout garantiert
konsistent angewendet werden.
"""

import os
import sqlite3

DB_PATH = os.environ.get(
    "CHARGE_DB_PATH",
    os.path.join(os.path.dirname(__file__), "..", "..", "data", "charging.db"),
)
# Ort des Schemas. Im Container liegt es unter /srv/schema, weil /srv/data
# vom Anwender ueberlagert wird — ein dort abgelegtes Schema waere nach dem
# Einhaengen eines Volumes verschwunden. Bei lokaler Installation gilt der
# Pfad neben der Anwendung.
SCHEMA_PATH = os.environ.get(
    "CHARGE_SCHEMA_PATH",
    os.path.join(os.path.dirname(__file__), "..", "..", "data", "schema.sql"))


def get_connection() -> sqlite3.Connection:
    """Liefert eine SQLite-Verbindung mit WAL-Modus und Busy-Timeout (NFA-08)."""
    conn = sqlite3.connect(DB_PATH, timeout=10.0)
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA foreign_keys=ON;")
    conn.row_factory = sqlite3.Row
    return conn


def _needs_source_constraint_migration(conn: sqlite3.Connection) -> bool:
    """Prueft, ob charging_sessions noch eine veraltete CHECK-Constraint hat
    (betrifft ueber Updates hinweg erhaltene, bereits existierende Datenbanken).
    Deckt sowohl das fehlende 'loxone_api' (aeltere Migration) als auch das
    fehlende Quelle 'bmw_app' (BMW-Ladehistorie über CarData) ab."""
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='charging_sessions'"
    ).fetchone()
    if row is None or row[0] is None:
        return False
    return "loxone_api" not in row[0] or "bmw_app" not in row[0]


def _migrate_charging_sessions_source_constraint(conn: sqlite3.Connection) -> None:
    """Baut charging_sessions neu auf, um 'loxone_api' und 'bmw_app' in die
    source-CHECK-Constraint aufzunehmen — SQLite erlaubt kein direktes ALTER
    auf CHECK-Constraints. Kopiert alle echten Spalten (nicht die GENERATED-
    Spalten energy_kwh/amount_eur, die werden beim Neuanlegen automatisch neu
    berechnet)."""
    conn.executescript(
        """
        ALTER TABLE charging_sessions RENAME TO charging_sessions_old;

        CREATE TABLE charging_sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            wallbox_id INTEGER NOT NULL REFERENCES wallboxes(id),
            user_id INTEGER NOT NULL REFERENCES users_config(id),
            source TEXT NOT NULL CHECK (source IN ('ocpp', 'csv', 'manual', 'loxone_api', 'bmw_app')),
            start_timestamp DATETIME NOT NULL,
            end_timestamp DATETIME,
            meter_start_wh INTEGER NOT NULL,
            meter_stop_wh INTEGER,
            energy_kwh REAL GENERATED ALWAYS AS
                (CASE WHEN meter_stop_wh IS NOT NULL THEN (meter_stop_wh - meter_start_wh) / 1000.0 ELSE NULL END) VIRTUAL,
            price_per_kwh REAL NOT NULL,
            amount_eur REAL GENERATED ALWAYS AS
                (CASE WHEN meter_stop_wh IS NOT NULL THEN ((meter_stop_wh - meter_start_wh) / 1000.0) * price_per_kwh ELSE NULL END) VIRTUAL,
            rfid_tag TEXT,
            classification TEXT CHECK (classification IN ('dienstlich', 'privat') OR classification IS NULL),
            pv_mode TEXT,
            status TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'closed', 'anomaly')),
            created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

        INSERT INTO charging_sessions
            (id, wallbox_id, user_id, source, start_timestamp, end_timestamp,
             meter_start_wh, meter_stop_wh, price_per_kwh, rfid_tag, classification,
             pv_mode, status, created_at, updated_at)
        SELECT
            id, wallbox_id, user_id, source, start_timestamp, end_timestamp,
            meter_start_wh, meter_stop_wh, price_per_kwh, rfid_tag, classification,
            pv_mode, status, created_at, updated_at
        FROM charging_sessions_old;

        DROP TABLE charging_sessions_old;

        CREATE INDEX IF NOT EXISTS idx_sessions_period ON charging_sessions(start_timestamp, wallbox_id);
        """
    )
    conn.commit()


def _needs_event_log_migration(conn: sqlite3.Connection) -> bool:
    """Prueft, ob event_log noch die veraltete CHECK-Constraint ohne 'manual'
    hat (fehlte bisher, wodurch z. B. die BMW-Import-Bestaetigungsmeldung
    lautlos verworfen wurde — log_event faengt Fehler bewusst ab)."""
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='event_log'"
    ).fetchone()
    if row is None or row[0] is None:
        return False
    # 'bmw' kam mit der ConnectedDrive-Anbindung hinzu (Sprint 6): ohne diesen
    # Wert verwarf log_event BMW-Sync-Meldungen lautlos.
    return "manual" not in row[0] or "'bmw'" not in row[0]


def _migrate_event_log_source_constraint(conn: sqlite3.Connection) -> None:
    """Baut event_log neu auf, um 'manual', 'bmw' und 'bmw_app' in die
    source-CHECK-Constraint aufzunehmen — SQLite erlaubt kein direktes ALTER
    auf CHECK-Constraints."""
    conn.executescript(
        """
        ALTER TABLE event_log RENAME TO event_log_old;

        CREATE TABLE event_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source TEXT NOT NULL CHECK (source IN ('ocpp', 'loxone_api', 'system', 'manual', 'bmw', 'bmw_app')),
            level TEXT NOT NULL DEFAULT 'info' CHECK (level IN ('info', 'warning', 'error')),
            message TEXT NOT NULL,
            created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

        INSERT INTO event_log (id, source, level, message, created_at)
        SELECT id, source, level, message, created_at FROM event_log_old;

        DROP TABLE event_log_old;
        """
    )
    conn.commit()


def _migrate_bmw_trips_nach_trips(conn: sqlite3.Connection) -> None:
    """Uebertraegt bereits zugeordnete BMW-Fahrten in die Hauptliste.

    Bis v11.46 landeten nur DIENSTLICHE Fahrten in `trips`; private wurden
    lediglich in `bmw_trips` markiert. Seit der Zusammenfuehrung stehen alle
    Fahrten in einer Liste — die privaten aus der Zeit davor fehlen dort aber
    und wuerden das Fahrtenbuch lueckenhaft machen. Diese Migration holt sie
    nach, einmalig und ohne Doubletten (nur Eintraege ohne trip_id)."""
    tabellen = {r["name"] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    if "bmw_trips" not in tabellen:
        return
    spalten = {r["name"] for r in conn.execute("PRAGMA table_info(trips)").fetchall()}
    if "fahrtart" not in spalten:
        return

    offen = conn.execute(
        """SELECT * FROM bmw_trips
           WHERE trip_id IS NULL AND category IN ('PRIVAT', 'DIENSTLICH')"""
    ).fetchall()
    if not offen:
        return

    satz_row = conn.execute(
        "SELECT value FROM app_settings WHERE key = 'default_km_rate'").fetchone()
    try:
        satz = float(satz_row["value"]) if satz_row else 0.15
    except (TypeError, ValueError):
        satz = 0.15

    uebertragen = 0
    for b in offen:
        dienstlich = b["category"] == "DIENSTLICH"
        cur = conn.execute(
            """INSERT INTO trips
               (user_id, trip_date, start_address, end_address, distance_km,
                purpose, rate_chosen, vehicle_id, fahrtart)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (b["user_id"], (b["start_time"] or "")[:10],
             b["start_address"] or "—", b["end_address"] or "—",
             b["distance_km"] or 0,
             "Dienstfahrt (BMW-Import)" if dienstlich else "Privatfahrt (BMW-Import)",
             satz if dienstlich else 0.0, b["vehicle_id"],
             "dienstlich" if dienstlich else "privat"))
        conn.execute("UPDATE bmw_trips SET trip_id = ? WHERE id = ?",
                     (cur.lastrowid, b["id"]))
        uebertragen += 1
    conn.commit()
    if uebertragen:
        import logging
        logging.getLogger(__name__).info(
            f"{uebertragen} bereits zugeordnete BMW-Fahrten in die Fahrtenliste übernommen.")


def _repariere_verwaiste_verweise(conn: sqlite3.Connection) -> None:
    """Repariert Fremdschluessel, die auf eine geloeschte Hilfstabelle zeigen.

    Betrifft Datenbanken, die mit einer fehlerhaften Fassung der
    Wallbox-Migration umgebaut wurden: Dort verweist charging_sessions auf
    'wallboxes_alt'. Jeder Schreibzugriff scheitert dann mit 'no such table'.
    Die Tabelle wird deshalb ohne diesen Verweis neu aufgebaut — die Daten
    bleiben vollstaendig erhalten."""
    for tabelle in ("charging_sessions", "wallbox_status", "wallbox_live_metrics",
                    "loxone_wallbox_config"):
        try:
            zeile = conn.execute(
                "SELECT sql FROM sqlite_master WHERE type='table' AND name=?",
                (tabelle,)).fetchone()
            if not zeile or "wallboxes_alt" not in (zeile["sql"] or ""):
                continue

            import re as _re
            neues_sql = _re.sub(r'REFERENCES\s+"?wallboxes_alt"?', "REFERENCES wallboxes",
                                zeile["sql"], flags=_re.IGNORECASE)

            conn.execute("PRAGMA foreign_keys=OFF")
            conn.execute("PRAGMA legacy_alter_table=ON")
            indizes = [r["sql"] for r in conn.execute(
                "SELECT sql FROM sqlite_master WHERE type='index' AND tbl_name=? "
                "AND sql IS NOT NULL", (tabelle,))]
            conn.execute("BEGIN")
            conn.execute(f"ALTER TABLE {tabelle} RENAME TO {tabelle}_reparatur")
            conn.execute(neues_sql)
            spalten = [r["name"] for r in conn.execute(f"PRAGMA table_info({tabelle})")]
            liste = ", ".join(f'"{s}"' for s in spalten)
            conn.execute(f"INSERT INTO {tabelle} ({liste}) "
                         f"SELECT {liste} FROM {tabelle}_reparatur")
            conn.execute(f"DROP TABLE {tabelle}_reparatur")
            for idx in indizes:
                try:
                    conn.execute(idx)
                except sqlite3.Error:
                    pass
            conn.commit()
        except sqlite3.Error:
            conn.rollback()
        finally:
            try:
                conn.execute("PRAGMA legacy_alter_table=OFF")
                conn.execute("PRAGMA foreign_keys=ON")
            except sqlite3.Error:
                pass


def _rette_umbau_reste(conn: sqlite3.Connection) -> None:
    """Stellt eine Tabelle wieder her, deren Umbau abgebrochen ist.

    SQLite kennt kein Aendern von CHECK-Bedingungen; die Tabelle wird dafuer
    umbenannt, neu angelegt und umkopiert. Bricht das dazwischen ab, existiert
    nur noch die umbenannte Tabelle — jeder weitere Zugriff scheitert dann mit
    'no such table'. Dieser Fall wird hier eingesammelt, bevor das Schema eine
    leere Tabelle daneben anlegt."""
    for hilfsname, echtname in (("wallboxes_alt", "wallboxes"),):
        try:
            hat_hilf = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                (hilfsname,)).fetchone()
            if not hat_hilf:
                continue
            hat_echt = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                (echtname,)).fetchone()
            if hat_echt:
                # Beide da: Enthaelt die Hilfstabelle mehr, gewinnt sie.
                n_hilf = conn.execute(f"SELECT COUNT(*) FROM {hilfsname}").fetchone()[0]
                n_echt = conn.execute(f"SELECT COUNT(*) FROM {echtname}").fetchone()[0]
                if n_hilf > n_echt:
                    conn.execute(f"DROP TABLE {echtname}")
                    conn.execute(f"ALTER TABLE {hilfsname} RENAME TO {echtname}")
                else:
                    conn.execute(f"DROP TABLE {hilfsname}")
            else:
                conn.execute(f"ALTER TABLE {hilfsname} RENAME TO {echtname}")
            conn.commit()
        except sqlite3.Error:
            conn.rollback()


def _migrate_wallbox_extern_ocpp(conn: sqlite3.Connection) -> None:
    """Erlaubt die Quelle 'extern_ocpp' fuer Wallboxen.

    SQLite kann CHECK-Bedingungen nicht nachtraeglich aendern; die Tabelle
    muss dafuer neu aufgebaut werden. Der Umbau laeuft in einer Transaktion
    und stellt Indizes wieder her — beides fehlte in der ersten Fassung und
    fuehrte zu 'no such table: main.wallboxes_alt', wenn ein Schritt
    fehlschlug und ein halb umgebauter Zustand zurueckblieb.
    """
    # Erst aufraeumen: Ist von einem frueheren, abgebrochenen Versuch noch
    # eine Hilfstabelle uebrig, blockiert sie jeden weiteren Lauf.
    try:
        rest = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name='wallboxes_alt'").fetchone()
        if rest:
            haupt = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' "
                "AND name='wallboxes'").fetchone()
            if haupt:
                # Beide vorhanden: Die Hilfstabelle ist ein Ueberbleibsel.
                conn.execute("DROP TABLE wallboxes_alt")
            else:
                # Nur die Hilfstabelle: Der Umbau brach nach dem Umbenennen
                # ab — zurueckbenennen, damit die Daten nicht verloren gehen.
                conn.execute("ALTER TABLE wallboxes_alt RENAME TO wallboxes")
            conn.commit()
    except sqlite3.Error:
        conn.rollback()

    zeile = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='wallboxes'").fetchone()
    if not zeile:
        return
    import re as _re
    sql_alt = zeile["sql"] or ""
    if "extern_ocpp" in sql_alt:
        return   # bereits umgebaut
    # Auch die Vorpruefung per Muster: 'CHECK ( source_type' mit Leerzeichen
    # rutschte an einem festen Vergleich vorbei.
    if not _re.search(r"CHECK\s*\(\s*source_type", sql_alt, _re.IGNORECASE):
        return   # nie mit Bedingung angelegt

    # Indizes merken — beim Umbenennen wandern sie mit und verschwinden
    # anschliessend mit der Hilfstabelle.
    indizes = [r["sql"] for r in conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='index' AND tbl_name='wallboxes' "
        "AND sql IS NOT NULL")]

    # Die Bedingung per Muster entfernen statt per Zeichenkettenvergleich:
    # Je nach Herkunft der Datenbank stehen dort Leerzeichen anders, und ein
    # starrer Vergleich lief dann ins Leere.
    neues_sql, ersetzt = _re.subn(
        r"CHECK\s*\(\s*source_type\s+IN\s*\(.*?\)\s*\)", "", sql_alt,
        flags=_re.IGNORECASE | _re.DOTALL)
    if not ersetzt:
        return

    try:
        conn.execute("PRAGMA foreign_keys=OFF")
        # ENTSCHEIDEND: Seit SQLite 3.25 schreibt ALTER TABLE ... RENAME die
        # Fremdschluessel ANDERER Tabellen automatisch mit um. charging_sessions
        # verwies danach auf 'wallboxes_alt' — und nach dem Loeschen der
        # Hilfstabelle ins Leere. Genau daher kam 'no such table:
        # main.wallboxes_alt' bei jedem spaeteren Zugriff.
        conn.execute("PRAGMA legacy_alter_table=ON")
        conn.execute("BEGIN")
        conn.execute("ALTER TABLE wallboxes RENAME TO wallboxes_alt")
        conn.execute(neues_sql)
        spalten = [r["name"] for r in conn.execute("PRAGMA table_info(wallboxes)")]
        liste = ", ".join(f'"{s}"' for s in spalten)
        conn.execute(f"INSERT INTO wallboxes ({liste}) SELECT {liste} FROM wallboxes_alt")
        conn.execute("DROP TABLE wallboxes_alt")
        for idx in indizes:
            try:
                conn.execute(idx)
            except sqlite3.Error:
                pass   # Index existiert bereits oder ist nicht wiederherstellbar
        conn.commit()
    except sqlite3.Error:
        conn.rollback()
        # Nach dem Ruecksetzen kann die Tabelle noch umbenannt sein.
        try:
            if conn.execute("SELECT name FROM sqlite_master WHERE type='table' "
                            "AND name='wallboxes_alt'").fetchone() and \
               not conn.execute("SELECT name FROM sqlite_master WHERE type='table' "
                                "AND name='wallboxes'").fetchone():
                conn.execute("ALTER TABLE wallboxes_alt RENAME TO wallboxes")
                conn.commit()
        except sqlite3.Error:
            pass
    finally:
        conn.execute("PRAGMA legacy_alter_table=OFF")
        conn.execute("PRAGMA foreign_keys=ON")


def _migrate_trips_fahrtart(conn: sqlite3.Connection) -> None:
    """Ergaenzt die Spalte 'fahrtart' in trips (Sprint 7).

    Bisher enthielt die Tabelle ausschliesslich Dienstfahrten. Fuer ein
    lueckenloses Fahrtenbuch muessen aber alle Fahrten erfasst sein — private
    eingeschlossen. Bestandsdaten sind sicher als 'dienstlich' einzustufen,
    da bisher nichts anderes gespeichert wurde."""
    vorhanden = {r["name"] for r in
                 conn.execute("PRAGMA table_info(trips)").fetchall()}
    if "fahrtart" not in vorhanden:
        conn.execute("ALTER TABLE trips ADD COLUMN fahrtart TEXT NOT NULL DEFAULT 'dienstlich'")
        conn.commit()


def _migrate_live_metrics_columns(conn: sqlite3.Connection) -> None:
    """Ergaenzt die OCPP-Livewert-Spalten in wallbox_live_metrics (Sprint 5).
    Bestehende Installationen haben die Tabelle noch ohne Phasenstroeme,
    MID-Zaehlerstand und Peak-Leistung — ALTER TABLE ADD COLUMN ist hier
    unkritisch, da nur Spalten hinzukommen."""
    vorhanden = {r["name"] for r in
                 conn.execute("PRAGMA table_info(wallbox_live_metrics)").fetchall()}
    neue = {
        "current_l1_a": "REAL", "current_l2_a": "REAL", "current_l3_a": "REAL",
        "meter_total_wh": "INTEGER", "peak_power_kw": "REAL",
    }
    for spalte, typ in neue.items():
        if spalte not in vorhanden:
            conn.execute(f"ALTER TABLE wallbox_live_metrics ADD COLUMN {spalte} {typ}")
    conn.commit()


def _needs_charging_location_migration(conn: sqlite3.Connection) -> bool:
    """Prueft, ob charging_sessions noch die Spalte 'charging_location' fehlt
    (Ladeort zuhause/extern — wichtig, damit
    unterwegs geladene, oft schon separat abgerechnete Sessions nicht faelsch-
    lich in den Eigenstrom-Beleg einfliessen)."""
    cols = [row[1] for row in conn.execute("PRAGMA table_info(charging_sessions)").fetchall()]
    return "charging_location" not in cols


def _migrate_charging_location_column(conn: sqlite3.Connection) -> None:
    """Einfaches ALTER TABLE ADD COLUMN reicht hier aus (im Gegensatz zu
    CHECK-Constraint-Aenderungen an BESTEHENDEN Spalten unterstuetzt SQLite
    das Hinzufuegen einer NEUEN Spalte mit CHECK direkt)."""
    conn.execute(
        "ALTER TABLE charging_sessions ADD COLUMN charging_location TEXT NOT NULL DEFAULT 'zuhause' "
        "CHECK (charging_location IN ('zuhause', 'extern'))"
    )
    conn.execute("ALTER TABLE charging_sessions ADD COLUMN charging_location_note TEXT")
    conn.commit()


def _needs_session_vehicle_migration(conn: sqlite3.Connection) -> bool:
    """Prueft, ob charging_sessions noch die Spalte 'vehicle_id' fehlt.

    Ohne sie laesst sich ein Ladevorgang keinem Fahrzeug zuordnen -- die
    Wallbox misst nur Strom und kennt kein Fahrzeug. Zugeordnet wird ueber
    den RFID-Tag der Ladekarte (siehe vehicle_repository.rfid_zuordnung),
    damit das Dashboard bei mehreren Fahrzeugen die richtigen Kennzahlen
    zeigen kann."""
    cols = [row[1] for row in conn.execute("PRAGMA table_info(charging_sessions)").fetchall()]
    return "vehicle_id" not in cols


def _migrate_session_vehicle_column(conn: sqlite3.Connection) -> None:
    """Fuegt die Fahrzeug-Zuordnung hinzu. Bewusst ohne NOT NULL und ohne
    Vorbelegung: Bestandsdaten bleiben unzugeordnet (NULL) statt einem
    womoeglich falschen Fahrzeug zugeschlagen zu werden -- eine falsche
    Zuordnung waere in einem Abrechnungsbeleg schlimmer als gar keine."""
    conn.execute("ALTER TABLE charging_sessions ADD COLUMN vehicle_id INTEGER")
    conn.commit()


def _needs_manually_paused_migration(conn: sqlite3.Connection) -> bool:
    """Prueft, ob loxone_auth_backoff noch die Spalte 'manually_paused' fehlt
    (manueller Not-Aus-Schalter, siehe Modul-Docstring schema.sql § 5.16)."""
    cols = [row[1] for row in conn.execute("PRAGMA table_info(loxone_auth_backoff)").fetchall()]
    return len(cols) > 0 and "manually_paused" not in cols


def _migrate_normalize_timestamps(conn: sqlite3.Connection) -> None:
    """Normalisiert ISO-8601-Timestamps ('2026-08-22T19:32:49.465Z') in der
    charging_sessions-Tabelle auf das einheitliche SQLite-Format
    ('2026-08-22 19:32:49') — behebt den Sortier-Bug der OCPP-Sessions
    (ASCII 'T' > ' ' liess sie stets vor Loxone-API-Sessions erscheinen
    unabhaengig von der tatsaechlichen Uhrzeit).
    Idempotent: SQLite datetime() gibt bei bereits normalisierten Werten
    exakt dieselbe Zeichenkette zurueck, also wird nichts kaputt gemacht."""
    conn.execute(
        """UPDATE charging_sessions
           SET start_timestamp = datetime(start_timestamp),
               end_timestamp   = CASE WHEN end_timestamp IS NOT NULL
                                      THEN datetime(end_timestamp)
                                      ELSE NULL END,
               updated_at      = CURRENT_TIMESTAMP
           WHERE start_timestamp LIKE '%T%' OR end_timestamp LIKE '%T%'"""
    )
    count = conn.execute(
        "SELECT changes()"
    ).fetchone()[0]
    conn.commit()
    if count:
        import logging
        logging.getLogger(__name__).info(
            f"Timestamp-Migration: {count} Session(en) auf ISO-Format normalisiert."
        )


def _migrate_manually_paused_column(conn: sqlite3.Connection) -> None:
    conn.execute("ALTER TABLE loxone_auth_backoff ADD COLUMN manually_paused INTEGER NOT NULL DEFAULT 0")
    conn.commit()


def _needs_documents_doctype_migration(conn: sqlite3.Connection) -> bool:
    """Prüft, ob die documents-Tabelle noch die veraltete doc_type-CHECK-Constraint
    ohne 'fahrtenbuch' hat (sonst schlägt das Speichern des Fahrtenbuch-Belegs fehl)."""
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='documents'"
    ).fetchone()
    if row is None or row[0] is None:
        return False
    return "fahrtenbuch" not in row[0]


def _migrate_documents_doctype_constraint(conn: sqlite3.Connection) -> None:
    """Baut documents neu auf, um 'fahrtenbuch' in die doc_type-CHECK-Constraint
    aufzunehmen — SQLite erlaubt kein direktes ALTER auf CHECK-Constraints.
    Erhält alle Spalten inkl. optionalem pdf_data-BLOB (falls vorhanden)."""
    cols = [r["name"] for r in conn.execute("PRAGMA table_info(documents)").fetchall()]
    has_pdf = "pdf_data" in cols
    pdf_col_def = ",\n            pdf_data BLOB" if has_pdf else ""
    pdf_col_name = ", pdf_data" if has_pdf else ""
    conn.executescript(
        f"""
        ALTER TABLE documents RENAME TO documents_old;

        CREATE TABLE documents (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            doc_type TEXT NOT NULL CHECK (doc_type IN ('ladestrom', 'fahrtkosten_ag', 'fahrtkosten_fa', 'fahrtenbuch')),
            period_start DATE NOT NULL,
            period_end DATE NOT NULL,
            user_id INTEGER NOT NULL REFERENCES users_config(id),
            file_path TEXT NOT NULL,
            checksum_sha256 TEXT NOT NULL,
            generated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP{pdf_col_def}
        );

        INSERT INTO documents
            (id, doc_type, period_start, period_end, user_id, file_path, checksum_sha256, generated_at{pdf_col_name})
        SELECT
            id, doc_type, period_start, period_end, user_id, file_path, checksum_sha256, generated_at{pdf_col_name}
        FROM documents_old;

        DROP TABLE documents_old;
        """
    )
    conn.commit()


def init_db() -> None:
    """Legt das Schema an, falls die Datenbank noch nicht existiert (idempotent),
    und fuehrt notwendige Migrationen an bestehenden Datenbanken durch."""
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = get_connection()
    try:
        # VOR dem Schema: Reste eines abgebrochenen Tabellenumbaus einsammeln.
        # Danach waere es zu spaet — das Schema legt eine leere 'wallboxes' an,
        # und die Daten blieben in der Hilfstabelle liegen.
        _rette_umbau_reste(conn)
        _repariere_verwaiste_verweise(conn)

        with open(SCHEMA_PATH, "r", encoding="utf-8") as f:
            conn.executescript(f.read())
        conn.commit()

        if _needs_source_constraint_migration(conn):
            _migrate_charging_sessions_source_constraint(conn)
        if _needs_event_log_migration(conn):
            _migrate_event_log_source_constraint(conn)
        _migrate_live_metrics_columns(conn)
        _migrate_trips_fahrtart(conn)
        _migrate_wallbox_extern_ocpp(conn)
        _migrate_bmw_trips_nach_trips(conn)
        if _needs_charging_location_migration(conn):
            _migrate_charging_location_column(conn)
        if _needs_session_vehicle_migration(conn):
            _migrate_session_vehicle_column(conn)
        if _needs_manually_paused_migration(conn):
            _migrate_manually_paused_column(conn)
        if _needs_documents_doctype_migration(conn):
            _migrate_documents_doctype_constraint(conn)
        _migrate_normalize_timestamps(conn)
        _cleanup_stale_open_sessions(conn)
    finally:
        conn.close()


def _cleanup_stale_open_sessions(conn: sqlite3.Connection) -> None:
    """Meldet verwaiste Sessions, schliesst sie aber NICHT mehr automatisch.

    Frueher wurden Sessions ueber 8 Stunden beim Start still geschlossen. Fuer
    eine revisionssichere Abrechnung ist das ungeeignet: Der Anwender erfuhr
    nie davon, und die fehlende Energiemenge tauchte kommentarlos im Beleg auf.
    Die Erkennung liegt jetzt im compliance_service (FA-COMP-02) und ist in der
    Oberflaeche sichtbar — dort entscheidet der Anwender selbst, was geschieht.
    Hier bleibt nur ein Protokolleintrag als Hinweis."""
    import logging
    row = conn.execute(
        """SELECT COUNT(*) AS c FROM charging_sessions
           WHERE status='open'
             AND (julianday('now') - julianday(start_timestamp)) * 24 > 24"""
    ).fetchone()
    anzahl = row["c"] if row else 0
    if anzahl:
        logging.getLogger(__name__).info(
            f"{anzahl} Session(s) laenger als 24 h offen — siehe Datenpruefung."
        )


def write_audit_log(
    entity_type: str,
    entity_id: int,
    field_changed: str,
    old_value: str | None,
    new_value: str | None,
    changed_by: str,
) -> None:
    """FA-COMP-01: unveraenderliches Audit-Log, ausschliesslich INSERT."""
    conn = get_connection()
    try:
        conn.execute(
            """
            INSERT INTO audit_log
                (entity_type, entity_id, field_changed, old_value, new_value, changed_by)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (entity_type, entity_id, field_changed, old_value, new_value, changed_by),
        )
        conn.commit()
    finally:
        conn.close()


def get_db_path() -> str:
    """Pfad zur Datenbankdatei — fuer Sicherung und Wiederherstellung."""
    return DB_PATH


def close_all() -> None:
    """Schliesst offene Verbindungen und leert das WAL-Journal.

    Vor dem Ersetzen der Datenbankdatei noetig: Ohne Checkpoint bleiben
    Aenderungen in charging.db-wal stehen und wuerden nach dem Austausch
    zu einem inkonsistenten Stand fuehren."""
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        conn.close()
    except Exception:
        pass
