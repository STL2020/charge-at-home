# Entpacke-Nach-C-Projekte.ps1
# Charge@Home - Entpack-Skript mit Datenpersistenz
#
# VEREINFACHT (siehe Pflichtenheft-Changelog): Der Verschluesselungs-Schluessel
# fuer Loxone-Passwoerter liegt seit dieser Version NICHT mehr im Projektordner,
# sondern in einem stabilen, betriebssystemueblichen Nutzerverzeichnis
# (%APPDATA%\.charge-at-home\encryption_key) - dieses Skript muss ihn deshalb
# nicht mehr sichern/wiederherstellen. Das behebt "fehler: entschluesselung"
# dauerhaft, unabhaengig davon, ob dieses Skript ueberhaupt verwendet wird.

$ErrorActionPreference = "Stop"
$zielOrdner = "C:\Projekte"
$projektName = "charge-at-home"

Write-Host "Charge@Home - Entpack-Skript" -ForegroundColor Cyan
Write-Host ""

# Frueh aus dem Zielordner heraustreten: Wird das Skript von dort gestartet,
# blockiert das eigene Arbeitsverzeichnis spaeter das Loeschen.
$aktuellerPfad = (Get-Location).Path
if ($aktuellerPfad -like "$zielOrdner\$projektName*") {
    Write-Host "Wechsle aus dem Zielordner heraus ..." -ForegroundColor Yellow
    Set-Location $zielOrdner
}


# Neueste ZIP-Datei finden (Wildcard, keine starren Versionsnamen)
$suchPfade = @(
    (Join-Path $PSScriptRoot "Charge-at-Home_Sprint*.zip"),
    (Join-Path $env:USERPROFILE "Downloads\Charge-at-Home_Sprint*.zip")
)
$alleZips = @()
foreach ($pfad in $suchPfade) {
    $alleZips += Get-ChildItem -Path $pfad -ErrorAction SilentlyContinue
}
$alleZips = $alleZips | Sort-Object FullName -Unique | Sort-Object LastWriteTime -Descending
if ($alleZips.Count -eq 0) {
    Write-Host "FEHLER: Keine Charge-at-Home_Sprint*.zip gefunden (weder hier noch in Downloads)." -ForegroundColor Red
    exit 1
}
$neuesteZip = $alleZips[0]
Write-Host "Verwende: $($neuesteZip.Name) (zuletzt geaendert: $($neuesteZip.LastWriteTime))"

$zielProjekt = Join-Path $zielOrdner $projektName
$datenOrdner = Join-Path $zielProjekt "data"
$dbDatei = Join-Path $datenOrdner "charging.db"
$dbDateiWal = Join-Path $datenOrdner "charging.db-wal"
$dbDateiShm = Join-Path $datenOrdner "charging.db-shm"
$dokumenteOrdner = Join-Path $datenOrdner "documents"

$sicherungOrdner = Join-Path $env:TEMP "charge-at-home-backup-$(Get-Date -Format 'yyyyMMddHHmmss')"
$hatBestand = Test-Path $dbDatei

if ($hatBestand) {
    Write-Host "Bestehende Installation gefunden - sichere Daten nach $sicherungOrdner ..." -ForegroundColor Yellow
    New-Item -ItemType Directory -Path $sicherungOrdner -Force | Out-Null

    Copy-Item $dbDatei (Join-Path $sicherungOrdner "charging.db") -Force
    if (Test-Path $dbDateiWal) { Copy-Item $dbDateiWal (Join-Path $sicherungOrdner "charging.db-wal") -Force }
    if (Test-Path $dbDateiShm) { Copy-Item $dbDateiShm (Join-Path $sicherungOrdner "charging.db-shm") -Force }

    if (Test-Path $dokumenteOrdner) {
        Copy-Item $dokumenteOrdner (Join-Path $sicherungOrdner "documents") -Recurse -Force
        Write-Host "  - Generierte Belege (documents/) gesichert."
    }
    Write-Host "  - Datenbank gesichert. (Verschluesselungs-Schluessel liegt jetzt ausserhalb des Projektordners und muss nicht mehr gesichert werden.)" -ForegroundColor Green
}

if (Test-Path $zielProjekt) {
    # WICHTIG: Aus dem Zielordner heraustreten, bevor er geloescht wird.
    # Wird das Skript aus C:\Projekte\charge-at-home gestartet, verweigert
    # Windows das Loeschen mit "because it is in use" - gemeint ist dann nicht
    # ein fremder Prozess, sondern das eigene Arbeitsverzeichnis.
    Set-Location $env:TEMP

    # Laufende Instanzen beenden: Eine gestartete Anwendung haelt die
    # Datenbankdatei offen und blockiert das Loeschen ebenfalls.
    $laufende = Get-Process python, pythonw -ErrorAction SilentlyContinue |
        Where-Object { $_.Path -like "$zielProjekt*" -or $_.Path -like "*charge-at-home*" }
    if ($laufende) {
        Write-Host "Beende laufende Instanz ..." -ForegroundColor Yellow
        $laufende | Stop-Process -Force -ErrorAction SilentlyContinue
        Start-Sleep -Seconds 2
    }

    Write-Host "Loesche alte Projektdateien (Daten bleiben gesichert) ..."
    # Drei Versuche: Virenscanner und der Explorer greifen manchmal noch kurz
    # auf gerade freigegebene Dateien zu.
    $geloescht = $false
    for ($versuch = 1; $versuch -le 3; $versuch++) {
        try {
            Remove-Item $zielProjekt -Recurse -Force -ErrorAction Stop
            $geloescht = $true
            break
        } catch {
            if ($versuch -lt 3) {
                Write-Host "  Ordner noch belegt, warte 3 Sekunden (Versuch $versuch von 3) ..." -ForegroundColor Yellow
                Start-Sleep -Seconds 3
            }
        }
    }
    if (-not $geloescht) {
        Write-Host ""
        Write-Host "FEHLER: $zielProjekt laesst sich nicht loeschen." -ForegroundColor Red
        Write-Host ""
        Write-Host "Haeufige Ursachen:" -ForegroundColor Yellow
        Write-Host "  * Die Anwendung laeuft noch - das schwarze Fenster schliessen"
        Write-Host "  * Der Ordner ist im Explorer geoeffnet - Fenster schliessen"
        Write-Host "  * Eine Datei ist im Editor offen"
        Write-Host ""
        Write-Host "Die Sicherung liegt unter:" -ForegroundColor Cyan
        Write-Host "  $sicherungOrdner"
        Write-Host ""
        exit 1
    }
}

Write-Host "Entpacke $($neuesteZip.Name) nach $zielOrdner ..."
Expand-Archive -Path $neuesteZip.FullName -DestinationPath $zielOrdner -Force

if ($hatBestand) {
    Write-Host "Stelle gesicherte Daten wieder her ..." -ForegroundColor Yellow
    New-Item -ItemType Directory -Path $datenOrdner -Force | Out-Null

    Copy-Item (Join-Path $sicherungOrdner "charging.db") $dbDatei -Force
    if (Test-Path (Join-Path $sicherungOrdner "charging.db-wal")) {
        Copy-Item (Join-Path $sicherungOrdner "charging.db-wal") $dbDateiWal -Force
    }
    if (Test-Path (Join-Path $sicherungOrdner "charging.db-shm")) {
        Copy-Item (Join-Path $sicherungOrdner "charging.db-shm") $dbDateiShm -Force
    }
    if (Test-Path (Join-Path $sicherungOrdner "documents")) {
        Copy-Item (Join-Path $sicherungOrdner "documents") $dokumenteOrdner -Recurse -Force
    }
    Write-Host "Setup und alle Daten (inkl. Loxone-Zugangsdaten) bleiben erhalten." -ForegroundColor Green
}

Write-Host ""
Write-Host "Fertig. Starte die Anwendung ..." -ForegroundColor Cyan
Set-Location $zielProjekt
Start-Process -FilePath (Join-Path $zielProjekt "start.bat")
Start-Sleep -Seconds 5
Start-Process "http://localhost:8501"
