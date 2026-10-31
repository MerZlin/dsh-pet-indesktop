from __future__ import annotations

import ctypes
from ctypes import wintypes
import logging
import os
import time

from PySide6.QtCore import QObject, QRect, Qt, QTimer
from PySide6.QtGui import QCursor

logger = logging.getLogger("petcore.platform.win")

_GWL_EXSTYLE = -20
_WS_EX_TRANSPARENT = 0x00000020
_WS_EX_NOACTIVATE = 0x08000000
_WS_EX_TOOLWINDOW = 0x00000080
_WS_EX_LAYERED = 0x00080000
_WS_EX_APPWINDOW = 0x00040000
_HWND_TOPMOST = -1
_HWND_NOTOPMOST = -2
_SWP_NOSIZE = 0x0001
_SWP_NOMOVE = 0x0002
_SWP_NOACTIVATE = 0x0010


def _user32():
    """取 user32 并声明 64 位安全的 argtypes（HWND 是指针，默认 int32 会截断）。"""
    user32 = ctypes.windll.user32
    try:
        user32.GetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int]
        user32.GetWindowLongW.restype = ctypes.c_long
        user32.SetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_long]
        user32.SetWindowLongW.restype = ctypes.c_long
    except Exception:
        logger.debug("configure user32 failed", exc_info=True)
    return user32


def set_click_through(hwnd: int, enabled: bool) -> bool:
    if os.name != "nt":
        return False
    try:
        user32 = _user32()
        style = user32.GetWindowLongW(hwnd, _GWL_EXSTYLE)
        updated = style | _WS_EX_TRANSPARENT if enabled else style & ~_WS_EX_TRANSPARENT
        if updated == style:
            return True
        user32.SetWindowLongW(hwnd, _GWL_EXSTYLE, updated)
        return True
    except Exception:
        logger.debug("set_click_through failed", exc_info=True)
        return False


def apply_tool_window(hwnd: int) -> None:
    if os.name != "nt":
        return
    try:
        user32 = _user32()
        style = user32.GetWindowLongW(hwnd, _GWL_EXSTYLE)
        user32.SetWindowLongW(hwnd, _GWL_EXSTYLE, style | _WS_EX_TOOLWINDOW | _WS_EX_NOACTIVATE)
    except Exception:
        logger.debug("apply_tool_window failed", exc_info=True)


def apply_stream_window(hwnd: int, *, keycolor: bool = False) -> None:
    """直播捕获兼容：还原成普通顶层窗口（可被 OBS/直播姬枚举与捕获）。

    Qt 的 ``Tool`` 窗口带 ``WS_EX_TOOLWINDOW``（不进任务栏/Alt-Tab），部分捕获
    后端会直接跳过；``WS_EX_NOACTIVATE`` 还会让窗口拿不到激活态。这里两个都清掉。

    ``keycolor=True`` 时还要清掉 ``WS_EX_LAYERED``：Qt 在运行期把
    ``WA_TranslucentBackground`` 改回 False 后不会自动去掉分层位（实测同一个
    HWND 一直是 layered），而分层窗口正是 OBS BitBlt/WGC 读回黑屏的来源。
    """
    if os.name != "nt":
        return
    try:
        user32 = _user32()
        style = user32.GetWindowLongW(hwnd, _GWL_EXSTYLE)
        clear = _WS_EX_TOOLWINDOW | _WS_EX_NOACTIVATE
        if keycolor:
            clear |= _WS_EX_LAYERED
        updated = style & ~clear
        if updated != style:
            user32.SetWindowLongW(hwnd, _GWL_EXSTYLE, updated)
    except Exception:
        logger.debug("apply_stream_window failed", exc_info=True)


def window_exstyle(hwnd: int) -> int:
    """当前扩展样式；非 Windows/读取失败返回 0。"""
    if os.name != "nt":
        return 0
    try:
        return int(_user32().GetWindowLongW(hwnd, _GWL_EXSTYLE))
    except Exception:
        return 0


def set_topmost(hwnd: int, enabled: bool) -> bool:
    """只改 Z 序，不重建 HWND（OBS 窗口源不会因此掉源）。"""
    if os.name != "nt":
        return False
    try:
        user32 = ctypes.windll.user32
        user32.SetWindowPos.argtypes = [
            wintypes.HWND,
            wintypes.HWND,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_uint,
        ]
        user32.SetWindowPos.restype = wintypes.BOOL
        return bool(
            user32.SetWindowPos(
                hwnd,
                _HWND_TOPMOST if enabled else _HWND_NOTOPMOST,
                0,
                0,
                0,
                0,
                _SWP_NOMOVE | _SWP_NOSIZE | _SWP_NOACTIVATE,
            )
        )
    except Exception:
        logger.debug("set_topmost failed", exc_info=True)
        return False


def apply_click_through(window, enabled: bool) -> None:
    if os.name == "nt":
        set_click_through(int(window.winId()), enabled)
        return
    window.setWindowFlag(Qt.WindowType.WindowTransparentForInput, bool(enabled))
    if window.isVisible():
        window.show()


#: SendInput 的鼠标事件常量。
_MOUSEEVENTF_LEFTDOWN = 0x0002
_MOUSEEVENTF_LEFTUP = 0x0004


class _MouseInput(ctypes.Structure):
    _fields_ = (
        ("dx", ctypes.c_long),
        ("dy", ctypes.c_long),
        ("mouseData", ctypes.c_ulong),
        ("dwFlags", ctypes.c_ulong),
        ("time", ctypes.c_ulong),
        ("dwExtraInfo", ctypes.c_void_p),
    )


class _Input(ctypes.Structure):
    _fields_ = (("type", ctypes.c_ulong), ("mi", _MouseInput))


def _send_left_click(user32=None) -> bool:
    """在当前光标位置注入一次真实的左键 down+up；非 Windows/失败返回 False。"""
    if os.name != "nt":
        return False
    try:
        user32 = user32 or _user32()
        try:
            user32.SendInput.argtypes = [
                wintypes.UINT,
                ctypes.POINTER(_Input),
                ctypes.c_int,
            ]
            user32.SendInput.restype = wintypes.UINT
        except Exception:
            pass
        events = (_Input * 2)()
        events[0].type = 0  # INPUT_MOUSE
        events[0].mi.dwFlags = _MOUSEEVENTF_LEFTDOWN
        events[1].type = 0
        events[1].mi.dwFlags = _MOUSEEVENTF_LEFTUP
        sent = user32.SendInput(2, events, ctypes.sizeof(_Input))
        return int(sent) == 2
    except Exception:
        logger.debug("SendInput left click failed", exc_info=True)
        return False


def forward_left_click(
    window,
    *,
    hold_ms: int = 80,
    user32=None,
    send_input=None,
) -> bool:
    """把一次左键单击转发给桌宠下面的窗口。

    穿透模式不能一直挂着 ``WS_EX_TRANSPARENT``（那样右键也会被放走），所以
    在松手时临时置穿透位、注入真实左键、再恢复。注入前先把窗口变透明，系统
    命中测试才会跳过桌宠、落到下面的窗口；恢复留到输入处理之后（``hold_ms``）。
    """
    if os.name != "nt":
        return False
    user32 = user32 or _user32()
    hwnd = int(window.winId())
    try:
        style = int(user32.GetWindowLongW(hwnd, _GWL_EXSTYLE))
        user32.SetWindowLongW(hwnd, _GWL_EXSTYLE, style | _WS_EX_TRANSPARENT)
    except Exception:
        logger.debug("forward click: set transparent failed", exc_info=True)
        return False
    hold_ms = max(20, int(hold_ms))
    window._through_hold_until = time.monotonic() + hold_ms / 1000.0

    def restore() -> None:
        try:
            user32.SetWindowLongW(hwnd, _GWL_EXSTYLE, style)
        except Exception:
            logger.debug("forward click: restore style failed", exc_info=True)
        finally:
            window._through_hold_until = 0.0

    click = send_input or _send_left_click
    try:
        ok = bool(click(user32))
    except Exception:
        logger.debug("forward click: SendInput raised", exc_info=True)
        ok = False
    if not ok:
        restore()
        return False
    QTimer.singleShot(hold_ms, window, restore)
    return True


class PerPixelInputController(QObject):
    NORMAL_INTERVAL_MS = 10
    IDLE_INTERVAL_MS = 50

    def __init__(self, window, is_transparent, parent: QObject | None = None):
        super().__init__(parent or window)
        self._window = window
        self._is_transparent = is_transparent
        self._applied: bool | None = None
        self._timer = QTimer(self)
        self._timer.setInterval(self.NORMAL_INTERVAL_MS)
        self._timer.timeout.connect(self.refresh)
        self._timer.start()

    def stop(self) -> None:
        """隐藏/关闭时停表并清穿透位；显示时由 ``resume()`` 重新收敛。"""
        self._timer.stop()
        try:
            self._apply(False)
        except (RuntimeError, AttributeError):
            pass

    def resume(self) -> None:
        """显示/恢复：作废缓存，同步收敛一次再重开轮询。"""
        self._applied = None
        self.refresh()
        self._timer.start()

    def refresh(self) -> None:
        if os.name != "nt":
            return
        window = self._window
        try:
            if not window.isVisible():
                self._apply(False)
                return
            # 转发点击期间窗口必须保持透明（让系统命中下面的窗口），由
            # forward_left_click 的恢复计时独占样式，轮询不得抢先写回。
            if time.monotonic() < float(getattr(window, "_through_hold_until", 0.0) or 0.0):
                return
            position = QCursor.pos()
            local = window.mapFromGlobal(position)
            inside = QRect(0, 0, window.width(), window.height()).contains(local)
            press_active = bool(
                getattr(window, "dragging", False)
                or getattr(window, "_left_press_active", False)
                or getattr(window, "_right_press_active", False)
            )
            interval = (
                self.IDLE_INTERVAL_MS
                if press_active or not inside
                else self.NORMAL_INTERVAL_MS
            )
            if self._timer.interval() != interval:
                self._timer.setInterval(interval)
            # 穿透模式不再整窗挂 WS_EX_TRANSPARENT（否则右键也被放走）：
            # 只有光标落在透明像素上才真正穿透，左键穿透由窗口在松手时转发。
            enabled = False
            if inside and not press_active:
                enabled = bool(self._is_transparent(local))
            self._apply(enabled)
        except (RuntimeError, AttributeError):
            logger.debug("per-pixel refresh failed", exc_info=True)

    def _apply(self, enabled: bool) -> None:
        if self._applied == enabled:
            return
        set_click_through(int(self._window.winId()), enabled)
        self._applied = enabled
