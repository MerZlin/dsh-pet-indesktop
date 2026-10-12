"""X11 workspace hints for persistent desktop companions."""
from __future__ import annotations

import ctypes
from functools import lru_cache
import logging
import sys

from PySide6.QtCore import QEvent, QObject, QTimer
from PySide6.QtGui import QGuiApplication


class _ClientData(ctypes.Union):
    _fields_ = [('b', ctypes.c_char * 20), ('s', ctypes.c_short * 10), ('l', ctypes.c_long * 5)]


class _ClientMessage(ctypes.Structure):
    _fields_ = [('type', ctypes.c_int), ('serial', ctypes.c_ulong),
                ('send_event', ctypes.c_int), ('display', ctypes.c_void_p),
                ('window', ctypes.c_ulong), ('message_type', ctypes.c_ulong),
                ('format', ctypes.c_int), ('data', _ClientData)]


class _XEvent(ctypes.Union):
    _fields_ = [('client', _ClientMessage), ('pad', ctypes.c_long * 24)]


@lru_cache(maxsize=1)
def _xlib():
    lib = ctypes.CDLL('libX11.so.6')
    declarations = {
        'XOpenDisplay': ([ctypes.c_char_p], ctypes.c_void_p),
        'XDefaultRootWindow': ([ctypes.c_void_p], ctypes.c_ulong),
        'XInternAtom': ([ctypes.c_void_p, ctypes.c_char_p, ctypes.c_int], ctypes.c_ulong),
        'XSendEvent': ([ctypes.c_void_p, ctypes.c_ulong, ctypes.c_int, ctypes.c_long,
                        ctypes.POINTER(_XEvent)], ctypes.c_int),
        'XCloseDisplay': ([ctypes.c_void_p], ctypes.c_int),
    }
    for name, (args, result) in declarations.items():
        function = getattr(lib, name)
        function.argtypes, function.restype = args, result
    return lib


def _request_all_desktops(window_id: int) -> bool:
    """Ask the WM to pin a mapped window (EWMH 5.5); never activate it."""
    display = None
    try:
        lib = _xlib()
        display = lib.XOpenDisplay(None)
        if not display:
            return False
        atom = lib.XInternAtom(display, b'_NET_WM_DESKTOP', 1)
        if not atom:
            return False
        event = _XEvent()
        event.client.type = 33  # ClientMessage
        event.client.display = display
        event.client.window = window_id
        event.client.message_type = atom
        event.client.format = 32
        event.client.data.l[0] = 0xffffffff  # All desktops, not just always-on-top.
        event.client.data.l[1] = 1  # Normal application source.
        return bool(lib.XSendEvent(display, lib.XDefaultRootWindow(display), 0,
                                   (1 << 20) | (1 << 19), ctypes.byref(event)))
    except (OSError, AttributeError):
        logging.debug('X11 all-desktops request unavailable', exc_info=True)
        return False
    finally:
        if display:
            lib.XCloseDisplay(display)  # Flush request and release this connection.


class _AllDesktopsFilter(QObject):
    def __init__(self, window):
        super().__init__(window)
        self._pending = False

    def eventFilter(self, window, event):
        if event.type() in (QEvent.Type.Show, QEvent.Type.WinIdChange):
            self.schedule()
        return False

    def schedule(self):
        if not self._pending:
            self._pending = True
            QTimer.singleShot(0, self, self.apply)

    def apply(self):
        self._pending = False
        window = self.parent()
        if window is not None and window.isWindow() and window.isVisible():
            _request_all_desktops(int(window.winId()))


def keep_on_all_desktops(window) -> None:
    """Reapply after show/native-window recreation; embedded children are skipped."""
    if sys.platform != 'linux' or QGuiApplication.platformName() != 'xcb':
        return
    if getattr(window, '_all_desktops_filter', None) is None:
        watcher = _AllDesktopsFilter(window)
        window._all_desktops_filter = watcher
        window.installEventFilter(watcher)
        if window.isVisible():
            watcher.schedule()
