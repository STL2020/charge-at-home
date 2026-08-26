"""OCPP 1.6-J Server für das LoxBerry-Plugin.

WARUM OHNE FREMDBIBLIOTHEKEN
----------------------------
Ein LoxBerry-Plugin soll sich installieren lassen, ohne dass der Anwender
erst Pakete nachinstalliert. Die verbreiteten Bibliotheken 'ocpp' und
'websockets' bringen Abhängigkeiten mit, die auf älteren Raspberry-Systemen
Ärger machen. OCPP 1.6-J ist im Kern überschaubar — WebSocket-Rahmen plus
JSON-Listen —, deshalb ist es hier direkt mit der Standardbibliothek
umgesetzt.

WAS DAS PROTOKOLL VERLANGT
--------------------------
Jede Nachricht ist eine JSON-Liste:

    [2, "<id>", "<Aktion>", {…}]   Anfrage der Wallbox (CALL)
    [3, "<id>", {…}]               unsere Antwort (CALLRESULT)
    [4, "<id>", "<Code>", "…", {}] Fehler (CALLERROR)

Die Wallbox baut die Verbindung auf und schickt Anfragen; der Server
antwortet. Umgekehrt darf der Server ebenfalls Anfragen stellen — davon
nutzen wir 'TriggerMessage', um nach dem Ladebeginn sofort Messwerte zu
bekommen, statt bis zum nächsten Intervall zu warten.

ABLAUF EINER LADUNG
-------------------
    BootNotification      Wallbox meldet sich nach dem Einschalten
    StatusNotification    "Preparing" → Fahrzeug steckt
    StartTransaction      Ladebeginn mit Zählerstand
    MeterValues           laufende Messwerte
    StopTransaction       Ladeende mit Zählerstand
"""
from __future__ import annotations

import base64
import hashlib
import json
import socket
import sqlite3
import struct
import threading
import time
import uuid
from datetime import datetime, timezone

# WebSocket-Kennung laut RFC 6455 — fest vorgegeben, nicht frei wählbar
WS_MAGIC = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"

CALL = 2
CALLRESULT = 3
CALLERROR = 4

# Messwerte im 15-Sekunden-Takt: Bei 60 Sekunden wirkt die Anzeige träge und
# kurze Ladevorgänge bleiben ohne einen einzigen Zwischenwert.
MESSINTERVALL_S = 15
MESSWERTE = ("Energy.Active.Import.Register,Power.Active.Import,"
             "Current.Import,Voltage")


def _jetzt() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


# ── WebSocket-Rahmen ───────────────────────────────────────────────────────

def _lies_rahmen(sock: socket.socket) -> tuple[int, bytes] | None:
    """Liest einen WebSocket-Rahmen.

    Rückgabe: (Opcode, Nutzdaten) oder None, wenn die Gegenstelle geht.
    Fortsetzungsrahmen bleiben unberücksichtigt — OCPP-Nachrichten sind
    klein genug, dass Wallboxen sie nicht aufteilen."""
    def genau(n: int) -> bytes | None:
        puffer = b""
        while len(puffer) < n:
            teil = sock.recv(n - len(puffer))
            if not teil:
                return None
            puffer += teil
        return puffer

    kopf = genau(2)
    if not kopf:
        return None
    opcode = kopf[0] & 0x0F
    maskiert = bool(kopf[1] & 0x80)
    laenge = kopf[1] & 0x7F

    if laenge == 126:
        erweitert = genau(2)
        if not erweitert:
            return None
        laenge = struct.unpack(">H", erweitert)[0]
    elif laenge == 127:
        erweitert = genau(8)
        if not erweitert:
            return None
        laenge = struct.unpack(">Q", erweitert)[0]

    maske = genau(4) if maskiert else None
    daten = genau(laenge) if laenge else b""
    if daten is None:
        return None
    if maske:
        # Clients müssen maskieren; die Umkehrung ist ein einfaches XOR
        daten = bytes(b ^ maske[i % 4] for i, b in enumerate(daten))
    return opcode, daten


def _sende_rahmen(sock: socket.socket, daten: bytes, opcode: int = 0x1) -> None:
    """Sendet einen Rahmen. Server maskieren nicht."""
    kopf = bytearray([0x80 | opcode])
    laenge = len(daten)
    if laenge < 126:
        kopf.append(laenge)
    elif laenge < 65536:
        kopf.append(126)
        kopf += struct.pack(">H", laenge)
    else:
        kopf.append(127)
        kopf += struct.pack(">Q", laenge)
    sock.sendall(bytes(kopf) + daten)


def _handshake(sock: socket.socket) -> str | None:
    """Beantwortet den Verbindungsaufbau.

    Rückgabe: die Charge-Point-Kennung aus dem Pfad, etwa '/ocpp/WB1' → 'WB1'.
    Wallboxen hängen ihre Kennung an die konfigurierte Adresse an."""
    daten = sock.recv(4096)
    if not daten or b"Upgrade: websocket" not in daten:
        return None
    text = daten.decode("utf-8", errors="ignore")
    zeilen = text.split("\r\n")

    schluessel = ""
    for zeile in zeilen:
        if zeile.lower().startswith("sec-websocket-key:"):
            schluessel = zeile.split(":", 1)[1].strip()
    if not schluessel:
        return None

    pfad = zeilen[0].split(" ")[1] if zeilen else "/"
    kennung = pfad.rstrip("/").split("/")[-1] or "unbekannt"

    akzept = base64.b64encode(
        hashlib.sha1((schluessel + WS_MAGIC).encode()).digest()).decode()
    antwort = ("HTTP/1.1 101 Switching Protocols\r\n"
               "Upgrade: websocket\r\n"
               "Connection: Upgrade\r\n"
               f"Sec-WebSocket-Accept: {akzept}\r\n"
               "Sec-WebSocket-Protocol: ocpp1.6\r\n\r\n")
    sock.sendall(antwort.encode())
    return kennung


# ── OCPP-Verarbeitung ──────────────────────────────────────────────────────

class Ladepunkt:
    """Eine verbundene Wallbox."""

    def __init__(self, sock: socket.socket, kennung: str, db_pfad: str,
                 protokoll, zustand: dict, mqtt=None):
        self.sock = sock
        self.kennung = kennung
        self.db_pfad = db_pfad
        self.log = protokoll
        self.zustand = zustand
        # Optionale MQTT-Ausgabe. Faellt sie aus, laeuft die Erfassung weiter —
        # sie ist eine Zugabe, keine Voraussetzung.
        self.mqtt = mqtt
        self.transaktionen: dict[int, dict] = {}
        self.letzte_leistung = 0.0
        # Welche Anfrage zu welcher Nachrichten-ID gehört — sonst lässt sich
        # eine eintreffende Antwort nicht zuordnen.
        self.offene_anfragen: dict[str, str] = {}
        self.messwert_erhalten = False

    # ── Versand ────────────────────────────────────────────────────────────
    def _antworte(self, nachrichten_id: str, nutzlast: dict) -> None:
        _sende_rahmen(self.sock, json.dumps(
            [CALLRESULT, nachrichten_id, nutzlast]).encode())

    def _frage(self, aktion: str, nutzlast: dict) -> None:
        """Stellt der Wallbox eine Anfrage. Die Antwort wird nicht abgewartet —
        sie läuft über die normale Empfangsschleife."""
        mid = str(uuid.uuid4())
        self.offene_anfragen[mid] = aktion
        if len(self.offene_anfragen) > 40:      # nicht unbegrenzt wachsen lassen
            self.offene_anfragen.pop(next(iter(self.offene_anfragen)))
        _sende_rahmen(self.sock, json.dumps([CALL, mid, aktion, nutzlast]).encode())

    # ── Empfang ────────────────────────────────────────────────────────────
    def bearbeite(self, rohdaten: bytes) -> None:
        try:
            nachricht = json.loads(rohdaten.decode("utf-8"))
        except Exception:
            self.log("OCPP", f"Unlesbare Nachricht von {self.kennung}", "WARN")
            return
        if not isinstance(nachricht, list) or len(nachricht) < 3:
            return

        typ = nachricht[0]

        # Antworten auf eigene Anfragen: Bisher wurden sie verworfen — damit
        # blieb unsichtbar, ob die Wallbox unsere Konfiguration überhaupt
        # angenommen hat. Genau das ist aber die Frage, wenn keine Messwerte
        # eintreffen.
        if typ == CALLRESULT:
            inhalt = nachricht[2] if len(nachricht) > 2 else {}
            offen = self.offene_anfragen.pop(nachricht[1], None)
            if offen:
                status = (inhalt or {}).get("status", "")
                if offen == "ChangeConfiguration":
                    # Nicht jede Wallbox kennt jede Einstellung — go-e etwa
                    # lehnt zwei der vier ab und liefert trotzdem tadellose
                    # Messwerte. Das ist normal und kein Warnfall. Gezählt
                    # wird trotzdem, damit am Ende eine Bilanz im Protokoll
                    # steht statt vier Einzelmeldungen.
                    if status == "Accepted":
                        self.konfig_ok += 1
                    else:
                        self.konfig_abgelehnt.append(status)
                elif status in ("Rejected", "NotSupported"):
                    self.log("OCPP", f"{self.kennung}: '{offen}' abgelehnt ({status})", "WARN")
            return

        if typ == CALLERROR:
            offen = self.offene_anfragen.pop(nachricht[1], "Anfrage")
            fehler = nachricht[2] if len(nachricht) > 2 else "?"
            self.log("OCPP", f"{self.kennung}: '{offen}' fehlgeschlagen — {fehler}", "WARN")
            return

        if typ != CALL:
            return

        _, nachrichten_id, aktion = nachricht[0], nachricht[1], nachricht[2]
        nutzlast = nachricht[3] if len(nachricht) > 3 else {}
        behandler = getattr(self, f"_bei_{aktion.lower()}", None)
        if behandler is None:
            # Unbekannte Aktionen bestätigen statt abzulehnen: Manche
            # Wallboxen trennen die Verbindung nach einem CALLERROR.
            self._antworte(nachrichten_id, {})
            return
        try:
            behandler(nachrichten_id, nutzlast)
        except Exception as e:
            self.log("OCPP", f"Fehler bei {aktion}: {e}", "WARN")
            self._antworte(nachrichten_id, {})

    # ── Einzelne Aktionen ──────────────────────────────────────────────────
    def _bei_bootnotification(self, mid: str, p: dict) -> None:
        hersteller = p.get("chargePointVendor", "?")
        modell = p.get("chargePointModel", "?")
        self.log("OCPP", f"Wallbox angemeldet: {hersteller} {modell} ({self.kennung})")
        if self.mqtt:
            self.mqtt.sende_zustand(self.kennung, {
                "hersteller": hersteller, "modell": modell,
                "seriennummer": p.get("chargePointSerialNumber", ""),
                "firmware": p.get("firmwareVersion", ""),
                "online": True})
        self._antworte(mid, {"currentTime": _jetzt(), "interval": 300, "status": "Accepted"})
        # Nach dem Handschlag konfigurieren — vorher ignorieren manche Geräte
        threading.Timer(1.0, self._konfiguriere).start()

    def _konfiguriere(self) -> None:
        """Stellt Messintervall und Messgrößen ein."""
        konfig = (
            ("MeterValueSampleInterval", str(MESSINTERVALL_S)),
            ("MeterValuesSampledData", MESSWERTE),
            ("StopTxnSampledData", "Energy.Active.Import.Register"),
            ("AuthorizeRemoteTxRequests", "false"),
        )
        ok = 0
        for schluessel, wert in konfig:
            try:
                self._frage("ChangeConfiguration", {"key": schluessel, "value": wert})
                ok += 1
            except Exception as e:
                self.log("OCPP", f"ChangeConfiguration {schluessel} fehlgeschlagen: {e}", "WARN")
                break
        if ok == len(konfig):
            # Kurz warten, bis die Antworten da sind — dann eine
            # zusammenfassende Meldung statt vier einzelner.
            def _bilanz():
                time.sleep(2.5)
                gesamt = len(konfig)
                if not self.konfig_abgelehnt:
                    self.log("OCPP", f"{self.kennung}: Konfiguriert — "
                                     f"Messwerte alle {MESSINTERVALL_S} s")
                else:
                    self.log("OCPP",
                             f"{self.kennung}: {self.konfig_ok} von {gesamt} "
                             f"Einstellungen übernommen. Die übrigen kennt diese "
                             f"Wallbox nicht — das ist normal, die Messwerte "
                             f"kommen trotzdem.")
            threading.Thread(target=_bilanz, daemon=True).start()
            # Sofort einen Messwert anfordern. Bleibt er aus, meldet die
            # Wallbox entweder nichts oder unterstützt TriggerMessage nicht —
            # in beiden Fällen soll das im Protokoll stehen, statt dass der
            # Anwender vor einer stillen Anzeige sitzt.
            threading.Timer(2.0, self._pruefe_messwerte).start()

    def _pruefe_messwerte(self) -> None:
        """Fordert einen Messwert an und meldet, wenn keiner eintrifft."""
        self.messwert_erhalten = False
        try:
            self._frage("TriggerMessage",
                        {"requestedMessage": "MeterValues", "connectorId": 1})
        except Exception:
            return

        def nachsehen():
            if not self.messwert_erhalten:
                self.log("OCPP", f"{self.kennung}: Noch keine Messwerte erhalten. "
                                 f"Manche Wallboxen senden erst beim Laden.", "WARN")
        threading.Timer(20.0, nachsehen).start()

    def _bei_heartbeat(self, mid: str, p: dict) -> None:
        self._antworte(mid, {"currentTime": _jetzt()})

    def _bei_authorize(self, mid: str, p: dict) -> None:
        # Im Privathaushalt wird jede Karte akzeptiert; die Zuordnung erfolgt
        # später über die Kennung, nicht über eine Freigabeliste.
        self._antworte(mid, {"idTagInfo": {"status": "Accepted"}})

    def _bei_statusnotification(self, mid: str, p: dict) -> None:
        status = p.get("status", "")
        self._antworte(mid, {})
        if status in ("Charging", "Preparing", "SuspendedEV", "SuspendedEVSE", "Available"):
            self.log("OCPP", f"{self.kennung}: {status}")
        if self.mqtt:
            self.mqtt.sende_zustand(self.kennung, {
                "ereignis": status,
                "verbunden": status not in ("Available", "Unavailable", "Faulted"),
                "laedt": status == "Charging",
                "fehler": p.get("errorCode", "NoError"),
            })
        if status == "Charging":
            # Sofort Messwerte anfordern, statt bis zum Intervall zu warten
            try:
                self._frage("TriggerMessage",
                            {"requestedMessage": "MeterValues", "connectorId": 1})
            except Exception:
                pass

    def _bei_starttransaction(self, mid: str, p: dict) -> None:
        zaehler = int(p.get("meterStart", 0) or 0)
        transaktion = int(datetime.now().timestamp()) % 2_000_000
        self.transaktionen[transaktion] = {
            "start": p.get("timestamp") or _jetzt(),
            "zaehler_start": zaehler,
            "tag": p.get("idTag", ""),
        }
        self.log("OCPP", f"{self.kennung}: Ladung begonnen, Zähler {zaehler} Wh")
        self._antworte(mid, {"transactionId": transaktion,
                             "idTagInfo": {"status": "Accepted"}})
        self.zustand["laedt"] = True
        if self.mqtt:
            self.mqtt.sende_zustand(self.kennung, {
                "laedt": True, "benutzer": p.get("idTag", ""),
                "zaehler_start_wh": zaehler, "transaktion": transaktion})

    # Zuordnung der OCPP-Messgroessen auf lesbare Namen. Alles, was die
    # Wallbox meldet, wird weitergereicht — was der Anwender damit macht,
    # ist seine Sache.
    MESSGROESSEN = {
        "Power.Active.Import": "leistung_kw",
        "Energy.Active.Import.Register": "zaehler_wh",
        "Current.Import": "strom_a",
        "Current.Offered": "strom_angeboten_a",
        "Voltage": "spannung_v",
        "Temperature": "temperatur_c",
        "SoC": "ladestand_prozent",
        "Power.Offered": "leistung_angeboten_kw",
        "Frequency": "frequenz_hz",
    }

    def _bei_metervalues(self, mid: str, p: dict) -> None:
        self._antworte(mid, {})
        gemessen: dict[str, float] = {}

        for eintrag in p.get("meterValue", []):
            for wert in eintrag.get("sampledValue", []):
                messgroesse = wert.get("measurand", "Energy.Active.Import.Register")
                try:
                    zahl = float(wert.get("value", 0))
                except (TypeError, ValueError):
                    continue

                name = self.MESSGROESSEN.get(messgroesse)
                if name is None:
                    # Unbekannte Groessen trotzdem weiterreichen: Manche
                    # Hersteller melden Eigenes, das dem Anwender nuetzen kann.
                    name = messgroesse.replace(".", "_").lower()

                if messgroesse in ("Power.Active.Import", "Power.Offered"):
                    if (wert.get("unit") or "W").upper() == "W":
                        zahl /= 1000.0
                    zahl = round(zahl, 3)

                # Je Phase getrennt melden, wenn die Wallbox das unterscheidet
                phase = wert.get("phase")
                gemessen[f"{name}_{phase.lower()}" if phase else name] = zahl

        if "leistung_kw" in gemessen:
            self.letzte_leistung = gemessen["leistung_kw"]
            self.zustand["leistung_kw"] = gemessen["leistung_kw"]
        if "zaehler_wh" in gemessen:
            self.zustand["zaehler_wh"] = int(gemessen["zaehler_wh"])

        if gemessen:
            self.messwert_erhalten = True
            leistung = gemessen.get("leistung_kw", "–")
            zaehler  = gemessen.get("zaehler_wh", "–")
            self.log("OCPP", f"{self.kennung}: {leistung} kW · Zähler {zaehler} Wh")
            if self.mqtt:
                self.mqtt.sende_zustand(self.kennung, gemessen)

    def _bei_stoptransaction(self, mid: str, p: dict) -> None:
        self._antworte(mid, {"idTagInfo": {"status": "Accepted"}})
        transaktion = int(p.get("transactionId", 0) or 0)
        daten = self.transaktionen.pop(transaktion, None)
        zaehler_ende = int(p.get("meterStop", 0) or 0)
        self.zustand["laedt"] = False
        self.zustand["leistung_kw"] = 0.0

        if daten is None:
            self.log("OCPP", f"{self.kennung}: Ladeende ohne bekannten Beginn — "
                             f"verworfen (Zähler {zaehler_ende} Wh)", "WARN")
            return

        menge_wh = max(0, zaehler_ende - daten["zaehler_start"])
        if menge_wh <= 0:
            self.log("OCPP", f"{self.kennung}: Ladung ohne Energiefluss — nicht gespeichert")
            return

        self._speichere(daten["start"], p.get("timestamp") or _jetzt(),
                        daten["zaehler_start"], zaehler_ende, menge_wh)
        self.log("OCPP", f"{self.kennung}: Ladung beendet, {menge_wh / 1000:.2f} kWh")

        if self.mqtt:
            dauer = 0
            try:
                von = datetime.fromisoformat(daten["start"].replace("Z", "+00:00"))
                bis = datetime.fromisoformat(
                    (p.get("timestamp") or _jetzt()).replace("Z", "+00:00"))
                dauer = int((bis - von).total_seconds() / 60)
            except Exception:
                pass
            self.mqtt.sende_ladung(self.kennung, {
                "energie_kwh": round(menge_wh / 1000.0, 3),
                "zaehler_start_wh": daten["zaehler_start"],
                "zaehler_ende_wh": zaehler_ende,
                "dauer_min": dauer,
                "benutzer": daten.get("tag", ""),
                "beginn": daten["start"],
                "ende": p.get("timestamp") or _jetzt(),
            })
            self.mqtt.sende_zustand(self.kennung, {"laedt": False, "leistung_kw": 0})

    def _speichere(self, start: str, ende: str, zaehler_start: int,
                   zaehler_ende: int, menge_wh: int) -> None:
        """Legt den abgeschlossenen Ladevorgang ab.

        Zählerstände werden mitgeführt: Sie sind der belastbare Nachweis
        gegenüber Arbeitgeber und Finanzamt — eine bloße Mengenangabe ist
        eine Behauptung."""
        def als_lokal(text: str) -> str:
            try:
                return (datetime.fromisoformat(text.replace("Z", "+00:00"))
                        .astimezone().strftime("%Y-%m-%d %H:%M:%S"))
            except Exception:
                return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        kwh = menge_wh / 1000.0
        conn = sqlite3.connect(self.db_pfad)
        try:
            preis = 0.28
            try:
                zeile = conn.execute(
                    "SELECT value FROM config WHERE key = 'price_per_kwh'").fetchone()
                if zeile:
                    preis = float(zeile[0])
            except sqlite3.Error:
                pass
            conn.execute(
                """INSERT INTO charging_sessions
                   (source, meter_id, start_time, end_time, meter_start, meter_stop,
                    energy_kwh, price_per_kwh, cost_eur)
                   VALUES ('OCPP', ?, ?, ?, ?, ?, ?, ?, ?)""",
                (self.kennung, als_lokal(start), als_lokal(ende),
                 zaehler_start, zaehler_ende, round(kwh, 3),
                 preis, round(kwh * preis, 2)))
            conn.commit()
        finally:
            conn.close()


def starte_server(db_pfad: str, protokoll, zustand: dict, port: int = 9000,
                  mqtt=None) -> None:
    """Nimmt Wallbox-Verbindungen entgegen. Läuft dauerhaft."""
    lauscher = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    lauscher.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        lauscher.bind(("0.0.0.0", port))
        lauscher.listen(8)
    except OSError as e:
        zustand["laeuft"] = False
        # Den Grund merken, damit die Oberflaeche ihn nennen kann, statt nur
        # "gestoppt" anzuzeigen.
        zustand["ocpp_fehler"] = (
            f"Port {port} ist bereits belegt. Bitte unter Einstellungen einen "
            f"anderen wählen — und ihn auch in der Wallbox eintragen.")
        protokoll("OCPP", f"Port {port} ist belegt — kein Wallbox-Empfang. "
                          f"Unter Einstellungen einen anderen Port wählen.", "CRITICAL")
        return

    zustand["laeuft"] = True
    zustand["ocpp_fehler"] = ""
    protokoll("OCPP", f"Server bereit auf Port {port} — wartet auf Wallboxen")

    while True:
        try:
            verbindung, adresse = lauscher.accept()
        except OSError:
            continue

        def betreue(sock: socket.socket, ip: str) -> None:
            kennung = None
            try:
                sock.settimeout(30)
                kennung = _handshake(sock)
                if not kennung:
                    sock.close()
                    return
                protokoll("OCPP", f"Verbindung von {ip} als '{kennung}'")
                # Set statt Zähler: so bleibt 2 Wallboxen = 2, auch wenn
                # sich eine kurz trennt und neu verbindet.
                if "verbundene" not in zustand:
                    zustand["verbundene"] = set()
                zustand["verbundene"].add(kennung)
                zustand["verbunden"] = len(zustand["verbundene"])
                if mqtt:
                    mqtt.sende_zustand(kennung, {"online": True, "adresse": ip})

                punkt = Ladepunkt(sock, kennung, db_pfad, protokoll, zustand, mqtt)
                # Großzügiger Zeitrahmen: Zwischen zwei Ladungen können
                # Stunden liegen, in denen nur Heartbeats kommen.
                sock.settimeout(600)
                while True:
                    rahmen = _lies_rahmen(sock)
                    if rahmen is None:
                        break
                    opcode, daten = rahmen
                    if opcode == 0x8:            # Verbindungsabbau
                        break
                    if opcode == 0x9:            # Ping → Pong
                        _sende_rahmen(sock, daten, opcode=0xA)
                        continue
                    if opcode in (0x1, 0x2):
                        punkt.bearbeite(daten)
            except socket.timeout:
                protokoll("OCPP", f"'{kennung or ip}' meldet sich nicht mehr", "WARN")
            except Exception as e:
                protokoll("OCPP", f"Verbindung zu '{kennung or ip}' beendet: "
                                  f"{type(e).__name__}", "WARN")
            finally:
                # sock auf None: laufende Hintergrund-Threads (_konfiguriere,
                # _pruefe_messwerte) sehen das und senden nicht mehr.
                if punkt:
                    punkt.sock = None
                if "verbundene" in zustand and kennung:
                    zustand["verbundene"].discard(kennung)
                    zustand["verbunden"] = len(zustand["verbundene"])
                else:
                    zustand["verbunden"] = max(0, zustand.get("verbunden", 1) - 1)
                if mqtt and kennung:
                    mqtt.sende_zustand(kennung, {"online": False, "laedt": False,
                                                 "leistung_kw": 0})
                try:
                    sock.close()
                except Exception:
                    pass

        threading.Thread(target=betreue, args=(verbindung, adresse[0]),
                         daemon=True).start()
