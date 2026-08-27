"""Datenqualitaets-Pruefungen fuer die revisionssichere Abrechnung.

Pflichtenheft-Referenz: FA-COMP-02 (Zombie-Sessions), FA-COMP-03 (Zaehler-
ueberlauf). Beide Pruefungen dienen demselben Zweck: Auffaellige Datensaetze
sollen SICHTBAR werden, statt still im Hintergrund korrigiert oder gar
geloescht zu werden. Ein Beleg fuer das Finanzamt darf keine Zahlen enthalten,
deren Herkunft niemand mehr nachvollziehen kann.

Grundsatz: Diese Funktionen markieren und melden, sie loeschen niemals.
Die Entscheidung, was mit einem auffaelligen Datensatz geschieht, trifft der
Anwender.
"""
from __future__ import annotations

from datetime import datetime

from services.db_service import get_connection
import services.event_log_service as event_log_service

# Eine Ladesession, die laenger offen ist, kann nicht mehr plausibel laufen:
# Selbst ein 100-kWh-Akku an einer 2,3-kW-Schuko-Dose ist nach ~44 h voll.
ZOMBIE_STUNDEN = 24

# Sprung im Zaehlerstand, ab dem ein Ueberlauf oder Zaehlertausch wahrscheinlicher
# ist als eine echte Ladung (in Wh). 200 kWh in einer Session ist bei privaten
# Wallboxen praktisch ausgeschlossen.
UEBERLAUF_SCHWELLE_WH = 200_000

# Typische Zaehlerkapazitaeten, bei denen ein Ueberlauf auftreten kann
UEBERLAUF_GRENZEN_WH = [1_000_000_00, 1_000_000_0, 1_000_000]  # 10 MWh, 1 MWh, ...


def finde_zombie_sessions(user_id: int | None = None) -> list[dict]:
    """Sessions, die ueber ZOMBIE_STUNDEN offen sind.

    Ursache ist meist ein Verbindungsabbruch: Die Wallbox meldet
    StartTransaction, aber das zugehoerige StopTransaction geht verloren."""
    conn = get_connection()
    try:
        sql = """SELECT cs.*, wb.name AS wallbox_name,
                        ROUND((julianday('now') - julianday(cs.start_timestamp)) * 24, 1) AS offen_stunden
                 FROM charging_sessions cs
                 LEFT JOIN wallboxes wb ON wb.id = cs.wallbox_id
                 WHERE cs.status = 'open'
                   AND (julianday('now') - julianday(cs.start_timestamp)) * 24 > ?"""
        params: list = [ZOMBIE_STUNDEN]
        if user_id is not None:
            sql += " AND cs.user_id = ?"
            params.append(user_id)
        sql += " ORDER BY cs.start_timestamp"
        return [dict(r) for r in conn.execute(sql, params).fetchall()]
    finally:
        conn.close()


def finde_zaehler_anomalien(user_id: int | None = None) -> list[dict]:
    """Sessions mit unplausiblem Zaehlerverlauf.

    Zwei Faelle:
      'rueckwaerts' — Endstand kleiner als Startstand. Klassischer Ueberlauf
                      (Zaehler springt auf 0) oder ein Zaehlertausch.
      'sprung'      — Differenz groesser als UEBERLAUF_SCHWELLE_WH, also mehr
                      Energie als ein Pkw-Akku aufnehmen kann."""
    conn = get_connection()
    try:
        sql = """SELECT cs.*, wb.name AS wallbox_name
                 FROM charging_sessions cs
                 LEFT JOIN wallboxes wb ON wb.id = cs.wallbox_id
                 WHERE cs.meter_stop_wh IS NOT NULL
                   AND cs.meter_start_wh IS NOT NULL
                   AND (cs.meter_stop_wh < cs.meter_start_wh
                        OR (cs.meter_stop_wh - cs.meter_start_wh) > ?)"""
        params: list = [UEBERLAUF_SCHWELLE_WH]
        if user_id is not None:
            sql += " AND cs.user_id = ?"
            params.append(user_id)
        sql += " ORDER BY cs.start_timestamp DESC"
        rows = [dict(r) for r in conn.execute(sql, params).fetchall()]
    finally:
        conn.close()

    for r in rows:
        diff = (r["meter_stop_wh"] or 0) - (r["meter_start_wh"] or 0)
        if diff < 0:
            r["anomalie"] = "rueckwaerts"
            r["korrektur_vorschlag_wh"] = _ueberlauf_korrektur(
                r["meter_start_wh"], r["meter_stop_wh"])
            r["beschreibung"] = (
                f"Zählerstand fällt von {r['meter_start_wh']/1000:.1f} auf "
                f"{r['meter_stop_wh']/1000:.1f} kWh — vermutlich Überlauf oder Zählertausch.")
        else:
            r["anomalie"] = "sprung"
            r["korrektur_vorschlag_wh"] = None
            r["beschreibung"] = (
                f"{diff/1000:.1f} kWh in einer Session — unplausibel hoch, "
                f"bitte Zählerstände prüfen.")
    return rows


def _ueberlauf_korrektur(start_wh: int, stop_wh: int) -> int | None:
    """Schaetzt die tatsaechliche Energiemenge bei einem Zaehlerueberlauf.

    Rechnung: (Zaehlerkapazitaet - Startstand) + Endstand. Es wird die
    kleinste Kapazitaet gewaehlt, die ueber dem Startstand liegt und ein
    plausibles Ergebnis liefert. Bleibt das Ergebnis unplausibel, wird
    bewusst kein Vorschlag gemacht — lieber keine Zahl als eine falsche."""
    for grenze in sorted(UEBERLAUF_GRENZEN_WH):
        if start_wh < grenze:
            korrektur = (grenze - start_wh) + stop_wh
            if 0 < korrektur <= UEBERLAUF_SCHWELLE_WH:
                return korrektur
    return None


def markiere_als_anomalie(session_id: int, grund: str = "") -> None:
    """Setzt den Status auf 'anomaly'. Der Datensatz bleibt vollstaendig
    erhalten und ist in der Sessionliste weiterhin sichtbar — er wird nur
    von der automatischen Abrechnung ausgenommen."""
    conn = get_connection()
    try:
        conn.execute(
            "UPDATE charging_sessions SET status='anomaly', updated_at=CURRENT_TIMESTAMP WHERE id=?",
            (session_id,))
        conn.commit()
    finally:
        conn.close()
    event_log_service.log_event("system", "warning",
        f"Session {session_id} als auffällig markiert{': ' + grund if grund else ''}.")


def schliesse_zombie(session_id: int) -> None:
    """Schliesst eine Zombie-Session zum Startzeitpunkt ohne Energiemenge.

    Bewusst mit 0 kWh: Wie viel tatsaechlich geladen wurde, ist nicht
    rekonstruierbar. Eine geschaetzte Menge in einem Steuerbeleg waere
    schlimmer als gar keine."""
    conn = get_connection()
    try:
        conn.execute(
            """UPDATE charging_sessions
               SET status='closed',
                   end_timestamp = start_timestamp,
                   meter_stop_wh = meter_start_wh,
                   updated_at = CURRENT_TIMESTAMP
               WHERE id = ? AND status = 'open'""",
            (session_id,))
        conn.commit()
    finally:
        conn.close()
    event_log_service.log_event("system", "info",
        f"Zombie-Session {session_id} ohne Energiemenge geschlossen.")


def pruefbericht(user_id: int | None = None) -> dict:
    """Gesamtuebersicht fuer die Oberflaeche."""
    zombies = finde_zombie_sessions(user_id)
    anomalien = finde_zaehler_anomalien(user_id)
    return {
        "zombies": zombies,
        "zaehler_anomalien": anomalien,
        "anzahl_gesamt": len(zombies) + len(anomalien),
        "geprueft_am": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "schwellen": {
            "zombie_stunden": ZOMBIE_STUNDEN,
            "ueberlauf_kwh": UEBERLAUF_SCHWELLE_WH / 1000,
        },
    }
