"""Abrechnungs-Konfigurator / Entscheidungs-Engine.

Vergleicht die vier steuerlichen Wege, ein Fahrzeug abzurechnen, und gibt eine
Handlungsempfehlung mit Ampel-Logik:

  Firmenwagen (Arbeitgeber-Fahrzeug):
    Typ A  — Pauschalversteuerung (1 % / 0,5 % / 0,25 %-Regel)
    Typ B  — Fahrtenbuchversteuerung (echter Privatanteil)

  Privat-PKW (Arbeitnehmer-Fahrzeug):
    Typ C1 — Gesetzliche Pauschale 0,30 €/km (§ 9 EStG), kein Fahrtenbuch
    Typ C2 — Vollkostenrechnung (R 9.5 LStR), Fahrtenbuch zwingend

Zusätzlich die PV-Sonderlogik: geladener Heimstrom aus eigener PV wird auf der
Kostenseite nur mit den Opportunitätskosten (entgangene Einspeisevergütung)
angesetzt, auf der Erstattungsseite aber mit der BMF-Pauschale (0,34 €/kWh)
vergütet — die Differenz ist steuerfreier Reingewinn.

Alle Funktionen sind rein (keine DB-Zugriffe), damit sie isoliert testbar sind
und 1:1 die Frontend-Rechnung spiegeln.
"""
from __future__ import annotations

GESETZLICHE_PAUSCHALE = 0.30   # €/km, § 9 EStG
BMF_LADESTROM = 0.34           # €/kWh, BMF-Pauschale Heimladung
STANDARD_AFA_JAHRE = 6         # lineare AfA bei Kauf


# ───────────────────────── Privat-PKW (Typ C) ──────────────────────────────

def echter_km_satz(k_gesamt: float, d_gesamt: float) -> float:
    """Tatsächlicher Vollkostensatz €/km = Jahres-Gesamtkosten / Gesamtfahrleistung."""
    if d_gesamt <= 0:
        return 0.0
    return k_gesamt / d_gesamt


def typ_c1_pauschale(d_dienst: float, ag_erstattung: float, steuersatz: float) -> dict:
    """Typ C1 — gesetzliche Pauschale 0,30 €/km."""
    diff = max(0.0, GESETZLICHE_PAUSCHALE - ag_erstattung)
    werbungskosten = d_dienst * diff
    erstattung = werbungskosten * steuersatz
    return {
        "typ": "C1",
        "name": "Privat-PKW · Pauschale (0,30 €/km)",
        "km_satz": round(GESETZLICHE_PAUSCHALE, 4),
        "diff_pro_km": round(diff, 4),
        "werbungskosten": round(werbungskosten, 2),
        "steuererstattung": round(erstattung, 2),
        "dokumentationsaufwand": "gering",
        "fahrtenbuch_noetig": False,
    }


def typ_c2_vollkosten(k_gesamt: float, d_gesamt: float, d_dienst: float,
                       ag_erstattung: float, steuersatz: float) -> dict:
    """Typ C2 — Vollkostenrechnung mit echtem km-Satz (Fahrtenbuch nötig)."""
    satz = echter_km_satz(k_gesamt, d_gesamt)
    diff = max(0.0, satz - ag_erstattung)
    werbungskosten = d_dienst * diff
    erstattung = werbungskosten * steuersatz
    return {
        "typ": "C2",
        "name": "Privat-PKW · Vollkosten (Fahrtenbuch)",
        "km_satz": round(satz, 4),
        "diff_pro_km": round(diff, 4),
        "werbungskosten": round(werbungskosten, 2),
        "steuererstattung": round(erstattung, 2),
        "dokumentationsaufwand": "hoch",
        "fahrtenbuch_noetig": True,
    }


# ───────────────────────── Firmenwagen (Typ A/B) ───────────────────────────

def antrieb_satz(blp: float, antrieb: str) -> float:
    """Versteuerungssatz für den geldwerten Vorteil je Antriebsart.
    BEV bis 95.000 € BLP: 0,25 %; darüber 0,5 %. PHEV: 0,5 %. Verbrenner: 1,0 %."""
    a = (antrieb or "").lower()
    if a in ("elektro", "bev", "e"):
        return 0.0025 if blp <= 95000 else 0.005
    if a in ("phev", "hybrid", "plug-in-hybrid"):
        return 0.005
    return 0.01  # Verbrenner


def typ_a_firmenwagen_pauschal(blp: float, antrieb: str, entfernung_km: float,
                                steuersatz: float) -> dict:
    """Typ A — Firmenwagen mit 1%/0,5%/0,25%-Pauschalversteuerung.
    Geldwerter Vorteil p. M. = BLP × Antriebssatz  +  BLP × 0,03 % × Entfernung-km.
    Netto-Belastung des Arbeitnehmers = GWV × Grenzsteuersatz.
    (Ergebnis als Jahres-KOSTEN, negativ dargestellt = Belastung.)"""
    satz = antrieb_satz(blp, antrieb)
    gwv_nutzung = blp * satz
    gwv_arbeitsweg = blp * 0.0003 * entfernung_km
    gwv_monat = gwv_nutzung + gwv_arbeitsweg
    gwv_jahr = gwv_monat * 12
    netto_belastung_jahr = gwv_jahr * steuersatz
    return {
        "typ": "A",
        "name": "Firmenwagen · Pauschal (1 %-Regel)",
        "antrieb_satz_pct": round(satz * 100, 3),
        "gwv_monat": round(gwv_monat, 2),
        "gwv_jahr": round(gwv_jahr, 2),
        "netto_belastung_jahr": round(netto_belastung_jahr, 2),
        # Als "Ertrag" negativ: ein Firmenwagen kostet den AN Steuer statt zu erstatten
        "steuererstattung": round(-netto_belastung_jahr, 2),
        "dokumentationsaufwand": "keiner",
        "fahrtenbuch_noetig": False,
    }


def typ_b_firmenwagen_fahrtenbuch(k_gesamt_ag: float, d_gesamt: float,
                                   d_privat: float, d_arbeit: float,
                                   steuersatz: float) -> dict:
    """Typ B — Firmenwagen mit Fahrtenbuchversteuerung.
    Privatanteil-Quote = (Privat-km + Arbeitsweg-km) / Gesamt-km.
    Tatsächlicher GWV p. a. = AG-Gesamtkosten × Privatquote.
    Netto-Belastung = GWV_echt × Grenzsteuersatz."""
    quote_privat = (d_privat + d_arbeit) / d_gesamt if d_gesamt > 0 else 0.0
    gwv_echt = k_gesamt_ag * quote_privat
    netto_belastung_jahr = gwv_echt * steuersatz
    return {
        "typ": "B",
        "name": "Firmenwagen · Fahrtenbuch",
        "privatquote_pct": round(quote_privat * 100, 1),
        "gwv_jahr": round(gwv_echt, 2),
        "netto_belastung_jahr": round(netto_belastung_jahr, 2),
        "steuererstattung": round(-netto_belastung_jahr, 2),
        "dokumentationsaufwand": "hoch",
        "fahrtenbuch_noetig": True,
    }


# ───────────────────────── PV-Sonderlogik ──────────────────────────────────

def pv_marge(kwh_pv_jahr: float, opportunitaetskosten: float = 0.08,
             bmf_erstattung: float = BMF_LADESTROM) -> dict:
    """Steuerfreier Reingewinn aus PV-Heimladung.
    Marge/kWh = BMF-Erstattung − entgangene Einspeisevergütung.
    Nur relevant, wenn der Arbeitgeber Ladestrom pauschal erstattet."""
    marge_kwh = max(0.0, bmf_erstattung - opportunitaetskosten)
    gewinn_jahr = marge_kwh * kwh_pv_jahr
    return {
        "marge_pro_kwh": round(marge_kwh, 4),
        "kwh_pv_jahr": round(kwh_pv_jahr, 0),
        "opportunitaetskosten": round(opportunitaetskosten, 4),
        "bmf_erstattung": round(bmf_erstattung, 4),
        "gewinn_jahr": round(gewinn_jahr, 2),
    }


# ───────────────────────── Entscheidungs-Engine ────────────────────────────

def empfehlung_privat(satz_echt: float) -> dict:
    """Handlungsempfehlung Privat-PKW über Schwellenwert 0,30 €/km."""
    if satz_echt > GESETZLICHE_PAUSCHALE:
        return {
            "empfehlung": "C2",
            "ampel": "gruen",
            "titel": "Fahrtenbuch führen (Typ C2)",
            "begruendung": (
                f"Dein echter Kilometersatz ({satz_echt:.3f} €/km) liegt über der "
                f"0,30-€-Pauschale. Die Vollkostenrechnung bringt spürbar mehr "
                f"Steuererstattung — der Fahrtenbuch-Aufwand lohnt sich."
            ),
        }
    return {
        "empfehlung": "C1",
        "ampel": "blau",
        "titel": "Pauschale ansetzen (Typ C1)",
        "begruendung": (
            f"Dein echter Kilometersatz ({satz_echt:.3f} €/km) liegt unter oder gleich "
            f"der 0,30-€-Pauschale. Die Pauschale bringt gleich viel oder mehr — "
            f"ohne den Aufwand eines Fahrtenbuchs."
        ),
    }


def empfehlung_firmenwagen(dienstquote_pct: float) -> dict:
    """Handlungsempfehlung Firmenwagen über Dienstquote."""
    if dienstquote_pct > 60:
        return {
            "empfehlung": "B",
            "ampel": "gruen",
            "titel": "Fahrtenbuch führen (Typ B)",
            "begruendung": (
                f"Deine Dienstquote ({dienstquote_pct:.0f} %) ist hoch. Mit Fahrtenbuch "
                f"versteuerst du deutlich weniger als über die pauschale Regel."
            ),
        }
    if dienstquote_pct < 40:
        return {
            "empfehlung": "A",
            "ampel": "blau",
            "titel": "Pauschalversteuerung (Typ A)",
            "begruendung": (
                f"Deine Dienstquote ({dienstquote_pct:.0f} %) ist niedrig. Die Pauschal-"
                f"versteuerung ist günstiger und ohne bürokratischen Aufwand."
            ),
        }
    return {
        "empfehlung": "B",
        "ampel": "gelb",
        "titel": "Grenzbereich — Fahrtenbuch prüfen",
        "begruendung": (
            f"Deine Dienstquote ({dienstquote_pct:.0f} %) liegt im Graubereich (40–60 %). "
            f"Ein Fahrtenbuch kann sich lohnen — rechne beide Varianten genau durch."
        ),
    }


def berechne_szenario(params: dict) -> dict:
    """Zentrale Berechnung: nimmt alle Parameter, liefert alle vier Wege + Empfehlung.

    Erwartete Keys in params (alle optional mit sinnvollen Defaults):
      k_gesamt_privat, d_gesamt, d_dienst, d_privat, d_arbeit,
      ag_erstattung, steuersatz,
      blp, antrieb, entfernung_km, k_gesamt_ag,
      pv_aktiv, kwh_pv_jahr, pv_opportunitaet
    """
    p = params
    k_gesamt = float(p.get("k_gesamt_privat", 0) or 0)
    d_gesamt = float(p.get("d_gesamt", 0) or 0)
    d_dienst = float(p.get("d_dienst", 0) or 0)
    d_arbeit = float(p.get("d_arbeit", 0) or 0)
    ag = float(p.get("ag_erstattung", 0.15) or 0)
    s = float(p.get("steuersatz", 0.42) or 0)

    # Dienst-km darf Gesamt nicht übersteigen
    d_dienst = min(d_dienst, d_gesamt) if d_gesamt > 0 else d_dienst
    d_privat = float(p.get("d_privat", max(0.0, d_gesamt - d_dienst - d_arbeit)))

    # ── Energiekosten-Aufschlüsselung (falls Detaildaten übergeben) ──
    # Statt eines pauschalen k_gesamt kann die Energie differenziert berechnet werden:
    # Heimladung, Unterwegs-Laden und PV-Eigenverbrauch (bewertet mit entgangener
    # Einspeisevergütung als Opportunitätskosten).
    energie = None
    if p.get("energie_detail"):
        verbrauch_100 = float(p.get("verbrauch_kwh_100", 19) or 19)
        kwh_gesamt = d_gesamt * verbrauch_100 / 100.0
        anteil_heim = float(p.get("anteil_heim", 0.6) or 0)       # Netzstrom zuhause
        anteil_pv = float(p.get("anteil_pv", 0.0) or 0)           # PV-Eigenverbrauch
        anteil_unterwegs = max(0.0, 1 - anteil_heim - anteil_pv)  # öffentlich
        preis_heim = float(p.get("preis_heim", 0.30) or 0)
        preis_unterwegs = float(p.get("preis_unterwegs", 0.55) or 0)
        pv_opp = float(p.get("pv_opportunitaet", 0.08) or 0.08)   # entgangene Einspeisung

        kwh_heim = kwh_gesamt * anteil_heim
        kwh_pv = kwh_gesamt * anteil_pv
        kwh_unterwegs = kwh_gesamt * anteil_unterwegs
        kosten_heim = kwh_heim * preis_heim
        kosten_pv = kwh_pv * pv_opp          # PV kostet die entgangene Einspeisung
        kosten_unterwegs = kwh_unterwegs * preis_unterwegs
        energie_gesamt = kosten_heim + kosten_pv + kosten_unterwegs
        # Effektiver Mischpreis: was die kWh im Schnitt wirklich kostet
        # (Heim, PV-Opportunitaet und DC-Laden zusammengefasst)
        mischpreis = (energie_gesamt / kwh_gesamt) if kwh_gesamt > 0 else 0.0
        energie = {
            "kwh_gesamt": round(kwh_gesamt, 0),
            "kwh_heim": round(kwh_heim, 0), "kosten_heim": round(kosten_heim, 2),
            "kwh_pv": round(kwh_pv, 0), "kosten_pv": round(kosten_pv, 2),
            "kwh_unterwegs": round(kwh_unterwegs, 0), "kosten_unterwegs": round(kosten_unterwegs, 2),
            "energie_gesamt_jahr": round(energie_gesamt, 2),
            "mischpreis_kwh": round(mischpreis, 4),
            "anteil_heim_pct": round(anteil_heim * 100, 1),
            "anteil_pv_pct": round(anteil_pv * 100, 1),
            "anteil_unterwegs_pct": round(anteil_unterwegs * 100, 1),
            "pv_opportunitaet": round(pv_opp, 4),
        }

    satz = echter_km_satz(k_gesamt, d_gesamt)

    # Privat-PKW
    c1 = typ_c1_pauschale(d_dienst, ag, s)
    c2 = typ_c2_vollkosten(k_gesamt, d_gesamt, d_dienst, ag, s)
    vorteil_c2 = round(c2["steuererstattung"] - c1["steuererstattung"], 2)

    # Firmenwagen (optional — nur wenn BLP gesetzt)
    a = b = None
    dienstquote = (d_dienst / d_gesamt * 100) if d_gesamt > 0 else 0.0
    if p.get("blp"):
        blp = float(p.get("blp") or 0)
        antrieb = p.get("antrieb", "elektro")
        entfernung = float(p.get("entfernung_km", 0) or 0)
        k_ag = float(p.get("k_gesamt_ag", k_gesamt) or 0)
        # Eigenanteil an der Leasingrate senkt den geldwerten Vorteil (Zuzahlung AN)
        eigenanteil_monat = float(p.get("fw_eigenanteil_monat", 0) or 0)
        a = typ_a_firmenwagen_pauschal(blp, antrieb, entfernung, s)
        b = typ_b_firmenwagen_fahrtenbuch(k_ag, d_gesamt, d_privat, d_arbeit, s)
        if eigenanteil_monat > 0:
            # Zuzahlung mindert GWV (steuerlich anerkannt), erhöht aber reale Ausgabe
            ea_jahr = eigenanteil_monat * 12
            for typ_obj in (a, b):
                typ_obj["eigenanteil_jahr"] = round(ea_jahr, 2)
                typ_obj["netto_belastung_jahr"] = round(
                    max(0, typ_obj["netto_belastung_jahr"] - ea_jahr * s) + ea_jahr, 2)
                typ_obj["steuererstattung"] = round(-typ_obj["netto_belastung_jahr"], 2)

    # PV-Sonderlogik — kWh entweder direkt oder aus Fahrleistung hergeleitet
    pv = None
    if p.get("pv_aktiv"):
        kwh = float(p.get("kwh_pv_jahr", 0) or 0)
        if kwh <= 0:
            verbrauch_100 = float(p.get("verbrauch_kwh_100", 19) or 19)
            heimlade_anteil = float(p.get("heimlade_anteil", 0.7) or 0.7)
            kwh = d_gesamt * verbrauch_100 / 100.0 * heimlade_anteil
        if kwh > 0:
            pv = pv_marge(kwh, float(p.get("pv_opportunitaet", 0.08) or 0.08))

    # AG-Zuschuss / Car Allowance (Brutto → Netto nach Grenzsteuersatz, falls versteuert)
    allowance = None
    allowance_netto_jahr = 0.0
    if p.get("ag_zuschuss_brutto"):
        brutto_monat = float(p.get("ag_zuschuss_brutto") or 0)
        versteuert = bool(p.get("ag_zuschuss_versteuert", True))
        netto_monat = brutto_monat * (1 - s) if versteuert else brutto_monat
        allowance_netto_jahr = netto_monat * 12
        allowance = {
            "brutto_monat": round(brutto_monat, 2),
            "netto_monat": round(netto_monat, 2),
            "netto_jahr": round(allowance_netto_jahr, 2),
            "versteuert": versteuert,
        }

    # ── Kassenbon: "Was kostet mich das Auto wirklich?" ──
    # Wird IMMER berechnet (auch ohne Car Allowance). Einnahmen-Seite:
    # AG-Fahrtkostenerstattung + Car Allowance (netto) + Steuererstattung (bester
    # Weg) + PV-Bonus. Ausgaben-Seite: Jahres-Gesamtkosten. Saldo = real.
    netto_belastung_privat = None
    if k_gesamt > 0:
        fahrt_erstattung_ag = d_dienst * ag
        beste_erstattung = max(c1["steuererstattung"], c2["steuererstattung"])
        pv_bonus = pv["gewinn_jahr"] if pv else 0.0
        einnahmen = fahrt_erstattung_ag + allowance_netto_jahr + beste_erstattung + pv_bonus
        saldo = einnahmen - k_gesamt
        # Liquiditaets-Trennung: was fliesst sofort (AG zahlt monatlich) und was
        # erst nach der Steuererklaerung (Finanzamt, i. d. R. im Folgejahr)?
        sofort = fahrt_erstattung_ag + allowance_netto_jahr + pv_bonus
        spaeter = beste_erstattung
        netto_belastung_privat = {
            "gesamtkosten_jahr": round(k_gesamt, 2),
            "fahrt_erstattung_ag": round(fahrt_erstattung_ag, 2),
            "abzgl_allowance_netto": round(allowance_netto_jahr, 2),
            "abzgl_steuererstattung": round(beste_erstattung, 2),
            "pv_bonus": round(pv_bonus, 2),
            "einnahmen_gesamt": round(einnahmen, 2),
            "saldo_jahr": round(saldo, 2),
            "saldo_monat": round(saldo / 12, 2),
            # Liquiditaet
            "sofort_jahr": round(sofort, 2),
            "sofort_monat": round(sofort / 12, 2),
            "spaeter_jahr": round(spaeter, 2),
            "saldo_sofort_jahr": round(sofort - k_gesamt, 2),
            "saldo_sofort_monat": round((sofort - k_gesamt) / 12, 2),
            # Kompatibilität (positiv = Belastung)
            "reale_belastung_jahr": round(-saldo, 2),
            "reale_belastung_monat": round(-saldo / 12, 2),
        }

    # Empfehlungen
    emp_privat = empfehlung_privat(satz)
    emp_firma = empfehlung_firmenwagen(dienstquote) if (a and b) else None

    return {
        "eingaben": {
            "k_gesamt_privat": round(k_gesamt, 2),
            "d_gesamt": round(d_gesamt, 0),
            "d_dienst": round(d_dienst, 0),
            "d_privat": round(d_privat, 0),
            "d_arbeit": round(d_arbeit, 0),
            "ag_erstattung": round(ag, 4),
            "steuersatz": round(s, 4),
            "dienstquote_pct": round(dienstquote, 1),
        },
        "echter_km_satz": round(satz, 4),
        "privat": {
            "c1": c1,
            "c2": c2,
            "vorteil_c2_pro_jahr": vorteil_c2,
            "vorteil_c2_pro_monat": round(vorteil_c2 / 12, 2),
            "empfehlung": emp_privat,
        },
        "firmenwagen": ({"a": a, "b": b, "empfehlung": emp_firma} if (a and b) else None),
        "pv": pv,
        "allowance": allowance,
        "energie": energie,
        "netto_belastung_privat": netto_belastung_privat,
    }


# ═══════════════════════════════════════════════════════════════════════════
# FAHRZEUG-FINDER — Antriebsart-Vergleich (Diesel / Benzin / BEV / PHEV)
# ═══════════════════════════════════════════════════════════════════════════

# Realistische Referenzwerte (Startwerte, alle im UI überschreibbar).
# Verbräuche: l/100km bzw. kWh/100km. AfA-Basis: Anschaffung / Nutzungsdauer.
ANTRIEBSART_DEFAULTS = {
    "diesel": {
        "label": "Diesel",
        "verbrauch": 6.5,          # l/100 km
        "energiepreis": 1.70,      # €/l
        "einheit": "l",
        "wartung_pro_100km": 3.5,  # € Verschleiß/Wartung je 100 km
        "versicherung_jahr": 900,
        "kfz_steuer_jahr": 320,
        "anschaffung": 45000,
        "co2_pro_km": 140,          # g/km (Orientierung)
    },
    "benzin": {
        "label": "Benziner",
        "verbrauch": 7.5,
        "energiepreis": 1.75,
        "einheit": "l",
        "wartung_pro_100km": 3.0,
        "versicherung_jahr": 850,
        "kfz_steuer_jahr": 180,
        "anschaffung": 42000,
        "co2_pro_km": 155,
    },
    "bev": {
        "label": "Elektro (BEV)",
        "verbrauch": 19.0,          # kWh/100 km
        "energiepreis": 0.30,       # €/kWh (Mischpreis Heim/öffentlich)
        "einheit": "kWh",
        "wartung_pro_100km": 1.8,   # E-Autos: weniger Verschleiß
        "versicherung_jahr": 950,
        "kfz_steuer_jahr": 0,        # BEV bis 2030 steuerbefreit
        "anschaffung": 52000,
        "co2_pro_km": 0,
    },
    "phev": {
        "label": "Plug-in-Hybrid",
        "verbrauch": 4.5,           # l/100 km (kombiniert, elektrisch anteilig)
        "energiepreis": 1.75,
        "einheit": "l",
        "verbrauch_strom": 12.0,    # zusätzlich kWh/100 km im E-Betrieb
        "strompreis": 0.30,
        "e_anteil": 0.5,            # 50 % elektrisch gefahren
        "wartung_pro_100km": 2.8,
        "versicherung_jahr": 1000,
        "kfz_steuer_jahr": 90,
        "anschaffung": 55000,
        "co2_pro_km": 60,
    },
}

AFA_JAHRE_FINDER = 6  # Nutzungsdauer für Anschaffungs-Abschreibung


def _energiekosten_jahr(art: str, cfg: dict, km_jahr: float) -> float:
    """Energiekosten pro Jahr je Antriebsart (berücksichtigt PHEV-Mix)."""
    faktor = km_jahr / 100.0
    if art == "phev":
        e_anteil = cfg.get("e_anteil", 0.5)
        # Verbrenner-Anteil
        sprit = cfg["verbrauch"] * cfg["energiepreis"] * faktor * (1 - e_anteil)
        # E-Anteil
        strom = cfg.get("verbrauch_strom", 12.0) * cfg.get("strompreis", 0.30) * faktor * e_anteil
        return sprit + strom
    return cfg["verbrauch"] * cfg["energiepreis"] * faktor


def berechne_fahrzeug_finder(params: dict) -> dict:
    """Vergleicht die vier Antriebsarten für ein gegebenes Fahrprofil.

    params:
      km_jahr, tagestour_km, heimladung (bool), externe_ladung (bool),
      reichweite_bedarf_km, overrides (dict je Antriebsart mit Feldern),
      nur_arten (Liste, optional — sonst alle vier)
    Ergebnis: Kostenaufschlüsselung je Antriebsart + Rangfolge + Eignung + Empfehlung.
    """
    p = params
    km_jahr = float(p.get("km_jahr", 20000) or 20000)
    tagestour = float(p.get("tagestour_km", 0) or 0)
    heimladung = bool(p.get("heimladung", True))
    externe_ladung = bool(p.get("externe_ladung", False))
    reichweite_bedarf = float(p.get("reichweite_bedarf_km", tagestour) or tagestour)
    overrides = p.get("overrides", {}) or {}
    arten = p.get("nur_arten") or ["diesel", "benzin", "bev", "phev"]

    ergebnisse = []
    for art in arten:
        base = dict(ANTRIEBSART_DEFAULTS.get(art, {}))
        base.update(overrides.get(art, {}))  # Nutzer-Overrides
        if not base:
            continue

        energie = _energiekosten_jahr(art, base, km_jahr)
        verschleiss = base["wartung_pro_100km"] * (km_jahr / 100.0)
        versicherung = base["versicherung_jahr"]
        kfz_steuer = base["kfz_steuer_jahr"]
        afa = base["anschaffung"] / AFA_JAHRE_FINDER
        gesamt = energie + verschleiss + versicherung + kfz_steuer + afa

        # Eignungs-Check (weiche Faktoren)
        eignung = "gut"
        eignung_hinweise = []
        if art == "bev":
            reichweite_bev = 350  # typische Alltagsreichweite
            if reichweite_bedarf > reichweite_bev and not externe_ladung:
                eignung = "kritisch"
                eignung_hinweise.append(
                    f"Reichweitenbedarf ({reichweite_bedarf:.0f} km) über typischer "
                    f"BEV-Alltagsreichweite (~{reichweite_bev} km) — externe Ladung nötig.")
            elif not heimladung and not externe_ladung:
                eignung = "kritisch"
                eignung_hinweise.append("Ohne Heim- oder externe Ladung nicht praktikabel.")
            elif not heimladung:
                eignung = "bedingt"
                eignung_hinweise.append("Keine Heimladung — höhere Energiekosten & Aufwand (öffentlich laden).")
        if art == "phev":
            if not heimladung:
                eignung = "bedingt"
                eignung_hinweise.append("Ohne Heimladung fährt der PHEV meist im teuren Benzinbetrieb.")

        ergebnisse.append({
            "art": art,
            "label": base["label"],
            "kosten": {
                "energie": round(energie, 0),
                "verschleiss": round(verschleiss, 0),
                "versicherung": round(versicherung, 0),
                "kfz_steuer": round(kfz_steuer, 0),
                "afa": round(afa, 0),
                "gesamt": round(gesamt, 0),
            },
            "kosten_pro_km": round(gesamt / km_jahr, 3) if km_jahr > 0 else 0,
            "co2_pro_km": base.get("co2_pro_km", 0),
            "eignung": eignung,
            "eignung_hinweise": eignung_hinweise,
            "verbrauch": base["verbrauch"],
            "einheit": base.get("einheit", "l"),
            "energiepreis": base["energiepreis"],
        })

    # Rangfolge nach Gesamtkosten (nur geeignete zuerst)
    def sort_key(e):
        eignungs_rang = {"gut": 0, "bedingt": 1, "kritisch": 2}.get(e["eignung"], 3)
        return (eignungs_rang, e["kosten"]["gesamt"])
    ergebnisse_sortiert = sorted(ergebnisse, key=sort_key)

    empfehlung = None
    if ergebnisse_sortiert:
        beste = ergebnisse_sortiert[0]
        # Günstigste insgesamt (ohne Eignungsfilter) für den Vergleich
        guenstigste = min(ergebnisse, key=lambda e: e["kosten"]["gesamt"])
        ersparnis = round(max(e["kosten"]["gesamt"] for e in ergebnisse) - beste["kosten"]["gesamt"], 0)
        empfehlung = {
            "art": beste["art"],
            "label": beste["label"],
            "gesamt_jahr": beste["kosten"]["gesamt"],
            "ersparnis_vs_teuerste": ersparnis,
            "eignung": beste["eignung"],
            "hinweise": beste["eignung_hinweise"],
            "guenstigste_ohne_eignung": guenstigste["label"] if guenstigste["art"] != beste["art"] else None,
        }

    return {
        "profil": {
            "km_jahr": round(km_jahr, 0),
            "tagestour_km": round(tagestour, 0),
            "heimladung": heimladung,
            "externe_ladung": externe_ladung,
            "reichweite_bedarf_km": round(reichweite_bedarf, 0),
        },
        "arten": ergebnisse_sortiert,
        "empfehlung": empfehlung,
    }
