"""
OCPP-Client-Scheduler — FA-OCPP-CLIENT-01.

Eigenstaendiger Hintergrundprozess (analog zu loxone/poller.py), der
periodisch alle aktivierten OCPP-Client-Konfigurationen durchgeht und
ausstehende Sessions an den jeweils konfigurierten externen OCPP-Dienst
weiterreicht (siehe services/ocpp_client_service.py fuer die Details/den
fachlichen Hintergrund).

Bewusst als SEPARATER Prozess wie der Loxone-Poller und der OCPP-Server —
ein haengender/langsamer externer Dienst soll nie die anderen Hintergrund-
prozesse (Loxone-Polling, OCPP-Server) beeintraechtigen koennen.
"""

import asyncio
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from repositories import ocpp_client_repository
from services import ocpp_client_service, event_log_service

CYCLE_SECONDS = 30


def run_all_cycles_once() -> None:
    """Fuehrt fuer JEDE aktivierte Konfiguration einen Verbindungszyklus aus.
    Synchron aufrufbar (fuehrt intern die noetigen async-Zyklen aus) — so
    bleibt die Aufrufstelle (Schleife unten, oder ein Test) einfach."""
    try:
        import websockets
    except ImportError:
        event_log_service.log_event(
            "ocpp", "error",
            "OCPP-Client: Paket 'websockets' nicht installiert — Client-Modus pausiert. "
            "'pip install websockets' nachholen und neu starten."
        )
        return

    configs = ocpp_client_repository.list_enabled_configs()
    for config in configs:
        asyncio.run(ocpp_client_service.run_client_cycle(websockets, config["wallbox_id"], config))


def main() -> None:
    print("OCPP-Client-Scheduler gestartet.")
    event_log_service.log_event("system", "info", "OCPP-Client-Scheduler gestartet")
    while True:
        run_all_cycles_once()
        time.sleep(CYCLE_SECONDS)


if __name__ == "__main__":
    main()
