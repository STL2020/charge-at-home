#!/usr/bin/env bash
# =============================================================================
#  Lastprüfung — was verbraucht auf diesem LoxBerry die Rechenzeit?
#
#  Aufruf über SSH:
#      bash /opt/loxberry/bin/plugins/echargeocpp/lastpruefung.sh
#
#  Misst 30 Sekunden lang und nennt die zehn größten Verbraucher. Damit
#  laesst sich beantworten, ob dieses Plugin die Ursache ist — oder etwas
#  anderes.
# =============================================================================

echo
echo "  Lastprüfung — $(date '+%d.%m.%Y %H:%M')"
echo "  ──────────────────────────────────────────────────────────────"
echo

# ── Gerät ────────────────────────────────────────────────────────────────
MODELL=$(tr -d '\0' < /proc/device-tree/model 2>/dev/null || echo "unbekannt")
KERNE=$(nproc 2>/dev/null || echo "?")
RAM=$(free -m 2>/dev/null | awk '/^Mem:/{print $2" MB, davon "$3" MB belegt"}')
echo "  Gerät:  $MODELL"
echo "  Kerne:  $KERNE"
echo "  Speicher: $RAM"
echo

# ── Auslastung im Mittel ─────────────────────────────────────────────────
LAST=$(cut -d' ' -f1-3 /proc/loadavg)
echo "  Lastmittel (1 / 5 / 15 Minuten): $LAST"
if [ "$KERNE" != "?" ]; then
    EINS=$(cut -d' ' -f1 /proc/loadavg)
    echo "  Zum Vergleich: Ab $KERNE,00 ist das Gerät voll ausgelastet."
fi
echo

# ── Die größten Verbraucher ──────────────────────────────────────────────
echo "  Messe 30 Sekunden ..."
echo

# Erste Momentaufnahme
ps -eo pid,pcpu,pmem,comm --sort=-pcpu > /tmp/last_vorher.txt
sleep 30
ps -eo pid,pcpu,pmem,comm --sort=-pcpu > /tmp/last_nachher.txt

echo "  Die zehn größten Verbraucher"
echo "  ──────────────────────────────────────────────────────────────"
printf "  %-8s %7s %7s  %s\n" "PID" "CPU %" "RAM %" "Programm"
head -11 /tmp/last_nachher.txt | tail -10 | while read pid cpu mem name; do
    printf "  %-8s %7s %7s  %s\n" "$pid" "$cpu" "$mem" "$name"
done
echo

# ── Dieses Plugin gesondert ──────────────────────────────────────────────
echo "  eCharge@Home OCPP"
echo "  ──────────────────────────────────────────────────────────────"
PID=$(pgrep -f "echargeocpp.py" | head -1)
if [ -z "$PID" ]; then
    echo "  Der Dienst läuft nicht."
else
    ZEILE=$(ps -p "$PID" -o pid,pcpu,pmem,etime,rss --no-headers)
    set -- $ZEILE
    echo "  PID:            $1"
    echo "  Rechenzeit:     $2 %"
    echo "  Arbeitsspeicher: $3 %  ($(( $5 / 1024 )) MB)"
    echo "  Läuft seit:     $4"
    echo
    # Einordnung
    CPU_GANZ=${2%.*}
    if [ "${CPU_GANZ:-0}" -lt 5 ]; then
        echo "  → Unauffällig. Das Plugin ist nicht die Ursache einer hohen Last."
    elif [ "${CPU_GANZ:-0}" -lt 20 ]; then
        echo "  → Erhöht, aber im Rahmen. Läuft gerade eine Ladung?"
    else
        echo "  → Auffällig hoch. Bitte das Protokoll mitschicken:"
        echo "     /opt/loxberry/log/plugins/echargeocpp/ocpp.log"
    fi
fi
echo

# ── Häufige andere Ursachen ──────────────────────────────────────────────
echo "  Andere Dienste auf diesem Gerät"
echo "  ──────────────────────────────────────────────────────────────"
for dienst in mosquitto influxd grafana-server telegraf node_exporter \
              apache2 nginx mariadbd mysqld python3 perl; do
    TREFFER=$(pgrep -c "$dienst" 2>/dev/null || echo 0)
    if [ "$TREFFER" -gt 0 ]; then
        CPU=$(ps -C "$dienst" -o pcpu= 2>/dev/null | awk '{s+=$1} END {printf "%.1f", s}')
        printf "  %-18s %s Prozess(e), zusammen %s %% CPU\n" "$dienst" "$TREFFER" "${CPU:-0}"
    fi
done
echo

# ── Temperatur und Drosselung ────────────────────────────────────────────
if [ -f /sys/class/thermal/thermal_zone0/temp ]; then
    T=$(( $(cat /sys/class/thermal/thermal_zone0/temp) / 1000 ))
    echo "  Temperatur: ${T} °C"
    [ "$T" -gt 75 ] && echo "  → Über 75 °C drosselt ein Raspberry Pi die Taktrate."
fi
if command -v vcgencmd >/dev/null 2>&1; then
    DROSSEL=$(vcgencmd get_throttled 2>/dev/null)
    echo "  $DROSSEL"
    echo "  (throttled=0x0 bedeutet: alles in Ordnung)"
fi
echo

# ── Speicherplatz ────────────────────────────────────────────────────────
echo "  Speicherplatz"
echo "  ──────────────────────────────────────────────────────────────"
df -h / /opt 2>/dev/null | grep -v "^Filesystem\|^Dateisystem" | awk '{printf "  %-20s %s belegt von %s (%s)\n", $6, $3, $2, $5}'
echo

rm -f /tmp/last_vorher.txt /tmp/last_nachher.txt
echo "  Fertig. Diese Ausgabe hilft bei einer Rückfrage."
echo
