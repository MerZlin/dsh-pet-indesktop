# -*- coding: utf-8 -*-
"""独立灵动岛胶囊窗口。

- 常驻顶层小窗，可显示图标、名称、信息槽、状态灯；
- 支持拖拽、屏幕顶部吸附、位置持久化；
- 单击胶囊切换桌宠显示/隐藏（由应用层连接 clicked 信号）。
"""
from __future__ import annotations

from PySide6.QtCore import QPoint, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QGuiApplication, QPainter
from PySide6.QtWidgets import QApplication, QWidget

from . import catalog

_CAPSULE_HEIGHT = 44
_EDGE_MARGIN = 16
_SNAP_TOP_THRESHOLD = 24


def _cfg_dict(config) -> dict:
    value = config.get("dynamic_island", {})
    return value if isinstance(value, dict) else {}


class DynamicIsland(QWidget):
    """胶囊形态的独立灵动岛窗口。"""

    clicked = Signal()

    def __init__(self, config, parent=None):
        super().__init__(parent)
        self.config = config
        self._cfg = _cfg_dict(config)
        self.setObjectName("dynamic-island")
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

        self._drag_offset: QPoint | None = None
        self._dragging = False
        self._press_global: QPoint | None = None
        self._balance_tier_text = "余额峰谷 --"
        self._balance_text = "余额 --"

        self._info_timer = QTimer(self)
        self._info_timer.setInterval(30_000)
        self._info_timer.timeout.connect(self.update)
        self._info_timer.start()

        self._apply_position()

    # ------------------------------------------------------------ 对外
    def set_balance_info(self, tier_text: str, balance_text: str) -> None:
        self._balance_tier_text = str(tier_text or "余额峰谷 --")
        self._balance_text = str(balance_text or "余额 --")
        self.update()

    def refresh_from_config(self) -> None:
        self._cfg = _cfg_dict(self.config)
        self._apply_position()
        self.update()

    # ------------------------------------------------------------ 内部
    def _visible_parts(self) -> tuple[bool, bool, bool, bool]:
        c = self._cfg
        icon = bool(c.get("show_icon", True))
        name = bool(c.get("show_name", True))
        info = bool(c.get("show_info", True))
        status = bool(c.get("show_status", True))
        if not (icon or name or info or status):
            info = True
        return icon, name, info, status

    def _info_text(self) -> str:
        mode = str(self._cfg.get("info_mode") or "time")
        if mode == "custom":
            return str(self._cfg.get("custom_text") or "").strip() or "自定义"
        if mode == "balance_tier":
            return self._balance_tier_text
        if mode == "balance":
            return self._balance_text
        from PySide6.QtCore import QTime

        return QTime.currentTime().toString("HH:mm")

    def _character_name(self) -> str:
        character_id = str(self.config.get("character", catalog.DEFAULT_CHARACTER))
        return self.config.character_alias(character_id) or character_id

    def _update_size(self) -> None:
        icon, name, info, status = self._visible_parts()
        fm = self.fontMetrics()
        width = 28  # 左右内边距
        if icon:
            width += 28 + 8
        if name:
            width += fm.horizontalAdvance(self._character_name()) + 8
        if info:
            width += fm.horizontalAdvance(self._info_text()) + 8
        if status:
            width += 8 + 6
        width += 12
        self.setFixedSize(max(120, width), _CAPSULE_HEIGHT)

    def _apply_position(self) -> None:
        self._update_size()
        x = self._cfg.get("x")
        y = self._cfg.get("y")
        screen = QGuiApplication.primaryScreen()
        available = screen.availableGeometry() if screen is not None else None
        if isinstance(x, (int, float)) and isinstance(y, (int, float)):
            pos = QPoint(int(x), int(y))
        else:
            pos = QPoint(available.right() - self.width() - _EDGE_MARGIN, available.top() + _EDGE_MARGIN) if available else QPoint(100, 100)
        if available is not None:
            pos.setX(max(available.left(), min(pos.x(), available.right() - self.width() + 1)))
            pos.setY(max(available.top(), min(pos.y(), available.bottom() - self.height() + 1)))
        self.move(pos)

    def _save_position(self) -> None:
        island = dict(self._cfg)
        island["x"] = self.x()
        island["y"] = self.y()
        self._cfg = island
        self.config.set("dynamic_island", island)
        self.config.save()

    # ------------------------------------------------------------ 绘制
    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(0, 0, self.width(), self.height())
        radius = rect.height() / 2.0

        # 柔和阴影：底层半透明圆角矩形
        shadow = rect.translated(0, 2)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(0, 0, 0, 46))
        painter.drawRoundedRect(shadow, radius, radius)

        # 胶囊主体
        painter.setBrush(QColor(28, 30, 38, 235))
        painter.drawRoundedRect(rect, radius, radius)

        icon, name, info, status = self._visible_parts()
        x = 16.0
        painter.setPen(Qt.PenStyle.NoPen)
        if icon:
            painter.setBrush(QColor(64, 184, 255, 255))
            painter.drawEllipse(QRectF(x, (self.height() - 26) / 2, 26, 26))
            painter.setPen(QColor(255, 255, 255))
            fm = self.fontMetrics()
            icon_text = "🐟"
            painter.drawText(
                QRectF(x, (self.height() - fm.height()) / 2 - 1, 26, fm.height()),
                Qt.AlignmentFlag.AlignCenter,
                icon_text,
            )
            painter.setPen(Qt.PenStyle.NoPen)
            x += 26 + 8

        painter.setPen(QColor(235, 238, 245))
        if name:
            text = self._character_name()
            painter.drawText(
                QRectF(x, 0, self.fontMetrics().horizontalAdvance(text), self.height()),
                Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                text,
            )
            x += self.fontMetrics().horizontalAdvance(text) + 8

        if info:
            info_text = self._info_text()
            painter.setPen(QColor(160, 170, 190))
            painter.drawText(
                QRectF(x, 0, self.fontMetrics().horizontalAdvance(info_text), self.height()),
                Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                info_text,
            )
            x += self.fontMetrics().horizontalAdvance(info_text) + 8

        if status:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(80, 220, 120))
            painter.drawEllipse(QRectF(x, (self.height() - 6) / 2, 6, 6))

        painter.end()

    # ------------------------------------------------------------ 鼠标
    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self._press_global = event.globalPosition().toPoint()
            self._drag_offset = self._press_global - self.pos()
            self._dragging = False
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        if self._press_global is None or event.buttons() & Qt.MouseButton.LeftButton == 0:
            return
        global_pos = event.globalPosition().toPoint()
        if not self._dragging and (global_pos - self._press_global).manhattanLength() >= QApplication.startDragDistance():
            self._dragging = True
        if self._dragging and self._drag_offset is not None:
            self.move(global_pos - self._drag_offset)
            event.accept()

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if event.button() != Qt.MouseButton.LeftButton:
            return
        if not self._dragging:
            self.clicked.emit()
        else:
            available = QGuiApplication.primaryScreen().availableGeometry() if QGuiApplication.primaryScreen() else None
            if available is not None:
                x = max(available.left(), min(self.x(), available.right() - self.width() + 1))
                y = max(available.top(), min(self.y(), available.bottom() - self.height() + 1))
                if y <= available.top() + _SNAP_TOP_THRESHOLD:
                    y = available.top()
                self.move(x, y)
            self._save_position()
        self._press_global = None
        self._drag_offset = None
        self._dragging = False
        event.accept()
