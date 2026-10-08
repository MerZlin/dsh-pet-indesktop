@echo off
rem ============================================================
rem  dsh-pet 独立桌宠 —— 一键启动
rem
rem  用法：双击本文件即可。
rem  可选参数：
rem    --console / -c   前台控制台模式启动（排查报错用，退出后窗口保留）
rem    --check          只检查环境并打印启动命令，不真正启动
rem  可选环境变量：
rem    PET_PYTHON       没有 .venv 时，用它指定的 python.exe 去创建
rem
rem  行为：优先使用仓库内 .venv；缺失则自动创建并安装 requirements.txt；
rem        依赖缺失时自动补装；最后用 pythonw 无控制台窗口启动桌宠。
rem ============================================================

chcp 65001 >nul
setlocal enabledelayedexpansion

rem ---- 项目根目录（去掉结尾反斜杠，避免 "路径\" 的引号转义坑）----
set "ROOT=%~dp0"
if "%ROOT:~-1%"=="\" set "ROOT=%ROOT:~0,-1%"
cd /d "%ROOT%"

set "MODE=launch"
if /i "%~1"=="--console" set "MODE=console"
if /i "%~1"=="-c"        set "MODE=console"
if /i "%~1"=="--check"   set "MODE=check"

rem ---- 是否由资源管理器双击启动（决定出错时要不要 pause）----
rem 双击时 cmdcmdline 形如：cmd /c ""D:\...\run.bat" "（含「引号 空格 引号」尾巴）；
rem 从终端调用没有这个尾巴，所以不会误 pause 卡住调用方。
set "INTERACTIVE="
echo %cmdcmdline% | findstr /c:"\" \"" >nul && set "INTERACTIVE=1"

echo ============================================================
echo   dsh-pet 独立桌宠 · 一键启动          模式: %MODE%
echo ============================================================
echo   项目目录: %ROOT%
echo.

rem ---------- 1) 选择解释器 ----------
set "PY="
set "PYW="
if exist "%ROOT%\.venv\Scripts\python.exe"  set "PY=%ROOT%\.venv\Scripts\python.exe"
if exist "%ROOT%\.venv\Scripts\pythonw.exe" set "PYW=%ROOT%\.venv\Scripts\pythonw.exe"
if defined PY (
  echo [1/3] 解释器: 仓库内虚拟环境 .venv
) else (
  echo [1/3] 未找到 .venv，开始创建虚拟环境...
  call :make_venv
  if errorlevel 1 goto :fail
)

rem ---------- 2) 运行依赖 ----------
echo [2/3] 检查运行依赖 PySide6 ...
"%PY%" -c "import PySide6" >nul 2>&1
if errorlevel 1 (
  echo       缺少依赖，正在安装 requirements.txt ...
  "%PY%" -m pip install --disable-pip-version-check -r "%ROOT%\requirements.txt"
  if errorlevel 1 (
    echo [x] 依赖安装失败，请手动执行：
    echo     "%PY%" -m pip install -r "%ROOT%\requirements.txt"
    goto :fail
  )
  "%PY%" -c "import PySide6" >nul 2>&1
  if errorlevel 1 (
    echo [x] 依赖安装后仍无法导入 PySide6。
    goto :fail
  )
)
echo       依赖就绪。

rem ---------- 3) 启动 ----------
if /i "%MODE%"=="check" (
  echo [3/3] --check：跳过启动。实际启动命令为：
  echo        "%PYW%" -m pet
  goto :done
)

if /i "%MODE%"=="console" (
  echo [3/3] 前台控制台模式启动（关掉桌宠即返回本窗口）...
  echo.
  "%PY%" -m pet
  set "RC=!errorlevel!"
  echo.
  echo 桌宠已退出，退出码 !RC!
  goto :done_pause
)

echo [3/3] 正在启动桌宠...
rem 重定向到 nul：不要把本窗口的 stdout/stderr 交给桌宠。
rem 已验证：双击 / 终端 `cmd /c run.bat` 调用下本脚本 0.5 秒内退出，桌宠独立存活。
rem 已知环境行为：若外层工具用管道捕获本脚本输出（如 DSH 的后台作业），该作业会
rem 一直显示 running 直到桌宠退出——那是外层等待管道关闭所致，不是脚本卡住。
rem 排查启动报错请用 --console。
start "dsh-pet" /d "%ROOT%" "%PYW%" -m pet >nul 2>&1

rem 轮询确认桌宠真的起来了（冷启动要加载素材库/ffmpeg，3 秒不够；
rem 判据是本仓库路径 + -m pet，避免把别的 checkout 的桌宠误判成本次启动）
set "PETOK="
for /l %%I in (1,1,8) do (
  if not defined PETOK (
    powershell -NoProfile -Command "if (Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like '*%ROOT%*' -and $_.CommandLine -like '*-m pet*' }) { exit 0 } else { exit 1 }" >nul 2>&1
    if not errorlevel 1 (
      set "PETOK=1"
    ) else (
      ping -n 2 127.0.0.1 >nul
    )
  )
)
if defined PETOK (
  echo       桌宠已启动：看屏幕右下角或系统托盘图标。
) else (
  echo       已发出启动请求，但 8 秒内没有看到桌宠进程。
  echo       请用 --console 参数前台运行，看具体报错。
)
goto :done

rem ============================================================
rem  子过程
rem ============================================================

:make_venv
set "BASE="
if defined PET_PYTHON if exist "%PET_PYTHON%" set "BASE=%PET_PYTHON%"
if not defined BASE call :scan_std_dirs
if not defined BASE call :scan_path
if not defined BASE (
  echo [x] 找不到 Python 3.10 或更高版本。
  echo     请先安装：https://www.python.org/downloads/
  echo     或设置环境变量 PET_PYTHON 指向 python.exe。
  exit /b 1
)
echo       基础解释器: %BASE%
echo %BASE% | findstr /i "msys mingw cygwin" >nul
if not errorlevel 1 echo       [warn] 该解释器可能没有 PySide6 预编译包；若安装失败请改用官方 CPython 3.12。
"%BASE%" -m venv "%ROOT%\.venv"
if errorlevel 1 (
  echo [x] 创建虚拟环境失败。
  exit /b 1
)
set "PY=%ROOT%\.venv\Scripts\python.exe"
set "PYW=%ROOT%\.venv\Scripts\pythonw.exe"
exit /b 0

:scan_std_dirs
rem 优先官方安装目录（本机 CPython 3.12 就在这里），避免误选 msys/cygwin 解释器
for /d %%D in ("%LOCALAPPDATA%\Programs\Python\Python3*") do (
  if not defined BASE if exist "%%~fD\python.exe" call :probe "%%~fD\python.exe"
)
for /d %%D in ("%ProgramFiles%\Python3*") do (
  if not defined BASE if exist "%%~fD\python.exe" call :probe "%%~fD\python.exe"
)
exit /b 0

:scan_path
for %%C in (py.exe python3.exe python.exe) do (
  if not defined BASE for /f "delims=" %%P in ('where %%C 2^>nul') do (
    if not defined BASE call :probe "%%P"
  )
)
exit /b 0

:probe
rem 只接受 3.10+
"%~1" -c "import sys;raise SystemExit(0 if sys.version_info>=(3,10) else 1)" >nul 2>&1
if not errorlevel 1 set "BASE=%~1"
exit /b 0

:done
echo.
echo 完成。
if defined INTERACTIVE if /i not "%MODE%"=="check" pause
endlocal
exit /b 0

:done_pause
if defined INTERACTIVE pause
endlocal
exit /b 0

:fail
echo.
echo 启动失败。请把上面的报错发给开发者。
if defined INTERACTIVE pause
endlocal
exit /b 1
