; Datei ist UTF-8 mit BOM gespeichert (nötig für Umlaute in Inno Setup 6).
; Inno-Setup-Skript für Yellotools Market Intelligence (kostenlos: https://jrsoftware.org/isinfo.php)
; Aufruf:  ISCC.exe /DAppVersion=0.5.0 installer\MarketIntel.iss
; Erwartet das PyInstaller-Ergebnis unter dist\MarketIntel\ (pyinstaller installer\MarketIntel.spec).
;
; Installation ohne Administratorrechte in den Benutzerordner (%LOCALAPPDATA%\Programs).
; Daten (Datenbank, Einstellungen, Exporte) liegen getrennt in %LOCALAPPDATA%\YellotoolsMarketIntel
; und bleiben bei Deinstallation und Update erhalten.

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif

[Setup]
AppId={{B3101690-6255-4A26-80E0-A33948E3EB22}
AppName=Yellotools Market Intelligence
AppVersion={#AppVersion}
AppVerName=Yellotools Market Intelligence {#AppVersion}
DefaultDirName={autopf}\Yellotools Market Intelligence
DefaultGroupName=Yellotools Market Intelligence
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir=..\dist-installer
OutputBaseFilename=MarketIntel-Setup-{#AppVersion}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
UninstallDisplayIcon={app}\MarketIntel.exe
UninstallDisplayName=Yellotools Market Intelligence

[Languages]
Name: "german"; MessagesFile: "compiler:Languages\German.isl"

[Tasks]
Name: "desktopicon"; Description: "Verknüpfung auf dem Desktop"; Flags: unchecked

[Files]
Source: "..\dist\MarketIntel\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "mi.cmd"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{autoprograms}\Yellotools Market Intelligence"; Filename: "{app}\MarketIntel.exe"; Comment: "Oberfläche im Browser öffnen"
Name: "{autoprograms}\Market Intelligence – Eingabeaufforderung"; Filename: "{cmd}"; Parameters: "/k set ""PATH={app};%PATH%"" && echo Befehle: mi --help"; WorkingDir: "{userdocs}"
Name: "{autodesktop}\Yellotools Market Intelligence"; Filename: "{app}\MarketIntel.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\MarketIntel.exe"; Description: "Market Intelligence jetzt starten"; Flags: nowait postinstall skipifsilent
