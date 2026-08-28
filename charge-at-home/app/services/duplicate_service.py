"""Erkennung von Doppelabrechnungen: überlappende Sessions aus verschiedenen Quellen.
Kritisch für § 3 Nr. 50 EStG — wenn Wallbox-MID und Fahrzeug-App denselben
Ladezeitraum erfassen, würde die Ladung doppelt abgerechnet."""
from datetime import datetime


def _parse(ts: str):
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            return datetime.strptime(ts, fmt)
        except (ValueError, TypeError):
            continue
    return None


def _overlaps(a_start, a_end, b_start, b_end) -> bool:
    """Prüft ob zwei Zeiträume sich überschneiden."""
    if not all([a_start, a_end, b_start, b_end]):
        return False
    return a_start < b_end and b_start < a_end


def _session_metrics(s: dict) -> dict:
    """Ermittelt kWh, Betrag (€), Preis/kWh und Dauer (min) einer Session."""
    try:
        from services.billing_service import compute_energy_and_amount
        kwh, amount = compute_energy_and_amount(s)
    except Exception:
        # Fallback: direkt aus Zählerständen
        start_wh = s.get("meter_start_wh") or 0
        stop_wh = s.get("meter_stop_wh") or start_wh
        kwh = round((stop_wh - start_wh) / 1000.0, 2)
        rate = s.get("price_per_kwh") or s.get("kwh_price") or 0.34
        amount = round(kwh * rate, 2)
    st = _parse(s.get("start_timestamp", ""))
    en = _parse(s.get("end_timestamp", "")) or st
    dur_min = int((en - st).total_seconds() // 60) if (st and en) else 0
    rate = round(amount / kwh, 4) if kwh else 0.0
    return {"kwh": round(kwh, 2), "amount": round(amount, 2), "rate": rate, "duration_min": dur_min}


def _session_info(s: dict) -> dict:
    m = _session_metrics(s)
    return {
        "id": s.get("id"), "source": s.get("source", ""),
        "start": s.get("start_timestamp"), "end": s.get("end_timestamp"),
        "kwh": m["kwh"], "amount": m["amount"], "rate": m["rate"], "duration_min": m["duration_min"],
    }


def find_overlapping_sessions(sessions: list) -> list:
    """Findet Paare von Sessions aus UNTERSCHIEDLICHEN Quellen, deren Ladezeiträume
    sich überschneiden. Jeder Konflikt enthält Vergleichsdaten (kWh, €, Preis, Dauer)."""
    conflicts = []
    parsed = []
    for s in sessions:
        st = _parse(s.get("start_timestamp", ""))
        en = _parse(s.get("end_timestamp", "")) or st
        parsed.append((s, st, en))

    for i in range(len(parsed)):
        s1, st1, en1 = parsed[i]
        for j in range(i + 1, len(parsed)):
            s2, st2, en2 = parsed[j]
            src1 = s1.get("source", "")
            src2 = s2.get("source", "")
            app_sources = {"bmw_app"}
            wallbox_sources = {"loxone_api", "ocpp"}
            is_cross = ((src1 in app_sources and src2 in wallbox_sources) or
                        (src1 in wallbox_sources and src2 in app_sources))
            if is_cross and _overlaps(st1, en1, st2, en2):
                info_a = _session_info(s1)
                info_b = _session_info(s2)
                # Empfehlung: Wallbox MID-Sensor ist der rechtssichere Messnachweis
                wallbox_side = "a" if src1 in wallbox_sources else "b"
                conflicts.append({
                    "session_a": info_a,
                    "session_b": info_b,
                    "recommended": wallbox_side,          # Wallbox = §3 Nr.50-konform
                    "higher_amount": "a" if info_a["amount"] >= info_b["amount"] else "b",
                })
    return conflicts


def resolve_all_keep_wallbox(sessions: list) -> list:
    """Gibt die IDs der Fahrzeug-App-Sessions zurück, die bei Konflikten
    entfernt werden sollten (Wallbox behalten = MID-Messnachweis)."""
    to_remove = set()
    conflicts = find_overlapping_sessions(sessions)
    for c in conflicts:
        app_side = c["session_b"] if c["recommended"] == "a" else c["session_a"]
        to_remove.add(app_side["id"])
    return list(to_remove)


def resolve_all_keep_higher(sessions: list) -> list:
    """Gibt die IDs der Sessions mit dem NIEDRIGEREN Betrag zurück (zum Entfernen),
    behält also je Konflikt den höheren Betrag."""
    to_remove = set()
    conflicts = find_overlapping_sessions(sessions)
    for c in conflicts:
        lower = c["session_b"] if c["higher_amount"] == "a" else c["session_a"]
        to_remove.add(lower["id"])
    return list(to_remove)


def has_mixed_sources(sessions: list) -> bool:
    """True wenn Sessions aus App UND Wallbox gemischt vorkommen."""
    srcs = {s.get("source", "") for s in sessions}
    return "bmw_app" in srcs and bool(srcs & {"loxone_api", "ocpp"})
