# -*- coding: utf-8 -*-
<#
.SYNOPSIS
    在 BongoCat 上游源码副本上应用「dsh-pet 分支」的最小改动。

.DESCRIPTION
    新模式复用的运行时来自我们 fork 的 BongoCat（MerZlin/BongoCat 的 dsh-pet
    分支）。本脚本把改动打到一份上游克隆上，改动清单保持最小：

      1. src/composables/useAppMenu.ts —— 退出菜单新增「切回原桌宠」
         （行为与「退出」一致：退出进程；桌宠侧监听到子进程结束即恢复原桌宠）；
      2. src-tauri/tauri.conf.json —— identifier 改为 com.merzlin.dsh-pet-bongocat
         （与官方版隔离设置目录与单实例锁）；
      3. src-tauri/tauri.conf.json —— 自动更新端点改为本项目的占位地址，
         避免官方更新把用户拉回官方版并抹掉「切回原桌宠」入口。

    开机自启默认值已是 false（src/stores/general.ts），无需改动。
    脚本幂等：重复执行不会重复插入。用法（用 PowerShell 7 / pwsh 执行）：

      git clone --depth 1 https://github.com/ayangweb/BongoCat.git C:\src\BongoCat
      cd C:\src\BongoCat
      git checkout -b dsh-pet
      pwsh -File <本仓库>\scripts\apply_bongo_fork_changes.ps1 -RepoPath C:\src\BongoCat
      git commit -am "dsh-pet: 切回原桌宠入口 + 隔离标识 + 停用官方更新"
      git remote add fork git@github.com:MerZlin/BongoCat.git
      git push fork dsh-pet
#>
param(
    [Parameter(Mandatory = $true)][string]$RepoPath,
    [string]$UpdaterPlaceholder = 'https://github.com/MerZlin/dsh-pet-indesktop/releases/latest/download/bongocat-latest.json'
)

$ErrorActionPreference = 'Stop'
$repo = (Resolve-Path $RepoPath).Path
$menuFile = Join-Path $repo 'src\composables\useAppMenu.ts'
$confFile = Join-Path $repo 'src-tauri\tauri.conf.json'
foreach ($f in @($menuFile, $confFile)) {
    if (-not (Test-Path $f)) { throw "不是 BongoCat 源码（缺 $f）: $repo" }
}

$utf8 = [System.Text.UTF8Encoding]::new($false)
$changed = @()

function Read-Normalized([string]$path) {
    # 统一按 LF 处理：Windows 上 core.autocrlf=true 的克隆是 CRLF，
    # 直接匹配 LF 锚点会失配（脚本对两种行尾都要能跑）。
    $raw = [System.IO.File]::ReadAllText($path, $utf8)
    return @{ text = $raw.Replace("`r`n", "`n"); crlf = $raw.Contains("`r`n") }
}

function Write-Normalized([string]$path, [string]$text, [bool]$crlf) {
    if ($crlf) { $text = $text.Replace("`n", "`r`n") }
    [System.IO.File]::WriteAllText($path, $text, $utf8)
}

# ---------- 1) 退出菜单新增「切回原桌宠」 ----------
$doc = Read-Normalized $menuFile
$menu = $doc.text
if ($menu -match '切回原桌宠') {
    Write-Host "[fork] 菜单项已存在，跳过" -ForegroundColor Yellow
} else {
    $anchor = "  const getExitMenu = async () => {`n    return await Promise.all([`n      MenuItem.new({`n"
    if (-not $menu.Contains($anchor)) {
        throw "找不到 getExitMenu 锚点（上游可能已改版）：$menuFile"
    }
    $insert = "  const getExitMenu = async () => {`n    return await Promise.all([`n" +
        "      // dsh-pet fork: 桌宠侧监听本进程退出，并在其结束后自动恢复原桌宠，`n" +
        "      // 因此这一项与官方「退出」同义，只是把语义讲清楚（用户是从桌宠的`n" +
        "      // 模式切换进来的）。`n" +
        "      MenuItem.new({`n" +
        "        text: '切回原桌宠',`n" +
        "        action: () => exit(0),`n" +
        "      }),`n" +
        "      MenuItem.new({`n"
    $menu = $menu.Replace($anchor, $insert)
    Write-Normalized $menuFile $menu $doc.crlf
    $changed += 'useAppMenu.ts: 退出菜单新增「切回原桌宠」'
}

# ---------- 2) 隔离应用标识 ----------
$confDoc = Read-Normalized $confFile
$conf = $confDoc.text
if ($conf -match 'com\.merzlin\.dsh-pet-bongocat') {
    Write-Host "[fork] identifier 已隔离，跳过" -ForegroundColor Yellow
} else {
    $before = $conf
    $conf = $conf -replace '"identifier":\s*"[^"]*"', '"identifier": "com.merzlin.dsh-pet-bongocat"'
    if ($conf -eq $before) { throw "未找到 identifier 字段：$confFile" }
    $changed += 'tauri.conf.json: identifier -> com.merzlin.dsh-pet-bongocat'
}

# ---------- 3) 停用官方自动更新端点 ----------
if ($conf -match [regex]::Escape($UpdaterPlaceholder)) {
    Write-Host "[fork] 更新端点已改写，跳过" -ForegroundColor Yellow
} else {
    $pattern = '(?s)("endpoints":\s*\[).*?(\])'
    if ($conf -notmatch $pattern) { throw "未找到 plugins.updater.endpoints：$confFile" }
    $replacement = "`$1`n        `"$UpdaterPlaceholder`"`n      `$2"
    $conf = [regex]::Replace($conf, $pattern, $replacement, 1)
    $changed += 'tauri.conf.json: 自动更新端点改为本项目占位地址（不会安装官方版）'
}
Write-Normalized $confFile $conf $confDoc.crlf

# ---------- 自检：JSON 仍可解析 ----------
$parsed = Get-Content -LiteralPath $confFile -Raw -Encoding UTF8 | ConvertFrom-Json
if ($parsed.identifier -ne 'com.merzlin.dsh-pet-bongocat') {
    throw "identifier 改写后校验失败: $($parsed.identifier)"
}
if ($parsed.plugins.updater.endpoints -notcontains $UpdaterPlaceholder) {
    throw "更新端点改写后校验失败"
}

Write-Host "[fork] 已应用改动：" -ForegroundColor Green
$changed | ForEach-Object { Write-Host "  - $_" }
Write-Host "[fork] 改完请 git diff 复核后提交并推送到 MerZlin/BongoCat 的 dsh-pet 分支" -ForegroundColor Cyan
