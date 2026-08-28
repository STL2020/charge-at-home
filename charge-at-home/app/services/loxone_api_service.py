"""
Direkte Loxone-API — FA-LS-10 (Alternative zu OCPP, § 6.4).

WICHTIGER HINWEIS ZUM TESTSTATUS UND ZUR KORREKTUR:
Die offizielle Loxone-Dokumentation "Communicating with the Miniserver"
(Stand 2025.06.03) weist selbst auf eine Mehrdeutigkeit hin: "the key from
the getkey2-response is hex-encoded and might need to be converted to ASCII
before being used to create a hash." Es ist vorab nicht bekannt, ob ein
gegebener Miniserver den Schluessel als rohe, hex-dekodierte Bytes oder als
ASCII-Text der Hex-Zeichenkette fuer den HMAC erwartet.

Diese Datei versucht deshalb BEIDE Varianten nacheinander (erst hex-dekodiert,
bei 401/Ablehnung dann ASCII-Text) und nutzt automatisch, welche vom
jeweiligen Miniserver akzeptiert wird. Das ist eine pragmatische Reaktion auf
eine vom Hersteller selbst offen gelassene Unklarheit, kein Rätselraten ohne
Grundlage.

Weiterhin gilt: Dieser Code konnte in der Entwicklungsumgebung NICHT gegen
einen echten Miniserver getestet werden (kein Netzwerkzugriff auf
Loxone-Hardware aus der Sandbox heraus). Ein konkreter 401-Fehler an echter
Hardware hat bereits einen echten Bug aufgedeckt (Schritt 1 war faelschlich
einfacher SHA1 statt HMAC-SHA1) — der jetzige Fallback-Mechanismus ist die
direkte Reaktion auf einen weiteren, noch bestehenden 401-Fall.

ALTERNATIVE, DIE GEPRUEFT WURDE: Das eigenstaendige PyPI-Paket 'pyloxone-api'
(pip install pyloxone-api) desselben Autors wie das Home-Assistant-Plugin.
Seit Februar 2025 archiviert, letztes Release November 2021, Alpha-Reifegrad
— bewusst nicht als Abhaengigkeit uebernommen, nur als Referenz genutzt.

FA-FK-03-Analogie: Die direkte Loxone-API bleibt bewusst eine ALTERNATIVE
zu OCPP (FA-LS-08), nicht die einzige Option — OCPP ist offiziell von
Loxone seit Config 15.1 unterstuetzt und bereits vollstaendig getestet.
"""

import base64
import hashlib
import hmac
import http.cookiejar
import json
import re
import urllib.error
import urllib.parse
import urllib.request

TIMEOUT_SECONDS = 5
USER_AGENT = "ChargeAtHomeBillingEngine/1.0"


class LoxoneConnectionError(Exception):
    pass


def _http_get(url: str, opener=None) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    opener_fn = opener.open if opener is not None else urllib.request.urlopen
    with opener_fn(req, timeout=TIMEOUT_SECONDS) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _key_variants(key_hex: str) -> list[bytes]:
    """Die zwei moeglichen Interpretationen des von getkey2 gelieferten Schluessels
    (siehe Modul-Docstring). Reihenfolge: zuerst die haeufiger dokumentierte Variante."""
    variants = []
    try:
        variants.append(bytes.fromhex(key_hex))
    except ValueError:
        pass
    variants.append(key_hex.encode("ascii"))
    return variants


def _compute_final_hash(key_bytes: bytes, username: str, password: str, salt: str, hash_func) -> str:
    pw_hash = hmac.new(key_bytes, f"{password}:{salt}".encode("utf-8"), hash_func).hexdigest().upper()
    return hmac.new(key_bytes, f"{username}:{pw_hash}".encode("utf-8"), hash_func).hexdigest()


def _try_authenticate(host: str, username: str, password: str, opener=None):
    """Versucht den Handshake mit beiden Schluessel-Varianten. Liefert bei Erfolg
    (True, meldung) bzw. (opener, None) je nach Aufrufkontext, sonst (False, meldung)."""
    key_resp = _http_get(f"http://{host}/jdev/sys/getkey2/{urllib.parse.quote(username)}", opener)
    key_data = key_resp.get("LL", {}).get("value", {})
    salt = key_data.get("salt")
    key_hex = key_data.get("key")
    hash_alg = key_data.get("hashAlg", "SHA1")

    if not salt or not key_hex:
        return False, "Miniserver antwortete ohne Salt/Key — Firmware-Version prüfen (§ 6.4)."

    hash_func = hashlib.sha1 if hash_alg == "SHA1" else hashlib.sha256
    last_error_code = None

    for key_bytes in _key_variants(key_hex):
        final_hash = _compute_final_hash(key_bytes, username, password, salt, hash_func)
        try:
            token_resp = _http_get(
                f"http://{host}/jdev/sys/gettoken/{final_hash}/{urllib.parse.quote(username)}/4/1/ChargeAtHome",
                opener,
            )
        except urllib.error.HTTPError as exc:
            last_error_code = exc.code
            continue
        code = token_resp.get("LL", {}).get("Code", token_resp.get("LL", {}).get("code"))
        if str(code) == "200":
            return True, "Verbindung erfolgreich — Token erhalten."
        last_error_code = code

    return False, f"Miniserver lehnte beide Schlüssel-Varianten ab (letzter Code: {last_error_code})."


def get_value_basic_auth(host: str, username: str, password: str, name_or_uuid: str) -> tuple[float | None, str]:
    """FA-LS-10, bevorzugter einfacher Weg: liest einen Wert ueber den aelteren,
    unauthentifizierten-Token-freien Endpunkt /dev/sps/io/{name}, mit simplem
    HTTP-Basic-Auth in der URL (kein RSA/Token-Handshake noetig).

    Diese Methode ist durch die Praxis mehrerer aktiver Projekte bestaetigt
    (u. a. evcc.io nutzt genau dieses Muster produktiv fuer Loxone-Zaehler-
    werte). {name_or_uuid} kann entweder der in Loxone Config vergebene Name
    eines virtuellen Eingangs (z. B. "vi2") ODER die UUID eines Bausteins sein.

    Rueckgabe: (wert, meldung). wert ist None bei Fehler.
    """
    url = f"http://{host}/dev/sps/io/{urllib.parse.quote(name_or_uuid)}"
    credentials = base64.b64encode(f"{username}:{password}".encode("utf-8")).decode("ascii")
    req = urllib.request.Request(url, headers={
        "User-Agent": USER_AGENT,
        "Authorization": f"Basic {credentials}",
    })
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_SECONDS) as resp:
            body = resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        return None, f"HTTP {exc.code}: {exc.reason}"
    except Exception as exc:
        return None, f"Verbindungsfehler: {exc}"

    # Antwortformat: <LL control="dev/sps/io/vi2" value="6.4" Code="200"/>
    match = re.search(r'value="([^"]+)"', body)
    code_match = re.search(r'Code="([^"]+)"', body)
    if match is None:
        return None, f"Kein 'value'-Attribut in der Antwort gefunden: {body[:200]}"
    if code_match and code_match.group(1) != "200":
        return None, f"Miniserver-Code {code_match.group(1)}: {body[:200]}"
    try:
        return float(match.group(1)), "OK"
    except ValueError:
        return None, f"Wert '{match.group(1)}' nicht als Zahl lesbar"


def get_wallbox_all_values(host: str, username: str, password: str, wallbox_uuid: str) -> tuple[dict | None, str]:
    """FA-LS-10, bevorzugte Methode fuer Wallbox2-Bausteine: liest ALLE Werte auf
    einmal ueber /dev/sps/io/{uuid}/all. Notwendig, weil einzelne "states" von
    Wallbox2 (z. B. "total") nicht individuell abrufbar sind (404), aber ueber
    diesen Sammel-Befehl alle mitgeliefert werden — inklusive eines fertigen,
    von Loxone selbst aufbereiteten Log-Eintrags der letzten Ladesession (Lcl).

    Rueckgabe: (dict mit allen Werten nach 'name', meldung).
    """
    url = f"http://{host}/dev/sps/io/{urllib.parse.quote(wallbox_uuid)}/all"
    credentials = base64.b64encode(f"{username}:{password}".encode("utf-8")).decode("ascii")
    req = urllib.request.Request(url, headers={
        "User-Agent": USER_AGENT,
        "Authorization": f"Basic {credentials}",
    })
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_SECONDS) as resp:
            body = resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        return None, f"HTTP {exc.code}: {exc.reason}"
    except Exception as exc:
        return None, f"Verbindungsfehler: {exc}"

    code_match = re.search(r'Code="([^"]+)"', body)
    if code_match and code_match.group(1) != "200":
        return None, f"Miniserver-Code {code_match.group(1)}"

    values = {}
    for m in re.finditer(r'<output\s+name="([^"]+)"\s+nr="(\d+)"\s+value="([^"]*)"', body):
        values[m.group(1)] = m.group(3)
    return values, "OK"


def test_connection_basic_auth(host: str, username: str, password: str, name_or_uuid: str = "") -> tuple[bool, str]:
    """Testet den einfachen HTTP-Basic-Auth-Weg. Ohne name_or_uuid wird nur
    die Struktur-Datei abgerufen (prueft Zugangsdaten, ohne einen konkreten
    Baustein zu kennen)."""
    if name_or_uuid:
        value, msg = get_value_basic_auth(host, username, password, name_or_uuid)
        if value is not None:
            return True, f"Verbindung erfolgreich — aktueller Wert: {value}"
        return False, msg

    structure = get_structure_file(host, username, password)
    if structure is not None:
        return True, "Verbindung erfolgreich — Struktur-Datei abrufbar."
    return False, "Struktur-Datei nicht abrufbar (Zugangsdaten oder Host prüfen)."


def test_connection(host: str, username: str, password: str) -> tuple[bool, str]:
    """FA-LS-10 "Verbindung testen": Versucht den Token-Handshake mit beiden
    Schluessel-Interpretationen (siehe Modul-Docstring).

    Rückgabe: (erfolgreich, Meldungstext). Wirft keine Exceptions nach
    aussen — jeder Fehlerfall wird als (False, Grund) zurueckgegeben.
    """
    try:
        return _try_authenticate(host, username, password)
    except Exception as exc:
        return False, f"Verbindung fehlgeschlagen: {exc}"


def authenticate(host: str, username: str, password: str):
    """FA-LS-10: Fuehrt den Token-Handshake durch (beide Schluessel-Varianten,
    siehe Modul-Docstring) und liefert bei Erfolg einen wiederverwendbaren
    'Opener' (mit Session-Cookie fuer Folgeanfragen) zurueck, sonst None.
    """
    cookie_jar = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cookie_jar))
    try:
        ok, _ = _try_authenticate(host, username, password, opener=opener)
        return opener if ok else None
    except Exception:
        return None



def get_structure_file(host: str, username: str | None = None, password: str | None = None) -> dict | None:
    """Liefert die LoxAPP3.json (Struktur-/Namensinformation, keine Messwerte, siehe § 6.4).
    Optional mit HTTP-Basic-Auth, da dieser Endpunkt laut Browser-Zugriff oft
    einfach per Login-Dialog geschützt ist (einfacher als der Token-Handshake)."""
    try:
        req = urllib.request.Request(
            f"http://{host}/data/LoxAPP3.json", headers={"User-Agent": USER_AGENT}
        )
        if username and password:
            credentials = base64.b64encode(f"{username}:{password}".encode("utf-8")).decode("ascii")
            req.add_header("Authorization", f"Basic {credentials}")
        with urllib.request.urlopen(req, timeout=TIMEOUT_SECONDS) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception:
        return None


# Erkennungsmerkmale fuer Wallbox-Bausteine.
#
# Frueher wurde ausschliesslich auf das Wort "wallbox" in Typ oder Name
# geprueft. Das traf zwar bei der Standardbezeichnung "Wallbox2" zu, ging
# aber daneben, sobald jemand seinen Baustein "Ladestation Garage" nennt und
# Loxone einen anderen Typ vergibt — der Baustein tauchte dann in der Liste
# gar nicht auf, obwohl er da war.
WALLBOX_NAMENSMUSTER = ("wallbox", "ladestation", "ladepunkt", "ladesaeule",
                        "ladesäule", "charger", "charging", "e-auto", "eauto")

# Ausgaenge, die nur ein Wallbox-Baustein besitzt. Sie sind das
# verlaesslichste Merkmal: Sie haengen nicht daran, wie jemand den Baustein
# benannt hat.
WALLBOX_KENNAUSGAENGE = {"Mr", "Ccc", "Clc", "Cac", "Cclc"}


def _ist_wallbox(name: str, typ: str, eintrag: dict) -> bool:
    """Prueft auf drei Wegen, ob ein Baustein eine Wallbox ist.

    Reihenfolge nach Verlaesslichkeit: erst der Typ, dann die typischen
    Ausgaenge, zuletzt der Name."""
    if "wallbox" in (typ or "").lower():
        return True

    # Charakteristische Ausgaenge — unabhaengig von der Benennung
    zustaende = eintrag.get("states") or {}
    if isinstance(zustaende, dict) and WALLBOX_KENNAUSGAENGE & set(zustaende.keys()):
        return True

    name_klein = (name or "").lower()
    return any(muster in name_klein for muster in WALLBOX_NAMENSMUSTER)


def list_structure_controls(structure: dict, wallbox_only: bool = False) -> list[dict]:
    """Extrahiert eine flache, nach Namen sortierte Liste aller Bausteine
    (UUID + Name + Typ) aus einer LoxAPP3.json — fuer eine normale Auswahlliste
    in der Oberfläche, statt die Datei von Hand durchsuchen zu muessen.

    wallbox_only=True filtert auf Bausteine, die als Wallbox erkennbar sind
    (Typ enthaelt "Wallbox", z. B. "Wallbox2", oder Name enthaelt "wallbox")
    — Rueckmeldung des Auftraggebers: eine Liste mit hunderten unsortierten
    Bausteinen (Lampen, Beschattung etc.) ist nicht benutzbar, wenn eigentlich
    nur der/die Wallbox-Baustein(e) gesucht werden.
    """
    controls = structure.get("controls", {}) if isinstance(structure, dict) else {}
    result = []
    for uuid, entry in controls.items():
        if not isinstance(entry, dict):
            continue
        name = entry.get("name", uuid)
        typ = entry.get("type", "")
        is_wallbox = _ist_wallbox(name, typ, entry)
        if wallbox_only and not is_wallbox:
            continue
        result.append({"uuid": uuid, "name": name, "type": typ, "is_wallbox": is_wallbox})
        # Unterbausteine (subControls) NUR durchsuchen, wenn nicht explizit auf
        # Wallbox gefiltert wird — die relevanten Werte eines Wallbox2-Bausteins
        # kommen ueber /all auf dem Baustein SELBST, nicht ueber einzelne
        # Unter-UUIDs (siehe § 6.4a: einzelne "states" sind bei Wallbox2 nicht
        # individuell abrufbar).
        if not wallbox_only:
            for sub_uuid, sub_entry in (entry.get("subControls") or {}).items():
                if isinstance(sub_entry, dict):
                    result.append({
                        "uuid": sub_uuid,
                        "name": f"{name} → {sub_entry.get('name', sub_uuid)}",
                        "type": sub_entry.get("type", ""),
                        "is_wallbox": False,
                    })
    result.sort(key=lambda c: (not c["is_wallbox"], c["name"].lower()))
    return result


def get_value(opener, host: str, uuid: str) -> float | None:
    """FA-LS-10: Liest den aktuellen Wert eines Loxone-Bausteins (z. B. Zaehlerstand)
    ueber die authentifizierte Session. UNGETESTET gegen echte Hardware."""
    try:
        data = _http_get(f"http://{host}/jdev/sps/io/{uuid}", opener)
        value = data.get("LL", {}).get("value")
        return float(value) if value is not None else None
    except Exception:
        return None
