"""Repository fuer trips. Reine SQL-Zugriffsschicht (§ 8.1)."""

from services.db_service import get_connection


def insert_trip(
    user_id: int, trip_date: str, start_address: str, end_address: str,
    distance_km: float, purpose: str, rate_chosen: float, vehicle_id: int = None,
    fahrtart: str = "dienstlich",
) -> int:
    """Legt eine Fahrt an.

    `fahrtart` unterscheidet dienstliche von privaten Fahrten. Private Fahrten
    gehoeren ins Fahrtenbuch (Lueckenlosigkeit nach R 9.5 LStR), erhalten aber
    zwingend den Satz 0,00 €/km — sie duerfen nie erstattet werden."""
    # Nur dienstliche Fahrten duerfen einen Erstattungssatz tragen. 'offen'
    # kennzeichnet importierte Fahrten, die der Nutzer noch zuordnen muss.
    if fahrtart != "dienstlich":
        rate_chosen = 0.0
    conn = get_connection()
    try:
        cols = [r["name"] for r in conn.execute("PRAGMA table_info(trips)").fetchall()]
        if "vehicle_id" not in cols:
            try: conn.execute("ALTER TABLE trips ADD COLUMN vehicle_id INTEGER"); conn.commit()
            except Exception: pass
        if "fahrtart" not in cols:
            try:
                conn.execute("ALTER TABLE trips ADD COLUMN fahrtart TEXT NOT NULL DEFAULT 'dienstlich'")
                conn.commit()
            except Exception: pass
        cur = conn.execute(
            """INSERT INTO trips
               (user_id, trip_date, start_address, end_address, distance_km,
                purpose, rate_chosen, vehicle_id, fahrtart)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (user_id, trip_date, start_address, end_address, distance_km,
             purpose, rate_chosen, vehicle_id, fahrtart),
        )
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def set_fahrtart(trip_ids: list[int], fahrtart: str, standard_satz: float | None = None) -> int:
    """Sammeländerung der Fahrtart.

    Private Fahrten werden zwingend auf 0,00 €/km gesetzt, damit sie nicht
    versehentlich abgerechnet werden. Bei dienstlichen Fahrten ohne Satz wird
    optional der Standardsatz eingetragen — sonst stuende dort 'keine
    Erstattung', was bei einer Dienstfahrt fast immer unbeabsichtigt ist."""
    if not trip_ids:
        return 0
    platzhalter = ",".join("?" * len(trip_ids))
    conn = get_connection()
    try:
        if fahrtart == "dienstlich":
            conn.execute(f"UPDATE trips SET fahrtart = ? WHERE id IN ({platzhalter})",
                         [fahrtart, *trip_ids])
            if standard_satz:
                conn.execute(
                    f"""UPDATE trips SET rate_chosen = ?
                        WHERE id IN ({platzhalter}) AND COALESCE(rate_chosen, 0) = 0""",
                    [float(standard_satz), *trip_ids])
        else:
            conn.execute(
                f"UPDATE trips SET fahrtart = ?, rate_chosen = 0 WHERE id IN ({platzhalter})",
                [fahrtart, *trip_ids])
        conn.commit()
        return len(trip_ids)
    finally:
        conn.close()


def set_rate(trip_ids: list[int], rate: float) -> int:
    """Sammeländerung des Erstattungssatzes — wirkt nur auf Dienstfahrten.

    Private Fahrten bleiben unberuehrt bei 0,00 €/km; ein Erstattungssatz waere
    dort steuerlich unzulaessig."""
    if not trip_ids:
        return 0
    platzhalter = ",".join("?" * len(trip_ids))
    conn = get_connection()
    try:
        cur = conn.execute(
            f"""UPDATE trips SET rate_chosen = ?
                WHERE id IN ({platzhalter}) AND fahrtart = 'dienstlich'""",
            [float(rate), *trip_ids])
        conn.commit()
        return cur.rowcount
    finally:
        conn.close()


def list_trips(user_id: int, period_start: str | None = None, period_end: str | None = None,
               vehicle_id: int | None = None, fahrtart: str | None = None,
               nur_dienstlich: bool = False) -> list[dict]:
    """Fahrten des Nutzers.

    `nur_dienstlich=True` ist fuer Belege und Abrechnungen zwingend: Private
    Fahrten stehen zwar im Fahrtenbuch, duerfen aber in keiner Erstattung und
    keinem Steuerbeleg auftauchen."""
    query = "SELECT * FROM trips WHERE user_id = ?"
    params: list = [user_id]
    if nur_dienstlich:
        query += " AND COALESCE(fahrtart, 'dienstlich') = 'dienstlich'"
    elif fahrtart:
        query += " AND COALESCE(fahrtart, 'dienstlich') = ?"
        params.append(fahrtart)
    if period_start:
        query += " AND date(trip_date) >= date(?)"
        params.append(period_start)
    if period_end:
        query += " AND date(trip_date) <= date(?)"
        params.append(period_end)
    if vehicle_id is not None:
        # Fahrten dieses Fahrzeugs — Alt-Einträge ohne vehicle_id werden mitgenommen,
        # damit bestehende Daten nicht verloren gehen.
        query += " AND (vehicle_id = ? OR vehicle_id IS NULL)"
        params.append(vehicle_id)
    query += " ORDER BY trip_date DESC"
    conn = get_connection()
    try:
        return [dict(r) for r in conn.execute(query, params).fetchall()]
    finally:
        conn.close()


def get_trip(trip_id: int) -> dict | None:
    conn = get_connection()
    try:
        row = conn.execute("SELECT * FROM trips WHERE id = ?", (trip_id,)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def update_trip(
    trip_id: int, trip_date: str, start_address: str, end_address: str,
    distance_km: float, purpose: str, rate_chosen: float,
) -> None:
    conn = get_connection()
    try:
        conn.execute(
            """UPDATE trips
               SET trip_date = ?, start_address = ?, end_address = ?,
                   distance_km = ?, purpose = ?, rate_chosen = ?
               WHERE id = ?""",
            (trip_date, start_address, end_address, distance_km, purpose, rate_chosen, trip_id),
        )
        conn.commit()
    finally:
        conn.close()


def delete_trip(trip_id: int) -> None:
    conn = get_connection()
    try:
        conn.execute("DELETE FROM trips WHERE id = ?", (trip_id,))
        conn.commit()
    finally:
        conn.close()
