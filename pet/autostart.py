# -*- coding: utf-8 -*-
"""
开机自启动管理（跨平台）。

- Windows：HKCU Run 注册表键（无需管理员权限）；
- macOS：LaunchAgents plist（~/Library/LaunchAgents/）；
- 其他平台：no-op（返回 False / 不操作）。

设计原则：**系统自启配置是唯一真相**。菜单勾选状态直接查它们，不与 config.json
冗余存储，避免两处状态不同步。

命令按运行形态自适应：
- PyInstaller 打包（sys.frozen）：自启动指向 exe / .app 内二进制自身；
- 源码运行：Windows 用 `pythonw -m pet`，macOS/Linux 用 `python -m pet`（带工作目录）。
"""

from __future__ import annotations

import sys
from pathlib import Path

APP_ID = "com.merzlin.dsh-pet-standalone"
VALUE_NAME = "dsh-pet-standalone"
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"

_IS_WIN = sys.platform == "win32"
_IS_MAC = sys.platform == "darwin"

if _IS_WIN:
    import winreg


def _project_root() -> Path:
    return Path(__file__).resolve().parent.parent


def _plist_path() -> Path:
    return Path.home() / "Library" / "LaunchAgents" / f"{APP_ID}.plist"


def _pythonw_path() -> str:
    """Windows 源码运行时，取与 python.exe 同目录的 pythonw.exe（无控制台窗口）。"""
    exe = sys.executable
    if _IS_WIN and exe.lower().endswith("python.exe"):
        return exe[: -len("python.exe")] + "pythonw.exe"
    return exe


def is_enabled() -> bool:
    """当前是否已注册开机自启。"""
    if _IS_WIN:
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
                winreg.QueryValueEx(key, VALUE_NAME)
                return True
        except FileNotFoundError:
            return False
    if _IS_MAC:
        return _plist_path().exists()
    return False


def _win_command() -> str:
    if getattr(sys, "frozen", False):
        return f'"{sys.executable}"'
    return f'cmd /c cd /d "{_project_root()}" && "{_pythonw_path()}" -m pet'


def _mac_program_args() -> list[str]:
    if getattr(sys, "frozen", False):
        # .app 内二进制路径，直接作为 LaunchAgent 程序运行
        return [str(sys.executable)]
    return [sys.executable, "-m", "pet"]


def enable() -> None:
    if _IS_WIN:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
            winreg.SetValueEx(key, VALUE_NAME, 0, winreg.REG_SZ, _win_command())
    elif _IS_MAC:
        import plistlib

        _plist_path().parent.mkdir(parents=True, exist_ok=True)
        plist: dict = {
            "Label": APP_ID,
            "ProgramArguments": _mac_program_args(),
            "RunAtLoad": True,
        }
        if not getattr(sys, "frozen", False):
            plist["WorkingDirectory"] = str(_project_root())
        with _plist_path().open("wb") as f:
            plistlib.dump(plist, f)


def disable() -> None:
    if _IS_WIN:
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
                winreg.DeleteValue(key, VALUE_NAME)
        except FileNotFoundError:
            pass
    elif _IS_MAC:
        _plist_path().unlink(missing_ok=True)


def set_enabled(on: bool) -> None:
    enable() if on else disable()
