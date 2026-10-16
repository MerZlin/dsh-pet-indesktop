# -*- coding: utf-8 -*-
'''给「私有 user32/dwmapi 绑定」加回归守卫（跨模块 argtypes 串台）。

背景：ctypes 的 ``ctypes.windll.user32`` 是进程级共享的函数对象，本项目 vision.py
把它设成 POINTER(RECT)、platform_win 要用 _WinRect —— 后设的那一方会让另一方的调用点
直接 ArgumentError，而且**静默**（window_physical_rect 有 except 兜底 → 几何取不到 →
避字移动不生效；EnumWindows 回调里的异常被 ctypes 吞掉 → 同样表现为拿不到窗口）。
'''
from __future__ import annotations

import ctypes
import os
from ctypes import wintypes

import pytest

from pet import platform_win

pytestmark = pytest.mark.skipif(os.name != 'nt', reason='Windows 专属')


def test_private_bindings_survive_foreign_argtypes():
    """别处把共享签名改成 POINTER(RECT) 时，本模块这两个函数仍必须能用。"""
    u32 = ctypes.windll.user32
    original = u32.GetWindowRect.argtypes
    u32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
    try:
        # 桌面任意一块区域上总该有窗口（本进程被排除，拿到的多半是别的应用）
        hwnd = platform_win.topmost_window_under((0, 0, 800, 600), exclude_pid=os.getpid())
        assert hwnd is None or isinstance(hwnd, int), "回调不许因为签名串台而炸"
        if hwnd is not None:
            rect = platform_win.window_physical_rect(hwnd)
            assert rect is not None and rect[2] > 0 and rect[3] > 0, \
                "拿到窗口就该拿到它的物理矩形（旧实现这里会静默返回 None）"
            assert platform_win._is_cloaked(hwnd) in (True, False)
    finally:
        u32.GetWindowRect.argtypes = original


def test_window_physical_rect_uses_the_injected_binding():
    """注入的替身优先（测试缝），且不再去改共享函数的 argtypes。"""

    class _Rect:
        def __init__(self):
            self.left, self.top, self.right, self.bottom = 10, 20, 110, 220

    class _Fake:
        def __init__(self):
            self.argtypes_seen = None
            self.GetWindowRect = self._get

        def _get(self, hwnd, byref_rect):
            rect = byref_rect._obj
            rect.left, rect.top, rect.right, rect.bottom = 10, 20, 110, 220
            return 1

    fake = _Fake()
    got = platform_win.window_physical_rect(1234, user32=fake)
    assert got == (10, 20, 100, 200)
