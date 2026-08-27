"""
Loxone-Statistik-Import — zuverlässige Alternative zum Live-Polling (FA-LS-10-Ergänzung).

Loxone Config kann jeden aufgezeichneten Statistik-Wert (z. B. den Wallbox-
Zählerstand) regulär als CSV exportieren (Format: Datum;Uhrzeit;Wert, siehe
offizielle Loxone-Doku "Statistics"). Dieser Weg ist NICHT live, dafür aber
zuverlässig und ohne die Unsicherheiten des Token-Auth-Handshakes.

Technischer Kniff: Jede Zeile der exportierten CSV wird als ein einzelner
"Poll-Messwert" behandelt und durch dieselbe, bereits getestete Heuristik aus
services/loxone_poll_service.py geschickt (Sessions entstehen bei Anstieg,
werden fortgeschrieben, enden nach anhaltend unveraendertem Wert) — nur dass
die Werte hier aus einer Datei kommen statt aus einer Live-Abfrage.
"""

import csv
import io

from services import loxone_poll_service


def parse_and_import_statistics_csv(csv_text: str, wallbox_id: int, user_id: int, price_per_kwh: float) -> dict:
    """Erwartet eine Loxone-Statistik-Export-CSV mit Spalten Datum;Uhrzeit;Wert
    (Semikolon-getrennt, wie von Loxone Config exportiert). Werte werden als
    Wh interpretiert (Zaehlerstand); bei kWh-Export ggf. vorher x1000 nötig.

    Rückgabe: {'processed': int, 'started': int, 'updated': int, 'closed': int, 'skipped': list}
    """
    reader = csv.reader(io.StringIO(csv_text), delimiter=";")
    counts = {"processed": 0, "started": 0, "updated": 0, "closed": 0, "no_change": 0, "unchanged_open": 0}
    skipped = []

    for i, row in enumerate(reader, start=1):
        if len(row) < 3:
            skipped.append({"line": i, "reason": "Zeile hat weniger als 3 Spalten"})
            continue
        datum, uhrzeit, wert_raw = row[0].strip(), row[1].strip(), row[2].strip()
        if datum.lower() in ("datum", "date"):
            continue  # Kopfzeile überspringen

        try:
            meter_wh = int(round(float(wert_raw.replace(",", "."))))
        except ValueError:
            skipped.append({"line": i, "reason": f"Wert '{wert_raw}' nicht als Zahl lesbar"})
            continue

        # Loxone-Datumsformat ist ueblicherweise DD.MM.YYYY - auf ISO umstellen
        try:
            day, month, year = datum.split(".")
            iso_date = f"{year}-{month.zfill(2)}-{day.zfill(2)}"
        except ValueError:
            skipped.append({"line": i, "reason": f"Datum '{datum}' nicht im erwarteten Format TT.MM.JJJJ"})
            continue

        now = f"{iso_date} {uhrzeit}"
        result = loxone_poll_service.process_poll_reading(
            wallbox_id=wallbox_id, user_id=user_id, current_meter_wh=meter_wh,
            price_per_kwh=price_per_kwh, now=now,
        )
        counts["processed"] += 1
        counts[result["action"]] = counts.get(result["action"], 0) + 1

    return {**counts, "skipped": skipped}
