# -*- coding: utf-8 -*-
"""桌面右下角自绘通知（不依赖托盘气泡/系统 Toast）。

QSystemTrayIcon.showMessage 在 Windows 托盘图标被收进“隐藏的图标”时可能不会
弹窗。为了确保“切走窗口也能在右下角看到通知”，这里提供一个轻量置顶自绘气泡，
点击可跳回调用方指定的页面。
"""
from __future__ import annotations

from PySide6.QtCore import QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QGuiApplication, QPainter, QPen
from PySide6.QtWidgets import QWidget


class DesktopNotification(QWidget):
    """右下角常驻/限时通知气泡。"""

    clicked = Signal()

    _HEIGHT = 68
    _MARGIN = 16
    _GAP = 10

    def __init__(self, title: str, message: str, *, on_click=None, duration_ms: int = 5000, parent=None):
        super().__init__(parent)
        self._title = str(title or "")
        self._message = str(message or "")
        self._on_click = on_click
        self.setObjectName("desktop-notification")
        flags = (
            Qt.WindowType.Tool
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.WindowDoesNotAcceptFocus
        )
        self.setWindowFlags(flags)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        if hasattr(Qt.WidgetAttribute, "WA_MacAlwaysShowToolWindow"):
            self.setAttribute(Qt.WidgetAttribute.WA_MacAlwaysShowToolWindow, True)

        self._closed = False
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self.close)
        self._timer.start(max(1500, int(duration_ms)))

        self._resize_for_text()

    # ------------------------------------------------------------ 对外
    def activate_click(self) -> None:
        self.clicked.emit()
        if callable(self._on_click):
            try:
                self._on_click()
            finally:
                self.close()

    # ------------------------------------------------------------ 尺寸与绘制
    def _resize_for_text(self) -> None:
        fm = self.fontMetrics()
        title_w = fm.horizontalAdvance(self._title) if self._title else 0
        message_w = fm.horizontalAdvance(self._message) if self._message else 0
        width = max(240, min(420, max(title_w, message_w) + 48))
        self.setFixedSize(width, self._HEIGHT)

    def _style_colors(self):
        return QColor(30, 32, 38, 242), QColor(240, 244, 250), QColor(160, 170, 190)

    def paintEvent(self, event) -> None:  # noqa: N802
        del event
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(1, 1, self.width() - 2, self.height() - 2)
        bg, fg, secondary = self._style_colors()

        shadow = QRectF(0, 3, self.width(), self.height())
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(0, 0, 0, 70))
        painter.drawRoundedRect(shadow, 14, 14)

        painter.setBrush(bg)
        painter.setPen(QPen(QColor(255, 255, 255, 26), 1))
        painter.drawRoundedRect(rect, 13, 13)

        x = 16.0
        y = 12.0
        painter.setPen(fg)
        if self._title:
            fm = self.fontMetrics()
            painter.drawText(
                QRectF(x, y, self.width() - 32, fm.height()),
                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                self._title,
            )
            y += fm.height() + 2
        if self._message:
            fm = self.fontMetrics()
            painter.setPen(secondary)
            painter.drawText(
                QRectF(x, y, self.width() - 32, fm.height() * 2),
                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop,
                self._message,
            )
        painter.end()

    # ------------------------------------------------------------ 鼠标
    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self.activate_click()
            event.accept()
            return
        super().mousePressEvent(event)

    def closeEvent(self, event) -> None:  # noqa: N802
        self._timer.stop()
        self._closed = True
        super().closeEvent(event)

    def is_closed(self) -> bool:
        return self._closed

    def position_bottom_right(self, screen=None, stack_index: int = 0) -> None:
        screen = screen or QGuiApplication.primaryScreen()
        if screen is None:
            return
        available = screen.availableGeometry()
        x = available.right() - self.width() - self._MARGIN
        y = available.bottom() - self.height() - self._MARGIN
        if stack_index:
            y -= (self.height() + self._GAP) * stack_index
        # 防止多通知把第一屏顶部推出屏幕
        y = max(available.top() + self._MARGIN, y)
        self.move(x, y)


def position_stack(windows: list[QWidget]) -> None:
    """把一组通知从右下角向上排列。"""
    screen = QGuiApplication.primaryScreen()
    if screen is None:
        return
    alive = [w for w in windows if not (hasattr(w, "is_closed") and w.is_closed())]
    for index, win in enumerate(alive):
        if isinstance(win, DesktopNotification):
            win.position_bottom_right(screen, index)
