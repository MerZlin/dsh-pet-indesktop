# -*- coding: utf-8 -*-
"""Palette-aware icons shared by both context-menu themes."""
from __future__ import annotations

from math import cos, pi, sin

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QBitmap, QBrush, QColor, QIcon, QPainter, QPainterPath, QPen, QPixmap, QPolygonF, QRegion
from PySide6.QtWidgets import QMenu, QStyle


def _icon_theme(widget) -> tuple[str, bool]:
    """Resolve (menuStyle, modernDark) from the widget or its nearest ancestor.

    Settings and chat windows pin ``menuStyle=modern`` on the container so every
    icon rendered inside inherits the outline colour (#595959 on light surfaces,
    #d6d6d6 on dark ones) instead of the app palette. The app palette follows
    the OS theme: on a dark system its foreground is white, which painted white
    glyphs on the QSS-light chat surfaces (invisible close/minimize/add icons).
    Individual widgets may still flip ``modernDark`` (e.g. the accent send
    button) without restating the style.
    """
    style = ""
    dark = False
    current = widget
    while current is not None:
        value = str(current.property("menuStyle") or "")
        if value:
            style = value
        if current.property("modernDark"):
            dark = True
        if style and dark:
            break
        current = current.parent()
    return style, dark


def small_icon_size(menu: QMenu) -> int:
    style, _dark = _icon_theme(menu)
    if style == "modern":
        return 18
    return max(12, int(menu.style().pixelMetric(QStyle.PixelMetric.PM_SmallIconSize, None, menu)))


def _new_icon_canvas(widget, requested_size: int | None = None):
    size = requested_size or small_icon_size(widget)
    dpr = widget.devicePixelRatioF() or 1.0
    pixmap = QPixmap(max(1, round(size * dpr)), max(1, round(size * dpr)))
    pixmap.setDevicePixelRatio(dpr)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    painter.scale(size / 16.0, size / 16.0)
    style, dark = _icon_theme(widget)
    color = (
        QColor("#d6d6d6" if dark else "#595959")
        if style == "modern"
        else widget.palette().color(widget.foregroundRole())
    )
    painter.setPen(QPen(color, 1.35, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
    painter.setBrush(Qt.BrushStyle.NoBrush)
    return pixmap, painter, color


def vector_menu_icon(menu: QMenu, name: str, size: int | None = None) -> QIcon:
    """Draw a crisp 16-unit semantic icon using the current menu palette."""
    pixmap, painter, color = _new_icon_canvas(menu, size)
    path = QPainterPath()

    if name == "search":
        painter.drawEllipse(QPointF(6.5, 6.5), 4.5, 4.5)
        painter.drawLine(QPointF(9.8, 9.8), QPointF(14.0, 14.0))
    elif name == "chat":
        painter.drawRoundedRect(QRectF(1.5, 2.0, 13.0, 9.5), 2.4, 2.4)
        path.moveTo(5.2, 11.2); path.lineTo(4.0, 14.0); path.lineTo(7.4, 11.5)
        painter.drawPath(path)
        painter.drawLine(QPointF(4.6, 6.7), QPointF(11.4, 6.7))
    elif name == "screen":
        painter.drawRoundedRect(QRectF(1.5, 2.5, 13.0, 9.0), 1.6, 1.6)
        painter.drawLine(QPointF(6.0, 13.5), QPointF(10.0, 13.5))
        painter.drawLine(QPointF(8.0, 11.5), QPointF(8.0, 13.5))
        painter.drawEllipse(QPointF(8.0, 7.0), 2.2, 1.5)
    elif name == "hide":
        painter.drawEllipse(QPointF(8.0, 8.0), 6.0, 3.8)
        painter.drawEllipse(QPointF(8.0, 8.0), 1.5, 1.5)
        painter.drawLine(QPointF(2.5, 13.5), QPointF(13.5, 2.5))
    elif name == "balance":
        painter.drawEllipse(QPointF(8.0, 8.0), 6.0, 6.0)
        painter.drawLine(QPointF(5.0, 5.5), QPointF(11.0, 5.5))
        painter.drawLine(QPointF(8.0, 4.0), QPointF(8.0, 12.0))
        painter.drawArc(QRectF(5.2, 5.0, 5.6, 6.0), 70 * 16, 220 * 16)
    elif name == "update":
        painter.drawArc(QRectF(2.0, 2.0, 12.0, 12.0), 35 * 16, 285 * 16)
        painter.drawPolygon(QPolygonF([QPointF(10.7, 1.8), QPointF(14.0, 2.5), QPointF(12.0, 5.3)]))
    elif name == "download":
        painter.drawLine(QPointF(8.0, 2.0), QPointF(8.0, 10.0))
        painter.drawLine(QPointF(4.8, 7.0), QPointF(8.0, 10.2))
        painter.drawLine(QPointF(11.2, 7.0), QPointF(8.0, 10.2))
        painter.drawLine(QPointF(3.0, 13.5), QPointF(13.0, 13.5))
    elif name == "settings":
        gear = QPolygonF()
        for index in range(24):
            angle = -pi / 2 + index * pi / 12
            radius = 6.2 if index % 3 == 1 else 5.15
            gear.append(QPointF(8.0 + cos(angle) * radius, 8.0 + sin(angle) * radius))
        painter.drawPolygon(gear)
        painter.drawEllipse(QPointF(8.0, 8.0), 2.15, 2.15)
    elif name == "play":
        painter.drawPolygon(QPolygonF([QPointF(4.0, 2.5), QPointF(13.0, 8.0), QPointF(4.0, 13.5)]))
    elif name == "speed":
        painter.drawArc(QRectF(2.0, 3.0, 12.0, 12.0), 0, 180 * 16)
        painter.drawLine(QPointF(8.0, 9.0), QPointF(11.6, 5.6))
        painter.setBrush(QBrush(color)); painter.drawEllipse(QPointF(8.0, 9.0), 1.0, 1.0); painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawLine(QPointF(3.0, 11.8), QPointF(13.0, 11.8))
    elif name == "physics":
        painter.drawEllipse(QPointF(4.2, 4.0), 2.0, 2.0)
        path.moveTo(2.0, 12.8)
        path.cubicTo(5.0, 8.7, 8.8, 14.2, 14.0, 8.5)
        painter.drawPath(path)
        painter.drawLine(QPointF(11.0, 8.7), QPointF(14.0, 8.5))
        painter.drawLine(QPointF(13.7, 8.5), QPointF(13.0, 11.4))
    elif name == "character":
        painter.drawEllipse(QPointF(8.0, 5.0), 2.8, 2.8)
        painter.drawArc(QRectF(2.8, 8.0, 10.4, 6.0), 18 * 16, 144 * 16)
    elif name == "corner":
        painter.drawLine(QPointF(13.5, 4.0), QPointF(13.5, 13.5)); painter.drawLine(QPointF(4.0, 13.5), QPointF(13.5, 13.5))
        painter.drawLine(QPointF(3.0, 3.0), QPointF(10.5, 10.5)); painter.drawLine(QPointF(6.5, 10.5), QPointF(10.5, 10.5)); painter.drawLine(QPointF(10.5, 6.5), QPointF(10.5, 10.5))
    elif name == "pin":
        painter.drawRoundedRect(QRectF(4.5, 2.0, 7.0, 5.0), 1.0, 1.0)
        painter.drawLine(QPointF(5.5, 7.0), QPointF(3.5, 10.0)); painter.drawLine(QPointF(10.5, 7.0), QPointF(12.5, 10.0))
        painter.drawLine(QPointF(3.5, 10.0), QPointF(12.5, 10.0)); painter.drawLine(QPointF(8.0, 10.0), QPointF(8.0, 14.0))
    elif name == "pause":
        painter.drawRoundedRect(QRectF(3.5, 2.5, 3.2, 11.0), 0.7, 0.7); painter.drawRoundedRect(QRectF(9.3, 2.5, 3.2, 11.0), 0.7, 0.7)
    elif name == "spawn":
        # A small fish is more semantic than a coloured avatar and keeps the
        # modern menu's monochrome outline language coherent.
        body = QPainterPath()
        body.moveTo(3.0, 8.0)
        body.cubicTo(5.2, 3.9, 10.8, 3.9, 13.0, 8.0)
        body.cubicTo(10.8, 12.1, 5.2, 12.1, 3.0, 8.0)
        painter.drawPath(body)
        painter.drawPolygon(QPolygonF([QPointF(3.2, 8.0), QPointF(1.2, 4.8), QPointF(1.2, 11.2)]))
        painter.drawPoint(QPointF(10.5, 7.0))
    elif name == "autostart":
        painter.drawArc(QRectF(2.0, 2.0, 12.0, 12.0), 38 * 16, 282 * 16)
        painter.setBrush(QBrush(color)); painter.drawPolygon(QPolygonF([QPointF(10.7, 1.9), QPointF(14.0, 2.5), QPointF(12.1, 5.3)])); painter.setBrush(Qt.BrushStyle.NoBrush)
    elif name == "size":
        painter.drawLine(QPointF(3.0, 13.0), QPointF(13.0, 3.0))
        painter.drawLine(QPointF(3.0, 8.5), QPointF(3.0, 13.0)); painter.drawLine(QPointF(7.5, 13.0), QPointF(3.0, 13.0))
        painter.drawLine(QPointF(8.5, 3.0), QPointF(13.0, 3.0)); painter.drawLine(QPointF(13.0, 3.0), QPointF(13.0, 7.5))
    elif name == "harness":
        painter.drawRoundedRect(QRectF(1.5, 2.5, 13.0, 11.0), 1.5, 1.5)
        painter.drawLine(QPointF(4.0, 6.0), QPointF(6.0, 8.0)); painter.drawLine(QPointF(6.0, 8.0), QPointF(4.0, 10.0)); painter.drawLine(QPointF(8.0, 10.0), QPointF(11.5, 10.0))
    elif name == "web":
        painter.drawEllipse(QPointF(8.0, 8.0), 6.0, 6.0)
        painter.drawEllipse(QPointF(8.0, 8.0), 2.7, 6.0)
        painter.drawLine(QPointF(2.3, 8.0), QPointF(13.7, 8.0))
    elif name == "template":
        painter.drawRoundedRect(QRectF(1.5, 2.0, 5.0, 12.0), 1.0, 1.0)
        painter.drawRoundedRect(QRectF(8.0, 2.0, 6.5, 5.0), 1.0, 1.0); painter.drawRoundedRect(QRectF(8.0, 8.5, 6.5, 5.5), 1.0, 1.0)
    elif name in {"application", "launcher"}:
        painter.drawRoundedRect(QRectF(2.0, 2.0, 12.0, 12.0), 2.0, 2.0)
        for x, y in ((5.0, 5.0), (11.0, 5.0), (5.0, 11.0), (11.0, 11.0)):
            painter.drawEllipse(QPointF(x, y), 1.15, 1.15)
    elif name == "appearance":
        painter.drawEllipse(QPointF(8.0, 8.0), 5.8, 5.8)
        painter.drawArc(QRectF(4.0, 4.0, 8.0, 8.0), 90 * 16, 180 * 16)
        painter.drawLine(QPointF(8.0, 2.2), QPointF(8.0, 13.8))
    elif name in {"back", "save"}:
        painter.drawLine(QPointF(13.5, 8.0), QPointF(3.0, 8.0))
        painter.drawLine(QPointF(3.0, 8.0), QPointF(7.0, 4.0))
        painter.drawLine(QPointF(3.0, 8.0), QPointF(7.0, 12.0))
    elif name == "minimize":
        painter.drawLine(QPointF(3.0, 11.5), QPointF(13.0, 11.5))
    elif name == "clear":
        painter.drawLine(QPointF(3.0, 12.5), QPointF(13.0, 12.5))
        painter.drawLine(QPointF(5.0, 12.5), QPointF(5.0, 5.0))
        painter.drawLine(QPointF(11.0, 12.5), QPointF(11.0, 5.0))
        painter.drawLine(QPointF(3.8, 5.0), QPointF(12.2, 5.0))
        painter.drawLine(QPointF(6.0, 2.8), QPointF(10.0, 2.8))
    elif name == "add":
        painter.drawEllipse(QPointF(8.0, 8.0), 6.0, 6.0)
        painter.drawLine(QPointF(8.0, 4.5), QPointF(8.0, 11.5)); painter.drawLine(QPointF(4.5, 8.0), QPointF(11.5, 8.0))
    elif name == "remove":
        painter.drawLine(QPointF(3.0, 3.0), QPointF(13.0, 13.0)); painter.drawLine(QPointF(13.0, 3.0), QPointF(3.0, 13.0))
    elif name == "more":
        painter.setBrush(QBrush(color))
        for x in (4.0, 8.0, 12.0): painter.drawEllipse(QPointF(x, 8.0), 0.9, 0.9)
        painter.setBrush(Qt.BrushStyle.NoBrush)
    elif name == "multi_select":
        painter.drawEllipse(QPointF(4.0, 4.5), 1.5, 1.5)
        painter.drawEllipse(QPointF(4.0, 11.5), 1.5, 1.5)
        painter.drawLine(QPointF(7.5, 4.5), QPointF(13.5, 4.5))
        painter.drawLine(QPointF(7.5, 11.5), QPointF(13.5, 11.5))
    elif name == "rename" or name == "edit":
        # Document + pencil is recognisable even at native 16 px menu size;
        # the previous three-line mark looked like an unexplained hook.
        painter.drawRoundedRect(QRectF(2.2, 2.0, 8.3, 11.5), 1.2, 1.2)
        painter.drawLine(QPointF(4.2, 5.0), QPointF(8.2, 5.0))
        painter.drawLine(QPointF(4.2, 7.4), QPointF(7.0, 7.4))
        painter.setBrush(QBrush(color))
        pencil = QPolygonF([
            QPointF(6.8, 12.8), QPointF(7.5, 10.2),
            QPointF(12.6, 5.1), QPointF(14.2, 6.7), QPointF(9.1, 11.8),
        ])
        painter.drawPolygon(pencil)
        painter.setBrush(Qt.BrushStyle.NoBrush)
    elif name == "copy":
        painter.drawRoundedRect(QRectF(5.0, 3.0, 8.0, 9.0), 1.3, 1.3)
        painter.drawRoundedRect(QRectF(2.5, 5.5, 8.0, 8.0), 1.3, 1.3)
    elif name == "retry":
        painter.drawArc(QRectF(2.4, 2.4, 11.2, 11.2), 35 * 16, 285 * 16)
        painter.drawLine(QPointF(10.8, 2.8), QPointF(13.8, 3.2)); painter.drawLine(QPointF(13.8, 3.2), QPointF(12.4, 6.0))
    elif name in {"thumbs_up", "thumbs_down"}:
        if name == "thumbs_down": painter.scale(1.0, -1.0); painter.translate(0, -16)
        path.moveTo(3.0, 7.0); path.lineTo(6.0, 7.0); path.lineTo(8.2, 3.0)
        path.cubicTo(8.8, 1.9, 10.1, 2.5, 10.0, 3.8); path.lineTo(9.8, 6.0)
        path.lineTo(13.0, 6.0); path.lineTo(12.0, 13.0); path.lineTo(6.0, 13.0); path.lineTo(3.0, 11.5); path.closeSubpath(); painter.drawPath(path)
    elif name == "attach":
        path.moveTo(5.1, 8.9); path.lineTo(9.5, 4.5); path.cubicTo(12.2, 1.8, 15.1, 5.0, 12.7, 7.4)
        path.lineTo(7.0, 13.1); path.cubicTo(3.0, 17.1, -0.7, 12.3, 2.5, 9.1); path.lineTo(8.1, 3.5); painter.drawPath(path)
    elif name == "send":
        painter.drawPolygon(QPolygonF([QPointF(2.0, 8.0), QPointF(13.5, 2.5), QPointF(10.8, 13.5), QPointF(7.7, 9.0)]))
        painter.drawLine(QPointF(2.0, 8.0), QPointF(7.7, 9.0)); painter.drawLine(QPointF(7.7, 9.0), QPointF(13.5, 2.5))
    elif name == "stop":
        painter.drawRoundedRect(QRectF(4.0, 4.0, 8.0, 8.0), 1.3, 1.3)
    elif name == "sidebar":
        painter.drawRoundedRect(QRectF(2.0, 2.5, 12.0, 11.0), 2.0, 2.0)
        painter.drawLine(QPointF(6.1, 2.8), QPointF(6.1, 13.2))
        painter.drawLine(QPointF(3.8, 5.5), QPointF(4.7, 5.5))
        painter.drawLine(QPointF(3.8, 8.0), QPointF(4.7, 8.0))
    elif name in {"tools", "functions"}:
        painter.drawRoundedRect(QRectF(1.5, 2.0, 13.0, 12.0), 2.0, 2.0)
        painter.drawLine(QPointF(5.0, 5.0), QPointF(11.0, 11.0)); painter.drawEllipse(QPointF(4.5, 4.5), 1.5, 1.5); painter.drawEllipse(QPointF(11.5, 11.5), 1.5, 1.5)
    elif name == "exit":
        painter.drawLine(QPointF(3.0, 3.0), QPointF(13.0, 13.0)); painter.drawLine(QPointF(13.0, 3.0), QPointF(3.0, 13.0))
    else:
        painter.drawEllipse(QPointF(8.0, 8.0), 5.5, 5.5)

    painter.end()
    return QIcon(pixmap)


def vector_widget_icon(widget, name: str, size: int = 18) -> QIcon:
    """Render the same icon language for sidebar and form widgets."""
    return vector_menu_icon(widget, name, size)


def pet_avatar_menu_icon(menu: QMenu, pet) -> QIcon:
    """Fill the native icon slot while neutralising source Retina DPR metadata."""
    size = small_icon_size(menu)
    dpr = menu.devicePixelRatioF() or 1.0
    source = pet.icon_pixmap(max(32, round(size * dpr * 2)))
    return fitted_pet_pixmap_icon(menu, source)


def animation_avatar_menu_icon(menu: QMenu, pet, animation_name: str) -> QIcon:
    size = small_icon_size(menu)
    dpr = menu.devicePixelRatioF() or 1.0
    render = getattr(pet, "animation_icon_pixmap", None)
    source = render(animation_name, max(32, round(size * dpr * 2))) if callable(render) else None
    if source is None or not hasattr(source, "isNull"):
        source = pet.icon_pixmap(max(32, round(size * dpr * 2)))
    return fitted_pet_pixmap_icon(menu, source)


def fitted_pet_pixmap_icon(menu: QMenu, source: QPixmap) -> QIcon:
    size = small_icon_size(menu)
    dpr = menu.devicePixelRatioF() or 1.0
    if source is None or source.isNull():
        return QIcon()
    # A frame pixmap may retain DPR=2. Drawing it by point makes Qt interpret a
    # 32px source as only 16 logical px and was the cause of the tiny avatar.
    source.setDevicePixelRatio(1.0)
    # Animation frames use the full video canvas. Scaling that canvas directly
    # reduced the visible character to only a few pixels in the 18px menu slot.
    # Crop transparent padding first, exactly as the runtime pet icon does.
    image = source.toImage()
    bounds = QRegion(QBitmap.fromImage(image.createAlphaMask())).boundingRect()
    if bounds.isValid() and not bounds.isEmpty():
        source = QPixmap.fromImage(image.copy(bounds))
    canvas = QPixmap(max(1, round(size * dpr)), max(1, round(size * dpr)))
    canvas.setDevicePixelRatio(dpr)
    canvas.fill(Qt.GlobalColor.transparent)
    painter = QPainter(canvas)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
    extent = size * 1.04
    ratio = min(extent / source.width(), extent / source.height())
    width = source.width() * ratio
    height = source.height() * ratio
    target = QRectF((size - width) / 2.0, (size - height) / 2.0 - 0.2, width, height)
    painter.drawPixmap(target, source, QRectF(0, 0, source.width(), source.height()))
    painter.end()
    return QIcon(canvas)
