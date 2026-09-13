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
    [switch]$SkipBuild,
    [switch]$SkipVerify
)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
$env:PYTHONUTF8 = '1'
$env:PYTHONIOENCODING = 'utf-8'

function Resolve-ReleaseDir {
    # Cargo 工作区在仓库根时产物在 <repo>\target\...，老布局在 src-tauri\target\...
    # （实测上游 master 是前者），两种布局 + 有无 --target 三元组都要能命中。
    param([string]$Repo, [string]$Triple)
    $candidates = @(
        (Join-Path $Repo "target\$Triple\release"),
        (Join-Path $Repo 'target\release'),
        (Join-Path $Repo "src-tauri\target\$Triple\release"),
        (Join-Path $Repo 'src-tauri\target\release')
    )
    foreach ($candidate in $candidates) {
        if (Test-Path (Join-Path $candidate '*.exe')) { return $candidate }
    }
    throw "找不到构建产物目录（尝试过：$($candidates -join '；')）"
}

function Resolve-BuiltExe {
    # cargo 包名是 bongo-cat（产物 bongo-cat.exe），Tauri 打包时才改名成
    # 产品名 BongoCat.exe；--no-bundle 不会改名，所以这里统一收敛成
    # BongoCat.exe（桌宠侧按这个名字查找运行时）。
    param([string]$Dir)
    foreach ($name in @('BongoCat.exe', 'bongo-cat.exe')) {
        $candidate = Join-Path $Dir $name
        if (Test-Path $candidate) { return $candidate }
    }
    $fallback = Get-ChildItem -LiteralPath $Dir -Filter '*.exe' -File |
        Sort-Object Length -Descending | Select-Object -First 1
    if (-not $fallback) { throw "构建产物目录里没有 exe: $Dir" }
    Write-Host "[bongo] 未找到预期 exe 名，改用 $($fallback.Name)" -ForegroundColor Yellow
    return $fallback.FullName
}

function Copy-RuntimePayload {
    param([string]$From, [string]$To, [string]$AssetsSource = '')
    $exe = Resolve-BuiltExe $From
    if (Test-Path $To) { Remove-Item -LiteralPath $To -Recurse -Force }
    New-Item -ItemType Directory -Force -Path $To | Out-Null
    Copy-Item -Path (Join-Path $From '*') -Destination $To -Recurse -Force
    $targetExe = Join-Path $To 'BongoCat.exe'
    if ($exe -ne $targetExe) { Copy-Item -LiteralPath $exe -Destination $targetExe -Force }
    # tauri --no-bundle 不保证把 bundle.resources 复制到 exe 同级目录（实测
    # 上游是「有时在、有时不在」），缺了就从 src-tauri\assets 按相对路径补，
    # 保证 resolveResource('assets/models') 能命中。
    $modelRel = 'assets\models\standard\cat.model3.json'
    if (-not (Test-Path (Join-Path $To $modelRel))) {
        if (-not $AssetsSource -or -not (Test-Path (Join-Path $AssetsSource 'models'))) {
            throw "运行时缺 assets\models（Live2D 预置模型）：$To（来源 $From，assets 源 '$AssetsSource'）"
        }
        Write-Host "[bongo] 产物未带资源，从 $AssetsSource 补齐 assets\" -ForegroundColor Yellow
        Copy-Item -Path (Join-Path $AssetsSource '*') -Destination (Join-Path $To 'assets') -Recurse -Force
    }
    if (-not (Test-Path $targetExe)) { throw "运行时缺 BongoCat.exe: $To" }
    if (-not (Test-Path (Join-Path $To $modelRel))) {
        throw "运行时缺 assets\models\standard（Live2D 预置模型）: $To"
    }
    Write-Host "[bongo] 运行时已就绪: $To" -ForegroundColor Green
}

# OutputDir 允许绝对路径（本地联调/换盘打包）；相对路径按仓库根解析。
$output = if ([System.IO.Path]::IsPathRooted($OutputDir)) { $OutputDir } else { Join-Path $root $OutputDir }

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
        if ($SkipBuild) {
            Write-Host "[bongo] -SkipBuild：复用已有构建产物" -ForegroundColor Yellow
        } else {
            Write-Host "[bongo] pnpm tauri build --no-bundle（目标 $RustTarget）..." -ForegroundColor Cyan
            pnpm tauri build --no-bundle --target $RustTarget
            if ($LASTEXITCODE -ne 0) { throw "tauri build 失败" }
        }
    } finally {
        Pop-Location
    }
    $release = Resolve-ReleaseDir -Repo $repo -Triple $RustTarget
    Copy-RuntimePayload -From $release -To $output -AssetsSource (Join-Path $repo 'src-tauri\assets')
}

if (-not $SkipVerify) {
    Write-Host "[bongo] 素材自检..." -ForegroundColor Cyan
    python scripts\verify_bongo_assets.py --runtime $output --strict
    if ($LASTEXITCODE -ne 0) { throw "键鼠跟随素材自检失败" }
}

Write-Host "[bongo] 完成。打包时 scripts\build_onedir.ps1 会自动带上 $OutputDir" -ForegroundColor Green
