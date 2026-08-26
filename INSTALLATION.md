# eCharge@Home — Installation

Zwei Wege stehen zur Wahl. Der Unterschied ist wichtig, deshalb zuerst die
Entscheidungshilfe.

---

## Welcher Weg?

| | **Docker** | **Lokal auf dem Rechner** |
|---|---|---|
| Läuft auf | NAS, Raspberry Pi, Server, jedem Rechner mit Docker | Windows, macOS, Linux |
| Rechner muss laufen | nein — das Gerät läuft ohnehin durch | **ja, dauerhaft** |
| Startet nach Stromausfall | selbsttätig | nein, von Hand |
| OCPP-Server für Wallboxen | uneingeschränkt nutzbar | nur solange das Programm offen ist |
| Einrichtung | einmalig etwas mehr Aufwand | Doppelklick |

**Empfehlung:** Wer eine Wallbox über OCPP anbinden will, nimmt Docker. Der
Grund steht weiter unten unter „Warum der Rechner laufen muss".

---

## Weg 1 — Docker (empfohlen)

Geeignet für Synology, QNAP, Unraid, Raspberry Pi, Portainer und jeden
Rechner mit Docker Desktop.

### Voraussetzung

Docker muss installiert sein. Auf einem NAS ist es meist als Paket
verfügbar („Container Manager" bei Synology, „Container Station" bei QNAP).

### Einrichtung

1. Das Paket entpacken, sodass `docker-compose.yml`, `Dockerfile` und der
   Ordner `app/` nebeneinander liegen.

2. Im Terminal in dieses Verzeichnis wechseln und starten:

   ```
   docker compose up -d
   ```

   Beim ersten Mal dauert das einige Minuten — das Abbild wird gebaut.

3. Im Browser aufrufen:

   ```
   http://<IP-des-Geräts>:8501
   ```

Das war es. Der Container startet ab jetzt bei jedem Neustart des Geräts von
selbst.

### Portainer

Portainer kann nicht aus einem lokalen Verzeichnis bauen. Zwei Möglichkeiten:

**A — Abbild vorher bauen** (auf einem Rechner mit Docker):

```
docker build -t echarge/at-home:latest .
docker save echarge/at-home:latest -o echarge.tar
```

Die Datei `echarge.tar` in Portainer unter **Images → Import** laden. Dann
unter **Stacks → Add stack** den Inhalt von `portainer-stack.yml` einfügen.
Vorher den Pfad unter `volumes:` an das eigene Gerät anpassen.

**B — Über Git**: Stack mit Repository-Herkunft anlegen und auf
`docker-compose.yml` verweisen. Portainer baut dann selbst.

### Synology (Container Manager)

1. Ordner anlegen: `/volume1/docker/echarge/data`
2. Container Manager → **Projekt** → **Erstellen**
3. Pfad wählen, `docker-compose.yml` hochladen
4. Starten

### Daten sichern

Alles liegt im Ordner `data/` — Datenbank, Belege, Einstellungen. Diesen
Ordner sichern, mehr ist nicht nötig. Die Anwendung bringt zusätzlich eine
eigene Sicherungsfunktion mit (Einstellungen → System).

### Aktualisieren

```
docker compose down
docker compose up -d --build
```

Die Daten im Ordner `data/` bleiben dabei unberührt.

---

## Weg 2 — Lokal auf dem Rechner

Für alle, die kein NAS haben oder die Anwendung erst ausprobieren möchten.

### Windows

Doppelklick auf **`start.bat`**.

Das Skript prüft, ob Docker vorhanden ist, und nutzt es dann. Andernfalls
startet es die Anwendung direkt mit Python. Ein Browserfenster öffnet sich.

Benötigt Python 3.10 oder neuer, falls kein Docker installiert ist.
Beim ersten Start werden die nötigen Pakete nachinstalliert.

### macOS und Linux

```
./start.sh
```

### Aufrufen

```
http://localhost:8501
```

Vom Handy oder Tablet im selben Netz:

```
http://<IP-des-Rechners>:8501
```

### ⚠ Warum der Rechner laufen muss

**Die Anwendung läuft nur, solange das Programm geöffnet ist.** Wird das
Fenster geschlossen oder der Rechner heruntergefahren, ist sie nicht mehr
erreichbar.

Für die reine Erfassung und Auswertung ist das unerheblich — man öffnet sie,
wenn man sie braucht.

**Bei angebundener Wallbox über OCPP ist es das nicht:**

Eine Wallbox meldet ihre Ladevorgänge in dem Moment, in dem sie stattfinden.
Und das ist meistens nachts. Ist die Anwendung dann geschlossen, findet die
Wallbox niemanden — der Ladevorgang geht ungezählt vorbei und lässt sich
nachträglich nicht rekonstruieren.

Wer über OCPP anbindet, sollte deshalb:

- **Docker auf einem durchlaufenden Gerät** nutzen (siehe Weg 1), **oder**
- den Rechner samt geöffnetem Programm dauerhaft laufen lassen, **oder**
- das kostenlose **LoxBerry-Plugin** als Vermittler einsetzen. Es nimmt die
  Ladevorgänge rund um die Uhr entgegen; eCharge@Home holt sie später ab.
  Der Rechner darf dann jederzeit aus sein.

Für **Loxone-Wallboxen** gilt das nicht: Deren Daten liegen im Miniserver und
werden beim nächsten Start nachgeladen.

### Automatisch mitstarten (Windows)

Wer die Anwendung dauerhaft laufen lassen möchte, legt eine Verknüpfung zu
`start.bat` in den Autostart-Ordner:

1. `Windows` + `R`, dann `shell:startup` eingeben
2. Verknüpfung zu `start.bat` dort hineinlegen

Der Rechner darf dann nicht in den Ruhezustand wechseln — unter
**Energieoptionen** entsprechend einstellen.

---

## Netzwerk

| Port | Wofür | Wird benötigt |
|---|---|---|
| **8501** | Weboberfläche | immer |
| **9000** | OCPP 1.6-J | nur bei direkt angebundener Wallbox (Vollversion) |

Beide Ports müssen im Heimnetz erreichbar sein. Eine Freigabe ins Internet
ist **nicht** nötig und wird ausdrücklich nicht empfohlen — die Anwendung ist
für den Betrieb im eigenen Netz ausgelegt.

---

## Wenn etwas nicht funktioniert

| Beobachtung | Ursache und Abhilfe |
|---|---|
| Seite nicht erreichbar | Läuft der Container? `docker ps` zeigt es. Bei lokaler Installation: Ist das Fenster noch offen? |
| Port 8501 belegt | Anderes Programm nutzt ihn. In `docker-compose.yml` auf `8502:8501` ändern |
| Wallbox verbindet sich nicht | Port 9000 muss erreichbar sein. Bei lokaler Installation: Firewall prüfen |
| Daten nach Update weg | Der Ordner `data/` wurde nicht eingebunden. `volumes:` in der Compose-Datei prüfen |
| Zeitstempel falsch | `TZ` in der Compose-Datei anpassen |

---

## Datenschutz

Die Anwendung arbeitet vollständig lokal. Keine Verbindung zu fremden
Servern, keine Registrierung, keine Übermittlung von Nutzungsdaten. Alle
Daten verbleiben im Ordner `data/` auf dem eigenen Gerät.

Einzige Ausnahme: Wird ein Lizenzschlüssel eingegeben, wird dieser einmalig
zur Prüfung übermittelt.
