# Charge@Home Billing Engine
### Produktspezifikation & Funktionsübersicht
**Version 1.0 · August 2026 · Status: Entwurf für Entwicklungsstart**

---

## 1. Produktüberblick

Charge@Home ist eine modulare, selbstgehostete Software zur revisionssicheren Erfassung und Abrechnung von privaten Wallbox-Ladevorgängen für dienstlich veranlasste Fahrten mit dem Privat- oder Firmenfahrzeug. Die Anwendung läuft vollständig lokal (Docker-Container, keine Cloud-Abhängigkeit) und deckt die drei in Deutschland relevanten Abrechnungsszenarien für Elektrofahrzeuge ab.

**Kernversprechen:**
- **100 % lokal** – keine Cloud-Anmeldung, kein Abo, keine Datenweitergabe an Dritte.
- **Native Loxone-Integration** – direkte OCPP-Anbindung an den Loxone Miniserver, ohne zusätzlichen Cloud-Dienst eines Drittanbieters (siehe Abschnitt 4.2).
- **Alle drei Abrechnungsfälle in einem System** – Firmenwagen mit 1-%-Regelung, Firmenwagen mit Fahrtenbuch, sowie privates Fahrzeug mit Car-Allowance/Kilometerpauschale.
- **Belegqualität statt Excel-Schätzung** – automatisch erzeugte, prüfungssichere PDF-Nachweise statt manueller Zusammenstellung.

---

## 2. Zielgruppe & Einsatzszenarien

Die Anwendung ist für Einzelnutzer, kleine Teams und Unternehmen mit Außendienst-/Vielfahrer-Mitarbeitenden gedacht, die ihr Elektrofahrzeug zu Hause laden und die Ladekosten gegenüber dem Arbeitgeber oder dem Finanzamt dokumentieren müssen. Die Software unterscheidet drei Konfigurationen, zwischen denen pro Nutzer:in gewählt wird:

| | **Fall A** | **Fall B** | **Fall C** |
|---|---|---|---|
| Bezeichnung | 1-%-Regelung | Fahrtenbuchmethode | Car Allowance / privater Pkw |
| Fahrzeug | Firmenwagen (Arbeitgeber) | Firmenwagen (Arbeitgeber) | Privateigentum/Leasing des Mitarbeitenden |
| Rechtsgrundlage Ladestrom | § 3 Nr. 50 EStG (BMF-Schreiben 11.11.2025) | identisch zu Fall A | kein steuerfreier Auslagenersatz auf Ladestrom; Kostennachweis gegenüber Arbeitgeber und/oder Kilometerpauschale gegenüber Finanzamt |
| Relevante Module | Ladestrom-Modul | Ladestrom-Modul | Ladestrom-Modul **+** Fahrtkosten-Modul |
| Beleg-Rechtstext | Steuerfreier Auslagenersatz | Steuerfreier Auslagenersatz | Neutraler Kostennachweis |

Alle drei Fälle nutzen dieselbe technische Grundlage; nur Belegtext, Rechenlogik und aktivierte Module unterscheiden sich je nach gewählter Konfiguration.

---

## 3. Architekturprinzip

- **Deployment:** Einzelner Docker-Container (Docker Compose), Ports 8501 (Web-UI) und 9000 (OCPP-WebSocket).
- **Datenhaltung:** Lokale SQLite-Datenbank mit WAL-Modus, Append-only-Änderungsprotokoll für Nachvollziehbarkeit.
- **Dateneingang:** Drei parallele, austauschbare Eingangskanäle (siehe Modul 4.1–4.2), die alle in dasselbe einheitliche Datenmodell schreiben.
- **Frontend:** Streamlit-basierte Weboberfläche zur Verwaltung, Klassifizierung und Auswertung.
- **PDF-Erzeugung:** ReportLab, mit fallabhängigen Textbausteinen.

---

## 4. Module im Detail

### 4.1 Modul: Ladestrom-Erfassung (Kernmodul, für alle drei Fälle)

**Inhalt:**
- Erfassung einzelner Ladesessions mit Start-/Endzeitpunkt, Zählerstand (Wh), berechneter Netto-kWh, zugeordneter Wallbox und ggf. RFID-Tag.
- Freies, monatsvariables Preisfeld pro kWh (Default: 0,34 €/kWh als BMF-Referenzwert 2026, frei überschreibbar für individuellen Stromtarif oder dynamische Tarife).
- Drei Eingangswege in ein gemeinsames Datenmodell:
  1. **OCPP 1.6-J Live-Anbindung** (Central-System-Rolle, python-ocpp) – Echtzeit-Erfassung von BootNotification, Authorize, Start-/StopTransaction, MeterValues.
  2. **Native Loxone-Integration** – siehe 4.2.
  3. **CSV-/Log-Import** – manueller oder automatisierter Import von Wallbox-Exportdateien (z. B. monatlicher Loxone-Mailexport) als Zero-Config-Fallback ohne Netzwerkkonfiguration.
- Automatische Erkennung von Anomalien: unvollständige Transaktionen, Zählerüberlauf/-tausch, doppelte Sessions.
- Optionales PV-Modus-Feld pro Session (nicht verpflichtend, siehe Abschnitt 4.4).

**Nutzung:** Nutzer:innen sehen eine chronologische Liste aller Ladesessions, können sie filtern (Zeitraum, Wallbox, ggf. Dienst/Privat bei Fall A/B) und für die Belegerstellung freigeben.

---

### 4.2 Modul: Native Loxone-Integration (Alleinstellungsmerkmal)

**Inhalt:**
- Direkte OCPP-1.6-J-Anbindung der Loxone Wallbox an den integrierten Central-System-Server der Anwendung (Loxone unterstützt OCPP nativ seit Config-Version 15.1).
- **Kein Cloud-Dienst eines Drittanbieters erforderlich** – im Unterschied zu allen bekannten kommerziellen Wettbewerbslösungen (z. B. ChargeReport, MOBIKO, MENNEKES ativo), die sämtlich über eine Hersteller- oder Anbieter-Cloud laufen.
- Die Ladedaten verbleiben durchgehend im lokalen Netzwerk des Nutzers; die Verbindung Loxone-Miniserver ↔ Charge@Home-Server läuft direkt (ws:// bzw. wss:// im Heimnetz).
- Statusanzeige pro Wallbox in Echtzeit (Laden / Bereit / Fehler / Offline).

**Nutzung:** Einmalige Konfiguration der Backend-Adresse in Loxone Config (WebSocket-URL des Charge@Home-Servers); danach vollautomatische, laufende Erfassung ohne manuellen Export-Schritt. Dies ist der empfohlene Weg für alle Nutzer:innen mit Loxone-Wallbox und Netzwerkzugriff auf den Miniserver.

---

### 4.3 Modul: Fahrtkosten / Kilometerpauschale (nur Fall C)

**Inhalt:**
- Erfassung einzelner Dienstfahrten: Datum, Start-Adresse, Ziel-Adresse (Distanzberechnung z. B. über lokal betreibbaren Routendienst), Anlass, gefahrene Kilometer.
- Wahlweise Satz pro Fahrt: 0,15 €/km, 0,30 €/km oder frei editierbar (abhängig von der individuellen Arbeitgeber-Vereinbarung).
- Checkbox-Option "keine Arbeitgeber-Erstattung" für Nutzer:innen ohne jegliche Kilometererstattung – in diesem Fall automatische Berechnung der vollen 0,30 €/km als Werbungskosten-Grundlage.
- Automatische Differenzberechnung: (0,30 € − gewählter Arbeitgeber-Satz) × km = Werbungskosten-Vorschlag für die Steuererklärung.

**Zwei getrennte PDF-Exporte:**
1. **Arbeitgeber-Beleg** (frei wählbarer Zeitraum): Fahrtenliste mit gewähltem Satz, Gesamtsumme, Unterschriftenfeld.
2. **Finanzamt-Jahresexport**: vollständige Fahrtenliste zu 0,30 €/km, abzüglich bereits erhaltener Arbeitgeber-Erstattung, verbleibender Differenzbetrag als Vorschlag für Anlage N, Zeile 71.

**Nutzung:** Läuft unabhängig vom Ladestrom-Modul; eine Fahrt muss keiner konkreten Ladesession zugeordnet werden.

---

### 4.4 Modul: Beleg- und PDF-Erstellung

**Inhalt:**
- A4-Belegvorlage mit Kopfdaten (Name, Anschrift, Kennzeichen, Arbeitgeberanschrift), Messbasis (Zählerquelle), Sitzungstabelle, Summenblock, Unterschriftenfeld.
- **Fallabhängiger Rechtstext:**
  - Fall A/B: Verweis auf steuerfreien Auslagenersatz nach § 3 Nr. 50 EStG.
  - Fall C: neutrale Formulierung ("Kostennachweis zur Erstattung durch den Arbeitgeber"), ohne Steuerfreiheits-Anspruch.
- Optionaler Parallel-Vergleich Pauschale (0,34 €/kWh) vs. individueller Vertragstarif mit Anzeige, welche Methode im jeweiligen Abrechnungszeitraum günstiger gewesen wäre.
- Fortlaufende Belegnummer, Erstellungsdatum, optionale Prüfsumme/QR-Code zur nachträglichen Manipulationserkennung.

**Nutzung:** Ein-Klick-Erzeugung für einen gewählten Zeitraum, Download oder direkter Mailversand an Reisekostenstelle.

---

### 4.5 Modul: Auswertung & Dashboard

**Inhalt:**
- Status-Icons je Wallbox (Laden / Bereit / Fehler / Offline).
- Balkendiagramme: kWh bzw. Kosten pro Monat, gruppiert nach Wallbox oder Abrechnungsfall.
- Verlaufsdiagramme: kumulierter Energieverbrauch, Kostenentwicklung, Vorjahresvergleich.
- Filterbare Tabellenansicht mit Kennzahlen (Ø-Preis/kWh, Gesamtkosten, Sitzungsanzahl).

**Nutzung:** Zentrale Übersicht für Monatsauswahl, Plausibilisierung und schnelle Fehlererkennung (z. B. auffällig hoher Verbrauch).

---

### 4.6 Modul: Datenhaltung, Sicherheit & Compliance

**Inhalt:**
- SQLite mit WAL-Modus für gleichzeitigen Lese-/Schreibzugriff.
- Append-only-Änderungsprotokoll (wer hat wann welche Session umklassifiziert).
- **GoBD-konforme Aufbewahrung als hartes Requirement:** 10 Jahre Aufbewahrungspflicht (§ 147 AO) – im Unterschied zu Hersteller-Apps, die Sessions häufig nach 12–24 Monaten löschen.
- Backup-Strategie über kontinuierliches WAL-Streaming (z. B. litestream) auf ein zweites Volume.
- Kein Netzwerkzugriff nach außen erforderlich; optionale TLS-Absicherung (wss://) bei Fernzugriff über Reverse Proxy.

**Nutzung:** Läuft im Hintergrund, ohne Nutzerinteraktion; Datenexport (ZIP/CSV) jederzeit über die Weboberfläche möglich.

---

## 5. Abgrenzung zu bestehenden Lösungen

| | Charge@Home | Kommerzielle Anbieter (z. B. ChargeReport, MOBIKO) |
|---|---|---|
| Hosting | Selbstgehostet, lokal | Cloud/SaaS |
| Kosten | Einmalig, keine Abo-Gebühr | Monatliches Abo pro Nutzer |
| Loxone-Anbindung | Direkt, ohne Cloud-Zwischenschicht | Über Hersteller-Cloud/OAuth |
| Abgedeckte Fälle | A, B **und** C | Ausschließlich Fall A/B (Dienstwagen) |
| Datenhoheit | Vollständig beim Nutzer | Beim Anbieter (EU-Server) |

---

## 6. Ausbaustufen (Roadmap)

1. **MVP:** Ladestrom-Modul mit CSV-Import (Loxone-Mailexport), einfache Belegerstellung für Fall A/B/C.
2. **Robustheit & Recht:** Zombie-Transaction-Erkennung, GoBD-Änderungsprotokoll, Backup-Automatisierung, Fahrtkosten-Modul.
3. **Komfort & Integration:** OCPP-Central-System (Loxone + Drittanbieter-Wallboxen live), Pauschale-vs-Real-Vergleichsrechner, DATEV-Export, Dashboard-Erweiterung.
4. **Release-Paket:** Docker-Hardening, Mehrbenutzerfähigkeit (optional), Setup-Dokumentation je Wallbox-Hersteller.

---

## 7. Technischer Stack (Kurzüberblick)

Python (AsyncIO, python-ocpp), SQLite, Streamlit, ReportLab, Docker Compose. Details und Alternativen-Abwägung (u. a. gegenüber C#/.NET) sind im begleitenden Architektur-Review dokumentiert.
