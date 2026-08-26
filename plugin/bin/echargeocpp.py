#!/usr/bin/env python3
"""eCharge@Home OCPP — Dienst für den LoxBerry.

AUFGABE
-------
Nimmt Ladevorgänge von Wallboxen per OCPP 1.6-J entgegen und stellt sie über
eine HTTP-Schnittstelle bereit. Mehr nicht — bewusst.

Warum auf dem LoxBerry: Ein OCPP-Server muss erreichbar sein, wenn geladen
wird. Das ist meist nachts, wenn Arbeitsplatzrechner aus sind. Der LoxBerry
läuft durch, also gehen keine Ladungen verloren.

Für Loxone-Wallboxen wird dieses Plugin nicht gebraucht — deren Daten liest
eCharge@Home unmittelbar über den Miniserver.

SCHNITTSTELLE
-------------
    GET  /api/status              Betriebszustand und letzte Meldungen
    GET  /api/sessions            Ladevorgänge (Standardzugriff)
    GET  /api/sessions?seit=…     nur ab Zeitpunkt (ISO oder Unix)
    GET  /api/log                 Protokoll
    POST /api/config              Preis je kWh setzen

Das Format entspricht dem, was eCharge@Home erwartet. Andere Anwendungen
können es ebenso lesen — es ist gewöhnliches JSON ohne Eigenheiten.

PORTS
-----
    9000   OCPP — hier melden sich die Wallboxen
    8042   HTTP — hier holen Anwendungen die Daten ab
"""
from __future__ import annotations

import json
import os
import signal
import sqlite3
import sys
import threading
import time
import urllib.parse
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

# Eigenes Verzeichnis in den Suchpfad — ocpp_server.py liegt daneben
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ocpp_server  # noqa: E402

# Standardports. Sie lassen sich in der Weboberflaeche aendern — etwa wenn
# ein anderes Plugin dieselben belegt. Weicht der Dienst selbsttaetig aus,
# muesste der Anwender das erst bemerken und die Wallbox nachziehen; besser
# ist, er legt die Ports von vornherein fest.
OCPP_PORT_STANDARD = 9000
HTTP_PORT_STANDARD = 8042

OCPP_PORT = OCPP_PORT_STANDARD
HTTP_PORT = HTTP_PORT_STANDARD


# LoxBerry gibt die Pfade über die Umgebung vor. Die Rückfallwerte greifen
# nur beim Testen außerhalb eines LoxBerry.
LBHOME = os.environ.get("LBHOMEDIR", "/opt/loxberry")
PLUGIN = os.environ.get("LBPPLUGINDIR", "echargeocpp")
DATENVERZEICHNIS = os.path.join(LBHOME, "data", "plugins", PLUGIN)
LOGVERZEICHNIS = os.path.join(LBHOME, "log", "plugins", PLUGIN)
DB_PFAD = os.path.join(DATENVERZEICHNIS, "ocpp.db")
LOG_PFAD = os.path.join(LOGVERZEICHNIS, "ocpp.log")

os.makedirs(DATENVERZEICHNIS, exist_ok=True)
os.makedirs(LOGVERZEICHNIS, exist_ok=True)

# Betriebszustand für die Anzeige. Wird vom OCPP-Server fortgeschrieben.
# Zwei getrennte Angaben:
#   dienst_laeuft   Die Schnittstelle antwortet — die Weboberflaeche ist bedienbar
#   laeuft          Der OCPP-Server nimmt Wallbox-Verbindungen an
#
# Beides zu vermengen war falsch: Ist der OCPP-Port belegt, laeuft der Dienst
# trotzdem. Die Oberflaeche meldete dann "gestoppt", obwohl sie gerade mit ihm
# sprach — und der eigentliche Grund blieb verborgen.
# Zustand der MQTT-Ausgabe — für die Anzeige in der Oberfläche.
MQTT_ZUSTAND: dict = {"verbunden": False, "broker": ""}

ZUSTAND = {
    "dienst_laeuft": True,
    "laeuft": False,
    "ocpp_fehler": "",
    "verbunden": 0,
    "leistung_kw": 0.0,
    "zaehler_wh": 0,
    "laedt": False,
    "gestartet": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
}


# ── Datenhaltung ───────────────────────────────────────────────────────────

def datenbank_anlegen() -> None:
    conn = sqlite3.connect(DB_PFAD)
    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS charging_sessions (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                source        TEXT    NOT NULL DEFAULT 'OCPP',
                meter_id      TEXT,
                start_time    TIMESTAMP NOT NULL,
                end_time      TIMESTAMP,
                meter_start   NUMERIC DEFAULT 0,
                meter_stop    NUMERIC DEFAULT 0,
                energy_kwh    NUMERIC NOT NULL,
                price_per_kwh NUMERIC DEFAULT 0.28,
                cost_eur      NUMERIC DEFAULT 0,
                created_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )""")
        # Doppelte Einträge verhindern, falls eine Wallbox dieselbe
        # Transaktion zweimal meldet — kommt bei Verbindungsabbrüchen vor.
        conn.execute("""CREATE UNIQUE INDEX IF NOT EXISTS idx_session_eindeutig
                        ON charging_sessions (meter_id, start_time)""")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS system_logs (
                id        INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                source    TEXT,
                level     TEXT,
                message   TEXT
            )""")
        conn.execute("CREATE TABLE IF NOT EXISTS config (key TEXT PRIMARY KEY, value TEXT)")
        conn.execute("INSERT OR IGNORE INTO config VALUES ('price_per_kwh', '0.28')")
        conn.commit()
    finally:
        conn.close()


def protokoll(quelle: str, text: str, stufe: str = "INFO") -> None:
    """Schreibt eine Meldung an drei Stellen.

    Logdatei      fuer den Logviewer des LoxBerry
    Datenbank     fuer das Terminal in der Weboberflaeche
    Systemlog     fuer 'journalctl' und die Ereignisanzeige des LoxBerry

    Das Systemlog nur bei Warnungen und Fehlern: Ein Dienst, der dort jede
    Kleinigkeit meldet, macht die Anzeige unbrauchbar."""
    zeit = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    zeile = f"{zeit} [{stufe}] {quelle}: {text}\n"
    try:
        with open(LOG_PFAD, "a", encoding="utf-8") as f:
            f.write(zeile)
    except OSError:
        pass

    if stufe in ("WARN", "CRITICAL"):
        try:
            import subprocess
            subprocess.run(["/usr/bin/logger", "-t", "echargeocpp",
                            f"[{stufe}] {quelle}: {text}"],
                           check=False, timeout=2)
        except Exception:
            pass
    try:
        conn = sqlite3.connect(DB_PFAD, timeout=5)
        # Zeit ausdruecklich mitgeben: Der Standardwert CURRENT_TIMESTAMP von
        # SQLite ist UTC, waehrend die Logdatei Ortszeit schreibt. Dieselbe
        # Meldung erschien dadurch zweimal mit zwei Stunden Abstand.
        conn.execute("INSERT INTO system_logs (timestamp, source, level, message) "
                     "VALUES (?,?,?,?)",
                     (zeit, quelle, stufe, text))
        # Protokoll begrenzen: Auf einem Raspberry mit SD-Karte ist
        # unbegrenztes Wachstum keine gute Idee.
        conn.execute("""DELETE FROM system_logs WHERE id NOT IN
                        (SELECT id FROM system_logs ORDER BY id DESC LIMIT 500)""")
        conn.commit()
        conn.close()
    except sqlite3.Error:
        pass
    print(f"{zeit} [{stufe}] {quelle}: {text}", flush=True)


def _zeilen(sql: str, werte: tuple = ()) -> list:
    conn = sqlite3.connect(DB_PFAD, timeout=5)
    conn.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in conn.execute(sql, werte)]
    finally:
        conn.close()


# ── HTTP-Schnittstelle ─────────────────────────────────────────────────────

# Die Einstellungen liegen in einer schlichten JSON-Datei, nicht in der
# Datenbank.
#
# Grund: Die Weboberflaeche muss sie auch dann aendern koennen, wenn der
# Dienst gerade nicht laeuft — etwa weil ein Port belegt ist und genau das
# behoben werden soll. Ginge die Aenderung ueber die HTTP-Schnittstelle des
# Dienstes, waere sie in genau dem Fall unerreichbar, in dem man sie braucht.
KONFIG_PFAD = os.path.join(DATENVERZEICHNIS, "konfig.json")


def konfiguration_lesen() -> dict:
    """Liest die Einstellungen. Fehlende Werte werden ergaenzt."""
    werte = {"ocpp_port": OCPP_PORT_STANDARD,
             "http_port": HTTP_PORT_STANDARD,
             "price_per_kwh": 0.28,
             # MQTT ist ausgeschaltet, bis der Anwender es einschaltet:
             # Ungefragt in fremde Themen zu schreiben, waere ungehoerig.
             "mqtt_aktiv": False,
             "mqtt_host": "",      # leer = Einstellungen des MQTT-Gateways
             "mqtt_port": 1883,
             "mqtt_user": "",
             "mqtt_pass": "",
             "mqtt_topic": "echargeocpp"}
    try:
        with open(KONFIG_PFAD, encoding="utf-8") as f:
            gelesen = json.load(f)
        for schluessel in werte:
            if schluessel in gelesen:
                werte[schluessel] = gelesen[schluessel]
    except (OSError, ValueError):
        pass
    return werte


def _ports_lesen() -> tuple[int, int]:
    k = konfiguration_lesen()
    return int(k["ocpp_port"]), int(k["http_port"])



# Abrufe werden gemeldet, aber nicht bei jedem Aufruf: Eine Anwendung, die
# alle paar Sekunden nachfragt, wuerde das Protokoll sonst zumuellen.
_LETZTER_ABRUF: dict[str, float] = {}


def _melde_abruf(ip: str, kennung: str) -> None:
    jetzt = time.time()
    # 30 Sekunden statt 5 Minuten: Der Anwender will sehen, DASS abgerufen
    # wird — bei einer Prüfung alle paar Sekunden reicht das aus, ohne das
    # Protokoll zu fluten.
    if jetzt - _LETZTER_ABRUF.get(ip, 0) < 30:
        return
    _LETZTER_ABRUF[ip] = jetzt
    # Die Kennung des Aufrufers kann sehr lang sein. Statt sie abzuschneiden
    # — was zu Zeilen wie "Mozilla/5.0 (Windows NT 10.0; Win64; x64" fuehrt —
    # wird der Aufrufer benannt.
    k = kennung.lower()
    if "python" in k or "urllib" in k:
        name = "eCharge@Home"
    elif any(b in k for b in ("mozilla", "chrome", "safari", "firefox", "edg")):
        name = "Browser"
    elif "curl" in k or "wget" in k:
        name = "Kommandozeile"
    elif kennung:
        name = kennung.split("/")[0][:24]
    else:
        name = "Unbekannte Anwendung"
    protokoll("API", f"{name} hat Ladevorgänge abgerufen ({ip})")


class Schnittstelle(BaseHTTPRequestHandler):
    """Stellt die gesammelten Daten bereit.

    Bewusst ohne Anmeldung: Der Dienst hört nur im lokalen Netz, und die
    Daten enthalten nichts Schützenswertes über die Ladezeiten hinaus. Eine
    Passwortabfrage würde die Einrichtung erschweren, ohne etwas zu gewinnen.
    """

    server_version = "eChargeOCPP/2.0"

    def log_message(self, *args) -> None:
        pass   # Der eigene Zugriffsprotokollant bleibt still — gemeldet wird
               # unten gezielt, wenn eine Anwendung Daten abholt.

    def _sende(self, daten: dict, status: int = 200) -> None:
        inhalt = json.dumps(daten, ensure_ascii=False, default=str).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(inhalt)))
        # Zugriff aus dem Browser erlauben, damit auch Weboberflächen
        # anderer Anwendungen die Daten lesen können
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(inhalt)

    def do_OPTIONS(self) -> None:
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_GET(self) -> None:
        teile = urllib.parse.urlparse(self.path)
        pfad = teile.path.rstrip("/") or "/"
        frage = urllib.parse.parse_qs(teile.query)

        if pfad in ("/", "/api"):
            self._sende({
                "dienst": "eCharge@Home OCPP",
                "version": "2.0.0",
                "endpunkte": ["/api/status", "/api/sessions", "/api/log"],
                "ocpp_port": OCPP_PORT,
                "http_port": HTTP_PORT,
            })

        elif pfad == "/api/status":
            k = konfiguration_lesen()
            self._sende({
                "ocpp": ZUSTAND,
                "ocpp_port": OCPP_PORT,
                "http_port": HTTP_PORT,
                # Eigene Uhrzeit mitliefern: Der Browser kann sonst nicht
                # verlässlich rechnen — "2026-08-24 20:34:31" ohne Zeitzone
                # deutet er je nach Hersteller als Ortszeit oder als UTC.
                # Bei zwei Stunden Versatz stand dort "zuletzt 18:38".
                "jetzt": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "mqtt_aktiv": bool(k.get("mqtt_aktiv")),
                "mqtt_verbunden": bool(MQTT_ZUSTAND.get("verbunden")),
                "mqtt_broker": MQTT_ZUSTAND.get("broker", ""),
                "sessions_gesamt": _zeilen("SELECT COUNT(*) AS n FROM charging_sessions")[0]["n"],
                "kwh_gesamt": round(_zeilen(
                    "SELECT COALESCE(SUM(energy_kwh), 0) AS s FROM charging_sessions")[0]["s"], 2),
                "letzte_meldungen": _zeilen(
                    "SELECT timestamp, source, level, message FROM system_logs "
                    "ORDER BY id DESC LIMIT 20"),
            })

        elif pfad == "/api/sessions":
            # Abrufe melden: Der Anwender soll im Terminal sehen, dass sich
            # seine Abrechnung verbunden hat — sonst bleibt unklar, ob die
            # Anbindung wirklich funktioniert.
            _melde_abruf(self.client_address[0], self.headers.get("User-Agent", ""))
            # 'seit' erlaubt es einer Anwendung, nur Neues abzuholen. Ohne
            # Angabe kommt alles — das ist der einfachere Weg für den Anfang.
            seit = (frage.get("seit") or [None])[0]
            grenze = int((frage.get("limit") or ["500"])[0])
            if seit:
                daten = _zeilen(
                    "SELECT * FROM charging_sessions WHERE start_time >= ? "
                    "ORDER BY start_time DESC LIMIT ?", (seit, grenze))
            else:
                daten = _zeilen(
                    "SELECT * FROM charging_sessions ORDER BY start_time DESC LIMIT ?",
                    (grenze,))
            self._sende({"sessions": daten, "anzahl": len(daten)})

        elif pfad == "/api/config":
            k = konfiguration_lesen()
            k["ocpp_port_aktiv"] = OCPP_PORT
            k["http_port_aktiv"] = HTTP_PORT
            self._sende(k)

        elif pfad == "/api/log":
            self._sende({"logs": _zeilen(
                "SELECT timestamp, source, level, message FROM system_logs "
                "ORDER BY id DESC LIMIT 200")})

        else:
            self._sende({"fehler": "Unbekannter Pfad",
                         "bekannt": ["/api/status", "/api/sessions", "/api/log"]}, 404)

    def do_POST(self) -> None:
        teile = urllib.parse.urlparse(self.path)
        laenge = int(self.headers.get("Content-Length", 0) or 0)
        try:
            daten = json.loads(self.rfile.read(laenge).decode("utf-8")) if laenge else {}
        except ValueError:
            self._sende({"ok": False, "fehler": "Ungültiges JSON"}, 400)
            return

        if teile.path.rstrip("/") == "/api/simulation":
            # Erzeugt Beispiel-Ladevorgänge zum Ausprobieren — vor allem, um
            # die MQTT-Ausgabe zu prüfen, ohne ein Fahrzeug anstecken zu
            # müssen. Alle Einträge tragen die Kennung SIM-* und lassen sich
            # darüber vollständig wieder entfernen.
            anzahl = int(daten.get("anzahl", 3) or 3)
            anzahl = max(1, min(anzahl, 20))
            erzeugt = _simulation_anlegen(anzahl, VEROEFFENTLICHER)
            self._sende({"ok": True, "erzeugt": erzeugt,
                         "hinweis": "Testdaten mit Ladepunkt SIM-1"})
            return

        if teile.path.rstrip("/") == "/api/systemlast":
            # Misst, wer auf diesem Geraet Rechenzeit belegt. Die haeufigste
            # Rueckfrage lautet, ob das Plugin den LoxBerry auslastet —
            # gemessen laesst sich das in einer Minute klaeren.
            import subprocess as _sp
            skript = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                  "lastpruefung.sh")
            try:
                erg = _sp.run(["bash", skript], capture_output=True,
                              text=True, timeout=75)
                ausgabe = (erg.stdout or "") + (erg.stderr or "")
            except Exception as e:
                ausgabe = f"Messung fehlgeschlagen: {e}"
            protokoll("System", "Systemlast gemessen")
            self._sende({"ok": True, "ausgabe": ausgabe})
            return

        if teile.path.rstrip("/") == "/api/protokoll/loeschen":
            conn = sqlite3.connect(DB_PFAD, timeout=5)
            try:
                anzahl = conn.execute("DELETE FROM system_logs").rowcount
                conn.commit()
            finally:
                conn.close()
            # Die Logdatei ebenfalls leeren — sonst tauchen die alten Zeilen
            # beim naechsten Aufruf der Oberflaeche wieder auf.
            try:
                open(LOG_PFAD, "w", encoding="utf-8").close()
            except OSError:
                pass
            protokoll("System", "Protokoll geleert")
            self._sende({"ok": True, "geloescht": anzahl})
            return

        if teile.path.rstrip("/") == "/api/ladungen/loeschen":
            conn = sqlite3.connect(DB_PFAD, timeout=5)
            try:
                anzahl = conn.execute("DELETE FROM charging_sessions").rowcount
                conn.commit()
                conn.execute("VACUUM")
            finally:
                conn.close()
            protokoll("System", f"Alle {anzahl} Ladevorgänge gelöscht")
            self._sende({"ok": True, "geloescht": anzahl})
            return

        if teile.path.rstrip("/") == "/api/simulation/loeschen":
            conn = sqlite3.connect(DB_PFAD, timeout=5)
            try:
                cur = conn.execute(
                    "DELETE FROM charging_sessions WHERE meter_id LIKE 'SIM-%'")
                anzahl = cur.rowcount
                conn.commit()
            finally:
                conn.close()
            protokoll("System", f"{anzahl} Testladungen entfernt")
            self._sende({"ok": True, "geloescht": anzahl})
            return

        if teile.path.rstrip("/") == "/api/config":
            # Die Weboberflaeche schreibt unmittelbar in konfig.json; dieser
            # Weg bleibt fuer andere Anwendungen bestehen.
            k = konfiguration_lesen()
            for schluessel, wandler in (("price_per_kwh", float),
                                        ("ocpp_port", int), ("http_port", int)):
                if daten.get(schluessel) not in (None, ""):
                    try:
                        k[schluessel] = wandler(daten[schluessel])
                    except (TypeError, ValueError):
                        pass
            try:
                with open(KONFIG_PFAD, "w", encoding="utf-8") as f:
                    json.dump(k, f, indent=2)
            except OSError as e:
                self._sende({"ok": False, "fehler": f"Nicht schreibbar: {e}"}, 500)
                return
            protokoll("Konfig", "Einstellungen geändert")
            self._sende({"ok": True, **k})

        else:
            self._sende({"fehler": "Unbekannter Pfad"}, 404)


# ── Start ──────────────────────────────────────────────────────────────────

PID_PFAD = os.path.join(DATENVERZEICHNIS, "dienst.pid")


def _dienst_antwortet(port: int) -> bool:
    """Prüft, ob auf dem Port bereits ein eCharge@Home-Dienst lauscht.

    Nicht irgendein Programm — nur der eigene Dienst zählt. Ein fremdes
    Programm auf demselben Port ist ein anderer Fall und führt zum
    Ausweichen, nicht zum Abbruch."""
    try:
        import urllib.request
        with urllib.request.urlopen(
                f"http://127.0.0.1:{port}/api/status", timeout=2) as r:
            return b"ocpp" in r.read()[:200].lower()
    except Exception:
        return False


def _alte_instanz_beenden() -> None:
    """Beendet eine noch laufende Instanz.

    Das Startskript kann das nicht zuverlaessig: Die Weboberflaeche laeuft
    als Webserver-Benutzer, der Dienst als loxberry — pkill scheitert dann
    lautlos an fehlenden Rechten. Der neue Prozess kennt dagegen die PID
    aus der Datei und kann gezielt beenden; scheitert auch das, weicht er
    nicht auf einen anderen Port aus, sondern legt sich schlafen und
    ueberlaesst der alten Instanz das Feld."""
    try:
        with open(PID_PFAD) as f:
            alte = int(f.read().strip())
    except (OSError, ValueError):
        return
    if alte == os.getpid():
        return
    try:
        os.kill(alte, 0)              # existiert der Prozess?
    except OSError:
        return                        # nein — verwaiste Datei
    protokoll("System", f"Beende vorherige Instanz (PID {alte})")
    try:
        os.kill(alte, signal.SIGTERM)
        for _ in range(15):
            time.sleep(0.4)
            try:
                os.kill(alte, 0)
            except OSError:
                return                # beendet
        os.kill(alte, signal.SIGKILL)
        time.sleep(1)
    except OSError as e:
        protokoll("System", f"Vorherige Instanz nicht beendbar: {e}", "WARN")


# Der laufende Veröffentlichter — damit die Simulation auch über MQTT geht.
VEROEFFENTLICHER = None


def _simulation_anlegen(anzahl: int, mqtt=None) -> int:
    """Legt Beispiel-Ladevorgänge an und meldet sie über MQTT.

    Die Werte sind realistisch gewählt: 11 kW Wechselstrom, 2 bis 6 Stunden
    Ladedauer, Zählerstände fortlaufend. So lässt sich prüfen, ob Anzeige,
    Abrechnung und MQTT-Ausgabe zusammenspielen, ohne ein Fahrzeug
    anzustecken."""
    import random
    conn = sqlite3.connect(DB_PFAD, timeout=5)
    try:
        zeile = conn.execute(
            "SELECT MAX(meter_stop) FROM charging_sessions").fetchone()
        zaehler = int(zeile[0] or 800000)
        preis = float(konfiguration_lesen().get("price_per_kwh", 0.28))

        erzeugt = 0
        for i in range(anzahl):
            tage_zurueck = anzahl - i
            beginn = datetime.now() - timedelta(days=tage_zurueck,
                                                hours=random.randint(0, 4))
            dauer_min = random.randint(120, 380)
            ende = beginn + timedelta(minutes=dauer_min)
            kwh = round(dauer_min / 60 * random.uniform(9.5, 11.2), 2)
            start_wh = zaehler
            ende_wh = zaehler + int(kwh * 1000)
            zaehler = ende_wh

            conn.execute(
                "INSERT INTO charging_sessions (source, meter_id, start_time, "
                "end_time, meter_start, meter_stop, energy_kwh, price_per_kwh, "
                "cost_eur) VALUES ('ocpp','SIM-1',?,?,?,?,?,?,?)",
                (beginn.strftime("%Y-%m-%d %H:%M:%S"),
                 ende.strftime("%Y-%m-%d %H:%M:%S"),
                 start_wh, ende_wh, kwh, preis, round(kwh * preis, 2)))
            erzeugt += 1

            if mqtt:
                mqtt.sende_ladung("SIM-1", {
                    "energie_kwh": kwh,
                    "zaehler_start_wh": start_wh,
                    "zaehler_ende_wh": ende_wh,
                    "dauer_min": dauer_min,
                    "kosten_eur": round(kwh * preis, 2),
                    "beginn": beginn.isoformat(timespec="seconds"),
                    "ende": ende.isoformat(timespec="seconds"),
                })
        conn.commit()
    finally:
        conn.close()

    if mqtt:
        # Auch Live-Werte senden, damit sich die MQTT-Anbindung vollständig
        # prüfen lässt — nicht nur abgeschlossene Ladungen.
        mqtt.sende_zustand("SIM-1", {
            "online": True, "laedt": True, "leistung_kw": 11.02,
            "zaehler_wh": zaehler, "strom_a_l1": 16.0, "spannung_v": 231,
            "ladestand_prozent": 72, "hersteller": "Simulation",
            "modell": "Testwallbox", "benutzer": "SIM-RFID"})

    protokoll("System", f"{anzahl} Testladungen angelegt"
                        + (" und über MQTT gemeldet" if mqtt else ""))
    return erzeugt


def main() -> None:
    global OCPP_PORT, HTTP_PORT

    datenbank_anlegen()
    _alte_instanz_beenden()
    try:
        with open(PID_PFAD, "w") as f:
            f.write(str(os.getpid()))
    except OSError:
        pass
    protokoll("System", "=" * 52)
    protokoll("System", "eCharge@Home OCPP 2.9.3 startet")
    protokoll("System", f"Daten: {DB_PFAD}")

    kfg = konfiguration_lesen()
    wunsch_ocpp = int(kfg["ocpp_port"])
    wunsch_http = int(kfg["http_port"])

    # Automatisch den nächsten freien Port suchen — nur wenn der Anwender
    # keinen explizit gespeichert hat, also noch auf dem Standard steht.
    def suche_port(wunsch, von=1024, bis=65535):
        import socket as _s
        for p in range(wunsch, min(wunsch + 20, bis + 1)):
            t = _s.socket()
            t.setsockopt(_s.SOL_SOCKET, _s.SO_REUSEADDR, 1)
            try:
                t.bind(("0.0.0.0", p)); t.close(); return p
            except OSError:
                t.close()
        return wunsch  # als Fallback, Fehlermeldung kommt beim Binden

    # Läuft bereits eine Instanz? Der Wunschport ist die zuverlässigste
    # Antwort darauf — belegt ihn ein anderer eCharge@Home-Dienst, wäre ein
    # zweiter überflüssig. Früher wich er auf den nächsten Port aus; dabei
    # entstanden zwei Dienste, wechselnde Adressen und ein Protokoll voller
    # Startmeldungen.
    if _dienst_antwortet(wunsch_http):
        protokoll("System", f"Es läuft bereits ein Dienst auf Port {wunsch_http}. "
                            f"Dieser Start wird abgebrochen.", "WARN")
        sys.exit(0)

    OCPP_PORT = suche_port(wunsch_ocpp)
    HTTP_PORT = suche_port(wunsch_http)
    if OCPP_PORT != wunsch_ocpp:
        protokoll("System", f"Port {wunsch_ocpp} ist von einem fremden Programm "
                            f"belegt — OCPP nutzt {OCPP_PORT}. Diese Adresse in "
                            f"der Wallbox eintragen!", "WARN")
    if HTTP_PORT != wunsch_http:
        protokoll("System", f"Port {wunsch_http} ist belegt — Schnittstelle nutzt "
                            f"{HTTP_PORT}", "WARN")
    ZUSTAND["ocpp_port"] = OCPP_PORT
    ZUSTAND["http_port"] = HTTP_PORT

    # MQTT-Ausgabe vorbereiten. Sie ist freiwillig — ohne sie laeuft alles
    # unveraendert weiter.
    veroeffentlicher = None
    try:
        import mqtt_ausgabe
        veroeffentlicher = mqtt_ausgabe.Veroeffentlicher(konfiguration_lesen(), protokoll)
        veroeffentlicher.starte()
        MQTT_ZUSTAND["verbunden"] = bool(
            veroeffentlicher.verbindung and veroeffentlicher.verbindung.verbunden)
        if veroeffentlicher.verbindung:
            MQTT_ZUSTAND["broker"] = (f"{veroeffentlicher.verbindung.host}:"
                                      f"{veroeffentlicher.verbindung.port}")
    except Exception as e:
        protokoll("MQTT", f"Ausgabe nicht verfügbar: {type(e).__name__}", "WARN")

    global VEROEFFENTLICHER
    VEROEFFENTLICHER = veroeffentlicher

    threading.Thread(
        target=ocpp_server.starte_server,
        args=(DB_PFAD, protokoll, ZUSTAND, OCPP_PORT, veroeffentlicher),
        daemon=True).start()

    time.sleep(0.5)

    try:
        server = ThreadingHTTPServer(("0.0.0.0", HTTP_PORT), Schnittstelle)
    except OSError as e:
        # Sagen, was zu tun ist, statt nur zu scheitern.
        protokoll("System", f"Port {HTTP_PORT} ist belegt ({e}). "
                            f"Bitte in den Einstellungen einen anderen wählen.",
                  "CRITICAL")
        sys.exit(1)

    # Den tatsaechlich genutzten Port hinterlegen: Die Weboberflaeche liest
    # ihn dort, statt 8042 anzunehmen und ins Leere zu greifen.
    try:
        with open(os.path.join(DATENVERZEICHNIS, "ports.json"), "w") as f:
            json.dump({"ocpp": OCPP_PORT, "http": HTTP_PORT,
                       "wunsch_ocpp": wunsch_ocpp, "wunsch_http": wunsch_http}, f)
    except OSError:
        pass

    protokoll("System", f"Schnittstelle bereit auf Port {HTTP_PORT}")
    # Die eigene Adresse steht dem Dienst nicht verlaesslich zur Verfuegung
    # (mehrere Netzwerkkarten, Docker-Bruecken). Die Weboberflaeche kennt sie
    # dagegen aus dem Browseraufruf und setzt sie dort ein.
    protokoll("System", f"OCPP-Port {OCPP_PORT} · Schnittstelle {HTTP_PORT} — "
                        f"Adresse für die Wallbox steht in der Weboberfläche")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        protokoll("System", "Beendet")


if __name__ == "__main__":
    main()
