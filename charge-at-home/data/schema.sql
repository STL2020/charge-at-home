-- Charge@Home Billing Engine — Datenbankschema
-- Basis: Pflichtenheft v2.6, Abschnitt 5
-- WAL-Modus wird beim Verbindungsaufbau in app/services/db_service.py gesetzt (NFA-08/09).

-- 5.1 users_config
CREATE TABLE IF NOT EXISTS users_config (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    abrechnungsfall TEXT NOT NULL CHECK (abrechnungsfall IN ('A', 'B', 'C')),
    employer_rate_choice REAL,
    default_kwh_price REAL NOT NULL DEFAULT 0.34,
    language_pref TEXT NOT NULL DEFAULT 'de',
    theme_pref TEXT NOT NULL DEFAULT 'dark',
    license_status TEXT NOT NULL DEFAULT 'demo' CHECK (license_status IN ('demo', 'licensed')),
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- 5.2 wallboxes (inkl. FA-LS-10 / NFA-11: direkte Loxone-API als Alternative zu OCPP)
CREATE TABLE IF NOT EXISTS wallboxes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    -- 'extern_ocpp': Die Wallbox meldet sich bei einem Dienst auf einem anderen
    -- Geraet (LoxBerry, NAS), von dem diese Anwendung die Ladevorgaenge abholt.
    source_type TEXT NOT NULL CHECK (source_type IN ('ocpp', 'csv', 'manual',
                                                     'loxone_api', 'extern_ocpp')),
    serial_number TEXT,
    ocpp_charge_point_id TEXT UNIQUE,
    loxone_host TEXT,
    loxone_username TEXT,
    loxone_password_encrypted TEXT,
    location TEXT,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- 5.3 charging_sessions
CREATE TABLE IF NOT EXISTS charging_sessions (
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
    -- FA-LS-BMW-02: Ladeort, primaer fuer BMW-Ladehistorie-Import relevant
    -- (Ruecksprache Auftraggeber: enthaelt auch Ladungen unterwegs, z. B.
    -- Raststaette — diese sind oft schon separat/extern abgerechnet, z. B.
    -- ueber Tankkarte, und duerfen NICHT in den Eigenstrom-Beleg fuer zu
    -- Hause geladenen Strom einfliessen). 'zuhause' = Standard fuer alle
    -- ueber die eigene Wallbox erfassten Sessions.
    charging_location TEXT NOT NULL DEFAULT 'zuhause' CHECK (charging_location IN ('zuhause', 'extern')),
    charging_location_note TEXT,
    status TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'closed', 'anomaly')),
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_sessions_period ON charging_sessions(start_timestamp, wallbox_id);

-- 5.4 trips (nur Fall C relevant)
CREATE TABLE IF NOT EXISTS trips (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users_config(id),
    trip_date DATE NOT NULL,
    start_address TEXT NOT NULL,
    end_address TEXT NOT NULL,
    distance_km REAL NOT NULL,
    purpose TEXT NOT NULL,
    rate_chosen REAL NOT NULL,
    -- Fuer ein lueckenloses Fahrtenbuch (R 9.5 LStR) muessen ALLE Fahrten
    -- erfasst sein, auch private. Diese zaehlen nur fuer die Jahresfahrleistung
    -- und duerfen niemals in Belege oder Erstattungen einfliessen.
    fahrtart TEXT NOT NULL DEFAULT 'dienstlich'
        CHECK (fahrtart IN ('dienstlich', 'privat', 'arbeitsweg', 'offen')),
    employer_amount_eur REAL GENERATED ALWAYS AS (distance_km * rate_chosen) VIRTUAL,
    diff_amount_eur REAL GENERATED ALWAYS AS (distance_km * (0.30 - rate_chosen)) VIRTUAL,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- 5.5 audit_log (FA-COMP-01) — ausschliesslich INSERT aus der Anwendungsschicht
CREATE TABLE IF NOT EXISTS audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    entity_type TEXT NOT NULL,
    entity_id INTEGER NOT NULL,
    field_changed TEXT NOT NULL,
    old_value TEXT,
    new_value TEXT,
    changed_by TEXT NOT NULL,
    changed_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- 5.6 documents (generierte Belege)
CREATE TABLE IF NOT EXISTS documents (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    doc_type TEXT NOT NULL CHECK (doc_type IN ('ladestrom', 'fahrtkosten_ag', 'fahrtkosten_fa', 'fahrtenbuch')),
    period_start DATE NOT NULL,
    period_end DATE NOT NULL,
    user_id INTEGER NOT NULL REFERENCES users_config(id),
    file_path TEXT NOT NULL,
    checksum_sha256 TEXT NOT NULL,
    generated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- 5.7 persons (leichtgewichtige Personen-Stammdaten fuer Belege, z. B. Familie/zweite Person;
-- bewusst getrennt von users_config, das die eine installierte Instanz konfiguriert, siehe § 2.1)
CREATE TABLE IF NOT EXISTS persons (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    email TEXT,
    personalnummer TEXT,
    kfz_kennzeichen TEXT,
    telefon TEXT,
    home_address TEXT,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- 5.8 wallbox_status (Live-Status je Wallbox, FA-LS-09) — eigene Tabelle statt
-- neuer Spalte an wallboxes, damit bestehende, ueber Updates hinweg erhaltene
-- Datenbanken (siehe Entpack-Skript) unveraendert kompatibel bleiben.
CREATE TABLE IF NOT EXISTS wallbox_status (
    wallbox_id INTEGER PRIMARY KEY REFERENCES wallboxes(id),
    status TEXT NOT NULL DEFAULT 'unbekannt',
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- 5.9 loxone_wallbox_config (welcher Loxone-Baustein/UUID gehört zu welcher
-- direkten-API-Wallbox, FA-LS-10) — eigene Tabelle aus demselben
-- Migrations-Grund wie wallbox_status.
CREATE TABLE IF NOT EXISTS loxone_wallbox_config (
    wallbox_id INTEGER PRIMARY KEY REFERENCES wallboxes(id),
    uuid TEXT NOT NULL
);

-- 5.10 loxone_poll_state (Zwischenspeicher für die Polling-basierte
-- Session-Erkennung, siehe services/loxone_poll_service.py — Fallback-Weg)
CREATE TABLE IF NOT EXISTS loxone_poll_state (
    wallbox_id INTEGER PRIMARY KEY REFERENCES wallboxes(id),
    last_meter_wh INTEGER,
    unchanged_count INTEGER NOT NULL DEFAULT 0,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- 5.11 loxone_last_charge_log (letzter bekannter Lcl-Text je Wallbox2-Baustein,
-- fuer Aenderungserkennung — eigene Tabelle statt Spalte an loxone_poll_state,
-- aus demselben Migrations-Grund wie § 5.8/5.9.)
CREATE TABLE IF NOT EXISTS loxone_last_charge_log (
    wallbox_id INTEGER PRIMARY KEY REFERENCES wallboxes(id),
    lcl_text TEXT,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- 5.11b loxone_log_reconcile_state (Ausfallsicherheit, siehe Pflichtenheft-
-- Changelog): Live-Polling sieht immer nur den JEWEILS LETZTEN Lcl-Eintrag —
-- laeuft die App waehrend zwei aufeinanderfolgenden Ladevorgaengen nicht,
-- wird der aeltere stillschweigend uebersprungen. Der auf dem Miniserver
-- selbst per Logger-Baustein persistierte Log (unabhaengig von unserer App)
-- wird deshalb zusaetzlich periodisch vollstaendig eingelesen und mit der
-- Datenbank abgeglichen (Duplikat-sicher), um Luecken durch Ausfallzeiten
-- zu schliessen.
CREATE TABLE IF NOT EXISTS loxone_log_reconcile_state (
    wallbox_id INTEGER PRIMARY KEY REFERENCES wallboxes(id),
    log_path TEXT NOT NULL DEFAULT '/dev/fsget/log/wallbox.log',
    last_reconciled_at DATETIME,
    last_imported_count INTEGER NOT NULL DEFAULT 0
);

-- 5.12 wallbox_live_metrics (Live-Momentaufnahme je Wallbox — aktuelle Leistung,
-- Verbindungsstatus, letzter Sync-Zeitpunkt — fuer die Live-Ansicht in der
-- Oberflaeche, damit sichtbar wird, DASS und WANN zuletzt synchronisiert wurde.)
CREATE TABLE IF NOT EXISTS wallbox_live_metrics (
    wallbox_id INTEGER PRIMARY KEY REFERENCES wallboxes(id),
    current_power_kw REAL,
    connected INTEGER,
    raw_snapshot TEXT,
    -- OCPP 1.6-J Livewerte (Sprint 5, Wallbox-Paritaet):
    -- Phasenstroeme aus Current.Import, absoluter MID-Zaehler aus
    -- Energy.Active.Import.Register, Peak der laufenden Session.
    current_l1_a REAL,
    current_l2_a REAL,
    current_l3_a REAL,
    meter_total_wh INTEGER,
    peak_power_kw REAL,
    last_sync_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- 5.13 event_log (Protokoll-/Log-Ansicht, FA-LOG-01 — verpflichtender Punkt)
-- Zeigt Hintergrund-Ereignisse (OCPP-Verbindungsversuche, Loxone-Poll-Zyklen,
-- Fehler) sichtbar in der Oberflaeche, statt nur in der Server-Konsole.
-- 'manual' ergaenzt fuer nutzerausgeloeste Aktionen (z. B. BMW-Ladehistorie-
-- Import) — vorher fehlte dieser Wert, wodurch die Bestaetigungsmeldung
-- lautlos verworfen wurde (log_event faengt Fehler bewusst ab, siehe dort).
CREATE TABLE IF NOT EXISTS event_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source TEXT NOT NULL CHECK (source IN ('ocpp', 'loxone_api', 'system', 'manual')),
    level TEXT NOT NULL DEFAULT 'info' CHECK (level IN ('info', 'warning', 'error')),
    message TEXT NOT NULL,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- 5.14 app_settings (generischer Key-Value-Speicher fuer Einstellungen wie
-- das Loxone-Poll-Intervall — eigene Tabelle statt Spalte an users_config,
-- aus demselben Migrations-Grund wie die anderen neu hinzugekommenen
-- Tabellen: neue Spalten an bestehenden Tabellen werden von bereits ueber
-- Updates hinweg erhaltenen Datenbanken nicht automatisch uebernommen.)
CREATE TABLE IF NOT EXISTS app_settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

-- 5.15 ocpp_message_counts (Diagnose, siehe services/ocpp_log_service.py):
-- persistente Zaehlung, welche OCPP-Nachrichtentypen von welchem Chargepoint
-- ueberhaupt jemals eingegangen sind — macht auf einen Blick nachvollziehbar,
-- ob z. B. MeterValues/StartTransaction/StopTransaction jemals ankommen.
CREATE TABLE IF NOT EXISTS ocpp_message_counts (
    message_type TEXT NOT NULL,
    charge_point_id TEXT NOT NULL,
    count INTEGER NOT NULL DEFAULT 0,
    first_seen_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    last_seen_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (message_type, charge_point_id)
);

-- 5.18 ocpp_client_config (FA-OCPP-CLIENT-01): Konfiguration fuer den
-- OCPP-CLIENT-Modus — zusaetzlich zum bestehenden Server-Modus (Loxone
-- verbindet sich zu UNS) kann eine Wallbox jetzt auch als Charge Point zu
-- einem EXTERNEN OCPP-Dienst verbinden. Zweck (Ruecksprache Auftraggeber):
-- die ueber den Log-Import zuverlaessig erfassten Sessions (die Loxone
-- selbst NIE per eigenem OCPP sendet) als echte StartTransaction/
-- MeterValues/StopTransaction an einen externen OCPP-Dienst weiterreichen.
CREATE TABLE IF NOT EXISTS ocpp_client_config (
    wallbox_id INTEGER PRIMARY KEY REFERENCES wallboxes(id),
    remote_url TEXT NOT NULL,
    remote_charge_point_id TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 1,
    last_relayed_session_id INTEGER,
    last_connect_attempt_at DATETIME,
    last_connect_success_at DATETIME,
    last_error TEXT
);
-- 5.16 loxone_auth_backoff (Ausfallsicherheit, siehe Pflichtenheft-Changelog):
-- verhindert, dass der Poller bei falschen/ungueltigen Zugangsdaten den
-- Miniserver mit wiederholten Login-Versuchen im Sekundentakt bombardiert —
-- genau das hat einmal zu einer IP-Sperre durch den Miniserver selbst
-- gefuehrt ("too many failed login attempts"). Nach mehreren aufeinander-
-- folgenden Fehlversuchen wird die Wartezeit bis zum naechsten Versuch
-- schrittweise erhoeht (exponentielles Backoff), statt bedingungslos jeden
-- Zyklus erneut zu versuchen.
CREATE TABLE IF NOT EXISTS loxone_auth_backoff (
    wallbox_id INTEGER PRIMARY KEY REFERENCES wallboxes(id),
    consecutive_failures INTEGER NOT NULL DEFAULT 0,
    last_attempt_at DATETIME,
    last_success_at DATETIME,
    -- Manueller Not-Aus (Ruecksprache: automatisches Backoff reicht nicht,
    -- wenn der Miniserver GERADE gesperrt ist und der Nutzer sofortige,
    -- garantierte Ruhe braucht, statt auf Backoff-Zeiten zu vertrauen).
    -- 1 = Polling komplett pausiert, unabhaengig vom Fehlversuch-Zaehler.
    manually_paused INTEGER NOT NULL DEFAULT 0
);

-- PKW-Kosten (für Vollkostenrechnung)
CREATE TABLE IF NOT EXISTS pkw_costs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    person_id INTEGER REFERENCES persons(id) ON DELETE CASCADE,
    kategorie TEXT NOT NULL,      -- 'leasing','versicherung','wartung','reifen','tuev','sonstige','allowance'
    bezeichnung TEXT NOT NULL,    -- Freitext z.B. "BMW AG Leasing"
    betrag REAL NOT NULL,         -- Betrag in €
    intervall TEXT NOT NULL DEFAULT 'monatlich', -- monatlich, quartaerlich, jaehrlich
    aktiv INTEGER NOT NULL DEFAULT 1,
    notiz TEXT,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
);
-- Car Allowance Konfiguration
CREATE TABLE IF NOT EXISTS car_allowance (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    person_id INTEGER REFERENCES persons(id) ON DELETE CASCADE,
    monatlicher_betrag REAL NOT NULL DEFAULT 0,
    lohnsteuerklasse INTEGER NOT NULL DEFAULT 1,  -- 1-6
    versteuert INTEGER NOT NULL DEFAULT 0,         -- 0=steuerfrei, 1=steuerpflichtig
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- 5.20 bmw_trips (Sprint 6) — Rohdaten des BMW-ConnectedDrive-Imports.
-- Bewusst getrennt von 'trips': Hier landen ALLE synchronisierten Fahrten
-- unbewertet, auch private. Erst die Klassifizierung durch den Nutzer
-- ueberfuehrt eine Fahrt als Dienstfahrt in 'trips' (abrechnungsrelevant).
-- 'bmw_trip_id' ist die stabile ID aus der Quelle und verhindert Doppelimporte.
CREATE TABLE IF NOT EXISTS bmw_trips (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users_config(id),
    -- Kein FK auf vehicles: die Tabelle entsteht erst per Migration,
    -- daher wuerde die Referenz beim Erstaufbau ins Leere greifen.
    vehicle_id INTEGER,
    bmw_trip_id TEXT NOT NULL UNIQUE,
    start_time DATETIME,
    end_time DATETIME,
    start_mileage INTEGER,
    end_mileage INTEGER,
    distance_km REAL NOT NULL DEFAULT 0,
    start_address TEXT,
    end_address TEXT,
    -- UNVERARBEITET → der Nutzer hat noch nicht entschieden
    -- DIENSTLICH    → wurde als Dienstfahrt uebernommen (siehe trip_id)
    -- PRIVAT        → bleibt privat, zaehlt nur fuer den Gesamtkilometer-Nachweis
    category TEXT NOT NULL DEFAULT 'UNVERARBEITET'
        CHECK (category IN ('UNVERARBEITET', 'DIENSTLICH', 'PRIVAT')),
    trip_id INTEGER REFERENCES trips(id) ON DELETE SET NULL,
    imported_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_bmw_trips_category ON bmw_trips(category);
