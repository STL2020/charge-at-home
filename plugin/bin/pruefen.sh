#!/bin/bash
#
# Sucht die Ursache, wenn der Dienst nicht antwortet.
#
# Aufruf:  ./pruefen.sh
# Behebt nichts, sondern zeigt, woran es liegt — mit dem jeweils passenden
# naechsten Schritt.

echo ""
echo "eCharge@Home OCPP — Pruefung"
echo "============================="
echo ""

# 1. Laeuft der Prozess?
echo "1. Prozess"
if pgrep -f "echargeocpp.py" >/dev/null; then
    echo "   OK — laeuft (PID $(pgrep -f echargeocpp.py | tr '\n' ' '))"
else
    echo "   Der Dienst laeuft NICHT."
    echo "   Starten mit:"
    echo "     nohup python3 $(dirname "$0")/echargeocpp.py &"
fi
echo ""

# 2. Antworten die Ports?
#
# Geprueft wird per Verbindungsversuch statt mit 'ss' oder 'netstat': Beide
# sind nicht auf jedem System vorhanden und zeigen ohne root-Rechte oft
# nichts an — die Meldung "Port frei" waere dann schlicht falsch.
echo "2. Ports"
for PORT in 9000 8042; do
    ERREICHBAR=$(python3 - "$PORT" <<'PYEND'
import socket, sys
s = socket.socket()
s.settimeout(2)
try:
    s.connect(("127.0.0.1", int(sys.argv[1])))
    print("ja")
except Exception:
    print("nein")
finally:
    s.close()
PYEND
)
    if [ "$ERREICHBAR" = "ja" ]; then
        echo "   $PORT — OK, nimmt Verbindungen an"
    else
        if pgrep -f "echargeocpp.py" >/dev/null; then
            echo "   $PORT — antwortet NICHT, obwohl der Dienst laeuft."
            echo "     Vermutlich war der Port beim Start belegt. Protokoll unten pruefen."
        else
            echo "   $PORT — antwortet nicht (Dienst laeuft ja auch nicht)"
        fi
    fi
done
echo ""

# 3. Antwortet die Schnittstelle?
echo "3. Schnittstelle"
if command -v curl >/dev/null 2>&1; then
    ANTWORT=$(curl -s -m 3 http://127.0.0.1:8042/api/status 2>&1)
    if echo "$ANTWORT" | grep -q "laeuft"; then
        echo "   OK — antwortet"
        echo "   $(echo "$ANTWORT" | head -c 160)"
    else
        echo "   Keine Antwort auf Port 8042."
    fi
else
    echo "   curl nicht vorhanden — uebersprungen"
fi
echo ""

# 4. Python
echo "4. Python"
if command -v python3 >/dev/null 2>&1; then
    echo "   OK — $(python3 --version 2>&1)"
else
    echo "   FEHLT. Installieren mit: sudo apt-get install python3"
fi
echo ""

# 5. Letzte Meldungen
echo "5. Letzte Meldungen"
LOGDIR=$(dirname "$(dirname "$(dirname "$0")")")/log/plugins/$(basename "$(dirname "$0")")
for DATEI in "$LOGDIR/ocpp.log" "$LOGDIR/start.log"; do
    if [ -f "$DATEI" ]; then
        echo "   $DATEI:"
        tail -n 8 "$DATEI" | sed 's/^/     /'
    fi
done
echo ""
echo "Bleibt es unklar: Dienst im Vordergrund starten, dann steht der Fehler"
echo "unmittelbar auf dem Bildschirm:"
echo "  python3 $(dirname "$0")/echargeocpp.py"
echo ""
