# -*- coding: utf-8 -*-
"""
macOS 原生窗口辅助 —— 从 pet/window.py 剥离（结构优化批 6-3）。

objc runtime 调用必须按原生签名声明 ctypes 的 restype/argtypes，
指针截断或整数宽度错误会造成进程崩溃。
"""

from __future__ import annotations

import logging
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


def _mac_no_window_shadow_flag() -> Qt.WindowType:
    """macOS：关掉窗口系统阴影的窗口标志（非 darwin 返回空标志，可直接 ``|=``）。

    issue #229：macOS 桌宠窗口外圈有一圈系统阴影黑边，普通模式与直播捕获模式
    都有。Qt 的 cocoa 插件把这个标志直接翻成 ``[NSWindow setHasShadow:NO]``
    （qtbase 6.8 ``QCocoaWindow::setWindowFlags``，qcocoawindow.mm:726；窗口创建
    路径的 ``hasShadow`` 也由同一份 flags 决定），所以这里不需要 objc ctypes：
    标志跟着窗口 flags 走，Qt 每次重建原生窗口都会按它重新下发。

    注入点必须在 **flags 构造处**（``OverlayWindow.__init__``、PetWindow 的
    ``build_window_flags``）：可见窗口上 ``setWindowFlag`` 会重建原生窗口（频闪，
    ``window_screen.set_stream_capture_mode`` 就是被迫走这条路的先例），而构造处
    天然覆盖「捕获模式开关重算 flags」这条路径，无需每次显示补写。

    非 darwin 返回 ``Qt.WindowType(0)``：X11 与 Windows 的插件都不消费该标志
    （那里没有系统阴影可关），空标志让调用点写成 ``flags |= ...`` 而不必判平台。
    """
    if sys.platform == 'darwin':
        return Qt.WindowType.NoDropShadowWindowHint
    return Qt.WindowType(0)


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


def _mac_set_hides_on_deactivate(view_id: int) -> bool:
    """macOS 原生：把 NSWindow 的 ``hidesOnDeactivate`` 置 NO。

    与 :func:`_keep_macos_tool_window_visible` 的 Qt 属性**并存**，不是替代：
    Cocoa 在应用失活时 order out 窗口的真正判据就是这个原生属性，而 Qt 属性
    ``WA_MacAlwaysShowToolWindow`` 在部分系统版本上被反馈为不再生效
    （issue #262：macOS 26.2 上 ``[NSWindow hidesOnDeactivate]`` 仍为 YES）。
    直接写原生属性与 Qt 版本无关，两条路只要有一条生效即可。

    **必须在原生窗口就绪后调用**（``showEvent`` 里 ``winId()`` 才指向 NSView；
    构造期调用拿到的是未就绪句柄）。返回值仅表示"是否下发成功"，失败不抛。

    只在真实 cocoa 平台执行：offscreen/minimal 下 ``winId()`` 不是 NSView 指针，
    objc_msgSend 会直接 SIGSEGV（无法被 try/except 捕获）——与
    :func:`_mac_set_window_level` 同一口径，故本函数在测试环境下恒返回 False。
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

        # SEL 是 64 位指针：ctypes 默认按 c_int 截断返回值会得到损坏的 SEL，
        # ObjC runtime 随即 SIGSEGV（同 _mac_set_window_level 的既有结论）。
        objc.sel_registerName.restype = ctypes.c_void_p
        objc.sel_registerName.argtypes = [ctypes.c_char_p]

        msg = objc.objc_msgSend
        msg.restype = ctypes.c_void_p

        sel_window = objc.sel_registerName(b'window')
        sel_set_hides = objc.sel_registerName(b'setHidesOnDeactivate:')

        # [view window] —— 无参，返回 NSWindow*
        msg.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        window = msg(ctypes.c_void_p(view_id), sel_window)
        if not window:
            return False

        # [window setHidesOnDeactivate:NO] —— ObjC BOOL 是 1 字节，
        # 必须 c_bool；借用 NSInteger 的 c_long 会按 8 字节读栈，实测会
        # 写坏相邻 ivar 或直接段错误。
        msg.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_bool]
        msg(ctypes.c_void_p(window), sel_set_hides, False)
        return True
    except Exception:
        return False


def _mac_set_window_all_spaces(view_id: int) -> bool:
    """让合成窗加入所有 Spaces；每次显示时重放，以覆盖原生窗口重建。"""
    if sys.platform != 'darwin':
        return False
    from PySide6.QtGui import QGuiApplication
    if QGuiApplication.platformName() != 'cocoa':
        return False  # offscreen 的 winId 不是 NSView 指针
    try:
        import ctypes
        import ctypes.util

        objc = ctypes.cdll.LoadLibrary(
            ctypes.util.find_library('objc') or '/usr/lib/libobjc.A.dylib')
        objc.sel_registerName.restype = ctypes.c_void_p
        objc.sel_registerName.argtypes = [ctypes.c_char_p]
        msg = objc.objc_msgSend
        msg.restype = ctypes.c_void_p
        msg.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        window = msg(ctypes.c_void_p(view_id), objc.sel_registerName(b'window'))
        if not window:
            logging.warning('macOS: 合成窗尚无 NSWindow，无法设置 Spaces 策略')
            return False

        msg.restype = ctypes.c_ulong  # NSWindowCollectionBehavior 是 NSUInteger
        behavior = msg(ctypes.c_void_p(window),
                       objc.sel_registerName(b'collectionBehavior'))
        # Qt.Tool 默认 MoveToActiveSpace；它与 CanJoinAllSpaces 互斥。
        behavior = (behavior & ~2) | 1
        msg.restype = None
        msg.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_ulong]
        msg(ctypes.c_void_p(window),
            objc.sel_registerName(b'setCollectionBehavior:'), behavior)
        return True
    except Exception:
        logging.warning('macOS: 设置合成窗 Spaces 策略失败', exc_info=True)
        return False


def _mac_set_dock_icon_visible(visible: bool) -> None:
    """Switch the macOS application policy without restarting the pet.

    The speech bubble itself owns the non-activating window flags; application
    activation policy must not be used as a focus workaround because Accessory
    Regular (0) displays a Dock item; Accessory (1) keeps the application out
    of the Dock. Pet tool windows own their independent visibility/focus flags.
    """
    if sys.platform != 'darwin':
        return
    try:
        import ctypes
        import ctypes.util

        objc = ctypes.cdll.LoadLibrary(ctypes.util.find_library('objc') or '/usr/lib/libobjc.A.dylib')
        objc.sel_registerName.restype = ctypes.c_void_p
        objc.objc_getClass.restype = ctypes.c_void_p
        msg = objc.objc_msgSend
        msg.restype = ctypes.c_void_p
        msg.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        shared = msg(
            objc.objc_getClass(b'NSApplication'),
            objc.sel_registerName(b'sharedApplication'),
        )
        # NSApplicationActivationPolicyRegular = 0; Accessory = 1
        msg.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_long]
        msg(shared, objc.sel_registerName(b'setActivationPolicy:'), 0 if visible else 1)
    except Exception:
        pass
