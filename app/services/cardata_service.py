"""BMW CarData — Fahrtenerfassung über die REST-API.

Bewusst OHNE MQTT-Dauerverbindung: Ein permanenter Streaming-Prozess waere ein
dritter Hintergrunddienst neben Flask und dem OCPP-Server, mit eigener
Verbindungsueberwachung und Token-Rotation im laufenden Betrieb. Die REST-API
liefert dieselben Werte; bei einem Abruf alle 30 Minuten bleiben wir mit 48
Aufrufen unter dem Tageslimit von 50.

FAHRTERKENNUNG
--------------
CarData liefert keine fertige Fahrtenliste, sondern Momentaufnahmen. Aus zwei
aufeinanderfolgenden Abrufen laesst sich eine Fahrt aber zuverlaessig ableiten:

    Kilometerstand unveraendert  → Fahrzeug stand, nichts zu tun
    Kilometerstand gestiegen     → Fahrt fand statt
        Distanz  = Differenz der Kilometerstaende (exakt, vom Tacho)
        Start    = Position und Zeit des vorherigen Abrufs
        Ziel     = Position und Zeit des aktuellen Abrufs

Die Distanz ist damit exakt. Unschaerfer sind nur die Zeitstempel: Bei einem
30-Minuten-Takt kann der Startzeitpunkt bis zu eine halbe Stunde vor der
tatsaechlichen Abfahrt liegen. Fuer ein Fahrtenbuch ist das unkritisch, da
Datum und Kilometer zaehlen — der Nutzer kann die Zeiten beim Zuordnen
korrigieren.

Erkannte Fahrten landen als UNVERARBEITET in `bmw_trips` und werden dort per
1-Klick als dienstlich oder privat eingestuft.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta

from repositories import settings_repository, bmw_trip_repository
from services import cardata_auth_service as auth
from services import db_service
import services.event_log_service as event_log_service

API_BASIS = "https://api-cardata.bmwgroup.com"

# Datenpunkte, die fuer die Fahrterkennung noetig sind. Die Namen stammen aus
# dem Telematikdatenkatalog; sie muessen im Portal unter "Configure data
# stream" ausgewaehlt sein, sonst liefert BMW keine Werte.
# Abrufe pro Tag laut CarData-Bedingungen
TAGESLIMIT = 50

DESCRIPTOR_KM = "vehicle.vehicle.travelledDistance"
DESCRIPTOR_LAT = "vehicle.cabin.infotainment.navigation.currentLocation.latitude"
DESCRIPTOR_LON = "vehicle.cabin.infotainment.navigation.currentLocation.longitude"

# Fahrt-Ende-Datensatz: BMW meldet Kilometerstand und Zeitpunkt der zuletzt
# beendeten Fahrt. Genauer als der reine Momentanwert, weil der Zeitstempel
# vom Fahrzeug stammt und nicht vom Abrufzeitpunkt.
DESCRIPTOR_TRIP_KM = "vehicle.trip.segment.end.travelledDistance"
DESCRIPTOR_TRIP_ZEIT = "vehicle.trip.segment.end.time"
DESCRIPTOR_TRIP_SOC = "vehicle.trip.segment.end.drivetrain.batteryManagement.hvSoc"

# Fahrzeugdaten fuer die Stammdatenanzeige und den Konfigurator. Besonders
# wertvoll: der echte Durchschnittsverbrauch ersetzt den bisherigen Schaetzwert.
DESCRIPTOR_VERBRAUCH = "vehicle.drivetrain.avgElectricRangeConsumption"
DESCRIPTOR_AKKU_MAX = "vehicle.drivetrain.batteryManagement.batterySizeMax"
# Fallback: batterySizeMax liefert bei manchen Fahrzeugen/Konfigurationen
# zuverlaessig 0 statt eines echten Werts oder eines fehlenden Feldes (siehe
# BUG-Hinweis bei _kombiniere_akkukapazitaet). maxEnergy gilt in der
# Community als das verlaesslichere Aequivalent fuer dieselbe Kenngroesse.
DESCRIPTOR_AKKU_MAX_ALT = "vehicle.drivetrain.batteryManagement.maxEnergy"
DESCRIPTOR_AKKU_SOH = "vehicle.powertrain.electric.battery.stateOfHealth.displayed"
DESCRIPTOR_SOC = "vehicle.powertrain.electric.battery.stateOfCharge.displayed"
DESCRIPTOR_REICHWEITE = "vehicle.drivetrain.electricEngine.kombiRemainingElectricRange"
DESCRIPTOR_SERVICE = "vehicle.status.serviceDistance.next"
DESCRIPTOR_WOCHE = "vehicle.vehicle.averageWeeklyDistanceLongTerm"

# Wartungstermine ueber die Live-Schnittstelle. Bisher standen sie nur im
# Datenarchiv — auf das man nach der Anforderung Stunden warten muss.
# 'conditionBasedServices' liefert dieselben Angaben sofort: naechste
# Hauptuntersuchung, Service, Bremsfluessigkeit.
DESCRIPTOR_CBS = "vehicle.status.conditionBasedServices"
# Angesteckt/laedt — der einzige Live-Status, der wirklich zur Ladeabrechnung
# gehoert statt zur Fernbedienung (siehe Diskussion: Tueren/Fenster bewusst
# nicht erfasst, das deckt die BMW-App bereits ab).
#
# KORREKTUR (28.08.): Hier hatte ich zuvor faelschlich behauptet, der
# urspruengliche Deskriptor (anyPosition.isPlugged) sei erfunden und
# existiere nicht — falsch. Stephan hat per Bildschirmfoto aus dem echten
# Portal bestaetigt: er existiert, mit genau dieser deutschen Beschreibung,
# und war die ganze Zeit bereits aktiv. Mein Fehler war ein voreiliger
# Schluss aus einer PDF-Fundstelle und einem Community-Beispiel, nicht
# eine Ueberpruefung am echten Portal. Beide Deskriptoren sind real und
# jetzt aktiv — welcher tatsaechlich zuverlaessig sendet, ist offen, daher
# werden beide gelesen: das echte Bool zuerst (direkter, kein Text-Umweg),
# der Text-Status als Rueckfall.
DESCRIPTOR_ANGESTECKT = "vehicle.powertrain.tractionBattery.charging.port.anyPosition.isPlugged"
DESCRIPTOR_ANGESTECKT_ALT = "vehicle.body.chargingPort.status"

# Ladevorgang im Detail — "angesteckt" allein sagt nichts darueber, ob
# gerade tatsaechlich geladen wird oder die Sitzung pausiert/beendet ist.
DESCRIPTOR_LADESTATUS = "vehicle.drivetrain.electricEngine.charging.status"
DESCRIPTOR_RESTLADEDAUER = "vehicle.drivetrain.electricEngine.charging.timeToFullyCharged"
DESCRIPTOR_STECKERTYP = "vehicle.drivetrain.electricEngine.charging.method"

# Ladeklappe und allgemeine Fahrzeugverriegelung — beide ladebezogen
# (Kabel gesichert bzw. Fahrzeug zu), anders als Tueren/Fenster einzeln,
# die reine Fernbedienungs-Funktion waeren und bewusst nicht als
# Einzelwerte, sondern nur zusammengefasst als ein "offen/zu"-Signal
# gefuehrt werden (siehe DESCRIPTOR_TUEREN/_FENSTER unten).
DESCRIPTOR_LADEKLAPPE = "vehicle.body.flap.isLocked"
DESCRIPTOR_VERRIEGELUNG = "vehicle.cabin.door.lock.status"

# Tueren und Fenster einzeln abgefragt (BMW liefert keinen Sammelwert),
# aber im Frontend zu einem einzigen Chip zusammengefasst -- siehe
# _fahrzeugdaten_tueren_fenster_offen() weiter unten.
DESCRIPTOR_TUER_VL = "vehicle.cabin.door.row1.driver.isOpen"
DESCRIPTOR_TUER_VR = "vehicle.cabin.door.row1.passenger.isOpen"
DESCRIPTOR_TUER_HL = "vehicle.cabin.door.row2.driver.isOpen"
DESCRIPTOR_TUER_HR = "vehicle.cabin.door.row2.passenger.isOpen"
DESCRIPTOR_FENSTER_VL = "vehicle.cabin.window.row1.driver.status"
DESCRIPTOR_FENSTER_VR = "vehicle.cabin.window.row1.passenger.status"
DESCRIPTOR_FENSTER_HL = "vehicle.cabin.window.row2.driver.status"
DESCRIPTOR_FENSTER_HR = "vehicle.cabin.window.row2.passenger.status"

CONTAINER_DESCRIPTORS = [
    # Fahrterfassung
    DESCRIPTOR_KM, DESCRIPTOR_LAT, DESCRIPTOR_LON,
    DESCRIPTOR_TRIP_KM, DESCRIPTOR_TRIP_ZEIT, DESCRIPTOR_TRIP_SOC,
    # Fahrzeugdaten
    DESCRIPTOR_VERBRAUCH, DESCRIPTOR_AKKU_MAX, DESCRIPTOR_AKKU_MAX_ALT, DESCRIPTOR_AKKU_SOH,
    DESCRIPTOR_SOC, DESCRIPTOR_REICHWEITE, DESCRIPTOR_SERVICE, DESCRIPTOR_WOCHE,
    DESCRIPTOR_ANGESTECKT, DESCRIPTOR_ANGESTECKT_ALT,
    # Ladevorgang im Detail
    DESCRIPTOR_LADESTATUS, DESCRIPTOR_RESTLADEDAUER, DESCRIPTOR_STECKERTYP,
    # Verriegelung
    DESCRIPTOR_LADEKLAPPE, DESCRIPTOR_VERRIEGELUNG,
    DESCRIPTOR_TUER_VL, DESCRIPTOR_TUER_VR, DESCRIPTOR_TUER_HL, DESCRIPTOR_TUER_HR,
    DESCRIPTOR_FENSTER_VL, DESCRIPTOR_FENSTER_VR, DESCRIPTOR_FENSTER_HL, DESCRIPTOR_FENSTER_HR,
    # Wartung
    DESCRIPTOR_CBS,
]

# Unterhalb dieser Distanz gilt eine Aenderung als Messrauschen bzw.
# Rangieren und erzeugt keine Fahrt.
MIN_DISTANZ_KM = 0.5

# Ab dieser Spitzenleistung gilt ein Ladevorgang als Gleichstrom-Schnellladung.
# Eine dreiphasige Heim-Wallbox erreicht maximal 22 kW, in der Praxis meist 11 kW;
# Schnelllader beginnen bei 50 kW. Der Zwischenraum ist bewusst grosszuegig.
DC_SCHWELLE_KW = 25.0


# ── HTTP ───────────────────────────────────────────────────────────────────

def _request(methode: str, pfad: str, token: str, vehicle_id: int,
             body: dict | None = None, params: dict | None = None) -> dict:
    """Aufruf gegen die CarData-API. Wirft nie; Fehler kommen als Dict zurueck."""
    url = f"{API_BASIS}{pfad}"
    if params:
        url += "?" + urllib.parse.urlencode(params)
    daten = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=daten, method=methode, headers={
        "User-Agent": "eCharge-at-Home/1.0 (+https://www.loewemann.com)",
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
        "Content-Type": "application/json",
        # Von BMW verlangt, kennzeichnet die genutzte API-Version
        "x-version": "v1",
    })
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            roh = resp.read().decode("utf-8")
            # Kontingent aus den Kopfzeilen lesen, falls BMW eines meldet.
            # Die Namen sind nicht dokumentiert — deshalb mehrere Varianten.
            # Fehlen sie, greift der eigene Zaehler.
            _merke_kontingent(vehicle_id, resp.headers)
            _zaehle_abruf(vehicle_id)
            return {"ok": True, "daten": json.loads(roh) if roh else {}}
    except urllib.error.HTTPError as e:
        _zaehle_abruf(vehicle_id)          # auch abgelehnte Aufrufe zaehlen beim Limit
        try:
            _merke_kontingent(vehicle_id, e.headers)
        except Exception:
            pass
        try:
            fehler = json.loads(e.read().decode("utf-8"))
        except Exception:
            fehler = {}
        # Antwort im Original festhalten: Ohne sie bleibt bei einem 403 voellig
        # offen, ob Berechtigung, Fahrzeug oder Kontingent das Problem ist.
        try:
            event_log_service.log_event("bmw", "warning",
                f"CarData HTTP {e.code} bei {pfad}: "
                + json.dumps(fehler, ensure_ascii=False)[:400])
        except Exception:
            pass
        return {"ok": False, "status": e.code, "fehler": fehler,
                "meldung": _fehlertext(e.code, fehler)}
    except Exception as e:
        return {"ok": False, "status": None, "fehler": {},
                "meldung": f"CarData nicht erreichbar ({type(e).__name__})."}


def _merke_kontingent(vehicle_id: int, headers) -> None:
    """Liest das verbleibende Kontingent aus den Antwort-Kopfzeilen — je
    Fahrzeug, da unterschiedliche Fahrzeuge unterschiedliche BMW-Konten und
    damit getrennte Tageskontingente haben koennen.

    BMW dokumentiert nicht, ob und unter welchem Namen ein Rest gemeldet
    wird. Deshalb werden die ueblichen Schreibweisen geprueft. Findet sich
    nichts, bleibt es beim eigenen Zaehler — der ist ohnehin die
    verlaesslichere Grundlage, weil er auch Aufrufe mitzaehlt, die gar
    nicht erst beim Server ankamen.
    """
    if not headers:
        return
    kandidaten = ("x-ratelimit-remaining", "ratelimit-remaining",
                  "x-rate-limit-remaining", "x-quota-remaining")
    for name in kandidaten:
        wert = headers.get(name)
        if wert is None:
            continue
        try:
            settings_repository.set_setting(f"cardata_rest_gemeldet_{vehicle_id}", str(int(wert)))
            settings_repository.set_setting(
                f"cardata_rest_gemeldet_am_{vehicle_id}",
                datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
            return
        except (TypeError, ValueError):
            continue


def _zaehle_abruf(vehicle_id: int) -> None:
    """Zaehlt einen verbrauchten Abruf dieses Fahrzeugs fuer den laufenden Tag.

    BMW setzt das Kontingent zur Tagesmitte zurueck (UTC). Hier wird nach
    lokalem Datum gezaehlt — das weicht hoechstens um wenige Stunden ab und
    ist fuer eine Anzeige genau genug.
    """
    heute = datetime.now().strftime("%Y-%m-%d")
    try:
        tag = settings_repository.get_setting(f"cardata_zaehler_tag_{vehicle_id}") or ""
        stand = int(settings_repository.get_setting(f"cardata_zaehler_{vehicle_id}") or 0)
    except (TypeError, ValueError):
        tag, stand = "", 0
    if tag != heute:
        tag, stand = heute, 0
    settings_repository.set_setting(f"cardata_zaehler_tag_{vehicle_id}", tag)
    settings_repository.set_setting(f"cardata_zaehler_{vehicle_id}", str(stand + 1))


def kontingent(vehicle_id: int) -> dict:
    """Verbrauch und Rest dieses Fahrzeugs fuer die Anzeige."""
    heute = datetime.now().strftime("%Y-%m-%d")
    try:
        tag = settings_repository.get_setting(f"cardata_zaehler_tag_{vehicle_id}") or ""
        verbraucht = int(settings_repository.get_setting(f"cardata_zaehler_{vehicle_id}") or 0)
    except (TypeError, ValueError):
        tag, verbraucht = "", 0
    if tag != heute:
        verbraucht = 0

    gemeldet = None
    try:
        roh = settings_repository.get_setting(f"cardata_rest_gemeldet_{vehicle_id}")
        am = settings_repository.get_setting(f"cardata_rest_gemeldet_am_{vehicle_id}") or ""
        # Nur verwenden, wenn die Meldung von heute stammt
        if roh not in (None, "") and am[:10] == heute:
            gemeldet = int(roh)
    except (TypeError, ValueError):
        pass

    return {
        "limit": TAGESLIMIT,
        "verbraucht": verbraucht,
        "rest": gemeldet if gemeldet is not None else max(0, TAGESLIMIT - verbraucht),
        "von_bmw_gemeldet": gemeldet is not None,
    }


def _fehlertext(status: int, fehler: dict) -> str:
    """Uebersetzt die CarData-Fehlercodes (CU-xxx) in Klartext.

    Laut Spezifikation heisst das Feld 'exveErrorId'; frueher wurde hier am
    falschen Namen gesucht, sodass nur ein nichtssagendes 'HTTP 403' uebrig
    blieb. Die Begleitfelder werden mitgegeben — sie enthalten oft den
    entscheidenden Hinweis."""
    code = (fehler.get("exveErrorId") or fehler.get("errorId")
            or fehler.get("error") or "").upper()
    zusatz = " ".join(str(fehler.get(k, "")) for k in
                      ("exveErrorMsg", "exveNote", "error_description")).strip()
    texte = {
        "CU-102": "Zugang abgelaufen — wird beim nächsten Versuch erneuert.",
        "CU-103": "Der Zugang ist nicht für CarData freigeschaltet.",
        "CU-104": "Keine Berechtigung für diese Fahrgestellnummer. Bist du "
                  "Hauptnutzer des Fahrzeugs im BMW-Portal?",
        "CU-105": "Container nicht gefunden — bitte neu anlegen.",
        "CU-120": "Die Fahrgestellnummer hat ein falsches Format (17 Zeichen, Großbuchstaben).",
        "CU-124": "Maximale Anzahl Container erreicht (10). Bitte im Portal aufräumen.",
        "CU-402": "Ein Datenpunkt ist unbekannt — im Portal unter "
                  "'Configure data stream' prüfen.",
        "CU-429": "Tageslimit von 50 Abrufen erreicht. Morgen geht es weiter.",
    }
    if code in texte:
        return f"{texte[code]} [{code}]" + (f" — {zusatz}" if zusatz else "")
    if code:
        return f"CarData meldet {code}" + (f": {zusatz}" if zusatz else "")
    if status == 429:
        return "Tageslimit von 50 Abrufen erreicht."
    if status == 401:
        return "Nicht angemeldet oder Zugang abgelaufen."
    if status and status >= 500:
        return "BMW-Server antwortet derzeit nicht."
    return (f"Unerwartete Antwort von CarData (HTTP {status})."
            + (f" {zusatz}" if zusatz else ""))


# ── Fahrzeuge ──────────────────────────────────────────────────────────────

def hole_fahrzeuge(vehicle_id: int) -> dict:
    """Fahrzeuge des BMW-Kontos, das an dieses App-Fahrzeug angemeldet ist.

    Nur als Hauptnutzer zugeordnete liefern Daten. Wird beim Anlegen eines
    Fahrzeugs 'aus BMW-Konto importieren' genutzt, um die VIN auszuwaehlen."""
    token = auth.hole_access_token(vehicle_id)
    if not token:
        return {"ok": False, "meldung": "Nicht angemeldet."}
    # BMW hat den Endpunkt im Lauf der Zeit umbenannt. Die Reihenfolge geht
    # von der aktuellen Fassung zu aelteren — der erste Treffer gewinnt.
    versuche = [
        "/customers/vehicles/mappings",
        "/customers/vehicles",
        "/customer/vehicles/mappings",
    ]
    letzte_meldung = ""
    for pfad in versuche:
        antwort = _request("GET", pfad, token, vehicle_id)
        if antwort["ok"]:
            roh = antwort["daten"]
            liste = (roh.get("mappings") or roh.get("vehicles")
                     or (roh if isinstance(roh, list) else []))
            fahrzeuge = [{"vin": f.get("vin"), "typ": f.get("mappingType", "")}
                         for f in liste if isinstance(f, dict) and f.get("vin")]
            if fahrzeuge:
                return {"ok": True, "fahrzeuge": fahrzeuge}
            letzte_meldung = ("Das Konto liefert keine Fahrzeuge. Ist der Wagen "
                              "als Hauptnutzer zugeordnet?")
        else:
            letzte_meldung = antwort["meldung"]

    return {"ok": False, "meldung": letzte_meldung or "Abruf fehlgeschlagen."}


# ── Container ──────────────────────────────────────────────────────────────

def stelle_container_sicher(vehicle_id: int) -> dict:
    """Legt den Container mit den Fahrt-Datenpunkten fuer dieses Fahrzeug an,
    falls noetig.

    Die Container-ID wird gespeichert; ein erneuter Aufruf verbraucht dann
    kein weiteres Kontingent.

    BUG BEHOBEN (28.08.): Ein bestehender Container wurde bisher immer
    unveraendert weiterverwendet, auch wenn CONTAINER_DESCRIPTORS sich seit
    seiner Anlage geaendert hatte. Neu hinzugekommene Deskriptoren (etwa
    'maxEnergy' oder 'isPlugged') wurden dadurch von BMW nie ausgeliefert —
    nicht ueber den Stream (der haengt am Portal, nicht am Container) und
    nicht ueber den manuellen Abruf, weil der Container schlicht nicht
    danach gefragt hat. Jetzt wird bei jedem Aufruf verglichen, ob sich die
    gewuenschten Deskriptoren geaendert haben; falls ja, wird der alte
    Container (best effort) geloescht und ein neuer mit der aktuellen
    Liste angelegt."""
    from repositories import vehicle_bmw_repository as bmw_repo
    aktuelle_deskriptoren = json.dumps(sorted(CONTAINER_DESCRIPTORS))
    daten = bmw_repo.get(vehicle_id)
    vorhandene = daten["container_id"]
    gespeicherte_deskriptoren = daten.get("container_deskriptoren") or ""

    if vorhandene and gespeicherte_deskriptoren == aktuelle_deskriptoren:
        return {"ok": True, "container_id": vorhandene, "neu": False}

    token = auth.hole_access_token(vehicle_id)
    if not token:
        return {"ok": False, "meldung": "Nicht angemeldet."}

    if vorhandene:
        # Best effort: alten Container aufraeumen, damit die pro Konto
        # begrenzte Anzahl (max. 10) nicht durch veraltete Container
        # aufgebraucht wird. Schlaegt das fehl, wird trotzdem ein neuer
        # angelegt — ein verwaister alter Container ist unschoen, aber
        # kein Grund, den Fortschritt zu blockieren.
        try:
            _request("DELETE", f"/customers/containers/{vorhandene}", token, vehicle_id)
        except Exception:
            pass

    antwort = _request("POST", "/customers/containers", token, vehicle_id, body={
        "name": "eChargeHome Fahrtenbuch",
        "purpose": "Fahrtenerfassung für die steuerliche Abrechnung",
        "technicalDescriptors": CONTAINER_DESCRIPTORS,
    })
    if not antwort["ok"]:
        return {"ok": False, "meldung": antwort["meldung"]}

    cid = (antwort["daten"].get("containerId")
           or antwort["daten"].get("id") or "")
    if not cid:
        return {"ok": False, "meldung": "BMW lieferte keine Container-ID zurück."}
    bmw_repo.set_felder(vehicle_id, container_id=cid, container_deskriptoren=aktuelle_deskriptoren)
    event_log_service.log_event("bmw", "info", f"CarData-Container angelegt: {cid}")
    return {"ok": True, "container_id": cid, "neu": True}


# ── Datenabruf & Fahrterkennung ────────────────────────────────────────────

def _zahl(wert) -> float | None:
    try:
        return float(wert)
    except (TypeError, ValueError):
        return None


def _bool(wert) -> bool | None:
    """Wandelt BMWs Wahrheitswerte in ein Python-bool um. None, wenn der
    Wert fehlt.

    Deckt drei Formen ab: echte true/false-Werte (die meisten Deskriptoren),
    Text-Aufzaehlungen wie 'CONNECTED'/'DISCONNECTED' (isPlugConnected) UND
    praefigierte Varianten wie 'FLAP_LOCKED'/'FLAP_UNLOCKED' (Ladeklappe) —
    deshalb Endungspruefung statt exaktem Abgleich. 'UNLOCKED' zuerst
    pruefen, sonst wuerde das enthaltene 'LOCKED' faelschlich True liefern.
    """
    if wert is None:
        return None
    if isinstance(wert, bool):
        return wert
    text = str(wert).strip().upper()
    if text.endswith("UNLOCKED") or text in ("DISCONNECTED", "FALSE", "0", "OFF"):
        return False
    if text.endswith("LOCKED") or text in ("CONNECTED", "TRUE", "1", "ON", "SECURED"):
        return True
    return None


def _kombiniere_akkukapazitaet(feld) -> float | None:
    """Akkukapazitaet aus zwei moeglichen Deskriptoren.

    BUG BEHOBEN (28.08.): batterySizeMax lieferte bei manchen Fahrzeugen
    zuverlaessig 0 statt eines echten Werts — eine Hochvoltbatterie mit
    0 kWh gibt es nicht, das ist immer ein fehlender/nicht unterstuetzter
    Wert, keine echte Angabe. maxEnergy gilt als das robustere Aequivalent
    (siehe DESCRIPTOR_AKKU_MAX_ALT-Kommentar) und wird als Rueckfalloption
    genutzt, wenn die erste Quelle leer oder 0 ist."""
    primaer = _zahl(feld(DESCRIPTOR_AKKU_MAX)[0])
    if primaer:
        return primaer
    return _zahl(feld(DESCRIPTOR_AKKU_MAX_ALT)[0])


def _fenster_offen(wert) -> bool | None:
    """Fenster liefern einen Zustandstext (CLOSED/INTERMEDIATE/OPEN/INVALID),
    kein reines Bool. INTERMEDIATE (einen Spalt offen) zaehlt als "offen" —
    fuer die Zusammenfassung "alles zu" reicht ein Spalt, um das Bild zu
    kippen."""
    if wert is None:
        return None
    text = str(wert).strip().upper()
    if text == "CLOSED":
        return False
    if text in ("OPEN", "INTERMEDIATE"):
        return True
    return None


# Menschenlesbare Bezeichnung samt MDI-Icon je Lademethode. Nur die
# AC-Werte sind aus dem Katalog bestaetigt (siehe DESCRIPTOR_STECKERTYP-
# Diskussion); die DC-Varianten sind die plausibelsten Bezeichner nach
# demselben Namensmuster, aber NICHT am echten HPC-Ladevorgang
# gegengeprueft — muss beim ersten echten Schnellladevorgang verifiziert
# werden (siehe Protokoll-Auswertung dafuer nutzen).
STECKERTYP_ANZEIGE = {
    "AC_TYPE1PLUG": ("Typ 1 · AC", "mdi-ev-plug-type1", False),
    "AC_TYPE2PLUG": ("Typ 2 · AC", "mdi-ev-plug-type2", False),
    "DC_CCS1PLUG": ("CCS1 · HPC", "mdi-ev-plug-ccs1", True),
    "DC_CCS2PLUG": ("CCS2 · HPC", "mdi-ev-plug-ccs2", True),
    "DC_COMBO1": ("CCS1 · HPC", "mdi-ev-plug-ccs1", True),
    "DC_COMBO2": ("CCS2 · HPC", "mdi-ev-plug-ccs2", True),
}


def _steckertyp_anzeige(wert) -> dict | None:
    """Wandelt den rohen Lademethoden-Code in eine anzeigefertige Form um.
    None, wenn kein Wert vorliegt oder der Code unbekannt ist (dann zeigt
    das Frontend einfach keinen Steckertyp-Chip, statt einen falschen zu
    erfinden)."""
    if not wert:
        return None
    eintrag = STECKERTYP_ANZEIGE.get(str(wert).strip().upper())
    if not eintrag:
        return None
    text, icon, ist_dc = eintrag
    return {"text": text, "icon": icon, "dc": ist_dc}


# Ladestatus-Rohwert -> anzeigefertiger Text. NOCHARGING bewusst als "None"
# behandelt (kein Chip), da es der Normalzustand ist und keinen eigenen
# Hinweis verdient — nur ein tatsaechlich aktiver/gestoerter Zustand ist
# meldenswert.
LADESTATUS_ANZEIGE = {
    "CHARGINGACTIVE": "Lädt aktiv",
    "INITIALIZATION": "Ladevorgang startet",
    "CHARGINGPAUSED": "Ladepause",
    "CHARGINGENDED": "Ladevorgang beendet",
    "CHARGINGERROR": "Ladefehler",
}


def lese_telematik(vehicle_id: int, vin: str) -> dict:
    """Aktuelle Werte des Containers dieses Fahrzeugs (1 Aufruf vom Tageslimit)."""
    token = auth.hole_access_token(vehicle_id)
    if not token:
        return {"ok": False, "meldung": "Nicht angemeldet."}
    cont = stelle_container_sicher(vehicle_id)
    if not cont["ok"]:
        return cont

    antwort = _request("GET", f"/customers/vehicles/{vin}/telematicData", token, vehicle_id,
                       params={"containerId": cont["container_id"]})
    if not antwort["ok"]:
        return {"ok": False, "meldung": antwort["meldung"]}

    roh = antwort["daten"].get("telematicData", antwort["daten"])
    # Nachvollziehbar machen, welche Datenpunkte das Fahrzeug tatsaechlich
    # liefert — je nach Modell und Ausstattung fehlen einzelne Werte.
    try:
        geliefert = [k for k, v in roh.items()
                     if isinstance(v, dict) and v.get("value") not in (None, "")]
        fehlend = [d for d in CONTAINER_DESCRIPTORS if d not in geliefert]
        event_log_service.log_event("bmw", "info",
            f"Telematik-Abruf: {len(geliefert)} von {len(CONTAINER_DESCRIPTORS)} "
            f"Datenpunkten geliefert"
            + (f" — ohne Wert: {', '.join(d.split('.')[-1] for d in fehlend[:5])}"
               if fehlend else ""))
    except Exception:
        pass

    def feld(name):
        eintrag = roh.get(name) or {}
        return eintrag.get("value"), eintrag.get("timestamp")

    km_wert, km_zeit = feld(DESCRIPTOR_KM)
    lat_wert, _ = feld(DESCRIPTOR_LAT)
    lon_wert, _ = feld(DESCRIPTOR_LON)
    trip_km, trip_km_zeit = feld(DESCRIPTOR_TRIP_KM)
    trip_zeit_wert, _ = feld(DESCRIPTOR_TRIP_ZEIT)
    trip_soc, _ = feld(DESCRIPTOR_TRIP_SOC)

    # "" statt None fuer "nicht ladend" (NOCHARGING/unbekannt) — die
    # Merge-Regel in _speichere_fahrzeugdaten() ueberschreibt nur mit
    # Werten, die "is not None" sind. Mit None wuerde "Ladevorgang
    # beendet" nie ankommen, ein veralteter "Laedt aktiv"-Chip bliebe
    # stehen. "" ist ein gueltiger, expliziter Wert und loescht ihn korrekt.
    ladestatus_roh = feld(DESCRIPTOR_LADESTATUS)[0]
    if ladestatus_roh is None:
        laedt_aktiv_text = None
    else:
        laedt_aktiv_text = LADESTATUS_ANZEIGE.get(str(ladestatus_roh).strip().upper()) or ""

    return {
        "ok": True,
        "km": _zahl(km_wert),
        "lat": _zahl(lat_wert),
        "lon": _zahl(lon_wert),
        "zeitpunkt": km_zeit,
        # Fahrt-Ende laut Fahrzeug — wird bevorzugt, wenn vorhanden
        "trip_km": _zahl(trip_km),
        "trip_zeit": trip_zeit_wert or trip_km_zeit,
        "trip_soc": _zahl(trip_soc),
        # Fahrzeugdaten
        "verbrauch_kwh_100": _zahl(feld(DESCRIPTOR_VERBRAUCH)[0]),
        "akku_max_kwh": _kombiniere_akkukapazitaet(feld),
        "akku_soh_prozent": _zahl(feld(DESCRIPTOR_AKKU_SOH)[0]),
        "soc_prozent": _zahl(feld(DESCRIPTOR_SOC)[0]),
        "reichweite_km": _zahl(feld(DESCRIPTOR_REICHWEITE)[0]),
        "service_in_km": _zahl(feld(DESCRIPTOR_SERVICE)[0]),
        "woche_km": _zahl(feld(DESCRIPTOR_WOCHE)[0]),
        "angesteckt": _bool(feld(DESCRIPTOR_ANGESTECKT)[0])
                      if _bool(feld(DESCRIPTOR_ANGESTECKT)[0]) is not None
                      else _bool(feld(DESCRIPTOR_ANGESTECKT_ALT)[0]),
        # Ladevorgang im Detail
        "laedt_aktiv_text": laedt_aktiv_text,
        "restladedauer_min": _zahl(feld(DESCRIPTOR_RESTLADEDAUER)[0]),
        "steckertyp": _steckertyp_anzeige(feld(DESCRIPTOR_STECKERTYP)[0]),
        # Verriegelung
        "ladeklappe_zu": _bool(feld(DESCRIPTOR_LADEKLAPPE)[0]),
        "verriegelt": _bool(feld(DESCRIPTOR_VERRIEGELUNG)[0]),
        # Acht Einzelwerte statt vorberechneter Zusammenfassung -- die
        # Zusammenfassung entsteht erst beim Lesen in fahrzeugdaten(), aus
        # dem GESAMTEN gespeicherten Stand (siehe dortiger Kommentar).
        "tuer_vl": _bool(feld(DESCRIPTOR_TUER_VL)[0]),
        "tuer_vr": _bool(feld(DESCRIPTOR_TUER_VR)[0]),
        "tuer_hl": _bool(feld(DESCRIPTOR_TUER_HL)[0]),
        "tuer_hr": _bool(feld(DESCRIPTOR_TUER_HR)[0]),
        "fenster_vl": _fenster_offen(feld(DESCRIPTOR_FENSTER_VL)[0]),
        "fenster_vr": _fenster_offen(feld(DESCRIPTOR_FENSTER_VR)[0]),
        "fenster_hl": _fenster_offen(feld(DESCRIPTOR_FENSTER_HL)[0]),
        "fenster_hr": _fenster_offen(feld(DESCRIPTOR_FENSTER_HR)[0]),
        # Wartungstermine live statt aus dem Archiv
        "wartung": _lies_wartungstermine(feld(DESCRIPTOR_CBS)[0]),
        "roh": roh,
    }


def _lies_wartungstermine(wert) -> dict:
    """Wandelt 'conditionBasedServices' in Termine um.

    BMW liefert eine Liste je Wartungsposition mit Typ, Faelligkeitsdatum
    und Restkilometern. Die Bezeichnungen weichen je nach Sprache ab,
    deshalb wird auf Schluesselwoerter geprueft statt auf exakte Namen.
    """
    if not wert:
        return {}
    posten = wert
    if isinstance(posten, str):
        try:
            posten = json.loads(posten)
        except Exception:
            return {}
    if isinstance(posten, dict):
        posten = posten.get("items") or posten.get("services") or []
    if not isinstance(posten, list):
        return {}

    ergebnis: dict = {}
    for p in posten:
        if not isinstance(p, dict):
            continue
        typ = str(p.get("type") or p.get("name") or "").lower()
        datum = (p.get("dateTime") or p.get("date") or "")[:10]
        rest_km = p.get("distance") or p.get("remainingDistance")
        if not datum and not rest_km:
            continue
        if "vehicle_check" in typ or "untersuchung" in typ or "inspect" in typ:
            ergebnis["hu_faellig"] = datum
        elif "brake_fluid" in typ or "brems" in typ:
            ergebnis["bremsfluessigkeit"] = datum
        elif "oil" in typ or "service" in typ or "check" in typ:
            ergebnis["service_faellig"] = datum
            if rest_km:
                ergebnis["service_in_km"] = rest_km
    return ergebnis


def _koordinaten_text(lat, lon) -> str:
    """Koordinaten in eine lesbare Adresse aufloesen.

    BMW liefert nur Laengen- und Breitengrad. Frueher stand deshalb
    '50.57912, 7.22698' im Fahrtenbuch — auf einem Beleg unbrauchbar, weil
    daran niemand den Zweck der Fahrt erkennt.

    Schlaegt die Aufloesung fehl, bleibt die Koordinate stehen: eine Fahrt
    mit unschoener Ortsangabe ist besser als gar keine.
    """
    if lat is None or lon is None:
        return ""
    try:
        from services import geocoding_service
        return geocoding_service.adresse_aus_koordinaten(lat, lon)
    except Exception:
        return f"{lat:.5f}, {lon:.5f}"


def status(vehicle_id: int) -> dict:
    """Zustand der Anbindung dieses Fahrzeugs fuer die Oberflaeche."""
    from repositories import vehicle_bmw_repository as bmw_repo
    from repositories import vehicle_repository
    a = auth.status(vehicle_id)
    a["container_id"] = bmw_repo.get(vehicle_id)["container_id"]
    fahrzeug = vehicle_repository.get_vehicle(vehicle_id) or {}
    a["vin"] = fahrzeug.get("vin") or ""
    a["descriptors"] = CONTAINER_DESCRIPTORS
    return a


# ── Fahrzeugdaten (Stammdaten aus dem Fahrzeug) ────────────────────────────

FAHRZEUGDATEN_FELDER = (
    "verbrauch_kwh_100", "akku_max_kwh", "akku_soh_prozent", "soc_prozent",
    "reichweite_km", "service_in_km", "woche_km", "km", "angesteckt",
    # Ladevorgang im Detail
    "laedt_aktiv_text", "restladedauer_min", "steckertyp",
    # Verriegelung -- acht Einzelwerte statt vorberechneter Zusammenfassung,
    # siehe Kommentar bei _tueren_fenster_offen()/fahrzeugdaten().
    "ladeklappe_zu", "verriegelt",
    "tuer_vl", "tuer_vr", "tuer_hl", "tuer_hr",
    "fenster_vl", "fenster_vr", "fenster_hl", "fenster_hr",
)


def _fahrzeug_pflegen(vehicle_id: int, vin: str, daten: dict) -> None:
    """Frischt die Stammdaten dieses Fahrzeugs mit den Live-Werten auf
    (Kilometerstand, Wartungstermine) — fallen beim Telematik-Abruf ohnehin
    an, ein Datenarchiv-Import ist dafuer nicht mehr noetig."""
    from repositories import vehicle_repository

    wartung = daten.get("wartung") or {}
    werte = {
        "vin": vin,
        "km_stand": int(daten["km"]) if daten.get("km") else None,
        "km_stand_datum": (daten.get("zeitpunkt") or "")[:10] or None,
        "hu_faellig": wartung.get("hu_faellig"),
        "service_faellig": wartung.get("service_faellig"),
        "bremsfluessigkeit": wartung.get("bremsfluessigkeit"),
    }
    werte = {k: v for k, v in werte.items() if v is not None}
    if werte:
        vehicle_repository.setze_stammdaten(vehicle_id, werte)


def _speichere_fahrzeugdaten(vehicle_id: int, daten: dict) -> None:
    """Legt die zuletzt gemeldeten Fahrzeugwerte dieses Fahrzeugs ab —
    Grundlage fuer die Status-Kachel im Dashboard.

    Sie fallen beim Telematik-Abruf ohnehin an; getrennt abzurufen wuerde
    nur unnoetig Kontingent verbrauchen.

    BUG BEHOBEN (28.08.): Diese Funktion hat den kompletten Datensatz bei
    jedem Abruf ERSETZT statt zu ERGAENZEN. Liefert BMW bei einem Abruf nur
    einen Teil der Felder (durchaus ueblich — nicht jeder Wert kommt bei
    jeder Anfrage mit), gingen zuvor bekannte, weiterhin gueltige Werte
    (z. B. Ladestand, Reichweite, Akkukapazitaet) ersatzlos verloren, auch
    wenn sich am Fahrzeug nichts geaendert hatte. Jetzt wird auf den
    bestehenden Datensatz aufgesetzt: nur tatsaechlich gelieferte Felder
    werden aktualisiert, der Rest bleibt erhalten."""
    from repositories import vehicle_bmw_repository as bmw_repo
    neue_werte = {f: daten.get(f) for f in FAHRZEUGDATEN_FELDER if daten.get(f) is not None}
    if daten.get("lat") is not None:
        neue_werte["lat"] = daten["lat"]
    if daten.get("lon") is not None:
        neue_werte["lon"] = daten["lon"]
    if not neue_werte:
        return

    bestehend = fahrzeugdaten(vehicle_id)
    bestehend.update(neue_werte)
    bestehend["stand"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # Adresse einmal hier aufloesen statt bei jedem Dashboard-Aufruf neu —
    # sonst muesste die Status-Kachel bei jeder Anzeige einen externen
    # Geocoding-Abruf ausloesen. adresse_aus_koordinaten() ist ohnehin auf
    # gerundete Koordinaten gecacht, ein Doppel-Abruf fuer denselben Standort
    # kostet also nichts Zusaetzliches.
    if bestehend.get("lat") is not None and bestehend.get("lon") is not None:
        try:
            from services import geocoding_service
            bestehend["standort_adresse"] = geocoding_service.adresse_aus_koordinaten(
                bestehend["lat"], bestehend["lon"])
        except Exception:
            pass  # Adresse ist Beiwerk — Koordinaten allein reichen als Rueckfall

    bmw_repo.set_felder(vehicle_id, fzg_daten=json.dumps(bestehend, ensure_ascii=False))


def _tueren_fenster_offen(d: dict) -> tuple[bool | None, int]:
    """Fasst die acht einzeln gespeicherten Tuer-/Fenster-Zustaende (siehe
    _speichere_fahrzeugdaten) zu einem "offen/zu"-Signal zusammen. Wird
    beim LESEN berechnet, nicht beim Speichern — die acht Werte treffen
    ueber mehrere Nachrichten verteilt ein, eine einzelne Nachricht kennt
    daher nie alle acht auf einmal. Der gespeicherte Gesamtstand (nach dem
    Merge in _speichere_fahrzeugdaten) enthaelt dagegen den jeweils
    letzten bekannten Wert jeder einzelnen Tuer/jedes Fensters."""
    schluessel = ("tuer_vl", "tuer_vr", "tuer_hl", "tuer_hr",
                  "fenster_vl", "fenster_vr", "fenster_hl", "fenster_hr")
    bekannte = [d[s] for s in schluessel if d.get(s) is not None]
    if not bekannte:
        return None, 0
    anzahl_offen = sum(1 for b in bekannte if b)
    return anzahl_offen > 0, anzahl_offen


def fahrzeugdaten(vehicle_id: int) -> dict:
    """Zuletzt bekannte Fahrzeugdaten dieses Fahrzeugs fuer die Anzeige."""
    from repositories import vehicle_bmw_repository as bmw_repo
    roh = bmw_repo.get(vehicle_id)["fzg_daten"]
    try:
        d = json.loads(roh) if roh else {}
    except Exception:
        return {}
    offen, anzahl = _tueren_fenster_offen(d)
    d["tueren_fenster_offen"] = offen
    d["tueren_fenster_anzahl_offen"] = anzahl
    return d


def aktualisiere_fahrzeugdaten(vehicle_id: int) -> dict:
    """Ruft die aktuellen Werte ab und legt sie fuer die Status-Kachel ab —
    ein einzelner Aufruf vom Tageskontingent, unabhaengig vom Stream.

    Frischt nebenbei auch Kilometerstand und Wartungstermine des
    Fahrzeug-Datensatzes auf."""
    from repositories import vehicle_repository
    fahrzeug = vehicle_repository.get_vehicle(vehicle_id) or {}
    vin = (fahrzeug.get("vin") or "").strip()
    if not vin:
        return {"ok": False, "meldung": "Keine Fahrgestellnummer hinterlegt."}

    jetzt = lese_telematik(vehicle_id, vin)
    if not jetzt["ok"]:
        return jetzt

    _speichere_fahrzeugdaten(vehicle_id, jetzt)
    try:
        _fahrzeug_pflegen(vehicle_id, vin, jetzt)
    except Exception:
        pass   # Stammdaten sind Beiwerk, der Abruf gilt trotzdem als erfolgreich
    return {"ok": True, **fahrzeugdaten(vehicle_id)}


# ── Ladehistorie über die API ──────────────────────────────────────────────
# Eigener Endpunkt laut CarData-Dokumentation:
#   GET /customer/vehicles/{vin}/chargingHistory
#     → vehicle.powertrain.electric.battery.charging.history.sessionsList
# Liefert dieselben Daten wie das Datenarchiv, aber tagesaktuell — inklusive
# Ladeort mit Adresse. Das ist steuerlich entscheidend: Nur zuhause geladener
# Strom faellt unter den steuerfreien Auslagenersatz (§ 3 Nr. 50 EStG).

def lese_ladehistorie(vehicle_id: int, vin: str, tage: int = 30) -> dict:
    """Ladevorgaenge eines Zeitraums (1 Abruf vom Tageslimit).

    Umgesetzt nach der offiziellen Spezifikation (swagger-customer-api-v1):
      GET /customers/vehicles/{vin}/chargingHistory?from=…&to=…
    'from' und 'to' sind PFLICHT — fehlen sie, antwortet BMW mit einem Fehler.
    Die Nutzdaten stehen unter 'data', die Fortsetzung unter 'next_token'."""
    token = auth.hole_access_token(vehicle_id)
    if not token:
        return {"ok": False, "meldung": "Nicht angemeldet."}

    bis = datetime.now()
    von = bis - timedelta(days=max(1, tage))
    alle: list = []
    next_token = None

    for seite in range(5):   # Sicherheitsgrenze gegen Endlosschleifen
        params = {
            "from": von.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "to": bis.strftime("%Y-%m-%dT%H:%M:%SZ"),
        }
        if next_token:
            params["nextToken"] = next_token
        antwort = _request("GET", f"/customers/vehicles/{vin}/chargingHistory",
                           token, vehicle_id, params=params)
        if not antwort["ok"]:
            event_log_service.log_event("bmw", "warning",
                f"Ladehistorie nicht abrufbar: {antwort['meldung']}")
            return {"ok": False, "meldung": antwort["meldung"]}

        roh = antwort["daten"]
        teil = roh.get("data") if isinstance(roh, dict) else roh
        alle.extend(teil or [])
        next_token = roh.get("next_token") if isinstance(roh, dict) else None
        if not next_token:
            break

    event_log_service.log_event("bmw", "info",
        f"Ladehistorie {von.strftime('%d.%m.')}–{bis.strftime('%d.%m.%Y')}: "
        f"{len(alle)} Ladevorgänge empfangen.")
    return {"ok": True, "sessions": alle}


def _lokalzeit(zeitstempel, zeitzone: str = "") -> datetime:
    """Rechnet einen Unix-Zeitstempel in die Ortszeit des Fahrzeugs um.

    Ohne diese Umrechnung wuerde die Zeitzone des Servers gelten — im
    Docker-Container ist das UTC. Eine Ladung um 00:30 Berliner Zeit wuerde
    dann auf 22:30 des VORTAGS datiert; am Monatsersten faellt sie sogar in
    den Vormonat und landet in der falschen Abrechnung.

    Die Ladehistorie liefert die Zeitzone je Ladevorgang mit ('timeZone'),
    weil im Ausland geladene Sessions eine andere haben koennen."""
    from zoneinfo import ZoneInfo
    try:
        zone = ZoneInfo(zeitzone) if zeitzone else ZoneInfo("Europe/Berlin")
    except Exception:
        zone = ZoneInfo("Europe/Berlin")

    # BMW liefert den Zeitpunkt in zwei Formaten: Das Datenarchiv nutzt
    # Unix-Sekunden, die Live-API ein ISO-8601-Datum. Beide muessen
    # verarbeitet werden — sonst schlaegt der Import an genau der Quelle
    # fehl, die er eigentlich auslesen soll.
    if isinstance(zeitstempel, (int, float)):
        wert = int(zeitstempel)
        if wert > 10_000_000_000:      # Millisekunden statt Sekunden
            wert //= 1000
        return datetime.fromtimestamp(wert, zone).replace(tzinfo=None)

    text = str(zeitstempel or "").strip()
    if not text:
        raise ValueError("leerer Zeitstempel")

    # Reine Ziffern: Unix-Sekunden (oder Millisekunden bei 13 Stellen)
    if text.isdigit():
        wert = int(text)
        if wert > 10_000_000_000:      # Millisekunden
            wert //= 1000
        return datetime.fromtimestamp(wert, zone).replace(tzinfo=None)

    # ISO-8601, etwa "2026-08-22T20:25:12.000Z"
    iso = text.replace("Z", "+00:00")
    dt = datetime.fromisoformat(iso)
    if dt.tzinfo is None:
        # Ohne Zeitzonenangabe gilt UTC — so liefert es die CarData-API.
        from datetime import timezone as _tz
        dt = dt.replace(tzinfo=_tz.utc)
    return dt.astimezone(zone).replace(tzinfo=None)


def _ist_heimladung(session: dict, heim_adresse: str) -> bool:
    """Prueft, ob am hinterlegten Wohnort geladen wurde.

    Verglichen wird die Strasse ohne Hausnummer: BMW liefert je nach
    GPS-Genauigkeit die Hausnummer um ein bis zwei Stellen abweichen kann."""
    if not heim_adresse:
        return False
    ort = (session.get("chargingLocation") or {})
    adresse = (ort.get("formattedAddress") or "").lower()
    if not adresse:
        return False
    import re as _re
    def strasse(text):
        text = _re.sub(r"\d+", "", text.lower())
        return _re.sub(r"[^a-zäöüß]+", "", text)[:14]
    return strasse(adresse) and strasse(adresse) == strasse(heim_adresse)


def _wallbox_zuhause() -> int:
    """Die Wallbox fuer Heimladungen.

    Reihenfolge: eine per OCPP oder Loxone angebundene Wallbox, sonst die
    als Standard markierte, sonst die einzige vorhandene. Erst wenn gar
    keine existiert, wird eine angelegt.

    Der Grund: Wer zuhause laedt, hat dort genau einen Ladepunkt. Ob die
    Daten per OCPP kommen oder aus der BMW-App, aendert daran nichts —
    es bleibt dieselbe Wallbox.
    """
    from services import db_service
    conn = db_service.get_connection()
    try:
        # 1. Angebundene Wallbox (OCPP, Loxone) — die misst tatsaechlich
        z = conn.execute(
            """SELECT id FROM wallboxes
               WHERE source_type IN ('ocpp', 'loxone_api')
               ORDER BY id LIMIT 1""").fetchone()
        if z:
            return z["id"]

        # 2. Irgendeine vorhandene, aber keine der frueher automatisch
        #    angelegten BMW-Eintraege
        z = conn.execute(
            """SELECT id FROM wallboxes
               WHERE name NOT LIKE 'BMW %' AND name NOT LIKE 'Unterwegs%'
               ORDER BY id LIMIT 1""").fetchone()
        if z:
            return z["id"]
    finally:
        conn.close()

    # 3. Keine vorhanden — dann eine anlegen
    from repositories import wallbox_repository
    return wallbox_repository.get_or_create_wallbox(
        "Wallbox zuhause", source_type="manual")


def importiere_ladesessions(vehicle_id: int, vin: str, user_id: int,
                            ueberschneidung_pruefen: bool | None = None,
                            sessions_liste: list | None = None) -> dict:
    """Uebernimmt die Ladehistorie dieses Fahrzeugs als Ladesessions.

    Der Ladeort entscheidet ueber die steuerliche Behandlung, deshalb wird er
    als 'zuhause' oder 'unterwegs' vermerkt. Ohne diese Unterscheidung waere
    unterwegs geladener Strom faelschlich als Heimladung abgerechnet.

    `sessions_liste` erlaubt es, dieselbe Verarbeitung (Heimladung-Erkennung,
    Preise, Duplikatschutz, Energieermittlung) auf bereits vorliegende
    Eintraege anzuwenden, statt sie live abzurufen — genutzt vom
    Archiv-Import (ZIP aelter als 30 Tage), damit die Geschaeftslogik nicht
    doppelt gepflegt werden muss."""
    from repositories import session_repository, wallbox_repository

    # Standard: pruefen. Wer lieber alles importiert und selbst aussortiert,
    # schaltet es in den Einstellungen ab.
    if ueberschneidung_pruefen is None:
        ueberschneidung_pruefen = (
            settings_repository.get_setting("bmw_duplikate_pruefen") or "1") == "1"

    if sessions_liste is not None:
        sessions = sessions_liste
    else:
        gelesen = lese_ladehistorie(vehicle_id, vin)
        if not gelesen["ok"]:
            return gelesen
        sessions = gelesen["sessions"]

    # ROHDATEN-DIAGNOSE: Den ersten Datensatz unveraendert protokollieren.
    # Bei Importproblemen ist das die einzig verlaessliche Auskunft darueber,
    # was BMW tatsaechlich liefert — Vermutungen ueber Feldnamen und Formate
    # haben hier schon genug Zeit gekostet.
    if sessions:
        try:
            probe = dict(sessions[0])
            bloecke = probe.get("chargingBlocks") or []
            probe["chargingBlocks"] = f"<{len(bloecke)} Blöcke>"
            if bloecke:
                probe["_erster_block"] = bloecke[0]
            event_log_service.log_event("bmw", "info",
                "Rohdaten des ersten Ladevorgangs: "
                + json.dumps(probe, ensure_ascii=False, default=str)[:900])
        except Exception:
            event_log_service.log_event("bmw", "warning",
                f"Erster Datensatz nicht lesbar (Typ {type(sessions[0]).__name__})")
    if not sessions:
        return {"ok": True, "neu": 0, "gefunden": 0,
                "meldung": "Keine Ladevorgänge in den letzten 30 Tagen."}

    heim = _heimadresse(sessions)

    # HEIMLADUNGEN gehoeren auf die vorhandene Wallbox, nicht auf eine
    # erfundene zweite. Frueher entstand hier "BMW (zuhause)" — wer bereits
    # eine eigene Wallbox betreibt, hatte danach zwei Eintraege fuer
    # denselben Ladepunkt und musste jede Auswertung doppelt lesen.
    wb_heim = _wallbox_zuhause()

    # UNTERWEGS ist etwas anderes: Fremde Ladesaeulen sind keine eigene
    # Wallbox und gehoeren nicht mit ihr vermischt. Ein Sammeleintrag
    # genuegt — welche Saeule es war, steht ohnehin in der Ortsangabe.
    from repositories import wallbox_repository
    wb_extern = wallbox_repository.get_or_create_wallbox(
        "Unterwegs geladen", source_type="manual")

    # Preise je Ladeart. Heimladung laeuft ueber den eigenen Vertrag, extern
    # ueber Ladekarte oder Ad-hoc-Tarif — dort liegen die Preise deutlich
    # hoeher, besonders am Schnelllader.
    preis_heim = float(settings_repository.get_setting("contract_kwh_price")
                       or settings_repository.get_setting("default_kwh_price") or 0.34)
    preis_dc = float(settings_repository.get_setting("preis_dc_kwh") or 0.79)
    preis_ac_extern = float(settings_repository.get_setting("preis_ac_extern_kwh") or 0.59)
    # Getrennte Zaehler: Ohne sie bleibt unklar, warum ein Import nichts
    # uebernimmt — die haeufigste Rueckfrage bei jedem Datenimport.
    neu = uebersprungen = doppelt = 0
    ohne_zeit = ohne_energie = bereits_da = heimladungen = 0
    for s in sessions:
        start = s.get("startTime")
        if not start:
            ohne_zeit += 1
            uebersprungen += 1
            continue
        zone = s.get("timeZone") or ""
        try:
            start_dt = _lokalzeit(start, zone)
        except Exception as e:
            # Frueher wurde hier stumm uebersprungen — der Import meldete
            # dann "0 uebernommen" ohne jeden Grund. Jetzt wird der Fall
            # gezaehlt und einmalig protokolliert.
            if ohne_zeit == 0:
                event_log_service.log_event("bmw", "warning",
                    f"Zeitstempel nicht lesbar: {start!r} ({type(e).__name__})")
            ohne_zeit += 1
            uebersprungen += 1
            continue
        # Endzeitpunkt bevorzugt aus dem Feld 'endTime' (Spezifikation),
        # sonst aus der Ladedauer hochgerechnet.
        if s.get("endTime"):
            ende_dt = _lokalzeit(s["endTime"], zone)
        else:
            ende_dt = start_dt + timedelta(seconds=int(s.get("totalChargingDurationSec") or 0))
        start_ts = start_dt.strftime("%Y-%m-%d %H:%M:%S")

        zuhause = _ist_heimladung(s, heim)
        wb_id = wb_heim if zuhause else wb_extern

        # HEIMLADUNGEN werden standardmaessig NICHT uebernommen.
        #
        # Der Grund ist steuerlich: Abgerechnet wird der Strom aus der
        # eigenen Wallbox, und den misst deren Zaehler — bei MID-Geraeten
        # eichrechtlich belastbar. BMW meldet dagegen einen vom Fahrzeug
        # geschaetzten Wert. Beides zusammen ergibt eine Doppelerfassung
        # mit zwei unterschiedlichen Zahlen fuer denselben Vorgang.
        #
        # Externe Ladungen sind etwas anderes: Die misst keine eigene
        # Wallbox, und fuer sie liegen Belege von Ladekarte oder Anbieter
        # vor. Dort ist die BMW-Angabe eine sinnvolle Ergaenzung.
        #
        # Wer keine eigene Wallbox betreibt, schaltet 'bmw_heimladungen'
        # ein — dann kommt alles herein.
        heim_uebernehmen = (settings_repository.get_setting("bmw_heimladungen") == "1")
        if zuhause and not heim_uebernehmen:
            uebersprungen += 1
            heimladungen += 1
            continue

        # Zusaetzlicher Schutz, falls Heimladungen doch gewuenscht sind:
        # bereits von der Wallbox erfasste Zeitraeume nicht doppelt anlegen.
        if (zuhause and ueberschneidung_pruefen
                and _heimladung_bereits_erfasst(start_dt, ende_dt, wb_heim)):
            uebersprungen += 1
            doppelt += 1
            continue

        # ENERGIEMENGE — drei Quellen, nach Verlaesslichkeit geordnet:
        #
        # 1. 'energyConsumedFromPowerGridKwh' — laut Spezifikation Bestandteil
        #    jeder Ladesession: die netzseitig bezogene Energie, von BMW
        #    gemeldet. Genau diese Menge misst der Hausstromzaehler und genau
        #    sie wird erstattet.
        # 2. Ladebloecke: Σ (mittlere Netzleistung x Blockdauer) — dasselbe
        #    Ergebnis, aber gerechnet statt gemeldet.
        # 3. Ladezustand x Akkukapazitaet: liefert nur die im Akku
        #    ANGEKOMMENE Energie und liegt zu niedrig — beim AC-Laden zuhause
        #    gehen rund 8 % als Ladeverlust verloren, die trotzdem bezahlt
        #    werden muessen. Daher mit Aufschlag und nur als letzte Wahl.
        kwh = 0.0
        direkt = s.get("energyConsumedFromPowerGridKwh")
        if direkt is not None:
            try:
                kwh = float(direkt)
            except (TypeError, ValueError):
                kwh = 0.0

        # 2. Wahl: aus den Ladebloecken rechnen (Leistung x Dauer).
        bloecke = s.get("chargingBlocks") or []
        if kwh <= 0:
            for block in bloecke:
                leistung = float(block.get("averagePowerGridKw") or 0)
                dauer_s = int(block.get("endTime", 0)) - int(block.get("startTime", 0))
                if leistung > 0 and dauer_s > 0:
                    kwh += leistung * dauer_s / 3600.0
        aus_bloecken = kwh > 0

        if not aus_bloecken:
            soc_start = s.get("displayedStartSoc")
            soc_ende = s.get("displayedSoc")
            akku = _akku_kapazitaet(vehicle_id)
            if soc_start is not None and soc_ende is not None and akku:
                # Ladeverlust aufschlagen, damit die Menge dem Netzbezug
                # entspricht (AC rund 8 %, DC rund 2 %).
                netto = max(0.0, (float(soc_ende) - float(soc_start)) / 100.0 * akku)
                kwh = netto * 1.08
        if kwh <= 0:
            # Kein Fehler: BMW protokolliert auch Steckvorgaenge, bei denen
            # keine Energie floss (Ladezustand unveraendert, etwa 44 → 44 %).
            # Solche Eintraege gehoeren nicht in eine Abrechnung.
            ohne_energie += 1
            uebersprungen += 1
            continue

        # ENDZEITPUNKT — 'endTime' ist laut Spezifikation ein Pflichtfeld und
        # damit die verlaesslichste Quelle. Fehlt es (aeltere Archivdaten),
        # dient die Spanne der Ladebloecke als Ersatz.
        # 'totalChargingDurationSec' waere ungeeignet: Es zaehlt nur die aktiven
        # Abschnitte und unterschlaegt Pausen, etwa beim netzdienlichen Laden.
        if s.get("endTime"):
            ende_dt = _lokalzeit(s["endTime"], zone)
        if bloecke:
            b_start = min(int(b.get("startTime", 0)) for b in bloecke)
            b_ende = max(int(b.get("endTime", 0)) for b in bloecke)
            if b_start and b_ende > b_start:
                start_dt = _lokalzeit(b_start, zone)
                ende_dt = _lokalzeit(b_ende, zone)
                start_ts = start_dt.strftime("%Y-%m-%d %H:%M:%S")

        # DOPPELIMPORT VERHINDERN — erst jetzt, nach der Zeitkorrektur aus den
        # Ladebloecken. Frueher geprueft, waere der Zeitstempel ein anderer
        # als der spaeter gespeicherte, und derselbe Ladevorgang liesse sich
        # beliebig oft erneut einlesen.
        if session_repository.session_exists_near_start(wb_id, start_ts[:16]):
            bereits_da += 1
            uebersprungen += 1
            continue

        # LADEART aus der Spitzenleistung ableiten. Die Trennung ist in der
        # Praxis eindeutig: Eine Heim-Wallbox liefert bis 11 kW (dreiphasig
        # 16 A), Schnelllader dagegen 50 kW und mehr. Alles darueber ist
        # zwangslaeufig DC — und deutlich teurer.
        max_kw = 0.0
        for block in bloecke:
            max_kw = max(max_kw, float(block.get("averagePowerGridKw") or 0))
        ist_dc = max_kw > DC_SCHWELLE_KW

        if zuhause:
            preis = preis_heim
        elif ist_dc:
            preis = preis_dc
        else:
            preis = preis_ac_extern

        session_repository.insert_session(
            user_id=user_id,
            wallbox_id=wb_id,
            source="bmw_app",
            start_timestamp=start_ts,
            end_timestamp=ende_dt.strftime("%Y-%m-%d %H:%M:%S"),
            meter_start_wh=0, meter_stop_wh=int(kwh * 1000),
            price_per_kwh=preis,
            status="closed",
            charging_location="zuhause" if zuhause else "extern",
            charging_location_note=(s.get("chargingLocation") or {}).get("formattedAddress") or "")
        neu += 1

    if neu:
        event_log_service.log_event("bmw", "info",
            f"CarData: {neu} Ladevorgänge importiert ({uebersprungen} bereits bekannt).")
    # Immer protokollieren, auch wenn nichts uebernommen wurde — gerade dann
    # ist die Aufschluesselung entscheidend.
    event_log_service.log_event("bmw", "info",
        f"Ladehistorie verarbeitet: {len(sessions)} empfangen · {neu} übernommen · "
        f"{heimladungen} Heimladungen (Wallbox misst selbst) · "
        f"{bereits_da} bereits vorhanden · {doppelt} von der Wallbox erfasst · "
        f"{ohne_energie} ohne Energiefluss · {ohne_zeit} ohne Zeitstempel")
    return {"ok": True, "neu": neu, "gefunden": len(sessions),
            "heimladungen": heimladungen,
            "uebersprungen": uebersprungen, "doppelt": doppelt,
            "bereits_da": bereits_da, "ohne_energie": ohne_energie,
            "ohne_zeit": ohne_zeit}


def _akku_kapazitaet(vehicle_id: int) -> float:
    """Akkukapazitaet aus den zuletzt gelesenen Fahrzeugdaten."""
    d = fahrzeugdaten(vehicle_id)
    try:
        return float(d.get("akku_max_kwh") or 0)
    except (TypeError, ValueError):
        return 0.0


def _heimladung_bereits_erfasst(start_dt, ende_dt, eigene_wallbox_id: int) -> bool:
    """Prueft, ob eine andere Wallbox denselben Zeitraum bereits erfasst hat.

    Wer eine eigene Wallbox betreibt, hat die Heimladung schon ueber OCPP oder
    den Loxone-Import erfasst. Wuerde sie zusaetzlich aus der BMW-Historie
    uebernommen, stuende dieselbe Energiemenge zweimal in der Abrechnung —
    ein handfestes Problem gegenueber dem Finanzamt (§ 3 Nr. 50 EStG).

    Verglichen werden die Zeitraeume auf Ueberschneidung, nicht die exakten
    Startzeiten: Wallbox und Fahrzeug messen an verschiedenen Punkten und
    melden denselben Vorgang mit leicht abweichenden Zeitstempeln.
    """
    conn = db_service.get_connection()
    try:
        treffer = conn.execute(
            """SELECT COUNT(*) AS c FROM charging_sessions
               WHERE wallbox_id != ?
                 AND end_timestamp IS NOT NULL
                 AND datetime(start_timestamp) < datetime(?)
                 AND datetime(end_timestamp)   > datetime(?)""",
            (eigene_wallbox_id,
             ende_dt.strftime("%Y-%m-%d %H:%M:%S"),
             start_dt.strftime("%Y-%m-%d %H:%M:%S"))).fetchone()
        return (treffer["c"] if treffer else 0) > 0
    finally:
        conn.close()


def _zeitzone(name: str | None):
    """Zeitzone aus dem BMW-Feld 'timeZone', sonst deutsche Zeit.

    Der Server laeuft im Container ueblicherweise in UTC. Ohne Umrechnung
    laegen die importierten Zeiten zwei Stunden daneben — eine Ladung um
    00:30 Uhr erschiene sogar am Vortag."""
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo(name) if name else ZoneInfo("Europe/Berlin")
    except Exception:
        try:
            from zoneinfo import ZoneInfo
            return ZoneInfo("Europe/Berlin")
        except Exception:
            return None



def _heimadresse(sessions: list | None = None) -> str:
    """Ermittelt die Wohnadresse fuer den Abgleich mit den Ladeorten.

    Reihenfolge:
      1. Ausdrueckliche Einstellung 'heim_adresse'
      2. Wohnanschrift der hinterlegten Person (persons.home_address)
      3. Haeufigster Ladeort in der Historie

    Punkt 3 ist die Absicherung: Ohne bekannte Adresse wuerde JEDE Ladung als
    'extern' gelten und faelschlich mit dem teuren Fremdtarif abgerechnet.
    Der Ort, an dem mit Abstand am haeufigsten geladen wird, ist in der Praxis
    immer der eigene Stellplatz — niemand faehrt zwanzigmal zur selben
    Ladesaeule, wenn er zuhause laden kann."""
    ausdruecklich = (settings_repository.get_setting("heim_adresse") or "").strip()
    if ausdruecklich:
        return ausdruecklich

    # Wohnanschrift der Person
    try:
        conn = db_service.get_connection()
        try:
            row = conn.execute(
                "SELECT home_address FROM persons WHERE home_address IS NOT NULL "
                "AND TRIM(home_address) != '' LIMIT 1").fetchone()
            if row and row["home_address"]:
                return row["home_address"].strip()
        finally:
            conn.close()
    except Exception:
        pass

    # Haeufigster Ladeort
    if sessions:
        from collections import Counter
        orte = Counter()
        for s in sessions:
            adr = (s.get("chargingLocation") or {}).get("formattedAddress")
            if adr:
                orte[adr] += 1
        if orte:
            haeufigster, anzahl = orte.most_common(1)[0]
            # Nur uebernehmen, wenn er deutlich dominiert
            if anzahl >= 3 and anzahl >= len(sessions) * 0.3:
                event_log_service.log_event("bmw", "info",
                    f"Keine Wohnadresse hinterlegt — häufigster Ladeort "
                    f"'{haeufigster}' ({anzahl}x) wird als Zuhause gewertet.")
                return haeufigster
    return ""

# ENTFERNT (28.08.): importiere_fahrten_aus_ladehistorie leitete Fahrten aus
# Ladepunkten ab — derselbe fachliche Fehler wie beim entfernten
# Archiv-Import (siehe cardata_archiv_service.rekonstruiere_fahrten-Docstring):
# zwischen zwei Ladungen kann beliebig viel liegen. Fahrten entstehen jetzt
# ausschliesslich aus dem MQTT-Stream (cardata_stream_service).
