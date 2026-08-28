"""
OCPP-Client-Service — FA-OCPP-CLIENT-01.

Ergaenzt den bestehenden OCPP-SERVER-Modus (Loxone verbindet sich zu UNS,
siehe ocpp_server/server.py) um einen CLIENT-Modus: wir verbinden uns selbst
als Charge Point zu einem EXTERNEN OCPP-Dienst.

Hintergrund/Nutzen (Ruecksprache Auftraggeber): Loxones eigene OCPP-
Integration sendet nachweislich NIE StartTransaction/MeterValues/
StopTransaction (siehe OCPP-Diagnose, Pflichtenheft-Changelog) — nur
BootNotification/Heartbeat/StatusNotification. Ueber den direkten Loxone-
API-Log-Import (loxone_log_import_service.py) haben wir die tatsaechlichen
Ladedaten aber zuverlaessig UND exakt. Dieser Client-Modus reicht genau
diese bereits vorhandenen, geprueften Sessions als ECHTE OCPP-Transaktionen
(StartTransaction -> MeterValues -> StopTransaction) an einen externen
OCPP-Dienst weiter — z. B. ein kommerzielles Fuhrpark-/Abrechnungssystem,
das eigene OCPP-Daten erwartet.

Bewusst schlank gehalten: keine Abhaengigkeit von der 'ocpp'-Bibliothek
(die ist fuer die SERVER-Seite gedacht) — rohe JSON-RPC-Nachrichten per
'websockets'-Client, analog zum bestehenden Roh-Logging-Wrapper auf der
Server-Seite. Damit bleibt die Nachrichtenstruktur voll unter unserer
Kontrolle und leicht nachvollziehbar/testbar.

WICHTIGER TESTSTATUS-HINWEIS (wie bei ocpp_server/server.py): 'websockets'
war in dieser Entwicklungsumgebung nicht installierbar (Sandbox ohne
PyPI-Zugriff) — die Nachrichten-Konstruktion und die Relay-Logik (welche
Sessions werden wann als Transaktion gesendet) sind isoliert mit Mocks
getestet, aber nicht live gegen einen echten externen OCPP-Dienst.
"""

import json
import uuid
from datetime import datetime, timezone

from repositories import ocpp_client_repository, session_repository
from services import event_log_service


def _call_message(action: str, payload: dict) -> str:
    """Baut eine OCPP-J CALL-Nachricht (messageTypeId=2)."""
    return json.dumps([2, str(uuid.uuid4()), action, payload])


def build_boot_notification(charge_point_id: str) -> str:
    return _call_message("BootNotification", {
        "chargePointVendor": "ChargeAtHome",
        "chargePointModel": "LoxoneRelay",
        "chargePointSerialNumber": charge_point_id,
        "firmwareVersion": "1.0",
    })


def build_heartbeat() -> str:
    return _call_message("Heartbeat", {})


def build_status_notification(status: str) -> str:
    return _call_message("StatusNotification", {
        "connectorId": 1,
        "errorCode": "NoError",
        "status": status,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    })


def build_start_transaction(session: dict) -> tuple[str, str]:
    """Rueckgabe: (Nachricht, transaction_id_hinweis) — transaction_id wird
    hier als Session-ID der eigenen DB verwendet (eindeutig, nachvollziehbar)."""
    start_dt = datetime.strptime(session["start_timestamp"], "%Y-%m-%d %H:%M:%S")
    msg = _call_message("StartTransaction", {
        "connectorId": 1,
        "idTag": session.get("rfid_tag") or "CHARGEATHOME",
        "meterStart": session["meter_start_wh"],
        "timestamp": start_dt.replace(tzinfo=timezone.utc).isoformat(),
    })
    return msg, str(session["id"])


def build_meter_values(session: dict, transaction_id: int) -> str:
    end_dt = datetime.strptime(session["end_timestamp"], "%Y-%m-%d %H:%M:%S")
    return _call_message("MeterValues", {
        "connectorId": 1,
        "transactionId": transaction_id,
        "meterValue": [{
            "timestamp": end_dt.replace(tzinfo=timezone.utc).isoformat(),
            "sampledValue": [{
                "value": str(session["meter_stop_wh"]),
                "context": "Sample.Periodic",
                "measurand": "Energy.Active.Import.Register",
                "unit": "Wh",
            }],
        }],
    })


def build_stop_transaction(session: dict, transaction_id: int) -> str:
    end_dt = datetime.strptime(session["end_timestamp"], "%Y-%m-%d %H:%M:%S")
    return _call_message("StopTransaction", {
        "transactionId": transaction_id,
        "meterStop": session["meter_stop_wh"],
        "timestamp": end_dt.replace(tzinfo=timezone.utc).isoformat(),
        "reason": "EVDisconnected",
    })


def get_sessions_to_relay(wallbox_id: int, last_relayed_session_id: int | None) -> list[dict]:
    """Liefert alle abgeschlossenen Sessions dieser Wallbox, die noch nicht an
    den externen Dienst weitergereicht wurden, chronologisch sortiert."""
    closed = session_repository.list_closed_sessions_for_wallbox(wallbox_id)
    if last_relayed_session_id:
        closed = [s for s in closed if s["id"] > last_relayed_session_id]
    return closed


async def run_client_cycle(websockets_module, wallbox_id: int, config: dict) -> None:
    """Ein einzelner Verbindungszyklus: verbinden, BootNotification, alle
    ausstehenden Sessions als Transaktionen relayen, sauber trennen. Wird von
    einem aeusseren Scheduler (aehnlich loxone/poller.py) periodisch
    aufgerufen — KEINE Dauerverbindung, um die Implementierung einfach und
    fehlertolerant zu halten (bei Verbindungsabbruch einfach naechster
    Zyklus)."""
    url = config["remote_url"]
    charge_point_id = config["remote_charge_point_id"]
    ws_url = f"{url.rstrip('/')}/{charge_point_id}"

    try:
        async with websockets_module.connect(ws_url, subprotocols=["ocpp1.6"]) as ws:
            await ws.send(build_boot_notification(charge_point_id))
            await ws.recv()  # BootNotification-Antwort

            sessions = get_sessions_to_relay(wallbox_id, config.get("last_relayed_session_id"))
            for session in sessions:
                start_msg, txn_hint = build_start_transaction(session)
                await ws.send(start_msg)
                await ws.recv()

                transaction_id = session["id"]  # eigene Session-ID als Transaktions-ID verwendet
                await ws.send(build_meter_values(session, transaction_id))
                await ws.recv()

                await ws.send(build_stop_transaction(session, transaction_id))
                await ws.recv()

                ocpp_client_repository.set_last_relayed_session(wallbox_id, session["id"])
                event_log_service.log_event(
                    "ocpp", "info",
                    f"OCPP-Client: Session #{session['id']} ({session['meter_stop_wh'] - session['meter_start_wh']} Wh) "
                    f"an externen Dienst '{charge_point_id}' übertragen."
                )

        ocpp_client_repository.record_connect_attempt(wallbox_id, success=True)
    except Exception as exc:
        ocpp_client_repository.record_connect_attempt(wallbox_id, success=False, error=str(exc))
        event_log_service.log_event(
            "ocpp", "warning",
            f"OCPP-Client: Verbindung zu '{charge_point_id}' ({url}) fehlgeschlagen: {exc}"
        )
