"""
Loxone-Poller — FA-LS-10, Hintergrundprozess.

WICHTIGER HINWEIS ZUM TESTSTATUS: Diese Datei orchestriert den HTTP-Transport
(services/loxone_api_service.py, ungetestet gegen echte Hardware) und die
Session-Erkennungslogik (services/loxone_poll_service.py, vollstaendig
getestet). Der Prozess selbst konnte deshalb nicht end-to-end gegen einen
echten Miniserver getestet werden — nur die beiden Bausteine einzeln.

Laeuft als dritter, eigenstaendiger Prozess (NFA-09), analog zum OCPP-Server.
Poll-Intervall: 60 Sekunden (Kompromiss aus Aktualitaet und Netzwerklast,
respektiert implizit auch etwaige Nominatim-aehnliche Rate-Limits des
Miniservers, siehe § 6.4).
"""

import os
import sys
import time
from datetime import datetime

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from services import crypto_service, loxone_api_service, loxone_poll_service, loxone_wallbox2_service, event_log_service, loxone_log_import_service
from repositories import loxone_config_repository, wallbox_repository, settings_repository
from services.db_service import get_connection

DEFAULT_POLL_INTERVAL_SECONDS = 60
# Aktuell ungenutzt (Log-Abgleich laeuft bewusst bei jedem Zyklus, siehe
# _is_reconcile_due) — als Erweiterungspunkt belassen, falls die Logdatei
# irgendwann so gross wird, dass ein selteneres Intervall sinnvoller ist.
RECONCILE_INTERVAL_HOURS = 24


def _get_poll_interval() -> int:
    """Liest das Poll-Intervall aus den Einstellungen (§ 5.14) — kann jetzt in
    der Oberflaeche geaendert werden, statt fest im Code zu stehen. Rueckmeldung
    des Auftraggebers: "in welchem Sekundentakt liest er, koennte man es
    einstellen?"."""
    try:
        return max(10, int(settings_repository.get_setting("loxone_poll_interval_seconds")))
    except (ValueError, TypeError):
        return DEFAULT_POLL_INTERVAL_SECONDS


def _get_single_user():
    conn = get_connection()
    try:
        return conn.execute("SELECT * FROM users_config ORDER BY id LIMIT 1").fetchone()
    finally:
        conn.close()


def _is_reconcile_due(wallbox_id: int) -> bool:
    """Der Log-Abgleich ist bewusst guenstig genug (einfacher HTTP-GET,
    duplikat-sicherer Import), um bei JEDEM Poll-Zyklus zu laufen — nicht
    nur einmal taeglich. Das bringt Sessions praktisch in Echtzeit in die
    Datenbank, statt bis zu 24h zu warten. Diese Funktion bleibt als
    Erweiterungspunkt bestehen (z. B. falls die Logdatei irgendwann so gross
    wird, dass ein selteneres Intervall sinnvoller ist)."""
    return True


def reconcile_wallbox_log(wb: dict, user_id: int, default_price: float, password: str) -> None:
    """Fuehrt den vollstaendigen Log-Abgleich fuer eine Wallbox durch — bei
    jedem Poll-Zyklus (siehe _is_reconcile_due). Holt IMMER die komplette
    Datei und reicht sie an den duplikat-sicheren Importer weiter — laesst
    sich damit beliebig oft und nach beliebig langen Ausfallzeiten gefahrlos
    wiederholen."""
    if not _is_reconcile_due(wb["id"]):
        return

    loxone_config_repository.ensure_log_reconcile_row(wb["id"])
    state = loxone_config_repository.get_log_reconcile_state(wb["id"])
    log_path = state["log_path"] if state else "/dev/fsget/log/wallbox.log"

    log_text, err = loxone_log_import_service.fetch_log_file_http(
        wb["loxone_host"], wb["loxone_username"], password, log_path,
    )
    if log_text is None:
        event_log_service.log_event(
            "loxone_api", "warning",
            f"'{wb['name']}': Log-Abgleich fehlgeschlagen ({err}) — nächster Versuch im nächsten Poll-Zyklus."
        )
        return  # last_reconciled_at bewusst NICHT aktualisieren, damit es beim naechsten Mal erneut versucht wird

    result = loxone_log_import_service.import_full_log_text(wb["id"], user_id, log_text, default_price)
    loxone_config_repository.mark_log_reconciled(wb["id"], result["imported"])
    if result["imported"] > 0:
        event_log_service.log_event(
            "loxone_api", "info",
            f"'{wb['name']}': Log-Abgleich — {result['imported']} neue Session(en) aus der "
            f"Miniserver-eigenen Log-Datei übernommen."
        )
    # Bei 0 importierten Sessions bewusst KEIN Protokolleintrag — sonst würde
    # bei jedem 60s-Zyklus eine Zeile "0 importiert" das Protokoll fluten.


def _backoff_seconds_for(consecutive_failures: int) -> int:
    """Exponentielles Backoff, gedeckelt bei 1 Stunde: 0 Fehlversuche -> sofort,
    1 -> 60s, 2 -> 120s, 3 -> 300s, 4 -> 600s, 5 -> 1800s, ab 6 -> 3600s."""
    schedule = {0: 0, 1: 60, 2: 120, 3: 300, 4: 600, 5: 1800}
    return schedule.get(consecutive_failures, 3600)


def _is_auth_backoff_active(wallbox_id: int) -> tuple[bool, int]:
    """Prueft, ob dieser Wallbox aktuell wegen wiederholter Fehlversuche
    pausiert werden soll (siehe Modul-Docstring / schema.sql § 5.16).
    Rueckgabe: (pausiert?, aktuelle Fehlversuchs-Anzahl)."""
    from datetime import datetime, timedelta
    state = loxone_config_repository.get_auth_backoff_state(wallbox_id)
    if state is None or state.get("consecutive_failures", 0) == 0:
        return False, 0
    failures = state["consecutive_failures"]
    if not state.get("last_attempt_at"):
        return False, failures
    try:
        last_attempt = datetime.strptime(state["last_attempt_at"], "%Y-%m-%d %H:%M:%S")
    except (ValueError, TypeError):
        return False, failures
    wait_needed = _backoff_seconds_for(failures)
    still_waiting = datetime.now() - last_attempt < timedelta(seconds=wait_needed)
    return still_waiting, failures


def poll_once() -> None:
    user = _get_single_user()
    if user is None:
        return  # noch kein Setup abgeschlossen

    wallboxes = loxone_config_repository.list_loxone_api_wallboxes_with_config()
    for wb in wallboxes:
        if not wb.get("loxone_uuid"):
            continue  # keine UUID konfiguriert, siehe Einstellungen

        # Manueller Not-Aus (Ruecksprache: Miniserver war durch wiederholte
        # Fehlversuche gesperrt, Nutzer brauchte eine SOFORTIGE, garantierte
        # Moeglichkeit, jeden weiteren Verbindungsversuch zu stoppen, statt
        # sich auf Backoff-Wartezeiten verlassen zu muessen).
        backoff_state = loxone_config_repository.get_auth_backoff_state(wb["id"])
        if backoff_state and backoff_state.get("manually_paused"):
            wallbox_repository.set_status(wb["id"], "pausiert (manuell)")
            continue

        # Ausfallsicherheit (siehe Pflichtenheft-Changelog): bei wiederholt
        # fehlgeschlagenen Login-Versuchen (z. B. falsches Passwort) NICHT
        # bedingungslos jeden Zyklus erneut versuchen — genau das hat den
        # Miniserver einmal dazu gebracht, die IP-Adresse wegen "too many
        # failed login attempts" zu sperren. Stattdessen schrittweise laenger
        # warten, bis der Nutzer das Passwort korrigiert hat.
        paused, failures = _is_auth_backoff_active(wb["id"])
        if paused:
            wallbox_repository.set_status(wb["id"], "fehler: zugangsdaten (pausiert)")
            continue

        try:
            password = crypto_service.decrypt(wb["loxone_password_encrypted"])
        except Exception as exc:
            wallbox_repository.set_status(wb["id"], "fehler: entschlüsselung")
            # cryptography.fernet.InvalidToken hat bewusst KEINEN Text (str(exc) == ""),
            # das fuehrte zu unlesbaren "()"-Protokolleintraegen. Klartext-Erklaerung
            # stattdessen, da dies der mit Abstand haeufigste Fall ist (Schluessel
            # passt nicht mehr zum gespeicherten Passwort — siehe Pflichtenheft).
            reason = str(exc) or "Schlüssel passt nicht zum gespeicherten Passwort (InvalidToken)"
            event_log_service.log_event(
                "loxone_api", "error",
                f"'{wb['name']}': Entschlüsselung fehlgeschlagen — {reason}. Lösung: Bearbeiten öffnen, Passwort neu eingeben, speichern."
            )
            continue

        # Primaerer Weg fuer Wallbox2-Bausteine: /all abfragen fuer die
        # Live-Anzeige (Vc/Cac/Cp — reine Zahlen). Abrechnungsrelevante
        # Sessions entstehen NICHT hier, sondern ausschliesslich ueber den
        # vollstaendigen Log-Datei-Abgleich (reconcile_wallbox_log), der
        # nachweislich jeden Uebergang lueckenlos erfasst — siehe Modul-
        # Docstring von loxone_wallbox2_service.py fuer die Entscheidungs-
        # historie.
        all_values, msg = loxone_api_service.get_wallbox_all_values(
            wb["loxone_host"], wb["loxone_username"], password, wb["loxone_uuid"],
        )
        if all_values is not None:
            loxone_config_repository.record_auth_success(wb["id"])
            wallbox_repository.set_status(wb["id"], "charging" if all_values.get("Cac") == "1" else "online")
            result = loxone_wallbox2_service.process_all_values(
                wb["id"], user["id"], all_values, user["default_kwh_price"],
            )
            # DIAGNOSE statt Verschleierung: fehlen die fuer Wallbox2 erwarteten
            # Felder komplett, war das bisher als "?" bzw. "None" kaschiert,
            # OHNE erkennbar zu machen, dass etwas nicht stimmt (zu Recht als
            # Fehler bemaengelt). Stattdessen jetzt eine klare Warnung mit den
            # TATSAECHLICH erhaltenen Feldnamen — das zeigt sofort, ob z. B.
            # die konfigurierte UUID auf einen falschen (Nicht-Wallbox2-)
            # Baustein zeigt, statt still falsche Werte anzuzeigen.
            missing = [k for k in ("Cp", "Vc", "Cac") if k not in all_values]
            if missing:
                event_log_service.log_event(
                    "loxone_api", "warning",
                    f"'{wb['name']}': erwartete Wallbox2-Felder fehlen in der Antwort: {missing}. "
                    f"Tatsächlich erhaltene Felder ({len(all_values)}): {sorted(all_values.keys())}. "
                    f"Mögliche Ursache: konfigurierte UUID zeigt nicht auf den Wallbox2-Baustein selbst. "
                    f"Bitte UUID in den Wallbox-Einstellungen prüfen (\"Struktur laden\" erneut ausführen)."
                )
            else:
                cp = all_values.get("Cp")
                event_log_service.log_event(
                    "loxone_api", "info",
                    f"'{wb['name']}': Live-Sync ok, aktuelle Leistung {cp} kW, "
                    f"verbunden={all_values.get('Vc')}, lädt_aktiv={all_values.get('Cac')}"
                    + (f" — {result['lcl_info']}" if result.get("lcl_info") else "")
                )
            reconcile_wallbox_log(wb, user["id"], user["default_kwh_price"], password)
            continue

        event_log_service.log_event("loxone_api", "warning", f"'{wb['name']}': /all fehlgeschlagen ({msg}), versuche Einzelwert-Fallback")

        # Fallback: einzelnen Wert lesen (fuer einfachere Bausteine wie
        # Virtuelle Eingaenge, kein Wallbox2)
        value, msg = loxone_api_service.get_value_basic_auth(
            wb["loxone_host"], wb["loxone_username"], password, wb["loxone_uuid"],
        )
        if value is None:
            opener = loxone_api_service.authenticate(wb["loxone_host"], wb["loxone_username"], password)
            if opener is None:
                failures = loxone_config_repository.record_auth_failure(wb["id"])
                wallbox_repository.set_status(wb["id"], "offline")
                wait_s = _backoff_seconds_for(failures)
                event_log_service.log_event(
                    "loxone_api", "error",
                    f"'{wb['name']}': Verbindung fehlgeschlagen ({msg}). "
                    f"Fehlversuch {failures} in Folge — nächster Versuch erst in {wait_s}s "
                    f"(Ausfallsicherheit gegen Miniserver-Sperren bei falschem Passwort)."
                )
                continue
            loxone_config_repository.record_auth_success(wb["id"])
            wallbox_repository.set_status(wb["id"], "online")
            value = loxone_api_service.get_value(opener, wb["loxone_host"], wb["loxone_uuid"])
            if value is None:
                event_log_service.log_event("loxone_api", "warning", f"'{wb['name']}': Verbunden, aber kein Wert lesbar")
                continue
        else:
            loxone_config_repository.record_auth_success(wb["id"])
            wallbox_repository.set_status(wb["id"], "online")

        result = loxone_poll_service.process_poll_reading(
            wallbox_id=wb["id"], user_id=user["id"], current_meter_wh=int(value),
            price_per_kwh=user["default_kwh_price"],
        )
        if result["action"] in ("started", "updated"):
            wallbox_repository.set_status(wb["id"], "charging")
        elif result["action"] == "closed":
            wallbox_repository.set_status(wb["id"], "ready")


def main() -> None:
    print(f"Loxone-Poller gestartet.")
    event_log_service.log_event("system", "info", "Loxone-Poller gestartet")
    while True:
        interval = _get_poll_interval()
        try:
            poll_once()
        except Exception as exc:
            print(f"[{datetime.now()}] Fehler im Poll-Zyklus: {exc}")
            event_log_service.log_event("loxone_api", "error", f"Unerwarteter Fehler im Poll-Zyklus: {exc}")
        time.sleep(interval)


if __name__ == "__main__":
    main()
