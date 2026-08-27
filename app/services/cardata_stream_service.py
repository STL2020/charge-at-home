"""BMW CarData MQTT-Streaming.

Der entscheidende Unterschied zum Abrufen: BMW schickt Aenderungen von
sich aus, sobald sie eintreten — ohne Tageskontingent. Damit wird jede
Fahrt erfasst, auch die kurze am Morgen und die um fuenf Uhr.

Beim Abrufen alle 30 Minuten verschmelzen zwei Fahrten, die dicht
aufeinander folgen, zu einer. Genau das war das Problem, das sich
steuerlich nicht sauber aufloesen liess.

ZUGANG
------
    Host:      customer.streaming-cardata.bmwgroup.com
    Port:      9000
    Benutzer:  die GCID des BMW-Kontos
    Passwort:  der id_token aus dem OAuth-Verfahren (rund 1 Stunde gueltig)
    Thema:     <gcid>/<vin>

Der id_token laeuft staendig ab und muss erneuert werden. Deshalb baut
dieser Dienst die Verbindung vor Ablauf von sich aus neu auf.

TOPIC-KLARSTELLUNG (28.08.)
----------------------------
Kurzzeitig auf "nur die VIN" geaendert, weil das Portal im Feld "Topic"
ausschliesslich die VIN anzeigt. Das war falsch: BMWs eigene Anleitung
(Kapitel "Streaming", Abschnitt 4.3.2/4.4) stellt klar, dass das
Portal-Feld "Topic" nur einen Baustein liefert und das tatsaechliche
MQTT-Thema durch den Anwender selbst als "username/topic" (hier:
gcid/vin) zusammengesetzt werden muss. Zurueckgesetzt auf <gcid>/<vin>.

VORAUSSETZUNGEN beim Anwender
-----------------------------
    1. Im BMW-Portal einen CarData-Client anlegen
    2. Das Fahrzeug muss als Hauptnutzer zugeordnet sein
    3. Die gewuenschten Datenpunkte im Container auswaehlen

Ohne Schritt 1 gibt es keine Client-ID und keinen Stream.

GETESTET
--------
Die Nachrichtenverarbeitung und Fahrterkennung sind mit simulierten
Daten geprueft. Die MQTT-Verbindung selbst nicht — dafuer braucht es ein
echtes Konto. Fehler beim Verbinden landen im Protokoll.
"""
from __future__ import annotations

import json
import threading
import time
from datetime import datetime, timedelta

from repositories import settings_repository
import services.event_log_service as event_log_service

MQTT_HOST_STANDARD = "customer.streaming-cardata.bmwgroup.com"
MQTT_PORT_STANDARD = 9000


def mqtt_host() -> str:
    """Host für die Stream-Verbindung — einstellbar statt fest verdrahtet.

    Grund: Host und Port stehen im BMW-Portal als Teil der individuellen
    Streaming-Zugangsdaten. Zwar sind sie bei allen bekannten Konten bisher
    identisch, aber fest im Code zu verankern, was eigentlich Kontodaten
    sind, ist derselbe Fehler wie bei Client-ID und VIN — deshalb
    einstellbar, mit dem bekannten Wert als Vorbelegung."""
    return settings_repository.get_setting("cardata_stream_host") or MQTT_HOST_STANDARD


def mqtt_port() -> int:
    wert = settings_repository.get_setting("cardata_stream_port")
    try:
        return int(wert) if wert else MQTT_PORT_STANDARD
    except (TypeError, ValueError):
        return MQTT_PORT_STANDARD


def setze_verbindung(host: str, port: int) -> None:
    settings_repository.set_setting("cardata_stream_host", (host or "").strip())
    settings_repository.set_setting("cardata_stream_port", str(int(port)) if port else "")

# Der id_token gilt rund eine Stunde. Zehn Minuten vorher wird erneuert,
# damit die Verbindung nicht mitten in einer Fahrt abreisst.
TOKEN_PUFFER_S = 600

# Zustand des Hintergrund-Threads. Bewusst modulweit: Es darf nur eine
# Verbindung geben, sonst zaehlt BMW mehrere Sitzungen.
_zustand = {
    "laeuft": False,
    "verbunden": False,
    "abonniert": None,   # None = noch keine Rückmeldung, True/False danach
    "letzte_nachricht": None,
    "fehler": "",
    "nachrichten": 0,
    "verbindungen": 0,
}
_client = None
_thread = None

# ── Protokoll fuer die Oberflaeche ──────────────────────────────────────────
# Ringpuffer im Arbeitsspeicher, damit sich jede Verbindungsstufe direkt in
# der App verfolgen laesst — kein Putty, kein docker exec noetig. Bewusst
# nicht in der Datenbank: reine Laufzeit-Diagnose, kein Datensatz mit
# Aufbewahrungspflicht, und ein Ringpuffer im Speicher genuegt dafuer.
_PROTOKOLL_MAX = 300
_protokoll: list[str] = []


def _protokoll_schreiben(zeile: str) -> None:
    zeitstempel = datetime.now().strftime("%H:%M:%S")
    _protokoll.append(f"[{zeitstempel}] {zeile}")
    if len(_protokoll) > _PROTOKOLL_MAX:
        del _protokoll[:len(_protokoll) - _PROTOKOLL_MAX]


def protokoll_lesen() -> list[str]:
    return list(_protokoll)


def protokoll_leeren() -> None:
    _protokoll.clear()


def status() -> dict:
    """Zustand fuer die Anzeige.

    'aktiv' ist die gespeicherte Einstellung, 'laeuft' der tatsaechliche
    Thread-Zustand dieses Prozesses — beide koennen auseinanderfallen,
    naemlich direkt nach einem Neustart, bevor die Anwendung den Stream
    wieder aufgenommen hat. Genau das soll hier sichtbar werden, statt
    schweigend als "wird aufgebaut" durchzugehen.
    """
    fehler = _zustand["fehler"]
    if (_zustand["laeuft"] and not _zustand["verbunden"] and not fehler
            and _zustand.get("versuch_seit")):
        try:
            wartet_s = (datetime.now() - datetime.fromisoformat(
                _zustand["versuch_seit"])).total_seconds()
        except Exception:
            wartet_s = 0
        if wartet_s > 45:
            fehler = ("Seit über 45 Sekunden keine Verbindung zustande "
                      "gekommen. Meist Netzwerk (Port 9000 ausgehend "
                      "blockiert?) oder ein abgelaufener Token — im "
                      "Zweifel Haken entfernen und neu setzen.")
    # Verbunden, aber BMW hat das Abonnement abgelehnt: sieht sonst identisch
    # aus wie "verbunden und wartet auf Daten" — genau der Unterschied, der
    # bisher nirgends sichtbar war.
    if _zustand["verbunden"] and _zustand.get("abonniert") is False and not fehler:
        fehler = ("Verbunden, aber BMW hat das Abonnement des Themas "
                  "abgelehnt (siehe Protokoll für den genauen Code). Meist "
                  "falsche GCID/VIN-Kombination oder das Fahrzeug ist nicht "
                  "als Hauptnutzer zugeordnet.")
    return {
        "aktiv": aktiviert(),
        "laeuft": _zustand["laeuft"],
        "verbunden": _zustand["verbunden"],
        "abonniert": _zustand.get("abonniert"),
        "letzte_nachricht": _zustand["letzte_nachricht"],
        "nachrichten": _zustand["nachrichten"],
        "verbindungen": _zustand.get("verbindungen", 0),
        "fehler": fehler,
        "host": mqtt_host(),
        "port": mqtt_port(),
    }


def aktiviert() -> bool:
    return (settings_repository.get_setting("cardata_stream_aktiv") or "0") == "1"


def setze_aktiv(an: bool) -> None:
    settings_repository.set_setting("cardata_stream_aktiv", "1" if an else "0")
    if an:
        starte()
    else:
        stoppe()


# ── Verbindung ─────────────────────────────────────────────────────────────

def starte() -> dict:
    """Startet den Stream im Hintergrund.

    Gibt sofort zurueck — der Verbindungsaufbau laeuft im Thread. Ob er
    geklappt hat, steht danach in status().
    """
    global _thread

    if _zustand["laeuft"]:
        return {"ok": True, "meldung": "Stream läuft bereits."}
    # Ein noch laufender Thread aus einem frueheren Start muss erst enden,
    # sonst gibt es zwei Verbindungen — BMW zaehlt die als zwei Sitzungen.
    if _thread is not None and _thread.is_alive():
        return {"ok": True, "meldung": "Vorheriger Stream wird noch beendet."}

    try:
        import paho.mqtt.client  # noqa: F401
    except ImportError:
        _zustand["fehler"] = ("Die MQTT-Bibliothek fehlt. Sie ist Bestandteil "
                              "des Docker-Abbilds — bei lokaler Installation "
                              "mit 'pip install paho-mqtt' nachrüsten.")
        return {"ok": False, "meldung": _zustand["fehler"]}

    gcid = settings_repository.get_setting("cardata_gcid") or ""
    vin = settings_repository.get_setting("cardata_vin") or ""
    if not gcid or not vin:
        _zustand["fehler"] = ("GCID oder Fahrgestellnummer fehlen. Beide "
                              "entstehen bei der Anmeldung.")
        return {"ok": False, "meldung": _zustand["fehler"]}

    _zustand["laeuft"] = True
    _zustand["fehler"] = ""
    _zustand["versuch_seit"] = datetime.now().isoformat(timespec="seconds")
    _protokoll_schreiben(f"Stream wird gestartet (GCID {gcid}, VIN {vin}) …")
    _thread = threading.Thread(target=_schleife, args=(gcid, vin), daemon=True)
    _thread.start()
    event_log_service.log_event("bmw", "info", "CarData-Stream gestartet.")
    return {"ok": True, "meldung": "Stream gestartet."}


def stoppe() -> dict:
    global _client
    _zustand["laeuft"] = False
    _zustand["verbunden"] = False
    if _client is not None:
        try:
            _client.disconnect()
        except Exception:
            pass
        _client = None
    event_log_service.log_event("bmw", "info", "CarData-Stream angehalten.")
    return {"ok": True}


def _schleife(gcid: str, vin: str) -> None:
    """Haelt die Verbindung offen und erneuert den Token rechtzeitig.

    Faengt jeden Fehler ab: Ein Aussetzer bei BMW darf den Thread nicht
    beenden, sonst laeuft der Stream bis zum Neustart nicht mehr.
    """
    global _client
    import paho.mqtt.client as mqtt
    from services import cardata_auth_service as auth

    wartezeit = 5      # waechst bei wiederholten Fehlern

    while _zustand["laeuft"]:
        try:
            token = auth.hole_id_token()
            if not token:
                _zustand["fehler"] = "Kein gültiger Token — bitte neu anmelden."
                time.sleep(60)
                continue

            client = mqtt.Client(
                mqtt.CallbackAPIVersion.VERSION2,
                client_id=f"echarge-{vin[-6:]}",
                protocol=mqtt.MQTTv5,
            )
            client.username_pw_set(gcid, token)
            client.tls_set()

            # Zusammensetzung nach offizieller BMW-Doku (Kapitel "Streaming"):
            # "You can create a subscription by using your username and
            # topic such as 'username/topic'" — das im Portal angezeigte
            # Topic-Feld (nur die VIN) ist ein Baustein, nicht das fertige
            # MQTT-Thema. Siehe TOPIC-KLARSTELLUNG im Modul-Docstring.
            thema = f"{gcid}/{vin}"

            # Nur die erste Verbindung protokollieren. MQTT baut nach jeder
            # Unterbrechung selbst neu auf — jedes Mal eine Meldung zu
            # schreiben fuellt das Protokoll, ohne etwas auszusagen.
            erstverbindung = {"erledigt": False}

            def bei_verbindung(c, userdata, flags, rc, props=None):
                if rc == 0:
                    _zustand["verbunden"] = True
                    _zustand["abonniert"] = None   # zuruecksetzen, Antwort steht noch aus
                    _zustand["fehler"] = ""
                    _zustand["verbindungen"] = _zustand.get("verbindungen", 0) + 1
                    _protokoll_schreiben(f"✓ Verbunden (Code {rc}). Abonniere Thema {thema} …")
                    c.subscribe(thema, qos=1)
                    if not erstverbindung["erledigt"]:
                        erstverbindung["erledigt"] = True
                        event_log_service.log_event("bmw", "info",
                            f"Stream verbunden — Thema {thema}")
                else:
                    _zustand["verbunden"] = False
                    _zustand["fehler"] = f"Verbindung abgelehnt (Code {rc})"
                    _protokoll_schreiben(f"✕ Verbindung abgelehnt (Code {rc}).")

            def bei_abonnement(c, userdata, mid, reason_codes, properties=None):
                # BUG BEHOBEN (28.08.): Frueher wurde subscribe() aufgerufen,
                # ohne je die SUBACK-Antwort zu pruefen. "Verbunden" konnte
                # also true sein, waehrend BMW das Thema im Stillen ablehnt —
                # ohne dass das irgendwo sichtbar wurde. Reason-Code >= 128
                # bedeutet abgelehnt (siehe MQTTv5-Spezifikation).
                abgelehnt = any(getattr(code, "value", code) is not None
                               and getattr(code, "value", code) >= 128
                               for code in reason_codes)
                _zustand["abonniert"] = not abgelehnt
                codes = [getattr(code, "value", code) for code in reason_codes]
                if abgelehnt:
                    _protokoll_schreiben(f"✕ Abonnement abgelehnt (Code {codes}).")
                    event_log_service.log_event("bmw", "warning",
                        f"Stream: Abonnement von Thema '{thema}' abgelehnt "
                        f"(Code {codes}). Vermutlich GCID/VIN-Kombination "
                        f"falsch oder Fahrzeug nicht als Hauptnutzer "
                        f"zugeordnet.")
                else:
                    _protokoll_schreiben(f"✓ Abonnement bestätigt (Code {codes}). "
                                        f"Warte auf Nachrichten von BMW …")
                    event_log_service.log_event("bmw", "info",
                        f"Stream: Abonnement von Thema '{thema}' bestätigt.")

            def bei_trennung(c, userdata, flags, rc, props=None):
                _zustand["verbunden"] = False
                _protokoll_schreiben(f"Verbindung getrennt (Code {rc}).")

            def bei_nachricht(c, userdata, msg):
                try:
                    roh = msg.payload.decode("utf-8")
                    # Ganz bewusst gekuerzt, nicht weggelassen: die Ansicht in
                    # der App soll auch bei vielen Nachrichten benutzbar
                    # bleiben, aber der Inhalt muss erkennbar sein.
                    vorschau = roh if len(roh) <= 1000 else roh[:1000] + " …(gekürzt)"
                    _protokoll_schreiben(f"★ Nachricht auf '{msg.topic}': {vorschau}")
                    verarbeite_nachricht(roh)
                except Exception as e:
                    _protokoll_schreiben(f"✕ Nachricht nicht verarbeitbar ({type(e).__name__}).")
                    event_log_service.log_event("bmw", "warning",
                        f"Stream-Nachricht nicht verarbeitet: {type(e).__name__}")

            client.on_connect = bei_verbindung
            client.on_subscribe = bei_abonnement
            client.on_disconnect = bei_trennung
            client.on_message = bei_nachricht

            _protokoll_schreiben(f"Verbinde zu {mqtt_host()}:{mqtt_port()} als {gcid} …")
            client.connect(mqtt_host(), mqtt_port(), keepalive=60)
            _client = client
            client.loop_start()

            wartezeit = 5   # Verbindung steht — Wartezeit zuruecksetzen

            # Laufen lassen, bis der Token ablaeuft oder gestoppt wird
            ablauf = time.time() + 3600 - TOKEN_PUFFER_S
            while _zustand["laeuft"] and time.time() < ablauf:
                time.sleep(5)

            client.loop_stop()
            try:
                client.disconnect()
            except Exception:
                pass

        except Exception as e:
            _zustand["verbunden"] = False
            _zustand["fehler"] = f"{type(e).__name__}: {e}"
            _protokoll_schreiben(f"✕ Fehler: {_zustand['fehler'][:300]}")
            event_log_service.log_event("bmw", "warning",
                f"Stream-Fehler: {_zustand['fehler'][:200]}")
            time.sleep(wartezeit)
            wartezeit = min(300, wartezeit * 2)   # bis zu fuenf Minuten

    _zustand["verbunden"] = False
    _protokoll_schreiben("Stream angehalten.")


# ── Nachrichten ────────────────────────────────────────────────────────────

def verarbeite_nachricht(roh: str) -> dict:
    """Wertet eine Stream-Nachricht aus.

    BMW schickt je Ereignis ein JSON mit den geaenderten Datenpunkten.
    Breiten- und Laengengrad kommen manchmal getrennt — deshalb werden
    Teilwerte zwischengespeichert, bis beide vorliegen.

    Gibt zurueck, was erkannt wurde. Getrennt von der MQTT-Schicht, damit
    sich die Logik ohne Verbindung pruefen laesst.
    """
    try:
        d = json.loads(roh)
    except Exception:
        return {"ok": False, "grund": "unlesbar"}

    _zustand["nachrichten"] += 1
    _zustand["letzte_nachricht"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

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
        # BMW verschachtelt teils {"value": 123, "timestamp": "..."}
        if isinstance(eintrag, dict):
            eintrag = eintrag.get("value")
        try:
            return float(eintrag)
        except (TypeError, ValueError):
            return None

    lat, lon, km = zahl(LAT), zahl(LON), zahl(KM)
    if lat is None and lon is None and km is None:
        return {"ok": True, "relevant": False}

    # Teilwerte sammeln: Kommen Breite und Laenge getrennt, waere jede
    # einzeln wertlos.
    gepuffert = _lade_puffer()
    if lat is not None:
        gepuffert["lat"] = lat
    if lon is not None:
        gepuffert["lon"] = lon
    if km is not None:
        gepuffert["km"] = km
    gepuffert["zeit"] = datetime.now().isoformat(timespec="seconds")
    _speichere_puffer(gepuffert)

    # Vollstaendig? Dann als Standpunkt werten
    if all(gepuffert.get(k) is not None for k in ("lat", "lon", "km")):
        return _pruefe_fahrt(gepuffert)

    return {"ok": True, "relevant": True, "unvollstaendig": True}


def _lade_puffer() -> dict:
    roh = settings_repository.get_setting("cardata_stream_puffer") or ""
    try:
        return json.loads(roh) if roh else {}
    except Exception:
        return {}


def _speichere_puffer(d: dict) -> None:
    settings_repository.set_setting("cardata_stream_puffer",
                                    json.dumps(d, ensure_ascii=False))


def _pruefe_fahrt(jetzt: dict) -> dict:
    """Vergleicht mit dem letzten Standpunkt und legt bei Bedarf eine Fahrt an.

    Eine Fahrt entsteht, wenn der Kilometerstand gestiegen ist. Die
    Position allein genuegt nicht: GPS schwankt auch im Stand um einige
    Meter, und daraus duerfen keine Scheinfahrten werden.
    """
    from services import cardata_service

    vorher_roh = settings_repository.get_setting("cardata_stream_letzter") or ""
    try:
        vorher = json.loads(vorher_roh) if vorher_roh else {}
    except Exception:
        vorher = {}

    # Ersten Standpunkt nur merken
    if not vorher.get("km"):
        settings_repository.set_setting("cardata_stream_letzter",
                                        json.dumps(jetzt, ensure_ascii=False))
        return {"ok": True, "relevant": True, "erster_stand": True}

    distanz = round(float(jetzt["km"]) - float(vorher["km"]), 1)

    # Mindestens ein Kilometer: Darunter ist es Rangieren oder ein
    # Ablesefehler, keine Fahrt.
    if distanz < 1.0:
        return {"ok": True, "relevant": True, "keine_fahrt": True,
                "distanz": distanz}

    # Fahrt anlegen
    try:
        from repositories import trip_repository
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
            vehicle_id=_fahrzeug_id(),
            fahrtart="offen")

        settings_repository.set_setting("cardata_stream_letzter",
                                        json.dumps(jetzt, ensure_ascii=False))
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


def _fahrzeug_id() -> int | None:
    """Fahrzeug zur hinterlegten Fahrgestellnummer."""
    vin = (settings_repository.get_setting("cardata_vin") or "").strip()
    if not vin:
        return None
    from services import db_service
    conn = db_service.get_connection()
    try:
        spalten = [r["name"] for r in conn.execute("PRAGMA table_info(vehicles)")]
        if "vin" not in spalten:
            return None
        z = conn.execute("SELECT id FROM vehicles WHERE vin = ? LIMIT 1",
                         (vin,)).fetchone()
        return z["id"] if z else None
    except Exception:
        return None
    finally:
        conn.close()
