# Rauchtest der gebauten bzw. installierten Anwendung (Windows, PowerShell 7).
# Prüft: Version, CLI, CSV-Pflege unter Windows, Oberfläche per HTTP, Datenordner.
param([Parameter(Mandatory = $true)][string]$Exe)
$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [Text.UTF8Encoding]::new($false)  # App gibt UTF-8 aus (utf8_mode)
$env:MI_HOME = Join-Path $env:RUNNER_TEMP ("mihome-" + [guid]::NewGuid())

$version = (& $Exe --version | Out-String).Trim()
if ($LASTEXITCODE -ne 0 -or -not $version) { throw "Version: Exitcode $LASTEXITCODE" }
Write-Host "Version $version"

$list = & $Exe products list | Out-String
if ($LASTEXITCODE -ne 0 -or $list -notmatch "Produktmatrix") { throw "CLI products list fehlgeschlagen" }

$csv = Join-Path $env:MI_HOME "produkte.csv"
$toml = Join-Path $env:MI_HOME "produkte.toml"
& $Exe products export-csv $csv | Out-Null
$bytes = [IO.File]::ReadAllBytes($csv)
$text = [Text.Encoding]::UTF8.GetString($bytes)
if ($text.Contains("`r`r`n")) { throw "CSV enthält doppelte Zeilenenden (\r\r\n)" }
$import = & $Exe products import-csv $csv --out $toml | Out-String
if ($LASTEXITCODE -ne 0 -or $import -notmatch "in Ordnung") { throw "CSV-Roundtrip: $import" }

$port = 8799
$proc = Start-Process -FilePath $Exe -ArgumentList "--no-browser", "--port", "$port" -PassThru -WindowStyle Hidden
try {
    $ok = $false
    for ($i = 0; $i -lt 90; $i++) {
        try {
            $r = Invoke-WebRequest -Uri "http://127.0.0.1:$port/leads" -UseBasicParsing -TimeoutSec 5
            if ($r.StatusCode -eq 200) { $ok = $true; break }
        } catch { Start-Sleep -Seconds 1 }
    }
    if (-not $ok) { throw "Oberfläche antwortet nicht auf Port $port" }
    if ($r.Content -notmatch "Leads") { throw "Leads-Seite ohne erwarteten Inhalt" }
    $css = Invoke-WebRequest -Uri "http://127.0.0.1:$port/static/app.css" -UseBasicParsing -TimeoutSec 5
    if ($css.StatusCode -ne 200) { throw "Stylesheet fehlt" }
} finally {
    Stop-Process -Id $proc.Id -Force -ErrorAction SilentlyContinue
}
if (-not (Test-Path (Join-Path $env:MI_HOME "market_intel.db"))) { throw "Datenbank nicht im Datenordner" }
Write-Host "Rauchtest bestanden: $Exe"
