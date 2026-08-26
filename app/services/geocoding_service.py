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
