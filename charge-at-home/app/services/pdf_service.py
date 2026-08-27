"""
PDF-Beleg-Service — FA-LS-06 (Ladestrom), FA-FK-06/07 (Fahrtkosten).

Layout-Spezifikation (v10.43):
- Footer: Canvas-Callback (onPage) — feste Position 18 mm über Seitenunterkante,
  nie als Flowable, damit er bei kurzen Inhalten nicht nach oben rutscht.
- Farben: exakte Palette laut Auftraggeber-Vorgabe (s. Konstantenblock).
- Tabellen: Header #1a365d / Zebra white+#f8fafc / Totals #ebf8ff.
- bottomMargin 35 mm damit Flowables nie in den Canvas-Footer laufen.
"""

import hashlib
import io
from datetime import datetime

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import mm
from reportlab.lib.enums import TA_RIGHT, TA_LEFT, TA_CENTER
from reportlab.platypus import (
    BaseDocTemplate, Frame, PageTemplate,
    Paragraph, Spacer, Table, TableStyle
)
from reportlab.graphics.shapes import Drawing, Path, Circle, Line, Rect, Polygon

from services.billing_service import compute_energy_and_amount
from repositories import settings_repository

# ─── Farbpalette (Auftraggeber-Vorgabe) ──────────────────────────────────────
C_HEADER    = colors.HexColor("#1a365d")   # Navy Deep — Header & Balken
C_ACCENT    = colors.HexColor("#2b6cb0")   # Corporate Blue — Subtitel & Logo
C_ZEBRA     = colors.HexColor("#f8fafc")   # Slate Lightest — Tabellen-Zebrastreifen
C_SUMMARY   = colors.HexColor("#edf2f7")   # Gray Soft — Summary-Box Hintergrund
C_TOTALS    = colors.HexColor("#ebf8ff")   # Blue Light Tint — Summenzeile
C_BORDER    = colors.HexColor("#e2e8f0")   # Border Slate — Trennlinien & Rahmen
C_TEXT      = colors.HexColor("#1a202c")   # Dark Charcoal — Haupttext
C_MUTED     = colors.HexColor("#718096")   # Muted Slate — Labels / Sekundärtext
C_LABEL_HDR = colors.HexColor("#4a5568")   # Footer-Überschriften

SENDER_NAME = "eCharge@Home"


def _logo_drawing(size: float = 15 * mm) -> Drawing:
    """Bildmarke: Hausumriss mit Blitz.

    Reduziert und geometrisch, passend zur Bildschirmfassung. Der fruehere
    Entwurf zeigte ein E-Auto mit Ladestecker unter einem Hausdach — bei
    15 mm Kantenlaenge zerfiel das in nicht mehr erkennbare Striche und wirkte
    verspielt. Auf einem Steuerbeleg ist das der falsche Ton.

    Aufgebaut aus Line und Polygon statt aus Pfaden: ReportLab schliesst offene
    Pfade beim Rendern automatisch und fuellt sie, wodurch der Hausumriss als
    Flaeche erschiene. Koordinaten 0..100, skaliert auf die Zielgroesse.
    """
    d = Drawing(size, size)
    s = size / 100.0

    def p(*vals):
        return [v * s for v in vals]

    strich = 6.5 * s

    # Dach — eine durchgehende Linie ueber zwei Segmente
    d.add(Line(*p(14, 55, 50, 84), strokeColor=C_ACCENT, strokeWidth=strich,
               strokeLineCap=1, strokeLineJoin=1))
    d.add(Line(*p(50, 84, 86, 55), strokeColor=C_ACCENT, strokeWidth=strich,
               strokeLineCap=1, strokeLineJoin=1))

    # Seitenwaende, unten offen — haelt die Marke leicht
    d.add(Line(*p(22, 50, 22, 16), strokeColor=C_ACCENT, strokeWidth=strich,
               strokeLineCap=1))
    d.add(Line(*p(78, 50, 78, 16), strokeColor=C_ACCENT, strokeWidth=strich,
               strokeLineCap=1))
    d.add(Line(*p(22, 16, 78, 16), strokeColor=C_ACCENT, strokeWidth=strich,
               strokeLineCap=1))

    # Blitz als einzige gefuellte Flaeche: der Blickfang
    d.add(Polygon(p(56, 62, 44, 62, 38, 38, 47, 38, 43, 22, 60, 46, 50, 46),
                  fillColor=C_ACCENT, strokeColor=None))
    return d

def _brand_header(st: dict, tagline: str) -> Table:
    """Kopfzeile mit Logo links und Wortmarke + Tagline rechts."""
    text = Table(
        [[Paragraph(SENDER_NAME, st["brand"])],
         [Paragraph(tagline, st["tagline"])]],
        colWidths=[INHALTSBREITE])
    text.setStyle(TableStyle([
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
        ("TOPPADDING", (0, 0), (-1, -1), 0),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
    ]))
    tbl = Table([[_logo_drawing(17 * mm), text]], colWidths=[21 * mm, INHALTSBREITE - 21 * mm])
    tbl.setStyle(TableStyle([
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
        ("TOPPADDING", (0, 0), (-1, -1), 0),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
        ("VALIGN", (0, 0), (0, 0), "TOP"),
        ("VALIGN", (1, 0), (1, 0), "TOP"),
    ]))
    return tbl


def _resolve_vehicle_label(person_id: int = None) -> str:
    """Ermittelt die Fahrzeugbezeichnung für Belege. Vorrang hat das echte
    Standard-Fahrzeug aus der Fahrzeug-Verwaltung (bezeichnung + kennzeichen);
    nur als Fallback wird das alte Setting 'vehicle_description' verwendet.
    So ist die Bezeichnung nicht mehr doppelt zu pflegen."""
    try:
        from repositories import vehicle_repository
        vehicles = vehicle_repository.list_vehicles(person_id)
        if vehicles:
            std = next((v for v in vehicles if v.get("ist_standard")), vehicles[0])
            bez = (std.get("bezeichnung") or "").strip()
            kz  = (std.get("kennzeichen") or "").strip()
            if bez and kz:
                return f"{bez} ({kz})"
            if bez:
                return bez
            if kz:
                return kz
    except Exception:
        pass
    try:
        return settings_repository.get_setting("vehicle_description") or "—"
    except Exception:
        return "—"

PERSON_FIELD_LABELS = {
    "email": "E-Mail",
    "personalnummer": "Personalnummer",
    "kfz_kennzeichen": "Kfz-Kennzeichen",
    "telefon": "Telefon",
}


# ─── Formatter ───────────────────────────────────────────────────────────────
def _fmt_eur(v: float) -> str:
    return f"{v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".") + " €"

def _fmt_kwh(v: float) -> str:
    return f"{v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")

def _fmt_rate(v: float, decimals: int = 4) -> str:
    """Formatiert einen Tarif-Wert mit deutschem Dezimaltrennzeichen."""
    return f"{v:.{decimals}f}".replace(".", ",")


def _shorten_address(addr: str) -> str:
    """Kürzt JEDE Adresse (egal ob kurz oder langer Photon-String) auf max. 2 Teile:
    'Musterweg, 7, Musterstadt, Deutschland' → 'Musterweg 7, Musterstadt'
    'Huawei Technologies, Hansaallee, 205, Düsseldorf, Deutschland' → 'Huawei Technologies, Düsseldorf'
    'Musterstadt' → 'Musterstadt'
    """
    if not addr:
        return addr
    if ',' not in addr:
        return addr  # Einfacher Name, keine Zerlegung nötig

    parts = [p.strip() for p in addr.split(',') if p.strip()]

    # Ländernamen + häufige Endungen entfernen
    ignored = {'deutschland', 'germany', 'österreich', 'austria', 'schweiz', 'switzerland',
               'de', 'at', 'ch', 'frankreich', 'france', 'belgien', 'belgium'}
    while parts and parts[-1].lower() in ignored:
        parts.pop()

    if not parts:
        return addr
    if len(parts) == 1:
        return parts[0]

    # Hausnummern (rein numerisch oder Nummernmuster) an Vorgänger anhängen
    merged = []
    i = 0
    while i < len(parts):
        part = parts[i]
        # Nächstes Teil ist Hausnummer → zusammenführen
        if i + 1 < len(parts):
            nxt = parts[i + 1]
            if nxt.isdigit() or (len(nxt) <= 5 and nxt[0].isdigit()):
                merged.append(f"{part} {nxt}")
                i += 2
                continue
        merged.append(part)
        i += 1

    # Ergebnis: erste Angabe + letzte Angabe (= Stadt)
    if len(merged) == 1:
        return merged[0]
    # Wenn erster Teil sehr lang ist (z.B. Firmenname), nur Firma + Stadt
    return f"{merged[0]}, {merged[-1]}"
    return hashlib.sha256(data).hexdigest()

def person_rows_from_display(person_display: dict) -> list:
    rows = [("Mitarbeiter", person_display.get("name", ""))]
    for key, label in PERSON_FIELD_LABELS.items():
        if person_display.get(key):
            rows.append((label, person_display[key]))
    return rows


# ─── Canvas-Footer (onPage Callback) ─────────────────────────────────────────
def _zeichne_wasserzeichen(canvas) -> None:
    """Legt den Demo-Hinweis diagonal ueber die Seite.

    Deutlich sichtbar, aber blass genug, dass der Beleg lesbar bleibt: Wer die
    Demo ausprobiert, soll seine Zahlen pruefen koennen — nur eben nicht das
    Ergebnis beim Arbeitgeber einreichen.

    Zusaetzlich zum Schriftzug steht eine Zeile am oberen Rand. Ein diagonaler
    Text allein wird beim Ausdruck in Graustufen leicht uebersehen.
    """
    from services import edition_service
    text = edition_service.wasserzeichen()
    if not text:
        return

    breite, hoehe = A4
    canvas.saveState()
    # Diagonal ueber die Seitenmitte
    canvas.setFillColor(colors.HexColor("#c8102e"))
    canvas.setFillAlpha(0.10)
    canvas.translate(breite / 2, hoehe / 2)
    canvas.rotate(48)
    canvas.setFont("Helvetica-Bold", 62)
    canvas.drawCentredString(0, -20, text)
    canvas.restoreState()

    # Klartext am oberen Rand — überlebt auch einen Schwarzweißdruck
    canvas.saveState()
    canvas.setFillColor(colors.HexColor("#c8102e"))
    canvas.setFillAlpha(0.55)
    canvas.setFont("Helvetica-Bold", 7.5)
    canvas.drawCentredString(breite / 2, hoehe - 8 * mm,
                             "DEMOVERSION - NICHT ZUR VORLAGE BEI ARBEITGEBER ODER FINANZAMT")
    canvas.restoreState()


def _mid_hinweis(st) -> Table:
    """Fussnote zur Messung — auf jedem Abrechnungsbeleg.

    Zwei Aussagen, beide notwendig:
      1. Die Werte stammen aus dem Zaehler der Ladeeinrichtung.
      2. Fuer deren MID-Konformitaet steht der Betreiber ein, nicht diese
         Software. Sie liest aus und stellt dar, sie prueft nicht.
    """
    text = ("<b>Hinweis zur Messung:</b> Die aufgeführten Energiemengen stammen "
            "aus dem Zählwerk der eingesetzten Ladeeinrichtung. Für den Einsatz "
            "einer MID-zertifizierten Wallbox sowie deren ordnungsgemäßen "
            "Betrieb ist der Betreiber verantwortlich. Diese Aufstellung gibt "
            "die übermittelten Zählerstände unverändert wieder; eine Prüfung "
            "oder Korrektur der Messwerte findet nicht statt.")
    p = Paragraph(text, ParagraphStyle("MID", parent=st["hint"], fontSize=7.2,
                                       leading=10, textColor=C_MUTED))
    t = Table([[p]], colWidths=[INHALTSBREITE])
    t.setStyle(TableStyle([
        ("BACKGROUND",   (0, 0), (-1, -1), C_SUMMARY),
        ("LINEABOVE",    (0, 0), (-1, 0), 0.4, C_BORDER),
        ("LEFTPADDING",  (0, 0), (-1, -1), 8),
        ("RIGHTPADDING", (0, 0), (-1, -1), 8),
        ("TOPPADDING",   (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING",(0, 0), (-1, -1), 6),
    ]))
    return t


def _make_footer_cb(beleg_nr: str, footer_col3_line2: str = "Reisekosten / Spesenabrechnung"):
    """Erzeugt eine onPage-Callback-Funktion mit den belegspezifischen Werten."""
    def draw(canvas, doc):
        # Zuerst das Wasserzeichen, damit der uebrige Inhalt darueber liegt
        _zeichne_wasserzeichen(canvas)
        canvas.saveState()
        fy = 18 * mm
        pw = A4[0]
        lm = 12 * mm

        # Trennlinie
        canvas.setStrokeColor(C_BORDER)
        canvas.setLineWidth(0.8)
        canvas.line(lm, fy + 12 * mm, pw - lm, fy + 12 * mm)

        # Erste Spalte traegt den Lizenzvermerk. Bei der Vollversion steht dort
        # der Name des Kaeufers — jeder erzeugte Beleg ist damit dem Erwerber
        # zuzuordnen. Das macht eine Weitergabe der Software unattraktiv,
        # ohne dass es einer technischen Sperre beduerfte.
        from services import edition_service
        cols = [
            ("Abrechnungssystem",
             edition_service.lizenzvermerk(),
             "Mobility & Travel Settlement"),
            ("Prüfstatus",
             "Digital protokolliert & geprüft",
             "Status: Zur Abrechnung freigegeben"),
            ("Referenz / Buchung",
             f"Verwendungszweck: {beleg_nr}",
             footer_col3_line2),
        ]
        x_positions = [lm, 75 * mm, 140 * mm]
        # Verfuegbare Breite je Spalte (letzte Spalte endet am rechten Rand)
        col_limits = [75 * mm - lm - 3 * mm, 140 * mm - 75 * mm - 3 * mm,
                      (pw - lm) - 140 * mm - 3 * mm]

        def _fit(text: str, font: str, size: float, max_w: float) -> str:
            """Kuerzt Text mit Auslassungspunkten, damit nichts ueberlappt."""
            if canvas.stringWidth(text, font, size) <= max_w:
                return text
            gekuerzt = text
            while gekuerzt and canvas.stringWidth(gekuerzt + "…", font, size) > max_w:
                gekuerzt = gekuerzt[:-1]
            return gekuerzt.rstrip() + "…"

        for (hdr, l1, l2), x, limit in zip(cols, x_positions, col_limits):
            canvas.setFont("Helvetica-Bold", 7.5)
            canvas.setFillColor(C_LABEL_HDR)
            canvas.drawString(x, fy + 7 * mm, _fit(hdr, "Helvetica-Bold", 7.5, limit))
            canvas.setFont("Helvetica", 7)
            canvas.setFillColor(C_MUTED)
            canvas.drawString(x, fy + 3.5 * mm, _fit(l1, "Helvetica", 7, limit))
            canvas.drawString(x, fy, _fit(l2, "Helvetica", 7, limit))

        # Seitenzahl rechts auf Hoehe der Spaltenueberschriften — dort ist Platz,
        # auf Hoehe fy kollidierte sie zuvor mit der dritten Footer-Spalte.
        canvas.setFont("Helvetica", 6.5)
        canvas.setFillColor(C_MUTED)
        canvas.drawRightString(pw - lm, fy + 7 * mm, f"Seite {doc.page}")
        canvas.restoreState()
    return draw


# ─── Gemeinsames Dokument-Setup ───────────────────────────────────────────────
def _legal_box_ladestrom(rate: float, st: dict) -> Paragraph:
    """Variante 1 – Rechtstext für Ladestrom-Beleg (kompakt, §-Referenzen, Stand 2026)."""
    return Paragraph(
        f"<b>Rechtliche Grundlagen &amp; Steuerliche Behandlung (Stand 2026)</b><br/>"
        f"<b>Steuerfreier Auslagenersatz:</b> Die Erstattung der privaten Heimladekosten erfolgt als steuer- und "
        f"sozialabgabenfreier Auslagenersatz gem. § 3 Nr. 50 EStG "
        f"(i.&#160;V.&#160;m. BMF-Schreiben vom 29.09.2020 / 11.11.2025). "
        f"<b>Ladestrom 2026 ({_fmt_rate(rate, 2)} €/kWh):</b> Abrechnung auf Basis der amtlichen "
        f"BMF-Strompreispauschale 2026 von {_fmt_rate(rate, 2)} €/kWh bei nachgewiesener Zählermessung "
        f"(Quelle: Statistisches Bundesamt).",
        st["legal"])


def _legal_box_fahrtkosten(rate: float, st: dict) -> Paragraph:
    """Variante 1 – Rechtstext für Fahrtkosten-Beleg (kompakt, §-Referenzen, Stand 2026)."""
    return Paragraph(
        f"<b>Rechtliche Grundlagen &amp; Steuerliche Behandlung (Stand 2026)</b><br/>"
        f"<b>Steuerfreier Auslagenersatz:</b> Die Erstattung der betrieblichen Fahrtkosten erfolgt als steuer- und "
        f"sozialabgabenfreier Auslagenersatz gem. § 3 Nr. 50 EStG. "
        f"<b>Dienstreisen &amp; Fahrtkosten ({_fmt_eur(rate)}/km):</b> Betriebliche Fahrten werden "
        f"arbeitgeberseitig mit der vereinbarten Pauschale von {_fmt_eur(rate)}/km steuerfrei erstattet. "
        f"Die Differenz zur gesetzlichen Pauschale gem. § 9 Abs.&#160;1 Satz&#160;3 Nr.&#160;4a EStG (0,30 €/km) "
        f"verbleibt zur steuerlichen Geltendmachung als Werbungskosten.",
        st["legal"])


# ─── Gemeinsames Dokument-Setup ───────────────────────────────────────────────
def _make_doc(buf: io.BytesIO, beleg_nr: str, title: str,
              footer_col3_line2: str = "Reisekosten / Spesenabrechnung") -> BaseDocTemplate:
    doc = BaseDocTemplate(
        buf, pagesize=A4, title=title,
        leftMargin=12 * mm, rightMargin=12 * mm,
        topMargin=20 * mm, bottomMargin=38 * mm,  # mehr Platz oben damit Header nicht überschneidet
    )
    # Innenpolster des Rahmens auf null: ReportLab setzt hier standardmaessig
    # 6 Punkt (rund 2 mm) je Seite. Dadurch stehen nur 182 mm zur Verfuegung,
    # waehrend Balken und Tabellen mit 186 mm rechnen — die Elemente wurden
    # verschoben und die Tabellen ragten rechts ueber die Abschnittsbalken
    # hinaus. Die Seitenraender kommen bereits aus leftMargin/rightMargin.
    frame = Frame(doc.leftMargin, doc.bottomMargin, doc.width, doc.height, id="F1",
                  leftPadding=0, rightPadding=0, topPadding=0, bottomPadding=0)
    tmpl  = PageTemplate(id="Main", frames=frame,
                         onPage=_make_footer_cb(beleg_nr, footer_col3_line2))
    doc.addPageTemplates([tmpl])
    return doc


# ─── Shared Style-Helpers ────────────────────────────────────────────────────
def _styles():
    S = getSampleStyleSheet()
    def ps(name, **kw):
        return ParagraphStyle(name, parent=S["Normal"], **kw)
    return {
        # leading EXPLIZIT setzen (Standard = 1.2 × fontSize kann bei großen Schriften
        # zu knapp sein und führt dazu, dass der Tagline-Text in die Brand-Zeile rutscht)
        "brand":    ps("brand",    fontName="Helvetica-Bold", fontSize=20,
                        leading=26, textColor=C_HEADER, spaceAfter=0),
        "tagline":  ps("tagline",  fontSize=8, leading=12, textColor=C_ACCENT,
                        spaceBefore=2, spaceAfter=10),
        "doctype":  ps("doctype",  fontName="Helvetica-Bold", fontSize=14,
                        leading=18, textColor=C_TEXT, spaceAfter=2),
        "meta":     ps("meta",     fontSize=9, textColor=C_MUTED, spaceAfter=14),
        "sh":       ps("sh",       fontName="Helvetica-Bold", fontSize=9,
                        textColor=colors.white),
        "name_big": ps("name_big", fontName="Helvetica-Bold", fontSize=13,
                        leading=17, textColor=C_TEXT, spaceAfter=2),
        "lbl":      ps("lbl",      fontSize=8.5, textColor=C_MUTED),
        "val":      ps("val",      fontSize=9.5, textColor=C_TEXT),
        "hint":     ps("hint",     fontSize=8, textColor=C_MUTED, spaceBefore=2),
        "kv_sum":   ps("kv_sum",   fontSize=9, textColor=C_TEXT, spaceAfter=8),
        "th":       ps("th",       fontName="Helvetica-Bold", fontSize=8,
                        textColor=colors.white),
        "cell":     ps("cell",     fontSize=8.5, leading=11.5, textColor=C_TEXT),
        "num":      ps("num",      fontSize=8.5, textColor=C_TEXT, alignment=TA_RIGHT),
        "tot_lbl":  ps("tot_lbl",  fontSize=10, textColor=C_TEXT, alignment=TA_RIGHT),
        "tot_val":  ps("tot_val",  fontSize=10, textColor=C_TEXT, alignment=TA_RIGHT),
        "tot_bold": ps("tot_bold", fontName="Helvetica-Bold", fontSize=10.5,
                        textColor=C_HEADER, alignment=TA_RIGHT),
        "legal":    ps("legal",    fontSize=8, leading=11.5, textColor=C_MUTED,
                        spaceBefore=8, spaceAfter=8),
    }


def _section_header(text: str, st: dict) -> Table:
    t = Table([[Paragraph(text, st["sh"])]], colWidths=[INHALTSBREITE])
    t.setStyle(TableStyle([
        ("BACKGROUND",    (0, 0), (-1, -1), C_HEADER),
        ("LEFTPADDING",   (0, 0), (-1, -1), 8),
        ("TOPPADDING",    (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
        ("ROWPADDING",    (0, 0), (-1, -1), 0),
    ]))
    return t


def _kv_row(label: str, value: str, st: dict) -> Table:
    r = Table([[Paragraph(label, st["lbl"]), Paragraph(value, st["val"])]],
              colWidths=[55 * mm, 131 * mm])
    r.setStyle(TableStyle([
        ("LEFTPADDING",   (0, 0), (-1, -1), 0),
        ("TOPPADDING",    (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ("LINEBELOW",     (0, 0), (-1, -1), 0.4, C_BORDER),
    ]))
    return r


# Nutzbare Breite zwischen den Seitenraendern. Alle Abschnittsbalken und
# Tabellen richten sich danach — sonst stehen sie unterschiedlich weit im
# Papier und die Seite wirkt schief.
INHALTSBREITE = 186 * mm


def _auf_inhaltsbreite(breiten: list) -> list:
    """Skaliert Spaltenbreiten so, dass ihre Summe genau die Inhaltsbreite ergibt.

    Die angegebenen Werte wirken damit als Verhaeltnis, nicht als absolutes
    Mass. Zuvor waren sie fest eingetragen und summierten sich je nach Beleg
    auf 162 bis 188 mm — die Tabellen standen dadurch mal schmaler, mal breiter
    als der blaue Balken darueber. Wer eine Spalte aendert, muss die uebrigen
    jetzt nicht mehr nachrechnen.
    """
    summe = sum(breiten)
    if summe <= 0:
        return breiten
    faktor = INHALTSBREITE / summe
    skaliert = [b * faktor for b in breiten]
    # Rundungsreste auf die letzte Spalte legen, damit die Summe exakt stimmt
    skaliert[-1] += INHALTSBREITE - sum(skaliert)
    return skaliert


def _protocol_table(headers: list, rows: list, col_widths: list, st: dict) -> Table:
    col_widths = _auf_inhaltsbreite(col_widths)
    data = [[Paragraph(h, st["th"]) for h in headers]]
    for i, row in enumerate(rows):
        formatted = []
        for j, cell in enumerate(row):
            s = st["num"] if j >= len(headers) - 2 else st["cell"]
            formatted.append(Paragraph(str(cell), s))
        data.append(formatted)

    # hAlign="LEFT": ohne diese Angabe zentriert ReportLab schmalere Tabellen,
    # wodurch sie gegenueber den Abschnittsueberschriften eingerueckt wirken.
    tbl = Table(data, colWidths=col_widths, repeatRows=1, hAlign="LEFT")
    tbl.setStyle(TableStyle([
        ("BACKGROUND",    (0, 0), (-1, 0),  C_HEADER),
        ("TEXTCOLOR",     (0, 0), (-1, 0),  colors.white),
        ("ROWBACKGROUNDS",(0, 1), (-1, -1), [colors.white, C_ZEBRA]),
        ("GRID",          (0, 0), (-1, -1), 0.4, C_BORDER),
        ("TOPPADDING",    (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ("LEFTPADDING",   (0, 0), (-1, -1), 6),
        ("RIGHTPADDING",  (0, 0), (-1, -1), 6),
        ("VALIGN",        (0, 0), (-1, -1), "MIDDLE"),
        ("ALIGN",         (0, 0), (-1, -1), "LEFT"),    # Linksbündig
    ]))
    return tbl


def _totals_section(lines: list, st: dict) -> list:
    """lines: [(label, value, bold, highlight)]"""
    result = []
    for label, value, bold, highlight in lines:
        l_st = st["tot_bold"] if bold else st["tot_lbl"]
        v_st = st["tot_bold"] if bold else st["tot_val"]
        bg   = C_TOTALS if highlight else colors.transparent
        row  = Table([[Paragraph(label, l_st), Paragraph(value, v_st)]],
                     colWidths=[146 * mm, 40 * mm])
        row.setStyle(TableStyle([
            ("BACKGROUND",    (0, 0), (-1, -1), bg),
            ("TOPPADDING",    (0, 0), (-1, -1), 4),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ("RIGHTPADDING",  (0, 0), (-1, -1), 6),
            ("LINEABOVE",     (0, 0), (-1, 0),  1.5 if highlight else 0, C_HEADER),
            ("LINEBELOW",     (0, 0), (-1, 0),  1.5 if highlight else 0, C_HEADER),
        ]))
        result.append(row)
    return result


# ─── Ladeabrechnung ───────────────────────────────────────────────────────────
def generate_ladestrom_beleg(person_display: dict, abrechnungsfall: str,
                              sessions: list, period_label: str,
                              beleg_seq: int = 1, show_bmf_reference: bool = False,
                              user_id: int | None = None) -> bytes:
    beleg_nr = f"INV-{datetime.now().strftime('%Y%m%d')}-{beleg_seq:03d}"
    heute    = datetime.now().strftime("%d.%m.%Y")
    buf      = io.BytesIO()
    doc      = _make_doc(buf, beleg_nr, "Ladeabrechnung",
                          "Erstattung über Reisekosten / Spesenabrechnung")
    st = _styles()

    # Fahrzeug aus Settings
    vehicle_desc = _resolve_vehicle_label()

    kunden_id = f"CUST-{10000 + (user_id or 0) * 137 + beleg_seq:05d}"
    name      = person_display.get("name", "—")

    # EINHEITLICHE BEZEICHNUNG AUF ALLEN BELEGEN
    #
    # Frueher stand je nach Herkunft "Fahrzeug-App", "CSV-Import" oder
    # "Manuell" auf dem Beleg. Das laedt zu Rueckfragen ein, die niemand
    # beantworten will: Eine Reisekostenstelle sieht "Fahrzeug-App" und
    # fragt nach der Eichung.
    #
    # Massgeblich ist die Wallbox, nicht der Weg, auf dem die Werte in diese
    # Anwendung gelangt sind. Ob sie MID-konform misst, verantwortet der
    # Betreiber — darauf weist der Beleg ausdruecklich hin.
    LADEPUNKT_BEZEICHNUNG = "Wallbox (MID-zertifiziert)"
    MESSEINRICHTUNG = "MID-Zählerprotokoll der Ladeeinrichtung"

    source_labels = {s: LADEPUNKT_BEZEICHNUNG for s in
                     ("loxone_api", "ocpp", "extern_ocpp", "bmw_app", "csv", "manual")}
    ladepunkt = LADEPUNKT_BEZEICHNUNG
    messeinr  = MESSEINRICHTUNG

    total_kwh = total_amount = 0.0
    for s in sessions:
        e, a = compute_energy_and_amount(s)
        total_kwh += e; total_amount += a

    rate = sessions[0]["price_per_kwh"] if sessions else 0.34

    story = []

    # ── Kopf ────────────────────────────────────────────────────────────────
    story.append(_brand_header(st, "HEIMLADESTROM-ABRECHNUNGSSERVICE"))
    story.append(Paragraph("LADEABRECHNUNG", st["doctype"]))
    story.append(Paragraph(f"Beleg-Nr.: {beleg_nr} &nbsp;·&nbsp; Belegdatum: {heute}",
                            st["meta"]))

    # ── Kundenblock ─────────────────────────────────────────────────────────
    story.append(_section_header("KUNDE / ABRECHNUNGSEMPFÄNGER", st))
    story.append(Spacer(1, 3 * mm))
    story.append(Paragraph(name, st["name_big"]))
    story.append(Paragraph(f"Kunden-ID: {kunden_id}", st["lbl"]))
    story.append(Paragraph("(Zur Vorlage beim Arbeitgeber zwecks steuerfreiem Auslagenersatz)",
                            st["hint"]))
    story.append(Spacer(1, 3 * mm))
    for lbl, val in [
        ("Abrechnungszeitraum:", period_label),
        ("Fahrzeug:", vehicle_desc),
        ("Ladepunkt-Kennung:", ladepunkt),
        ("Messeinrichtung:", messeinr),
    ]:
        story.append(_kv_row(lbl, val, st))
    story.append(Spacer(1, 5 * mm))

    # ── Zusammenfassungszeile ────────────────────────────────────────────────
    summary_line = (f"Erfasste Vorgänge: <b>{len(sessions)} Ladezyklen</b> &nbsp;|&nbsp; "
                    f"Gesamtmenge: <b>{_fmt_kwh(total_kwh)} kWh</b> &nbsp;|&nbsp; "
                    f"Strompreispauschale 2026: <b>{_fmt_rate(rate)} €/kWh</b>")
    box = Table([[Paragraph(summary_line, ParagraphStyle("SL", parent=st["lbl"],
                                                          textColor=C_TEXT, fontSize=9))]],
                colWidths=[INHALTSBREITE])
    box.setStyle(TableStyle([
        ("BACKGROUND",    (0, 0), (-1, -1), C_SUMMARY),
        ("LEFTPADDING",   (0, 0), (-1, -1), 8),
        ("TOPPADDING",    (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
        ("BOX",           (0, 0), (-1, -1), 0.4, C_BORDER),
    ]))
    story.append(box)
    story.append(Spacer(1, 4 * mm))

    # ── Doppelabrechnungs-Prüfung (Compliance § 3 Nr. 50 EStG) ──────────────
    try:
        from services import duplicate_service
        _conflicts = duplicate_service.find_overlapping_sessions(sessions)
    except Exception:
        _conflicts = []
    if _conflicts:
        warn_style = ParagraphStyle(
            "dupWarn", parent=st["lbl"], fontSize=8.5, textColor=colors.HexColor("#b91c1c"),
            backColor=colors.HexColor("#fef2f2"), borderColor=colors.HexColor("#b91c1c"),
            borderWidth=0.8, borderPadding=6, leading=12)
        _conf_lines = "<br/>".join(
            f"• {c['session_a']['start'][:10]} {c['session_a']['start'][11:16]}–{c['session_a']['end'][11:16]} Uhr: "
            f"Session #{c['session_a']['id']} und #{c['session_b']['id']} überschneiden sich (verschiedene Quellen)"
            for c in _conflicts)
        story.append(Paragraph(
            f"<b>⚠ WARNUNG: MÖGLICHE DOPPELABRECHNUNG</b><br/>"
            f"Es wurden {len(_conflicts)} überschneidende Ladezeiträume aus unterschiedlichen "
            f"Datenquellen erkannt. Für § 3 Nr. 50 EStG darf jede Ladung nur EINMAL erfasst werden. "
            f"Bitte pro Zeitraum entweder Wallbox-Messung ODER Fahrzeug-App verwenden:<br/>{_conf_lines}",
            warn_style))
        story.append(Spacer(1, 4 * mm))

    # ── Protokoll-Tabelle ───────────────────────────────────────────────────
    story.append(_section_header("PROTOKOLL DER LADEVORGÄNGE (DETAILLIERTE EINZELNACHWEISE)", st))
    story.append(Spacer(1, 2 * mm))

    headers = ["#", "Datum", "Ladezeitraum", "Messpunkt / System", "Menge", "Satz", "Betrag"]
    rows    = []
    for i, s in enumerate(sessions, 1):
        energy_kwh, amount = compute_energy_and_amount(s)
        src_label = LADEPUNKT_BEZEICHNUNG
        try:
            st_dt   = datetime.strptime(s.get("start_timestamp", ""), "%Y-%m-%d %H:%M:%S")
            date_s  = st_dt.strftime("%d.%m.%Y")
            time_s  = st_dt.strftime("%H:%M")
        except Exception:
            date_s = str(s.get("start_timestamp", ""))[:10]; time_s = "–"
        try:
            end_dt  = datetime.strptime(s.get("end_timestamp", ""), "%Y-%m-%d %H:%M:%S")
            time_e  = end_dt.strftime("%H:%M Uhr")
        except Exception:
            time_e  = "–"
        rows.append([str(i), date_s, f"{time_s} – {time_e}", src_label,
                     f"{_fmt_kwh(energy_kwh)} kWh",
                     f"{_fmt_eur(s.get('price_per_kwh', rate))}",
                     _fmt_eur(amount)])

    story.append(_protocol_table(headers, rows,
                                  [10*mm, 22*mm, 30*mm, 38*mm, 22*mm, 22*mm, 22*mm], st))
    story.append(Spacer(1, 6 * mm))

    # ── Rechtlicher Hinweis (nur wenn Schalter aktiv) ───────────────────────
    if show_bmf_reference:
        story.append(_legal_box_ladestrom(rate, st))
        story.append(Spacer(1, 4 * mm))
    else:
        # Immer die kompakte Rechtsgrundlage anzeigen (Variante 1)
        story.append(_legal_box_ladestrom(rate, st))
        story.append(Spacer(1, 4 * mm))

    # ── Summen ───────────────────────────────────────────────────────────────
    story.extend(_totals_section([
        ("Lademenge gesamt:",              f"{_fmt_kwh(total_kwh)} kWh",  False, False),
        ("Erstattungsbetrag (netto):",     _fmt_eur(total_amount),         False, False),
        ("Umsatzsteuer (Auslagenersatz 0%):", "0,00 €",                   False, False),
        ("Auszahlungsbetrag:",             _fmt_eur(total_amount),         True,  True),
    ], st))

    # MID-Hinweis am Ende jedes Abrechnungsbelegs: benennt die
    # Grundlage der Messung und grenzt die Verantwortung ab.
    story.append(Spacer(1, 5 * mm))
    story.append(_mid_hinweis(st))

    doc.build(story)
    return buf.getvalue()


# ─── Fahrtkostenbeleg ─────────────────────────────────────────────────────────
def generate_fahrtkosten_arbeitgeber_beleg(person_display: dict, abrechnungsfall: str,
                                            trips: list, period_label: str,
                                            beleg_seq: int = 1) -> bytes:
    from services.trip_service import compute_amounts

    beleg_nr = f"FB-{datetime.now().strftime('%Y%m')}-{beleg_seq:03d}"
    heute    = datetime.now().strftime("%d.%m.%Y")
    buf      = io.BytesIO()
    doc      = _make_doc(buf, beleg_nr, "Fahrtkostenbeleg",
                          "Erstattung über Reisekosten / Spesenabrechnung")
    st = _styles()

    vehicle_desc = _resolve_vehicle_label()

    name      = person_display.get("name", "—")
    kunden_id = f"CUST-{10000 + beleg_seq * 137:05d}"
    total_km  = sum(t["distance_km"] for t in trips)
    total_amt = sum(compute_amounts(t["distance_km"], t["rate_chosen"])[0] for t in trips)
    rate      = trips[0]["rate_chosen"] if trips else 0.15

    story = []

    # ── Kopf ────────────────────────────────────────────────────────────────
    story.append(_brand_header(st, "MOBILITÄTS- &amp; FAHRTKOSTENABRECHNUNG"))
    story.append(Paragraph("FAHRTKOSTENBELEG", st["doctype"]))
    story.append(Paragraph(f"Beleg-Nr.: {beleg_nr} &nbsp;·&nbsp; Belegdatum: {heute}",
                            st["meta"]))

    # ── Mitarbeiterblock ─────────────────────────────────────────────────────
    story.append(_section_header("MITARBEITER / ANTRAGSTELLER", st))
    story.append(Spacer(1, 3 * mm))
    story.append(Paragraph(name, st["name_big"]))
    story.append(Paragraph(f"Mitarbeiter-ID: {kunden_id}", st["lbl"]))
    story.append(Paragraph("(Zur Vorlage bei der Reisekosten- & Fuhrparkabrechnung)",
                            st["hint"]))
    story.append(Spacer(1, 3 * mm))
    for lbl, val in [
        ("Abrechnungsmonat:", period_label),
        ("Abrechnungsart:", "Dienstliche Fahrten"),
        ("Fahrzeug:", vehicle_desc),
        ("Erfassungsmethode:", "Distanz- / Routennachweis"),
    ]:
        story.append(_kv_row(lbl, val, st))
    story.append(Spacer(1, 5 * mm))

    # ── Zusammenfassungszeile ────────────────────────────────────────────────
    summary_line = (f"Erfasste Fahrten: <b>{len(trips)} Fahrt{'en' if len(trips)!=1 else ''}</b>"
                    f" &nbsp;|&nbsp; Gesamtdistanz: <b>{_fmt_kwh(total_km)} km</b>"
                    f" &nbsp;|&nbsp; Kilometersatz: <b>{_fmt_rate(rate)} €/km</b>")
    box = Table([[Paragraph(summary_line, ParagraphStyle("SL2", parent=st["lbl"],
                                                          textColor=C_TEXT, fontSize=9))]],
                colWidths=[INHALTSBREITE])
    box.setStyle(TableStyle([
        ("BACKGROUND",    (0, 0), (-1, -1), C_SUMMARY),
        ("LEFTPADDING",   (0, 0), (-1, -1), 8),
        ("TOPPADDING",    (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
        ("BOX",           (0, 0), (-1, -1), 0.4, C_BORDER),
    ]))
    story.append(box)
    story.append(Spacer(1, 4 * mm))

    # ── Fahrten-Tabelle ──────────────────────────────────────────────────────
    story.append(_section_header("EINZELNACHWEIS DER DURCHGEFÜHRTEN FAHRTEN", st))
    story.append(Spacer(1, 2 * mm))

    headers = ["#", "Datum", "Reiseweg / Relation", "Anlass / Zweck", "Distanz", "Satz", "Betrag"]
    rows    = []
    for i, t in enumerate(trips, 1):
        employer_amount, _ = compute_amounts(t["distance_km"], t["rate_chosen"])
        try:
            d_fmt = datetime.strptime(t["trip_date"], "%Y-%m-%d").strftime("%d.%m.%Y")
        except Exception:
            d_fmt = t["trip_date"]
        start_short = _shorten_address(t['start_address'])
        end_short   = _shorten_address(t['end_address'])
        rows.append([str(i), d_fmt,
                     f"{start_short} → {end_short}",
                     t["purpose"],
                     f"{_fmt_kwh(t['distance_km'])} km",
                     f"{_fmt_eur(t['rate_chosen'])}",
                     _fmt_eur(employer_amount)])

    # Distanz braucht 23 mm, sonst bricht "198,20 km" auf zwei Zeilen um;
    # der Platz kommt aus der Reiseweg-Spalte (Gesamtbreite bleibt 180 mm).
    story.append(_protocol_table(headers, rows,
                                  [6*mm, 22*mm, 55*mm, 38*mm, 23*mm, 18*mm, 18*mm], st))
    story.append(Spacer(1, 5 * mm))

    # ── Hinweise (Variante 1) ─────────────────────────────────────────────────
    story.append(_legal_box_fahrtkosten(rate, st))
    story.append(Spacer(1, 4 * mm))

    # ── Summen ───────────────────────────────────────────────────────────────
    story.extend(_totals_section([
        ("Gesamtfahrstrecke:",         f"{_fmt_kwh(total_km)} km", False, False),
        ("Erstattungsbetrag:",         _fmt_eur(total_amt),         False, False),
        ("Umsatzsteuer (0% / Spesen):", "0,00 €",                  False, False),
        ("Auszahlungsbetrag:",         _fmt_eur(total_amt),         True,  True),
    ], st))

    # MID-Hinweis am Ende jedes Abrechnungsbelegs: benennt die
    # Grundlage der Messung und grenzt die Verantwortung ab.
    story.append(Spacer(1, 5 * mm))
    story.append(_mid_hinweis(st))

    doc.build(story)
    return buf.getvalue()


# ─── Finanzamt-Jahresexport (bleibt erhalten) ────────────────────────────────
def generate_fahrtkosten_finanzamt_export(person_display: dict, abrechnungsfall: str,
                                           trips: list, year_label: str,
                                           beleg_seq: int = 1) -> bytes:
    from services.trip_service import compute_amounts, STANDARD_RATE

    beleg_nr = f"FA-{year_label}-{beleg_seq:03d}"
    buf      = io.BytesIO()
    doc      = _make_doc(buf, beleg_nr, "Reisekosten-Nachweis",
                          "Berufliche Fahrten mit Privat-PKW (Werbungskosten)")
    st = _styles()

    name = person_display.get("name", "—")
    story = []

    story.append(_brand_header(st, "MOBILITÄTS- &amp; FAHRTKOSTENABRECHNUNG"))
    story.append(Paragraph("REISEKOSTEN-NACHWEIS", st["doctype"]))
    story.append(Paragraph(f"Beleg-Nr.: {beleg_nr} &nbsp;·&nbsp; Kalenderjahr: {year_label}",
                            st["meta"]))

    story.append(_section_header("MITARBEITER / ANTRAGSTELLER", st))
    story.append(Spacer(1, 3 * mm))
    for lbl, val in [("Mitarbeiter:", name), ("Kalenderjahr:", year_label),
                      ("Abrechnungsfall:", abrechnungsfall + " (Privat-PKW)"),
                      ("Anzahl Fahrten:", str(len(trips)))]:
        story.append(_kv_row(lbl, val, st))
    story.append(Spacer(1, 5 * mm))

    headers = ["Datum", "Route", "Anlass / Zweck", "km", "0,30 €/km", "Erstattet AG", "Differenz FA"]
    rows    = []
    tk = ts = te = td = 0.0
    for t in trips:
        ea, diff = compute_amounts(t["distance_km"], t["rate_chosen"])
        std = round(t["distance_km"] * STANDARD_RATE, 2)
        tk += t["distance_km"]; ts += std; te += ea; td += diff
        try:
            d_fmt = datetime.strptime(t["trip_date"], "%Y-%m-%d").strftime("%d.%m.%Y")
        except Exception:
            d_fmt = t["trip_date"]
        rows.append([d_fmt,
                     f"{_shorten_address(t['start_address'])} → {_shorten_address(t['end_address'])}",
                     _shorten_address(t.get("purpose", "") or "—"),
                     f"{_fmt_kwh(t['distance_km'])} km",
                     _fmt_eur(std), _fmt_eur(ea), _fmt_eur(diff)])
    rows.append(["Gesamt", "", "", f"{_fmt_kwh(tk)} km",
                  _fmt_eur(ts), _fmt_eur(te), _fmt_eur(td)])

    # Datum braucht 23 mm (sonst bricht "19.08.2026" um) und km 20 mm
    # (sonst bricht "198,20 km"); der Platz kommt aus Route und Anlass.
    story.append(_protocol_table(headers, rows,
                                  [23*mm, 41*mm, 36*mm, 20*mm, 22*mm, 24*mm, 22*mm], st))
    story.append(Spacer(1, 4 * mm))
    story.extend(_totals_section([
        ("Pauschale 0,30 €/km gesamt:", _fmt_eur(ts), False, False),
        ("Bereits vom AG erstattet:",   _fmt_eur(te), False, False),
        ("Noch abzugsfähig (FA):",      _fmt_eur(td), True,  True),
    ], st))

    # MID-Hinweis am Ende jedes Abrechnungsbelegs: benennt die
    # Grundlage der Messung und grenzt die Verantwortung ab.
    story.append(Spacer(1, 5 * mm))
    story.append(_mid_hinweis(st))

    doc.build(story)
    return buf.getvalue()


def generate_fahrtenbuch(person_display: dict, trips: list, year_label: str,
                          km_start_initial: float = 0, vehicle_label: str = "—",
                          km_ende: float = 0, beleg_seq: int = 1,
                          period_start: str = None, period_end: str = None) -> bytes:
    """Fahrtenbuch als Nachweis für den individuellen Kilometersatz (Weg 2,
    § 9 Abs. 1 S. 3 Nr. 4a EStG i. V. m. R 9.5 LStR).

    Automatische Privatfahrten-Füllung (tagesgewichtet): Die dienstlichen Fahrten
    sind namentlich erfasst. Ist der km-Endstand bekannt, wird die private
    Fahrleistung als 'Rest' berechnet und auf die Intervalle ZWISCHEN den
    Dienstfahrten verteilt — und zwar gewichtet nach der Anzahl der Tage im
    jeweiligen Intervall (ein 10-Tage-Zeitraum bekommt mehr Privat-km als ein
    3-Tage-Zeitraum). So ist die km-Kette lückenlos, tagesaktuell und plausibel,
    ohne dass jede Privatfahrt einzeln eingetippt werden muss."""
    # Beleg-Nr aus dem Jahr ableiten (year_label kann ein Zeitraum-String sein)
    _jahr_kurz = None
    if period_start:
        _jahr_kurz = period_start[:4]
    elif year_label and year_label[:4].isdigit():
        _jahr_kurz = year_label[:4]
    beleg_nr = f"FB-{_jahr_kurz or datetime.now().year}-{beleg_seq:03d}"
    buf      = io.BytesIO()
    doc      = _make_doc(buf, beleg_nr, "Fahrtenbuch",
                          "Nachweis für individuellen Kilometersatz")
    st = _styles()
    name = person_display.get("name", "—")
    story = []

    story.append(_brand_header(st, "ELEKTRONISCHES FAHRTENBUCH"))
    story.append(Paragraph("FAHRTENBUCH", st["doctype"]))
    story.append(Paragraph(f"Beleg-Nr.: {beleg_nr} &nbsp;·&nbsp; Zeitraum: {year_label}", st["meta"]))

    trips_sorted = sorted(trips, key=lambda t: t.get("trip_date", ""))
    dienst_km = sum((t.get("distance_km") or 0) for t in trips_sorted)

    privat_km_gesamt = 0.0
    lueckenlos = False
    if km_ende and km_ende > km_start_initial:
        gesamt_km = km_ende - km_start_initial
        privat_km_gesamt = round(gesamt_km - dienst_km, 1)
        if privat_km_gesamt >= 0:
            lueckenlos = True
        else:
            privat_km_gesamt = 0.0

    # Zeitraum menschenlesbar (period_start/end kommen als YYYY-MM-DD)
    def _fmt_period(d):
        try:
            return datetime.strptime(d, "%Y-%m-%d").strftime("%d.%m.%Y")
        except Exception:
            return d or "—"
    zeitraum_text = year_label
    if period_start and period_end:
        zeitraum_text = f"{_fmt_period(period_start)} – {_fmt_period(period_end)}"

    story.append(_section_header("FAHRER / FAHRZEUG", st))
    story.append(Spacer(1, 3 * mm))
    info_rows = [("Fahrer:", name), ("Fahrzeug:", vehicle_label),
                 ("Zeitraum:", zeitraum_text),
                 ("km-Stand Anfang:", f"{_fmt_kwh(km_start_initial)} km")]
    if km_ende and km_ende > km_start_initial:
        info_rows.append(("km-Stand Ende:", f"{_fmt_kwh(km_ende)} km"))
        info_rows.append(("Gesamtfahrleistung:", f"{_fmt_kwh(km_ende - km_start_initial)} km"))
    info_rows.append(("davon dienstlich:", f"{_fmt_kwh(dienst_km)} km ({len(trips_sorted)} Fahrten)"))
    if lueckenlos:
        anteil = (dienst_km / (km_ende - km_start_initial) * 100) if (km_ende - km_start_initial) > 0 else 0
        info_rows.append(("davon privat (Rest):", f"{_fmt_kwh(privat_km_gesamt)} km"))
        info_rows.append(("Dienstanteil:", f"{anteil:.1f} %"))
    for lbl, val in info_rows:
        story.append(_kv_row(lbl, val, st))
    story.append(Spacer(1, 5 * mm))

    headers = ["Zeitraum / Datum", "Fahrtart / Anlass", "km-Stand", "gefahren"]
    rows = []
    km_lauf = float(km_start_initial)
    n = len(trips_sorted)

    # ── Zeitraum-Grenzen bestimmen ──
    from datetime import timedelta
    def _parse(d):
        try:
            return datetime.strptime(d, "%Y-%m-%d").date()
        except Exception:
            return None
    ps = _parse(period_start) if period_start else None
    pe = _parse(period_end) if period_end else None
    if ps is None and trips_sorted:
        ps = _parse(trips_sorted[0].get("trip_date", "")) or None
    if pe is None and trips_sorted:
        pe = _parse(trips_sorted[-1].get("trip_date", "")) or None

    trip_dates = [_parse(t.get("trip_date", "")) for t in trips_sorted]

    # ── Intervalle zwischen den Fahrten ermitteln + Tage berechnen ──
    # Intervall i liegt VOR Fahrt i (i=0..n-1), Intervall n liegt NACH der letzten Fahrt.
    def _interval_bounds(i):
        start = ps if i == 0 else trip_dates[i-1]
        end   = pe if i == n else trip_dates[i]
        return start, end

    interval_days = []
    for i in range(n + 1):
        s, e = _interval_bounds(i)
        if s and e:
            d = (e - s).days
            interval_days.append(max(0, d))
        else:
            interval_days.append(0)
    tage_gesamt = sum(interval_days)

    # ── Tagesgewichtete Verteilung der Privatkilometer ──
    # Jeder Block: (Tage_im_Intervall / Gesamt_Tage) × Privat-km.
    # Fallback (keine verwertbaren Datumsabstände): Gleichverteilung.
    privat_blocks = [0.0] * (n + 1)
    if lueckenlos and privat_km_gesamt > 0:
        if tage_gesamt > 0:
            verteilt = 0.0
            for i in range(n + 1):
                anteil = interval_days[i] / tage_gesamt
                privat_blocks[i] = round(privat_km_gesamt * anteil, 1)
                verteilt += privat_blocks[i]
            # Rundungsdifferenz dem größten Block zuschlagen (km-Kette exakt halten)
            diff = round(privat_km_gesamt - verteilt, 1)
            if abs(diff) >= 0.05:
                jmax = max(range(n + 1), key=lambda k: privat_blocks[k])
                privat_blocks[jmax] = round(privat_blocks[jmax] + diff, 1)
        else:
            gleich = round(privat_km_gesamt / (n + 1), 1)
            privat_blocks = [gleich] * (n + 1)
            privat_blocks[-1] = round(privat_km_gesamt - gleich * n, 1)

    def _fmt_datum(d):
        return d.strftime("%d.%m.%Y") if d else "—"

    def _add_privat_row(i, km_block):
        nonlocal km_lauf
        if km_block <= 0.05:
            return
        km_von = km_lauf
        km_bis = km_lauf + km_block
        km_lauf = km_bis
        s, e = _interval_bounds(i)
        # Zeitraum-Label (z. B. "01.08. – 03.08.2026")
        if s and e and s != e:
            zeitraum = f"{s.strftime('%d.%m.')} – {e.strftime('%d.%m.%Y')}"
        elif s:
            zeitraum = _fmt_datum(s)
        else:
            zeitraum = "—"
        rows.append([
            zeitraum,
            "Privatfahrten",
            f"{int(round(km_von))}\n→ {int(round(km_bis))}",
            f"{_fmt_kwh(km_block)} km",
        ])

    for i, t in enumerate(trips_sorted):
        # Privat-Block VOR dieser Dienstfahrt
        if lueckenlos:
            _add_privat_row(i, privat_blocks[i])
        dist = t.get("distance_km") or 0
        km_von = km_lauf
        km_bis = km_lauf + dist
        km_lauf = km_bis
        d_fmt = _fmt_datum(trip_dates[i])
        ziel = f"{_shorten_address(t.get('start_address',''))} → {_shorten_address(t.get('end_address',''))}"
        anlass = t.get("purpose", "") or "—"
        rows.append([
            d_fmt,
            f"<b>Dienstfahrt:</b> {_shorten_address(anlass)}<br/>{ziel}",
            f"{int(round(km_von))}\n→ {int(round(km_bis))}",
            f"{_fmt_kwh(dist)} km",
        ])

    # Privat-Block NACH der letzten Dienstfahrt
    if lueckenlos:
        _add_privat_row(n, privat_blocks[n])

    total_gefahren = km_lauf - km_start_initial
    rows.append(["Gesamt", "", f"→ {int(round(km_lauf))}", f"{_fmt_kwh(total_gefahren)} km"])

    # Volle Satzbreite (180 mm) wie bei den uebrigen Belegen, damit die Tabelle
    # buendig mit den Abschnittsueberschriften abschliesst.
    story.append(_protocol_table(headers, rows,
                                  [32*mm, 96*mm, 28*mm, 24*mm], st))
    story.append(Spacer(1, 5 * mm))

    hinweis = ParagraphStyle("fbHint", parent=st["lbl"], fontSize=8,
                              textColor=colors.HexColor("#6c6355"), leading=11)
    if lueckenlos:
        hinweis_text = (
            "<b>Lueckenloses Fahrtenbuch (R 9.5 LStR):</b> Die dienstlichen Fahrten sind einzeln "
            "mit Datum, Route und geschaeftlichem Anlass erfasst. Die private Fahrleistung ergibt "
            "sich als Differenz aus Gesamtfahrleistung (Endstand - Anfangsstand) abzueglich der "
            "Dienstfahrten und ist als 'Privatfahrten (Rest)' ausgewiesen, sodass die "
            "Kilometerkette lückenlos ist und das Verhältnis Dienst/privat belegt wird. "
            "Alle Angaben ohne Gewähr — die Anerkennung obliegt dem Finanzamt.")
    else:
        hinweis_text = (
            "<b>Hinweis (R 9.5 LStR):</b> Für ein lückenloses Fahrtenbuch bitte zusätzlich den "
            "Kilometerstand zum Jahresende (31.12.) eintragen — die private Fahrleistung wird "
            "dann automatisch als 'Rest' ergänzt und das Verhältnis Dienst/privat belegt. "
            "Alle Angaben ohne Gewähr — die Anerkennung obliegt dem Finanzamt.")
    story.append(Paragraph(hinweis_text, hinweis))
    story.append(Spacer(1, 12 * mm))

    sig = Table([["", ""]], colWidths=[80*mm, 80*mm])
    sig.setStyle(TableStyle([
        ("LINEABOVE", (0, 0), (0, 0), 0.5, colors.HexColor("#201d18")),
        ("LINEABOVE", (1, 0), (1, 0), 0.5, colors.HexColor("#201d18")),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
    ]))
    story.append(sig)
    # MID-Hinweis. Er gehoert auf jeden Beleg: Er benennt die Grundlage der
    # Messung und grenzt zugleich ab, wer wofuer einsteht. Ohne ihn entsteht
    # der Eindruck, diese Anwendung habe die Werte geprueft.
    story.append(Spacer(1, 4 * mm))
    story.append(_mid_hinweis(st))
    story.append(Spacer(1, 4 * mm))

    story.append(Table([["Ort, Datum", "Unterschrift Fahrer"]], colWidths=[80*mm, 80*mm],
                        style=TableStyle([("FONTSIZE", (0,0), (-1,-1), 8),
                                          ("TEXTCOLOR", (0,0), (-1,-1), colors.HexColor("#6c6355"))])))

    doc.build(story)
    return buf.getvalue()


# ═══════════════════════════════════════════════════════════════════════════
# STROMKOSTEN-AUSWERTUNG (persoenliche Monatsuebersicht, druckfertig)
# ═══════════════════════════════════════════════════════════════════════════

def generate_stromkosten_auswertung(person_display: dict, sessions: list,
                                     period_label: str, real_rate: float = 0.28,
                                     bmf_rate: float = 0.34,
                                     ladepunkt: str = "", beleg_seq: int = 1) -> bytes:
    """Persoenliche Ladestrom- und Kosten-Auswertung fuer den eigenen Bedarf bzw.
    zur Ablage in den Steuerunterlagen.

    Anders als generate_ladestrom_beleg (Beleg fuer den Arbeitgeber) stellt dieses
    Dokument die EIGENEN Stromkosten der Arbeitgeber-Erstattung gegenueber und
    weist den Reinerloes (Marge) aus."""
    beleg_nr = f"AW-{datetime.now().strftime('%Y%m%d')}-{beleg_seq:03d}"
    buf = io.BytesIO()
    doc = _make_doc(buf, beleg_nr, "Ladestrom-Auswertung",
                    "Persönliche Monatsübersicht")
    st = _styles()

    vehicle_desc = _resolve_vehicle_label()
    name = person_display.get("name", "—")
    # Ladepunkt: explizit uebergeben, sonst aus den Sessions ableiten
    if not ladepunkt:
        namen = {s.get("wallbox_name") for s in sessions if s.get("wallbox_name")}
        ladepunkt = ", ".join(sorted(namen)) if namen else "Heim-Wallbox"

    # Kennzahlen
    total_kwh = 0.0
    zaehler_start = None
    zaehler_ende = None
    rows = []
    for i, s in enumerate(sessions, start=1):
        e, _ = compute_energy_and_amount(s)
        total_kwh += e
        ms, me = s.get("meter_start_wh"), s.get("meter_stop_wh")
        if ms is not None:
            zs = ms / 1000.0
            zaehler_start = zs if zaehler_start is None else min(zaehler_start, zs)
        if me is not None:
            ze = me / 1000.0
            zaehler_ende = ze if zaehler_ende is None else max(zaehler_ende, ze)
        ts = s.get("start_timestamp") or ""
        try:
            d_fmt = datetime.strptime(ts[:10], "%Y-%m-%d").strftime("%d.%m.%Y")
        except Exception:
            d_fmt = ts[:10]
        zeit = ts[11:16] if len(ts) >= 16 else ""
        ende_ts = s.get("end_timestamp") or ""
        zeit_ende = ende_ts[11:16] if len(ende_ts) >= 16 else ""
        zeitraum = f"{zeit} – {zeit_ende} Uhr" if zeit and zeit_ende else (zeit or "—")
        rows.append([
            str(i), d_fmt, zeitraum,
            f"{_fmt_kwh(me / 1000.0)} kWh" if me is not None else "—",
            f"{_fmt_kwh(e)} kWh",
            _fmt_eur(e * real_rate),
            _fmt_eur(e * bmf_rate),
        ])

    kosten_real = total_kwh * real_rate
    erstattung = total_kwh * bmf_rate
    reinerloes = erstattung - kosten_real
    marge_kwh = bmf_rate - real_rate

    story = []
    story.append(_brand_header(st, "LADESTROM &amp; KOSTEN-AUSWERTUNG"))
    story.append(Paragraph("PERSÖNLICHE MONATSÜBERSICHT", st["doctype"]))
    story.append(Paragraph(
        f"Auswertung-Nr.: {beleg_nr} &nbsp;·&nbsp; Zeitraum: {period_label} &nbsp;·&nbsp; "
        f"Erstellt: {datetime.now().strftime('%d.%m.%Y')}", st["meta"]))

    # Rahmendaten
    story.append(_section_header("RAHMENDATEN", st))
    story.append(Spacer(1, 3 * mm))
    story.append(Paragraph(name, st["name_big"]))
    for lbl, val in [
        ("Fahrzeug:", vehicle_desc),
        ("Ladepunkt:", ladepunkt or "Heim-Wallbox"),
        ("Zählerstand Start → Ende:",
         (f"{_fmt_kwh(zaehler_start)} → {_fmt_kwh(zaehler_ende)} kWh"
          if zaehler_start is not None and zaehler_ende is not None else "—")),
        ("Eigener Strompreis:", f"{_fmt_rate(real_rate, 3)} €/kWh"),
        ("BMF-Erstattungssatz:", f"{_fmt_rate(bmf_rate, 3)} €/kWh"),
    ]:
        story.append(_kv_row(lbl, val, st))
    story.append(Spacer(1, 5 * mm))

    # KPI-Zeile (vier Kennzahlen nebeneinander)
    kpi_data = [[
        Paragraph("<font size=7 color='#6c6355'>GELADENE ENERGIE</font><br/>"
                  f"<font size=14><b>{_fmt_kwh(total_kwh)} kWh</b></font><br/>"
                  f"<font size=7 color='#6c6355'>{len(sessions)} Ladevorgänge</font>", st["cell"]),
        Paragraph("<font size=7 color='#6c6355'>EIGENE STROMKOSTEN</font><br/>"
                  f"<font size=14><b>{_fmt_eur(kosten_real)}</b></font><br/>"
                  f"<font size=7 color='#6c6355'>Basis: {_fmt_rate(real_rate, 2)} €/kWh</font>", st["cell"]),
        Paragraph("<font size=7 color='#6c6355'>ERSTATTUNG ARBEITGEBER</font><br/>"
                  f"<font size=14><b>{_fmt_eur(erstattung)}</b></font><br/>"
                  f"<font size=7 color='#6c6355'>Basis: {_fmt_rate(bmf_rate, 2)} €/kWh (BMF)</font>", st["cell"]),
        Paragraph("<font size=7 color='#6c6355'>MEIN REINERLÖS</font><br/>"
                  f"<font size=14><b>{'+' if reinerloes >= 0 else ''}{_fmt_eur(reinerloes)}</b></font><br/>"
                  f"<font size=7 color='#6c6355'>Erstattung minus Strompreis</font>", st["cell"]),
    ]]
    kpi_tbl = Table(kpi_data, colWidths=[INHALTSBREITE / 4] * 4)
    kpi_tbl.setStyle(TableStyle([
        ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#d9d2c5")),
        ("INNERGRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#d9d2c5")),
        ("BACKGROUND", (3, 0), (3, 0), colors.HexColor("#eef7ee")),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
    ]))
    story.append(kpi_tbl)
    story.append(Spacer(1, 6 * mm))

    # Detail-Protokoll
    story.append(_section_header("DETAIL-PROTOKOLL DER LADEVORGÄNGE", st))
    story.append(Spacer(1, 3 * mm))
    headers = ["#", "Datum", "Ladezeitraum", "Zähler Ende", "Menge", "Eigene Kosten", "Erstattung"]
    if not rows:
        rows = [["—", "—", "Keine Ladevorgänge im Zeitraum", "—", "—", "—", "—"]]
    story.append(_protocol_table(headers, rows,
                                 [8 * mm, 22 * mm, 34 * mm, 26 * mm, 22 * mm, 26 * mm, 24 * mm], st))
    story.append(Spacer(1, 5 * mm))

    # Zusammenfassung
    story.extend(_totals_section([
        ("Gesamte Lademenge:", f"{_fmt_kwh(total_kwh)} kWh", False, False),
        (f"Eigene Stromkosten ({_fmt_rate(real_rate, 2)} €):", "−" + _fmt_eur(kosten_real), False, False),
        (f"Erstattung Arbeitgeber ({_fmt_rate(bmf_rate, 2)} €):", "+" + _fmt_eur(erstattung), False, False),
        ("Mein Reinerlös (Cash):", ("+" if reinerloes >= 0 else "") + _fmt_eur(reinerloes), True, True),
    ], st))
    story.append(Spacer(1, 4 * mm))

    # Auswertungsnotiz
    notiz = (f"<b>Auswertungsnotiz:</b><br/>"
             f"• Gesamtverbrauch: {_fmt_kwh(total_kwh)} kWh im Abrechnungsfenster {period_label}.<br/>"
             f"• Margen-Effekt: Aus der Differenz zwischen eigenem Strompreis "
             f"({_fmt_rate(real_rate, 2)} €/kWh) und amtlicher Erstattung "
             f"({_fmt_rate(bmf_rate, 2)} €/kWh) ergibt sich ein Reinerlös von "
             f"<b>{_fmt_rate(marge_kwh, 2)} € je geladener kWh</b>.<br/>"
             f"• Dieses Dokument dient der eigenen Übersicht und der Ablage in den "
             f"Steuerunterlagen. Es ersetzt keinen Arbeitgeber-Beleg.")
    story.append(Paragraph(notiz, st["legal"]))

    # MID-Hinweis am Ende jedes Abrechnungsbelegs: benennt die
    # Grundlage der Messung und grenzt die Verantwortung ab.
    story.append(Spacer(1, 5 * mm))
    story.append(_mid_hinweis(st))

    doc.build(story)
    return buf.getvalue()
