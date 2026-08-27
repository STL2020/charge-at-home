#!/usr/bin/env python3
"""
CarData-Stream-Diagnose — eigenständig, unabhängig von der laufenden App.

WARUM EIN EIGENES SKRIPT
-------------------------
Die App-Oberfläche zeigt nur einen verdichteten Status ("Verbunden" /
"Nicht verbunden" / Fehlertext). Das reicht nicht, um zwischen den drei
möglichen Ursachen zu unterscheiden, wenn keine Daten ankommen:

  1. Der Login-Token trägt nicht die Streaming-Berechtigung
     (cardata:streaming:read) — meist, weil die Anmeldung vor Einführung
     des Streams erfolgte und beim Erneuern keine neuen Berechtigungen
     hinzukommen.
  2. Die Verbindung steht gar nicht erst (Netzwerk, Token, Zugangsdaten).
  3. Die Verbindung steht, aber es kommt nichts — dann liegt es an BMW
     selbst (Datenpunkte im Portal nicht ausgewählt, Fahrzeug meldet
     nichts, o.ä.), nicht mehr an dieser Anwendung.

Dieses Skript meldet sich UNABHÄNGIG vom App-Login neu an (eigene Tokens,
eigene Ablage unter /srv/data/mqtt_diagnose_tokens.json — rührt die
laufende Anmeldung der App nicht an), fordert dabei ausdrücklich BEIDE
Berechtigungen an (API + Streaming) und zeigt danach jede Stufe des
MQTT-Verbindungsaufbaus einzeln: Verbindung, Anmeldung (Connect-Code),
Abonnement (Subscribe-Code je Thema), jede eingehende Nachricht im
Klartext, jede Trennung mit Grund.

WICHTIG BEIM AUSFÜHREN
------------------------
BMW erlaubt nur eine gleichzeitige Verbindung je Konto (GCID). Läuft der
Datenstrom der App parallel, kann das gegenseitige Verbindungsabbrüche
verursachen und das Bild verfälschen. Vor dem Test also unter
Einstellungen → BMW den Haken bei "Datenstrom nutzen" entfernen, danach
dieses Skript laufen lassen.

AUSFÜHRUNG (auf der Synology)
-------------------------------
    sudo docker cp app/mqtt_diagnose.py echarge:/srv/app/mqtt_diagnose.py
    sudo docker exec -it echarge python3 mqtt_diagnose.py

Mit Strg+C jederzeit sauber beendbar. Das vollständige Protokoll landet
zusätzlich als Datei unter /srv/data/ — bleibt also auch nach dem
Beenden erhalten und ist über die Synology-Freigabe einsehbar, ohne
erneut in den Container zu müssen.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime

BASIS_URL = "https://customer.bmwgroup.com"
DEVICE_CODE_URL = f"{BASIS_URL}/gcdm/oauth/device/code"
TOKEN_URL = f"{BASIS_URL}/gcdm/oauth/token"

MQTT_HOST = os.environ.get("CARDATA_MQTT_HOST", "customer.streaming-cardata.bmwgroup.com")
MQTT_PORT = int(os.environ.get("CARDATA_MQTT_PORT", "9000"))

SCOPE = "authenticate_user openid cardata:api:read cardata:streaming:read"

# Eigene Ablage, getrennt von der App — nichts hiervon beeinflusst die
# gespeicherte Anmeldung der laufenden Anwendung.
_DATENVERZEICHNIS = "/srv/data" if os.path.isdir("/srv/data") else "."
TOKEN_DATEI = os.path.join(_DATENVERZEICHNIS, "mqtt_diagnose_tokens.json")
LOG_DATEI = os.path.join(
    _DATENVERZEICHNIS,
    f"mqtt_diagnose_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log")

_log_datei_handle = open(LOG_DATEI, "a", encoding="utf-8")


def log(zeile: str = "") -> None:
    """Schreibt gleichzeitig auf den Bildschirm und ins Protokoll."""
    zeitstempel = datetime.now().strftime("%H:%M:%S")
    text = f"[{zeitstempel}] {zeile}" if zeile else ""
    print(text, flush=True)
    _log_datei_handle.write(text + "\n")
    _log_datei_handle.flush()


# ── PKCE ─────────────────────────────────────────────────────────────────

def _erzeuge_pkce() -> tuple[str, str]:
    verifier = base64.urlsafe_b64encode(secrets.token_bytes(64)).decode().rstrip("=")
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).decode().rstrip("=")
    return verifier, challenge


def _post_form(url: str, daten: dict) -> dict:
    body = urllib.parse.urlencode(daten).encode("utf-8")
    req = urllib.request.Request(url, data=body, headers={
        "User-Agent": "eCharge-at-Home-Diagnose/1.0 (+https://www.loewemann.com)",
        "Content-Type": "application/x-www-form-urlencoded",
        "Accept": "application/json",
    })
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return {"ok": True, "daten": json.loads(resp.read().decode("utf-8"))}
    except urllib.error.HTTPError as e:
        try:
            fehler = json.loads(e.read().decode("utf-8"))
        except Exception:
            fehler = {}
        return {"ok": False, "status": e.code, "fehler": fehler}
    except Exception as e:
        return {"ok": False, "status": None,
                "fehler": {"error": f"{type(e).__name__}: {e}"}}


# ── Anmeldung (Device Code Flow, eigenständig) ────────────────────────────

def _neue_anmeldung(client_id: str) -> dict:
    verifier, challenge = _erzeuge_pkce()
    antwort = _post_form(DEVICE_CODE_URL, {
        "client_id": client_id,
        "response_type": "device_code",
        "scope": SCOPE,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    })
    if not antwort["ok"]:
        log(f"✕ Geräteanmeldung abgelehnt: {antwort['fehler']}")
        sys.exit(1)

    d = antwort["daten"]
    log("")
    log("=" * 68)
    log("BROWSER-BESTÄTIGUNG NÖTIG")
    log("=" * 68)
    log(f"  URL:  {d.get('verification_uri_complete') or d.get('verification_uri')}")
    log(f"  Code: {d.get('user_code')}")
    log("Im Browser öffnen und bestätigen — dieses Skript wartet von selbst.")
    log("=" * 68)
    log("")

    intervall = int(d.get("interval", 5))
    ablauf = time.time() + int(d.get("expires_in", 600))
    device_code = d.get("device_code", "")

    while time.time() < ablauf:
        time.sleep(intervall)
        antwort = _post_form(TOKEN_URL, {
            "client_id": client_id,
            "device_code": device_code,
            "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
            "code_verifier": verifier,
        })
        if antwort["ok"]:
            tokens = antwort["daten"]
            tokens["_client_id"] = client_id
            tokens["_geholt_um"] = time.time()
            with open(TOKEN_DATEI, "w", encoding="utf-8") as f:
                json.dump(tokens, f)
            return tokens
        code = (antwort.get("fehler", {}).get("error") or "").lower()
        if code == "authorization_pending":
            log("… noch keine Bestätigung im Browser, warte weiter.")
            continue
        if code == "slow_down":
            intervall += 5
            continue
        log(f"✕ Anmeldung fehlgeschlagen: {antwort['fehler']}")
        sys.exit(1)

    log("✕ Anmeldecode abgelaufen, ohne bestätigt zu werden. Bitte neu starten.")
    sys.exit(1)


def _hole_tokens(client_id: str, erzwinge_neuanmeldung: bool) -> dict:
    if not erzwinge_neuanmeldung and os.path.exists(TOKEN_DATEI):
        try:
            with open(TOKEN_DATEI, encoding="utf-8") as f:
                tokens = json.load(f)
            alter_s = time.time() - tokens.get("_geholt_um", 0)
            if alter_s < int(tokens.get("expires_in", 3600)) - 120:
                log(f"Vorhandener Diagnose-Token wiederverwendet "
                    f"(noch {int((int(tokens.get('expires_in', 3600)) - alter_s) / 60)} "
                    f"Min. gültig). Für eine frische Anmeldung: "
                    f"'--neu' anhängen.")
                return tokens
        except Exception:
            pass
    return _neue_anmeldung(client_id)


# ── MQTT-Verbindung mit vollem Protokoll ──────────────────────────────────

def _verbinde_und_lausche(gcid: str, id_token: str, vin: str) -> None:
    try:
        import paho.mqtt.client as mqtt
    except ImportError:
        log("✕ paho-mqtt fehlt. Im Docker-Abbild eigentlich enthalten — "
            "läuft dieses Skript im selben Container wie die App?")
        sys.exit(1)

    thema = f"{gcid}/{vin}"
    zaehler = {"nachrichten": 0}

    client = mqtt.Client(
        mqtt.CallbackAPIVersion.VERSION2,
        client_id=f"echarge-diagnose-{vin[-6:]}-{secrets.token_hex(3)}",
        protocol=mqtt.MQTTv5,
    )
    client.username_pw_set(gcid, id_token)
    client.tls_set()
    client.enable_logger()   # gibt zusätzlich die rohen paho-internen Frames aus

    def bei_verbindung(c, userdata, flags, rc, props=None):
        # rc ist bei MQTTv5 ein ReasonCode-Objekt, bei v3 eine Zahl — beides
        # als Text ausgeben, damit nichts verschluckt wird.
        log(f"CONNECT-Antwort: {rc} ({'ERFOLG' if int(rc) == 0 else 'FEHLGESCHLAGEN'})")
        if int(rc) != 0:
            log("  → Häufigste Ursachen bei Fehlschlag: Token abgelaufen/"
                "ungültig, GCID falsch, oder Streaming-Scope fehlt im Token.")
            return
        log(f"Abonniere Thema: {thema}")
        ergebnis = c.subscribe(thema, qos=1)
        log(f"  SUBSCRIBE gesendet, mid={ergebnis[1]}")

    def bei_abonnement(c, userdata, mid, reason_codes, properties=None):
        log(f"SUBACK erhalten (mid={mid}): {reason_codes}")
        for code in reason_codes:
            wert = getattr(code, "value", code)
            if wert is not None and wert >= 128:
                log(f"  ✕ Abonnement abgelehnt (Code {wert}). Das Thema "
                    f"'{thema}' wurde von BMW nicht akzeptiert — meist "
                    f"falsche GCID/VIN-Kombination oder keine Freigabe für "
                    f"dieses Fahrzeug.")
            else:
                log(f"  ✓ Abonnement bestätigt (QoS {wert}).")

    def bei_trennung(c, userdata, flags, rc, props=None):
        log(f"Verbindung getrennt: {rc}")

    def bei_nachricht(c, userdata, msg):
        zaehler["nachrichten"] += 1
        log("")
        log(f"★ NACHRICHT #{zaehler['nachrichten']} auf '{msg.topic}':")
        try:
            hübsch = json.dumps(json.loads(msg.payload.decode("utf-8")),
                                ensure_ascii=False, indent=2)
        except Exception:
            hübsch = repr(msg.payload)
        log(hübsch)
        log("")

    def bei_log(c, userdata, level, buf):
        # Paho-interne Zeile — sehr detailliert (TLS-Handshake, PINGREQ/RESP,
        # rohe Paket-Bytes). Standardmaessig unterdrueckt, bei Bedarf die
        # folgende Zeile einkommentieren.
        # log(f"  (paho) {buf}")
        pass

    client.on_connect = bei_verbindung
    client.on_subscribe = bei_abonnement
    client.on_disconnect = bei_trennung
    client.on_message = bei_nachricht
    client.on_log = bei_log

    log(f"Verbinde zu {MQTT_HOST}:{MQTT_PORT} als Benutzer {gcid} …")
    client.connect(MQTT_HOST, MQTT_PORT, keepalive=60)
    client.loop_start()

    log("Warte auf Nachrichten. Jetzt fahren (mindestens gut 1 km, GPS-Rauschen "
        "unter 1 km zaehlt nicht als Fahrt) oder das Fahrzeug ver-/entriegeln, "
        "um eine Aenderung auszuloesen. Strg+C zum Beenden.")
    log(f"Vollständiges Protokoll auch in: {LOG_DATEI}")

    letzter_heartbeat = time.time()
    try:
        while True:
            time.sleep(1)
            if time.time() - letzter_heartbeat > 30:
                letzter_heartbeat = time.time()
                log(f"… weiterhin aktiv, bisher {zaehler['nachrichten']} "
                    f"Nachricht(en) empfangen.")
    except KeyboardInterrupt:
        log("Beende auf Wunsch (Strg+C) …")
    finally:
        client.loop_stop()
        try:
            client.disconnect()
        except Exception:
            pass
        log(f"Insgesamt {zaehler['nachrichten']} Nachricht(en) empfangen "
            f"in dieser Sitzung.")


def main() -> None:
    neu = "--neu" in sys.argv

    client_id = os.environ.get("CARDATA_CLIENT_ID", "")
    vin = os.environ.get("CARDATA_VIN", "")

    if not (client_id and vin):
        # Bequemlichkeit: aus der App-Datenbank lesen, falls vorhanden — nur
        # lesend, es wird nichts in der App-DB veraendert. Host/Port werden
        # unabhaengig von Client-ID/VIN immer mitgelesen, falls dort hinterlegt.
        try:
            import sqlite3
            db_pfad = os.environ.get("CHARGE_DB_PATH", "/srv/data/charging.db")
            if os.path.exists(db_pfad):
                conn = sqlite3.connect(db_pfad)
                for schluessel, ziel in (("cardata_client_id", "client_id"),
                                         ("cardata_vin", "vin"),
                                         ("cardata_stream_host", "host"),
                                         ("cardata_stream_port", "port")):
                    row = conn.execute(
                        "SELECT value FROM app_settings WHERE key = ?", (schluessel,)
                    ).fetchone()
                    if row and row[0]:
                        if ziel == "client_id" and not client_id:
                            client_id = row[0]
                        if ziel == "vin" and not vin:
                            vin = row[0]
                        if ziel == "host" and "CARDATA_MQTT_HOST" not in os.environ:
                            globals()["MQTT_HOST"] = row[0]
                        if ziel == "port" and "CARDATA_MQTT_PORT" not in os.environ:
                            try:
                                globals()["MQTT_PORT"] = int(row[0])
                            except ValueError:
                                pass
                conn.close()
        except Exception:
            pass

    if not client_id:
        client_id = input("BMW CarData Client-ID: ").strip()
    if not vin:
        vin = input("Fahrgestellnummer (VIN): ").strip().upper()

    log(f"Protokolldatei: {LOG_DATEI}")
    log(f"Client-ID: {client_id}   VIN: {vin}")
    log(f"Host: {MQTT_HOST}   Port: {MQTT_PORT}")
    log(f"Angeforderte Scopes: {SCOPE}")

    tokens = _hole_tokens(client_id, erzwinge_neuanmeldung=neu)

    gcid = tokens.get("gcid", "")
    id_token = tokens.get("id_token", "")
    gewaehrter_scope = tokens.get("scope", "(von BMW nicht mitgeteilt)")

    log("")
    log(f"Angemeldet. GCID: {gcid}")
    log(f"Von BMW bestätigter Scope: {gewaehrter_scope}")
    if "streaming" not in str(gewaehrter_scope).lower():
        log("⚠ ACHTUNG: 'cardata:streaming:read' taucht in der Bestätigung "
            "nicht auf — falls BMW den Scope tatsächlich nicht mitliefert "
            "(manche Anbieter tun das grundsätzlich nicht), ist das kein "
            "sicheres Warnzeichen. Steht dagegen ein anderer, sichtbar "
            "eingeschränkter Scope da, ist im Portal die Freigabe für "
            "'CarData Stream' zu prüfen.")
    log("")

    if not gcid or not id_token:
        log("✕ Keine gültigen Zugangsdaten erhalten — kann nicht verbinden.")
        sys.exit(1)

    _verbinde_und_lausche(gcid, id_token, vin)


if __name__ == "__main__":
    try:
        main()
    finally:
        _log_datei_handle.close()
