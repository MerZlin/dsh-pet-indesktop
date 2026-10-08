<#
.SYNOPSIS
    停止 dsh-pet 独立桌宠进程（源码运行 / 打包版）。

.DESCRIPTION
    默认范围：只停「本仓库源码启动」的桌宠——
      cmdline 匹配 `-m pet`，且自身命令行含本仓库路径，
      或父链（≤6 层）里能找到本仓库解释器（venv 重定向器 → 基础解释器
      子进程的形态：子进程 cmdline 只有基础 python 路径，必须靠父链认领）。
    -All：不限仓库，停掉全机器所有 `-m pet` 与 `dsh-pet-standalone*` 进程。

    停止方式与产品自身的「退出子肥鱼」一致（pet/child_pet_cleanup.py:67）：
      taskkill /PID <pid> /T /F
    /T 同时终止 ffmpeg 解码子进程；/F 是必需的——产品入口设了
    `setQuitOnLastWindowClosed(False)`（pet/app.py:3193），关窗不清进程，
    因此不存在可从外部触发的「优雅退出」通道。

    杀之前逐个复核 CreationDate 与命令行（PID 复用防线，对齐
    child_pet_cleanup 的实机事故教训：复用 PID 直接 taskkill 会误杀无辜进程）。

    配置 / 会话 / 待办数据全部保留在数据目录，本脚本只停进程、不删数据。

.PARAMETER List
    只列出将要停止的进程，不执行停止。

.PARAMETER All
    不按仓库过滤，停止全机器所有桌宠（含打包版 dsh-pet-standalone*.exe）。

.PARAMETER TimeoutSec
    等待进程退出的秒数（默认 10）。超时后对残留进程强制 Stop-Process。

.PARAMETER Help
    打印用法。

.EXAMPLE
    powershell -NoProfile -ExecutionPolicy Bypass -File scripts\stop_pet.ps1
.EXAMPLE
    powershell -NoProfile -ExecutionPolicy Bypass -File scripts\stop_pet.ps1 -List -All
#>
[CmdletBinding()]
param(
    [switch]$List,
    [switch]$All,
    [int]$TimeoutSec = 10,
    [switch]$Help
)

$ErrorActionPreference = 'Stop'
try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch { }

$repoRoot = Split-Path -Parent $PSScriptRoot
$petCmdPattern = '(?i)(?:^|\s)-m\s+pet(?:\s|$)'
$excludePattern = '(?i)--uninstall-cleanup'

function Write-Usage {
    Write-Host @'
用法：
  stop.bat                 停止本仓库源码启动的桌宠
  stop.bat --list          只列出将要停止的进程，不停止
  stop.bat --all           停止全机器所有桌宠（含打包版）
  stop.bat --timeout 20    等待退出秒数（默认 10）

也可以直接调用 PowerShell 脚本：
  powershell -NoProfile -ExecutionPolicy Bypass -File scripts\stop_pet.ps1 [-List] [-All] [-TimeoutSec 20]
'@
}

function Get-ProcessTable {
    # python/pythonw 覆盖源码运行；dsh-pet-standalone* 覆盖打包版
    $filter = "Name = 'python.exe' OR Name = 'pythonw.exe' OR Name LIKE 'dsh-pet-standalone%'"
    return @(Get-CimInstance Win32_Process -Filter $filter -ErrorAction SilentlyContinue)
}

function Test-IsPetCommandLine {
    param([string]$CommandLine)
    if ([string]::IsNullOrWhiteSpace($CommandLine)) { return $false }
    if ($CommandLine -match $excludePattern) { return $false }  # --uninstall-cleanup 是短命清理进程
    return ($CommandLine -match $petCmdPattern)
}

function Test-OwnedByRepo {
    param($Proc, $ById)
    $cur = $Proc
    for ($i = 0; $i -lt 6 -and $cur; $i++) {
        if ($cur.CommandLine -and ($cur.CommandLine -like "*$repoRoot*")) { return $true }
        $parentId = [int]$cur.ParentProcessId
        if (-not $ById.ContainsKey($parentId)) { break }
        $cur = $ById[$parentId]
    }
    return $false
}

function Get-ShortCommandLine {
    param([string]$CommandLine, [int]$Max = 96)
    if ([string]::IsNullOrWhiteSpace($CommandLine)) { return '' }
    $c = $CommandLine.Trim()
    if ($c.Length -le $Max) { return $c }
    return $c.Substring(0, $Max - 3) + '...'
}

function Get-PetKind {
    param($Proc)
    if ($Proc.Name -like 'dsh-pet-standalone*') { return '打包版' }
    if ($Proc.CommandLine -match '(?i)--settings') { return '设置页' }
    return '桌宠'
}

if ($Help) { Write-Usage; exit 0 }

Write-Host '============================================================'
Write-Host '  dsh-pet 独立桌宠 · 停止脚本'
Write-Host '============================================================'
Write-Host ("  仓库: {0}" -f $repoRoot)
if ($All) {
    Write-Host '  范围: 全机器（含其它 checkout 与打包版）'
} else {
    Write-Host '  范围: 仅本仓库启动的桌宠（--all 可放开）'
}
Write-Host ''

$table = Get-ProcessTable
$byId = @{}
foreach ($p in $table) { $byId[[int]$p.ProcessId] = $p }

$candidates = @($table | Where-Object { Test-IsPetCommandLine $_.CommandLine })
if (-not $All) {
    $candidates = @($candidates | Where-Object { Test-OwnedByRepo $_ $byId })
} else {
    $packaged = @($table | Where-Object { $_.Name -like 'dsh-pet-standalone*' })
    if ($packaged.Count -gt 0) { $candidates = @($candidates + $packaged) }
}

if ($candidates.Count -eq 0) {
    Write-Host '  没有找到正在运行的桌宠。'
    Write-Host ''
    exit 0
}

Write-Host ("  找到 {0} 个进程：" -f $candidates.Count)
$rows = @()
foreach ($p in ($candidates | Sort-Object ProcessId)) {
    $rows += [pscustomobject]@{
        PID      = [int]$p.ProcessId
        父PID    = [int]$p.ParentProcessId
        类型     = Get-PetKind $p
        启动时间 = ([datetime]$p.CreationDate).ToString('HH:mm:ss')
        命令行   = Get-ShortCommandLine $p.CommandLine
    }
}
$rows | Format-Table -AutoSize | Out-Host

if ($List) {
    Write-Host '  --list：仅列出，未执行停止。'
    Write-Host ''
    exit 0
}

# 只对「根」进程执行 /T：子孙（基础解释器子进程、设置进程、ffmpeg）由进程树带走
$ids = @($candidates | ForEach-Object { [int]$_.ProcessId })
$roots = @($candidates | Where-Object { $ids -notcontains [int]$_.ParentProcessId })

$stopped = New-Object System.Collections.ArrayList
$skipped = New-Object System.Collections.ArrayList
$failed  = New-Object System.Collections.ArrayList

Write-Host '  正在停止...'
foreach ($p in $roots) {
    $targetId = [int]$p.ProcessId
    $fresh = Get-CimInstance Win32_Process -Filter "ProcessId = $targetId" -ErrorAction SilentlyContinue
    if (-not $fresh) {
        [void]$skipped.Add("$targetId 已自行退出")
        continue
    }
    if (([datetime]$fresh.CreationDate) -ne ([datetime]$p.CreationDate)) {
        [void]$skipped.Add("$targetId PID 已被复用，跳过（防误杀）")
        continue
    }
    if ((-not (Test-IsPetCommandLine $fresh.CommandLine)) -and ($fresh.Name -notlike 'dsh-pet-standalone*')) {
        [void]$skipped.Add("$targetId 命令行已变化，跳过（防误杀）")
        continue
    }

    $out = & taskkill /PID $targetId /T /F 2>&1
    $code = $LASTEXITCODE
    if ($code -eq 0) {
        [void]$stopped.Add($targetId)
    } else {
        $text = (($out | Out-String).Trim())
        if ($text -match '没有找到|not found|不存在') {
            [void]$stopped.Add($targetId)   # 已经不在 = 目标达成
        } else {
            [void]$failed.Add("$targetId（taskkill 退出码 ${code}: $text）")
        }
    }
}

# 等待进程树真正退出，残留的再强制收口
$deadline = (Get-Date).AddSeconds([Math]::Max(1, $TimeoutSec))
$alive = @()
do {
    $alive = @()
    foreach ($p in $candidates) {
        if (Get-Process -Id ([int]$p.ProcessId) -ErrorAction SilentlyContinue) {
            $alive += [int]$p.ProcessId
        }
    }
    if ($alive.Count -eq 0) { break }
    Start-Sleep -Milliseconds 250
} while ((Get-Date) -lt $deadline)

if ($alive.Count -gt 0) {
    Write-Host ("  等待超时，强制结束残留进程: {0}" -f ($alive -join ', '))
    foreach ($stuckId in $alive) {
        try {
            Stop-Process -Id $stuckId -Force -ErrorAction Stop
            [void]$stopped.Add($stuckId)
        } catch {
            [void]$failed.Add("$stuckId（Stop-Process 失败: $($_.Exception.Message)）")
        }
    }
    Start-Sleep -Milliseconds 500
}

# 终验：确认目标进程确实都不在了
$stillAlive = @()
$goneIds = @()
foreach ($p in $candidates) {
    $candId = [int]$p.ProcessId
    if (Get-Process -Id $candId -ErrorAction SilentlyContinue) {
        $stillAlive += $candId
    } else {
        $goneIds += $candId
    }
}
$goneIds = @($goneIds | Sort-Object -Unique)
$rootKilled = @($stopped | Sort-Object -Unique)

Write-Host ''
if ($goneIds.Count -gt 0) {
    Write-Host ("  [ok] 已停止 {0} 个进程: {1}" -f $goneIds.Count, ($goneIds -join ', '))
    if ($rootKilled.Count -lt $goneIds.Count) {
        Write-Host ("       （其中 {0} 个是 taskkill /T 随进程树一并带走的子孙，含 ffmpeg 解码进程）" -f ($goneIds.Count - $rootKilled.Count))
    }
} else {
    Write-Host '  [i] 没有进程需要停止。'
}
if ($skipped.Count -gt 0) {
    Write-Host ("  [i] 跳过: {0}" -f ($skipped -join '；'))
}
if ($stillAlive.Count -gt 0) {
    Write-Host ("  [x] 仍在运行: {0}" -f ($stillAlive -join ', '))
    Write-Host '      可尝试：任务管理器结束进程，或以管理员身份重跑本脚本。'
}
if ($failed.Count -gt 0) {
    Write-Host ("  [x] 失败: {0}" -f ($failed -join '；'))
}
Write-Host ''
Write-Host '  桌宠的配置 / 会话 / 待办数据均未改动（仅在数据目录保留运行标记，下次启动会自动清理）。'
Write-Host ''

if ($stillAlive.Count -gt 0 -or $failed.Count -gt 0) { exit 1 }
exit 0
