; NSIS-Skript für Yellotools Market Intelligence.
; NSIS: zlib/libpng-Lizenz – kostenlos, auch für kommerzielle Nutzung (https://nsis.sourceforge.io).
; Kompression zlib (das LZMA-Modul stünde unter einer eigenen Lizenz, CPL).
; Aufruf:  makensis /DVERSION=0.5.0 installer\MarketIntel.nsi
; Erwartet das PyInstaller-Ergebnis unter dist\MarketIntel\ (pyinstaller installer\MarketIntel.spec).
;
; Installation ohne Administratorrechte in %LOCALAPPDATA%\Programs. Daten (Datenbank,
; Einstellungen, Exporte) liegen getrennt in %LOCALAPPDATA%\YellotoolsMarketIntel und bleiben
; bei Update und Deinstallation erhalten. Still: Setup.exe /S, Uninstall.exe /S.

Unicode true
SetCompressor zlib
!include "MUI2.nsh"
!include "FileFunc.nsh"

!ifndef VERSION
  !define VERSION "0.0.0"
!endif
!define APPNAME "Yellotools Market Intelligence"
!define REGKEY "Software\Microsoft\Windows\CurrentVersion\Uninstall\YellotoolsMarketIntel"
!define CMDLINK "Market Intelligence – Eingabeaufforderung"

Name "${APPNAME} ${VERSION}"
!define MUI_ICON "MarketIntel.ico"
!define MUI_UNICON "MarketIntel.ico"
VIProductVersion "${VERSION}.0"
VIAddVersionKey "ProductName" "${APPNAME}"
VIAddVersionKey "ProductVersion" "${VERSION}"
VIAddVersionKey "FileVersion" "${VERSION}"
VIAddVersionKey "FileDescription" "${APPNAME} – Installation"
VIAddVersionKey "LegalCopyright" "Yellotools"
OutFile "..\dist-installer\MarketIntel-Setup-${VERSION}.exe"
RequestExecutionLevel user
InstallDir "$LOCALAPPDATA\Programs\${APPNAME}"
InstallDirRegKey HKCU "${REGKEY}" "InstallLocation"

!define MUI_FINISHPAGE_RUN "$INSTDIR\MarketIntel.exe"
!define MUI_FINISHPAGE_RUN_TEXT "Market Intelligence jetzt starten"
!insertmacro MUI_PAGE_DIRECTORY
!insertmacro MUI_PAGE_INSTFILES
!insertmacro MUI_PAGE_FINISH
!insertmacro MUI_UNPAGE_CONFIRM
!insertmacro MUI_UNPAGE_INSTFILES
!insertmacro MUI_LANGUAGE "German"

!macro EnsureClosed
  ; Läuft MarketIntel.exe oder mi.exe noch, lässt Windows die Datei nicht zum Schreiben öffnen.
  ; (Umbenennen wäre kein Test: Windows erlaubt das Umbenennen laufender Programme.)
  retry:
  StrCpy $R1 "$INSTDIR\MarketIntel.exe"
  Call ${UN}CheckLocked
  StrCmp $R0 "locked" busy
  StrCpy $R1 "$INSTDIR\mi.exe"
  Call ${UN}CheckLocked
  StrCmp $R0 "locked" busy done
  busy:
  IfSilent 0 +3
    SetErrorLevel 5
    Quit
  MessageBox MB_RETRYCANCEL|MB_ICONEXCLAMATION "Market Intelligence läuft noch. Bitte die Anwendung beenden und „Wiederholen“ wählen." IDRETRY retry
  SetErrorLevel 5
  Quit
  done:
!macroend

!macro CheckLockedFunc PREFIX
Function ${PREFIX}CheckLocked
  ; $R1 = Datei; Ergebnis in $R0: "locked" oder "free"
  StrCpy $R0 "free"
  IfFileExists "$R1" 0 end
  ClearErrors
  FileOpen $0 "$R1" a
  IfErrors 0 +3
    StrCpy $R0 "locked"
    Goto end
  FileClose $0
  end:
FunctionEnd
!macroend
!insertmacro CheckLockedFunc ""
!insertmacro CheckLockedFunc "un."

Section "Installieren"
  !define UN ""
  !insertmacro EnsureClosed
  !undef UN
  ; Update: alte Bibliotheken entfernen (nur der eigene Unterordner), dann neu kopieren
  RMDir /r "$INSTDIR\_internal"
  Delete "$INSTDIR\mi.cmd"  ; bis Version 0.5: Kommandozeile als Batchdatei
  SetOutPath "$INSTDIR"
  File /r "..\dist\MarketIntel\*"
  File "MarketIntel.ico"
  WriteUninstaller "$INSTDIR\Uninstall.exe"

  CreateShortcut "$SMPROGRAMS\${APPNAME}.lnk" "$INSTDIR\MarketIntel.exe" "" "$INSTDIR\MarketIntel.ico" 0 SW_SHOWNORMAL "" "Marktanalyse-Anwendung öffnen"
  CreateShortcut "$DESKTOP\${APPNAME}.lnk" "$INSTDIR\MarketIntel.exe" "" "$INSTDIR\MarketIntel.ico" 0 SW_SHOWNORMAL "" "Marktanalyse-Anwendung öffnen"
  SetOutPath "$DOCUMENTS"  ; Arbeitsordner der Eingabeaufforderung
  CreateShortcut "$SMPROGRAMS\${CMDLINK}.lnk" "$SYSDIR\cmd.exe" '/k set "PATH=$INSTDIR;%PATH%" && echo Befehle: mi --help'

  WriteRegStr HKCU "${REGKEY}" "DisplayName" "${APPNAME}"
  WriteRegStr HKCU "${REGKEY}" "DisplayVersion" "${VERSION}"
  WriteRegStr HKCU "${REGKEY}" "DisplayIcon" "$INSTDIR\MarketIntel.ico"
  WriteRegStr HKCU "${REGKEY}" "Publisher" "Yellotools"
  ${GetSize} "$INSTDIR" "/S=0K" $0 $1 $2
  WriteRegDWORD HKCU "${REGKEY}" "EstimatedSize" "$0"
  WriteRegStr HKCU "${REGKEY}" "InstallLocation" "$INSTDIR"
  WriteRegStr HKCU "${REGKEY}" "UninstallString" '"$INSTDIR\Uninstall.exe"'
  WriteRegStr HKCU "${REGKEY}" "QuietUninstallString" '"$INSTDIR\Uninstall.exe" /S'
  WriteRegDWORD HKCU "${REGKEY}" "NoModify" 1
  WriteRegDWORD HKCU "${REGKEY}" "NoRepair" 1
SectionEnd

Section "Uninstall"
  !define UN "un."
  !insertmacro EnsureClosed
  !undef UN
  Delete "$SMPROGRAMS\${APPNAME}.lnk"
  Delete "$DESKTOP\${APPNAME}.lnk"
  Delete "$SMPROGRAMS\${CMDLINK}.lnk"
  ; nur eigene Dateien entfernen – nie den Datenordner und nie fremde Dateien im Zielordner
  RMDir /r "$INSTDIR\_internal"
  Delete "$INSTDIR\MarketIntel.exe"
  Delete "$INSTDIR\mi.exe"
  Delete "$INSTDIR\mi.cmd"
  Delete "$INSTDIR\MarketIntel.ico"
  Delete "$INSTDIR\Uninstall.exe"
  RMDir "$INSTDIR"
  DeleteRegKey HKCU "${REGKEY}"
SectionEnd
