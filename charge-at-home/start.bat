@echo off
REM ===================================================================
REM  eCharge@Home — Start (Windows)
REM
REM  Nutzt Docker, wenn vorhanden. Sonst wird die Anwendung unmittelbar
REM  mit Python gestartet — dann muss dieses Fenster geoeffnet bleiben.
REM ===================================================================

cd /d "%~dp0"
setlocal enabledelayedexpansion

echo.
echo  ============================================
echo   eCharge@Home
echo   Ladeabrechnung und Fahrtenbuch
echo  ============================================
echo.

REM Eigene Netzwerkadresse ermitteln, damit der Aufruf vom Handy klappt
set LANIP=localhost
for /f "tokens=2 delims=:" %%a in ('ipconfig ^| findstr /r "IPv4.*10\.\|IPv4.*192\.\|IPv4.*172\."') do (
    set LANIP=%%a
    goto :ip_gefunden
)
:ip_gefunden
set LANIP=%LANIP: =%

where docker >nul 2>nul
if %ERRORLEVEL% EQU 0 goto :mit_docker
goto :ohne_docker


:mit_docker
echo  Docker gefunden — starte im Hintergrund.
echo.
docker compose up --build -d
if %ERRORLEVEL% NEQ 0 (
    echo.
    echo  Der Start ist fehlgeschlagen. Laeuft Docker Desktop?
    echo.
    pause
    exit /b 1
)
echo.
echo  ============================================
echo   Laeuft im Hintergrund
echo  ============================================
echo.
echo   Aufrufen:     http://localhost:8501
echo   Im Netzwerk:  http://%LANIP%:8501
echo.
echo   Der Container startet ab jetzt bei jedem
echo   Neustart des Rechners von selbst.
echo.
echo   Anhalten mit: docker compose down
echo.
echo  Dieses Fenster kann geschlossen werden.
echo.
pause
exit /b 0


:ohne_docker
echo  Docker nicht gefunden — starte unmittelbar mit Python.
echo.

where python >nul 2>nul
if %ERRORLEVEL% NEQ 0 (
    echo  FEHLER: Python wurde nicht gefunden.
    echo.
    echo  Bitte Python 3.10 oder neuer installieren:
    echo    https://www.python.org/downloads/
    echo.
    echo  Beim Installieren "Add Python to PATH" ankreuzen.
    echo.
    pause
    exit /b 1
)

if not exist ".venv" (
    echo  [1/3] Richte Arbeitsumgebung ein — das dauert beim ersten Mal
    echo        einige Minuten ...
    python -m venv .venv
)
call .venv\Scripts\activate.bat

echo  [2/3] Pruefe benoetigte Pakete ...
pip install --quiet --disable-pip-version-check -r requirements.txt

echo  [3/3] Starte ...
echo.
echo  ============================================
echo   WICHTIG
echo  ============================================
echo.
echo   Dieses Fenster muss GEOEFFNET BLEIBEN.
echo   Wird es geschlossen, ist die Anwendung
echo   nicht mehr erreichbar.
echo.
echo   Bei angebundener Wallbox ueber OCPP gilt:
echo   Der Rechner muss laufen, wenn geladen wird
echo   - also meist nachts. Sonst gehen Lade-
echo   vorgaenge verloren.
echo.
echo   Dauerbetrieb? Dann besser Docker auf einem
echo   NAS oder Raspberry Pi. Siehe INSTALLATION.md
echo.
echo  --------------------------------------------
echo.
echo   Aufrufen:     http://localhost:8501
echo   Im Netzwerk:  http://%LANIP%:8501
echo.
echo   Beenden mit Strg+C
echo.

cd app
start "" http://localhost:8501
python app.py

echo.
echo  Die Anwendung wurde beendet.
pause
