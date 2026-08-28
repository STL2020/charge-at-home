"""Anmeldung mit Passwort — optional zuschaltbar.

Die Anwendung ist fuer den Betrieb im eigenen Netz gedacht und hatte
deshalb bisher bewusst keine Anmeldung. Wer sie von aussen erreichbar
macht, braucht eine: sonst hat jeder mit der Adresse vollen Zugriff auf
Fahrtenbuch, Adressen, Wallbox-Zugangsdaten und Belege.

Bewusste Entscheidungen:

* Standardmaessig AUS. Ein bestehender Betrieb im Heimnetz soll durch ein
  Update nicht ploetzlich ausgesperrt werden.
* Kein Klartext-Passwort. Gespeichert wird ausschliesslich ein
  PBKDF2-HMAC-SHA256-Hash mit zufaelligem Salz und hoher Iterationszahl.
* Nur Standardbibliothek (hashlib, hmac, secrets) — keine zusaetzliche
  Abhaengigkeit, die im Docker-Abbild fehlen koennte.
* Sitzungen liegen im Arbeitsspeicher. Nach einem Neustart muss man sich
  neu anmelden. Das ist gewollt: fuer eine Einzelplatz-Anwendung ist das
  zumutbar und spart eine Angriffsflaeche in der Datenbank.
* Sperre nach mehreren Fehlversuchen gegen automatisiertes Durchprobieren.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import time

from repositories import settings_repository

# Schluessel in der Einstellungstabelle
SCHLUESSEL_AKTIV = "auth_aktiv"
SCHLUESSEL_HASH = "auth_passwort_hash"

# PBKDF2-Parameter. 240.000 Iterationen liegen ueber der Empfehlung des
# BSI und kosten auf einer NAS-CPU nur Sekundenbruchteile — beim Anmelden
# faellt das nicht auf, macht Durchprobieren aber teuer.
_ITERATIONEN = 240_000
_ALGORITHMUS = "pbkdf2_sha256"

# Sitzungsdauer: 12 Stunden. Lang genug fuer einen Arbeitstag, kurz genug,
# dass ein vergessener Browser nicht wochenlang offen bleibt.
SITZUNGSDAUER_S = 12 * 3600

# Sperre: nach 5 Fehlversuchen 15 Minuten Pause fuer diese Herkunftsadresse.
MAX_FEHLVERSUCHE = 5
SPERRDAUER_S = 15 * 60

# {token: ablaufzeitpunkt}
_sitzungen: dict[str, float] = {}
# {ip: (anzahl_fehlversuche, gesperrt_bis)}
_fehlversuche: dict[str, tuple[int, float]] = {}


def _hash_erzeugen(passwort: str, salz: bytes | None = None) -> str:
    """Erzeugt einen Passwort-Hash im Format algorithmus$salz$hash."""
    if salz is None:
        salz = secrets.token_bytes(16)
    ableitung = hashlib.pbkdf2_hmac(
        "sha256", passwort.encode("utf-8"), salz, _ITERATIONEN)
    return f"{_ALGORITHMUS}${salz.hex()}${ableitung.hex()}"


def _hash_pruefen(passwort: str, gespeichert: str) -> bool:
    """Prueft ein Passwort gegen den gespeicherten Hash.

    Vergleicht mit hmac.compare_digest statt '==', damit die Vergleichsdauer
    keine Rueckschluesse auf die Anzahl uebereinstimmender Zeichen zulaesst.
    """
    try:
        algorithmus, salz_hex, hash_hex = gespeichert.split("$", 2)
        if algorithmus != _ALGORITHMUS:
            return False
        erwartet = hashlib.pbkdf2_hmac(
            "sha256", passwort.encode("utf-8"), bytes.fromhex(salz_hex), _ITERATIONEN)
        return hmac.compare_digest(erwartet.hex(), hash_hex)
    except Exception:
        return False


def ist_aktiv() -> bool:
    """True, wenn die Anmeldung eingeschaltet UND ein Passwort gesetzt ist.

    Beide Bedingungen zusammen: ein eingeschalteter Schutz ohne Passwort
    wuerde den Anwender aussperren, ohne irgendetwas zu schuetzen.
    """
    try:
        return (settings_repository.get_setting(SCHLUESSEL_AKTIV) == "1"
                and bool(settings_repository.get_setting(SCHLUESSEL_HASH)))
    except Exception:
        return False


def passwort_gesetzt() -> bool:
    try:
        return bool(settings_repository.get_setting(SCHLUESSEL_HASH))
    except Exception:
        return False


def passwort_setzen(neues_passwort: str, altes_passwort: str | None = None) -> dict:
    """Setzt oder aendert das Passwort.

    Ist bereits eines gesetzt, muss das alte mitgegeben werden — sonst
    koennte jeder mit Zugriff auf die laufende Sitzung es stillschweigend
    austauschen.
    """
    neues_passwort = (neues_passwort or "").strip()
    if len(neues_passwort) < 8:
        return {"ok": False, "meldung": "Das Passwort muss mindestens 8 Zeichen haben."}

    bestehend = settings_repository.get_setting(SCHLUESSEL_HASH)
    if bestehend:
        if not altes_passwort or not _hash_pruefen(altes_passwort, bestehend):
            return {"ok": False, "meldung": "Das bisherige Passwort stimmt nicht."}

    settings_repository.set_setting(SCHLUESSEL_HASH, _hash_erzeugen(neues_passwort))
    # Alle bestehenden Sitzungen beenden: nach einem Passwortwechsel soll
    # sich jeder neu ausweisen, auch ein moeglicher Mitleser.
    _sitzungen.clear()
    return {"ok": True, "meldung": "Passwort gespeichert."}


def schutz_umschalten(aktiv: bool) -> dict:
    if aktiv and not passwort_gesetzt():
        return {"ok": False,
                "meldung": "Bitte zuerst ein Passwort vergeben, sonst sperrt der Schutz nur dich selbst aus."}
    settings_repository.set_setting(SCHLUESSEL_AKTIV, "1" if aktiv else "0")
    if not aktiv:
        _sitzungen.clear()
    return {"ok": True}


def _gesperrt_bis(ip: str) -> float:
    anzahl, bis = _fehlversuche.get(ip, (0, 0.0))
    return bis if bis > time.time() else 0.0


def anmelden(passwort: str, ip: str = "") -> dict:
    """Prueft das Passwort und gibt bei Erfolg ein Sitzungs-Token zurueck."""
    gesperrt = _gesperrt_bis(ip)
    if gesperrt:
        verbleibend = int(gesperrt - time.time())
        return {"ok": False,
                "meldung": f"Zu viele Fehlversuche. Bitte {verbleibend // 60 + 1} Minuten warten."}

    gespeichert = settings_repository.get_setting(SCHLUESSEL_HASH)
    if not gespeichert or not _hash_pruefen(passwort or "", gespeichert):
        anzahl, _ = _fehlversuche.get(ip, (0, 0.0))
        anzahl += 1
        bis = time.time() + SPERRDAUER_S if anzahl >= MAX_FEHLVERSUCHE else 0.0
        _fehlversuche[ip] = (anzahl, bis)
        offen = MAX_FEHLVERSUCHE - anzahl
        zusatz = f" Noch {offen} Versuch(e)." if 0 < offen <= 2 else ""
        return {"ok": False, "meldung": "Passwort falsch." + zusatz}

    _fehlversuche.pop(ip, None)
    token = secrets.token_urlsafe(32)
    _sitzungen[token] = time.time() + SITZUNGSDAUER_S
    return {"ok": True, "token": token}


def sitzung_gueltig(token: str | None) -> bool:
    if not token:
        return False
    ablauf = _sitzungen.get(token)
    if ablauf is None:
        return False
    if ablauf < time.time():
        _sitzungen.pop(token, None)
        return False
    return True


def abmelden(token: str | None) -> None:
    if token:
        _sitzungen.pop(token, None)
