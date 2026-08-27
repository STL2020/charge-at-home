"""
eCharge@Home Billing Engine — Flask-Haupteinstiegspunkt.

Ersetzt die urspruengliche Streamlit-Variante (Change Request, siehe
Pflichtenheft-Changelog). Grund: Streamlit erlaubte keine zuverlaessige,
millimetergenaue Kontrolle ueber das bereits abgenommene Mockup-Design
(native Widgets liessen sich nur unvollstaendig per CSS ueberschreiben).

Architektur unveraendert nach § 8.1: Diese Datei ist die Presentation
Layer, greift ausschliesslich ueber services/* auf die Datenbank zu.
"""

import io
import json
import os
from datetime import datetime

from flask import Flask, render_template, request, jsonify, send_file

from services.db_service import init_db, get_connection, write_audit_log
import services.db_service as db_service  # Alias für direkten Zugriff in Admin-Routen
from services.i18n_service import get_all_translations, SUPPORTED_LANGUAGES
from services.license_service import is_demo, activate_license as check_license_key, session_limit_reached
from services import license_service
from services import compliance_service
from services import backup_service
from services import ocpp_port_service
from services import edition_service
from services import payhip_service
from services import demodaten_service
from services import extern_ocpp_service
from services import import_service, billing_service, pdf_service, geocoding_service, trip_service
from services import decision_service
from services import cardata_auth_service, cardata_service, cardata_archiv_service
from services import crypto_service, ocpp_service, loxone_api_service, analytics_service, loxone_stats_import_service, loxone_wallbox2_service, event_log_service, loxone_ftp_service, loxone_log_import_service, ocpp_log_service
from repositories import ocpp_client_repository

from repositories import wallbox_repository, session_repository, trip_repository, person_repository, loxone_config_repository, document_repository, settings_repository
from repositories import pkw_repository
from repositories import vehicle_repository
from repositories import bmw_trip_repository

app = Flask(__name__)

# ─── Globaler Error-Handler: IMMER JSON zurückgeben, nie HTML ─────────────────
# Flask gibt bei unbehandelten Fehlern standardmäßig eine HTML-Fehlerseite zurück
# (<!doctype html>...) die das Frontend nicht als JSON parsen kann. Dieser Handler
# fängt alle Fehler ab und gibt immer ein JSON-Objekt zurück.
@app.errorhandler(Exception)
def handle_any_exception(exc):
    import traceback
    tb = traceback.format_exc()
    app.logger.error(f"Unhandled exception: {exc}\n{tb}")
    return jsonify({"error": str(exc), "type": type(exc).__name__}), 500

@app.errorhandler(404)
def handle_404(exc):
    if request.path.startswith("/api/"):
        return jsonify({"error": "not_found", "path": request.path}), 404
    # Für nicht-API-Routen: normale 404-Seite oder zur App weiterleiten
    return jsonify({"error": "not_found"}), 404

PFLICHTENHEFT_VERSION = "13.4"

# Fassung, die dem Anwender gezeigt wird. Die Pflichtenheft-Nummer daneben ist
# die interne Baunummer — beide zusammen machen Rückfragen eindeutig.
RELEASE_VERSION = "1.0.0"

# Interne Sprint-Status-Uebersicht (temporaer, kein permanenter Produktbestandteil,
# siehe Pflichtenheft § 14). Spiegelt den tatsaechlichen Umsetzungsstand der FA-IDs
# aus dem Pflichtenheft wider - manuell nachgefuehrt bei Abschluss eines Sprints.
PROJECT_STATUS = [
    {"sprint": 0, "id": "FA-SYS-01", "modul": "Fundament", "text": "Mehrsprachigkeit DE/EN", "status": "fertig", "view": "setup"},
    {"sprint": 0, "id": "FA-SYS-02", "modul": "Fundament", "text": "Hell-/Dunkel-Modus", "status": "fertig", "view": "setup"},
    {"sprint": 0, "id": "FA-SYS-03", "modul": "Fundament", "text": "Demo-/Lizenzmodus", "status": "fertig", "view": "einstellungen"},
    {"sprint": 0, "id": "FA-SYS-04", "modul": "Fundament", "text": "Setup: Abrechnungsfall A/B/C", "status": "fertig", "view": "setup"},
    {"sprint": 0, "id": "FA-SYS-05", "modul": "Fundament", "text": "ZIP + Start-Skript", "status": "fertig", "view": None},
    {"sprint": 1, "id": "FA-LS-01", "modul": "Ladestrom-Kern", "text": "CSV-Import Ladesessions", "status": "fertig", "view": "ladesessions"},
    {"sprint": 1, "id": "FA-LS-02", "modul": "Ladestrom-Kern", "text": "Manuelle Session-Erfassung", "status": "fertig", "view": "ladesessions"},
    {"sprint": 1, "id": "FA-LS-03", "modul": "Ladestrom-Kern", "text": "Sessionliste mit Filter", "status": "fertig", "view": "ladesessions"},
    {"sprint": 1, "id": "FA-LS-04", "modul": "Ladestrom-Kern", "text": "Dienst-/Privat-Klassifizierung", "status": "fertig", "view": "ladesessions"},
    {"sprint": 1, "id": "FA-LS-05", "modul": "Ladestrom-Kern", "text": "Monatsvariabler kWh-Preis", "status": "fertig", "view": "einstellungen"},
    {"sprint": 1, "id": "FA-LS-06", "modul": "Ladestrom-Kern", "text": "PDF-Belegerzeugung", "status": "fertig", "view": "belege"},
    {"sprint": 1, "id": "FA-LS-11", "modul": "Ladestrom-Kern", "text": "Session bearbeiten/löschen", "status": "fertig", "view": "ladesessions"},
    {"sprint": 1, "id": "FA-LS-12", "modul": "Ladestrom-Kern", "text": "Zählerstand-Autofill", "status": "fertig", "view": "ladesessions"},
    {"sprint": 2, "id": "FA-FK-01", "modul": "Fahrtkosten", "text": "Fahrt-Erfassung", "status": "fertig", "view": "fahrten"},
    {"sprint": 2, "id": "FA-FK-02", "modul": "Fahrtkosten", "text": "Distanzberechnung (Luftlinie, Nominatim)", "status": "fertig", "view": "fahrten"},
    {"sprint": 2, "id": "FA-FK-04", "modul": "Fahrtkosten", "text": "Satzauswahl (0,15/0,30/frei)", "status": "fertig", "view": "fahrten"},
    {"sprint": 2, "id": "FA-FK-05", "modul": "Fahrtkosten", "text": "Werbungskosten-Differenz", "status": "fertig", "view": "fahrten"},
    {"sprint": 2, "id": "FA-FK-06", "modul": "Fahrtkosten", "text": "PDF Arbeitgeber-Beleg", "status": "fertig", "view": "belege"},
    {"sprint": 2, "id": "FA-FK-07", "modul": "Fahrtkosten", "text": "PDF Finanzamt-Jahresexport", "status": "fertig", "view": "belege"},
    {"sprint": 2, "id": "FA-PERS-01", "modul": "Personen-Stammdaten", "text": "Personen anlegen/bearbeiten/löschen", "status": "fertig", "view": "einstellungen"},
    {"sprint": 2, "id": "FA-PERS-02", "modul": "Personen-Stammdaten", "text": "Dynamische Personen-Felder je Beleg", "status": "fertig", "view": "belege"},
    {"sprint": 3, "id": "FA-LS-08", "modul": "OCPP-Live", "text": "OCPP-Central-System (Geschäftslogik getestet, Transport ungetestet, keine MeterValues — Loxone-seitige Einschränkung)", "status": "fertig", "view": "wallbox"},
    {"sprint": 3, "id": "FA-LS-09", "modul": "OCPP-Live", "text": "Live-Statusanzeige je Wallbox", "status": "fertig", "view": "dashboard"},
    {"sprint": 3, "id": "FA-LS-10", "modul": "OCPP-Live", "text": "Direkte Loxone-API (Wallbox2, Vc/Cac/Cp für Live-Anzeige)", "status": "fertig", "view": "wallbox"},
    {"sprint": 3, "id": "FA-LS-13", "modul": "OCPP-Live", "text": "Log-Datei-Import als alleinige Session-Quelle (jeder Poll-Zyklus, duplikatsicher, gegen echte Hardware getestet)", "status": "fertig", "view": "wallbox"},
    {"sprint": 3, "id": "FA-LS-14", "modul": "OCPP-Live", "text": "Dashboard-Live-Werte automatische Aktualisierung (10s-Intervall)", "status": "fertig", "view": "dashboard"},
    {"sprint": 4, "id": "FA-DASH-01", "modul": "Auswertung", "text": "Balkendiagramm kWh/Monat", "status": "fertig", "view": "auswertung"},
    {"sprint": 4, "id": "FA-DASH-02", "modul": "Auswertung", "text": "Verlaufsdiagramm Kosten", "status": "fertig", "view": "auswertung"},
    {"sprint": 4, "id": "FA-DASH-03", "modul": "Auswertung", "text": "Pauschale-vs-Real-Vergleichsrechner", "status": "fertig", "view": "auswertung"},
    {"sprint": 4, "id": "FA-DASH-04", "modul": "Auswertung", "text": "Kennzahlentabelle", "status": "fertig", "view": "auswertung"},
    {"sprint": 4, "id": "FA-LS-BMW-01", "modul": "Historische Daten", "text": "BMW-Ladehistorie-Import (XLSX), eigenständig auf Ladesessions-Seite, duplikatsicher, gegen echte Datei getestet", "status": "fertig", "view": "ladesessions"},
    {"sprint": 4, "id": "FA-LS-BMW-02", "modul": "Historische Daten", "text": "Zuhause/Extern-Erkennung (Straßenname-Abgleich, gegen echte Hausnummer-Abweichung getestet), Ausschluss externer Ladungen aus dem Eigenstrom-Beleg", "status": "fertig", "view": "ladesessions"},
    {"sprint": 5, "id": "FA-COMP-01", "modul": "Compliance", "text": "Audit-Log (bereits aktiv genutzt bei Klassifizierung und Ladeort-Änderung)", "status": "fertig", "view": None},
    {"sprint": 5, "id": "FA-COMP-02", "modul": "Compliance", "text": "Zombie-Session-Erkennung", "status": "fertig", "view": "ladesessions"},
    {"sprint": 5, "id": "FA-COMP-03", "modul": "Compliance", "text": "Zählerüberlauf-Erkennung", "status": "fertig", "view": "ladesessions"},
    {"sprint": 5, "id": "FA-COMP-04", "modul": "Compliance", "text": "Automatisiertes Backup", "status": "fertig", "view": None},
    {"sprint": 5, "id": "FA-COMP-05", "modul": "Compliance", "text": "Datenexport ZIP/CSV", "status": "fertig", "view": "einstellungen"},
    {"sprint": 6, "id": "FA-UX-01", "modul": "Menüstruktur", "text": "Setup als einmaliger Erst-Assistent statt Dauermenüpunkt (Navigation wird ausgeblendet, bis ein Nutzer angelegt ist)", "status": "fertig", "view": "setup"},
    {"sprint": 6, "id": "FA-UX-02", "modul": "Menüstruktur", "text": "Trennung 'Wallbox' (Verbindung, Bausteine, Sync) und 'Einstellungen' (Preis, Adresse, Personen, Lizenz)", "status": "fertig", "view": "wallbox"},
    {"sprint": 6, "id": "FA-UX-03", "modul": "Menüstruktur", "text": "Sofort-Validierung der Wallbox2-UUID im Formular (prüft Cp/Vc/Cac direkt bei Auswahl, statt erst später im Protokoll aufzufallen)", "status": "fertig", "view": "wallbox"},
    {"sprint": 6, "id": "FA-LOX-STATS-01", "modul": "Historische Daten", "text": "Statistikdatei (Kanal _2) als Lücken-Erkennung neben Log-Import — HTTP-Erreichbarkeit noch gegen echten Miniserver zu prüfen", "status": "geplant", "view": "wallbox"},
    {"sprint": 6, "id": "FA-OCPP-DIAG-01", "modul": "Diagnose", "text": "OCPP-Diagnose: persistente Logdatei (data/ocpp_raw.log) + Nachrichtentyp-Zählung, zeigt konkret welche Typen (nie) ankommen", "status": "fertig", "view": "protokoll"},
    {"sprint": 6, "id": "FA-LS-06-V2", "modul": "Ladestrom-Kern", "text": "Ladebeleg-Layout auf neue 'eCHARGE FLEET'-Referenzvorlage umgestellt (Protokoll-Tabelle, Zusammenfassung, Fußbereich); Rechtstext-Absatz an denselben Schalter wie FA-LS-BMF-01 gekoppelt, löst den Konflikt mit FA-LS-07 auf, ohne es zu überschreiben", "status": "fertig", "view": "belege"},
    {"sprint": 7, "id": "FA-LS-06-V3", "modul": "Ladestrom-Kern", "text": "Ladebeleg auf 'eCharge@Home'-Referenz aktualisiert (KUNDE/ABRECHNUNGSEMPFÄNGER, Kunden-ID, Dienstfahrzeug-Feld, MID-konform bestätigt lt. Auftraggeber), Formatierungsbug bei Tausendertrennzeichen behoben", "status": "fertig", "view": "belege"},
    {"sprint": 7, "id": "FA-OCPP-CLIENT-01", "modul": "OCPP", "text": "Neuer OCPP-CLIENT-Modus (zusätzlich zum bestehenden Server-Modus): Wallbox kann jetzt als Charge Point zu einem externen OCPP-Dienst verbinden und reicht die zuverlässig per Log-Import erfassten Sessions als echte StartTransaction/MeterValues/StopTransaction weiter — löst das Problem, dass Loxone selbst diese Daten nie sendet", "status": "fertig", "view": "wallbox"},
    # ── Aktuelle Roadmap (Master-Spezifikation, Sprints 1–7) ──────────────
    {"sprint": 1, "id": "S1-01", "modul": "Stabilität", "text": "Router-Bugfix: Render-Loop bei 'Neue Fahrt erfassen'", "status": "fertig", "view": "dashboard"},
    {"sprint": 1, "id": "S1-02", "modul": "Branding", "text": "Splashscreen 5 s, Logo (E-Auto + Stecker + Haus), Claim, Footer", "status": "fertig", "view": None},
    {"sprint": 1, "id": "S1-03", "modul": "Branding", "text": "Markenname eCharge@Home in der gesamten Anwendung", "status": "fertig", "view": None},
    {"sprint": 1, "id": "S1-04", "modul": "Belege", "text": "PDF-Layout: Überlappungen, Spaltenbreiten, Linksbündigkeit, Umlaute", "status": "fertig", "view": "belege"},
    {"sprint": 1, "id": "S1-05", "modul": "Belege", "text": "Produktlogo als Vektor in allen PDF-Belegen", "status": "fertig", "view": "belege"},
    {"sprint": 2, "id": "S2-01", "modul": "UI-Harmonisierung", "text": "Filterleiste Auswertung Stromkosten wie im Fahrten-Modul", "status": "fertig", "view": "auswertung"},
    {"sprint": 2, "id": "S2-02", "modul": "UI-Harmonisierung", "text": "Protokoll: eine Tabelle mit Quell-Dropdown statt getrennter Reiter", "status": "fertig", "view": "protokoll"},
    {"sprint": 2, "id": "S2-03", "modul": "UI-Harmonisierung", "text": "Vergleichskarten C1/C2/A/B: gleiche Höhe + Badge 'Empfohlene Option'", "status": "fertig", "view": "konfigurator"},
    {"sprint": 3, "id": "S3-01", "modul": "Energiepreise", "text": "Mischkalkulation Heimtarif / DC-Tarif / Lade-Split", "status": "fertig", "view": "konfigurator"},
    {"sprint": 3, "id": "S3-02", "modul": "Energiepreise", "text": "Button 'Marktpreise laden' mit Referenzwerten (ohne externe API)", "status": "fertig", "view": "konfigurator"},
    {"sprint": 4, "id": "S4-01", "modul": "Steuer-Hilfe", "text": "Info-Drawer zu C1, C2, A und B mit steuerlicher Einordnung", "status": "fertig", "view": "konfigurator"},
    {"sprint": 5, "id": "S5-01", "modul": "Wallbox", "text": "OCPP: Wirkleistung kW/A, MID-Zählerstand, Tagesenergie, Peak", "status": "fertig", "view": "wallbox"},
    {"sprint": 5, "id": "S5-02", "modul": "Wallbox", "text": "Loxone: Verbindungsdaten cachen für 'Struktur laden'", "status": "fertig", "view": "wallbox"},
    {"sprint": 6, "id": "S6-01", "modul": "BMW Telematik", "text": "BMW CarData: Anmeldung per Device Code Flow (offizielle Schnittstelle)", "status": "fertig", "view": None},
    {"sprint": 6, "id": "S6-02", "modul": "BMW Telematik", "text": "Tabelle bmw_trips + Repository", "status": "fertig", "view": None},
    {"sprint": 6, "id": "S6-03", "modul": "BMW Telematik", "text": "CarData-Routen: Anmeldung, Abruf, Automatik, Klassifizierung", "status": "fertig", "view": None},
    {"sprint": 6, "id": "S6-04", "modul": "BMW Telematik", "text": "UI: CarData-Anmeldung, Abruf, 1-Klick Dienstlich/Privat", "status": "fertig", "view": "einstellungen"},
    {"sprint": 7, "id": "S7-01", "modul": "Release", "text": "Offline-Hilfedatei help.html mit Header-Link", "status": "fertig", "view": None},
    {"sprint": 7, "id": "S7-02", "modul": "Release", "text": "Markenrechtshinweis und Apache-2.0-Copyright im Impressum", "status": "fertig", "view": None},
    {"sprint": 7, "id": "S7-03", "modul": "Release", "text": "Einsprachig Deutsch — englische Fassung entfällt (Wunschkriterium)", "status": "fertig"},
    {"sprint": 7, "id": "S7-04", "modul": "Release", "text": "Feld 'Gültig ab' fest auf 01.01.2026", "status": "fertig", "view": "einstellungen"},
    {"sprint": 7, "id": "S7-05", "modul": "Release", "text": "Payhip-Lizenzlogik Free vs. Pro", "status": "fertig", "view": "einstellungen"},

    {"sprint": 8, "id": "S8-01", "modul": "BMW Telematik", "text": "CarData-Stream: Wiederaufnahme nach Container-Neustart (fehlte bisher — Einstellung blieb 'an', Hintergrund-Thread lief nach Neustart aber nicht mehr)", "status": "fertig", "view": None},
    {"sprint": 8, "id": "S8-02", "modul": "BMW Telematik", "text": "CarData-Stream: Host/Port einstellbar statt fest im Code (Streaming-Zugangsdaten aus dem BMW-Portal), Statusanzeige meldet nach 45s ohne Verbindung einen echten Fehler statt endlos 'wird aufgebaut'", "status": "fertig", "view": "einstellungen"},
    {"sprint": 8, "id": "S8-03", "modul": "Diagnose", "text": "Eigenstaendiges Diagnose-Werkzeug mqtt_diagnose.py: separate Anmeldung mit explizit angefordertem Streaming-Scope, protokolliert jede Verbindungsstufe (Connect/Subscribe/Nachricht) einzeln in Datei und auf dem Bildschirm", "status": "fertig", "view": None},
    {"sprint": 8, "id": "S8-04", "modul": "BMW Telematik", "text": "CarData-Stream: SUBACK-Pruefung nachgeruestet — subscribe() wurde aufgerufen, ohne je die Antwort von BMW auszuwerten; 'Verbunden' konnte also bei im Stillen abgelehntem Thema-Abonnement stehen bleiben, ohne dass Fahrten je ankamen. Status unterscheidet jetzt 'verbunden, Abonnement offen/bestaetigt/abgelehnt'", "status": "fertig", "view": "einstellungen"},
    {"sprint": 8, "id": "S8-05", "modul": "Diagnose", "text": "Live-Protokoll der Stream-Verbindung direkt in der App (Einstellungen -> BMW), kein Terminal/Putty mehr noetig: jede Verbindungsstufe (Connect/Subscribe/Nachricht/Fehler) im Ringpuffer, automatische Aktualisierung alle 5s nach Muster der bestehenden OCPP-Rohdaten-Anzeige", "status": "fertig", "view": "einstellungen"},
    {"sprint": 8, "id": "S8-06", "modul": "BMW Telematik", "text": "CarData-Stream: erzwungene Token-Erneuerung nach wiederholten Verbindungsfehlschlaegen — beobachtet in Produktion (echtes Protokoll): nach 'Normal disconnection' (vermutlich zweite Sitzung mit gleichem Konto) wurden alle Rekonnektversuche mit dem als 'noch gueltig' gebuchten, tatsaechlich aber toten Token wiederholt, endlos 'Bad user name or password'. Ab dem 2. Fehlschlag in Folge wird jetzt zwangsweise erneuert.", "status": "fertig", "view": "einstellungen"},
    {"sprint": 8, "id": "S8-07", "modul": "BMW Telematik", "text": "Server-seitige Ausschliesslichkeit Stream vs. periodischer Abruf — bisher nur im Frontend beim Anklicken der Checkbox hergestellt. War 'cardata_auto' aus einer Zeit vor dem Umstieg auf den Stream noch auf '1' gespeichert, liefen nach jedem Neustart BEIDE Mechanismen gleichzeitig: der periodische Abruf erneuert ueber denselben Login regelmaessig Access- und ID-Token und kappt damit vermutlich die laufende Stream-Sitzung (Ursache fuer den in S8-06 behobenen Fehlerfall). Jetzt bei setze_aktiv() UND beim Neustart hart erzwungen.", "status": "fertig", "view": "einstellungen"},

    {"sprint": 9, "id": "S9-01", "modul": "BMW Telematik", "text": "Umbau: BMW-CarData-Verbindung (Client-ID, GCID, Token, Stream) von anwendungsweiten Einstellungen auf je-Fahrzeug-Datensaetze umgestellt (neue Tabelle vehicle_bmw_connections). Mehrere Fahrzeuge koennen jetzt gleichzeitig eigene, unabhaengige BMW-Konten nutzen — cardata_auth_service, cardata_stream_service (mehrere parallele MQTT-Verbindungen), cardata_service und alle Routen (/api/vehicles/<id>/cardata/...) vollstaendig umgeschrieben, mit zwei simulierten Fahrzeugen end-to-end getestet.", "status": "fertig", "view": "fahrzeuge"},
    {"sprint": 9, "id": "S9-02", "modul": "BMW Telematik", "text": "Fahrzeug-Dialog: Umschalter 'Manuell' / 'Aus BMW-Konto importieren' mit vollstaendigem Anmeldefluss (Client-ID -> Geraetecode-Bestaetigung -> Fahrzeugauswahl vom Konto -> Verbindung), Stream-Schalter, Ladehistorie-Import (API + Archiv-ZIP), Live-Protokoll und Verbindung-trennen direkt im Dialog je Fahrzeug.", "status": "fertig", "view": "fahrzeuge"},
    {"sprint": 9, "id": "S9-03", "modul": "Dashboard", "text": "Fahrzeugstatus-Kachel: Reichweite, Ladestand, Kilometerstand, Standort-Link je verbundenem Fahrzeug, mit gezieltem Aktualisieren-Knopf (ein Abruf vom Tageskontingent statt automatischem Dauerabruf).", "status": "fertig", "view": "dashboard"},
    {"sprint": 9, "id": "S9-04", "modul": "Aufraeumen", "text": "Alte globale BMW-Einstellungsseite, Automatik-Fahrten-Ableitung (API-basiert) und toter Code (pruefe_fahrt, rekonstruiere_fahrten, importiere_archiv, Automatik-Timer) vollstaendig entfernt.", "status": "fertig", "view": "einstellungen"},
    {"sprint": 9, "id": "S9-05", "modul": "BMW Telematik", "text": "Archiv-Import (ZIP, Ladehistorie als Ladesessions) mit echter Kundendatei end-to-end getestet: 49 Eintraege gelesen, 35 Heimladungen korrekt uebersprungen, 4 externe Schnellladungen mit plausiblen Werten importiert.", "status": "fertig", "view": "fahrzeuge"},
    {"sprint": 9, "id": "S9-06", "modul": "Fahrzeuge", "text": "BUG BEHOBEN: closeVehicleModal() raeumte die Protokoll- und Anmelde-Timer nicht auf. Bei mehrfachem Oeffnen/Anzeigen des Protokolls liefen mehrere 5-Sekunden-Timer gleichzeitig, jeder schrieb wachsende Textmengen ins DOM — nach ein paar Testlaeufen liess das die Seite spuerbar haengen, bis hin zum nicht mehr reagierenden Schliessen-Knopf. Timer werden jetzt beim Oeffnen UND Schliessen bereinigt, Protokollanzeige zusaetzlich auf die letzten 40 Zeilen begrenzt.", "status": "fertig", "view": "fahrzeuge"},
    {"sprint": 9, "id": "S9-07", "modul": "Fahrzeuge", "text": "Link zur BMW-Portal-Anmeldung im Fahrzeug-Dialog korrigiert auf https://www.bmw.de/de-de/mybmw/vehicle-overview (Fahrzeug waehlen -> BMW CarData -> Technical Access) statt des alten CarData-Direktlinks.", "status": "fertig", "view": "fahrzeuge"},
    {"sprint": 9, "id": "S9-08", "modul": "Einstellungen", "text": "BUG BEHOBEN: showSettingsTab() referenzierte '_streamLogAutoRefreshTimer', eine Variable, die beim Entfernen der alten globalen BMW-Protokollanzeige geloescht wurde. Jeder Tabwechsel ausser 'BMW' brach dadurch mit einem ReferenceError ab, bevor der neue Tab sichtbar geschaltet wurde — alle Bereiche ausser BMW blieben leer. node --check erkennt das nicht (nur Syntax, keine Variablenpruefung); ein echter no-undef-Lint-Durchlauf (eslint) ist deshalb ab sofort fester Teil der Pruefung vor jeder Auslieferung.", "status": "fertig", "view": "einstellungen"},
    {"sprint": 9, "id": "S9-09", "modul": "Oberflaeche", "text": "Hauptbereich (.topbar, .content) auf max. 1680px begrenzt und zentriert. Auf sehr breiten Monitoren zog sich der Inhalt zuvor ueber die gesamte Fensterbreite; Tabellen ohne Datenzeilen (z.B. Fahrten, Ladevorgaenge) verteilten ihre Spaltenkoepfe dann mit grossen, unschoenen Luecken. Automatisch behoben, keine Aenderung auf normalen/schmalen Fenstern.", "status": "fertig", "view": None},
    {"sprint": 9, "id": "S9-10", "modul": "Ladevorgaenge", "text": "BUG BEHOBEN: Die Ladevorgaenge-Tabelle (12 Spalten) lief ueber ihre zugeteilte Breite hinaus und schob sich sichtbar HINTER das Bearbeiten-Feld rechts daneben, sobald eine Session zum Bearbeiten geoeffnet war — bei Fahrten (9 Spalten) fiel das nicht auf. Listenspalte bekommt jetzt einen eigenen horizontalen Scrollbereich statt ueberzulaufen; wirkt automatisch, kein manueller Splitter noetig.", "status": "fertig", "view": "ladevorgaenge"},
    {"sprint": 9, "id": "S9-11", "modul": "Einstellungen", "text": "Bereich 'BMW CarData' aufgeraeumt und in 'Ladeimport' umbenannt: verwaiste Checkbox 'Archiv-Import bei den Fahrten anzeigen' entfernt (referenzierte eine laengst geloeschte Funktion, Text war seit dem Fahrzeug-Umbau irrefuehrend -- der Archiv-Import lebt jetzt im Fahrzeug-Dialog und hat mit der Fahrten-Ansicht nichts mehr zu tun). Verwaiste Verweiskarte auf die alte globale BMW-Verbindung sowie die nicht mehr zutreffende Vollversions-Sperre fuer diesen Bereich entfernt (Wohnadresse, Strompreise und Heimladungen-Uebernahme sind editionsunabhaengig nutzbar). Uebrig bleiben nur die Einstellungen, die wirklich global sind.", "status": "fertig", "view": "einstellungen"},
    {"sprint": 9, "id": "S9-12", "modul": "BMW Telematik", "text": "WICHTIGE KLARSTELLUNG: Das BMW-Datenarchiv enthaelt nachweislich KEINE Fahrten, nur Ladehistorie (mit Stephans echter Archivdatei bestaetigt: 49 Eintraege, alle Ladevorgaenge, null Fahrten). Ein 'Fahrten aus Archiv importieren'-Knopf ist deshalb technisch nicht umsetzbar. Stattdessen: BUG BEHOBEN — beim Umbau auf je-Fahrzeug-Verbindungen ging die Uebernahme von Kilometerstand, HU-Termin, Service, Bremsfluessigkeit und Reifengroesse aus dem Archiv verloren (importiere_ladehistorie_datei rief lies_fahrzeugdaten() nicht mehr auf). Wieder hergestellt und mit echter Archivdatei bestaetigt (alle 5 Felder korrekt uebernommen).", "status": "fertig", "view": "fahrzeuge"},
    {"sprint": 9, "id": "S9-13", "modul": "BMW Telematik", "text": "Archiv-Import (ZIP-Datei einlesen) bewusst aus der Vollversions-Sperre herausgenommen (neue Pruefung 'bmw_archiv' statt 'bmw'): braucht keine laufende BMW-Anmeldung und kein Tageskontingent, nur eine bereits heruntergeladene Datei — anders als die Live-API-Anbindung. Im Fahrzeug-Dialog jetzt permanent sichtbar und nutzbar, auch ohne vorherige Anmeldung und in jeder Ausgabe.", "status": "fertig", "view": "fahrzeuge"},
    {"sprint": 9, "id": "S9-14", "modul": "Dashboard", "text": "Fahrzeugstatus-Kachel komplett neu gestaltet: volle Breite statt schmaler Mehrspalten-Kacheln, konsolidierte Datenpunkte im etablierten Kachel-Stil (Reichweite, Ladestand, Kilometerstand, naechster Service, Verbrauch, Akkukapazitaet, Akkuzustand, Wochenlaufleistung), zusaetzlich eine eingebettete Kartenvorschau des letzten bekannten Standorts (derselbe kostenlose Google-Maps-Embed wie bei der Fahrten-Routenvorschau, kein API-Key noetig).", "status": "fertig", "view": "dashboard"},

]


def _current_user():
    conn = get_connection()
    try:
        return conn.execute("SELECT * FROM users_config ORDER BY id LIMIT 1").fetchone()
    finally:
        conn.close()


def _resolve_loxone_credentials(data, wallbox_id=None):
    """Ermittelt die Loxone-Zugangsdaten fuer einen Aufruf.

    Vorrang haben Angaben aus dem Aufruf selbst — so laesst sich eine
    Verbindung pruefen, bevor die Wallbox gespeichert ist. Fehlen sie oder
    sind sie unvollstaendig, werden die gespeicherten Daten der Wallbox
    ergaenzt. Beim Passwort ist das der Regelfall: Die Oberflaeche schickt
    es aus Sicherheitsgruenden nicht erneut mit, wenn es unveraendert ist.

    Rueckgabe: (host, benutzer, passwort, fehlertext).
    Ist der Fehlertext gesetzt, sind die uebrigen Werte unbrauchbar.
    """
    data = data or {}
    host = (data.get("loxone_host") or "").strip()
    username = (data.get("loxone_username") or "").strip()
    password = data.get("loxone_password") or ""

    # Gespeicherte Daten heranziehen, wo der Aufruf nichts mitbringt
    if wallbox_id and (not host or not username or not password):
        try:
            wb = wallbox_repository.get_wallbox(int(wallbox_id))
        except (TypeError, ValueError):
            wb = None
        if wb is not None:
            if not host:
                host = (wb["loxone_host"] or "").strip()
            if not username:
                username = (wb["loxone_username"] or "").strip()
            if not password:
                gespeichert = wb["loxone_password_encrypted"] or ""
                password = crypto_service.decrypt(gespeichert) if gespeichert else ""

    if not host:
        return None, None, None, ("Es ist keine Adresse des Miniservers "
                                  "hinterlegt. Bitte IP-Adresse eintragen.")
    if not username or not password:
        return None, None, None, ("Benutzername oder Passwort fehlen. Bitte "
                                  "die Zugangsdaten des Miniservers eintragen.")

    # Schema und abschliessenden Schraegstrich entfernen — beides taucht beim
    # Kopieren aus dem Browser mit auf und liesse jede Anfrage scheitern.
    host = host.replace("http://", "").replace("https://", "").rstrip("/")

    return host, username, password, None


def _row_to_dict(row):
    return dict(row) if row is not None else None


@app.before_request
def _ensure_db():
    init_db()


@app.route("/")
def index():
    user = _current_user()
    user_dict = _row_to_dict(user)

    lang = user_dict["language_pref"] if user_dict else "de"
    theme = user_dict["theme_pref"] if user_dict else "dark"
    license_status = user_dict["license_status"] if user_dict else "demo"
    default_price = user_dict["default_kwh_price"] if user_dict else 0.34

    i18n_blob = {code: get_all_translations(code) for code in SUPPORTED_LANGUAGES}

    return render_template(
        "index.html",
        lang=lang,
        theme=theme,
        user=user_dict,
        is_demo=is_demo(license_status),
        license_label="Demo" if is_demo(license_status) else "Lizenziert",
        default_kwh_price=f"{default_price:.2f}".replace(".", ","),
        pflichtenheft_version=PFLICHTENHEFT_VERSION,
        release_version=RELEASE_VERSION,
        i18n_json=json.dumps(i18n_blob, ensure_ascii=False),
        app_state_json=json.dumps({"user": user_dict}, ensure_ascii=False, default=str),
        project_status=PROJECT_STATUS,
    )


@app.route("/api/setup", methods=["POST"])
def api_setup():
    """FA-SYS-04: Name + Abrechnungsfall speichern (legt Nutzer an oder aktualisiert ihn)."""
    data = request.get_json(force=True)
    name = (data.get("name") or "").strip()
    fall = data.get("abrechnungsfall")
    lang = data.get("language_pref", "de")
    theme = data.get("theme_pref", "dark")

    if not name or fall not in ("A", "B", "C"):
        return jsonify({"error": "invalid_input"}), 400

    user = _current_user()
    conn = get_connection()
    try:
        if user is None:
            conn.execute(
                """INSERT INTO users_config (name, abrechnungsfall, language_pref, theme_pref)
                   VALUES (?, ?, ?, ?)""",
                (name, fall, lang, theme),
            )
        else:
            conn.execute(
                """UPDATE users_config
                   SET name = ?, abrechnungsfall = ?, language_pref = ?, theme_pref = ?
                   WHERE id = ?""",
                (name, fall, lang, theme, user["id"]),
            )
        conn.commit()
    finally:
        conn.close()
    return jsonify({"ok": True})


@app.route("/api/price", methods=["POST"])
def api_price():
    data = request.get_json(force=True)
    price = data.get("default_kwh_price")
    user = _current_user()
    if user is None or price is None:
        return jsonify({"error": "no_user_or_price"}), 400
    conn = get_connection()
    try:
        conn.execute(
            "UPDATE users_config SET default_kwh_price = ? WHERE id = ?",
            (float(price), user["id"]),
        )
        conn.commit()
    finally:
        conn.close()
    return jsonify({"ok": True})


@app.route("/api/license/aktivieren", methods=["POST"])
def api_license_aktivieren():
    """Lizenzschluessel bei Payhip pruefen und speichern."""
    data = request.get_json(force=True) or {}
    ergebnis = payhip_service.aktiviere(str(data.get("key") or ""))
    if ergebnis.get("ok"):
        edition_service.edition.cache_clear()
    return jsonify(ergebnis)


@app.route("/api/license/entfernen", methods=["POST"])
def api_license_entfernen():
    """Lizenz von diesem Rechner loesen — etwa vor einem Rechnerwechsel."""
    payhip_service.entferne()
    edition_service.edition.cache_clear()
    return jsonify({"ok": True})


@app.route("/api/license/status", methods=["GET"])
def api_license_status():
    """Lizenzstatus inkl. verbrauchter Free-Kontingente des laufenden Monats."""
    user = _current_user()
    if user is None:
        return jsonify({"error": "no_user"}), 400
    monat = datetime.now().strftime("%Y-%m")
    conn = get_connection()
    try:
        s_cnt = conn.execute(
            """SELECT COUNT(*) c FROM charging_sessions
               WHERE user_id = ? AND strftime('%Y-%m', start_timestamp) = ?""",
            (user["id"], monat)).fetchone()["c"]
        f_cnt = conn.execute(
            """SELECT COUNT(*) c FROM trips
               WHERE user_id = ? AND strftime('%Y-%m', trip_date) = ?""",
            (user["id"], monat)).fetchone()["c"]
    finally:
        conn.close()
    # Der Funktionsumfang haengt am ausgelieferten Paket, nicht an einem
    # gespeicherten Status — siehe edition_service.
    info = edition_service.limit_info(s_cnt, f_cnt)
    info["lizenz"] = payhip_service.status()
    return jsonify(info)


@app.route("/api/rfid-tags", methods=["GET"])
def api_rfid_tags():
    """Bereits verwendete RFID-Tags, zur Vorschlags-Anzeige (datalist), kein eigenes Repository noetig."""
    user = _current_user()
    if user is None:
        return jsonify({"tags": []})
    conn = get_connection()
    try:
        rows = conn.execute(
            """SELECT DISTINCT rfid_tag FROM charging_sessions
               WHERE user_id = ? AND rfid_tag IS NOT NULL AND rfid_tag != ''
               ORDER BY rfid_tag""",
            (user["id"],),
        ).fetchall()
        return jsonify({"tags": [r["rfid_tag"] for r in rows]})
    finally:
        conn.close()


@app.route("/api/sessions", methods=["GET"])
def api_sessions_list():
    """FA-LS-03: Sessionliste mit Filter (Zeitraum, Wallbox)."""
    user = _current_user()
    if user is None:
        return jsonify({"error": "no_user"}), 400

    period_start = request.args.get("von") or None
    period_end = request.args.get("bis") or None
    wallbox_id = request.args.get("wallbox_id")
    wallbox_id = int(wallbox_id) if wallbox_id else None

    sessions = session_repository.list_sessions(
        user_id=user["id"], period_start=period_start, period_end=period_end, wallbox_id=wallbox_id,
    )
    return jsonify({
        "sessions": [billing_service.session_to_api_dict(s) for s in sessions],
        "show_classification": user["abrechnungsfall"] in ("A", "B"),
    })


@app.route("/api/wallboxes", methods=["GET"])
def api_wallboxes_list():
    return jsonify({"wallboxes": wallbox_repository.list_wallboxes()})


@app.route("/api/sessions/manual", methods=["POST"])
def api_sessions_manual():
    """FA-LS-02: Manuelle Erfassung einer Einzelsession."""
    user = _current_user()
    if user is None:
        return jsonify({"error": "no_user"}), 400

    # Free-Version: Grenze gilt je Kalendermonat und nur fuer NEUE Eintraege —
    # bereits erfasste Daten bleiben vollstaendig nutzbar (Sprint 7).
    monat = datetime.now().strftime("%Y-%m")
    _conn = get_connection()
    try:
        anzahl_monat = _conn.execute(
            """SELECT COUNT(*) c FROM charging_sessions
               WHERE user_id = ? AND strftime('%Y-%m', start_timestamp) = ?""",
            (user["id"], monat)).fetchone()["c"]
    finally:
        _conn.close()
    if license_service.monats_limit_erreicht("session", anzahl_monat, user["license_status"]):
        return jsonify({"error": "free_limit_reached", "art": "session",
                        "limit": license_service.FREE_SESSIONS_PRO_MONAT,
                        "message": (f"Free-Version: maximal "
                                    f"{license_service.FREE_SESSIONS_PRO_MONAT} Ladesessions "
                                    f"pro Monat. Mit einer Pro-Lizenz unbegrenzt.")}), 403

    data = request.get_json(force=True)
    try:
        wallbox_name = data["wallbox"]
        start_ts = data["start"].replace("T", " ") + ":00"
        meter_start = int(data["meter_start"])
        meter_stop = int(data["meter_end"])
    except (KeyError, ValueError):
        return jsonify({"error": "invalid_input"}), 400

    if meter_stop < meter_start:
        return jsonify({"error": "meter_stop_before_start"}), 400

    end_ts = None
    if data.get("end"):
        end_ts = data["end"].replace("T", " ") + ":00"

    # Ladeort und Preis kommen jetzt aus dem Formular. Der Ort entscheidet
    # ueber die steuerliche Behandlung, der Preis folgt daraus — beides war
    # bisher nicht erfassbar und musste nachtraeglich korrigiert werden.
    ort = data.get("charging_location")
    if ort not in ("zuhause", "extern"):
        ort = "zuhause"
    try:
        preis = float(data.get("price_per_kwh") or 0)
    except (TypeError, ValueError):
        preis = 0
    if preis <= 0:
        preis = float(user["default_kwh_price"] or 0.34)

    wallbox_id = wallbox_repository.get_or_create_wallbox(wallbox_name, source_type="manual")
    session_id = session_repository.insert_session(
        wallbox_id=wallbox_id,
        user_id=user["id"],
        source="manual",
        start_timestamp=start_ts,
        end_timestamp=end_ts,
        meter_start_wh=meter_start,
        meter_stop_wh=meter_stop,
        price_per_kwh=preis,
        rfid_tag=data.get("rfid") or None,
        classification=None,
        charging_location=ort,
        status="closed" if end_ts else "open",
    )
    return jsonify({"ok": True, "session_id": session_id})


@app.route("/api/sessions/import", methods=["POST"])
def api_sessions_import():
    """FA-LS-01: CSV-Import gemäß § 6.2."""
    user = _current_user()
    if user is None:
        return jsonify({"error": "no_user"}), 400

    if "file" not in request.files:
        return jsonify({"error": "no_file"}), 400

    csv_text = request.files["file"].read().decode("utf-8-sig")
    result = import_service.parse_and_import(csv_text, user_id=user["id"], default_price=user["default_kwh_price"])
    return jsonify(result)


@app.route("/api/sessions/<int:session_id>/classify", methods=["POST"])
def api_sessions_classify(session_id):
    """FA-LS-04: Nachträgliche Dienst-/Privat-Klassifizierung, mit Audit-Log."""
    user = _current_user()
    if user is None:
        return jsonify({"error": "no_user"}), 400

    data = request.get_json(force=True)
    new_value = data.get("classification")
    if new_value not in ("dienstlich", "privat"):
        return jsonify({"error": "invalid_classification"}), 400

    ok = billing_service.set_classification(session_id, new_value, changed_by=user["name"])
    if not ok:
        return jsonify({"error": "session_not_found"}), 404
    return jsonify({"ok": True})


@app.route("/api/sessions/<int:session_id>/charging-location", methods=["POST"])
def api_sessions_charging_location(session_id):
    """FA-LS-BMW-02: Ladeort (zuhause/extern) nachträglich korrigieren, falls
    die automatische Erkennung beim BMW-Import falsch lag. Mit Audit-Log,
    da dies direkt beeinflusst, ob die Session in den Eigenstrom-Beleg
    einfließt."""
    user = _current_user()
    if user is None:
        return jsonify({"error": "no_user"}), 400

    data = request.get_json(force=True)
    new_value = data.get("charging_location")
    if new_value not in ("zuhause", "extern"):
        return jsonify({"error": "invalid_charging_location"}), 400

    ok = billing_service.set_charging_location(session_id, new_value, changed_by=user["name"])
    if not ok:
        return jsonify({"error": "session_not_found"}), 404

    # Preis mitfuehren: Eine faelschlich als extern eingestufte Heimladung
    # traegt sonst weiterhin den teuren Fremdtarif — und umgekehrt.
    if data.get("preis_anpassen"):
        if new_value == "zuhause":
            preis = float(settings_repository.get_setting("contract_kwh_price")
                          or settings_repository.get_setting("default_kwh_price") or 0.34)
        else:
            preis = float(settings_repository.get_setting("preis_ac_extern_kwh") or 0.59)
        conn = get_connection()
        try:
            conn.execute("UPDATE charging_sessions SET price_per_kwh = ? WHERE id = ?",
                         (preis, session_id))
            conn.commit()
        finally:
            conn.close()
    return jsonify({"ok": True})


@app.route("/api/sessions/<int:session_id>/close", methods=["POST"])
def api_session_close(session_id):
    """Manuelles Schließen einer offenen Session — z. B. wenn StopTransaction
    verpasst wurde und die Session als 'offen' in der DB hängt."""
    user = _current_user()
    if user is None:
        return jsonify({"error": "no_user"}), 400
    session = session_repository.get_session(session_id)
    if session is None or session.get("user_id") != user["id"]:
        return jsonify({"error": "not_found"}), 404
    if session.get("status") == "closed":
        return jsonify({"ok": True, "message": "Bereits geschlossen."})
    meter_stop = session.get("meter_stop_wh") or session.get("meter_start_wh", 0)
    end_ts = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
    session_repository.close_session(session_id, meter_stop, end_ts)
    event_log_service.log_event("manual", "info",
        f"Session #{session_id} manuell geschlossen (meter_stop={meter_stop} Wh)")
    return jsonify({"ok": True})


@app.route("/api/sessions/<int:session_id>/raw", methods=["GET"])
def api_sessions_raw(session_id):
    """Rohdaten (inkl. Wh-Zählerstände) fuer den Bearbeiten-Dialog."""
    session = session_repository.get_session(session_id)
    if session is None:
        return jsonify({"error": "session_not_found"}), 404
    return jsonify(session)


@app.route("/api/wallboxes/last-meter", methods=["GET"])
def api_wallbox_last_meter():
    """Letzter bekannter Zählerstand einer Wallbox, zum Vorbefüllen der manuellen Erfassung."""
    name = request.args.get("name", "").strip()
    if not name:
        return jsonify({"last_meter_wh": None})
    value = session_repository.get_last_meter_for_wallbox_name(name)
    return jsonify({"last_meter_wh": value})


@app.route("/api/sessions/<int:session_id>", methods=["PUT"])
def api_sessions_update(session_id):
    """Bearbeiten einer bestehenden Session (manuelle Korrektur)."""
    user = _current_user()
    if user is None:
        return jsonify({"error": "no_user"}), 400

    existing = session_repository.get_session(session_id)
    if existing is None:
        return jsonify({"error": "session_not_found"}), 404

    data = request.get_json(force=True)
    try:
        wallbox_name = data["wallbox"]
        start_ts = data["start"].replace("T", " ") + ":00"
        meter_start = int(data["meter_start"])
        meter_stop = int(data["meter_end"])
    except (KeyError, ValueError):
        return jsonify({"error": "invalid_input"}), 400

    if meter_stop < meter_start:
        return jsonify({"error": "meter_stop_before_start"}), 400

    end_ts = None
    if data.get("end"):
        end_ts = data["end"].replace("T", " ") + ":00"

    wallbox_id = wallbox_repository.get_or_create_wallbox(wallbox_name, source_type="manual")
    session_repository.update_session(
        session_id=session_id,
        wallbox_id=wallbox_id,
        start_timestamp=start_ts,
        end_timestamp=end_ts,
        meter_start_wh=meter_start,
        meter_stop_wh=meter_stop,
        rfid_tag=data.get("rfid") or None,
    )
    write_audit_log("charging_sessions", session_id, "manual_edit",
                     json.dumps(existing, default=str), json.dumps(data), user["name"])
    return jsonify({"ok": True})


@app.route("/api/sessions/<int:session_id>", methods=["DELETE"])
def api_sessions_delete(session_id):
    user = _current_user()
    if user is None:
        return jsonify({"error": "no_user"}), 400
    existing = session_repository.get_session(session_id)
    if existing is None:
        return jsonify({"error": "session_not_found"}), 404
    session_repository.delete_session(session_id)
    write_audit_log("charging_sessions", session_id, "deleted",
                     json.dumps(existing, default=str), None, user["name"])
    return jsonify({"ok": True})


@app.route("/api/sessions/delete-all", methods=["POST"])
def api_sessions_delete_all():
    """Loescht ALLE Ladesessions des aktuellen Nutzers. Mit Audit-Log-Eintrag
    je geloeschter Session (wie beim Einzel-Loeschen), damit die Historie
    nachvollziehbar bleibt."""
    user = _current_user()
    if user is None:
        return jsonify({"error": "no_user"}), 400
    sessions = session_repository.list_sessions(user_id=user["id"])
    for s in sessions:
        session_repository.delete_session(s["id"])
        write_audit_log("charging_sessions", s["id"], "deleted",
                         json.dumps(s, default=str), None, user["name"])

    # Auch die Merkposten des BMW-Imports zuruecksetzen. Sonst gelten die
    # Ladevorgaenge dort weiterhin als "schon geholt" und liessen sich nie
    # wieder importieren — genau der Zustand, in dem man nach einem
    # Komplettloeschen ratlos vor einem leeren Bildschirm steht.
    zurueckgesetzt = 0
    conn = get_connection()
    try:
        for key, in conn.execute(
                "SELECT key FROM app_settings WHERE key LIKE 'cardata_stand_%'").fetchall():
            conn.execute("DELETE FROM app_settings WHERE key = ?", (key,))
            zurueckgesetzt += 1
        conn.commit()
    finally:
        conn.close()

    event_log_service.log_event("system", "info",
        f"Alle Ladesessions gelöscht ({len(sessions)}). "
        f"BMW-Importstand zurückgesetzt — erneuter Abruf holt alles zurück.")
    return jsonify({"ok": True, "deleted_count": len(sessions),
                    "import_zurueckgesetzt": zurueckgesetzt})


# ---------------------------------------------------------------------------
# Personen-Stammdaten (FA-PERS-01/02) — leichtgewichtig, unabhaengig vom
# Einzelnutzer-Setup (users_config), fuer Familie/zweite Person am Beleg.
# ---------------------------------------------------------------------------

@app.route("/api/persons", methods=["GET"])
def api_persons_list():
    return jsonify({"persons": person_repository.list_persons()})


@app.route("/api/persons", methods=["POST"])
def api_persons_create():
    data = request.get_json(force=True)
    name = (data.get("name") or "").strip()
    if not name:
        return jsonify({"error": "name_required"}), 400
    person_id = person_repository.insert_person(
        name=name,
        email=(data.get("email") or "").strip() or None,
        personalnummer=(data.get("personalnummer") or "").strip() or None,
        kfz_kennzeichen=(data.get("kfz_kennzeichen") or "").strip() or None,
        telefon=(data.get("telefon") or "").strip() or None,
        home_address=(data.get("home_address") or "").strip() or None,
    )
    return jsonify({"ok": True, "person_id": person_id})


@app.route("/api/persons/<int:person_id>", methods=["PUT"])
def api_persons_update(person_id):
    existing = person_repository.get_person(person_id)
    if existing is None:
        return jsonify({"error": "person_not_found"}), 404
    data = request.get_json(force=True)
    name = (data.get("name") or "").strip()
    if not name:
        return jsonify({"error": "name_required"}), 400
    person_repository.update_person(
        person_id=person_id, name=name,
        email=(data.get("email") or "").strip() or None,
        personalnummer=(data.get("personalnummer") or "").strip() or None,
        kfz_kennzeichen=(data.get("kfz_kennzeichen") or "").strip() or None,
        telefon=(data.get("telefon") or "").strip() or None,
        home_address=(data.get("home_address") or "").strip() or None,
    )
    return jsonify({"ok": True})


@app.route("/api/persons/home-address", methods=["GET"])
def api_persons_home_address():
    """Stammadresse der ersten Person — für Auto-Fill im Fahrtformular."""
    addr = person_repository.get_first_home_address()
    return jsonify({"address": addr})


@app.route("/api/persons/<int:person_id>", methods=["DELETE"])
def api_persons_delete(person_id):
    if person_repository.get_person(person_id) is None:
        return jsonify({"error": "person_not_found"}), 404
    person_repository.delete_person(person_id)
    return jsonify({"ok": True})


def _resolve_person_display(user: dict) -> dict:
    """Personen-Daten für Beleg. Priorität:
    1. person_id im Query-Parameter → aus persons-Tabelle
    2. Erste angelegte Person → aus persons-Tabelle  
    3. Fallback → user['name'] aus users_config."""
    person_id = request.args.get("person_id")
    if person_id and person_id.isdigit():
        person = person_repository.get_person(int(person_id))
    else:
        # Automatisch die erste Person aus der DB verwenden
        persons = person_repository.list_persons()
        person = persons[0] if persons else None

    base = dict(person) if person else {"name": user["name"]}
    display = {"name": base.get("name", user["name"])}

    # Felder-Checkboxen: ?include_email=1&include_kfz_kennzeichen=1 etc.
    # Wenn keine Checkboxen übergeben, Standard-Felder einbeziehen
    explicit_fields = any(
        request.args.get(f"include_{f}") for f in ("email", "personalnummer", "kfz_kennzeichen", "telefon")
    )
    for field in ("email", "personalnummer", "kfz_kennzeichen", "telefon"):
        if request.args.get(f"include_{field}") == "1":
            display[field] = base.get(field)
        elif not explicit_fields and base.get(field):
            # Standard: alle nicht-leeren Felder einbeziehen
            display[field] = base.get(field)
    return display


# ---------------------------------------------------------------------------
# Sprint 3 — OCPP-Live-Anbindung & Wallbox-Verwaltung (FA-LS-08/09/10)
# ---------------------------------------------------------------------------

@app.route("/api/wallboxes/full", methods=["GET"])
def api_wallboxes_full():
    """Wallboxen inkl. Live-Status und offener Session-Info (FA-LS-09)."""
    user = _current_user()
    wallboxes = wallbox_repository.list_wallboxes_with_status()
    for wb in wallboxes:
        wb.pop("loxone_password_encrypted", None)
        backoff_state = loxone_config_repository.get_auth_backoff_state(wb["id"])
        wb["polling_paused"] = bool(backoff_state and backoff_state.get("manually_paused"))

        # Offene Session mitliefern (für OCPP-Karte live info)
        if user:
            open_sess = session_repository.get_open_session_for_wallbox(wb["id"])
            if open_sess:
                try:
                    start_dt = datetime.strptime(open_sess["start_timestamp"], "%Y-%m-%d %H:%M:%S")
                    elapsed_min = int((datetime.now() - start_dt).total_seconds() / 60)
                except Exception:
                    elapsed_min = 0
                kwh_so_far = round(
                    ((open_sess.get("meter_stop_wh") or open_sess["meter_start_wh"])
                     - open_sess["meter_start_wh"]) / 1000.0, 3
                )
                wb["open_session"] = {
                    "session_id":   open_sess["id"],
                    "elapsed_min":  elapsed_min,
                    "kwh_so_far":   kwh_so_far,
                    "meter_start_wh": open_sess["meter_start_wh"],
                    "meter_now_wh": open_sess.get("meter_stop_wh") or open_sess["meter_start_wh"],
                }
            else:
                wb["open_session"] = None

        # OCPP-Livewerte (Wirkleistung, Phasenstroeme, MID-Zaehler, Peak,
        # Tagesenergie) fuer die Wallbox-Karten mitliefern.
        try:
            lm = ocpp_service.get_live_metrics(wb["id"])
            if lm:
                wb["live_metrics"] = {
                    "power_kw":        lm.get("current_power_kw"),
                    "peak_power_kw":   lm.get("peak_power_kw"),
                    "meter_total_wh":  lm.get("meter_total_wh"),
                    "current_l1_a":    lm.get("current_l1_a"),
                    "current_l2_a":    lm.get("current_l2_a"),
                    "current_l3_a":    lm.get("current_l3_a"),
                    "tagesenergie_kwh": lm.get("tagesenergie_kwh"),
                    "last_sync_at":    lm.get("last_sync_at"),
                }
        except Exception:
            wb["live_metrics"] = None

        # live_status "online" → "charging" wenn Loxone aktiv lädt (Cac=1)
        # Der Poller setzt charging korrekt; aber bei Loxone-API-Wallboxen
        # kann "online" verbleiben wenn der Status nicht rechtzeitig aktualisiert
        # wird — daher hier zusätzlich raw_snapshot prüfen:
        if wb.get("live_status") == "online" and wb.get("source_type") == "loxone_api":
            metrics = loxone_config_repository.get_live_metrics(wb["id"])
            if metrics and metrics.get("raw_snapshot"):
                try:
                    raw = json.loads(metrics["raw_snapshot"])
                    if raw.get("Cac") == "1":
                        wb["live_status"] = "charging"
                except Exception:
                    pass

    return jsonify({"wallboxes": wallboxes})


@app.route("/api/wallboxes", methods=["POST"])
def api_wallboxes_create():

    # In der Demo laesst sich genau eine Wallbox einrichten — genug, um die
    # Anbindung zu pruefen, zu wenig fuer den Betrieb mehrerer Ladepunkte.
    try:
        vorhandene = len(wallbox_repository.list_wallboxes())
        if edition_service.wallbox_limit_erreicht(vorhandene):
            return jsonify(edition_service.gesperrt_hinweis("mehrere_wallboxen")), 402
    except Exception:
        pass
    data = request.get_json(force=True)
    name = (data.get("name") or "").strip()
    source_type = data.get("source_type", "ocpp")
    if not name or source_type not in ("ocpp", "loxone_api", "extern_ocpp"):
        return jsonify({"error": "invalid_input"}), 400

    ocpp_id = None
    loxone_host = loxone_user = loxone_pw_enc = None

    if source_type == "ocpp":
        ocpp_id = (data.get("ocpp_charge_point_id") or "").strip()
        if not ocpp_id:
            return jsonify({"error": "ocpp_charge_point_id_required"}), 400
    elif source_type == "extern_ocpp":
        # Beim externen Dienst gibt es keine Zugangsdaten zu speichern: Die
        # Wallbox meldet sich dort an, nicht hier. Die Verbindung zum Dienst
        # steht in den Einstellungen und gilt fuer alle Wallboxen dieser Art.
        loxone_host = loxone_user = ""
        loxone_pw_enc = None
        adresse = (data.get("extern_adresse") or "").strip()
        if adresse:
            extern_ocpp_service.speichere_konfiguration(
                adresse, data.get("extern_pfad", ""), name, aktiv=True)
    else:
        loxone_host = (data.get("loxone_host") or "").strip()
        loxone_user = (data.get("loxone_username") or "").strip()
        loxone_pw = data.get("loxone_password") or ""
        if not loxone_host or not loxone_user or not loxone_pw:
            return jsonify({"error": "loxone_credentials_required"}), 400
        loxone_pw_enc = crypto_service.encrypt(loxone_pw)  # NFA-11: niemals Klartext speichern

    wallbox_id = wallbox_repository.create_wallbox(
        name=name, source_type=source_type, ocpp_charge_point_id=ocpp_id,
        loxone_host=loxone_host, loxone_username=loxone_user, loxone_password_encrypted=loxone_pw_enc,
        location=(data.get("location") or "").strip() or None,
    )

    if source_type == "loxone_api":
        loxone_uuid = (data.get("loxone_uuid") or "").strip()
        if loxone_uuid:
            loxone_config_repository.set_uuid(wallbox_id, loxone_uuid)

    return jsonify({"ok": True, "wallbox_id": wallbox_id})


@app.route("/api/wallboxes/<int:wallbox_id>", methods=["DELETE"])
def api_wallboxes_delete(wallbox_id):
    if wallbox_repository.get_wallbox(wallbox_id) is None:
        return jsonify({"error": "wallbox_not_found"}), 404
    force = request.args.get("force") == "1"
    try:
        ok, message = wallbox_repository.delete_wallbox(wallbox_id, force=force)
    except sqlite3.IntegrityError as exc:
        # Absicherung: falls je eine neue Tabelle mit wallbox_id-Bezug
        # hinzukommt, die _tables_referencing_wallbox() aus irgendeinem
        # Grund nicht erfasst, soll das nie wieder als nackter 500er ohne
        # verstaendliche Meldung enden.
        return jsonify({"error": "delete_failed", "message": f"Löschen fehlgeschlagen (Datenbank-Constraint): {exc}"}), 500
    if not ok:
        return jsonify({"error": "wallbox_has_sessions", "message": message}), 409
    return jsonify({"ok": True, "message": message})


@app.route("/api/wallboxes/<int:wallbox_id>", methods=["PUT"])
def api_wallboxes_update(wallbox_id):
    if wallbox_repository.get_wallbox(wallbox_id) is None:
        return jsonify({"error": "wallbox_not_found"}), 404
    data = request.get_json(force=True)
    name = (data.get("name") or "").strip()
    if not name:
        return jsonify({"error": "name_required"}), 400
    source_type = data.get("source_type", "ocpp")
    ocpp_id = (data.get("ocpp_charge_point_id") or "").strip() or None
    loxone_host = (data.get("loxone_host") or "").strip() or None
    loxone_user = (data.get("loxone_username") or "").strip() or None

    existing = wallbox_repository.get_wallbox(wallbox_id)
    loxone_pw_enc = existing.get("loxone_password_encrypted")
    new_password = data.get("loxone_password") or ""
    if new_password:  # nur ueberschreiben, wenn tatsaechlich ein neues Passwort eingegeben wurde
        loxone_pw_enc = crypto_service.encrypt(new_password)

    wallbox_repository.update_wallbox(
        wallbox_id, name=name, source_type=source_type, ocpp_charge_point_id=ocpp_id,
        loxone_host=loxone_host, loxone_username=loxone_user, loxone_password_encrypted=loxone_pw_enc,
        location=(data.get("location") or "").strip() or None,
    )

    loxone_uuid = (data.get("loxone_uuid") or "").strip()
    if source_type == "loxone_api" and loxone_uuid:
        loxone_config_repository.set_uuid(wallbox_id, loxone_uuid)

    return jsonify({"ok": True})


@app.route("/api/wallboxes/delete-all", methods=["POST"])
def api_wallboxes_delete_all():
    """Loescht alle Wallboxen, die keine zugeordneten Sessions haben. Wallboxen
    MIT Sessions werden übersprungen (Datenverlust-Schutz bleibt bestehen) und
    in der Antwort aufgelistet, damit der Nutzer weiss, was übrig blieb."""
    all_wb = wallbox_repository.list_wallboxes()
    deleted, skipped = [], []
    for wb in all_wb:
        try:
            ok, message = wallbox_repository.delete_wallbox(wb["id"])
        except sqlite3.IntegrityError as exc:
            skipped.append({"name": wb["name"], "reason": f"Datenbank-Constraint: {exc}"})
            continue
        if ok:
            deleted.append(wb["name"])
        else:
            skipped.append({"name": wb["name"], "reason": message})
    return jsonify({"ok": True, "deleted": deleted, "skipped": skipped})




@app.route("/api/dashboard/recent-sessions", methods=["GET"])
def api_dashboard_recent_sessions():
    """Letzte Ladevorgänge (kWh je Session) für das Balkendiagramm.
    Optional: ?wallbox=<Wallbox-Name> für gefilterte Ansicht."""
    user = _current_user()
    if user is None:
        return jsonify({"error": "no_user"}), 400
    sessions = session_repository.list_sessions(user_id=user["id"])
    wb_filter = request.args.get("wallbox", "").strip()

    closed = [s for s in sessions
              if s.get("meter_stop_wh") is not None
              and s.get("meter_start_wh") is not None
              and s["meter_stop_wh"] > s["meter_start_wh"]]

    if wb_filter:
        closed = [s for s in closed if (s.get("wallbox_name") or "") == wb_filter]

    closed_sorted = sorted(closed, key=lambda s: s["start_timestamp"])[-12:]
    result = []
    for s in closed_sorted:
        try:
            label = datetime.strptime(s["start_timestamp"], "%Y-%m-%d %H:%M:%S").strftime("%d.%m.")
        except Exception:
            label = str(s["start_timestamp"])[:10]
        kwh = round((s["meter_stop_wh"] - s["meter_start_wh"]) / 1000.0, 2)
        if kwh > 0:
            result.append({
                "label": label,
                "kwh": kwh,
                "source": s.get("source", ""),
            })
    return jsonify({"sessions": result, "debug_total": len(closed), "filter": wb_filter})


@app.route("/api/dashboard/summary", methods=["GET"])
def api_dashboard_summary():
    """Liefert die tatsaechlichen Kennzahlen fuer das Dashboard — ersetzt die
    zuvor fest im Template verankerten Mockup-Zahlen (412 kWh, 140,08 € etc.),
    die nie mit der echten Datenbank verbunden waren (Rückmeldung des
    Auftraggebers: Dashboard passte nicht zu den echten Werten)."""
    user = _current_user()
    if user is None:
        return jsonify({"error": "no_user"}), 400

    today = datetime.now()

    # Zeitraum aus der zentralen Auswahl des Dashboards. Ohne Angabe gilt der
    # laufende Monat — so bleiben aeltere Aufrufe gueltig.
    von_param = (request.args.get("von") or "").strip()
    bis_param = (request.args.get("bis") or "").strip()
    zeit_label = (request.args.get("label") or "").strip()

    month_label_de = ["Januar", "Februar", "März", "April", "Mai", "Juni", "Juli", "August",
                       "September", "Oktober", "November", "Dezember"][today.month - 1]

    if von_param and bis_param:
        month_start = von_param
        month_end = bis_param
    else:
        month_start = today.replace(day=1).strftime("%Y-%m-%d")
        month_end = today.strftime("%Y-%m-%d")
        zeit_label = f"{month_label_de} {today.year}"

    ls_summary = analytics_service.period_summary(user["id"], month_start, month_end)

    trips = trip_repository.list_trips(user["id"], month_start, month_end, nur_dienstlich=True)
    trip_km = sum(t.get("distance_km") or 0 for t in trips)

    # ALLE Fahrten fuer die Verbrauchsrechnung — dienstlich wie privat.
    # Der geladene Strom traegt beide; nur die Dienstkilometer im Nenner
    # ergaebe einen zu hohen Verbrauch. Bei 1084 dienstlichen und 500
    # privaten Kilometern waeren das rund 45 % zu viel.
    alle_trips = trip_repository.list_trips(user["id"], month_start, month_end)
    km_gesamt = sum(t.get("distance_km") or 0 for t in alle_trips)

    # ── Strom-Vergleich ───────────────────────────────────────────────────────
    contract_rate = float(settings_repository.get_setting("contract_kwh_price") or user["default_kwh_price"] or 0.34)
    compare = analytics_service.compare_pauschale_vs_real(ls_summary["total_kwh"], 0.34, contract_rate)
    strom_erstattung  = round(compare["pauschale_amount"], 2)  # Pauschale × kWh
    strom_kosten      = round(compare["real_amount"], 2)        # Vertragspreis × kWh
    strom_reinerloes  = round(strom_erstattung - strom_kosten, 2)

    # ── Fahrtkosten-Auswertung ────────────────────────────────────────────────
    GESETZLICHE_PAUSCHALE = 0.30  # § 9 EStG
    trip_km = sum(t.get("distance_km") or 0 for t in trips)
    trip_ag_rate = float(settings_repository.get_setting("default_km_rate") or 0.15)
    fahrt_erstattung   = round(trip_km * trip_ag_rate, 2)          # was AG zahlt
    fahrt_werbungskosten = round(trip_km * (GESETZLICHE_PAUSCHALE - trip_ag_rate), 2)  # Differenz
    # Steuerschätzung: Werbungskosten × angenommener Grenzsteuersatz
    steuersatz = float(settings_repository.get_setting("persoenlicher_steuersatz") or 0.35)
    fahrt_steuer_schaetzung = round(fahrt_werbungskosten * steuersatz, 2)

    # ── Kombinierter Reinerlös (Erstattungen) ─────────────────────────────────
    gesamt_erstattung = round(strom_erstattung + fahrt_erstattung, 2)
    gesamt_reinerloes = round(strom_reinerloes + fahrt_erstattung, 2)  # Cash aus Erstattungen
    gesamt_inkl_steuer = round(gesamt_reinerloes + fahrt_steuer_schaetzung, 2)

    # ── PKW-Vollkosten einbeziehen (maßgebliche Bilanz!) ──────────────────────
    # Rückmeldung Auftraggeber: Das Dashboard sah positiv aus, weil die
    # PKW-Ausgaben (Leasing/Versicherung/Reifen) fehlten. Die Vollkostenrechnung
    # ist das Ausschlaggebende — daher wird die echte Bilanz hier gespiegelt:
    # über ALLE Fahrzeuge summierte Monats-Ausgaben + AG-Zuschuesse netto.
    pkw_ausgaben_monat = 0.0
    allowance_netto_sum = 0.0
    try:
        all_vehicles = vehicle_repository.list_vehicles()
        for v in all_vehicles:
            pkw_ausgaben_monat += pkw_repository.monatliche_kosten_gesamt(vehicle_id=v["id"])
            # Neue Multi-Zuschuss-Ebene (Car Allowance, Tankkarte, Jobticket …);
            # faellt auf den Alt-Eintrag zurueck, falls noch nichts migriert wurde.
            summe = pkw_repository.zuschuesse_summe(vehicle_id=v["id"], steuersatz=steuersatz)
            if summe["brutto_monat"] > 0:
                allowance_netto_sum += summe["netto_monat"]
            else:
                va = pkw_repository.get_car_allowance(vehicle_id=v["id"])
                brutto = float(va.get("monatlicher_betrag") or 0)
                allowance_netto_sum += round(brutto * (1 - steuersatz), 2) if va.get("versteuert") else brutto
        pkw_ausgaben_monat = round(pkw_ausgaben_monat, 2)
        allowance_netto_sum = round(allowance_netto_sum, 2)
    except Exception:
        pkw_ausgaben_monat = 0.0
        allowance_netto_sum = 0.0

    # Vollkosten-Bilanz = Erstattungen(Cash) + Allowance − PKW-Ausgaben (+ Steuer)
    vollkosten_cash = round(gesamt_reinerloes + allowance_netto_sum - pkw_ausgaben_monat, 2)
    vollkosten_inkl_steuer = round(vollkosten_cash + fahrt_steuer_schaetzung, 2)
    hat_vollkosten = pkw_ausgaben_monat > 0 or allowance_netto_sum > 0

    # Live-Leistung: neuester bekannter Wert ueber alle Loxone-API-Wallboxen
    conn = get_connection()
    try:
        row = conn.execute(
            """SELECT wlm.current_power_kw, wlm.connected, wlm.last_sync_at, wlm.raw_snapshot, wb.name, wb.location
               FROM wallbox_live_metrics wlm JOIN wallboxes wb ON wb.id = wlm.wallbox_id
               ORDER BY wlm.last_sync_at DESC LIMIT 1"""
        ).fetchone()
    finally:
        conn.close()
    live = None
    live_session = None
    if row is not None:
        live = {
            "current_power_kw": row["current_power_kw"], "connected": bool(row["connected"]),
            "wallbox_name": row["name"], "wallbox_location": row["location"], "last_sync_at": row["last_sync_at"],
        }
        # Detaillierte, laufende Ladesession — abgeleitet vom Referenzbild
        # (native Wallbox-App-Ansicht: Verbunden seit, Dauer, Geladene Energie,
        # Gesamte Ladekosten), auf Wunsch fuers Dashboard nachgebaut.
        raw = {}
        if row["raw_snapshot"]:
            try:
                raw = json.loads(row["raw_snapshot"])
            except (json.JSONDecodeError, TypeError):
                raw = {}
        vehicle_connected = raw.get("Vc") == "1"
        charging_active = raw.get("Cac") == "1"
        if vehicle_connected:
            connected_since = None
            duration_label = None
            try:
                lcl = raw.get("Lcl", "")
                m = re.match(r"(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}):Fahrzeug verbunden", lcl)
                if m:
                    since_dt = datetime.strptime(m.group(1), "%Y-%m-%d %H:%M:%S")
                    connected_since = since_dt.strftime("%H:%M")
                    delta = datetime.now() - since_dt
                    total_min = max(0, int(delta.total_seconds() // 60))
                    duration_label = f"{total_min // 60}h {total_min % 60}min" if total_min >= 60 else f"{total_min}min"
            except Exception:
                pass
            live_session = {
                "wallbox_name": row["name"], "wallbox_location": row["location"],
                "charging_active": charging_active,
                "current_power_kw": row["current_power_kw"],
                "target_power_kw": float(raw["Tp"]) if raw.get("Tp") else None,
                "connected_since": connected_since,
                "duration_label": duration_label,
                "energy_so_far_kwh": float(raw["Ccc"]) if raw.get("Ccc") else 0.0,
                "cost_so_far": float(raw["Cclc"]) if raw.get("Cclc") else 0.0,
            }

    # OCPP-Weg: falls keine Live-Session ueber die direkte API gefunden wurde,
    # in den echten offenen DB-Sessions nachsehen (OCPP legt bei StartTransaction
    # eine offene Session an, MeterValues aktualisiert laufend den Zaehlerstand
    # — anders als der Loxone-API-Weg, der Sessions erst rueckwirkend anlegt).
    # Einheitliche Karte im Dashboard, egal welcher Datenweg gerade aktiv ist.
    if live_session is None:
        open_session = session_repository.get_any_open_session(user["id"])
        if open_session is not None:
            start_dt = datetime.strptime(open_session["start_timestamp"], "%Y-%m-%d %H:%M:%S")
            delta = datetime.now() - start_dt
            total_min = max(0, int(delta.total_seconds() // 60))
            kwh_so_far = ((open_session.get("meter_stop_wh") or open_session["meter_start_wh"]) - open_session["meter_start_wh"]) / 1000.0
            live_session = {
                "wallbox_name": open_session.get("wallbox_location") or open_session.get("wallbox_name"),
                "wallbox_location": open_session.get("wallbox_location"),
                "charging_active": True,
                "current_power_kw": None,  # OCPP MeterValues liefert i.d.R. keine Momentanleistung in unserer Auswertung
                "target_power_kw": None,
                "connected_since": start_dt.strftime("%H:%M"),
                "duration_label": f"{total_min // 60}h {total_min % 60}min" if total_min >= 60 else f"{total_min}min",
                "energy_so_far_kwh": round(kwh_so_far, 2),
                "cost_so_far": round(kwh_so_far * user["default_kwh_price"], 2),
            }

    # OCPP-Weg (reine Verbindungsstatus-Aenderung, KEINE Session): Falls weder
    # eine offene Session noch die direkte API etwas liefert, aber der zuletzt
    # per StatusNotification gemeldete Zustand auf ein angeschlossenes Fahrzeug
    # hindeutet (Preparing/Charging/SuspendedEV/SuspendedEVSE/Finishing), soll
    # das trotzdem sichtbar sein — bestaetigt vom Auftraggeber: StatusNotification
    # kommt tatsaechlich an, auch ohne dass eine volle Ladesession existiert
    # (z. B. Fahrzeug nur angesteckt, noch nicht autorisiert/gestartet).
    OCPP_VEHICLE_PRESENT_STATES = {"Preparing", "Charging", "SuspendedEVSE", "SuspendedEV", "Finishing"}
    if live_session is None:
        conn = get_connection()
        try:
            status_row = conn.execute(
                """SELECT ws.status, ws.updated_at, wb.name, wb.location
                   FROM wallbox_status ws JOIN wallboxes wb ON wb.id = ws.wallbox_id
                   ORDER BY ws.updated_at DESC LIMIT 5"""
            ).fetchall()
        finally:
            conn.close()
        for sr in status_row:
            if sr["status"] in OCPP_VEHICLE_PRESENT_STATES:
                live_session = {
                    "wallbox_name": sr["location"] or sr["name"],
                    "wallbox_location": sr["location"],
                    "charging_active": sr["status"] == "Charging",
                    "current_power_kw": None,
                    "target_power_kw": None,
                    "connected_since": None,
                    "duration_label": None,
                    "energy_so_far_kwh": 0.0,
                    "cost_so_far": 0.0,
                    "ocpp_status_raw": sr["status"],
                }
                break

    # Letzte Aktivitaet: neueste Sessions + Fahrten gemeinsam, nach Zeit sortiert
    recent_sessions = session_repository.list_sessions(user_id=user["id"])
    recent_sessions.sort(key=lambda s: s.get("created_at") or s["start_timestamp"], reverse=True)
    recent_trips = trip_repository.list_trips(user["id"])
    recent_trips.sort(key=lambda t: t.get("created_at") or t["trip_date"], reverse=True)

    activity = []
    for s in recent_sessions[:3]:
        if s.get("status") == "open":
            continue  # laufende Sessions gehoeren in die Live-Session-Karte, nicht in "Letzte Aktivitaet" als "beendet"
        kwh, amount = billing_service.compute_energy_and_amount(s)
        activity.append({
            "text": f"Ladesession {s.get('wallbox_name', '')} beendet — {_dash_fmt_kwh(kwh)} kWh",
            "time": s.get("end_timestamp") or s["start_timestamp"],
        })
    for t in recent_trips[:3]:
        activity.append({"text": f"Fahrt \"{t.get('purpose', '')}\" erfasst — {t.get('distance_km', 0)} km", "time": t["trip_date"]})
    activity.sort(key=lambda a: a["time"], reverse=True)

    return jsonify({
        "month_label": f"{month_label_de} {today.year}",
        "kwh_this_month":       ls_summary["total_kwh"],
        "cost_this_month":      ls_summary["total_cost"],
        "session_count":        ls_summary["session_count"],
        "avg_kwh_per_session":  round(ls_summary["total_kwh"] / ls_summary["session_count"], 1) if ls_summary["session_count"] else 0,
        "trip_count":           len(trips),
        "trip_km":              round(trip_km, 1),
        "km_gesamt":            round(km_gesamt, 1),
        # Strom
        "strom_erstattung":     strom_erstattung,
        "strom_kosten":         strom_kosten,
        "strom_reinerloes":     strom_reinerloes,
        # Fahrtkosten
        "fahrt_erstattung":     fahrt_erstattung,
        "fahrt_werbungskosten": fahrt_werbungskosten,
        "fahrt_steuer_schaetzung": fahrt_steuer_schaetzung,
        # Gesamt (Erstattungen)
        "gesamt_erstattung":    gesamt_erstattung,
        "gesamt_reinerloes":    gesamt_reinerloes,
        "gesamt_inkl_steuer":   gesamt_inkl_steuer,
        "steuersatz_pct":       round(steuersatz * 100),
        # Vollkosten (maßgebliche Bilanz — inkl. PKW-Ausgaben & Allowance)
        "pkw_ausgaben_monat":   pkw_ausgaben_monat,
        "allowance_netto":      allowance_netto_sum,
        "vollkosten_cash":      vollkosten_cash,
        "vollkosten_inkl_steuer": vollkosten_inkl_steuer,
        "hat_vollkosten":       hat_vollkosten,
        # Legacy für bestehenden JS-Code
        "pauschale_diff":       strom_reinerloes,
        "cheaper":              compare["cheaper"],
        "live":                 live,
        "live_session":         live_session,
        "activity":             activity[:5],
    })


def _dash_fmt_kwh(v):
    return f"{v:,.1f}".replace(",", "X").replace(".", ",").replace("X", ".")


@app.route("/api/settings/home-address", methods=["GET"])
def api_get_home_address():
    return jsonify({"address": settings_repository.get_setting("home_address")})


@app.route("/api/settings/home-address", methods=["PUT"])
def api_set_home_address():
    data = request.get_json(force=True)
    address = (data.get("address") or "").strip()
    settings_repository.set_setting("home_address", address)
    return jsonify({"ok": True, "address": address})


@app.route("/api/settings/loxone-poll-interval", methods=["GET"])
def api_get_poll_interval():
    return jsonify({"seconds": int(settings_repository.get_setting("loxone_poll_interval_seconds"))})


@app.route("/api/settings/loxone-poll-interval", methods=["PUT"])
def api_set_poll_interval():
    data = request.get_json(force=True)
    try:
        seconds = int(data.get("seconds"))
    except (TypeError, ValueError):
        return jsonify({"error": "invalid_value"}), 400
    if seconds < 10:
        return jsonify({"error": "too_low", "message": "Mindestens 10 Sekunden, um den Miniserver nicht zu überlasten."}), 400
    settings_repository.set_setting("loxone_poll_interval_seconds", str(seconds))
    return jsonify({"ok": True, "seconds": seconds})


@app.route("/api/settings/bmf-reference", methods=["GET"])
def api_get_bmf_reference():
    """FA-LS-BMF-01: optionale BMF-Schreiben-Referenz auf dem Ladestrom-Beleg
    (Standard: aus — siehe FA-LS-07, Beleg bleibt auf Wunsch des Auftraggebers
    standardmäßig neutral/fallunabhängig ohne Rechtstext)."""
    return jsonify({"enabled": settings_repository.get_setting("show_bmf_reference") == "1"})


@app.route("/api/settings/bmf-reference", methods=["PUT"])
def api_set_bmf_reference():
    data = request.get_json(force=True)
    enabled = bool(data.get("enabled"))
    settings_repository.set_setting("show_bmf_reference", "1" if enabled else "0")
    return jsonify({"ok": True, "enabled": enabled})


@app.route("/api/settings/vehicle-description", methods=["GET"])
def api_get_vehicle_description():
    return jsonify({"description": settings_repository.get_setting("vehicle_description")})


@app.route("/api/settings/vehicle-description", methods=["PUT"])
def api_set_vehicle_description():
    data = request.get_json(force=True)
    description = (data.get("description") or "").strip()
    settings_repository.set_setting("vehicle_description", description)
    return jsonify({"ok": True, "description": description})


@app.route("/api/events", methods=["GET"])
def api_events():
    """FA-LOG-01: Protokoll-/Log-Ansicht — verpflichtender Programmpunkt."""
    source = request.args.get("source")
    level = request.args.get("level")
    events = event_log_service.list_events(limit=150, source=source, level=level)
    return jsonify({"events": events})


@app.route("/api/ocpp/raw-log/export", methods=["GET"])
def api_ocpp_raw_log_export():
    """Rohdaten-Logdatei als CSV-Download."""
    lines = ocpp_log_service.get_log_file_tail(max_lines=10000)
    csv_lines = ["zeitpunkt;richtung;charge_point;nachricht"]
    for line in lines:
        parts = line.split(" ", 3)
        if len(parts) >= 4:
            ts   = f"{parts[0]} {parts[1]}"
            arrow = parts[2]
            rest = parts[3]
            # Charge-Point-ID aus "[WB1]" extrahieren
            cp = ""
            msg = rest
            if rest.startswith("[") and "]" in rest:
                end = rest.index("]")
                cp  = rest[1:end]
                msg = rest[end+2:] if len(rest) > end+1 else ""
            dir_label = "eingehend" if arrow == "->" else "ausgehend"
            csv_lines.append(f'"{ts}";"{dir_label}";"{cp}";"{msg.replace(chr(34), chr(39))}"')
    csv_content = "\n".join(csv_lines)
    return Response(
        csv_content, mimetype="text/csv; charset=utf-8",
        headers={"Content-Disposition": f"attachment; filename=ocpp_raw_{datetime.now().strftime('%Y%m%d_%H%M')}.csv"}
    )


@app.route("/api/ocpp/raw-log", methods=["DELETE"])
def api_ocpp_raw_log_delete():
    """Löscht die Rohdaten-Logdatei (Protokoll leeren)."""
    import os
    log_path = ocpp_log_service.LOG_FILE_PATH
    try:
        if os.path.exists(log_path):
            os.remove(log_path)
        event_log_service.log_event("manual", "info", "OCPP-Rohdaten-Logdatei gelöscht.")
        return jsonify({"ok": True})
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.route("/api/events/export", methods=["GET"])
def api_events_export():
    """Protokoll als Textdatei herunterladen.

    Fuer Rueckfragen: Wer ein Problem meldet, kann die Datei anhaengen
    statt Zeilen abzutippen. Der Zeitraum laesst sich begrenzen, damit
    nicht Monate alter Ballast mitkommt.
    """
    from flask import Response
    tage = request.args.get("tage", type=int) or 7
    quelle = (request.args.get("quelle") or "").strip()

    conn = get_connection()
    try:
        bedingungen = ["created_at >= datetime('now', ?)"]
        werte = [f"-{max(1, min(365, tage))} days"]
        if quelle:
            bedingungen.append("source = ?")
            werte.append(quelle)

        zeilen = conn.execute(
            f"""SELECT created_at, source, level, message
                FROM event_log WHERE {' AND '.join(bedingungen)}
                ORDER BY created_at""", werte).fetchall()
    finally:
        conn.close()

    kopf = [
        "eCharge@Home — Protokollauszug",
        f"Erstellt:  {datetime.now().strftime('%d.%m.%Y %H:%M:%S')}",
        f"Zeitraum:  letzte {tage} Tage" + (f" · Quelle: {quelle}" if quelle else ""),
        f"Version:   {PFLICHTENHEFT_VERSION}",
        f"Einträge:  {len(zeilen)}",
        "=" * 78,
        "",
    ]
    inhalt = "\n".join(kopf) + "\n".join(
        f"{z['created_at']}  {(z['level'] or '').upper():7}  "
        f"{(z['source'] or ''):12}  {z['message']}"
        for z in zeilen)

    name = f"echarge-protokoll-{datetime.now().strftime('%Y%m%d-%H%M')}.txt"
    return Response(inhalt, mimetype="text/plain; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="{name}"'})


@app.route("/api/event-log/clear", methods=["DELETE"])
def api_event_log_clear():
    """Löscht alle Einträge im Event-Log (Protokoll leeren)."""
    conn = db_service.get_connection()
    try:
        conn.execute("DELETE FROM event_log")
        conn.commit()
        return jsonify({"ok": True})
    finally:
        conn.close()


@app.route("/api/ocpp/raw-log", methods=["GET"])
def api_ocpp_raw_log():
    """Liefert die letzten Zeilen der OCPP-Rohdaten-Logdatei direkt."""
    lines = ocpp_log_service.get_log_file_tail(max_lines=int(request.args.get("n", 200)))
    info = ocpp_log_service.get_log_file_info()
    return jsonify({"lines": lines, "count": len(lines), "file_info": info})


@app.route("/api/ocpp/port", methods=["POST"])
def api_ocpp_port():
    """Port des eingebauten OCPP-Servers aendern.

    9000 ist haeufig belegt — Portainer nutzt ihn standardmaessig. Ohne
    Wahlmoeglichkeit muesste der Anwender den anderen Dienst umziehen.
    """
    daten = request.get_json(force=True, silent=True) or {}
    try:
        port = int(daten.get("port", 0))
    except (TypeError, ValueError):
        return jsonify({"ok": False, "fehler": "Ungültige Portangabe."}), 400
    if not (1024 <= port <= 65535):
        return jsonify({"ok": False,
                        "fehler": "Der Port muss zwischen 1024 und 65535 liegen."}), 400

    # Belegt? Dann gar nicht erst uebernehmen — sonst startet der Dienst
    # nicht mehr und der Anwender sucht die Ursache woanders.
    if not ocpp_port_service.ist_frei(port) and port != ocpp_port_service.ocpp_port():
        # Der eigene Dienst darf den Port natuerlich schon halten
        return jsonify({"ok": False,
                        "fehler": f"Port {port} ist bereits belegt."}), 409

    settings_repository.set_setting("ocpp_port", str(port))
    event_log_service.log_event("system", "info", f"OCPP-Port auf {port} geändert.")
    # Der OCPP-Server laeuft in einem eigenen Vorgang und liest den Port beim
    # Start. Ist er gerade aktiv, muss er einmal aus- und wieder eingeschaltet
    # werden — darauf weist die Antwort hin.
    lief = False
    try:
        lief = bool(settings_repository.get_setting("ocpp_server_enabled") == "1")
    except Exception:
        pass
    return jsonify({"ok": True, "port": port, "neustart_noetig": lief})


@app.route("/api/ocpp/diagnose", methods=["GET"])
def api_ocpp_diagnose():
    """OCPP-Diagnose (Ruecksprache Auftraggeber: 'wir brauchen eine Logdatei,
    die uns anzeigt, welche Daten hier richtig ankommen und wo der Fehler
    steckt'): zeigt, welche OCPP-Nachrichtentypen ueberhaupt jemals
    eingegangen sind (persistente Zaehlung, siehe ocpp_log_service.py) sowie
    die letzten Zeilen der dauerhaften Rohdaten-Logdatei."""
    return jsonify({
        "message_counts": ocpp_log_service.get_message_type_counts(),
        "log_tail": ocpp_log_service.get_log_file_tail(max_lines=200),
        "expected_but_missing": [
            t for t in ("StartTransaction", "MeterValues", "StopTransaction")
            if t not in {c["message_type"] for c in ocpp_log_service.get_message_type_counts()}
        ],
    })


@app.route("/api/wallboxes/<int:wallbox_id>/toggle-polling", methods=["POST"])
def api_wallbox_toggle_polling(wallbox_id):
    """Manueller Not-Aus fuer den Hintergrund-Poller (Ruecksprache Auftrag-
    geber: Miniserver wurde durch wiederholte Fehlversuche gesperrt — braucht
    eine SOFORTIGE, garantierte Moeglichkeit, jeden weiteren Verbindungs-
    versuch zu stoppen, unabhaengig vom automatischen Backoff)."""
    if wallbox_repository.get_wallbox(wallbox_id) is None:
        return jsonify({"error": "wallbox_not_found"}), 404
    data = request.get_json(force=True)
    paused = bool(data.get("paused"))
    loxone_config_repository.set_polling_paused(wallbox_id, paused)
    event_log_service.log_event(
        "manual", "info",
        f"Polling für Wallbox #{wallbox_id} {'pausiert' if paused else 'fortgesetzt'} (manuell)."
    )
    return jsonify({"ok": True, "paused": paused})


@app.route("/api/wallboxes/<int:wallbox_id>/ocpp-client-test", methods=["POST"])
def api_ocpp_client_test(wallbox_id):
    """FA-OCPP-CLIENT-02: Verbindungstest für den OCPP-Client-Modus.
    Baut eine kurze WebSocket-Verbindung auf, sendet BootNotification
    und prueft ob der externe Server antwortet. Wird direkt nach dem
    Speichern aufgerufen, damit der Nutzer sofort weiss ob die URL
    erreichbar ist — kein Raten nach dem Speichern."""
    import asyncio
    data = request.get_json(force=True, silent=True) or {}
    remote_url = (data.get("remote_url") or "").strip()
    remote_charge_point_id = (data.get("remote_charge_point_id") or "").strip()
    if not remote_url or not remote_charge_point_id:
        return jsonify({"ok": False, "message": "URL und Charge-Point-ID erforderlich."}), 400

    async def _test():
        try:
            import websockets as ws_mod
            full_url = f"{remote_url.rstrip('/')}/{remote_charge_point_id}"
            async with ws_mod.connect(full_url, subprotocols=["ocpp1.6"], open_timeout=5) as ws:
                import json, uuid
                boot_msg = json.dumps([2, str(uuid.uuid4()), "BootNotification", {
                    "chargePointVendor": "ChargeAtHome",
                    "chargePointModel": "ConnectionTest",
                }])
                await ws.send(boot_msg)
                response = await asyncio.wait_for(ws.recv(), timeout=5)
                parsed = json.loads(response)
                if parsed[0] == 3:
                    return True, f"Verbunden ✓ — Server hat geantwortet"
                return False, f"Unerwartete Antwort: {response[:80]}"
        except asyncio.TimeoutError:
            return False, "Zeitüberschreitung — Server antwortet nicht innerhalb von 5s."
        except ConnectionRefusedError:
            return False, "Verbindung abgelehnt — läuft der externe OCPP-Server?"
        except OSError as e:
            return False, f"Nicht erreichbar: {e}"
        except ImportError:
            return False, "websockets-Paket nicht installiert (pip install websockets)."
        except Exception as e:
            return False, f"Fehler: {e}"

    ok, message = asyncio.run(_test())
    return jsonify({"ok": ok, "message": message})


@app.route("/api/wallboxes/<int:wallbox_id>/ocpp-client-config", methods=["GET"])
def api_get_ocpp_client_config(wallbox_id):
    """FA-OCPP-CLIENT-01: Konfiguration fuer den OCPP-Client-Modus (wir als
    Charge Point, verbinden uns zu einem externen OCPP-Dienst)."""
    config = ocpp_client_repository.get_config(wallbox_id)
    if config is None:
        return jsonify({"configured": False})
    return jsonify({"configured": True, **config})


@app.route("/api/wallboxes/<int:wallbox_id>/ocpp-client-config", methods=["PUT"])
def api_set_ocpp_client_config(wallbox_id):
    if wallbox_repository.get_wallbox(wallbox_id) is None:
        return jsonify({"error": "wallbox_not_found"}), 404
    data = request.get_json(force=True)
    remote_url = (data.get("remote_url") or "").strip()
    remote_charge_point_id = (data.get("remote_charge_point_id") or "").strip()
    enabled = bool(data.get("enabled", True))
    if not remote_url or not remote_charge_point_id:
        return jsonify({"error": "missing_fields", "message": "Bitte externe OCPP-Adresse und Charge-Point-ID angeben."}), 400
    if not (remote_url.startswith("ws://") or remote_url.startswith("wss://")):
        return jsonify({"error": "invalid_url", "message": "Adresse muss mit ws:// oder wss:// beginnen."}), 400
    ocpp_client_repository.set_config(wallbox_id, remote_url, remote_charge_point_id, enabled)
    return jsonify({"ok": True})


@app.route("/api/wallboxes/<int:wallbox_id>/ocpp-client-config", methods=["DELETE"])
def api_delete_ocpp_client_config(wallbox_id):
    ocpp_client_repository.delete_config(wallbox_id)
    return jsonify({"ok": True})


@app.route("/api/wallboxes/<int:wallbox_id>/live-metrics", methods=["GET"])
def api_wallbox_live_metrics(wallbox_id):
    """FA-LS-10: Live-Ansicht — letzte bekannte Leistung/Status + Sync-Zeitpunkt,
    UND alle uebrigen Rohfelder aus der letzten /all-Antwort (Rückmeldung des
    Auftraggebers: "wir brauchen viel mehr Informationen" — nicht nur Cp)."""
    metrics = loxone_config_repository.get_live_metrics(wallbox_id)
    if metrics is None:
        return jsonify({"has_data": False})
    raw = {}
    if metrics.get("raw_snapshot"):
        try:
            raw = json.loads(metrics["raw_snapshot"])
        except (json.JSONDecodeError, TypeError):
            raw = {}
    return jsonify({
        "has_data": True,
        "current_power_kw": metrics["current_power_kw"],
        "connected": bool(metrics["connected"]) if metrics["connected"] is not None else None,
        "last_sync_at": metrics["last_sync_at"],
        "raw_fields": raw,
    })


@app.route("/api/wallboxes/<int:wallbox_id>/check-wallbox2-log", methods=["POST"])
def api_wallbox_check_wallbox2_log(wallbox_id):
    """FA-LS-10, bevorzugte Methode für Wallbox2-Bausteine: fragt /all ab und
    erzeugt bei Aenderung des Lcl-Feldes sofort eine fertige Session — für
    direktes Feedback statt auf den 60s-Hintergrund-Poller zu warten."""
    user = _current_user()
    if user is None:
        return jsonify({"error": "no_user"}), 400
    wb = wallbox_repository.get_wallbox(wallbox_id)
    if wb is None:
        return jsonify({"error": "wallbox_not_found"}), 404

    data = request.get_json(force=True)
    wallbox_uuid = (data.get("loxone_uuid") or "").strip()
    host, username, password, error = _resolve_loxone_credentials(data, wallbox_id)
    if error:
        return jsonify({"ok": False, "message": error})
    if not wallbox_uuid:
        return jsonify({"ok": False, "message": "Bitte die Wallbox-UUID angeben."})

    all_values, message = loxone_api_service.get_wallbox_all_values(host, username, password, wallbox_uuid)
    if all_values is None:
        return jsonify({"ok": False, "message": message})

    result = loxone_wallbox2_service.process_all_values(wallbox_id, user["id"], all_values, user["default_kwh_price"])
    return jsonify({"ok": True, "all_values": all_values, **result})


@app.route("/api/wallboxes/ftp-browse", methods=["POST"])
def api_wallboxes_ftp_browse():
    """Wieder eingebaut auf ausdruecklichen Wunsch des Auftraggebers — echtes
    Durchsuchen des Miniserver-Dateisystems per FTP, um herauszufinden, wo die
    in der Loxone-App sichtbare Historie tatsaechlich als Datei liegt."""
    data = request.get_json(force=True)
    path = data.get("path") or "/"
    host, username, password, error = _resolve_loxone_credentials(data, data.get("wallbox_id"))
    if error:
        return jsonify({"ok": False, "message": error})
    entries, message = loxone_ftp_service.list_directory(host, username, password, path)
    if entries is None:
        return jsonify({"ok": False, "message": message})
    return jsonify({"ok": True, "path": path, "entries": entries})


@app.route("/api/wallboxes/ftp-download", methods=["POST"])
def api_wallboxes_ftp_download():
    """Laedt eine per FTP gefundene Datei komplett herunter, zur Ansicht/Analyse
    im Browser — z. B. um zu pruefen, ob eine gefundene Datei die gesuchte
    Ladehistorie enthaelt."""
    data = request.get_json(force=True)
    path = (data.get("path") or "").strip()
    if not path:
        return jsonify({"ok": False, "message": "Bitte einen Dateipfad angeben."})
    host, username, password, error = _resolve_loxone_credentials(data, data.get("wallbox_id"))
    if error:
        return jsonify({"ok": False, "message": error})
    content, message = loxone_ftp_service.download_file(host, username, password, path)
    if content is None:
        return jsonify({"ok": False, "message": message})
    return send_file(
        io.BytesIO(content), mimetype="text/plain", as_attachment=True,
        download_name=path.strip("/").split("/")[-1] or "download.txt",
    )


@app.route("/api/wallboxes/loxone-structure", methods=["POST"])
def api_wallboxes_loxone_structure():
    """Liefert eine normale Auswahlliste von Bausteinen statt der rohen LoxAPP3.json.

    Filtert standardmaessig auf Wallbox-Bausteine (erkennbar an Typ/Namen
    "Wallbox") — Rueckmeldung des Auftraggebers: eine Liste mit hunderten
    unsortierten Bausteinen (Lampen, Beschattung etc.) ist nicht praktikabel.
    Mit all=1 kann weiterhin die volle, ungefilterte Liste abgerufen werden
    (z. B. falls die automatische Erkennung bei einem individuellen Namen
    versagt)."""
    data = request.get_json(force=True)
    show_all = bool(data.get("show_all"))
    host, username, password, error = _resolve_loxone_credentials(data, data.get("wallbox_id"))
    if error:
        return jsonify({"error": "host_required", "message": error}), 400
    structure = loxone_api_service.get_structure_file(host, username or None, password or None)
    if structure is None:
        return jsonify({"error": "structure_unreachable", "controls": []})
    controls = loxone_api_service.list_structure_controls(structure, wallbox_only=not show_all)
    return jsonify({"controls": controls, "wallbox_only": not show_all})


@app.route("/api/wallboxes/<int:wallbox_id>/import-log-text", methods=["POST"])
def api_wallbox_import_log_text(wallbox_id):
    """FA-SESS-LOG-01: Loxone-Log direkt als Text importieren (Sessions-Seite,
    Datei-Upload-Variante). Gleiche Logik wie reconcile-log, aber der Log-Text
    kommt direkt aus dem Request-Body statt vom Miniserver geholt zu werden.
    Hierher verschoben aus der Wallbox-Konfigurationsseite — ein Import gehoert
    zu den Sessions, nicht zur Wallbox-Konfiguration."""
    user = _current_user()
    if user is None:
        return jsonify({"error": "no_user"}), 400
    wb = wallbox_repository.get_wallbox(wallbox_id)
    if wb is None:
        return jsonify({"error": "wallbox_not_found"}), 404
    data = request.get_json(force=True, silent=True) or {}
    log_text = data.get("log_text", "")
    if not log_text.strip():
        return jsonify({"error": "empty_log", "message": "Keine Log-Daten im Request."}), 400
    result = loxone_log_import_service.import_full_log_text(
        wallbox_id, user["id"], log_text, user["default_kwh_price"],
    )
    event_log_service.log_event(
        "manual", "info",
        f"'{wb['name']}': Log-Datei-Upload — {result['imported']} Session(en) importiert, "
        f"{result['skipped_duplicate']} bereits vorhanden, {result['total_lines']} Zeilen geprüft."
    )
    return jsonify({"ok": True, **result})


@app.route("/api/wallboxes/<int:wallbox_id>/fetch-and-import-log", methods=["POST"])
def api_wallbox_fetch_and_import_log(wallbox_id):
    """FA-SESS-LOG-01: Loxone-Log direkt vom Miniserver holen und importieren
    (Sessions-Seite, Miniserver-Variante). Leitet an reconcile-log weiter."""
    return api_wallbox_reconcile_log(wallbox_id)


@app.route("/api/wallboxes/<int:wallbox_id>/reconcile-log", methods=["POST"])
def api_wallbox_reconcile_log(wallbox_id):
    """Loest den vollstaendigen Log-Abgleich sofort aus, statt auf den naechsten
    taeglichen Zyklus zu warten (Ausfallsicherheit, siehe loxone_log_import_service.py).
    Nuetzlich z. B. um sicherzustellen, dass eine gerade eben beendete Ladung
    schon vor dem naechsten planmaessigen Durchlauf verlaesslich importiert ist."""
    user = _current_user()
    if user is None:
        return jsonify({"error": "no_user"}), 400
    wb = wallbox_repository.get_wallbox(wallbox_id)
    if wb is None:
        return jsonify({"error": "wallbox_not_found"}), 404

    data = request.get_json(force=True, silent=True) or {}
    host, username, password, error = _resolve_loxone_credentials(data, wallbox_id)
    if error:
        return jsonify({"error": "credentials_missing", "message": error}), 400

    loxone_config_repository.ensure_log_reconcile_row(wallbox_id)
    state = loxone_config_repository.get_log_reconcile_state(wallbox_id)
    log_path = (data.get("log_path") or "").strip() or (state["log_path"] if state else "/dev/fsget/log/wallbox.log")
    if data.get("log_path"):
        loxone_config_repository.set_log_path(wallbox_id, log_path)

    log_text, fetch_error = loxone_log_import_service.fetch_log_file_http(host, username, password, log_path)
    if log_text is None:
        return jsonify({"error": "fetch_failed", "message": fetch_error}), 502

    result = loxone_log_import_service.import_full_log_text(
        wallbox_id, user["id"], log_text, user["default_kwh_price"],
    )
    loxone_config_repository.mark_log_reconciled(wallbox_id, result["imported"])
    event_log_service.log_event(
        "loxone_api", "info",
        f"'{wb['name']}': manueller Log-Abgleich — {result['imported']} Session(en) nachgetragen, "
        f"{result['skipped_duplicate']} bereits vorhanden."
    )
    return jsonify({"ok": True, **result})


@app.route("/api/wallboxes/<int:wallbox_id>/import-statistics-csv", methods=["POST"])
def api_wallbox_import_statistics_csv(wallbox_id):
    """Zuverlässige Alternative zum Live-Polling: importiert einen Loxone-Statistik-CSV-Export."""
    user = _current_user()
    if user is None:
        return jsonify({"error": "no_user"}), 400
    if wallbox_repository.get_wallbox(wallbox_id) is None:
        return jsonify({"error": "wallbox_not_found"}), 404
    if "file" not in request.files:
        return jsonify({"error": "no_file"}), 400

    csv_text = request.files["file"].read().decode("utf-8-sig")
    result = loxone_stats_import_service.parse_and_import_statistics_csv(
        csv_text, wallbox_id=wallbox_id, user_id=user["id"], price_per_kwh=user["default_kwh_price"],
    )
    return jsonify(result)


@app.route("/api/wallboxes/read-value", methods=["POST"])
def api_wallboxes_read_value():
    """Liest den aktuellen Wert eines Bausteins sofort — für direktes Feedback
    nach Auswahl in der Struktur-Liste, statt auf den Hintergrund-Poller zu warten.

    FA-UX-03: Versucht ZUERST /all (Wallbox2-spezifisch) und prüft explizit,
    ob die fürs Live-Tracking benötigten Felder (Cp/Vc/Cac) enthalten sind —
    zeigt sofort im Formular an, welche Felder tatsächlich zurückkamen, statt
    dass eine falsch konfigurierte UUID (z. B. zeigt auf einen Stromzähler
    statt den Wallbox2-Baustein) erst spaeter im Protokoll auffaellt (siehe
    Pflichtenheft-Changelog: genau das ist einmal passiert und war muehsam
    zu diagnostizieren)."""
    data = request.get_json(force=True)
    uuid = (data.get("loxone_uuid") or "").strip()
    host, username, password, error = _resolve_loxone_credentials(data, data.get("wallbox_id"))
    if error:
        return jsonify({"ok": False, "message": error})
    if not uuid:
        return jsonify({"ok": False, "message": "Bitte einen Baustein angeben."})

    all_values, all_msg = loxone_api_service.get_wallbox_all_values(host, username, password, uuid)
    if all_values is not None:
        required = ["Cp", "Vc", "Cac"]
        missing = [f for f in required if f not in all_values]
        if not missing:
            return jsonify({
                "ok": True, "wallbox2_valid": True,
                "message": f"✓ Wallbox2-Baustein korrekt erkannt — Leistung: {all_values.get('Cp')} kW, "
                           f"verbunden: {all_values.get('Vc')}, lädt: {all_values.get('Cac')}",
            })
        else:
            received = sorted(all_values.keys())
            return jsonify({
                "ok": False, "wallbox2_valid": False,
                "message": f"⚠ Antwort erhalten, aber KEIN Wallbox2-Baustein — erwartete Felder "
                           f"{missing} fehlen. Tatsächlich erhalten ({len(received)}): {received}. "
                           f"Vermutlich falscher Baustein ausgewählt (z. B. ein Stromzähler statt der Wallbox2).",
            })

    value, message = loxone_api_service.get_value_basic_auth(host, username, password, uuid)
    if value is not None:
        return jsonify({"ok": True, "value": value, "message": f"Loxone Miniserver erfolgreich verbunden — aktueller Wert: {value}"})

    opener = loxone_api_service.authenticate(host, username, password)
    if opener is not None:
        value2 = loxone_api_service.get_value(opener, host, uuid)
        if value2 is not None:
            return jsonify({"ok": True, "value": value2, "message": f"Loxone Miniserver erfolgreich verbunden — aktueller Wert: {value2}"})

    return jsonify({"ok": False, "message": f"Kein Wert lesbar: {message or all_msg}"})


@app.route("/api/wallboxes/test-connection", methods=["POST"])
def api_wallboxes_test_connection():
    """FA-LS-10 "Verbindung testen" — versucht zuerst den einfacheren HTTP-Basic-Auth-Weg
    (siehe evcc-Community, Pflichtenheft-Changelog v6.7), dann den komplexeren Token-Handshake.
    Erfolgsmeldung bewusst ohne technische Interna (Rückmeldung: 'Ausdrucksweise wie
    [Einfacher Weg]/[Token-Handshake] ist zu technisch für Endnutzer) — Fehlermeldung
    behält technische Details, da diese fürs Debugging wertvoll sind (siehe Backoff-Diagnose)."""
    data = request.get_json(force=True)
    uuid_hint = (data.get("loxone_uuid") or "").strip()
    host, username, password, error = _resolve_loxone_credentials(data, data.get("wallbox_id"))
    if error:
        return jsonify({"ok": False, "message": error})

    ok, message = loxone_api_service.test_connection_basic_auth(host, username, password, uuid_hint)
    if ok:
        return jsonify({"ok": True, "message": "Loxone Miniserver erfolgreich verbunden!"})

    ok2, message2 = loxone_api_service.test_connection(host, username, password)
    if ok2:
        return jsonify({"ok": True, "message": "Loxone Miniserver erfolgreich verbunden!"})
    return jsonify({"ok": False, "message": f"Verbindung fehlgeschlagen: {message} | Zusätzlich versucht: {message2}"})


# ---------------------------------------------------------------------------
# Sprint 4 — Auswertung & Vergleichsrechner (FA-DASH-01 bis FA-DASH-04)
# ---------------------------------------------------------------------------

@app.route("/api/analytics/monthly", methods=["GET"])
def api_analytics_monthly():
    user = _current_user()
    if user is None:
        return jsonify({"error": "no_user"}), 400
    months = int(request.args.get("months", 6))
    wallbox_id = request.args.get("wallbox_id")
    wallbox_id = int(wallbox_id) if wallbox_id else None
    classification = request.args.get("classification") or None
    data = analytics_service.monthly_kwh_cost(user["id"], months=months, wallbox_id=wallbox_id, classification=classification)
    return jsonify({"months": data})


@app.route("/api/analytics/summary", methods=["GET"])
def api_analytics_summary():
    user = _current_user()
    if user is None:
        return jsonify({"error": "no_user"}), 400
    von = request.args.get("von")
    bis = request.args.get("bis")
    wallbox_id = request.args.get("wallbox_id")
    wallbox_id = int(wallbox_id) if wallbox_id else None
    classification = request.args.get("classification") or None
    summary = analytics_service.period_summary(user["id"], von, bis, wallbox_id, classification)
    return jsonify(summary)


@app.route("/api/analytics/compare", methods=["POST"])
def api_analytics_compare():
    user = _current_user()
    if user is None:
        return jsonify({"error": "no_user"}), 400
    data = request.get_json(force=True)
    try:
        total_kwh = float(data.get("total_kwh", 0))
        real_rate = float(data.get("real_rate", 0))
        pauschale_rate = float(data.get("pauschale_rate", 0.34))
    except (TypeError, ValueError):
        return jsonify({"error": "invalid_input"}), 400
    return jsonify(analytics_service.compare_pauschale_vs_real(total_kwh, pauschale_rate, real_rate))


import hashlib
import os
import re
import sqlite3


def _persist_document(doc_type: str, period_start: str | None, period_end: str | None,
                       user_id: int, pdf_bytes: bytes) -> int:
    """Speichert PDF-Bytes als DB-BLOB (Neustart-sicher) und optional als Datei."""
    docs_dir = os.path.join(os.path.dirname(__file__), "..", "data", "documents")
    os.makedirs(docs_dir, exist_ok=True)
    checksum = hashlib.sha256(pdf_bytes).hexdigest()
    ps = period_start or "0001-01-01"
    pe = period_end   or "9999-12-31"
    filename  = f"{doc_type}_{ps}_{pe}_{checksum[:8]}.pdf"
    file_path = os.path.join(docs_dir, filename)
    try:
        with open(file_path, "wb") as f:
            f.write(pdf_bytes)
    except Exception:
        file_path = f"db-only:{checksum[:8]}"
    return document_repository.save_document(
        doc_type, ps, pe, user_id, file_path, checksum, pdf_bytes=pdf_bytes
    )


@app.route("/api/sessions/duplicate-resolve", methods=["POST"])
def api_sessions_duplicate_resolve():
    """Löst alle Doppelabrechnungs-Konflikte auf einmal.
    strategy='wallbox' → Fahrzeug-App-Sessions entfernen (MID-Messnachweis behalten)
    strategy='higher'  → jeweils niedrigeren Betrag entfernen."""
    from services import duplicate_service
    user = _current_user()
    if user is None:
        return jsonify({"error": "no_user"}), 400
    data = request.get_json(force=True) or {}
    strategy = data.get("strategy", "wallbox")
    sessions = session_repository.list_sessions(user_id=user["id"])
    if strategy == "higher":
        to_remove = duplicate_service.resolve_all_keep_higher(sessions)
    else:
        to_remove = duplicate_service.resolve_all_keep_wallbox(sessions)
    removed = 0
    fehler = []
    for sid in to_remove:
        try:
            session_repository.delete_session(sid)
            removed += 1
        except Exception as e:
            # Frueher wurde hier stumm weitergemacht. Schlug das Loeschen
            # fehl, blieb die Session bestehen — und die Warnung kam beim
            # naechsten Seitenaufruf wieder, ohne dass jemand wusste warum.
            fehler.append(f"#{sid}: {type(e).__name__}")

    if fehler:
        event_log_service.log_event("system", "warning",
            f"Doppelte Ladevorgänge: {len(fehler)} konnten nicht entfernt "
            f"werden ({', '.join(fehler[:3])}).")

    # Nachpruefen: Sind wirklich keine Konflikte mehr da? Wenn doch, hat
    # die Aufloesung nicht gegriffen und der Anwender soll das erfahren.
    rest = duplicate_service.find_overlapping_sessions(
        session_repository.list_sessions(user_id=user["id"]))
    return jsonify({"ok": True, "removed": removed, "removed_ids": to_remove,
                    "verbleibend": len(rest),
                    "fehler": fehler[:5] if fehler else None})


@app.route("/api/sessions/duplicate-check", methods=["GET"])
def api_sessions_duplicate_check():
    """Prüft alle Sessions auf Doppelabrechnungs-Risiko (Wallbox vs. Fahrzeug-App)."""
    from services import duplicate_service
    user = _current_user()
    if user is None:
        return jsonify({"conflicts": [], "mixed_sources": False})
    von = request.args.get("von")
    bis = request.args.get("bis")
    sessions = session_repository.list_sessions(user_id=user["id"])
    if von or bis:
        def in_range(s):
            ts = s.get("start_timestamp", "")[:10]
            if von and ts < von: return False
            if bis and ts > bis: return False
            return True
        sessions = [s for s in sessions if in_range(s)]
    conflicts = duplicate_service.find_overlapping_sessions(sessions)
    return jsonify({
        "conflicts": conflicts,
        "conflict_count": len(conflicts),
        "mixed_sources": duplicate_service.has_mixed_sources(sessions),
    })


@app.route("/api/vehicles", methods=["GET"])
def api_vehicles_list():
    person_id = request.args.get("person_id", type=int)
    vehicles = vehicle_repository.list_vehicles(person_id)
    return jsonify({"vehicles": vehicles})


@app.route("/api/vehicles/aus-archiv", methods=["POST"])
def api_vehicle_aus_archiv():
    """Legt ein Fahrzeug aus dem BMW-CarData-Archiv an.

    Das ZIP enthaelt Fahrgestellnummer, Kilometerstand und die
    Wartungstermine (Condition Based Service). Ohne diesen Weg muesste
    der Anwender alles abtippen — die Termine hat er sonst nur in der
    BMW-App.
    """
    datei = request.files.get("datei")
    if not datei or not datei.filename:
        return jsonify({"ok": False, "fehler": "Keine Datei erhalten."}), 400
    if not datei.filename.lower().endswith(".zip"):
        return jsonify({"ok": False,
                        "fehler": "Bitte das ZIP aus dem BMW-Portal wählen."}), 400

    import tempfile, os as _os
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".zip")
    try:
        datei.save(tmp.name)
        tmp.close()
        daten = cardata_archiv_service.lies_fahrzeugdaten(tmp.name)
        if not daten or not daten.get("vin"):
            return jsonify({"ok": False,
                            "fehler": "Im Archiv wurden keine Fahrzeugdaten "
                                      "gefunden."}), 400

        # Schon vorhanden? Dann wird aufgefrischt statt doppelt angelegt.
        vorher = vehicle_repository.list_vehicles()
        bekannt = any(v.get("vin") == daten["vin"] for v in vorher)

        daten.setdefault("bezeichnung", "BMW")
        vid = vehicle_repository.anlegen_aus_bmw(daten)

        event_log_service.log_event("bmw", "info",
            f"Fahrzeug aus Archiv {'aktualisiert' if bekannt else 'angelegt'}: "
            f"{daten['vin']} · {daten.get('km_stand', '?')} km")
        return jsonify({"ok": True, "neu": not bekannt, "vehicle_id": vid,
                        **{k: v for k, v in daten.items() if k != "bezeichnung"}})
    finally:
        try:
            _os.unlink(tmp.name)
        except Exception:
            pass


@app.route("/api/vehicles", methods=["POST"])
def api_vehicles_add():
    data = request.get_json(force=True) or {}
    person_id = data.get("person_id")
    if not person_id:
        return jsonify({"error": "person_required"}), 400
    # Fahrzeugdaten und Termine — nur die uebergebenen Felder anfassen,
    # damit von Hand gepflegte Werte beim Bearbeiten erhalten bleiben.
    zusatz = {k: data[k] for k in
              ("vin", "km_stand", "km_stand_datum", "hu_faellig",
               "service_faellig", "bremsfluessigkeit", "nutzungsart", "fahrtenbuch_ab",
               "reifen_vorne", "reifen_hinten")
              if data.get(k)}

    if data.get("id"):
        vehicle_repository.update_vehicle(
            int(data["id"]), data.get("bezeichnung", ""), data.get("kennzeichen", ""),
            data.get("antrieb", "elektro"), bool(data.get("ist_standard")))
        if zusatz:
            vehicle_repository.setze_stammdaten(int(data["id"]), zusatz)
        return jsonify({"ok": True, "id": data["id"]})

    vid = vehicle_repository.insert_vehicle(
        person_id, data.get("bezeichnung", ""), data.get("kennzeichen", ""),
        data.get("antrieb", "elektro"), bool(data.get("ist_standard")))
    if zusatz:
        vehicle_repository.setze_stammdaten(vid, zusatz)
    return jsonify({"ok": True, "id": vid})


@app.route("/api/vehicles/<int:vehicle_id>", methods=["DELETE"])
def api_vehicles_delete(vehicle_id):
    vehicle_repository.delete_vehicle(vehicle_id)
    return jsonify({"ok": True})


@app.route("/api/pkw/costs", methods=["GET"])
def api_pkw_costs_list():
    """Listet PKW-Kosten je Fahrzeug (oder Person als Fallback)."""
    vehicle_id = request.args.get("vehicle_id", type=int)
    person_id = request.args.get("person_id", type=int)
    if vehicle_id:
        costs = pkw_repository.list_pkw_costs(vehicle_id=vehicle_id)
        return jsonify({"costs": costs, "monthly_total": pkw_repository.monatliche_kosten_gesamt(vehicle_id=vehicle_id), "vehicle_id": vehicle_id})
    if not person_id:
        persons = person_repository.list_persons()
        if not persons:
            return jsonify({"costs": [], "monthly_total": 0})
        person_id = persons[0]["id"]
    costs = pkw_repository.list_pkw_costs(person_id=person_id)
    return jsonify({"costs": costs, "monthly_total": pkw_repository.monatliche_kosten_gesamt(person_id=person_id), "person_id": person_id})


@app.route("/api/pkw/costs", methods=["POST"])
def api_pkw_costs_add():
    """Fügt einen PKW-Kostenposten hinzu (je Fahrzeug)."""
    data = request.get_json(force=True) or {}
    vehicle_id = data.get("vehicle_id")
    person_id = data.get("person_id")
    if vehicle_id and not person_id:
        v = vehicle_repository.get_vehicle(int(vehicle_id))
        person_id = v["person_id"] if v else None
    if not person_id:
        persons = person_repository.list_persons()
        if not persons:
            return jsonify({"error": "no_person"}), 400
        person_id = persons[0]["id"]
    cid = pkw_repository.upsert_pkw_cost(
        person_id, data.get("kategorie", "sonstige"), data.get("bezeichnung", ""),
        float(data.get("betrag") or 0), data.get("intervall", "monatlich"),
        data.get("notiz", ""), vehicle_id=int(vehicle_id) if vehicle_id else None)
    return jsonify({"ok": True, "id": cid})


@app.route("/api/pkw/costs/<int:cost_id>", methods=["DELETE"])
def api_pkw_costs_delete(cost_id):
    pkw_repository.delete_pkw_cost(cost_id)
    return jsonify({"ok": True})


@app.route("/api/pkw/allowance", methods=["GET"])
def api_pkw_allowance_get():
    vehicle_id = request.args.get("vehicle_id", type=int)
    person_id = request.args.get("person_id", type=int)
    if vehicle_id:
        return jsonify(pkw_repository.get_car_allowance(vehicle_id=vehicle_id))
    if not person_id:
        persons = person_repository.list_persons()
        if not persons:
            return jsonify({"monatlicher_betrag": 0, "lohnsteuerklasse": 1, "versteuert": 0})
        person_id = persons[0]["id"]
    return jsonify(pkw_repository.get_car_allowance(person_id=person_id))


@app.route("/api/pkw/allowance", methods=["POST"])
def api_pkw_allowance_save():
    data = request.get_json(force=True) or {}
    vehicle_id = data.get("vehicle_id")
    person_id = data.get("person_id")
    if vehicle_id and not person_id:
        v = vehicle_repository.get_vehicle(int(vehicle_id))
        person_id = v["person_id"] if v else None
    if not person_id:
        persons = person_repository.list_persons()
        if not persons:
            return jsonify({"error": "no_person"}), 400
        person_id = persons[0]["id"]
    pkw_repository.save_car_allowance(
        person_id, float(data.get("monatlicher_betrag") or 0),
        int(data.get("lohnsteuerklasse") or 1), bool(data.get("versteuert")),
        vehicle_id=int(vehicle_id) if vehicle_id else None)
    return jsonify({"ok": True})


def _migrate_orphan_pkw_costs(conn) -> None:
    """Einmalige Bereinigung: Alt-Kostenzeilen ohne vehicle_id (aus der früheren
    Person-Ansicht) dem Standard-Fahrzeug der Person zuordnen ODER entfernen,
    wenn bereits eine identische Fahrzeug-Zeile existiert (verhindert die
    Doppelzählung in der Person-Ansicht)."""
    cols = [r["name"] for r in conn.execute("PRAGMA table_info(pkw_costs)").fetchall()]
    if "vehicle_id" not in cols:
        return
    orphans = conn.execute(
        "SELECT * FROM pkw_costs WHERE (vehicle_id IS NULL OR vehicle_id='') AND aktiv=1"
    ).fetchall()
    for o in orphans:
        # Gibt es eine identische Zeile MIT vehicle_id (gleiche Person, Kategorie, Betrag, Intervall)?
        dup = conn.execute(
            """SELECT id FROM pkw_costs
               WHERE person_id=? AND kategorie=? AND betrag=? AND intervall=?
                 AND vehicle_id IS NOT NULL AND aktiv=1 LIMIT 1""",
            (o["person_id"], o["kategorie"], o["betrag"], o["intervall"])
        ).fetchone()
        if dup:
            # Doppelt vorhanden → Alt-Zeile deaktivieren
            conn.execute("UPDATE pkw_costs SET aktiv=0 WHERE id=?", (o["id"],))
        else:
            # Einzeln → dem Standard-Fahrzeug der Person zuordnen (falls vorhanden)
            std = conn.execute(
                "SELECT id FROM vehicles WHERE person_id=? AND aktiv=1 ORDER BY ist_standard DESC, id LIMIT 1",
                (o["person_id"],)
            ).fetchone()
            if std:
                conn.execute("UPDATE pkw_costs SET vehicle_id=? WHERE id=?", (std["id"], o["id"]))
    conn.commit()


@app.route("/api/pkw/vollkosten", methods=["GET"])
def api_pkw_vollkosten():
    """Kombinierte Vollkostenrechnung — pro Fahrzeug ODER pro Person.
    Elektro-Fahrzeuge: Strom + Fahrt + Allowance − Ausgaben.
    Verbrenner: KEINE Stromerstattung (keine Wallbox-Zuordnung), nur Fahrt + Allowance − Ausgaben."""
    user = _current_user()
    if user is None:
        return jsonify({"error": "no_user"}), 400

    # Einmalige Bereinigung verwaister Alt-Kosten (behebt Doppelzählung)
    _conn = get_connection()
    try:
        _migrate_orphan_pkw_costs(_conn)
    except Exception:
        pass
    finally:
        _conn.close()

    vehicle_id = request.args.get("vehicle_id", type=int)
    persons = person_repository.list_persons()
    person_id = request.args.get("person_id", type=int)

    today = datetime.now()
    month_start = today.replace(day=1).strftime("%Y-%m-%d")
    today_str = today.strftime("%Y-%m-%d")
    contract_rate = float(settings_repository.get_setting("contract_kwh_price") or user["default_kwh_price"] or 0.34)
    km_rate = float(settings_repository.get_setting("default_km_rate") or 0.15)
    steuersatz = float(settings_repository.get_setting("persoenlicher_steuersatz") or 0.35)

    # Kontext bestimmen
    antrieb = "elektro"
    label = "Alle Fahrzeuge"
    if vehicle_id:
        veh = vehicle_repository.get_vehicle(vehicle_id)
        if not veh:
            return jsonify({"error": "vehicle_not_found"}), 404
        antrieb = veh["antrieb"]
        label = veh["bezeichnung"] + (f" ({veh['kennzeichen']})" if veh.get("kennzeichen") else "")
        person_id = veh["person_id"]
        pkw_ausgaben = pkw_repository.monatliche_kosten_gesamt(vehicle_id=vehicle_id)
        pkw_costs_list = pkw_repository.list_pkw_costs(vehicle_id=vehicle_id)
        allowance = pkw_repository.get_car_allowance(vehicle_id=vehicle_id)
        # Fahrten nur für dieses Fahrzeug
        trips = [t for t in trip_repository.list_trips(user["id"], month_start, today_str, nur_dienstlich=True)
                 if t.get("vehicle_id") == vehicle_id or t.get("vehicle_id") is None]
    else:
        if not person_id:
            person_id = persons[0]["id"] if persons else None
        if not person_id:
            return jsonify({"error": "no_person"}), 400
        label = next((p["name"] for p in persons if p["id"] == person_id), "Person")
        # WICHTIG: Person-Ansicht = Summe der Fahrzeuge dieser Person.
        # Wir aggregieren strikt über die Fahrzeuge (vehicle_id), damit jede
        # Kostenzeile GENAU EINMAL zählt. Früher wurde per person_id summiert,
        # was Alt-Einträge (person_id ohne vehicle_id) doppelt mitzählte
        # (Rückmeldung Auftraggeber: Person-Kosten waren exakt doppelt so hoch
        # wie die Fahrzeug-Kosten, obwohl nur ein Fahrzeug existiert).
        vehs = vehicle_repository.list_vehicles(person_id)
        pkw_ausgaben = 0.0
        pkw_costs_list = []
        allowance_brutto_sum = 0.0
        allowance_versteuert_any = False
        allowance_lstk = 1
        for v in vehs:
            pkw_ausgaben += pkw_repository.monatliche_kosten_gesamt(vehicle_id=v["id"])
            pkw_costs_list.extend(pkw_repository.list_pkw_costs(vehicle_id=v["id"]))
            va = pkw_repository.get_car_allowance(vehicle_id=v["id"])
            allowance_brutto_sum += float(va.get("monatlicher_betrag") or 0)
            if va.get("versteuert"):
                allowance_versteuert_any = True
            allowance_lstk = va.get("lohnsteuerklasse", allowance_lstk)
        pkw_ausgaben = round(pkw_ausgaben, 2)
        # Zusammengesetzte Allowance über alle Fahrzeuge
        allowance = {"monatlicher_betrag": allowance_brutto_sum,
                     "versteuert": 1 if allowance_versteuert_any else 0,
                     "lohnsteuerklasse": allowance_lstk}
        trips = trip_repository.list_trips(user["id"], month_start, today_str, nur_dienstlich=True)
        # Strom nur wenn mind. 1 E-Fahrzeug (oder noch gar keine Fahrzeuge)
        antrieb = "elektro" if any(v["antrieb"] == "elektro" for v in vehs) or not vehs else "verbrenner"

    # 1. Stromerstattung — NUR bei Elektro
    ls = analytics_service.period_summary(user["id"], month_start, today_str)
    if antrieb == "elektro":
        strom_erstattung = round(ls["total_kwh"] * 0.34, 2)
        strom_kosten     = round(ls["total_kwh"] * contract_rate, 2)
        strom_reinerloes = round(strom_erstattung - strom_kosten, 2)
        strom_kwh = ls["total_kwh"]
    else:
        strom_erstattung = strom_kosten = strom_reinerloes = 0.0
        strom_kwh = 0.0

    # 2. Fahrtkosten (gilt für BEIDE Antriebsarten)
    trip_km = sum(t.get("distance_km") or 0 for t in trips)
    fahrt_erstattung = round(trip_km * km_rate, 2)

    # ── Werbungskosten: Standard-Modus (Pauschale) vs. Profi-Modus (echter Satz) ──
    GESETZLICHE_PAUSCHALE = 0.30  # § 9 EStG
    # Standard: (0,30 − AG-Satz) × berufliche km
    werbungskosten_standard = round(trip_km * (GESETZLICHE_PAUSCHALE - km_rate), 2)

    # Profi-Modus: echten €/km-Satz aus Jahres-Gesamtkosten ÷ Jahresfahrleistung
    # Jahres-Gesamtkosten = PKW-Ausgaben/Monat × 12
    jahres_gesamtkosten = round(pkw_ausgaben * 12, 2)
    jahr_num = today.year
    jahr_start = today.replace(month=1, day=1).strftime("%Y-%m-%d")

    # Dienst-km im laufenden Jahr (Ist) + Hochrechnung aufs Gesamtjahr
    trips_ytd = trip_repository.list_trips(user["id"], jahr_start, today_str, nur_dienstlich=True)
    if vehicle_id:
        trips_ytd = [t for t in trips_ytd if t.get("vehicle_id") == vehicle_id or t.get("vehicle_id") is None]
    dienst_km_ytd = sum(t.get("distance_km") or 0 for t in trips_ytd)
    tage_seit_jahresbeginn = max(1, (today - today.replace(month=1, day=1)).days + 1)
    tage_im_jahr = 366 if (jahr_num % 4 == 0 and (jahr_num % 100 != 0 or jahr_num % 400 == 0)) else 365
    dienst_km_jahr = round(dienst_km_ytd / tage_seit_jahresbeginn * tage_im_jahr, 1)

    # ── Fahrtenbuch-Daten: echte Gesamtfahrleistung + Dienst/Privat-Anteil ──
    # Vorrang hat das Fahrtenbuch (km-Stand 01.01. → aktueller/31.12.-Stand),
    # weil es die tatsächliche Fahrleistung belegt. Daraus wird der reale
    # Dienstanteil abgeleitet und – falls das Jahr noch läuft – hochgerechnet.
    km_start_fb = float(settings_repository.get_setting(f"km_stand_jahresanfang_{jahr_num}") or 0)
    km_ende_fb  = float(settings_repository.get_setting(f"km_stand_jahresende_{jahr_num}") or 0)
    fahrtenbuch_aktiv = km_ende_fb > km_start_fb > 0

    if fahrtenbuch_aktiv:
        # Ist-Gesamtfahrleistung laut Fahrtenbuch (belegt durch km-Stände)
        gesamt_km_ist = km_ende_fb - km_start_fb
        # Realer Dienstanteil aus erfassten Dienstfahrten / Ist-Gesamtfahrleistung
        dienstanteil = (dienst_km_ytd / gesamt_km_ist) if gesamt_km_ist > 0 else 0
        # Hochrechnung aufs volle Steuerjahr (linear nach Tagen).
        # Ist der eingetragene Endstand bereits der Jahresende-Stand (Dez.),
        # entspricht die Hochrechnung faktisch dem Ist-Wert.
        hochrechnung_faktor = tage_im_jahr / tage_seit_jahresbeginn
        gesamt_km_jahr = round(gesamt_km_ist * hochrechnung_faktor, 1)
        # Dienstanteil bleibt konstant → Dienst-km/Jahr = Anteil × Jahres-km
        dienst_km_jahr = round(gesamt_km_jahr * dienstanteil, 1)
        privat_km_ist = round(gesamt_km_ist - dienst_km_ytd, 1)
        privat_km_jahr = round(gesamt_km_jahr - dienst_km_jahr, 1)
        km_quelle = "fahrtenbuch"
    else:
        # Fallback: manuell eingetragene Jahresfahrleistung oder Dienst-Hochrechnung
        key_km = f"jahresfahrleistung_{'v'+str(vehicle_id) if vehicle_id else 'p'+str(person_id)}"
        gesamt_km_jahr = float(settings_repository.get_setting(key_km) or 0)
        if gesamt_km_jahr <= 0:
            gesamt_km_jahr = dienst_km_jahr if dienst_km_jahr > 0 else 0
        dienstanteil = (dienst_km_jahr / gesamt_km_jahr) if gesamt_km_jahr > 0 else 0
        privat_km_jahr = round(max(0, gesamt_km_jahr - dienst_km_jahr), 1)
        privat_km_ist = privat_km_jahr
        gesamt_km_ist = gesamt_km_jahr
        km_quelle = "manuell"

    # Echter €/km-Satz = volle Jahreskosten ÷ hochgerechnete Jahres-Gesamtfahrleistung
    # (beide auf das volle Steuerjahr bezogen — die für die Steuererklärung relevante Größe)
    echter_km_satz = round(jahres_gesamtkosten / gesamt_km_jahr, 3) if gesamt_km_jahr > 0 else 0.0
    # Profi-Werbungskosten für den aktuellen Monat: (echter Satz − AG-Satz) × Dienst-km im Monat
    werbungskosten_profi = round(max(0, echter_km_satz - km_rate) * trip_km, 2)

    # ── Jahres-Vollkostenrechnung (hochgerechnet aufs Steuerjahr) ──
    # Anteilige Fahrzeugkosten, die dienstlich veranlasst sind:
    dienstlicher_kostenanteil = round(jahres_gesamtkosten * dienstanteil, 2)
    # Werbungskosten Jahr = (echter Satz − AG) × Dienst-km/Jahr
    werbungskosten_profi_jahr = round(max(0, echter_km_satz - km_rate) * dienst_km_jahr, 2)
    werbungskosten_standard_jahr = round((GESETZLICHE_PAUSCHALE - km_rate) * dienst_km_jahr, 2)
    steuer_profi_jahr = round(werbungskosten_profi_jahr * steuersatz, 2)
    steuer_standard_jahr = round(werbungskosten_standard_jahr * steuersatz, 2)

    # Aktiver Modus (gespeichert je Kontext)
    key_mode = f"km_modus_{'v'+str(vehicle_id) if vehicle_id else 'p'+str(person_id)}"
    km_modus = settings_repository.get_setting(key_mode) or "standard"
    werbungskosten = werbungskosten_profi if km_modus == "profi" else werbungskosten_standard
    fahrt_steuer = round(werbungskosten * steuersatz, 2)

    # 3. Car Allowance
    allowance_brutto = float(allowance.get("monatlicher_betrag") or 0)
    allowance_netto = round(allowance_brutto * (1 - steuersatz), 2) if allowance.get("versteuert") else allowance_brutto

    # 4. Bilanz
    einnahmen = round(strom_erstattung + fahrt_erstattung + allowance_netto, 2)
    reinerloes_cash = round(strom_reinerloes + fahrt_erstattung + allowance_netto, 2)
    bilanz = round(reinerloes_cash - pkw_ausgaben, 2)
    bilanz_inkl_steuer = round(bilanz + fahrt_steuer, 2)

    return jsonify({
        "month_label": today.strftime("%B %Y"),
        "label": label,
        "antrieb": antrieb,
        "strom": {"erstattung": strom_erstattung, "kosten": strom_kosten, "reinerloes": strom_reinerloes, "kwh": strom_kwh},
        "fahrt": {"erstattung": fahrt_erstattung, "werbungskosten": werbungskosten, "steuer_schaetzung": fahrt_steuer, "km": round(trip_km, 1)},
        "km_profi": {
            "modus": km_modus,
            "jahres_gesamtkosten": jahres_gesamtkosten,
            "gesamt_km_jahr": round(gesamt_km_jahr, 1),
            "dienst_km_jahr_geschaetzt": dienst_km_jahr,
            "dienst_km_ytd": round(dienst_km_ytd, 1),
            "echter_km_satz": echter_km_satz,
            "werbungskosten_standard": werbungskosten_standard,
            "werbungskosten_profi": werbungskosten_profi,
            "steuer_standard": round(werbungskosten_standard * steuersatz, 2),
            "steuer_profi": round(werbungskosten_profi * steuersatz, 2),
            "ag_km_satz": km_rate,
            "pauschale": GESETZLICHE_PAUSCHALE,
            # ── Fahrtenbuch-basierte Jahres-Vollkostenrechnung ──
            "km_quelle": km_quelle,
            "fahrtenbuch_aktiv": fahrtenbuch_aktiv,
            "km_start_fb": round(km_start_fb, 0),
            "km_ende_fb": round(km_ende_fb, 0),
            "gesamt_km_ist": round(gesamt_km_ist, 1),
            "privat_km_ist": round(privat_km_ist, 1),
            "dienstanteil_pct": round(dienstanteil * 100, 1),
            "privatanteil_pct": round((1 - dienstanteil) * 100, 1),
            "privat_km_jahr": privat_km_jahr,
            "dienstlicher_kostenanteil": dienstlicher_kostenanteil,
            "werbungskosten_standard_jahr": werbungskosten_standard_jahr,
            "werbungskosten_profi_jahr": werbungskosten_profi_jahr,
            "steuer_standard_jahr": steuer_standard_jahr,
            "steuer_profi_jahr": steuer_profi_jahr,
            "jahr": jahr_num,
        },
        "allowance": {"brutto": allowance_brutto, "netto": allowance_netto, "versteuert": bool(allowance.get("versteuert")), "lohnsteuerklasse": allowance.get("lohnsteuerklasse", 1)},
        "pkw_ausgaben_monat": pkw_ausgaben,
        "pkw_costs": pkw_costs_list,
        "einnahmen_gesamt": einnahmen,
        "reinerloes_cash": reinerloes_cash,
        "bilanz": bilanz,
        "bilanz_inkl_steuer": bilanz_inkl_steuer,
        "steuersatz_pct": round(steuersatz * 100),
    })


@app.route("/api/pkw/km-modus", methods=["POST"])
def api_pkw_km_modus():
    """Speichert Standard- vs. Profi-Modus und optional die Jahresfahrleistung
    je Kontext (Fahrzeug oder Person)."""
    data = request.get_json(force=True) or {}
    vehicle_id = data.get("vehicle_id")
    person_id = data.get("person_id")
    ctx = f"v{vehicle_id}" if vehicle_id else f"p{person_id}"
    if "modus" in data:
        modus = "profi" if data.get("modus") == "profi" else "standard"
        settings_repository.set_setting(f"km_modus_{ctx}", modus)
    if "gesamt_km_jahr" in data:
        settings_repository.set_setting(f"jahresfahrleistung_{ctx}", str(float(data.get("gesamt_km_jahr") or 0)))
    return jsonify({"ok": True})


@app.route("/api/tax/grenzsteuersatz", methods=["GET"])
def api_grenzsteuersatz():
    """Berechnet Grenz- und Durchschnittssteuersatz aus dem zvE (§ 32a EStG)."""
    from services import tax_service
    zve = request.args.get("zve", type=float) or 0
    splitting = request.args.get("splitting", type=int) or 1
    if zve <= 0:
        return jsonify({"error": "invalid_zve"}), 400
    return jsonify(tax_service.grenzsteuersatz(zve, splitting))


@app.route("/api/settings/steuersatz", methods=["GET"])
def api_get_steuersatz():
    val = settings_repository.get_setting("persoenlicher_steuersatz")
    return jsonify({"pct": round(float(val) * 100) if val else 35})

@app.route("/api/settings/steuersatz", methods=["POST"])
def api_set_steuersatz():
    data = request.get_json(force=True) or {}
    pct = float(data.get("pct") or 35)
    settings_repository.set_setting("persoenlicher_steuersatz", str(pct / 100.0))
    return jsonify({"ok": True})


@app.route("/api/settings/contract-kwh-price", methods=["POST"])
def api_set_contract_kwh_price():
    """Speichert den individuellen Vertragsstrompreis für das Dashboard."""
    data = request.get_json(force=True) or {}
    rate = float(data.get("rate") or 0)
    settings_repository.set_setting("contract_kwh_price", str(rate))
    return jsonify({"ok": True})


@app.route("/api/settings/contract-kwh-price", methods=["GET"])
def api_get_contract_kwh_price():
    val = settings_repository.get_setting("contract_kwh_price")
    return jsonify({"rate": float(val) if val else 0.29})


@app.route("/api/settings/setup", methods=["POST"])
def api_settings_setup():
    """Wizard-Setup: Basiseinstellungen und User anlegen."""
    data = request.get_json(force=True) or {}
    name = (data.get("name") or "").strip()
    fall = data.get("abrechnungsfall", "C")
    kwh_price = float(data.get("default_kwh_price") or 0.34)
    vehicle = (data.get("vehicle_description") or "").strip()

    conn = db_service.get_connection()
    try:
        existing = conn.execute("SELECT id FROM users_config LIMIT 1").fetchone()
        if existing:
            conn.execute(
                "UPDATE users_config SET name=?, abrechnungsfall=?, default_kwh_price=? WHERE id=?",
                (name, fall, kwh_price, existing["id"])
            )
        else:
            conn.execute(
                "INSERT INTO users_config (name, abrechnungsfall, default_kwh_price) VALUES (?,?,?)",
                (name, fall, kwh_price)
            )
        conn.commit()
    finally:
        conn.close()

    if vehicle:
        settings_repository.set_setting("vehicle_description", vehicle)
    return jsonify({"ok": True})


@app.route("/api/admin/reset-data", methods=["POST"])
def api_admin_reset_data():
    """Loescht ausgewaehlte Datenbereiche.

    Die Auswahl kommt aus der Oberflaeche. Bisher loeschte die Route immer
    dasselbe, unabhaengig davon, was angehakt war — die Kaestchen hatten
    keine Wirkung.

    Bereiche:
        bewegungsdaten  Ladevorgaenge, Fahrten, Belege, Protokolle
        wallboxen       Wallboxen samt Zustand und Messwerten
        fahrzeuge       Fahrzeuge und deren Kosten
        personen        Personen, Car Allowance, Arbeitgeber
        einstellungen   Tarife, Adressen, Anbindungen
    """
    data = request.get_json(force=True, silent=True) or {}
    if data.get("confirm") != "RESET_CONFIRMED":
        return jsonify({"error": "Bestätigung fehlt"}), 400

    bereiche = data.get("bereiche") or {}
    # Ohne Angabe der alte Umfang — so bleiben bestehende Aufrufe gueltig.
    if not bereiche:
        bereiche = {"bewegungsdaten": True}

    gruppen = {
        "bewegungsdaten": [
            "charging_sessions", "trips", "documents", "event_log",
            "ocpp_message_counts", "loxone_last_charge_log",
            "loxone_log_reconcile_state", "billing_entries", "bmw_trips",
        ],
        "wallboxen": [
            "wallbox_status", "wallbox_live_metrics", "loxone_wallbox_config",
            "loxone_poll_state", "wallboxes",
        ],
        "fahrzeuge": ["pkw_costs", "vehicles"],
        "personen": ["car_allowance", "employers", "persons"],
        "einstellungen": ["app_settings", "tariffs"],
    }

    conn = get_connection()
    geloescht = {}
    try:
        conn.execute("PRAGMA foreign_keys = OFF")
        for name, tabellen in gruppen.items():
            if not bereiche.get(name):
                continue
            anzahl = 0
            for tabelle in tabellen:
                try:
                    anzahl += conn.execute(f"DELETE FROM {tabelle}").rowcount
                except Exception:
                    pass   # Tabelle gibt es in dieser Fassung nicht
            geloescht[name] = anzahl

        # Zaehlerstaende der Loxone-Abfrage zuruecksetzen, wenn Bewegungsdaten
        # weg sind — sonst wuerde der naechste Abruf dort fortsetzen, wo es
        # keine Daten mehr gibt.
        if bereiche.get("bewegungsdaten") and not bereiche.get("wallboxen"):
            try:
                conn.execute("UPDATE loxone_poll_state SET last_log_line = NULL, "
                             "last_session_start = NULL")
            except Exception:
                pass

        conn.execute("PRAGMA foreign_keys = ON")
        conn.commit()

        was = ", ".join(k for k, v in bereiche.items() if v) or "nichts"
        event_log_service.log_event("manual", "warning",
                                    f"Zurückgesetzt: {was}")
        return jsonify({"ok": True, "geloescht": geloescht})
    except Exception as exc:
        conn.rollback()
        return jsonify({"error": str(exc)}), 500
    finally:
        conn.close()


@app.route("/api/admin/disclaimer-accepted", methods=["GET"])
def api_disclaimer_status():
    val = settings_repository.get_setting("disclaimer_accepted")
    return jsonify({"accepted": val == "1"})


@app.route("/api/admin/disclaimer-accepted", methods=["POST"])
def api_disclaimer_accept():
    settings_repository.set_setting("disclaimer_accepted", "1")
    return jsonify({"ok": True})


@app.route("/api/admin/setup-complete", methods=["GET"])
def api_setup_complete_check():
    """Prüft ob der erste Setup abgeschlossen wurde."""
    val = settings_repository.get_setting("setup_complete")
    return jsonify({"complete": val == "1"})


@app.route("/api/admin/setup-complete", methods=["POST"])
def api_setup_complete_set():
    settings_repository.set_setting("setup_complete", "1")
    return jsonify({"ok": True})


@app.route("/api/admin/wipe-all", methods=["POST"])
def api_admin_wipe_all():
    """Setzt ALLES zurück inkl. Stammdaten — echter Werksreset."""
    data = request.get_json(force=True) or {}
    if data.get("confirm") != "WIPE_ALL_CONFIRMED":
        return jsonify({"error": "Bestätigung fehlt"}), 400

    tables = [
        "charging_sessions", "trips", "documents", "event_log",
        "ocpp_message_counts", "wallbox_status", "wallbox_live_metrics",
        "loxone_last_charge_log", "loxone_log_reconcile_state",
        "loxone_poll_state", "loxone_wallbox_config", "loxone_auth_backoff",
        "wallboxes", "persons", "users_config",
        "audit_log", "ocpp_client_config",
    ]
    from services.db_service import get_connection as _get_conn2
    conn = _get_conn2()
    try:
        conn.execute("PRAGMA foreign_keys = OFF")
        for table in tables:
            try:
                conn.execute(f"DELETE FROM {table}")
            except Exception:
                pass  # Tabelle existiert vielleicht nicht in älterer DB
        # App-Einstellungen außer Disclaimer
        try:
            conn.execute("DELETE FROM app_settings WHERE key != 'disclaimer_accepted'")
        except Exception:
            pass
        conn.execute("PRAGMA foreign_keys = ON")
        conn.commit()
        return jsonify({"ok": True})
    except Exception as exc:
        conn.rollback()
        return jsonify({"error": str(exc)}), 500
    finally:
        conn.close()


@app.route("/api/server-info", methods=["GET"])
def api_server_info():
    """Liefert die eigene LAN-IP des Servers — wird im Frontend fuer die
    OCPP-URL-Anzeige benoetigt, damit auch bei Aufruf der App ueber
    'localhost' die korrekte, fuer Loxone erreichbare Adresse angezeigt wird."""
    import socket
    try:
        # UDP-Trick: kurz eine Verbindung zu einer externen Adresse oeffnen
        # (kein echtes Paket gesendet), um die lokale IP zu ermitteln die
        # fuer ausgehende Verbindungen verwendet wird — das ist die IP,
        # die auch andere Geraete im LAN zum Erreichen dieses Rechners nutzen.
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("8.8.8.8", 80))
            lan_ip = s.getsockname()[0]
    except Exception:
        lan_ip = socket.gethostbyname(socket.gethostname())
    # Port aus den Einstellungen — 9000 ist haeufig belegt (Portainer)
    return jsonify({"lan_ip": lan_ip, "ocpp_port": ocpp_port_service.ocpp_port()})


@app.route("/api/ocpp/status", methods=["GET"])
def api_ocpp_status():
    """Echter Verbindungstest statt der bisherigen fest einprogrammierten
    "OCPP-Server (Beispiel): Online"-Anzeige, die permanent in der Seitenleiste
    stand, unabhaengig davon ob der Prozess ueberhaupt lief.
    Liefert zusaetzlich den Hauptschalter-Status (Einstellungen > OCPP)."""
    import socket
    # Der OCPP-Server ist der Vollversion vorbehalten. In der Demo bleibt die
    # Wallbox-Anbindung ueber die Loxone-API moeglich — damit laesst sich der
    # Ablauf pruefen, ohne dass fremde Ladestationen betrieben werden koennen.
    if not edition_service.funktion_verfuegbar("ocpp_server"):
        return jsonify({"online": False, "enabled": False, "gesperrt": True,
                        "meldung": "Der OCPP-Server ist der Vollversion vorbehalten."})
    enabled = (settings_repository.get_setting("ocpp_server_enabled") or "1") == "1"
    try:
        with socket.create_connection(("127.0.0.1", ocpp_port_service.ocpp_port()), timeout=1.0):
            return jsonify({"online": True, "enabled": enabled})
    except OSError:
        return jsonify({"online": False, "enabled": enabled})


@app.route("/api/ocpp/toggle", methods=["POST"])
def api_ocpp_toggle():
    """Hauptschalter fuer den OCPP-Dienst (FA-Dokument Punkt 2). Deaktiviert
    lehnt der Server neue Verbindungen sofort ab und schreibt keine Ladedaten
    mehr — der Prozess selbst bleibt am Leben (kein Neustart noetig)."""
    if not edition_service.funktion_verfuegbar("ocpp_server"):
        return jsonify(edition_service.gesperrt_hinweis("ocpp_server")), 402
    data = request.get_json(force=True) or {}
    enabled = bool(data.get("enabled", True))
    settings_repository.set_setting("ocpp_server_enabled", "1" if enabled else "0")
    event_log_service.log_event("ocpp", "info",
        f"OCPP-Dienst manuell {'aktiviert' if enabled else 'deaktiviert'} (Einstellungen).")
    return jsonify({"ok": True, "enabled": enabled})


@app.route("/api/documents", methods=["GET"])
def api_documents_list():
    """Echter Belegverlauf — ersetzt die bisherige Mockup-Liste im Template.
    Optional filterbar nach Jahr/Monat (FA-LS-06-UX-Nachbesserung)."""
    user = _current_user()
    if user is None:
        return jsonify({"error": "no_user"}), 400
    year = request.args.get("year")
    month = request.args.get("month")
    docs = document_repository.list_documents(user["id"], year=year, month=month)
    for d in docs:
        d.pop("file_path", None)  # interner Pfad muss nicht ans Frontend
    return jsonify({"documents": docs})


@app.route("/api/documents/<int:document_id>", methods=["DELETE"])
def api_documents_delete(document_id):
    file_path = document_repository.delete_document(document_id)
    if file_path is None:
        return jsonify({"error": "document_not_found"}), 404
    if os.path.exists(file_path):
        os.remove(file_path)
    return jsonify({"ok": True})


@app.route("/api/documents/<int:document_id>/download", methods=["GET"])
def api_documents_download(document_id):
    doc = document_repository.get_document(document_id)
    if doc is None:
        return jsonify({"error": "document_not_found"}), 404
    # Primär: PDF-Bytes aus DB-BLOB (Neustart-sicher)
    if doc.get("pdf_data"):
        inline = request.args.get("inline") == "1"
        doc_type_label = {"ladestrom": "Ladeabrechnung", "fahrtkosten_ag": "Fahrtkostenbeleg",
                           "fahrtkosten_fa": "Jahresexport"}.get(doc["doc_type"], "Beleg")
        fname = f"{doc_type_label}_{doc['period_start']}_{doc['period_end']}.pdf"
        return send_file(io.BytesIO(bytes(doc["pdf_data"])), mimetype="application/pdf",
                         as_attachment=not inline, download_name=fname)
    # Fallback: Datei auf Disk
    if doc.get("file_path") and os.path.exists(doc["file_path"]):
        return send_file(doc["file_path"], mimetype="application/pdf", as_attachment=True,
                         download_name=os.path.basename(doc["file_path"]))
    return jsonify({"error": "document_not_found",
                    "hint": "Datei nicht mehr vorhanden und kein DB-BLOB. Bitte Beleg neu generieren."}), 404


@app.route("/api/documents/selection", methods=["GET"])
def api_document_selection():
    """Beleg aus manuell gewählten Session-IDs und Trip-IDs.
    Erzeugt Ladeabrechnung und/oder Fahrtkostenbeleg für die Auswahl."""
    user = _current_user()
    if user is None:
        return jsonify({"error": "no_user"}), 400
    session_ids = [int(x) for x in request.args.getlist("session_ids") if x.isdigit()]
    trip_ids    = [int(x) for x in request.args.getlist("trip_ids")    if x.isdigit()]
    inline      = request.args.get("inline") == "1"
    person_display = _resolve_person_display(user)
    pdf_parts = []

    if session_ids:
        sessions = [session_repository.get_session(sid) for sid in session_ids]
        sessions = [s for s in sessions if s and s.get("user_id") == user["id"]]
        if sessions:
            period = f"{min(s['start_timestamp'][:10] for s in sessions)} bis {max(s['start_timestamp'][:10] for s in sessions)}"
            pdf_parts.append(pdf_service.generate_ladestrom_beleg(
                person_display, user["abrechnungsfall"], sessions, period,
                beleg_seq=session_ids[0],
                show_bmf_reference=settings_repository.get_setting("show_bmf_reference") == "1",
                user_id=user["id"],
            ))

    if trip_ids:
        from repositories import trip_repository as _tr
        trips = [_tr.get_trip(tid) for tid in trip_ids]
        trips = [t for t in trips if t and t.get("user_id") == user["id"]]
        if trips:
            period_t = (trips[0].get("trip_date") or "")[:7]
            pdf_parts.append(pdf_service.generate_fahrtkosten_arbeitgeber_beleg(
                person_display, user["abrechnungsfall"], trips, period_t, beleg_seq=trip_ids[0]
            ))

    if not pdf_parts:
        return jsonify({"error": "no_data"}), 400

    # Mehrere PDFs → zusammenführen
    if len(pdf_parts) == 1:
        pdf_bytes = pdf_parts[0]
    else:
        from pypdf import PdfWriter, PdfReader
        writer = PdfWriter()
        for part in pdf_parts:
            reader = PdfReader(io.BytesIO(part))
            for page in reader.pages:
                writer.add_page(page)
        buf = io.BytesIO()
        writer.write(buf)
        pdf_bytes = buf.getvalue()

    return send_file(io.BytesIO(pdf_bytes), mimetype="application/pdf",
                     as_attachment=not inline,
                     download_name=f"Auswahl_Beleg.pdf")


@app.route("/api/documents/ladestrom/single/<int:session_id>", methods=["GET"])
def api_document_ladestrom_single(session_id):
    """Einzelbeleg für eine einzige Ladesession."""
    user = _current_user()
    if user is None:
        return jsonify({"error": "no_user"}), 400
    session = session_repository.get_session(session_id)
    if session is None or session.get("user_id") != user["id"]:
        return jsonify({"error": "not_found"}), 404
    person_display = _resolve_person_display(user)
    pdf_bytes = pdf_service.generate_ladestrom_beleg(
        person_display, user["abrechnungsfall"], [session],
        (session.get("start_timestamp") or "")[:10],
        beleg_seq=session_id,
        show_bmf_reference=settings_repository.get_setting("show_bmf_reference") == "1",
        user_id=user["id"],
    )
    inline = request.args.get("inline") == "1"
    return send_file(io.BytesIO(pdf_bytes), mimetype="application/pdf",
                     as_attachment=not inline,
                     download_name=f"Ladestrom_Session_{session_id}.pdf")


@app.route("/api/documents/fahrtkosten-ag/single/<int:trip_id>", methods=["GET"])
def api_document_fahrtkosten_single(trip_id):
    """Einzelbeleg für eine einzige Fahrt."""
    user = _current_user()
    if user is None:
        return jsonify({"error": "no_user"}), 400
    from repositories import trip_repository
    trip = trip_repository.get_trip(trip_id)
    if trip is None or trip.get("user_id") != user["id"]:
        return jsonify({"error": "not_found"}), 404
    person_display = _resolve_person_display(user)
    period_label = (trip.get("trip_date") or "")[:7]
    pdf_bytes = pdf_service.generate_fahrtkosten_arbeitgeber_beleg(
        person_display, user["abrechnungsfall"], [trip], period_label, beleg_seq=trip_id
    )
    inline = request.args.get("inline") == "1"
    return send_file(io.BytesIO(pdf_bytes), mimetype="application/pdf",
                     as_attachment=not inline,
                     download_name=f"Fahrtkosten_Fahrt_{trip_id}.pdf")


@app.route("/api/documents/ladestrom", methods=["GET"])
def api_document_ladestrom():
    """FA-LS-06: PDF-Belegerzeugung mit dynamischen Personen-Feldern (FA-PERS-02)."""
    user = _current_user()
    if user is None:
        return jsonify({"error": "no_user"}), 400

    von = request.args.get("von")
    bis = request.args.get("bis")
    sessions = session_repository.list_sessions(user_id=user["id"], period_start=von, period_end=bis)
    # FA-LS-BMW-02: extern geladene Sessions (z. B. Raststaette, oeffentlicher
    # Lader) sind haeufig bereits separat
    # abgerechnet (z. B. Tankkarte) und duerfen NICHT zusaetzlich in den
    # Eigenstrom-Beleg fuer zu Hause geladenen Strom einfliessen.
    sessions = [s for s in sessions if s.get("charging_location", "zuhause") == "zuhause"]

    person_display = _resolve_person_display(user)
    try:
        from datetime import datetime as _dt
        _von_fmt = _dt.strptime(von, "%Y-%m-%d").strftime("%d.%m.%Y") if von else "—"
        _bis_fmt = _dt.strptime(bis, "%Y-%m-%d").strftime("%d.%m.%Y") if bis else "—"
    except Exception:
        _von_fmt = von or "—"; _bis_fmt = bis or "—"
    period_label = f"{_von_fmt} bis {_bis_fmt}"
    pdf_bytes = pdf_service.generate_ladestrom_beleg(
        person_display, user["abrechnungsfall"], sessions, period_label, user["id"],
        show_bmf_reference=settings_repository.get_setting("show_bmf_reference") == "1",
        user_id=user["id"],
    )
    _persist_document("ladestrom", von, bis, user["id"], pdf_bytes)

    return send_file(
        io.BytesIO(pdf_bytes),
        mimetype="application/pdf",
        as_attachment=True,
        download_name=f"Ladestrom_Beleg_{von or 'gesamt'}_{bis or ''}.pdf",
    )


# ---------------------------------------------------------------------------
# Sprint 2 — Fahrtkosten-Modul (FA-FK-01 bis FA-FK-07)
# ---------------------------------------------------------------------------

@app.route("/api/trips", methods=["GET"])
def api_trips_list():
    user = _current_user()
    if user is None:
        return jsonify({"error": "no_user"}), 400
    von = request.args.get("von") or None
    bis = request.args.get("bis") or None
    trips = trip_repository.list_trips(user_id=user["id"], period_start=von, period_end=bis)
    return jsonify({"trips": [trip_service.trip_to_api_dict(t) for t in trips]})


@app.route("/api/trips/autocomplete", methods=["GET"])
def api_trips_autocomplete():
    """Photon-Adress-Autocomplete fuer die Fahrteneingabe — Tippfehler-tolerant."""
    q = request.args.get("q", "").strip()
    if len(q) < 2:
        return jsonify({"results": []})
    from services import geocoding_service
    results = geocoding_service.autocomplete_address(q, limit=5)
    return jsonify({"results": results})


@app.route("/api/trips/estimate-distance", methods=["POST"])
def api_trips_estimate_distance():
    """FA-FK-02: Distanzberechnung aus Adressen – mit bis zu 3 Alternativrouten."""
    data = request.get_json(force=True)
    start = data.get("start_address", "").strip()
    end = data.get("end_address", "").strip()
    want_alts = bool(data.get("alternatives", False))
    if not start or not end:
        return jsonify({"error": "missing_addresses"}), 400
    distance, reason, alts = geocoding_service.estimate_distance_km(start, end, want_alts)
    if distance is None:
        return jsonify({"error": "geocoding_failed", "message": reason, "distance_km": None})
    return jsonify({"distance_km": distance, "alternatives": alts})


@app.route("/api/trips", methods=["POST"])
def api_trips_create():
    """FA-FK-01/03/04: Fahrt erfassen (Distanz aus Berechnung oder manueller Eingabe)."""
    user = _current_user()
    if user is None:
        return jsonify({"error": "no_user"}), 400

    # Free-Version: Grenze je Kalendermonat, nur fuer neue Eintraege
    _monat = datetime.now().strftime("%Y-%m")
    _conn = get_connection()
    try:
        _anzahl = _conn.execute(
            """SELECT COUNT(*) c FROM trips
               WHERE user_id = ? AND strftime('%Y-%m', trip_date) = ?""",
            (user["id"], _monat)).fetchone()["c"]
    finally:
        _conn.close()
    if license_service.monats_limit_erreicht("fahrt", _anzahl, user["license_status"]):
        return jsonify({"error": "free_limit_reached", "art": "fahrt",
                        "limit": license_service.FREE_FAHRTEN_PRO_MONAT,
                        "message": (f"Free-Version: maximal "
                                    f"{license_service.FREE_FAHRTEN_PRO_MONAT} Dienstfahrten "
                                    f"pro Monat. Mit einer Pro-Lizenz unbegrenzt.")}), 403

    data = request.get_json(force=True)
    try:
        trip_date = data["trip_date"]
        start_address = data["start_address"]
        end_address = data["end_address"]
        distance_km = float(data["distance_km"])
        purpose = data["purpose"]
        rate_chosen = float(data["rate_chosen"])
    except (KeyError, ValueError):
        return jsonify({"error": "invalid_input"}), 400

    trip_id = trip_repository.insert_trip(
        user_id=user["id"], trip_date=trip_date, start_address=start_address,
        end_address=end_address, distance_km=distance_km, purpose=purpose, rate_chosen=rate_chosen,
        vehicle_id=data.get("vehicle_id"),
    )
    return jsonify({"ok": True, "trip_id": trip_id})


@app.route("/api/trips/<int:trip_id>", methods=["PUT"])
def api_trips_update(trip_id):
    user = _current_user()
    if user is None:
        return jsonify({"error": "no_user"}), 400
    existing = trip_repository.get_trip(trip_id)
    if existing is None:
        return jsonify({"error": "trip_not_found"}), 404

    data = request.get_json(force=True)
    try:
        trip_repository.update_trip(
            trip_id=trip_id, trip_date=data["trip_date"], start_address=data["start_address"],
            end_address=data["end_address"], distance_km=float(data["distance_km"]),
            purpose=data["purpose"], rate_chosen=float(data["rate_chosen"]),
        )
    except (KeyError, ValueError):
        return jsonify({"error": "invalid_input"}), 400

    write_audit_log("trips", trip_id, "manual_edit", json.dumps(existing, default=str), json.dumps(data), user["name"])
    return jsonify({"ok": True})


@app.route("/api/trips/<int:trip_id>", methods=["DELETE"])
def api_trips_delete(trip_id):
    user = _current_user()
    if user is None:
        return jsonify({"error": "no_user"}), 400
    existing = trip_repository.get_trip(trip_id)
    if existing is None:
        return jsonify({"error": "trip_not_found"}), 404
    trip_repository.delete_trip(trip_id)
    write_audit_log("trips", trip_id, "deleted", json.dumps(existing, default=str), None, user["name"])
    return jsonify({"ok": True})


@app.route("/api/documents/begleitschreiben", methods=["GET"])
def api_document_begleitschreiben():
    """Variante 2: Begleitschreiben (Anschreiben) an Personalabteilung / Fuhrparkmanagement."""
    user = _current_user()
    if user is None:
        return jsonify({"error": "no_user"}), 400
    person_display = _resolve_person_display(user)
    von = request.args.get("von", "")
    bis = request.args.get("bis", "")
    from datetime import datetime as _dt
    try:
        mon = _dt.strptime(von, "%Y-%m-%d").strftime("%B %Y") if von else "aktueller Abrechnungsmonat"
    except Exception:
        mon = von or "aktueller Abrechnungsmonat"

    name = person_display.get("name", user["name"])
    pnr  = person_display.get("personalnummer", "")
    kfz  = person_display.get("kfz_kennzeichen", "")
    rate_kwh  = float(settings_repository.get_setting("contract_kwh_price") or 0.34)
    rate_km   = float(settings_repository.get_setting("default_km_rate") or 0.15)
    heute     = __import__("datetime").date.today().strftime("%d.%m.%Y")

    text = f"""Betreff: Einreichung der Auslagenabrechnung (Ladestrom & Fahrtkosten) – {mon}

Sehr geehrte Damen und Herren,

anbei erhalten Sie die detaillierten Einzelnachweise für die im Abrechnungszeitraum angefallenen betrieblichen Mobilitätskosten zur steuerfreien Erstattung gem. § 3 Nr. 50 EStG:

Mitarbeiter: {name}{f" | Personalnr.: {pnr}" if pnr else ""}{f" | Kfz: {kfz}" if kfz else ""}

1. Heimladestrom (Wallbox):
   • Die Erfassung der Lademenge erfolgte über ein internes Zählersystem (OCPP / Loxone Wallbox2-Baustein).
   • Der Ansatz erfolgt gemäß BMF-Schreiben vom 11.11.2025 auf Basis der amtlichen Strompreispauschale
     für 2026 in Höhe von {str(rate_kwh).replace('.', ',')} €/kWh.

2. Dienstliche Fahrten:
   • Abrechnung der betrieblich veranlassten Einzelfahrten auf Basis des vereinbarten Satzes
     von {str(rate_km).replace('.', ',')} €/km.
   • Hinweis für die Lohnbuchhaltung: Der Differenzbetrag zur gesetzlichen Pauschale von 0,30 €/km
     nach § 9 Abs. 1 Satz 3 Nr. 4a EStG wird im Rahmen der persönlichen Einkommensteuererklärung
     geltend gemacht.

Ich bitte um Prüfung und Auszahlung des Gesamtbetrags über die nächste Reisekosten-/Spesenabrechnung.

Mit freundlichen Grüßen
{name}

Datum: {heute}
"""
    inline = request.args.get("inline") == "1"
    from io import BytesIO
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import mm
    from reportlab.lib import colors
    buf = BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=25*mm, rightMargin=25*mm,
                             topMargin=30*mm, bottomMargin=30*mm)
    C_DARK = colors.HexColor("#1a365d")
    head  = ParagraphStyle("h", fontName="Helvetica-Bold", fontSize=11, textColor=C_DARK,
                            spaceAfter=6, leading=15)
    body  = ParagraphStyle("b", fontName="Helvetica", fontSize=10, leading=15,
                            spaceAfter=4, textColor=colors.HexColor("#2d3748"))
    story = []
    for line in text.strip().split("\n"):
        line = line.rstrip()
        if line.startswith("Betreff:"):
            story.append(Paragraph(line, head))
        elif line == "":
            story.append(Spacer(1, 4*mm))
        else:
            story.append(Paragraph(line.replace("•", "–").replace("&", "&amp;"), body))
    doc.build(story)
    pdf_bytes = buf.getvalue()
    return send_file(BytesIO(pdf_bytes), mimetype="application/pdf",
                     as_attachment=not inline,
                     download_name=f"Begleitschreiben_{mon.replace(' ','_')}.pdf")


@app.route("/api/documents/fahrtkosten-ag", methods=["GET"])
def api_document_fahrtkosten_ag():
    """FA-FK-06: PDF Arbeitgeber-Beleg, mit dynamischen Personen-Feldern (FA-PERS-02)."""
    user = _current_user()
    if user is None:
        return jsonify({"error": "no_user"}), 400
    von = request.args.get("von")
    bis = request.args.get("bis")
    trips = trip_repository.list_trips(user_id=user["id"], period_start=von, period_end=bis, nur_dienstlich=True)
    person_display = _resolve_person_display(user)
    try:
        from datetime import datetime as _dt
        _von_fmt = _dt.strptime(von, "%Y-%m-%d").strftime("%d.%m.%Y") if von else "—"
        _bis_fmt = _dt.strptime(bis, "%Y-%m-%d").strftime("%d.%m.%Y") if bis else "—"
    except Exception:
        _von_fmt = von or "—"; _bis_fmt = bis or "—"
    period_label = f"{_von_fmt} bis {_bis_fmt}"
    pdf_bytes = pdf_service.generate_fahrtkosten_arbeitgeber_beleg(person_display, user["abrechnungsfall"], trips, period_label, user["id"])
    _persist_document("fahrtkosten_ag", von, bis, user["id"], pdf_bytes)
    inline = request.args.get("inline") == "1"
    return send_file(io.BytesIO(pdf_bytes), mimetype="application/pdf",
                     as_attachment=not inline,
                     download_name=f"Fahrtkosten_AG_Beleg_{von or 'gesamt'}.pdf")


@app.route("/api/documents/fahrtkosten-fa", methods=["GET"])
def api_document_fahrtkosten_fa():
    """FA-FK-07: PDF Finanzamt-Auswertung (Werbungskosten). Optionaler Zeitraum
    (von/bis) und optionaler AG-Satz-Override, falls für einen Zeitraum eine andere
    (oder keine) Arbeitgeber-Erstattung galt als je Fahrt gespeichert."""
    user = _current_user()
    if user is None:
        return jsonify({"error": "no_user"}), 400
    jahr = request.args.get("jahr", str(datetime.now().year))
    von = request.args.get("von") or f"{jahr}-01-01"
    bis = request.args.get("bis") or f"{jahr}-12-31"
    label = request.args.get("von") and f"{von} bis {bis}" or jahr
    trips = trip_repository.list_trips(user_id=user["id"], period_start=von, period_end=bis, nur_dienstlich=True)

    # Optionaler Erstattungssatz-Override (z. B. 0 = "keine AG-Erstattung in diesem Zeitraum")
    rate_override = request.args.get("rate_override")
    if rate_override is not None and rate_override != "":
        try:
            ro = float(rate_override)
            trips = [dict(t, rate_chosen=ro) for t in trips]
        except ValueError:
            pass

    person_display = _resolve_person_display(user)
    pdf_bytes = pdf_service.generate_fahrtkosten_finanzamt_export(person_display, user["abrechnungsfall"], trips, label, user["id"])
    _persist_document("fahrtkosten_fa", von, bis, user["id"], pdf_bytes)
    return send_file(io.BytesIO(pdf_bytes), mimetype="application/pdf", as_attachment=True,
                      download_name=f"Fahrtkosten_Finanzamt_{jahr}.pdf")


@app.route("/api/documents/fahrtenbuch", methods=["GET"])
def api_document_fahrtenbuch():
    """Fahrtenbuch-PDF als Nachweis für den individuellen Kilometersatz (Weg 2).
    Freier Zeitraum (von/bis): nur Dienstfahrten in diesem Zeitraum werden
    übernommen — z. B. ab Jobantritt / Fahrzeug-Übernahme, nicht zwingend ab 01.01.
    Privatfahrten werden aus der km-Differenz tagesgewichtet ergänzt."""
    user = _current_user()
    if user is None:
        return jsonify({"error": "no_user"}), 400

    heute = datetime.now()
    # Zeitraum: neue von/bis-Parameter haben Vorrang; Fallback auf jahr (Alt-Aufrufe)
    von = request.args.get("von")
    bis = request.args.get("bis")
    if not von or not bis:
        jahr = request.args.get("jahr", str(heute.year))
        von = f"{jahr}-01-01"
        bis = heute.strftime("%Y-%m-%d") if int(jahr) >= heute.year else f"{jahr}-12-31"

    label_zeitraum = f"{von} bis {bis}"
    # Fahrzeug bestimmen: URL-Parameter oder Standard-Fahrzeug (für Zwei-Autos-Fall)
    veh_id = request.args.get("vehicle_id", type=int)
    if veh_id is None:
        try:
            from repositories import vehicle_repository
            vehs = vehicle_repository.list_vehicles()
            std = next((v for v in vehs if v.get("ist_standard")), (vehs[0] if vehs else None))
            veh_id = std["id"] if std else None
        except Exception:
            veh_id = None
    # Nur Dienstfahrten INNERHALB des Zeitraums UND dieses Fahrzeugs
    # Bewusst ALLE Fahrten, auch private: Ein Fahrtenbuch wird nur anerkannt,
    # wenn es lueckenlos ist (R 9.5 LStR). Die Trennung erfolgt im Dokument.
    trips = trip_repository.list_trips(user_id=user["id"], period_start=von, period_end=bis,
                                        vehicle_id=veh_id)
    person_display = _resolve_person_display(user)

    # Kilometerstaende: Manuelle Eingabe hat Vorrang. Fehlt sie, werden die
    # vom Fahrzeug gemeldeten Tachostaende der importierten Fahrten genutzt —
    # ein belastbarer Nachweis, weil die Werte aus dem Bordcomputer stammen.
    km_start = request.args.get("km_start", type=float) or 0
    km_ende = request.args.get("km_ende", type=float) or 0
    if not km_start or not km_ende:
        try:
            bereich = bmw_trip_repository.km_bereich(user["id"], von, bis)
            if bereich.get("vorhanden"):
                km_start = km_start or bereich["km_start"]
                km_ende = km_ende or bereich["km_ende"]
        except Exception:
            pass

    vehicle_label = pdf_service._resolve_vehicle_label()
    pdf_bytes = pdf_service.generate_fahrtenbuch(
        person_display, trips, label_zeitraum, km_start, vehicle_label,
        km_ende=km_ende, period_start=von, period_end=bis)
    _persist_document("fahrtenbuch", von, bis, user["id"], pdf_bytes)
    return send_file(io.BytesIO(pdf_bytes), mimetype="application/pdf", as_attachment=True,
                      download_name=f"Fahrtenbuch_{von}_bis_{bis}.pdf")


@app.route("/api/settings/fahrtenbuch-zeitraum", methods=["GET"])
def api_get_fahrtenbuch_zeitraum():
    return jsonify({
        "von": settings_repository.get_setting("fahrtenbuch_von") or "",
        "bis": settings_repository.get_setting("fahrtenbuch_bis") or "",
        "km_start": float(settings_repository.get_setting("fahrtenbuch_km_start") or 0),
        "km_ende": float(settings_repository.get_setting("fahrtenbuch_km_ende") or 0),
    })


@app.route("/api/settings/fahrtenbuch-zeitraum", methods=["POST"])
def api_set_fahrtenbuch_zeitraum():
    data = request.get_json(force=True) or {}
    if data.get("von"): settings_repository.set_setting("fahrtenbuch_von", str(data["von"]))
    if data.get("bis"): settings_repository.set_setting("fahrtenbuch_bis", str(data["bis"]))
    settings_repository.set_setting("fahrtenbuch_km_start", str(float(data.get("km_start") or 0)))
    settings_repository.set_setting("fahrtenbuch_km_ende", str(float(data.get("km_ende") or 0)))
    return jsonify({"ok": True})


@app.route("/api/settings/km-jahresanfang", methods=["GET"])
def api_get_km_jahresanfang():
    jahr = request.args.get("jahr", str(datetime.now().year))
    val = settings_repository.get_setting(f"km_stand_jahresanfang_{jahr}")
    val_ende = settings_repository.get_setting(f"km_stand_jahresende_{jahr}")
    return jsonify({"jahr": jahr,
                     "km_start": float(val) if val else 0,
                     "km_ende": float(val_ende) if val_ende else 0})


@app.route("/api/settings/km-jahresanfang", methods=["POST"])
def api_set_km_jahresanfang():
    data = request.get_json(force=True) or {}
    jahr = str(data.get("jahr") or datetime.now().year)
    settings_repository.set_setting(f"km_stand_jahresanfang_{jahr}", str(float(data.get("km_start") or 0)))
    if "km_ende" in data:
        settings_repository.set_setting(f"km_stand_jahresende_{jahr}", str(float(data.get("km_ende") or 0)))
    return jsonify({"ok": True})


@app.route("/api/decision/defaults", methods=["GET"])
def api_decision_defaults():
    """Liefert die echten Startwerte des Nutzers für den Abrechnungs-Konfigurator:
    Jahres-Gesamtkosten, Fahrleistung (hochgerechnet), Dienstanteil, Car Allowance,
    Steuersatz und ggf. PV-Ladung. Der Nutzer kann beim Öffnen wählen, ob er mit
    diesen echten Werten oder mit neutralen Standardwerten startet."""
    user = _current_user()
    if user is None:
        return jsonify({"error": "no_user"}), 400

    vehicle_id = request.args.get("vehicle_id", type=int)
    # Standard-Fahrzeug bestimmen
    if vehicle_id is None:
        try:
            vehs = vehicle_repository.list_vehicles()
            std = next((v for v in vehs if v.get("ist_standard")), (vehs[0] if vehs else None))
            vehicle_id = std["id"] if std else None
        except Exception:
            vehicle_id = None

    # Jahres-Gesamtkosten aus erfassten PKW-Kosten × 12
    pkw_monat = 0.0
    antrieb = "elektro"
    kostenposten = []
    if vehicle_id:
        try:
            pkw_monat = pkw_repository.monatliche_kosten_gesamt(vehicle_id=vehicle_id)
            # Aufgeschlüsselte Kostenposten (auf Monat normiert) für die Anzeige
            for c in pkw_repository.list_pkw_costs(vehicle_id=vehicle_id):
                iv = c.get("intervall")
                m = c["betrag"]/3 if iv == "quartaerlich" else c["betrag"]/12 if iv == "jaehrlich" else c["betrag"]
                kostenposten.append({
                    "kategorie": c.get("kategorie"),
                    "bezeichnung": c.get("bezeichnung"),
                    "monat": round(m, 2),
                    "jahr": round(m * 12, 2),
                })
            veh = vehicle_repository.get_vehicle(vehicle_id)
            if veh:
                antrieb = veh.get("antrieb") or "elektro"
        except Exception:
            pass
    k_gesamt = round(pkw_monat * 12, 2)

    # Fahrleistung aus Fahrtenbuch-Zeitraum (falls vorhanden) hochrechnen
    from datetime import datetime as _dt
    heute = _dt.now()
    km_start = float(settings_repository.get_setting("fahrtenbuch_km_start") or 0)
    km_ende = float(settings_repository.get_setting("fahrtenbuch_km_ende") or 0)
    von = settings_repository.get_setting("fahrtenbuch_von") or f"{heute.year}-01-01"

    # Dienst-km aus erfassten Fahrten des laufenden Jahres
    jahr_start = f"{heute.year}-01-01"
    trips = trip_repository.list_trips(user["id"], jahr_start, heute.strftime("%Y-%m-%d"),
                                        vehicle_id=vehicle_id, nur_dienstlich=True)
    dienst_km_ytd = sum(t.get("distance_km") or 0 for t in trips)
    tage = max(1, (heute - heute.replace(month=1, day=1)).days + 1)
    tage_jahr = 366 if (heute.year % 4 == 0 and (heute.year % 100 != 0 or heute.year % 400 == 0)) else 365
    dienst_km_jahr = round(dienst_km_ytd / tage * tage_jahr, 0)

    if km_ende > km_start > 0:
        gesamt_ist = km_ende - km_start
        try:
            d_von = _dt.strptime(von, "%Y-%m-%d")
            tage_ist = max(1, (heute - d_von).days + 1)
        except Exception:
            tage_ist = tage
        # Nur hochrechnen, wenn der Zeitraum plausibel lang ist (>= 60 Tage),
        # sonst würde ein kurzer Anlaufzeitraum zu absurd hohen Jahres-km führen.
        if tage_ist >= 60:
            gesamt_km_jahr = round(gesamt_ist / tage_ist * tage_jahr, 0)
        else:
            # Kurzer Zeitraum: Ist-Wert als konservative Basis nehmen
            gesamt_km_jahr = round(gesamt_ist, 0)
    else:
        gesamt_km_jahr = max(dienst_km_jahr, 15000)

    # Car Allowance + AG-km-Satz
    ag_rate = float(settings_repository.get_setting("default_km_rate") or 0.15)
    steuersatz = float(settings_repository.get_setting("persoenlicher_steuersatz") or 0.42)

    # AG-Zuschuss: Summe ALLER Zuschuesse (Car Allowance, Tankkarte, Jobticket …)
    ag_zuschuss_brutto = 0.0
    ag_zuschuss_versteuert = True
    if vehicle_id:
        try:
            summe = pkw_repository.zuschuesse_summe(vehicle_id=vehicle_id, steuersatz=steuersatz)
            if summe["brutto_monat"] > 0:
                ag_zuschuss_brutto = summe["brutto_monat"]
                # Netto/Brutto-Verhaeltnis: steuerfreie Anteile beruecksichtigt.
                # Wenn netto == brutto, ist alles steuerfrei.
                ag_zuschuss_versteuert = summe["netto_monat"] < summe["brutto_monat"] - 0.01
            else:
                ca = pkw_repository.get_car_allowance(vehicle_id=vehicle_id)
                if ca:
                    ag_zuschuss_brutto = float(ca.get("monatlicher_betrag") or 0)
                    ag_zuschuss_versteuert = bool(ca.get("versteuert", True))
        except Exception:
            pass

    # PV-Ladung schätzen (falls PV-Kostenposten existiert – heuristisch)
    kwh_pv = 0.0

    return jsonify({
        "vehicle_id": vehicle_id,
        "antrieb": antrieb,
        "echte_werte": {
            "k_gesamt_privat": k_gesamt if k_gesamt > 0 else 9600,
            "d_gesamt": gesamt_km_jahr if gesamt_km_jahr > 0 else 20000,
            "d_dienst": dienst_km_jahr if dienst_km_jahr > 0 else 6000,
            "ag_erstattung": ag_rate,
            "steuersatz": steuersatz,
            "kwh_pv_jahr": kwh_pv,
            "ag_zuschuss_brutto": ag_zuschuss_brutto,
            "ag_zuschuss_versteuert": ag_zuschuss_versteuert,
        },
        "standard_werte": {
            "k_gesamt_privat": 9600,
            "d_gesamt": 20000,
            "d_dienst": 6000,
            "ag_erstattung": 0.15,
            "steuersatz": 0.42,
            "kwh_pv_jahr": 0,
            "ag_zuschuss_brutto": 0,
            "ag_zuschuss_versteuert": True,
        },
        "hat_echte_daten": k_gesamt > 0 or dienst_km_ytd > 0,
        "kostenposten": kostenposten,
    })


@app.route("/api/decision/calc", methods=["POST"])
def api_decision_calc():
    """Führt die Entscheidungs-Engine mit den übergebenen Parametern aus."""
    params = request.get_json(force=True) or {}
    result = decision_service.berechne_szenario(params)
    return jsonify(result)


@app.route("/api/decision/finder", methods=["POST"])
def api_decision_finder():
    """Fahrzeug-Finder: Antriebsart-Vergleich (Diesel/Benzin/BEV/PHEV)."""
    params = request.get_json(force=True) or {}
    return jsonify(decision_service.berechne_fahrzeug_finder(params))


@app.route("/api/decision/finder-defaults", methods=["GET"])
def api_decision_finder_defaults():
    """Referenz-Startwerte je Antriebsart für den Finder (im UI editierbar)."""
    return jsonify({"antriebsarten": decision_service.ANTRIEBSART_DEFAULTS})


@app.route("/api/documents/stromkosten-auswertung", methods=["GET"])
def api_document_stromkosten_auswertung():
    """Druckfertige Ladestrom- und Kosten-Auswertung (persoenliche Monatsuebersicht):
    stellt die eigenen Stromkosten der AG-Erstattung gegenueber und weist den
    Reinerloes aus. Parameter: von, bis, wallbox_id, real_rate, bmf_rate."""
    user = _current_user()
    if user is None:
        return jsonify({"error": "no_user"}), 400

    von = request.args.get("von") or None
    bis = request.args.get("bis") or None
    wallbox_id = request.args.get("wallbox_id")
    wallbox_id = int(wallbox_id) if wallbox_id else None

    sessions = session_repository.list_sessions(
        user_id=user["id"], period_start=von, period_end=bis, wallbox_id=wallbox_id)
    # 0-kWh-Sessions raus (kein Aussagewert im Nachweis)
    sessions = [s for s in sessions
                if s.get("meter_stop_wh") is not None
                and (s["meter_stop_wh"] - s["meter_start_wh"]) > 50]
    sessions.sort(key=lambda s: s.get("start_timestamp") or "")

    real_rate = float(request.args.get("real_rate")
                      or settings_repository.get_setting("contract_kwh_price") or 0.28)
    bmf_rate = float(request.args.get("bmf_rate")
                     or settings_repository.get_setting("bmf_kwh_rate") or 0.34)

    if von and bis:
        period_label = f"{von} bis {bis}"
        try:
            d1 = datetime.strptime(von, "%Y-%m-%d"); d2 = datetime.strptime(bis, "%Y-%m-%d")
            if d1.year == d2.year and d1.month == d2.month:
                monate = ["Januar","Februar","März","April","Mai","Juni","Juli",
                          "August","September","Oktober","November","Dezember"]
                period_label = f"{monate[d1.month-1]} {d1.year}"
        except Exception:
            pass
    else:
        period_label = "Gesamter Datenbestand"

    ladepunkt = ""
    if wallbox_id:
        try:
            wb = wallbox_repository.get_wallbox(wallbox_id)
            if wb:
                ladepunkt = wb.get("name") or ""
        except Exception:
            pass

    person_display = _resolve_person_display(user)
    pdf_bytes = pdf_service.generate_stromkosten_auswertung(
        person_display, sessions, period_label,
        real_rate=real_rate, bmf_rate=bmf_rate,
        ladepunkt=ladepunkt, beleg_seq=user["id"])
    _persist_document("ladestrom", von or "", bis or "", user["id"], pdf_bytes)
    return send_file(io.BytesIO(pdf_bytes), mimetype="application/pdf", as_attachment=True,
                     download_name="Ladestrom_Auswertung.pdf")


@app.route("/api/pkw/zuschuesse", methods=["GET"])
def api_zuschuesse_list():
    """Alle AG-Zuschuesse eines Fahrzeugs (Car Allowance, Tankkarte, Jobticket …)."""
    vehicle_id = request.args.get("vehicle_id", type=int)
    person_id = request.args.get("person_id", type=int)
    steuersatz = float(settings_repository.get_setting("persoenlicher_steuersatz") or 0.42)
    summe = pkw_repository.zuschuesse_summe(vehicle_id=vehicle_id, person_id=person_id,
                                            steuersatz=steuersatz)
    return jsonify({
        "zuschuesse": summe["posten"],
        "summe": {k: v for k, v in summe.items() if k != "posten"},
        "kategorien": pkw_repository.ZUSCHUSS_KATEGORIEN,
        "steuersatz": steuersatz,
    })


@app.route("/api/pkw/zuschuesse", methods=["POST"])
def api_zuschuss_add():
    data = request.get_json(force=True) or {}
    kategorie = (data.get("kategorie") or "sonstige").strip()
    betrag = float(data.get("betrag") or 0)
    if betrag <= 0:
        return jsonify({"error": "betrag_missing"}), 400
    zid = pkw_repository.add_zuschuss(
        kategorie=kategorie, betrag=betrag,
        versteuert=bool(data.get("versteuert", True)),
        vehicle_id=data.get("vehicle_id"), person_id=data.get("person_id"),
        bezeichnung=(data.get("bezeichnung") or "").strip() or None)
    return jsonify({"ok": True, "id": zid})


@app.route("/api/pkw/zuschuesse/<int:zuschuss_id>", methods=["DELETE"])
def api_zuschuss_delete(zuschuss_id):
    pkw_repository.delete_zuschuss(zuschuss_id)
    return jsonify({"ok": True})


@app.route("/api/marktpreise", methods=["GET"])
def api_marktpreise():
    """Energie-Referenzpreise als Startwerte fuer den Konfigurator.

    Bewusst ohne externe Preis-API: Die Werte dienen der Orientierung und sind
    in der Oberflaeche jederzeit von Hand ueberschreibbar. Eine Anbindung an
    einen Preisdienst wuerde Registrierung, API-Key-Verwaltung und laufende
    Pflege erfordern, ohne den Nutzen wesentlich zu erhoehen — der Anwender
    kennt seinen eigenen Tarif ohnehin genauer als jeder Durchschnittswert."""
    return jsonify({
        "quelle": "referenz",
        "stand": "Orientierungswerte 2026 — bitte an den eigenen Tarif anpassen",
        "preise": {
            "strom_haushalt":   {"wert": 0.37, "einheit": "€/kWh",
                                 "label": "Haushaltsstrom (Bundesdurchschnitt)"},
            "strom_neuvertrag": {"wert": 0.28, "einheit": "€/kWh",
                                 "label": "Haushaltsstrom (Neuvertrag)"},
            "strom_dc":         {"wert": 0.59, "einheit": "€/kWh",
                                 "label": "DC-Schnellladen (EnBW/Ionity/Aral)"},
            "diesel":           {"wert": 1.75, "einheit": "€/l", "label": "Diesel"},
            "benzin_e10":       {"wert": 1.85, "einheit": "€/l", "label": "Super E10"},
        },
        "hinweis": ("Orientierungswerte, keine Tagespreise. Trage deinen "
                    "tatsaechlichen Tarif ein — er ist immer genauer."),
    })


# ═══════════════════════════════════════════════════════════════════════════
# BMW CONNECTEDDRIVE — Fahrten-Import (Sprint 6)
# ═══════════════════════════════════════════════════════════════════════════

@app.route("/api/bmw/trips", methods=["GET"])
def api_bmw_trips():
    """Importierte Fahrten, optional nach Kategorie gefiltert."""
    if not edition_service.funktion_verfuegbar("bmw"):
        return jsonify(edition_service.gesperrt_hinweis("bmw")), 402
    user = _current_user()
    if user is None:
        return jsonify({"error": "no_user"}), 400
    kategorie = request.args.get("category") or None
    roh = settings_repository.get_setting("bmw_privat_schwelle_km")
    try:
        schwelle = float(roh) if roh not in (None, "") else 15.0
    except ValueError:
        schwelle = 15.0
    return jsonify({
        "trips": bmw_trip_repository.list_trips(user["id"], category=kategorie),
        "statistik": bmw_trip_repository.statistik(user["id"]),
        "offen": bmw_trip_repository.zaehle_offen(user["id"]),
        "schwelle_km": schwelle,
    })


@app.route("/api/bmw/schwelle", methods=["GET", "POST"])
def api_bmw_schwelle():
    """Distanzschwelle für die Vorauswahl dienstlich/privat.

    Bewusst nur ein VORSCHLAG: Die Zuordnung bleibt eine Entscheidung des
    Nutzers. Ein Fahrtenbuch mit automatisch gesetzten Klassifizierungen
    waere gegenueber dem Finanzamt schwer zu vertreten."""
    if not edition_service.funktion_verfuegbar("bmw"):
        return jsonify(edition_service.gesperrt_hinweis("bmw")), 402
    if request.method == "POST":
        data = request.get_json(force=True) or {}
        try:
            wert = max(0, float(data.get("schwelle_km", 15)))
        except (TypeError, ValueError):
            wert = 15
        settings_repository.set_setting("bmw_privat_schwelle_km", str(wert))
        return jsonify({"ok": True, "schwelle_km": wert})
    roh = settings_repository.get_setting("bmw_privat_schwelle_km")
    try:
        wert = float(roh) if roh not in (None, "") else 15.0
    except ValueError:
        wert = 15.0
    return jsonify({"schwelle_km": wert})


@app.route("/api/bmw/trips/kurze-als-privat", methods=["POST"])
def api_bmw_kurze_als_privat():
    """Sammelaktion: alle unverarbeiteten Fahrten unter der Schwelle als privat
    markieren. Erzeugt keine Eintraege in trips — private Fahrten zaehlen nur
    fuer den Gesamtkilometer-Nachweis."""
    if not edition_service.funktion_verfuegbar("bmw"):
        return jsonify(edition_service.gesperrt_hinweis("bmw")), 402
    user = _current_user()
    if user is None:
        return jsonify({"error": "no_user"}), 400
    roh = settings_repository.get_setting("bmw_privat_schwelle_km")
    try:
        schwelle = float(roh) if roh not in (None, "") else 15.0
    except ValueError:
        schwelle = 15.0

    offen = bmw_trip_repository.list_trips(user["id"], category="UNVERARBEITET")
    betroffen = [t for t in offen if (t.get("distance_km") or 0) < schwelle]
    for t in betroffen:
        bmw_trip_repository.set_category(t["id"], "PRIVAT")
    if betroffen:
        event_log_service.log_event("bmw", "info",
            f"{len(betroffen)} Fahrten unter {schwelle} km als privat markiert.")
    return jsonify({"ok": True, "anzahl": len(betroffen), "schwelle_km": schwelle,
                    "offen": bmw_trip_repository.zaehle_offen(user["id"])})


@app.route("/api/bmw/trips/<int:bmw_id>/classify", methods=["POST"])
def api_bmw_trip_classify(bmw_id):
    """1-Klick-Zuweisung: 'DIENSTLICH' legt zusaetzlich eine abrechenbare Fahrt
    in trips an, 'PRIVAT' markiert nur — die Adressdaten bleiben dann in der
    Rohtabelle und tauchen in keinem Beleg auf."""
    if not edition_service.funktion_verfuegbar("bmw"):
        return jsonify(edition_service.gesperrt_hinweis("bmw")), 402
    user = _current_user()
    if user is None:
        return jsonify({"error": "no_user"}), 400
    data = request.get_json(force=True) or {}
    kategorie = (data.get("category") or "").upper()
    if kategorie not in ("DIENSTLICH", "PRIVAT", "UNVERARBEITET"):
        return jsonify({"error": "ungueltige_kategorie"}), 400

    bmw_trip = bmw_trip_repository.get_trip(bmw_id)
    if not bmw_trip:
        return jsonify({"error": "nicht_gefunden"}), 404

    # Bei einer Korrektur MUSS ein zuvor angelegter Abrechnungseintrag
    # entfernt werden. Sonst bliebe eine faelschlich als dienstlich erfasste
    # Fahrt im Beleg stehen, obwohl sie inzwischen als privat gilt.
    alte_trip_id = bmw_trip.get("trip_id")
    if alte_trip_id and kategorie == "UNVERARBEITET":
        try:
            trip_repository.delete_trip(alte_trip_id)
            event_log_service.log_event("bmw", "info",
                f"Fahrt {bmw_id} umklassifiziert zu {kategorie} — "
                f"Abrechnungseintrag {alte_trip_id} entfernt.")
        except Exception:
            pass

    neue_trip_id = None
    if kategorie in ("DIENSTLICH", "PRIVAT"):
        # Auch private Fahrten kommen ins Fahrtenbuch — ein Fahrtenbuch wird
        # nur anerkannt, wenn es lueckenlos ist (R 9.5 LStR). Sie erhalten
        # den Satz 0,00 €/km und tauchen in keinem Beleg auf.
        if alte_trip_id:
            neue_trip_id = alte_trip_id
            standard = float(settings_repository.get_setting("default_km_rate") or 0.15)
            trip_repository.set_fahrtart(
                [alte_trip_id], "dienstlich" if kategorie == "DIENSTLICH" else "privat",
                standard_satz=standard if kategorie == "DIENSTLICH" else None)
        else:
            dienstlich = kategorie == "DIENSTLICH"
            rate = float(settings_repository.get_setting("default_km_rate") or 0.15) if dienstlich else 0.0
            datum = (bmw_trip.get("start_time") or "")[:10]
            neue_trip_id = trip_repository.insert_trip(
                user_id=user["id"], trip_date=datum,
                start_address=bmw_trip.get("start_address") or "—",
                end_address=bmw_trip.get("end_address") or "—",
                distance_km=bmw_trip.get("distance_km") or 0,
                purpose=(data.get("purpose")
                         or ("Dienstfahrt (BMW-Import)" if dienstlich else "Privatfahrt (BMW-Import)")),
                rate_chosen=rate, vehicle_id=bmw_trip.get("vehicle_id"),
                fahrtart="dienstlich" if dienstlich else "privat")

    bmw_trip_repository.set_category(bmw_id, kategorie, trip_id=neue_trip_id)
    return jsonify({"ok": True, "trip_id": neue_trip_id,
                    "offen": bmw_trip_repository.zaehle_offen(user["id"])})


# ═══════════════════════════════════════════════════════════════════════════
# DATENQUALITAET (FA-COMP-02 Zombie-Sessions, FA-COMP-03 Zaehlerueberlauf)
# ═══════════════════════════════════════════════════════════════════════════

@app.route("/api/compliance/pruefung", methods=["GET"])
def api_compliance_pruefung():
    """Prueft den Datenbestand auf auffaellige Sessions."""
    user = _current_user()
    if user is None:
        return jsonify({"error": "no_user"}), 400
    return jsonify(compliance_service.pruefbericht(user["id"]))


@app.route("/api/compliance/session/<int:session_id>/aktion", methods=["POST"])
def api_compliance_aktion(session_id):
    """Behandlung eines auffaelligen Datensatzes durch den Anwender.

    aktion:
      'schliessen'  — Zombie-Session ohne Energiemenge schliessen
      'markieren'   — als 'anomaly' kennzeichnen (bleibt sichtbar, faellt aber
                      aus der automatischen Abrechnung heraus)
      'korrigieren' — Ueberlauf-Korrektur uebernehmen (nur bei Vorschlag)"""
    user = _current_user()
    if user is None:
        return jsonify({"error": "no_user"}), 400
    data = request.get_json(force=True) or {}
    aktion = data.get("aktion")

    if aktion == "schliessen":
        compliance_service.schliesse_zombie(session_id)
    elif aktion == "markieren":
        compliance_service.markiere_als_anomalie(session_id, data.get("grund", ""))
    elif aktion == "korrigieren":
        wh = data.get("korrektur_wh")
        if not wh:
            return jsonify({"error": "kein_wert"}), 400
        sess = session_repository.get_session(session_id)
        if not sess:
            return jsonify({"error": "nicht_gefunden"}), 404
        neuer_stop = (sess["meter_start_wh"] or 0) + int(wh)
        conn = get_connection()
        try:
            conn.execute(
                """UPDATE charging_sessions SET meter_stop_wh = ?,
                   updated_at = CURRENT_TIMESTAMP WHERE id = ?""",
                (neuer_stop, session_id))
            conn.commit()
        finally:
            conn.close()
        event_log_service.log_event("system", "info",
            f"Session {session_id}: Zählerüberlauf korrigiert auf {int(wh)/1000:.1f} kWh.")
    else:
        return jsonify({"error": "unbekannte_aktion"}), 400

    return jsonify({"ok": True, "bericht": compliance_service.pruefbericht(user["id"])})


# ═══════════════════════════════════════════════════════════════════════════
# BMW CARDATA — Anmeldung und Fahrtenabruf (offizielle BMW-Schnittstelle)
# ═══════════════════════════════════════════════════════════════════════════

# Voreinstellung fuer den Anlass. Wird beim ersten Aufruf gespeichert und
# ist danach frei bearbeitbar — jede Branche hat eigene Begriffe.
ANLASS_STANDARD = ['Kundentermin', 'Partnergespräch', 'Projektbesprechung', 'Schulung / Training', 'Meeting intern', 'Außendienstbesuch', 'Messebesuch', 'Lieferantenbesuch']


@app.route("/api/anlaesse", methods=["GET"])
def api_anlaesse_lesen():
    """Katalog der Fahrtanlaesse."""
    roh = settings_repository.get_setting("fahrt_anlaesse")
    if not roh:   # None oder leer — beides heisst: Vorschlaege verwenden
        return jsonify({"anlaesse": ANLASS_STANDARD})
    try:
        liste = json.loads(roh)
        if isinstance(liste, list):
            return jsonify({"anlaesse": [str(x) for x in liste if str(x).strip()]})
    except Exception:
        pass
    return jsonify({"anlaesse": ANLASS_STANDARD})


@app.route("/api/anlaesse", methods=["POST"])
def api_anlaesse_speichern():
    """Katalog ersetzen. Leere Eintraege und Dubletten fallen weg."""
    daten = request.get_json(force=True, silent=True) or {}
    roh = daten.get("anlaesse")
    if not isinstance(roh, list):
        return jsonify({"ok": False, "fehler": "Ungültige Liste."}), 400

    # Reihenfolge erhalten, Dubletten entfernen — dict.fromkeys statt set,
    # damit die vom Anwender gewaehlte Sortierung bestehen bleibt.
    sauber = list(dict.fromkeys(
        s.strip() for s in (str(x) for x in roh) if s.strip()))
    if len(sauber) > 60:
        return jsonify({"ok": False,
                        "fehler": "Höchstens 60 Einträge."}), 400

    if not sauber:
        # Leere Liste heisst: zurueck zu den Vorschlaegen. Waere sie als
        # leeres Feld gespeichert, bliebe die Auswahl dauerhaft leer.
        settings_repository.set_setting("fahrt_anlaesse", "")
        return jsonify({"ok": True, "anzahl": len(ANLASS_STANDARD),
                        "zurueckgesetzt": True})

    settings_repository.set_setting("fahrt_anlaesse", json.dumps(sauber, ensure_ascii=False))
    return jsonify({"ok": True, "anzahl": len(sauber)})


@app.route("/api/fahrten/vollstaendigkeit", methods=["GET"])
def api_fahrten_vollstaendigkeit():
    """Prueft, ob das Fahrtenbuch lueckenlos ist — falls es eines sein soll.

    Bei der Nutzungsart 'reisekosten' ist die Frage gegenstandslos: Dort
    werden nur dienstliche Fahrten nachgewiesen, Luecken sind unerheblich.
    Beim 'fahrtenbuch' dagegen fuehrt eine einzige fehlende Fahrt dazu,
    dass das Finanzamt die gesamte Aufzeichnung verwirft
    (§ 6 Abs. 1 Nr. 4 EStG).
    """
    user = _current_user()
    if user is None:
        return jsonify({"error": "no_user"}), 400

    fahrzeuge = vehicle_repository.list_vehicles()
    relevante = [f for f in fahrzeuge if f.get("nutzungsart") == "fahrtenbuch"]
    if not relevante:
        return jsonify({"ok": True, "pruefung_noetig": False})

    conn = get_connection()
    ergebnisse = []
    try:
        for fz in relevante:
            zeilen = conn.execute(
                """SELECT trip_date, distance_km, start_address, end_address
                   FROM trips WHERE user_id = ? AND vehicle_id = ?
                   ORDER BY trip_date""",
                (user["id"], fz["id"])).fetchall()
            if not zeilen:
                continue

            gefahren = sum((z["distance_km"] or 0) for z in zeilen)
            km_stand = fz.get("km_stand")

            eintrag = {
                "fahrzeug": fz.get("bezeichnung"),
                "fahrten": len(zeilen),
                "erfasste_km": round(gefahren, 1),
                "von": zeilen[0]["trip_date"],
                "bis": zeilen[-1]["trip_date"],
            }

            # Wenn ein Kilometerstand bekannt ist, laesst sich die Luecke beziffern
            if km_stand:
                # Erster bekannter Stand: aus der aeltesten Fahrt ableiten
                eintrag["km_stand_aktuell"] = km_stand
            ergebnisse.append(eintrag)
    finally:
        conn.close()

    return jsonify({"ok": True, "pruefung_noetig": True,
                    "fahrzeuge": ergebnisse})


@app.route("/api/fahrten/adressen-aufloesen", methods=["POST"])
def api_adressen_aufloesen():
    """Koordinaten in bereits gespeicherten Fahrten nachtraeglich aufloesen.

    Fahrten, die vor dem Einbau der Rueckwaertssuche importiert wurden,
    tragen als Adresse nur die Koordinate. Das laesst sich nachholen, ohne
    den Import zu wiederholen.
    """
    user = _current_user()
    if user is None:
        return jsonify({"error": "no_user"}), 400

    import re as _re
    from services import geocoding_service
    # Eine Adresse, die nur aus zwei Zahlen besteht — nichts anderes wird
    # angefasst, damit von Hand eingetragene Orte unberuehrt bleiben.
    muster = _re.compile(r"^\s*-?\d{1,3}\.\d+\s*,\s*-?\d{1,3}\.\d+\s*$")

    conn = get_connection()
    geaendert = 0
    try:
        zeilen = conn.execute(
            """SELECT id, start_address, end_address FROM trips
               WHERE user_id = ?""", (user["id"],)).fetchall()
        for z in zeilen:
            neu_start, neu_ende = z["start_address"], z["end_address"]
            for feld, wert in (("start", z["start_address"]),
                               ("ende", z["end_address"])):
                if not wert or not muster.match(str(wert)):
                    continue
                try:
                    lat, lon = [float(x.strip()) for x in str(wert).split(",")]
                except ValueError:
                    continue
                aufgeloest = geocoding_service.adresse_aus_koordinaten(lat, lon)
                # Nur uebernehmen wenn die Aufloesung eine echte Adresse
                # geliefert hat — nicht nochmal dieselbe Koordinate.
                # Frueher stand hier ein Mustervergleich, der den Rückfall-
                # wert (dieselbe Koordinate) ebenfalls ablehnte, weil er
                # wie eine Koordinate aussah. Damit blieb geaendert=0 und
                # die Meldung lautete "keine Koordinaten gefunden".
                if aufgeloest and aufgeloest.strip() != str(wert).strip():
                    if feld == "start":
                        neu_start = aufgeloest
                    else:
                        neu_ende = aufgeloest
            if neu_start != z["start_address"] or neu_ende != z["end_address"]:
                conn.execute(
                    "UPDATE trips SET start_address = ?, end_address = ? WHERE id = ?",
                    (neu_start, neu_ende, z["id"]))
                geaendert += 1
        conn.commit()
    finally:
        conn.close()

    event_log_service.log_event("system", "info",
        f"Adressen nachgetragen: {geaendert} Fahrten.")
    return jsonify({"ok": True, "geaendert": geaendert})


@app.route("/api/wallboxes/zusammenfuehren", methods=["POST"])
def api_wallboxen_zusammenfuehren():
    """Fuehrt die automatisch angelegte BMW-Heimwallbox mit der echten zusammen.

    Frueher legte der BMW-Import "BMW (zuhause)" an, auch wenn schon eine
    Wallbox vorhanden war. Wer beides nutzt, hat danach zwei Eintraege fuer
    denselben Ladepunkt. Diese Funktion schreibt die Ladevorgaenge auf die
    echte Wallbox um und entfernt den ueberfluessigen Eintrag.
    """
    user = _current_user()
    if user is None:
        return jsonify({"error": "no_user"}), 400

    conn = get_connection()
    verschoben = 0
    geloescht = []
    try:
        # Zielwallbox: angebunden, sonst die erste ohne BMW-Namen
        ziel = conn.execute(
            """SELECT id, name FROM wallboxes
               WHERE source_type IN ('ocpp', 'loxone_api')
               ORDER BY id LIMIT 1""").fetchone()
        if not ziel:
            ziel = conn.execute(
                """SELECT id, name FROM wallboxes
                   WHERE name NOT LIKE 'BMW %' ORDER BY id LIMIT 1""").fetchone()
        if not ziel:
            return jsonify({"ok": False,
                            "fehler": "Keine Wallbox gefunden, auf die "
                                      "zusammengeführt werden könnte."}), 400

        # Nur die Heim-Variante zusammenfuehren — "unterwegs" bleibt
        # eigenstaendig, das sind fremde Ladesaeulen.
        quellen = conn.execute(
            """SELECT id, name FROM wallboxes
               WHERE name = 'BMW (zuhause)' AND id != ?""", (ziel["id"],)).fetchall()

        for q in quellen:
            cur = conn.execute(
                "UPDATE charging_sessions SET wallbox_id = ? WHERE wallbox_id = ?",
                (ziel["id"], q["id"]))
            verschoben += cur.rowcount
            conn.execute("PRAGMA foreign_keys=OFF")
            conn.execute("DELETE FROM wallboxes WHERE id = ?", (q["id"],))
            geloescht.append(q["name"])
        conn.commit()
    finally:
        conn.close()

    if verschoben or geloescht:
        event_log_service.log_event("system", "info",
            f"Wallboxen zusammengeführt: {verschoben} Ladevorgänge auf "
            f"'{ziel['name']}' übertragen.")
    return jsonify({"ok": True, "verschoben": verschoben,
                    "entfernt": geloescht, "ziel": ziel["name"]})


@app.route("/api/vehicles/<int:vehicle_id>/cardata/stream", methods=["GET"])
def api_vehicle_cardata_stream_status(vehicle_id):
    """Zustand des MQTT-Streams dieses Fahrzeugs."""
    from services import cardata_stream_service
    return jsonify(cardata_stream_service.status(vehicle_id))


@app.route("/api/vehicles/<int:vehicle_id>/cardata/stream-verbindung", methods=["GET", "POST"])
def api_vehicle_cardata_stream_verbindung(vehicle_id):
    """Host und Port fuer die Stream-Verbindung dieses Fahrzeugs.

    Beide stehen im BMW-Portal als Teil der individuellen
    Streaming-Zugangsdaten (nicht als App-Geheimnis) und sollen deshalb
    eintragbar sein statt im Code zu stehen — siehe cardata_stream_service.
    """
    from services import cardata_stream_service
    if request.method == "GET":
        return jsonify({
            "host": cardata_stream_service.mqtt_host(vehicle_id),
            "port": cardata_stream_service.mqtt_port(vehicle_id),
            "host_standard": cardata_stream_service.MQTT_HOST_STANDARD,
            "port_standard": cardata_stream_service.MQTT_PORT_STANDARD,
        })
    daten = request.get_json(force=True, silent=True) or {}
    host = str(daten.get("host") or "").strip()
    try:
        port = int(daten.get("port") or 0)
    except (TypeError, ValueError):
        return jsonify({"ok": False, "meldung": "Port muss eine Zahl sein."}), 400
    cardata_stream_service.setze_verbindung(vehicle_id, host, port)
    return jsonify({"ok": True,
                    "host": cardata_stream_service.mqtt_host(vehicle_id),
                    "port": cardata_stream_service.mqtt_port(vehicle_id)})


@app.route("/api/vehicles/<int:vehicle_id>/cardata/stream/protokoll", methods=["GET"])
def api_vehicle_cardata_stream_protokoll(vehicle_id):
    """Live-Protokoll der Stream-Verbindung dieses Fahrzeugs — direkt in
    der App statt im Terminal. Jede Verbindungsstufe (Connect, Subscribe,
    Nachricht, Fehler) landet hier, nicht nur die spärlichen Einträge im
    allgemeinen Ereignisprotokoll."""
    from services import cardata_stream_service
    return jsonify({"log_tail": cardata_stream_service.protokoll_lesen(vehicle_id)})


@app.route("/api/vehicles/<int:vehicle_id>/cardata/stream/protokoll", methods=["DELETE"])
def api_vehicle_cardata_stream_protokoll_leeren(vehicle_id):
    from services import cardata_stream_service
    cardata_stream_service.protokoll_leeren(vehicle_id)
    return jsonify({"ok": True})


@app.route("/api/vehicles/<int:vehicle_id>/cardata/stream", methods=["POST"])
def api_vehicle_cardata_stream_setzen(vehicle_id):
    """Stream dieses Fahrzeugs ein- oder ausschalten.

    Der Stream ersetzt den regelmaessigen Abruf: BMW schickt Aenderungen
    von sich aus, ohne Tageskontingent. Damit wird jede Fahrt erfasst,
    auch kurze und solche zu ungewoehnlichen Zeiten.
    """
    if not edition_service.funktion_verfuegbar("bmw"):
        return jsonify(edition_service.gesperrt_hinweis("bmw")), 402

    from services import cardata_stream_service
    daten = request.get_json(force=True, silent=True) or {}
    an = bool(daten.get("aktiv"))
    cardata_stream_service.setze_aktiv(vehicle_id, an)
    return jsonify({"ok": True, **cardata_stream_service.status(vehicle_id)})


@app.route("/api/vehicles/<int:vehicle_id>/cardata/kontingent", methods=["GET"])
def api_vehicle_cardata_kontingent(vehicle_id):
    """Verbrauchte und verbleibende Abrufe des Tages fuer dieses Fahrzeug."""
    return jsonify(cardata_service.kontingent(vehicle_id))


@app.route("/api/vehicles/<int:vehicle_id>/cardata/status", methods=["GET"])
def api_vehicle_cardata_status(vehicle_id):
    if not edition_service.funktion_verfuegbar("bmw"):
        return jsonify(edition_service.gesperrt_hinweis("bmw")), 402
    from repositories import vehicle_bmw_repository as bmw_repo
    st = cardata_service.status(vehicle_id)
    st["letzter_abruf"] = bmw_repo.get(vehicle_id)["letzter_abruf"]
    return jsonify(st)


@app.route("/api/vehicles/<int:vehicle_id>/cardata/anmelden", methods=["POST"])
def api_vehicle_cardata_anmelden(vehicle_id):
    """Schritt 1: Geraetecode fuer dieses Fahrzeug anfordern. Der Nutzer
    bestaetigt danach im Browser."""
    if not edition_service.funktion_verfuegbar("bmw"):
        return jsonify(edition_service.gesperrt_hinweis("bmw")), 402
    data = request.get_json(force=True) or {}
    return jsonify(cardata_auth_service.starte_geraeteanmeldung(
        vehicle_id, str(data.get("client_id") or "").strip(),
        mit_streaming=bool(data.get("mit_streaming"))))


@app.route("/api/vehicles/<int:vehicle_id>/cardata/tokens", methods=["POST"])
def api_vehicle_cardata_tokens(vehicle_id):
    """Schritt 2: Nach der Bestaetigung die Token fuer dieses Fahrzeug abholen.

    Solange die Bestaetigung aussteht, meldet BMW 'authorization_pending' —
    das Frontend fragt dann in Abstaenden erneut."""
    if not edition_service.funktion_verfuegbar("bmw"):
        return jsonify(edition_service.gesperrt_hinweis("bmw")), 402
    ergebnis = cardata_auth_service.hole_tokens(vehicle_id)
    if ergebnis.get("ok"):
        event_log_service.log_event("bmw", "info",
            f"CarData-Anmeldung erfolgreich (Konto-ID {ergebnis.get('gcid','—')}). "
            f"Token gültig für 1 Stunde, Verbindung 2 Wochen.")
    elif not ergebnis.get("wartet"):
        event_log_service.log_event("bmw", "warning",
            f"CarData-Anmeldung fehlgeschlagen: {ergebnis.get('meldung','')}")
    return jsonify(ergebnis)


@app.route("/api/vehicles/<int:vehicle_id>/cardata/abmelden", methods=["POST"])
def api_vehicle_cardata_abmelden(vehicle_id):
    if not edition_service.funktion_verfuegbar("bmw"):
        return jsonify(edition_service.gesperrt_hinweis("bmw")), 402
    from services import cardata_stream_service
    cardata_stream_service.stoppe(vehicle_id)
    cardata_auth_service.abmelden(vehicle_id)
    return jsonify({"ok": True})


@app.route("/api/vehicles/<int:vehicle_id>/cardata/fahrzeuge", methods=["GET"])
def api_vehicle_cardata_fahrzeuge(vehicle_id):
    """Fahrzeuge des BMW-Kontos, das an dieses App-Fahrzeug angemeldet ist
    (verbraucht einen Abruf vom Tageslimit).

    Dient dazu, beim Anlegen 'aus BMW-Konto importieren' die passende VIN
    auszuwaehlen — die manuelle Eingabe bleibt daneben immer moeglich."""
    if not edition_service.funktion_verfuegbar("bmw"):
        return jsonify(edition_service.gesperrt_hinweis("bmw")), 402
    ergebnis = cardata_service.hole_fahrzeuge(vehicle_id)
    if not ergebnis.get("ok"):
        event_log_service.log_event("bmw", "warning",
            f"CarData Fahrzeugliste: {ergebnis.get('meldung', 'unbekannter Fehler')}")
    return jsonify(ergebnis)


@app.route("/api/vehicles/<int:vehicle_id>/cardata/vin", methods=["POST"])
def api_vehicle_cardata_vin(vehicle_id):
    """Fahrgestellnummer dieses Fahrzeugs festlegen (nach Auswahl aus der
    BMW-Kontoliste oder von Hand eingetragen)."""
    if not edition_service.funktion_verfuegbar("bmw"):
        return jsonify(edition_service.gesperrt_hinweis("bmw")), 402
    data = request.get_json(force=True) or {}
    vin = str(data.get("vin") or "").strip().upper()
    vehicle_repository.setze_stammdaten(vehicle_id, {"vin": vin})
    return jsonify({"ok": True, "vin": vin})


@app.route("/api/vehicles/<int:vehicle_id>/cardata/archiv-ladesessions", methods=["POST"])
def api_vehicle_cardata_archiv_ladesessions(vehicle_id):
    """Importiert die Ladehistorie aus dem BMW-Datenarchiv (ZIP) fuer
    dieses Fahrzeug als Ladesessions — einmaliger Vorgang fuer Zeitraeume
    vor den letzten 30 Tagen. Die laufende Verbindung (Datenstrom) deckt
    danach alles Weitere ab; das Archiv wird dafuer nicht mehr gebraucht.

    Ersetzt den frueheren Archiv-Import, der stattdessen Fahrten aus
    Ladepunkten rekonstruierte (entfernt: zwischen zwei Ladungen kann
    beliebig viel liegen, siehe cardata_archiv_service).

    Bewusst NICHT hinter "bmw" (Vollversion) gesperrt: das Einlesen einer
    bereits heruntergeladenen ZIP-Datei braucht keine laufende BMW-Anmeldung
    und belastet kein Tageskontingent — anders als die live-API-Anbindung.
    """
    if not edition_service.funktion_verfuegbar("bmw_archiv"):
        return jsonify(edition_service.gesperrt_hinweis("bmw_archiv")), 402
    user = _current_user()
    if user is None:
        return jsonify({"error": "no_user"}), 400
    if "file" not in request.files:
        return jsonify({"ok": False, "meldung": "Keine Datei übermittelt."}), 400

    datei = request.files["file"]
    if not datei.filename.lower().endswith(".zip"):
        return jsonify({"ok": False,
                        "meldung": "Bitte das ZIP-Archiv aus dem BMW-Portal hochladen."}), 400

    fahrzeug = vehicle_repository.get_vehicle(vehicle_id) or {}
    import tempfile
    with tempfile.NamedTemporaryFile(suffix=".zip", delete=False) as tmp:
        datei.save(tmp.name)
        pfad = tmp.name
    try:
        ergebnis = cardata_archiv_service.importiere_ladehistorie_datei(
            vehicle_id, pfad, user["id"], vin=fahrzeug.get("vin") or "")
    finally:
        try:
            os.unlink(pfad)
        except Exception:
            pass
    if ergebnis.get("ok") and ergebnis.get("neu"):
        event_log_service.log_event("bmw", "info",
            f"Archiv-Import: {ergebnis['neu']} Ladevorgänge übernommen.")
    return jsonify(ergebnis)


@app.route("/api/vehicles/<int:vehicle_id>/cardata/ladesessions", methods=["POST"])
def api_vehicle_cardata_ladesessions(vehicle_id):
    """Importiert die Ladehistorie der letzten 30 Tage dieses Fahrzeugs als
    Ladesessions.

    Der Ladeort wird mitgefuehrt: Nur zuhause geladener Strom faellt unter den
    steuerfreien Auslagenersatz (§ 3 Nr. 50 EStG)."""
    if not edition_service.funktion_verfuegbar("bmw"):
        return jsonify(edition_service.gesperrt_hinweis("bmw")), 402
    user = _current_user()
    if user is None:
        return jsonify({"error": "no_user"}), 400
    fahrzeug = vehicle_repository.get_vehicle(vehicle_id) or {}
    vin = fahrzeug.get("vin") or ""
    if not vin:
        return jsonify({"ok": False, "meldung": "Bitte zuerst die Fahrgestellnummer eintragen."})
    return jsonify(cardata_service.importiere_ladesessions(vehicle_id, vin, user["id"]))


@app.route("/api/vehicles/<int:vehicle_id>/cardata/fahrzeugdaten", methods=["GET"])
def api_vehicle_cardata_fahrzeugdaten(vehicle_id):
    """Zuletzt vom Fahrzeug gemeldete Stammdaten (Reichweite, Akku, Standort,
    Service) — Grundlage der Status-Kachel im Dashboard."""
    if not edition_service.funktion_verfuegbar("bmw"):
        return jsonify(edition_service.gesperrt_hinweis("bmw")), 402
    return jsonify(cardata_service.fahrzeugdaten(vehicle_id))


@app.route("/api/vehicles/<int:vehicle_id>/cardata/fahrzeugdaten/aktualisieren", methods=["POST"])
def api_vehicle_cardata_fahrzeugdaten_aktualisieren(vehicle_id):
    """Ruft die aktuellen Fahrzeugwerte einmalig ab (1 Abruf vom Tageslimit) —
    fuer die 'Jetzt aktualisieren'-Schaltflaeche an der Status-Kachel,
    unabhaengig davon, ob der Stream laeuft."""
    if not edition_service.funktion_verfuegbar("bmw"):
        return jsonify(edition_service.gesperrt_hinweis("bmw")), 402
    ergebnis = cardata_service.aktualisiere_fahrzeugdaten(vehicle_id)
    if not ergebnis.get("ok"):
        event_log_service.log_event("bmw", "warning",
            f"Fahrzeugdaten-Abruf: {ergebnis.get('meldung', 'unbekannter Fehler')}")
    return jsonify(ergebnis)


@app.route("/api/vehicles/<int:vehicle_id>/cardata/import-zuruecksetzen", methods=["POST"])
def api_vehicle_cardata_import_zuruecksetzen(vehicle_id):
    """Setzt den BMW-Import dieses Fahrzeugs vollstaendig zurueck.

    Entfernt die aus CarData stammenden Ladesessions und die
    Referenztabelle der Fahrten. Danach holt ein erneuter Abruf wirklich
    alles — ohne dass Reste den Duplikatschutz auslösen."""
    if not edition_service.funktion_verfuegbar("bmw"):
        return jsonify(edition_service.gesperrt_hinweis("bmw")), 402
    user = _current_user()
    if user is None:
        return jsonify({"error": "no_user"}), 400

    conn = get_connection()
    try:
        sessions = conn.execute(
            """DELETE FROM charging_sessions WHERE source = 'bmw_app' AND user_id = ?
               AND wallbox_id IN (SELECT id FROM wallboxes)""",
            (user["id"],)).rowcount
        referenzen = conn.execute(
            "DELETE FROM bmw_trips WHERE user_id = ? AND vehicle_id = ?",
            (user["id"], vehicle_id)).rowcount
        conn.commit()
    finally:
        conn.close()

    event_log_service.log_event("bmw", "info",
        f"BMW-Import zurückgesetzt: {sessions} Ladevorgänge, "
        f"{referenzen} Fahrt-Referenzen entfernt.")
    return jsonify({"ok": True, "sessions": sessions, "referenzen": referenzen})




@app.route("/api/trips/sammel-fahrtart", methods=["POST"])
def api_trips_sammel_fahrtart():
    """Mehrere Fahrten auf einmal als dienstlich oder privat einstufen."""
    user = _current_user()
    if user is None:
        return jsonify({"error": "no_user"}), 400
    data = request.get_json(force=True) or {}
    ids = [int(i) for i in (data.get("trip_ids") or [])]
    art = (data.get("fahrtart") or "").lower()
    if art not in ("dienstlich", "privat", "arbeitsweg"):
        return jsonify({"error": "ungueltige_fahrtart"}), 400
    standard = float(settings_repository.get_setting("default_km_rate") or 0.15)
    anzahl = trip_repository.set_fahrtart(
        ids, art, standard_satz=standard if art == "dienstlich" else None)
    if anzahl:
        event_log_service.log_event("system", "info",
            f"{anzahl} Fahrten als '{art}' eingestuft.")
    return jsonify({"ok": True, "anzahl": anzahl, "fahrtart": art})


@app.route("/api/trips/sammel-satz", methods=["POST"])
def api_trips_sammel_satz():
    """Erstattungssatz mehrerer Fahrten setzen (0,00 / 0,15 / 0,30 €/km).

    Wirkt ausschliesslich auf Dienstfahrten — bei privaten waere ein
    Erstattungssatz steuerlich unzulaessig."""
    user = _current_user()
    if user is None:
        return jsonify({"error": "no_user"}), 400
    data = request.get_json(force=True) or {}
    ids = [int(i) for i in (data.get("trip_ids") or [])]
    try:
        satz = float(data.get("rate", 0))
    except (TypeError, ValueError):
        return jsonify({"error": "ungueltiger_satz"}), 400
    if satz < 0 or satz > 1:
        return jsonify({"error": "satz_ausserhalb_bereich"}), 400
    geaendert = trip_repository.set_rate(ids, satz)
    uebersprungen = len(ids) - geaendert
    return jsonify({"ok": True, "anzahl": geaendert,
                    "uebersprungen": uebersprungen, "rate": satz})


@app.route("/api/trips/km-bereich", methods=["GET"])
def api_trips_km_bereich():
    """Kilometerstaende aus den importierten BMW-Fahrten eines Zeitraums.

    Dient als Vorschlag fuer das Fahrtenbuch: Statt die Staende von Hand zu
    suchen, kommen sie direkt aus den Fahrzeugdaten."""
    user = _current_user()
    if user is None:
        return jsonify({"error": "no_user"}), 400
    return jsonify(bmw_trip_repository.km_bereich(
        user["id"], request.args.get("von"), request.args.get("bis")))


@app.route("/api/settings/heimadresse", methods=["GET", "POST"])
def api_settings_heimadresse():
    """Wohnadresse fuer die Zuordnung 'zuhause' oder 'unterwegs'.

    Ist sie nicht gesetzt, greift die Wohnanschrift der Person; hilfsweise
    wird der haeufigste Ladeort verwendet."""
    if request.method == "POST":
        data = request.get_json(force=True) or {}
        settings_repository.set_setting("heim_adresse",
                                        str(data.get("adresse") or "").strip())
        return jsonify({"ok": True})
    return jsonify({
        "adresse": settings_repository.get_setting("heim_adresse") or "",
        "erkannt": cardata_service._heimadresse(),
    })



@app.route("/api/settings/bmw-heimladungen", methods=["GET", "POST"])
def api_bmw_heimladungen():
    """Ob Heimladungen aus der BMW-App uebernommen werden.

    Standard ist AUS: Der Strom aus der eigenen Wallbox wird von deren
    Zaehler gemessen — bei MID-Geraeten eichrechtlich belastbar. Die
    BMW-Angabe ist eine Fahrzeugschaetzung und taugt nicht als zweiter
    Nachweis fuer denselben Vorgang. Externe Ladungen kommen unabhaengig
    davon immer herein.
    """
    if request.method == "GET":
        return jsonify({"uebernehmen":
                        settings_repository.get_setting("bmw_heimladungen") == "1"})

    daten = request.get_json(force=True, silent=True) or {}
    an = bool(daten.get("uebernehmen"))
    settings_repository.set_setting("bmw_heimladungen", "1" if an else "0")
    event_log_service.log_event("system", "info",
        f"BMW-Heimladungen: {'werden übernommen' if an else 'werden übersprungen'}")
    return jsonify({"ok": True, "uebernehmen": an})


@app.route("/api/settings/bmw-duplikate", methods=["GET", "POST"])
def api_settings_bmw_duplikate():
    """Steuert, ob der BMW-Import Heimladungen ueberspringt, die die eigene
    Wallbox bereits erfasst hat."""
    if request.method == "POST":
        data = request.get_json(force=True) or {}
        settings_repository.set_setting("bmw_duplikate_pruefen",
                                        "1" if data.get("pruefen") else "0")
        return jsonify({"ok": True})
    return jsonify({"pruefen":
        (settings_repository.get_setting("bmw_duplikate_pruefen") or "1") == "1"})


@app.route("/api/settings/ladepreise", methods=["GET", "POST"])
def api_settings_ladepreise():
    """Preise je Ladeart. Extern liegt deutlich ueber dem Heimtarif, am
    Schnelllader nochmals hoeher — die Unterscheidung erfolgt automatisch
    anhand der gemeldeten Ladeleistung."""
    if request.method == "POST":
        data = request.get_json(force=True) or {}
        for key, feld in (("preis_dc_kwh", "dc"), ("preis_ac_extern_kwh", "ac_extern")):
            if data.get(feld) is not None:
                settings_repository.set_setting(key, str(float(data[feld])))
        return jsonify({"ok": True})
    return jsonify({
        "dc": float(settings_repository.get_setting("preis_dc_kwh") or 0.79),
        "ac_extern": float(settings_repository.get_setting("preis_ac_extern_kwh") or 0.59),
        "heim": float(settings_repository.get_setting("contract_kwh_price")
                      or settings_repository.get_setting("default_kwh_price") or 0.34),
        "dc_schwelle_kw": cardata_service.DC_SCHWELLE_KW,
    })


# ═══════════════════════════════════════════════════════════════════════════
# DATENSICHERUNG, EXPORT UND ZURUECKSETZEN (FA-COMP-04, FA-COMP-05)
# ═══════════════════════════════════════════════════════════════════════════

@app.route("/api/backup/status", methods=["GET"])
def api_backup_status():
    """Anzahl der Datensaetze — zeigt vor jedem Eingriff, worum es geht."""
    return jsonify({"datensaetze": backup_service.zaehle_datensaetze()})


@app.route("/api/backup/erstellen", methods=["GET"])
def api_backup_erstellen():
    """Vollstaendige Sicherung als ZIP."""
    daten, name = backup_service.erstelle_sicherung()
    return send_file(io.BytesIO(daten), mimetype="application/zip",
                     as_attachment=True, download_name=name)


@app.route("/api/backup/export-csv", methods=["GET"])
def api_backup_export_csv():
    """Nutzdaten als CSV — lesbar ohne diese Software."""
    daten, name = backup_service.exportiere_csv()
    return send_file(io.BytesIO(daten), mimetype="application/zip",
                     as_attachment=True, download_name=name)


@app.route("/api/backup/einspielen", methods=["POST"])
def api_backup_einspielen():
    """Sicherung wiederherstellen. Der bestehende Stand wird vorher gesichert."""
    if "file" not in request.files:
        return jsonify({"ok": False, "meldung": "Keine Datei übermittelt."}), 400
    datei = request.files["file"]
    if not datei.filename.lower().endswith(".zip"):
        return jsonify({"ok": False, "meldung": "Bitte eine ZIP-Sicherung hochladen."}), 400
    return jsonify(backup_service.spiele_sicherung_ein(datei.read()))


@app.route("/api/backup/zuruecksetzen", methods=["POST"])
def api_backup_zuruecksetzen():
    """Auslieferungszustand herstellen. Sichert vorher automatisch."""
    data = request.get_json(force=True) or {}
    # Ausdrueckliche Bestaetigung verlangen: Ein versehentlicher Aufruf soll
    # nicht den gesamten Bestand kosten.
    if data.get("bestaetigung") != "ZURUECKSETZEN":
        return jsonify({"ok": False, "meldung": "Bestätigung fehlt."}), 400
    try:
        return jsonify(backup_service.setze_zurueck(
            behalte_stammdaten=bool(data.get("behalte_stammdaten", True)),
            bereiche=data.get("bereiche")))
    except Exception as e:
        # Den Grund nennen statt nur zu scheitern — sonst steht der Anwender
        # vor "fehlgeschlagen" und weiß nicht, was zu tun ist.
        event_log_service.log_event("system", "error",
            f"Zurücksetzen fehlgeschlagen: {type(e).__name__}: {e}")
        return jsonify({"ok": False,
                        "meldung": f"{type(e).__name__}: {e}",
                        "hinweis": ("Läuft gerade ein Ladevorgang oder ist eine "
                                    "Beleg-Datei geöffnet? Dann bitte kurz warten "
                                    "und erneut versuchen.")}), 500


@app.route("/api/demodaten/status", methods=["GET"])
def api_demodaten_status():
    return jsonify(demodaten_service.bestand())


@app.route("/api/demodaten/erzeugen", methods=["POST"])
def api_demodaten_erzeugen():
    """Fuellt die Anwendung mit einem vollstaendigen Jahr Beispieldaten."""
    user = _current_user()
    if user is None:
        return jsonify({"ok": False, "meldung": "Bitte zuerst die Einrichtung abschließen."}), 400
    # silent=True: Der Aufruf kommt teils ohne Rumpf (einfacher Knopfdruck),
    # teils mit Monatszahl. Ohne diese Nachsicht scheiterte er mit HTTP 400.
    data = request.get_json(force=True, silent=True) or {}
    monate = max(1, min(24, int(data.get("monate", 12))))
    return jsonify(demodaten_service.erzeuge(user["id"], monate))


@app.route("/api/demodaten/entfernen", methods=["POST"])
def api_demodaten_entfernen():
    """Entfernt ausschliesslich die Beispieldaten."""
    return jsonify(demodaten_service.entferne())


@app.route("/api/hilfe/kapitel", methods=["GET"])
def api_hilfe_kapitel():
    """Liefert die Hilfe zerlegt in Kapitel.

    Statt help.html in einem neuen Browser-Tab zu oeffnen, wird sie in den
    Einstellungen eingebettet: Wer eine Frage hat, verliert sonst den Kontext
    und muss anschliessend zurueckfinden. Die Datei bleibt die einzige Quelle
    — sie wird hier nur zerlegt, nicht kopiert."""
    import re as _re
    pfad = os.path.join(app.static_folder, "help.html")
    try:
        with open(pfad, encoding="utf-8") as f:
            roh = f.read()
    except OSError:
        return jsonify({"kapitel": [], "anzahl": 0,
                        "fehler": "Hilfedatei nicht gefunden."})

    # Nur den Inhaltsbereich verwenden — Kopf, Navigation und Stil der
    # eigenstaendigen Seite wuerden das Layout der Anwendung stoeren.
    haupt = _re.search(r"<main[^>]*>(.*?)</main>", roh, _re.S)
    inhalt = haupt.group(1) if haupt else roh

    # Querverweise auf andere Kapitel in die eingebettete Ansicht umlenken,
    # damit sie nicht ins Leere zeigen.
    ziel = 'href="#" onclick="hilfeKapitel(\'\\1\'); return false;"'

    kapitel = []
    teile = _re.split(r'<h2 id="([^"]+)">([^<]+)</h2>', inhalt)
    for i in range(1, len(teile) - 2, 3):
        text = _re.sub(r'href="#([a-z0-9-]+)"', ziel, teile[i + 2])
        kapitel.append({"id": teile[i],
                        "titel": teile[i + 1].strip(),
                        "html": text.strip()})
    return jsonify({"kapitel": kapitel, "anzahl": len(kapitel)})


@app.route("/api/extern-ocpp/konfig", methods=["GET", "POST"])
def api_extern_ocpp_konfig():
    """Verbindungsdaten zu einem externen OCPP-Dienst."""
    if request.method == "POST":
        data = request.get_json(force=True) or {}
        return jsonify(extern_ocpp_service.speichere_konfiguration(
            data.get("adresse", ""), data.get("pfad", ""),
            data.get("wallbox_name", ""), bool(data.get("aktiv", True))))
    return jsonify(extern_ocpp_service.konfiguration())


@app.route("/api/extern-ocpp/test", methods=["POST"])
def api_extern_ocpp_test():
    """Prueft, ob die Gegenstelle antwortet und verwertbare Daten liefert."""
    return jsonify(extern_ocpp_service.teste_verbindung())


@app.route("/api/extern-ocpp/import", methods=["POST"])
def api_extern_ocpp_import():
    """Holt neue Ladevorgaenge vom externen Dienst ab."""
    user = _current_user()
    if user is None:
        return jsonify({"ok": False, "meldung": "Bitte zuerst die Einrichtung abschließen."}), 400
    return jsonify(extern_ocpp_service.importiere(user["id"]))


if __name__ == "__main__":
    # MQTT-Streams aller Fahrzeuge wieder aufnehmen, deren Stream
    # eingeschaltet ist — je Fahrzeug unabhaengig, seit dem Umbau auf
    # mehrere gleichzeitige BMW-Verbindungen (28.08.).
    #
    # BUG BEHOBEN: Die Einstellung 'stream_aktiv' wird dauerhaft je
    # Fahrzeug gespeichert, der eigentliche Hintergrund-Thread aber nur
    # beim manuellen Umschalten in den Einstellungen gestartet (der
    # Zustand lebt nur im Prozessspeicher). Nach jedem Container-Neustart —
    # Update, Docker-Neustart, Absturz — war die Einstellung weiterhin
    # "an", der Stream lief aber nicht mehr, ohne dass das irgendwo
    # auffiel. Ohne erneutes Anklicken der Checkbox kamen danach nie
    # wieder Meldungen an.
    #
    # Der fruehere periodische Abruf ("Automatischer Abruf") ist entfernt —
    # der Stream deckt die Fahrterkennung vollstaendig ab, ohne
    # Tageskontingent. Die Ausschliesslichkeit beider Mechanismen (frueher
    # hier noetig, weil ein gleichzeitiger Tokenabruf die Stream-Sitzung
    # kappen konnte) ist damit gegenstandslos geworden.
    try:
        from services import cardata_stream_service
        from repositories import vehicle_bmw_repository as bmw_repo
        for vid in bmw_repo.liste_mit_aktivem_stream():
            try:
                cardata_stream_service.starte(vid)
            except Exception:
                pass
    except Exception:
        pass

    app.run(host="0.0.0.0", port=8501, debug=False)
