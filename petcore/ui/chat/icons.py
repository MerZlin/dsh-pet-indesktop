"""聊天窗工具图标：16 单位单色线稿，移植自主分支 ``pet/context_menus/icons.py``。

只画聊天窗口用得到的那几枚；不 import ``pet.*``。默认墨色为浅色表面上的静息灰，
``dark=True`` 换成强调色/深色底上的白。DPR 按控件的 ``devicePixelRatioF`` 生成，
1.5 倍屏不会发虚。
"""

from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPainterPath, QPen, QPixmap, QPolygonF
from PySide6.QtWidgets import QToolButton

INK_LIGHT = "#595959"
INK_DARK = "#ffffff"


def ink_color(dark: bool = False) -> QColor:
    return QColor(INK_DARK if dark else INK_LIGHT)


def _canvas(widget, size: int, dark: bool) -> tuple[QPixmap, QPainter, QColor]:
    dpr = widget.devicePixelRatioF() if widget is not None else 1.0
    pixmap = QPixmap(max(1, round(size * dpr)), max(1, round(size * dpr)))
    pixmap.setDevicePixelRatio(dpr)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    painter.scale(size / 16.0, size / 16.0)
    color = ink_color(dark)
    painter.setPen(
        QPen(
            color,
            1.35,
            Qt.PenStyle.SolidLine,
            Qt.PenCapStyle.RoundCap,
            Qt.PenJoinStyle.RoundJoin,
        )
    )
    painter.setBrush(Qt.BrushStyle.NoBrush)
    return pixmap, painter, color


def _draw(name: str, painter: QPainter, color: QColor) -> None:
    path = QPainterPath()
    if name == "add":
        painter.drawEllipse(QPointF(8.0, 8.0), 6.0, 6.0)
        painter.drawLine(QPointF(8.0, 4.5), QPointF(8.0, 11.5))
        painter.drawLine(QPointF(4.5, 8.0), QPointF(11.5, 8.0))
    elif name == "pin":
        painter.drawRoundedRect(QRectF(4.5, 2.0, 7.0, 5.0), 1.0, 1.0)
        painter.drawLine(QPointF(5.5, 7.0), QPointF(3.5, 10.0))
        painter.drawLine(QPointF(10.5, 7.0), QPointF(12.5, 10.0))
        painter.drawLine(QPointF(3.5, 10.0), QPointF(12.5, 10.0))
        painter.drawLine(QPointF(8.0, 10.0), QPointF(8.0, 14.0))
    elif name == "minimize":
        painter.drawLine(QPointF(3.0, 11.5), QPointF(13.0, 11.5))
    elif name == "exit":
        painter.drawLine(QPointF(3.0, 3.0), QPointF(13.0, 13.0))
        painter.drawLine(QPointF(13.0, 3.0), QPointF(3.0, 13.0))
    elif name == "sidebar":
        painter.drawRoundedRect(QRectF(2.0, 2.5, 12.0, 11.0), 2.0, 2.0)
        painter.drawLine(QPointF(6.1, 2.8), QPointF(6.1, 13.2))
        painter.drawLine(QPointF(3.8, 5.5), QPointF(4.7, 5.5))
        painter.drawLine(QPointF(3.8, 8.0), QPointF(4.7, 8.0))
    elif name == "send":
        painter.drawPolygon(
            QPolygonF(
                [QPointF(2.0, 8.0), QPointF(13.5, 2.5), QPointF(10.8, 13.5), QPointF(7.7, 9.0)]
            )
        )
        painter.drawLine(QPointF(2.0, 8.0), QPointF(7.7, 9.0))
        painter.drawLine(QPointF(7.7, 9.0), QPointF(13.5, 2.5))
    elif name == "stop":
        painter.drawRoundedRect(QRectF(4.0, 4.0, 8.0, 8.0), 1.3, 1.3)
    elif name == "copy":
        painter.drawRoundedRect(QRectF(5.0, 3.0, 8.0, 9.0), 1.3, 1.3)
        painter.drawRoundedRect(QRectF(2.5, 5.5, 8.0, 8.0), 1.3, 1.3)
    elif name == "attach":
        path.moveTo(5.1, 8.9)
        path.lineTo(9.5, 4.5)
        path.cubicTo(12.2, 1.8, 15.1, 5.0, 12.7, 7.4)
        path.lineTo(7.0, 13.1)
        path.cubicTo(3.0, 17.1, -0.7, 12.3, 2.5, 9.1)
        path.lineTo(8.1, 3.5)
        painter.drawPath(path)
    elif name == "clear":
        painter.drawLine(QPointF(3.0, 12.5), QPointF(13.0, 12.5))
        painter.drawLine(QPointF(5.0, 12.5), QPointF(5.0, 5.0))
        painter.drawLine(QPointF(11.0, 12.5), QPointF(11.0, 5.0))
        painter.drawLine(QPointF(3.8, 5.0), QPointF(12.2, 5.0))
        painter.drawLine(QPointF(6.0, 2.8), QPointF(10.0, 2.8))
    elif name == "remove":
        painter.drawLine(QPointF(3.0, 3.0), QPointF(13.0, 13.0))
        painter.drawLine(QPointF(13.0, 3.0), QPointF(3.0, 13.0))
    elif name == "retry":
        painter.drawArc(QRectF(2.4, 2.4, 11.2, 11.2), 35 * 16, 285 * 16)
        painter.drawLine(QPointF(10.8, 2.8), QPointF(13.8, 3.2))
        painter.drawLine(QPointF(13.8, 3.2), QPointF(12.4, 6.0))
    elif name == "mic":
        painter.drawRoundedRect(QRectF(6.0, 2.0, 4.0, 7.0), 2.0, 2.0)
        path.moveTo(3.8, 7.6)
        path.cubicTo(3.8, 12.8, 12.2, 12.8, 12.2, 7.6)
        painter.drawPath(path)
        painter.drawLine(QPointF(8.0, 12.3), QPointF(8.0, 14.2))
        painter.drawLine(QPointF(6.0, 14.2), QPointF(10.0, 14.2))
    else:
        painter.drawEllipse(QPointF(8.0, 8.0), 5.5, 5.5)


def tool_icon(widget, name: str, size: int = 16, *, dark: bool = False) -> QIcon:
    """Painter-drawn icon in the chat window's 16-unit outline language."""
    pixmap, painter, color = _canvas(widget, max(8, int(size)), bool(dark))
    try:
        _draw(name, painter, color)
    finally:
        painter.end()
    return QIcon(pixmap)


def tool_button(
    parent,
    object_name: str,
    icon_name: str,
    tooltip: str,
    *,
    dark: bool = False,
    size: int = 16,
) -> QToolButton:
    button = QToolButton(parent)
    button.setObjectName(object_name)
    button.setProperty("modernDark", bool(dark))
    button.setIcon(tool_icon(button, icon_name, size, dark=dark))
    button.setToolTip(tooltip)
    button.setAccessibleName(tooltip)
    return button
