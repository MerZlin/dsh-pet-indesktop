; -*- mode: ini -*-
; Generic dsh-pet-standalone onedir installer (Inno Setup 6)
;
; Compile (defaults build the webm-chat variant):
;   E:\tools\InnoSetup6\ISCC.exe packaging\dsh-pet.iss
;
; Other variants (override defines on the command line):
;   E:\tools\InnoSetup6\ISCC.exe /DMyAppShortName=dsh-pet-standalone-webm `
;       /DMyAppExeName=dsh-pet-standalone-webm.exe `
;       /DMyAppDir=..\dist-onedir\dsh-pet-standalone-webm `
;       /DMyAppId=xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx `
;       /DMyAppDisplay="dsh-pet-standalone (WebM)" packaging\dsh-pet.iss
;
; Output: dist-onedir\<shortname>-setup.exe

#ifndef MyAppShortName
#define MyAppShortName "dsh-pet-standalone-webm-chat"
#endif
#ifndef MyAppExeName
#define MyAppExeName "dsh-pet-standalone-webm-chat.exe"
#endif
#ifndef MyAppDir
#define MyAppDir "..\dist-onedir\dsh-pet-standalone-webm-chat"
#endif
#ifndef MyAppId
; NOTE: value must include the double-brace escaping required by AppId ({{GUID})
#define MyAppId "{{BE859155-E238-4D47-B16D-F1B2AC2AFB0E}"
#endif
#ifndef MyAppDisplay
#define MyAppDisplay "dsh-pet-standalone (WebM Chat)"
#endif
#ifndef MyAppVersion
; 默认版本与 pet/__init__.py 的 __version__ 一致；CI 构建时通过
; /DMyAppVersion=<版本> 从单一来源注入（见 build-windows.yml）。
#define MyAppVersion "4.0.2"
#endif

[Setup]
AppId={#MyAppId}
AppName={#MyAppDisplay}
AppVersion={#MyAppVersion}
AppPublisher=merzlin
; 安装包图标（待机封面帧生成，scripts/make_icon.py）
SetupIconFile=..\assets\icon.ico
; Per-user install, no admin needed; user may pick any drive in the wizard
PrivilegesRequired=lowest
DefaultDirName={localappdata}\Programs\{#MyAppShortName}
DisableProgramGroupPage=yes
OutputDir=..\dist-onedir
OutputBaseFilename={#MyAppShortName}-setup
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
UninstallDisplayName={#MyAppDisplay}

[Languages]
Name: "chinesesimp"; MessagesFile: "compiler:Languages\ChineseSimplified.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
Source: "{#MyAppDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\{#MyAppShortName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{autodesktop}\{#MyAppShortName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchProgram,{#MyAppShortName}}"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
; Defensive: remove any residual runtime dirs (onedir normally leaves none)
Type: filesandordirs; Name: "{app}\_MEI*"
