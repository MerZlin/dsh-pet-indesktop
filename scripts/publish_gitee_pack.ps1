<#
.SYNOPSIS
把素材包分片推到 Gitee 仓库（国内镜像），并打印可直接写进索引的 raw 地址。

.DESCRIPTION
Gitee 免费仓库单文件上限 100 MB，所以 126 MB 的素材包必须切分：
先用 `scripts/make_animation_pack.py --part-bytes 62914560 --parts-base-url ...` 切好，
再跑本脚本把 `packs/` 目录推上去。

令牌只从环境变量 `GITEE_TOKEN` 或 `.scratch/gitee-token.txt` 读，**不写进 git 配置**：
推送时用一次性的 URL 鉴权，推完把 remote 改回不带令牌的形式。

用法：
  pwsh -NoProfile -ExecutionPolicy Bypass -File scripts/publish_gitee_pack.ps1 `
    -Source .scratch/animations-gitee -Repo dsh-pet-animations
#>
param(
    [string]$Source = ".scratch/animations-gitee",
    [string]$Repo = "dsh-pet-animations",
    [string]$WorkDir = ".scratch/gitee-repo",
    [string]$Branch = "main",
    [string]$Description = "dsh-pet-core 角色素材包（分片）"
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot

function Fail([string]$message) {
    Write-Host "错误：$message" -ForegroundColor Red
    exit 1
}

$token = $env:GITEE_TOKEN
if (-not $token) {
    $tokenFile = Join-Path $root ".scratch/gitee-token.txt"
    if (Test-Path $tokenFile) {
        $token = (Get-Content $tokenFile -Raw).Trim()
    }
}
if (-not $token) {
    Fail "没有 Gitee 令牌：设置环境变量 GITEE_TOKEN，或写到 .scratch/gitee-token.txt"
}

$sourceDir = if ([System.IO.Path]::IsPathRooted($Source)) { $Source } else { Join-Path $root $Source }
if (-not (Test-Path $sourceDir)) { Fail "缺少产物目录 $sourceDir" }
$parts = Get-ChildItem -Path $sourceDir -Filter "*.zip.*" | Sort-Object Name
if (-not $parts) { Fail "没有分片文件（先跑 make_animation_pack.py --part-bytes）" }

$api = "https://gitee.com/api/v5"
$headers = @{ "Content-Type" = "application/json" }

# 1) 我是谁
$user = Invoke-RestMethod -Uri "$api/user?access_token=$token" -Headers $headers -Method Get
$owner = $user.login
Write-Host "Gitee 账号：$owner"

# 2) 仓库不存在就建（已存在会返回 400，忽略）
$repoUrl = "https://gitee.com/$owner/$Repo.git"
try {
    Invoke-RestMethod -Uri "$api/user/repos?access_token=$token" -Headers $headers -Method Post -Body (
        @{ name = $Repo; description = $Description; private = $false; auto_init = $true } | ConvertTo-Json
    ) | Out-Null
    Write-Host "已创建仓库 $owner/$Repo"
}
catch {
    Write-Host "仓库已存在或创建失败，继续尝试推送"
}

# 3) 工作区：克隆/初始化，拷入分片与索引
$work = if ([System.IO.Path]::IsPathRooted($WorkDir)) { $WorkDir } else { Join-Path $root $WorkDir }
if (-not (Test-Path (Join-Path $work ".git"))) {
    & git clone "https://gitee.com/$owner/$Repo.git" $work
    if ($LASTEXITCODE -ne 0) {
        New-Item -ItemType Directory -Force -Path $work | Out-Null
        Push-Location $work
        & git init
        & git remote add origin "https://gitee.com/$owner/$Repo.git"
        Pop-Location
    }
}
$packsDir = Join-Path $work "packs"
New-Item -ItemType Directory -Force -Path $packsDir | Out-Null
Copy-Item (Join-Path $sourceDir "packs-index.json") $packsDir -Force
Copy-Item (Join-Path $root "packaging/animations-repo/README.md") (Join-Path $work "README.md") -Force
foreach ($part in $parts) {
    Copy-Item $part.FullName $packsDir -Force
}
$ignore = Join-Path $work ".gitignore"
if (-not (Test-Path $ignore)) {
    Set-Content -Path $ignore -Value "*.zip`n" -Encoding utf8
}

# 4) 推送（令牌只在这一次命令里出现）
$authUrl = "https://oauth2:$token@gitee.com/$owner/$Repo.git"
Push-Location $work
try {
    & git add -A
    & git -c user.name="$owner" -c user.email="$owner@users.noreply.gitee.com" commit -m "chore(packs): 更新素材包分片" 2>&1 | Out-Host
    & git branch -M $Branch
    & git push --force $authUrl "$Branch:$Branch"
    if ($LASTEXITCODE -ne 0) { Fail "推送失败（检查令牌是否有 projects 权限）" }
    & git remote set-url origin "https://gitee.com/$owner/$Repo.git"
}
finally {
    Pop-Location
}

Write-Host ""
Write-Host "分片地址（写进 packs-index.json 的 parts[].urls）：" -ForegroundColor Green
foreach ($part in $parts) {
    Write-Host ("  https://gitee.com/{0}/{1}/raw/{2}/packs/{3}" -f $owner, $Repo, $Branch, $part.Name)
}
Write-Host ""
Write-Host "首页：https://gitee.com/$owner/$Repo"
