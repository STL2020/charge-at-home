"""Generisches Key-Value-Settings-Repository (§ 5.14)."""

from services.db_service import get_connection

DEFAULTS = {
    "loxone_poll_interval_seconds": "60",
    "home_address": "",
    # FA-LS-BMF-01: BMF-Schreiben-Referenz auf dem Ladestrom-Beleg — bewusst
    # standardmäßig AUS (siehe FA-LS-07-Historie: Beleg sollte auf expliziten
    # Wunsch des Auftraggebers neutral/fallunabhängig bleiben, ohne
    # Rechtstext). Jetzt als bewusst OPTIONALE Ergänzung, nicht als Zwang.
    "show_bmf_reference": "0",
    # FA-LS-06-V3: neues Feld fuer den Ladebeleg (Referenzvorlage "eCharge@Home"
    # zeigt "Dienstfahrzeug: BMW E-Fahrzeug (Dienstwagen)") — wir speichern
    # keine Fahrzeugdaten automatisch, der Nutzer traegt es einmalig ein.
    "vehicle_description": "",
}


def get_setting(key: str) -> str:
    conn = get_connection()
    try:
        row = conn.execute("SELECT value FROM app_settings WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else DEFAULTS.get(key, "")
    finally:
        conn.close()


def set_setting(key: str, value: str) -> None:
    conn = get_connection()
    try:
        conn.execute(
            """INSERT INTO app_settings (key, value) VALUES (?, ?)
               ON CONFLICT(key) DO UPDATE SET value = excluded.value""",
            (key, value),
        )
        conn.commit()
    finally:
        conn.close()
