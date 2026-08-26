#!/bin/bash
# Start/Stopp/Status des eCharge@Home OCPP-Dienstes.

SKRIPTPFAD="$(cd "$(dirname "$0")" && pwd)"
DIENST="$SKRIPTPFAD/echargeocpp.py"
LBHOME="${LBHOMEDIR:-/opt/loxberry}"

# Plugin-Ordner aus dem Skript-Pfad:
#   /opt/loxberry/bin/plugins/echargeocpp/dienst.sh
#   → dirname → .../echargeocpp
#   → basename → echargeocpp  ✓
# Die frühere Berechnung nahm dirname zweimal → "plugins" statt "echargeocpp".
PLUGIN="$(basename "$SKRIPTPFAD")"
[ "$PLUGIN" = "bin" ] && PLUGIN="echargeocpp"   # Fallback bei ungewöhnlichem Aufruf

LOGDIR="$LBHOME/log/plugins/$PLUGIN"
DATADIR="$LBHOME/data/plugins/$PLUGIN"
mkdir -p "$LOGDIR" "$DATADIR"

laeuft() {
    pgrep -f "echargeocpp.py" >/dev/null 2>&1
}

stoppen() {
    pkill -f "echargeocpp.py" 2>/dev/null
    for i in $(seq 1 10); do
        laeuft || return 0
        sleep 1
    done
    pkill -9 -f "echargeocpp.py" 2>/dev/null
    sleep 1
}

starten() {
    [ -f "$DIENST" ] || { echo "FEHLER: $DIENST fehlt"; return 2; }
    stoppen

    # LBPPLUGINDIR = Ordnername des Plugins (nicht der volle Pfad).
    # Python liest ihn für Daten- und Log-Verzeichnis.
    export LBPPLUGINDIR="$PLUGIN"
    export LBHOMEDIR="$LBHOME"

    if command -v setsid >/dev/null 2>&1; then
        setsid python3 "$DIENST" </dev/null >>"$LOGDIR/start.log" 2>&1 &
    else
        nohup python3 "$DIENST" </dev/null >>"$LOGDIR/start.log" 2>&1 &
    fi

    for i in $(seq 1 8); do
        sleep 1
        laeuft && {
            echo "Dienst gestartet (PID $(pgrep -f 'echargeocpp.py' | head -1))"
            echo "Log: $LOGDIR/ocpp.log"
            return 0
        }
    done
    echo "Dienst konnte nicht gestartet werden"
    tail -5 "$LOGDIR/start.log" 2>/dev/null
    return 1
}

case "${1:-start}" in
    start|restart) starten ;;
    stop)          stoppen; echo "Dienst beendet" ;;
    status)
        if laeuft; then echo "laeuft"; exit 0
        else echo "gestoppt"; exit 1; fi ;;
    *) echo "Aufruf: $0 {start|stop|restart|status}"; exit 2 ;;
esac
