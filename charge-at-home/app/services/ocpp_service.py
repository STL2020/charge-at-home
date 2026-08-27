"""
OCPP-Service — Geschaeftslogik fuer die Central-System-Nachrichten (FA-LS-08/09).

Bewusst getrennt vom eigentlichen OCPP-Transport-Layer (app/ocpp_server/server.py,
der die Pakete 'ocpp' und 'websockets' braucht): Diese Datei enthaelt nur
reines Python + Datenbankzugriff und ist deshalb unabhaengig von diesen
beiden Paketen unit-testbar (siehe Pflichtenheft-Changelog zu Sprint 3 —
'ocpp'/'websockets' liessen sich in der Entwicklungsumgebung nicht installieren,
diese Kernlogik aber schon gegen die echte Datenbank verifiziert werden).
"""

from datetime import datetime

import services.event_log_service as event_log_service
from repositories import wallbox_repository, session_repository
from services import db_service


def handle_boot_notification(charge_point_id: str) -> bool:
    """NFA-10: Allowlist-Pruefung. True = Accepted, False = Rejected.

    Unbekannte charge_point_id werden NICHT automatisch angelegt — das
    Anlegen einer Wallbox erfolgt ausschliesslich manuell (§ 7.7/Einstellungen).
    """
    wb = wallbox_repository.get_by_ocpp_id(charge_point_id)
    accepted = wb is not None
    if accepted:
        wallbox_repository.set_status(wb["id"], "online")
        event_log_service.log_event("ocpp", "info", f"BootNotification akzeptiert: '{charge_point_id}' (Wallbox: {wb['name']})")
    else:
        event_log_service.log_event(
            "ocpp", "warning",
            f"BootNotification ABGELEHNT: '{charge_point_id}' ist bei keiner OCPP-Wallbox in den Einstellungen "
            f"als Charge-Point-ID hinterlegt. Prüfen: stimmt die ID in Loxone Config exakt mit der ID in den "
            f"App-Einstellungen überein?",
        )
    return accepted


def handle_status_notification(charge_point_id: str, ocpp_status: str) -> None:
    """FA-LS-09: Live-Statusanzeige je Wallbox."""
    wb = wallbox_repository.get_by_ocpp_id(charge_point_id)
    if wb is not None:
        wallbox_repository.set_status(wb["id"], ocpp_status)
        event_log_service.log_event("ocpp", "info", f"StatusNotification '{charge_point_id}': {ocpp_status}")


def _get_single_user_id() -> int | None:
    conn = db_service.get_connection()
    try:
        row = conn.execute("SELECT id FROM users_config ORDER BY id LIMIT 1").fetchone()
        return row["id"] if row else None
    finally:
        conn.close()


def close_sessions_on_reboot(charge_point_id: str) -> None:
    """Schliesst alle offenen Sessions einer Wallbox wenn ein BootNotification
    empfangen wird — ein Neustart beendet implizit alle laufenden Transaktionen.
    Hintergrund: 6x StartTransaction aber nur 5x StopTransaction im Protokoll
    bedeutet 1 Session haengt dauerhaft als 'offen', weil der Miniserver neu
    startete und das StopTransaction nicht mehr senden konnte (Reboot =/= nur
    Verbindungsabbruch; Loxone queuut zwar bei Netzausfaellen, aber nicht nach
    Stromverlust/Reboot). Dieser Hook stellt sicher, dass der Neustart die
    offene Session korrekt terminiert."""
    wb = wallbox_repository.get_by_ocpp_id(charge_point_id)
    if wb is None:
        return
    conn = db_service.get_connection()
    try:
        open_sessions = conn.execute(
            "SELECT id, meter_stop_wh, meter_start_wh FROM charging_sessions "
            "WHERE wallbox_id=? AND source='ocpp' AND status='open'",
            (wb["id"],)
        ).fetchall()
        if open_sessions:
            now = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
            for row in open_sessions:
                meter_stop = row["meter_stop_wh"] or row["meter_start_wh"]
                conn.execute(
                    """UPDATE charging_sessions SET status='closed', end_timestamp=?,
                       meter_stop_wh=?, updated_at=CURRENT_TIMESTAMP WHERE id=?""",
                    (now, meter_stop, row["id"])
                )
                event_log_service.log_event("ocpp", "warning",
                    f"Session #{row['id']} bei BootNotification '{charge_point_id}' "
                    f"geschlossen — Wallbox-Neustart beendete die Transaktion implizit.")
            conn.commit()
        wallbox_repository.set_status(wb["id"], "ready")
    finally:
        conn.close()


def handle_start_transaction(charge_point_id: str, id_tag: str, meter_start_wh: int,
                              timestamp: str | None = None) -> int | None:
    """FA-LS-08: Legt eine offene Session an. Schließt vorherige offene OCPP-Sessions
    dieser Wallbox automatisch (Sicherheitsnetz falls StopTransaction verpasst wurde —
    z. B. weil Loxone innerhalb von Sekunden Stop+Start sendet und unser Server
    die Session noch als 'open' hat)."""
    wb = wallbox_repository.get_by_ocpp_id(charge_point_id)
    if wb is None:
        return None
    user_id = _get_single_user_id()
    if user_id is None:
        return None

    # Sicherheitsnetz: ggf. noch offene Session dieser Wallbox schliessen
    _close_stale_ocpp_sessions(wb["id"], meter_start_wh, timestamp)

    start_ts = timestamp or datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
    session_id = session_repository.insert_session(
        wallbox_id=wb["id"], user_id=user_id, source="ocpp",
        start_timestamp=start_ts, end_timestamp=None,
        meter_start_wh=meter_start_wh, meter_stop_wh=None,
        price_per_kwh=_get_current_price(user_id), rfid_tag=id_tag,
        classification=None, status="open",
    )
    wallbox_repository.set_status(wb["id"], "charging")
    event_log_service.log_event("ocpp", "info",
        f"StartTransaction '{charge_point_id}': Session {session_id} gestartet (Zähler {meter_start_wh} Wh, Tag: {id_tag})")
    return session_id


def _close_stale_ocpp_sessions(wallbox_id: int, meter_stop_wh: int, timestamp: str | None) -> None:
    """Schliesst alle offenen OCPP-Sessions dieser Wallbox mit dem aktuellen
    meterStart als meterStop — tritt auf wenn StopTransaction + neues StartTransaction
    so schnell aufeinanderfolgen, dass die DB-Aktualisierung noch aussteht
    (Loxone trennt und verbindet innerhalb von Sekunden)."""
    conn = db_service.get_connection()
    try:
        open_sessions = conn.execute(
            "SELECT id FROM charging_sessions WHERE wallbox_id=? AND source='ocpp' AND status='open'",
            (wallbox_id,)
        ).fetchall()
        if open_sessions:
            end_ts = timestamp or datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
            for row in open_sessions:
                conn.execute(
                    """UPDATE charging_sessions SET status='closed', meter_stop_wh=?,
                       end_timestamp=?, updated_at=CURRENT_TIMESTAMP WHERE id=?""",
                    (meter_stop_wh, end_ts, row["id"])
                )
                event_log_service.log_event("ocpp", "info",
                    f"Session {row['id']} automatisch geschlossen (Sicherheitsnetz vor neuem StartTransaction)")
            conn.commit()
    finally:
        conn.close()


def handle_meter_values(session_id: int, meter_value_wh: int,
                         charge_point_id: str | None = None,
                         timestamp: str | None = None) -> None:
    """Aktualisiert MeterValues. Falls die Session (transactionId) in der DB fehlt,
    aber die Wallbox registriert ist, wird eine Recovery-Session angelegt.

    Hintergrund (Rückmeldung Auftraggeber): Kommen MeterValues für eine
    transactionId an, deren StartTransaction unser Server verpasst hat (z. B.
    weil die Wallbox bereits mitten in einer Transaktion war, als die Verbindung
    aufgebaut wurde, oder nach einem App-Neustart), wurden die Werte bisher
    still verworfen — es entstand KEIN Ladeeintrag. Jetzt wird die Ladung
    rückwirkend erfasst, sobald ein sinnvoller Zählerstand vorliegt."""
    session = session_repository.get_session(session_id)
    if session is None:
        # Recovery: Session nachträglich anlegen, wenn Wallbox bekannt ist
        if charge_point_id:
            recovered = _recover_orphan_session(session_id, meter_value_wh, charge_point_id, timestamp)
            if recovered:
                return
        _warn_unknown_session_once(session_id)
        return
    session_repository.update_meter_stop_only(session_id, meter_value_wh)
    event_log_service.log_event("ocpp", "info", f"MeterValues Session {session_id}: {meter_value_wh} Wh")


def _recover_orphan_session(transaction_id: int, meter_value_wh: int,
                             charge_point_id: str, timestamp: str | None) -> bool:
    """Legt nachträglich eine offene Session für eine verwaiste transactionId an.
    Nur wenn die Wallbox registriert ist. Gibt True zurück bei Erfolg."""
    wb = wallbox_repository.get_by_ocpp_id(charge_point_id)
    if wb is None:
        return False
    user_id = _get_single_user_id()
    if user_id is None:
        return False
    # Prüfen ob evtl. schon eine offene OCPP-Session dieser Wallbox existiert
    conn = db_service.get_connection()
    try:
        existing = conn.execute(
            "SELECT id, meter_start_wh FROM charging_sessions WHERE wallbox_id=? AND source='ocpp' AND status='open' ORDER BY id DESC LIMIT 1",
            (wb["id"],)
        ).fetchone()
    finally:
        conn.close()
    if existing:
        # Es gibt bereits eine offene Session → diese aktualisieren statt neue anzulegen
        session_repository.update_meter_stop_only(existing["id"], meter_value_wh)
        return True
    # Neue Recovery-Session: meter_start = aktueller Zählerstand (Ladung ab jetzt zählen)
    start_ts = timestamp or datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
    session_id = session_repository.insert_session(
        wallbox_id=wb["id"], user_id=user_id, source="ocpp",
        start_timestamp=start_ts, end_timestamp=None,
        meter_start_wh=meter_value_wh, meter_stop_wh=meter_value_wh,
        price_per_kwh=_get_current_price(user_id), rfid_tag=None,
        classification=None, status="open",
    )
    wallbox_repository.set_status(wb["id"], "charging")
    event_log_service.log_event(
        "ocpp", "info",
        f"Recovery: Verwaiste Transaktion {transaction_id} auf Wallbox '{charge_point_id}' "
        f"→ neue Session {session_id} angelegt (Startzähler {meter_value_wh} Wh). "
        f"Die Ladung wird ab diesem Zählerstand erfasst."
    )
    return True


_warned_unknown_sessions: set = set()

def _warn_unknown_session_once(session_id: int) -> None:
    """Loggt eine Warnung EINMAL pro unbekannter transactionId (nicht bei jedem Sample)."""
    if session_id not in _warned_unknown_sessions:
        _warned_unknown_sessions.add(session_id)
        event_log_service.log_event(
            "ocpp", "warning",
            f"MeterValues für unbekannte Session {session_id} ignoriert "
            f"(Wallbox nicht registriert oder Session nach DB-Reset nicht mehr vorhanden). "
            f"Wallbox trennen & neu verbinden um neue Session zu starten."
        )


def handle_stop_transaction(session_id: int, meter_stop_wh: int, timestamp: str | None = None) -> None:
    end_ts = timestamp or datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
    session_repository.close_session(session_id, meter_stop_wh, end_ts)
    session = session_repository.get_session(session_id)
    if session is not None:
        wallbox_repository.set_status(session["wallbox_id"], "ready")
        event_log_service.log_event("ocpp", "info",
            f"StopTransaction Session {session_id}: geschlossen bei {meter_stop_wh} Wh "
            f"({round((meter_stop_wh - (session.get('meter_start_wh') or 0)) / 1000.0, 3)} kWh geladen)")
    else:
        event_log_service.log_event("ocpp", "warning",
            f"StopTransaction: Session {session_id} nicht in DB gefunden — "
            f"möglicherweise bereits durch Sicherheitsnetz geschlossen.")


def _get_current_price(user_id: int) -> float:
    conn = db_service.get_connection()
    try:
        row = conn.execute("SELECT default_kwh_price FROM users_config WHERE id = ?", (user_id,)).fetchone()
        return row["default_kwh_price"] if row else 0.34
    finally:
        conn.close()


def update_live_metrics(charge_point_id: str, power_w: int | None = None,
                        phasen: dict | None = None,
                        meter_total_wh: int | None = None) -> None:
    """Schreibt OCPP-Livewerte in wallbox_live_metrics (Sprint 5).

    Nur uebergebene Werte werden aktualisiert — so ueberschreibt eine
    MeterValues-Nachricht ohne Phasenangabe nicht die zuletzt bekannten
    Stroeme. Die Peak-Leistung wird als Maximum fortgeschrieben und bei
    StartTransaction zurueckgesetzt (siehe reset_peak_power)."""
    wb = wallbox_repository.get_by_ocpp_id(charge_point_id)
    if not wb:
        return
    phasen = phasen or {}
    power_kw = round(power_w / 1000.0, 3) if power_w is not None else None

    conn = db_service.get_connection()
    try:
        conn.execute(
            "INSERT OR IGNORE INTO wallbox_live_metrics (wallbox_id) VALUES (?)",
            (wb["id"],))
        felder, werte = [], []
        if power_kw is not None:
            felder.append("current_power_kw = ?"); werte.append(power_kw)
            # Peak nur erhoehen, nie senken
            felder.append("peak_power_kw = MAX(COALESCE(peak_power_kw, 0), ?)")
            werte.append(power_kw)
        if meter_total_wh is not None:
            felder.append("meter_total_wh = ?"); werte.append(meter_total_wh)
        for phase, spalte in (("L1", "current_l1_a"), ("L2", "current_l2_a"), ("L3", "current_l3_a")):
            if phase in phasen:
                felder.append(f"{spalte} = ?"); werte.append(phasen[phase])
        felder.append("connected = 1")
        felder.append("last_sync_at = ?"); werte.append(datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
        werte.append(wb["id"])
        conn.execute(
            f"UPDATE wallbox_live_metrics SET {', '.join(felder)} WHERE wallbox_id = ?",
            werte)
        conn.commit()
    finally:
        conn.close()


def reset_peak_power(charge_point_id: str) -> None:
    """Setzt die Peak-Leistung zurueck — aufzurufen bei StartTransaction,
    damit der Spitzenwert sich immer auf die laufende Session bezieht."""
    wb = wallbox_repository.get_by_ocpp_id(charge_point_id)
    if not wb:
        return
    conn = db_service.get_connection()
    try:
        conn.execute("INSERT OR IGNORE INTO wallbox_live_metrics (wallbox_id) VALUES (?)", (wb["id"],))
        conn.execute("UPDATE wallbox_live_metrics SET peak_power_kw = 0 WHERE wallbox_id = ?", (wb["id"],))
        conn.commit()
    finally:
        conn.close()


def get_live_metrics(wallbox_id: int) -> dict:
    """Livewerte inklusive Tagesenergie (kWh seit 00:00 Uhr).

    Liefert fuer JEDE Wallbox denselben Satz Kennzahlen, unabhaengig davon, ob
    die Daten per OCPP oder ueber die Loxone-API hereinkommen. Werte, die eine
    Quelle nicht direkt meldet, werden — soweit moeglich — aus den Sessions
    abgeleitet: Der Zaehlerstand ist dann der zuletzt bekannte Endstand, die
    Tagesenergie stammt ohnehin immer aus den Sessions des laufenden Tages
    (bleibt so auch nach einem Neustart korrekt)."""
    conn = db_service.get_connection()
    try:
        row = conn.execute(
            "SELECT * FROM wallbox_live_metrics WHERE wallbox_id = ?", (wallbox_id,)
        ).fetchone()
        heute = datetime.now().strftime("%Y-%m-%d")
        tag = conn.execute(
            """SELECT COALESCE(SUM(
                   CASE WHEN meter_stop_wh IS NOT NULL AND meter_start_wh IS NOT NULL
                        THEN (meter_stop_wh - meter_start_wh) ELSE 0 END), 0) AS wh
               FROM charging_sessions
               WHERE wallbox_id = ? AND date(start_timestamp) = ?""",
            (wallbox_id, heute)).fetchone()
        d = dict(row) if row else {}
        d["tagesenergie_kwh"] = round((tag["wh"] or 0) / 1000.0, 2)

        # Zaehlerstand-Fallback: letzter bekannter Endstand aus den Sessions.
        # NUR wenn die Quelle absolute Zaehlerstaende liefert. Der Loxone-
        # Log-Import speichert die Werte session-relativ (Start 0 → Menge),
        # dort waere der "Zaehlerstand" nur die Lademenge der letzten Session.
        # Erkennungsmerkmal: Ein Startwert von 0 bedeutet relative Zaehlung.
        if not d.get("meter_total_wh"):
            letzte = conn.execute(
                """SELECT meter_start_wh, meter_stop_wh FROM charging_sessions
                   WHERE wallbox_id = ? AND meter_stop_wh IS NOT NULL
                   ORDER BY start_timestamp DESC LIMIT 1""",
                (wallbox_id,)).fetchone()
            if letzte and letzte["meter_stop_wh"] and (letzte["meter_start_wh"] or 0) > 0:
                d["meter_total_wh"] = letzte["meter_stop_wh"]
                d["meter_total_hergeleitet"] = True
        return d
    finally:
        conn.close()
