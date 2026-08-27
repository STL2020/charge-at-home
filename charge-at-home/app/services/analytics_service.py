"""
Analytics-Service — FA-DASH-01 bis 04 (Monatsverlauf, Kennzahlen, Vergleichsrechner).
"""

import calendar
from datetime import datetime

from repositories import session_repository, trip_repository

MONTH_NAMES_DE = ["Jan", "Feb", "Mär", "Apr", "Mai", "Jun", "Jul", "Aug", "Sep", "Okt", "Nov", "Dez"]


def _session_kwh_cost(session: dict) -> tuple[float, float]:
    if session.get("meter_stop_wh") is None:
        return 0.0, 0.0
    kwh = (session["meter_stop_wh"] - session["meter_start_wh"]) / 1000.0
    return kwh, kwh * session["price_per_kwh"]


def _filter_sessions(sessions: list, classification: str | None) -> list:
    if not classification:
        return sessions
    return [s for s in sessions if s.get("classification") == classification]


def monthly_kwh_cost(user_id: int, months: int = 6, wallbox_id: int | None = None,
                      classification: str | None = None) -> list[dict]:
    """FA-DASH-01/02: kWh und Kosten je Monat, letzte `months` Monate inkl. aktuellem Monat."""
    today = datetime.now()
    results = []
    for i in range(months - 1, -1, -1):
        year, month = today.year, today.month - i
        while month <= 0:
            month += 12
            year -= 1
        start = f"{year:04d}-{month:02d}-01"
        last_day = calendar.monthrange(year, month)[1]
        end = f"{year:04d}-{month:02d}-{last_day:02d}"

        sessions = _filter_sessions(
            session_repository.list_sessions(user_id, period_start=start, period_end=end, wallbox_id=wallbox_id),
            classification,
        )
        kwh_sum = cost_sum = 0.0
        for s in sessions:
            kwh, cost = _session_kwh_cost(s)
            kwh_sum += kwh
            cost_sum += cost

        results.append({
            "label": f"{MONTH_NAMES_DE[month - 1]}",
            "year": year,
            "kwh": round(kwh_sum, 2),
            "cost": round(cost_sum, 2),
        })
    return results


def period_summary(user_id: int, period_start: str | None, period_end: str | None,
                    wallbox_id: int | None = None, classification: str | None = None) -> dict:
    """FA-DASH-04: Kennzahlentabelle."""
    sessions = _filter_sessions(
        session_repository.list_sessions(user_id, period_start, period_end, wallbox_id), classification,
    )
    total_kwh = total_cost = 0.0
    session_count = 0
    for s in sessions:
        kwh, cost = _session_kwh_cost(s)
        if s.get("meter_stop_wh") is not None:
            total_kwh += kwh
            total_cost += cost
            session_count += 1

    avg_price = round(total_cost / total_kwh, 4) if total_kwh > 0 else 0.0
    # Nur Dienstfahrten: Private Fahrten gehoeren ins Fahrtenbuch, nicht in
    # die Kosten- und Erstattungsauswertung.
    trips = trip_repository.list_trips(user_id, period_start, period_end, nur_dienstlich=True)

    return {
        "avg_price_per_kwh": avg_price,
        "total_cost": round(total_cost, 2),
        "total_kwh": round(total_kwh, 2),
        "session_count": session_count,
        "trip_count": len(trips),
    }


def compare_pauschale_vs_real(total_kwh: float, pauschale_rate: float, real_rate: float) -> dict:
    """FA-DASH-03: Pauschale-vs-Real-Vergleichsrechner."""
    pauschale_amount = round(total_kwh * pauschale_rate, 2)
    real_amount = round(total_kwh * real_rate, 2)
    if real_amount < pauschale_amount:
        cheaper = "real"
    elif pauschale_amount < real_amount:
        cheaper = "pauschale"
    else:
        cheaper = "gleich"
    return {"pauschale_amount": pauschale_amount, "real_amount": real_amount, "cheaper": cheaper}
