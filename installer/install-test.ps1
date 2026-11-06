# Installer auf Windows prüfen: still installieren, Verknüpfung und Programm prüfen,
# installierte Anwendung testen, still deinstallieren. Läuft im GitHub-Workflow.
$ErrorActionPreference = "Stop"
$setup = Get-ChildItem "dist-installer\MarketIntel-Setup-*.exe" | Select-Object -First 1
if (-not $setup) { throw "Setup-Datei fehlt" }
Write-Host "Installiere $($setup.Name)"
$p = Start-Process $setup.FullName -ArgumentList "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/CURRENTUSER", "/LOG=install.log" -Wait -PassThru
if ($p.ExitCode -ne 0) { Get-Content install.log; throw "Installation fehlgeschlagen ($($p.ExitCode))" }

$dir = Join-Path $env:LOCALAPPDATA "Programs\Yellotools Market Intelligence"
$exe = Join-Path $dir "MarketIntel.exe"
if (-not (Test-Path $exe)) { Get-Content install.log; throw "MarketIntel.exe fehlt in $dir" }
$link = Join-Path $env:APPDATA "Microsoft\Windows\Start Menu\Programs\Yellotools Market Intelligence.lnk"
if (-not (Test-Path $link)) { throw "Startmenü-Verknüpfung fehlt" }

& "$PSScriptRoot\smoke-test.ps1" -Exe $exe
$mi = (& (Join-Path $dir "mi.cmd") --version | Out-String).Trim()
if (-not $mi) { throw "mi.cmd liefert keine Version" }

$uninstaller = Join-Path $dir "unins000.exe"
Start-Process $uninstaller -ArgumentList "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART" -Wait | Out-Null
for ($i = 0; $i -lt 60 -and (Test-Path $exe); $i++) { Start-Sleep -Seconds 1 }
if (Test-Path $exe) { throw "Deinstallation hat MarketIntel.exe nicht entfernt" }
if (Test-Path $link) { throw "Deinstallation hat die Startmenü-Verknüpfung nicht entfernt" }
Write-Host "Installation, Start und Deinstallation bestanden"
