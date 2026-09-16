# -*- coding: utf-8 -*-
"""issue #98 回归：桌宠不得夺走其他应用的前台与键盘焦点。

现场症状：全局 Ctrl+C/Ctrl+V 失效（右键复制粘贴同样无效），退出桌宠后恢复。
实测根因：桌宠窗口是 WS_EX_TOOLWINDOW 工具窗口，但**没有 WS_EX_NOACTIVATE**，
因此鼠标点击会把它变成前台窗口（`GetForegroundWindow()` 变成桌宠）。此后用户
的键盘输入全部落到桌宠上，而桌宠既不处理 Ctrl+C 也不处理 Ctrl+V，也没有任何
控件持有焦点 —— 观感就是"整机复制粘贴坏了"，点回原窗口或退出桌宠才恢复。

本文件锁定修复契约：
1. 桌宠窗口必须带 WS_EX_NOACTIVATE（Windows）；点击它不改变前台窗口；
2. 同时必须仍能收到鼠标点击（NOACTIVATE 只影响激活，不影响消息投递）。
"""
from __future__ import annotations

import ctypes
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest
from PySide6.QtWidgets import QApplication

from pet.config import Config
from pet.window import PetWindow
from tests.test_window_pause import FakeLibrary

# 本文件必须跑在真实窗口系统上：offscreen 平台根本没有原生 HWND
# （GetWindowLongW 拿不到扩展样式），也没有真实前台窗口可判定。
# headless CI / 本地 offscreen 套件自动跳过；Windows 桌面环境（含 CI runner）
# 会真正执行。
_WINDOWS_REAL_DISPLAY = (
    sys.platform == 'win32'
    and os.environ.get('QT_QPA_PLATFORM', '').lower() != 'offscreen'
)
pytestmark = pytest.mark.skipif(
    not _WINDOWS_REAL_DISPLAY,
    reason='需要真实窗口系统（原生扩展样式 + 真实前台窗口）',
)

WS_EX_NOACTIVATE = 0x08000000
WS_EX_TOOLWINDOW = 0x00000080
GWL_EXSTYLE = -20
INPUT_MOUSE = 0
MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004

# Win32 句柄/结构只在 Windows 上有意义，且 ctypes.windll 在 macOS/Linux 上
# 根本不存在——这些必须**惰性**构造：模块级触碰会让非 Windows 平台的
# collection 直接 AttributeError，skip 标记救不了（CI 实测）。
_u32 = None


def _user32():
    """惰性取得已声明签名的 user32（仅在 _WINDOWS_REAL_DISPLAY 下调用）。"""
    global _u32
    if _u32 is None:
        from ctypes import wintypes as wt

        lib = ctypes.windll.user32
        lib.GetForegroundWindow.restype = wt.HWND
        lib.GetWindowLongW.argtypes = [wt.HWND, ctypes.c_int]
        lib.WindowFromPoint.restype = wt.HWND
        lib.WindowFromPoint.argtypes = [wt.POINT]
        _u32 = lib
    return _u32


def _mouse_input_structs():
    """惰性定义 MOUSEINPUT/INPUT（依赖 wintypes，非 Windows 上不可用）。"""
    from ctypes import wintypes as wt

    class _MouseInput(ctypes.Structure):
        _fields_ = [
            ('dx', wt.LONG), ('dy', wt.LONG), ('mouseData', wt.DWORD),
            ('dwFlags', wt.DWORD), ('time', wt.DWORD),
            ('dwExtraInfo', ctypes.POINTER(ctypes.c_ulong)),
        ]

    class _Input(ctypes.Structure):
        _fields_ = [('type', wt.DWORD), ('mi', _MouseInput)]

    return _MouseInput, _Input


def _hwnd(value) -> int:
    if value is None:
        return 0
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _ex_style(hwnd: int) -> int:
    from ctypes import wintypes as wt

    return int(_user32().GetWindowLongW(wt.HWND(hwnd), GWL_EXSTYLE))


def _pump(app, times: int = 30) -> None:
    for _ in range(times):
        app.processEvents()


def _click_at(x: int, y: int) -> None:
    """真实鼠标点击（SendInput）：Qt 的 activateWindow 对后台进程会被系统拒绝，
    无法复现用户点击，必须用真实输入事件。"""
    mouse_input, input_struct = _mouse_input_structs()
    u32 = _user32()
    u32.SetCursorPos(int(x), int(y))
    time.sleep(0.15)
    for flag in (MOUSEEVENTF_LEFTDOWN, MOUSEEVENTF_LEFTUP):
        message = input_struct(type=INPUT_MOUSE,
                               mi=mouse_input(0, 0, 0, flag, 0, None))
        u32.SendInput(1, ctypes.byref(message), ctypes.sizeof(input_struct))
        time.sleep(0.05)


def _make_pet(tmp_path) -> PetWindow:
    return PetWindow(FakeLibrary(), Config(base=tmp_path))


def test_pet_window_has_noactivate_ex_style(tmp_path):
    """桌宠窗口必须带 WS_EX_NOACTIVATE，否则点击会夺走前台（issue #98）。"""
    app = QApplication.instance() or QApplication([])
    win = _make_pet(tmp_path)
    try:
        win.show()
        _pump(app)
        ex = _ex_style(int(win.winId()))
        assert ex & WS_EX_TOOLWINDOW, '桌宠应仍是工具窗口（不进任务栏/Alt+Tab）'
        assert ex & WS_EX_NOACTIVATE, (
            f'桌宠窗口缺少 WS_EX_NOACTIVATE（ex={ex:#010x}）：点击它会夺走前台窗口，'
            '导致用户的 Ctrl+C/Ctrl+V 落到桌宠上（issue #98）'
        )
    finally:
        win.close()
        win.deleteLater()
        _pump(app)


def test_clicking_pet_does_not_steal_foreground(tmp_path):
    """跨进程实测：点击桌宠后，前台窗口仍属于原来的应用。

    必须跨进程：同进程窗口共享前台队列，`GetForegroundWindow()` 分不清。
    """
    app = QApplication.instance() or QApplication([])
    holder = Path(__file__).resolve().parent / 'helpers' / 'foreground_holder.py'
    proc = subprocess.Popen(
        [sys.executable, str(holder)],
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True,
        cwd=str(holder.parents[2]),
    )
    win = None
    try:
        parts = (proc.stdout.readline() or '').split()
        holder_hwnd = int(parts[0]) if parts else 0
        holder_is_foreground = bool(int(parts[1])) if len(parts) > 1 else False
        assert holder_hwnd, '无法启动前台持有进程'
        if not holder_is_foreground:
            pytest.skip('前台锁拒绝把新窗口推上前台（本机环境），'
                        '无法判定桌宠是否夺前台')

        win = _make_pet(tmp_path)
        # 先给窗口一帧真实角色内容：点击必须落在不透明区域，否则逐像素穿透会
        # 按设计让点击穿透到下层，就测不到"点击是否夺前台"（与捕获兼容用例同法）。
        win.movie = win.lib.movie(win.idle)
        win._rebuild_frame()
        win.show()
        _pump(app, 40)
        pet_hwnd = int(win.winId())

        from ctypes import wintypes as wt

        u32 = _user32()
        rect = wt.RECT()
        u32.GetWindowRect(wt.HWND(pet_hwnd), ctypes.byref(rect))
        bounds = win._mask_bounds
        if bounds is not None and not bounds.isEmpty():
            lx, ly = bounds.center().x(), bounds.center().y()
        else:
            lx, ly = win.width() // 2, win.height() // 2
        cx, cy = rect.left + lx, rect.top + ly

        # 安全哨兵：只有光标下确实是桌宠窗口时才发真实点击，绝不误点别的应用
        under = _hwnd(u32.WindowFromPoint(wt.POINT(cx, cy)))
        if under != pet_hwnd:
            pytest.skip(f'桌宠未处于光标所在位置（under={under:#x}），跳过真实点击')

        _click_at(cx, cy)
        _pump(app, 50)

        foreground = _hwnd(u32.GetForegroundWindow())
        assert foreground != pet_hwnd, (
            '点击桌宠后它成了前台窗口：用户的键盘输入（含 Ctrl+C/Ctrl+V）会落到'
            f'桌宠上而不是原应用（issue #98）。foreground={foreground:#x} pet={pet_hwnd:#x}'
        )
        assert foreground == holder_hwnd, (
            f'点击桌宠后前台窗口既不是桌宠也不是原应用：'
            f'foreground={foreground:#x} holder={holder_hwnd:#x}'
        )
    finally:
        if win is not None:
            win.close()
            win.deleteLater()
            _pump(app)
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
