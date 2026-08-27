"""BMW CarData MQTT-Streaming — je Fahrzeug eine eigene, unabhaengige Verbindung.

Der entscheidende Unterschied zum Abrufen: BMW schickt Aenderungen von
sich aus, sobald sie eintreten — ohne Tageskontingent. Damit wird jede
Fahrt erfasst, auch die kurze am Morgen und die um fuenf Uhr.

Beim Abrufen alle 30 Minuten verschmelzen zwei Fahrten, die dicht
aufeinander folgen, zu einer. Genau das war das Problem, das sich
steuerlich nicht sauber aufloesen liess.

MEHRERE FAHRZEUGE (28.08.)
----------------------------
Fruehere Fassung hielt genau einen Verbindungszustand als Modulvariablen —
es passte also nur ein BMW-Fahrzeug gleichzeitig. Jetzt haelt das Modul
je Fahrzeug einen eigenen Zustand, Client, Thread und ein eigenes
Protokoll (jeweils in einem Dict, Schluessel ist die vehicle_id). Jede
oeffentliche Funktion nimmt deshalb eine `vehicle_id` entgegen. Zwei
Fahrzeuge koennen so vollkommen unabhaengig voneinander streamen, auch mit
unterschiedlichen BMW-Konten.

ZUGANG (je Fahrzeug)
---------------------
    Host:      customer.streaming-cardata.bmwgroup.com
    Port:      9000
    Benutzer:  die GCID des BMW-Kontos dieses Fahrzeugs
    Passwort:  der id_token aus dem OAuth-Verfahren (rund 1 Stunde gueltig)
    Thema:     <gcid>/<vin>

Der id_token laeuft staendig ab und muss erneuert werden. Deshalb baut
dieser Dienst die Verbindung vor Ablauf von sich aus neu auf.

TOPIC-KLARSTELLUNG
-------------------
Das im BMW-Portal angezeigte "Topic"-Feld zeigt nur die VIN — das ist ein
Baustein, nicht das fertige MQTT-Thema. Laut offizieller BMW-Doku (Kapitel
"Streaming") setzt sich das tatsaechliche Thema als "username/topic"
zusammen, hier also gcid/vin.

VORAUSSETZUNGEN beim Anwender (je Fahrzeug)
---------------------------------------------
    1. Im BMW-Portal einen CarData-Client anlegen
    2. Das Fahrzeug muss als Hauptnutzer zugeordnet sein
    3. Die gewuenschten Datenpunkte im Container auswaehlen (siehe
       "Datenauswahl aendern" — ohne Latitude/Longitude/travelledDistance
       ausgewaehlt kommt nichts Verwertbares an, auch bei korrekter
       Verbindung nicht)

GETESTET
--------
Nachrichtenverarbeitung und Fahrterkennung mit simulierten Daten geprueft;
Verbindung, SUBACK-Auswertung und Token-Zwangserneuerung gegen ein echtes
Konto verifiziert (Protokoll-Auswertung 28.08.).
"""
from __future__ import annotations

import json
import threading
import time
from datetime import datetime

from repositories import vehicle_bmw_repository as bmw_repo
import services.event_log_service as event_log_service

MQTT_HOST_STANDARD = "customer.streaming-cardata.bmwgroup.com"
MQTT_PORT_STANDARD = 9000

# Der id_token gilt rund eine Stunde. Zehn Minuten vorher wird erneuert,
# damit die Verbindung nicht mitten in einer Fahrt abreisst.
TOKEN_PUFFER_S = 600

_PROTOKOLL_MAX = 300

# Alles je Fahrzeug (vehicle_id), nicht mehr modulweit einmalig.
_zustand: dict[int, dict] = {}
_client: dict[int, object] = {}
_thread: dict[int, threading.Thread] = {}
_protokoll: dict[int, list[str]] = {}


def _leerer_zustand() -> dict:
    return {
        "laeuft": False,
        "verbunden": False,
        "abonniert": None,   # None = noch keine Rückmeldung, True/False danach
        "letzte_nachricht": None,
        "fehler": "",
        "nachrichten": 0,
        "verbindungen": 0,
    }


def _z(vehicle_id: int) -> dict:
    """Zustands-Dict dieses Fahrzeugs, bei Bedarf neu angelegt."""
    return _zustand.setdefault(vehicle_id, _leerer_zustand())


def mqtt_host(vehicle_id: int) -> str:
    """Host für die Stream-Verbindung dieses Fahrzeugs — einstellbar statt
    fest verdrahtet, weil Host/Port Teil der individuellen
    Streaming-Zugangsdaten aus dem BMW-Portal sind."""
    return bmw_repo.get(vehicle_id)["stream_host"] or MQTT_HOST_STANDARD


def mqtt_port(vehicle_id: int) -> int:
    wert = bmw_repo.get(vehicle_id)["stream_port"]
    try:
        return int(wert) if wert else MQTT_PORT_STANDARD
    except (TypeError, ValueError):
        return MQTT_PORT_STANDARD


def setze_verbindung(vehicle_id: int, host: str, port: int) -> None:
    bmw_repo.set_felder(vehicle_id,
                        stream_host=(host or "").strip(),
                        stream_port=str(int(port)) if port else "")


# ── Protokoll fuer die Oberflaeche ──────────────────────────────────────────
# Ringpuffer im Arbeitsspeicher je Fahrzeug, damit sich jede Verbindungsstufe
# direkt in der App verfolgen laesst — kein Putty, kein docker exec noetig.

def _protokoll_schreiben(vehicle_id: int, zeile: str) -> None:
    zeitstempel = datetime.now().strftime("%H:%M:%S")
    puffer = _protokoll.setdefault(vehicle_id, [])
    puffer.append(f"[{zeitstempel}] {zeile}")
    if len(puffer) > _PROTOKOLL_MAX:
        del puffer[:len(puffer) - _PROTOKOLL_MAX]


def protokoll_lesen(vehicle_id: int) -> list[str]:
    return list(_protokoll.get(vehicle_id, []))


def protokoll_leeren(vehicle_id: int) -> None:
    _protokoll[vehicle_id] = []


def status(vehicle_id: int) -> dict:
    """Zustand dieses Fahrzeugs fuer die Anzeige.

    'aktiv' ist die gespeicherte Einstellung, 'laeuft' der tatsaechliche
    Thread-Zustand dieses Prozesses — beide koennen auseinanderfallen,
    naemlich direkt nach einem Neustart, bevor die Anwendung den Stream
    wieder aufgenommen hat.
    """
    z = _z(vehicle_id)
    fehler = z["fehler"]
    if (z["laeuft"] and not z["verbunden"] and not fehler
            and z.get("versuch_seit")):
        try:
            wartet_s = (datetime.now() - datetime.fromisoformat(
                z["versuch_seit"])).total_seconds()
        except Exception:
            wartet_s = 0
        if wartet_s > 45:
            fehler = ("Seit über 45 Sekunden keine Verbindung zustande "
                      "gekommen. Meist Netzwerk (Port 9000 ausgehend "
                      "blockiert?) oder ein abgelaufener Token — im "
                      "Zweifel Haken entfernen und neu setzen.")
    if z["verbunden"] and z.get("abonniert") is False and not fehler:
        fehler = ("Verbunden, aber BMW hat das Abonnement des Themas "
                  "abgelehnt (siehe Protokoll für den genauen Code). Meist "
                  "falsche GCID/VIN-Kombination oder das Fahrzeug ist nicht "
                  "als Hauptnutzer zugeordnet.")
    return {
        "aktiv": aktiviert(vehicle_id),
        "laeuft": z["laeuft"],
        "verbunden": z["verbunden"],
        "abonniert": z.get("abonniert"),
        "letzte_nachricht": z["letzte_nachricht"],
        "nachrichten": z["nachrichten"],
        "verbindungen": z.get("verbindungen", 0),
        "fehler": fehler,
        "host": mqtt_host(vehicle_id),
        "port": mqtt_port(vehicle_id),
    }


def aktiviert(vehicle_id: int) -> bool:
    return bmw_repo.get(vehicle_id)["stream_aktiv"]


def setze_aktiv(vehicle_id: int, an: bool) -> None:
    bmw_repo.set_felder(vehicle_id, stream_aktiv=1 if an else 0)
    if an:
        starte(vehicle_id)
    else:
        stoppe(vehicle_id)


# ── Verbindung ─────────────────────────────────────────────────────────────

def starte(vehicle_id: int) -> dict:
    """Startet den Stream dieses Fahrzeugs im Hintergrund.

    Gibt sofort zurueck — der Verbindungsaufbau laeuft im Thread. Ob er
    geklappt hat, steht danach in status(vehicle_id).
    """
    z = _z(vehicle_id)

    if z["laeuft"]:
        return {"ok": True, "meldung": "Stream läuft bereits."}
    # Ein noch laufender Thread aus einem frueheren Start muss erst enden,
    # sonst gibt es zwei Verbindungen fuer dasselbe Fahrzeug — BMW zaehlt
    # die als zwei Sitzungen.
    alter_thread = _thread.get(vehicle_id)
    if alter_thread is not None and alter_thread.is_alive():
        return {"ok": True, "meldung": "Vorheriger Stream wird noch beendet."}

    try:
        import paho.mqtt.client  # noqa: F401
    except ImportError:
        z["fehler"] = ("Die MQTT-Bibliothek fehlt. Sie ist Bestandteil "
                       "des Docker-Abbilds — bei lokaler Installation "
                       "mit 'pip install paho-mqtt' nachrüsten.")
        return {"ok": False, "meldung": z["fehler"]}

    from repositories import vehicle_repository
    fahrzeug = vehicle_repository.get_vehicle(vehicle_id) or {}
    gcid = bmw_repo.get(vehicle_id)["gcid"]
    vin = (fahrzeug.get("vin") or "").strip()
    if not gcid or not vin:
        z["fehler"] = ("GCID oder Fahrgestellnummer fehlen. Beide "
                       "entstehen bei der Anmeldung dieses Fahrzeugs.")
        return {"ok": False, "meldung": z["fehler"]}

    z["laeuft"] = True
    z["fehler"] = ""
    z["versuch_seit"] = datetime.now().isoformat(timespec="seconds")
    _protokoll_schreiben(vehicle_id, f"Stream wird gestartet (GCID {gcid}, VIN {vin}) …")
    t = threading.Thread(target=_schleife, args=(vehicle_id, gcid, vin), daemon=True)
    _thread[vehicle_id] = t
    t.start()
    event_log_service.log_event("bmw", "info",
        f"CarData-Stream gestartet (Fahrzeug {fahrzeug.get('bezeichnung') or vehicle_id}).")
    return {"ok": True, "meldung": "Stream gestartet."}


def stoppe(vehicle_id: int) -> dict:
    z = _z(vehicle_id)
    z["laeuft"] = False
    z["verbunden"] = False
    client = _client.get(vehicle_id)
    if client is not None:
        try:
            client.disconnect()
        except Exception:
            pass
        _client[vehicle_id] = None
    event_log_service.log_event("bmw", "info", "CarData-Stream angehalten.")
    return {"ok": True}


def stream_laeuft_irgendwo() -> list[int]:
    """Vehicle-IDs, deren Stream-Thread gerade laeuft — fuer eine
    Gesamtuebersicht ueber alle Fahrzeuge."""
    return [vid for vid, z in _zustand.items() if z.get("laeuft")]


def _schleife(vehicle_id: int, gcid: str, vin: str) -> None:
    """Haelt die Verbindung dieses Fahrzeugs offen und erneuert den Token
    rechtzeitig.

    Faengt jeden Fehler ab: Ein Aussetzer bei BMW darf den Thread nicht
    beenden, sonst laeuft der Stream bis zum Neustart nicht mehr."""
    import paho.mqtt.client as mqtt
    from services import cardata_auth_service as auth

    z = _z(vehicle_id)
    wartezeit = 5      # waechst bei wiederholten Fehlern
    # Nach wiederholten Fehlschlaegen wird der Token zwangsweise erneuert,
    # statt der eigenen Gueltigkeitsbuchhaltung zu vertrauen — beobachtet:
    # ein von BMW im Stillen invalidierter Token (z.B. durch eine zweite
    # Sitzung mit demselben Konto) fuehrt sonst zu endlosem
    # "Bad user name or password" mit demselben toten Token.
    fehlschlaege_in_folge = 0

    while z["laeuft"]:
        try:
            if fehlschlaege_in_folge >= 2:
                _protokoll_schreiben(vehicle_id,
                    f"{fehlschlaege_in_folge} Fehlschläge in Folge — erzwinge "
                    f"Token-Erneuerung statt es erneut mit demselben zu versuchen.")
                auth.erneuere_tokens(vehicle_id, erzwingen=True)

            token = auth.hole_id_token(vehicle_id)
            if not token:
                z["fehler"] = "Kein gültiger Token — bitte neu anmelden."
                time.sleep(60)
                continue

            client = mqtt.Client(
                mqtt.CallbackAPIVersion.VERSION2,
                client_id=f"echarge-{vin[-6:]}",
                protocol=mqtt.MQTTv5,
            )
            client.username_pw_set(gcid, token)
            client.tls_set()

            # Zusammensetzung nach offizieller BMW-Doku (Kapitel "Streaming").
            thema = f"{gcid}/{vin}"
            erstverbindung = {"erledigt": False}

            def bei_verbindung(c, userdata, flags, rc, props=None):
                nonlocal fehlschlaege_in_folge
                if rc == 0:
                    fehlschlaege_in_folge = 0
                    z["verbunden"] = True
                    z["abonniert"] = None
                    z["fehler"] = ""
                    z["verbindungen"] = z.get("verbindungen", 0) + 1
                    _protokoll_schreiben(vehicle_id,
                        f"✓ Verbunden (Code {rc}). Abonniere Thema {thema} …")
                    c.subscribe(thema, qos=1)
                    if not erstverbindung["erledigt"]:
                        erstverbindung["erledigt"] = True
                        event_log_service.log_event("bmw", "info",
                            f"Stream verbunden — Thema {thema}")
                else:
                    z["verbunden"] = False
                    z["fehler"] = f"Verbindung abgelehnt (Code {rc})"
                    _protokoll_schreiben(vehicle_id, f"✕ Verbindung abgelehnt (Code {rc}).")

            def bei_abonnement(c, userdata, mid, reason_codes, properties=None):
                abgelehnt = any(getattr(code, "value", code) is not None
                               and getattr(code, "value", code) >= 128
                               for code in reason_codes)
                z["abonniert"] = not abgelehnt
                codes = [getattr(code, "value", code) for code in reason_codes]
                if abgelehnt:
                    _protokoll_schreiben(vehicle_id, f"✕ Abonnement abgelehnt (Code {codes}).")
                    event_log_service.log_event("bmw", "warning",
                        f"Stream: Abonnement von Thema '{thema}' abgelehnt "
                        f"(Code {codes}). Vermutlich GCID/VIN-Kombination "
                        f"falsch oder Fahrzeug nicht als Hauptnutzer zugeordnet.")
                else:
                    _protokoll_schreiben(vehicle_id,
                        f"✓ Abonnement bestätigt (Code {codes}). Warte auf Nachrichten von BMW …")
                    event_log_service.log_event("bmw", "info",
                        f"Stream: Abonnement von Thema '{thema}' bestätigt.")

            def bei_trennung(c, userdata, flags, rc, props=None):
                z["verbunden"] = False
                _protokoll_schreiben(vehicle_id, f"Verbindung getrennt (Code {rc}).")

            def bei_nachricht(c, userdata, msg):
                try:
                    roh = msg.payload.decode("utf-8")
                    vorschau = roh if len(roh) <= 1000 else roh[:1000] + " …(gekürzt)"
                    _protokoll_schreiben(vehicle_id, f"★ Nachricht auf '{msg.topic}': {vorschau}")
                    verarbeite_nachricht(vehicle_id, roh)
                except Exception as e:
                    _protokoll_schreiben(vehicle_id,
                        f"✕ Nachricht nicht verarbeitbar ({type(e).__name__}).")
                    event_log_service.log_event("bmw", "warning",
                        f"Stream-Nachricht nicht verarbeitet: {type(e).__name__}")

            client.on_connect = bei_verbindung
            client.on_subscribe = bei_abonnement
            client.on_disconnect = bei_trennung
            client.on_message = bei_nachricht

            _protokoll_schreiben(vehicle_id,
                f"Verbinde zu {mqtt_host(vehicle_id)}:{mqtt_port(vehicle_id)} als {gcid} …")
            client.connect(mqtt_host(vehicle_id), mqtt_port(vehicle_id), keepalive=60)
            _client[vehicle_id] = client
            client.loop_start()

            wartezeit = 5   # Verbindung steht — Wartezeit zuruecksetzen

            # Laufen lassen, bis der Token ablaeuft oder gestoppt wird
            ablauf = time.time() + 3600 - TOKEN_PUFFER_S
            while z["laeuft"] and time.time() < ablauf:
                time.sleep(5)

            client.loop_stop()
            try:
                client.disconnect()
            except Exception:
                pass

        except Exception as e:
            fehlschlaege_in_folge += 1
            z["verbunden"] = False
            z["fehler"] = f"{type(e).__name__}: {e}"
            _protokoll_schreiben(vehicle_id,
                f"✕ Fehler ({fehlschlaege_in_folge}. in Folge): {z['fehler'][:300]}")
            event_log_service.log_event("bmw", "warning",
                f"Stream-Fehler: {z['fehler'][:200]}")
            time.sleep(wartezeit)
            wartezeit = min(300, wartezeit * 2)   # bis zu fuenf Minuten

    z["verbunden"] = False
    _protokoll_schreiben(vehicle_id, "Stream angehalten.")


# ── Nachrichten ────────────────────────────────────────────────────────────

def verarbeite_nachricht(vehicle_id: int, roh: str) -> dict:
    """Wertet eine Stream-Nachricht dieses Fahrzeugs aus.

    BMW schickt je Ereignis ein JSON mit den geaenderten Datenpunkten.
    Breiten- und Laengengrad kommen manchmal getrennt — deshalb werden
    Teilwerte je Fahrzeug zwischengespeichert, bis beide vorliegen.
    """
    try:
        d = json.loads(roh)
    except Exception:
        return {"ok": False, "grund": "unlesbar"}

    z = _z(vehicle_id)
    z["nachrichten"] += 1
    z["letzte_nachricht"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    werte = d.get("data") or d.get("telematicData") or d
    if not isinstance(werte, dict):
        return {"ok": False, "grund": "kein Datenobjekt"}

    # Die drei Werte, aus denen Fahrten entstehen
    LAT = "vehicle.cabin.infotainment.navigation.currentLocation.latitude"
    LON = "vehicle.cabin.infotainment.navigation.currentLocation.longitude"
    KM = "vehicle.vehicle.travelledDistance"

    def zahl(schluessel):
        eintrag = werte.get(schluessel)
        if eintrag is None:
            return None
        if isinstance(eintrag, dict):
            eintrag = eintrag.get("value")
        try:
            return float(eintrag)
        except (TypeError, ValueError):
            return None

    lat, lon, km = zahl(LAT), zahl(LON), zahl(KM)
    if lat is None and lon is None and km is None:
        return {"ok": True, "relevant": False}

    gepuffert = _lade_puffer(vehicle_id)
    if lat is not None:
        gepuffert["lat"] = lat
    if lon is not None:
        gepuffert["lon"] = lon
    if km is not None:
        gepuffert["km"] = km
    gepuffert["zeit"] = datetime.now().isoformat(timespec="seconds")
    _speichere_puffer(vehicle_id, gepuffert)

    if all(gepuffert.get(k) is not None for k in ("lat", "lon", "km")):
        return _pruefe_fahrt(vehicle_id, gepuffert)

    return {"ok": True, "relevant": True, "unvollstaendig": True}


def _lade_puffer(vehicle_id: int) -> dict:
    roh = bmw_repo.get(vehicle_id)["stream_puffer"]
    try:
        return json.loads(roh) if roh else {}
    except Exception:
        return {}


def _speichere_puffer(vehicle_id: int, d: dict) -> None:
    bmw_repo.set_felder(vehicle_id, stream_puffer=json.dumps(d, ensure_ascii=False))


def _pruefe_fahrt(vehicle_id: int, jetzt: dict) -> dict:
    """Vergleicht mit dem letzten Standpunkt dieses Fahrzeugs und legt bei
    Bedarf eine Fahrt an.

    Eine Fahrt entsteht, wenn der Kilometerstand gestiegen ist. Die
    Position allein genuegt nicht: GPS schwankt auch im Stand um einige
    Meter, und daraus duerfen keine Scheinfahrten werden.
    """
    vorher_roh = bmw_repo.get(vehicle_id)["stream_letzter_stand"]
    try:
        vorher = json.loads(vorher_roh) if vorher_roh else {}
    except Exception:
        vorher = {}

    if not vorher.get("km"):
        bmw_repo.set_felder(vehicle_id,
                            stream_letzter_stand=json.dumps(jetzt, ensure_ascii=False))
        return {"ok": True, "relevant": True, "erster_stand": True}

    distanz = round(float(jetzt["km"]) - float(vorher["km"]), 1)

    if distanz < 1.0:
        return {"ok": True, "relevant": True, "keine_fahrt": True, "distanz": distanz}

    try:
        from repositories import trip_repository
        from services import cardata_service
        user_id = _erster_nutzer()
        if user_id is None:
            return {"ok": False, "grund": "kein Nutzer"}

        start_adresse = cardata_service._koordinaten_text(
            vorher.get("lat"), vorher.get("lon"))
        ziel_adresse = cardata_service._koordinaten_text(
            jetzt.get("lat"), jetzt.get("lon"))

        datum = (jetzt.get("zeit") or "")[:10] or datetime.now().strftime("%Y-%m-%d")

        trip_repository.insert_trip(
            user_id=user_id,
            trip_date=datum,
            start_address=start_adresse or "—",
            end_address=ziel_adresse or "—",
            distance_km=distanz,
            purpose="",
            rate_chosen=0.0,
            vehicle_id=vehicle_id,
            fahrtart="offen")

        bmw_repo.set_felder(vehicle_id,
                            stream_letzter_stand=json.dumps(jetzt, ensure_ascii=False))
        event_log_service.log_event("bmw", "info",
            f"Stream: Fahrt über {distanz} km erkannt "
            f"({vorher.get('km')} → {jetzt.get('km')} km).")
        return {"ok": True, "fahrt": True, "distanz": distanz,
                "von": start_adresse, "nach": ziel_adresse}

    except Exception as e:
        event_log_service.log_event("bmw", "warning",
            f"Stream: Fahrt konnte nicht angelegt werden ({type(e).__name__}).")
        return {"ok": False, "grund": str(e)}


def _erster_nutzer() -> int | None:
    from services import db_service
    conn = db_service.get_connection()
    try:
        z = conn.execute("SELECT id FROM users_config ORDER BY id LIMIT 1").fetchone()
        return z["id"] if z else None
    finally:
        conn.close()
