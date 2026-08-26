# Baue-Portable.ps1
# eCharge@Home - Portables Windows-Paket
#
# WARUM DIESER WEG STATT EINER EXE
# --------------------------------
# Eine selbst erstellte EXE loest bei Windows SmartScreen eine Warnung aus
# ("Der Computer wurde geschuetzt"). Sie verschwindet erst mit einem
# Code-Signatur-Zertifikat fuer rund 250 Euro im Jahr - oder wenn genuegend
# Menschen die Datei heruntergeladen haben, was bei kleinen Stueckzahlen nie
# eintritt.
#
# Dieses Paket umgeht das Problem, statt es zu bezahlen: Es enthaelt keine
# selbst erstellte EXE, sondern die offizielle "embeddable" Python-Fassung von
# python.org - signiert von der Python Software Foundation. Gestartet wird
# ueber eine Verknuepfung. SmartScreen hat damit nichts zu pruefen.
#
# Weitere Vorteile:
#   * Keine Installation, kein Administrator noetig
#   * Laeuft vom USB-Stick
#   * Ein Update tauscht nur den Programmordner, Daten bleiben
#
# AUSFUEHREN
#   .\Baue-Portable.ps1

$ErrorActionPreference = "Stop"
$projektWurzel = $PSScriptRoot
$paketName     = "eChargeHome-Portable"
$ausgabe       = Join-Path $projektWurzel $paketName
$pythonVersion = "3.12.7"

Write-Host ""
Write-Host "eCharge@Home - Portables Paket erstellen" -ForegroundColor Cyan
Write-Host "=========================================" -ForegroundColor Cyan
Write-Host ""

# -- 1. Aufraeumen -----------------------------------------------------------
if (Test-Path $ausgabe) {
    Write-Host "Entferne vorheriges Paket ..."
    Remove-Item $ausgabe -Recurse -Force
}
New-Item -ItemType Directory -Path $ausgabe -Force | Out-Null

# -- 2. Python holen --------------------------------------------------------
# Die embeddable-Fassung ist ein ZIP ohne Installer: entpacken genuegt.
$pythonOrdner = Join-Path $ausgabe "python"
$pythonZip    = Join-Path $env:TEMP "python-embed-$pythonVersion.zip"
$pythonUrl    = "https://www.python.org/ftp/python/$pythonVersion/python-$pythonVersion-embed-amd64.zip"

if (-not (Test-Path $pythonZip)) {
    Write-Host "Lade Python $pythonVersion (etwa 11 MB) ..." -ForegroundColor Yellow
    Invoke-WebRequest -Uri $pythonUrl -OutFile $pythonZip
}
Write-Host "Entpacke Python ..."
Expand-Archive -Path $pythonZip -DestinationPath $pythonOrdner -Force

# Die embeddable-Fassung sucht Pakete standardmaessig nicht im Ordner.
# Diese Zeile schaltet das frei - ohne sie findet Python die Bibliotheken nicht.
$pthDatei = Get-ChildItem -Path $pythonOrdner -Filter "python*._pth" | Select-Object -First 1
if ($pthDatei) {
    $inhalt = Get-Content $pthDatei.FullName
    $inhalt = $inhalt -replace '^#\s*import site', 'import site'
    if ($inhalt -notcontains "Lib\site-packages") { $inhalt += "Lib\site-packages" }
    if ($inhalt -notcontains "..\app") { $inhalt += "..\app" }
    $inhalt | Set-Content $pthDatei.FullName
}

# -- 3. Abhaengigkeiten installieren -----------------------------------------
Write-Host "Richte pip ein ..."
$getPip = Join-Path $env:TEMP "get-pip.py"
if (-not (Test-Path $getPip)) {
    Invoke-WebRequest -Uri "https://bootstrap.pypa.io/get-pip.py" -OutFile $getPip
}
& (Join-Path $pythonOrdner "python.exe") $getPip --no-warn-script-location 2>&1 | Out-Null

Write-Host "Installiere Abhaengigkeiten ..." -ForegroundColor Yellow
$zielPakete = Join-Path $pythonOrdner "Lib\site-packages"
& (Join-Path $pythonOrdner "python.exe") -m pip install `
    --target $zielPakete `
    --no-warn-script-location `
    --quiet `
    -r (Join-Path $projektWurzel "requirements.txt")

if ($LASTEXITCODE -ne 0) {
    Write-Host "FEHLER: Abhaengigkeiten konnten nicht installiert werden." -ForegroundColor Red
    exit 1
}

# -- 4. Anwendung kopieren --------------------------------------------------
Write-Host "Kopiere Anwendung ..."
Copy-Item (Join-Path $projektWurzel "app") (Join-Path $ausgabe "app") -Recurse -Force
Copy-Item (Join-Path $projektWurzel "data") (Join-Path $ausgabe "data") -Recurse -Force `
    -Exclude "charging.db", "*.db-wal", "*.db-shm", "*.log"

# Aufraeumen: Zwischenstaende gehoeren nicht ins Auslieferungspaket
Get-ChildItem -Path (Join-Path $ausgabe "app") -Include "__pycache__" -Recurse -Directory |
    Remove-Item -Recurse -Force -ErrorAction SilentlyContinue

# -- 5. Startdatei ----------------------------------------------------------
# Die Daten liegen im Paketordner, damit das Ganze vom USB-Stick laeuft und
# beim Kopieren alles mitkommt.
$starter = Join-Path $ausgabe "app\_portable_start.py"
@'
"""Startpunkt der portablen Fassung.

Legt die Daten neben dem Programm ab, damit das Paket vollstaendig
kopierbar bleibt (etwa auf einen USB-Stick), und oeffnet den Browser.
"""
import os
import sys
import threading
import webbrowser

basis = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
daten = os.path.join(basis, "daten")
os.makedirs(os.path.join(daten, "documents"), exist_ok=True)
os.environ.setdefault("CHARGE_DB_PATH", os.path.join(daten, "charging.db"))

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.chdir(os.path.dirname(os.path.abspath(__file__)))

from app import app  # noqa: E402

PORT = 8501


def browser_oeffnen():
    import time
    time.sleep(2)
    webbrowser.open(f"http://localhost:{PORT}")


if __name__ == "__main__":
    print()
    print("   eCharge@Home")
    print("   " + "-" * 46)
    print(f"   Adresse:  http://localhost:{PORT}")
    print(f"   Daten:    {daten}")
    print()
    print("   Der Browser oeffnet sich gleich von selbst.")
    print("   Zum Beenden dieses Fenster schliessen.")
    print()
    threading.Thread(target=browser_oeffnen, daemon=True).start()
    app.run(host="127.0.0.1", port=PORT, debug=False, use_reloader=False)
'@ | Set-Content -Path $starter -Encoding UTF8

# -- 6. Start-Verknuepfung ---------------------------------------------------
# Eine CMD-Datei statt einer EXE: Windows prueft sie nicht ueber SmartScreen.
@'
@echo off
title eCharge@Home
cd /d "%~dp0"
python\python.exe app\_portable_start.py
if errorlevel 1 (
  echo.
  echo Beim Start ist ein Fehler aufgetreten.
  echo Bitte diese Meldung an den Support weitergeben.
  echo.
  pause
)
'@ | Set-Content -Path (Join-Path $ausgabe "eCharge@Home starten.cmd") -Encoding ASCII

# -- 7. Anleitung -----------------------------------------------------------
@'
eCharge@Home - Portable Fassung
================================

STARTEN
-------
  Doppelklick auf "eCharge@Home starten.cmd".
  Der Browser oeffnet sich nach wenigen Sekunden von selbst.
  Erscheint er nicht: http://localhost:8501 von Hand aufrufen.

  Zum Beenden das schwarze Fenster schliessen.


INSTALLATION
------------
  Keine. Diesen Ordner an einen beliebigen Ort kopieren - etwa
  C:\Programme\eChargeHome oder auf einen USB-Stick.
  Es wird nichts in die Registrierung geschrieben und keine
  Administratorberechtigung benoetigt.


IHRE DATEN
----------
  Alles liegt im Unterordner "daten":
    charging.db    Ladevorgaenge, Fahrten, Einstellungen
    documents\     erzeugte PDF-Belege

  Diesen Ordner regelmaessig sichern - oder in der Anwendung unter
  Einstellungen -> System -> Sicherung herunterladen.


UPDATE
------
  1. Ordner "daten" an einen sicheren Ort kopieren
  2. Neues Paket entpacken
  3. Ordner "daten" zurueckkopieren

  Ihre Daten bleiben dabei vollstaendig erhalten.


WINDOWS MELDET SICH BEIM ERSTEN START?
--------------------------------------
  Moeglich ist ein Hinweis der Firewall, weil die Anwendung einen
  lokalen Webserver startet. "Zugriff zulassen" fuer private Netzwerke
  genuegt - die Anwendung ist nur auf diesem Rechner erreichbar
  (127.0.0.1) und sendet nichts nach aussen.

  Wurde das Paket als ZIP heruntergeladen, kann Windows die Dateien
  blockieren. Rechtsklick auf die ZIP-Datei -> Eigenschaften ->
  unten "Zulassen" ankreuzen -> danach entpacken.


SYSTEMVORAUSSETZUNGEN
---------------------
  Windows 10 oder 11, 64 Bit. Sonst nichts - Python ist enthalten.
'@ | Set-Content -Path (Join-Path $ausgabe "LIESMICH.txt") -Encoding UTF8

# -- 8. Ergebnis ------------------------------------------------------------
$groesse = [math]::Round((Get-ChildItem $ausgabe -Recurse |
    Measure-Object -Property Length -Sum).Sum / 1MB, 1)

Write-Host ""
Write-Host "Fertig." -ForegroundColor Green
Write-Host "  $ausgabe"
Write-Host "  Groesse: $groesse MB"
Write-Host ""
Write-Host "Zum Testen:" -ForegroundColor Cyan
Write-Host "  Doppelklick auf '$paketName\eCharge@Home starten.cmd'"
Write-Host ""
Write-Host "Zur Weitergabe:" -ForegroundColor Cyan
Write-Host "  Den Ordner als ZIP packen. Der Kunde entpackt und startet -"
Write-Host "  keine Installation, keine SmartScreen-Warnung, kein Zertifikat."
Write-Host ""

$antwort = Read-Host "Jetzt als ZIP packen? (j/n)"
if ($antwort -eq "j") {
    $zipDatei = Join-Path $projektWurzel "$paketName.zip"
    if (Test-Path $zipDatei) { Remove-Item $zipDatei -Force }
    Compress-Archive -Path $ausgabe -DestinationPath $zipDatei
    $zipGroesse = [math]::Round((Get-Item $zipDatei).Length / 1MB, 1)
    Write-Host "  $zipDatei" -ForegroundColor Green
    Write-Host "  Groesse: $zipGroesse MB" -ForegroundColor Green
}
