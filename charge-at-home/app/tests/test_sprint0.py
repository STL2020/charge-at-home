"""
Sprint-0-Tests — Abnahmekriterien aus Pflichtenheft § 9 / § 10.

Ausfuehren mit:  cd app && python -m pytest tests/test_sprint0.py -v
"""

import os
import sqlite3
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from services import i18n_service
from services import license_service


def test_i18n_de_and_en_load():
    de = i18n_service.t("app.name", "de")
    en = i18n_service.t("app.name", "en")
    assert de == "eCharge@Home"
    assert en == "eCharge@Home"
    assert i18n_service.t("nav.ladesessions", "de") == "Ladesessions"
    assert i18n_service.t("nav.ladesessions", "en") == "Charging Sessions"


def test_i18n_missing_key_is_visible_not_silent():
    result = i18n_service.t("this.key.does.not.exist", "de")
    assert result == "[[this.key.does.not.exist]]"


def test_i18n_unsupported_language_falls_back_to_de():
    i18n_service.reload_cache()
    result = i18n_service.t("app.name", "fr")
    assert result == "eCharge@Home"


def test_demo_session_limit_enforced():
    assert license_service.session_limit_reached(19, "demo") is False
    assert license_service.session_limit_reached(20, "demo") is True
    assert license_service.session_limit_reached(999, "demo") is True


def test_licensed_status_has_no_limit():
    assert license_service.session_limit_reached(999999, "licensed") is False


def test_watermark_only_in_demo():
    assert license_service.watermark_for("demo") == "DEMO"
    assert license_service.watermark_for("licensed") is None


def test_schema_creates_all_six_tables():
    schema_path = os.path.join(os.path.dirname(__file__), "..", "..", "data", "schema.sql")
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
        db_path = tmp.name
    try:
        conn = sqlite3.connect(db_path)
        with open(schema_path, "r", encoding="utf-8") as f:
            conn.executescript(f.read())
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        expected = {
            "users_config", "wallboxes", "charging_sessions",
            "trips", "audit_log", "documents",
        }
        assert expected.issubset(tables), f"Fehlende Tabellen: {expected - tables}"
        conn.close()
    finally:
        os.remove(db_path)


def test_abrechnungsfall_check_constraint():
    """FA-SYS-04: nur A/B/C sind gueltige Werte."""
    schema_path = os.path.join(os.path.dirname(__file__), "..", "..", "data", "schema.sql")
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
        db_path = tmp.name
    try:
        conn = sqlite3.connect(db_path)
        with open(schema_path, "r", encoding="utf-8") as f:
            conn.executescript(f.read())
        conn.execute(
            "INSERT INTO users_config (name, abrechnungsfall) VALUES (?, ?)",
            ("Test", "C"),
        )
        conn.commit()
        raised = False
        try:
            conn.execute(
                "INSERT INTO users_config (name, abrechnungsfall) VALUES (?, ?)",
                ("Test2", "X"),
            )
            conn.commit()
        except sqlite3.IntegrityError:
            raised = True
        assert raised, "CHECK-Constraint fuer abrechnungsfall greift nicht"
        conn.close()
    finally:
        os.remove(db_path)
