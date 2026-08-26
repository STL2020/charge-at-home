#!/usr/bin/env bash
# =============================================================================
#  eCharge@Home — Installation
#
#  Fuer Linux, Raspberry Pi, Synology, QNAP und jedes System mit Docker.
#  Aufruf ueber SSH (PuTTY, Terminal):
#
#      bash install.sh
#
#  Das Skript fragt ab, was es wissen muss, legt die Verzeichnisse an,
#  erzeugt die Konfiguration und startet die Anwendung. Kein Kopieren von
#  Hand, kein Bearbeiten von YAML-Dateien.
# =============================================================================

set -euo pipefail

# ── Darstellung ──────────────────────────────────────────────────────────────
if [ -t 1 ]; then
    GRUEN=$'\033[0;32m'; ROT=$'\033[0;31m'; GELB=$'\033[0;33m'
    BLAU=$'\033[0;36m';  FETT=$'\033[1m';   AUS=$'\033[0m'
else
    GRUEN=''; ROT=''; GELB=''; BLAU=''; FETT=''; AUS=''
fi

titel()   { printf '\n%s%s%s\n' "$FETT" "$1" "$AUS"; printf '%s\n' "$(printf '─%.0s' {1..62})"; }
ok()      { printf '  %s✓%s %s\n' "$GRUEN" "$AUS" "$1"; }
warn()    { printf '  %s!%s %s\n' "$GELB" "$AUS" "$1"; }
fehler()  { printf '  %s✗%s %s\n' "$ROT" "$AUS" "$1"; }
info()    { printf '    %s\n' "$1"; }

abbruch() { fehler "$1"; printf '\n'; exit 1; }

# ── Begruessung ──────────────────────────────────────────────────────────────
clear 2>/dev/null || true
cat <<'KOPF'

  ┌────────────────────────────────────────────────────────────┐
  │                                                            │
  │   eCharge@Home — Installation                              │
  │   Ladeabrechnung und Fahrtenbuch                           │
  │                                                            │
  └────────────────────────────────────────────────────────────┘

  Dieses Skript richtet die Anwendung vollstaendig ein. Es stellt
  einige Fragen und erledigt danach alles selbst.

  Abbrechen jederzeit mit Strg+C.

KOPF

read -r -p "  Fortfahren? [J/n] " _weiter
case "${_weiter:-j}" in
    [nN]*) printf '\n  Abgebrochen.\n\n'; exit 0 ;;
esac

# ── 1. System pruefen ────────────────────────────────────────────────────────
titel "1. System pruefen"

# Docker vorhanden?
if command -v docker >/dev/null 2>&1; then
    DOCKER_VERSION=$(docker --version 2>/dev/null | head -1)
    ok "Docker gefunden: ${DOCKER_VERSION}"
else
    fehler "Docker ist nicht installiert."
    printf '\n'
    info "Auf einem Raspberry Pi oder Debian/Ubuntu:"
    info "    curl -fsSL https://get.docker.com | sh"
    info "    sudo usermod -aG docker \$USER"
    info "    (danach einmal ab- und wieder anmelden)"
    printf '\n'
    info "Auf einer Synology: Paketzentrum → Container Manager installieren"
    info "Auf einer QNAP:     App Center → Container Station installieren"
    printf '\n'
    abbruch "Bitte Docker installieren und das Skript erneut aufrufen."
fi

# Laeuft der Docker-Dienst?
if ! docker info >/dev/null 2>&1; then
    fehler "Docker ist installiert, aber nicht erreichbar."
    printf '\n'
    info "Moegliche Ursachen:"
    info "  • Der Dienst laeuft nicht:  sudo systemctl start docker"
    info "  • Fehlende Berechtigung:    sudo usermod -aG docker \$USER"
    info "    (danach neu anmelden)"
    printf '\n'
    abbruch "Bitte beheben und erneut aufrufen."
fi
ok "Docker-Dienst laeuft"

# Compose vorhanden? Beide Schreibweisen zulassen
if docker compose version >/dev/null 2>&1; then
    COMPOSE="docker compose"
elif command -v docker-compose >/dev/null 2>&1; then
    COMPOSE="docker-compose"
else
    abbruch "Docker Compose fehlt. Bitte nachinstallieren."
fi
ok "Docker Compose: ${COMPOSE}"

# Systemart erkennen — bestimmt den vorgeschlagenen Pfad
SYSTEM="linux"
PFAD_VORSCHLAG="$HOME/echarge"
if [ -d /volume1 ]; then
    SYSTEM="synology";  PFAD_VORSCHLAG="/volume1/docker/echarge"
elif [ -d /share/Container ]; then
    SYSTEM="qnap";      PFAD_VORSCHLAG="/share/Container/echarge"
elif [ -d /mnt/user/appdata ]; then
    SYSTEM="unraid";    PFAD_VORSCHLAG="/mnt/user/appdata/echarge"
elif grep -qi raspberry /proc/cpuinfo 2>/dev/null; then
    SYSTEM="raspberry"; PFAD_VORSCHLAG="$HOME/echarge"
fi
ok "System erkannt: ${SYSTEM}"

# Quellverzeichnis — dort liegt dieses Skript
QUELLE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [ ! -d "$QUELLE/app" ] || [ ! -f "$QUELLE/Dockerfile" ]; then
    fehler "Die Anwendungsdateien fehlen."
    info "Erwartet werden 'app/' und 'Dockerfile' neben diesem Skript."
    info "Aktuelles Verzeichnis: $QUELLE"
    printf '\n'
    abbruch "Bitte das Paket vollstaendig entpacken."
fi
ok "Anwendungsdateien gefunden"

# ── 2. Fragen ────────────────────────────────────────────────────────────────
titel "2. Einstellungen"

printf '\n  %sWohin soll die Anwendung installiert werden?%s\n' "$FETT" "$AUS"
info "Dort landen Datenbank, Belege und Einstellungen."
info "Dieses Verzeichnis sollte in die Datensicherung."
printf '\n'
read -r -p "  Pfad [${PFAD_VORSCHLAG}]: " ZIEL
ZIEL="${ZIEL:-$PFAD_VORSCHLAG}"

printf '\n  %sAuf welchem Port soll die Weboberflaeche laufen?%s\n' "$FETT" "$AUS"
info "Standard ist 8501. Aendern, falls der Port belegt ist."
printf '\n'
read -r -p "  Port [8501]: " PORT_WEB
PORT_WEB="${PORT_WEB:-8501}"

printf '\n  %sSoll der OCPP-Server aktiviert werden?%s\n' "$FETT" "$AUS"
info "Nur noetig, wenn sich eine Wallbox direkt hier anmelden soll."
info "Fuer Loxone-Wallboxen oder das LoxBerry-Plugin: nein."
info "(Der eingebaute OCPP-Server ist der Vollversion vorbehalten.)"
printf '\n'
read -r -p "  OCPP-Port 9000 freigeben? [j/N] " _ocpp
OCPP_AN="nein"
case "${_ocpp:-n}" in [jJyY]*) OCPP_AN="ja" ;; esac

printf '\n  %sZeitzone%s\n' "$FETT" "$AUS"
info "Bestimmt die Zeitstempel auf Belegen und im Protokoll."
printf '\n'
TZ_VORSCHLAG="$(cat /etc/timezone 2>/dev/null || echo 'Europe/Berlin')"
read -r -p "  Zeitzone [${TZ_VORSCHLAG}]: " ZEITZONE
ZEITZONE="${ZEITZONE:-$TZ_VORSCHLAG}"

# ── 3. Zugriffsschutz ────────────────────────────────────────────────────────
titel "3. Zugriffsschutz"

cat <<'SICHERHEIT'

  Die Anwendung hat KEINE eigene Benutzeranmeldung. Wer die Adresse
  erreicht, sieht das vollstaendige Fahrtenbuch mit Adressen und
  Fahrzwecken, alle Ladevorgaenge und die Zugangsdaten der Wallbox.

  Im eigenen Netz ist das unproblematisch. Soll die Anwendung aber
  ueber das Internet erreichbar sein, ist ein Zugangsschutz zwingend.

SICHERHEIT

read -r -p "  Zugangsschutz einrichten (Caddy mit Passwort)? [j/N] " _caddy
CADDY_AN="nein"
CADDY_BENUTZER=""
CADDY_HASH=""
CADDY_DOMAIN=""

case "${_caddy:-n}" in
    [jJyY]*)
        CADDY_AN="ja"
        printf '\n'
        read -r -p "  Benutzername [admin]: " CADDY_BENUTZER
        CADDY_BENUTZER="${CADDY_BENUTZER:-admin}"

        # Passwort zweimal abfragen, verdeckt
        while :; do
            printf '  Passwort (Eingabe bleibt verborgen): '
            read -r -s CADDY_PASS1; printf '\n'
            if [ ${#CADDY_PASS1} -lt 8 ]; then
                warn "Mindestens 8 Zeichen bitte."
                continue
            fi
            printf '  Passwort wiederholen: '
            read -r -s CADDY_PASS2; printf '\n'
            [ "$CADDY_PASS1" = "$CADDY_PASS2" ] && break
            warn "Die Eingaben stimmen nicht ueberein."
        done

        printf '\n'
        info "Optional: Domainname fuer ein Let's-Encrypt-Zertifikat."
        info "Leer lassen fuer Betrieb ohne Zertifikat (nur im Heimnetz)."
        printf '\n'
        read -r -p "  Domain [leer]: " CADDY_DOMAIN

        # Passwort-Hash von Caddy selbst erzeugen lassen
        printf '\n'
        info "Erzeuge Passwort-Hash …"
        CADDY_HASH="$(docker run --rm caddy:2-alpine \
                      caddy hash-password --plaintext "$CADDY_PASS1" 2>/dev/null || true)"
        unset CADDY_PASS1 CADDY_PASS2
        if [ -z "$CADDY_HASH" ]; then
            warn "Hash konnte nicht erzeugt werden — Zugangsschutz wird uebersprungen."
            CADDY_AN="nein"
        else
            ok "Passwort-Hash erzeugt"
        fi
        ;;
    *)
        info "Kein Zugangsschutz — Betrieb nur im eigenen Netz vorgesehen."
        ;;
esac

# ── 4. Zusammenfassung ───────────────────────────────────────────────────────
titel "4. Zusammenfassung"

printf '\n'
printf '    Verzeichnis   %s\n' "$ZIEL"
printf '    Weboberflaeche Port %s\n' "$PORT_WEB"
printf '    OCPP-Server   %s\n' "$OCPP_AN"
printf '    Zeitzone      %s\n' "$ZEITZONE"
printf '    Zugangsschutz %s\n' "$CADDY_AN"
[ "$CADDY_AN" = "ja" ] && printf '    Benutzer      %s\n' "$CADDY_BENUTZER"
[ -n "$CADDY_DOMAIN" ] && printf '    Domain        %s\n' "$CADDY_DOMAIN"
printf '\n'

read -r -p "  So installieren? [J/n] " _los
case "${_los:-j}" in
    [nN]*) printf '\n  Abgebrochen. Nichts veraendert.\n\n'; exit 0 ;;
esac

# ── 5. Installieren ──────────────────────────────────────────────────────────
titel "5. Installation"

# Verzeichnisse
mkdir -p "$ZIEL/data" "$ZIEL/app"
ok "Verzeichnisse angelegt"

# Dateien kopieren — nur was gebraucht wird
cp -r "$QUELLE/app/." "$ZIEL/app/"
cp "$QUELLE/Dockerfile" "$ZIEL/"
[ -f "$QUELLE/requirements.txt" ] && cp "$QUELLE/requirements.txt" "$ZIEL/"
[ -f "$QUELLE/entrypoint.sh" ] && cp "$QUELLE/entrypoint.sh" "$ZIEL/"
[ -d "$QUELLE/data" ] && cp -rn "$QUELLE/data/." "$ZIEL/data/" 2>/dev/null || true
[ -d "$QUELLE/doku" ] && cp -r "$QUELLE/doku" "$ZIEL/" 2>/dev/null || true
ok "Anwendungsdateien kopiert"

# docker-compose.yml erzeugen
{
    echo "# Erzeugt von install.sh am $(date '+%Y-%m-%d %H:%M')"
    echo "# Aenderungen hier sind moeglich; das Skript ueberschreibt die Datei"
    echo "# nur bei einer erneuten Installation."
    echo ""
    echo "services:"
    echo "  echarge:"
    echo "    build: ."
    echo "    container_name: echarge"
    echo "    restart: unless-stopped"
    echo "    volumes:"
    echo "      - ./data:/srv/data"
    echo "    environment:"
    echo "      - TZ=${ZEITZONE}"
    if [ "$CADDY_AN" = "ja" ]; then
        # Hinter dem Proxy: kein direkter Port nach aussen
        echo "    expose:"
        echo "      - \"8501\""
    else
        echo "    ports:"
        echo "      - \"${PORT_WEB}:8501\""
    fi
    [ "$OCPP_AN" = "ja" ] && { echo "      - \"9000:9000\""; }
    echo "    healthcheck:"
    echo "      test: [\"CMD\", \"python3\", \"-c\", \"import urllib.request; urllib.request.urlopen('http://127.0.0.1:8501/', timeout=5)\"]"
    echo "      interval: 60s"
    echo "      timeout: 10s"
    echo "      retries: 3"
    echo "      start_period: 40s"

    if [ "$CADDY_AN" = "ja" ]; then
        echo ""
        echo "  # Zugangsschutz. Caddy nimmt die Anfragen entgegen, prueft"
        echo "  # Benutzer und Passwort und reicht sie erst dann weiter."
        echo "  caddy:"
        echo "    image: caddy:2-alpine"
        echo "    container_name: echarge-proxy"
        echo "    restart: unless-stopped"
        echo "    depends_on:"
        echo "      - echarge"
        echo "    ports:"
        echo "      - \"${PORT_WEB}:80\""
        [ -n "$CADDY_DOMAIN" ] && echo "      - \"443:443\""
        echo "    volumes:"
        echo "      - ./Caddyfile:/etc/caddy/Caddyfile:ro"
        echo "      - ./caddy-daten:/data"
        echo "      - ./caddy-konfig:/config"
        echo "    environment:"
        echo "      - TZ=${ZEITZONE}"
    fi
} > "$ZIEL/docker-compose.yml"
ok "docker-compose.yml erzeugt"

# Caddyfile
if [ "$CADDY_AN" = "ja" ]; then
    {
        echo "# Erzeugt von install.sh am $(date '+%Y-%m-%d %H:%M')"
        echo "#"
        echo "# basic_auth schuetzt den Zugang. Der Hash wurde mit"
        echo "#   caddy hash-password"
        echo "# erzeugt und laesst sich nicht zurueckrechnen."
        echo ""
        if [ -n "$CADDY_DOMAIN" ]; then
            echo "${CADDY_DOMAIN} {"
        else
            echo ":80 {"
        fi
        echo "    basic_auth {"
        echo "        ${CADDY_BENUTZER} ${CADDY_HASH}"
        echo "    }"
        echo "    reverse_proxy echarge:8501"
        echo ""
        echo "    log {"
        echo "        output file /data/zugriffe.log"
        echo "    }"
        echo "}"
    } > "$ZIEL/Caddyfile"
    mkdir -p "$ZIEL/caddy-daten" "$ZIEL/caddy-konfig"
    ok "Caddyfile erzeugt"
fi

# Hilfsskripte fuer den taeglichen Umgang
cat > "$ZIEL/start.sh" <<'START'
#!/usr/bin/env bash
cd "$(dirname "$0")" || exit 1
docker compose up -d && echo "Gestartet." || docker-compose up -d
START

cat > "$ZIEL/stop.sh" <<'STOP'
#!/usr/bin/env bash
cd "$(dirname "$0")" || exit 1
docker compose down && echo "Angehalten." || docker-compose down
STOP

cat > "$ZIEL/update.sh" <<'UPDATE'
#!/usr/bin/env bash
# Neues Paket ueber app/ kopieren, dann dieses Skript aufrufen.
cd "$(dirname "$0")" || exit 1
echo "Sichere Datenbank …"
mkdir -p sicherungen
tar czf "sicherungen/vor-update-$(date +%Y%m%d-%H%M).tar.gz" data/ 2>/dev/null
echo "Baue neu …"
docker compose down
docker compose up -d --build
echo "Fertig."
UPDATE

cat > "$ZIEL/sichern.sh" <<'SICHERN'
#!/usr/bin/env bash
cd "$(dirname "$0")" || exit 1
mkdir -p sicherungen
DATEI="sicherungen/echarge-$(date +%Y%m%d-%H%M).tar.gz"
tar czf "$DATEI" data/
echo "Gesichert: $DATEI"
SICHERN

chmod +x "$ZIEL"/*.sh
ok "Hilfsskripte angelegt (start, stop, update, sichern)"

# ── 6. Starten ───────────────────────────────────────────────────────────────
titel "6. Erster Start"

printf '\n  Das Abbild wird gebaut. Beim ersten Mal dauert das\n'
printf '  einige Minuten — bitte warten.\n\n'

cd "$ZIEL"
if ! $COMPOSE up -d --build; then
    printf '\n'
    fehler "Der Start ist fehlgeschlagen."
    info "Meldungen ansehen mit:  cd $ZIEL && $COMPOSE logs"
    printf '\n'
    exit 1
fi

# Warten bis die Anwendung antwortet
printf '\n  Warte auf die Anwendung '
BEREIT="nein"
for _ in $(seq 1 30); do
    if curl -sf -o /dev/null "http://127.0.0.1:${PORT_WEB}/" 2>/dev/null; then
        BEREIT="ja"; break
    fi
    # Mit Zugangsschutz antwortet der Proxy mit 401 — das gilt als bereit
    if [ "$CADDY_AN" = "ja" ] && \
       [ "$(curl -s -o /dev/null -w '%{http_code}' "http://127.0.0.1:${PORT_WEB}/" 2>/dev/null)" = "401" ]; then
        BEREIT="ja"; break
    fi
    printf '.'
    sleep 2
done
printf '\n'

# ── 7. Fertig ────────────────────────────────────────────────────────────────
IP="$(hostname -I 2>/dev/null | awk '{print $1}')"
[ -z "$IP" ] && IP="$(ip route get 1.1.1.1 2>/dev/null | awk '{print $7; exit}')"
[ -z "$IP" ] && IP="<IP-des-Geraets>"

titel "Fertig"

if [ "$BEREIT" = "ja" ]; then
    ok "Die Anwendung laeuft"
else
    warn "Die Anwendung antwortet noch nicht — sie braucht vielleicht"
    info "noch einen Moment. Zustand pruefen mit: $COMPOSE ps"
fi

printf '\n'
printf '  %sAufrufen unter%s\n\n' "$FETT" "$AUS"
if [ -n "$CADDY_DOMAIN" ]; then
    printf '      %shttps://%s%s\n' "$BLAU" "$CADDY_DOMAIN" "$AUS"
else
    printf '      %shttp://%s:%s%s\n' "$BLAU" "$IP" "$PORT_WEB" "$AUS"
fi
[ "$CADDY_AN" = "ja" ] && printf '\n      Benutzer: %s\n' "$CADDY_BENUTZER"

printf '\n  %sVerwaltung%s\n\n' "$FETT" "$AUS"
printf '      cd %s\n' "$ZIEL"
printf '      ./start.sh      Starten\n'
printf '      ./stop.sh       Anhalten\n'
printf '      ./sichern.sh    Daten sichern\n'
printf '      ./update.sh     Nach einem Update neu bauen\n'

printf '\n  %sDaten%s\n\n' "$FETT" "$AUS"
printf '      %s/data\n' "$ZIEL"
printf '      Dieses Verzeichnis in die Datensicherung aufnehmen.\n'

if [ "$CADDY_AN" != "ja" ]; then
    printf '\n  %sHinweis zum Zugriff%s\n\n' "$GELB" "$AUS"
    printf '      Die Anwendung ist ohne Passwort erreichbar. Das ist im\n'
    printf '      eigenen Netz in Ordnung. Machen Sie sie nicht ueber das\n'
    printf '      Internet erreichbar — fuer den Zugriff von unterwegs\n'
    printf '      eignet sich ein VPN.\n'
fi

printf '\n  Anleitungen liegen unter %s/doku\n\n' "$ZIEL"
