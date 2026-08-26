#!/bin/sh
# Charge@Home - Entry Point (Flask + OCPP + Loxone-Poller, getrennte Prozesse nach NFA-09)
set -e

echo "Charge@Home Billing Engine - Sprint 3 (vollstaendig)"
cd app

echo "Starte OCPP-Central-System auf Port 9000 (Hintergrundprozess) ..."
python ocpp_server/server.py &

echo "Starte Loxone-Poller fuer direkte API-Wallboxen (Hintergrundprozess) ..."
python loxone/poller.py &

echo "Starte Web-Server auf Port 8501 (Vordergrundprozess) ..."
exec python app.py
