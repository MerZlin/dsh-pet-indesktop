# -*- coding: utf-8 -*-
"""
开机自启动管理 —— 通过 HKCU Run 注册表键（无需管理员权限）。

设计原则：**注册表是唯一真相**。菜单勾选状态直接查注册表，不与 config.json
冗余存储，避免两处状态不同步。

命令按运行形态自适应：
- PyInstaller 打包（sys.frozen）：自启动指向 exe 自身路径；
- 源码运行（python -m pet）：自启动指向 `cmd /c cd /d <项目根> && pythonw -m pet`。
"""

from __future__ import annotations

import os
import sys
import winreg

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
VALUE_NAME = "dsh-pet-standalone"


def is_enabled() -> bool:
    """当前是否已注册开机自启。"""
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            winreg.QueryValueEx(key, VALUE_NAME)
            return True
    except FileNotFoundError:
        return False


def _pythonw_path() -> str:
    """源码运行时，取与 python.exe 同目录的 pythonw.exe（无控制台窗口）。"""
    exe = sys.executable
    if exe.lower().endswith("python.exe"):
        return exe[: -len("python.exe")] + "pythonw.exe"
    return exe


def _command() -> str:
    if getattr(sys, "frozen", False):
        # PyInstaller 打包：直接指向 exe 自身
        return f'"{sys.executable}"'
    # 源码运行：cd 到项目根再 pythonw -m pet（保证 -m pet 能找到包）
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    pyw = _pythonw_path()
    return f'cmd /c cd /d "{project_root}" && "{pyw}" -m pet'


def enable() -> None:
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
        winreg.SetValueEx(key, VALUE_NAME, 0, winreg.REG_SZ, _command())


def disable() -> None:
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
            winreg.DeleteValue(key, VALUE_NAME)
    except FileNotFoundError:
        pass


def set_enabled(on: bool) -> None:
    enable() if on else disable()
