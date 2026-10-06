; Inno Setup script for KherveLAB — per-user install, no admin rights.
;
; Not run by hand: packaging/build_installer.py freezes the app and then calls
;
;   ISCC.exe /DAPP_VERSION=0.28.N /DSRC_DIR=...\dist\KherveLAB /DOUT_DIR=...\dist
;            /DICON_FILE=...\build\KherveLAB.ico KherveLAB.iss
;
; Installs to %LOCALAPPDATA%\Programs\KherveLAB, so there is no elevation
; prompt. The lab's data (lab.db) lives in the user's KherveLAB-data folder,
; never under {app}, so an upgrade or uninstall leaves it alone.
;
; Copyright (C) 2026 Gwilherm Kerherve. GPL-3.0-or-later.

#ifndef APP_VERSION
  #define APP_VERSION "0.0.0"
#endif
#ifndef SRC_DIR
  #define SRC_DIR "..\dist\KherveLAB"
#endif
#ifndef OUT_DIR
  #define OUT_DIR "..\dist"
#endif
#ifndef ICON_FILE
  #define ICON_FILE "..\build\KherveLAB.ico"
#endif

#define AppName "KherveLAB"
#define AppPublisher "Gwilherm Kerherve"
#define AppURL "https://khervetools.com/tools/khervelab"
#define AppExe "KherveLAB.exe"

[Setup]
AppId={{6B0E3C1A-52D4-4F7E-9A61-3C8D2B7F4E15}
AppName={#AppName}
AppVersion={#APP_VERSION}
AppVerName={#AppName} {#APP_VERSION}
AppPublisher={#AppPublisher}
AppPublisherURL={#AppURL}
AppSupportURL={#AppURL}
VersionInfoVersion={#APP_VERSION}
DefaultDirName={localappdata}\Programs\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
LicenseFile=..\LICENSE
SetupIconFile={#ICON_FILE}
UninstallDisplayIcon={app}\{#AppExe}
OutputDir={#OUT_DIR}
OutputBaseFilename={#AppName}-Setup-{#APP_VERSION}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
MinVersion=10.0

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; GroupDescription: "Additional shortcuts:"; Flags: unchecked

[InstallDelete]
; Inno never removes a file a newer build dropped, and the Python packages
; (PyQt6, reportlab) are ABI-bound to each other: an upgrade must
; not leave the old _internal tree beside the new one. It is all build
; output; nothing the user made lives under {app}.
Type: filesandordirs; Name: "{app}\_internal"

[Files]
Source: "{#SRC_DIR}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExe}"
Name: "{group}\{#AppName} web server"; Filename: "{app}\KherveLAB-server.exe"; Comment: "Booking web pages on the lab network, without the app window"
Name: "{group}\Uninstall {#AppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExe}"; Description: "Launch {#AppName}"; Flags: nowait postinstall skipifsilent
