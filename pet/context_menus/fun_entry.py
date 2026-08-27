# -*- coding: utf-8 -*-
"""Custom first row for the playful modern-menu image action."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QCursor, QFontMetrics, QMouseEvent, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import QHBoxLayout, QLabel, QMenu, QWidget, QWidgetAction

from ..fun_image_popup import oijingjing_image_path, open_ojingjing_window, resolve_fun_asset


def _circle_photo(path: Path, size: int, dpr: float) -> QPixmap:
    canvas = QPixmap(round(size * dpr), round(size * dpr))
    canvas.setDevicePixelRatio(dpr)
    canvas.fill(Qt.GlobalColor.transparent)
    source = QPixmap(str(path))
    painter = QPainter(canvas)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
    clip = QPainterPath()
    clip.addEllipse(QRectF(0.5, 0.5, size - 1.0, size - 1.0))
    painter.setClipPath(clip)
    crop = min(source.width(), source.height())
    source_rect = QRectF((source.width() - crop) / 2, (source.height() - crop) / 2, crop, crop)
    painter.drawPixmap(QRectF(0, 0, size, size), source, source_rect)
    painter.end()
    return canvas


class ClickAccessory(QWidget):
    def __init__(self, parent: QWidget, text: str = "请点击") -> None:
        super().__init__(parent)
        self.setObjectName("ojingjingClickAccessory")
        self._text = str(text or "请点击")[:20]
        self.setProperty("text", self._text)
        self.setFixedSize(54, 20)
        font = parent.font()
        pixel_size = font.pixelSize() if font.pixelSize() > 0 else 13
        font.setPixelSize(max(9, pixel_size - 2))
        self.setFont(font)

    def displayText(self) -> str:  # noqa: N802 - Qt-style diagnostic API
        return QFontMetrics(self.font()).elidedText(
            self._text, Qt.TextElideMode.ElideRight, 39,
        )

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt API
        del event
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        color = QColor("#777777")
        painter.setPen(
            QPen(
                color,
                1.25,
                Qt.PenStyle.SolidLine,
                Qt.PenCapStyle.RoundCap,
                Qt.PenJoinStyle.RoundJoin,
            )
        )
        pointer = QPainterPath()
        pointer.moveTo(2.5, 3.0)
        pointer.lineTo(10.5, 10.0)
        pointer.lineTo(7.0, 10.7)
        pointer.lineTo(9.2, 15.2)
        pointer.lineTo(6.8, 16.4)
        pointer.lineTo(4.7, 11.9)
        pointer.lineTo(2.5, 14.5)
        pointer.closeSubpath()
        painter.drawPath(pointer)
        painter.setFont(self.font())
        painter.drawText(QRectF(15, 0, 39, 20), Qt.AlignmentFlag.AlignVCenter, self.displayText())


class ElidedLabel(QLabel):
    def __init__(self, text: str, parent=None) -> None:
        self._full_text = str(text)
        super().__init__("", parent)

    def displayText(self) -> str:  # noqa: N802 - Qt-style diagnostic API
        return self.fontMetrics().elidedText(
            self._full_text, Qt.TextElideMode.ElideRight, max(1, self.width()),
        )

    def resizeEvent(self, event) -> None:  # noqa: N802
        self.setText(self.displayText())
        super().resizeEvent(event)

    def setFont(self, font) -> None:  # noqa: N802
        super().setFont(font)
        self.setText(self.displayText())


class OjingjingMenuEntry(QWidget):
    clicked = Signal()

    def __init__(self, menu: QMenu, config: dict | None = None) -> None:
        super().__init__(menu)
        self._config = dict(config or {})
        self._menu = menu
        self._hovered = False
        self.setObjectName("ojingjingMenuEntry")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setMouseTracking(True)
        self.setMinimumWidth(224)
        self.setFixedHeight(39)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 2, 8, 2)
        layout.setSpacing(6)
        avatar = QLabel(self)
        avatar.setObjectName("ojingjingAvatar")
        avatar.setFixedSize(27, 27)
        avatar_path = resolve_fun_asset(self._config.get("avatar"), oijingjing_image_path())
        avatar.setPixmap(_circle_photo(avatar_path, 27, self.devicePixelRatioF() or 1.0))
        layout.addWidget(avatar)
        self.title_label = ElidedLabel(str(self._config.get("title") or "厉害了我的鲸"), self)
        self.title_label.setObjectName("ojingjingTitle")
        self.title_label.setFixedWidth(105)
        self.title_label.setFont(menu.font())
        layout.addWidget(self.title_label)
        layout.addStretch(1)
        self.click_accessory = ClickAccessory(self, str(self._config.get("hint") or "请点击"))
        layout.addWidget(self.click_accessory)
        self.clicked.connect(self._activate)

    def sizeHint(self) -> QSize:  # noqa: N802 - Qt API
        return QSize(224, 39)

    def enterEvent(self, event) -> None:  # noqa: N802 - Qt API
        self._hovered = True
        self.update()
        super().enterEvent(event)

    def showEvent(self, event) -> None:  # noqa: N802 - Qt API
        super().showEvent(event)
        # 菜单弹出时鼠标可能已在项上方（如顶行彩蛋项），此时没有 enter 事件，
        # 按光标位置合成初始 hover 状态
        self._hovered = self.rect().contains(self.mapFromGlobal(QCursor.pos()))
        self.update()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:  # noqa: N802 - Qt API
        # Windows 上跨子窗口（头像/文字/提示）移动可能丢失 enter/leave，
        # 鼠标移动作为 hover 的兜底恢复
        self._hovered = True
        self.update()
        super().mouseMoveEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802 - Qt API
        # 鼠标移入 avatar/文字/提示等子 widget 时也会触发 leave，
        # 光标整体仍在项内则保持高亮
        self._hovered = self.rect().contains(self.mapFromGlobal(QCursor.pos()))
        self.update()
        super().leaveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:  # noqa: N802 - Qt API
        if event.button() == Qt.MouseButton.LeftButton and self.rect().contains(event.position().toPoint()):
            self.clicked.emit()
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt API
        del event
        if not self._hovered:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor("#eeeeee"))
        painter.drawRoundedRect(QRectF(0, 0, self.width(), self.height()), 9, 9)

    def _activate(self) -> None:
        config = dict(self._config)
        menu = self._menu
        # QWidgetAction 的自绘 widget 点击不会关闭原生菜单；macOS 上
        # NSMenu 跟踪循环未结束时 show/raise 会被 AppKit 抑制（首点无效）。
        # 复用菜单关闭后回调机制：菜单仍可见时把开窗动作排到 exec() 返回后。
        if menu.isVisible():
            root = menu
            while isinstance(root.parent(), QMenu):
                root = root.parent()
            root._deferred_callbacks = list(
                getattr(root, "_deferred_callbacks", ())
            ) + [lambda config=config: open_ojingjing_window(config)]
            menu.close()
            return
        QTimer.singleShot(0, lambda config=config: open_ojingjing_window(config))


def add_ojingjing_entry(menu: QMenu, config: dict | None = None) -> QWidgetAction:
    config = dict(config or {})
    action = QWidgetAction(menu)
    action.setText(str(config.get("title") or "厉害了我的鲸"))
    action.setDefaultWidget(OjingjingMenuEntry(menu, config))
    menu.addAction(action)
    return action
