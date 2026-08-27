"""
Loxone-Poll-Service — Geschaeftslogik der Polling-basierten Session-Erkennung (FA-LS-10).

Anders als OCPP (explizite StartTransaction/StopTransaction-Nachrichten) liefert
das Abfragen ("Polling") eines Zaehlerstands ueber die direkte Loxone-API kein
explizites Start-/Stop-Signal. Dieses Modul erkennt Ladevorgaenge deshalb
heuristisch:

- Steigt der Zaehlerstand gegenueber dem letzten bekannten Wert an, OHNE dass
  eine offene Session existiert -> ein neuer Ladevorgang hat begonnen.
- Steigt der Zaehlerstand innerhalb einer offenen Session weiter an -> wird
  wie MeterValues bei OCPP als Zwischenstand behandelt (§ 6.1-Prinzip).
- Bleibt der Zaehlerstand ueber `stale_threshold` aufeinanderfolgende
  Poll-Zyklen unveraendert, obwohl eine Session offen ist -> Ladevorgang gilt
  als beendet, Session wird geschlossen.

Bewusst getrennt vom eigentlichen HTTP-Abruf (services/loxone_api_service.py),
damit diese Kernlogik ohne echten Miniserver-Zugriff unit-testbar ist (siehe
Pflichtenheft-Changelog: dieser Teil wurde vollstaendig getestet, der HTTP-
Transport selbst konnte mangels erreichbarer Hardware nicht getestet werden).
"""

from datetime import datetime

from repositories import session_repository, loxone_config_repository


def process_poll_reading(
    wallbox_id: int,
    user_id: int,
    current_meter_wh: int,
    price_per_kwh: float,
    stale_threshold: int = 3,
    now: str | None = None,
) -> dict:
    """Verarbeitet einen einzelnen Poll-Messwert. Rueckgabe beschreibt die ausgefuehrte Aktion,
    v. a. fuer Tests/Logging: {'action': 'started'|'updated'|'closed'|'unchanged_open'|'no_change'}."""
    now = now or datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
    open_session = session_repository.get_open_session_for_wallbox(wallbox_id)
    poll_state = loxone_config_repository.get_poll_state(wallbox_id)
    last_value = poll_state["last_meter_wh"] if poll_state else None
    unchanged_count = poll_state["unchanged_count"] if poll_state else 0

    if open_session is None:
        if last_value is not None and current_meter_wh > last_value:
            # Anstieg ohne offene Session -> neuer Ladevorgang beginnt bei last_value
            session_id = session_repository.insert_session(
                wallbox_id=wallbox_id, user_id=user_id, source="loxone_api",
                start_timestamp=now, end_timestamp=None,
                meter_start_wh=last_value, meter_stop_wh=current_meter_wh,
                price_per_kwh=price_per_kwh, rfid_tag=None, classification=None, status="open",
            )
            loxone_config_repository.set_poll_state(wallbox_id, current_meter_wh, 0)
            return {"action": "started", "session_id": session_id}
        loxone_config_repository.set_poll_state(wallbox_id, current_meter_wh, 0)
        return {"action": "no_change"}

    # Es existiert eine offene Session
    if last_value is not None and current_meter_wh > last_value:
        session_repository.update_meter_stop_only(open_session["id"], current_meter_wh)
        loxone_config_repository.set_poll_state(wallbox_id, current_meter_wh, 0)
        return {"action": "updated", "session_id": open_session["id"]}

    # Zaehlerstand unveraendert -> moeglicherweise beendet
    unchanged_count += 1
    if unchanged_count >= stale_threshold:
        session_repository.close_session(open_session["id"], current_meter_wh, now)
        loxone_config_repository.set_poll_state(wallbox_id, current_meter_wh, 0)
        return {"action": "closed", "session_id": open_session["id"]}

    loxone_config_repository.set_poll_state(wallbox_id, current_meter_wh, unchanged_count)
    return {"action": "unchanged_open", "unchanged_count": unchanged_count}
