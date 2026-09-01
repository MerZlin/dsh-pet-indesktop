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
# 设置页样式表（批6-7 从 modern_settings_dialog.py 抽出）：全部变体都需要
$settingsQssData = @(
    '--add-data', 'pet\settings_styles.qss;pet',
    '--add-data', 'pet\settings_styles_dark.qss;pet',
    '--add-data', 'pet\settings_styles_dark_browser.qss;pet'
)

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
        --add-data "pet\menu_templates;pet\menu_templates" `
        @chatData `
        @settingsQssData `
        --add-data "assets\sounds;assets\sounds" `
        --add-data "assets\chat;assets\chat" `
        --add-data "integrations;integrations" `
        @excludes `
        $entry
    if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed: $LASTEXITCODE" }
}

$appDir = Join-Path $root "dist-onedir\$name"
if (-not (Test-Path $appDir)) { throw "Build output missing: $appDir" }

# 中文编码自检（issue #26）：字节码字面量/文本资源/中文文件名任一项被
# 编码污染即中止，绝不把乱码包发出去。
if (-not $SkipCheck) {
    Write-Host "[1.5/3] Chinese-encoding self-check on bundle..." -ForegroundColor Cyan
    python scripts\check_bundle_encoding.py --dir $appDir
    if ($LASTEXITCODE -ne 0) {
        throw "Bundle encoding check failed - refusing to package garbled output (issue #26)"
    }
}

if (-not $SkipZip) {
    Write-Host "[2/3] Packing portable zip..." -ForegroundColor Cyan
    $zip = Join-Path $root "dist-onedir\$name-portable.zip"
    Remove-Item $zip -Force -ErrorAction SilentlyContinue
    Compress-Archive -Path "$appDir\*" -DestinationPath $zip -CompressionLevel Optimal
    Write-Host "      $zip ($([math]::Round((Get-Item $zip).Length/1MB,1)) MB)" -ForegroundColor Green
}

Write-Host "[3/3] Done. onedir dir: $appDir" -ForegroundColor Green
Write-Host "      Installer: compile packaging\dsh-pet-$Variant.iss with ISCC.exe"
