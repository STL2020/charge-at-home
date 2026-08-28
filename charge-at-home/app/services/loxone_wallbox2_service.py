"""
Loxone-Wallbox2-Service — FA-LS-10.

VERLAUF DER ENTSCHEIDUNG (siehe Pflichtenheft-Changelog, wichtig fuer
Nachvollziehbarkeit): Zunaechst wurde ausschliesslich das "Lcl"-Textfeld
ausgewertet — Problem: zeigt immer nur den letzten Eintrag, eine dazwischen
liegende "getrennt"-Zeile kann von einer neueren "verbunden"-Zeile
ueberschrieben werden, BEVOR wir sie je gesehen haben (nachweislich in der
Praxis aufgetreten). Als Ersatz wurde kurzzeitig auf "Mr" (Meter Reading,
reine Zahl) umgestellt — Ruecksprache mit dem Auftraggeber ergab aber, dass
unklar ist, ob sich Mr ueberhaupt ueber Session-Grenzen hinweg fortsetzt oder
pro Ladevorgang zurueckgesetzt wird (nicht verifizierbar ohne aufwendigen
Feldtest). PARALLEL wurde die Logger-Datei (die der Auftraggeber zwischen-
zeitlich entfernt hatte) wieder angeschlossen und mit echten Log-Zeilen belegt
— das beweist: der Logger erfasst LUECKENLOS jeden Uebergang, inklusive sehr
kurzer Wiederanstecker (< 20 Sekunden), die ein 60s-Polling zuverlaessig
verpassen wuerde.

ENDGUELTIGE ARCHITEKTUR (diese Datei): Die vollstaendige Logdatei (siehe
loxone_log_import_service.py) ist die ALLEINIGE Quelle fuer abgeschlossene,
abrechnungsrelevante Sessions — sie liefert Loxones eigene, exakte Berechnung
(Energie/Dauer/Kosten), nicht unsere eigene Naeherung. Diese Datei hier
uebernimmt NUR NOCH die Live-Anzeige (aktuell verbunden? laedt gerade? mit
wieviel kW?) ueber die reinen Zahlenfelder Vc/Cac/Cp — bewusst OHNE daraus
selbst Sessions anzulegen, um Doppelbuchungen mit dem Log-Import
auszuschliessen.
"""

import re

from services import db_service
from repositories import loxone_config_repository

# Ereigniszeilen ohne Abrechnungsdaten, etwa
#   "2026-08-23 22:37:41:Fahrzeug verbunden;user:<Benutzerkennung>"
# Sie markieren den BEGINN einer Ladesession — die vollstaendige Zeile mit
# Energie und Dauer folgt erst beim Abstecken. Fuer die Zuordnung zum
# Benutzer und den Startzeitpunkt sind sie trotzdem wertvoll.
LCL_EREIGNIS_PATTERN = re.compile(
    r"(?P<timestamp>\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2}):"
    r"(?P<event>[^;]+)"
    r"(?:;user:(?P<user>[^;]*))?"
)

LCL_PATTERN = re.compile(
    r"(?P<timestamp>\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2}):"
    r"(?P<event>[^;]+);"
    r"user:(?P<user>[^;]*);"
    r"Geladene Energie:(?P<energy_kwh>[\d.,]+)\s*kWh;"
    r"Dauer:(?P<duration_s>\d+)\s*s;"
    r"(?P<cost_eur>[\d.,]+)\s*€"
)


def parse_last_charge_log(lcl_text: str) -> dict | None:
    """Zerlegt einen Lcl-formatierten Text (auch mit vorangestelltem
    Logger-Zeilen-Praefix "TIMESTAMP;LoggerName;…", siehe .search() statt
    .match()) in seine Bestandteile. Wird sowohl hier fuer die reine
    Info-Anzeige als auch von loxone_log_import_service.py fuer den
    eigentlichen, abrechnungsrelevanten Import verwendet. None, falls das
    Format nicht passt (z. B. "verbunden"-Zeilen ohne Abrechnungsdaten)."""
    if not lcl_text:
        return None
    match = LCL_PATTERN.search(lcl_text)
    if match is None:
        return None
    return {
        "timestamp": match.group("timestamp"),
        "event": match.group("event"),
        "user": match.group("user"),
        "energy_kwh": float(match.group("energy_kwh").replace(",", ".")),
        "duration_s": int(match.group("duration_s")),
        "cost_eur": float(match.group("cost_eur").replace(",", ".")),
    }


def parse_lcl_ereignis(lcl_text: str) -> dict | None:
    """Liest auch Zeilen ohne Abrechnungsdaten aus.

    Der Baustein schreibt in Lcl mehrere Zeilentypen: Beim Anstecken eine
    Ereigniszeile, beim Abstecken die vollstaendige mit Energie und Dauer.
    Frueher wurde der erste Typ verworfen — dabei nennt er Startzeitpunkt und
    Benutzer, beides fuer die spaetere Zuordnung nuetzlich."""
    if not lcl_text:
        return None
    voll = parse_last_charge_log(lcl_text)
    if voll is not None:
        voll["typ"] = "abgeschlossen"
        return voll
    m = LCL_EREIGNIS_PATTERN.search(lcl_text)
    if m is None:
        return None
    return {
        "typ": "ereignis",
        "timestamp": m.group("timestamp"),
        "event": (m.group("event") or "").strip(),
        "user": (m.group("user") or "").strip() or None,
    }


def process_all_values(wallbox_id: int, user_id: int, all_values: dict, default_price: float) -> dict:
    """Verarbeitet eine /all-Antwort NUR fuer die Live-Anzeige (siehe Modul-
    Docstring) — legt bewusst KEINE Sessions an (das macht ausschliesslich
    der Log-Import, um Doppelbuchungen zu vermeiden). Speichert aktuelle
    Leistung (Cp) und Verbindungsstatus (Vc = "Vehicle connected") als
    Live-Momentaufnahme (siehe wallbox_live_metrics).

    Rueckgabe: {'action': 'live_update', 'connected': bool, 'charging': bool}."""
    werte = lies_alle_ausgaenge(all_values)
    current_power = werte.get("leistung_kw")
    # Vc = "Vehicle connected" (Fahrzeug verbunden).
    # Ca = "Charging allowed" waere nur eine Erlaubnis, kein Verbindungsstatus.
    connected = werte.get("verbunden", False)
    charging = werte.get("laedt", False)
    import json as _json
    loxone_config_repository.set_live_metrics(
        wallbox_id, current_power, connected,
        _json.dumps(all_values, ensure_ascii=False))

    # Zaehlerstand und Zaehlwerke fortschreiben: 'Mr' ist der ABSOLUTE
    # Zaehlerstand des Bausteins und damit derselbe Nachweiswert wie bei OCPP.
    # Bisher wurde er ignoriert — deshalb zeigte die Wallbox-Karte keinen
    # Zaehlerstand und die Loxone-Daten galten als nicht revisionssicher.
    try:
        speichere_zaehlerwerte(wallbox_id, werte)
    except Exception:
        pass

    # Lcl NUR fuer eine lesbare Protokollzeile parsen (rein informativ) —
    # der eigentliche, abrechnungsrelevante Import laeuft ueber die
    # vollstaendige Logdatei (loxone_log_import_service.py), nicht hier.
    lcl_text = all_values.get("Lcl", "")
    last_lcl = loxone_config_repository.get_last_lcl(wallbox_id)
    lcl_info = None
    if lcl_text != last_lcl:
        loxone_config_repository.set_poll_state_lcl(wallbox_id, lcl_text)
        parsed_for_log = parse_last_charge_log(lcl_text)
        if parsed_for_log is not None:
            lcl_info = (
                f"Lcl zur Info: {parsed_for_log['energy_kwh']} kWh, {parsed_for_log['duration_s']}s "
                f"— wird per Log-Datei-Abgleich abrechnungswirksam erfasst, nicht direkt hier."
            )
        else:
            lcl_info = f"Lcl zur Info: '{lcl_text}'"

    return {
        "action": "live_update",
        "connected": connected,
        "charging": charging,
        "current_power_kw": current_power,
        "lcl_info": lcl_info,
    }



# ── Vollstaendige Auswertung der Bausteinausgaenge ─────────────────────────
# Der Loxone-Wallbox-Baustein stellt ueber /dev/sps/io/<UUID>/all saemtliche
# Ausgaenge bereit. Bisher wurden nur drei davon gelesen (Cp, Vc, Cac) — der
# Zaehlerstand und die Verbrauchszaehler blieben ungenutzt, obwohl sie in
# derselben Antwort stehen.
#
# Feldbedeutungen laut offizieller Dokumentation (Baustein "Wallbox"):
#   Mr    Meter reading             Zaehlerstand (absolut)
#   Cp    Current charging power    aktuelle Ladeleistung
#   Ccc   Consumption current charge  Verbrauch der laufenden Ladung
#   Clc   Consumption last charge   Verbrauch der letzten Ladung
#   Cd/Cld  Consumption today / yesterday
#   Cw    Consumption this week
#   Cm/Clm  Consumption this month / last month
#   Cy/Cly  Consumption this year / last year
#   Cclc  Charging costs last charge  Kosten der letzten Ladung
#   Vc    Vehicle connected         Fahrzeug verbunden
#   Cac   Charging active           Ladevorgang aktiv
#   Ca    Charging allowed          Laden erlaubt (Erlaubnis, kein Status)
#   M     Current charging mode     Lademodus 1-5, 99 = manuell
#   Tp    Target charging power     Zielleistung des Modus
#   Ls    Load shedding             Lastabwurf aktiv
#   Uid   User ID                   angemeldeter Benutzer
#   Ss/Se Pulse session started/ended
#   Lcl   Last charge log           Textzeile der letzten Ladung

def _zahl(rohwert, min_wert=None):
    """Loxone liefert Zahlen als Zeichenkette, teils mit Einheit.

    `min_wert` begrenzt nach unten: Der Baustein meldet die Ladeleistung im
    Ruhezustand als '-0.000', was als negative Leistung unsinnig waere."""
    if rohwert in (None, ""):
        return None
    try:
        text = str(rohwert).strip().split(" ")[0].replace(",", ".")
        wert = float(text)
        if min_wert is not None:
            # '+ 0.0' normalisiert negative Null: Python behandelt -0.0 als
            # gleich 0.0, sodass ein reiner Vergleich sie durchlaesst und die
            # Anzeige "-0,00 kW" zeigt.
            wert = max(wert, min_wert) + 0.0
        return wert
    except (TypeError, ValueError):
        return None


# Obergrenze fuer die Zaehlwerke des Bausteins. Eine private Wallbox liefert
# hoechstens 11 kW; selbst bei Dauerbetrieb sind das rund 8.000 kWh im Monat.
# Werte darueber stammen erfahrungsgemaess aus dem Anlernen des Zaehlers und
# wuerden jede Auswertung verfaelschen.
MAX_MONAT_KWH = 5000.0
MAX_JAHR_KWH = 40000.0


def _plausibel(wert, grenze, vergleich=None):
    """Verwirft offensichtlich unmoegliche Zaehlwerte.

    Zusaetzlich zur festen Obergrenze wird gegen einen kleineren Zeitraum
    geprueft: Ein Monatswert, der das Tausendfache des Wochenwerts betraegt,
    ist kein Verbrauch, sondern ein Zaehlerfehler."""
    if wert is None:
        return None
    if wert < 0 or wert > grenze:
        return None
    if vergleich is not None and vergleich > 0 and wert > vergleich * 500:
        return None
    return wert


def _flag(rohwert) -> bool:
    return str(rohwert).strip() in ("1", "1.0", "true", "True")


def lies_alle_ausgaenge(all_values: dict) -> dict:
    """Wandelt die Rohantwort in benannte Werte um.

    Fehlende Felder ergeben None statt eines Fehlers: Je nach Konfiguration
    des Bausteins sind nicht alle Ausgaenge sichtbar."""
    # Der Gesamtzaehler ist die Obergrenze fuer jeden Zeitraum: In einem Monat
    # kann nicht mehr geflossen sein, als der Zaehler insgesamt zaehlt. Diese
    # Regel entlarvt fehlerhafte Zaehlwerke zuverlaessiger als jede feste
    # Grenze — im Testfall meldete der Baustein 19.472 kWh im Jahr bei einem
    # Gesamtstand von 452 kWh.
    gesamt = _zahl(all_values.get("Mr"))

    def zeitraum(feld, grenze, vergleich=None):
        wert = _plausibel(_zahl(all_values.get(feld)), grenze, vergleich)
        if wert is not None and gesamt is not None and wert > gesamt * 1.02:
            return None
        return wert

    return {
        # Zaehlerstand und Mengen
        "zaehlerstand_kwh": gesamt,
        "menge_aktuell_kwh": _zahl(all_values.get("Ccc")),
        "menge_letzte_kwh": _zahl(all_values.get("Clc")),
        "kosten_letzte_eur": _zahl(all_values.get("Cclc")),
        # Verbrauchszaehler des Bausteins — mit Plausibilitaetspruefung,
        # weil fehlerhafte Zaehlerstaende aus der Inbetriebnahme dauerhaft
        # in diesen Werten stehen bleiben.
        "verbrauch_heute_kwh": zeitraum("Cd", 500),
        "verbrauch_gestern_kwh": zeitraum("Cld", 500),
        "verbrauch_woche_kwh": zeitraum("Cw", 2000),
        "verbrauch_monat_kwh": zeitraum("Cm", MAX_MONAT_KWH, _zahl(all_values.get("Cw"))),
        "verbrauch_vormonat_kwh": zeitraum("Clm", MAX_MONAT_KWH),
        "verbrauch_jahr_kwh": zeitraum("Cy", MAX_JAHR_KWH),
        "verbrauch_vorjahr_kwh": zeitraum("Cly", MAX_JAHR_KWH),
        # Betriebszustand — Leistung nie negativ
        "leistung_kw": _zahl(all_values.get("Cp"), min_wert=0.0),
        "zielleistung_kw": _zahl(all_values.get("Tp"), min_wert=0.0),
        "verbunden": _flag(all_values.get("Vc")),
        "laedt": _flag(all_values.get("Cac")),
        "laden_erlaubt": _flag(all_values.get("Ca")),
        "lastabwurf": _flag(all_values.get("Ls")),
        "lademodus": _zahl(all_values.get("M")),
        # Zuordnung und Protokoll
        "benutzer_id": (all_values.get("Uid") or "").strip() or None,
        "session_gestartet": _flag(all_values.get("Ss")),
        "session_beendet": _flag(all_values.get("Se")),
        "letzte_ladung_log": all_values.get("Lcl") or "",
    }


def speichere_zaehlerwerte(wallbox_id: int, werte: dict) -> None:
    """Schreibt Zaehlerstand und Spitzenleistung in die Live-Kennzahlen.

    Der Zaehlerstand kommt in kWh und wird in Wattstunden abgelegt, weil die
    uebrige Anwendung damit rechnet."""
    stand = werte.get("zaehlerstand_kwh")
    if stand is None:
        return
    conn = db_service.get_connection()
    try:
        conn.execute(
            """INSERT INTO wallbox_live_metrics (wallbox_id, meter_total_wh, peak_power_kw, last_sync_at)
               VALUES (?, ?, ?, CURRENT_TIMESTAMP)
               ON CONFLICT(wallbox_id) DO UPDATE SET
                   meter_total_wh = excluded.meter_total_wh,
                   peak_power_kw = MAX(COALESCE(wallbox_live_metrics.peak_power_kw, 0),
                                       COALESCE(excluded.peak_power_kw, 0)),
                   last_sync_at = CURRENT_TIMESTAMP""",
            (wallbox_id, int(stand * 1000), werte.get("leistung_kw") or 0))
        conn.commit()
    finally:
        conn.close()
