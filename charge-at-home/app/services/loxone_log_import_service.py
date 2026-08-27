"""
Log-Abgleich-Service — Ausfallsicherheit (siehe Pflichtenheft-Changelog).

Live-Polling (/all + Lcl, siehe loxone_wallbox2_service.py) sieht bei jeder
Abfrage nur den JEWEILS LETZTEN abgeschlossenen Ladevorgang. Laeuft die App
(oder der Poller-Prozess) waehrend zwei aufeinanderfolgenden Ladevorgaengen
nicht durchgehend, wird der aeltere davon stillschweigend uebersprungen —
Rueckmeldung des Auftraggebers: "wir muessen sicherstellen, dass die Daten
auch wirklich immer zur Verfuegung stehen, auch wenn die Applikation mal
nicht laeuft".

Die Loesung: Ein Logger-Baustein in Loxone Config schreibt jede Lcl-Aenderung
UNABHAENGIG von unserer App fortlaufend in eine Datei auf dem Miniserver
selbst (bestaetigter, funktionierender Pfad: /dev/fsget/log/wallbox.log,
abrufbar per HTTP-GET mit Basic-Auth). Dieser Service liest die komplette
Datei periodisch neu ein und gleicht JEDEN darin enthaltenen Eintrag mit der
Datenbank ab (Duplikat-sicher ueber wallbox_id + exakten Start-Zeitstempel) —
selbst wenn unsere App tagelang nicht lief, holt der naechste erfolgreiche
Abgleich alles nach, was der Miniserver in der Zwischenzeit selbst
mitgeschrieben hat.
"""

import base64
import urllib.request
import urllib.error

from services import loxone_wallbox2_service
from repositories import session_repository


def fetch_log_file_http(host: str, username: str, password: str, log_path: str) -> tuple[str | None, str]:
    """Laedt die komplette Log-Datei per HTTP-GET mit Basic-Auth. Rueckgabe:
    (Text, Fehlermeldung). Text ist None bei Fehler."""
    path = log_path if log_path.startswith("/") else "/" + log_path
    url = f"http://{host}{path}"
    credentials = base64.b64encode(f"{username}:{password}".encode("utf-8")).decode("ascii")
    req = urllib.request.Request(url, headers={"Authorization": f"Basic {credentials}"})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            raw = resp.read()
    except urllib.error.HTTPError as exc:
        return None, f"HTTP-Fehler {exc.code}: {exc.reason}"
    except Exception as exc:
        return None, f"Verbindung fehlgeschlagen: {exc}"

    for encoding in ("utf-8", "latin-1"):
        try:
            return raw.decode(encoding), ""
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace"), ""


def import_full_log_text(wallbox_id: int, user_id: int, log_text: str, default_price: float) -> dict:
    """Parst JEDE Zeile der Log-Datei, die dem Lcl-Format entspricht, und legt
    fehlende, bereits abgeschlossene Sessions an. Bereits vorhandene Sessions
    (gleiche wallbox_id + exakter Start-Zeitstempel) werden uebersprungen —
    macht wiederholte Aufrufe mit derselben (wachsenden) Datei sicher."""
    imported = 0
    skipped_duplicate = 0
    unparseable = 0
    skipped_connect_only = 0

    for line in log_text.splitlines():
        line = line.strip()
        if not line:
            continue
        parsed = loxone_wallbox2_service.parse_last_charge_log(line)
        if parsed is None:
            # "Fahrzeug verbunden"-Zeilen (Session-Start, keine Abrechnungsdaten)
            # und andere Nicht-Lcl-Zeilen werden bewusst uebersprungen, nicht
            # als Fehler gezaehlt.
            if "Fahrzeug verbunden" in line:
                skipped_connect_only += 1
            else:
                unparseable += 1
            continue

        from datetime import datetime, timedelta
        end_dt = datetime.strptime(parsed["timestamp"], "%Y-%m-%d %H:%M:%S")
        start_dt = end_dt - timedelta(seconds=parsed["duration_s"])
        start_str = start_dt.strftime("%Y-%m-%d %H:%M:%S")

        if session_repository.session_exists_at_end(wallbox_id, parsed["timestamp"]):
            skipped_duplicate += 1
            continue

        energy_wh = round(parsed["energy_kwh"] * 1000)
        session_repository.insert_session(
            wallbox_id=wallbox_id, user_id=user_id, source="loxone_api",
            start_timestamp=start_str, end_timestamp=parsed["timestamp"],
            meter_start_wh=0, meter_stop_wh=energy_wh,
            price_per_kwh=default_price, status="closed",
        )
        imported += 1

    return {
        "imported": imported,
        "skipped_duplicate": skipped_duplicate,
        "skipped_connect_only": skipped_connect_only,
        "unparseable": unparseable,
        "total_lines": len(log_text.splitlines()),
    }
