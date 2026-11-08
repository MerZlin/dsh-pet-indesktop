"""Cocoa policy. Every NSView operation requires a real cocoa QApplication.

No process-wide swizzling: explicit interactive windows can still activate.
All native calls run on the GUI thread; typed function pointers avoid changing
objc_msgSend's shared ctypes signature between calls.
"""
from __future__ import annotations

import ctypes as C
import logging
import sys
from functools import lru_cache

logger = logging.getLogger(__name__)


def is_cocoa() -> bool:
    if sys.platform != "darwin":
        return False
    from PySide6.QtGui import QGuiApplication
    return QGuiApplication.instance() is not None and QGuiApplication.platformName() == "cocoa"


@lru_cache(maxsize=1)
def _objc():
    lib = C.CDLL("/usr/lib/libobjc.A.dylib")
    lib.sel_registerName.argtypes = [C.c_char_p]
    lib.sel_registerName.restype = C.c_void_p
    lib.objc_getClass.argtypes = [C.c_char_p]
    lib.objc_getClass.restype = C.c_void_p
    return lib


def _send(receiver, selector, restype=C.c_void_p, argtypes=(), args=()):
    lib = _objc()
    fn = C.CFUNCTYPE(restype, C.c_void_p, C.c_void_p, *argtypes)(("objc_msgSend", lib))
    return fn(receiver, lib.sel_registerName(selector.encode("ascii")), *args)


def _window(view_id):
    return _send(int(view_id), "window") if view_id else None


def configure_application() -> bool:
    if not is_cocoa():
        return False
    app = _send(_objc().objc_getClass(b"NSApplication"), "sharedApplication")
    return bool(_send(app, "setActivationPolicy:", C.c_bool, (C.c_long,), (1,)))


def apply_window(view_id: int, *, on_top: bool = False, passive: bool = True) -> bool:
    if not is_cocoa():
        return False
    window = _window(view_id)
    if not window:
        return False
    flags = int(_send(window, "collectionBehavior", C.c_ulong))
    # Preserve other flags; MoveToActiveSpace and CanJoinAllSpaces conflict.
    flags = (flags & ~2) | 1 | (1 << 8)  # CanJoinAllSpaces | FullScreenAuxiliary
    if passive:
        flags |= 1 << 6  # IgnoresCycle; do not enter Cmd-` window cycling.
    _send(window, "setCollectionBehavior:", None, (C.c_ulong,), (flags,))
    _send(window, "setHidesOnDeactivate:", None, (C.c_bool,), (False,))
    if passive:
        _send(window, "setHasShadow:", None, (C.c_bool,), (False,))
        _send(window, "setLevel:", None, (C.c_long,), (3 if on_top else 0,))
    return True


def order_front(view_id: int) -> bool:
    if not is_cocoa():
        return False
    window = _window(view_id)
    if not window:
        return False
    _send(window, "orderFrontRegardless", None)
    return True


def set_ignores_mouse(view_id: int, enabled: bool) -> bool:
    if not is_cocoa():
        return False
    window = _window(view_id)
    if not window:
        return False
    _send(window, "setIgnoresMouseEvents:", None, (C.c_bool,), (bool(enabled),))
    return True


@lru_cache(maxsize=1)
def _capture_api():
    lib = C.CDLL("/System/Library/Frameworks/CoreGraphics.framework/CoreGraphics")
    preflight = lib.CGPreflightScreenCaptureAccess
    preflight.argtypes, preflight.restype = [], C.c_bool
    ask = lib.CGRequestScreenCaptureAccess
    ask.argtypes, ask.restype = [], C.c_bool
    return preflight, ask


def screen_capture_preflight() -> bool:
    """Read TCC without creating a GUI or requesting access (also used by --check)."""
    return bool(_capture_api()[0]())


def capture_allowed(*, request: bool = False) -> bool:
    if not is_cocoa():
        return True
    if screen_capture_preflight():
        return True
    if request:
        _capture_api()[1]()
    return screen_capture_preflight()
