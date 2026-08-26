"""
OCPP-1.6-J Central-System-Server — FA-LS-08.

WICHTIGER HINWEIS ZUM TESTSTATUS:
In der Entwicklungsumgebung, in der dieser Code entstanden ist, konnten die
Pakete 'ocpp' und 'websockets' nicht installiert werden (Netzwerk-Sandbox
ohne PyPI-Zugriff, gleiches Problem wie zuvor bei Streamlit). Diese Datei
ist deshalb nach der dokumentierten python-ocpp-API (v1.6) geschrieben,
konnte hier aber NICHT gestartet oder gegen einen echten/simulierten
Client getestet werden. Die eigentliche Geschaeftslogik (Zugriffskontrolle,
Datenbank-Updates, Zwischenspeicherung) ist ausgelagert nach
services/ocpp_service.py und DORT vollstaendig getestet — diese Datei ist
nur die duenne Transport-Anbindung. Bitte bei Docker-Betrieb (wo pip
normalen Internetzugang hat) einmal gegen eine echte Wallbox oder einen
OCPP-Simulator verifizieren.

Startet als eigenstaendiger Prozess (NFA-09), getrennt vom Flask-Prozess,
beide greifen ueber dieselbe SQLite-Datei (WAL-Modus) auf denselben Stand zu.
"""

import asyncio
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import websockets
from ocpp.routing import on
from ocpp.v16 import ChargePoint as BaseChargePoint
from ocpp.v16 import call_result, call as ocpp_call
from ocpp.v16.enums import RegistrationStatus, AuthorizationStatus

from services import ocpp_service, event_log_service, ocpp_log_service
from repositories import settings_repository

def ocpp_port() -> int:
    """Port des eingebauten OCPP-Servers.

    Einstellbar, weil 9000 haeufig belegt ist — Portainer nutzt ihn
    standardmaessig. Reihenfolge: gespeicherte Einstellung, dann
    Umgebungsvariable, sonst der Standard.
    """
    try:
        wert = settings_repository.get_setting("ocpp_port")
        if wert and str(wert).strip().isdigit():
            p = int(str(wert).strip())
            if 1024 <= p <= 65535:
                return p
    except Exception:
        pass
    try:
        p = int(os.environ.get("CHARGE_OCPP_PORT", "9000"))
        if 1024 <= p <= 65535:
            return p
    except (TypeError, ValueError):
        pass
    return 9000


# Rueckwaertskompatibel: aeltere Aufrufe erwarten die Konstante
OCPP_PORT = 9000


def _normalize_ts(ts: str | None) -> str:
    """Konvertiert OCPP-ISO-Timestamps ('2026-08-22T19:57:14.620Z')
    in das DB-Format ('2026-08-22 19:57:14'). Alle anderen Formate
    werden unveraendert durchgereicht."""
    if not ts:
        return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    for fmt in ("%Y-%m-%dT%H:%M:%S.%fZ", "%Y-%m-%dT%H:%M:%SZ",
                "%Y-%m-%dT%H:%M:%S.%f+00:00", "%Y-%m-%dT%H:%M:%S+00:00"):
        try:
            return datetime.strptime(ts, fmt).strftime("%Y-%m-%d %H:%M:%S")
        except ValueError:
            continue
    return ts  # Fallback: unveraendert


def _mv_timestamp(meter_value) -> str | None:
    """Extrahiert den Timestamp aus dem ersten MeterValue-Eintrag (für Recovery)."""
    try:
        for mv in (meter_value or []):
            ts = mv.get("timestamp")
            if ts:
                return _normalize_ts(ts)
    except Exception:
        pass
    return None


def _cr(name: str):
    """Kompatibilitaet zwischen python-ocpp-Versionen."""
    return getattr(call_result, name, None) or getattr(call_result, f"{name}Payload")


def _call(name: str):
    """Dasselbe fuer call-Klassen (Anfragen vom Server zur Wallbox)."""
    return getattr(ocpp_call, name, None) or getattr(ocpp_call, f"{name}Payload")


class ChargePoint(BaseChargePoint):

    @on("BootNotification")
    async def on_boot_notification(self, charge_point_model, charge_point_vendor, **kwargs):
        event_log_service.log_event(
            "ocpp", "info",
            f"BootNotification '{self.id}': {charge_point_vendor} {charge_point_model}"
        )
        accepted = ocpp_service.handle_boot_notification(self.id)

        # KRITISCH: BootNotification = Wallbox-Neustart.
        # Laut OCPP-Spec und Loxone-Dokumentation werden Transaktionsnachrichten
        # bei Netzwerkausfällen in eine Warteschlange gestellt. Falls der Miniserver
        # NEUSTARTET (statt nur die Verbindung kurz verliert), sind offene
        # Transaktionen jedoch beendet — ein Neustart terminiert laufende Sessions
        # implizit. Wir schließen deshalb alle offenen Sessions dieser Wallbox,
        # damit #S-26 nicht dauerhaft als "offen" in der DB hängt.
        if accepted:
            ocpp_service.close_sessions_on_reboot(self.id)
            asyncio.ensure_future(self._configure_after_boot())

        return _cr("BootNotification")(
            current_time=datetime.now(timezone.utc).isoformat(),
            interval=300,
            status=RegistrationStatus.accepted if accepted else RegistrationStatus.rejected,
        )

    @on("Authorize")
    async def on_authorize(self, id_tag, **kwargs):
        # Freigabe erfolgt hier grundsaetzlich; feingranulare Tag-Rollen
        # (Dienst/Privat) werden erst bei der Klassifizierung (FA-LS-04)
        # in der Weboberflaeche vergeben, nicht schon auf Protokollebene.
        return _cr("Authorize")(id_tag_info={"status": AuthorizationStatus.accepted})

    @on("StartTransaction")
    async def on_start_transaction(self, connector_id, id_tag, meter_start, timestamp, **kwargs):
        # ISO-Timestamp aus OCPP in DB-Format konvertieren
        ts_norm = _normalize_ts(timestamp)
        session_id = ocpp_service.handle_start_transaction(self.id, id_tag, meter_start, ts_norm)
        # Peak-Leistung gehoert zur jeweiligen Session — daher hier zuruecksetzen
        try:
            ocpp_service.reset_peak_power(self.id)
        except Exception:
            pass
        return _cr("StartTransaction")(
            transaction_id=session_id if session_id is not None else 0,
            id_tag_info={"status": AuthorizationStatus.accepted if session_id else AuthorizationStatus.blocked},
        )

    @on("MeterValues")
    async def on_meter_values(self, connector_id, meter_value, transaction_id=None, **kwargs):
        """MeterValues-Handler — robust gegen unterschiedliche Reihenfolgen der
        sampledValue-Eintraege und gegen python-ocpp-Versionsunterschiede.

        KRITISCHER BUG behoben (gefunden durch Abgleich mit echtem Referenz-Dokument
        des Auftraggebers): python-ocpp konvertiert nur die DIREKTEN Funktions-
        parameter in snake_case (meter_value kommt korrekt als snake_case an),
        aber innerhalb der Liste bleiben die Dicts unveraendert -- 'sampledValue'
        bleibt camelCase, nicht 'sampled_value'. Unser alter Code hatte 'sampled_value'
        geschrieben und den KeyError stillschweigend verschluckt (except pass),
        weshalb NIE ein Zählerstand aus MeterValues in die Datenbank geschrieben
        wurde. Jetzt: beide Schreibweisen probieren + gezielt nach
        Energy.Active.Import.Register suchen statt blind [0] zu lesen."""
        if not transaction_id or not meter_value:
            return _cr("MeterValues")()
        energy_wh = None
        power_w = None
        phasen = {}          # "L1"/"L2"/"L3" -> Ampere
        for mv in meter_value:
            # python-ocpp liefert nested Dicts unveraendert (camelCase),
            # nur top-level Parameter werden zu snake_case konvertiert.
            sampled = mv.get("sampledValue") or mv.get("sampled_value") or []
            for sv in sampled:
                measurand = sv.get("measurand", "")
                unit      = sv.get("unit", "Wh")
                value_str = sv.get("value", "")
                try:
                    val = float(value_str)
                except (TypeError, ValueError):
                    continue
                # Zaehlerstand: manche Wallboxen senden kWh statt Wh
                if measurand == "Energy.Active.Import.Register":
                    if unit in ("Wh", ""):
                        energy_wh = int(val)
                    elif unit == "kWh":
                        energy_wh = int(val * 1000)
                # Wirkleistung: W oder kW
                elif measurand == "Power.Active.Import":
                    if unit == "W":
                        power_w = int(val)
                    elif unit == "kW":
                        power_w = int(val * 1000)
                # Phasenstrom je Leiter (phase = "L1", "L2", "L3")
                elif measurand == "Current.Import" and unit in ("A", ""):
                    phase = sv.get("phase") or "L1"
                    phasen[phase] = round(val, 1)

        # Livewerte persistieren, damit die Oberflaeche kW, A, Zaehlerstand und
        # Peak anzeigen kann — bisher landeten diese Werte nur im Logtext.
        if power_w is not None or phasen or energy_wh is not None:
            try:
                ocpp_service.update_live_metrics(
                    charge_point_id=self.id,
                    power_w=power_w,
                    phasen=phasen,
                    meter_total_wh=energy_wh,
                )
            except Exception as e:
                event_log_service.log_event("ocpp", "warning",
                    f"Livewerte '{self.id}' nicht gespeichert: {type(e).__name__}")

        if energy_wh is not None:
            ocpp_service.handle_meter_values(transaction_id, energy_wh,
                                             charge_point_id=self.id, timestamp=_mv_timestamp(meter_value))
            amp = ", ".join(f"{k} {v} A" for k, v in sorted(phasen.items())) if phasen else ""
            event_log_service.log_event(
                "ocpp", "info",
                f"MeterValues '{self.id}': {energy_wh} Wh"
                + (f", {power_w} W" if power_w is not None else "")
                + (f", {amp}" if amp else "")
            )
        return _cr("MeterValues")()

    @on("StopTransaction")
    async def on_stop_transaction(self, meter_stop, timestamp, transaction_id, **kwargs):
        """StopTransaction — meter_stop ist der finale Zählerstand in Wh."""
        final_wh = meter_stop
        if not final_wh:
            for td in (kwargs.get("transaction_data") or []):
                sampled = td.get("sampledValue") or td.get("sampled_value") or []
                for sv in sampled:
                    if sv.get("measurand", "") == "Energy.Active.Import.Register":
                        try:
                            final_wh = int(float(sv["value"]))
                            break
                        except (ValueError, KeyError):
                            pass
        ts_norm = _normalize_ts(timestamp)
        ocpp_service.handle_stop_transaction(transaction_id, final_wh or meter_stop, ts_norm)
        return _cr("StopTransaction")(id_tag_info={"status": AuthorizationStatus.accepted})

    @on("Heartbeat")
    async def on_heartbeat(self, **kwargs):
        return _cr("Heartbeat")(current_time=datetime.now(timezone.utc).isoformat())

    async def _configure_after_boot(self):
        """Konfiguriert die Wallbox direkt nach dem BootNotification-Handshake.
        Laut offiz. Loxone-OCPP-Dokumentation muss 'Variablen schreiben/lesen'
        unterstuetzt sein — wir nutzen ChangeConfiguration um:
        1. AuthorizeRemoteTxRequests=false: Wallbox startet Transaktion auf
           RemoteStartTransaction sofort, ohne lokale RFID-Autorisierung
        2. MeterValueSampleInterval: Messintervall 60 Sekunden
        3. MeterValuesSampledData: Welche Messwerte periodisch gesendet werden
        4. StopTxnSampledData: Messwerte beim Transaktionsende"""
        await asyncio.sleep(1.0)  # kurz warten bis BootNotification-Response zugestellt

        # Messintervall bewusst auf 15 Sekunden: Bei 60 Sekunden wirkt die
        # Live-Anzeige traege und ein kurzer Ladevorgang kann voellig ohne
        # Zwischenwert bleiben. 15 Sekunden ergeben eine fluessige Kurve, ohne
        # die Wallbox oder das Netzwerk nennenswert zu belasten.
        # 'Voltage' ergaenzt: Zusammen mit Strom und Phasenzahl laesst sich
        # damit pruefen, ob wirklich dreiphasig geladen wird.
        configs = [
            ("AuthorizeRemoteTxRequests", "false"),
            ("MeterValueSampleInterval",  "15"),
            ("MeterValuesSampledData",
             "Energy.Active.Import.Register,Power.Active.Import,Current.Import,Voltage"),
            ("StopTxnSampledData",        "Energy.Active.Import.Register"),
        ]
        for key, value in configs:
            try:
                req_cls = _call("ChangeConfiguration") or _call("ChangeConfigurationPayload")
                req = req_cls(key=key, value=value)
                resp = await self.call(req)
                status = getattr(resp, "status", "?")
                event_log_service.log_event(
                    "ocpp", "info",
                    f"ChangeConfiguration '{self.id}': {key}={value} → {status}"
                )
            except Exception as e:
                event_log_service.log_event(
                    "ocpp", "warning",
                    f"ChangeConfiguration '{self.id}': {key} fehlgeschlagen — {e}"
                )

    async def _fordere_messwerte_an(self):
        """Fordert per TriggerMessage sofort aktuelle Messwerte an.

        OCPP 1.6 sieht dafuer 'TriggerMessage' vor. Nicht jede Wallbox
        unterstuetzt es — schlaegt der Aufruf fehl, ist das unkritisch, dann
        kommen die Werte eben mit dem naechsten regulaeren Intervall."""
        await asyncio.sleep(0.4)
        try:
            req_cls = _call("TriggerMessage") or _call("TriggerMessagePayload")
            if req_cls is None:
                return
            resp = await self.call(req_cls(requested_message="MeterValues",
                                            connector_id=1))
            event_log_service.log_event("ocpp", "info",
                f"TriggerMessage '{self.id}': MeterValues → {getattr(resp, 'status', '?')}")
        except Exception as e:
            event_log_service.log_event("ocpp", "info",
                f"TriggerMessage '{self.id}' nicht möglich ({type(e).__name__}) — "
                f"Werte kommen mit dem nächsten Messintervall.")

    async def _send_remote_start(self):
        """Sendet RemoteStartTransaction an die Wallbox nachdem 'Preparing'
        empfangen wurde. Laut Loxone-Doku: 'Transaktionen können durch den
        OCPP-Server aus der Ferne gestartet werden.' Mit
        AuthorizeRemoteTxRequests=false startet die Wallbox sofort."""
        await asyncio.sleep(0.5)
        try:
            req_cls = _call("RemoteStartTransaction") or _call("RemoteStartTransactionPayload")
            req = req_cls(connector_id=1, id_tag="CHARGEATHOME")
            response = await self.call(req)
            status = getattr(response, "status", "?")
            event_log_service.log_event(
                "ocpp", "info",
                f"RemoteStartTransaction '{self.id}' → {status}"
            )
        except Exception as e:
            event_log_service.log_event(
                "ocpp", "warning",
                f"RemoteStartTransaction '{self.id}' fehlgeschlagen: {e}"
            )

    @on("StatusNotification")
    async def on_status_notification(self, connector_id, status, error_code, **kwargs):
        ocpp_service.handle_status_notification(self.id, status)
        event_log_service.log_event("ocpp", "info", f"StatusNotification '{self.id}': {status}")

        # RemoteStartTransaction nur senden wenn die Wallbox tatsaechlich
        # auf Autorisierung wartet — NICHT auf Available (Fahrzeug getrennt).
        # Aus dem Log-Test bestätigt: Preparing → Accepted → StartTransaction ✅
        if connector_id > 0 and status in ("Preparing", "SuspendedEV", "SuspendedEVSE"):
            asyncio.ensure_future(self._send_remote_start())

        # Beim Wechsel auf 'Charging' sofort Messwerte anfordern, statt bis zum
        # naechsten Intervall zu warten. Ohne das bleibt die Anzeige nach dem
        # Einstecken bis zu 15 Sekunden leer — genau in dem Moment, in dem man
        # hinschaut.
        if connector_id > 0 and status == "Charging":
            asyncio.ensure_future(self._fordere_messwerte_an())

        return _cr("StatusNotification")()


class _RawLoggingWebSocket:
    """Durchreich-Wrapper um das echte Websocket-Objekt — protokolliert JEDE
    eingehende Rohnachricht als vollstaendigen String im Protokoll, BEVOR die
    ocpp-Bibliothek sie ueberhaupt interpretiert/an einen @on()-Handler
    weiterleitet. Wichtig, weil Nachrichtentypen, fuer die wir keinen eigenen
    Handler geschrieben haben, sonst stillschweigend verworfen wuerden, ohne
    je im Protokoll sichtbar zu werden — Rueckmeldung des Auftraggebers:
    "wir brauchen eine Option, die den kompletten String der eingehenden
    OCPP-Verbindung zeigt".

    ZUSAETZLICH (Ruecksprache: "wir brauchen auf jeden Fall eine Logdatei,
    die uns anzeigt, welche Daten hier richtig ankommen"): schreibt JEDE
    Rohnachricht zusaetzlich in eine eigene, dauerhafte Logdatei (nicht der
    500-Eintraege-Rotation des gemeinsamen event_log unterworfen) und zaehlt
    persistent, welche Nachrichtentypen ueberhaupt jemals eingegangen sind
    — siehe services/ocpp_log_service.py."""

    def __init__(self, websocket, charge_point_id: str):
        self._ws = websocket
        self._charge_point_id = charge_point_id

    async def recv(self):
        message = await self._ws.recv()
        event_log_service.log_event(
            "ocpp", "info", f"ROH von '{self._charge_point_id}': {message}"
        )
        ocpp_log_service.log_raw_message("in", self._charge_point_id, message)
        ocpp_log_service.record_message_type(self._charge_point_id, message)
        return message

    async def send(self, message):
        event_log_service.log_event(
            "ocpp", "info", f"ROH an '{self._charge_point_id}': {message}"
        )
        ocpp_log_service.log_raw_message("out", self._charge_point_id, message)
        return await self._ws.send(message)

    def __getattr__(self, name):
        # Alles andere (close, ping, remote_address, ...) unveraendert durchreichen.
        return getattr(self._ws, name)


async def on_connect(websocket, path=None):
    """Kompatibel mit alten und neuen websockets-Versionen: aeltere Versionen
    (<13) rufen den Handler mit (websocket, path) auf, neuere (>=13, ueber
    websockets.asyncio.server) nur noch mit (websocket) — der Pfad muss dort
    aus dem Connection-Objekt selbst gelesen werden. Fund aus echtem
    Praxistest: TypeError: on_connect() missing 1 required positional
    argument: 'path' bei der neueren API-Variante."""
    if path is None:
        request = getattr(websocket, "request", None)
        path = getattr(request, "path", None) if request is not None else None
        if path is None:
            path = getattr(websocket, "path", "/ocpp/unknown")
    charge_point_id = path.strip("/").split("/")[-1]

    # Hauptschalter (Einstellungen > OCPP): bei Deaktivierung wird die
    # Verbindung sofort abgelehnt, es werden keine Ladedaten verarbeitet.
    try:
        enabled = (settings_repository.get_setting("ocpp_server_enabled") or "1") == "1"
    except Exception:
        enabled = True
    if not enabled:
        event_log_service.log_event(
            "ocpp", "warning",
            f"Verbindung von '{charge_point_id}' abgelehnt: OCPP-Dienst ist deaktiviert."
        )
        await websocket.close(code=1013, reason="OCPP-Dienst deaktiviert")
        return

    event_log_service.log_event("ocpp", "info", f"Eingehende WebSocket-Verbindung: '{charge_point_id}' (Pfad: {path})")
    wrapped = _RawLoggingWebSocket(websocket, charge_point_id)
    cp = ChargePoint(charge_point_id, wrapped)
    await cp.start()


async def main():
    print(f"websockets-Version: {getattr(websockets, '__version__', 'unbekannt')}")
    server = await websockets.serve(
        on_connect, "0.0.0.0", ocpp_port(), subprotocols=["ocpp1.6"]
    )
    print(f"OCPP-Central-System hoert auf Port {ocpp_port()} (ws://0.0.0.0:{ocpp_port()}/ocpp/<ChargePointId>)")
    await server.wait_closed()


if __name__ == "__main__":
    asyncio.run(main())
