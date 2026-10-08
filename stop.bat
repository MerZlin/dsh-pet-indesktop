@echo off
rem ============================================================
rem  dsh-pet 独立桌宠 —— 停止脚本
rem
rem  用法：双击本文件即可停止「本仓库源码启动」的桌宠。
rem  可选参数：
rem    --list / -list        只列出将要停止的进程，不停止
rem    --all  / -all         停止全机器所有桌宠（含其它 checkout 与打包版）
rem    --timeout 20          等待进程退出的秒数（默认 10）
rem    --help / -h           打印用法
rem
rem  实现见 scripts\stop_pet.ps1：与产品自身「退出子肥鱼」同款
rem  taskkill /PID x /T /F（连 ffmpeg 解码子进程一起带走），
rem  并在杀之前校验 PID 未被复用、命令行未变化。
rem ============================================================

chcp 65001 >nul
setlocal enabledelayedexpansion

set "ROOT=%~dp0"
if "%ROOT:~-1%"=="\" set "ROOT=%ROOT:~0,-1%"

rem ---- 是否由资源管理器双击启动（决定结束时要不要 pause）----
rem 双击时 cmdcmdline 形如：cmd /c ""D:\...\stop.bat" "（含「引号 空格 引号」尾巴）；
rem 从终端调用没有这个尾巴，所以不会误 pause 卡住调用方。
set "INTERACTIVE="
echo %cmdcmdline% | findstr /c:"\" \"" >nul && set "INTERACTIVE=1"

rem ---- 参数归一化：把 --list / -list / /list 统一成 PowerShell 的 -List ----
set "ARGS="
if not "%~1"=="" (
  for %%A in (%*) do call :norm "%%~A"
)

powershell -NoProfile -ExecutionPolicy Bypass -File "%ROOT%\scripts\stop_pet.ps1" !ARGS!
set "RC=%ERRORLEVEL%"

echo.
if defined INTERACTIVE pause
endlocal & exit /b %RC%

rem ============================================================
:norm
set "A=%~1"
set "A=%A:/=-%"
if "%A:~0,1%"=="-" set "A=%A:~1%"
if "%A:~0,1%"=="-" set "A=%A:~1%"
if /i "%A%"=="list"    (set "ARGS=!ARGS! -List"       & exit /b 0)
if /i "%A%"=="all"     (set "ARGS=!ARGS! -All"        & exit /b 0)
if /i "%A%"=="help"    (set "ARGS=!ARGS! -Help"       & exit /b 0)
if /i "%A%"=="h"       (set "ARGS=!ARGS! -Help"       & exit /b 0)
if /i "%A%"=="timeout" (set "ARGS=!ARGS! -TimeoutSec" & exit /b 0)
set "ARGS=!ARGS! %~1"
exit /b 0
