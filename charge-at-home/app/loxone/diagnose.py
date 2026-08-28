"""
Loxone-Verbindungs-Diagnose — Schritt-für-Schritt-Test gegen einen echten Miniserver.

Zweck: Anders als der eingebaute "Verbindung testen"-Button, der nur Erfolg/
Misserfolg zeigt, gibt dieses Skript bei JEDEM einzelnen Schritt die volle
rohe HTTP-Antwort aus. Damit lässt sich genau sehen, an welcher Stelle und
warum der Handshake scheitert — Grundlage, um den Code in
services/loxone_api_service.py gezielt zu korrigieren, statt zu raten.

Nutzung (im Projektordner, z. B. via Claude Code mit echtem Netzwerkzugriff):
    python app/loxone/diagnose.py <IP-Adresse> <Benutzername> <Passwort>

Beispiel:
    python app/loxone/diagnose.py 192.168.1.60 admin geheim123
"""

import base64
import base64
import hashlib
import hmac
import json
import sys
import urllib.error
import urllib.parse
import urllib.request

TIMEOUT = 8
USER_AGENT = "ChargeAtHomeBillingEngine-Diagnose/1.0"


def step(title: str):
    print()
    print("=" * 70)
    print(f"SCHRITT: {title}")
    print("=" * 70)


def raw_get(url: str, extra_headers: dict | None = None):
    """Fuehrt ein GET aus und gibt (status_code, body_text, headers) zurueck,
    auch bei Fehlerstatus (kein Exception-Abbruch), damit man die Antwort sieht."""
    headers = {"User-Agent": USER_AGENT}
    if extra_headers:
        headers.update(extra_headers)
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            body = resp.read().decode("utf-8", errors="replace")
            return resp.status, body, dict(resp.headers)
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        return e.code, body, dict(e.headers or {})
    except Exception as e:
        return None, f"AUSNAHME: {type(e).__name__}: {e}", {}


def print_result(status, body):
    print(f"HTTP-Status: {status}")
    print(f"Rohantwort (erste 500 Zeichen):")
    print(body[:500])
    try:
        parsed = json.loads(body)
        print("Als JSON geparst:", json.dumps(parsed, indent=2, ensure_ascii=False)[:800])
        return parsed
    except (json.JSONDecodeError, TypeError):
        print("(keine gueltige JSON-Antwort — evtl. XML oder HTML, siehe oben)")
        return None


def main():
    if len(sys.argv) not in (4, 5):
        print("Nutzung: python diagnose.py <IP-Adresse> <Benutzername> <Passwort> [Name-oder-UUID]")
        print("Das optionale 4. Argument (Name-oder-UUID) aktiviert Schritt 6, den empfohlenen,")
        print("einfacheren Testweg via HTTP-Basic-Auth (siehe evcc-Diskussion).")
        sys.exit(1)

    host, username, password = sys.argv[1], sys.argv[2], sys.argv[3]
    print(f"Teste Miniserver {host} mit Benutzer '{username}' ...")

    # --- Schritt 0: Erreichbarkeit + Firmware-Version ---
    step("0: Firmware-Version (jdev/cfg/api)")
    status, body, _ = raw_get(f"http://{host}/jdev/cfg/api")
    print_result(status, body)

    # --- Schritt 0b (EMPFOHLEN, siehe evcc-Diskussion #3260): Direktes Auslesen
    # via /dev/sps/io/{Name}, HTTP-Basic-Auth, KEIN Token/RSA-Handshake noetig.
    # Bewusst ganz vorne, unabhaengig von den folgenden komplexeren Schritten —
    # falls das hier klappt, ist der Rest (Schritte 1-4) gar nicht mehr noetig.
    step("0b (EMPFOHLEN): Direktes Auslesen via /dev/sps/io/{Name}, HTTP-Basic-Auth")
    credentials = base64.b64encode(f"{username}:{password}".encode("utf-8")).decode("ascii")
    if len(sys.argv) >= 5:
        control_name = sys.argv[4]
        url = f"http://{host}/dev/sps/io/{urllib.parse.quote(control_name)}"
        print(f"URL: {url} (Zugangsdaten per HTTP-Basic-Auth-Header, nicht in der URL sichtbar)")
        status, body, _ = raw_get(url, {"Authorization": f"Basic {credentials}"})
        print_result(status, body)
        if status == 200 and 'value="' in body:
            print()
            print(">>> ERFOLG! Dieser einfache Weg funktioniert bereits.")
            print(">>> Die komplexeren Schritte 1-4 unten sind dann nicht mehr noetig.")
            print(">>> Das ist der empfohlene Weg fuer eCharge@Home")
            print(">>> (services/loxone_api_service.py, get_value_basic_auth()).")
    else:
        print("(uebersprungen: kein Name/UUID als 4. Argument angegeben)")
        print("Aufruf mit Namen/UUID: python diagnose.py <IP> <User> <Passwort> <Name-oder-UUID>")
        print("Beispiel: der in Loxone Config vergebene Name eines virtuellen Eingangs, z. B. 'vi2'.")

    # --- Schritt 1: getPublicKey (fuer verschluesselte Kommunikation) ---
    step("1: RSA Public Key (jdev/sys/getPublicKey) — nur relevant, falls Schritt 0b nicht funktioniert hat")
    status, body, _ = raw_get(f"http://{host}/jdev/sys/getPublicKey")
    print_result(status, body)
    print(">>> Falls hier ein Schluessel zurueckkommt: Der Miniserver erwartet")
    print(">>> vermutlich verschluesselte Kommunikation (RSA+AES-Huelle), nicht")
    print(">>> nur den einfachen Hash-Handshake. Das ist ein deutlich groesserer")
    print(">>> Umbau, siehe Nachricht dazu.")

    # --- Schritt 2: getkey2 (Salt + Key + hashAlg) ---
    step(f"2: Key/Salt fuer Benutzer '{username}' (jdev/sys/getkey2/{username})")
    status, body, _ = raw_get(f"http://{host}/jdev/sys/getkey2/{urllib.parse.quote(username)}")
    parsed = print_result(status, body)

    if status != 200 or parsed is None:
        print()
        print("!!! Schritt 2 ist bereits fehlgeschlagen — das ist eigentlich der")
        print("!!! unauthentifizierte allererste Schritt. Moegliche Gruende:")
        print("!!!  - Miniserver verlangt HTTPS statt HTTP (Port 443 statt 80?)")
        print("!!!  - Miniserver verlangt HTTP-Basic-Auth bereits fuer diesen Schritt")
        print("!!!  - Falscher Port/URL-Pfad fuer diese Firmware-Version")
        print("!!! Springe zum empfohlenen, unabhaengigen Testweg (Schritt 6) ...")
        salt = key_hex = None
        hash_func = hashlib.sha1
    else:
        key_data = parsed.get("LL", {}).get("value", {})
        salt = key_data.get("salt")
        key_hex = key_data.get("key")
        hash_alg = key_data.get("hashAlg", "SHA1")
        print(f"\n>>> salt={salt}, key={key_hex}, hashAlg={hash_alg}")
        hash_func = hashlib.sha1 if hash_alg == "SHA1" else hashlib.sha256

    if salt and key_hex:
        # --- Schritt 3: gettoken mit BEIDEN Schluessel-Varianten ---
        for variant_name, key_bytes in [
            ("hex-dekodierte Rohbytes", bytes.fromhex(key_hex)),
            ("ASCII-Text der Hex-Zeichenkette", key_hex.encode("ascii")),
        ]:
            step(f"3: gettoken mit Schluessel-Variante '{variant_name}'")
            pw_hash = hmac.new(key_bytes, f"{password}:{salt}".encode("utf-8"), hash_func).hexdigest().upper()
            final_hash = hmac.new(key_bytes, f"{username}:{pw_hash}".encode("utf-8"), hash_func).hexdigest()
            print(f"PwHash:     {pw_hash}")
            print(f"FinalHash:  {final_hash}")
            url = f"http://{host}/jdev/sys/gettoken/{final_hash}/{urllib.parse.quote(username)}/4/1/ChargeAtHomeDiagnose"
            print(f"URL: {url}")
            status, body, _ = raw_get(url)
            print_result(status, body)

        # --- Schritt 4: getjwt als Alternative (neuere Firmware) ---
        step("4: getjwt als Alternative (neuere Miniserver-Versionen)")
        key_bytes = bytes.fromhex(key_hex)
        pw_hash = hmac.new(key_bytes, f"{password}:{salt}".encode("utf-8"), hash_func).hexdigest().upper()
        final_hash = hmac.new(key_bytes, f"{username}:{pw_hash}".encode("utf-8"), hash_func).hexdigest()
        url = f"http://{host}/jdev/sys/getjwt/{final_hash}/{urllib.parse.quote(username)}/4/1/ChargeAtHomeDiagnose"
        print(f"URL: {url}")
        status, body, _ = raw_get(url)
        print_result(status, body)
    else:
        print("(Schritte 3+4 uebersprungen, da Schritt 2 keine Salt/Key-Daten lieferte)")

    # --- Schritt 5: HTTP Basic Auth auf die Struktur-Datei (Vergleichswert) ---
    step("5: HTTP-Basic-Auth auf /data/LoxAPP3.json (Vergleichswert zu Schritt 0b)")
    status, body, _ = raw_get(f"http://{host}/data/LoxAPP3.json", {"Authorization": f"Basic {credentials}"})
    print(f"HTTP-Status: {status}")
    print(f"Antwortlaenge: {len(body)} Zeichen")
    if status == 200:
        print(">>> HTTP-Basic-Auth funktioniert auch fuer die Struktur-Datei.")

    print()
    print("=" * 70)
    print("DIAGNOSE FERTIG. Bitte die komplette Ausgabe oben zurueckmelden.")
    print("=" * 70)


if __name__ == "__main__":
    main()
