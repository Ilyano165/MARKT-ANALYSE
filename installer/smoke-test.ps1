# Rauchtest der gebauten bzw. installierten Anwendung (Windows, PowerShell 7).
# Prüft: Kommandozeile mi.exe (Version, CLI, CSV-Pflege), Server ohne Fenster per HTTP,
# echtes Anwendungsfenster (WebView2) mit Seiteninhalt, Einzelinstanz, Protokolldatei.
param([Parameter(Mandatory = $true)][string]$Exe)
$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [Text.UTF8Encoding]::new($false)  # App gibt UTF-8 aus (utf8_mode)
$env:MI_HOME = Join-Path $env:RUNNER_TEMP ("mihome-" + [guid]::NewGuid())
$env:MI_NO_DIALOG = "1"  # Fehler nie als modalen Dialog (niemand könnte ihn schließen)
$mi = Join-Path (Split-Path $Exe) "mi.exe"
if (-not (Test-Path $mi)) { throw "mi.exe fehlt neben $Exe" }

$version = (& $mi --version | Out-String).Trim()
if ($LASTEXITCODE -ne 0 -or -not $version) { throw "Version: Exitcode $LASTEXITCODE" }
Write-Host "Version $version"

$list = & $mi products list | Out-String
if ($LASTEXITCODE -ne 0 -or $list -notmatch "Produktmatrix") { throw "CLI products list fehlgeschlagen" }

$csv = Join-Path $env:MI_HOME "produkte.csv"
$toml = Join-Path $env:MI_HOME "produkte.toml"
& $mi products export-csv $csv | Out-Null
$bytes = [IO.File]::ReadAllBytes($csv)
$text = [Text.Encoding]::UTF8.GetString($bytes)
if ($text.Contains("`r`r`n")) { throw "CSV enthält doppelte Zeilenenden (\r\r\n)" }
$import = & $mi products import-csv $csv --out $toml | Out-String
if ($LASTEXITCODE -ne 0 -or $import -notmatch "in Ordnung") { throw "CSV-Roundtrip: $import" }
$jobs = & $mi jobs | Out-String
if ($LASTEXITCODE -ne 0) { throw "mi jobs fehlgeschlagen" }

# KI (Phase H): NOVA als Standard, Schlüssel in der Windows-Anmeldeinformationsverwaltung,
# echter Aufruf der Anthropic-API mit ungültigem Schlüssel (klare Meldung, kein stiller Wechsel).
$ai = & $mi ai status | Out-String
Write-Host $ai
if ($ai -notmatch "Aktiver Provider: NOVA") { throw "Standard-Provider ist nicht NOVA: $ai" }
if ($ai -notmatch "Windows-Anmeldeinformationsverwaltung") { throw "Kein Credential Manager: $ai" }
$fake = "sk-ant-api03-smoketest-ungueltig-" + [guid]::NewGuid().ToString("N").Substring(0, 8)
$saved = $fake | & $mi ai set-key anthropic --stdin | Out-String
if ($LASTEXITCODE -ne 0 -or $saved -notmatch "Anmeldeinformationsverwaltung") { throw "Schlüssel nicht gespeichert: $saved" }
$cred = cmdkey /list:MarketIntel/ai/anthropic | Out-String
Write-Host $cred
if ($cred -notmatch "Target:\s*MarketIntel/ai/anthropic") { throw "Eintrag fehlt in der Anmeldeinformationsverwaltung: $cred" }
& $mi ai use anthropic | Out-Null
$test = & $mi ai test anthropic | Out-String
Write-Host $test
if ($LASTEXITCODE -ne 3 -or $test -notmatch "API-Key ungültig") { throw "Ungültiger Schlüssel nicht klar gemeldet: $test" }
$st = & $mi ai status | Out-String
if ($st -notmatch "Aktiver Provider: Anthropic" -or $st.Contains($fake)) { throw "Status falsch oder Schlüssel im Klartext: $st" }
$dbBytes = [IO.File]::ReadAllText((Join-Path $env:MI_HOME "market_intel.db"), [Text.Encoding]::Latin1)
if ($dbBytes.Contains($fake)) { throw "Schlüssel steht in der Datenbank" }
$rm = & $mi ai remove-key anthropic | Out-String
if ($rm -notmatch "gelöscht") { throw "Entfernen fehlgeschlagen: $rm" }
$cred = cmdkey /list:MarketIntel/ai/anthropic | Out-String
Write-Host $cred
if ($cred -match "Target:\s*MarketIntel/ai/anthropic") { throw "Eintrag nach dem Entfernen noch vorhanden: $cred" }
$st = & $mi ai status | Out-String
if ($st -notmatch "Aktiver Provider: NOVA" -or $st -notmatch "kein API-Key") { throw "Nach dem Entfernen nicht NOVA: $st" }
$nova = & $mi ai test nova | Out-String
Write-Host $nova
if ($LASTEXITCODE -ne 3 -or $nova -notmatch "NOVA") { throw "NOVA-Fehlermeldung unklar: $nova" }
& $mi ai use nova | Out-Null

# Server ohne Fenster
$port = 8799
$proc = Start-Process -FilePath $Exe -ArgumentList "--no-browser", "--port", "$port" -PassThru -WindowStyle Hidden
try {
    $ok = $false
    for ($i = 0; $i -lt 90; $i++) {
        try {
            $r = Invoke-WebRequest -Uri "http://127.0.0.1:$port/health" -UseBasicParsing -TimeoutSec 5
            if ($r.StatusCode -eq 200) { $ok = $true; break }
        } catch { Start-Sleep -Seconds 1 }
    }
    if (-not $ok) { throw "Oberfläche antwortet nicht auf Port $port" }
    foreach ($path in "/leads", "/jobs", "/jobs/new", "/imports", "/maintenance", "/settings/ai", "/ai/status.json") {
        $page = Invoke-WebRequest -Uri "http://127.0.0.1:$port$path" -UseBasicParsing -TimeoutSec 10
        if ($page.StatusCode -ne 200) { throw "$path antwortet mit $($page.StatusCode)" }
    }
    $css = Invoke-WebRequest -Uri "http://127.0.0.1:$port/static/app.css" -UseBasicParsing -TimeoutSec 5
    if ($css.StatusCode -ne 200) { throw "Stylesheet fehlt" }
} finally {
    Stop-Process -Id $proc.Id -Force -ErrorAction SilentlyContinue
    Start-Sleep -Seconds 2
}
if (-not (Test-Path (Join-Path $env:MI_HOME "market_intel.db"))) { throw "Datenbank nicht im Datenordner" }

# Echtes Anwendungsfenster: öffnet WebView2, liest Titel und Navigation aus dem Fenster,
# prüft die Einzelinstanz (Zweitstart weckt das Fenster) und schließt sich selbst.
$out = Join-Path $env:MI_HOME "fenster.json"
$p = Start-Process -FilePath $Exe -ArgumentList "--smoke-test", $out -PassThru -Wait
if (-not (Test-Path $out)) { throw "Fenstertest ohne Ergebnis (Exitcode $($p.ExitCode)); Protokoll: $(Get-Content (Join-Path $env:MI_HOME 'logs\market_intel.log') -Raw)" }
$result = Get-Content $out -Raw | ConvertFrom-Json
Write-Host "Fenstertest: $(Get-Content $out -Raw)"
if ($p.ExitCode -ne 0) { throw "Fenstertest fehlgeschlagen (Exitcode $($p.ExitCode))" }
if ($result.mode -ne "webview") { throw "Kein eigenes Fenster (Modus $($result.mode))" }
if ($result.title -notmatch "Market Intelligence") { throw "Fenster zeigt nicht die Anwendung: $($result.title)" }
if ([int]$result.nav -lt 10) { throw "Navigation im Fenster unvollständig ($($result.nav) Einträge)" }
if (-not $result.second_instance_focus) { throw "Einzelinstanz: Zweitstart nicht übergeben" }
if (-not (Test-Path (Join-Path $env:MI_HOME "logs\market_intel.log"))) { throw "Protokolldatei fehlt" }
Write-Host "Rauchtest bestanden: $Exe"
