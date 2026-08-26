"""
i18n-Service — zentrale Uebersetzungs-Lookup-Funktion.

Pflichtenheft-Referenz: FA-SYS-01, NFA-04.
Jeder sichtbare UI-Text MUSS ueber t(key, lang) aufgerufen werden.
Kein hartcodierter Anzeigetext im UI-Code (app/pages/*.py, app/app.py).
"""

import json
import os

_CACHE: dict[str, dict] = {}
_I18N_DIR = os.path.join(os.path.dirname(__file__), "..", "i18n")

SUPPORTED_LANGUAGES = ("de",)   # einsprachig: siehe Pflichtenheft S7-03


def _load(lang: str) -> dict:
    if lang not in SUPPORTED_LANGUAGES:
        lang = "de"
    if lang not in _CACHE:
        path = os.path.join(_I18N_DIR, f"{lang}.json")
        with open(path, "r", encoding="utf-8") as f:
            _CACHE[lang] = json.load(f)
    return _CACHE[lang]


def t(key: str, lang: str = "de") -> str:
    """Liefert den uebersetzten Text zu key in Sprache lang.

    Faellt auf den Key selbst zurueck, falls die Uebersetzung fehlt,
    damit fehlende Keys im UI sofort auffallen statt einen Fehler zu werfen.
    """
    data = _load(lang)
    return data.get(key, f"[[{key}]]")


def get_all_translations(lang: str) -> dict:
    """Liefert das vollstaendige Uebersetzungswoerterbuch einer Sprache.

    Wird von app.py genutzt, um alle Sprachen als JSON-Block ins Template
    einzubetten (fuer die clientseitige i18n-Umschaltung ohne Seiten-Reload).
    """
    return dict(_load(lang))


def reload_cache() -> None:
    """Fuer Tests: erzwingt erneutes Laden der JSON-Dateien."""
    _CACHE.clear()
