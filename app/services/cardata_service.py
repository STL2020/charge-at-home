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
DESCRIPTOR_AKKU_SOH = "vehicle.powertrain.electric.battery.stateOfHealth.displayed"
DESCRIPTOR_SOC = "vehicle.powertrain.electric.battery.stateOfCharge.displayed"
DESCRIPTOR_REICHWEITE = "vehicle.drivetrain.electricEngine.kombiRemainingElectricRange"
DESCRIPTOR_SERVICE = "vehicle.status.serviceDistance.next"
DESCRIPTOR_WOCHE = "vehicle.vehicle.averageWeeklyDistanceLongTerm"

CONTAINER_DESCRIPTORS = [
    # Fahrterfassung
    DESCRIPTOR_KM, DESCRIPTOR_LAT, DESCRIPTOR_LON,
    DESCRIPTOR_TRIP_KM, DESCRIPTOR_TRIP_ZEIT, DESCRIPTOR_TRIP_SOC,
    # Fahrzeugdaten
    DESCRIPTOR_VERBRAUCH, DESCRIPTOR_AKKU_MAX, DESCRIPTOR_AKKU_SOH,
    DESCRIPTOR_SOC, DESCRIPTOR_REICHWEITE, DESCRIPTOR_SERVICE, DESCRIPTOR_WOCHE,
]

# Unterhalb dieser Distanz gilt eine Aenderung als Messrauschen bzw.
# Rangieren und erzeugt keine Fahrt.
MIN_DISTANZ_KM = 0.5

# Ab dieser Spitzenleistung gilt ein Ladevorgang als Gleichstrom-Schnellladung.
# Eine dreiphasige Heim-Wallbox erreicht maximal 22 kW, in der Praxis meist 11 kW;
# Schnelllader beginnen bei 50 kW. Der Zwischenraum ist bewusst grosszuegig.
DC_SCHWELLE_KW = 25.0


# ── HTTP ───────────────────────────────────────────────────────────────────

def _request(methode: str, pfad: str, token: str,
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
            return {"ok": True, "daten": json.loads(roh) if roh else {}}
    except urllib.error.HTTPError as e:
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

def hole_fahrzeuge() -> dict:
    """Fahrzeuge des Kontos. Nur als Hauptnutzer zugeordnete liefern Daten."""
    token = auth.hole_access_token()
    if not token:
        return {"ok": False, "meldung": "Nicht angemeldet."}
    antwort = _request("GET", "/customers/vehicles/mappings", token)
    if not antwort["ok"]:
        return {"ok": False, "meldung": antwort["meldung"]}
    roh = antwort["daten"]
    liste = roh.get("mappings", roh if isinstance(roh, list) else [])
    return {"ok": True, "fahrzeuge": [
        {"vin": f.get("vin"), "typ": f.get("mappingType", "")} for f in liste]}


# ── Container ──────────────────────────────────────────────────────────────

def stelle_container_sicher() -> dict:
    """Legt den Container mit den Fahrt-Datenpunkten an, falls noetig.

    Die Container-ID wird gespeichert; ein erneuter Aufruf verbraucht dann
    kein weiteres Kontingent."""
    vorhandene = settings_repository.get_setting("cardata_container_id") or ""
    if vorhandene:
        return {"ok": True, "container_id": vorhandene, "neu": False}

    token = auth.hole_access_token()
    if not token:
        return {"ok": False, "meldung": "Nicht angemeldet."}

    antwort = _request("POST", "/customers/containers", token, body={
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
    settings_repository.set_setting("cardata_container_id", cid)
    event_log_service.log_event("bmw", "info", f"CarData-Container angelegt: {cid}")
    return {"ok": True, "container_id": cid, "neu": True}


# ── Datenabruf & Fahrterkennung ────────────────────────────────────────────

def _zahl(wert) -> float | None:
    try:
        return float(wert)
    except (TypeError, ValueError):
        return None


def lese_telematik(vin: str) -> dict:
    """Aktuelle Werte des Containers fuer ein Fahrzeug (1 Aufruf vom Tageslimit)."""
    token = auth.hole_access_token()
    if not token:
        return {"ok": False, "meldung": "Nicht angemeldet."}
    cont = stelle_container_sicher()
    if not cont["ok"]:
        return cont

    antwort = _request("GET", f"/customers/vehicles/{vin}/telematicData", token,
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
        "akku_max_kwh": _zahl(feld(DESCRIPTOR_AKKU_MAX)[0]),
        "akku_soh_prozent": _zahl(feld(DESCRIPTOR_AKKU_SOH)[0]),
        "soc_prozent": _zahl(feld(DESCRIPTOR_SOC)[0]),
        "reichweite_km": _zahl(feld(DESCRIPTOR_REICHWEITE)[0]),
        "service_in_km": _zahl(feld(DESCRIPTOR_SERVICE)[0]),
        "woche_km": _zahl(feld(DESCRIPTOR_WOCHE)[0]),
        "roh": roh,
    }


def _lade_letzten_stand(vin: str) -> dict:
    roh = settings_repository.get_setting(f"cardata_stand_{vin}") or ""
    try:
        return json.loads(roh) if roh else {}
    except Exception:
        return {}


def _speichere_stand(vin: str, stand: dict) -> None:
    settings_repository.set_setting(f"cardata_stand_{vin}", json.dumps(stand))


def pruefe_fahrt(vin: str, user_id: int, vehicle_id: int | None = None) -> dict:
    """Ruft die aktuellen Werte ab und legt bei erkannter Fahrt einen Eintrag an.

    Der jeweils letzte Stand wird gespeichert und beim naechsten Aufruf als
    Startpunkt verwendet. Beim allerersten Abruf entsteht daher noch keine
    Fahrt — es fehlt der Vergleichswert."""
    jetzt = lese_telematik(vin)
    if not jetzt["ok"]:
        return jetzt
    if jetzt["km"] is None:
        return {"ok": False, "meldung": ("Kein Kilometerstand geliefert. Ist "
                                         f"'{DESCRIPTOR_KM}' im Portal ausgewählt?")}

    # Fahrzeugdaten mitspeichern — sie kommen ohnehin im selben Abruf und
    # ersparen spaeter einen zusaetzlichen Aufruf vom Tageskontingent.
    _speichere_fahrzeugdaten(vin, jetzt)

    vorher = _lade_letzten_stand(vin)
    # Zeitstempel des Fahrzeugs bevorzugen: Der Fahrt-Ende-Wert stammt vom
    # Bordcomputer und trifft den tatsaechlichen Ankunftszeitpunkt, waehrend
    # unser Abruf bis zu 30 Minuten spaeter erfolgen kann.
    zeit = jetzt.get("trip_zeit") or jetzt["zeitpunkt"] or datetime.now().isoformat(timespec="seconds")
    neuer_stand = {
        "km": jetzt["km"], "lat": jetzt["lat"], "lon": jetzt["lon"],
        "zeitpunkt": zeit,
    }
    _speichere_stand(vin, neuer_stand)

    if not vorher or vorher.get("km") is None:
        return {"ok": True, "fahrt_erkannt": False,
                "meldung": "Ausgangsstand gespeichert. Ab dem nächsten Abruf werden Fahrten erkannt.",
                "km": jetzt["km"]}

    distanz = round(jetzt["km"] - vorher["km"], 1)
    if distanz < MIN_DISTANZ_KM:
        event_log_service.log_event("bmw", "info",
            f"Fahrtabruf: keine Änderung (Kilometerstand {int(jetzt['km'])} km, "
            f"zuletzt {int(vorher['km'])} km).")
        return {"ok": True, "fahrt_erkannt": False,
                "meldung": "Keine neue Fahrt seit dem letzten Abruf.",
                "km": jetzt["km"]}

    # Fahrt-ID aus Kilometerständen: stabil und eindeutig, verhindert
    # Doppelanlage bei mehrfachem Abruf.
    trip_id = f"CD-{vin}-{int(vorher['km'])}-{int(jetzt['km'])}"
    trip = {
        "trip_id": trip_id,
        "start_time": _als_dbzeit(vorher.get("zeitpunkt")),
        "end_time": _als_dbzeit(neuer_stand["zeitpunkt"]),
        "start_mileage": int(vorher["km"]),
        "end_mileage": int(jetzt["km"]),
        "distance_km": distanz,
        "start_address": _koordinaten_text(vorher.get("lat"), vorher.get("lon")),
        "end_address": _koordinaten_text(jetzt["lat"], jetzt["lon"]),
    }
    # Verwaiste Referenzen entfernen: Wurde eine Fahrt geloescht, soll sie
    # erneut importiert werden koennen.
    bmw_trip_repository.raeume_verwaiste_auf(user_id)
    bekannt = bmw_trip_repository.bekannte_trip_ids(user_id)
    neu = 0
    if trip["trip_id"] not in bekannt:
        # Direkt in die normale Fahrtenliste — dort wird zugeordnet und
        # bearbeitet, ohne zweite Oberflaeche.
        from repositories import trip_repository
        neue_id = trip_repository.insert_trip(
            user_id=user_id, trip_date=(trip["start_time"] or "")[:10],
            start_address=trip["start_address"] or "—",
            end_address=trip["end_address"] or "—",
            distance_km=trip["distance_km"], purpose="",
            rate_chosen=0.0, vehicle_id=vehicle_id, fahrtart="offen")
        bmw_trip_repository.insert_trip_ref(user_id, trip, neue_id, vehicle_id=vehicle_id)
        neu = 1
    if neu:
        event_log_service.log_event("bmw", "info",
            f"CarData: Fahrt über {distanz} km erkannt ({trip['start_mileage']} → {trip['end_mileage']} km).")
    return {"ok": True, "fahrt_erkannt": bool(neu), "distanz_km": distanz,
            "km": jetzt["km"], "trip": trip if neu else None}


def _als_dbzeit(iso: str | None) -> str:
    """ISO-8601 von BMW in unser Datenbankformat umsetzen."""
    if not iso:
        return datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    text = str(iso).replace("Z", "").replace("T", " ")
    return text[:19] if len(text) >= 19 else datetime.now().strftime("%Y-%m-%d %H:%M:%S")


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


def status() -> dict:
    """Zustand der Anbindung fuer die Oberflaeche."""
    a = auth.status()
    a["container_id"] = settings_repository.get_setting("cardata_container_id") or ""
    a["vin"] = settings_repository.get_setting("cardata_vin") or ""
    a["descriptors"] = CONTAINER_DESCRIPTORS
    return a


# ── Automatischer Abruf im Hintergrund ─────────────────────────────────────
# Bewusst ein schlanker Timer-Thread im Flask-Prozess statt eines eigenen
# Dienstes: Der Abruf ist ein einzelner HTTP-Aufruf alle 30 Minuten, dafuer
# braucht es keinen separaten Prozess mit eigener Ueberwachung.

import threading

_timer: "threading.Timer | None" = None
STANDARD_INTERVALL_MIN = 30


def _intervall_minuten() -> int:
    """Abrufintervall aus den Einstellungen; 0 schaltet den Automatikbetrieb ab.

    Untergrenze 30 Minuten: Bei 50 erlaubten Abrufen pro Tag waeren kuerzere
    Abstaende nicht durchzuhalten (48 Abrufe bei 30 Minuten)."""
    roh = settings_repository.get_setting("cardata_intervall_min")
    try:
        wert = int(roh) if roh not in (None, "") else STANDARD_INTERVALL_MIN
    except ValueError:
        wert = STANDARD_INTERVALL_MIN
    if wert <= 0:
        return 0
    return max(30, wert)


def _automatischer_abruf() -> None:
    """Ein Durchlauf: Fahrt pruefen, dann neu einplanen.

    Faengt jeden Fehler ab — ein Ausfall der BMW-Server darf den Timer nicht
    beenden, sonst liefe die Automatik bis zum Neustart nicht mehr."""
    try:
        vin = settings_repository.get_setting("cardata_vin") or ""
        aktiv = (settings_repository.get_setting("cardata_auto") or "0") == "1"
        if vin and aktiv and auth.status().get("angemeldet"):
            from services import db_service
            conn = db_service.get_connection()
            try:
                zeile = conn.execute("SELECT id FROM users_config LIMIT 1").fetchone()
                user_id = zeile["id"] if zeile else None
            finally:
                conn.close()
            if user_id:
                ergebnis = pruefe_fahrt(vin, user_id)
                settings_repository.set_setting(
                    "cardata_letzter_abruf",
                    datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
                if not ergebnis.get("ok"):
                    event_log_service.log_event(
                        "bmw", "warning",
                        f"CarData-Abruf fehlgeschlagen: {ergebnis.get('meldung', '')}")
    except Exception as e:
        try:
            event_log_service.log_event("bmw", "warning",
                f"CarData-Automatik: {type(e).__name__}")
        except Exception:
            pass
    finally:
        starte_automatik()


def starte_automatik() -> None:
    """Plant den naechsten Abruf ein (bzw. verschiebt ihn)."""
    global _timer
    if _timer is not None:
        _timer.cancel()
        _timer = None
    minuten = _intervall_minuten()
    if minuten <= 0:
        return
    _timer = threading.Timer(minuten * 60, _automatischer_abruf)
    _timer.daemon = True   # blockiert das Herunterfahren nicht
    _timer.start()


def stoppe_automatik() -> None:
    global _timer
    if _timer is not None:
        _timer.cancel()
        _timer = None


# ── Fahrzeugdaten (Stammdaten aus dem Fahrzeug) ────────────────────────────

FAHRZEUGDATEN_FELDER = (
    "verbrauch_kwh_100", "akku_max_kwh", "akku_soh_prozent", "soc_prozent",
    "reichweite_km", "service_in_km", "woche_km", "km",
)


def _speichere_fahrzeugdaten(vin: str, daten: dict) -> None:
    """Legt die zuletzt gemeldeten Fahrzeugwerte ab.

    Sie fallen beim Fahrten-Abruf ohnehin an; getrennt abzurufen wuerde nur
    unnoetig Kontingent verbrauchen."""
    werte = {f: daten.get(f) for f in FAHRZEUGDATEN_FELDER if daten.get(f) is not None}
    if not werte:
        return
    werte["stand"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    settings_repository.set_setting(f"cardata_fzg_{vin}", json.dumps(werte))


def fahrzeugdaten(vin: str = "") -> dict:
    """Zuletzt bekannte Fahrzeugdaten fuer die Anzeige."""
    vin = vin or settings_repository.get_setting("cardata_vin") or ""
    if not vin:
        return {}
    roh = settings_repository.get_setting(f"cardata_fzg_{vin}") or ""
    try:
        d = json.loads(roh) if roh else {}
    except Exception:
        return {}
    d["vin"] = vin
    return d


# ── Ladehistorie über die API ──────────────────────────────────────────────
# Eigener Endpunkt laut CarData-Dokumentation:
#   GET /customer/vehicles/{vin}/chargingHistory
#     → vehicle.powertrain.electric.battery.charging.history.sessionsList
# Liefert dieselben Daten wie das Datenarchiv, aber tagesaktuell — inklusive
# Ladeort mit Adresse. Das ist steuerlich entscheidend: Nur zuhause geladener
# Strom faellt unter den steuerfreien Auslagenersatz (§ 3 Nr. 50 EStG).

def lese_ladehistorie(vin: str, tage: int = 30) -> dict:
    """Ladevorgaenge eines Zeitraums (1 Abruf vom Tageslimit).

    Umgesetzt nach der offiziellen Spezifikation (swagger-customer-api-v1):
      GET /customers/vehicles/{vin}/chargingHistory?from=…&to=…
    'from' und 'to' sind PFLICHT — fehlen sie, antwortet BMW mit einem Fehler.
    Die Nutzdaten stehen unter 'data', die Fortsetzung unter 'next_token'."""
    token = auth.hole_access_token()
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
                           token, params=params)
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


def importiere_ladesessions(vin: str, user_id: int,
                            ueberschneidung_pruefen: bool | None = None) -> dict:
    """Uebernimmt die Ladehistorie als Ladesessions.

    Der Ladeort entscheidet ueber die steuerliche Behandlung, deshalb wird er
    als 'zuhause' oder 'unterwegs' vermerkt. Ohne diese Unterscheidung waere
    unterwegs geladener Strom faelschlich als Heimladung abgerechnet."""
    from repositories import session_repository, wallbox_repository

    # Standard: pruefen. Wer lieber alles importiert und selbst aussortiert,
    # schaltet es in den Einstellungen ab.
    if ueberschneidung_pruefen is None:
        ueberschneidung_pruefen = (
            settings_repository.get_setting("bmw_duplikate_pruefen") or "1") == "1"

    gelesen = lese_ladehistorie(vin)
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
    wb_heim = wallbox_repository.get_or_create_wallbox("BMW (zuhause)", source_type="manual")
    wb_extern = wallbox_repository.get_or_create_wallbox("BMW (unterwegs)", source_type="manual")

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
    ohne_zeit = ohne_energie = bereits_da = 0
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

        # DOPPELERFASSUNG: Wer eine eigene Wallbox betreibt, hat dieselbe
        # Heimladung bereits ueber OCPP oder den Loxone-Import erfasst.
        # Die Pruefung laesst sich abschalten — dann kommt alles herein und
        # der Anwender raeumt selbst auf. Das ist eine Entscheidung ueber die
        # eigenen Daten und gehoert deshalb nicht in die Software hinein
        # verdrahtet.
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
            akku = _akku_kapazitaet(vin)
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
        f"{bereits_da} bereits vorhanden · {doppelt} von der Wallbox erfasst · "
        f"{ohne_energie} ohne Energiefluss · {ohne_zeit} ohne Zeitstempel")
    return {"ok": True, "neu": neu, "gefunden": len(sessions),
            "uebersprungen": uebersprungen, "doppelt": doppelt,
            "bereits_da": bereits_da, "ohne_energie": ohne_energie,
            "ohne_zeit": ohne_zeit}


def _akku_kapazitaet(vin: str) -> float:
    """Akkukapazitaet aus den zuletzt gelesenen Fahrzeugdaten."""
    d = fahrzeugdaten(vin)
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



# ── Fahrten aus der Ladehistorie ableiten ──────────────────────────────────
# Der wichtigste Unterschied zu den Telematikdaten: BMW haelt die Ladehistorie
# 30 Tage vor. Ein Abruf im Monat genuegt also, um sowohl Ladevorgaenge als
# auch Fahrten vollstaendig zu erfassen — die Anwendung muss dafuer NICHT
# dauerhaft laufen.
#
# Moeglich wird das, weil jeder Ladevorgang Kilometerstand und Ladeort traegt:
# Zwischen zwei Ladungen an verschiedenen Orten liegt zwangslaeufig die
# gefahrene Strecke. Dieselbe Rechnung wie beim Archiv-Import, nur mit
# tagesaktuellen Daten.

def importiere_fahrten_aus_ladehistorie(vin: str, user_id: int,
                                        vehicle_id: int | None = None,
                                        tage: int = 30) -> dict:
    """Leitet Fahrten aus der abgerufenen Ladehistorie ab."""
    from services import cardata_archiv_service

    gelesen = lese_ladehistorie(vin, tage=tage)
    if not gelesen["ok"]:
        return gelesen
    sessions = gelesen["sessions"]
    if not sessions:
        return {"ok": True, "neu": 0, "gefunden": 0,
                "meldung": "Keine Ladevorgänge im Zeitraum."}

    fahrten = cardata_archiv_service.rekonstruiere_fahrten(sessions, vin)
    if not fahrten:
        return {"ok": True, "neu": 0, "gefunden": 0,
                "meldung": "Keine Fahrten ableitbar — es fehlen Ortswechsel."}

    bmw_trip_repository.raeume_verwaiste_auf(user_id)
    bekannt = bmw_trip_repository.bekannte_trip_ids(user_id)
    neue = [f for f in fahrten if f["trip_id"] not in bekannt]

    from repositories import trip_repository
    gespeichert = 0
    for f in neue:
        trip_id = trip_repository.insert_trip(
            user_id=user_id, trip_date=(f.get("start_time") or "")[:10],
            start_address=f.get("start_address") or "—",
            end_address=f.get("end_address") or "—",
            distance_km=f.get("distance_km") or 0,
            purpose="",
            rate_chosen=0.0, vehicle_id=vehicle_id, fahrtart="offen")
        bmw_trip_repository.insert_trip_ref(user_id, f, trip_id, vehicle_id=vehicle_id)
        gespeichert += 1

    km = round(sum(f["distance_km"] for f in fahrten), 1)
    event_log_service.log_event("bmw", "info",
        f"Fahrten aus Ladehistorie: {gespeichert} neu von {len(fahrten)} "
        f"({km} km, {len(sessions)} Ladevorgänge ausgewertet).")
    return {"ok": True, "neu": gespeichert, "gefunden": len(fahrten),
            "uebersprungen": len(fahrten) - len(neue), "km_gesamt": km,
            "ladevorgaenge": len(sessions)}
