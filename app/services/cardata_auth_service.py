"""BMW CarData — Authentifizierung nach OAuth 2.0 Device Authorization Grant.

Loest die frueher genutzte inoffizielle Anbindung ab, die BMW am
29.09.2025 fuer Drittanbieter gesperrt hat. CarData ist der offizielle,
von BMW bereitgestellte Weg fuer EU-Fahrzeuge — ohne Captcha, ohne
nachgebaute App-Schnittstelle.

ABLAUF (siehe BMW CarData Integration Guide, Kapitel 2)
-------------------------------------------------------
1. Der Nutzer erzeugt im MyBMW-Portal eine Client-ID und abonniert die
   Dienste "CarData API" und "CarData Stream".
2. `starte_geraeteanmeldung()` fordert Geraete- und Nutzercode an. Der Nutzer
   bestaetigt die Anmeldung einmalig im Browser.
3. `hole_tokens()` tauscht den Geraetecode gegen drei Token:
      access_token   — REST-API, 1 Stunde gueltig
      id_token       — MQTT-Stream, 1 Stunde gueltig
      refresh_token  — erneuert alle drei, 2 Wochen gueltig
4. `erneuere_tokens()` haelt die Anmeldung dauerhaft am Leben. Solange
   mindestens alle zwei Wochen erneuert wird, ist keine neue Bestaetigung
   noetig.

Die Absicherung erfolgt per PKCE (S256): Ein zufaelliger `code_verifier`
wird gehasht als `code_challenge` mitgeschickt und beim Tokenabruf im
Klartext nachgereicht. So kann ein abgefangener Geraetecode allein nicht
missbraucht werden.
"""
from __future__ import annotations

import base64
import hashlib
import json
import secrets
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta

from repositories import settings_repository

BASIS_URL = "https://customer.bmwgroup.com"
DEVICE_CODE_URL = f"{BASIS_URL}/gcdm/oauth/device/code"
TOKEN_URL = f"{BASIS_URL}/gcdm/oauth/token"

# "authenticate_user" und "openid" werden von BMW empfohlen. Die cardata-Scopes
# muessen GENAU zu den im Portal freigeschalteten Diensten passen: Wird ein
# Scope angefordert, fuer den die Client-ID nicht registriert ist, lehnt BMW
# die gesamte Anmeldung ab ("access_denied").
#
# Wir rufen die Daten ueber die REST-API ab, brauchen also nur
# 'cardata:api:read'. Der Streaming-Scope ist zusaetzlich anforderbar, falls
# der Nutzer beide Dienste freigeschaltet hat — noetig ist er nicht.
SCOPE_BASIS = "authenticate_user openid"
SCOPE_API = "cardata:api:read"
SCOPE_STREAM = "cardata:streaming:read"


def _scopes(mit_streaming: bool = False) -> str:
    teile = [SCOPE_BASIS, SCOPE_API]
    if mit_streaming:
        teile.append(SCOPE_STREAM)
    return " ".join(teile)


# Rueckwaertskompatibel fuer bestehende Aufrufe
SCOPES = f"{SCOPE_BASIS} {SCOPE_API}"

# Token vorzeitig erneuern, damit ein laufender Abruf nicht mitten in der
# Verarbeitung ungueltig wird.
ERNEUERN_VOR_SEKUNDEN = 300


# ── PKCE ───────────────────────────────────────────────────────────────────

def _erzeuge_pkce() -> tuple[str, str]:
    """Liefert (code_verifier, code_challenge) nach RFC 7636, Methode S256."""
    verifier = base64.urlsafe_b64encode(secrets.token_bytes(64)).decode().rstrip("=")
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).decode().rstrip("=")
    return verifier, challenge


# ── HTTP-Hilfsfunktion ─────────────────────────────────────────────────────

def _post_form(url: str, daten: dict) -> dict:
    """POST mit formcodiertem Body. Wirft nie — Fehler kommen als Dict zurueck,
    damit ein Ausfall der BMW-Server nie die Anwendung mitreisst."""
    body = urllib.parse.urlencode(daten).encode("utf-8")
    req = urllib.request.Request(url, data=body, headers={
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
        return {"ok": False, "status": e.code, "fehler": fehler,
                "meldung": _http_meldung(e.code, fehler)}
    except Exception as e:
        return {"ok": False, "status": None, "fehler": {},
                "meldung": f"BMW nicht erreichbar ({type(e).__name__})."}


def _http_meldung(status: int, fehler: dict) -> str:
    """Uebersetzt die haeufigsten Fehler in verstaendliche Hinweise."""
    code = (fehler.get("error") or fehler.get("errorId") or "").lower()
    if code == "authorization_pending":
        return "Bestätigung im Browser steht noch aus."
    if code == "slow_down":
        return "Zu schnell abgefragt — bitte kurz warten."
    if code == "expired_token":
        return "Der Anmeldecode ist abgelaufen. Bitte neu starten."
    if code == "access_denied":
        return ("Die Anmeldung wurde abgelehnt. Häufigste Ursache: Die "
                "Client-ID ist nicht für die angeforderten Dienste "
                "freigeschaltet. Prüfe im Portal, ob 'Request access to "
                "CarData API' aktiviert ist, warte danach etwa 60 Sekunden "
                "und versuche es erneut. Alternativ im Portal beide Dienste "
                "freischalten und hier die Option 'Streaming mit anfordern' "
                "setzen.")
    if status == 400:
        return ("Anfrage abgelehnt. Prüfe, ob die Client-ID stimmt und ob im "
                "Portal beide Dienste (CarData API und CarData Stream) "
                "freigeschaltet sind.")
    if status == 403:
        return ("Zugriff verweigert. Meist fehlt die Freischaltung im Portal "
                "oder sie ist noch nicht aktiv — nach dem Freischalten etwa "
                "60 Sekunden warten.")
    if status == 429:
        return "Zu viele Anfragen an BMW. Bitte einige Minuten warten."
    if status and status >= 500:
        return "BMW-Server antwortet derzeit nicht. Bitte später erneut versuchen."
    return f"Unerwartete Antwort von BMW (HTTP {status})."


# ── Schritt 1: Geräteanmeldung starten ─────────────────────────────────────

def starte_geraeteanmeldung(client_id: str, mit_streaming: bool = False) -> dict:
    """Fordert Geraete- und Nutzercode an.

    `mit_streaming` nur setzen, wenn im Portal auch "CarData Stream"
    freigeschaltet ist — sonst lehnt BMW die Anmeldung komplett ab.
    Der `code_verifier` wird zwischengespeichert, weil er beim spaeteren
    Tokenabruf nachgereicht werden muss."""
    client_id = (client_id or "").strip()
    if not client_id:
        return {"ok": False, "meldung": "Bitte zuerst die Client-ID eintragen."}

    verifier, challenge = _erzeuge_pkce()
    antwort = _post_form(DEVICE_CODE_URL, {
        "client_id": client_id,
        "response_type": "device_code",
        "scope": _scopes(mit_streaming),
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    })
    if not antwort["ok"]:
        return {"ok": False, "meldung": antwort["meldung"]}

    d = antwort["daten"]
    settings_repository.set_setting("cardata_client_id", client_id)
    settings_repository.set_setting("cardata_code_verifier", verifier)
    settings_repository.set_setting("cardata_device_code", d.get("device_code", ""))
    return {
        "ok": True,
        "user_code": d.get("user_code"),
        "verification_uri": d.get("verification_uri"),
        "verification_uri_complete": d.get("verification_uri_complete"),
        "interval": int(d.get("interval", 5)),
        "expires_in": int(d.get("expires_in", 600)),
    }


# ── Schritt 2: Tokens abholen ──────────────────────────────────────────────

def hole_tokens() -> dict:
    """Tauscht den Geraetecode gegen die drei Token.

    Wird aufgerufen, NACHDEM der Nutzer die Anmeldung im Browser bestaetigt
    hat. Steht die Bestaetigung noch aus, meldet BMW 'authorization_pending' —
    das ist kein Fehler, sondern die Aufforderung, es gleich erneut zu
    versuchen."""
    client_id = settings_repository.get_setting("cardata_client_id") or ""
    device_code = settings_repository.get_setting("cardata_device_code") or ""
    verifier = settings_repository.get_setting("cardata_code_verifier") or ""
    if not (client_id and device_code and verifier):
        return {"ok": False, "meldung": "Keine laufende Anmeldung. Bitte neu starten."}

    antwort = _post_form(TOKEN_URL, {
        "client_id": client_id,
        "device_code": device_code,
        "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
        "code_verifier": verifier,
    })
    if not antwort["ok"]:
        wartet = (antwort.get("fehler", {}).get("error") == "authorization_pending")
        return {"ok": False, "wartet": wartet, "meldung": antwort["meldung"]}

    _speichere_tokens(antwort["daten"])
    return {"ok": True, "gcid": antwort["daten"].get("gcid")}


def _speichere_tokens(d: dict) -> None:
    gueltig_bis = datetime.now() + timedelta(seconds=int(d.get("expires_in", 3600)))
    settings_repository.set_setting("cardata_access_token", d.get("access_token", ""))
    settings_repository.set_setting("cardata_id_token", d.get("id_token", ""))
    settings_repository.set_setting("cardata_refresh_token", d.get("refresh_token", ""))
    settings_repository.set_setting("cardata_gcid", d.get("gcid", ""))
    settings_repository.set_setting("cardata_token_gueltig_bis",
                                    gueltig_bis.strftime("%Y-%m-%d %H:%M:%S"))
    settings_repository.set_setting("cardata_refresh_erneuert_am",
                                    datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    # Einmalige Anmeldedaten werden nicht mehr gebraucht
    settings_repository.set_setting("cardata_device_code", "")
    settings_repository.set_setting("cardata_code_verifier", "")


# ── Schritt 3: Tokens erneuern ─────────────────────────────────────────────

def erneuere_tokens(erzwingen: bool = False) -> dict:
    """Erneuert Access-, ID- und Refresh-Token.

    Wird vor jedem Zugriff aufgerufen; erneuert nur, wenn noetig. BMW setzt
    dabei auch die Frist des Refresh-Tokens zurueck, weshalb regelmaessige
    Nutzung eine erneute Browser-Bestaetigung erspart."""
    refresh = settings_repository.get_setting("cardata_refresh_token") or ""
    client_id = settings_repository.get_setting("cardata_client_id") or ""
    if not (refresh and client_id):
        return {"ok": False, "meldung": "Nicht angemeldet."}

    if not erzwingen and not token_laeuft_ab():
        return {"ok": True, "erneuert": False}

    antwort = _post_form(TOKEN_URL, {
        "grant_type": "refresh_token",
        "refresh_token": refresh,
        "client_id": client_id,
    })
    if not antwort["ok"]:
        return {"ok": False, "meldung": antwort["meldung"]}
    _speichere_tokens(antwort["daten"])
    return {"ok": True, "erneuert": True}


def token_laeuft_ab() -> bool:
    """True, wenn der Access-Token in Kuerze ungueltig wird."""
    bis = settings_repository.get_setting("cardata_token_gueltig_bis") or ""
    try:
        ende = datetime.strptime(bis, "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return True
    return (ende - datetime.now()).total_seconds() < ERNEUERN_VOR_SEKUNDEN


def hole_access_token() -> str | None:
    """Gueltiger Access-Token fuer REST-Aufrufe, erneuert bei Bedarf."""
    if token_laeuft_ab():
        if not erneuere_tokens().get("ok"):
            return None
    return settings_repository.get_setting("cardata_access_token") or None


def hole_id_token() -> str | None:
    """Gueltiger ID-Token fuer den MQTT-Stream, erneuert bei Bedarf."""
    if token_laeuft_ab():
        if not erneuere_tokens().get("ok"):
            return None
    return settings_repository.get_setting("cardata_id_token") or None


def status() -> dict:
    """Anmeldestatus fuer die Oberflaeche."""
    refresh = settings_repository.get_setting("cardata_refresh_token") or ""
    erneuert_am = settings_repository.get_setting("cardata_refresh_erneuert_am") or ""
    tage_seit = None
    if erneuert_am:
        try:
            tage_seit = (datetime.now() -
                         datetime.strptime(erneuert_am, "%Y-%m-%d %H:%M:%S")).days
        except ValueError:
            pass
    # Der Refresh-Token gilt zwei Wochen; danach ist eine erneute
    # Browser-Bestaetigung noetig.
    ablauf_droht = tage_seit is not None and tage_seit >= 11
    return {
        "angemeldet": bool(refresh),
        "client_id_gesetzt": bool(settings_repository.get_setting("cardata_client_id")),
        "gcid": settings_repository.get_setting("cardata_gcid") or "",
        "erneuert_am": erneuert_am,
        "tage_seit_erneuerung": tage_seit,
        "ablauf_droht": ablauf_droht,
        "token_gueltig_bis": settings_repository.get_setting("cardata_token_gueltig_bis") or "",
    }


def abmelden() -> None:
    """Entfernt alle gespeicherten Anmeldedaten."""
    for key in ("cardata_access_token", "cardata_id_token", "cardata_refresh_token",
                "cardata_gcid", "cardata_token_gueltig_bis",
                "cardata_refresh_erneuert_am", "cardata_device_code",
                "cardata_code_verifier"):
        settings_repository.set_setting(key, "")
