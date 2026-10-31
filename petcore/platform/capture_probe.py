"""直播捕获自检：窗口样式 + PrintWindow/BitBlt 读回的非黑占比。

桌宠是 ``WA_TranslucentBackground``（``WS_EX_LAYERED`` + ``UpdateLayeredWindow``）
时，窗口 DC 往往为空：BitBlt 读到全黑，PrintWindow(PW_RENDERFULLCONTENT) 仍能
读到画面。色键模式是不透明窗口，两条读回都应有画面。这个模块只报事实，不猜
OBS 版本，也不写窗口标题/正文到日志。
"""

from __future__ import annotations

import ctypes
import os
from ctypes import wintypes
from typing import Any

from . import win as win_mod

_PW_RENDERFULLCONTENT = 0x00000002
_SRCCOPY = 0x00CC0020
_CAPTUREBLT = 0x40000000
_DIB_RGB_COLORS = 0
_BI_RGB = 0


def nonblack_ratio(pixels: bytes, width: int, height: int, *, threshold: int = 8) -> float:
    """BGRA/RGB 像素里「任一 RGB 通道 > threshold」的比例（0..1）。

    纯函数：合成图、截屏回读、测试共用。alpha 不参与判定——透明窗口的
    PrintWindow 回读可能 alpha=0 但 RGB 有内容。
    """
    width = max(0, int(width))
    height = max(0, int(height))
    total = width * height
    raw = bytes(pixels or b"")
    if total <= 0 or len(raw) < total * 4:
        return 0.0
    nonzero = 0
    limit = max(0, min(255, int(threshold)))
    for index in range(0, total * 4, 4):
        if (
            raw[index] > limit
            or raw[index + 1] > limit
            or raw[index + 2] > limit
        ):
            nonzero += 1
    return nonzero / total


class _BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = (
        ("biSize", wintypes.DWORD),
        ("biWidth", wintypes.LONG),
        ("biHeight", wintypes.LONG),
        ("biPlanes", wintypes.WORD),
        ("biBitCount", wintypes.WORD),
        ("biCompression", wintypes.DWORD),
        ("biSizeImage", wintypes.DWORD),
        ("biXPelsPerMeter", wintypes.LONG),
        ("biYPelsPerMeter", wintypes.LONG),
        ("biClrUsed", wintypes.DWORD),
        ("biClrImportant", wintypes.DWORD),
    )


class _BITMAPINFO(ctypes.Structure):
    _fields_ = (
        ("bmiHeader", _BITMAPINFOHEADER),
        ("bmiColors", wintypes.DWORD * 3),
    )


def _api():
    user32 = ctypes.windll.user32
    gdi32 = ctypes.windll.gdi32
    user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
    user32.GetWindowRect.restype = wintypes.BOOL
    user32.GetWindowDC.argtypes = [wintypes.HWND]
    user32.GetWindowDC.restype = wintypes.HDC
    user32.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]
    user32.ReleaseDC.restype = ctypes.c_int
    user32.PrintWindow.argtypes = [wintypes.HWND, wintypes.HDC, wintypes.UINT]
    user32.PrintWindow.restype = wintypes.BOOL
    user32.IsWindowVisible.argtypes = [wintypes.HWND]
    user32.IsWindowVisible.restype = wintypes.BOOL
    gdi32.CreateCompatibleDC.argtypes = [wintypes.HDC]
    gdi32.CreateCompatibleDC.restype = wintypes.HDC
    gdi32.CreateCompatibleBitmap.argtypes = [wintypes.HDC, ctypes.c_int, ctypes.c_int]
    gdi32.CreateCompatibleBitmap.restype = wintypes.HBITMAP
    gdi32.SelectObject.argtypes = [wintypes.HDC, wintypes.HGDIOBJ]
    gdi32.SelectObject.restype = wintypes.HGDIOBJ
    gdi32.BitBlt.argtypes = [
        wintypes.HDC,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        wintypes.HDC,
        ctypes.c_int,
        ctypes.c_int,
        wintypes.DWORD,
    ]
    gdi32.BitBlt.restype = wintypes.BOOL
    gdi32.GetDIBits.argtypes = [
        wintypes.HDC,
        wintypes.HBITMAP,
        wintypes.UINT,
        wintypes.UINT,
        ctypes.c_void_p,
        ctypes.POINTER(_BITMAPINFO),
        wintypes.UINT,
    ]
    gdi32.GetDIBits.restype = ctypes.c_int
    gdi32.DeleteObject.argtypes = [wintypes.HGDIOBJ]
    gdi32.DeleteObject.restype = wintypes.BOOL
    gdi32.DeleteDC.argtypes = [wintypes.HDC]
    gdi32.DeleteDC.restype = wintypes.BOOL
    return user32, gdi32


def _read_pixels(hwnd: int, *, printwindow: bool) -> tuple[bytes, int, int, str]:
    user32, gdi32 = _api()
    rect = wintypes.RECT()
    if not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
        return b"", 0, 0, "GetWindowRect failed"
    width = max(1, int(rect.right - rect.left))
    height = max(1, int(rect.bottom - rect.top))
    screen_dc = user32.GetWindowDC(hwnd)
    if not screen_dc:
        return b"", width, height, "GetWindowDC failed"
    mem_dc = gdi32.CreateCompatibleDC(screen_dc)
    bitmap = gdi32.CreateCompatibleBitmap(screen_dc, width, height)
    if not mem_dc or not bitmap:
        if bitmap:
            gdi32.DeleteObject(bitmap)
        if mem_dc:
            gdi32.DeleteDC(mem_dc)
        user32.ReleaseDC(hwnd, screen_dc)
        return b"", width, height, "GDI allocation failed"
    old = gdi32.SelectObject(mem_dc, bitmap)
    try:
        if printwindow:
            ok = user32.PrintWindow(hwnd, mem_dc, _PW_RENDERFULLCONTENT)
        else:
            ok = gdi32.BitBlt(
                mem_dc,
                0,
                0,
                width,
                height,
                screen_dc,
                0,
                0,
                _SRCCOPY | _CAPTUREBLT,
            )
        if not ok:
            return b"", width, height, "readback failed"
        info = _BITMAPINFO()
        info.bmiHeader.biSize = ctypes.sizeof(_BITMAPINFOHEADER)
        info.bmiHeader.biWidth = width
        info.bmiHeader.biHeight = -height
        info.bmiHeader.biPlanes = 1
        info.bmiHeader.biBitCount = 32
        info.bmiHeader.biCompression = _BI_RGB
        size = width * height * 4
        buffer = ctypes.create_string_buffer(size)
        copied = gdi32.GetDIBits(
            mem_dc,
            bitmap,
            0,
            height,
            buffer,
            ctypes.byref(info),
            _DIB_RGB_COLORS,
        )
        if copied != height:
            return b"", width, height, "GetDIBits failed"
        return buffer.raw, width, height, ""
    finally:
        if old:
            gdi32.SelectObject(mem_dc, old)
        gdi32.DeleteObject(bitmap)
        gdi32.DeleteDC(mem_dc)
        user32.ReleaseDC(hwnd, screen_dc)


def probe_window(hwnd: int) -> dict[str, Any]:
    """对真实窗口做一次读回自检；非 Windows/句柄无效时返回 supported=False。"""
    if os.name != "nt":
        return {"supported": False, "reason": "not_windows"}
    hwnd = int(hwnd or 0)
    if hwnd <= 0:
        return {"supported": False, "reason": "no_hwnd"}
    user32, _gdi32 = _api()
    visible = bool(user32.IsWindowVisible(hwnd))
    style = win_mod.window_exstyle(hwnd)
    print_pixels, width, height, print_error = _read_pixels(hwnd, printwindow=True)
    blt_pixels, _w, _h, blt_error = _read_pixels(hwnd, printwindow=False)
    print_ratio = nonblack_ratio(print_pixels, width, height)
    blt_ratio = nonblack_ratio(blt_pixels, width, height)
    if blt_ratio >= 0.5:
        method = "BitBlt 也可；优先 Windows 10 (1903+)"
    elif print_ratio >= 0.1:
        method = "BitBlt 黑屏；请用 Windows 10 (1903+) 窗口捕获，或切色键模式"
    else:
        method = "两种读回都接近黑屏；请切到色键窗口模式"
    return {
        "supported": True,
        "hwnd": hwnd,
        "width": width,
        "height": height,
        "visible": visible,
        "exstyle": style,
        "toolwindow": bool(style & 0x00000080),
        "noactivate": bool(style & 0x08000000),
        "layered": bool(style & 0x00080000),
        "appwindow": bool(style & 0x00040000),
        "printwindow": round(print_ratio, 4),
        "bitblt": round(blt_ratio, 4),
        "printwindow_error": print_error,
        "bitblt_error": blt_error,
        "suggested_method": method,
    }
