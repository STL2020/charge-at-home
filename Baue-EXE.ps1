# Baue-EXE.ps1
# eCharge@Home - Erzeugt eine einzelne Windows-Datei zum Doppelklick
#
# WARUM EINE EXE
# --------------
# Die Zielgruppe sind Aussendienstmitarbeiter mit Firmenlaptop - Menschen, die
# abrechnen wollen, nicht Docker einrichten. Eine Datei, Doppelklick, Browser
# oeffnet sich: Das ist der Unterschied zwischen "benutzt es" und "hat es nie
# installiert".
#
# WAS DIESES SKRIPT TUT
# ---------------------
#   1. Prueft Python und installiert PyInstaller, falls noetig
#   2. Buendelt Flask, SQLite, ReportLab und alle Vorlagen in eine Datei
#   3. Legt das Ergebnis in dist\eChargeHome.exe ab
#
# AUSFUEHREN
#   .\Baue-EXE.ps1
# Bei blockierter Ausfuehrung:
#   powershell -ExecutionPolicy Bypass -File .\Baue-EXE.ps1

$ErrorActionPreference = "Stop"
$projektWurzel = $PSScriptRoot
$appOrdner     = Join-Path $projektWurzel "app"
$ausgabe       = Join-Path $projektWurzel "dist"

Write-Host ""
Write-Host "eCharge@Home - EXE erstellen" -ForegroundColor Cyan
Write-Host "=============================" -ForegroundColor Cyan
Write-Host ""

# -- 1. Voraussetzungen -----------------------------------------------------
try {
    $pythonVersion = & python --version 2>&1
    Write-Host "Python gefunden: $pythonVersion"
} catch {
    Write-Host "FEHLER: Python ist nicht installiert oder nicht im PATH." -ForegroundColor Red
    Write-Host "  Herunterladen: https://www.python.org/downloads/"
    Write-Host "  Bei der Installation 'Add Python to PATH' ankreuzen."
    exit 1
}

Write-Host "Pruefe PyInstaller ..."
$hatPyInstaller = & python -m PyInstaller --version 2>&1
if ($LASTEXITCODE -ne 0) {
    Write-Host "  wird installiert ..." -ForegroundColor Yellow
    & python -m pip install --quiet pyinstaller
    if ($LASTEXITCODE -ne 0) {
        Write-Host "FEHLER: PyInstaller konnte nicht installiert werden." -ForegroundColor Red
        exit 1
    }
}

Write-Host "Pruefe Abhaengigkeiten ..."
& python -m pip install --quiet -r (Join-Path $projektWurzel "requirements.txt")

# -- 2. Startdatei erzeugen -------------------------------------------------
# Eine eigene Startdatei statt app.py direkt: Sie oeffnet den Browser und
# legt die Daten im Benutzerverzeichnis ab statt neben der EXE - sonst
# lieferte jedes Update eine leere Datenbank mit.
$starter = Join-Path $appOrdner "_exe_start.py"
@'
"""Startpunkt der Windows-Fassung.

Legt die Datenbank im Benutzerverzeichnis ab (%APPDATA%\eChargeHome), damit
sie ein Update der EXE ueberlebt, und oeffnet den Browser automatisch.
"""
import os
import sys
import threading
import webbrowser

# Datenverzeichnis VOR dem Import der Anwendung setzen - sie liest den Pfad
# beim Laden aus der Umgebung.
datenordner = os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")),
                           "eChargeHome")
os.makedirs(os.path.join(datenordner, "documents"), exist_ok=True)
os.environ.setdefault("CHARGE_DB_PATH", os.path.join(datenordner, "charging.db"))

# Bei PyInstaller liegen die Vorlagen im entpackten Temporaerordner
if getattr(sys, "frozen", False):
    os.chdir(sys._MEIPASS)
    sys.path.insert(0, sys._MEIPASS)

from app import app  # noqa: E402

PORT = 8501


def browser_oeffnen():
    import time
    time.sleep(1.5)
    webbrowser.open(f"http://localhost:{PORT}")


if __name__ == "__main__":
    print()
    print("  eCharge@Home laeuft")
    print(f"  Oeffne http://localhost:{PORT}")
    print(f"  Daten:  {datenordner}")
    print()
    print("  Zum Beenden dieses Fenster schliessen.")
    print()
    threading.Thread(target=browser_oeffnen, daemon=True).start()
    app.run(host="127.0.0.1", port=PORT, debug=False, use_reloader=False)
'@ | Set-Content -Path $starter -Encoding UTF8

Write-Host "Startdatei erzeugt."

# -- 3. Bauen ---------------------------------------------------------------
Write-Host ""
Write-Host "Baue EXE - das dauert ein bis drei Minuten ..." -ForegroundColor Yellow

Push-Location $appOrdner
try {
    $argumente = @(
        "-m", "PyInstaller",
        "--onefile",                       # eine einzige Datei
        "--name", "eChargeHome",
        "--distpath", $ausgabe,
        "--workpath", (Join-Path $projektWurzel "build"),
        "--specpath", (Join-Path $projektWurzel "build"),
        "--add-data", "templates;templates",
        "--add-data", "static;static",
        "--add-data", "i18n;i18n",
        "--add-data", "../data/schema.sql;data",
        "--hidden-import", "reportlab.graphics.barcode.code128",
        "--hidden-import", "reportlab.pdfbase._fontdata_enc_winansi",
        "--collect-submodules", "reportlab",
        "--noconfirm",
        "--clean",
        "_exe_start.py"
    )
    # Icon nur mitgeben, wenn vorhanden - ein fehlendes bricht den Bau sonst ab
    $icon = Join-Path $appOrdner "static\favicon.ico"
    if (Test-Path $icon) { $argumente += @("--icon", $icon) }

    & python @argumente
    if ($LASTEXITCODE -ne 0) { throw "PyInstaller meldete einen Fehler." }
} finally {
    Pop-Location
    Remove-Item $starter -ErrorAction SilentlyContinue
}

# -- 4. Ergebnis ------------------------------------------------------------
$exe = Join-Path $ausgabe "eChargeHome.exe"
if (Test-Path $exe) {
    $groesse = [math]::Round((Get-Item $exe).Length / 1MB, 1)
    Write-Host ""
    Write-Host "Fertig." -ForegroundColor Green
    Write-Host "  $exe"
    Write-Host "  Groesse: $groesse MB"
    Write-Host ""
    Write-Host "Weitergabe an Kunden:" -ForegroundColor Cyan
    Write-Host "  Diese eine Datei genuegt. Doppelklick startet die Anwendung"
    Write-Host "  und oeffnet den Browser. Es wird nichts installiert."
    Write-Host ""
    Write-Host "  Die Daten liegen unter %APPDATA%\eChargeHome und bleiben"
    Write-Host "  bei einem Austausch der EXE erhalten."
    Write-Host ""
    Write-Host "Alternative ohne SmartScreen-Warnung:" -ForegroundColor Cyan
    Write-Host "  .\Baue-Portable.ps1 erzeugt ein Paket ohne eigene EXE."
    Write-Host "  Es enthaelt die signierte Python-Fassung von python.org,"
    Write-Host "  wird per CMD-Datei gestartet und loest keine Warnung aus."
    Write-Host ""
    Write-Host "Hinweis zu Windows SmartScreen:" -ForegroundColor Yellow
    Write-Host "  Ohne Code-Signatur meldet Windows beim ersten Start eine"
    Write-Host "  Warnung. Der Kunde klickt 'Weitere Informationen' und dann"
    Write-Host "  'Trotzdem ausfuehren'. Ein Signaturzertifikat kostet rund"
    Write-Host "  250 Euro im Jahr und beseitigt die Meldung."
} else {
    Write-Host "FEHLER: Es wurde keine EXE erzeugt. Siehe Ausgabe oben." -ForegroundColor Red
    exit 1
}
