"""Read-only desktop queries, independent of Qt and optional screen understanding.

Importing this module performs no queries. Native handles remain owned by this
backend; reasons are fixed codes and never contain user titles or raw exceptions.
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Generic, Literal, Protocol, TypeVar

T = TypeVar("T")
QueryStatus = Literal["ok", "no_window", "unknown", "unsupported", "error"]
CursorState = Literal["showing", "hidden", "suppressed"]
CURSOR_SHOWING = 0x00000001
CURSOR_SUPPRESSED = 0x00000002


@dataclass(frozen=True)
class QueryResult(Generic[T]):
    status: QueryStatus
    value: T | None = None
    reason: str = ""


@dataclass(frozen=True)
class ForegroundInfo:
    hwnd: int
    pid: int
    process_name: str
    title: str
    rect: tuple[int, int, int, int]


class DesktopQueryPort(Protocol):
    def foreground_window(self) -> QueryResult[ForegroundInfo]: ...
    def cursor_state(self) -> QueryResult[CursorState]: ...
    def idle_time(self) -> QueryResult[float]: ...


class CursorInfo(ctypes.Structure):
    _fields_ = [("cbSize", ctypes.c_uint), ("flags", ctypes.c_uint), ("hCursor", ctypes.wintypes.HANDLE), ("ptScreenPos", ctypes.wintypes.POINT)]


class LastInputInfo(ctypes.Structure):
    _fields_ = [("cbSize", ctypes.c_uint), ("dwTime", ctypes.c_ulong)]


class _SystemDesktopQuery:
    def __init__(self, user32=None) -> None:
        # Only the legacy cursor API injects a ctypes-like library. Do not load
        # DLLs here: default access and module import must remain side-effect free.
        self._user32 = user32

    def foreground_window(self) -> QueryResult[ForegroundInfo]:
        if sys.platform != "win32":
            return QueryResult("unsupported", reason="platform_unsupported")
        try:
            user32 = ctypes.windll.user32
            kernel32 = ctypes.windll.kernel32
            # 显式声明签名：默认 restype=c_int 会在 64 位下截断 HWND/HANDLE
            user32.GetForegroundWindow.restype = ctypes.wintypes.HWND
            user32.IsWindowVisible.argtypes = [ctypes.wintypes.HWND]
            user32.IsIconic.argtypes = [ctypes.wintypes.HWND]
            user32.GetWindowRect.argtypes = [ctypes.wintypes.HWND, ctypes.POINTER(ctypes.wintypes.RECT)]
            user32.GetWindowTextLengthW.argtypes = [ctypes.wintypes.HWND]
            user32.GetWindowTextW.argtypes = [ctypes.wintypes.HWND, ctypes.c_wchar_p, ctypes.c_int]
            user32.GetWindowThreadProcessId.argtypes = [ctypes.wintypes.HWND, ctypes.POINTER(ctypes.c_ulong)]
            kernel32.OpenProcess.restype = ctypes.wintypes.HANDLE
            kernel32.OpenProcess.argtypes = [ctypes.c_ulong, ctypes.c_int, ctypes.c_ulong]
            kernel32.QueryFullProcessImageNameW.argtypes = [
                ctypes.wintypes.HANDLE,
                ctypes.c_ulong,
                ctypes.c_wchar_p,
                ctypes.POINTER(ctypes.c_ulong),
            ]
            kernel32.CloseHandle.argtypes = [ctypes.wintypes.HANDLE]
            hwnd = user32.GetForegroundWindow()
            if not hwnd:
                return QueryResult("no_window", reason="no_foreground")

            # 检查窗口可见性与最小化
            if not user32.IsWindowVisible(hwnd):
                return QueryResult("no_window", reason="not_visible")
            if user32.IsIconic(hwnd):
                return QueryResult("no_window", reason="minimized")

            # 检查是否被 DWM 隐藏/幽灵（如虚拟桌面切换、UWP 挂起）
            # DWMWA_CLOAKED = 14；DWM 不可用时继续使用 User32 信息。
            dwmapi = None
            try:
                dwmapi = ctypes.windll.dwmapi
                dwmapi.DwmGetWindowAttribute.argtypes = [
                    ctypes.wintypes.HWND,
                    ctypes.c_ulong,
                    ctypes.c_void_p,
                    ctypes.c_ulong,
                ]
                dwmapi.DwmGetWindowAttribute.restype = ctypes.c_long
                cloaked = ctypes.c_int(0)
                if dwmapi.DwmGetWindowAttribute(hwnd, 14, ctypes.byref(cloaked), ctypes.sizeof(cloaked)) == 0 and cloaked.value != 0:
                    return QueryResult("no_window", reason="cloaked")
            except (AttributeError, OSError, TypeError, RuntimeError):
                dwmapi = None

            # 获取窗口矩形边界：优先 DwmGetWindowAttribute(DWMWA_EXTENDED_FRAME_BOUNDS = 9)
            rect: tuple[int, int, int, int] | None = None
            if dwmapi is not None:
                try:
                    rect_dwm = ctypes.wintypes.RECT()
                    if dwmapi.DwmGetWindowAttribute(hwnd, 9, ctypes.byref(rect_dwm), ctypes.sizeof(rect_dwm)) == 0:
                        w = rect_dwm.right - rect_dwm.left
                        h = rect_dwm.bottom - rect_dwm.top
                        if w > 0 and h > 0:
                            rect = (rect_dwm.left, rect_dwm.top, w, h)
                except (AttributeError, OSError, TypeError, RuntimeError):
                    pass

            if rect is None:
                rect_raw = ctypes.wintypes.RECT()
                if user32.GetWindowRect(hwnd, ctypes.byref(rect_raw)):
                    w = rect_raw.right - rect_raw.left
                    h = rect_raw.bottom - rect_raw.top
                    if w > 0 and h > 0:
                        rect = (rect_raw.left, rect_raw.top, w, h)

            if rect is None:
                return QueryResult("no_window", reason="invalid_rect")

            # 标题
            length = user32.GetWindowTextLengthW(hwnd)
            title = ""
            if length > 0:
                buf = ctypes.create_unicode_buffer(length + 1)
                user32.GetWindowTextW(hwnd, buf, length + 1)
                title = buf.value.strip()

            # PID 与 进程名
            pid = ctypes.c_ulong(0)
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            proc = ""
            if pid.value:
                hproc = kernel32.OpenProcess(0x1000, False, pid.value)  # PROCESS_QUERY_LIMITED_INFORMATION
                if hproc:
                    try:
                        pbuf = ctypes.create_unicode_buffer(260)
                        size = ctypes.c_ulong(260)
                        if kernel32.QueryFullProcessImageNameW(hproc, 0, pbuf, ctypes.byref(size)):
                            proc = Path(pbuf.value).name
                    finally:
                        kernel32.CloseHandle(hproc)

            return QueryResult("ok", ForegroundInfo(int(hwnd), int(pid.value), proc, title, rect))
        except Exception:
            return QueryResult("error", reason="foreground_query_failed")

    def cursor_state(self) -> QueryResult[CursorState]:
        if sys.platform != "win32":
            return QueryResult("unsupported", reason="platform_unsupported")
        try:
            user32 = self._user32 or ctypes.windll.user32
            get_cursor_info = user32.GetCursorInfo
            get_cursor_info.argtypes = [ctypes.POINTER(CursorInfo)]
            get_cursor_info.restype = ctypes.wintypes.BOOL
            info = CursorInfo()
            info.cbSize = ctypes.sizeof(CursorInfo)
            if not get_cursor_info(ctypes.byref(info)):
                return QueryResult("unknown", reason="cursor_unavailable")
            if info.flags & CURSOR_SHOWING:
                return QueryResult("ok", "showing")
            if info.flags & CURSOR_SUPPRESSED:
                return QueryResult("ok", "suppressed")
            return QueryResult("ok", "hidden")
        except (AttributeError, OSError, TypeError, RuntimeError):
            return QueryResult("error", reason="cursor_query_failed")

    def idle_time(self) -> QueryResult[float]:
        if sys.platform != "win32":
            return QueryResult("unsupported", reason="platform_unsupported")
        try:
            info = LastInputInfo()
            info.cbSize = ctypes.sizeof(LastInputInfo)
            if ctypes.windll.user32.GetLastInputInfo(ctypes.byref(info)):
                # Preserve the legacy GetTickCount subtraction, including its
                # zero clamp; tick wrap handling is a separate behavior change.
                uptime_ms = ctypes.windll.kernel32.GetTickCount()
                return QueryResult("ok", max(0.0, (uptime_ms - info.dwTime) / 1000.0))
        except Exception:
            pass
        return QueryResult("error", reason="idle_query_failed")


_DEFAULT_QUERY: DesktopQueryPort = _SystemDesktopQuery()


def get_desktop_query(*, user32=None) -> DesktopQueryPort:
    """Return the stateless backend; optional user32 preserves the cursor seam."""
    return _DEFAULT_QUERY if user32 is None else _SystemDesktopQuery(user32)
