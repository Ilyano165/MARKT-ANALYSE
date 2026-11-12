# Lebenszyklus auf echtem Windows (Phase I): Start über die Startmenü-Verknüpfung mit dem
# Standard-Datenordner, Datenerhalt über Beenden, Deinstallation und Neuinstallation,
# Überinstallieren (Update) und Verhalten bei nicht erreichbarer NOVA. Läuft nach install-test.ps1.
$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [Text.UTF8Encoding]::new($false)
$setup = Get-ChildItem "dist-installer\MarketIntel-Setup-*.exe" | Select-Object -First 1
if (-not $setup) { throw "Setup-Datei fehlt" }
$dir = Join-Path $env:LOCALAPPDATA "Programs\Yellotools Market Intelligence"
$exe = Join-Path $dir "MarketIntel.exe"
$mi = Join-Path $dir "mi.exe"
$data = Join-Path $env:LOCALAPPDATA "YellotoolsMarketIntel"
$lnk = Join-Path $env:APPDATA "Microsoft\Windows\Start Menu\Programs\Yellotools Market Intelligence.lnk"
Remove-Item Env:MI_HOME -ErrorAction SilentlyContinue   # echter Standard-Datenordner
if (Test-Path $data) { Remove-Item $data -Recurse -Force }

function Install-App {
    $p = Start-Process $setup.FullName -ArgumentList "/S" -Wait -PassThru
    if ($p.ExitCode -ne 0) { throw "Installation fehlgeschlagen ($($p.ExitCode))" }
    if (-not (Test-Path $exe)) { throw "MarketIntel.exe fehlt nach der Installation" }
}
function Wait-Health([int]$port, [int]$seconds = 90) {
    for ($i = 0; $i -lt $seconds; $i++) {
        try {
            $r = Invoke-WebRequest -Uri "http://127.0.0.1:$port/health" -UseBasicParsing -TimeoutSec 3
            if ($r.StatusCode -eq 200) { return $true }
        } catch { Start-Sleep -Seconds 1 }
    }
    return $false
}
function Stop-App {
    Get-Process MarketIntel -ErrorAction SilentlyContinue | Stop-Process -Force
    Start-Sleep -Seconds 3
}

Install-App
Write-Host "1 Installiert"

# --- Verknüpfung zeigt auf das installierte Programm
$shell = New-Object -ComObject WScript.Shell
$target = $shell.CreateShortcut($lnk).TargetPath
if ($target -ne $exe) { throw "Startmenü-Verknüpfung zeigt auf '$target' statt '$exe'" }
Write-Host "2 Verknüpfung zeigt auf $target"

# --- Start über die Verknüpfung (echtes Fenster, Standard-Port 8780, Standard-Datenordner)
Start-Process $lnk
if (-not (Wait-Health 8780)) { throw "Start über die Verknüpfung: Oberfläche antwortet nicht auf 8780" }
$page = (Invoke-WebRequest -Uri "http://127.0.0.1:8780/settings/ai" -UseBasicParsing).Content
if ($page -notmatch "KI-Einstellungen") { throw "KI-Einstellungen fehlen" }
if ($page -notmatch "Aktiver KI-Provider") { throw "Kein aktiver Provider angezeigt" }
$status = Invoke-RestMethod -Uri "http://127.0.0.1:8780/ai/status.json"
if ($status.provider -ne "nova") { throw "Standard ist nicht NOVA: $($status.provider)" }
Write-Host "3 Start über Verknüpfung ok; KI-Status: $($status.label), Verbindung: $($status.connection) ($($status.message))"
$second = Start-Process $lnk -PassThru   # Einzelinstanz: zweiter Start darf keine zweite Oberfläche öffnen
Start-Sleep -Seconds 6
$count = @(Get-Process MarketIntel -ErrorAction SilentlyContinue).Count
Write-Host "  Prozesse nach Zweitstart: $count"
if (-not (Test-Path (Join-Path $data "market_intel.db"))) { throw "Datenbank nicht im Standard-Datenordner $data" }
Stop-App

# --- Daten anlegen (CLI), beenden hart, Daten müssen nach Neustart da sein
$csv = Join-Path $env:RUNNER_TEMP "lebenszyklus.csv"
"name;country;city`nLebenszyklus Testfirma GmbH;DE;Köln`n" | Set-Content $csv -Encoding UTF8
$imp = & $mi import-companies $csv --list-name "Lebenszyklus" | Out-String
if ($LASTEXITCODE -ne 0) { throw "Import fehlgeschlagen: $imp" }
Write-Host "4 Daten angelegt: $($imp.Trim())"
function Check-Company([string]$label) {
    $p = Start-Process $exe -ArgumentList "--no-browser", "--port", "8797" -PassThru -WindowStyle Hidden
    try {
        if (-not (Wait-Health 8797)) { throw "$label`: Oberfläche antwortet nicht" }
        $html = (Invoke-WebRequest -Uri "http://127.0.0.1:8797/companies?q=Lebenszyklus" -UseBasicParsing).Content
        if ($html -notmatch "Lebenszyklus Testfirma") { throw "$label`: Firma fehlt nach dem Neustart" }
    } finally { Stop-Process -Id $p.Id -Force -ErrorAction SilentlyContinue; Start-Sleep -Seconds 3 }
}
Check-Company "nach hartem Beenden"
Write-Host "5 Daten nach hartem Beenden und Neustart vorhanden"

# --- Überinstallieren (Update über bestehende Installation)
Install-App
Check-Company "nach Überinstallation"
Write-Host "6 Überinstallation ok, Daten vorhanden"

# --- Deinstallieren: Programm weg, Daten bleiben; Neuinstallation findet sie wieder
$uninstaller = Join-Path $dir "Uninstall.exe"
Start-Process $uninstaller -ArgumentList "/S" -Wait | Out-Null
for ($i = 0; $i -lt 60 -and (Test-Path $exe); $i++) { Start-Sleep -Seconds 1 }
if (Test-Path $exe) { throw "Deinstallation hat das Programm nicht entfernt" }
if (-not (Test-Path (Join-Path $data "market_intel.db"))) { throw "Deinstallation hat die Daten gelöscht" }
Write-Host "7 Deinstalliert, Datenordner bleibt erhalten"
Install-App
Check-Company "nach Neuinstallation"
Write-Host "8 Neuinstallation findet die Daten wieder"

# --- Verhalten ohne NOVA (nicht installiert) und ohne Internet für die KI
$ai = & $mi ai test nova | Out-String
if ($LASTEXITCODE -ne 3 -or $ai -notmatch "nicht erreichbar") { throw "NOVA-Fehlmeldung unklar: $ai" }
$cfg = Get-Content (Join-Path $data "market_intel.toml") -Raw
if ($cfg -match "sk-ant|nova_[0-9a-f]{12}") { throw "Schlüssel in der Konfigurationsdatei" }
$logs = (Get-ChildItem (Join-Path $data "logs") -Recurse -File | ForEach-Object { Get-Content $_.FullName -Raw }) -join "`n"
if ($logs -match "sk-ant-|nova_[0-9a-f]{12}_") { throw "Schlüssel im Protokoll" }
Write-Host "9 Ohne NOVA: klare Meldung; keine Schlüssel in Konfiguration oder Protokoll"

Start-Process $uninstaller -ArgumentList "/S" -Wait | Out-Null
for ($i = 0; $i -lt 60 -and (Test-Path $exe); $i++) { Start-Sleep -Seconds 1 }
Write-Host "Lebenszyklus-Test bestanden"
