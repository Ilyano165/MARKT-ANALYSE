# Rauchtest der gebauten bzw. installierten Anwendung (Windows, PowerShell 7).
# Prüft: Kommandozeile mi.exe (Version, CLI, CSV-Pflege), Server ohne Fenster per HTTP,
# echtes Anwendungsfenster (WebView2) mit Seiteninhalt, Einzelinstanz, Protokolldatei.
param([Parameter(Mandatory = $true)][string]$Exe)
$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [Text.UTF8Encoding]::new($false)  # App gibt UTF-8 aus (utf8_mode)
$env:MI_HOME = Join-Path $env:RUNNER_TEMP ("mihome-" + [guid]::NewGuid())
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
    foreach ($path in "/leads", "/jobs", "/jobs/new", "/imports", "/maintenance") {
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
if ($result.title -notmatch "Unternehmen") { throw "Fenster zeigt nicht die Anwendung: $($result.title)" }
if (-not $result.second_instance_focus) { throw "Einzelinstanz: Zweitstart nicht übergeben" }
if (-not (Test-Path (Join-Path $env:MI_HOME "logs\market_intel.log"))) { throw "Protokolldatei fehlt" }
Write-Host "Rauchtest bestanden: $Exe"
