# Charge@Home Billing Engine
## Pflichtenheft (Technische Spezifikation)
**Version 10.24 · August 2026 · Ersetzt Version 1.0 vollständig · Änderungen ggü. v10.10: NEU FA-LS-BMF-01 — optionale BMF-Schreiben-Referenz auf dem Ladestrom-Beleg. Hintergrund: BMF-Schreiben vom 11.11.2025 (GZ IV C 5 – S 2334/19/10007 :005, gültig 01.01.2026–31.12.2030) ersetzt die bisherigen Pauschalen (15–70 €/Monat) durch verpflichtenden kWh-genauen Nachweis, mit Wahlrecht zwischen individuellem Strompreis oder bundesweiter Pauschale (0,34 €/kWh für 2026, einheitlich fürs Kalenderjahr). Bemerkenswert: mehrere bereits unabhängig davon gebaute Funktionen entsprechen bereits den neuen Anforderungen — kWh-genauer Log-Import, BMW-Fahrzeugdaten als Nachweis (vom BMF jetzt explizit als gültige Quelle anerkannt), Zuhause/Extern-Trennung (entspricht der Regel, dass extern geladener Strom zusätzlich und getrennt erstattet werden darf). WICHTIG — Konflikt mit FA-LS-07 bewusst aufgelöst, nicht überschrieben: FA-LS-07 (Change Request des Auftraggebers, siehe dort) verlangt einen neutralen, fallunabhängigen Beleg ohne Rechtstext. Die neue Referenz ist deshalb standardmäßig DEAKTIVIERT (neue Einstellung, Tabelle `app_settings`, Schlüssel `show_bmf_reference`) und muss explizit in den Einstellungen zugeschaltet werden — FA-LS-07 bleibt im Standardfall unangetastet. Bei Aktivierung erscheint eine kleine, dezente Fußzeile mit dem Aktenzeichen. Getestet: PDF unterscheidet sich nachweislich je nach Schalterstellung, Route liest/schreibt korrekt.** WALLBOX-ÜBERSICHT VOLLSTÄNDIG NEU GESTALTET — berechtigte Kritik des Auftraggebers, dass die bisherige Tabellen-Darstellung "überladen" und "man erkennt nichts" war. Ersetzt durch klar getrennte, einzeln auf-/zuklappbare Gruppen-Knoten (Tree-View-Stil) — eine Wallbox = ein eigener, umrandeter Block mit Name/Typ/Status im Kopf und Details/Live-Daten/Aktionen im aufklappbaren Körper, statt aller Information in einer gemeinsamen Tabellenzeile. Neues CSS (`.wallbox-node` etc.), `loadWallboxesTable()` komplett neu geschrieben. Getestet: Rendering mit mehreren Wallboxen unterschiedlichen Typs, Route liefert weiterhin korrekt alle Felder inkl. `polling_paused`.** TECHNISCHER JARGON AUS NUTZER-SICHTBAREN MELDUNGEN ENTFERNT — Rückmeldung, dass Meldungen wie "[Einfacher Weg] Verbindung erfolgreich" interne Implementierungsdetails (Basic-Auth vs. Token-Handshake) unnötig nach außen tragen. Erfolgsmeldungen jetzt einheitlich "Loxone Miniserver erfolgreich verbunden!" — Fehlermeldungen behalten bewusst technische Details (Ausfallsicherheits-Diagnose braucht das, siehe Backoff-Historie).** AUSFALLSICHERHEIT GEGEN MINISERVER-SPERREN (kritisch) — der Poller versuchte bisher bedingungslos bei jedem Zyklus erneut zu authentifizieren; bei falschem Passwort und kurzem Poll-Intervall führte das nachweislich zu einer IP-Sperre durch den Miniserver selbst ("too many failed login attempts"). Neue Tabelle `loxone_auth_backoff`: exponentielles Backoff nach Fehlversuchen (60s→120s→300s→600s→1800s→max. 3600s) PLUS manueller Not-Aus-Schalter ("Polling pausieren"), der unabhängig vom Backoff sofort jeden weiteren Verbindungsversuch stoppt. Verifiziert: 30 simulierte Zyklen mit durchgehend falschem Passwort erzeugen nur 3 tatsächliche Anfragen statt 90; manueller Pausier-Schalter verhindert nachweislich 100% der Anfragen, auch bei gültigem Passwort.** OCPP-DIAGNOSE ergänzt (Rückmeldung: "wir brauchen eine Logdatei, die zeigt welche Daten ankommen") — neue dauerhafte Logdatei (`data/ocpp_raw.log`, unabhängig von der 500-Eintrag-Rotation des allgemeinen Protokolls) plus persistente Nachrichtentyp-Zählung (`ocpp_message_counts`), macht auf einen Blick nachvollziehbar, welche OCPP-Nachrichtentypen jemals eingegangen sind und bestätigt konkret (nicht nur vermutet), dass StartTransaction/MeterValues/StopTransaction nie ankommen.** MENÜSTRUKTUR NEU GEORDNET (FA-UX-01/02/03) — "Setup" ist kein Dauermenüpunkt mehr, sondern läuft nur beim allerersten Start als fokussierter Assistent; "Einstellungen" in "Wallbox" (Verbindung/Bausteine) und "Einstellungen" (Preis/Adresse/Personen/Lizenz) aufgeteilt; UUID-Auswahl validiert jetzt sofort im Formular, ob die erwarteten Wallbox2-Felder vorhanden sind, statt den Fehler erst später im Protokoll sichtbar zu machen.** BMW-LADEHISTORIE-IMPORT (FA-LS-BMW-01/02) — eigenständiger Import auf der Ladesessions-Seite für den XLSX-Export der Hersteller-App, füllt Lücken aus Zeiträumen ohne eigene zuverlässige Datenquelle. Automatische Zuhause/Extern-Erkennung über Straßennamen-Abgleich (nicht volle Adresse — BMW meldet gelegentlich eine abweichende Hausnummer), externe Ladungen fließen NICHT in den Eigenstrom-Beleg ein, bleiben aber sichtbar und manuell korrigierbar. Getestet gegen echte Datei (11 Sessions) und synthetische Grenzfälle.**

**Version 10.10 · August 2026 · Ersetzt Version 1.0 vollständig · Änderungen ggü. v10.9: OCPP-DATENFRAGE ENDGÜLTIG GEKLÄRT (mit Beweis, nicht mehr Vermutung). Die vollständige Rohdaten-Protokollierung (v10.8) zeigt über mehrere echte, vollständige Ladezyklen hinweg (Statuswechsel zu "Charging" zeitlich deckungsgleich mit den über die direkte API erfassten echten Sessions #S-2/#S-3) ausschließlich `BootNotification`, `Heartbeat` und `StatusNotification` — an KEINER Stelle `StartTransaction`, `MeterValues` oder `StopTransaction`, obwohl Ec dauerhaft aktiv ist und die Ladungen real stattfanden. Da die Rohdaten-Protokollierung nachweislich 100% des Datenverkehrs erfasst, ist damit belegt: Loxones OCPP-Integration überträgt in dieser Konfiguration keine Transaktions-/Zählerdaten, nur den Verbindungsstatus. FESTGELEGTE KONSEQUENZ: Die direkte Loxone-API bleibt die einzige Datenquelle für die Abrechnung. OCPP bleibt ausschließlich für die reine Live-Status-Anzeige relevant. Weitere Fehlersuche nach einem OCPP-Transaktions-Bug auf unserer Seite ist damit abgeschlossen — bestätigte Eigenschaft der Loxone-seitigen OCPP-Implementierung, kein Code-Fehler.** FTP-BROWSER WIEDER EINGEBAUT — in v10.0 im Rahmen der "zu viele Datenwege"-Vereinfachung entfernt, jetzt auf ausdrücklichen Wunsch des Auftraggebers zurückgeholt: Recherche in drei realen GitHub-Projekten bestätigte, dass die Loxone-App-Historie über einen am Lcl-Ausgang angeschlossenen Logger entsteht, dessen Log-Datei per FTP abrufbar ist — der Auftraggeber wollte zu Recht nicht auf einen neuen Logger warten, sondern das TATSÄCHLICH VORHANDENE Dateisystem direkt durchsuchen. Neuer `loxone_ftp_service.py` (MLSD mit Fallback auf NLST bei jedem Fehler), neue Routen `/api/wallboxes/ftp-browse` und `/api/wallboxes/ftp-download`, neue Oberfläche mit Ordner-Navigation und Datei-Download. Isoliert mit simulierter Verzeichnisstruktur getestet. Zusätzlich: doppelten "Alle löschen"-Button bereinigt, letztes verbliebenes Emoji im Template ersetzt.** VOLLSTÄNDIGE OCPP-ROHDATEN-PROTOKOLLIERUNG ergänzt — Auftraggeber widerlegte die vorherige Ec-Hypothese zu Recht: Da Ec dauerhaft aktiv ist und Log-Dateien sowie Loxone-App zuverlässig Sessions zeigen, kann fehlende Autorisierung nicht die Ursache sein. Konkrete Forderung: "eine Option, die den kompletten String der eingehenden OCPP-Verbindung zeigt". Neuer Websocket-Wrapper `_RawLoggingWebSocket` protokolliert jetzt JEDE rohe Nachricht (empfangen UND gesendet) im Protokoll, BEVOR die `ocpp`-Bibliothek sie interpretiert. Deckt insbesondere Nachrichtentypen ab, für die wir keinen Handler geschrieben haben — diese wurden bisher stillschweigend verworfen. Isoliert getestet: Wrapper protokolliert nachweislich auch einen simulierten, unbekannten Aktionstyp vollständig.** VERSCHLÜSSELUNG VOLLSTÄNDIG ENTFERNT (NFA-11 geändert) — ausdrücklicher, klar begründeter Wunsch des Auftraggebers, nachdem die Schlüssel-Verwaltung bereits drei separate Bug-Wellen verursacht hatte. Begründung: Loxone-Zugangsdaten für das eigene Heimnetz sind keine Bankdaten, Zuverlässigkeit wiegt schwerer. `crypto_service.py` auf reine Durchreich-Funktionen reduziert (Funktionsnamen unverändert). Getestet: Passwort wird exakt wie eingegeben gespeichert, "Struktur laden" funktioniert ohne jede Möglichkeit eines Entschlüsselungsfehlers. WICHTIG: Bereits verschlüsselt gespeicherte Passwörter sind einmalig ungültig — betroffene Wallboxen benötigen einmaliges erneutes Eingeben. ZUSÄTZLICHER FUND: OCPP-Verbindungspfad zeigte "/ocpp/WB1/WB1" (ID verdoppelt) — unsere Pfad-Auswertung bleibt davon unberührt, deutet aber auf eine mögliche Doppel-Konfiguration in Loxone Config hin (volle URL UND separate Charge-Point-ID) — als Prüfhinweis mitgegeben, keine Code-Änderung nötig.** MEILENSTEIN BESTÄTIGT — Auftraggeber bestätigt: StatusNotification-Nachrichten kommen tatsächlich am OCPP-Server an. Dashboard erweitert: Live-Session-Karte zeigt jetzt auch reine Verbindungsstatus-Änderungen ohne volle Ladesession — z. B. "Preparing" (Fahrzeug angesteckt, noch nicht gestartet), "SuspendedEV"/"SuspendedEVSE", "Finishing". Deutsche Labels ergänzt. Detailansicht zeigt bei reinem Status transparent "noch keine Messwerte (nur Status: ...)" statt irreführender 0,00-kWh-Werte. Bei "Available" (Kabel abgezogen) verschwindet die Karte korrekt. End-to-End mit dem Szenario des Auftraggebers getestet.** DREI WEITERE, ECHTE BUGS behoben. (1) STRUKTURELLE LÖSUNG für "fehler: entschlüsselung": Der Schlüssel lag bisher unter `data/.encryption_key` — innerhalb des Projektordners, der bei jedem Update komplett gelöscht wird. Das machte die Persistenz abhängig davon, dass ein externes Skript IMMER korrekt verwendet wird. Schlüssel liegt jetzt AUSSERHALB des Projektordners in einem stabilen Nutzerverzeichnis (%APPDATA%\\.charge-at-home unter Windows), das kein ZIP-Update mehr berührt. Automatische Migration eines eventuell vorhandenen alten Schlüssels. Isoliert getestet. (2) "Struktur laden" verlangte trotz Passwort-Eingabe weiterhin "IP-Adresse fehlt" — beim letzten Zugangsdaten-Fix (v9.8) übersehen. Korrigiert und getestet. (3) ECHTER CSS-BUG: Die Eingabefeld-Stilregel listete `input[type=password]` gar nicht auf — Passwort-Felder bekamen nie die dunkle Theme-Farbe ("Feld bleibt weiß"). Behoben, inklusive Browser-Autofill-Überschreibung.** Datenschutz-Bug bei Platzhaltertexten behoben — mehrere Formularfelder nutzten unbeabsichtigt personenbezogene Beispieldaten des Auftraggebers als Platzhalter: "Anna Löwemann"/"anna@beispiel.de", "AW-SL 2026E" (echtes Ahrweiler-Kennzeichenkürzel), "Lange Fuhr 7, 53424 Remagen" (tatsächliche eigene Adresse), "Bad Neuenahr"/"Euskirchen" (reale Umgebung). Alle durch generische Beispiele ersetzt ("Max Mustermann", "Musterstraße 1, 12345 Musterstadt" etc.). Das gesetzlich vorgeschriebene Impressum bleibt bewusst unverändert — das ist absichtlich korrekte Pflichtangabe, keine leakende Platzhalter-Information. ZUSÄTZLICH ECHTER CSS-BUG BEHOBEN: `.icon-btn` setzte nirgends eine explizite Textfarbe; da alle SVG-Icons `stroke="currentColor"` nutzen, übernahmen sie eine zufällig geerbte, im Dunkelmodus teils unleserliche Farbe — genau die gemeldete schlechte Erkennbarkeit der Bearbeiten-/Löschen-Icons. Jetzt explizit gesetzt. Neue `.icon-btn-danger`-Klasse für Löschen-Buttons (rote Hervorhebung, klare Unterscheidung von Bearbeiten). Lichtmodus-Farbschema von Amber/Braun auf Blau/Grün umgestellt (Loxone-/Elektro-Ästhetik) — nur Farbwerte geändert, Variablennamen unverändert.** UX-Bug in der Fahrten-Erfassung behoben — das Distanz-Feld nutzte "42,3" als statischen Platzhaltertext, der zufällig wie ein echt berechneter Wert aussah. In Kombination mit fehlgeschlagener Adress-Geokodierung entstand der Eindruck, ein Wert sei bereits eingetragen, obwohl das Feld leer war — "Arbeitgeber-Erstattung" zeigte korrekt 0,00 €, wirkte aber wie ein Rechenfehler. Platzhaltertext auf "km manuell eingeben" geändert. Zur Geokodierungs-Fehlermeldung: Zieladresse enthielt Tippfehler ("Düsseldof Hansaalle" statt "Düsseldorf Hansaallee") — wahrscheinliche Ursache, keine Codeänderung nötig. NEU: Stammadresse-Einstellung (Tabelle `app_settings`, Schlüssel `home_address`) — wird automatisch als Start-Adresse bei "Neue Fahrt" vorbelegt. Vollständig getestet. Zum gemeldeten Problem "OCPP verbunden, aber keine Werte": Auftraggeber wird gebeten, das Protokoll im fraglichen Zeitraum zu prüfen, um zu unterscheiden, ob StatusNotification-Nachrichten ankommen oder die Verbindung trotz Anzeige in Loxone keine Nachrichten sendet.** STRATEGISCHE NEUGEWICHTUNG (§ 1.5 überarbeitet) — nach praktischer Erfahrung dieses Sprints (mehrfache Versionsinkompatibilitäten bei der direkten API, Verschlüsselungs-Schlüssel-Probleme, notwendiger Zusatz-Logger für Historie) empfiehlt der Auftraggeber OCPP als primären, herstellerunabhängigen Weg, die direkte Loxone-API nur noch als optionale "Erweiterte Live-Ansicht". Diese Einschätzung wird geteilt und ersetzt die frühere Gewichtung (v8.5). BUGFIX: Die "Aktuelle Ladesession"-Karte im Dashboard (v10.1) deckte bisher nur den direkten-API-Weg ab, da beim OCPP-Weg keine Abfrage laufender, offener Sessions erfolgte. Neue Funktion `get_any_open_session()`, Dashboard-Endpunkt prüft jetzt zusätzlich auf offene OCPP-Sessions. Mit dem vom Auftraggeber gezeigten Referenzbild der nativen Loxone-App (OCPP-verwaltete Wallbox) nachgestellt und verifiziert: 2 kWh, 0,68 €, Standort und Verbindungszeit korrekt. Zusätzlicher Bugfix: laufende Sessions erschienen fälschlich als "beendet" in der Aktivitätsliste. Mit den echten Log-Daten des Auftraggebers verifiziert, dass unser Parser exakt die 2 echten Sessions (0,4 kWh/0,15€ und 4,4 kWh/1,51€) korrekt extrahiert.** Anklickbare Live-Session-Detailansicht im Dashboard ergänzt, angelehnt an einen vom Auftraggeber gezeigten Screenshot der nativen Wallbox-App ("Fahrzeug verbunden", Aktuelle/Soll-Leistung, Verbunden seit, Dauer, Geladene Energie, Gesamte Ladekosten). Die bestehende Gauge-Karte im Dashboard ist jetzt anklickbar und blendet ein Detail-Panel mit denselben Kennzahlen ein, abgeleitet aus den Live-Metriken der direkten Loxone-API. Mit dynamisch aus dem Referenz-Screenshot abgeleiteten Werten getestet (2 kWh, 0,69 €, ~12 Minuten) — stimmt exakt überein. Bestätigung aus dem Screenshot: "verwaltet durch OCPP Server Integration" am unteren Rand — unabhängiger Beleg, dass OCPP bei diesem Miniserver inzwischen aktiv die Wallbox-Steuerung übernimmt.**

**Version 10.0 · August 2026 · Ersetzt Version 1.0 vollständig · Änderungen ggü. v9.9: ARCHITEKTUR-KURSKORREKTUR (§ 1.5 neu gefasst, ersetzt v8.5) — nach praktischer Erfahrung dieses Sprints wird OCPP jetzt als primärer, empfohlener Weg festgelegt, die direkte Loxone-API als optionale "Erweiterte Live-Ansicht". FTP-Browser und HTTP-Log-Datei-Import vollständig entfernt (Routen, Service `loxone_ftp_service.py`, JS-Funktionen, tote Service-Funktionen, FTP-Karte im Template) — beide erforderten einen zusätzlichen Logger-Baustein bei redundantem Nutzen. Getestet: alte Routen liefern korrekt 404. Neue Beispiel-CSV-Datei mit Download-Link, gegen den echten Import-Service getestet (3/3 Zeilen importiert). Fehlermeldung bei Entschlüsselungsfehlern verbessert — `InvalidToken` liefert bewusst keinen Text, jetzt Klartext-Erklärung mit Lösungshinweis. "Car Allowance" durch "Privates Fahrzeug" ersetzt.** Bezeichnung "Car Allowance" für Abrechnungsfall C auf Wunsch des Auftraggebers durch "Privates Fahrzeug" ersetzt (Dashboard, Setup-Auswahlkarte, i18n/de.json) — geprüft, dass keine Stelle im Template den englischen Begriff mehr rendert. Zusätzlich: Der HTTP-Log-Pfad (`/dev/fsget/log/{Dateiname}.log`) wird jetzt direkt im "Log-Datei (HTTP)"-Dialog als vollständige URL angezeigt, damit selbst im Browser nachgesehen werden kann, ob dort eine Datei existiert, bevor ein Import versucht wird.** Grundlegende UX-Korrektur bei Loxone-Verbindungstests — Rückmeldung: "die Verbindung steht bereits" und trotzdem verlangte "Verbindung testen"/"Wert jetzt lesen" bei einer gespeicherten Wallbox erneut Zugangsdaten. Neue Hilfsfunktion `_resolve_loxone_credentials()` ergänzt fehlende Zugangsdaten bei einer existierenden Wallbox automatisch serverseitig — inklusive Entschlüsselung des gespeicherten Passworts, ohne dass es je erneut ins Frontend übertragen wird (NFA-11 bleibt gewahrt). Betrifft `/api/wallboxes/test-connection`, `/api/wallboxes/read-value` und `/api/wallboxes/<id>/check-wallbox2-log`. Frontend übermittelt jetzt `editingWallboxId`, sodass diese Aktionen bei einer gespeicherten Wallbox ohne erneute Passworteingabe funktionieren. End-to-End getestet. ZUSÄTZLICH: Neue, live aktualisierte Anzeige der exakten OCPP-WebSocket-Verbindungs-URL im Wallbox-Formular (mit Kopieren-Button) — Rückmeldung, dass unklar war, welche URL in Loxone Config einzutragen ist.** Verbleibende Emoji-Icons vollständig durch SVG ersetzt — die vorherige UI-Überarbeitung (v9.6) hatte nur die Live-Ansicht behandelt, die Action-Icons in der Wallbox-Tabelle, im Belegverlauf und FTP-Browser blieben Emojis. Jetzt vollständig auf Inline-SVG umgestellt, mit einer technisch bedingten Ausnahme: `<option>`-Elemente können kein SVG enthalten, dort reiner Textmarker "[Wallbox]" statt Emoji. Zusätzlich: klickbarer Hinweis "Passwort neu eingeben →" erscheint automatisch bei "fehler: entschlüsselung", öffnet direkt das Bearbeiten-Formular. Klarstellung: eine OCPP-Wallbox kann "Charging"-Status über die legitime `StatusNotification`-Nachricht zeigen (unabhängig von Start/StopTransaction) — ein während der python-ocpp-Namenskollision (v9.5) gesetzter Status ist aber vermutlich veralteter Teststand, kein bestätigter Live-Durchlauf, und sollte über das Protokoll verifiziert werden.** UI-ÜBERARBEITUNG der Live-Ansicht und Wallbox-Verwaltung. Die Live-Metriken erschienen bisher als dichter, schwer lesbarer Fließtext; Emoji-Icons (🚗⚡↻) wirkten unprofessionell. Neues strukturiertes Panel (`.live-panel`) mit klar getrennten Bereichen: Leistung/Sync oben, farblich hervorgehobener Fahrzeugstatus in der Mitte, Kennzahlen-Grid unten. Emojis durch konsistente Inline-SVG-Icons ersetzt. ZUSÄTZLICH ECHTER BUG GEFUNDEN: Der "Bearbeite:"-Hinweis nutzte `var(--surface-2)` als Hintergrundfarbe — diese CSS-Variable existiert im gesamten Stylesheet nicht, wodurch der Hinweis ohne sichtbaren Hintergrund erschien und mit dem Seitenhintergrund verschmolz. Behoben (jetzt `--amber-soft` mit Rahmen), Formular-Labels und Eingabefelder generell vergrößert/kontrastreicher. Mit den vom Auftraggeber gelieferten echten Live-Werten (10,72 kW, Fahrzeug verbunden) getestet.** WEITERER MEILENSTEIN UND BUGFIX aus echtem Server-Log — die echte Loxone-Wallbox ("Wallbox 11kW Air", Loxone Electronics, Firmware 1.4.7) sendet tatsächlich BootNotification an unseren OCPP-Server, mit vollständigen Gerätedaten. Das bestätigt: die Namenskollisions- und websockets-Korrekturen (v8.9/v9.0) waren erfolgreich. Neuer Fehler: `AttributeError: module 'ocpp.v16.call_result' has no attribute 'BootNotificationPayload'`. Ursache: Die installierte `python-ocpp`-Version (>=2.0) benennt die Antwort-Klassen ohne "Payload"-Suffix. Behoben mit derselben Kompatibilitätsstrategie wie bei websockets: neue Hilfsfunktion `_cr()` ermittelt die richtige Klasse zur Laufzeit, für alle sechs OCPP-Nachrichtentypen. Isoliert mit beiden Namenskonventionen getestet. Dies ist bereits die dritte python-ocpp/websockets-Versionsinkompatibilität, die erst durch echte Praxistests ans Licht kam.** Loxone-Poll-Intervall einstellbar gemacht — Rückmeldung des Auftraggebers, dass eine Pause am Fahrzeug erst mit Verzögerung in der App ankam. Ursache: Die 15s-Auto-Aktualisierung der Oberfläche zeigt nur den letzten bekannten Wert, die eigentliche Abfrage lief weiterhin fest alle 60s. Neue Tabelle `app_settings`, neues Feld in den Einstellungen, Poller liest das Intervall jetzt bei jedem Zyklus aus der Datenbank statt aus einer festen Konstante. Mindestwert 10s. Vollständig getestet: Standardwert 60s, Änderung auf 15s persistiert und vom Poller korrekt übernommen, zu niedrige Werte abgelehnt. Zusätzlich klargestellt: OCPP sendet MeterValues laufend WÄHREND des Ladens (push-basiert), nicht erst am Ende — bei diesem Miniserver kommt aber vermutlich noch keine Nachricht an, da die Verbindung schon bei BootNotification abgelehnt wird, solange keine passende OCPP-Wallbox-ID hinterlegt ist. Fund: Lcl protokolliert auch Session-BEGINN ("Fahrzeug verbunden"), nicht nur -Ende — unser Parser erkennt aktuell nur das Ende-Format.** Automatische Wallbox-Erkennung in der Struktur-Auswahl — Rückmeldung des Auftraggebers, dass eine Liste mit hunderten unsortierten Bausteinen nicht praktikabel ist. `list_structure_controls()` erkennt jetzt automatisch Wallbox-Bausteine anhand Typ (enthält "Wallbox", z. B. "Wallbox2") oder Name, und filtert die Auswahlliste standardmäßig darauf. Neue Checkbox "alle Bausteine anzeigen" als Fallback. Mit einer simulierten Struktur (50 Lampen + 1 Wallbox2) über die echte Flask-Route getestet: Standardfall liefert korrekt nur den einen relevanten Eintrag statt aller 51.** FELDBEDEUTUNGEN OFFIZIELL BESTÄTIGT UND EIN ECHTER FEHLER KORRIGIERT — Auftraggeber lieferte den Link zur offiziellen Loxone-Dokumentation des Wallbox-Bausteins. Ergebnis: `Ca` und `Vc` waren in unserer Implementierung vertauscht. Korrekt laut Hersteller: `Ca` = "Charging allowed" (Laden ist erlaubt — eine Policy, KEIN Verbindungsstatus), `Vc` = "Vehicle connected" (Fahrzeug tatsächlich verbunden). Behoben in `loxone_wallbox2_service.py` und `poller.py`, mit Testfall verifiziert. Zusätzlich neue Felder in der Live-Ansicht nutzbar gemacht: `Cac` ("Charging active" — lädt gerade tatsächlich) und `Ccc` ("Consumption current charge" — laufender Verbrauch der AKTUELL AKTIVEN Session, live). Live-Ansicht zeigt jetzt: Fahrzeug verbunden/nicht, lädt aktiv/nicht, und bei laufender Session live deren bisherigen Verbrauch und Kosten. Erster Fall, in dem eine Feldbedeutung durch Herstellerdokumentation statt nur Beobachtung verifiziert wurde.** FA-LOG-01 (Protokoll-Ansicht) UMGESETZT — bisher nur als verpflichtender Punkt dokumentiert, jetzt gebaut. Neue Tabelle `event_log`, zentraler `event_log_service.py`, neue Seite "Protokoll" (Quelle-/Stufe-Filter, Auto-Refresh alle 15s). OCPP-Central-System, Loxone-Poller und Wallbox2-Log-Prüfung protokollieren jetzt jeden relevanten Schritt (BootNotification akzeptiert/abgelehnt inkl. Grund, StatusNotification, StartTransaction, MeterValues, StopTransaction, Loxone-Sync-Erfolg/Fehler). Direkt bestätigt: eine abgelehnte OCPP-BootNotification erscheint jetzt mit konkreter Erklärung statt stillem Nichts-Passiert. Zusätzlich die Live-Ansicht der Loxone-API-Wallboxen erweitert (Rückmeldung: "wir brauchen viel mehr Informationen"): zeigt jetzt zusätzlich Maximalleistung, Gesamtzähler, Verbrauch heute/Woche/Monat/Jahr, RFID-Tag. Wichtiger Hinweis zur wahrscheinlichsten Ursache für "OCPP läuft, aber keine Daten": Das Central-System akzeptiert nur Charge-Point-IDs, die auch als OCPP-Wallbox in den Einstellungen hinterlegt sind (NFA-10) — ohne exakt passenden Eintrag wird die Verbindung schon bei BootNotification abgelehnt. Jetzt über das Protokoll direkt überprüfbar statt vermutet.** MEILENSTEIN — OCPP-Server läuft erstmals nachweislich gegen echte Hardware. Nach Behebung der Namenskollision (v8.9) verband sich die Loxone-Wallbox tatsächlich zum Server; neuer Fehler aus echtem Log: `TypeError: on_connect() missing 1 required positional argument: 'path'`. Ursache: Die installierte `websockets`-Version (>=13, neue `websockets.asyncio.server`-API) ruft den Verbindungs-Handler nur noch mit einem Argument (der Connection) statt zwei (websocket, path) auf — der Pfad muss stattdessen aus `connection.request.path` gelesen werden. `on_connect()` jetzt kompatibel zu beiden API-Generationen, isoliert mit beiden Aufrufkonventionen getestet. Zusätzlich: `websockets`-Version wird beim Start protokolliert. Dies ist der erste dokumentierte Fall, in dem eine tatsächliche eingehende OCPP-Verbindung von echter Hardware am Central-System ankam.** ZWEI GRUNDLEGENDE BUGS aus echtem Server-Log des Auftraggebers behoben. (1) OCPP-Server startete nie wirklich — `ModuleNotFoundError: No module named 'ocpp.routing'`. Ursache: eigener Projektordner hieß `app/ocpp/`, identisch zum echten PyPI-Paket `ocpp`; da `server.py` das `app/`-Verzeichnis auf den Python-Suchpfad setzt, gewann der eigene, leere Ordner gegen die echte Bibliothek — bestand seit Sprint 3, nie zuvor erkannt. Ordner umbenannt zu `app/ocpp_server/`, alle Verweise in `entrypoint.sh`/`start.sh`/`start.bat` aktualisiert. (2) Wallbox-Löschung schlug weiterhin mit FOREIGN-KEY-Fehler fehl, sobald Live-Metriken-Daten vorlagen — `wallbox_live_metrics` fehlte in der Lösch-Bereinigung, derselbe wiederkehrende Fehlertyp wie zuvor. Grundlegend behoben: `delete_wallbox()` ermittelt jetzt automatisch ALLE Tabellen mit `wallbox_id`-Spalte über `sqlite_master`, statt eine von Hand gepflegte Liste zu nutzen. Zusätzliche Absicherung: verbleibende IntegrityError-Fälle geben jetzt eine verständliche Meldung statt eines nackten 500-Fehlers. Beide Fälle mit dem exakten Szenario aus dem Log nachgestellt und verifiziert behoben.** Mehrere konkrete Bugs aus Screenshots des Auftraggebers behoben: (1) Loxone-UUID wurde nie an die Oberfläche zurückgeliefert (fehlender JOIN in `list_wallboxes_with_status()`), dadurch beim Bearbeiten immer leer und fälschlich als "nicht gespeichert" wahrgenommen — behoben und getestet. (2) "fehler: entschlüsselung" zurückverfolgt auf eine echte Ursache: Das Entpack-Skript sicherte bisher nur die Datenbank, NICHT den Verschlüsselungs-Schlüssel (`data/.encryption_key`) — nach jedem Update wurde ein neuer Schlüssel erzeugt, wodurch alte verschlüsselte Loxone-Passwörter unlesbar wurden. Skript korrigiert, sichert jetzt beides. (3) Hartcodierte Sidebar-Badges "18"/"7" (weiterer Mockup-Rest) durch echte, live nachgeladene Zahlen ersetzt. (4) Impressum-Funktion war durch einen eigenen Fehler unbrauchbar geworden (Kommentar und Funktionsdefinition auf derselben Zeile verschmolzen) — behoben. (5) Klassifizierungs-Filter in der Auswertung wurde unabhängig vom Abrechnungsfall immer angezeigt, obwohl sich Dienst/Privat nur bei Fall A/B überhaupt setzen lässt — Filter blendet sich jetzt korrekt aus, mit Hinweistext. (6) Belegverlauf um Jahr-/Monats-Filter, tabellarische Darstellung und echte Löschfunktion erweitert. NEU als verpflichtender Programmpunkt aufgenommen (§ 3.7, FA-LOG-01): sichtbare Protokoll-/Log-Ansicht — noch nicht umgesetzt, aber jetzt formal festgehalten.** SYSTEMATISCHE VOLLAUDIT AUF VERBLIEBENE MOCKUP-RESTE — berechtigte, scharfe Kritik des Auftraggebers: einzelne Sprint-für-Sprint-Korrekturen reichen nicht, wenn an anderer Stelle unbemerkt weiterhin Fake-Daten als echt erscheinen. Vollständige Durchsuchung des Templates ergab drei weitere, bisher unentdeckte Fälle: (1) Die Seitenleiste zeigte auf JEDER Seite dauerhaft "OCPP-Server (Beispiel): Online" — fest einprogrammiert, unabhängig davon ob der Prozess überhaupt lief; jetzt echter Verbindungstest (`/api/ocpp/status`, TCP-Verbindungsversuch auf Port 9000), alle 20s aktualisiert. (2) Der "Belegverlauf" auf der Belege-Seite zeigte drei erfundene PDF-Einträge mit deaktivierten "Geplant für Sprint 1/2"-Buttons — die `documents`-Tabelle (§ 5.6) war seit ihrer Einführung nie befüllt worden, jede PDF-Erzeugung wurde nur einmalig gestreamt statt gespeichert. Neues Repository `document_repository.py`, echte Persistierung bei jeder Beleg-Erzeugung (Datei + Prüfsumme + DB-Eintrag), echter Belegverlauf mit funktionierendem Re-Download. (3) Eine statische "Beleg-Vorschau (Beispiel)" mit erfundenem "Max Mustermann" wurde als redundant entfernt. Zusätzlich die "System-Topologie"-Grafik ehrlich als Konzept-Diagramm gekennzeichnet und um einen echten Live-Leistungswert ergänzt, wo verfügbar. Alle Korrekturen einzeln Ende-zu-Ende getestet.** KRITISCHER BUG BEHOBEN — das Dashboard zeigte seit der ursprünglichen "Complete Visual Skeleton"-Entwurfsphase (§ 2.4) durchgehend feste Mockup-Zahlen (412 kWh, 140,08 €, 18 Sessions, 6,8 kW Gauge), die NIE mit der echten Datenbank verbunden waren. Rückmeldung des Auftraggebers: nach Bereinigung aller Test-Wallboxen bis auf eine echte Loxone-Wallbox mit einer einzigen echten Session (27,2 kWh) zeigte das Dashboard weiterhin die alten, fest einprogrammierten Werte. Neuer Endpunkt `/api/dashboard/summary` berechnet echte Kennzahlen (kWh/Kosten diesen Monat, Session- und Fahrtenanzahl, Ø kWh/Session, Pauschale-vs-Real-Differenz, aktuelle Live-Leistung aus `wallbox_live_metrics`, echte letzte Aktivität aus Sessions/Fahrten). Gauge- und Zähler-Animation vollständig von hartcodierten Werten auf echte Serverdaten umgestellt. Ende-zu-Ende getestet: vor jeder Session zeigt das Dashboard korrekt 0/leer, nach Verarbeitung der echten Loxone-Session exakt 27,2 kWh / 9,25 € / 1 Session / 6,8 kW live. Dashboard aktualisiert sich jetzt auch bei Navigation zurück zur Startseite neu.** PRODUKTVISION EXPLIZIT DOKUMENTIERT (§ 1.5, neu) — der Auftraggeber hat klargestellt, dass die direkte Loxone-API bewusst kein Notbehelf ist, sondern der eigentliche Differenzierungsfaktor gegenüber generischen OCPP-Abrechnungs-Apps: tiefe, Loxone-native Integration statt eines weiteren austauschbaren OCPP-Endpunkts. Live-Ansicht ergänzt (neue Tabelle `wallbox_live_metrics`) — direkte Antwort auf die Rückmeldung, dass der 60-Sekunden-Hintergrund-Abgleich bisher komplett unsichtbar war und der Auftraggeber nach eigener Konfiguration keinerlei sichtbaren Erfolg feststellen konnte. Jetzt wird bei jeder `/all`-Abfrage zusätzlich zur Session-Erkennung auch die aktuelle Ladeleistung (`Cp`) und der Verbindungsstatus (`Ca`) gespeichert und in der Wallbox-Verwaltung direkt unter der jeweiligen Wallbox angezeigt, inklusive Zeitpunkt des letzten Syncs (alle 15s automatisch aktualisiert) und einem manuellen Sofort-Aktualisieren-Button. Mit den vom Auftraggeber gelieferten echten Werten Ende-zu-Ende getestet: „has_data: false" vor der ersten Synchronisation, danach korrekt „verbunden: ja, 0 kW, Sync-Zeitpunkt" nach Verarbeitung der echten Miniserver-Antwort.** Zwangs-Löschung ergänzt — Wallboxen mit zugeordneten Sessions ließen sich nur nach vorherigem Einzel-Löschen jeder Session entfernen (kein Weg, beides in einem Schritt zu tun). Neuer Parameter `force=1` an `DELETE /api/wallboxes/{id}` löscht Wallbox samt aller zugeordneten Sessions in einem Schritt (mit Sicherheitsabfrage in der Oberfläche). Neuer Button "Alle Wallboxen ohne Sessions löschen" in der Wallbox-Verwaltung, verknüpft mit dem bereits vorhandenen, aber bislang nicht in der Oberfläche verlinkten `/api/wallboxes/delete-all`-Endpunkt. Vollständig getestet: normales Löschen bleibt blockiert bei vorhandenen Sessions, Force-Löschen entfernt Wallbox und Session in einem Schritt, Delete-All überspringt Wallboxen mit Sessions korrekt. UX-Überarbeitung der Wallbox-/Session-Verwaltung auf Wunsch des Auftraggebers. Bugfix: `delete_wallbox()` bereinigte die neu hinzugekommene Tabelle `loxone_last_charge_log` nicht — jede Wallbox, bei der "Wallbox2-Log prüfen" einmal gelaufen war, ließ sich dadurch nicht mehr löschen (FOREIGN-KEY-Verletzung), obwohl frisch angelegte, nie geprüfte Wallboxen funktionierten — exakt das vom Auftraggeber beschriebene Verhalten. Behoben und mit genau diesem Szenario nachgestellt getestet. Neu: kompakte Icon-Buttons statt Textbuttons in der Wallbox-Tabelle (✎ Bearbeiten, 🗑 Löschen, zusätzlich 📊/📁/📄/🔌 für Loxone-spezifische Aktionen), echte Bearbeiten-Funktion (bisher nur Anlegen/Löschen möglich — PUT-Route und Formular-Wiederverwendung ergänzt), "Alle löschen" sowohl für Wallboxen (überspringt und meldet Wallboxen mit noch zugeordneten Sessions) als auch für Ladesessions (mit vollständigem Audit-Log-Eintrag je gelöschter Session). Alle vier neuen/reparierten Funktionen über die echte Flask-API getestet.** Ladebeleg auf ausdrücklichen, feldgenauen Wunsch des Auftraggebers vollständig neu geschrieben (eigene Funktion statt gemeinsamem Layout-Gerüst) — nicht mehr nur stilistisch, sondern strukturell 1:1 nach der Chargemap-Rechnung: "Lieferadresse"-Block oben rechts, "Rechnung an:"-Block (Nachname, vollständiger Name, Adresse, Telefon/E-Mail aus den Personen-Feldern) links neben separatem "Rechnungsdatum"/"Fälligkeitsdatum"-Block rechts, Tabelle mit exakt den Original-Spalten (Beschreibung/Einzelpreis ohne MwSt/Menge/MwSt./Summe) statt eigener Spaltenaufteilung — ein Ladevorgang = eine Zeile mit Menge 1 und Gesamtbetrag als Einzelpreis, Beschreibungstext im Fließtext-Format ("Ladevorgang durchgeführt am [Wochentag] [Tag]. [Monat] [Jahr] von [Zeit] - [Ort] ([kWh] kWh)"), vollständige Summen-Kaskade (Gesamtbetrag ohne MwSt. / Gesamt-MwSt. / Gesamtbetrag inkl. MwSt. / grau hinterlegter "Zu zahlender Nettobetrag inkl. MwSt."). MwSt. durchgehend 0,00 % (bleibt inhaltlich Auslagenersatz, keine echte Umsatzsteuerpflicht). Mit den vom Auftraggeber übermittelten Chargemap-Beispielwerten getestet (27,45 €), Fahrtkosten-Belege unverändert im Regressionstest bestanden.** Letzte Korrektur zur exakten Chargemap-Angleichung — Unterschriftszeile und wiederholtes "Erstellt am" am Dokumentenende vollständig entfernt (Chargemap-Vorlage endet direkt nach der Summenzeile, ohne weiteren Inhalt). Überschrift von "Beleg Nr." auf "Rechnung Nr." geändert. Dokument endet jetzt exakt nach der Summenzeile, wie im Original. Regressionstest aller drei Beleg-Typen bestanden.** Ladebeleg-Layout auf ausdrücklichen Wunsch des Auftraggebers exakt an die Chargemap-Vorlage angeglichen ("exakt so"). Struktur jetzt 1:1: Marke oben links ("Charge Home" statt Firma des Auftraggebers — bewusst als Produktmarke wie "Chargemap", nicht als Rechtsperson), Adressblock oben rechts (statt "Lieferadresse"), Beleg-Nummer als alleinige, einzige Überschrift (kein zusätzlicher Beleg-Typ-Titel mehr — auf Wunsch entfernt), "Für:"-Block links neben farbig hinterlegter "Erstellt am"-Box rechts (analog zur blauen "Rechnungsdatum"-Box bei Chargemap). Fußzeile mit Firmendaten vollständig entfernt (auf Wunsch). Alle drei Beleg-Typen (Ladestrom, Fahrtkosten-AG, Fahrtkosten-Finanzamt) im Regressionstest weiterhin korrekt.** Ladebeleg-Layout grundlegend neu gestaltet — Rückmeldung des Auftraggebers, dass die farbige Vollbild-Kopfleiste (v7.7) den beiden Vorlagen (Audi Charging/Elli, Chargemap) optisch nicht ähnlich genug war. Jetzt strukturell näher am Original: überwiegend weißer Hintergrund statt Farbbalken, kleiner Firmen-Schriftzug oben links (Löwemann IT, Adresse, Kontakt) mit einfachem Referenz-Block oben rechts (Beleg-Nr./Erstellt am, analog zu "Kundennummer/Rechnungsdatum" bei Elli), schlichte schwarze Überschrift mit dünner Trennlinie statt Farbblock, Fußzeile mit Firmendaten (wie bei beiden Vorlagen). Tabellen-Kopfzeile bleibt farbig (wie bei Chargemap). Per Rasterung visuell geprüft (Ladestrom- und beide Fahrtkosten-Belege), Regressionstest über die echte App bestanden.** Wallbox-Standort als echtes Feld ergänzt (Datenbankspalte `location` war bereits vorhanden, aber ungenutzt) — neues Eingabefeld in der Wallbox-Verwaltung, erscheint im Ladebeleg als "Ladestation" (Adresse statt nur interner Wallbox-Name), zusätzlich "Ladepunkt-Typ: AC" in der Berechnungszeile ergänzt — entspricht der Bezeichnungskonvention öffentlicher AC-Ladestationen (z. B. an Supermärkten/Baumärkten), auf Wunsch des Auftraggebers. Layout-Bug behoben: Die Adresse lief zunächst in die Datums-Spalte hinein (kein Zeilenumbruch in der Zelle); Ladestation-Spalte ist jetzt umbruchfähig (ReportLab-Paragraph statt Klartext) und Spaltenbreiten angepasst. Per Rasterung visuell geprüft, Fahrtkosten-Belege (nutzen dasselbe Layout-Gerüst) im Regressionstest unverändert korrekt.** Ladebeleg-Layout (FA-LS-06) auf Wunsch des Auftraggebers deutlich überarbeitet — Vorbild waren zwei echte, professionelle Ladeanbieter-Rechnungen (Audi Charging/Elli, Chargemap), die der Auftraggeber als Muster hochgeladen hat. Neu: farbiger Kopfbereich mit Absender (Löwemann IT) und Beleg-Nummer, je Ladevorgang jetzt Ladestation, exakte Ladebeginn-/Ladeende-Uhrzeit (nicht nur Datum) sowie eine eigene, kursive Berechnungszeile ("Zählerstand X Wh → Y Wh · Berechnung: A kWh × B €/kWh = C €") — analog zur "Ladekosten und Nutzung"-Zeile bei Elli. Bleibt inhaltlich ein interner Auslagenersatz-Eigenbeleg ohne MwSt.-Ausweisung und ohne Rechtstext (unverändert seit Change Request in Sprint 1) — nur das äußere Erscheinungsbild wurde professioneller gestaltet. Gemeinsames Layout-Gerüst wird auch von beiden Fahrtkosten-Belegen genutzt; Regressionstest bestanden (50 km × 0,15 €/km = 7,50 € weiterhin exakt). Layout-Bug bei der Beleg-Nummer-Umbruch behoben (riss zunächst mitten im Wort ab) und visuell per PDF-Rasterung geprüft.** Passwort-Persistenz robuster gemacht — trotz v7.5-Fix verschwand das Passwort weiterhin zwischen Aktionen (z. B. nach erfolgreichem "Verbindung testen" bei anschließendem "FTP durchsuchen"). Ursache: Der bisherige Zwischenspeicher wurde erst NACH einer erfolgreichen Aktion befüllt, nicht sofort bei Eingabe. Umgestellt auf sofortige, tastendruckbasierte Speicherung (`input`-Event-Listener auf dem Passwort-Feld) statt nachträglicher Zwischenspeicherung — das Passwort wird jetzt in dem Moment gemerkt, in dem es eingegeben wird, unabhängig davon, ob danach eine Aktion erfolgreich war. Zusätzlich global (nicht mehr je Wallbox) zwischengespeichert, da in der Praxis meist ohnehin nur ein Miniserver im Spiel ist.** Weiteren UX-Bug behoben — die Meldung "Bitte zuerst IP, Benutzername und Passwort eingeben" erschien wiederholt, obwohl Zugangsdaten bereits erfolgreich verwendet worden waren (z. B. beim Weiterklicken im FTP-Ordner-Browser). Ursache: Nur der ursprüngliche Button-Klick füllte IP/Benutzername automatisch nach; das Weiternavigieren innerhalb des Browsers rief die Prüfung direkt und ohne erneute Vorbelegung auf. Neuer Passwort-Zwischenspeicher (`wallboxPasswordCache`) merkt sich das einmal eingegebene Passwort je Wallbox für die laufende Sitzung (nur im Browser-Speicher, nirgends persistiert); `ftpLoadPath()` heilt sich jetzt zusätzlich selbst, indem es bei leeren Feldern automatisch erneut IP/Benutzername nachlädt.** Bugfix in `get_log_file_http()` — Eingabe von `/log/wallbox.log` (naheliegende Schreibweise, angelehnt an den FTP-Pfad) führte zu einer doppelten `/log/`-URL (`.../log//log/wallbox.log`) und damit zu 404, da der Endpunkt `/dev/fsget/log/` das Präfix bereits fest enthält. Eingabe wird jetzt normalisiert (führendes `/`, führendes `log/`, fehlende Dateiendung) — getestet mit fünf verschiedenen Schreibweisen (`/log/wallbox.log`, `log/wallbox.log`, `wallbox.log`, `/wallbox.log`, `wallbox`), alle führen jetzt nachweislich zur identischen, korrekten URL.** UX-Bug behoben — die Aktions-Buttons bei bereits gespeicherten Loxone-API-Wallboxen ("FTP durchsuchen", "Log-Datei (HTTP)", "Wallbox2-Log prüfen") lasen die Zugangsdaten fälschlicherweise aus dem Formular für neue Wallboxen, das bei einer schon gespeicherten Wallbox meist leer ist ("Bitte zuerst IP, Benutzername und Passwort eingeben" trotz vorhandener Wallbox). Jetzt werden IP-Adresse und Benutzername beim Klick automatisch aus der gespeicherten Wallbox übernommen (`/api/wallboxes/full`); nur das Passwort muss weiterhin einmal eingegeben werden, da es aus Sicherheitsgründen (NFA-11) nie im Klartext über die API zurückgegeben wird — per Test bestätigt, dass die API-Antwort tatsächlich kein Passwort-Feld enthält.** Auf Hinweis des Auftraggebers (offizielle Loxone-Logger-Dokumentation) zweiten, einfacheren Weg für Log-Dateien ergänzt: HTTP-GET auf `/dev/fsget/log/{Dateiname}.log` — nutzt denselben, bei diesem Miniserver bereits bestätigt funktionierenden Basic-Auth-Mechanismus, umgeht damit mögliche FTP-spezifische Probleme (Port 21, Freigabe-Einstellung) vollständig. Neue Funktion `import_full_log_text()` verarbeitet eine komplette, mehrzeilige Logger-Datei auf einen Schlag. Mit den echten Beispieldaten aus dem Referenzprojekt (s2patrick/LoxoneWallboxReporting, 6 Zeilen, Januar 2024) vollständig getestet: alle 6 historischen Sessions korrekt importiert (Summe 132,4 kWh stimmt exakt), wiederholter Import derselben Datei erzeugt nachweislich keine Duplikate. Neuer "Log-Datei (HTTP)"-Button in der Wallbox-Verwaltung. Voraussetzung bleibt: ein Logger-Baustein muss in Loxone Config an die Wallbox angeschlossen sein — ohne Logger gibt es keine Datei zum Abrufen, unabhängig vom Übertragungsweg. (Frühere Änderungen v7.0/v7.1: Wallbox2-Log-Erkennung über `/all` + Lcl-Feld, mit echten Werten getestet — 27,2 kWh, korrekte Zeiten; FTP-Fehlerbehandlung robuster gemacht, deckt aber möglicherweise nicht die vollständige Ursache des gemeldeten FTP-Problems ab.)**

---

## 1. Einleitung

### 1.1 Zweck des Dokuments
Dieses Pflichtenheft ist die vollständige, technische Umsetzungsspezifikation für die Charge@Home Billing Engine. Es beschreibt **was** gebaut wird (Lastenheft-Anteil, siehe begleitende Produktspezifikation) und **wie** es technisch umgesetzt wird (Pflichtenheft im engeren Sinn): Datenmodell, Schnittstellen, Architektur, Anforderungskatalog, Testkriterien. Es ist die alleinige Referenz für die Implementierung; Abweichungen davon sind Change Requests (siehe Abschnitt 11), keine stillschweigenden Anpassungen während der Umsetzung.

### 1.2 Geltungsbereich
Gilt für die Sprints 0–6. DATEV-Export ist explizit nicht Teil dieses Dokuments (siehe Abschnitt 13).

### 1.3 Referenzdokumente
- Produktspezifikation "Charge@Home Billing Engine" v1.0
- Steuerlich-fachliche Klärungen aus der Projektentwicklung (Fälle A/B/C, BMF-Schreiben 11.11.2025, Kilometerpauschale 2026)

### 1.4 Abkürzungen
FA = Funktionale Anforderung · NFA = Nicht-funktionale Anforderung · DoR = Definition of Ready · DoD = Definition of Done · CS = Central System (OCPP) · CP = Charge Point (OCPP)

### 1.5 Produktvision — Architektur-Kurskorrektur (überarbeitet v10.0, ersetzt v8.5)
**Diese Fassung widerspricht bewusst der ursprünglichen Festlegung aus v8.5 und ersetzt sie vollständig.** Dort war festgehalten: die direkte Loxone-API sei der strategische Kern, OCPP nur Fallback. Nach praktischer Erfahrung dieses Sprints (mehrere Loxone-spezifische Bugs: Ca/Vc-Verwechslung, Wallbox2-States nicht einzeln abrufbar, wiederholte Verschlüsselungsschlüssel-Probleme, notwendiger zusätzlicher Logger-Baustein für Historie) hat der Auftraggeber die Einschätzung revidiert, und nach eigener Abwägung wird dem zugestimmt:

- **OCPP ist der primäre, empfohlene Weg** — herstellerunabhängig (funktioniert mit jeder OCPP-1.6-kompatiblen Wallbox, nicht nur Loxone), push-basiert (echtzeitnah ohne Poll-Intervall), erfordert kein Zugangsdaten-Management auf unserer Seite (keine Verschlüsselung, keine Loxone-Eigenheiten), einfachere Einrichtung (eine URL in Loxone Config, kein zusätzlicher Logger-Baustein nötig).
- **Die direkte Loxone-API bleibt bestehen, aber als optionale "Erweiterte Live-Ansicht"** — für Loxone-Nutzer, die den granularen Echtzeit-Blick (aktuelle Leistung, Fahrzeug verbunden/lädt aktiv, laufende Session) zusätzlich wollen. Kein Kernstück der Abrechnung mehr.
- **CSV-Import bleibt als universeller, abhängigkeitsfreier Fallback** — funktioniert immer, unabhängig vom Wallbox-Typ.
- **FTP-Browser und HTTP-Log-Datei-Import wurden ENTFERNT** (v10.0) — beide erforderten einen zusätzlichen, manuell einzurichtenden Logger-Baustein in Loxone Config und lieferten im Kern dieselben Daten, die OCPP bzw. die Live-Ansicht bereits liefern. Der Wartungs- und Erklärungsaufwand von fünf parallelen Dateneingangswegen stand in keinem Verhältnis zum Zusatznutzen.

---

## 2. Systemkontext und Vorgehensmodell

### 2.1 Systemkontext
Einzelplatz- bzw. Kleinstinstallation, self-hosted, ein Docker-Container, ein bis wenige gleichzeitige Nutzer, ein bis wenige Wallboxen. Kein Multi-Tenant-Betrieb in Sprint 0–6.

### 2.2 Vorgehensmodell (explizit benannt)
Es wird ein **inkrementelles Vorgehensmodell mit sprintweise vollständig abgeschlossenen Wasserfall-Zyklen** verwendet ("Spec-first-Inkrement-Modell"):

- Der **gesamte funktionale und technische Umfang wird vorab in diesem Dokument fixiert** (kein Scrum-typisches Backlog-Grooming, keine Neupriorisierung während der Umsetzung).
- Jeder Sprint durchläuft intern die klassischen Wasserfall-Phasen **Design → Implementierung → Test → Abnahme**, bevor der nächste Sprint beginnt.
- Es gibt für jeden Sprint eine **Definition of Ready** (Startbedingung) und eine **Definition of Done** (Abschlussbedingung, inkl. Regressionstest der Vorgänger-Sprints).
- Begründung dieser Wahl: Der Kontext ist Einzelentwicklung mit KI-gestützter Umsetzung (Claude Code) ohne Product-Owner-Rolle im klassischen Scrum-Sinn; ein vorab fixierter Funktionsumfang verhindert Scope Creep und wiederholte Neubewertung bereits getroffener Entscheidungen.
- Änderungen am fixierten Umfang sind ausschließlich über das Change-Management-Verfahren in Abschnitt 11 möglich, nicht ad hoc während der Implementierung.

### 2.3 Qualitätssicherung je Sprint
Jeder Sprint schließt mit: (1) Unit-Tests für neue Funktionen, (2) manuellem Abnahmetest anhand der in Abschnitt 10 definierten Testfälle, (3) Regressionstest aller Vorgänger-Sprint-Funktionen, (4) Auslieferung als ZIP + Start-Skript (Abschnitt 12).

### 2.4 Vollständiges UI-Skelett ab Sprint 1 ("Complete Visual Skeleton")
Abweichend von einer rein schrittweisen UI-Entstehung wird die gesamte in Abschnitt 7 spezifizierte Oberfläche bereits mit Abschluss von Sprint 1 vollständig visuell bereitgestellt — nicht erst über die Sprints verteilt sichtbar. Dies betrifft ausschließlich die **visuelle Schicht** (Layout, Navigation, Formulare, Tabellenköpfe, Modals); die fachliche Logik und Datenanbindung folgen unverändert der in Abschnitt 10 festgelegten Sprint-Reihenfolge.

Konkret gilt ab Sprint 1:
- Alle sieben Seiten aus Abschnitt 7 sind vollständig gerendert und über die Navigation erreichbar, inklusive aller Formularfelder, Buttons und Tabellenköpfe.
- Elemente, deren fachliche Logik erst in einem späteren Sprint folgt (z. B. Live-OCPP-Status vor Sprint 3, Vergleichsrechner vor Sprint 4, Audit-Log-Anzeige vor Sprint 5), werden sichtbar, aber sichtbar deaktiviert (`disabled`) dargestellt, mit Tooltip/Hinweistext im Format „Geplant für Sprint X — Modul Y".
- Wo für die Beurteilung von Layout/Proportionen nötig (Diagramme, Tabellen), werden statische Testdaten gerendert.
- Ein nachträgliches Ergänzen zuvor fehlender Grundelemente (Seiten, Formularfelder, Navigationspunkte) in späteren Sprints ist unzulässig — das UI-Skelett entsteht einmalig und vollständig in Sprint 1.

Begründung: Verhindert layoutbedingte Überraschungen und mehrfaches Nacharbeiten der Oberfläche über den Projektverlauf, entsprechend dem in § 11 verankerten Ziel, unkontrolliertes Nacharbeiten zu vermeiden.

---

## 3. Funktionale Anforderungen

Nummerierung: `FA-<Modul>-<laufende Nummer>`. Alle hier gelisteten Anforderungen sind verbindlich (Muss) für den jeweils angegebenen Sprint — es gibt in Sprint 0–6 keine optionalen Funktionen außerhalb des in Abschnitt 13 genannten Ausschlusses.

### 3.1 Modul System/Fundament (SYS)

| ID | Anforderung | Akzeptanzkriterium | Sprint |
|---|---|---|---|
| FA-SYS-01 | Das System unterstützt Deutsch und Englisch über ein zentrales Wörterbuch. | Jeder sichtbare UI-Text ist über einen i18n-Key aufrufbar; kein hartcodierter Text im UI-Code. | 0 |
| FA-SYS-02 | Das System bietet einen Hell- und einen Dunkel-Modus. | Umschaltung ändert alle UI-Elemente konsistent, Einstellung bleibt pro Sitzung erhalten. | 0 |
| FA-SYS-03 | Das System unterscheidet Demo- und Lizenzmodus. | Demo-Modus begrenzt Sessions auf 20 und versieht PDFs mit Wasserzeichen „DEMO"; Lizenzschlüssel-Eingabe hebt beide Einschränkungen auf. | 0 |
| FA-SYS-04 | Jede Nutzerin/jeder Nutzer wählt genau einen Abrechnungsfall (A/B/C). | Auswahl ist Pflichtfeld beim Einrichten, steuert nachgelagert Rechtstexte und aktive Module. | 0 |
| FA-SYS-05 | Das System liefert sich als ZIP mit Start-Skript aus. | Skript entpackt, installiert Abhängigkeiten, startet Container, gibt Aufruf-URL aus — ohne manuelle Zwischenschritte. | 0 |

### 3.2 Modul Ladestrom (LS)

| ID | Anforderung | Akzeptanzkriterium | Sprint |
|---|---|---|---|
| FA-LS-01 | Import von Ladesessions aus CSV-Datei gemäß Format in Abschnitt 6.2. | Testdatei mit 10 Zeilen wird vollständig und fehlerfrei in `charging_sessions` übernommen. | 1 |
| FA-LS-02 | Manuelle Erfassung einer Einzelsession. | Formular mit Pflichtfeldern (Wallbox, Start, Ende, kWh) legt korrekten Datensatz an. | 1 |
| FA-LS-03 | Sessionliste mit Filter nach Zeitraum und Wallbox. | Filterung liefert korrekte Teilmenge, keine False Positives/Negatives im Testdatensatz. | 1 |
| FA-LS-04 | Nachträgliche Dienst-/Privat-Klassifizierung pro Session (nur Fall A/B relevant). | Änderung wird gespeichert und im Audit-Log (FA-COMP-01) erfasst. | 1 |
| FA-LS-05 | Monatsvariabler kWh-Preis, Default 0,34 €. | Preisänderung wirkt nur auf Sessions im gewählten Zeitraum, historische Belege bleiben unverändert. | 1 |
| FA-LS-06 | PDF-Belegerzeugung für gewählten Zeitraum. | Beleg enthält Kopfdaten, Sitzungstabelle, Summenblock, professionelles Layout (Kopfzeile mit Beleg-Nr., Akzentfarbe, formatierte Zählerstände). | 1 |
| FA-LS-07 | ~~Fallabhängiger Rechtstext im Beleg~~ — **entfallen auf Wunsch des Auftraggebers** (Change Request, siehe Changelog v4.3): Der Beleg enthält keine steuerliche/rechtliche Bewertung mehr, sondern ist ein reiner, fallunabhängiger Kostennachweis. | Beleg enthält keinerlei Verweis auf § 3 Nr. 50 EStG oder sonstige rechtliche Einordnung, unabhängig vom Abrechnungsfall. | 1 |
| FA-LS-BMF-01 | Optionale BMF-Schreiben-Referenz auf dem Ladestrom-Beleg (siehe Changelog v10.24) — standardmäßig AUS, um FA-LS-07 nicht zu verletzen. Bei Aktivierung in den Einstellungen erscheint eine kleine Fußzeile mit Verweis auf das BMF-Schreiben vom 11.11.2025 (GZ IV C 5 – S 2334/19/10007 :005). | Schalter `show_bmf_reference` in `app_settings`, Default "0". Beleg bleibt bei deaktiviertem Schalter exakt wie bei FA-LS-07 gefordert. | 10 |
| FA-LS-08 | Live-Erfassung über OCPP-Central-System (Loxone direkt + Drittanbieter). | Wallbox verbindet sich, Ladevorgänge erscheinen automatisch in derselben Sessionliste wie CSV-Importe (Feld `source`). | 3 |
| FA-LS-09 | Statusanzeige je Wallbox (Laden/Bereit/Fehler/Offline). | Statuswechsel der angebundenen Wallbox wird innerhalb von 30 Sekunden in der UI sichtbar. | 3 |
| FA-LS-10 | Alternative Live-Erfassung über die native Loxone HTTP/WebSocket-API als Alternative zu OCPP. **STATUS (siehe Changelog v6.6): Auftraggeber stuft diesen Weg als zwingend wichtig ein, trotz erheblicher Protokoll-Komplexität (RSA-Verschlüsselungshülle bei neueren Firmware-Versionen, ggf. JWT-Token statt Legacy-Token — siehe offizielle Loxone-Dokumentation "Communicating with the Miniserver"). Aktiv in Klärung, nicht zurückgestellt.** Zuverlässiger Parallelweg bleibt der **Loxone-Statistik-CSV-Import**. | Diagnose-Werkzeug `app/loxone/diagnose.py` erstellt — testet jeden Handshake-Schritt einzeln mit voller Rohantwort, zur Nutzung mit echtem Netzwerkzugriff (z. B. über Claude Code lokal beim Auftraggeber, da diese Entwicklungsumgebung keinen Zugriff auf das Heimnetzwerk hat). CSV-Import-Weg vollständig funktionsfähig und getestet. | 3 |
| FA-LS-11 | Bearbeiten und Löschen bestehender Ladesessions (manuelle Korrektur von Fehleingaben). | Session lässt sich über "Bearbeiten" mit vorausgefüllten Werten ändern und speichern; "Löschen" entfernt die Session nach Bestätigung. Beide Aktionen erzeugen einen Audit-Log-Eintrag. | 1 |
| FA-LS-12 | Automatische Übernahme des letzten bekannten Zählerstands als Vorschlag für "Zählerstand Start" bei manueller Erfassung, sobald eine bekannte Wallbox ausgewählt/eingegeben wird. | Für eine Wallbox mit vorhandenen Sessions wird beim Verlassen des Wallbox-Feldes automatisch der zuletzt bekannte Zählerstand in "Zählerstand Start" eingetragen. | 1 |

### 3.3 Modul Fahrtkosten (FK) — nur Fall C aktiv

| ID | Anforderung | Akzeptanzkriterium | Sprint |
|---|---|---|---|
| FA-FK-01 | Erfassung einer Fahrt mit Start-/Zieladresse, Datum, Anlass. | Datensatz wird korrekt in `trips` gespeichert. | 2 |
| FA-FK-02 | Distanzberechnung aus den Adressen. | Für ein Testadresspaar entspricht die berechnete Distanz (±5 %) einer manuell nachgeprüften Referenzstrecke. | 2 |
| FA-FK-03 | Manuelle km-Eingabe als Alternative zur Adressberechnung. | Nutzer kann km direkt eingeben, wenn keine Netzwerkverbindung für Routendienst besteht. | 2 |
| FA-FK-04 | Satzauswahl je Fahrt: 0,15 €/km, 0,30 €/km, frei editierbar, oder „keine Erstattung". | Gewählter Satz wird pro Fahrt persistent gespeichert (nicht nur als globale Einstellung). | 2 |
| FA-FK-05 | Automatische Berechnung Werbungskosten-Differenz (0,30 € − Arbeitgeber-Satz) × km. | Für Testfall 0,15 €-Satz und 100 km ergibt sich rechnerisch 15,00 € Differenz. | 2 |
| FA-FK-06 | PDF-Export „Arbeitgeber-Beleg" für frei wählbaren Zeitraum. | Enthält Fahrtenliste mit gewähltem Satz und Gesamtsumme. | 2 |
| FA-FK-07 | PDF-Export „Finanzamt-Jahresexport" für ein Kalenderjahr. | Enthält volle Fahrtenliste zu 0,30 €/km, bereits erhaltene Arbeitgeber-Erstattung, verbleibenden Differenzbetrag. | 2 |

### 3.4 Modul Auswertung (DASH)

| ID | Anforderung | Akzeptanzkriterium | Sprint |
|---|---|---|---|
| FA-DASH-01 | Balkendiagramm kWh/Kosten pro Monat. | Für Testdatensatz mit 3 Monaten zeigt Diagramm korrekte Summen je Monat. | 4 |
| FA-DASH-02 | Verlaufsdiagramm kumulierter Verbrauch/Kosten. | Kumulierte Linie entspricht der laufenden Summe der Einzelmonate. | 4 |
| FA-DASH-03 | Pauschale-vs-Real-Vergleichsrechner. | Für einen Testmonat mit hinterlegtem Realtarif zeigt das System beide Beträge und die günstigere Variante korrekt an. | 4 |
| FA-DASH-04 | Filterbare Kennzahlentabelle (Ø-Preis/kWh, Gesamtkosten, Sitzungsanzahl). | Werte stimmen mit manueller Nachrechnung des Testdatensatzes überein. | 4 |

### 3.5 Modul Compliance (COMP)

| ID | Anforderung | Akzeptanzkriterium | Sprint |
|---|---|---|---|
| FA-COMP-01 | Unveränderliches Audit-Log für alle nachträglichen Änderungen. | Jede Änderung an `charging_sessions` oder `trips` erzeugt einen `audit_log`-Eintrag mit Alt-/Neuwert, Zeitstempel, Nutzer. | 5 |
| FA-COMP-02 | Erkennung unvollständiger Transaktionen ("Zombie-Sessions"). | Eine Session ohne StopTransaction nach 24h wird automatisch markiert und in der UI hervorgehoben. | 5 |
| FA-COMP-03 | Erkennung Zählerüberlauf/-tausch. | Testfall mit `meter_stop < meter_start` wird als Anomalie markiert, nicht stillschweigend verrechnet. | 5 |
| FA-COMP-04 | Automatisiertes Backup (WAL-Streaming). | Simulierter Containerabsturz führt zu keinem Datenverlust seit letztem Checkpoint. | 5 |
| FA-COMP-05 | Datenexport als ZIP/CSV jederzeit möglich. | Export enthält alle Tabellen vollständig und ist erneut importierbar/lesbar. | 5 |

### 3.6 Modul Personen-Stammdaten (PERS) — Ergänzung nach Sprint 2

Leichtgewichtige, von `users_config` bewusst getrennte Stammdatenverwaltung für Belege (z. B. Familie/zweite Person, die dieselbe Installation mitnutzt, aber eigene Belege benötigt). Kein Bestandteil der Mehrbenutzer-Frage aus § 2.1 — es ändert sich nichts an Preis, Lizenz, Sprache oder Abrechnungsfall der einen installierten Instanz; nur die Kopfdaten eines einzelnen Belegs werden personenbezogen befüllt.

| ID | Anforderung | Akzeptanzkriterium | Sprint |
|---|---|---|---|
| FA-PERS-01 | Personen anlegen/bearbeiten/löschen (Name, E-Mail, Personalnummer, Kfz-Kennzeichen, Telefon — nur Name Pflichtfeld). | Verwaltung unter Einstellungen; alle vier Zusatzfelder sind einzeln optional und werden korrekt gespeichert. | 2 (Ergänzung) |
| FA-PERS-02 | Beleg-Erzeugung mit Personen-Auswahl und dynamischer Checkbox-Auswahl, welche Zusatzfelder im Dokument erscheinen. | Ohne Auswahl erscheint der Setup-Name ohne Zusatzfelder; mit Personen-Auswahl erscheinen nur die tatsächlich angehakten Felder (verifiziert: Kfz+E-Mail angezeigt, nicht angehakte Personalnummer korrekt nicht im PDF enthalten). | 2 (Ergänzung) |

### 3.7 Modul Protokoll/Log-Ansicht (PFLICHTPUNKT, ergänzt v8.8)
Auf ausdrücklichen Wunsch des Auftraggebers als verpflichtender Programmpunkt aufgenommen — nicht optional. Begründung: Wiederholt sind Fehler (Verschlüsselungs-Fehler, Verbindungsprobleme, falsch initialisierte Felder) erst durch Zufall in der Oberfläche aufgefallen, ohne dass der Auftraggeber selbst nachvollziehen konnte, was im Hintergrund passiert ist oder welche Fehler aufgetreten sind.

| ID | Anforderung | Akzeptanzkriterium | Sprint |
|---|---|---|---|
| FA-LOG-01 | Sichtbare Protokoll-/Log-Ansicht in der Oberfläche, die zeigt: Hintergrund-Poll-Zyklen (Erfolg/Fehler je Wallbox), OCPP-Server-Status-Änderungen, Fehler bei Entschlüsselung/Verbindung/Import, jeweils mit Zeitstempel. | Ansicht zeigt die letzten N Ereignisse, aktualisiert sich automatisch, macht Fehler wie "fehler: entschlüsselung" sofort sichtbar und nachvollziehbar, ohne dass der Nutzer Entwickler-Tools oder Server-Konsolen einsehen muss. | **Umgesetzt (v9.1)** |

---

## 4. Nicht-funktionale Anforderungen

| ID | Kategorie | Anforderung |
|---|---|---|
| NFA-01 | Betrieb | Vollständiger Betrieb ohne externe Cloud-Abhängigkeit; einzige optionale externe Verbindung ist die direkte OCPP-Verbindung zu Wallboxen im lokalen Netz. |
| NFA-02 | Datenhaltung | SQLite im WAL-Modus; keine automatische Löschung von Daten vor Ablauf von 10 Jahren (§ 147 AO). |
| NFA-03 | Sicherheit | Kein Klartext-Passwort-Logging. Innerhalb des lokalen Subnetzes ist unverschlüsseltes `ws://` zulässig; verpflichtend TLS (wss://) über Reverse Proxy ist **nur** erforderlich, sobald die Wallbox über das öffentliche Internet auf den Server zugreift. |
| NFA-04 | Internationalisierung | Vollständige Trennung von Text und Code ab Sprint 0 (siehe FA-SYS-01). |
| NFA-05 | Wartbarkeit | Schichtenarchitektur (siehe Abschnitt 8) mit klarer Trennung UI/Business-Logik/Datenzugriff. |
| NFA-06 | Portabilität | Lauffähig als Docker-Container auf jedem Linux/Windows/Mac-Docker-Host. |
| NFA-07 | Nachvollziehbarkeit | Jede Berechnung (kWh×Preis, km×Satz) muss aus den gespeicherten Rohdaten reproduzierbar sein — keine irreversiblen Aggregationen ohne Rohdatenerhalt. |
| NFA-08 | Nebenläufigkeit | Jede SQLite-Verbindung wird mit `timeout=10.0` Sekunden geöffnet (Busy-Timeout), um `database is locked`-Fehler bei gleichzeitigem Schreibzugriff (OCPP-Prozess) und Lesezugriff (PDF-Export, UI) abzufangen statt sofort fehlzuschlagen. |
| NFA-09 | Prozessisolation | Der OCPP-Dienst (asyncio-Event-Loop) läuft als **eigenständiger Prozess**, getrennt vom Streamlit-Prozess, gestartet über ein gemeinsames Entry-Point-Skript im Container. Ein Absturz eines der beiden Prozesse darf den jeweils anderen nicht beenden. Begründung: Streamlit und ein asyncio-Event-Loop sind unterschiedliche Ausführungsmodelle; ein gemeinsamer Thread mit eigenem Event-Loop birgt das Risiko, dass eine unbehandelte Exception den OCPP-Dienst still beendet, ohne dass dies in der UI sichtbar wird. Getrennte Prozesse mit unabhängigem Neustart sind hier robuster als ein Thread-Modell. |
| NFA-10 | Zugriffskontrolle OCPP | Eingehende OCPP-Verbindungen werden gegen eine in `wallboxes` hinterlegte Allowlist von `ocpp_charge_point_id`-Werten geprüft; unbekannte Charge-Point-IDs werden abgelehnt (kein automatisches Anlegen unbekannter Wallboxen). Zusätzlich optional HTTP-Basic-Auth auf WSS-Ebene bei Fernzugriff (OCPP 1.6 Security Whitepaper), verpflichtend sobald der Server über das Internet erreichbar ist (vgl. NFA-03). Klarstellung zur Verbindungsrichtung: Die Wallbox verbindet sich zum Central System, nicht umgekehrt — es werden keine Zugangsdaten der Wallbox selbst (z. B. Loxone-Miniserver-Login) in der Anwendung gespeichert; die OCPP-Backend-Adresse wird stattdessen einmalig auf Seiten der Wallbox-Konfiguration (z. B. Loxone Config) hinterlegt. |
| NFA-11 | Speicherung von Loxone-Zugangsdaten | **Geändert (v10.7, ausdrücklicher Wunsch des Auftraggebers):** Ursprünglich Verschlüsselung (Fernet/AES) gefordert — nach wiederholten, für den Nutzer sehr störenden Bugs rund um die Schlüssel-Verwaltung (Schlüssel-Verlust nach Updates, vermutete Race-Condition zwischen den drei parallelen Prozessen) wird `loxone_password_encrypted` jetzt bewusst im Klartext gespeichert. Begründung des Auftraggebers: Es handelt sich um Zugangsdaten zum eigenen Miniserver im eigenen Heimnetz, keine Bankdaten — die Abwägung Sicherheit vs. Zuverlässigkeit fällt zugunsten der Zuverlässigkeit aus. Feldname bleibt unverändert (historisch), enthält aber ab sofort Klartext. |
| NFA-12 | Design-Umsetzung | Visuelle Identität "Industrial Precision": dunkler Graphit-/Blauschwarz-Hintergrund mit dezentem Rastermuster; Amber-Kupfer-Glow als primärer Akzent für Energie-/Geldwerte, gedämpftes Teal als sekundärer Akzent für OCPP-/Netzwerkstatus (bewusst kein reines Neongrün-auf-Schwarz, um generische "KI-Dashboard"-Optik zu vermeiden). Kennzahlen in Mono-Schrift mit tabellarischen Ziffern und einmaliger Hochzähl-Animation beim Laden. Ein einzelnes Signatur-Element (analoges Halbkreis-Gauge für die aktuelle Ladeleistung) trägt die gestalterische Aufmerksamkeit, der Rest der Oberfläche bleibt zurückhaltend (Referenz: begleitendes UI-Mockup v2). **Umsetzung (ab v3.1, Flask):** Direktes, eigenständiges CSS (`app/static/style.css`) ohne Umweg über ein Widget-Framework — das bereits abgenommene Mockup-Markup wird nahezu unverändert als Jinja2-Template ausgeliefert. Hell-/Dunkel-Umschaltung erfolgt clientseitig über `data-theme`-Attribut und CSS-Variablen (funktioniert zuverlässig für alle Elemente, da keine nativen Fremd-Widgets mehr involviert sind — die frühere Einschränkung bei Streamlit entfällt damit vollständig). |
| NFA-13 | Barrierefreiheit bei Animation | Alle Animationen (Gauge, Zähler-Hochzählen, Balken-Einblendung) respektieren `prefers-reduced-motion`; bei aktivierter Einstellung werden Endzustände sofort ohne Animation angezeigt. |
| NFA-14 | Markenidentität | Die Anwendung verfügt über ein eigenes Icon/Logo (SVG, skalierbar), verwendet als Sidebar-Markenzeichen und als Grundlage für ein Favicon; Bildmotiv: stilisierter Blitz kombiniert mit einem Gauge-Ring, passend zur Design-Sprache aus NFA-12. |
| NFA-15 | Impressum-Zugänglichkeit | Von jeder Seite der Anwendung aus über einen Link im Sidebar-Footer erreichbar: ein Impressum-Dialog mit (a) Produktname, Versionsnummer und Build-/Renderstand der laufenden Instanz, (b) Betreiber-Pflichtangaben gemäß § 5 TMG (Unternehmen, Anschrift, Vertretungsberechtigte, Kontakt, USt-ID, redaktionell Verantwortlicher) sowie (c) Hinweis zur EU-Streitschlichtungsplattform. Die Betreiberangaben sind über eine Konfigurationsdatei anpassbar, nicht hartcodiert, da sie sich je nach Betreiber der jeweiligen Installation unterscheiden. |

---

## 5. Datenmodell

### 5.1 Tabelle `users_config`

| Feld | Typ | Constraint | Beschreibung |
|---|---|---|---|
| id | INTEGER | PK, AUTOINCREMENT | |
| name | TEXT | NOT NULL | |
| abrechnungsfall | TEXT | NOT NULL, CHECK IN ('A','B','C') | Steuert Rechtstext und aktive Module |
| employer_rate_choice | REAL | NULL | Nur relevant bei Fall C: gewählter Arbeitgeber-Kilometersatz (z. B. 0,15) |
| default_kwh_price | REAL | NOT NULL, DEFAULT 0.34 | |
| language_pref | TEXT | NOT NULL, DEFAULT 'de' | |
| theme_pref | TEXT | NOT NULL, DEFAULT 'light' | |
| license_status | TEXT | NOT NULL, DEFAULT 'demo', CHECK IN ('demo','licensed') | |
| created_at | DATETIME | NOT NULL, DEFAULT CURRENT_TIMESTAMP | |

### 5.2 Tabelle `wallboxes`

| Feld | Typ | Constraint | Beschreibung |
|---|---|---|---|
| id | INTEGER | PK, AUTOINCREMENT | |
| name | TEXT | NOT NULL | |
| source_type | TEXT | NOT NULL, CHECK IN ('ocpp','csv','manual','loxone_api') | `loxone_api` = direkte Anbindung über die native Loxone HTTP/WebSocket-API, alternativ zu OCPP |
| serial_number | TEXT | NULL | |
| ocpp_charge_point_id | TEXT | NULL, UNIQUE | Nur bei source_type='ocpp' |
| loxone_host | TEXT | NULL | Nur bei source_type='loxone_api': IP-Adresse oder Hostname des Miniservers |
| loxone_username | TEXT | NULL | Nur bei source_type='loxone_api' |
| loxone_password_encrypted | TEXT | NULL | Nur bei source_type='loxone_api'; **niemals im Klartext gespeichert**, siehe NFA-11 |
| location | TEXT | NULL | |
| created_at | DATETIME | NOT NULL, DEFAULT CURRENT_TIMESTAMP | |

### 5.3 Tabelle `charging_sessions`

| Feld | Typ | Constraint | Beschreibung |
|---|---|---|---|
| id | INTEGER | PK, AUTOINCREMENT | |
| wallbox_id | INTEGER | FK → wallboxes.id, NOT NULL | |
| user_id | INTEGER | FK → users_config.id, NOT NULL | |
| source | TEXT | NOT NULL, CHECK IN ('ocpp','csv','manual') | |
| start_timestamp | DATETIME | NOT NULL | |
| end_timestamp | DATETIME | NULL | NULL = laufende/offene Session |
| meter_start_wh | INTEGER | NOT NULL | Immer in Wh, keine kWh-Speicherung |
| meter_stop_wh | INTEGER | NULL | |
| energy_kwh | REAL | GENERATED (meter_stop_wh − meter_start_wh) / 1000.0 | Berechnetes Feld |
| price_per_kwh | REAL | NOT NULL | Zum Zeitpunkt der Belegerstellung eingefroren |
| amount_eur | REAL | GENERATED energy_kwh × price_per_kwh | Decimal-Rechnung in der Anwendungsschicht, nicht Float |
| rfid_tag | TEXT | NULL | |
| classification | TEXT | NULL, CHECK IN ('dienstlich','privat',NULL) | Nur Fall A/B relevant |
| pv_mode | TEXT | NULL | Optionales Feld, siehe Produktspezifikation Abschnitt PV |
| status | TEXT | NOT NULL, DEFAULT 'open', CHECK IN ('open','closed','anomaly') | |
| created_at | DATETIME | NOT NULL, DEFAULT CURRENT_TIMESTAMP | |
| updated_at | DATETIME | NOT NULL, DEFAULT CURRENT_TIMESTAMP | |

**Index:** `idx_sessions_period` auf (`start_timestamp`, `wallbox_id`) für Filterperformance.

### 5.4 Tabelle `trips`

| Feld | Typ | Constraint | Beschreibung |
|---|---|---|---|
| id | INTEGER | PK, AUTOINCREMENT | |
| user_id | INTEGER | FK → users_config.id, NOT NULL | |
| trip_date | DATE | NOT NULL | |
| start_address | TEXT | NOT NULL | |
| end_address | TEXT | NOT NULL | |
| distance_km | REAL | NOT NULL | |
| purpose | TEXT | NOT NULL | |
| rate_chosen | REAL | NOT NULL | 0.15 / 0.30 / individueller Wert / 0 (keine Erstattung) |
| employer_amount_eur | REAL | GENERATED distance_km × rate_chosen | |
| diff_amount_eur | REAL | GENERATED distance_km × (0.30 − rate_chosen) | Werbungskosten-Vorschlag |
| created_at | DATETIME | NOT NULL, DEFAULT CURRENT_TIMESTAMP | |

### 5.5 Tabelle `audit_log`

| Feld | Typ | Constraint | Beschreibung |
|---|---|---|---|
| id | INTEGER | PK, AUTOINCREMENT | |
| entity_type | TEXT | NOT NULL | z. B. 'charging_sessions', 'trips' |
| entity_id | INTEGER | NOT NULL | |
| field_changed | TEXT | NOT NULL | |
| old_value | TEXT | NULL | |
| new_value | TEXT | NULL | |
| changed_by | TEXT | NOT NULL | |
| changed_at | DATETIME | NOT NULL, DEFAULT CURRENT_TIMESTAMP | |

**Constraint:** Diese Tabelle ist ausschließlich INSERT-fähig aus der Anwendungsschicht heraus — kein UPDATE/DELETE-Pfad im Code vorgesehen.

### 5.6 Tabelle `documents` (generierte Belege)

| Feld | Typ | Constraint | Beschreibung |
|---|---|---|---|
| id | INTEGER | PK, AUTOINCREMENT | |
| doc_type | TEXT | NOT NULL, CHECK IN ('ladestrom','fahrtkosten_ag','fahrtkosten_fa') | |
| period_start | DATE | NOT NULL | |
| period_end | DATE | NOT NULL | |
| user_id | INTEGER | FK → users_config.id, NOT NULL | |
| file_path | TEXT | NOT NULL | |
| checksum_sha256 | TEXT | NOT NULL | Für Manipulationserkennung |
| generated_at | DATETIME | NOT NULL, DEFAULT CURRENT_TIMESTAMP | |

### 5.7 Tabelle `persons` (Personen-Stammdaten für Belege, § FA-PERS-01)

| Feld | Typ | Constraint | Beschreibung |
|---|---|---|---|
| id | INTEGER | PK, AUTOINCREMENT | |
| name | TEXT | NOT NULL | |
| email | TEXT | NULL | |
| personalnummer | TEXT | NULL | |
| kfz_kennzeichen | TEXT | NULL | |
| telefon | TEXT | NULL | |
| created_at | DATETIME | NOT NULL, DEFAULT CURRENT_TIMESTAMP | |

---

## 6. Schnittstellenspezifikation

### 6.1 OCPP-1.6-J-Schnittstelle (Sprint 3)

Central-System-Rolle auf Port 9000, WebSocket-Endpunkt `ws://<host>:9000/ocpp/<chargePointId>`.

| OCPP-Message | Verarbeitung |
|---|---|
| BootNotification | Legt/aktualisiert Eintrag in `wallboxes` an (idempotent bei Reconnect), Antwort `Accepted`. |
| Authorize | Prüft `idTag` gegen bekannte RFID-Liste, Antwort `Accepted`/`Blocked`. |
| BootNotification (Zugriffskontrolle, siehe NFA-10) | Vor Verarbeitung wird geprüft, ob die verbindende `chargePointId` in `wallboxes.ocpp_charge_point_id` bereits hinterlegt ist. Unbekannte IDs werden mit `Rejected` beantwortet und nicht automatisch angelegt — das Anlegen einer neuen Wallbox erfolgt ausschließlich manuell über die Einstellungen-Seite (§ 7.7). |
| StartTransaction | Legt neuen `charging_sessions`-Eintrag an: `meter_start_wh`, `start_timestamp`, `status='open'`. |
| MeterValues | Schreibt bei **jedem** eingehenden Sample den aktuellen Zählerstand in `meter_stop_wh` der offenen Session (Update, kein separater Tabelleneintrag nötig). Zweck: Bricht die Verbindung vor StopTransaction ab (Stromausfall, Netzwerkfehler), bleibt der zuletzt bekannte Zählerstand erhalten, statt dass die Session ohne jeden Energiewert als „Zombie" markiert wird (siehe FA-COMP-02). |
| StopTransaction | Setzt `meter_stop_wh` final, `end_timestamp`, `status='closed'`. |
| Heartbeat | Nur Verbindungserhalt, keine Datenpersistenz. |
| StatusNotification | Aktualisiert Live-Status für FA-LS-09. |

### 6.2 CSV-Import-Format (Sprint 1)

Erwartetes Format (Encoding UTF-8, Trennzeichen Semikolon, Dezimaltrennzeichen Komma):

```
Datum;Startzeit;Endzeit;ZaehlerstandStart_Wh;ZaehlerstandEnde_Wh;RFID_Tag;WallboxID
2026-08-01;18:32;22:47;125430;138920;TAG001;WB-GARAGE-1
```

Parser-Verhalten: Zeilen mit fehlendem Pflichtfeld werden übersprungen und in einem Importprotokoll (nicht in der DB) aufgelistet, nicht stillschweigend verworfen. Das exakte Loxone-Exportformat wird bei Vorliegen einer realen Beispieldatei angepasst; bis dahin gilt dieses Format als verbindliche Spezifikation für Sprint 1.

### 6.3 PDF-Generierungs-Schnittstelle

Eingabe: Liste von `charging_sessions`- bzw. `trips`-Datensätzen für einen Zeitraum, `abrechnungsfall`, `doc_type`. Ausgabe: PDF-Datei gemäß Layout in Abschnitt 7.5, zusätzlich Eintrag in `documents` mit SHA-256-Prüfsumme der erzeugten Datei.

### 6.4 Direkte Loxone-API-Schnittstelle (Sprint 3, Alternative zu 6.1)

Für Wallboxen mit `source_type='loxone_api'` erfolgt die Anbindung nicht über OCPP, sondern direkt über die native Loxone Miniserver-API:

1. Verbindungsaufbau mit `loxone_host`, `loxone_username`, `loxone_password_encrypted` (entschlüsselt zur Laufzeit, nie dauerhaft im Klartext im Speicher gehalten).
2. Token-Authentifizierung: Firmware-Version abfragen (`/jdev/cfg/api`), RSA-Public-Key holen (`/jdev/cfg/getPublicKey`), Token per HMAC-Hash aus den Zugangsdaten anfordern (`/jdev/sys/gettoken/...`).
3. Einmaliger Abruf der Struktur-Datei (`/data/LoxAPP3.json`) zur Ermittlung der UUID des Wallbox-Bausteins.
4. Laufende Live-Abfrage bzw. Abonnement des Wallbox-Werts über WebSocket (`ws://<host>/ws/rfc6455`) oder HTTP-Kommando `jdev/sps/io/{uuid}/{command}`.
5. Werte (Ladeleistung, Zählerstand, Status) werden in dasselbe `charging_sessions`-Schema geschrieben wie bei OCPP (Feld `source='loxone_api'`).

**Auswahlkriterium für den Nutzer (§ 7.7):** Beide Wege liefern dieselbe Datenqualität; OCPP benötigt keine gespeicherten Zugangsdaten (Verbindungsrichtung Wallbox → Server), die direkte API benötigt IP/Login, funktioniert aber unabhängig vom OCPP-Implementierungsstand der jeweiligen Loxone-Firmware-Version.

---

## 7. UI-Spezifikation (Seite für Seite)

| Seite | Zweck | Eingaben | Aktionen |
|---|---|---|---|
| 7.1 Setup/Einrichtung | Ersteinrichtung: Name, Abrechnungsfall, Sprache, Theme | Formularfelder gem. `users_config` | Speichern → Weiterleitung Dashboard |
| 7.2 Dashboard | Übersicht Status + Kennzahlen | Zeitraum-Filter | Navigation zu Detailmodulen |
| 7.3 Ladesessions | Liste, Filter, manuelle Eingabe, CSV-Import | Filterfelder, Upload-Button, Eingabeformular | Klassifizieren, Bearbeiten, Beleg erzeugen |
| 7.4 Fahrten | Liste, Fahrt-Eingabe | Adressfelder, Datum, Anlass, Satz | Speichern, Beleg erzeugen |
| 7.5 Belege | Erzeugte PDFs einsehen/erneut herunterladen | Zeitraum, Beleg-Typ | Generieren, Download |
| 7.6 Auswertung | Diagramme, Vergleichsrechner | Zeitraum-Filter | — (reine Anzeige) |
| 7.7 Einstellungen | Preis, Wallbox-Verwaltung, Lizenz | Preisfeld, Wallbox-CRUD (inkl. Verbindungsart-Auswahl OCPP/direkte Loxone-API mit bedingt eingeblendeten Feldern IP-Adresse/Benutzername/Passwort), Lizenzschlüssel | Speichern, Aktivieren, Verbindung testen |

**PDF-Layout (7.5, verbindlich für FA-LS-06/FA-FK-06/07):** Kopfzeile (Name, Anschrift, Kennzeichen, Arbeitgeber) → Messbasis-Angabe → Tabelle (Datum, Zeit, kWh/km, Satz, Betrag) → Summenzeile → Rechtstext (fallabhängig) → Belegnummer/Datum/Prüfsumme → Unterschriftenfeld.

---

## 8. Softwarearchitektur

### 8.1 Schichtenmodell
```
Presentation Layer   → /app/templates, /app/static (Flask + Jinja2 + statisches CSS/JS)
Application Layer    → /app/services     (billing_service, trip_service, pdf_service, import_service, ocpp_service)
Data Access Layer    → /app/repositories (Datenbankzugriff, ein Repository pro Tabelle aus Abschnitt 5)
Data Layer           → /data/charging.db (SQLite)
```
**Architektur-Änderung (Change Request, siehe Changelog):** Die Presentation Layer wurde von Streamlit auf Flask + Jinja2-Templates + eigenständiges CSS/JS umgestellt. Grund: Streamlits native Widgets (Textfelder, Radio-Buttons, Tabellen) ließen sich per CSS-Injection nicht zuverlässig und nicht pixelgenau an das bereits abgenommene UI-Mockup anpassen — wiederholte Kontrast-, Schriftgrößen- und Farbprobleme bei nativen Komponenten waren die Folge (dokumentiert in den Versionen 2.6–3.0). Flask liefert das bestehende, bereits bestätigte HTML/CSS/JS direkt aus, ohne Fremd-Rendering durch ein Widget-Framework — dadurch entfällt diese Fehlerklasse strukturell.
Regel: Die Presentation Layer greift nie direkt auf Repositories zu, ausschließlich über Services. Services enthalten die gesamte Geschäftslogik (Berechnungen, Rechtstext-Auswahl); Repositories enthalten ausschließlich SQL-Zugriff ohne Geschäftslogik.

### 8.2 Verzeichnisstruktur
```
/app
  /templates
    index.html
  /static
    style.css
    app.js
    logo.svg
  /services
  /repositories
  /ocpp
  /i18n
    de.json
    en.json
  /tests
/data
  charging.db
/docs
  Pflichtenheft.md
docker-compose.yml
Dockerfile
start.sh
start.bat
```

### 8.3 Technologie-Stack
Python 3.12, Flask (Jinja2-Templates, statisches CSS/JS, keine Frontend-Build-Pipeline nötig), python-ocpp (asyncio, ab Sprint 3), SQLite3 (über eigenes Repository-Pattern, kein ORM notwendig angesichts der geringen Tabellenanzahl), ReportLab (ab Sprint 1), Docker Compose.

### 8.4 Prozessmodell (siehe NFA-09)
Der Container startet zwei unabhängige Prozesse über ein gemeinsames Entry-Point-Skript:
```
entrypoint.sh
  → Prozess 1: python app.py (Flask)     (Port 8501)
  → Prozess 2: python ocpp_server.py     (Port 9000, eigener asyncio-Event-Loop, ab Sprint 3)
```
Beide Prozesse greifen ausschließlich über die Repository-Schicht (Abschnitt 8.1) auf dieselbe SQLite-Datei zu, nie direkt. Ein Absturz eines Prozesses wird über den Container-Healthcheck erkannt; der jeweils andere Prozess bleibt unberührt lauffähig.

---

## 9. Test- und Abnahmekonzept

- **Unit-Tests:** Für jede Berechnungsfunktion in `services/` (Decimal-Rechnung für Geldbeträge, keine Float-Rundungsfehler).
- **Integrationstests:** CSV-Import mit Testdatei, OCPP-Verbindung gegen Simulator, PDF-Erzeugung gegen Struktur-Check (enthält alle Pflichtfelder aus Abschnitt 7.5).
- **Manuelle Abnahme je Sprint:** Anhand der „Definition of Done"-Kriterien in Abschnitt 3 (Spalte Akzeptanzkriterium) und Abschnitt 10.
- **Regressionstest:** Vor Abschluss jedes Sprints werden alle Akzeptanzkriterien der Vorgänger-Sprints erneut geprüft.

---

## 10. Sprint-Spezifikation (Definition of Ready / Done je Sprint)

| Sprint | Definition of Ready | Abgedeckte FA-IDs | Definition of Done |
|---|---|---|---|
| 0 | Dieses Pflichtenheft ist verbindlich freigegeben. | FA-SYS-01 bis 05 | Alle FA-SYS-Kriterien erfüllt, ZIP+Start-Skript nachweislich lauffähig. |
| 1 | Sprint 0 abgenommen. | FA-LS-01 bis 07 | Alle FA-LS-01–07-Kriterien erfüllt. Zusätzlich gemäß § 2.4: vollständiges UI-Skelett aller sieben Seiten aus § 7 vorhanden und navigierbar; Elemente ohne in Sprint 1 vorhandene Logik sind sichtbar, aber deaktiviert, mit „Geplant für Sprint X — Modul Y"-Hinweis. Regressionstest Sprint 0 bestanden. |
| 2 | Sprint 1 abgenommen. | FA-FK-01 bis 07 | Alle FA-FK-Kriterien erfüllt, Regressionstest Sprint 0+1 bestanden. |
| 3 | Sprint 1 abgenommen (Datenmodell vorhanden). | FA-LS-08, FA-LS-09, FA-LS-10 | Loxone-Testverbindung über **beide** Wege (OCPP und direkte API) nachweislich erfolgreich, Prozessisolation nach NFA-09 verifiziert (erzwungener Abbruch des OCPP-Prozesses beendet Flask nicht und umgekehrt), MeterValues-Zwischenspeicherung nach Abschnitt 6.1 verifiziert (simulierter Verbindungsabbruch mitten in einer Session hinterlässt einen plausiblen `meter_stop_wh`-Wert statt NULL), gespeicherte Loxone-Zugangsdaten nachweislich verschlüsselt in der Datenbank (NFA-11), Regressionstest Sprint 0–2 bestanden. **Einschränkung:** Der OCPP-Transport-Layer selbst (`app/ocpp/server.py`) konnte in der Entwicklungsumgebung nicht ausgeführt/getestet werden (Pakete `ocpp`/`websockets` nicht installierbar, Sandbox-Netzwerkbeschränkung) — die komplette Geschäftslogik (Zugriffskontrolle, Session-Erstellung, Zwischenspeicherung, Abschluss) wurde jedoch vollständig gegen die echte Datenbank getestet (siehe `services/ocpp_service.py`, unabhängig von diesen zwei Paketen lauffähig). Echte Verbindung mit Wallbox-Hardware steht noch aus. |
| 4 | Sprint 1 und 3 abgenommen. | FA-DASH-01 bis 04 | Alle FA-DASH-Kriterien erfüllt, Regressionstest Sprint 0–3 bestanden. |
| 5 | Sprint 1 bis 3 abgenommen. | FA-COMP-01 bis 05 | Alle FA-COMP-Kriterien erfüllt, Regressionstest Sprint 0–4 bestanden. |
| 6 | Sprint 0 bis 5 abgenommen. | — (Härtung, keine neuen FA) | Vollständiger Durchlauf aller FA-Kriterien in beiden Sprachen/Themes ohne offene Punkte. |

---

## 11. Änderungsmanagement (Scope-Control)

Jede Änderung am hier fixierten Funktionsumfang während der Umsetzung erfordert:
1. Schriftliche Change-Request-Notiz (was ändert sich, warum, welcher Sprint ist betroffen).
2. Explizite Freigabe vor Umsetzung.
3. Keine Umsetzung „nebenbei" innerhalb eines laufenden Sprints ohne diese Freigabe.

Dies dient ausdrücklich dazu, unkontrolliertes Anwachsen des Funktionsumfangs während der Entwicklung zu verhindern.

---

## 12. Auslieferungsmechanik

ZIP-Paket + Start-Skript pro Sprint. Das Start-Skript (`start.sh` / `start.bat`):
1. Entpackt die ZIP-Datei (falls noch nicht geschehen).
2. Installiert benötigte Abhängigkeiten bzw. baut den Docker-Container.
3. Startet die Anwendung.
4. Gibt die Aufruf-URL im Terminal aus.

Ziel: Ein Doppelklick bzw. ein Kommandozeilen-Aufruf genügt für einen vollständigen Testlauf, ohne manuelle Zwischenschritte.

---

## 13. Bewusst ausgeklammerte Erweiterungen (außerhalb Sprint 0–6)

Folgende Punkte sind **nicht** Teil der Sprints 0–6, sondern als konkrete, dokumentierte spätere Upgrade-Optionen festgehalten — bewusst benannt, damit sie nicht als vage "vielleicht irgendwann" im Raum stehen, sondern als klar abgegrenzte, spätere Erweiterungsentscheidungen:

1. **DATEV-Export.** Buchungssatz-Export für die Lohn-/Finanzbuchhaltung. Wird erst nach vollständigem Abschluss aller sechs Sprints betrachtet.
2. **Dediziertes Mobile-Frontend / native App bzw. PWA.** Falls Bedarf für eine touch-optimierte native App oder Progressive Web App entsteht, ist das eine separat zu spezifizierende Erweiterung — keine Änderung an Sprint 0–6.
3. **PV-Anteil je Ladesession/Monat.** Technisch nur über die direkte Loxone-API möglich (nicht über OCPP) und nur, wenn der jeweilige Miniserver tatsächlich separate PV-Erzeugungs- und Netzbezugszähler als eigene Datenpunkte führt — nicht bei jeder Installation garantiert. Bleibt daher ein optionales Nice-to-have, kein Bestandteil von Sprint 0–6. Das Datenmodell ist dafür bereits vorbereitet (`charging_sessions.pv_mode`, § 5.3), sollte es später doch umgesetzt werden.

~~3. Vollständiger Wechsel der Presentation Layer~~ — **erledigt**, siehe Changelog Version 3.1 (Wechsel von Streamlit auf Flask, Change Request angenommen und umgesetzt).

Diese Punkte werden nicht "nebenbei" in laufende Sprints hineingezogen; ein Umstieg würde ein eigenes, neues Pflichtenheft-Kapitel mit eigener Sprint-Planung erhalten.

---

## 14. Interne Sprint-Status-Seite (temporär, kein Produktbestandteil)

Zur besseren Nachvollziehbarkeit des Umsetzungsstands während der Entwicklung enthält die Anwendung eine zusätzliche Seite "Sprint-Status" (Navigationsgruppe "Intern"), die den Status aller FA-IDs aus Abschnitt 3 je Sprint anzeigt (Fertig/Geplant). Diese Seite ist **kein dauerhafter Bestandteil des Produkts** — sie dient ausschließlich der internen Abstimmung während der Entwicklung und kann jederzeit entfernt, gelöscht oder ausgeblendet werden, ohne dass dies eine Änderung an den eigentlichen FA-/NFA-Anforderungen darstellt. Die Datenbasis (`PROJECT_STATUS` in `app.py`) wird manuell bei Abschluss eines Sprints nachgeführt.

## 15. Glossar

**Auslagenersatz:** Steuerfreie Erstattung tatsächlicher Kosten nach § 3 Nr. 50 EStG. **Belegnummer:** Fortlaufende Kennung eines generierten PDF-Dokuments. **CS/CP:** Central System / Charge Point (OCPP-Rollen). **GoBD:** Grundsätze zur ordnungsmäßigen Führung und Aufbewahrung von Büchern. **WAL:** Write-Ahead-Logging (SQLite-Modus für gleichzeitigen Zugriff).
