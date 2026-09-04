# -*- coding: utf-8 -*-
"""独立灵动岛胶囊窗口。

- 常驻顶层小窗，可显示图标、名称、信息槽、状态灯；
- 支持拖拽、屏幕顶部吸附、位置持久化；
- 单击胶囊切换桌宠显示/隐藏（由应用层连接 clicked 信号）。
"""
from __future__ import annotations

from PySide6.QtCore import QPoint, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QGuiApplication, QLinearGradient, QPainter, QPen
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
        self._pet_visible = True

        self._info_timer = QTimer(self)
        self._info_timer.setInterval(30_000)
        self._info_timer.timeout.connect(self._refresh)
        self._info_timer.start()

        # 余额峰谷档位切换的准点刷新：单次定时到下一切换边界，刷新后再排下一次。
        self._tier_tick_timer = QTimer(self)
        self._tier_tick_timer.setSingleShot(True)
        self._tier_tick_timer.timeout.connect(self._on_balance_tier_tick)
        self._schedule_next_balance_tier_refresh()

        self._apply_position()

    # ------------------------------------------------------------ 对外
    def set_balance_info(self, tier_text: str, balance_text: str) -> None:
        self._balance_tier_text = str(tier_text or "余额峰谷 --")
        self._balance_text = str(balance_text or "余额 --")
        self._refresh()

    def set_pet_visible(self, visible: bool) -> None:
        self._pet_visible = bool(visible)
        self.update()

    def refresh_from_config(self) -> None:
        self._cfg = _cfg_dict(self.config)
        self._schedule_next_balance_tier_refresh()
        self._refresh()

    # ------------------------------------------------------------ 余额峰谷
    def _balance_tier_display_text(self) -> str:
        """按系统时间直接生成余额峰谷文案：当前档位标签 + 下一档切换时间。

        不依赖余额查询；档位标签使用设置里的高峰/空闲（或梁文峰/梁文谷/自定义）。
        """
        from . import balance as balance_mod

        now = balance_mod.beijing_now()
        peak_label, idle_label = balance_mod.resolve_tier_labels(
            str(self.config.get("balance_tier_labels_mode", "default") or "default"),
            str(self.config.get("balance_tier_label_peak", "") or ""),
            str(self.config.get("balance_tier_label_idle", "") or ""),
        )
        # 保持无参调用以兼容现有测试/调用面（函数内部使用同一北京时间）。
        tier = balance_mod.deepseek_pricing_tier()
        label = peak_label if tier == "peak" else idle_label
        try:
            _next_tier, next_time = balance_mod.next_pricing_switch(now)
            time_text = balance_mod.format_switch_time(now, next_time)
            return f"{label} → {time_text}"
        except Exception:
            return label

    def _on_balance_tier_tick(self) -> None:
        """到达预设档位切换点：刷新显示，并排下一次准点刷新。"""
        self._refresh()
        self._schedule_next_balance_tier_refresh()

    def _schedule_next_balance_tier_refresh(self) -> None:
        """在下一个峰谷切换边界安排一次单发刷新（只在余额峰谷模式启用）。"""
        if str(self._cfg.get("info_mode") or "time") != "balance_tier":
            self._tier_tick_timer.stop()
            return
        try:
            from . import balance as balance_mod

            _next_tier, next_time = balance_mod.next_pricing_switch()
            now = balance_mod.beijing_now()
            delay_ms = max(250, int((next_time - now).total_seconds() * 1000) + 250)
            self._tier_tick_timer.start(delay_ms)
        except Exception:
            self._tier_tick_timer.stop()

    def _refresh(self) -> None:
        """内容变化后立即重算尺寸、夹回屏幕并重绘。"""
        self._update_size()
        self._clamp_to_screen()
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
            return self._balance_tier_display_text()
        if mode == "balance":
            return self._balance_text
        from PySide6.QtCore import QTime

        return QTime.currentTime().toString("HH:mm")

    def _info_color(self, fallback: QColor) -> QColor:
        """余额峰谷信息槽跟随设置的峰谷提示颜色开关。

        开启后高峰显示红色、低谷显示绿色；关闭或非余额峰谷模式使用普通次文字色。
        """
        if str(self._cfg.get("info_mode") or "time") != "balance_tier":
            return fallback
        if not bool(self.config.get("balance_tier_color_enabled", True)):
            return fallback
        try:
            from . import balance as balance_mod
            tier = balance_mod.deepseek_pricing_tier()
        except Exception:
            return fallback
        if tier == "peak":
            return QColor("#e5484d")
        if tier == "idle":
            return QColor("#30a46c")
        return fallback

    def _character_name(self) -> str:
        character_id = str(self.config.get("character", catalog.DEFAULT_CHARACTER))
        return self.config.character_alias(character_id) or character_id

    def _icon_text(self) -> str:
        return str(self._cfg.get("icon") or "🐳").strip()[:8] or "🐳"

    def _update_size(self) -> None:
        icon, name, info, status = self._visible_parts()
        fm = self.fontMetrics()
        # 左右边距各 16px，让状态灯到右边界的距离与图标到左边界的距离一致。
        width = 32
        if icon:
            width += 28 + 8
        if name:
            width += fm.horizontalAdvance(self._character_name()) + 8
        if info:
            width += fm.horizontalAdvance(self._info_text()) + 8
        if status:
            width += 10
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

    def _clamp_to_screen(self) -> None:
        screen = QGuiApplication.primaryScreen()
        if screen is None:
            return
        available = screen.availableGeometry()
        x = max(available.left(), min(self.x(), available.right() - self.width() + 1))
        y = max(available.top(), min(self.y(), available.bottom() - self.height() + 1))
        if (x, y) != (self.x(), self.y()):
            self.move(x, y)

    def _save_position(self) -> None:
        island = dict(self._cfg)
        island["x"] = self.x()
        island["y"] = self.y()
        self._cfg = island
        self.config.set("dynamic_island", island)
        self.config.save()

    # ------------------------------------------------------------ 绘制
    def _style_palette(self):
        """返回 (背景, 主文字色, 次文字色)。背景可为 QColor 或 QLinearGradient。"""
        style = str(self._cfg.get("style") or "dark")
        if style == "light":
            return QColor(255, 255, 255, 242), QColor(31, 35, 40), QColor(107, 114, 128)
        if style == "glass":
            gradient = QLinearGradient(0, 0, 0, self.height())
            gradient.setColorAt(0.0, QColor(255, 255, 255, 196))
            gradient.setColorAt(0.5, QColor(230, 242, 255, 150))
            gradient.setColorAt(1.0, QColor(255, 255, 255, 210))
            return gradient, QColor(35, 45, 60), QColor(90, 105, 125)
        return QColor(28, 30, 38, 235), QColor(235, 238, 245), QColor(160, 170, 190)

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

        # 胶囊主体（白色 / 黑色 / 玻璃质感）
        background, primary_color, secondary_color = self._style_palette()
        painter.setBrush(background)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawRoundedRect(rect, radius, radius)
        if str(self._cfg.get("style") or "dark") == "glass":
            # 玻璃高光描边，强化“液化玻璃”边缘
            painter.setPen(QPen(QColor(255, 255, 255, 130), 1))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRoundedRect(rect.adjusted(0.5, 0.5, -0.5, -0.5), radius - 0.5, radius - 0.5)

        icon, name, info, status = self._visible_parts()
        x = 16.0
        painter.setPen(Qt.PenStyle.NoPen)
        if icon:
            painter.setBrush(QColor(64, 184, 255, 255))
            painter.drawEllipse(QRectF(x, (self.height() - 26) / 2, 26, 26))
            painter.setPen(QColor(255, 255, 255))
            fm = self.fontMetrics()
            icon_text = self._icon_text()
            painter.drawText(
                QRectF(x, (self.height() - fm.height()) / 2 - 1, 26, fm.height()),
                Qt.AlignmentFlag.AlignCenter,
                icon_text,
            )
            painter.setPen(Qt.PenStyle.NoPen)
            x += 26 + 8

        painter.setPen(primary_color)
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
            painter.setPen(self._info_color(secondary_color))
            painter.drawText(
                QRectF(x, 0, self.fontMetrics().horizontalAdvance(info_text), self.height()),
                Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                info_text,
            )
            x += self.fontMetrics().horizontalAdvance(info_text) + 8

        if status:
            dot_size = 10
            dot_color = QColor(80, 220, 120) if self._pet_visible else QColor(130, 140, 155)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(dot_color)
            painter.drawEllipse(QRectF(x, (self.height() - dot_size) / 2, dot_size, dot_size))
            x += dot_size + 12

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
