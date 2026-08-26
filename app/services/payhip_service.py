"""Lizenzpruefung ueber Payhip (API v2, Produkt-Geheimschluessel).

WARUM DER SCHLUESSEL HIER STEHEN DARF
-------------------------------------
Payhip unterscheidet zwei Verfahren:

  v1  Konto-API-Schluessel — gilt fuer ALLE Produkte des Kontos. Gehoert
      nicht in ausgelieferte Software.
  v2  Produkt-Geheimschluessel (Praefix 'prod_sk_') — gilt nur fuer EIN
      Produkt. Payhip empfiehlt ihn ausdruecklich fuer "public applications
      where you shouldn't expose your API key to everyone".

Wir nutzen v2. Was jemand mit dem Schluessel anstellen koennte, beschraenkt
sich auf dieses eine Produkt: Lizenzen pruefen, aktivieren, deaktivieren.
Kein Zugriff auf Umsaetze, Preise, Kundendaten oder andere Produkte.

Die Anwendung ruft ausschliesslich 'verify' auf — 'enable' und 'disable'
sind bewusst nicht implementiert. Was nicht im Code steht, kann auch nicht
versehentlich ausgeloest werden.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta

from repositories import settings_repository
import services.event_log_service as event_log_service

VERIFY_URL = "https://payhip.com/api/v2/license/verify"

# Produkt-Geheimschluessel. Nur fuer eCharge@Home gueltig — siehe Modulkopf.
PRODUKT_SECRET = "prod_sk_smMFv_db967e20dac2288b311d7484cee933c123b2db95"
PRODUKT_LINK = "https://payhip.com/b/smMFv"

# Nach erfolgreicher Pruefung gilt die Lizenz ohne erneute Abfrage. Das haelt
# die Anwendung offline lauffaehig und schont den Dienst.
REVALIDIERUNG_TAGE = 14
KULANZ_TAGE = 30


def _verify(license_key: str) -> dict:
    """Fragt Payhip nach einem Schluessel.

    'erreichbar' unterscheidet eine echte Ablehnung von einem Netzproblem —
    nur im zweiten Fall greift die Kulanzregel."""
    url = f"{VERIFY_URL}?{urllib.parse.urlencode({'license_key': license_key})}"
    # User-Agent ausdruecklich setzen: Payhip laeuft hinter Cloudflare, und
    # Cloudflare weist die Standardkennung 'Python-urllib/3.x' mit HTTP 403
    # ab. Mit einer eigenen Kennung geht dieselbe Anfrage durch.
    req = urllib.request.Request(url, headers={
        "product-secret-key": PRODUKT_SECRET,
        "Accept": "application/json",
        "User-Agent": "eCharge-at-Home/1.0 (+https://www.loewemann.com)",
    })
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            roh = resp.read().decode("utf-8").strip()
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return {"gueltig": False, "erreichbar": True,
                    "meldung": "Dieser Lizenzschlüssel ist unbekannt."}
        if e.code == 401:
            return {"gueltig": False, "erreichbar": False,
                    "meldung": ("Payhip weist die Anmeldung ab. Bitte den "
                                "Anbieter benachrichtigen.")}
        if e.code == 403:
            return {"gueltig": False, "erreichbar": False,
                    "meldung": ("Zugriff auf Payhip abgelehnt. Ein Filter im "
                                "Netzwerk oder ein DNS-Blocker verhindert die "
                                "Verbindung.")}
        return {"gueltig": False, "erreichbar": False,
                "meldung": f"Payhip antwortet nicht wie erwartet (HTTP {e.code})."}
    except Exception as e:
        return {"gueltig": False, "erreichbar": False,
                "meldung": f"Payhip nicht erreichbar ({type(e).__name__})."}

    # Bei fehlgeschlagener Prüfung kommt laut Dokumentation eine leere Antwort
    if not roh:
        return {"gueltig": False, "erreichbar": True,
                "meldung": "Dieser Lizenzschlüssel ist ungültig."}
    try:
        daten = json.loads(roh)
    except ValueError:
        return {"gueltig": False, "erreichbar": True,
                "meldung": "Unerwartete Antwort von Payhip."}

    d = daten.get("data", daten) if isinstance(daten, dict) else {}
    aktiv = bool(d.get("enabled"))
    return {
        "gueltig": aktiv,
        "erreichbar": True,
        "meldung": None if aktiv else "Der Schlüssel wurde deaktiviert.",
        "kaeufer": d.get("buyer_email") or "",
        "nutzungen": d.get("uses"),
        "produkt": d.get("product_name") or "",
        "variante": d.get("variant_name") or "",
        "gekauft_am": (d.get("date") or "")[:10],
    }


def aktiviere(license_key: str) -> dict:
    """Prueft und speichert einen Lizenzschluessel."""
    key = (license_key or "").strip().upper()
    if len(key) < 8:
        return {"ok": False, "fehler": "Bitte den vollständigen Lizenzschlüssel eingeben."}

    ergebnis = _verify(key)
    if not ergebnis["gueltig"]:
        event_log_service.log_event("system", "warning",
            f"Lizenzprüfung fehlgeschlagen: {ergebnis['meldung']}")
        return {"ok": False, "fehler": ergebnis["meldung"]}

    heute = datetime.now().strftime("%Y-%m-%d")
    settings_repository.set_setting("lizenz_key", key)
    settings_repository.set_setting("lizenz_geprueft_am", heute)
    settings_repository.set_setting("lizenz_kaeufer", ergebnis.get("kaeufer") or "")
    settings_repository.set_setting("lizenz_gekauft_am", ergebnis.get("gekauft_am") or "")
    event_log_service.log_event("system", "info",
        f"Lizenz aktiviert für {ergebnis.get('kaeufer') or 'unbekannt'}.")
    return {"ok": True, "kaeufer": ergebnis.get("kaeufer"),
            "gekauft_am": ergebnis.get("gekauft_am"),
            "variante": ergebnis.get("variante")}


def status() -> dict:
    """Aktueller Lizenzzustand, mit gelegentlicher Nachprüfung.

    Die Prüfung läuft höchstens alle 14 Tage. Ist Payhip dann nicht
    erreichbar, bleibt die Lizenz weitere 30 Tage gültig — ein Serverausfall
    darf zahlende Kunden nicht aussperren."""
    key = (settings_repository.get_setting("lizenz_key") or "").strip()
    if not key:
        return {"lizenziert": False}

    geprueft = settings_repository.get_setting("lizenz_geprueft_am") or ""
    try:
        alter = (datetime.now() - datetime.strptime(geprueft, "%Y-%m-%d")).days
    except ValueError:
        alter = REVALIDIERUNG_TAGE + 1

    basis = {
        "lizenziert": True,
        "kaeufer": settings_repository.get_setting("lizenz_kaeufer") or "",
        "gekauft_am": settings_repository.get_setting("lizenz_gekauft_am") or "",
        "geprueft_vor_tagen": alter,
    }
    if alter < REVALIDIERUNG_TAGE:
        return basis

    ergebnis = _verify(key)
    if ergebnis["gueltig"]:
        settings_repository.set_setting("lizenz_geprueft_am",
                                        datetime.now().strftime("%Y-%m-%d"))
        basis["geprueft_vor_tagen"] = 0
        return basis

    if not ergebnis["erreichbar"]:
        if alter <= KULANZ_TAGE:
            basis["hinweis"] = (f"Payhip war nicht erreichbar. Die Lizenz gilt noch "
                                f"{KULANZ_TAGE - alter} Tage ohne erneute Prüfung.")
            return basis
        return {"lizenziert": False,
                "hinweis": "Lizenz konnte über 30 Tage nicht bestätigt werden."}

    # Ausdrücklich abgelehnt — etwa nach einer Rückerstattung
    settings_repository.set_setting("lizenz_key", "")
    event_log_service.log_event("system", "warning",
        f"Lizenz nicht mehr gültig: {ergebnis['meldung']}")
    return {"lizenziert": False, "hinweis": ergebnis["meldung"]}


def entferne() -> None:
    """Lizenz von diesem Rechner entfernen."""
    for key in ("lizenz_key", "lizenz_geprueft_am", "lizenz_kaeufer", "lizenz_gekauft_am"):
        settings_repository.set_setting(key, "")
