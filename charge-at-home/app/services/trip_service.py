"""Trip-Service — Geschaeftslogik fuer Fahrtkosten (§ 8.1 Application Layer)."""

STANDARD_RATE = 0.30  # § FA-FK-05: Obergrenze fuer Werbungskosten-Differenz


def compute_amounts(distance_km: float, rate_chosen: float) -> tuple[float, float]:
    """FA-FK-05: Arbeitgeber-Erstattung und Werbungskosten-Differenz.

    Differenz kann nicht negativ werden (falls rate_chosen > 0,30 gewaehlt wuerde).
    """
    employer_amount = round(distance_km * rate_chosen, 2)
    diff_amount = round(max(0.0, distance_km * (STANDARD_RATE - rate_chosen)), 2)
    return employer_amount, diff_amount


def trip_to_api_dict(trip: dict) -> dict:
    """Wandelt eine Fahrt fuer die Oberflaeche um.

    Bei privaten Fahrten sind Erstattung UND Werbungskosten zwingend null:
    Sie gehoeren ins Fahrtenbuch (Lueckenlosigkeit), duerfen aber weder
    erstattet noch steuerlich geltend gemacht werden. Die in der Datenbank
    hinterlegte Formel rechnet blind (Distanz x 0,30) und wuerde sonst auch
    fuer Privatfahrten einen Werbungskostenbetrag ausweisen."""
    fahrtart = trip.get("fahrtart") or "dienstlich"
    if fahrtart == "dienstlich":
        employer_amount, diff_amount = compute_amounts(
            trip["distance_km"], trip["rate_chosen"])
    else:
        employer_amount, diff_amount = 0.0, 0.0
    return {
        "id": trip["id"],
        "trip_date": trip["trip_date"],
        "start_address": trip["start_address"],
        "end_address": trip["end_address"],
        "distance_km": trip["distance_km"],
        "purpose": trip["purpose"],
        "rate_chosen": trip["rate_chosen"],
        "fahrtart": fahrtart,
        "employer_amount_eur": employer_amount,
        "diff_amount_eur": diff_amount,
    }
