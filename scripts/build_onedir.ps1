# -*- coding: utf-8 -*-
<#
.SYNOPSIS
    dsh-pet-standalone onedir build + portable zip packaging.

.DESCRIPTION
    Builds a PyInstaller --onedir variant (no runtime extraction, no _MEI cache),
    output at dist-onedir\<name>\ plus a <name>-portable.zip green package.

    Variants:
      webm-chat   - WebM assets + AI chat (default)
      webm        - WebM assets, no chat
      gif-chat    - GIF assets + AI chat (run with -Gif to generate GIFs first)
      gif         - GIF assets, no chat

    Encoding isolation (issue #26):
      The whole build runs with PYTHONUTF8=1 + PYTHONIOENCODING=utf-8 so neither
      PyInstaller nor the helper scripts can decode UTF-8 sources/resources with
      a legacy codepage (GBK/cp1252). After PyInstaller, an encoding self-check
      (scripts\check_bundle_encoding.py) scans the bundle's bytecode/resources/
      filenames for known Chinese literals and fails the build if any are garbled.

    Examples:
      powershell -ExecutionPolicy Bypass -File scripts\build_onedir.ps1
      powershell -ExecutionPolicy Bypass -File scripts\build_onedir.ps1 -Variant webm -SkipZip
#>
param(
    [string]$Variant = 'webm-chat',
    [switch]$SkipBuild,
    [switch]$SkipZip,
    [switch]$SkipCheck,
    [switch]$Gif
)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
$buildStamp = (Get-Date).ToUniversalTime().ToString('o')
Write-Host "[build] pet-runtime-2026-09-01.1 build_started=$buildStamp source=$root" -ForegroundColor DarkCyan

# 编码隔离（issue #26）：整个构建过程强制 UTF-8。
# - PYTHONIOENCODING 只解决控制台 print 中文；PYTHONUTF8=1 让 Python 的
#   locale.getpreferredencoding() 恒为 utf-8，杜绝 PyInstaller/辅助脚本按
#   GBK/cp1252 二次解码源码或资源（乱码包根因）。
$env:PYTHONUTF8 = '1'
$env:PYTHONIOENCODING = 'utf-8'

$variants = @{
    'webm-chat' = @{ Name = 'dsh-pet-standalone-webm-chat'; Entry = 'packaging\pet_entry.py' }
    'webm'      = @{ Name = 'dsh-pet-standalone-webm';      Entry = 'packaging\pet_entry_no_chat.py'; NoChat = $true }
    'gif-chat'  = @{ Name = 'dsh-pet-standalone-gif-chat';  Entry = 'packaging\pet_entry.py'; Gif = $true }
    'gif'       = @{ Name = 'dsh-pet-standalone-gif';       Entry = 'packaging\pet_entry_no_chat.py'; Gif = $true; NoChat = $true }
}

if (-not $variants.ContainsKey($Variant)) {
    throw "Unknown variant: $Variant (available: $($variants.Keys -join ', '))"
}
$name  = $variants[$Variant].Name
$entry = $variants[$Variant].Entry
$isGif = $variants[$Variant].Gif
$noChat = $variants[$Variant].NoChat

# Bridge is installed by pnpm after the desktop bundle is unpacked.  Keep its
# runtime dependency in the plugin manifest and fail early if it is removed.
$bridgeManifest = Join-Path $root 'integrations\dsh-pet-bridge\package.json'
if (-not (Test-Path $bridgeManifest)) { throw "Bridge manifest missing: $bridgeManifest" }
# PowerShell 5.1 reads Get-Content using the system ANSI codepage by default;
# package.json is UTF-8 and its Chinese description would become invalid JSON.
$bridgeManifestJson = [System.IO.File]::ReadAllText($bridgeManifest, [System.Text.Encoding]::UTF8)
$bridgePackage = $bridgeManifestJson | ConvertFrom-Json

# 桥接插件依赖完整性硬校验（issue: Cannot find package '@deepseek-ai/cosmokit'）：
# Cordis 在 ESM import 时会立即解析它的运行时依赖（cosmokit / standard-schema），
# dsh-invariants / dsh-llm 又依赖 schemastery。任何一个漏进 devDependencies 或
# 从打包中漏掉，都会让 DSH 的 plugin tree 初始化失败、DSH 无法启动。
$bridgeRequiredDeps = @(
    '@deepseek-ai/cordis',
    '@deepseek-ai/cosmokit',
    '@deepseek-ai/dsh-attachment',
    '@deepseek-ai/dsh-brand',
    '@deepseek-ai/dsh-invariants',
    '@deepseek-ai/dsh-llm',
    '@deepseek-ai/dsh-timeout',
    '@deepseek-ai/schemastery',
    '@standard-schema/spec'
)
foreach ($dep in $bridgeRequiredDeps) {
    if (-not $bridgePackage.dependencies.$dep) {
        throw "[bridge] missing runtime dependency '$dep' in integrations\dsh-pet-bridge\package.json (must be in dependencies, not dev/peer)"
    }
}
$bridgeLock = Join-Path $root 'integrations\dsh-pet-bridge\pnpm-lock.yaml'
if (-not (Test-Path $bridgeLock)) { throw "Bridge lockfile missing: $bridgeLock" }
$bridgeLockText = [System.IO.File]::ReadAllText($bridgeLock, [System.Text.Encoding]::UTF8)
foreach ($snapshot in @('@deepseek-ai/cosmokit@1.8.3', '@deepseek-ai/schemastery@3.18.2', '@standard-schema/spec@1.1.0')) {
    if (-not $bridgeLockText.Contains($snapshot)) {
        throw "[bridge] lockfile missing snapshot for '$snapshot' - run pnpm install in integrations\dsh-pet-bridge and commit pnpm-lock.yaml"
    }
}
# 若本地已安装 node_modules，则真实执行一次 ESM 导入冒烟（等价于 Cordis loader 的
# import 路径）。构建环境没有 node_modules 时（CI 首次 checkout）只做声明校验，
# 由 install_bridge 在运行时用 pnpm 落盘；两者都不允许静默放行缺声明的情况。
# 冒烟脚本必须位于 bridge 目录内：ESM bare specifier 从脚本自身目录向上解析
# node_modules，放在根 scripts\ 下会误报全部依赖缺失。
$bridgeNodeModules = Join-Path $root 'integrations\dsh-pet-bridge\node_modules'
if (Test-Path $bridgeNodeModules) {
    $smoke = Join-Path $root 'integrations\dsh-pet-bridge\verify_import.mjs'
    if (-not (Test-Path $smoke)) { throw "missing verify script: $smoke" }
    Write-Host "[bridge] verifying plugin import + transitive deps..." -ForegroundColor Cyan
    & node $smoke
    if ($LASTEXITCODE -ne 0) { throw "[bridge] plugin import smoke test failed (exit $LASTEXITCODE)" }
    Write-Host "[bridge] import smoke OK" -ForegroundColor Green
} else {
    Write-Host "[bridge] node_modules absent - manifest/lockfile declaration check only (pnpm add resolves at install time)" -ForegroundColor Yellow
}
$bridgeLlmVersion = $bridgePackage.dependencies.'@deepseek-ai/dsh-llm'
if ($bridgeLlmVersion) {
    Write-Host "[bridge] legacy dsh-llm dependency declared: $bridgeLlmVersion"
} else {
    Write-Host "[bridge] standalone message envelope: no external dsh-llm dependency"
}

# GIF builds ship assets/characters_gif (webm dir must NOT be bundled, else runtime prefers webm)
$datas = if ($isGif) { 'assets/characters_gif;assets/characters_gif' } else { 'assets/characters;assets/characters' }
# No-chat builds exclude the chat subsystem and keyring (kept out of the bundle)
$excludes = if ($noChat) { @('--exclude-module', 'pet.chat', '--exclude-module', 'keyring') } else { @() }
# Chat 版必须显式收集 keyring（API Key 系统安全存储）；no-chat 不收集
$keyringCollect = if ($noChat) { @() } else { @('--collect-all', 'keyring') }
$chatData = if ($noChat) { @() } else {
    @(
        '--add-data', 'pet\chat\legacy_styles.qss;pet\chat',
        '--add-data', 'pet\chat\modern_styles.qss;pet\chat'
    )
}

# GIF variants: generate GIF assets from webm first (auto when missing, -Gif forces regen)
if ($isGif -and -not $Gif -and -not (Test-Path 'assets\characters_gif')) {
    $Gif = $true
}
if ($Gif -and -not $SkipBuild) {
    Write-Host "[1/3] Generating GIF assets..." -ForegroundColor Cyan
    python scripts\convert_to_gif.py --force --clean
    if ($LASTEXITCODE -ne 0) { throw "convert_to_gif failed: $LASTEXITCODE" }
}

if (-not $SkipBuild) {
    Write-Host "[0/3] Generating app icon..." -ForegroundColor Cyan
    python scripts\make_icon.py
    if ($LASTEXITCODE -ne 0) { throw "make_icon failed: $LASTEXITCODE" }

    # DLL 冲突隔离（issue: Qt6Core "procedure not found" / 找不到指定的程序）：
    # conda 的 Library\bin 与 MiKTeX 的 bin\x64 各自携带一套 Qt6/ICU DLL（版本与
    # PySide6 6.11 不匹配）。PyInstaller 的 bindepend 会按 PATH 解析 Qt6Core.dll 的
    # icuuc.dll 依赖并把 conda 的 ICU 75 打进包内，运行时 QtCore 加载即报
    # "DLL load failed ... 找不到指定的程序"。这里在构建期间把这两类目录从 PATH
    # 剔除，让 bindepend 只看到 PySide6 自带 DLL 与系统 System32 的兼容 ICU。
    # 注意：若 PySide6 是 conda 包（DLL 在 Library\bin），此剔除会导致 DLL 缺失，
    # 此时应改用 pip 版 PySide6 构建（DLL 在 site-packages\PySide6）。
    $env:PATH = ($env:PATH -split ';' | Where-Object {
    $_ -and $_ -notmatch '(?i)(conda|miniconda)[\\/].*[\\/]Library[\\/]bin$' -and
        $_ -notmatch '(?i)[\\/]envs[\\/].*[\\/]Library[\\/]bin$' -and
            $_ -notmatch '(?i)MiKTeX[\\/]miktex[\\/]bin'
    }) -join ';'

    # PyInstaller must replace the previous onedir executable.  A prior
    # smoke test or manual launch may still hold the file open on Windows.
    $running = Get-Process -Name $name -ErrorAction SilentlyContinue
    if ($running) {
        Write-Host "[build] stopping running $name before rebuild..." -ForegroundColor Yellow
        $running | Stop-Process -Force
        Start-Sleep -Milliseconds 500
    }

    Write-Host "[1/3] PyInstaller --onedir building $name ..." -ForegroundColor Cyan
    # 注入变体标识：配置目录/会话/开机自启按变体隔离（pet/config.py 读取）。
    # 必须写 BOM-free UTF-8：PowerShell 5.1 的 Set-Content -Encoding UTF8 会带
    # BOM，且内容若含中文再被旧编辑器按 GBK 另存就会污染产物（issue #26）。
    $variantPy = Join-Path $root 'packaging\build_variant.py'
    [System.IO.File]::WriteAllText(
        $variantPy,
        "VARIANT = '$Variant'`n",
        [System.Text.UTF8Encoding]::new($false)
    )
    python -m PyInstaller --noconfirm --clean --onedir --windowed --noupx `
        --name $name `
        --distpath dist-onedir `
        --workpath build-onedir `
        --icon assets\icon.ico `
        --collect-all imageio_ffmpeg `
        --collect-all certifi `
        --collect-all PySide6.QtMultimedia `
        @keyringCollect `
        --add-data $datas `
        --add-data "assets\big_blue_fat_fish;assets\big_blue_fat_fish" `
        --add-data "pet\persona_phrases.json;pet" `
        --add-data "pet\menu_templates;pet\menu_templates" `
        @chatData `
        --add-data "assets\sounds;assets\sounds" `
        --add-data "assets\chat;assets\chat" `
        --add-data "integrations;integrations" `
        @excludes `
        $entry
    if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed: $LASTEXITCODE" }
}

$appDir = Join-Path $root "dist-onedir\$name"
if (-not (Test-Path $appDir)) { throw "Build output missing: $appDir" }

# ---------- Bridge node_modules 自包含修复（issue: Cannot find package '@deepseek-ai/cosmokit'） ----------
# PyInstaller 的 --add-data 会把 pnpm 的 junction 布局复制成损坏的空目录/源机器链接，
# 导致 Cordis loader import bridge 时 cosmokit 等传递依赖解析失败、DSH 无法启动。
# 这里把源码 node_modules 展开复制成自包含真实目录树，并在 dist 副本上跑 import 冒烟。
Write-Host "[bridge] repairing bundle node_modules (expand junctions)..." -ForegroundColor Cyan
python scripts\fix_bridge_bundle.py --app-dir $appDir
if ($LASTEXITCODE -ne 0) { throw "Bridge bundle repair failed: $LASTEXITCODE" }
Write-Host "[bridge] bundle node_modules self-contained OK" -ForegroundColor Green

# =====================================================================
# Qt runtime post-build (issue: shiboken6 "找不到指定的模块")
# =====================================================================
# conda 版 PySide6 的 Qt6 runtime DLL 位于 <env>\Library\bin（不在
# site-packages\PySide6），PyInstaller 只收集了 .pyd 绑定，导致运行时
# QtCore.pyd 加载失败。这里用 sys.prefix 定位 conda 环境（不猜目录层数），
# 独立成 post-build 阶段：把 Qt6*.dll 与已验证的非系统依赖复制进 bundle
# 的 PySide6 runtime 目录，并兜底补齐 platform plugin。
# 注意：本段 Write-Host 消息保持纯 ASCII——PowerShell 5.1 按系统 ANSI
# 码页解析 .ps1，可执行字符串里的中文在 GBK 环境下会破坏引号配对。
$pythonPrefix = (& python -c "import sys; print(sys.prefix)").Trim()
$condaBin = Join-Path $pythonPrefix "Library\bin"
$condaQtPlugins = Join-Path $pythonPrefix "Library\lib\qt6\plugins"
$bundlePySide = Join-Path $appDir "_internal\PySide6"
$bundleShiboken = Join-Path $appDir "_internal\shiboken6"

# 此前已用 pefile 解析出的 conda Qt6 非系统依赖（Q6 运行必需）
$qtNonSystemDeps = @(
    'MSVCP140.dll','MSVCP140_1.dll','MSVCP140_2.dll',
    'VCRUNTIME140.dll','VCRUNTIME140_1.dll',
    'double-conversion.dll','freetype.dll','libcrypto-3-x64.dll',
    'libpng16.dll','pcre2-16.dll','zlib.dll','zstd.dll',
    'icudt75.dll','icuin75.dll','icuuc75.dll'
)

Write-Host "[Qt] Python prefix: $pythonPrefix"

if (Test-Path (Join-Path $condaBin 'Qt6Core.dll')) {
    # ---------- conda 版 PySide6：补充 Qt runtime ----------
    Write-Host "[Qt] Runtime source: $condaBin"
    Write-Host "[Qt] Copying runtime..."
    if (-not (Test-Path $bundlePySide)) { throw "[Qt] bundle PySide6 dir missing: $bundlePySide" }
    Get-ChildItem $condaBin -Filter 'Qt6*.dll' -ErrorAction SilentlyContinue | ForEach-Object {
        Copy-Item $_.FullName $bundlePySide -Force
    }
    foreach ($dep in $qtNonSystemDeps) {
        $depSrc = Join-Path $condaBin $dep
        if (Test-Path $depSrc) { Copy-Item $depSrc $bundlePySide -Force }
    }
    # conda PySide6/Shiboken 共享 DLL 在 Library\bin，不在 site-packages\shiboken6
    # Shiboken.pyd 直接依赖 shiboken6.cp310-win_amd64.dll（conda 命名），
    # QtCore.pyd 直接依赖 pyside6.cp310-win_amd64.dll（conda 命名）——
    # 缺任何一个都会报 "DLL load failed while importing ..."。
    $shibokenDll = Join-Path $condaBin 'shiboken6.cp310-win_amd64.dll'
    if (Test-Path $shibokenDll) {
        Copy-Item $shibokenDll $bundleShiboken -Force
        Write-Host "[Qt] copied shiboken6.cp310-win_amd64.dll to shiboken6/"
    }
    $pyside6Dll = Join-Path $condaBin 'pyside6.cp310-win_amd64.dll'
    if (Test-Path $pyside6Dll) {
        Copy-Item $pyside6Dll $bundlePySide -Force
        Write-Host "[Qt] copied pyside6.cp310-win_amd64.dll to PySide6/"
    }
    Write-Host "[Qt] Copying plugins..."
    $dstPlatforms = Join-Path $bundlePySide 'plugins\platforms'
    if (-not (Test-Path (Join-Path $dstPlatforms 'qwindows.dll'))) {
        if (Test-Path (Join-Path $condaQtPlugins 'platforms')) {
            if (-not (Test-Path $dstPlatforms)) { New-Item -ItemType Directory -Path $dstPlatforms -Force | Out-Null }
            Copy-Item (Join-Path $condaQtPlugins 'platforms\*') $dstPlatforms -Force
            Write-Host "[Qt] copied platform plugins from $condaQtPlugins"
        } else {
            throw "[Qt] qwindows.dll missing and conda plugin dir not found: $condaQtPlugins"
        }
    }
    Write-Host "[Qt] Runtime validation OK" -ForegroundColor Green

    # ---------- Fix Python SSL DLL missing for conda builds ----------
    # Copy Python SSL dependencies (libcrypto/libssl) from conda Library/bin to bundle _internal root
    # Required because _ssl.pyd (Python's SSL module) depends on these DLLs, they aren't always collected automatically
    foreach ($dep in @('libcrypto-3-x64.dll', 'libssl-3-x64.dll')) {
        $depSrc = Join-Path $condaBin $dep
        $depDst = Join-Path $appDir "_internal\$dep"
        if (Test-Path $depSrc) {
            Copy-Item $depSrc $depDst -Force
            Write-Host "[SSL] copied $dep to bundle _internal/ (fixes import ssl DLL load error)"
        }
    }
} else {
    Write-Host "[Qt] pip PySide6 (Qt6 DLL bundled), runtime copy skipped" -ForegroundColor Yellow
}

# ---------- Qt runtime 硬性验证（无论 conda/pip 都执行） ----------
$qtRequired = @(
    'Qt6Core.dll','Qt6Gui.dll','Qt6Widgets.dll','plugins\platforms\qwindows.dll'
)
foreach ($rel in $qtRequired) {
    $full = Join-Path $bundlePySide $rel
    if (-not (Test-Path $full)) { throw "[Qt] required file missing in bundle: $rel" }
}
if (Test-Path (Join-Path $condaBin 'Qt6Core.dll')) {
    foreach ($dep in $qtNonSystemDeps) {
        if (-not (Test-Path (Join-Path $bundlePySide $dep))) {
            throw "[Qt] conda Qt dependency missing in bundle: $dep"
        }
    }
    # 验证 shiboken6.cp310-win_amd64.dll 与 pyside6.cp310-win_amd64.dll 已进 bundle
    if (-not (Test-Path (Join-Path $bundleShiboken 'shiboken6.cp310-win_amd64.dll'))) {
        throw "[Qt] shiboken6.cp310-win_amd64.dll missing in bundle shiboken6/"
    }
    if (-not (Test-Path (Join-Path $bundlePySide 'pyside6.cp310-win_amd64.dll'))) {
        throw "[Qt] pyside6.cp310-win_amd64.dll missing in bundle PySide6/"
    }
}
Write-Host "[Qt] Runtime validation OK" -ForegroundColor Green

# DLL 冲突自检（issue: Qt6Core "procedure not found"）：若构建环境 PATH 里混入
# conda/MiKTeX 的 Qt6 或 ICU DLL，PyInstaller 会错误打包进 onedir 目录，运行时
# QtCore 加载报 "找不到指定的程序"。此处扫描产物，发现即中止并给出明确指引。
# 注：我们主动补进 _internal\PySide6 的 Qt6/ICU DLL（conda Qt 自身运行所需的
# icu*.dll）属预期，自检白名单排除该 runtime 目录；其他位置出现的意外
# ICU/Qt6 DLL 仍按原规则报告冲突。
# pip PySide6 6.11.2 and the bundled Poppler runtime both use ICU 78.3.
# PyInstaller resolves Poppler's ICU dependencies into _internal root, so
# location alone is not enough to classify these files as a conflict. Keep
# rejecting stale/foreign ICU versions, while allowing this known-compatible
# pair only after checking the file version.
$allowedIcu = @('icuuc.dll', 'icudt78.dll')
$badIcu = Get-ChildItem -Recurse -Path $appDir -Filter 'icu*.dll' -ErrorAction SilentlyContinue |
    Where-Object {
        if ($_.DirectoryName -like '*\_internal\PySide6') { return $false }
        $version = $_.VersionInfo.FileVersion
        -not ($allowedIcu -contains $_.Name -and $version -like '78, 3, 0, 0*')
    }
$badQt = Get-ChildItem -Recurse -Path $appDir -Filter 'Qt6*.dll' -ErrorAction SilentlyContinue |
    Where-Object {
        # pip PySide6 wheels are collected by PyInstaller into _internal root;
        # conda builds and manually copied runtimes use PySide6/.  Both are
        # valid locations, while nested foreign runtime directories remain
        # rejected.
        $_.DirectoryName -notlike '*\_internal' -and
            $_.DirectoryName -notlike '*\PySide6*' -and
            $_.DirectoryName -notlike '*\shiboken6*'
    }
if ($badIcu -or $badQt) {
    $names = @($badIcu.Name) + @($badQt.Name)
    throw "Bundle contains incompatible Qt/ICU DLLs ($($names -join ', ')). " +
        "This causes 'DLL load failed ... 找不到指定的程序'. Build with a PATH " +
        "that excludes conda Library\bin and MiKTeX miktex\bin."
}

# 中文编码自检（issue #26）：字节码字面量/文本资源/中文文件名任一项被
# 编码污染即中止，绝不把乱码包发出去。
if (-not $SkipCheck) {
    Write-Host "[1.5/3] Chinese-encoding self-check on bundle..." -ForegroundColor Cyan
    python scripts\check_bundle_encoding.py --dir $appDir
    if ($LASTEXITCODE -ne 0) {
        throw "Bundle encoding check failed - refusing to package garbled output (issue #26)"
    }
}

# ---------- exe smoke test（启动成功才继续打包） ----------
# 注意：PyInstaller --windowed 在 import 失败时会弹错误对话框且进程存活，
# 只看"进程 8 秒没退出"是假阳性。这里先做确定性加载链验证（从 bundle 布局
# 真实加载 Shiboken/QtCore/QtGui/QtWidgets），再启动 exe 检查主窗口出现。
$exePath = Join-Path $appDir "$name.exe"
if (-not (Test-Path $exePath)) { throw "Build exe missing: $exePath" }

$verifyScript = Join-Path $root 'scripts\verify_bundle_qt.py'
if (-not (Test-Path $verifyScript)) { throw "missing verify script: $verifyScript" }
Write-Host "[smoke] verifying bundle DLL chain (Shiboken/QtCore/QtGui/QtWidgets)..." -ForegroundColor Cyan
python $verifyScript --internal (Join-Path $appDir '_internal')
if ($LASTEXITCODE -ne 0) { throw "[smoke] bundle DLL chain verification failed" }
Write-Host "[smoke] bundle DLL chain OK" -ForegroundColor Green

Write-Host "[smoke] Launching $exePath ..." -ForegroundColor Cyan
$proc = Start-Process -FilePath $exePath -PassThru
Start-Sleep -Seconds 10
if ($proc.HasExited) {
    throw "[smoke] exe exited early (code $($proc.ExitCode)) - runtime dependency broken"
}
$proc.Refresh()
if ($proc.MainWindowHandle -eq 0) {
    Stop-Process -Id $proc.Id -Force -ErrorAction SilentlyContinue
    throw "[smoke] exe running but no main window appeared - startup failed (likely 'Failed to execute script')"
}
Stop-Process -Id $proc.Id -Force -ErrorAction SilentlyContinue
Write-Host "[smoke] exe started OK" -ForegroundColor Green

if (-not $SkipZip) {
    Write-Host "[2/3] Packing portable zip..." -ForegroundColor Cyan
    $zip = Join-Path $root "dist-onedir\$name-portable.zip"
    Remove-Item $zip -Force -ErrorAction SilentlyContinue
    Compress-Archive -Path "$appDir\*" -DestinationPath $zip -CompressionLevel Optimal
    Write-Host "      $zip ($([math]::Round((Get-Item $zip).Length/1MB,1)) MB)" -ForegroundColor Green
}

Write-Host "[3/3] Done. onedir dir: $appDir" -ForegroundColor Green
Write-Host "      Installer: compile packaging\dsh-pet-$Variant.iss with ISCC.exe"
