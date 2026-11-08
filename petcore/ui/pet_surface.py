"""A pet's logical canvas can extend past a WM-constrained native viewport.

Python geometry methods expose the logical canvas to existing motion/collision
code. Native geometry is accessed explicitly through QWidget in this adapter.
All event-local coordinates are converted once at the PetWindow entry points.
"""
from __future__ import annotations

import sys

from PySide6.QtCore import QPoint, QRect, QSize
from PySide6.QtGui import QGuiApplication, QMouseEvent
from PySide6.QtWidgets import QWidget

from ..engine.viewport import viewport_for
from ..platform.screens import screen_for_rect, snapshot_from_qt


class PetSurface(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._viewport_enabled = sys.platform == "linux" and QGuiApplication.platformName() == "xcb"
        self._canvas_pos = QWidget.pos(self)
        self._canvas_size = QWidget.size(self)
        self._draw_offset = QPoint()

    def pos(self):
        return QPoint(self._canvas_pos) if self._viewport_enabled else super().pos()

    def x(self):
        return self.pos().x()

    def y(self):
        return self.pos().y()

    def size(self):
        return QSize(self._canvas_size) if self._viewport_enabled else super().size()

    def width(self):
        return self.size().width()

    def height(self):
        return self.size().height()

    def rect(self):
        return QRect(QPoint(), self.size())

    def geometry(self):
        return QRect(self.pos(), self.size()) if self._viewport_enabled else super().geometry()

    def frameGeometry(self):
        return self.geometry() if self._viewport_enabled else super().frameGeometry()

    def setFixedSize(self, *args):
        self._canvas_size = QSize(*args)
        if self._viewport_enabled:
            self.move(self.pos())
        else:
            super().setFixedSize(*args)

    def move(self, *args):
        position = QPoint(*args)
        self._canvas_pos = position
        if not self._viewport_enabled:
            return super().move(position)
        layout = getattr(self, "_layout", None) or snapshot_from_qt()
        width, height = self._canvas_size.width(), self._canvas_size.height()
        bx, by, bw, bh = getattr(self, "_body_local", (0, 0, width, height))
        screen = screen_for_rect((position.x() + bx, position.y() + by, bw, bh), layout)
        canvas = (position.x(), position.y(), width, height)
        viewport = viewport_for(canvas, screen.rect if screen else canvas)
        x, y, vw, vh = viewport.native
        self._draw_offset = QPoint(*viewport.offset)
        QWidget.setFixedSize(self, vw, vh)
        QWidget.move(self, x, y)
        self.update()
        # Native window may not move while the logical body moves along an edge.
        if hasattr(self, "_bubble_follow_pending"):
            self._schedule_bubble_follow()
            self.pet_moved.emit()

    def mapFromGlobal(self, point):
        return point - self.pos() if self._viewport_enabled else super().mapFromGlobal(point)

    def mapToGlobal(self, point):
        return point + self.pos() if self._viewport_enabled else super().mapToGlobal(point)

    def logical_mouse_event(self, event):
        if not self._viewport_enabled or self._draw_offset.isNull():
            return event
        return QMouseEvent(event.type(), event.position() - self._draw_offset,
                           event.globalPosition(), event.button(), event.buttons(), event.modifiers())
