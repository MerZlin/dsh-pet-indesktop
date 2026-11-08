"""Non-Windows input adapters. Linux uses painted alpha, never QCursor polling."""
from __future__ import annotations

import logging
import sys
import time
import weakref

from PySide6.QtCore import QEvent, QObject, QTimer
from PySide6.QtGui import QBitmap, QCursor, QGuiApplication, QImage, QRegion
from PySide6.QtWidgets import QWidget

from . import macos

logger = logging.getLogger(__name__)
ALPHA_HIT_THRESHOLD = 8


def alpha_rectangles(image: QImage) -> tuple[tuple[int, int, int, int], ...]:
    """Exact alpha runs via Qt's raster region builder (GUI thread only).

    Qt 6's xcb integration uses QRasterPlatformPixmap for these bitmaps; this
    conversion creates no native X window/shape and makes no server round trip.
    Odd-width rows are byte padded, not QImage's 32-bit scanline padding.
    """
    import numpy as np
    if image.isNull():
        return ()
    alpha = image.convertToFormat(QImage.Format.Format_Alpha8)
    pixels = np.frombuffer(alpha.constBits(), dtype=np.uint8).reshape(alpha.height(), alpha.bytesPerLine())
    mask = pixels[:, :alpha.width()] >= ALPHA_HIT_THRESHOLD
    bits = np.packbits(mask, axis=1, bitorder="little")
    bitmap = QBitmap.fromData(alpha.size(), bits.tobytes(), QImage.Format.Format_MonoLSB)
    return tuple((rect.x(), rect.y(), rect.width(), rect.height()) for rect in QRegion(bitmap))


class RegionInputController(QObject):
    def __init__(self, window, *, backend=None):
        super().__init__(window)
        if backend is None:
            from .x11_input import XFixesBackend
            backend = XFixesBackend()
        self.window, self.backend = window, backend
        self._signature = None
        self._native_id = None
        self._refreshing = False
        self._running = True
        self._closed = False
        self.available = True
        self.updates = 0
        self.processing_ns = 0
        self.max_processing_ns = 0
        window.installEventFilter(self)

    def eventFilter(self, watched, event):
        if event.type() in (QEvent.Type.Show, QEvent.Type.WinIdChange):
            QTimer.singleShot(0, self, self.native_handle_changed)
        elif event.type() == QEvent.Type.Hide:
            self.stop()
        return False

    def native_handle_changed(self):
        self._signature = None
        self._native_id = None
        if not self._closed and self.window.isVisible():
            self.resume()

    def resume(self):
        if self._closed or not self.available:
            return
        self._running = True
        self._signature = None
        self.refresh()

    def stop(self):
        if self._closed:
            return
        self._running = False
        self._signature = None
        handle = self.window.windowHandle()
        if handle is not None:
            try:
                self.backend.apply(int(handle.winId()), ())
            except (OSError, RuntimeError):
                logger.debug("XFixes unavailable during hide/close", exc_info=True)

    def close(self):
        if self._closed:
            return
        try:
            self.stop()
        finally:
            self._closed = True
            self.window.removeEventFilter(self)
            self.backend.close()

    def refresh(self):
        if not self._running or self._closed or self._refreshing or not self.window.isVisible():
            return
        window = self.window
        handle = window.windowHandle()
        if handle is None:
            return
        xid = int(handle.winId())
        signature = (xid, window.input_revision())
        if signature == self._signature:
            return
        started = time.perf_counter_ns()
        if window.click_through:
            rectangles = ()
        elif window.dragging or window._press_global is not None:
            dpr = float(window.devicePixelRatioF())
            rectangles = ((0, 0, round(QWidget.width(window) * dpr), round(QWidget.height(window) * dpr)),)
        else:
            rectangles = alpha_rectangles(window.input_image())
        self._refreshing = True
        try:
            if self._native_id != xid:
                # Qt and our private XCB connection must agree that CreateWindow
                # has reached the server. Only synchronize at handle creation.
                QGuiApplication.sync()
            self.backend.apply(xid, rectangles)
            if self._native_id != xid and hasattr(self.backend, "sync"):
                self.backend.sync()
            self._native_id = xid
            self._signature = signature
            self.updates += 1
        except (OSError, RuntimeError) as exc:
            logger.warning("XFixes unavailable: %s", exc)
            # Keep a usable pet-sized rectangle. Never switch to cursor polling.
            try:
                dpr = float(window.devicePixelRatioF())
                self.backend.apply(xid, ((0, 0, round(QWidget.width(window) * dpr), round(QWidget.height(window) * dpr)),))
            except (OSError, RuntimeError):
                pass
            self._running = False
            self.available = False
            window.setProperty("desktop_input_error", str(exc))
            window.say("透明区域穿透不可用，请通过托盘操作桌宠。", 6000)
        finally:
            self._refreshing = False
            elapsed = time.perf_counter_ns() - started
            self.processing_ns += elapsed
            self.max_processing_ns = max(self.max_processing_ns, elapsed)


class MacInputController(QObject):
    _members = weakref.WeakSet()
    _timer = None

    def __init__(self, window):
        super().__init__(window)
        self.window = window
        self._applied = None
        self._running = False
        self._closed = False
        window.installEventFilter(self)
        self.resume()

    def eventFilter(self, watched, event):
        if event.type() in (QEvent.Type.Show, QEvent.Type.WinIdChange):
            QTimer.singleShot(0, self, self.native_handle_changed)
        elif event.type() == QEvent.Type.Hide:
            self.stop()
        return False

    @classmethod
    def _poll(cls):
        active = False
        for member in tuple(cls._members):
            active = member.refresh() or active
        if cls._timer is not None:
            cls._timer.setInterval(10 if active else 50)

    def resume(self):
        if self._closed or not self.window.isVisible():
            return
        cls = type(self)
        self._running = True
        self._applied = None
        cls._members.add(self)
        if cls._timer is None:
            cls._timer = QTimer(QGuiApplication.instance())
            cls._timer.setInterval(50)
            cls._timer.timeout.connect(cls._poll)
        cls._timer.start()
        self.refresh()

    def stop(self):
        self._running = False
        type(self)._members.discard(self)
        if not type(self)._members and type(self)._timer is not None:
            type(self)._timer.stop()
        self._applied = None

    def close(self):
        self.stop()
        self._closed = True
        self.window.removeEventFilter(self)

    native_handle_changed = resume

    def refresh(self):
        window = self.window
        if not self._running or not window.isVisible():
            return False
        local = window.mapFromGlobal(QCursor.pos())
        inside = window.rect().contains(local)
        pressed = window.dragging or window._press_global is not None
        through = window.click_through or (not pressed and (not inside or window.is_transparent_at(local)))
        handle = window.windowHandle()
        if handle is None:
            return False
        state = (int(handle.winId()), through)
        if state != self._applied and macos.set_ignores_mouse(*state):
            self._applied = state
        return inside and not pressed


def create_controller(window):
    qpa = QGuiApplication.platformName()
    if sys.platform == "darwin" and qpa == "cocoa":
        return MacInputController(window)
    if sys.platform == "linux" and qpa == "xcb":
        try:
            return RegionInputController(window)
        except (OSError, RuntimeError) as exc:
            logger.warning("XFixes initialization failed: %s", exc)
            window.setProperty("desktop_input_error", str(exc))
            QTimer.singleShot(0, window, lambda: window.say("透明区域穿透不可用：请安装 libxcb-xfixes0 并检查 XWayland。", 7000))
    return None
