# -*- coding: utf-8 -*-
"""
开机自启动管理（跨平台）。

- Windows：HKCU Run 注册表键（无需管理员权限）；
- macOS：LaunchAgents plist（~/Library/LaunchAgents/）；
- 其他平台：no-op（返回 False / 不操作）。

设计原则：**系统自启配置是唯一真相**。菜单勾选状态直接查它们，不与 config.json
冗余存储，避免两处状态不同步。

命令按运行形态自适应：
- PyInstaller 打包（sys.frozen）：Windows 自启动先用 `start /D` 切到 exe 所在目录再启动 exe；
  macOS/Linux 指向 .app 内二进制自身；
- 源码运行：Windows 用 `pythonw -m pet`，macOS/Linux 用 `python -m pet`（带工作目录）。
"""

from __future__ import annotations

import sys
from pathlib import Path

from .config import APP_DIR_NAME

# macOS LaunchAgent 按变体隔离（plist 文件名/Label），避免多版本互覆盖
_APP_BASE_ID = "com.merzlin.dsh-pet-standalone"
PLIST_LABEL = (
    _APP_BASE_ID
    if APP_DIR_NAME == "dsh-pet-standalone"
    else f"{_APP_BASE_ID}.{APP_DIR_NAME}"
)
# Windows 自启注册表值名按变体隔离（如 dsh-pet-standalone-webm-chat），
# 使 Chat / 无 Chat 两个版本可同时开机自启且互不覆盖。
VALUE_NAME = APP_DIR_NAME
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"

_IS_WIN = sys.platform == "win32"
_IS_MAC = sys.platform == "darwin"

if _IS_WIN:
    import winreg


def _project_root() -> Path:
    return Path(__file__).resolve().parent.parent


def _plist_path() -> Path:
    return Path.home() / "Library" / "LaunchAgents" / f"{PLIST_LABEL}.plist"


def _pythonw_path() -> str:
    """Windows 源码运行时，取与 python.exe 同目录的 pythonw.exe（无控制台窗口）。"""
    exe = sys.executable
    if _IS_WIN and exe.lower().endswith("python.exe"):
        return exe[: -len("python.exe")] + "pythonw.exe"
    return exe


def _win_command_is_current(command: str) -> bool:
    """判断 Windows 自启命令是否已是“先切工作目录再启动”的新格式。"""
    return "cmd /c start" in command.lower()


def is_enabled() -> bool:
    """当前是否已注册开机自启。"""
    if _IS_WIN:
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
                command, _ = winreg.QueryValueEx(key, VALUE_NAME)
                # 兼容旧版：已开启但仍是旧命令（直接指向 exe，未切工作目录）时，
                # 自动升级为新命令，避免开机自启因 CWD 不可写而解压失败。
                if (
                    getattr(sys, "frozen", False)
                    and isinstance(command, str)
                    and not _win_command_is_current(command)
                ):
                    try:
                        with winreg.OpenKey(
                            winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE
                        ) as write_key:
                            winreg.SetValueEx(
                                write_key, VALUE_NAME, 0, winreg.REG_SZ, _win_command()
                            )
                    except OSError:
                        # 只读场景（如权限异常）不强求升级，仍视为已启用
                        pass
                return True
        except FileNotFoundError:
            return False
    if _IS_MAC:
        return _plist_path().exists()
    return False


def _win_command() -> str:
    if getattr(sys, "frozen", False):
        # onefile 的 runtime_tmpdir="." 是相对“当前工作目录”解析的；
        # 开机自启（HKCU Run）默认工作目录可能是 System32 等不可写目录。
        # 用 start 先切到 exe 所在目录再启动 exe，既保证解压目录在 exe 同目录，
        # 又不会让 cmd 窗口一直等待桌宠退出。
        exe = Path(sys.executable).resolve()
        return f'cmd /c start "" /D "{exe.parent}" "{exe}"'
    return f'cmd /c start "" /D "{_project_root()}" "{_pythonw_path()}" -m pet'


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
            "Label": PLIST_LABEL,
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
