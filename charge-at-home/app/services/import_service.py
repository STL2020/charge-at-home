"""
CSV-Import-Service — FA-LS-01.

Format exakt nach Pflichtenheft § 6.2:
Datum;Startzeit;Endzeit;ZaehlerstandStart_Wh;ZaehlerstandEnde_Wh;RFID_Tag;WallboxID
2026-08-01;18:32;22:47;125430;138920;TAG001;WB-GARAGE-1

Zeilen mit fehlendem Pflichtfeld werden uebersprungen und im Importprotokoll
aufgelistet, nicht stillschweigend verworfen (§ 6.2).
"""

import csv
import io
from datetime import datetime

from repositories import wallbox_repository, session_repository

REQUIRED_FIELDS = [
    "Datum", "Startzeit", "Endzeit",
    "ZaehlerstandStart_Wh", "ZaehlerstandEnde_Wh", "RFID_Tag", "WallboxID",
]


def parse_and_import(csv_text: str, user_id: int, default_price: float) -> dict:
    reader = csv.DictReader(io.StringIO(csv_text), delimiter=";")

    if reader.fieldnames is None or any(f not in reader.fieldnames for f in REQUIRED_FIELDS):
        return {
            "imported": 0,
            "skipped": [{"line": 1, "reason": f"Header unvollstaendig, erwartet: {';'.join(REQUIRED_FIELDS)}"}],
        }

    imported = 0
    skipped = []

    for i, row in enumerate(reader, start=2):  # Zeile 1 = Header
        missing = [f for f in REQUIRED_FIELDS if not row.get(f)]
        if missing:
            skipped.append({"line": i, "reason": f"Fehlende Felder: {', '.join(missing)}"})
            continue
        try:
            start_dt = datetime.strptime(f"{row['Datum']} {row['Startzeit']}", "%Y-%m-%d %H:%M")
            end_dt = datetime.strptime(f"{row['Datum']} {row['Endzeit']}", "%Y-%m-%d %H:%M")
            meter_start = int(row["ZaehlerstandStart_Wh"])
            meter_stop = int(row["ZaehlerstandEnde_Wh"])
        except (ValueError, KeyError) as exc:
            skipped.append({"line": i, "reason": f"Ungueltiges Format: {exc}"})
            continue

        if meter_stop < meter_start:
            skipped.append({"line": i, "reason": "Zaehlerstand Ende kleiner als Start (Anomalie, siehe NFA-Zaehlerueberlauf)"})
            continue

        wallbox_id = wallbox_repository.get_or_create_wallbox(row["WallboxID"], source_type="csv")
        session_repository.insert_session(
            wallbox_id=wallbox_id,
            user_id=user_id,
            source="csv",
            start_timestamp=start_dt.strftime("%Y-%m-%d %H:%M:%S"),
            end_timestamp=end_dt.strftime("%Y-%m-%d %H:%M:%S"),
            meter_start_wh=meter_start,
            meter_stop_wh=meter_stop,
            price_per_kwh=default_price,
            rfid_tag=row["RFID_Tag"],
            classification=None,
            status="closed",
        )
        imported += 1

    return {"imported": imported, "skipped": skipped}
