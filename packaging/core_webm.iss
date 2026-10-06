; New unified Core only. Compile via the trusted production build pipeline.
; Legacy installers/accepted runtime directories are deliberately unchanged.
#ifndef CoreDir
#error CoreDir must name the freshly audited production onedir Core
#endif
#ifndef CoreVersion
#error CoreVersion must be supplied by the release pipeline
#endif
#ifndef CoreOutputDir
#error CoreOutputDir must be an owned output directory
#endif

[Setup]
AppId={{29396687-82A4-5E4D-8E81-C9C94C94A356}
AppName=DSH Pet Core (WebM)
AppVersion={#CoreVersion}
AppPublisher=merzlin
PrivilegesRequired=lowest
DefaultDirName={localappdata}\Programs\dsh-pet-core-webm
DisableProgramGroupPage=yes
OutputDir={#CoreOutputDir}
OutputBaseFilename=dsh-pet-core-webm-setup
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
UsePreviousSetupType=no
UsePreviousTasks=no
CloseApplications=no
RestartApplications=no
UninstallDisplayName=DSH Pet Core (WebM)
UninstallDisplayIcon={app}\dsh-pet-core-webm.exe

[Languages]
Name: "chinesesimp"; MessagesFile: "compiler:Languages\ChineseSimplified.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[CustomMessages]
chinesesimp.OptionalPackages=离线选装官方扩展（仍需分别预检与确认）
english.OptionalPackages=Optional offline official packages (separate verification and confirmation)
chinesesimp.InstallAI=AI 对话与文本／代码文件理解
english.InstallAI=AI chat and text/code file understanding
chinesesimp.InstallScreen=屏幕理解
english.InstallScreen=Screen understanding
chinesesimp.NaturalExit=请自然退出此 Core 和设置后重试。安装器不会关闭用户进程。原因：
english.NaturalExit=Please exit this Core and its settings naturally, then retry. No processes will be closed. Reason:
chinesesimp.RemovalBlocked=两个 DLC 的代码清理尚未完成，Core 未删除。请恢复或自然退出占用进程后重试。个人数据保留。原因：
english.RemovalBlocked=Both DLC code removals must finish before Core deletion. Recover or exit occupied processes naturally and retry. Personal data is retained. Reason:

[Tasks]
Name: "ai"; Description: "{cm:InstallAI}"; GroupDescription: "{cm:OptionalPackages}"; Flags: unchecked
Name: "screen"; Description: "{cm:InstallScreen}"; GroupDescription: "{cm:OptionalPackages}"; Flags: unchecked
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
Source: "{#CoreDir}\*"; DestDir: "{app}"; Excludes: "portable.json,data\*,.core-files.lock"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\DSH Pet Core"; Filename: "{app}\dsh-pet-core-webm.exe"
Name: "{autodesktop}\DSH Pet Core"; Filename: "{app}\dsh-pet-core-webm.exe"; Tasks: desktopicon

[Run]
; Only an intent: no installer-side package writes/downloads or success receipt.
Filename: "{app}\dsh-pet-core-webm.exe"; Parameters: "{code:LocalPackageArguments}"; Check: SelectedLocalPackages; Flags: waituntilterminated
Filename: "{app}\dsh-pet-core-webm.exe"; Description: "{cm:LaunchProgram,DSH Pet Core}"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
; The held handle shares delete; it is closed after the uninstall operation.
Type: files; Name: "{app}\.core-files.lock"

[Code]
#include "core_code_gate.iss.inc"
#include "core_removal_gate.iss.inc"

function SelectedLocalPackages(): Boolean;
begin
  Result := WizardIsTaskSelected('ai') or WizardIsTaskSelected('screen');
end;

function LocalPackageArguments(Param: String): String;
begin
  Result := '--install-local-packages "' + ExpandConstant('{src}\packages') + '"';
  if WizardIsTaskSelected('ai') then Result := Result + ' official.ai-chat';
  if WizardIsTaskSelected('screen') then Result := Result + ' official.screen-understanding';
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
var Reason: String;
begin
  NeedsRestart := False;
  Result := '';
  if not AcquireCodeBarrier(ExpandConstant('{app}'), Reason) then
    Result := ExpandConstant('{cm:NaturalExit}') + ' ' + Reason;
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  // Core files have been written, but [Run] must use the normal shared barrier.
  if CurStep = ssPostInstall then ReleaseCodeBarrier();
end;

procedure DeinitializeSetup();
begin
  ReleaseCodeBarrier();
end;

function InitializeUninstall(): Boolean;
var Reason, Executable: String; ExitCode: Integer;
begin
  Result := False;
  if not AcquireCodeBarrier(ExpandConstant('{app}'), Reason) then begin
    MsgBox(ExpandConstant('{cm:RemovalBlocked}') + ' ' + Reason, mbError, MB_OK);
    Exit;
  end;
  if not AcquireRemovalBarrier(ExpandConstant('{userappdata}\dsh-pet-core-webm'), Reason) then begin
    ReleaseCodeBarrier();
    MsgBox(ExpandConstant('{cm:RemovalBlocked}') + ' ' + Reason, mbError, MB_OK);
    Exit;
  end;
  Executable := ExpandConstant('{app}\dsh-pet-core-webm.exe');
  if not FileExists(Executable) then Reason := 'core_maintenance_missing'
  else if not Exec(Executable, '--core-maintenance uninstall', ExpandConstant('{app}'), SW_SHOWNORMAL, ewWaitUntilTerminated, ExitCode) then Reason := 'core_maintenance_launch_failed'
  else if ExitCode = 0 then Result := True
  else Reason := 'core_maintenance_incomplete:' + IntToStr(ExitCode);
  // Keep the barrier until all Core deletions finish. An accepted per-package
  // uninstall is never undone if this overall uninstall is subsequently canceled.
  if not Result then begin
    ReleaseRemovalBarrier();
    ReleaseCodeBarrier();
    MsgBox(ExpandConstant('{cm:RemovalBlocked}') + ' ' + Reason, mbError, MB_OK);
  end;
end;

procedure DeinitializeUninstall();
begin
  ReleaseRemovalBarrier();
  ReleaseCodeBarrier();
end;
