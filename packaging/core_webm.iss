; Portable project-directory Core. The marker is owned by this Setup and is
; deliberately generated outside the audited Core input tree.
#ifndef CoreDir
#error CoreDir must name the freshly audited production onedir Core
#endif
#ifndef CoreVersion
#error CoreVersion must be supplied by the release pipeline
#endif
#ifndef CoreOutputDir
#error CoreOutputDir must be an owned output directory
#endif
#ifndef PackageDir
#error PackageDir must contain the audited official feature ZIPs
#endif
#ifndef PortableMarker
#error PortableMarker must name the Setup-owned portable marker
#endif

[Setup]
AppId={{29396687-82A4-5E4D-8E81-C9C94C94A356}
AppName=DSH Pet Core (WebM)
AppVersion={#CoreVersion}
AppPublisher=merzlin
PrivilegesRequired=lowest
DefaultDirName={localappdata}\Programs\dsh-pet-core-webm
DisableDirPage=no
UsePreviousAppDir=yes
DisableProgramGroupPage=yes
OutputDir={#CoreOutputDir}
OutputBaseFilename=dsh-pet-core-webm-setup
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
SetupIconFile=..\assets\icon.ico
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
chinesesimp.OptionalPackages=离线选装官方扩展（勾选后安装并启用）
english.OptionalPackages=Optional offline official packages (checked items are installed and enabled)
chinesesimp.InstallAI=AI 对话与文本／代码文件理解
english.InstallAI=AI chat and text/code file understanding
chinesesimp.InstallScreen=屏幕理解
english.InstallScreen=Screen understanding
chinesesimp.InstallLocation=请选择 NTFS 磁盘上的空目录，不要选择盘符根目录或含其他文件的目录。仅支持更新本产品目录或复用卸载后保留的 data；卸载会清理项目目录内的程序和扩展，请勿存放无关重要文件。
english.InstallLocation=Choose an empty directory on NTFS, not a drive root or a directory containing unrelated files. Only this product or its retained data directory can be reused. Uninstall removes program files and packages in this directory; do not store unrelated important files here.
chinesesimp.NaturalExit=请自然退出此 Core 和设置后重试。安装器不会关闭用户进程。原因：
english.NaturalExit=Please exit this Core and its settings naturally, then retry. No processes will be closed. Reason:
chinesesimp.MaintenanceFailed=官方扩展未能完成安装，Setup 已停止启动 Core。请查看 Core 日志。原因：
english.MaintenanceFailed=Official package installation failed; Setup will not launch Core. Check the Core log. Reason:
chinesesimp.RemovalWarning=将清理当前项目目录中的 Core 程序文件、portable.json 以及 data\plugins 中已安装的官方或外部扩展。data 中的个人数据默认保留；项目目录中的其他重要文件也可能被删除，请先检查并移出。项目目录外的原始 ZIP、源目录不受影响。当前目录：
english.RemovalWarning=Core program files, portable.json, and installed packages under data\plugins in this project directory will be removed. Personal data under data is kept by default; other important files in the project directory may also be deleted, so move them out first. Original ZIPs and source directories outside this directory are not touched. Project directory:
chinesesimp.RemovalDeleteData=同时删除 data 目录中的个人数据（配置、聊天记录、记忆及其他文件）？默认保留。
english.RemovalDeleteData=Also delete personal data under data (configuration, chats, memory and other files)? The default is keep.
chinesesimp.RemovalDeleteDataFinal=确认永久删除整个 data 目录？此操作不可恢复，且会删除已安装扩展和个人数据。
english.RemovalDeleteDataFinal=Confirm permanent deletion of the entire data directory? This cannot be undone and removes installed packages and personal data.
chinesesimp.RemovalBlocked=项目目录校验或占用检查未通过，尚未开始删除。请自然退出此目录的桌宠和设置，并检查目录权限后重试。原因：
english.RemovalBlocked=Project-directory validation or occupancy checks failed; deletion has not started. Exit this Core and its settings naturally, check directory permissions, then retry. Reason:
chinesesimp.RemovalCleanupFailed=项目目录清理未完成，已停止以避免越界删除。请根据原因处理后重试。原因：
english.RemovalCleanupFailed=Project-directory cleanup did not complete and stopped to avoid an out-of-bound deletion. Fix the reported reason and retry. Reason:

[Tasks]
Name: "ai"; Description: "{cm:InstallAI}"; GroupDescription: "{cm:OptionalPackages}"; Flags: unchecked
Name: "screen"; Description: "{cm:InstallScreen}"; GroupDescription: "{cm:OptionalPackages}"; Flags: unchecked
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
; Core code is copied separately from user-owned data. The marker is a
; Setup-owned file and is never taken from CoreDir.
Source: "{#CoreDir}\*"; DestDir: "{app}"; Excludes: "portable.json,data\*,.core-files.lock"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "{#PortableMarker}"; DestDir: "{app}"; DestName: "portable.json"; Flags: ignoreversion
; Owned non-executable receipt permits reinstallation after keeping data.
Source: "{#PortableMarker}"; DestDir: "{app}\data"; DestName: ".setup-project.json"; Flags: ignoreversion uninsneveruninstall
; Official packages are embedded in the installer and extracted only when the
; corresponding task is selected. No external packages directory is needed.
Source: "{#PackageDir}\official.ai-chat.zip"; DestDir: "{tmp}\dsh-pet-core-packages"; Flags: dontcopy; Tasks: ai
Source: "{#PackageDir}\official.screen-understanding.zip"; DestDir: "{tmp}\dsh-pet-core-packages"; Flags: dontcopy; Tasks: screen

[InstallDelete]
; Remove only the stale Core-owned frozen probe. Never sweep data during an
; update: configuration, credentials, package state and personal data survive.
Type: filesandordirs; Name: "{app}\_internal\feature-probe"

[Icons]
Name: "{autoprograms}\DSH Pet Core"; Filename: "{app}\dsh-pet-core-webm.exe"
Name: "{autodesktop}\DSH Pet Core"; Filename: "{app}\dsh-pet-core-webm.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\dsh-pet-core-webm.exe"; Description: "{cm:LaunchProgram,DSH Pet Core}"; Check: CanLaunchCore; Flags: nowait postinstall skipifsilent

[UninstallDelete]
; The native barriers are released by DeinitializeUninstall. These entries are
; a final cleanup for lock records left by the maintenance child.
Type: files; Name: "{app}\.core-files.lock"
Type: files; Name: "{app}\data\core-removal.lock"

[Code]
#include "core_code_gate.iss.inc"
#include "core_removal_gate.iss.inc"

var
  MaintenanceSucceeded: Boolean;
  DeletePersonalData: Boolean;

procedure InitializeWizard();
var OriginalHeight, AddedHeight: Integer;
begin
  OriginalHeight := WizardForm.SelectDirLabel.Height;
  WizardForm.SelectDirLabel.Caption := ExpandConstant('{cm:InstallLocation}');
  WizardForm.SelectDirLabel.WordWrap := True;
  WizardForm.SelectDirLabel.AdjustHeight();
  AddedHeight := WizardForm.SelectDirLabel.Height - OriginalHeight;
  WizardForm.SelectDirBrowseLabel.Top := WizardForm.SelectDirBrowseLabel.Top + AddedHeight;
  WizardForm.DirEdit.Top := WizardForm.DirEdit.Top + AddedHeight;
  WizardForm.DirBrowseButton.Top := WizardForm.DirBrowseButton.Top + AddedHeight;
end;

function NextButtonClick(CurPageID: Integer): Boolean;
var Reason: String;
begin
  Result := True;
  if CurPageID = wpSelectDir then begin
    Result := ValidateSetupTarget(WizardDirValue(), Reason);
    if not Result then
      MsgBox(ExpandConstant('{cm:InstallLocation}') + #13#10 + Reason, mbError, MB_OK);
  end;
end;

function SelectedLocalPackages(): Boolean;
begin
  Result := WizardIsTaskSelected('ai') or WizardIsTaskSelected('screen');
end;

function CanLaunchCore(): Boolean;
begin
  Result := MaintenanceSucceeded;
end;

function OfficialMaintenanceArguments(): String;
begin
  Result := '--core-maintenance install-packages "' + ExpandConstant('{tmp}\dsh-pet-core-packages') + '"';
  if WizardIsTaskSelected('ai') then Result := Result + ' official.ai-chat';
  if WizardIsTaskSelected('screen') then Result := Result + ' official.screen-understanding';
end;

function ExtractSelectedOfficialPackages(): Boolean;
var PackageRoot, Source: String;
begin
  Result := True;
  PackageRoot := ExpandConstant('{tmp}\dsh-pet-core-packages');
  if not ForceDirectories(PackageRoot) then Result := False;
  if Result and WizardIsTaskSelected('ai') then begin
    ExtractTemporaryFile('official.ai-chat.zip');
    Source := ExpandConstant('{tmp}\official.ai-chat.zip');
    Result := FileExists(Source) and FileCopy(Source, PackageRoot + '\official.ai-chat.zip', False);
  end;
  if Result and WizardIsTaskSelected('screen') then begin
    ExtractTemporaryFile('official.screen-understanding.zip');
    Source := ExpandConstant('{tmp}\official.screen-understanding.zip');
    Result := FileExists(Source) and FileCopy(Source, PackageRoot + '\official.screen-understanding.zip', False);
  end;
end;

function RunOfficialMaintenance(var Reason: String): Boolean;
var Executable, Arguments: String; ExitCode: Integer;
begin
  Result := False;
  Reason := '';
  Executable := ExpandConstant('{app}\dsh-pet-core-webm.exe');
  Arguments := OfficialMaintenanceArguments();
  if not FileExists(Executable) then Reason := 'core_maintenance_missing'
  else if not Exec(Executable, Arguments, ExpandConstant('{app}'), SW_HIDE, ewWaitUntilTerminated, ExitCode) then Reason := 'core_maintenance_launch_failed'
  else if ExitCode = 0 then Result := True
  else Reason := 'core_maintenance_incomplete:' + IntToStr(ExitCode);
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
var Reason: String;
begin
  NeedsRestart := False;
  MaintenanceSucceeded := True;
  Result := '';
  if not AcquireSetupBarrier(ExpandConstant('{app}'), Reason) then begin
    if Reason = 'core_in_use' then Result := ExpandConstant('{cm:NaturalExit}') + ' ' + Reason
    else Result := ExpandConstant('{cm:InstallLocation}') + #13#10 + Reason;
  end;
end;

procedure CurStepChanged(CurStep: TSetupStep);
var Reason: String;
begin
  if CurStep = ssPostInstall then begin
    MaintenanceSucceeded := ExtractSelectedOfficialPackages();
    if MaintenanceSucceeded and SelectedLocalPackages() then
      MaintenanceSucceeded := RunOfficialMaintenance(Reason)
    else if not MaintenanceSucceeded then
      Reason := 'official_package_extract_failed';
    ReleaseCodeBarrier();
    if not MaintenanceSucceeded then begin
      MsgBox(ExpandConstant('{cm:MaintenanceFailed}') + ' ' + Reason, mbError, MB_OK);
      Abort;
    end;
  end;
end;

procedure DeinitializeSetup();
begin
  ReleaseCodeBarrier();
end;

function ConfirmUninstallPlan(): Boolean;
var Warning, Question, FinalQuestion: String;
begin
  Result := False;
  DeletePersonalData := False;
  Warning := ExpandConstant('{cm:RemovalWarning}') + #13#10 + ExpandConstant('{app}');
  if MsgBox(Warning, mbConfirmation, MB_YESNO or MB_DEFBUTTON2) <> IDYES then Exit;
  Question := ExpandConstant('{cm:RemovalDeleteData}');
  if MsgBox(Question, mbConfirmation, MB_YESNO or MB_DEFBUTTON2) = IDYES then begin
    FinalQuestion := ExpandConstant('{cm:RemovalDeleteDataFinal}');
    if MsgBox(FinalQuestion, mbConfirmation, MB_YESNO or MB_DEFBUTTON2) <> IDYES then Exit;
    DeletePersonalData := True;
  end;
  Result := True;
end;

function IsUninstallerArtifact(Name: String): Boolean;
var UninstallerName, MetadataName: String;
begin
  UninstallerName := ExtractFileName(ExpandConstant('{uninstallexe}'));
  MetadataName := ChangeFileExt(UninstallerName, '.dat');
  Result := (CompareText(Name, UninstallerName) = 0) or (CompareText(Name, MetadataName) = 0);
end;

function DeleteSafeDirectory(Path: String; var Reason: String): Boolean;
var Count: LongWord;
begin
  Result := False;
  Reason := 'uninstall_delete_failed';
  if not DirExists(Path) then begin Result := True; Reason := ''; Exit; end;
  if not CodePathSafe(Path) then begin Reason := 'uninstall_boundary_invalid'; Exit; end;
  Count := 0;
  if not CodeDeletionTreeSafe(Path, 0, Count, False) then begin
    Reason := 'uninstall_reparse_or_hardlink'; Exit;
  end;
  if not DelTree(Path, True, True, True) then begin
    Reason := 'uninstall_delete_failed'; Exit;
  end;
  Result := True;
  Reason := '';
end;

function DeleteProjectProgramContent(var Reason: String): Boolean;
var Scan: TFindRec; Child, Name: String; Attributes: LongWord; Count: LongWord; Deleted: Boolean;
begin
  Result := False;
  Reason := 'uninstall_delete_failed';
  if not CodePathSafe(ExpandConstant('{app}')) then begin Reason := 'uninstall_boundary_invalid'; Exit; end;
  Count := 0;
  if not CodeDeletionTreeSafe(ExpandConstant('{app}'), 0, Count, True) then begin
    Reason := 'uninstall_reparse_or_hardlink'; Exit;
  end;
  if not FindFirst(AddBackslash(ExpandConstant('{app}')) + '*', Scan) then begin Result := True; Reason := ''; Exit; end;
  try
    repeat
      Name := Scan.Name;
      if (Name <> '.') and (Name <> '..') and (CompareText(Name, 'data') <> 0) and
         (CompareText(Name, '.core-files.lock') <> 0) and (not IsUninstallerArtifact(Name)) then begin
        Child := AddBackslash(ExpandConstant('{app}')) + Name;
        Attributes := CodeGetAttributes(Child);
        if (Attributes = CodeInvalidHandle) or ((Attributes and CodeReparsePoint) <> 0) then begin
          Reason := 'uninstall_reparse_or_hardlink'; Exit;
        end;
        if (Attributes and CodeDirectory) <> 0 then
          Deleted := DelTree(Child, True, True, True)
        else
          Deleted := DeleteFile(Child);
        if not Deleted then begin Reason := 'uninstall_delete_failed'; Exit; end;
      end;
    until not FindNext(Scan);
    Result := True;
    Reason := '';
  finally
    FindClose(Scan);
  end;
end;

function DeleteInstalledPackages(var Reason: String): Boolean;
begin
  Result := DeleteSafeDirectory(ExpandConstant('{app}\data\plugins'), Reason);
end;

function RunCoreRemovalMaintenance(var Reason: String): Boolean;
var Executable: String; ExitCode: Integer;
begin
  Result := False;
  Executable := ExpandConstant('{app}\dsh-pet-core-webm.exe');
  if not FileExists(Executable) then begin Reason := 'core_maintenance_missing'; Exit; end;
  if not Exec(Executable, '--core-maintenance uninstall', ExpandConstant('{app}'), SW_HIDE, ewWaitUntilTerminated, ExitCode) then begin
    Reason := 'core_maintenance_launch_failed'; Exit;
  end;
  if ExitCode <> 0 then begin Reason := 'core_maintenance_incomplete:' + IntToStr(ExitCode); Exit; end;
  Result := True;
  Reason := '';
end;

function InitializeUninstall(): Boolean;
begin
  // Inno may still present its own confirmation after this event. No locks,
  // maintenance child or writes are allowed until usUninstall below.
  Result := ConfirmUninstallPlan();
end;

function PrepareProjectRemoval(var Reason: String): Boolean;
begin
  Result := False;
  if not AcquirePortableCodeBarrier(ExpandConstant('{app}'), Reason) then begin
    MsgBox(ExpandConstant('{cm:RemovalBlocked}') + ' ' + Reason, mbError, MB_OK);
    Exit;
  end;
  if not AcquireRemovalBarrier(ExpandConstant('{app}\data'), Reason) then begin
    ReleaseCodeBarrier();
    MsgBox(ExpandConstant('{cm:RemovalBlocked}') + ' ' + Reason, mbError, MB_OK);
    Exit;
  end;
  if not RunCoreRemovalMaintenance(Reason) then begin
    ReleaseRemovalBarrier();
    ReleaseCodeBarrier();
    MsgBox(ExpandConstant('{cm:RemovalBlocked}') + ' ' + Reason, mbError, MB_OK);
    Exit;
  end;
  // Both barriers remain held until project cleanup has completed. No DLC
  // factory, ledger or package transaction is loaded in this path.
  Result := True;
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var Reason: String; Count: LongWord;
begin
  if CurUninstallStep = usUninstall then begin
    if not PrepareProjectRemoval(Reason) then Abort;
    // Validate selected data boundaries before deleting any program content.
    // This is a filesystem walk only: never inspect DLC ledgers or factories.
    Count := 0;
    if not CodeDeletionTreeSafe(ExpandConstant('{app}\data\plugins'), 0, Count, False) then begin
      MsgBox(ExpandConstant('{cm:RemovalCleanupFailed}') + ' uninstall_reparse_or_hardlink', mbError, MB_OK);
      Abort;
    end;
    if DeletePersonalData then begin
      Count := 0;
      if not CodeDeletionTreeSafe(ExpandConstant('{app}\data'), 0, Count, False) then begin
        MsgBox(ExpandConstant('{cm:RemovalCleanupFailed}') + ' uninstall_reparse_or_hardlink', mbError, MB_OK);
        Abort;
      end;
    end;
    if not DeleteProjectProgramContent(Reason) then begin
      MsgBox(ExpandConstant('{cm:RemovalCleanupFailed}') + ' ' + Reason, mbError, MB_OK);
      Abort;
    end;
    if not DeleteInstalledPackages(Reason) then begin
      MsgBox(ExpandConstant('{cm:RemovalCleanupFailed}') + ' ' + Reason, mbError, MB_OK);
      Abort;
    end;
  end else if CurUninstallStep = usPostUninstall then begin
    if DeletePersonalData then begin
      ReleaseRemovalBarrier();
      if not DeleteSafeDirectory(ExpandConstant('{app}\data'), Reason) then begin
        MsgBox(ExpandConstant('{cm:RemovalCleanupFailed}') + ' ' + Reason, mbError, MB_OK);
        Abort;
      end;
    end;
  end;
end;

procedure DeinitializeUninstall();
begin
  ReleaseRemovalBarrier();
  ReleaseCodeBarrier();
end;
