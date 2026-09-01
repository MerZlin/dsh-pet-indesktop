# -*- coding: utf-8 -*-
"""
macOS 原生窗口辅助 —— 从 pet/window.py 剥离（结构优化批 6-3）。

纯搬移：逐行搬移不改逻辑；objc runtime 调用的 ctypes 声明（restype/argtypes）
与搬移前逐字符一致，任何改动都可能造成 ObjC runtime 段错误。
"""

from __future__ import annotations

import sys

from PySide6.QtCore import Qt


def _keep_macos_tool_window_visible(window) -> None:
    """Tool windows must remain visible while another application is active.

    This is independent from the configurable z-order. Without the attribute,
    Cocoa automatically hides a Qt.Tool window when the accessory application
    resigns active, which looked like the WebM Chat pet had exited.
    """
    if sys.platform == 'darwin':
        window.setAttribute(Qt.WidgetAttribute.WA_MacAlwaysShowToolWindow, True)


def _mac_set_window_level(view_id: int, level: int) -> bool:
    """macOS 原生：把 NSWindow 层级设为指定值（3=置顶浮动，0=普通）。

    Qt 的 WindowStaysOnTopHint 在 macOS 上对无边框 Tool 窗口/运行时切换不可靠，
    这里用 objc runtime 直接调 [NSWindow setLevel:] 强制生效（ctypes 零依赖）。

    只在真实 cocoa 平台执行：offscreen/minimal 等测试平台下 winId() 不是
    NSView 指针，objc_msgSend 会直接 SIGSEGV（无法被 try/except 捕获）。
    """
    if sys.platform != 'darwin':
        return False
    try:
        from PySide6.QtGui import QGuiApplication
        if QGuiApplication.platformName() != 'cocoa':
            return False
    except Exception:
        return False
    try:
        import ctypes
        import ctypes.util

        lib_path = ctypes.util.find_library('objc') or '/usr/lib/libobjc.A.dylib'
        objc = ctypes.cdll.LoadLibrary(lib_path)

        # 关键：sel_registerName 返回 SEL（64 位指针）。ctypes 默认按 c_int(32 位)
        # 截断返回值，损坏的 SEL 会让 ObjC runtime 段错误（SIGSEGV），必须显式声明
        objc.sel_registerName.restype = ctypes.c_void_p
        objc.sel_registerName.argtypes = [ctypes.c_char_p]

        msg = objc.objc_msgSend
        msg.restype = ctypes.c_void_p

        sel_window = objc.sel_registerName(b'window')
        sel_set_level = objc.sel_registerName(b'setLevel:')
        sel_order_front = objc.sel_registerName(b'orderFrontRegardless')

        # [view window] —— 无参，返回 NSWindow*
        msg.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        window = msg(ctypes.c_void_p(view_id), sel_window)
        if not window:
            return False

        # [window setLevel:level] —— 一个 NSInteger 参数
        msg.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_long]
        msg(ctypes.c_void_p(window), sel_set_level, level)
        if level > 0:
            # Changing WindowStaysOnTopHint recreates the NSWindow. Setting the
            # floating level alone may leave the replacement ordered behind
            # the currently active application until Cocoa's next ordering
            # pass; orderFrontRegardless commits the new level immediately.
            msg.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
            msg(ctypes.c_void_p(window), sel_order_front)
        return True
    except Exception:
        return False
