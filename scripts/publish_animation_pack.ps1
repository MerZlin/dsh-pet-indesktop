<#
.SYNOPSIS
把素材包索引与 README 推到 Animations 仓库，并把 ZIP 作为 Release 资产上传。

.DESCRIPTION
素材包 ZIP 体积大，不进 git（仓库里只有索引与说明）；真正的下载走 Release 资产，
应用读的 `packs-index.json` 里的 urls 就指向它。

需要 `gh`（GitHub CLI）已安装并登录：`gh auth login --web`。

用法：
  pwsh -NoProfile -ExecutionPolicy Bypass -File scripts/publish_animation_pack.ps1
  pwsh -NoProfile -ExecutionPolicy Bypass -File scripts/publish_animation_pack.ps1 -Tag packs-v1 -WorkDir .scratch/animations-repo
#>
param(
    [string]$Repo = "git@github.com:MerZlin/Animations.git",
    [string]$RepoSlug = "MerZlin/Animations",
    [string]$WorkDir = ".scratch/animations-repo",
    [string]$Tag = "packs-v1",
    [string]$Title = "素材包 v1",
    [string]$Zip = "",
    [switch]$SkipRelease
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$work = if ([System.IO.Path]::IsPathRooted($WorkDir)) { $WorkDir } else { Join-Path $root $WorkDir }

function Fail([string]$message) {
    Write-Host "错误：$message" -ForegroundColor Red
    exit 1
}

if (-not (Get-Command gh -ErrorAction SilentlyContinue)) {
    Fail "没找到 gh（GitHub CLI）。先安装：winget install --id GitHub.cli"
}
& gh auth status *> $null
if ($LASTEXITCODE -ne 0) {
    Fail "gh 未登录。先运行：gh auth login --web"
}

$indexPath = Join-Path $work "packs-index.json"
if (-not (Test-Path $indexPath)) {
    Fail "缺少索引 $indexPath（先跑 scripts/make_animation_pack.py）"
}

if (-not (Test-Path (Join-Path $work ".git"))) {
    Write-Host "克隆 $Repo → $work"
    & git clone $Repo $work
    if ($LASTEXITCODE -ne 0) {
        # 空仓库 clone 会成功但没有任何分支；真的失败才退到 init。
        New-Item -ItemType Directory -Force -Path $work | Out-Null
        Push-Location $work
        & git init
        & git remote add origin $Repo
        Pop-Location
    }
}

Copy-Item (Join-Path $root "packaging/animations-repo/README.md") (Join-Path $work "README.md") -Force
$ignore = Join-Path $work ".gitignore"
if (-not (Test-Path $ignore)) {
    Set-Content -Path $ignore -Value "*.zip`n*.zip.part`n" -Encoding utf8
}

Push-Location $work
try {
    & git add -A
    & git -c user.name="MerZlin" -c user.email="1409828883@qq.com" commit -m "chore(packs): 更新素材包索引" 2>&1 | Out-Host
    & git branch -M main
    & git push -u origin main
    if ($LASTEXITCODE -ne 0) { Fail "推送失败（检查 SSH 权限）" }

    if (-not $SkipRelease) {
        $zipPath = if ($Zip) { $Zip } else { Get-ChildItem -Path $work -Filter "*.zip" | Select-Object -First 1 -ExpandProperty FullName }
        if (-not $zipPath -or -not (Test-Path $zipPath)) {
            Fail "没有可上传的 ZIP（先跑 scripts/make_animation_pack.py）"
        }
        Write-Host "上传 Release 资产：$zipPath"
        & gh release view $Tag --repo $RepoSlug *> $null
        if ($LASTEXITCODE -eq 0) {
            & gh release upload $Tag $zipPath --repo $RepoSlug --clobber
        }
        else {
            & gh release create $Tag $zipPath --repo $RepoSlug --title $Title --notes "dsh-pet-core 角色素材包（见 packs-index.json）"
        }
        if ($LASTEXITCODE -ne 0) { Fail "Release 上传失败" }
        & gh release view $Tag --repo $RepoSlug
    }
}
finally {
    Pop-Location
}

Write-Host "完成。" -ForegroundColor Green
