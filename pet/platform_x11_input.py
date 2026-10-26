# -*- coding: utf-8 -*-
"""Linux/X11（含 XWayland）逐像素输入区域控制。

问题背景（v4.3.4 实机回归）
--------------------------
v4.3.4 的 Linux 穿透实现见 ``pet/platform_passthrough.py``：
一个 Qt 定时器每 10/50ms 读 ``QCursor.pos()``，据此决定把整窗设成
``Qt::WindowTransparentForInput``（空输入区域）还是恢复输入区域。

这在 **XWayland** 下不成立：XWayland 只在指针位于 XWayland surface 上时
才更新自己的指针位置；当指针位于 GNOME 桌面、Wayland 原生应用等非
XWayland surface 上时，``XQueryPointer``（``QCursor.pos()`` 的底层）会
停在最后一次已知位置。桌宠 overlay 的输入区域平时是空的（点击穿透），
所以指针一离开 XWayland 窗口，轮询读到的位置就是陈旧的：

- 用户把鼠标移到桌宠身上，轮询却仍读到旧坐标（例如屏幕另一角）；
- 判定「光标不在宠物上」→ 输入区域继续保持空；
- 左右键点击全部穿透到下一层窗口，桌宠永远点不到。

也就是说「用轮询决定输入区域」和「输入区域本身会让 XWayland 丢失指针
位置」互相锁死。**输入区域必须由服务端根据桌宠的绘制范围直接判定**，
不能依赖全局光标位置。

修复方式
--------
不再轮询光标，而是把 window 的 **XFixes ShapeInput 输入区域**直接设成
当前所有可见 sprite 的 ``paint_bounds()`` 并集：

- 指针进入宠物包围盒 → X server / Wayland compositor 把输入交给 overlay，
  Qt 正常收到 enter / move / press；
- 指针离开包围盒 → 输入区域不含该点，事件照常交给下层应用；
- 不再读取全局光标，也就不存在 XWayland 位置陈旧的问题。

区域按 ``paint_bounds()``（而不是 ``rect()``）计算，与 ``sprite_at`` 的
粗筛同源：45° 探头/黄金回旋时可见像素会溢出帧矩形，与绘制范围一致才
「画在哪点得到哪」。包围盒内的透明像素仍会被 overlay 接住（点击落到
``sprite_at`` 的 alpha 细判上，未命中时事件被忽略）——这是相对逐像素
输入区域的一处可接受取舍：先保证桌宠能点得到。

多屏：每块屏一张 overlay，各自只计算自己的 ``sprites``；跨屏投影
（``_projections``）不参与输入区域，与旧 ``sprite_at`` 口径一致。

平台边界：仅在 Qt 平台插件为 ``xcb`` 时启用（Linux 默认如此，Wayland
会话下 ``pet/app.py`` 会强制切到 xcb）。原生 Wayland（用户显式设置
``QT_QPA_PLATFORM=wayland``）没有 XFixes，回退旧控制器。
"""

from __future__ import annotations

import ctypes
import ctypes.util
import logging
import sys
import threading

from PySide6.QtCore import QRect, QTimer

# X11 Shape 常量：ShapeInput（XFixes 用同一个 kind 值）
_SHAPE_INPUT = 2


class XRectangle(ctypes.Structure):
    _fields_ = [
        ("x", ctypes.c_short),
        ("y", ctypes.c_short),
        ("width", ctypes.c_ushort),
        ("height", ctypes.c_ushort),
    ]


class XErrorEvent(ctypes.Structure):
    """Xlib ``XErrorEvent``（只取判据要用的字段）。"""

    _fields_ = [
        ("type", ctypes.c_int),
        ("display", ctypes.c_void_p),
        ("resourceid", ctypes.c_ulong),
        ("serial", ctypes.c_ulong),
        ("error_code", ctypes.c_ubyte),
        ("request_code", ctypes.c_ubyte),
        ("minor_code", ctypes.c_ubyte),
    ]


#: X11 协议错误码：BadWindow（窗口在「读 winId → 发请求」之间被销毁时会命中）。
_BAD_WINDOW = 3

#: Xlib 错误处理器：**必须**吞掉 BadWindow，否则默认处理器会直接终止进程。
#:
#: 这是一个真实的时序窗口：``closeEvent`` 前窗口正常销毁，但屏迁移/测试收尾
#: 等路径可能绕过 closeEvent 用 ``deleteLater`` 销毁——我们在主线程读到 winId
#: 后、X server 处理请求前，窗口可能已经没了。Xlib 默认错误处理器打印一条
#: ``BadWindow`` 后 ``exit(1)``，桌宠会当场退出；Qt 自己走 xcb、有独立错误处理，
#: 管不到我们这条 Xlib 连接。所以自己装一个：BadWindow 静默忽略（下一次
#: ``refresh`` 会因为窗口不存在/已重建而自然收敛），其余协议错误记 WARNING
#: 但不终止进程（桌宠没有「宁可崩掉」的语义需求）。
_X_ERROR_HANDLER_TYPE = ctypes.CFUNCTYPE(
    ctypes.c_int, ctypes.c_void_p, ctypes.POINTER(XErrorEvent))
_X_ERROR_HANDLER = None


def _handle_x_error(display, event):
    """Xlib XErrorHandler：只记日志、不终止进程；返回 0 = 已处理。

    整个体包 try：Xlib 可能在解释器/日志系统收尾阶段回调（进程退出时的
    ``XCloseDisplay`` 也能触发排队错误），此时任何 Python 异常都只能静默。
    """
    try:
        code = int(event.contents.error_code) if event else 0
        if code != _BAD_WINDOW:
            logging.warning("X11 请求失败（error_code=%s），已忽略", code)
    except Exception:
        pass
    return 0


def _install_error_handler(x11) -> None:
    """给本进程的 Xlib 连接装错误处理器（进程级、只装一次）。"""
    global _X_ERROR_HANDLER
    if _X_ERROR_HANDLER is not None:
        return
    try:
        x11.XSetErrorHandler.restype = ctypes.c_void_p
        x11.XSetErrorHandler.argtypes = [_X_ERROR_HANDLER_TYPE]
        _X_ERROR_HANDLER = _X_ERROR_HANDLER_TYPE(_handle_x_error)
        x11.XSetErrorHandler(_X_ERROR_HANDLER)
    except Exception:
        # 装不上就退回默认处理器；正常路径按「只对活窗口写」避免触发。
        logging.debug("安装 X11 错误处理器失败", exc_info=True)
        _X_ERROR_HANDLER = None


def _load_x11():
    """加载 libX11 / libXfixes 并声明用到的函数签名（进程内只做一次）。"""
    global _LIBS
    if _LIBS is not None:
        return _LIBS
    libx11_name = ctypes.util.find_library("X11") or "libX11.so.6"
    libxfixes_name = ctypes.util.find_library("Xfixes") or "libXfixes.so.3"
    x11 = ctypes.CDLL(libx11_name)
    xfixes = ctypes.CDLL(libxfixes_name)

    x11.XOpenDisplay.restype = ctypes.c_void_p
    x11.XOpenDisplay.argtypes = [ctypes.c_char_p]
    x11.XFlush.argtypes = [ctypes.c_void_p]
    x11.XCloseDisplay.argtypes = [ctypes.c_void_p]

    xfixes.XFixesCreateRegion.restype = ctypes.c_ulong
    xfixes.XFixesCreateRegion.argtypes = [
        ctypes.c_void_p, ctypes.POINTER(XRectangle), ctypes.c_int]
    xfixes.XFixesSetWindowShapeRegion.restype = None
    xfixes.XFixesSetWindowShapeRegion.argtypes = [
        ctypes.c_void_p, ctypes.c_ulong, ctypes.c_int,
        ctypes.c_int, ctypes.c_int, ctypes.c_ulong]
    xfixes.XFixesDestroyRegion.restype = None
    xfixes.XFixesDestroyRegion.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
    _install_error_handler(x11)
    _LIBS = (x11, xfixes)
    return _LIBS


#: 进程级共享的 X Display（多屏时每块屏一张 overlay，共用一条连接）。
#:
#: 每条 ``XOpenDisplay`` 都是一个独立的 X 连接（socket + 服务端会话资源）。
#: 多屏各开一条既浪费、也让「谁在什么时候关连接」变成多点责任；XFixes 请求
#: 只是往同一条连接里排队，共享连接没有并发问题——所有 overlay 都在 Qt 主线程
#: 创建/销毁/刷新。因此这里做引用计数：第一个控制器打开，最后一个关掉。
_DISPLAY_LOCK = threading.Lock()
_DISPLAY: ctypes.c_void_p | None = None
_DISPLAY_REFS = 0
_LIBS = None


def _acquire_display():
    """取一条可用的 X Display（引用计数 +1）；失败返回 None。"""
    global _DISPLAY, _DISPLAY_REFS
    x11, _ = _load_x11()
    with _DISPLAY_LOCK:
        if _DISPLAY is None:
            display = x11.XOpenDisplay(None)
            if not display:
                return None
            _DISPLAY = ctypes.c_void_p(display)
        _DISPLAY_REFS += 1
        return _DISPLAY


def _release_display() -> None:
    """释放一次引用；归零时真正关连接。"""
    global _DISPLAY, _DISPLAY_REFS
    x11, _ = _load_x11()
    with _DISPLAY_LOCK:
        if _DISPLAY_REFS <= 0:
            return
        _DISPLAY_REFS -= 1
        if _DISPLAY_REFS == 0 and _DISPLAY is not None:
            try:
                x11.XCloseDisplay(_DISPLAY)
            except Exception:
                logging.debug("关闭 X11 Display 失败", exc_info=True)
            _DISPLAY = None


def platform_uses_x11_input_region() -> bool:
    """当前 Qt 平台插件是否走 X11（xcb / XWayland）。

    只在 xcb 上启用本控制器：Windows/macOS 有各自实现；原生 Wayland 无
    XFixes 输入区域可用，必须回退 ``QtPerPixelInputController``。
    """
    if not sys.platform.startswith("linux"):
        return False
    try:
        from PySide6.QtGui import QGuiApplication
        # 没有 QApplication 时 platformName() 在部分 Qt 版本会返回构建默认值，
        # 不能据此判平台；调用点是 showEvent，此时 app 必然已存在。
        if QGuiApplication.instance() is None:
            return False
        return QGuiApplication.platformName() == "xcb"
    except Exception:
        return False


class X11InputRegionController:
    """把 overlay 的 XFixes 输入区域限定为可见 sprite 的绘制包围盒。

    接口与 ``platform_passthrough.QtPerPixelInputController`` /
    ``platform_win.WindowsPerPixelInputController`` 保持一致
    （``resume`` / ``stop`` / ``refresh`` / ``set_drag_active`` / ``close``），
    由 ``OverlayWindow.showEvent`` 按平台分派创建。
    """

    #: 区域刷新频率：50Hz。桌宠走动时区域会跟着移动；50Hz（≤20ms 滞后）
    #: 对「移过去再点」的交互足够，又不会让每个 10ms tick 都发一次
    #: XFixes + compositor commit。
    UPDATE_INTERVAL_MS = 20

    def __init__(self, window) -> None:
        self._window = window
        self._timer = QTimer(window)
        self._timer.setInterval(self.UPDATE_INTERVAL_MS)
        self._timer.timeout.connect(self.refresh)
        self._x11 = None
        self._xfixes = None
        self._display = None
        self._closed = False
        self._last_key = None
        self._x11, self._xfixes = _load_x11()
        # 多屏时每块屏一张 overlay：共用一条 X 连接（引用计数，最后一个关）
        self._display = _acquire_display()
        if not self._display:
            raise OSError("无法打开 X11 display（XOpenDisplay 返回 NULL）")

    # ---------------------------------------------------------------- 生命周期
    def resume(self) -> None:
        """显示/恢复：立即收敛一次并开表（show() 返回时输入区域已正确）。"""
        self._last_key = None
        self.refresh()
        self._timer.start()

    def stop(self) -> None:
        """隐藏/关闭：停表并把输入区域清空（隐藏窗口不吞点击）。

        显示侧 ``resume`` 会重新计算；不关闭 Display，避免显隐循环反复
        建立 X 连接。
        """
        self._timer.stop()
        self._last_key = None
        # 只能对真实窗口 ID 写区域：XID 0 不是合法窗口，XFixes 会以 BadWindow
        # 报错（Xlib 默认处理器会打日志/终止进程）。窗口已销毁时宁可不写。
        try:
            win_id = int(self._window.winId())
        except (RuntimeError, AttributeError):
            win_id = 0
        if win_id:
            self._apply_region(win_id, [])

    def close(self) -> None:
        """进程级销毁（closeEvent）：停表 + 归还共享 X 连接。"""
        if self._closed:
            return
        self.stop()
        self._closed = True
        if self._display:
            _release_display()
            self._display = None

    def set_drag_active(self, active: bool) -> None:  # noqa: ARG002 (协议占位)
        """拖拽期无需降频：输入区域由 sprite 包围盒驱动，与光标轮询无关。"""
        # 拖拽中 sprite 跟随光标移动，区域每 20ms 跟着走；Qt 已建立隐式
        # grab，指针短暂离开区域也不会丢 move/release 事件。
        return

    # ---------------------------------------------------------------- 区域计算
    def _collect_rects(self) -> list[tuple[int, int, int, int]]:
        win = self._window
        if not win.isVisible() or bool(getattr(win, "mouse_through", False)):
            return []
        width, height = int(win.width()), int(win.height())
        if width <= 0 or height <= 0:
            return []
        window_rect = QRect(0, 0, width, height)
        rects: list[tuple[int, int, int, int]] = []
        for sprite in getattr(win, "sprites", ()):
            if not getattr(sprite, "visible", True):
                continue
            try:
                bounds = (sprite.paint_bounds()
                          if hasattr(sprite, "paint_bounds") else sprite.rect())
            except Exception:
                continue
            if bounds is None or bounds.isEmpty():
                continue
            clipped = bounds.intersected(window_rect)
            if clipped.isEmpty():
                continue
            rects.append((clipped.x(), clipped.y(),
                          clipped.width(), clipped.height()))
        return rects

    def refresh(self) -> None:
        """收敛一次输入区域；状态未变时零 X 请求。"""
        if self._closed or not self._display:
            return
        win = self._window
        try:
            win_id = int(win.winId())
        except (RuntimeError, AttributeError):
            return
        if win_id == 0:
            return
        rects = self._collect_rects()
        key = (win_id, tuple(rects))
        if key == self._last_key:
            return
        self._apply_region(win_id, rects)
        self._last_key = key

    def _apply_region(self, win_id: int, rects) -> None:
        if not self._display or not win_id:
            return
        count = len(rects)
        if count:
            array = (XRectangle * count)(
                *[XRectangle(x, y, w, h) for (x, y, w, h) in rects])
            region = self._xfixes.XFixesCreateRegion(
                self._display, array, count)
        else:
            region = self._xfixes.XFixesCreateRegion(
                self._display, None, 0)
        if not region:
            return
        try:
            self._xfixes.XFixesSetWindowShapeRegion(
                self._display, ctypes.c_ulong(win_id),
                _SHAPE_INPUT, 0, 0, ctypes.c_ulong(region))
            self._x11.XFlush(self._display)
        except Exception:
            logging.debug("设置 X11 输入区域失败", exc_info=True)
        finally:
            self._xfixes.XFixesDestroyRegion(
                self._display, ctypes.c_ulong(region))
