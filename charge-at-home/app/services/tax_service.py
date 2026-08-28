"""Deutsche Einkommensteuer nach § 32a EStG (Tarif 2026)."""


def einkommensteuer(zve: float) -> float:
    """Berechnet die tarifliche Einkommensteuer nach § 32a EStG (Grundtarif 2026).
    Basierend auf den 5 Tarifzonen mit Eckwerten für 2026."""
    x = int(zve)
    # Eckwerte 2026 (Grundfreibetrag ~12.348 €)
    if x <= 12348:
        return 0.0
    elif x <= 17443:
        # Zone 2: Progression 14% → ~24%
        y = (x - 12348) / 10000.0
        return (914.51 * y + 1400) * y
    elif x <= 68480:
        # Zone 3: Progression ~24% → 42%
        z = (x - 17443) / 10000.0
        return (173.10 * z + 2397) * z + 1015.13
    elif x <= 277825:
        # Zone 4: konstant 42%
        return 0.42 * x - 10911.92
    else:
        # Zone 5: konstant 45%
        return 0.45 * x - 19246.67


def grenzsteuersatz(zve: float, splitting: int = 1) -> dict:
    """Ermittelt den Grenzsteuersatz (Steuer auf den nächsten Euro).
    splitting=2 → Zusammenveranlagung (Splitting-Verfahren)."""
    zve = float(zve)
    if splitting == 2:
        # Splitting: Steuer = 2 × ESt(zvE/2)
        halb = zve / 2.0
        st_now = einkommensteuer(halb) * 2
        st_next = einkommensteuer(halb + 0.5) * 2  # +1€ gesamt = +0,5€ je Hälfte
        grenz = (st_next - st_now)
        durchschnitt = (st_now / zve) if zve > 0 else 0
    else:
        st_now = einkommensteuer(zve)
        st_next = einkommensteuer(zve + 1)
        grenz = st_next - st_now
        durchschnitt = (st_now / zve) if zve > 0 else 0

    return {
        "grenzsteuersatz": round(grenz, 3),
        "grenzsteuersatz_pct": round(grenz * 100, 1),
        "durchschnittssteuersatz_pct": round(durchschnitt * 100, 1),
        "steuer_gesamt": round(st_now, 2),
        "zone": _tarifzone(zve if splitting == 1 else zve / 2),
    }


def _tarifzone(zve: float) -> str:
    if zve <= 12348:
        return "Zone 1 – Grundfreibetrag (0 %)"
    elif zve <= 17443:
        return "Zone 2 – Progressionszone 1 (14–24 %)"
    elif zve <= 68480:
        return "Zone 3 – Progressionszone 2 (24–42 %)"
    elif zve <= 277825:
        return "Zone 4 – Spitzensteuersatz (42 %)"
    else:
        return "Zone 5 – Reichensteuer (45 %)"
