# -*- coding: utf-8 -*-
"""独立灵动岛胶囊窗口。

- 常驻顶层小窗，可显示图标、名称、信息槽、状态灯；
- 支持拖拽、四边停靠半隐藏（收成细条、鼠标靠近滑出）、位置持久化；
- 单击胶囊按 ``click_action`` 展开卡片（余额/最近消息/快捷按钮）或切换桌宠显隐；
- 事件动效（``event_effects``）：AI 回复/余额刷新/峰谷切换时位移弹跳 +
  表面轻压 + 短暂呼吸，静止时完全无定时器开销；dsh 工作时状态灯变蓝；
- ``bump()`` 供碰撞系统调用：被撞时表面压扁回弹（史莱姆果冻形变只作用于
  胶囊外形，文字图标保持锐利）+ 按撞击方向轻微踢动；
- 外观：黑/白/玻璃质感自绘 + 背景不透明度 + 主题色（五级）。
"""
from __future__ import annotations

import logging
import math
import time

from PySide6.QtCore import (
    QPoint, QRect, QRectF, QSize, Qt, QTimer, Signal,
)
from PySide6.QtGui import (
    QColor, QGuiApplication, QLinearGradient, QPainter, QPen, QPixmap,
)
from PySide6.QtWidgets import (
    QApplication, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget,
)

from . import catalog

logger = logging.getLogger(__name__)

_CAPSULE_HEIGHT = 44
_CAPSULE_INSET = 6  # 胶囊绘制内缩：给果冻形变/悬停放大/踢动留出窗口内余量
_EDGE_MARGIN = 16
_DOCK_THRESHOLD = 40
_STRIP_THICKNESS = 16
_STRIP_SIDE = 64  # 左右停靠时竖条的长度
_CARD_WIDTH = 340
_CARD_HEIGHT = 148
_CARD_AUTO_COLLAPSE_MS = 15_000
_DOCK_BACK_MS = 800
_BREATHE_AFTER_EVENT_S = 5.0

# 弹簧参数：位移踢动（kick）、受击倾斜（tilt）、果冻形变（squish）
_KICK_STIFFNESS = 210.0
_KICK_DAMPING = 17.0
_SCALE_STIFFNESS = 170.0
_SCALE_DAMPING = 15.0
_TILT_STIFFNESS = 120.0
_TILT_DAMPING = 10.0
_TILT_MAX_DEG = 3.5
_SCALE_MIN = 0.97
_SCALE_MAX = 1.03
# 史莱姆形变：只作用于胶囊外形（背景/描边/阴影），文字图标绝不参与变换——
# 形变的是"表面"，内容保持锐利（1.0=休息态，<1 压扁，弹簧回摆产生果冻抖动）
_SQUISH_STIFFNESS = 150.0
_SQUISH_DAMPING = 8.5
_SQUISH_MIN = 0.85
_SQUISH_MAX = 1.12
_ANIM_TICK_MS = 16  # ~60fps，仅动画活跃时运行，静止零开销

_DOCK_EDGES = ("none", "top", "bottom", "left", "right")

# 主题色预设（设置页可换）：图标底圈 / 事件闪光 / 停靠描边共用
_ACCENT_PRESETS = {
    "blue": "#40b8ff",
    "green": "#30a46c",
    "purple": "#9b7bff",
    "pink": "#ff7ab8",
    "orange": "#ffa94d",
}
_DEFAULT_ACCENT = "blue"


def spring_step(
    value: float, velocity: float, target: float, dt: float,
    stiffness: float = _KICK_STIFFNESS, damping: float = _KICK_DAMPING,
) -> tuple[float, float]:
    """阻尼弹簧积分一步（半隐式欧拉），返回新的 (value, velocity)。

    dt 由调用方按真实帧间隔传入（钳到 50ms 防切后台后爆炸）。
    """
    dt = max(0.0, min(float(dt), 0.05))
    accel = (target - value) * stiffness - velocity * damping
    velocity += accel * dt
    value += velocity * dt
    return value, velocity


def ease_out_cubic(t: float) -> float:
    t = max(0.0, min(1.0, float(t)))
    return 1.0 - (1.0 - t) ** 3


def dock_edge_for(
    rect: QRect, available: QRect, threshold: int = _DOCK_THRESHOLD,
) -> str:
    """按窗口矩形到屏幕可用区四边的距离判定停靠边，都不近则 "none"。

    四边等权、取最近者——顶部被系统栏/MyDockFinder 占用时用户可靠左右下。
    """
    distances = {
        "top": abs(rect.top() - available.top()),
        "bottom": abs(available.bottom() - rect.bottom()),
        "left": abs(rect.left() - available.left()),
        "right": abs(available.right() - rect.right()),
    }
    edge, distance = min(distances.items(), key=lambda item: item[1])
    return edge if distance <= threshold else "none"


def strip_rect_for(rect: QRect, edge: str, available: QRect) -> QRect:
    """停靠后的细条矩形：上下停靠压扁高度，左右停靠收成短竖条。"""
    if edge in ("top", "bottom"):
        y = available.top() if edge == "top" else available.bottom() - _STRIP_THICKNESS + 1
        return QRect(rect.left(), y, rect.width(), _STRIP_THICKNESS)
    if edge in ("left", "right"):
        x = available.left() if edge == "left" else available.right() - _STRIP_THICKNESS + 1
        length = min(_STRIP_SIDE, available.height())
        y = max(available.top(), min(rect.center().y() - length // 2,
                                     available.bottom() - length + 1))
        return QRect(x, y, _STRIP_THICKNESS, length)
    return QRect(rect)


def _cfg_dict(config) -> dict:
    value = config.get("dynamic_island", {})
    return value if isinstance(value, dict) else {}


class DynamicIsland(QWidget):
    """胶囊形态的独立灵动岛窗口。"""

    clicked = Signal()  # click_action == "toggle_pet" 时的单击（旧行为）
    toggle_pet_requested = Signal()
    open_chat_requested = Signal()
    open_settings_requested = Signal()
    card_expanded = Signal()  # 卡片展开（AppShell 借此静默刷新余额）

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
        self._agent_active = False
        self._last_message = ""
        # 几何变化回调（果冻墙碰撞体挂这里跟踪拖拽/停靠滑动）：AppShell 注入。
        self.on_geometry_changed = None
        # 桌宠聚合可见性回调（碰撞体据此挂起/恢复）：AppShell 注入。
        self.on_pet_visibility_changed = None

        # ---- 模式与动效状态（静止时全部为稳态，_anim_timer 不运行）----
        self._mode = "normal"  # normal / docked / expanded
        self._hover_peek = False  # 停靠状态下鼠标靠近的临时滑出
        # 位移弹跳（px）：事件下压回弹 / 受击定向踢动，绘制时整数化保持文字锐利
        self._kick_x = 0.0
        self._kick_y = 0.0
        self._kick_vx = 0.0
        self._kick_vy = 0.0
        self._tilt = 0.0
        self._tilt_v = 0.0
        # 果冻形变（史莱姆表面浮动）：只调制胶囊外形，不碰文字
        self._squish = 1.0
        self._squish_v = 0.0
        self._scale = 1.0  # 只做 ±3% 微缩放辅助（大缩放会糊文字，已弃用）
        self._scale_v = 0.0
        self._breathe_until = 0.0
        self._hover_scale_target = 1.0
        # 内容层位图缓存（paintEvent 的文字绘制是实测大头，见 _content_cache）
        self._content_pixmap: QPixmap | None = None
        self._content_cache_key: tuple | None = None
        self._geo_from: QRect | None = None
        self._geo_to: QRect | None = None
        self._geo_t = 0.0

        self._anim_timer = QTimer(self)
        self._anim_timer.setInterval(_ANIM_TICK_MS)
        self._anim_timer.timeout.connect(self._on_anim_tick)
        self._anim_last = 0.0

        self._dock_back_timer = QTimer(self)
        self._dock_back_timer.setSingleShot(True)
        self._dock_back_timer.setInterval(_DOCK_BACK_MS)
        self._dock_back_timer.timeout.connect(self._dock_back)

        self._card_collapse_timer = QTimer(self)
        self._card_collapse_timer.setSingleShot(True)
        self._card_collapse_timer.setInterval(_CARD_AUTO_COLLAPSE_MS)
        self._card_collapse_timer.timeout.connect(self.collapse_card)

        self._build_card()

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

    # ------------------------------------------------------------ 模式/配置
    @property
    def mode(self) -> str:
        return self._mode

    @property
    def dock_edge(self) -> str:
        # 「靠边半隐藏」总开关关着时，存储的停靠边视为无——停靠判定/细条
        # 绘制/碰撞跳过等所有读取处统一走"不停靠"，不留死状态（开关再开
        # 时存储的边恢复生效）
        if not self._edge_dock_enabled():
            return "none"
        return str(self._cfg.get("dock_edge") or "none")

    def _click_action(self) -> str:
        return str(self._cfg.get("click_action") or "expand")

    def _event_effects_enabled(self) -> bool:
        return bool(self._cfg.get("event_effects", True))

    def _edge_dock_enabled(self) -> bool:
        return bool(self._cfg.get("edge_dock", True))

    def _opacity(self) -> float:
        """背景不透明度倍率（0.4~1.0），设置页可调；非法值回退 1.0。"""
        try:
            value = float(self._cfg.get("opacity", 1.0))
        except (TypeError, ValueError):
            return 1.0
        return max(0.4, min(1.0, value))

    def _accent_color(self) -> QColor:
        key = str(self._cfg.get("accent") or _DEFAULT_ACCENT)
        return QColor(_ACCENT_PRESETS.get(key, _ACCENT_PRESETS[_DEFAULT_ACCENT]))

    # ------------------------------------------------------------ 对外
    def set_balance_info(self, tier_text: str, balance_text: str) -> None:
        self._balance_tier_text = str(tier_text or "余额峰谷 --")
        self._balance_text = str(balance_text or "余额 --")
        self._sync_card_labels()
        self._refresh()

    def set_pet_visible(self, visible: bool) -> None:
        self._pet_visible = bool(visible)
        callback = self.on_pet_visibility_changed
        if callable(callback):
            try:
                callback(bool(visible))
            except Exception:
                pass
        self._sync_card_labels()
        self.update()

    def set_agent_active(self, active: bool) -> None:
        """dsh/agent 工作状态：点亮蓝色状态灯；上升沿弹一下提示开工。"""
        active = bool(active)
        rising = active and not self._agent_active
        self._agent_active = active
        if rising:
            self._bounce(impulse=3.0)
        self.update()

    def set_last_message(self, text: str) -> None:
        """记录最近一条 AI 消息（展开卡片里显示摘要）。"""
        self._last_message = str(text or "").strip()
        self._sync_card_labels()

    def notify_event(self, kind: str = "generic") -> None:
        """事件动效入口：reply / balance / tier / generic。尊重 event_effects 开关。"""
        if not self._event_effects_enabled():
            return
        impulse = {"reply": 5.0, "balance": 3.5, "tier": 4.0}.get(kind, 3.0)
        self._bounce(impulse=impulse)
        if kind == "reply":
            self._breathe_until = time.monotonic() + _BREATHE_AFTER_EVENT_S
            self._ensure_anim_timer()

    def bump(self, strength: float = 1.0, dir_x: float = 0.0, dir_y: float = 0.0) -> None:
        """被桌宠撞到（果冻墙→水波感）：撞击点荡开涟漪 + 轻微定向踢动。

        dir_x/dir_y 为岛被顶着的方向（单位向量，由碰撞侧从对方 dv 取反得到）；
        位移与形变只作用于绘制层，窗口几何不动（岛的位置永远由用户拖拽决定）。
        """
        if not self._event_effects_enabled():
            return
        strength = max(0.2, min(float(strength), 3.0))
        # 史莱姆受击：表面压扁（弹簧回摆出果冻抖动）+ 轻微定向踢动/倾斜。
        # 踢动速度钳制：窗口只有 44px 高，多鱼同撞叠加时位移超窗会被裁掉
        self._squish_v -= 1.15 * strength
        self._kick_vx = max(-150.0, min(150.0, self._kick_vx + float(dir_x) * 110.0 * strength))
        self._kick_vy = max(-150.0, min(150.0, self._kick_vy + float(dir_y) * 110.0 * strength))
        self._tilt_v += 13.0 * strength * (1 if dir_x >= 0 else -1)
        self._ensure_anim_timer()

    def refresh_from_config(self) -> None:
        old_edge = self.dock_edge
        self._cfg = _cfg_dict(self.config)
        self._schedule_next_balance_tier_refresh()
        self._sync_card_labels()
        if self.dock_edge != old_edge:
            self._apply_position()
        # 「靠边半隐藏」被关闭时已停靠的岛要复位回正常形态——否则一直是
        # 16px 细条贴边（碰撞结算也随停靠跳过而长期失效）
        if not self._edge_dock_enabled() and self._mode == "docked":
            self._mode = "normal"
            self._hover_peek = False
            self._apply_fixed_size()
            self._apply_position()
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
        """到达预设档位切换点：刷新显示、播事件动效，并排下一次准点刷新。"""
        self._refresh()
        self.notify_event("tier")
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
        if self._mode == "docked" and not self._hover_peek:
            self.update()
            return
        self._update_size()
        self._clamp_to_screen()
        self.update()

    # ------------------------------------------------------------ 卡片
    def _build_card(self) -> None:
        """展开卡片：余额/峰谷两行 + 最近消息 + 三个快捷按钮。默认隐藏。"""
        self._card_box = QWidget(self)
        self._card_box.setObjectName("dynamic-island-card")
        # 卡片底板由窗口 paintEvent 统一绘制（圆角连续），控件容器自身透明
        self._card_box.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self._card_box.setStyleSheet("background: transparent;")
        layout = QVBoxLayout(self._card_box)
        layout.setContentsMargins(16, 6, 16, 12)
        layout.setSpacing(4)
        self._card_balance_label = QLabel(self._card_box)
        self._card_tier_label = QLabel(self._card_box)
        self._card_message_label = QLabel(self._card_box)
        self._card_message_label.setWordWrap(True)
        self._card_message_label.setMaximumHeight(34)
        for label in (self._card_balance_label, self._card_tier_label,
                      self._card_message_label):
            layout.addWidget(label)
        layout.addSpacing(2)
        button_row = QHBoxLayout()
        button_row.setSpacing(8)
        self._card_toggle_btn = QPushButton("显隐桌宠", self._card_box)
        self._card_chat_btn = QPushButton("聊天", self._card_box)
        self._card_settings_btn = QPushButton("设置", self._card_box)
        self._card_toggle_btn.clicked.connect(self._on_card_toggle)
        self._card_chat_btn.clicked.connect(self._on_card_chat)
        self._card_settings_btn.clicked.connect(self._on_card_settings)
        for btn in (self._card_toggle_btn, self._card_chat_btn,
                    self._card_settings_btn):
            button_row.addWidget(btn)
        layout.addLayout(button_row)
        self._card_box.hide()
        self._sync_card_labels()

    def _sync_card_labels(self) -> None:
        if not hasattr(self, "_card_box"):
            return
        _bg, primary, secondary = self._style_palette()
        primary_hex = primary.name() if isinstance(primary, QColor) else "#e5eaf5"
        secondary_hex = secondary.name() if isinstance(secondary, QColor) else "#a0aabe"
        accent_hex = self._accent_color().name()
        self._card_balance_label.setStyleSheet(
            f"color: {primary_hex}; font-weight: bold; font-size: 14px;")
        self._card_tier_label.setStyleSheet(f"color: {secondary_hex}; font-size: 11px;")
        self._card_message_label.setStyleSheet(f"color: {secondary_hex}; font-size: 11px;")
        # 按钮：圆角 8px、描边跟随主题色、悬停加亮；不用系统默认灰按钮
        button_style = (
            f"QPushButton {{ color: {primary_hex}; background: transparent;"
            f" border: 1px solid {accent_hex}; border-radius: 8px;"
            f" padding: 4px 10px; font-size: 12px; }}"
            f"QPushButton:hover {{ background: {accent_hex}; color: #ffffff; }}"
            f"QPushButton:pressed {{ background: {secondary_hex}; }}"
        )
        for btn in (self._card_toggle_btn, self._card_chat_btn,
                    self._card_settings_btn):
            btn.setStyleSheet(button_style)
        self._card_balance_label.setText(self._balance_text)
        self._card_tier_label.setText(self._balance_tier_text)
        message = self._last_message or "（暂无最近消息）"
        # 摘要最多两行，超出省略（标签限高 34px 配合 wordWrap）
        metrics = self._card_message_label.fontMetrics()
        elided = metrics.elidedText(
            message, Qt.TextElideMode.ElideRight, max(40, (_CARD_WIDTH - 32) * 2))
        self._card_message_label.setText(elided)
        self._card_toggle_btn.setText("隐藏桌宠" if self._pet_visible else "显示桌宠")

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        if hasattr(self, "_card_box"):
            self._card_box.setGeometry(
                0, _CAPSULE_HEIGHT, self.width(),
                max(0, self.height() - _CAPSULE_HEIGHT))

    def _on_card_toggle(self) -> None:
        self.toggle_pet_requested.emit()
        self.collapse_card()

    def _on_card_chat(self) -> None:
        self.open_chat_requested.emit()
        self.collapse_card()

    def _on_card_settings(self) -> None:
        self.open_settings_requested.emit()
        self.collapse_card()

    def expand_card(self) -> None:
        """展开卡片（单击胶囊的默认行为）。停靠状态先滑出再展开。"""
        if self._mode == "expanded":
            self.collapse_card()
            return
        self._dock_back_timer.stop()
        self._mode = "expanded"
        self._hover_peek = False
        # 单击展开时鼠标多半已在岛内（enterEvent 已把悬停放大目标置 1.02），
        # 展开态禁用整窗缩放，目标拉回 1.0（见 paintEvent 截边说明）
        self._hover_scale_target = 1.0
        self._card_box.show()
        self._sync_card_labels()
        self._animate_to(self._target_rect())
        self._card_collapse_timer.start()
        self.card_expanded.emit()

    def collapse_card(self, *, animate: bool = True) -> None:
        """收起卡片；配置为停靠边时收回细条。拖拽起手时 animate=False
        （动画会与拖拽抢几何，直接把窗拽回停靠位）。"""
        if self._mode != "expanded":
            return
        self._card_collapse_timer.stop()
        self._card_box.hide()
        self._mode = "docked" if self.dock_edge != "none" else "normal"
        self._hover_peek = False
        if animate:
            self._animate_to(self._target_rect())
        else:
            self._geo_from = None
            self._geo_to = None
            self._set_free_geometry(self._target_rect())
            self._apply_fixed_size()

    # ------------------------------------------------------------ 动效核心
    def _bounce(self, impulse: float = 4.0) -> None:
        """事件弹跳：下压几像素再弹簧回位（整数位移，文字不糊）+ 表面轻压。"""
        self._kick_y = 1.5 + min(float(impulse), 5.0)
        self._kick_vy = 0.0
        self._squish_v -= impulse * 0.14
        self._scale_v += impulse * 0.06
        self._ensure_anim_timer()

    def _ensure_anim_timer(self) -> None:
        # 隐藏即休眠：隐藏态不被后台事件（AI 回复/余额刷新）拉起 60fps
        # 动画定时器空转；showEvent 恢复时会重刷新并重启动效
        if not self.isVisible():
            return
        if not self._anim_timer.isActive():
            self._anim_last = time.monotonic()
            self._anim_timer.start()

    def _animating(self) -> bool:
        return (
            self._geo_to is not None
            or abs(self._kick_x) > 0.3 or abs(self._kick_y) > 0.3
            or abs(self._kick_vx) > 5.0 or abs(self._kick_vy) > 5.0
            or abs(self._squish - 1.0) > 0.004 or abs(self._squish_v) > 0.02
            or abs(self._scale - self._hover_scale_target) > 0.001
            or abs(self._scale_v) > 0.005
            or abs(self._tilt) > 0.05
            or abs(self._tilt_v) > 1.0
            or time.monotonic() < self._breathe_until
        )

    def _on_anim_tick(self) -> None:
        now = time.monotonic()
        dt = now - (self._anim_last or now)
        self._anim_last = now

        # 几何滑移（停靠/展开）：定时插值，ease-out
        if self._geo_to is not None and self._geo_from is not None:
            self._geo_t = min(1.0, self._geo_t + dt / 0.18)
            k = ease_out_cubic(self._geo_t)
            rect = QRect(
                round(self._geo_from.x() + (self._geo_to.x() - self._geo_from.x()) * k),
                round(self._geo_from.y() + (self._geo_to.y() - self._geo_from.y()) * k),
                round(self._geo_from.width() + (self._geo_to.width() - self._geo_from.width()) * k),
                round(self._geo_from.height() + (self._geo_to.height() - self._geo_from.height()) * k),
            )
            self._set_free_geometry(rect)
            if self._geo_t >= 1.0:
                self._geo_from = None
                self._geo_to = None
                self._set_free_geometry(self._target_rect())

        # 位移踢动回位 / 微缩放 / 受击倾斜 / 果冻形变回弹
        self._kick_x, self._kick_vx = spring_step(
            self._kick_x, self._kick_vx, 0.0, dt)
        self._kick_y, self._kick_vy = spring_step(
            self._kick_y, self._kick_vy, 0.0, dt)
        self._scale, self._scale_v = spring_step(
            self._scale, self._scale_v, self._hover_scale_target, dt,
            stiffness=_SCALE_STIFFNESS, damping=_SCALE_DAMPING)
        self._scale = max(_SCALE_MIN, min(_SCALE_MAX, self._scale))
        self._tilt, self._tilt_v = spring_step(
            self._tilt, self._tilt_v, 0.0, dt,
            stiffness=_TILT_STIFFNESS, damping=_TILT_DAMPING)
        self._squish, self._squish_v = spring_step(
            self._squish, self._squish_v, 1.0, dt,
            stiffness=_SQUISH_STIFFNESS, damping=_SQUISH_DAMPING)
        self._squish = max(_SQUISH_MIN, min(_SQUISH_MAX, self._squish))
        # 踢动位移钳制在窗口内（±5px）：弹簧过冲也不画出界被裁
        self._kick_x = max(-5.0, min(5.0, self._kick_x))
        self._kick_y = max(-5.0, min(5.0, self._kick_y))
        if not self._animating():
            self._kick_x = self._kick_y = 0.0
            self._kick_vx = self._kick_vy = 0.0
            self._squish = 1.0
            self._scale = 1.0 if self._hover_scale_target == 1.0 else self._hover_scale_target
            self._tilt = 0.0
            self._anim_timer.stop()
        self.update()

    def _finish_animations(self) -> None:
        """立即跳到所有动画终点（测试与即需布局用）。"""
        self._anim_timer.stop()
        self._geo_from = None
        self._geo_to = None
        self._kick_x = self._kick_y = 0.0
        self._kick_vx = self._kick_vy = 0.0
        self._squish = 1.0
        self._squish_v = 0.0
        self._scale = 1.0
        self._scale_v = 0.0
        self._tilt = 0.0
        self._tilt_v = 0.0
        self._breathe_until = 0.0
        self._hover_scale_target = 1.0
        self._set_free_geometry(self._target_rect())
        self.update()

    def _animate_to(self, target: QRect) -> None:
        if target == self.geometry() and not self._anim_timer.isActive():
            self._set_free_geometry(target)
            return
        self._geo_from = self.geometry()
        self._geo_to = target
        self._geo_t = 0.0
        self._ensure_anim_timer()

    def _emit_geometry_changed(self) -> None:
        callback = self.on_geometry_changed
        if callable(callback):
            try:
                callback()
            except Exception:
                pass

    def _set_free_geometry(self, rect: QRect) -> None:
        """动画期间解除固定尺寸直接摆几何；到位后由 _apply_fixed_size 锁定。"""
        self.setMinimumSize(0, 0)
        self.setMaximumSize(16777215, 16777215)
        self.setGeometry(rect)
        self._emit_geometry_changed()

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

    def _capsule_width(self) -> int:
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
        return max(120, width + _CAPSULE_INSET * 2)

    def _update_size(self) -> None:
        self._set_free_geometry(QRect(self.pos(), self._rest_size()))
        self._apply_fixed_size()

    def _rest_size(self) -> QSize:
        width = self._capsule_width()
        if self._mode == "expanded":
            return QSize(max(width, _CARD_WIDTH), _CAPSULE_HEIGHT + _CARD_HEIGHT)
        return QSize(width, _CAPSULE_HEIGHT)

    def _apply_fixed_size(self) -> None:
        size = self._rest_size()
        if self._mode == "docked" and not self._hover_peek:
            edge = self.dock_edge
            if edge in ("top", "bottom"):
                size = QSize(size.width(), _STRIP_THICKNESS)
            elif edge in ("left", "right"):
                size = QSize(_STRIP_THICKNESS, _STRIP_SIDE)
        self.setFixedSize(size)

    def _clamp_rect(self, rect: QRect) -> QRect:
        screen = self._current_screen()
        if screen is None:
            return rect
        available = screen.availableGeometry()
        x = max(available.left(), min(rect.x(), available.right() - rect.width() + 1))
        y = max(available.top(), min(rect.y(), available.bottom() - rect.height() + 1))
        return QRect(QPoint(x, y), rect.size())

    def _target_rect(self) -> QRect:
        """当前模式下的目标矩形（停靠细条 / 滑出预览 / 正常胶囊 / 展开卡片）。"""
        size = self._rest_size()
        edge = self.dock_edge
        screen = self._current_screen()
        if self._mode == "docked" and not self._hover_peek and screen is not None:
            return strip_rect_for(
                QRect(self.pos(), size), edge, screen.availableGeometry())
        if edge != "none" and self._mode in ("docked", "expanded") \
                and screen is not None:
            # 从停靠边向内展开：顶/底锚定该边，左右以细条纵向中心展开
            available = screen.availableGeometry()
            rect = QRect(self.pos(), size)
            if edge == "top":
                rect.moveTop(available.top())
            elif edge == "bottom":
                rect.moveBottom(available.bottom())
            elif edge == "left":
                rect.moveLeft(available.left())
                rect.moveTop(self.y() + self.height() // 2 - size.height() // 2)
            elif edge == "right":
                rect.moveRight(available.right())
                rect.moveTop(self.y() + self.height() // 2 - size.height() // 2)
            return self._clamp_rect(rect)
        return self._clamp_rect(QRect(self.pos(), size))

    def _current_screen(self):
        screen = QGuiApplication.screenAt(self.pos()) or QGuiApplication.primaryScreen()
        return screen

    def _apply_position(self) -> None:
        self._update_size()
        screen = self._current_screen()
        available = screen.availableGeometry() if screen is not None else None
        x = self._cfg.get("x")
        y = self._cfg.get("y")
        if isinstance(x, (int, float)) and isinstance(y, (int, float)):
            pos = QPoint(int(x), int(y))
        else:
            pos = (
                QPoint(available.right() - self.width() - _EDGE_MARGIN,
                       available.top() + _EDGE_MARGIN)
                if available else QPoint(100, 100)
            )
        self.move(pos)
        self._clamp_to_screen()
        # 展开卡片态不被停靠劫持：卡片是用户当前的交互焦点，设置保存触发的
        # 位置应用不能把 _mode 改成 docked（卡片会留在 16px 细条上收不起来）；
        # 收起时 collapse_card 会按 dock_edge 自行决定是否停靠
        if self.dock_edge != "none" and self._mode != "expanded":
            self._mode = "docked"
            self._hover_peek = False
            target = self._target_rect()
            self._set_free_geometry(target)
            self._apply_fixed_size()

    def _clamp_to_screen(self) -> None:
        if self._mode == "docked" and not self._hover_peek:
            return
        screen = self._current_screen()
        if screen is None:
            return
        available = screen.availableGeometry()
        x = max(available.left(), min(self.x(), available.right() - self.width() + 1))
        y = max(available.top(), min(self.y(), available.bottom() - self.height() + 1))
        if (x, y) != (self.x(), self.y()):
            self.move(x, y)
            # 位置校正也要通知碰撞体重提交，否则碰撞体留在旧位置
            self._emit_geometry_changed()

    def _save_position(self) -> None:
        island = dict(self._cfg)
        island["x"] = self.x()
        island["y"] = self.y()
        self._cfg = island
        self.config.set("dynamic_island", island)
        self.config.save()

    def _save_dock_edge(self, edge: str) -> None:
        island = dict(self._cfg)
        island["dock_edge"] = edge if edge in _DOCK_EDGES else "none"
        self._cfg = island
        self.config.set("dynamic_island", island)
        self.config.save()

    # ------------------------------------------------------------ 绘制
    def _style_palette(self):
        """返回 (背景, 主文字色, 次文字色)。背景可为 QColor 或 QLinearGradient。

        所有风格的 alpha 都乘上不透明度设置。
        """
        opacity = self._opacity()
        style = str(self._cfg.get("style") or "dark")
        if style == "light":
            return (QColor(255, 255, 255, round(242 * opacity)),
                    QColor(31, 35, 40), QColor(107, 114, 128))
        if style == "glass":
            gradient = QLinearGradient(0, 0, 0, self.height())
            gradient.setColorAt(0.0, QColor(255, 255, 255, round(196 * opacity)))
            gradient.setColorAt(0.5, QColor(230, 242, 255, round(150 * opacity)))
            gradient.setColorAt(1.0, QColor(255, 255, 255, round(210 * opacity)))
            return gradient, QColor(35, 45, 60), QColor(90, 105, 125)
        return (QColor(28, 30, 38, round(235 * opacity)),
                QColor(235, 238, 245), QColor(160, 170, 190))

    def _keyline_color(self) -> QColor:
        """深色底上的 1px 分界描边（暗壁纸下把胶囊轮廓勾出来）。"""
        style = str(self._cfg.get("style") or "dark")
        if style in ("light", "glass"):
            return QColor(0, 0, 0, 28)
        return QColor(255, 255, 255, 52)

    @staticmethod
    def _readable_card_background(background):
        """展开卡片底板：alpha 钳到下限 215。

        胶囊透明度设置只该影响胶囊本体（不挡视线）；卡片是阅读界面，
        跟着透明化会让余额/消息直接糊在壁纸上（实机反馈）。QColor 与
        glass 的 QLinearGradient 两种背景分别钳制。
        """
        if isinstance(background, QLinearGradient):
            clamped = QLinearGradient(background)
            for pos, color in background.stops():
                c = QColor(color)
                c.setAlpha(max(c.alpha(), 215))
                clamped.setColorAt(pos, c)
            return clamped
        c = QColor(background)
        c.setAlpha(max(c.alpha(), 215))
        return c

    def _status_dot_color(self) -> QColor:
        if self._agent_active:
            return QColor(64, 150, 255)
        if self._pet_visible:
            return QColor(80, 220, 120)
        return QColor(130, 140, 155)

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        # 展开卡片时跳过一切整窗变换（kick 平移/呼吸/倾斜/缩放）：卡片底板
        # 贴窗口边缘绘制，任何位移或 >1 缩放都会把它顶出窗口被硬切（截边
        # 实机反馈；kick 来自展开后的余额刷新弹跳，是必然路径）
        if self._mode == "expanded":
            tilt, scale = 0.0, 1.0
        else:
            # 动效变换：整数位移（文字锐利）+ 呼吸微浮动
            kick_x = round(self._kick_x)
            kick_y = round(self._kick_y)
            if time.monotonic() < self._breathe_until:
                kick_y += round(1.5 * math.sin(time.monotonic() * 2.6))
            if kick_x or kick_y:
                painter.translate(kick_x, kick_y)
            tilt = max(-_TILT_MAX_DEG, min(_TILT_MAX_DEG, self._tilt))
            scale = max(_SCALE_MIN, min(_SCALE_MAX, self._scale))
        if tilt or scale != 1.0:
            cx = self.width() / 2.0
            cy = min(self.height(), _CAPSULE_HEIGHT) / 2.0
            painter.translate(cx, cy)
            painter.rotate(tilt)
            painter.scale(scale, scale)
            painter.translate(-cx, -cy)

        if self._mode == "docked" and not self._hover_peek:
            self._paint_strip(painter)
        else:
            self._paint_capsule(painter)
        painter.end()

    def _squished_capsule_rect(self) -> tuple[QRectF, float]:
        """果冻形变后的胶囊绘制矩形（内缩 _CAPSULE_INSET 留形变余量）。

        只返回外形的几何——文字/图标不在这里面变换，保持锐利。
        squish<1 时竖向压扁、横向鼓出（面积大致守恒），绕胶囊中心形变。
        """
        base = QRectF(_CAPSULE_INSET, _CAPSULE_INSET,
                      self.width() - _CAPSULE_INSET * 2,
                      _CAPSULE_HEIGHT - _CAPSULE_INSET * 2)
        # 展开卡片态冻结形变（与整窗变换禁用同口径）：squish 鼓出会把
        # 贴边底板两侧的胶囊圆角削掉（实测 342.8px > 340px 窗口宽）
        squish = 1.0 if self._mode == "expanded" else \
            max(_SQUISH_MIN, min(_SQUISH_MAX, self._squish))
        if abs(squish - 1.0) < 0.002:
            return base, base.height() / 2.0
        sy = squish
        sx = 1.0 + (1.0 - sy) * 0.3  # 竖压横鼓（权重打折，鼓出不超出内缩余量）
        w, h = base.width() * sx, base.height() * sy
        cx, cy = base.center().x(), base.center().y()
        return QRectF(cx - w / 2.0, cy - h / 2.0, w, h), h / 2.0

    def _paint_strip(self, painter: QPainter) -> None:
        """停靠细条：圆角条 + 靠屏侧主题色亮线 + 状态点（竖条加图标）。"""
        rect = QRectF(0, 0, self.width(), self.height())
        radius = min(rect.width(), rect.height()) / 2.0
        background, primary, _secondary = self._style_palette()
        opacity = self._opacity()
        if isinstance(background, QColor):
            base = QColor(background)
            base.setAlpha(min(255, round(210 * opacity)))
        else:
            base = QColor(255, 255, 255, round(210 * opacity))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(base)
        painter.drawRoundedRect(rect, radius, radius)

        # 靠屏一侧的 2px 亮线（深壁纸上也能一眼找到细条）
        accent = QColor(self._accent_color())
        accent.setAlpha(220)
        painter.setBrush(accent)
        edge = self.dock_edge
        if edge == "top":
            painter.drawRoundedRect(QRectF(6, 0, rect.width() - 12, 2), 1, 1)
        elif edge == "bottom":
            painter.drawRoundedRect(QRectF(6, rect.height() - 2, rect.width() - 12, 2), 1, 1)
        elif edge == "left":
            painter.drawRoundedRect(QRectF(0, 6, 2, rect.height() - 12), 1, 1)
        elif edge == "right":
            painter.drawRoundedRect(QRectF(rect.width() - 2, 6, 2, rect.height() - 12), 1, 1)

        dot_size = 8
        painter.setBrush(self._status_dot_color())
        if edge in ("left", "right"):
            # 竖条：上方角色图标（小字号防裁切）、下方状态点
            painter.setPen(primary if isinstance(primary, QColor) else QColor(31, 35, 40))
            icon_font = painter.font()
            icon_font.setPixelSize(11)
            painter.setFont(icon_font)
            fm = painter.fontMetrics()
            painter.drawText(
                QRectF(0, 8, rect.width(), fm.height()),
                Qt.AlignmentFlag.AlignCenter, self._icon_text())
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(self._status_dot_color())
            painter.drawEllipse(QRectF(
                (rect.width() - dot_size) / 2, rect.height() - dot_size - 8,
                dot_size, dot_size))
        else:
            painter.drawEllipse(QRectF(
                (rect.width() - dot_size) / 2, (rect.height() - dot_size) / 2,
                dot_size, dot_size))

    def _content_cache(self) -> QPixmap:
        """内容层（图标/名称/信息/状态灯）的缓存位图。

        为什么缓存（拖岛扫鱼实测定案）：paintEvent 均值 6.2ms，大头是 CJK
        文字的 shaping/回退字体解析；而内容只在刷新（时间跳变/余额刷新/
        配置变更）时变化，拖拽/弹簧期间逐帧重画纯属浪费。缓存后每帧一次
        blit（亚毫秒）。blit 走同一 painter 的整窗变换（kick 为整数平移，
        文字锐利口径不变）；squish 只调制胶囊外形、本来就不碰内容层。
        key 覆盖所有影响像素的输入（可见项/文本/颜色/字体/DPR/宽度），
        任一变化自动重建，无需显式失效钩子。
        """
        icon, name, info, status = self._visible_parts()
        _background, primary_color, secondary_color = self._style_palette()
        info_color = self._info_color(secondary_color)
        dpr = self.devicePixelRatioF()
        key = (
            icon, name, info, status,
            self._icon_text() if icon else "",
            self._character_name() if name else "",
            self._info_text() if info else "",
            self._accent_color().name() if icon else "",
            primary_color.name() if name else "",
            info_color.name() if info else "",
            self._status_dot_color().name() if status else "",
            self.font().toString(), dpr, self.width(),
        )
        if self._content_pixmap is not None and self._content_cache_key == key:
            return self._content_pixmap
        pm = QPixmap(max(1, round(self.width() * dpr)),
                     max(1, round(_CAPSULE_HEIGHT * dpr)))
        pm.setDevicePixelRatio(dpr)
        pm.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pm)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setFont(self.font())
        x = _CAPSULE_INSET + 13.0
        painter.setPen(Qt.PenStyle.NoPen)
        if icon:
            painter.setBrush(self._accent_color())
            painter.drawEllipse(QRectF(x, (_CAPSULE_HEIGHT - 26) / 2, 26, 26))
            painter.setPen(QColor(255, 255, 255))
            fm = self.fontMetrics()
            painter.drawText(
                QRectF(x, (_CAPSULE_HEIGHT - fm.height()) / 2 - 1, 26, fm.height()),
                Qt.AlignmentFlag.AlignCenter,
                self._icon_text(),
            )
            painter.setPen(Qt.PenStyle.NoPen)
            x += 26 + 8
        painter.setPen(primary_color)
        if name:
            text = self._character_name()
            painter.drawText(
                QRectF(x, 0, self.fontMetrics().horizontalAdvance(text), _CAPSULE_HEIGHT),
                Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                text,
            )
            x += self.fontMetrics().horizontalAdvance(text) + 8
        if info:
            info_text = self._info_text()
            painter.setPen(info_color)
            painter.drawText(
                QRectF(x, 0, self.fontMetrics().horizontalAdvance(info_text), _CAPSULE_HEIGHT),
                Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                info_text,
            )
            x += self.fontMetrics().horizontalAdvance(info_text) + 8
        if status:
            dot_size = 10
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(self._status_dot_color())
            painter.drawEllipse(QRectF(x, (_CAPSULE_HEIGHT - dot_size) / 2, dot_size, dot_size))
            x += dot_size + 12
        painter.end()
        self._content_pixmap = pm
        self._content_cache_key = key
        return pm

    def _paint_capsule(self, painter: QPainter) -> None:
        # 展开时先画卡片底板（胶囊下方一整块，圆角连续；内容区遵守同心圆角）
        if self._mode == "expanded":
            panel = QRectF(0, 0, self.width(), self.height())
            background, _p, _s = self._style_palette()
            # 卡片底板走可读性钳制（不随胶囊透明度降下去）
            background = self._readable_card_background(background)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(0, 0, 0, 40))
            painter.drawRoundedRect(panel.translated(0, 2), 18, 18)
            painter.setBrush(background)
            painter.drawRoundedRect(panel, 18, 18)
            painter.setPen(QPen(self._keyline_color(), 1))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRoundedRect(panel.adjusted(0.5, 0.5, -0.5, -0.5), 17.5, 17.5)

        # 胶囊外形：内缩 _CAPSULE_INSET + 果冻形变（只有外形参与 squish）
        rect, radius = self._squished_capsule_rect()

        # 柔和阴影：底层半透明圆角矩形（跟随形变）
        if self._mode != "expanded":
            shadow = rect.translated(0, 2)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(0, 0, 0, 46))
            painter.drawRoundedRect(shadow, radius, radius)

        # 胶囊主体（白色 / 黑色 / 玻璃质感）；内容层已下沉到 _content_cache，
        # 主/次文字色在这里不再使用
        background, _primary_color, _secondary_color = self._style_palette()
        painter.setBrush(background)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawRoundedRect(rect, radius, radius)
        style = str(self._cfg.get("style") or "dark")
        if style == "glass":
            # 玻璃高光描边，强化“液化玻璃”边缘
            painter.setPen(QPen(QColor(255, 255, 255, 130), 1))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRoundedRect(rect.adjusted(0.5, 0.5, -0.5, -0.5), radius - 0.5, radius - 0.5)
        else:
            # 深色底 key line：在暗壁纸上把轮廓勾出来
            painter.setPen(QPen(self._keyline_color(), 1))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRoundedRect(rect.adjusted(0.5, 0.5, -0.5, -0.5), radius - 0.5, radius - 0.5)

        # 内容层（图标/名称/信息/状态灯）：缓存位图 blit——文字逐帧重画是
        # 实测大头（见 _content_cache）；内容绝不参与形变，口径与原"按未
        # 形变静止位绘制"一致
        painter.drawPixmap(0, 0, self._content_cache())

    # ------------------------------------------------------------ 显隐休眠
    def hideEvent(self, event) -> None:  # noqa: N802
        """隐藏即休眠：信息轮询/动效/停靠收回/卡片自动收起全部停表，
        隐藏态零定时器空转。"""
        super().hideEvent(event)
        self._info_timer.stop()
        self._tier_tick_timer.stop()
        self._anim_timer.stop()
        self._dock_back_timer.stop()
        self._card_collapse_timer.stop()

    def showEvent(self, event) -> None:  # noqa: N802
        """重新显示：恢复信息轮询并立即刷新（沉睡期间的内容变化补上），
        几何直接归位（隐藏可能打断了停靠/展开动画）。"""
        super().showEvent(event)
        self._finish_animations()
        self._info_timer.start()
        self._schedule_next_balance_tier_refresh()
        self._refresh()

    # ------------------------------------------------------------ 悬停
    def enterEvent(self, event) -> None:  # noqa: N802
        # 展开卡片时不悬停放大（整窗缩放会截掉贴边的卡片底板，见 paintEvent）
        self._hover_scale_target = 1.0 if self._mode == "expanded" else 1.02
        if self._mode == "docked" and not self._hover_peek:
            self._dock_back_timer.stop()
            self._hover_peek = True
            self._animate_to(self._target_rect())
        self._ensure_anim_timer()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802
        self._hover_scale_target = 1.0
        if self._mode == "docked" and self._hover_peek:
            self._dock_back_timer.start()
        self._ensure_anim_timer()
        super().leaveEvent(event)

    def _dock_back(self) -> None:
        if self._mode == "docked" and self._hover_peek:
            self._hover_peek = False
            self._animate_to(self._target_rect())

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
            # 起拖先清几何插值状态（滑出/停靠/归位动画的目标）：否则动画
            # 每 16ms 把窗口拽回目标位，与拖拽 move() 互抢（不跟手/60Hz
            # 抖动，且期间碰撞结算被 _geo_to 守卫全程跳过）。注意不停动画
            # 表：_on_anim_tick 只在 _geo_to 非 None 时插值几何，停表会把
            # squish/kick 弹簧冻在中途
            self._geo_from = None
            self._geo_to = None
            if self._mode == "expanded":
                self.collapse_card(animate=False)  # 拖拽起手直接落位，不播收起动画
            if self._mode == "docked":
                # 拖出停靠：进入正常模式跟随光标
                self._mode = "normal"
                self._hover_peek = False
                self._apply_fixed_size()
        if self._dragging and self._drag_offset is not None:
            self.move(global_pos - self._drag_offset)
            self._emit_geometry_changed()
            event.accept()

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if event.button() != Qt.MouseButton.LeftButton:
            return
        screen = self._current_screen()
        available = screen.availableGeometry() if screen is not None else None
        if not self._dragging:
            if self._mode == "docked" and not self._hover_peek:
                # 细条单击 = 滑出预览（不停留展开卡片，保持轻量）
                self._hover_peek = True
                self._animate_to(self._target_rect())
                self._dock_back_timer.start()
            elif self._click_action() == "toggle_pet":
                self.clicked.emit()
            else:
                self.expand_card()
        else:
            # 先夹回屏幕再判定停靠：拖出屏外的落点按夹后位置算距离
            if available is not None:
                x = max(available.left(), min(self.x(), available.right() - self.width() + 1))
                y = max(available.top(), min(self.y(), available.bottom() - self.height() + 1))
                self.move(x, y)
            edge = "none"
            if available is not None and self._edge_dock_enabled():
                edge = dock_edge_for(self.geometry(), available)
                rect = self.geometry()
                logger.info(
                    "灵动岛拖拽落点 (%d,%d)：距四边 top=%d bottom=%d left=%d right=%d"
                    "（阈值 %d）→ dock=%s",
                    rect.x(), rect.y(),
                    abs(rect.top() - available.top()),
                    abs(available.bottom() - rect.bottom()),
                    abs(rect.left() - available.left()),
                    abs(available.right() - rect.right()),
                    _DOCK_THRESHOLD, edge)
            self._save_dock_edge(edge)
            self._save_position()
            if edge != "none":
                self._mode = "docked"
                self._hover_peek = False
                self._animate_to(self._target_rect())
            elif self._mode == "docked":
                self._mode = "normal"
                self._animate_to(self._target_rect())
        self._press_global = None
        self._drag_offset = None
        self._dragging = False
        self._emit_geometry_changed()
        event.accept()

    # ------------------------------------------------------------ 测试钩子
    def _debug_state(self) -> dict:
        """测试用快照：模式/停靠边/几何动画是否活跃。"""
        return {
            "mode": self._mode,
            "dock_edge": self.dock_edge,
            "hover_peek": self._hover_peek,
            "animating": self._anim_timer.isActive(),
            "card_visible": self._card_box.isVisible(),
        }
