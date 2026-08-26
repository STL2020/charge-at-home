"""Veröffentlicht die OCPP-Daten über MQTT.

WARUM OHNE FREMDBIBLIOTHEK
--------------------------
'paho-mqtt' waere der uebliche Weg, ist aber auf einem frisch aufgesetzten
LoxBerry nicht vorhanden — und 'pip install' scheitert dort haeufig. Zum
reinen Veroeffentlichen braucht es nur zwei Pakettypen des Protokolls
(CONNECT und PUBLISH); das ist mit der Standardbibliothek machbar und macht
das Plugin unabhaengig von einer Installation, die schiefgehen kann.

Empfangen wird nichts. Wer Befehle an die Wallbox senden will, tut das ueber
deren eigene Oberflaeche — dafuer einen MQTT-Client mit Abonnements
mitzuschleppen, waere unverhaeltnismaessig.

EINSTELLUNGEN
-------------
Ist auf dem LoxBerry das MQTT-Gateway eingerichtet, werden dessen Zugangsdaten
uebernommen — Broker, Port, Benutzername, Passwort. Der Anwender muss dann
nichts eintragen. Nur wer einen anderen Broker nutzen will, traegt ihn von
Hand ein.

THEMEN
------
    echargeocpp/status                    Betriebszustand des Dienstes
    echargeocpp/<punkt>/verbunden         1 = Wallbox verbunden
    echargeocpp/<punkt>/laedt             1 = Ladung laeuft
    echargeocpp/<punkt>/leistung_kw       aktuelle Ladeleistung
    echargeocpp/<punkt>/zaehler_wh        Zaehlerstand
    echargeocpp/<punkt>/strom_a           Ladestrom
    echargeocpp/<punkt>/spannung_v        Spannung
    echargeocpp/<punkt>/lademodus         Modus laut Wallbox
    echargeocpp/<punkt>/benutzer          RFID-Kennung
    echargeocpp/<punkt>/ereignis          letzte Statusmeldung
    echargeocpp/<punkt>/ladung            abgeschlossene Ladung als JSON

Einzelwerte und JSON nebeneinander: Loxone verarbeitet einzelne Zahlen
unmittelbar, waehrend andere Systeme lieber ein vollstaendiges Objekt lesen.
"""
from __future__ import annotations

import json
import os
import socket
import struct
import threading
import time

# Wo das MQTT-Gateway seine Einstellungen ablegt. Ab LoxBerry 3.0 gehoert es
# zum Kern, liegt aber weiterhin am selben Ort.
GATEWAY_PFADE = (
    "/opt/loxberry/config/plugins/mqttgateway/mqtt.json",
    "/opt/loxberry/config/system/mqtt/mqtt.json",
)


def gateway_einstellungen() -> dict | None:
    """Liest Broker und Zugangsdaten aus dem MQTT-Gateway.

    Rückgabe None, wenn kein Gateway eingerichtet ist."""
    heim = os.environ.get("LBHOMEDIR", "/opt/loxberry")
    pfade = list(GATEWAY_PFADE) + [
        os.path.join(heim, "config/plugins/mqttgateway/mqtt.json"),
        os.path.join(heim, "config/system/mqtt/mqtt.json"),
    ]
    for pfad in pfade:
        try:
            with open(pfad, encoding="utf-8") as f:
                daten = json.load(f)
        except (OSError, ValueError):
            continue

        # Der Aufbau unterscheidet sich je nach Fassung: mal direkt, mal
        # unter "Main". Beides beruecksichtigen statt auf eine zu wetten.
        haupt = daten.get("Main", daten) if isinstance(daten, dict) else {}
        broker = (haupt.get("brokeraddress") or haupt.get("broker") or "").strip()
        if not broker:
            continue
        host, _, port = broker.partition(":")
        return {
            "host": host or "localhost",
            "port": int(port) if port.isdigit() else 1883,
            "benutzer": haupt.get("brokeruser") or "",
            "passwort": haupt.get("brokerpass") or "",
            "quelle": os.path.basename(os.path.dirname(pfad)),
        }
    return None


# ── Minimale MQTT-Umsetzung ────────────────────────────────────────────────

def _laenge(zahl: int) -> bytes:
    """Kodiert eine Laenge im MQTT-Format (variable Byteanzahl)."""
    ergebnis = b""
    while True:
        teil = zahl % 128
        zahl //= 128
        if zahl > 0:
            teil |= 0x80
        ergebnis += bytes([teil])
        if zahl == 0:
            return ergebnis


def _text(wert: str) -> bytes:
    """Zeichenkette mit vorangestellter Laenge."""
    roh = wert.encode("utf-8")
    return struct.pack(">H", len(roh)) + roh


class Verbindung:
    """Eine MQTT-Verbindung zum Veröffentlichen.

    Bewusst schlicht: verbinden, senden, bei Abbruch neu verbinden. Ein
    Ladevorgang dauert Stunden, in denen die Verbindung stehen muss — deshalb
    ein Lebenszeichen im Hintergrund."""

    def __init__(self, host: str, port: int = 1883, benutzer: str = "",
                 passwort: str = "", kennung: str = "echargeocpp"):
        self.host = host
        self.port = port
        self.benutzer = benutzer
        self.passwort = passwort
        self.kennung = f"{kennung}-{os.getpid()}"
        self.sock: socket.socket | None = None
        self._sperre = threading.Lock()
        self.verbunden = False
        self.letzter_fehler = ""

    def verbinde(self) -> bool:
        try:
            sock = socket.create_connection((self.host, self.port), timeout=6)
            sock.settimeout(6)

            merkmale = 0x02          # saubere Sitzung
            rumpf = _text("MQTT") + bytes([4])   # Protokollfassung 3.1.1
            if self.benutzer:
                merkmale |= 0x80
                if self.passwort:
                    merkmale |= 0x40
            rumpf += bytes([merkmale]) + struct.pack(">H", 60)   # 60 s Lebenszeichen
            rumpf += _text(self.kennung)
            if self.benutzer:
                rumpf += _text(self.benutzer)
                if self.passwort:
                    rumpf += _text(self.passwort)

            sock.sendall(bytes([0x10]) + _laenge(len(rumpf)) + rumpf)

            antwort = sock.recv(4)
            if len(antwort) < 4 or antwort[0] != 0x20:
                sock.close()
                self.letzter_fehler = "Unerwartete Antwort des Brokers"
                return False
            if antwort[3] != 0:
                gruende = {1: "Protokollfassung abgelehnt",
                           2: "Kennung abgelehnt",
                           3: "Broker nicht verfügbar",
                           4: "Benutzername oder Passwort falsch",
                           5: "Nicht berechtigt"}
                self.letzter_fehler = gruende.get(antwort[3], f"Code {antwort[3]}")
                sock.close()
                return False

            self.sock = sock
            self.verbunden = True
            self.letzter_fehler = ""
            threading.Thread(target=self._lebenszeichen, daemon=True).start()
            return True
        except Exception as e:
            self.letzter_fehler = f"{type(e).__name__}: {e}"
            self.verbunden = False
            return False

    def _lebenszeichen(self) -> None:
        """Sendet regelmäßig PINGREQ, damit der Broker die Verbindung hält."""
        while self.verbunden and self.sock:
            time.sleep(30)
            try:
                with self._sperre:
                    if self.sock:
                        self.sock.sendall(bytes([0xC0, 0x00]))
            except Exception:
                self.verbunden = False
                return

    def sende(self, thema: str, inhalt, behalten: bool = True) -> bool:
        """Veröffentlicht einen Wert.

        `behalten` sorgt dafür, dass der Broker den letzten Wert vorhält —
        ein später hinzukommender Empfänger sieht den Zustand sofort, statt
        auf die nächste Änderung zu warten."""
        if not self.verbunden and not self.verbinde():
            return False
        if isinstance(inhalt, (dict, list)):
            inhalt = json.dumps(inhalt, ensure_ascii=False)
        elif isinstance(inhalt, bool):
            inhalt = "1" if inhalt else "0"
        roh = str(inhalt).encode("utf-8")

        rumpf = _text(thema) + roh
        kopf = 0x30 | (0x01 if behalten else 0)
        try:
            with self._sperre:
                if not self.sock:
                    return False
                self.sock.sendall(bytes([kopf]) + _laenge(len(rumpf)) + rumpf)
            return True
        except Exception as e:
            self.letzter_fehler = f"{type(e).__name__}: {e}"
            self.verbunden = False
            try:
                if self.sock:
                    self.sock.close()
            except Exception:
                pass
            self.sock = None
            return False

    def trenne(self) -> None:
        self.verbunden = False
        try:
            if self.sock:
                with self._sperre:
                    self.sock.sendall(bytes([0xE0, 0x00]))
                    self.sock.close()
        except Exception:
            pass
        self.sock = None


# ── Anbindung an den Dienst ────────────────────────────────────────────────

class Veroeffentlicher:
    """Nimmt Werte entgegen und schickt sie an den Broker.

    Fehlschläge werden nicht weitergereicht: Ein nicht erreichbarer Broker
    darf die Erfassung der Ladevorgänge nicht stören — die ist das
    Wesentliche, MQTT nur eine zusätzliche Ausgabe."""

    def __init__(self, konfiguration: dict, protokoll):
        self.konfig = konfiguration or {}
        self.log = protokoll
        self.basis = (self.konfig.get("mqtt_topic") or "echargeocpp").strip("/")
        self.verbindung: Verbindung | None = None
        self.aktiv = bool(self.konfig.get("mqtt_aktiv"))
        self._gemeldet = False

    def starte(self) -> None:
        if not self.aktiv:
            return
        einstellungen = self._einstellungen()
        if not einstellungen:
            self.log("MQTT", "Kein Broker gefunden. Entweder das MQTT-Gateway "
                             "einrichten oder unter Einstellungen einen Broker "
                             "eintragen.", "WARN")
            self.aktiv = False
            return

        self.verbindung = Verbindung(
            einstellungen["host"], einstellungen["port"],
            einstellungen["benutzer"], einstellungen["passwort"])
        if self.verbindung.verbinde():
            woher = ("aus dem MQTT-Gateway" if einstellungen.get("quelle")
                     else "aus den Einstellungen")
            self.log("MQTT", f"Verbunden mit {einstellungen['host']}:"
                             f"{einstellungen['port']} ({woher}), "
                             f"Themenpräfix '{self.basis}'")
            self.sende("status", "bereit")
        else:
            self.log("MQTT", f"Broker {einstellungen['host']}:{einstellungen['port']} "
                             f"nicht erreichbar: {self.verbindung.letzter_fehler}",
                     "WARN")

    def _einstellungen(self) -> dict | None:
        """Eigene Angaben haben Vorrang, sonst das Gateway."""
        host = (self.konfig.get("mqtt_host") or "").strip()
        if host:
            return {"host": host,
                    "port": int(self.konfig.get("mqtt_port") or 1883),
                    "benutzer": self.konfig.get("mqtt_user") or "",
                    "passwort": self.konfig.get("mqtt_pass") or "",
                    "quelle": ""}
        return gateway_einstellungen()

    def sende(self, thema: str, wert, behalten: bool = True) -> None:
        if not self.aktiv or not self.verbindung:
            return
        erfolg = self.verbindung.sende(f"{self.basis}/{thema}", wert, behalten)
        if not erfolg and not self._gemeldet:
            # Nur einmal melden, sonst füllt ein ausgefallener Broker das
            # Protokoll bei jedem Messwert.
            self.log("MQTT", f"Senden fehlgeschlagen: "
                             f"{self.verbindung.letzter_fehler}", "WARN")
            self._gemeldet = True
        elif erfolg:
            self._gemeldet = False

    def sende_zustand(self, punkt: str, werte: dict) -> None:
        """Veröffentlicht die Live-Werte eines Ladepunkts."""
        for name, wert in werte.items():
            if wert is not None:
                self.sende(f"{punkt}/{name}", wert)

    def sende_ladung(self, punkt: str, ladung: dict) -> None:
        """Veröffentlicht eine abgeschlossene Ladung — einzeln und als JSON."""
        self.sende(f"{punkt}/ladung", ladung)
        for name in ("energie_kwh", "zaehler_start_wh", "zaehler_ende_wh",
                     "dauer_min", "kosten_eur"):
            if name in ladung:
                self.sende(f"{punkt}/letzte_ladung/{name}", ladung[name])

    def beende(self) -> None:
        if self.verbindung:
            self.sende("status", "beendet")
            self.verbindung.trenne()
