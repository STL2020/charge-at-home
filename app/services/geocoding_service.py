import math
"""
Geocoding-Service — FA-FK-02.

Photon (Komoot) fuer fehlertolerante Adresssuche (Fuzzy-Search, kein API-Key),
OSRM fuer echte Fahrstrecke in km (kein API-Key, oeffentliche Demo-Server).

Workflow:
  1. Photon: Adresse → GPS-Koordinaten (toleriert Tippfehler)
  2. OSRM:   Koordinaten Start+Ziel → Fahrstrecke in Metern → km

Beide Dienste kostenlos, kein Account erforderlich.
"""

import json
import urllib.parse
import urllib.request

PHOTON_URL = "https://photon.komoot.io/api/"
OSRM_URL   = "https://router.project-osrm.org/route/v1/driving/"
USER_AGENT  = "ChargeAtHomeBillingEngine/1.0"
TIMEOUT     = 10


def geocode_photon(query: str) -> tuple[tuple[float, float] | None, str]:
    """Photon: Adresse → (lon, lat) mit Tippfehlertoleranz. Kein API-Key."""
    params = urllib.parse.urlencode({"q": query, "limit": 1, "lang": "de"})
    url = f"{PHOTON_URL}?{params}"
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except Exception as exc:
        return None, f"Photon nicht erreichbar: {exc}"
    features = data.get("features", [])
    if not features:
        return None, f'Adresse nicht gefunden: "{query}"'
    coords = features[0]["geometry"]["coordinates"]  # [lon, lat]
    return (coords[0], coords[1]), "OK"


def route_distance_osrm(start_lon: float, start_lat: float,
                         end_lon: float,   end_lat: float,
                         alternatives: bool = False) -> tuple[float | None, str, list]:
    """OSRM: echte Fahrstrecke in km. Bei alternatives=True bis zu 3 Routen."""
    alt_param = "&alternatives=3" if alternatives else ""
    url = f"{OSRM_URL}{start_lon},{start_lat};{end_lon},{end_lat}?overview=false{alt_param}"
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except Exception as exc:
        return None, f"OSRM nicht erreichbar: {exc}", []
    routes = data.get("routes", [])
    if not routes:
        return None, "OSRM hat keine Route gefunden.", []
    primary_km = math.ceil(routes[0]["distance"] / 100.0) / 10.0  # aufrunden auf 0,1 km
    alt_routes = []
    for r in routes[1:]:
        alt_routes.append({
            "distance_km": math.ceil(r["distance"] / 100.0) / 10.0,
            "duration_min": round(r["duration"] / 60.0, 0)
        })
    return primary_km, "OK", alt_routes


def estimate_distance_km(start_address: str, end_address: str,
                          alternatives: bool = False) -> tuple[float | None, str, list]:
    """Vollständiger Workflow: Adresse → Koordinaten (Photon) → Fahrstrecke (OSRM)."""
    start_coords, start_err = geocode_photon(start_address)
    if start_coords is None:
        return None, f"Start-Adresse: {start_err}", []
    end_coords, end_err = geocode_photon(end_address)
    if end_coords is None:
        return None, f"Ziel-Adresse: {end_err}", []
    return route_distance_osrm(start_coords[0], start_coords[1],
                                end_coords[0],   end_coords[1], alternatives)


# Aufgeloeste Koordinaten werden gemerkt: Beim Import einer Fahrtenliste
# liegen viele Punkte dicht beieinander (dieselbe Einfahrt, derselbe
# Parkplatz). Ohne Zwischenspeicher waeren das dutzende Anfragen fuer
# dieselbe Adresse.
_adress_speicher: dict[tuple, str] = {}


def adresse_aus_koordinaten(lat: float, lon: float) -> str:
    """Koordinaten → lesbare Adresse (Rueckwaertssuche).

    BMW CarData liefert nur Laengen- und Breitengrad. Ohne Aufloesung steht
    im Fahrtenbuch '50.57912, 7.22698' — fuer einen Beleg unbrauchbar, weil
    niemand daran den Zweck der Fahrt erkennt.

    Bei einem Fehler wird die Koordinate zurueckgegeben; eine Fahrt ohne
    Adresse ist immer noch besser als eine verworfene Fahrt.
    """
    if lat is None or lon is None:
        return ""

    # Auf fuenf Nachkommastellen runden — etwa ein Meter genau. Punkte, die
    # dichter beieinander liegen, teilen sich den Eintrag.
    schluessel = (round(float(lat), 4), round(float(lon), 4))
    if schluessel in _adress_speicher:
        return _adress_speicher[schluessel]

    fallback = f"{float(lat):.5f}, {float(lon):.5f}"
    params = urllib.parse.urlencode({"lat": lat, "lon": lon, "lang": "de"})
    url = f"{PHOTON_URL.rstrip('/')}/reverse?{params}"
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            daten = json.loads(resp.read().decode("utf-8"))
    except Exception:
        return fallback

    merkmale = daten.get("features") or []
    if not merkmale:
        return fallback

    e = merkmale[0].get("properties", {})

    # Strasse mit Hausnummer, dann Ort — das ist die Form, die auf einem
    # Beleg erwartet wird.
    strasse = e.get("street") or e.get("name") or ""
    nummer = e.get("housenumber") or ""
    ort = e.get("city") or e.get("town") or e.get("village") or e.get("county") or ""
    plz = e.get("postcode") or ""

    teile = []
    if strasse:
        teile.append(f"{strasse} {nummer}".strip())
    if ort:
        teile.append(f"{plz} {ort}".strip())
    if not teile:
        # Kein Strassenname — etwa auf der Autobahn. Dann wenigstens
        # die Gegend nennen.
        for k in ("name", "district", "state", "country"):
            if e.get(k):
                teile.append(e[k])
                break

    ergebnis = ", ".join(teile) if teile else fallback

    # Liegt der Punkt in der eigenen Strasse, die hinterlegte Wohnadresse
    # einsetzen. GPS ist auf wenige Meter genau — das reicht nicht fuer die
    # Hausnummer, und dann steht die des Nachbarn im Fahrtenbuch.
    eigene = _eigene_adresse_wenn_passend(strasse, ort)
    if eigene:
        ergebnis = eigene

    _adress_speicher[schluessel] = ergebnis
    return ergebnis


def _strassenkern(text: str) -> str:
    """Strassenname ohne Hausnummer, Zusaetze und Schreibweisen.

    'Lange Fuhr 7' und 'Lange Fuhr 5' ergeben beide 'langefuhr' — damit
    laesst sich vergleichen, ohne an der Hausnummer zu scheitern.
    """
    import re
    if not text:
        return ""
    # Nur bis zum ersten Komma: 'Lange Fuhr 7, 53424 Remagen' soll denselben
    # Kern ergeben wie 'Lange Fuhr'. Ohne den Schnitt zoege der Ortsname mit
    # ein und der Vergleich schlaege fehl.
    t = text.split(",")[0].lower()
    t = re.sub(r"\d+", "", t)                      # Hausnummern weg
    t = t.replace("straße", "str").replace("strasse", "str")
    return re.sub(r"[^a-zäöüß]+", "", t)[:20]


def _eigene_adresse_wenn_passend(strasse: str, ort: str) -> str:
    """Gibt die hinterlegte Wohnadresse zurueck, wenn der Punkt dort liegt.

    Sonst leer. Verglichen wird nur die Strasse, nicht die Hausnummer.
    """
    try:
        from repositories import settings_repository
        heim = (settings_repository.get_setting("heim_adresse") or "").strip()
        if not heim:
            from services import db_service
            conn = db_service.get_connection()
            try:
                z = conn.execute(
                    "SELECT home_address FROM persons "
                    "WHERE home_address IS NOT NULL AND TRIM(home_address) != '' "
                    "LIMIT 1").fetchone()
                heim = (z["home_address"] or "").strip() if z else ""
            finally:
                conn.close()
        if not heim:
            return ""

        kern_gefunden = _strassenkern(strasse)
        kern_heim = _strassenkern(heim)
        if not kern_gefunden or kern_gefunden != kern_heim:
            return ""

        # Ort mitpruefen, sofern er in beiden bekannt ist: Strassennamen
        # wiederholen sich zwischen Staedten. 'Musterweg' in Bonn ist nicht
        # dasselbe wie 'Musterweg' in Remagen.
        import re
        heim_ort = ""
        teile_heim = [t.strip() for t in heim.split(",")]
        if len(teile_heim) > 1:
            # Postleitzahl abtrennen, nur der Ortsname zaehlt
            heim_ort = re.sub(r"^\s*\d{4,5}\s*", "", teile_heim[-1]).strip().lower()

        if heim_ort and ort and heim_ort not in ort.lower() and ort.lower() not in heim_ort:
            return ""

        return heim
    except Exception:
        pass
    return ""


def autocomplete_address(query: str, limit: int = 5) -> list[dict]:
    """Photon-Autocomplete: gibt Liste von Adressvorschlägen zurück.
    Für die Live-Suche im Fahrteneingabe-Formular."""
    if len(query.strip()) < 2:
        return []
    params = urllib.parse.urlencode({"q": query, "limit": limit, "lang": "de"})
    url = f"{PHOTON_URL}?{params}"
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except Exception:
        return []
    results = []
    for f in data.get("features", []):
        props = f.get("properties", {})
        parts = [props.get(k, "") for k in ("name", "street", "housenumber", "city", "country") if props.get(k)]
        label = ", ".join(parts) if parts else query
        results.append({
            "label": label,
            "lon": f["geometry"]["coordinates"][0],
            "lat": f["geometry"]["coordinates"][1],
        })
    return results
