; petcore onedir installer (Inno Setup 6)
; Build the app first: pwsh -File scripts/build_petcore.ps1
; Then: ISCC.exe packaging\petcore.iss
;
; 安装目录规则：
;   1. 目录页必须显示（DisableDirPage=no），用户可以改安装位置；
;   2. 已经装过时预选上次的安装路径（UsePreviousAppDir=yes，见下方 [Code]）。
#ifndef MyAppVersion
#define MyAppVersion "0.1.0"
#endif
; 候选包隔离输出：由 scripts/build_petcore.ps1 的 -OutputRoot 传入；
; 不传时保持旧行为（仓库根 dist）。
#ifndef MySourceDist
#define MySourceDist "..\dist\dsh-pet-core"
#endif
#ifndef MyOutputDir
#define MyOutputDir "..\dist"
#endif

[Setup]
AppId={{8f0f2d4e-4f6c-4f7a-9b7c-1f0b6a2d9c31}
AppName=dsh-pet core
AppVersion={#MyAppVersion}
AppPublisher=merzlin
PrivilegesRequired=lowest
; {code:GetDefaultDir} 读取卸载项里记录的 InstallLocation；没有旧安装时回落
; {localappdata}\Programs\dsh-pet-core。Inno 的 WizardDirValue 是只读属性，
; 不能在 InitializeSetup 里赋值，所以用脚本化常量来预选旧路径。
DefaultDirName={code:GetDefaultDir}
DisableDirPage=no
UsePreviousAppDir=yes
DisableProgramGroupPage=yes
OutputDir={#MyOutputDir}
OutputBaseFilename=dsh-pet-core-setup
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
CloseApplications=yes
CloseApplicationsFilter=*.exe
RestartApplications=no
SetupIconFile=..\assets\icon.ico
UninstallDisplayIcon={app}\dsh-pet-core.exe
UninstallDisplayName=dsh-pet core
; Smart App Control（VerifiedAndReputablePolicyState=1）会拦截安装包里未签名的 PE：
; 实测本机只拦了安装时才生成的 unins000.exe（CodeIntegrity 3077/3118），而用开发证书
; 签过的 setup/exe 都能跑。所以「装上时把卸载器也签掉」：签名工具由 build_petcore.ps1
; 通过 /Sdevsign=... 传进来（本机没有 signtool，用 scripts/sign_artifact.ps1 包装）。
; 不传 -SignThumbprint 时不生效，走原来的未签名流程。
#ifdef SignUninstaller
SignTool=devsign
SignedUninstaller=yes
#endif

[Languages]
Name: "chinesesimp"; MessagesFile: "ChineseSimplified.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked
; 以下官方 feature 默认不勾选；勾选才写入安装目录，不勾选完全不落盘。
Name: "mod_divination"; Description: "抽签（fun.divination）"; GroupDescription: "随附扩展：不勾选就不会写进安装目录，之后随安装程序卸载"; Flags: unchecked
Name: "mod_festival"; Description: "节日提醒（fun.festival）"; GroupDescription: "随附扩展：不勾选就不会写进安装目录，之后随安装程序卸载"; Flags: unchecked
Name: "mod_todo"; Description: "待办清单（reminder.todo）"; GroupDescription: "随附扩展：不勾选就不会写进安装目录，之后随安装程序卸载"; Flags: unchecked
Name: "mod_chime"; Description: "整点报时（voice.chime）"; GroupDescription: "随附扩展：不勾选就不会写进安装目录，之后随安装程序卸载"; Flags: unchecked
Name: "mod_music"; Description: "音乐信息（media.music）"; GroupDescription: "随附扩展：不勾选就不会写进安装目录，之后随安装程序卸载"; Flags: unchecked
Name: "mod_aichat"; Description: "AI 对话（ai.chat，需自备 API）"; GroupDescription: "随附扩展：不勾选就不会写进安装目录，之后随安装程序卸载"; Flags: unchecked
Name: "mod_ocr"; Description: "本地 OCR（tool.ocr，离线提取文字）"; GroupDescription: "随附扩展：不勾选就不会写进安装目录，之后随安装程序卸载"; Flags: unchecked
Name: "mod_weather"; Description: "天气（tool.weather，需联网）"; GroupDescription: "随附扩展：不勾选就不会写进安装目录，之后随安装程序卸载"; Flags: unchecked
Name: "mod_filereader"; Description: "文件解读（tool.file-reader，需 API）"; GroupDescription: "随附扩展：不勾选就不会写进安装目录，之后随安装程序卸载"; Flags: unchecked
Name: "mod_agent"; Description: "Agent 联动（ai.agent，需 DSH 桥接或自写事件）"; GroupDescription: "随附扩展：不勾选就不会写进安装目录，之后随安装程序卸载"; Flags: unchecked
Name: "mod_qq"; Description: "QQ 互通（im.qq，Beta：需自备 OneBot 网关 NapCat/Lagrange）"; GroupDescription: "随附扩展：不勾选就不会写进安装目录，之后随安装程序卸载"; Flags: unchecked
Name: "mod_aibrowser"; Description: "AI 网页（tool.ai-browser：在 AI 对话窗里内嵌网页版 AI，需 Edge WebView2 运行时）"; GroupDescription: "随附扩展：不勾选就不会写进安装目录，之后随安装程序卸载"; Flags: unchecked

[Files]
Source: "{#MySourceDist}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

; 随附扩展：装到安装目录的 mods\ 下（卸载随安装目录一起删除），`.trusted`
; 标记表示「安装器授权的官方包」。用户导入的扩展仍装到用户数据目录。
Source: "..\examples\petcore-mods\fun.divination\*"; DestDir: "{app}\mods\fun.divination"; Flags: ignoreversion recursesubdirs createallsubdirs; Excludes: "__pycache__\*,*.pyc"; Tasks: mod_divination
Source: "trust_marker.txt"; DestDir: "{app}\mods\fun.divination"; DestName: ".trusted"; Flags: ignoreversion; Tasks: mod_divination
Source: "..\examples\petcore-mods\fun.festival\*"; DestDir: "{app}\mods\fun.festival"; Flags: ignoreversion recursesubdirs createallsubdirs; Excludes: "__pycache__\*,*.pyc"; Tasks: mod_festival
Source: "trust_marker.txt"; DestDir: "{app}\mods\fun.festival"; DestName: ".trusted"; Flags: ignoreversion; Tasks: mod_festival
Source: "..\examples\petcore-mods\reminder.todo\*"; DestDir: "{app}\mods\reminder.todo"; Flags: ignoreversion recursesubdirs createallsubdirs; Excludes: "__pycache__\*,*.pyc"; Tasks: mod_todo
Source: "trust_marker.txt"; DestDir: "{app}\mods\reminder.todo"; DestName: ".trusted"; Flags: ignoreversion; Tasks: mod_todo
Source: "..\examples\petcore-mods\voice.chime\*"; DestDir: "{app}\mods\voice.chime"; Flags: ignoreversion recursesubdirs createallsubdirs; Excludes: "__pycache__\*,*.pyc"; Tasks: mod_chime
Source: "trust_marker.txt"; DestDir: "{app}\mods\voice.chime"; DestName: ".trusted"; Flags: ignoreversion; Tasks: mod_chime
Source: "..\examples\petcore-mods\media.music\*"; DestDir: "{app}\mods\media.music"; Flags: ignoreversion recursesubdirs createallsubdirs; Excludes: "__pycache__\*,*.pyc"; Tasks: mod_music
Source: "trust_marker.txt"; DestDir: "{app}\mods\media.music"; DestName: ".trusted"; Flags: ignoreversion; Tasks: mod_music
Source: "..\examples\petcore-mods\ai.chat\*"; DestDir: "{app}\mods\ai.chat"; Flags: ignoreversion recursesubdirs createallsubdirs; Excludes: "__pycache__\*,*.pyc"; Tasks: mod_aichat
Source: "trust_marker.txt"; DestDir: "{app}\mods\ai.chat"; DestName: ".trusted"; Flags: ignoreversion; Tasks: mod_aichat
Source: "..\examples\petcore-mods\tool.ocr\*"; DestDir: "{app}\mods\tool.ocr"; Flags: ignoreversion recursesubdirs createallsubdirs; Excludes: "__pycache__\*,*.pyc"; Tasks: mod_ocr
Source: "trust_marker.txt"; DestDir: "{app}\mods\tool.ocr"; DestName: ".trusted"; Flags: ignoreversion; Tasks: mod_ocr
Source: "..\examples\petcore-mods\tool.weather\*"; DestDir: "{app}\mods\tool.weather"; Flags: ignoreversion recursesubdirs createallsubdirs; Excludes: "__pycache__\*,*.pyc"; Tasks: mod_weather
Source: "trust_marker.txt"; DestDir: "{app}\mods\tool.weather"; DestName: ".trusted"; Flags: ignoreversion; Tasks: mod_weather
Source: "..\examples\petcore-mods\tool.file-reader\*"; DestDir: "{app}\mods\tool.file-reader"; Flags: ignoreversion recursesubdirs createallsubdirs; Excludes: "__pycache__\*,*.pyc"; Tasks: mod_filereader
Source: "trust_marker.txt"; DestDir: "{app}\mods\tool.file-reader"; DestName: ".trusted"; Flags: ignoreversion; Tasks: mod_filereader
Source: "..\examples\petcore-mods\ai.agent\*"; DestDir: "{app}\mods\ai.agent"; Flags: ignoreversion recursesubdirs createallsubdirs; Excludes: "__pycache__\*,*.pyc"; Tasks: mod_agent
Source: "trust_marker.txt"; DestDir: "{app}\mods\ai.agent"; DestName: ".trusted"; Flags: ignoreversion; Tasks: mod_agent
Source: "..\examples\petcore-mods\im.qq\*"; DestDir: "{app}\mods\im.qq"; Flags: ignoreversion recursesubdirs createallsubdirs; Excludes: "__pycache__\*,*.pyc"; Tasks: mod_qq
Source: "trust_marker.txt"; DestDir: "{app}\mods\im.qq"; DestName: ".trusted"; Flags: ignoreversion; Tasks: mod_qq
Source: "..\examples\petcore-mods\tool.ai-browser\*"; DestDir: "{app}\mods\tool.ai-browser"; Flags: ignoreversion recursesubdirs createallsubdirs; Excludes: "__pycache__\*,*.pyc"; Tasks: mod_aibrowser
Source: "trust_marker.txt"; DestDir: "{app}\mods\tool.ai-browser"; DestName: ".trusted"; Flags: ignoreversion; Tasks: mod_aibrowser

[Icons]
Name: "{autoprograms}\dsh-pet core"; Filename: "{app}\dsh-pet-core.exe"
Name: "{autodesktop}\dsh-pet core"; Filename: "{app}\dsh-pet-core.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\dsh-pet-core.exe"; Description: "{cm:LaunchProgram,dsh-pet core}"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
Type: filesandordirs; Name: "{app}\mods"

[Code]
function PreviousInstallDir(): String;
var
  Value: String;
begin
  Result := '';
  if RegQueryStringValue(HKCU,
    'Software\Microsoft\Windows\CurrentVersion\Uninstall\{8f0f2d4e-4f6c-4f7a-9b7c-1f0b6a2d9c31}_is1',
    'InstallLocation', Value) then
    Result := Value;
end;

function GetDefaultDir(Param: String): String;
var
  Previous: String;
begin
  Previous := PreviousInstallDir();
  if Previous <> '' then
    Result := Previous
  else
    Result := ExpandConstant('{localappdata}\Programs\dsh-pet-core');
end;

{ 「AI 网页」扩展（tool.ai-browser）的网页由系统的 Edge WebView2 渲染。
  Win11 一般自带；缺失时**不阻塞安装**，只在「准备安装」页提示一句，
  应用内的扩展设置页有「打开微软下载页」+「重试」。 }
function WebView2Present(): Boolean;
begin
  Result :=
    RegKeyExists(HKEY_LOCAL_MACHINE,
      'SOFTWARE\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}') or
    RegKeyExists(HKEY_LOCAL_MACHINE,
      'SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}') or
    RegKeyExists(HKEY_CURRENT_USER,
      'SOFTWARE\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}');
end;

function UpdateReadyMemo(Space, NewLine, MemoUserInfoInfo, MemoDirInfo,
  MemoTypeInfo, MemoComponentsInfo, MemoGroupInfo, MemoTasksInfo: String): String;
var
  Parts: String;
begin
  { 逐段拼回默认内容，只在末尾追加一条 WebView2 提示：自己覆盖这个函数时
    漏掉任何一段，用户的「准备安装」页就会少一块信息。 }
  Parts := '';
  if MemoUserInfoInfo <> '' then
    Parts := MemoUserInfoInfo;
  if MemoDirInfo <> '' then
  begin
    if Parts <> '' then Parts := Parts + NewLine + NewLine;
    Parts := Parts + MemoDirInfo;
  end;
  if MemoGroupInfo <> '' then
  begin
    if Parts <> '' then Parts := Parts + NewLine + NewLine;
    Parts := Parts + MemoGroupInfo;
  end;
  if MemoComponentsInfo <> '' then
  begin
    if Parts <> '' then Parts := Parts + NewLine + NewLine;
    Parts := Parts + MemoComponentsInfo;
  end;
  if MemoTasksInfo <> '' then
  begin
    if Parts <> '' then Parts := Parts + NewLine + NewLine;
    Parts := Parts + MemoTasksInfo;
  end;
  if not WebView2Present() then
  begin
    if Parts <> '' then Parts := Parts + NewLine + NewLine;
    Parts := Parts +
      '提示：未检测到 Microsoft Edge WebView2 运行时。' + NewLine +
      '「AI 网页」扩展需要它才能显示网页（不影响安装与其它功能）；' + NewLine +
      '缺省时可在「设置 → 扩展管理 → AI 网页」里点「打开微软下载页」安装。';
  end;
  Result := Parts;
end;
