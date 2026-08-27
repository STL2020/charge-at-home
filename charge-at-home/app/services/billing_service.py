"""Billing-Service — Geschaeftslogik fuer Ladesessions (§ 8.1 Application Layer)."""

from repositories import session_repository
from services.db_service import write_audit_log


def set_classification(session_id: int, new_value: str, changed_by: str) -> bool:
    """FA-LS-04: Klassifizierung aendern, mit Audit-Log-Eintrag (FA-COMP-01-Grundgeruest)."""
    session = session_repository.get_session(session_id)
    if session is None:
        return False
    old_value = session["classification"]
    session_repository.update_classification(session_id, new_value)
    write_audit_log("charging_sessions", session_id, "classification", old_value, new_value, changed_by)
    return True


def set_charging_location(session_id: int, new_value: str, changed_by: str) -> bool:
    """FA-LS-BMW-02: Ladeort (zuhause/extern) manuell korrigieren, falls die
    automatische Erkennung beim BMW-Import (haeufigste Adresse = zuhause)
    im Einzelfall falsch lag. Mit Audit-Log-Eintrag, da dies direkt
    beeinflusst, ob eine Session in den Eigenstrom-Beleg einfliesst."""
    session = session_repository.get_session(session_id)
    if session is None:
        return False
    old_value = session.get("charging_location")
    ok = session_repository.set_charging_location(session_id, new_value)
    if ok:
        write_audit_log("charging_sessions", session_id, "charging_location", old_value, new_value, changed_by)
    return ok


def compute_energy_and_amount(session: dict) -> tuple[float, float]:
    """Decimal-sichere(re) Berechnung ueber Wh-Rohdaten, nicht ueber evtl. gerundete kWh."""
    if session.get("meter_stop_wh") is None:
        return 0.0, 0.0
    energy_kwh = (session["meter_stop_wh"] - session["meter_start_wh"]) / 1000.0
    amount_eur = round(energy_kwh * session["price_per_kwh"], 2)
    return round(energy_kwh, 2), amount_eur


def session_to_api_dict(session: dict) -> dict:
    energy_kwh, amount_eur = compute_energy_and_amount(session)
    return {
        "id": session["id"],
        "start_timestamp": session["start_timestamp"],
        "end_timestamp": session["end_timestamp"],
        "wallbox_name": session.get("wallbox_name"),
        "energy_kwh": energy_kwh,
        "price_per_kwh": session["price_per_kwh"],
        "amount_eur": amount_eur,
        "classification": session["classification"],
        "source": session["source"],
        "status": session["status"],
        "rfid_tag": session.get("rfid_tag"),
        "charging_location": session.get("charging_location", "zuhause"),
        "charging_location_note": session.get("charging_location_note"),
    }
