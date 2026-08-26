#!/bin/bash
#
# Wird nach dem Kopieren der Dateien ausgefuehrt (als Benutzer loxberry).
#   $3 = Installationsordner des Plugins
#
# Rueckgabe: 0 = fertig, 1 = Warnung, 2 = Abbruch

PLUGINDIR=$3
[ -z "$PLUGINDIR" ] && PLUGINDIR="echargeocpp"

echo "<INFO> Richte eCharge@Home OCPP ein"

if ! command -v python3 >/dev/null 2>&1; then
    echo "<FAIL> Python 3 wurde nicht gefunden. Bitte nachinstallieren:"
    echo "<FAIL>   sudo apt-get install python3"
    exit 2
fi
echo "<OK> Python 3 gefunden: $(python3 --version 2>&1)"

# Es werden bewusst keine zusaetzlichen Pakete benoetigt: Das Plugin nutzt
# ausschliesslich die Standardbibliothek.

mkdir -p "$LBHOMEDIR/data/plugins/$PLUGINDIR"
mkdir -p "$LBHOMEDIR/log/plugins/$PLUGINDIR"
chmod +x "$LBHOMEDIR/bin/plugins/$PLUGINDIR/echargeocpp.py" 2>/dev/null
chmod +x "$LBHOMEDIR/bin/plugins/$PLUGINDIR/pruefen.sh" 2>/dev/null
echo "<OK> Verzeichnisse angelegt"

# DIENST SOFORT STARTEN
#
# Das Startskript unter daemon/ laeuft ausschliesslich beim Systemstart.
# Ohne diesen Schritt bliebe das Plugin nach der Installation stumm, und der
# Anwender saehe in der Oberflaeche nur "Dienst antwortet nicht" — ohne
# Hinweis, dass ein Neustart noetig waere.
DIENST="$LBHOMEDIR/bin/plugins/$PLUGINDIR/echargeocpp.py"
LOGDIR="$LBHOMEDIR/log/plugins/$PLUGINDIR"

if [ ! -f "$DIENST" ]; then
    echo "<FAIL> $DIENST wurde nicht gefunden"
    exit 2
fi

# Dienst starten. Die Einzelheiten stehen in dienst.sh — dort wird auch
# gewartet, bis die Ports einer alten Instanz frei sind. Genau daran ist
# der Neustart nach einem Update bisher gescheitert.
SKRIPT="$LBHOMEDIR/bin/plugins/$PLUGINDIR/dienst.sh"
chmod +x "$SKRIPT" 2>/dev/null

if [ -x "$SKRIPT" ]; then
    AUSGABE=$("$SKRIPT" start 2>&1)
    if echo "$AUSGABE" | grep -q "laeuft"; then
        echo "<OK> $AUSGABE"
        echo "<INFO> Wallbox einrichten mit: ws://<IP-dieses-Geraets>:9000/ocpp"
        echo "<INFO> Daten abrufbar unter:   http://<IP-dieses-Geraets>:8042/api/sessions"
    else
        # Kein Abbruch: Die Dateien liegen korrekt. Der Cron-Job startet den
        # Dienst spaetestens in fuenf Minuten, ein Eingriff ist nicht noetig.
        echo "<WARNING> Der Dienst laeuft noch nicht:"
        echo "$AUSGABE" | while read -r ZEILE; do echo "<WARNING>   $ZEILE"; done
        echo "<INFO> Das ist unkritisch: Die Pruefung laeuft alle fuenf Minuten"
        echo "<INFO> und startet den Dienst selbsttaetig. Alternativ laesst er"
        echo "<INFO> sich in der Weboberflaeche des Plugins starten."
    fi
else
    echo "<WARNING> dienst.sh fehlt — Dienst startet beim naechsten Neustart"
fi

exit 0
