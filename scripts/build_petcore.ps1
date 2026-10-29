# petcore onedir 打包脚本
# 用法：
#   pwsh -NoProfile -ExecutionPolicy Bypass -File scripts/build_petcore.ps1 -Clean
#   pwsh -NoProfile -ExecutionPolicy Bypass -File scripts/build_petcore.ps1 -Clean -Installer
#   # 代码签名（Smart App Control / SmartScreen 会拦截未签名的构建产物）：
#   pwsh ... -Clean -Installer -SignThumbprint <证书指纹>
#   证书需在 Cert:\CurrentUser\My，并把公钥导入 CurrentUser\Root + TrustedPublisher；
#   见 docs/ONEDIR_PACKAGING.md「构建产物签名」。
param(
    [switch]$Clean,
    [switch]$Installer,
    [switch]$Console,
    # 只解析并打印最终路径（供候选包脚本与单测验证收束规则），不做任何构建。
    [switch]$DryRun,
    [string]$SignThumbprint = "",
    [string]$TimestampServer = "http://timestamp.digicert.com",
    # 隔离输出：dist/work/spec 与 --check 结果全部落在该目录，便于候选包验收不污染根 dist。
    [string]$OutputRoot = "",
    # 构建用解释器；默认项目 .venv，缺依赖时指向独立构建 venv。
    [string]$PythonPath = ""
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

function Resolve-OutputRoot([string]$Value) {
    # 相对路径必须相对**仓库根**解析：`[IO.Path]::GetFullPath` 用的是进程 CWD，
    # 而 `Set-Location` 不改它，于是 `-OutputRoot rel` 会落到调用者目录（审计 AUD-37）。
    if ([IO.Path]::IsPathRooted($Value)) { return [IO.Path]::GetFullPath($Value) }
    return [IO.Path]::GetFullPath((Join-Path $root $Value))
}

function Normalize-Path([string]$Value) {
    return ([IO.Path]::GetFullPath($Value)).TrimEnd('\', '/')
}

$python = if ($PythonPath) { (Resolve-Path -LiteralPath $PythonPath).Path } else { Join-Path $root ".venv/Scripts/python.exe" }

$distRoot = if ($OutputRoot) { Resolve-OutputRoot $OutputRoot } else { Join-Path $root "dist" }
if ($OutputRoot) {
    # 尾部反斜杠会让 `-eq` 比较失配，先归一化再比。
    if ((Normalize-Path $distRoot) -eq (Normalize-Path $root)) {
        throw "OutputRoot must not be the repository root"
    }
    if (Test-Path (Join-Path $distRoot "dsh-pet-core-setup.exe")) {
        throw "OutputRoot already contains an installer; choose a fresh directory"
    }
}
$workRoot = if ($OutputRoot) { Join-Path $distRoot ".work" } else { Join-Path $root "build" }
$specRoot = if ($OutputRoot) { $distRoot } else { $root }

if ($DryRun) {
    Write-Host "root:     $(Normalize-Path $root)"
    Write-Host "dist:     $(Normalize-Path $distRoot)"
    Write-Host "work:     $(Normalize-Path $workRoot)"
    Write-Host "spec:     $(Normalize-Path $specRoot)"
    Write-Host "python:   $python"
    Write-Host "check:    $(Normalize-Path (Join-Path $distRoot 'check.json'))"
    return
}

if (-not (Test-Path $python)) {
    throw "missing python: $python"
}
if ($OutputRoot) {
    New-Item -ItemType Directory -Force -Path $distRoot | Out-Null
}

function Sign-Artifact([string]$Path) {
    if (-not $SignThumbprint) { return }
    if (-not (Test-Path $Path)) { throw "cannot sign missing file: $Path" }
    $cert = Get-Item "Cert:\CurrentUser\My\$SignThumbprint" -ErrorAction Stop
    try {
        $sig = Set-AuthenticodeSignature -FilePath $Path -Certificate $cert `
            -HashAlgorithm SHA256 -TimestampServer $TimestampServer
    } catch {
        Write-Host "timestamp server unavailable, signing without timestamp: $($_.Exception.Message)"
        $sig = Set-AuthenticodeSignature -FilePath $Path -Certificate $cert -HashAlgorithm SHA256
    }
    if ($sig.Status -ne "Valid") {
        # 时间戳服务器超时不一定抛异常，而是返回 UnknownError（0x80072ee2）；
        # 这种情况下再签一次不带时间戳的（签名本身有效，只是没有可信时间）。
        Write-Host "timestamped signature returned $($sig.Status); retrying without timestamp"
        $sig = Set-AuthenticodeSignature -FilePath $Path -Certificate $cert -HashAlgorithm SHA256
    }
    if ($sig.Status -ne "Valid") {
        throw "signature invalid for $Path : $($sig.Status) $($sig.StatusMessage)"
    }
    Write-Host "signed: $Path ($($cert.Subject))"
}

$pyiArgs = @(
    "-m", "PyInstaller",
    "--noconfirm",
    "--onedir",
    $(if ($Console) { "--console" } else { "--windowed" }),
    "--name", "dsh-pet-core",
    "--distpath", $distRoot,
    "--workpath", $workRoot,
    "--specpath", $specRoot,
    "--icon", "$root/assets/icon.ico",
    "--paths", $root,
    "--add-data", "$root/assets;assets",
    # 右键菜单默认布局随包分发；PyInstaller 只收集 .py，漏掉这份 JSON 会让
    # 菜单退化成「桌宠设置/退出」（代码侧有内置兜底，但打包仍必须带文件）。
    "--add-data", "$root/petcore/menu_templates;petcore/menu_templates",
    # 致谢/粉丝随包快照：关于页在断网时仍要显示「提交时状态」。
    "--add-data", "$root/petcore/community/data;petcore/community/data",
    "--collect-submodules", "petcore",
    "--hidden-import", "pet.webm_clip",
    "--hidden-import", "pet.library",
    "--hidden-import", "pet.frameseq_clip",
    "--hidden-import", "pet.frameseq_provision",
    "--hidden-import", "PIL",
    # 内置/示例 mod 以数据形式随包分发，PyInstaller 静态分析看不到它们的惰性 import：
    # psutil（网络信息/回收内存）、QtTextToSpeech（voice.chime 旧实现，仍保留以防回退）。
    "--hidden-import", "psutil",
    # fun.festival 动态加载；农历/24节气依赖不能靠静态分析碰巧收集。
    "--collect-submodules", "lunar_python",
    "--hidden-import", "PySide6.QtTextToSpeech",
    # P4-b 语音：edge-tts 在 petcore/net/tts.py 里惰性 import；SMTC 走 winrt，
    # 峰值检测走 pycaw（media.music 扩展）。漏收就是「装了扩展但合成/识别不可用」。
    "--collect-submodules", "edge_tts",
    "--hidden-import", "winrt.windows.media.control",
    "--hidden-import", "winrt.windows.foundation",
    "--hidden-import", "winrt.windows.storage.streams",
    "--hidden-import", "pycaw",
    # API 密钥存系统钥匙串（惰性 import）：keyring 的 Windows 后端与依赖必须显式收集。
    "--hidden-import", "keyring.backends.Windows",
    "--collect-submodules", "keyring",
    "--collect-submodules", "win32ctypes",
    # 本地 OCR（tool.ocr 扩展）：模型随包分发，rapidocr 的惰性 import 静态分析看不到。
    # 与主分支 build_onedir.ps1 同一口径：显式带 config.yaml/default_models.yaml，
    # 否则 rapidocr 会走「模型路径为空就下载」的分支。
    "--add-data", "$root/resources/rapidocr_models;resources/rapidocr_models",
    # 游戏帧率探针（灵动岛 fps 段）：PresentMon 二进制随包分发，运行时不查 PATH。
    # 构建前必须过 verify_presentmon.py 的文件名/SHA256/x86-64 三道门。
    "--add-data", "$root/resources/presentmon;resources/presentmon",
    "--collect-submodules", "rapidocr",
    "--hidden-import", "onnxruntime",
    "--hidden-import", "cv2",
    "--hidden-import", "numpy",
    "--hidden-import", "omegaconf",
    "packaging/petcore_entry.py"
)
$rapidocrDir = (& $python -c "import os, rapidocr; print(os.path.dirname(rapidocr.__file__))").Trim()
if ($rapidocrDir) {
    foreach ($cfgName in @("config.yaml", "default_models.yaml")) {
        $cfgPath = Join-Path $rapidocrDir $cfgName
        if (Test-Path $cfgPath) {
            $pyiArgs = @($pyiArgs[0], $pyiArgs[1], "--add-data", "$cfgPath;rapidocr") + $pyiArgs[2..($pyiArgs.Count - 1)]
        }
    }
}
if ($Clean) {
    $pyiArgs = @($pyiArgs[0], $pyiArgs[1], "--clean") + $pyiArgs[2..($pyiArgs.Count - 1)]
}
$builtin = Join-Path $root "petcore/builtin"
if (Test-Path $builtin) {
    $pyiArgs = @($pyiArgs[0], $pyiArgs[1], "--add-data", "$builtin;petcore/builtin") + $pyiArgs[2..($pyiArgs.Count - 1)]
}

Write-Host "== verify presentmon (source, before packaging) =="
# 先验源文件：缺件/换版时不该先跑一遍完整打包（onedir + Qt + OCR 模型要几分钟）。
& $python scripts/verify_presentmon.py --path resources/presentmon/PresentMon-2.6.0-x64.exe
if ($LASTEXITCODE -ne 0) {
    throw "bundled PresentMon failed verification (name/sha256/machine)"
}

Write-Host "== PyInstaller =="
& $python @pyiArgs
if ($LASTEXITCODE -ne 0) {
    throw "PyInstaller failed with exit code $LASTEXITCODE"
}

$exe = Join-Path $distRoot "dsh-pet-core/dsh-pet-core.exe"
if (-not (Test-Path $exe)) {
    throw "missing build output: $exe"
}

# Smart App Control/SmartScreen 会拦截未签名的新构建：先签名再冒烟。
Sign-Artifact $exe

Write-Host "== verify presentmon (packaged) =="
$builtPresentMon = Join-Path $distRoot "dsh-pet-core/_internal/resources/presentmon/PresentMon-2.6.0-x64.exe"
if (Test-Path $builtPresentMon) {
    & $python scripts/verify_presentmon.py --path $builtPresentMon
    if ($LASTEXITCODE -ne 0) {
        throw "packaged PresentMon failed verification"
    }
} else {
    Write-Host "warn: packaged PresentMon not found at $builtPresentMon (onedir layout may differ)"
}

Write-Host "== smoke --check --strict =="
$checkOut = Join-Path $distRoot "check.json"
# 脚本约定「不删任何东西」：用时间戳识别旧报告，而不是先删文件。
$checkBefore = $null
if (Test-Path -LiteralPath $checkOut) {
    $checkBefore = (Get-Item -LiteralPath $checkOut).LastWriteTimeUtc
}
# ``-Windowed`` 产物是 GUI 子系统：直接 ``& $exe`` 时 PowerShell 不等待进程退出
# （实测 52ms 返回、$LASTEXITCODE 为空、check.json 还没落盘），冒烟读到的永远是
# 上一份报告或直接抛「路径不存在」。必须用 Start-Process -Wait 拿真实退出码。
$smoke = Start-Process -FilePath $exe `
    -ArgumentList @("--check --strict", "--out", "`"$checkOut`"") `
    -Wait -PassThru -NoNewWindow
if ($smoke.ExitCode -ne 0) {
    throw "packaged --check --strict failed with exit code $($smoke.ExitCode)"
}
if (-not (Test-Path -LiteralPath $checkOut)) {
    throw "packaged --check --strict did not write $checkOut"
}
if ($checkBefore -and (Get-Item -LiteralPath $checkOut).LastWriteTimeUtc -le $checkBefore) {
    throw "packaged --check --strict left a stale report at $checkOut"
}
# 退出码之外再读一遍报告：菜单模板/内置配图/角色缺失都必须让构建红。
$report = Get-Content -Raw -LiteralPath $checkOut | ConvertFrom-Json
if ($report.menu_template -ne "json") {
    throw "packaged menu_template is '$($report.menu_template)' (expected json): petcore/menu_templates did not ship"
}
if ([int]$report.self_talk_images.builtin_readable -le 0) {
    throw "packaged builtin self-talk images are unreadable (assets or Qt image plugin missing)"
}
if (-not $report.characters -or $report.characters.Count -eq 0) {
    throw "packaged build contains no characters"
}
if ($report.diagnostics -and $report.diagnostics.Count -gt 0) {
    throw "packaged --check reported $($report.diagnostics.Count) diagnostic(s): $($report.diagnostics -join '; ')"
}
Write-Host ("check: menu_template={0} characters={1} builtin_images={2} strict={3}" -f `
    $report.menu_template, $report.characters.Count, $report.self_talk_images.builtin_readable, $report.strict.enabled)
Write-Host "built: $(Join-Path $distRoot 'dsh-pet-core')"

if ($Installer) {
    $iscc = Join-Path $root "tools/InnoSetup/ISCC.exe"
    if (-not (Test-Path $iscc)) {
        throw "missing tools/InnoSetup/ISCC.exe; run scripts/setup_packaging.ps1 first"
    }
    $version = (& $python -c "import petcore; print(petcore.__version__)").Trim()
    $isccArgs = @(
        "/DMyAppVersion=$version"
        "/DMySourceDist=$distRoot\dsh-pet-core"
        "/DMyOutputDir=$distRoot"
    )
    if ($SignThumbprint) {
        # 安装包里唯一「构建时不存在的 PE」是 unins000.exe（Inno 安装时现场生成），
        # 而 Smart App Control 实测只拦它。这里把我们的签名脚本注册成 Inno 的
        # SignTool（本机无 signtool.exe），由 iss 里的 SignedUninstaller=yes 调用。
        # 路径不含空格时不用引号；$q$f$q 让 Inno 自己给文件路径加引号。
        $signer = Join-Path $root "scripts/sign_artifact.ps1"
        $isccArgs = @(
            "/Sdevsign=pwsh -NoProfile -ExecutionPolicy Bypass -File $signer -Thumbprint $SignThumbprint -TimestampServer $TimestampServer -Path `$q`$f`$q"
            "/DSignUninstaller=1"
        ) + $isccArgs
        Write-Host "== Inno Setup (signed uninstaller) =="
    } else {
        Write-Host "== Inno Setup =="
    }
    & $iscc @isccArgs "packaging/petcore.iss"
    if ($LASTEXITCODE -ne 0) {
        throw "Inno Setup failed with exit code $LASTEXITCODE"
    }
    $setup = Join-Path $distRoot "dsh-pet-core-setup.exe"
    Sign-Artifact $setup
    Write-Host "installer: $setup"
}
