"""X11 overlay geometry and input-only shapes; other platforms keep their backend."""
from __future__ import annotations

import ctypes
from functools import lru_cache
from math import ceil
import sys

from PySide6.QtCore import QEvent, QObject, QPoint, QRect, Qt
from PySide6.QtGui import QBitmap, QGuiApplication, QImage, QPainter, QRegion


def is_x11():
    return sys.platform == 'linux' and QGuiApplication.platformName() == 'xcb'


def desktop_geometry(screen):
    if is_x11() and hasattr(screen, 'virtualGeometry'):
        return QRect(screen.virtualGeometry())
    return QRect(screen.geometry())


def desktop_bounds(screen):
    geometry = desktop_geometry(screen)
    screens = screen.virtualSiblings() if is_x11() and hasattr(screen, 'virtualSiblings') else [screen]
    available = QRect()
    for item in screens:
        available = available.united(item.availableGeometry())
    return available.translated(-geometry.topLeft())


class _Rectangle(ctypes.Structure):
    _fields_ = [('x', ctypes.c_short), ('y', ctypes.c_short),
                ('width', ctypes.c_ushort), ('height', ctypes.c_ushort)]


@lru_cache(maxsize=1)
def _libraries():
    x = ctypes.CDLL('libX11.so.6')
    shape = ctypes.CDLL('libXext.so.6')
    for name, args, result in (
        ('XOpenDisplay', [ctypes.c_char_p], ctypes.c_void_p),
        ('XCloseDisplay', [ctypes.c_void_p], ctypes.c_int),
        ('XFlush', [ctypes.c_void_p], ctypes.c_int),
    ):
        fn = getattr(x, name)
        fn.argtypes, fn.restype = args, result
    shape.XShapeCombineRectangles.argtypes = [
        ctypes.c_void_p, ctypes.c_ulong, ctypes.c_int, ctypes.c_int, ctypes.c_int,
        ctypes.POINTER(_Rectangle), ctypes.c_int, ctypes.c_int, ctypes.c_int]
    shape.XShapeCombineRectangles.restype = None
    return x, shape


class _Connection:
    def __init__(self):
        self.x, self.shape = _libraries()
        self.display = self.x.XOpenDisplay(None)
        if not self.display:
            raise RuntimeError('Cannot open X11 display for overlay input shape')

    def close(self, *_args):
        if self.display:
            self.x.XCloseDisplay(self.display)
            self.display = None


class LinuxOverlayInput(QObject):
    """Only painted sprite pixels receive input; the desktop stays clickable."""
    def __init__(self, window):
        super().__init__(window)
        self.window = window
        self.native = _Connection()
        self.last = None
        self.cache = {}
        self.applying = False
        window.destroyed.connect(self.native.close)
        window.installEventFilter(self)
        # Apply even before mapping: an empty overlay must never block the desktop.
        self.refresh()

    def eventFilter(self, window, event):
        if event.type() == QEvent.Type.Show or (
                event.type() == QEvent.Type.WinIdChange and window.isVisible()):
            if not self.native.display:
                self.native.display = self.native.x.XOpenDisplay(None)
            self.last = None
            self.refresh()
        elif event.type() == QEvent.Type.Close:
            self.native.close()
            self.cache.clear()
            self.last = None
        elif event.type() in (QEvent.Type.Paint, QEvent.Type.Resize):
            self.refresh()
        return False

    def refresh(self):
        if self.applying or not self.native.display:
            return
        self.applying = True
        try:
            window = self.window
            dpr = window.devicePixelRatioF()
            region = QRegion()
            live = {}
            if not window.mouse_through:
                for sprite in window.sprites:
                    if not getattr(sprite, 'visible', True):
                        continue
                    bounds = sprite.paint_bounds()
                    if bounds.isEmpty():
                        continue
                    sprite._rebuild_pixmap()
                    pixmap = sprite._pixmap
                    if pixmap is None:
                        continue
                    key = (pixmap.cacheKey(), bounds.size(), dpr,
                           sprite.pos.x() - bounds.x(), sprite.pos.y() - bounds.y(),
                           sprite._probe_angle, sprite._throw_angle, sprite._squash_progress)
                    cached = self.cache.get(sprite)
                    if cached is None or cached[0] != key:
                        image = QImage(ceil(bounds.width() * dpr), ceil(bounds.height() * dpr),
                                       QImage.Format.Format_ARGB32_Premultiplied)
                        image.fill(Qt.GlobalColor.transparent)
                        painter = QPainter(image)
                        painter.scale(dpr, dpr)
                        painter.translate(-bounds.topLeft())
                        sprite.paint(painter)
                        painter.end()
                        mask = QRegion(QBitmap.fromImage(image.createAlphaMask()))
                        cached = (key, mask)
                    live[sprite] = cached
                    region |= cached[1].translated(QPoint(round(bounds.x() * dpr), round(bounds.y() * dpr)))
            self.cache = live
            window_id = int(window.winId())
            signature = (window_id, region)
            if signature == self.last:
                return
            rectangles = list(region)
            raw = (_Rectangle * len(rectangles))(*[
                _Rectangle(rect.x(), rect.y(), rect.width(), rect.height()) for rect in rectangles])
            # ShapeInput=2, ShapeSet=0, Unsorted=0. Bounding/visual shape stays intact.
            self.native.shape.XShapeCombineRectangles(
                self.native.display, window_id, 2, 0, 0, raw, len(raw), 0, 0)
            self.native.x.XFlush(self.native.display)
            self.last = signature
        finally:
            self.applying = False
