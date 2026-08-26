#!/bin/bash
#
# Wird beim Entfernen des Plugins ausgefuehrt.
# Beendet den Dienst. Die Daten bleiben absichtlich erhalten — wer das
# Plugin erneut installiert, hat seine Ladehistorie sonst verloren.

pkill -f "echargeocpp.py" 2>/dev/null
/usr/bin/logger "eCharge@Home OCPP: Dienst beendet"
exit 0
