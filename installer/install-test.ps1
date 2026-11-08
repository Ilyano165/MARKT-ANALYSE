# Installer auf Windows prüfen: still installieren, Verknüpfung und Programm prüfen,
# installierte Anwendung testen, still deinstallieren. Läuft im GitHub-Workflow.
$ErrorActionPreference = "Stop"
$setup = Get-ChildItem "dist-installer\MarketIntel-Setup-*.exe" | Select-Object -First 1
if (-not $setup) { throw "Setup-Datei fehlt" }
Write-Host "Installiere $($setup.Name)"
$p = Start-Process $setup.FullName -ArgumentList "/S" -Wait -PassThru
if ($p.ExitCode -ne 0) { throw "Installation fehlgeschlagen ($($p.ExitCode))" }

$dir = Join-Path $env:LOCALAPPDATA "Programs\Yellotools Market Intelligence"
$exe = Join-Path $dir "MarketIntel.exe"
if (-not (Test-Path $exe)) { throw "MarketIntel.exe fehlt in $dir" }
$link = Join-Path $env:APPDATA "Microsoft\Windows\Start Menu\Programs\Yellotools Market Intelligence.lnk"
if (-not (Test-Path $link)) { throw "Startmenü-Verknüpfung fehlt" }
$reg = "HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\YellotoolsMarketIntel"
if ((Get-ItemProperty $reg).DisplayVersion -notmatch "^\d+\.\d+\.\d+$") { throw "Eintrag unter Apps & Features fehlt" }

$desktopLink = Join-Path ([Environment]::GetFolderPath("Desktop")) "Yellotools Market Intelligence.lnk"
if (-not (Test-Path $desktopLink)) { throw "Desktop-Verknüpfung fehlt" }
& "$PSScriptRoot\smoke-test.ps1" -Exe $exe
$mi = (& (Join-Path $dir "mi.exe") --version | Out-String).Trim()
if (-not $mi) { throw "mi.exe liefert keine Version" }

# Laufende Anwendung: Deinstallation muss abbrechen statt Dateien halb zu löschen
$running = Start-Process -FilePath $exe -ArgumentList "--no-browser", "--port", "8798" -PassThru -WindowStyle Hidden
Start-Sleep -Seconds 5
$blocked = Start-Process (Join-Path $dir "Uninstall.exe") -ArgumentList "/S", "_?=$dir" -Wait -PassThru
if (-not (Test-Path $exe)) { throw "Deinstallation hat trotz laufender Anwendung gelöscht" }
if ($blocked.ExitCode -eq 0) { throw "Deinstallation meldet Erfolg trotz laufender Anwendung" }
Stop-Process -Id $running.Id -Force
Start-Sleep -Seconds 2

$uninstaller = Join-Path $dir "Uninstall.exe"
Start-Process $uninstaller -ArgumentList "/S" -Wait | Out-Null  # kopiert sich und kehrt sofort zurück
for ($i = 0; $i -lt 60 -and (Test-Path $exe); $i++) { Start-Sleep -Seconds 1 }
if (Test-Path $exe) { throw "Deinstallation hat MarketIntel.exe nicht entfernt" }
if (Test-Path $link) { throw "Deinstallation hat die Startmenü-Verknüpfung nicht entfernt" }
if (Test-Path $desktopLink) { throw "Deinstallation hat die Desktop-Verknüpfung nicht entfernt" }
if (Test-Path $reg) { throw "Deinstallation hat den Eintrag unter Apps & Features nicht entfernt" }
Write-Host "Installation, Start und Deinstallation bestanden"
