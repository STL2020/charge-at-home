"""PKW-Kosten Repository – Vollkostenrechnung."""
from services.db_service import get_connection


def _ensure_tables(conn) -> None:
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS pkw_costs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            person_id INTEGER,
            kategorie TEXT NOT NULL,
            bezeichnung TEXT NOT NULL,
            betrag REAL NOT NULL,
            intervall TEXT NOT NULL DEFAULT 'monatlich',
            aktiv INTEGER NOT NULL DEFAULT 1,
            notiz TEXT,
            created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS car_allowance (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            person_id INTEGER,
            monatlicher_betrag REAL NOT NULL DEFAULT 0,
            lohnsteuerklasse INTEGER NOT NULL DEFAULT 1,
            versteuert INTEGER NOT NULL DEFAULT 0,
            created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
    """)
    conn.commit()


def list_pkw_costs(person_id: int = None, vehicle_id: int = None) -> list[dict]:
    conn = get_connection()
    try:
        _ensure_tables(conn)
        # Migration: vehicle_id-Spalte sicherstellen
        cols = [r["name"] for r in conn.execute("PRAGMA table_info(pkw_costs)").fetchall()]
        if "vehicle_id" not in cols:
            try: conn.execute("ALTER TABLE pkw_costs ADD COLUMN vehicle_id INTEGER"); conn.commit()
            except Exception: pass
        if vehicle_id is not None:
            rows = conn.execute(
                "SELECT * FROM pkw_costs WHERE vehicle_id=? AND aktiv=1 ORDER BY kategorie, id",
                (vehicle_id,)
            ).fetchall()
        elif person_id is not None:
            rows = conn.execute(
                "SELECT * FROM pkw_costs WHERE person_id=? AND aktiv=1 ORDER BY kategorie, id",
                (person_id,)
            ).fetchall()
        else:
            rows = []
        return [dict(r) for r in rows]
    finally:
        conn.close()


def upsert_pkw_cost(person_id: int, kategorie: str, bezeichnung: str,
                     betrag: float, intervall: str, notiz: str = "",
                     vehicle_id: int = None) -> int:
    conn = get_connection()
    try:
        _ensure_tables(conn)
        cols = [r["name"] for r in conn.execute("PRAGMA table_info(pkw_costs)").fetchall()]
        if "vehicle_id" not in cols:
            try: conn.execute("ALTER TABLE pkw_costs ADD COLUMN vehicle_id INTEGER"); conn.commit()
            except Exception: pass
        cur = conn.execute(
            """INSERT INTO pkw_costs (person_id,vehicle_id,kategorie,bezeichnung,betrag,intervall,notiz)
               VALUES (?,?,?,?,?,?,?)""",
            (person_id, vehicle_id, kategorie, bezeichnung, betrag, intervall, notiz or "")
        )
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def delete_pkw_cost(cost_id: int) -> None:
    conn = get_connection()
    try:
        _ensure_tables(conn)
        conn.execute("DELETE FROM pkw_costs WHERE id=?", (cost_id,))
        conn.commit()
    finally:
        conn.close()


def get_car_allowance(person_id: int = None, vehicle_id: int = None) -> dict:
    conn = get_connection()
    try:
        _ensure_tables(conn)
        cols = [r["name"] for r in conn.execute("PRAGMA table_info(car_allowance)").fetchall()]
        if "vehicle_id" not in cols:
            try: conn.execute("ALTER TABLE car_allowance ADD COLUMN vehicle_id INTEGER"); conn.commit()
            except Exception: pass
        if vehicle_id is not None:
            row = conn.execute(
                "SELECT * FROM car_allowance WHERE vehicle_id=? ORDER BY id DESC LIMIT 1",
                (vehicle_id,)
            ).fetchone()
        else:
            row = conn.execute(
                "SELECT * FROM car_allowance WHERE person_id=? ORDER BY id DESC LIMIT 1",
                (person_id,)
            ).fetchone()
        return dict(row) if row else {
            "monatlicher_betrag": 0, "lohnsteuerklasse": 1, "versteuert": 0
        }
    finally:
        conn.close()


def save_car_allowance(person_id: int, betrag: float,
                        lohnsteuerklasse: int, versteuert: bool,
                        vehicle_id: int = None) -> None:
    conn = get_connection()
    try:
        _ensure_tables(conn)
        cols = [r["name"] for r in conn.execute("PRAGMA table_info(car_allowance)").fetchall()]
        if "vehicle_id" not in cols:
            try: conn.execute("ALTER TABLE car_allowance ADD COLUMN vehicle_id INTEGER"); conn.commit()
            except Exception: pass
        if vehicle_id is not None:
            existing = conn.execute("SELECT id FROM car_allowance WHERE vehicle_id=?", (vehicle_id,)).fetchone()
        else:
            existing = conn.execute("SELECT id FROM car_allowance WHERE person_id=?", (person_id,)).fetchone()
        if existing:
            conn.execute(
                "UPDATE car_allowance SET monatlicher_betrag=?,lohnsteuerklasse=?,versteuert=?,vehicle_id=? WHERE id=?",
                (betrag, lohnsteuerklasse, 1 if versteuert else 0, vehicle_id, existing["id"])
            )
        else:
            conn.execute(
                "INSERT INTO car_allowance (person_id,vehicle_id,monatlicher_betrag,lohnsteuerklasse,versteuert) VALUES (?,?,?,?,?)",
                (person_id, vehicle_id, betrag, lohnsteuerklasse, 1 if versteuert else 0)
            )
        conn.commit()
    finally:
        conn.close()


def monatliche_kosten_gesamt(person_id: int = None, vehicle_id: int = None) -> float:
    """Summiert alle aktiven Kosten normiert auf Monat."""
    costs = list_pkw_costs(person_id=person_id, vehicle_id=vehicle_id)
    total = 0.0
    for c in costs:
        if c["intervall"] == "monatlich":
            total += c["betrag"]
        elif c["intervall"] == "quartaerlich":
            total += c["betrag"] / 3
        elif c["intervall"] == "jaehrlich":
            total += c["betrag"] / 12
    return round(total, 2)


# ═══════════════════════════════════════════════════════════════════════════
# AG-ZUSCHUESSE (mehrere Kategorien je Fahrzeug)
# ═══════════════════════════════════════════════════════════════════════════
# Ergaenzt die alte Einzel-Tabelle car_allowance: Ein Mitarbeiter kann mehrere
# Zuschuesse parallel bekommen (Car Allowance, Tankkarte, Jobticket, Pauschale),
# jeweils getrennt steuerpflichtig oder steuerfrei (§ 3 Nr. 50 EStG).

ZUSCHUSS_KATEGORIEN = {
    "car_allowance": "Car Allowance",
    "tankkarte":     "Tank-/Ladekarten-Pauschale",
    "jobticket":     "Fahrtkostenzuschuss ÖPNV / Jobticket",
    "aufwand":       "Pauschale Aufwandsentschädigung",
    "sonstige":      "Sonstiger Zuschuss",
}


def _ensure_zuschuss_table(conn) -> None:
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS ag_zuschuesse (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            person_id INTEGER,
            vehicle_id INTEGER,
            kategorie TEXT NOT NULL,
            bezeichnung TEXT,
            monatlicher_betrag REAL NOT NULL DEFAULT 0,
            versteuert INTEGER NOT NULL DEFAULT 1,
            aktiv INTEGER NOT NULL DEFAULT 1,
            created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
    """)
    conn.commit()


def list_zuschuesse(vehicle_id: int = None, person_id: int = None) -> list[dict]:
    """Alle aktiven Zuschuesse. Migriert einmalig einen evtl. vorhandenen
    Alt-Eintrag aus car_allowance in die neue Tabelle."""
    conn = get_connection()
    try:
        _ensure_zuschuss_table(conn)
        _ensure_tables(conn)
        # Einmalige Migration des Alt-Eintrags
        if vehicle_id is not None:
            vorhanden = conn.execute(
                "SELECT COUNT(*) c FROM ag_zuschuesse WHERE vehicle_id=?", (vehicle_id,)
            ).fetchone()["c"]
            if vorhanden == 0:
                cols = [r["name"] for r in conn.execute("PRAGMA table_info(car_allowance)").fetchall()]
                if "vehicle_id" in cols:
                    alt = conn.execute(
                        "SELECT * FROM car_allowance WHERE vehicle_id=? ORDER BY id DESC LIMIT 1",
                        (vehicle_id,)
                    ).fetchone()
                    if alt and (alt["monatlicher_betrag"] or 0) > 0:
                        conn.execute(
                            """INSERT INTO ag_zuschuesse
                               (person_id, vehicle_id, kategorie, bezeichnung,
                                monatlicher_betrag, versteuert)
                               VALUES (?,?,?,?,?,?)""",
                            (alt["person_id"], vehicle_id, "car_allowance", "Car Allowance",
                             alt["monatlicher_betrag"], alt["versteuert"])
                        )
                        conn.commit()
        if vehicle_id is not None:
            rows = conn.execute(
                "SELECT * FROM ag_zuschuesse WHERE vehicle_id=? AND aktiv=1 ORDER BY id",
                (vehicle_id,)).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM ag_zuschuesse WHERE person_id=? AND aktiv=1 ORDER BY id",
                (person_id,)).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def add_zuschuss(kategorie: str, betrag: float, versteuert: bool,
                 vehicle_id: int = None, person_id: int = None,
                 bezeichnung: str = None) -> int:
    conn = get_connection()
    try:
        _ensure_zuschuss_table(conn)
        label = bezeichnung or ZUSCHUSS_KATEGORIEN.get(kategorie, kategorie)
        cur = conn.execute(
            """INSERT INTO ag_zuschuesse
               (person_id, vehicle_id, kategorie, bezeichnung, monatlicher_betrag, versteuert)
               VALUES (?,?,?,?,?,?)""",
            (person_id, vehicle_id, kategorie, label, betrag, 1 if versteuert else 0))
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def delete_zuschuss(zuschuss_id: int) -> None:
    conn = get_connection()
    try:
        _ensure_zuschuss_table(conn)
        conn.execute("DELETE FROM ag_zuschuesse WHERE id=?", (zuschuss_id,))
        conn.commit()
    finally:
        conn.close()


def zuschuesse_summe(vehicle_id: int = None, person_id: int = None,
                     steuersatz: float = 0.42) -> dict:
    """Summiert alle Zuschuesse und rechnet Brutto auf Netto um:
    steuerpflichtige Zuschuesse werden um den Grenzsteuersatz gemindert,
    steuerfreie (§ 3 Nr. 50 EStG) fliessen voll ein."""
    items = list_zuschuesse(vehicle_id=vehicle_id, person_id=person_id)
    brutto = netto = 0.0
    for z in items:
        b = float(z.get("monatlicher_betrag") or 0)
        brutto += b
        netto += b * (1 - steuersatz) if z.get("versteuert") else b
    return {
        "brutto_monat": round(brutto, 2),
        "netto_monat": round(netto, 2),
        "netto_jahr": round(netto * 12, 2),
        "anzahl": len(items),
        "posten": items,
    }
