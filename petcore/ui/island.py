"""灵动岛胶囊窗口（唯一 Qt 面）。

职责只有四件事：**取数**（采样线程的快照）、**组段**（把读数变成
``layout._InfoSeg``）、**排版**（调 ``layout.plan_info_row`` 拿行计划）、**绘制**。
判定逻辑一律在 ``petcore/island/`` 的零 Qt 层里，这里不许再写一套。

三条不变量（改这个文件前先读）：

1. **宽度只由模板驱动**：段的盒宽来自声明样本（``max_text``），读数只在盒内伸缩。
   任何"按当前读数算宽"的写法都会让岛随数字抖。
2. **看不清就不采**：岛隐藏 / 卡片折叠 / 段被折进 ``+N`` / **收进停靠细条**时停掉
   对应采样线程。
3. **点胶囊 = 展开/收起卡片**；``island_click_action=toggle_pet`` 时改为透传点击。

停靠与动画口径（主分支 `pet/dynamic_island.py` 同源）：

* 拖到屏幕边缘 ``DOCK_THRESHOLD`` 内松手 → 收成 ``STRIP_THICKNESS`` 细条；
  悬停滑出预览（``_hover_peek``），光标离开 ``DOCK_BACK_MS`` 后收回，
  收回前必须复检可悬停区，否则细条盖回光标会形成 1s 自持振荡。
* 所有几何变化（停靠/滑出/展开/收起）都走 ``_animate_to``：180ms
  ``ease_out_cubic``、16ms 表、**仅动画期间运行**；拖拽期间几何完全冻结。
* 落点选屏按**松手光标**，不按窗口左上角（跨屏拖拽会"拖过去又弹回来"）。
"""

from __future__ import annotations

import logging
import time
from collections import deque
from dataclasses import dataclass

from PySide6.QtCore import QEvent, QPoint, QPointF, QRect, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import (
    QBitmap,
    QBrush,
    QColor,
    QFont,
    QFontMetrics,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
)
from PySide6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ..island import layout, segments as isl
from ..platform import diskmon, netmon, sysmon
from ..performance import runtime_metrics

logger = logging.getLogger("petcore.ui.island")

#: 胶囊窗口高（固定，永不随内容变）。
CAPSULE_HEIGHT = 44
#: 绘制内缩（左右上下留 6px 给阴影/悬停）。
CAPSULE_INSET = 6
#: 贴边边距。
EDGE_MARGIN = 16
#: 胶囊自然宽下限（极小内容下仍是可点可拖的胶囊）。
MIN_CAPSULE_WIDTH = layout._MIN_CAPSULE_W
#: 卡片宽度区间与自动收起时间。
CARD_MIN_WIDTH = 340
CARD_MAX_WIDTH = 420
CARD_AUTO_COLLAPSE_MS = 15000
#: 贴边停靠：拖到屏幕边缘 ``DOCK_THRESHOLD`` 内松手 → 收成细条；悬停滑出预览。
DOCK_THRESHOLD = layout.DOCK_THRESHOLD_PX
STRIP_THICKNESS = layout.STRIP_THICKNESS_PX
STRIP_SIDE = layout.STRIP_SIDE_PX
DOCK_BACK_MS = 800
#: 几何动画（停靠 / 滑出 / 展开 / 收起共用）：180ms，ease-out-cubic。
GEO_ANIM_SECONDS = 0.18
ANIM_TICK_MS = 16
#: 悬停放大目标与缩放弹簧（主分支 `_hover_scale_target`）：1.02 够被看见，又不会糊字。
HOVER_SCALE = 1.02
SCALE_STIFFNESS = 170.0
SCALE_DAMPING = 15.0
SCALE_MIN = 0.97
SCALE_MAX = 1.03
#: 事件反馈的果冻：竖压横鼓，绕胶囊中心。
SQUISH_STIFFNESS = 150.0
SQUISH_DAMPING = 8.5
SQUISH_MIN = 0.85
SQUISH_MAX = 1.12
SQUISH_V_MAX = 3.5
#: 撞岛定向踢动：绘制层位移弹簧（窗口几何不动）。**必须钳**——窗口只有 44px 高、
#: 上下各留 6px 内缩，位移超过 3px 就会被裁掉或压到边缘像素。
KICK_STIFFNESS = 160.0
KICK_DAMPING = 14.0
KICK_MAX_PX = 3.0
KICK_V_MAX = 220.0
#: 一次撞击的动效时间上限：连撞每击各有段动画，最后一击之后最多再跑这么久。
BUMP_ANIM_MAX_S = 0.9
#: 桌宠让开后被挪走的胶囊，连续这么多个采样拍都无遮挡才回到原位（防抖动）。
AVOID_RESTORE_TICKS = 3
#: 内容行左内边距 / 名字上限与间距（头标已退役，内容从 CONTENT_PAD 直接起排）。
CONTENT_PAD = layout._CONTENT_PAD_PX
NAME_MAX = layout._NAME_MAX_PX
NAME_GAP = layout._NAME_GAP_PX
#: 告警点尺寸（行尾状态点）。
STATUS_DOT = layout._STATUS_DOT_PX
STATUS_GAP = layout._STATUS_GAP_PX
#: 统一基线偏移（胶囊垂直中心下方 5px）。
BASELINE_DY = 5.0
#: 字号层（逻辑 px）。
FONT_LAYERS = {
    "primary": (14, QFont.Weight.DemiBold),
    "value": (13, QFont.Weight.DemiBold),
    "name": (12, QFont.Weight.Normal),
    "label": (10, QFont.Weight.Normal),
    "aux": (11, QFont.Weight.Normal),
}
#: 三套配色（主分支同口径：深浅色不跟系统主题，由配置显式选）。
STYLE_PALETTES = {
    "dark": {
        "bg": (28, 30, 38, 235),
        "text": (235, 238, 245),
        "muted": (160, 170, 190),
        "keyline": (255, 255, 255, 52),
        "divider": (255, 255, 255, 36),
    },
    "light": {
        "bg": (255, 255, 255, 242),
        "text": (31, 35, 40),
        "muted": (107, 114, 128),
        "keyline": (0, 0, 0, 28),
        "divider": (31, 35, 40, 31),
    },
    "glass": {
        "bg": (236, 244, 255, 214),
        "text": (35, 45, 60),
        "muted": (90, 105, 125),
        "keyline": (0, 0, 0, 28),
        "divider": (35, 45, 60, 36),
    },
}
#: 强调色（图标底圈）。
ACCENTS = {
    "blue": "#40b8ff",
    "green": "#34c77b",
    "purple": "#9b7bff",
    "pink": "#ff7ab8",
    "orange": "#ffa94d",
}
ALERT_COLORS = {"warn": "#f0b44c", "crit": "#f2777a"}
#: 磁盘段在胶囊上的标签：明确是 **C 盘**，不是「磁盘占用率」。
DISK_LABEL = "C 盘"
#: 卡片行里的段名（胶囊上是纯 glyph 的段也要有可读名字）。
CARD_NAMES = {
    "network": "网络",
    "cpu": "CPU",
    "mem": "内存",
    "disk": "C 盘剩余",
    "gpu": "GPU",
    "fps": "帧率",
}
#: 主信息段（时间）的测宽样本：定宽形态，时间跳变不推邻居。
TIME_BOX_SAMPLE = "88:88"


@dataclass(frozen=True)
class IslandConfig:
    """一次刷新读到的一组设置（`refresh_from_config()` 现取）。"""

    enabled: bool = False
    show_name: bool = True
    show_status: bool = True
    show_messages: bool = False
    info_mode: str = "time"
    custom_text: str = ""
    style: str = "dark"
    opacity: float = 1.0
    accent: str = "blue"
    click_action: str = "expand"
    metrics: tuple[str, ...] = (isl.CPU, isl.MEM)
    character_name: str = ""
    edge_dock: bool = True

    @classmethod
    def from_config(cls, config, *, character_name: str = "") -> "IslandConfig":
        def value(key, default):
            try:
                raw = config.get(key, default)
            except Exception:  # noqa: BLE001 - 配置异常回默认
                return default
            return default if raw is None else raw

        metrics = value("island_metrics", [])
        if not isinstance(metrics, (list, tuple)):
            metrics = []
        return cls(
            enabled=bool(value("island_enabled", False)),
            show_name=bool(value("island_show_name", True)),
            show_status=bool(value("island_show_status", True)),
            show_messages=bool(value("island_show_messages", False)),
            info_mode=str(value("island_info_mode", "time")),
            custom_text=str(value("island_custom_text", "") or ""),
            style=str(value("island_style", "dark")),
            opacity=float(value("island_opacity", 1.0)),
            accent=str(value("island_accent", "blue")),
            click_action=str(value("island_click_action", "expand")),
            metrics=tuple(str(item) for item in metrics),
            character_name=character_name,
            edge_dock=bool(value("island_edge_dock", True)),
        )


class DynamicIsland(QWidget):
    """顶部胶囊。点它展开卡片，再点收起；拖动可换位置。"""

    clicked = Signal()
    settings_requested = Signal()
    balance_requested = Signal()
    messages_cleared = Signal()
    card_expanded = Signal(bool)

    def __init__(self, config, *, character_name: str = "", parent=None) -> None:
        super().__init__(
            parent,
            Qt.WindowType.Tool
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.WindowDoesNotAcceptFocus,
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setObjectName("dynamicIsland")
        self.config = config

        self.settings = IslandConfig.from_config(config, character_name=character_name)
        self._fonts: dict[str, QFont] = {}
        self._metrics: dict[str, QFontMetrics] = {}
        self._font_key_cache: tuple | None = None
        self._alert: dict[str, str] = {}
        self._row: tuple = ()
        self._recent_messages = deque(maxlen=8)
        self._balance_text = ""
        self._balance_detail = ""
        self._balance_severity = ""
        self._registry = isl.SegmentRegistry()
        # GPU/FPS are optional platform probes.  The island consumes their
        # snapshots through this seam; it never starts a second sampler.
        self._probe_segments: dict[str, isl.SegmentSnapshot] = {}
        self._probe_unavailable: dict[str, str] = {}
        self._probe_sync_callback = None
        self._probe_wanted: frozenset[str] | None = None
        self._plan: layout.ShrinkPlan | None = None
        self._width_key: tuple | None = None
        self._expanded = False
        self._dragging = False
        self._press_global: QPoint | None = None
        self._drag_offset: QPoint | None = None
        self._ticks = 0
        self._avoidance_callback = None
        self._avoid_home: QPoint | None = None
        self._avoid_clear_ticks = 0
        self._feedback_until = 0.0
        # ---- 停靠与动效（主分支 `_mode` 拆成「停靠/滑出/展开」三个布尔，
        # 避免改遍旧的 `_expanded` 分支）
        self._docked = False
        self._hover_peek = False
        self._dock_edge = "none"
        self._geo_from: QRect | None = None
        self._geo_to: QRect | None = None
        self._geo_t = 0.0
        self._anim_last = 0.0
        self._hover_scale = 1.0
        self._hover_scale_v = 0.0
        self._hover_scale_target = 1.0
        self._squish = 1.0
        self._squish_v = 0.0
        self._kick_x = 0.0
        self._kick_y = 0.0
        self._kick_vx = 0.0
        self._kick_vy = 0.0
        self._bump_anim_left = 0.0
        self._geometry_callback = None
        # ---- 五态状态点 / 声纳 / 呼吸 / 内容位图缓存（主分支同源，数值在 layout 里）
        self._agent_state = layout.AGENT_IDLE
        self._agent_sources: dict[str, str] = {}
        self._pet_visible = True
        self._dot_done_until = 0.0
        self._sonar_started = 0.0
        self._breathe_started = 0.0
        self._breathe_until = 0.0
        self._content_pixmap: QPixmap | None = None
        self._content_key: tuple | None = None
        self._content_structure: tuple | None = None
        self._fade_from: QPixmap | None = None
        self._fade_t = 1.0
        self._fade_last = 0.0
        self._fade_masked: QPixmap | None = None
        self._fade_masked_key: tuple | None = None

        self.net_monitor = netmon.NetMonitor(interval=1.0)
        self.sys_monitor = sysmon.SysMonitor(interval=1.0)
        # 磁盘剩余按分钟级变化：5 秒一拍（钳制区间也在 diskmon 里），比这更快纯浪费。
        self.disk_monitor = diskmon.DiskMonitor(
            interval=diskmon.DEFAULT_INTERVAL_SECONDS
        )

        self._clock = QTimer(self)
        self._clock.setInterval(1000)
        self._clock.timeout.connect(self._tick)

        self._card_timer = QTimer(self)
        self._card_timer.setSingleShot(True)
        self._card_timer.setInterval(CARD_AUTO_COLLAPSE_MS)
        self._card_timer.timeout.connect(self.collapse_card)

        self._anim_timer = QTimer(self)
        self._anim_timer.setInterval(ANIM_TICK_MS)
        self._anim_timer.timeout.connect(self._on_anim_tick)

        self._dock_back_timer = QTimer(self)
        self._dock_back_timer.setSingleShot(True)
        self._dock_back_timer.setInterval(DOCK_BACK_MS)
        self._dock_back_timer.timeout.connect(self._dock_back)

        # 完成瞬态（4s 后回落空闲）、工作态声纳（30fps）、内容淡化（16ms）。
        self._dot_done_timer = QTimer(self)
        self._dot_done_timer.setSingleShot(True)
        self._dot_done_timer.setInterval(layout.DOT_DONE_MS)
        self._dot_done_timer.timeout.connect(self._on_dot_done_timeout)

        self._sonar_timer = QTimer(self)
        self._sonar_timer.setInterval(layout.SONAR_TICK_MS)
        self._sonar_timer.timeout.connect(self._on_sonar_tick)

        self._error_timer = QTimer(self)
        self._error_timer.setSingleShot(True)
        self._error_timer.setInterval(layout.AGENT_ERROR_TTL_MS)
        self._error_timer.timeout.connect(self._on_error_timeout)

        self._fade_timer = QTimer(self)
        self._fade_timer.setInterval(layout.CONTENT_FADE_TICK_MS)
        self._fade_timer.timeout.connect(self._on_fade_tick)

        self.card = _IslandCard(self)
        self.card.balance_requested.connect(self.balance_requested.emit)
        self.card.settings_requested.connect(self.settings_requested.emit)
        self.card.messages_cleared.connect(self.clear_messages)
        self.card.hide()

        self._rebuild_row()
        self._apply_position(initial=True)
        self._update_size()

    def register_segment(self, snapshot: isl.SegmentSnapshot, *, release=None) -> None:
        """注册扩展快照，并保留可撤销的资源释放回调。"""
        self._registry.register(snapshot, release=release)
        if self._expanded:
            self._rebuild_card()

    def unregister_segment(self, segment_id: str) -> bool:
        removed = self._registry.unregister(segment_id)
        if removed and self._expanded:
            self._rebuild_card()
        return removed

    def clear_segments(self) -> None:
        self._registry.clear()
        if self._expanded:
            self._rebuild_card()

    def set_probe_snapshot(self, snapshot: isl.SegmentSnapshot) -> None:
        """接收可选 GPU/FPS 探针快照，不在岛内创建采样线程。

        平台探针由宿主按自己的权限和生命周期负责；岛只接受稳定的
        ``SegmentSnapshot``。``unsupported`` 沿用主分支口径：**整段不占位**，
        原因只在卡片里说（占一个 ``--`` 盒会让用户以为指标坏了）。
        """
        if snapshot.id not in (isl.GPU, isl.FPS):
            raise ValueError("only GPU/FPS snapshots are accepted")
        state = snapshot.state if snapshot.state in isl.STATES else isl.STATE_UNKNOWN
        if state == isl.STATE_UNSUPPORTED:
            self._probe_segments.pop(snapshot.id, None)
            self._probe_unavailable[snapshot.id] = str(
                snapshot.detail.get("error_reason") or "当前环境不支持该指标"
            )
        else:
            self._probe_unavailable.pop(snapshot.id, None)
            if state != snapshot.state:
                snapshot = isl.SegmentSnapshot(
                    snapshot.id,
                    label=snapshot.label,
                    text=snapshot.text,
                    max_text=snapshot.max_text,
                    state=state,
                    detail=dict(snapshot.detail),
                )
            self._probe_segments[snapshot.id] = snapshot
        self._rebuild_row()
        self._update_size()
        if self._expanded:
            self._rebuild_card()

    def set_probe_sampling(self, metric_id: str) -> None:
        """把一个 GPU/FPS 段退回「采样中」的定宽占位（停采但保留行宽）。

        停采**不缩行**：盒样本与单位跟真实读数同一套（见 ``segments.PROBE_BOX_TEXT``），
        所以「停 → 起」不会翻转折叠计划，也不会把上一拍的旧读数留在胶囊上。
        真正的「未接入探针」（``unsupported`` / 段被移除）走 :meth:`clear_probe_snapshot`。
        """
        if metric_id not in (isl.GPU, isl.FPS):
            raise ValueError("only GPU/FPS snapshots are accepted")
        self._probe_unavailable.pop(metric_id, None)
        self._probe_segments[metric_id] = isl.SegmentSnapshot(
            metric_id,
            label=CARD_NAMES.get(metric_id, metric_id),
            text=isl.UNKNOWN_TEXT,
            max_text=isl.PROBE_BOX_TEXT[metric_id],
            state=isl.STATE_SAMPLING,
            detail={"unit": isl.PROBE_UNITS[metric_id]},
        )
        self._rebuild_row()
        self._update_size()
        if self._expanded:
            self._rebuild_card()

    def clear_probe_snapshot(self, metric_id: str | None = None) -> None:
        """清除一个或全部可选探针快照并回到「未接入探针」的明确状态。

        只给**关停/停用**用（宿主退出、岛关闭）；正常隐藏/折叠/停采走
        :meth:`set_probe_sampling` 保留行宽，勿用本方法制造宽度跳变。
        """
        if metric_id is None:
            self._probe_segments.clear()
            self._probe_unavailable.clear()
        elif metric_id in (isl.GPU, isl.FPS):
            self._probe_segments.pop(metric_id, None)
            self._probe_unavailable.pop(metric_id, None)
        else:
            return
        self._rebuild_row()
        self._update_size()
        if self._expanded:
            self._rebuild_card()

    def _probe_unavailable_reason(self, metric_id: str) -> str | None:
        reason = self._probe_unavailable.get(metric_id)
        if reason:
            return reason
        if metric_id not in (isl.GPU, isl.FPS):
            return None
        if metric_id in self._probe_segments:
            return None
        return "当前环境未接入可用 GPU/FPS 探针"

    def _feedback(self) -> None:
        """余额/消息到的瞬时反馈：外圈强调 + 一下果冻（竖压横鼓）。"""
        self._feedback_until = time.monotonic() + 0.48
        if not self._expanded:
            self._squish_v = max(-SQUISH_V_MAX, self._squish_v - 1.2)
            self._ensure_anim_timer()
        if self.isVisible():
            self.update()
            QTimer.singleShot(520, self, self.update)

    # ------------------------------------------------------------ 状态与事件（五态点）
    def set_agent_state(self, state: str, *, source: str = "core") -> None:
        """外部状态入口：``idle/working/waiting/error/done``，按**来源**归并。

        ``done`` 是瞬态：点亮 4s 完成绿并做一次「回复」呼吸，不写入来源表。
        ``hidden`` 不接受外部设置——它由 :meth:`set_pet_visible` 派生。

        多来源用 :func:`layout.resolve_agent_state` 归并（错误 > 待确认 > 工作）：
        截图提问收尾发 ``idle`` 不该把仍在流式回答的对话（``working``）一起关掉。
        ``error`` 带 8s TTL：petcore 的错误来源是「一次请求失败」，不像主分支有
        DSH 收敛器把 turn 结束掉，不给 TTL 就会永久红感叹。
        """
        value = str(state or "").strip().lower()
        if value == layout.AGENT_DONE:
            self._agent_sources.pop(str(source), None)
            self._dot_done_until = 0.0
            self._arm_dot_done()
            self.notify_event("reply")
            self._apply_agent_state()
            return
        if value not in layout.AGENT_STATES:
            return
        key = str(source or "core")
        if value == layout.AGENT_IDLE:
            self._agent_sources.pop(key, None)
        else:
            self._agent_sources[key] = value
        if value in (layout.AGENT_ERROR, layout.AGENT_WAITING):
            # 更高优先级的态顶掉完成瞬态：绿灯不许盖在红色感叹上。
            self._dot_done_until = 0.0
            self._dot_done_timer.stop()
        if value == layout.AGENT_ERROR:
            self._error_timer.start()
        else:
            self._error_timer.stop()
        self._apply_agent_state()

    def _apply_agent_state(self) -> None:
        self._agent_state = layout.resolve_agent_state(self._agent_sources.values())
        self._sync_sonar()
        if self.isVisible():
            self.update()

    def _on_error_timeout(self) -> None:
        """错误态到期：只清掉 error 来源，别动别的来源。"""
        for key, value in tuple(self._agent_sources.items()):
            if value == layout.AGENT_ERROR:
                self._agent_sources.pop(key, None)
        self._apply_agent_state()

    def notify_event(self, kind: str = "generic") -> None:
        """事件反馈：一下果冻；``reply`` 额外做 5s 呼吸浮动（主分支同口径）。"""
        impulse = {"reply": 5.0, "balance": 3.5, "tier": 4.0}.get(
            str(kind or "generic"), 3.0
        )
        if not self._expanded:
            # 主分支口径：冲量越大压得越狠（reply 5.0 → 速度 1.4，与旧的定点 1.2 同量级）。
            self._squish_v = max(-SQUISH_V_MAX, self._squish_v - impulse * 0.28)
        if str(kind) == "reply":
            now = time.monotonic()
            self._breathe_started = now
            self._breathe_until = now + layout.BREATHE_SECONDS
        self._ensure_anim_timer()
        if self.isVisible():
            self.update()

    def bump(self, strength: float = 1.0, dir_x: float = 0.0, dir_y: float = 0.0) -> None:
        """被桌宠撞到：果冻冲量 + 定向踢动（只作用于绘制层，窗口几何不动）。

        ``strength`` 由碰撞侧按``接近速度``换算并已在那边钳过上限；这里再钳一次
        （``0.2..3.0``）——多宠连撞会叠加，不钳会把冲量泵到画不出来。
        ``dir_x/dir_y`` 是「岛被顶着的方向」，即桌宠法线取反。
        """
        if self._expanded or self._strip_visible():
            # 展开态是整块面板（内部控件跟着画面抖会糊），细条态一像素内容都不画。
            return
        strength = max(0.2, min(float(strength), 3.0))
        self._squish_v = max(-SQUISH_V_MAX, min(SQUISH_V_MAX, self._squish_v - 1.15 * strength))
        kick_vx = self._kick_vx + float(dir_x) * 110.0 * strength
        kick_vy = self._kick_vy + float(dir_y) * 110.0 * strength
        self._kick_vx = max(-KICK_V_MAX, min(KICK_V_MAX, kick_vx))
        self._kick_vy = max(-KICK_V_MAX, min(KICK_V_MAX, kick_vy))
        self._bump_anim_left = BUMP_ANIM_MAX_S
        self._ensure_anim_timer()
        if self.isVisible():
            self.update()

    def set_pet_visible(self, visible: bool) -> None:
        """桌宠整体显隐（托盘/菜单）：隐藏态的状态点是空心中性色。"""
        value = bool(visible)
        if value == self._pet_visible:
            return
        self._pet_visible = value
        if self.isVisible():
            self.update()

    def _dot_state(self) -> str:
        return layout.dot_state(
            self._agent_state,
            pet_visible=self._pet_visible,
            done=self._dot_done_until > time.monotonic(),
        )

    def dot_style(self) -> tuple[str, str]:
        return layout.dot_style(self._dot_state())

    def _dot_color(self) -> QColor:
        _shape, role = self.dot_style()
        if role == "error":
            return QColor(layout.DOT_ERROR_COLOR)
        if role == "done":
            return QColor(layout.DOT_DONE_COLOR)
        if role == "accent":
            return self.accent_color()
        return QColor(*self._palette()["muted"])

    def _arm_dot_done(self) -> None:
        if not self.isVisible():
            # 看不见就别点表：4s 后到点也让用户看不到，白起一次定时器。
            self._dot_done_until = 0.0
            return
        self._dot_done_until = time.monotonic() + layout.DOT_DONE_MS / 1000.0
        self._dot_done_timer.start()
        self.update()

    def _on_dot_done_timeout(self) -> None:
        self._dot_done_until = 0.0
        if self.isVisible():
            self.update()

    # ------------------------------------------------------------ 声纳环
    def _sonar_active(self) -> bool:
        return (
            self.isVisible()
            and not self._expanded
            and not self._strip_visible()
            and self.settings.show_status
            and self._dot_state() == layout.AGENT_WORKING
        )

    def _sync_sonar(self) -> None:
        if self._sonar_active():
            if not self._sonar_timer.isActive():
                self._sonar_started = time.monotonic()
                self._sonar_timer.start()
        elif self._sonar_timer.isActive():
            self._sonar_timer.stop()

    def _on_sonar_tick(self) -> None:
        if not self._sonar_active():
            self._sonar_timer.stop()
            return
        self.update()

    def show_balance(self, result=None, error: str | None = None) -> None:
        """余额结果只写余额面（卡片行 + 告警级 + 气泡），**不动状态点**。

        状态点由 :meth:`set_agent_state` 的来源表管；余额失败在岛卡片里已经是一行
        红字，若再写状态点，一次没配服务的点击就会把红感叹永久钉住。
        """
        if error:
            self._balance_text = "余额不可用"
            self._balance_detail = str(error)[:120]
            self._balance_severity = isl.ALERT_CRIT
        elif result is not None:
            formatter = getattr(result, "format_text", None)
            self._balance_text = str(formatter() if callable(formatter) else result)[:160]
            self._balance_detail = "已更新"
            self._balance_severity = ""
            self.notify_event("balance")
        self._feedback()
        if self._expanded:
            self._rebuild_card()

    def add_message(self, text: str) -> None:
        if not self.settings.show_messages:
            # 消息显示关掉 = 岛不接收消息：不进历史、也不弹事件果冻（否则用户会看到
            # 「关了还在动」）。胶囊读数与余额反馈不受影响。
            return
        message = " ".join(str(text or "").split())[:240]
        if not message:
            return
        self._recent_messages.append(message)
        self.notify_event("generic")
        self._feedback()
        if self._expanded:
            self._rebuild_card()

    def clear_messages(self) -> None:
        self._recent_messages.clear()
        if self._expanded:
            self._rebuild_card()
        self.messages_cleared.emit()

    # ------------------------------------------------------------ 生命周期

    def start(self) -> None:
        self._clock.start()
        self._card_timer.stop()
        self._sync_sampling()

    def stop(self) -> None:
        self._feedback_until = 0.0
        self._anim_timer.stop()
        self._dock_back_timer.stop()
        self._sonar_timer.stop()
        self._dot_done_timer.stop()
        self._error_timer.stop()
        self._stop_content_fade()
        self._dot_done_until = 0.0
        self._agent_sources.clear()
        self._agent_state = layout.AGENT_IDLE
        self._breathe_until = 0.0
        self._breathe_started = 0.0
        self._geo_from = None
        self._geo_to = None
        self._stop_hover_animation()
        self._clock.stop()
        self._card_timer.stop()
        self.net_monitor.stop()
        self.sys_monitor.stop()
        self.disk_monitor.stop()
        # 收口必须当场通知宿主：GPU/FPS 的启停归宿主，只在心跳里补一次等于白采一秒
        # （ETW 会话/PDH query 都在别人手上）。隐藏后 `visible` 为假，回调收到空集。
        self._sync_sampling()

    def set_visible(self, visible: bool) -> None:
        if visible:
            if not self.isVisible():
                self.show()
                self.start()
            return
        if self.isVisible():
            self.hide()
        # 已经隐藏也要收口（hide() 不一定会再触发 hideEvent）：停采是幂等的。
        self.stop()

    def refresh_from_config(self) -> None:
        """设置改完调用：重读配置、重排段、按需重进采样。"""
        self.settings = IslandConfig.from_config(
            self.config, character_name=self.settings.character_name
        )
        if not self.settings.edge_dock and self._docked:
            # 关掉「贴边停靠」= 立刻退出细条：细条只有 16px，留在那里等于岛消失。
            self._docked = False
            self._hover_peek = False
            self._dock_back_timer.stop()
            self._apply_fixed_size()
            self._set_free_geometry(self._target_rect())
        self._rebuild_row()
        self._update_size()
        self._sync_sampling()
        self._content_key = None
        self._sync_sonar()
        if self._expanded:
            self._rebuild_card()

    def _sync_sampling(self) -> None:
        """看不清就不采：岛隐藏、段没开、段被折叠、收进停靠细条时都停样本。"""
        visible = (
            self.isVisible()
            and self._clock.isActive()
            and self.settings.enabled
            # 细条只有 16px：内容一个像素都不画，采样全是白烧。
            and not self._strip_visible()
        )
        # 「看不清就不采」只对**胶囊**成立：胶囊上被折进 `+N` 的段确实看不见。
        # 卡片态每段都有自己的明细行，折行只是排版，再据此停采会让推快照 →
        # 折行 → 停采 → 清快照 → 复原变成 1Hz 振荡（审计 AUD-25）。
        folded = set() if self._expanded else (set(self._plan.folded) if self._plan is not None else set())
        wants_net = (
            visible
            and isl.NETWORK in self.settings.metrics
            and isl.NETWORK not in folded
        )
        wants_sys = visible and any(
            metric in self.settings.metrics and metric not in folded
            for metric in (isl.CPU, isl.MEM)
        )
        wants_disk = visible and isl.DISK in self.settings.metrics and isl.DISK not in folded
        for monitor, wanted in (
            (self.net_monitor, wants_net),
            (self.sys_monitor, wants_sys),
            (self.disk_monitor, wants_disk),
        ):
            # Reconcile desired state, not thread liveness: a cancelled OS reader
            # may still be exiting. Retrying start on the next tick is safe.
            if wanted:
                monitor.start()
            else:
                monitor.stop()
        # GPU/FPS 探针由宿主持有（权限、子进程、线程边界都在宿主侧），岛只报
        # 「现在还需要采哪些段」；空集就等于全部停采。
        wanted_probes = frozenset(
            metric
            for metric in (isl.GPU, isl.FPS)
            if visible and metric in self.settings.metrics and metric not in folded
        )
        if wanted_probes != self._probe_wanted:
            self._probe_wanted = wanted_probes
            callback = self._probe_sync_callback
            if callable(callback):
                try:
                    callback(wanted_probes)
                except Exception:  # noqa: BLE001 - 探针同步失败不得影响绘制
                    logger.debug("island probe sync failed", exc_info=True)

    def set_probe_sync_callback(self, callback) -> None:
        """注册 GPU/FPS 启停回调：参数是当前仍需要采样的段 id 集合。"""
        self._probe_sync_callback = callback
        self._probe_wanted = None
        # 接上宿主 = 探针段从此按「定宽占位 ↔ 读数」呈现：行必须按新口径重建一次，
        # 否则接线前算出的行里根本没有这两段，要等下一次心跳才补上。
        self._rebuild_row()
        self._update_size()
        self._sync_sampling()

    # ------------------------------------------------------------ 字体与配色

    def _font(self, layer: str) -> QFont:
        font = self._fonts.get(layer)
        if font is None:
            size, weight = FONT_LAYERS[layer]
            font = QFont(self.font())
            font.setPixelSize(int(size))
            font.setWeight(weight)
            self._fonts[layer] = font
        return font

    def _fm(self, layer: str) -> QFontMetrics:
        metrics = self._metrics.get(layer)
        if metrics is None:
            metrics = QFontMetrics(self._font(layer))
            self._metrics[layer] = metrics
        return metrics

    def _font_key(self) -> tuple:
        """字体指纹：任一层度量变化（字号/家族/DPI）就换键 → 盒宽重测。"""
        if self._font_key_cache is None:
            self._font_key_cache = tuple(
                isl.font_metrics_key(self._fm(layer))
                for layer in ("primary", "value", "label", "name")
            )
        return self._font_key_cache

    def _palette(self) -> dict[str, tuple[int, int, int, int]]:
        palette = dict(STYLE_PALETTES.get(self.settings.style, STYLE_PALETTES["dark"]))
        alpha = max(0.4, min(1.0, float(self.settings.opacity)))
        bg = palette["bg"]
        palette["bg"] = (bg[0], bg[1], bg[2], int(round(bg[3] * alpha)))
        return palette

    def accent_color(self) -> QColor:
        return QColor(ACCENTS.get(self.settings.accent, ACCENTS["blue"]))

    # ------------------------------------------------------------ 组段

    def _now_text(self) -> str:
        if self.settings.info_mode == "custom":
            return self.settings.custom_text.strip()[:80]
        return time.strftime("%H:%M")

    def _primary_segment(self) -> layout._InfoSeg:
        text = self._now_text()
        box = TIME_BOX_SAMPLE
        elide = 0
        if self.settings.info_mode == "custom":
            box = text or "88:88"
            elide = 96
        return layout._InfoSeg(
            kind=layout.KIND_PRIMARY,
            seg_id="primary",
            label="",
            text=text or "--:--",
            box_text=box,
            state=isl.STATE_OK,
            elide_px=elide,
        )

    def _percent_segment(self, metric_id: str) -> layout._InfoSeg:
        sample = self.sys_monitor.snapshot()
        label = layout._DEVICE_LABELS.get(metric_id, metric_id)
        value = sample.cpu_percent if metric_id == isl.CPU else sample.mem_percent
        if sample.error and value is None:
            return layout._InfoSeg(
                kind=layout.KIND_METRIC,
                seg_id=metric_id,
                label=label,
                text=sysmon.UNKNOWN_TEXT,
                box_text=sysmon.MAX_TEXT.rstrip("%"),
                state=isl.STATE_ERROR,
                unit=isl.PERCENT_UNIT,
                detail={"error_reason": sample.error},
            )
        state = isl.STATE_OK if value is not None else isl.STATE_SAMPLING
        rendered = sysmon.format_cpu(value) if metric_id == isl.CPU else sysmon.format_mem(value)
        number, unit = isl.split_value_unit(rendered, isl.PERCENT_UNIT)
        self._alert[metric_id] = isl.alert_severity(
            metric_id, value, self._alert.get(metric_id, "")
        )
        return layout._InfoSeg(
            kind=layout.KIND_METRIC,
            seg_id=metric_id,
            label=label,
            text=number,
            box_text=sysmon.MAX_TEXT.rstrip("%"),
            state=state,
            unit=unit if unit else isl.PERCENT_UNIT,
            severity=self._alert[metric_id],
        )

    def _network_segment(self) -> layout._InfoSeg:
        sample = self.net_monitor.snapshot()
        state = isl.STATE_OK if sample is not None else isl.STATE_SAMPLING
        text = sample.text if sample is not None else ""
        return layout._InfoSeg(
            kind=layout.KIND_METRIC,
            seg_id=isl.NETWORK,
            label="",
            text=text or "",
            box_text=netmon.NETWORK_BOX_SAMPLE,
            state=state,
        )

    def _disk_segment(self) -> layout._InfoSeg:
        """C 盘剩余：单盒（``412G``，单位不随读数在 G/T 间跳，行宽因此恒定）。"""
        sample = self.disk_monitor.snapshot()
        if sample is None:
            return layout._InfoSeg(
                kind=layout.KIND_METRIC,
                seg_id=isl.DISK,
                label=DISK_LABEL,
                text="",
                box_text=diskmon.FREE_FIELD_SAMPLE,
                state=isl.STATE_SAMPLING,
            )
        if sample.free_bytes is None:
            return layout._InfoSeg(
                kind=layout.KIND_METRIC,
                seg_id=isl.DISK,
                label=DISK_LABEL,
                text=diskmon.UNKNOWN_TEXT,
                box_text=diskmon.FREE_FIELD_SAMPLE,
                state=isl.STATE_ERROR,
                detail={"error_reason": sample.error or "读不到磁盘容量"},
            )
        return layout._InfoSeg(
            kind=layout.KIND_METRIC,
            seg_id=isl.DISK,
            label=DISK_LABEL,
            text=sample.text,
            box_text=diskmon.FREE_FIELD_SAMPLE,
            state=isl.STATE_OK,
            detail={"note": sample.note} if sample.note else {},
        )

    def _metric_segments(self) -> tuple:
        row = []
        for metric_id in self.settings.metrics:
            if metric_id == isl.CPU or metric_id == isl.MEM:
                row.append(self._percent_segment(metric_id))
            elif metric_id == isl.NETWORK:
                row.append(self._network_segment())
            elif metric_id == isl.DISK:
                row.append(self._disk_segment())
            elif metric_id in (isl.GPU, isl.FPS):
                snapshot = self._probe_segments.get(metric_id)
                if snapshot is None:
                    if metric_id in self._probe_unavailable:
                        # 整机没有这个能力（unsupported）：不占位，原因只在卡片里说一行。
                        continue
                    if not callable(self._probe_sync_callback):
                        # 宿主没接探针（旧装配 / 接线失败）：按「未接入」不占位，卡片说原因。
                        continue
                    # 段已启用但**还没有**第一份快照（或刚停采）：照样占一行定宽
                    # 占位。行宽必须与有读数时逐像素相同，否则「快照到 → 变宽 →
                    # 折进 +N → 停采 → 变窄 → 复原」会变成 1Hz 的折叠振荡。
                    row.append(
                        layout._InfoSeg(
                            kind=layout.KIND_METRIC,
                            seg_id=metric_id,
                            label=CARD_NAMES.get(metric_id, metric_id),
                            text=isl.UNKNOWN_TEXT,
                            box_text=isl.PROBE_BOX_TEXT[metric_id],
                            state=isl.STATE_SAMPLING,
                            unit=isl.PROBE_UNITS[metric_id],
                            detail={},
                        )
                    )
                    continue
                state = snapshot.state if snapshot.state in isl.STATES else isl.STATE_UNKNOWN
                if state == isl.STATE_UNSUPPORTED:
                    # 主分支口径：不支持就不占位，原因只在卡片里说一行。
                    continue
                detail = dict(snapshot.detail)
                if state != isl.STATE_OK:
                    detail.setdefault("error_reason", "GPU/FPS 探针暂不可用")
                row.append(
                    layout._InfoSeg(
                        kind=layout.KIND_METRIC,
                        seg_id=metric_id,
                        label=snapshot.label or CARD_NAMES.get(metric_id, metric_id),
                        text=snapshot.text if state == isl.STATE_OK else "--",
                        box_text=snapshot.max_text or isl.PROBE_BOX_TEXT[metric_id],
                        state=state,
                        # 单位盒宽也是行宽的一部分：探针段永远留出该段的单位盒，
                        # 快照忘了声明单位时也不许比占位窄（两者必须同宽）。
                        unit=str(detail.get("unit") or isl.PROBE_UNITS[metric_id]),
                        detail=detail,
                        severity=str(detail.get("severity") or ""),
                    )
                )
            # 未知 id 不占位。
        return tuple(row)

    def _lead_trail(self, name_text: str) -> tuple[float, float]:
        lead = CONTENT_PAD
        trail = CONTENT_PAD
        if self.settings.show_status:
            trail = STATUS_GAP + STATUS_DOT + STATUS_GAP
        return float(lead), float(trail)

    def _name_text(self) -> str:
        if not self.settings.show_name:
            return ""
        return str(self.settings.character_name or "").strip()

    def _rebuild_row(self) -> None:
        self._row = (self._primary_segment(),) + self._metric_segments()

    def _available_width(self) -> int:
        screen = self._screen()
        if screen is None:
            return 0
        return max(0, screen.availableGeometry().width() - 2 * EDGE_MARGIN)

    def _screen(self):
        from PySide6.QtGui import QGuiApplication

        handle = self.windowHandle()
        screen = handle.screen() if handle is not None else None
        if screen is None:
            screen = QGuiApplication.primaryScreen()
        return screen

    def _screen_at(self, point: QPoint):
        """按**点**选屏（拖拽松手的落点）：``screenAt`` 命中不到时回退主屏。

        不能用窗口左上角选屏：跨屏拖拽的触发带恰等于抓取偏移量，光标刚过屏缝
        松手时左上角仍在原屏，会把整窗夹回原屏并弹回（主分支 issue #154）。
        """
        from PySide6.QtGui import QGuiApplication

        screen = QGuiApplication.screenAt(point) if point is not None else None
        return screen or QGuiApplication.primaryScreen()

    def _compute_plan(self, available_w: int | None = None) -> layout.ShrinkPlan:
        lead, trail = self._lead_trail(self._name_text())
        return layout.plan_info_row(
            self._row,
            lead_w=lead,
            trail_w=trail,
            name_text=self._name_text(),
            available_w=self._available_width() if available_w is None else int(available_w),
            primary_fm=self._fm("primary"),
            value_fm=self._fm("value"),
            label_fm=self._fm("label"),
            name_fm=self._fm("name"),
        )

    def _width_key_now(self) -> tuple:
        return (
            self._font_key(),
            self.settings.show_name,
            self.settings.show_status,
            self.settings.info_mode,
            self.settings.metrics,
            self._name_text(),
            self._available_width() // 4,  # 屏宽变化到 4px 粒度才重排
        )

    def _update_size(self) -> None:
        if self._dragging:
            # 拖拽期间几何完全冻结（主分支 `_update_size` 首行）：否则岛会在光标
            # 底下随换屏/读数抽动，抓取偏移量失效、手感变成「甩不掉」。
            return
        first_pass = self._width_key is None
        key = self._width_key_now()
        self._plan = self._compute_plan(self._card_budget() if self._expanded else None)
        self._width_key = key
        if self._expanded:
            self._apply_card_size()
        elif self._strip_visible():
            self._apply_fixed_size()
            if self._geo_to is None:
                self._set_free_geometry(self._target_rect())
        else:
            target_width = max(MIN_CAPSULE_WIDTH, self._plan.width)
            screen = self._screen()
            area = screen.availableGeometry() if screen is not None else None
            x = self.x()
            if area is not None and not first_pass:
                # 三分区锚点：贴左/贴右/居中各自固定一条边，避免「长出屏再弹回」。
                # 首次定型（__init__）跳过：那时 self.width() 还是默认值，位置由
                # `_apply_position()` 已经摆好，再锚一次会把恢复的位置挪走。
                x = layout.anchored_x(
                    x=self.x(),
                    width=self.width(),
                    new_width=target_width,
                    screen_left=area.x(),
                    screen_width=area.width(),
                )
            self.setFixedSize(QSize(target_width, CAPSULE_HEIGHT))
            if area is not None:
                self.move(x, self.y())
                self._clamp_to_screen()
        self._sync_sampling()

    def _tick(self) -> None:
        self._ticks += 1
        runtime_metrics.inc("ui_updates")
        previous_key = self._width_key
        self._rebuild_row()
        self._plan = self._compute_plan(self._card_budget() if self._expanded else None)
        if self._width_key_now() != previous_key:
            self._update_size()
        elif self._expanded:
            self._rebuild_card()
        else:
            # 只有「宽度没变、沿用现有盒宽」的拍才算合并更新；宽度重算另计。
            runtime_metrics.inc("coalesced_updates")
            self.update()
        self._sync_sampling()
        self._sync_sonar()
        callback = self._avoidance_callback
        if callable(callback):
            try:
                callback(self)
            except Exception:  # noqa: BLE001 - 避让失败不得影响采样和绘制
                logger.debug("island avoidance failed", exc_info=True)

    def set_avoidance_callback(self, callback) -> None:
        """设置轻量的宿主避让回调；回调只应读取窗口几何并调用 :meth:`avoid_overlaps`."""
        self._avoidance_callback = callback

    def avoid_overlaps(self, obstacles) -> bool:
        """在不移动桌宠的前提下把灵动岛挪开，桌宠走开后自动回到原位。

        候选点只来自当前屏幕四角；没有安全候选时保持原位置并返回 ``False``。
        自动挪动**不落盘**：``island_x/island_y`` 只记录用户自己摆的位置，否则
        桌宠路过一次就会把用户摆的胶囊永久挪到角落并写进配置。
        这是几何避让，不进入桌宠碰撞/物理模型。
        """
        if not self.isVisible():
            return True
        if self._dragging or self._docked:
            # 用户手里 / 已停靠在边上：自动避让会与拖拽抢 move()，也会把细条推离边缘。
            return True
        obstacles = tuple(rect for rect in obstacles if rect is not None and not rect.isNull())
        current = self.geometry()
        blocked = any(current.intersects(rect) for rect in obstacles)
        home = self._avoid_home
        if home is not None and not blocked:
            if any(QRect(home, self.size()).intersects(rect) for rect in obstacles):
                self._avoid_clear_ticks = 0
                return True
            self._avoid_clear_ticks += 1
            if self._avoid_clear_ticks >= AVOID_RESTORE_TICKS:
                self._avoid_home = None
                self._avoid_clear_ticks = 0
                self.move(home)
            return True
        if not blocked:
            return True
        point = self._free_corner(obstacles)
        if point is None:
            return False
        if home is None:
            self._avoid_home = QPoint(current.topLeft())
        self._avoid_clear_ticks = 0
        if point != current.topLeft():
            self.move(point)
        return True

    def _free_corner(self, obstacles) -> QPoint | None:
        screen = self._screen()
        if screen is None:
            return None
        area = screen.availableGeometry()
        for point in (
            QPoint(area.left(), area.top()),
            QPoint(area.right() - self.width() + 1, area.top()),
            QPoint(area.left(), area.bottom() - self.height() + 1),
            QPoint(area.right() - self.width() + 1, area.bottom() - self.height() + 1),
        ):
            candidate = QRect(point, self.size())
            if not area.contains(candidate):
                continue
            if any(candidate.intersects(rect) for rect in obstacles):
                continue
            return point
        return None

    # ------------------------------------------------------------ 位置

    def _apply_position(self, *, initial: bool = False) -> None:
        screen = self._screen()
        if screen is None:
            return
        area = screen.availableGeometry()
        saved_x = self._config_int("island_x")
        saved_y = self._config_int("island_y")
        if saved_x is not None and saved_y is not None:
            x, y = saved_x, saved_y
        else:
            x = area.x() + area.width() - self.width() - EDGE_MARGIN
            y = area.y() + EDGE_MARGIN
        self.move(self._clamp_point(x, y, area))
        # 读回上次停靠边：开关关着 / 值非法都退化为 none（存储值保留）。
        saved_edge = "none"
        try:
            raw = self.config.get("island_dock_edge", "none")
        except Exception:  # noqa: BLE001
            raw = "none"
        if str(raw or "none") in layout.DOCK_EDGES and str(raw) != "none":
            saved_edge = str(raw)
        self._dock_edge = saved_edge
        self._docked = saved_edge != "none" and self.settings.edge_dock
        if initial:
            self.update()
        if self._strip_visible():
            self._apply_fixed_size()
            self._set_free_geometry(self._target_rect())

    @staticmethod
    def _clamp_point(x: int, y: int, area) -> QPoint:
        x = max(area.x(), min(area.x() + area.width() - 1, int(x)))
        y = max(area.y(), min(area.y() + area.height() - 1, int(y)))
        return QPoint(x, y)

    def _clamp_to_screen(self) -> None:
        if self._strip_visible():
            # 细条本来就贴着屏幕边，钳制会把它推离边缘（主分支同口径）。
            return
        screen = self._screen()
        if screen is None:
            return
        area = screen.availableGeometry()
        x = max(area.x(), min(area.x() + area.width() - self.width(), self.x()))
        y = max(area.y(), min(area.y() + area.height() - self.height(), self.y()))
        if (x, y) != (self.x(), self.y()):
            self.move(x, y)
            self._emit_geometry_changed()

    def _config_int(self, key: str) -> int | None:
        try:
            value = self.config.get(key, None)
        except Exception:  # noqa: BLE001
            return None
        if value is None:
            return None
        try:
            return int(round(float(value)))
        except (TypeError, ValueError):
            return None

    def _save_position(self) -> None:
        screen = self._screen()
        x, y = self.x(), self.y()
        # 停靠态存的是**胶囊滑出位**：细条的 x/y 由 dock_edge + 屏幕推出来，
        # 把细条坐标写进配置会让下次启动在「细条位」上再判一次停靠。
        # 另外停靠时跳过 12px 贴边吸附——细条本来就在边上，吸附会改写用户落点。
        if screen is not None and self.dock_edge == "none":
            area = screen.availableGeometry()
            if abs(x - area.x()) <= 12:
                x = area.x()
            elif abs((x + self.width()) - (area.x() + area.width())) <= 12:
                x = area.x() + area.width() - self.width()
            if abs(y - area.y()) <= 12:
                y = area.y()
            elif abs((y + self.height()) - (area.y() + area.height())) <= 12:
                y = area.y() + area.height() - self.height()
            if (x, y) != (self.x(), self.y()):
                self.move(x, y)
        for key, value in (("island_x", x), ("island_y", y)):
            try:
                self.config.set(key, int(value))
            except Exception:  # noqa: BLE001 - 位置保存失败不该影响岛
                pass
        try:
            self.config.save()
        except Exception:  # noqa: BLE001
            pass

    def _save_dock_edge(self, edge: str) -> None:
        """落盘停靠边（白名单校验）；``none`` 也要写，否则下次启动会复活旧停靠。"""
        value = edge if edge in layout.DOCK_EDGES else "none"
        self._dock_edge = value
        self._docked = value != "none" and self.settings.edge_dock
        try:
            self.config.set("island_dock_edge", value)
            self.config.save()
        except Exception:  # noqa: BLE001 - 停靠写盘失败不该影响岛
            logger.debug("island dock edge save failed", exc_info=True)

    # ------------------------------------------------------------ 停靠与几何动画

    @property
    def dock_edge(self) -> str:
        """当前生效的停靠边：开关关掉或不在停靠态时一律 ``none``（存储值保留）。"""
        if not self.settings.edge_dock or not self._docked:
            return "none"
        return self._dock_edge

    def dock_state(self) -> dict:
        """测试/诊断快照（不含配置对象）。"""
        return {
            "docked": self._docked,
            "edge": self.dock_edge,
            "strip": self._strip_visible(),
            "peek": self._hover_peek,
            "expanded": self._expanded,
            "animating": self._anim_timer.isActive(),
            "dragging": self._dragging,
        }

    @property
    def dragging(self) -> bool:
        """用户正拿着胶囊拖（碰撞侧据此采样岛速、并在松手那一拍作废基线）。"""
        return bool(self._dragging)

    @property
    def geometry_animating(self) -> bool:
        """几何插值中（停靠/滑出/展开/收起）：此刻的位移不是拖拽。"""
        return self._geo_to is not None

    def collision_rect(self) -> tuple[int, int, int, int] | None:
        """碰撞体矩形：**只覆盖胶囊本体**，卡片区域不是幽灵墙。

        不可见 / 未启用 / 停靠细条 / 几何动画中返回 ``None``——细条 16×64 被当成
        直径 44 的胶囊会把贴边桌宠挤走；动画峰值上千 px/s 会凭空拍飞静止桌宠。
        返回的是屏幕坐标（与桌宠 ``body_rect()`` 同一坐标系），窗口层只做 QRect
        互转，判定在 ``petcore/engine/island_field.py``。
        """
        if not self.settings.enabled or not self.isVisible():
            return None
        if self._strip_visible() or self._geo_to is not None:
            return None
        rect = self.geometry()
        return (
            int(rect.x()),
            int(rect.y()),
            int(rect.width()),
            int(min(rect.height(), CAPSULE_HEIGHT)),
        )

    def _strip_visible(self) -> bool:
        """细条态 = 已停靠（开关也开着）+ 没滑出 + 没展开；内容一个像素都不画。"""
        if not self._docked or self._hover_peek or self._expanded:
            return False
        # 开关关掉时 `dock_edge` 退化为 none：此时按自由态处理，别把岛锁在 16px。
        return self.dock_edge != "none"

    def _strip_length(self) -> int:
        screen = self._screen_at(self.pos())
        area = screen.availableGeometry() if screen is not None else None
        length = STRIP_SIDE if area is None else min(STRIP_SIDE, area.height())
        return max(STRIP_THICKNESS, int(length))

    def _rest_size(self) -> QSize:
        if self._expanded:
            return QSize(
                self._card_width(),
                CAPSULE_HEIGHT + self.card.sizeHint().height() + 2 * CAPSULE_INSET,
            )
        width = self._plan.width if self._plan is not None else MIN_CAPSULE_WIDTH
        return QSize(max(MIN_CAPSULE_WIDTH, int(width)), CAPSULE_HEIGHT)

    def _apply_fixed_size(self) -> None:
        """按当前模式锁窗口尺寸（细条 16 厚；其余用 ``_rest_size``）。"""
        size = self._rest_size()
        if self._strip_visible():
            edge = self.dock_edge
            if edge in ("top", "bottom"):
                size = QSize(size.width(), STRIP_THICKNESS)
            elif edge in ("left", "right"):
                size = QSize(STRIP_THICKNESS, self._strip_length())
        self.setFixedSize(size)

    def _clamp_rect(self, rect: QRect, area) -> QRect:
        x = max(area.left(), min(rect.x(), area.right() - rect.width() + 1))
        y = max(area.top(), min(rect.y(), area.bottom() - rect.height() + 1))
        return QRect(x, y, rect.width(), rect.height())

    def _target_rect(self) -> QRect:
        """目标几何：细条贴边 / 从停靠边向内展开 / 自由态夹回屏幕。"""
        size = self._rest_size()
        screen = self._screen_at(self.pos()) or self._screen()
        area = screen.availableGeometry() if screen is not None else None
        if area is None:
            return QRect(self.pos(), size)
        rect = QRect(self.pos(), size)
        if self._strip_visible():
            # 二次居中：调用时窗口可能已经是 64 高的细条，直接拿左上角拼参考矩形
            # 会让中心凭空高 (64-44)/2 = 10px，每次收回细条都上跳一截。
            rect.moveTop(self.y() + self.height() // 2 - size.height() // 2)
            x, y, w, h = layout.strip_rect_for(
                (rect.x(), rect.y(), rect.width(), rect.height()),
                self.dock_edge,
                (area.x(), area.y(), area.width(), area.height()),
                thickness=STRIP_THICKNESS,
                side=STRIP_SIDE,
            )
            return QRect(x, y, w, h)
        if self.dock_edge != "none" and self._expanded:
            if self.dock_edge in ("top", "bottom"):
                rect.moveTop(
                    area.top()
                    if self.dock_edge == "top"
                    else area.bottom() - size.height() + 1
                )
            else:
                rect.moveLeft(
                    area.left()
                    if self.dock_edge == "left"
                    else area.right() - size.width() + 1
                )
                rect.moveTop(self.y() + self.height() // 2 - size.height() // 2)
        return self._clamp_rect(rect, area)

    def set_geometry_changed_callback(self, callback) -> None:
        """几何每帧变化时的回调（碰撞体重提交用）：必须空转安全、幂等。"""
        self._geometry_callback = callback

    def _emit_geometry_changed(self) -> None:
        callback = getattr(self, "_geometry_callback", None)
        if not callable(callback):
            return
        try:
            callback()
        except Exception:  # noqa: BLE001 - 回调失败不该打断动画
            logger.debug("island geometry callback failed", exc_info=True)

    def _set_free_geometry(self, rect: QRect) -> None:
        # 动画必须解锁固定尺寸，否则 setMinimumSize == setMaximumSize 会把
        # setGeometry 钳回原尺寸（动画看起来"完全没动"）。
        self.setMinimumSize(0, 0)
        self.setMaximumSize(16777215, 16777215)
        self.setGeometry(rect)
        self._emit_geometry_changed()

    def _animate_to(self, target: QRect) -> None:
        if not self.isVisible():
            # 隐藏态没有事件循环之外的帧：直接落位，否则 _geo_to 会永远停在半路，
            # 下次显示时从一个不存在的中途几何开始。
            self._geo_from = None
            self._geo_to = None
            self._set_free_geometry(QRect(target))
            self._apply_fixed_size()
            return
        if QRect(target) == self.geometry() and not self._anim_timer.isActive():
            self._set_free_geometry(QRect(target))
            self._apply_fixed_size()
            return
        self._geo_from = QRect(self.geometry())
        self._geo_to = QRect(target)
        self._geo_t = 0.0
        self._ensure_anim_timer()

    def _ensure_anim_timer(self) -> None:
        if not self.isVisible():
            return  # 隐藏即休眠：不可见时不起表
        if not self._anim_timer.isActive():
            self._anim_last = time.monotonic()
            self._anim_timer.start()

    def _animating(self) -> bool:
        if self._geo_to is not None:
            return True
        if not self._expanded and time.monotonic() < self._breathe_until:
            return True
        if self._expanded:
            return False
        return (
            abs(self._hover_scale - self._hover_scale_target) > 0.001
            or abs(self._hover_scale_v) > 0.005
            or abs(self._squish - 1.0) > 0.01
            or abs(self._squish_v) > 0.06
            or abs(self._kick_x) > 0.05
            or abs(self._kick_y) > 0.05
            or abs(self._kick_vx) > 0.5
            or abs(self._kick_vy) > 0.5
            or self._bump_anim_left > 0.0
        )

    def _on_anim_tick(self) -> None:
        now = time.monotonic()
        dt = max(0.0, min(0.05, now - self._anim_last))
        self._anim_last = now
        changed = False
        if self._geo_from is not None and self._geo_to is not None:
            self._geo_t = min(1.0, self._geo_t + dt / GEO_ANIM_SECONDS)
            k = layout.ease_out_cubic(self._geo_t)
            start, end = self._geo_from, self._geo_to
            rect = QRect(
                round(start.x() + (end.x() - start.x()) * k),
                round(start.y() + (end.y() - start.y()) * k),
                round(start.width() + (end.width() - start.width()) * k),
                round(start.height() + (end.height() - start.height()) * k),
            )
            self._set_free_geometry(rect)
            changed = True
            if self._geo_t >= 1.0:
                self._geo_from = None
                self._geo_to = None
                # 终点再对齐一次（幂等）：动画途中屏可能变了。
                self._set_free_geometry(self._target_rect())
                self._apply_fixed_size()
                # 落定再补发一拍：动画期间碰撞墙是关的（速度不可信），结束这一拍
                # 必须让宿主重算一次，否则被动画覆盖的桌宠要等到下次移动才被推出。
                self._emit_geometry_changed()
        # 悬停缩放弹簧（展开态整窗缩放会切卡片，目标强制 1.0）
        scale_target = 1.0 if self._expanded else self._hover_scale_target
        self._hover_scale_v += (
            (scale_target - self._hover_scale) * SCALE_STIFFNESS
            - self._hover_scale_v * SCALE_DAMPING
        ) * dt
        self._hover_scale = max(SCALE_MIN, min(SCALE_MAX, self._hover_scale + self._hover_scale_v * dt))
        # 果冻弹簧：竖压横鼓（横向只鼓不缩）
        if not self._expanded:
            self._squish_v += (
                (1.0 - self._squish) * SQUISH_STIFFNESS - self._squish_v * SQUISH_DAMPING
            ) * dt
            self._squish = max(SQUISH_MIN, min(SQUISH_MAX, self._squish + self._squish_v * dt))
            # 撞岛定向踢动：绘制层位移弹簧，钳在 ±KICK_MAX_PX（44px 窗没有余量）。
            self._kick_vx += (
                (0.0 - self._kick_x) * KICK_STIFFNESS - self._kick_vx * KICK_DAMPING
            ) * dt
            self._kick_vy += (
                (0.0 - self._kick_y) * KICK_STIFFNESS - self._kick_vy * KICK_DAMPING
            ) * dt
            self._kick_x = max(
                -KICK_MAX_PX, min(KICK_MAX_PX, self._kick_x + self._kick_vx * dt)
            )
            self._kick_y = max(
                -KICK_MAX_PX, min(KICK_MAX_PX, self._kick_y + self._kick_vy * dt)
            )
        self._bump_anim_left = max(0.0, self._bump_anim_left - dt)
        if not self._animating():
            self._anim_timer.stop()
            self._hover_scale_v = 0.0
            self._squish_v = 0.0
            self._squish = 1.0
            self._kick_x = 0.0
            self._kick_y = 0.0
            self._kick_vx = 0.0
            self._kick_vy = 0.0
            self._bump_anim_left = 0.0
        if (
            changed
            or self._hover_scale != 1.0
            or self._squish != 1.0
            or self._kick_x != 0.0
            or self._kick_y != 0.0
        ):
            self.update()


    # ------------------------------------------------------------ 卡片

    def _card_width(self) -> int:
        # 用**胶囊态**（不折行）的自然宽度：拿展开态的 plan 宽度会随折叠缩水，
        # 越折越窄（420→340），预算跟着缩，又折得更多。
        capsule = self._compute_plan(None).width
        return max(CARD_MIN_WIDTH, min(CARD_MAX_WIDTH, max(capsule, CARD_MIN_WIDTH)))

    def _card_budget(self) -> int:
        """展开态里内容行的可用宽：卡片宽减去左右内缩。

        卡片比胶囊窄时（胶囊本来很宽、卡片被 420 上限钳住），行必须按**卡片宽**
        重排——否则内容会画到窗外被裁掉。放开时由 `_update_size()` 恢复屏宽预算。
        """
        return max(0, self._card_width() - 2 * CAPSULE_INSET - 2 * CONTENT_PAD)

    def _apply_card_size(self) -> None:
        width = self._card_width()
        self.card.setFixedWidth(width - 2 * CAPSULE_INSET)
        self.card.adjustSize()
        height = CAPSULE_HEIGHT + self.card.sizeHint().height() + 2 * CAPSULE_INSET
        self.setFixedSize(QSize(width, height))

    def _rebuild_card(self) -> None:
        palette = self._palette()
        self.card.set_colors(
            text=palette["text"],
            muted=palette["muted"],
            accent=self.accent_color().name(),
        )
        self.card.set_show_messages(bool(self.settings.show_messages))
        self.card.setWidth_and_rows(self._card_width() - 2 * CAPSULE_INSET, self._card_rows())
        self._apply_card_size()

    def _card_rows(self) -> list[tuple[str, str, str, str]]:
        """卡片 B 区：每段一行 ``(段名, 值, 详情, 告警级)``。"""
        rows: list[tuple[str, str, str, str]] = []
        if self._balance_text:
            rows.append(("余额", self._balance_text, self._balance_detail, self._balance_severity))
        for snapshot in self._registry.snapshots():
            detail = str(snapshot.detail.get("source") or snapshot.detail.get("updated_at") or "")
            severity = str(snapshot.detail.get("severity") or "")
            if snapshot.state == isl.STATE_ERROR:
                detail = str(snapshot.detail.get("error_reason") or detail or "读取失败")
            rows.append((snapshot.label or snapshot.id, snapshot.text or "--", detail, severity))
        if self.settings.show_messages:
            for message in reversed(tuple(self._recent_messages)):
                rows.append(("最近消息", message, "", ""))
        for metric_id in self.settings.metrics:
            reason = self._probe_unavailable_reason(metric_id)
            if reason:
                rows.append(
                    (
                        CARD_NAMES.get(metric_id, metric_id),
                        "--",
                        f"该指标不可用：{reason}",
                        isl.ALERT_CRIT,
                    )
                )
        for seg in self._row:
            if seg.kind == layout.KIND_PRIMARY:
                continue
            name = seg.label or CARD_NAMES.get(seg.seg_id, seg.seg_id)
            if seg.state == isl.STATE_ERROR:
                reason = str(seg.detail.get("error_reason") or "读不到读数")
                rows.append((name, "--", reason, isl.ALERT_CRIT))
                continue
            value = f"{seg.text}{seg.unit}" if seg.state == isl.STATE_OK else "--"
            if seg.state == isl.STATE_SAMPLING:
                detail = "采样中"
            else:
                detail = str(seg.detail.get("note") or "")
            rows.append((name, value, detail, seg.severity))
        if not rows:
            rows.append(("提示", "没有启用的指标", "设置 → 外观与交互 → 灵动岛", ""))
        return rows

    def expand_card(self) -> None:
        if self._expanded:
            return
        start = QRect(self.geometry())
        self._expanded = True
        self._hover_peek = False
        self._plan = self._compute_plan(self._card_budget())
        self._rebuild_card()
        self._sync_sampling()
        self.card.show()
        self.card.move(CAPSULE_INSET, CAPSULE_HEIGHT)
        self._card_timer.start()
        self._sync_sonar()  # 展开态抑制声纳：进出都要重判，别等 1Hz 心跳
        self._apply_fixed_size()
        # 布局定型（setFixedSize）会立刻改几何；想播动画必须先退回起点再插值。
        if self.isVisible():
            self._set_free_geometry(start)
        self._animate_to(self._target_rect())
        self.card_expanded.emit(True)
        self.update()

    def collapse_card(self) -> None:
        if not self._expanded:
            return
        start = QRect(self.geometry())
        self._expanded = False
        self._card_timer.stop()
        self.card.hide()
        # 收起后必须按**胶囊预算**重排：沿用卡片预算的 plan 会让胶囊停在卡片宽度。
        self._plan = self._compute_plan(None)
        self._width_key = self._width_key_now()
        self._sync_sampling()
        self._apply_fixed_size()
        self._sync_sonar()  # 回到胶囊态：working 时立刻把声纳环接回来
        if self.isVisible():
            self._set_free_geometry(start)
        self._animate_to(self._target_rect())
        self.card_expanded.emit(False)
        self.update()

    def is_expanded(self) -> bool:
        return self._expanded

    def plan(self) -> layout.ShrinkPlan | None:
        return self._plan

    # ------------------------------------------------------------ 交互

    def mousePressEvent(self, event) -> None:
        if event.button() != Qt.MouseButton.LeftButton:
            return
        # 点岛 = 用户明确要它在前（只改 z 序、不抢键盘焦点）。
        try:
            self.raise_()
        except RuntimeError:
            pass
        self._press_global = event.globalPosition().toPoint()
        self._drag_offset = self._press_global - self.pos()
        self._dragging = False
        event.accept()

    def mouseMoveEvent(self, event) -> None:
        if self._press_global is None or self._drag_offset is None:
            return
        buttons = getattr(event, "buttons", None)
        if callable(buttons):
            buttons = buttons()
        if buttons is not None and not (buttons & Qt.MouseButton.LeftButton):
            return
        point = event.globalPosition().toPoint()
        if not self._dragging:
            # `QWidget` 没有 `startDragDistance()`（旧写法在这里抛 AttributeError，
            # 于是每次拖拽都在第一帧死掉、退化成单击）。阈值走 Qt 平台值（约 10px）。
            if (point - self._press_global).manhattanLength() < QApplication.startDragDistance():
                return
            self._dragging = True
            self._stop_hover_animation()
            # 起拖立即跳到淡化终点帧：拖拽中每帧混合两张位图没有意义，还会拖慢手感。
            self._stop_content_fade()
            # 起拖先清几何插值：动画每 16ms 把窗口拽回目标位，会与拖拽 move() 互抢。
            # 但不停动画表本身（弹簧通道还要收敛）。
            self._geo_from = None
            self._geo_to = None
            if self._docked:
                # 拖出停靠边 = 回到自由态；细条一像素内容都不画，恢复后要重新采样。
                self._docked = False
                self._hover_peek = False
                self._dock_back_timer.stop()
                self._apply_fixed_size()
                self._sync_sampling()
        if self._expanded:
            # 拖拽中收起卡片直接落位（不播收起动画，否则会和跟手的 move() 打架）。
            self._expanded = False
            self._card_timer.stop()
            self.card.hide()
            self._plan = self._compute_plan(None)
            self._width_key = self._width_key_now()
            self._apply_fixed_size()
            self.card_expanded.emit(False)
        self.move(point - self._drag_offset)
        self._emit_geometry_changed()
        event.accept()

    def mouseReleaseEvent(self, event) -> None:
        if event.button() != Qt.MouseButton.LeftButton:
            return
        dragged = self._dragging
        # 选屏按**松手光标**：跨屏拖拽的触发带恰等于抓取偏移量，按窗口左上角
        # 选屏会把整窗夹回原屏，用户看到「拖过去又弹回来」（主分支 issue #154）。
        screen = self._screen_at(event.globalPosition().toPoint())
        area = screen.availableGeometry() if screen is not None else None
        self._press_global = None
        self._drag_offset = None
        if not dragged:
            self._dragging = False
            if self._strip_visible():
                # 细条上单击 = 滑出预览（不停留展开卡片，保持轻量）。
                self._hover_peek = True
                self._apply_fixed_size()
                self._animate_to(self._target_rect())
                self._dock_back_timer.start()
                self._sync_sampling()
                event.accept()
                return
            if self.settings.click_action == "toggle_pet":
                self.clicked.emit()
                event.accept()
                return
            if self._expanded:
                self.collapse_card()
            else:
                self.expand_card()
            event.accept()
            return
        # ---- 拖动落定：夹回 → 判停靠（看夹后落点）→ 重算尺寸 → 存盘 → 切模式
        if area is not None:
            x = max(area.left(), min(self.x(), area.right() - self.width() + 1))
            y = max(area.top(), min(self.y(), area.bottom() - self.height() + 1))
            self.move(x, y)
        edge = "none"
        if area is not None and self.settings.edge_dock:
            edge = layout.dock_edge_for(
                (self.x(), self.y(), self.width(), self.height()),
                (area.x(), area.y(), area.width(), area.height()),
                DOCK_THRESHOLD,
            )
        # 先翻标志再动几何：判停靠看的是用户挑的落点，不该被"刚落定的收缩"改判。
        self._dragging = False
        self._update_size()
        self._save_position()
        self._save_dock_edge(edge)
        self._hover_peek = False
        if edge != "none":
            self._apply_fixed_size()
            self._animate_to(self._target_rect())
        else:
            self._apply_fixed_size()
            self._animate_to(self._target_rect())
        # 用户亲手摆过之后，之前记录的「避让前原位」作废。
        self._avoid_home = None
        self._avoid_clear_ticks = 0
        self._sync_sampling()
        self._sync_sonar()  # 落定后进出细条态，声纳要立刻重判
        event.accept()

    def _hover_rects(self):
        """可悬停区：停靠态要同时算**滑出胶囊**与**细条**两张矩形。

        左右停靠时滑出胶囊（高 44）比细条（高 64）矮，细条上下各露 10px 死区：
        只看其中一张矩形，光标停在死区里时收回 → 细条盖回光标 → enter 又滑出，
        就是 1s 周期的自持振荡。逐矩形 ``contains``，不许取包围盒。
        """
        rect = self.geometry()
        if not self._docked or self._expanded:
            return (rect,)
        screen = self._screen_at(self.pos())
        area = screen.availableGeometry() if screen is not None else None
        if area is None:
            return (rect,)
        x, y, w, h = layout.strip_rect_for(
            (rect.x(), rect.y(), rect.width(), rect.height()),
            self.dock_edge,
            (area.x(), area.y(), area.width(), area.height()),
            thickness=STRIP_THICKNESS,
            side=STRIP_SIDE,
        )
        strip = QRect(x, y, w, h)
        return (rect,) if strip == rect else (rect, strip)

    def _cursor_in_hover_zone(self) -> bool:
        from PySide6.QtGui import QCursor

        point = QCursor.pos()
        return any(rect.contains(point) for rect in self._hover_rects())

    def _dock_back(self) -> None:
        if not (self._docked and self._hover_peek):
            return
        if self._cursor_in_hover_zone():
            # 复检：细条单击滑出的预览（光标就停在细条上）、以及光标还泡在死区里
            # 都会走到这里。此刻收回 → 细条立刻盖回光标 → enter 再滑出 = 自持振荡。
            self._dock_back_timer.start()
            return
        self._hover_peek = False
        self._apply_fixed_size()
        self._animate_to(self._target_rect())
        self._sync_sampling()
        self._sync_sonar()

    def _stop_hover_animation(self) -> None:
        """起拖 / 隐藏时停掉悬停缩放与果冻，跳到终点值（不留残影）。"""
        self._hover_scale = 1.0
        self._hover_scale_v = 0.0
        self._hover_scale_target = 1.0
        self._squish = 1.0
        self._squish_v = 0.0
        self._kick_x = 0.0
        self._kick_y = 0.0
        self._kick_vx = 0.0
        self._kick_vy = 0.0
        self._bump_anim_left = 0.0

    def enterEvent(self, event) -> None:
        if self._expanded:
            self._card_timer.stop()  # 悬停时别自动收起
        self._hover_scale_target = 1.0 if self._expanded else HOVER_SCALE
        if self._strip_visible():
            self._dock_back_timer.stop()
            self._hover_peek = True
            self._apply_fixed_size()
            self._animate_to(self._target_rect())
            self._sync_sampling()
            self._sync_sonar()  # 细条滑出成胶囊：working 时把声纳接回来
        self._ensure_anim_timer()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:
        if self._expanded:
            self._card_timer.start()
        self._hover_scale_target = 1.0
        if self._docked and self._hover_peek:
            # 这里刻意不按可悬停区拦表：光标只离开窗口、没离开死区时仍起表，
            # 到点由 `_dock_back` 复检；拦下会让「滑出」永久钉住。
            self._dock_back_timer.start()
        super().leaveEvent(event)

    def hideEvent(self, event) -> None:
        self.stop()
        self._emit_geometry_changed()  # 隐藏 = 撤墙：碰撞侧要清掉速度与接触表
        super().hideEvent(event)

    def moveEvent(self, event) -> None:
        # 位置是碰撞体的一部分：所有移动路径（拖拽、避让、落位、夹回）都必须通知
        # 碰撞侧，否则墙会停在旧位置（主分支在桥里装 Move/Resize 事件过滤器，同理）。
        super().moveEvent(event)
        self._emit_geometry_changed()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._emit_geometry_changed()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self.start()
        # 位图缓存带 DPR 与宽度：显示时一律重建，避免用上屏前的画布。
        self._content_key = None
        self._sync_sonar()
        self._emit_geometry_changed()  # 显示 = 复墙
        # 隐藏可能打断停靠/滑出动画：显示时先把几何收口到目标位，再起表。
        self._geo_from = None
        self._geo_to = None
        self._stop_hover_animation()
        self._apply_fixed_size()
        self._set_free_geometry(self._target_rect())
        self._raise_topmost()

    def _raise_topmost(self) -> None:
        self.raise_()

    def changeEvent(self, event) -> None:
        super().changeEvent(event)
        if event.type() == QEvent.Type.FontChange:
            self._fonts.clear()
            self._metrics.clear()
            self._font_key_cache = None
            self._content_key = None
            isl.clear_width_cache()
            self._update_size()

    # ------------------------------------------------------------ 绘制

    def capsule_rect(self) -> QRectF:
        return QRectF(
            CAPSULE_INSET,
            CAPSULE_INSET,
            max(1.0, self.width() - 2 * CAPSULE_INSET),
            CAPSULE_HEIGHT - 2 * CAPSULE_INSET,
        )

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        # 内容现在是位图：悬停缩放 1.02 时不做平滑就是最近邻，正文会发毛。
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        palette = self._palette()
        if self._strip_visible():
            self._paint_strip(painter, palette)
            return
        # 呼吸浮动：整窗（外壳 + 内容）一起上下 1.5px，整数像素保字锐利。
        breath = 0
        if not self._expanded and self._breathe_until > time.monotonic():
            breath = layout.breath_offset(time.monotonic() - self._breathe_started)
        if breath:
            painter.translate(0, breath)
        # 撞岛定向踢动：只搬绘制层（窗口几何永远由用户拖拽决定），已钳 ±3px。
        if self._kick_x or self._kick_y:
            painter.translate(self._kick_x, self._kick_y)
        # 悬停缩放绕**胶囊中心**（展开态不缩放：整窗缩放会把卡片切边）。
        if not self._expanded and abs(self._hover_scale - 1.0) > 0.001:
            cx = self.width() / 2.0
            cy = min(self.height(), CAPSULE_HEIGHT) / 2.0
            painter.translate(cx, cy)
            painter.scale(self._hover_scale, self._hover_scale)
            painter.translate(-cx, -cy)
        if self._expanded:
            # 展开态是一整块圆角面板：胶囊顶条与卡片底板连成一片，不再画第二层胶囊。
            rect = QRectF(0.5, 0.5, self.width() - 1.0, self.height() - 1.0)
            radius = 18.0
        else:
            rect = self._squished_capsule_rect()
            radius = rect.height() / 2.0
        path = QPainterPath()
        path.addRoundedRect(rect, radius, radius)

        self._paint_shell(painter, palette, rect, radius, path)
        self._paint_sheen(painter, rect, radius)
        if self._plan is not None:
            layer = self._content_layer()
            if layer is not None:
                self._paint_content_layer(painter, layer, path)
        self._paint_bevel(painter, rect, radius)
        if self._feedback_until > time.monotonic():
            accent = self.accent_color()
            accent.setAlpha(150)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(QPen(accent, 2.0))
            painter.drawPath(path)
        self._paint_sonar(painter, path)

    # ------------------------------------------------------------ 外壳质感
    def _paint_shell(self, painter: QPainter, palette, rect: QRectF, radius: float, path) -> None:
        """外壳三层投影 → 背景（glass 四停点渐变）→ keyline。"""
        style = str(self.settings.style or "dark")
        if not self._expanded:
            # 三层纯黑圆角矩形：只向下长、两侧内收（窗口高只有 44，没有外扩余量）。
            # 果冻 1.12 + 悬停 1.03 + 呼吸 +2 会一起把底边推过窗口，`drop` 按剩余
            # 高度收敛，否则底部描边被切平。
            painter.setPen(Qt.PenStyle.NoPen)
            headroom = max(0.0, self.height() - 1.5 - rect.bottom())
            for inset, drop, alpha in layout.CAPSULE_SHADOW_LAYERS:
                painter.setBrush(QColor(0, 0, 0, alpha))
                painter.drawRoundedRect(
                    rect.adjusted(inset, 1.0, -inset, min(drop, headroom)), radius, radius
                )
        painter.setPen(Qt.PenStyle.NoPen)
        if style == "glass":
            gradient = QLinearGradient(0, 0, 0, self.height())
            opacity = max(0.4, min(1.0, float(self.settings.opacity)))
            for stop, (red, green, blue, alpha) in layout.GLASS_STOPS:
                gradient.setColorAt(stop, QColor(red, green, blue, round(alpha * opacity)))
            painter.setBrush(QBrush(gradient))
        else:
            painter.setBrush(QColor(*palette["bg"]))
        painter.drawPath(path)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        if style == "glass":
            painter.setPen(QPen(QColor(*layout.GLASS_KEYLINE), 1.0))
            painter.drawRoundedRect(
                rect.adjusted(0.5, 0.5, -0.5, -0.5), radius - 0.5, radius - 0.5
            )
        else:
            painter.setPen(QPen(QColor(*palette["keyline"]), 1.0))
            painter.drawPath(path)

    def _paint_sheen(self, painter: QPainter, rect: QRectF, radius: float) -> None:
        """顶部 sheen：从 rect.top()+3 起覆盖 42% 高，白 α 按配色分档（light 不开）。"""
        alpha = layout.sheen_alpha(self.settings.style)
        if alpha <= 0:
            return
        sheen_rect = QRectF(
            rect.left(),
            rect.top() + layout.SHEEN_TOP_PX,
            rect.width(),
            max(0.0, rect.height() * layout.SHEEN_SPAN - layout.SHEEN_TOP_PX),
        )
        if sheen_rect.height() <= 0:
            return
        gradient = QLinearGradient(0, sheen_rect.top(), 0, sheen_rect.bottom())
        gradient.setColorAt(0.0, QColor(255, 255, 255, alpha))
        gradient.setColorAt(1.0, QColor(255, 255, 255, 0))
        painter.save()
        clip = QPainterPath()
        clip.addRoundedRect(rect, radius, radius)
        painter.setClipPath(clip)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(gradient))
        painter.drawRect(sheen_rect)
        painter.restore()

    def _paint_bevel(self, painter: QPainter, rect: QRectF, radius: float) -> None:
        """软陶笔：内缩 1.5 的圆角路径，上半周高光 / 下半周暗边，各 1px。"""
        inset = layout.BEVEL_INSET
        if rect.width() <= inset * 2 + 2 or rect.height() <= inset * 2 + 2:
            return
        highlight, shadow = layout.bevel_colors(self.settings.style)
        bevel_rect = rect.adjusted(inset, inset, -inset, -inset)
        bevel_radius = max(1.0, radius - inset)
        painter.save()
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setClipRect(
            QRectF(rect.left(), rect.top(), rect.width(), rect.height() / 2.0)
        )
        painter.setPen(QPen(QColor(*highlight), 1.0))
        painter.drawRoundedRect(bevel_rect, bevel_radius, bevel_radius)
        painter.setClipRect(
            QRectF(
                rect.left(),
                rect.top() + rect.height() / 2.0,
                rect.width(),
                rect.height() / 2.0,
            )
        )
        painter.setPen(QPen(QColor(*shadow), 1.0))
        painter.drawRoundedRect(bevel_rect, bevel_radius, bevel_radius)
        painter.restore()

    # ------------------------------------------------------------ 内容位图缓存 + 淡化
    def _content_cache_key(self) -> tuple:
        """位图缓存键：**所有会改变像素的输入**都必须在这里。

        段文案进键（时间/百分比每秒变就重建一张 44px 高的位图，比每帧重新做
        CJK shaping 便宜一个量级）；`box_text`/`elide_px` 也进键——它们是盒宽与
        右省略的输入，漏了会在 GPU/FPS 快照换样本时命中旧位图、落位不更新。
        不透明度**不进**键：内容层不画底色（`_palette()` 只把 bg 的 alpha 乘上它）。
        """
        plan = self._plan
        dot = self.dot_style() if self.settings.show_status else ("", "")
        return (
            self.width(),
            round(float(self.devicePixelRatioF() or 1.0), 2),
            self._font_key(),
            str(self.settings.style or "dark"),
            bool(self.settings.show_name),
            bool(self.settings.show_status),
            str(self.settings.accent or "blue"),
            self.accent_color().name(),
            plan.name if plan is not None else "",
            plan.level if plan is not None else 0,
            tuple(plan.folded) if plan is not None else (),
            tuple(
                (
                    seg.kind,
                    seg.seg_id,
                    seg.label,
                    seg.text,
                    seg.unit,
                    seg.state,
                    seg.severity,
                    seg.box_text,
                    seg.elide_px,
                )
                for seg in self._row
            ),
            dot,
        )

    def _structure_key(self) -> tuple:
        """结构键：只有**结构**变化才播 130ms 淡化，读数换字不闪。

        **不含窗口宽度**：宽度在几何动画里每帧都变，含进去等于每帧重起一次淡化
        （180ms 停靠动画 = 11 帧、11 次位图重建 + 掩码，而且新旧宽度不同时掩码
        尺寸不匹配会被 Qt 丢掉 → 旧图新图各画一遍 = 重影）。改用「行预算 + 段 id
        序列」，与主分支 `_structure_key` 的 `budget` 口径一致。
        """
        plan = self._plan
        return (
            self._card_budget() if self._expanded else self._available_width(),
            tuple(seg.seg_id for seg in self._row),
            plan.level if plan is not None else 0,
            plan.name if plan is not None else "",
            tuple(plan.folded) if plan is not None else (),
            bool(self.settings.show_name),
            bool(self.settings.show_status),
        )

    def _content_layer(self) -> QPixmap | None:
        key = self._content_cache_key()
        if self._content_pixmap is not None and self._content_key == key:
            return self._content_pixmap
        previous = self._content_pixmap
        rendered = self._render_content_pixmap()
        if rendered is None:
            return previous
        structure = self._structure_key()
        same_size = previous is not None and previous.size() == rendered.size()
        if (
            same_size
            and self._content_structure is not None
            and structure != self._content_structure
            and not self._dragging
        ):
            self._start_content_fade(previous)
        self._content_structure = structure
        self._content_pixmap = rendered
        self._content_key = key
        return rendered

    def _render_content_pixmap(self) -> QPixmap | None:
        width = max(1, int(self.width()))
        ratio = float(self.devicePixelRatioF() or 1.0)
        pixmap = QPixmap(
            max(1, round(width * ratio)), max(1, round(CAPSULE_HEIGHT * ratio))
        )
        if pixmap.isNull():
            return None
        pixmap.setDevicePixelRatio(ratio)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        try:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            self._paint_content(painter, self._palette())
        finally:
            painter.end()
        return pixmap

    def _paint_content_layer(self, painter: QPainter, layer: QPixmap, path) -> None:
        if self._fade_from is not None and self._fade_t < 1.0:
            masked = self._fade_mask_for(layer)
            if masked is not None:
                painter.save()
                painter.setClipPath(path)
                painter.setOpacity(layout.content_fade_alpha(self._fade_t))
                painter.drawPixmap(0, 0, masked)
                painter.restore()
        painter.drawPixmap(0, 0, layer)

    def _fade_mask_for(self, layer: QPixmap) -> QPixmap | None:
        """旧图按**新图的透明区**做遮罩：共用像素只画一次，避免淡化中途发白。

        旧图与新图尺寸不同（宽度变了）时**必须返回 None**：`setMask` 遇到尺寸不匹配
        会静默丢掉整张掩码，返回一张「没有掩码的副本」等于让调用方把旧图画两遍。
        """
        outgoing = self._fade_from
        if outgoing is None:
            return None
        if outgoing.size() != layer.size():
            return None
        key = (outgoing.cacheKey(), layer.cacheKey())
        if self._fade_masked is not None and self._fade_masked_key == key:
            return self._fade_masked
        mask = layer.toImage().createMaskFromColor(0, Qt.MaskMode.MaskOutColor)
        if mask.isNull():
            return None
        masked = QPixmap(outgoing)
        masked.setMask(QBitmap.fromImage(mask))
        self._fade_masked = masked
        self._fade_masked_key = key
        return masked

    def _start_content_fade(self, previous: QPixmap) -> None:
        self._fade_from = previous
        self._fade_masked = None
        self._fade_masked_key = None
        self._fade_t = 0.0
        self._fade_last = time.monotonic()
        if not self._fade_timer.isActive():
            self._fade_timer.start()

    def _stop_content_fade(self) -> None:
        self._fade_timer.stop()
        self._fade_from = None
        self._fade_masked = None
        self._fade_masked_key = None
        self._fade_t = 1.0

    def _on_fade_tick(self) -> None:
        now = time.monotonic()
        elapsed = max(0.0, now - self._fade_last)
        self._fade_last = now
        self._fade_t = min(
            1.0, self._fade_t + elapsed * 1000.0 / max(1.0, layout.CONTENT_FADE_MS)
        )
        if self._fade_t >= 1.0:
            self._stop_content_fade()
        self.update()

    # ------------------------------------------------------------ 状态点 / 声纳
    def _status_dot_rect(self) -> QRectF | None:
        plan = self._plan
        if plan is None:
            return None
        _, trail = self._lead_trail(plan.name)
        # 状态点落在行计划预留的 trail 槽里（end + STATUS_GAP），不能按窗口右边反推。
        dot_x = self.width() - trail + STATUS_GAP
        return QRectF(
            dot_x,
            CAPSULE_HEIGHT / 2.0 - STATUS_DOT / 2.0,
            STATUS_DOT,
            STATUS_DOT,
        )

    def _paint_status_dot(self, painter: QPainter, rect: QRectF) -> None:
        """状态点五态：实心 / 空心环 / 红底白感叹，外加一颗珠感高光。"""
        shape, _role = self.dot_style()
        color = self._dot_color()
        painter.save()
        painter.setBrush(Qt.BrushStyle.NoBrush)
        if shape == layout.DOT_HOLLOW:
            inset = layout.DOT_RING_PX / 2.0
            pen = QPen(color, layout.DOT_RING_PX)
            pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
            painter.setPen(pen)
            painter.drawEllipse(rect.adjusted(inset, inset, -inset, -inset))
        elif shape == layout.DOT_GLYPH:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(color)
            painter.drawEllipse(rect)
            glyph = QColor("#ffffff")
            glyph.setAlpha(235)
            pen = QPen(glyph, max(1.0, rect.width() * layout.DOT_GLYPH_W_RATIO))
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            painter.setPen(pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            cx = rect.center().x()
            painter.drawLine(
                QPointF(cx, rect.top() + rect.height() * layout.DOT_GLYPH_BAR_TOP_RATIO),
                QPointF(cx, rect.top() + rect.height() * layout.DOT_GLYPH_BAR_END_RATIO),
            )
            dot_y = rect.top() + rect.height() * layout.DOT_GLYPH_DOT_RATIO
            painter.drawLine(QPointF(cx, dot_y), QPointF(cx, dot_y + 0.02))
        else:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(color)
            painter.drawEllipse(rect)
        if shape != layout.DOT_HOLLOW:
            # 珠感高光只给实心/感叹点：空心环里本来是空的，高光会把环画回实心。
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(255, 255, 255, layout.DOT_HIGHLIGHT_ALPHA))
            painter.drawEllipse(
                QRectF(
                    rect.x() + layout.DOT_HIGHLIGHT_INSET_X,
                    rect.y() + layout.DOT_HIGHLIGHT_TOP,
                    rect.width() - 2 * layout.DOT_HIGHLIGHT_INSET_X,
                    rect.height() * layout.DOT_HIGHLIGHT_H_RATIO,
                )
            )
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.restore()

    def _paint_sonar(self, painter: QPainter, path) -> None:
        """工作态声纳环：以状态点圆心为心，半径 5→13、α90→0，画在内容之上。"""
        if not self._sonar_timer.isActive() or not self.settings.show_status:
            return
        dot = self._status_dot_rect()
        if dot is None:
            return
        phase = (
            (time.monotonic() - self._sonar_started) * 1000.0
            % layout.SONAR_PERIOD_MS
        ) / layout.SONAR_PERIOD_MS
        radius, alpha = layout.sonar_ring_params(phase)
        if alpha <= 0:
            return
        color = self._dot_color()
        color.setAlpha(alpha)
        painter.save()
        # 最大半径 13 会顶出胶囊右缘 1–2px（末相位 α≤11，肉眼几乎看不出），仍要裁剪。
        painter.setClipPath(path)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(color, layout.SONAR_PEN_PX))
        painter.drawEllipse(dot.center(), radius, radius)
        painter.restore()

    def _squished_capsule_rect(self) -> QRectF:
        """果冻形变只作用在胶囊外壳上：竖压横鼓，绕中心，内容不参与形变。"""
        base = self.capsule_rect()
        squish = max(SQUISH_MIN, min(SQUISH_MAX, self._squish))
        if abs(squish - 1.0) < 0.002:
            return base
        width = base.width() * (1.0 + max(0.0, 1.0 - squish) * 0.3)
        height = base.height() * squish
        center = base.center()
        return QRectF(
            center.x() - width / 2.0,
            center.y() - height / 2.0,
            width,
            height,
        )

    def _paint_strip(self, painter: QPainter, palette) -> None:
        """停靠细条：16px 扁平条 + 靠屏侧的 2px 强调线（内容不可见，故不画）。"""
        rect = QRectF(0.5, 0.5, max(1.0, self.width() - 1.0), max(1.0, self.height() - 1.0))
        radius = min(rect.width(), rect.height()) / 2.0
        path = QPainterPath()
        path.addRoundedRect(rect, radius, radius)
        background = QColor(*palette["bg"])
        background.setAlpha(int(210 * float(self.settings.opacity)))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(background)
        painter.drawPath(path)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(QColor(*palette["keyline"]), 1.0))
        painter.drawPath(path)
        accent = self.accent_color()
        accent.setAlpha(220)
        painter.setPen(QPen(accent, 2.0))
        edge = self.dock_edge
        if edge == "top":
            painter.drawLine(QPointF(4, 1.5), QPointF(self.width() - 4, 1.5))
        elif edge == "bottom":
            painter.drawLine(
                QPointF(4, self.height() - 2.0), QPointF(self.width() - 4, self.height() - 2.0)
            )
        elif edge == "left":
            painter.drawLine(
                QPointF(1.5, 4), QPointF(1.5, self.height() - 4)
            )
        elif edge == "right":
            painter.drawLine(
                QPointF(self.width() - 2.0, 4), QPointF(self.width() - 2.0, self.height() - 4)
            )

    def _paint_content(self, painter: QPainter, palette) -> None:
        plan = self._plan
        if plan is None:
            return
        baseline = CAPSULE_HEIGHT / 2.0 + BASELINE_DY
        x = CAPSULE_INSET + CONTENT_PAD
        if plan.name:
            painter.setFont(self._font("name"))
            painter.setPen(QColor(*palette["muted"]))
            painter.drawText(QPoint(int(x), int(baseline)), plan.name)
            x += plan.name_w + NAME_GAP

        placements = layout.place_info_segments(
            layout.apply_shrink(plan, self._row, primary_fm=self._fm("primary")),
            start_x=x,
            primary_fm=self._fm("primary"),
            value_fm=self._fm("value"),
            label_fm=self._fm("label"),
        )
        text_color = QColor(*palette["text"])
        muted = QColor(*palette["muted"])
        divider = QColor(*palette["divider"])
        for placement in placements:
            seg = placement.seg
            if placement.divider_x is not None:
                # 段间竖线带竖直 alpha 渐变（0→本色→本色→0）：直绘实线会在两端留硬头。
                base = QColor(*palette["divider"])
                transparent = QColor(base)
                transparent.setAlpha(0)
                gradient = QLinearGradient(
                    0, baseline - 7, 0, baseline + 7
                )
                gradient.setColorAt(0.0, transparent)
                gradient.setColorAt(0.3, base)
                gradient.setColorAt(0.7, base)
                gradient.setColorAt(1.0, transparent)
                painter.setPen(QPen(QBrush(gradient), 1.0))
                painter.drawLine(
                    int(placement.divider_x),
                    int(baseline - 7),
                    int(placement.divider_x),
                    int(baseline + 7),
                )
            if placement.label_w:
                painter.setFont(self._font("label"))
                painter.setPen(muted)
                painter.drawText(QPoint(int(placement.label_x), int(baseline)), seg.label)
            if seg.kind == layout.KIND_CHIP:
                painter.setFont(self._font("label"))
                painter.setPen(QPen(divider, 1.0))
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.drawRoundedRect(
                    QRectF(placement.box_x, CAPSULE_HEIGHT / 2.0 - 9, placement.box_w, 18),
                    9,
                    9,
                )
                if placement.alert_dot_x is not None:
                    painter.setPen(Qt.PenStyle.NoPen)
                    painter.setBrush(QColor(ALERT_COLORS.get(seg.severity, ALERT_COLORS["crit"])))
                    painter.drawEllipse(
                        QRectF(placement.alert_dot_x, CAPSULE_HEIGHT / 2.0 - 2, 4, 4)
                    )
                painter.setPen(muted)
                painter.drawText(QPoint(int(placement.text_x), int(baseline)), placement.text)
                continue
            color = text_color
            if seg.kind == layout.KIND_METRIC and seg.state == isl.STATE_OK and seg.severity:
                color = QColor(ALERT_COLORS.get(seg.severity, ALERT_COLORS["warn"]))
            elif seg.state != isl.STATE_OK:
                color = muted
            painter.setFont(
                self._font("primary") if seg.kind == layout.KIND_PRIMARY else self._font("value")
            )
            painter.setPen(color)
            painter.drawText(QPoint(int(placement.text_x), int(baseline)), placement.text)
            if placement.unit_x is not None and placement.unit_text:
                painter.setFont(self._font("label"))
                unit_color = QColor(color if seg.state == isl.STATE_OK else muted)
                # 单位与数值同色但弱化到 70%：单位是装饰，不该和读数抢亮度。
                unit_color.setAlphaF(
                    max(0.0, min(1.0, unit_color.alphaF() * layout.UNIT_ALPHA))
                )
                painter.setPen(unit_color)
                painter.drawText(
                    QPoint(int(placement.unit_x), int(baseline)), placement.unit_text
                )

        if self.settings.show_status:
            dot_rect = self._status_dot_rect()
            if dot_rect is not None:
                self._paint_status_dot(painter, dot_rect)


class _IslandCard(QWidget):
    """展开卡片：指标/余额/消息行与可访问操作按钮。"""

    balance_requested = Signal()
    settings_requested = Signal()
    messages_cleared = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("islandCard")
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(14, 6, 14, 12)
        self._layout.setSpacing(7)
        self._rows: list[tuple[QLabel, QLabel]] = []
        self._actions = QWidget(self)
        actions_layout = QHBoxLayout(self._actions)
        actions_layout.setContentsMargins(0, 0, 0, 0)
        actions_layout.setSpacing(6)
        self._refresh_button = QPushButton("刷新余额", self._actions)
        self._settings_button = QPushButton("设置", self._actions)
        self._clear_button = QPushButton("清除消息", self._actions)
        for button in (self._refresh_button, self._settings_button, self._clear_button):
            button.setAutoDefault(False)
            button.setMinimumHeight(26)
            actions_layout.addWidget(button)
        self._layout.addWidget(self._actions)
        self._hint = QLabel("点胶囊收起 · 拖动换位置", self)
        self._hint.setObjectName("islandCardHint")
        self._layout.addWidget(self._hint)
        self._refresh_button.clicked.connect(self.balance_requested.emit)
        self._settings_button.clicked.connect(self.settings_requested.emit)
        self._clear_button.clicked.connect(self.messages_cleared.emit)
        # 消息显示默认关闭：没有历史可清，「清除消息」跟着收起。
        self._clear_button.setVisible(False)
        self._text_color = "rgb(235, 238, 245)"
        self._muted_color = "rgb(160, 170, 190)"
        self._accent_color = "#40b8ff"

    def set_colors(self, *, text, muted, accent: str) -> None:
        self._text_color = "rgb({}, {}, {})".format(*text[:3])
        self._muted_color = "rgb({}, {}, {})".format(*muted[:3])
        self._accent_color = accent
        self._hint.setStyleSheet(
            f"color: {self._muted_color}; font-size: 11px; background: transparent;"
        )
        for button in (self._refresh_button, self._settings_button, self._clear_button):
            button.setStyleSheet(
                f"QPushButton {{ color: {self._text_color}; padding: 3px 8px; }}"
                f"QPushButton:hover {{ border: 1px solid {self._accent_color}; }}"
            )

    def set_show_messages(self, visible: bool) -> None:
        """消息显示关掉时，连「清除消息」按钮一起收起来（没有历史可清）。"""
        self._clear_button.setVisible(bool(visible))

    def setWidth_and_rows(self, width: int, rows: list[tuple[str, str, str, str]]) -> None:
        while len(self._rows) < len(rows):
            container = QWidget(self)
            container.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
            line = QVBoxLayout(container)
            line.setContentsMargins(0, 0, 0, 0)
            line.setSpacing(1)
            name = QLabel("", container)
            name.setObjectName("islandCardRow")
            value = QLabel("", container)
            value.setObjectName("islandCardValue")
            value.setWordWrap(False)
            line.addWidget(name)
            line.addWidget(value)
            # rows must stay above action row and hint.
            self._layout.insertWidget(max(0, self._layout.count() - 2), container)
            self._rows.append((name, value))
        for index, (name_label, value_label) in enumerate(self._rows):
            container = name_label.parentWidget()
            if index >= len(rows):
                container.hide()
                continue
            name, value, detail, severity = rows[index]
            name_label.setText(name)
            name_label.setStyleSheet(
                f"color: {self._muted_color}; font-size: 11px; background: transparent;"
            )
            value_label.setText(f"{value} · {detail}" if detail else value)
            alert = ALERT_COLORS.get(severity, "")
            color = alert or self._text_color
            value_label.setStyleSheet(
                f"color: {color}; font-size: 13px; background: transparent;"
            )
            container.show()
        self.setFixedWidth(max(120, int(width)))
        self.adjustSize()


__all__ = [
    "CARD_NAMES",
    "DynamicIsland",
    "IslandConfig",
    "CAPSULE_HEIGHT",
    "CARD_AUTO_COLLAPSE_MS",
    "ACCENTS",
    "STYLE_PALETTES",
]
