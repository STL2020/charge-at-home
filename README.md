# eCharge@Home

**Ladeabrechnung und Fahrtenbuch für Dienstfahrten mit dem eigenen
Elektrofahrzeug.**

Wer sein privates E-Auto dienstlich nutzt, kennt das Problem: Der Strom kommt
aus der eigenen Steckdose — und die stellt keine Quittung aus. Der
Hausstromzähler unterscheidet nicht zwischen Waschmaschine und Wallbox.

eCharge@Home erfasst jeden Ladevorgang mit Zählerstand, verbindet ihn mit den
gefahrenen Strecken und erzeugt daraus Eigenbelege für Arbeitgeber und
Finanzamt.

---

## Schnellstart

```bash
mkdir -p ~/echarge && cd ~/echarge
curl -O https://raw.githubusercontent.com/STL2020/echarge-at-home/main/docker-compose.ghcr.yml
docker compose -f docker-compose.ghcr.yml up -d
```

Aufrufen unter `http://<IP-des-Geräts>:8501`

Läuft auf Synology, QNAP, Unraid, Raspberry Pi und jedem System mit Docker.
Abbilder für `amd64` und `arm64`.

---

## Was es kann

- **Ladevorgänge erfassen** — über Loxone-API, OCPP 1.6-J, CSV oder von Hand
- **Fahrtenbuch** mit Zuordnung dienstlich / privat und Kilometerprüfung
- **Eigenbelege als PDF** — Ladestrom und Fahrtkosten, mit Pflichtangaben
- **Steuermodelle vergleichen** — Pauschale, tatsächliche Kosten,
  Auslagenersatz nach § 3 Nr. 50 EStG, Car Allowance
- **Vollkostenrechnung** — Leasing, Versicherung, Wartung gegen Erstattung
  und Steuervorteil

## Wallboxen

Herstellerunabhängig über **OCPP 1.6-J**: Easee · go-e · KEBA · Alfen · ABL ·
Zaptec · Webasto · Wallbox Pulsar · Mennekes · Compleo · Vestel · Autel ·
Heidelberg

Loxone-Wallboxen werden unmittelbar über den Miniserver angebunden.

Für LoxBerry gibt es ein **kostenloses OCPP-Plugin**, das Ladevorgänge rund
um die Uhr entgegennimmt und zusätzlich über MQTT bereitstellt.

---

## Ihre Daten bleiben bei Ihnen

Vollständig lokal. Keine Cloud, keine Registrierung, keine Übermittlung von
Nutzungsdaten. Ein Fahrtenbuch enthält, wo Sie wann waren und warum — das
gehört nirgendwo anders hin.

> **Hinweis:** Die Anwendung hat keine eigene Benutzeranmeldung und ist für
> den Betrieb im eigenen Netz ausgelegt. Für den Zugriff von unterwegs
> eignet sich ein VPN. Wer sie dennoch über das Internet erreichbar macht,
> trägt die Verantwortung für Absicherung und Zugriffsschutz.

---

## Demo und Vollversion

| | Demo | Vollversion |
|---|---|---|
| Ladevorgänge und Fahrten | unbegrenzt | unbegrenzt |
| Auswertungen | vollständig | vollständig |
| Belege | mit Wasserzeichen | ohne |
| Wallbox über Loxone-API | eine | mehrere |
| OCPP-Server für andere Hersteller | — | ✓ |
| BMW CarData | — | ✓ |

Die Demo ist zeitlich unbegrenzt.

**[Vollversion bei Payhip →](https://payhip.com/)**

---

## Dokumentation

Im Ordner `doku/`:

- **Schnellstart** — in zehn Minuten zum ersten Beleg
- **Benutzerhandbuch** — 24 Seiten, jede Funktion einzeln
- **Installationsanleitung** — Windows, macOS, Linux, Docker, NAS

---

## Rechtliches

Die Anwendung erzeugt Aufzeichnungen nach den üblichen Anforderungen. Ob
diese anerkannt werden, entscheidet das zuständige Finanzamt beziehungsweise
die Reisekostenstelle des Arbeitgebers.

Für die MID-Konformität der eingesetzten Wallbox ist der Betreiber
verantwortlich. Die Anwendung liest die übermittelten Zählerstände aus und
stellt sie dar; eine messtechnische Prüfung findet nicht statt.

Einzelheiten in `LIZENZ.txt` und im Reiter **Rechtliches**.

---

**[Löwemann IT Consulting](https://www.loewemann.com)** · Remagen
