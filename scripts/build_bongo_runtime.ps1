# -*- coding: utf-8 -*-
<#
.SYNOPSIS
    构建「键鼠跟随」模式复用的 BongoCat 运行时，并落到 external\bongocat\。

.DESCRIPTION
    新模式只做模式切换，运行时本体来自我们 fork 的 BongoCat
    （MerZlin/BongoCat 的 dsh-pet 分支，改动清单见 docs/KEY-MOUSE-MODE-*.md）。
    本脚本负责把那个仓库编译成可随包分发的目录：
      <OutputDir>\BongoCat.exe
      <OutputDir>\assets\models\**            （预置 Live2D 模型）
    并在最后跑一次素材自检（scripts\verify_bongo_assets.py）。

    依赖：Node + pnpm（含 tauri CLI）+ Rust 工具链（rustup/cargo）。CI 的
    windows-latest 自带 Rust；本机没有工具链时可用 -StagedDir 直接指定
    官方版/已构建产物做打包联调（不要提交进仓库，external/ 已被忽略）。

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File scripts\build_bongo_runtime.ps1 `
        -SourceRepo C:\src\BongoCat
    powershell -ExecutionPolicy Bypass -File scripts\build_bongo_runtime.ps1 `
        -StagedDir "$env:LOCALAPPDATA\Programs\BongoCat"
#>
param(
    [string]$SourceRepo = '',
    [string]$OutputDir = 'external\bongocat',
    [string]$StagedDir = '',
    [string]$RustTarget = 'x86_64-pc-windows-msvc',
    [switch]$SkipInstall,
    [switch]$SkipVerify
)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
$env:PYTHONUTF8 = '1'
$env:PYTHONIOENCODING = 'utf-8'

function Copy-RuntimePayload {
    param([string]$From, [string]$To)
    if (-not (Test-Path (Join-Path $From 'BongoCat.exe'))) {
        throw "运行时缺 BongoCat.exe: $From"
    }
    if (-not (Test-Path (Join-Path $From 'assets\models\standard\cat.model3.json'))) {
        throw "运行时缺 assets\models\standard（Live2D 预置模型）: $From"
    }
    if (Test-Path $To) { Remove-Item -LiteralPath $To -Recurse -Force }
    New-Item -ItemType Directory -Force -Path $To | Out-Null
    Copy-Item -Path (Join-Path $From '*') -Destination $To -Recurse -Force
    Write-Host "[bongo] 运行时已就绪: $To" -ForegroundColor Green
}

$output = Join-Path $root $OutputDir

if ($StagedDir) {
    Write-Host "[bongo] 直接采用现成运行时: $StagedDir" -ForegroundColor Cyan
    Copy-RuntimePayload -From (Resolve-Path $StagedDir).Path -To $output
} else {
    if (-not $SourceRepo) { throw "需要 -SourceRepo <BongoCat 仓库目录> 或 -StagedDir <已构建运行时目录>" }
    $repo = (Resolve-Path $SourceRepo).Path
    if (-not (Test-Path (Join-Path $repo 'src-tauri\tauri.conf.json'))) {
        throw "不是 BongoCat 仓库（缺 src-tauri\tauri.conf.json）: $repo"
    }
    Push-Location $repo
    try {
        if (-not $SkipInstall) {
            Write-Host "[bongo] pnpm install（含 tauri CLI）..." -ForegroundColor Cyan
            pnpm install --frozen-lockfile
            if ($LASTEXITCODE -ne 0) { throw "pnpm install 失败" }
        }
        Write-Host "[bongo] pnpm tauri build --no-bundle（目标 $RustTarget）..." -ForegroundColor Cyan
        pnpm tauri build --no-bundle --target $RustTarget
        if ($LASTEXITCODE -ne 0) { throw "tauri build 失败" }
    } finally {
        Pop-Location
    }
    # Tauri 构建产物布局：target\<triple>\release\ 下为 exe，
    # bundle 资源按 tauri.conf.json 的 resources 相对路径一并复制到该目录。
    $release = Join-Path $repo "src-tauri\target\$RustTarget\release"
    if (-not (Test-Path $release)) { $release = Join-Path $repo 'src-tauri\target\release' }
    Copy-RuntimePayload -From $release -To $output
}

if (-not $SkipVerify) {
    Write-Host "[bongo] 素材自检..." -ForegroundColor Cyan
    python scripts\verify_bongo_assets.py --runtime $output --strict
    if ($LASTEXITCODE -ne 0) { throw "键鼠跟随素材自检失败" }
}

Write-Host "[bongo] 完成。打包时 scripts\build_onedir.ps1 会自动带上 $OutputDir" -ForegroundColor Green
