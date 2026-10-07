; NSIS installer for hrdps-weather (Windows).
;
; Per-user by design: everything goes under %LOCALAPPDATA%, so there is no UAC prompt and no
; administrator account is needed. The app only writes to the user's own profile at runtime.
; It opens no listening port, so unlike a network service it needs no firewall rule.
;
; Build (from the repo root, with dist\hrdps-weather\ present):
;   makensis /DVERSION=1.0.0 packaging\hrdps-weather.nsi

!ifndef VERSION
  !define VERSION "0.0.0"
!endif

!define APPNAME   "hrdps-weather"
!define REGKEY    "Software\Microsoft\Windows\CurrentVersion\Uninstall\hrdps-weather"

Name            "${APPNAME} ${VERSION}"
OutFile         "..\dist\hrdps-weather-setup-x86_64.exe"
Unicode         True
RequestExecutionLevel user
InstallDir      "$LOCALAPPDATA\Programs\hrdps-weather"
InstallDirRegKey HKCU "Software\hrdps-weather" "InstallDir"
SetCompressor /SOLID lzma
BrandingText    "${APPNAME} ${VERSION}"

!include "MUI2.nsh"
!define MUI_ICON   "icon.ico"
!define MUI_UNICON "icon.ico"
!define MUI_FINISHPAGE_RUN "$INSTDIR\hrdps-weather-tray.exe"
!define MUI_FINISHPAGE_RUN_TEXT "Start hrdps-weather"
!define MUI_FINISHPAGE_TITLE "hrdps-weather is installed"
!define MUI_FINISHPAGE_TEXT "The weather icon appears in the notification area (click ^ if it is hidden).$\r$\n$\r$\nLeft-click: full window. Right-click: refresh, optional 'Start with Windows', quit.$\r$\n$\r$\nSet your location in %APPDATA%\hrdps-weather\config.toml (run 'hrdps-weather.exe config' to create it)."

!insertmacro MUI_PAGE_DIRECTORY
!insertmacro MUI_PAGE_INSTFILES
!insertmacro MUI_PAGE_FINISH
!insertmacro MUI_UNPAGE_CONFIRM
!insertmacro MUI_UNPAGE_INSTFILES
!insertmacro MUI_LANGUAGE "English"

Section "hrdps-weather" SecMain
  SectionIn RO
  SetOutPath "$INSTDIR"
  ; onedir build: both executables plus the shared _internal\ runtime must be installed together.
  File /r "..\dist\hrdps-weather\*.*"
  File "icon.ico"

  WriteUninstaller "$INSTDIR\uninstall.exe"
  CreateShortcut "$SMPROGRAMS\hrdps-weather.lnk" "$INSTDIR\hrdps-weather-tray.exe" "" "$INSTDIR\icon.ico"

  WriteRegStr HKCU "Software\hrdps-weather" "InstallDir" "$INSTDIR"
  WriteRegStr HKCU "${REGKEY}" "DisplayName"     "${APPNAME}"
  WriteRegStr HKCU "${REGKEY}" "DisplayVersion"  "${VERSION}"
  WriteRegStr HKCU "${REGKEY}" "Publisher"       "Carl Sansfaçon"
  WriteRegStr HKCU "${REGKEY}" "DisplayIcon"     "$INSTDIR\icon.ico"
  WriteRegStr HKCU "${REGKEY}" "InstallLocation" "$INSTDIR"
  WriteRegStr HKCU "${REGKEY}" "UninstallString" '"$INSTDIR\uninstall.exe"'
  WriteRegDWORD HKCU "${REGKEY}" "NoModify" 1
  WriteRegDWORD HKCU "${REGKEY}" "NoRepair" 1
SectionEnd

Section "Uninstall"
  ; Stop a running tray app so its files can be removed.
  nsExec::Exec 'taskkill /F /IM hrdps-weather-tray.exe'
  Delete "$SMPROGRAMS\hrdps-weather.lnk"
  ; Remove the optional "Start with Windows" entry if the user enabled it.
  DeleteRegValue HKCU "Software\Microsoft\Windows\CurrentVersion\Run" "HRDPSWeather"
  DeleteRegKey HKCU "${REGKEY}"
  DeleteRegKey HKCU "Software\hrdps-weather"
  RMDir /r "$INSTDIR"
SectionEnd
