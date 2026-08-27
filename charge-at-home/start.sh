#!/usr/bin/env bash
# ===================================================================
#  eCharge@Home — Start (macOS und Linux)
#
#  Nutzt Docker, wenn vorhanden. Sonst wird die Anwendung unmittelbar
#  mit Python gestartet — dann muss dieses Fenster geoeffnet bleiben.
# ===================================================================

cd "$(dirname "$0")" || exit 1

echo
echo " ============================================"
echo "  eCharge@Home"
echo "  Ladeabrechnung und Fahrtenbuch"
echo " ============================================"
echo

# Eigene Netzwerkadresse ermitteln, damit der Aufruf vom Handy klappt
if command -v ip >/dev/null 2>&1; then
    LANIP=$(ip route get 1.1.1.1 2>/dev/null | awk '{print $7; exit}')
elif command -v ipconfig >/dev/null 2>&1; then
    LANIP=$(ipconfig getifaddr en0 2>/dev/null || ipconfig getifaddr en1 2>/dev/null)
fi
[ -z "$LANIP" ] && LANIP="localhost"

if command -v docker >/dev/null 2>&1 && docker info >/dev/null 2>&1; then
    echo " Docker gefunden — starte im Hintergrund."
    echo
    if ! docker compose up --build -d; then
        echo
        echo " Der Start ist fehlgeschlagen. Laeuft der Docker-Dienst?"
        exit 1
    fi
    echo
    echo " ============================================"
    echo "  Laeuft im Hintergrund"
    echo " ============================================"
    echo
    echo "  Aufrufen:     http://localhost:8501"
    echo "  Im Netzwerk:  http://$LANIP:8501"
    echo
    echo "  Der Container startet ab jetzt bei jedem"
    echo "  Neustart des Geraets von selbst."
    echo
    echo "  Anhalten mit: docker compose down"
    echo
    exit 0
fi

echo " Docker nicht gefunden — starte unmittelbar mit Python."
echo

if ! command -v python3 >/dev/null 2>&1; then
    echo " FEHLER: Python 3 wurde nicht gefunden."
    echo
    echo "   macOS:  brew install python3"
    echo "   Debian: sudo apt install python3 python3-venv"
    echo
    exit 1
fi

if [ ! -d ".venv" ]; then
    echo " [1/3] Richte Arbeitsumgebung ein — das dauert beim ersten Mal"
    echo "       einige Minuten ..."
    python3 -m venv .venv
fi
# shellcheck disable=SC1091
. .venv/bin/activate

echo " [2/3] Pruefe benoetigte Pakete ..."
pip install --quiet --disable-pip-version-check -r requirements.txt

echo " [3/3] Starte ..."
echo
echo " ============================================"
echo "  WICHTIG"
echo " ============================================"
echo
echo "  Dieses Fenster muss GEOEFFNET BLEIBEN."
echo "  Wird es geschlossen, ist die Anwendung"
echo "  nicht mehr erreichbar."
echo
echo "  Bei angebundener Wallbox ueber OCPP gilt:"
echo "  Der Rechner muss laufen, wenn geladen wird"
echo "  - also meist nachts. Sonst gehen Lade-"
echo "  vorgaenge verloren."
echo
echo "  Dauerbetrieb? Dann besser Docker auf einem"
echo "  NAS oder Raspberry Pi. Siehe INSTALLATION.md"
echo
echo " --------------------------------------------"
echo
echo "  Aufrufen:     http://localhost:8501"
echo "  Im Netzwerk:  http://$LANIP:8501"
echo
echo "  Beenden mit Strg+C"
echo

cd app || exit 1
# Browser oeffnen, ohne den Start aufzuhalten
( sleep 3
  if command -v xdg-open >/dev/null 2>&1; then xdg-open http://localhost:8501
  elif command -v open >/dev/null 2>&1; then open http://localhost:8501
  fi ) >/dev/null 2>&1 &

python3 app.py

echo
echo " Die Anwendung wurde beendet."
