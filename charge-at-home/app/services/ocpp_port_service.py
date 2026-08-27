"""Port des eingebauten OCPP-Servers.

Bewusst ein eigenes, kleines Modul: Frueher stand die Funktion in
ocpp_server/server.py, das websockets und die ocpp-Bibliothek importiert.
Jede Route, die nur den Port wissen wollte, zog damit den halben
OCPP-Stapel mit — und scheiterte, sobald eine dieser Bibliotheken fehlte.
"""
from __future__ import annotations

import os

from repositories import settings_repository

STANDARD_PORT = 9000
MIN_PORT = 1024      # darunter braucht ein Prozess Sonderrechte
MAX_PORT = 65535


def ocpp_port() -> int:
    """Aktueller Port. Einstellung vor Umgebungsvariable vor Standard.

    9000 ist haeufig belegt — Portainer nutzt ihn standardmaessig.
    """
    try:
        wert = settings_repository.get_setting("ocpp_port")
        if wert and str(wert).strip().isdigit():
            p = int(str(wert).strip())
            if MIN_PORT <= p <= MAX_PORT:
                return p
    except Exception:
        pass

    try:
        p = int(os.environ.get("CHARGE_OCPP_PORT", STANDARD_PORT))
        if MIN_PORT <= p <= MAX_PORT:
            return p
    except (TypeError, ValueError):
        pass

    return STANDARD_PORT


def ist_gueltig(port: int) -> bool:
    return MIN_PORT <= port <= MAX_PORT


def ist_frei(port: int) -> bool:
    """Prueft, ob der Port belegt ist — vor dem Uebernehmen."""
    import socket
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        s.bind(("0.0.0.0", port))
        return True
    except OSError:
        return False
    finally:
        s.close()
