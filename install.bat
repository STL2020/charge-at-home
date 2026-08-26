@echo off
REM ===================================================================
REM  eCharge@Home — Installation fuer Windows
REM
REM  Prueft die Voraussetzungen, installiert Python bei Bedarf ueber
REM  winget und richtet die Anwendung ein.
REM
REM  Rechtsklick auf diese Datei → "Als Administrator ausfuehren",
REM  falls Python nachinstalliert werden soll.
REM ===================================================================

cd /d "%~dp0"
setlocal enabledelayedexpansion
color 0F

echo.
echo   +----------------------------------------------------------+
echo   ^|                                                          ^|
echo   ^|   eCharge@Home — Installation                            ^|
echo   ^|   Ladeabrechnung und Fahrtenbuch                         ^|
echo   ^|                                                          ^|
echo   +----------------------------------------------------------+
echo.
echo   Dieses Skript richtet die Anwendung ein. Es prueft, was
echo   vorhanden ist, und installiert Fehlendes nach.
echo.
pause

REM ── 1. Docker? ────────────────────────────────────────────────────
echo.
echo   1. System pruefen
echo   --------------------------------------------------------------
where docker >nul 2>nul
if %ERRORLEVEL% EQU 0 (
    docker info >nul 2>nul
    if !ERRORLEVEL! EQU 0 (
        echo    [OK] Docker gefunden und laeuft
        goto :mit_docker
    ) else (
        echo    [!]  Docker ist installiert, laeuft aber nicht.
        echo         Bitte Docker Desktop starten und erneut versuchen.
        echo.
        echo         Alternativ ohne Docker fortfahren.
        echo.
        choice /C JN /M "   Ohne Docker fortfahren"
        if !ERRORLEVEL! EQU 2 exit /b 1
    )
) else (
    echo    [ ]  Docker nicht gefunden — Installation mit Python
)

REM ── 2. Python? ────────────────────────────────────────────────────
echo.
where python >nul 2>nul
if %ERRORLEVEL% EQU 0 (
    for /f "tokens=2" %%v in ('python --version 2^>^&1') do set PYVER=%%v
    echo    [OK] Python !PYVER! gefunden
    goto :python_da
)

echo    [ ]  Python nicht gefunden
echo.
echo    Python wird fuer den Betrieb ohne Docker benoetigt.
echo.

REM winget ist ab Windows 10 (21H1) und Windows 11 vorhanden
where winget >nul 2>nul
if %ERRORLEVEL% EQU 0 (
    echo    Es kann automatisch installiert werden.
    echo.
    choice /C JN /M "   Python jetzt installieren"
    if !ERRORLEVEL! EQU 1 (
        echo.
        echo    Installiere Python — das dauert einige Minuten ...
        winget install --id Python.Python.3.12 --scope machine ^
               --accept-package-agreements --accept-source-agreements
        if !ERRORLEVEL! NEQ 0 (
            echo.
            echo    [X]  Die Installation ist fehlgeschlagen.
            echo         Bitte von Hand installieren:
            echo         https://www.python.org/downloads/
            echo.
            echo         WICHTIG: Beim Installieren "Add Python to PATH"
            echo         ankreuzen.
            echo.
            pause
            exit /b 1
        )
        echo.
        echo    [OK] Python installiert.
        echo.
        echo    Bitte dieses Fenster SCHLIESSEN und install.bat erneut
        echo    starten — Windows muss die Pfadangabe neu einlesen.
        echo.
        pause
        exit /b 0
    ) else (
        echo.
        echo    Ohne Python kann die Anwendung nicht starten.
        echo    https://www.python.org/downloads/
        echo.
        pause
        exit /b 1
    )
) else (
    echo    [X]  Automatische Installation nicht moeglich
    echo         ^(winget fehlt — Windows 10 aelter als 21H1^)
    echo.
    echo         Bitte von Hand installieren:
    echo         https://www.python.org/downloads/
    echo.
    echo         WICHTIG: "Add Python to PATH" ankreuzen.
    echo.
    pause
    exit /b 1
)

:python_da
REM ── 3. Arbeitsumgebung ────────────────────────────────────────────
echo.
echo   2. Arbeitsumgebung einrichten
echo   --------------------------------------------------------------
if not exist ".venv" (
    echo    Lege Arbeitsumgebung an ...
    python -m venv .venv
    if %ERRORLEVEL% NEQ 0 (
        echo    [X]  Fehlgeschlagen.
        pause
        exit /b 1
    )
)
call .venv\Scripts\activate.bat
echo    [OK] Arbeitsumgebung bereit

echo.
echo    Lade benoetigte Pakete — beim ersten Mal dauert das
echo    einige Minuten ...
pip install --quiet --disable-pip-version-check --upgrade pip
pip install --quiet --disable-pip-version-check -r requirements.txt
if %ERRORLEVEL% NEQ 0 (
    echo    [X]  Die Pakete konnten nicht geladen werden.
    echo         Besteht eine Internetverbindung?
    pause
    exit /b 1
)
echo    [OK] Pakete geladen

REM ── 4. Verknuepfung ───────────────────────────────────────────────
echo.
echo   3. Verknuepfung anlegen
echo   --------------------------------------------------------------
choice /C JN /M "   Verknuepfung auf dem Desktop anlegen"
if %ERRORLEVEL% EQU 1 (
    powershell -NoProfile -Command ^
      "$s=(New-Object -COM WScript.Shell).CreateShortcut([Environment]::GetFolderPath('Desktop')+'\eCharge@Home.lnk'); $s.TargetPath='%~dp0start.bat'; $s.WorkingDirectory='%~dp0'; $s.Description='eCharge@Home starten'; $s.Save()" >nul 2>nul
    if !ERRORLEVEL! EQU 0 (
        echo    [OK] Verknuepfung angelegt
    ) else (
        echo    [!]  Verknuepfung konnte nicht angelegt werden
    )
)

echo.
choice /C JN /M "   Beim Anmelden automatisch starten"
if %ERRORLEVEL% EQU 1 (
    powershell -NoProfile -Command ^
      "$s=(New-Object -COM WScript.Shell).CreateShortcut([Environment]::GetFolderPath('Startup')+'\eCharge@Home.lnk'); $s.TargetPath='%~dp0start.bat'; $s.WorkingDirectory='%~dp0'; $s.Save()" >nul 2>nul
    echo    [OK] Autostart eingerichtet
    echo.
    echo    Hinweis: Damit nachts geladene Strommengen erfasst werden,
    echo    darf der Rechner nicht in den Ruhezustand wechseln.
    echo    Einstellen unter: Systemsteuerung — Energieoptionen
)

REM ── 5. Fertig ─────────────────────────────────────────────────────
echo.
echo   Fertig
echo   --------------------------------------------------------------
echo.
echo    Starten mit einem Doppelklick auf start.bat
echo    oder ueber die Verknuepfung auf dem Desktop.
echo.
echo    Die Anwendung laeuft dann unter:
echo        http://localhost:8501
echo.
echo    Anleitungen liegen im Ordner doku
echo.
choice /C JN /M "   Jetzt starten"
if %ERRORLEVEL% EQU 1 (
    start "" "%~dp0start.bat"
)
exit /b 0


:mit_docker
echo.
echo   2. Mit Docker einrichten
echo   --------------------------------------------------------------
echo    Baue das Abbild — beim ersten Mal dauert das einige Minuten ...
echo.
docker compose up -d --build
if %ERRORLEVEL% NEQ 0 (
    echo.
    echo    [X]  Der Start ist fehlgeschlagen.
    echo         Meldungen ansehen mit: docker compose logs
    pause
    exit /b 1
)
echo.
echo   Fertig
echo   --------------------------------------------------------------
echo.
echo    Die Anwendung laeuft im Hintergrund und startet ab jetzt
echo    bei jedem Neustart des Rechners von selbst.
echo.
echo    Aufrufen unter:  http://localhost:8501
echo.
echo    Anhalten mit:    docker compose down
echo.
pause
start "" http://localhost:8501
exit /b 0
