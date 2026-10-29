# -*- coding: utf-8 -*-
"""网易云音乐悬浮控制器（Windows）。

四种形态（形态之间都走几何动画）：

- ``expanded``    226×38 胶囊：标题 + 上一首 / 播放暂停 / 下一首 + 收起
- ``disc``        38×38 圆盘：靠近边缘的镂空环带里跑呼吸灯 + ♪
- ``edge_bar``    贴边长条（竖直边 12×96）：中间镂空灯条，不亮时全透明
- ``edge_panel``  贴边控制面板（贴屏一侧直边，朝屏内一侧半圆）：
  只放上一首 / 播放暂停 / 下一首三个键

交互：
- 圆盘拖到屏幕边缘附近 → 吸附成贴边长条（动画）；
- 光标靠近长条 → 自动展开成控制面板；光标移开 → 自动收回成长条；
- 点击长条 → 展开成控制面板；
- 把长条拖离边缘 → 变回圆盘。

灯效：单色呼吸、随机彩变、彩虹流动、彩虹呼吸（只在播放时点亮）。
外框：单色或两色线性渐变，可在右键菜单里自选。

设计纪律：
- 不自己轮询 SMTC；播放状态只读 ``now_playing._session_facts``（纯内存）。
- 控制点击与桌宠右键菜单同画像：临时 daemon 线程调
  ``toggle_play_pause`` / ``skip_track``。
- 生命周期由 ``pet/app.py`` 的 ``main()`` 在 QApplication 建好后接管
  （``create_floatbar``），导入期不做任何副作用。

平台：仅 Windows。进程探测走 kernel32 Toolhelp32，控制走系统 SMTC
（网易云需开启其 ``smtcEnable`` 开关）；非 Windows 平台不建窗口。
"""

from __future__ import annotations

import json
import logging
import math
import os
import random
import sys
import threading

from PySide6.QtCore import (
    QEasingCurve,
    QPoint,
    QPointF,
    QRect,
    QRectF,
    Qt,
    QTimer,
    QVariantAnimation,
)
from PySide6.QtGui import (
    QBrush,
    QColor,
    QConicalGradient,
    QCursor,
    QFont,
    QLinearGradient,
    QMouseEvent,
    QPainter,
    QPainterPath,
    QPaintEvent,
    QPen,
    QPixmap,
    QRadialGradient,
    QTransform,
)
from PySide6.QtWidgets import QApplication, QColorDialog, QLabel, QMenu, QPushButton, QWidget

_STATE_FILE = "music_floatbar.json"
_REFRESH_MS = 3000
_VISIBILITY_MS = 1200   # 网易云进程存活检查周期

# ---------------------------------------------------------------- 形态与几何
_FORM_EXPANDED = "expanded"
_FORM_DISC = "disc"
_FORM_EDGE_BAR = "edge_bar"
_FORM_EDGE_PANEL = "edge_panel"
_FORMS = (_FORM_EXPANDED, _FORM_DISC, _FORM_EDGE_BAR, _FORM_EDGE_PANEL)
_EDGES = ("left", "right", "top", "bottom")

_ANIM_DURATION_MS = 300
_BREATHE_INTERVAL_MS = 35

_EXPANDED_W, _EXPANDED_H = 226, 38
_COLLAPSED_W, _COLLAPSED_H = 38, 38
_EXPANDED_RADIUS = 14
_COLLAPSED_RADIUS = _COLLAPSED_H / 2  # 19

# 贴边长条：thick = 垂直屏幕边缘的厚度，long = 沿边缘方向的长度
_EDGE_SNAP_PX = 24
_EDGE_THICK = 12
_EDGE_LONG = 96
# 贴边控制面板两套皮肤（右键「面板主题」切换）
#   vinyl   ：贴屏侧直边 + 朝屏内一侧半圆（形状同参考图），半圆画黑胶唱片、
#             矩形画哑光材质，三个控制键沿「垂直屏幕边缘」方向横排
#   rounded ：纯圆角矩形，三个控制键沿「屏幕边缘」方向竖排
_SKIN_VINYL = "vinyl"
_SKIN_ROUNDED = "rounded"
_PANEL_SKINS = (_SKIN_VINYL, _SKIN_ROUNDED)
_SKIN_DEFAULT = _SKIN_VINYL

# vinyl：perp = 垂直屏幕边缘的厚度（伸出屏幕），along = 沿边缘的长度；
# 半圆半径 = along / 2，黑胶圆心落在矩形段尽头，唱片会压住最后 along/2，
# 所以三个控制键的可用区只有 perp - along（96 px），perp 必须留够
_VINYL_PERP = 160
_VINYL_ALONG = 64
# rounded：贴屏侧直角、朝屏内一侧圆角的长条（像从屏幕边框里伸出来的一块）
_PANEL_THICK = 48
_PANEL_LONG = 120
_ROUNDED_RADIUS = 16.0   # 面板内侧圆角；贴屏侧两个角保持直角
_BEZEL_SHADE = 14.0      # 贴屏侧阴影带宽度：模拟屏幕边框压出来的投影
_EDGE_DETACH_PX = 30     # 贴边态沿垂直屏边方向拉出多少像素算「脱离贴边」
_EDGE_LAMP = 3.0        # 长条中间镂空灯条宽度
_EDGE_END_PAD = 8.0     # 灯条两端留白（避开端部半圆）

# 悬停自动开合
_HOVER_POLL_MS = 80
_HOVER_NEAR_PX = 30     # 光标距长条多少像素内自动展开
_HOVER_STAY_PX = 12     # 面板外扩多少像素仍算「没移开」
_HOVER_LEAVE_MS = 450   # 光标移开多久后自动收贴

# 呼吸灯：黑盘靠近边缘处挖出一圈镂空环带，灯就亮在这圈镂空里；
# 不亮时 alpha=0，镂空处直接透出桌面（真透明）。
_BREATHE_INSET_PX = 4.0       # 镂空环带外边缘距圆盘外缘
_BREATHE_RING_PX = 3.0        # 镂空环带宽度（= 灯条宽度）
_BREATHE_MAX_ALPHA = 255      # 呼吸最亮 alpha

# 彩虹流动的整体呼吸相位步长（0.055 rad / tick × 35ms ≈ 4s 一次，慢于流动 → 两套节奏）
_GLOW_STEP_RAD = 0.055

# 随机彩变：每个颜色保持时长、切换过渡时长、呼吸最低 alpha（太暗会看不见换色）
_RANDOM_HOLD_MS = 900
_RANDOM_FADE_MS = 500
_RANDOM_MIN_ALPHA = 90

# 外框：单色或两色线性渐变；固定 225 的 alpha 让桌面透一点出来
_FRAME_ALPHA = 225
_FRAME_DEFAULT = {"mode": "solid", "c1": "#18181c", "c2": "#2a2a33"}
_FRAME_PRESETS = (
    ("深空黑", "#18181c", None),
    ("午夜蓝", "#16233c", None),
    ("石墨灰", "#26262b", None),
    ("酒红", "#3a1418", None),
    ("靛紫渐变", "#1b1440", "#5a1f74"),
    ("墨绿渐变", "#0f2a22", "#123a4a"),
)

# 灯效方案（扩展点：加一行即可多一种方案）
#   kind="solid"   → 单色整体呼吸
#   kind="random"  → RGB 全范围随机换色 + 平滑过渡
#   kind="rainbow" → 纯彩虹流动（亮度恒定，只有颜色绕圈）
#   kind="rainbow_breath" → 彩虹流动 + 独立慢呼吸（两套节奏叠在一起）
_EFFECTS = {
    "red": ("网易云红", "solid", QColor(222, 52, 52)),
    "blue": ("冰蓝", "solid", QColor(64, 156, 255)),
    "purple": ("紫罗兰", "solid", QColor(168, 92, 255)),
    "green": ("荧光绿", "solid", QColor(60, 220, 140)),
    "random": ("彩变随机", "random", None),
    "rainbow": ("彩虹流动", "rainbow", None),
    "rainbow_breath": ("彩虹呼吸", "rainbow_breath", None),
}
_EFFECT_ORDER = [
    "red", "blue", "purple", "green", "random", "rainbow", "rainbow_breath",
]
_EFFECT_DEFAULT = "red"


def _appdata_dir() -> str:
    """悬浮条状态目录：跟随 ``config.APP_DIR_NAME``（变体感知），与角色目录同根。"""
    try:
        from pet.config import APP_DIR_NAME
    except Exception:
        APP_DIR_NAME = "dsh-pet-standalone"
    if sys.platform == "win32":
        base = os.environ.get("APPDATA") or os.path.expanduser("~")
    elif sys.platform == "darwin":
        base = os.path.join(os.path.expanduser("~"), "Library", "Application Support")
    else:
        base = os.environ.get("XDG_CONFIG_HOME") or os.path.join(os.path.expanduser("~"), ".config")
    return os.path.join(base, APP_DIR_NAME)


def _state_path() -> str:
    return os.path.join(_appdata_dir(), _STATE_FILE)


def _load_state() -> dict:
    try:
        with open(_state_path(), "r", encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save_state(state: dict) -> None:
    try:
        os.makedirs(_appdata_dir(), exist_ok=True)
        with open(_state_path(), "w", encoding="utf-8") as fh:
            json.dump(state, fh, ensure_ascii=False)
    except Exception:
        pass


_logger = logging.getLogger(__name__)


def _log_error(where: str, exc: BaseException) -> None:
    _logger.error("%s: %r", where, exc, exc_info=True)


def _log(msg: str) -> None:
    _logger.debug(msg)


def _is_cloudmusic_running() -> bool:
    """网易云音乐客户端（cloudmusic*.exe）是否在运行——只看进程，与播放状态无关。"""
    if sys.platform != "win32":
        return False
    try:
        import ctypes
        from ctypes import wintypes

        TH32CS_SNAPPROCESS = 0x00000002
        INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value

        class PROCESSENTRY32W(ctypes.Structure):
            _fields_ = [
                ("dwSize", wintypes.DWORD),
                ("cntUsage", wintypes.DWORD),
                ("th32ProcessID", wintypes.DWORD),
                ("th32DefaultHeapID", ctypes.c_void_p),
                ("th32ModuleID", wintypes.DWORD),
                ("cntThreads", wintypes.DWORD),
                ("th32ParentProcessID", wintypes.DWORD),
                ("pcPriClassBase", wintypes.LONG),
                ("dwFlags", wintypes.DWORD),
                ("szExeFile", wintypes.WCHAR * 260),
            ]

        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
        k32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
        k32.Process32FirstW.argtypes = [wintypes.HANDLE, ctypes.POINTER(PROCESSENTRY32W)]
        k32.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(PROCESSENTRY32W)]

        snap = k32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
        if not snap or snap == INVALID_HANDLE_VALUE:
            return False
        try:
            entry = PROCESSENTRY32W()
            entry.dwSize = ctypes.sizeof(PROCESSENTRY32W)
            if not k32.Process32FirstW(snap, ctypes.byref(entry)):
                return False
            while True:
                if "cloudmusic" in entry.szExeFile.lower():
                    return True
                if not k32.Process32NextW(snap, ctypes.byref(entry)):
                    return False
        finally:
            k32.CloseHandle(snap)
    except Exception as exc:
        _log_error("cloudmusic_probe", exc)
        return False


def _now_playing():
    from pet.music import now_playing
    return now_playing


def _is_anything_playing() -> bool:
    np = _now_playing()
    facts = getattr(np, "_session_facts", None)
    if not facts:
        return False
    return any(bool(f[2]) for f in facts)


def _do_control(op: str) -> bool:
    np = _now_playing()
    if op == "toggle":
        return bool(np.toggle_play_pause())
    if op == "next":
        return bool(np.skip_track("next"))
    if op == "prev":
        return bool(np.skip_track("previous"))
    return False


def _lerp_color(a: QColor, b: QColor, t: float) -> QColor:
    """两色按通道线性插值（用于随机彩变的平滑过渡）。"""
    t = max(0.0, min(1.0, t))
    return QColor(
        int(round(a.red() + (b.red() - a.red()) * t)),
        int(round(a.green() + (b.green() - a.green()) * t)),
        int(round(a.blue() + (b.blue() - a.blue()) * t)),
    )


class _GlyphButton(QPushButton):
    """控制键：矢量图标 + 圆形悬停光晕。

    不用 ``⏮ ▶ ⏭`` 字体符号——不同机器的字形差异太大，而且符号按钮
    在深色面板上观感廉价；这里直接画三角形与竖条，尺寸随按钮自适应。
    """

    _DRAG_SLOP_PX = 5

    def __init__(self, kind: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._kind = kind
        self._playing = False
        self._hover = False
        # 拖动转发：按下点（全局坐标）+ 是否已把事件交给父窗口的拖拽逻辑
        self._press_global: QPoint | None = None
        self._drag_forwarded = False
        self.setCursor(Qt.PointingHandCursor)
        self.setFocusPolicy(Qt.NoFocus)
        self.setAttribute(Qt.WA_TranslucentBackground, True)

    def set_playing(self, playing: bool) -> None:
        """播放 / 暂停图标随播放状态切换（只有 toggle 键用得上）。"""
        if playing != self._playing:
            self._playing = playing
            self.update()

    def enterEvent(self, event) -> None:
        self._hover = True
        self.update()

    def leaveEvent(self, event) -> None:
        self._hover = False
        self.update()

    # 贴边态最自然的「抓住面板拉出来」正好落在三个控制键上；QPushButton 默认
    # 按下就抓走鼠标，面板再也收不到移动事件。这里自己接管：没动 = 点击，
    # 移动超过阈值 = 把整个拖拽序列转给父窗口（它才知道贴的是哪条边）。
    def mousePressEvent(self, event) -> None:
        if event.button() != Qt.LeftButton:
            super().mousePressEvent(event)
            return
        self._press_global = event.globalPosition().toPoint()
        self._drag_forwarded = False
        self.setDown(True)
        event.accept()

    def mouseMoveEvent(self, event) -> None:
        if self._press_global is None or not (event.buttons() & Qt.LeftButton):
            super().mouseMoveEvent(event)
            return
        panel = self.parentWidget()
        if not self._drag_forwarded:
            moved = (event.globalPosition().toPoint() - self._press_global).manhattanLength()
            if moved < self._DRAG_SLOP_PX:
                event.accept()
                return
            self.setDown(False)
            if panel is None or not hasattr(panel, "_begin_drag"):
                super().mouseMoveEvent(event)
                return
            self._drag_forwarded = True
            panel._begin_drag(self._press_global)
        if panel is not None:
            panel.mouseMoveEvent(event)
        event.accept()

    def mouseReleaseEvent(self, event) -> None:
        if event.button() != Qt.LeftButton or self._press_global is None:
            super().mouseReleaseEvent(event)
            return
        press = self._press_global
        self._press_global = None
        self.setDown(False)
        if self._drag_forwarded:
            self._drag_forwarded = False
            panel = self.parentWidget()
            if panel is not None:
                panel.mouseReleaseEvent(event)
            event.accept()
            return
        moved = (event.globalPosition().toPoint() - press).manhattanLength()
        if moved < self._DRAG_SLOP_PX and self.rect().contains(event.position().toPoint()):
            self.click()
        event.accept()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setPen(Qt.NoPen)

        r = float(min(self.width(), self.height()))
        if self._hover or self.isDown():
            alpha = 52 if self.isDown() else 28
            painter.setBrush(QColor(255, 255, 255, alpha))
            painter.drawEllipse(QRectF(self.rect()).adjusted(1.0, 1.0, -1.0, -1.0))

        painter.setBrush(QColor(246, 246, 248, 210 if self.isDown() else 244))
        cx, cy = self.width() / 2.0, self.height() / 2.0
        s = r * 0.28
        if self._kind == "toggle":
            if self._playing:
                bw = max(2.0, r * 0.095)
                bh = r * 0.44
                painter.drawRoundedRect(QRectF(cx - bw * 2.0, cy - bh / 2.0, bw, bh), 1.0, 1.0)
                painter.drawRoundedRect(QRectF(cx + bw * 1.0, cy - bh / 2.0, bw, bh), 1.0, 1.0)
            else:
                path = QPainterPath(QPointF(cx - s * 0.55, cy - s))
                path.lineTo(cx + s * 0.95, cy)
                path.lineTo(cx - s * 0.55, cy + s)
                path.closeSubpath()
                painter.drawPath(path)
        elif self._kind == "prev":
            self._paint_tri(painter, cx, cy, s, -1.0)
            painter.drawRoundedRect(
                QRectF(cx - s * 1.05, cy - s, max(1.8, r * 0.075), s * 2.0), 1.0, 1.0
            )
        else:   # next
            self._paint_tri(painter, cx, cy, s, 1.0)
            painter.drawRoundedRect(
                QRectF(cx + s * 0.95, cy - s, max(1.8, r * 0.075), s * 2.0), 1.0, 1.0
            )
        painter.end()

    @staticmethod
    def _paint_tri(painter: QPainter, cx: float, cy: float, s: float, direction: float) -> None:
        """direction=-1 朝左（上一首），+1 朝右（下一首）。"""
        tip_x = cx + s * 0.75 * direction
        base_x = cx - s * 0.45 * direction
        path = QPainterPath(QPointF(base_x, cy - s))
        path.lineTo(tip_x, cy)
        path.lineTo(base_x, cy + s)
        path.closeSubpath()
        painter.drawPath(path)


# ---------------------------------------------------------------- 生命周期

def create_floatbar(app) -> MusicFloatBar | None:
    """在 QApplication 建好后创建悬浮条（由 ``pet/app.py`` 的 ``main()`` 调用）。

    非 Windows 平台返回 None：进程探测依赖 kernel32，控制依赖系统 SMTC。
    调用方负责 ``app.aboutToQuit.connect(bar.shutdown)``。
    """
    if sys.platform != "win32":
        return None
    bar = MusicFloatBar()
    bar._refresh_visibility()   # 网易云没开就先不出现
    _log(f"create_floatbar: visible={bar.isVisible()}")
    return bar


class MusicFloatBar(QWidget):
    """网易云悬浮控制器：胶囊 / 圆盘 / 贴边长条 / 贴边控制面板。"""

    _BTN_QSS = (
        "QPushButton { color: #f2f2f2; background: transparent; border: none;"
        " border-radius: 12px; }"
        "QPushButton:hover { background: rgba(255,255,255,32); }"
        "QPushButton:pressed { background: rgba(255,255,255,60); }"
    )

    def __init__(self) -> None:
        super().__init__(
            None,
            Qt.FramelessWindowHint
            | Qt.WindowStaysOnTopHint
            | Qt.Tool
            | Qt.WindowDoesNotAcceptFocus,
        )
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setCursor(Qt.PointingHandCursor)

        self._state = _load_state()
        self._edge = self._state_edge()
        self._form = self._state_form()

        effect = str(self._state.get("effect", _EFFECT_DEFAULT))
        self._effect = effect if effect in _EFFECTS else _EFFECT_DEFAULT
        self._frame = self._state_frame()
        self._skin = self._state_skin()

        # 哑光噪点纹理（首次绘制时生成一次，之后复用）
        self._noise: QBrush | None = None
        # 黑胶唱片高光旋转相位（只在播放时推进）
        self._vinyl_phase = 0.0

        self._animating = False
        self._anim_target: str | None = None

        # 动画驱动的绘制属性
        self._radius = float(_COLLAPSED_RADIUS if self._form == _FORM_DISC else _EXPANDED_RADIUS)
        self._edge_fade = 1.0 if self._form == _FORM_EDGE_PANEL else 0.0

        self._drag_pos = None
        self._press_pos = None
        # 贴边态拖动：拖的是哪条边 + 按下点沿边方向的偏移
        self._drag_edge: str | None = None
        self._drag_along = 0
        self._grabbed = False

        # 呼吸灯 sin 相位（0.12 rad / tick × 35ms ≈ 1.8s 一个完整呼吸）
        self._breathe_phase = 0.0
        self._breathe_step_rad = 0.12

        # 彩虹的独立慢呼吸相位（与流动不同步）
        self._glow_phase = 0.0

        # 随机彩变状态：当前色 → 目标色 的过渡
        self._rand_from = QColor(
            random.randint(0, 255), random.randint(0, 255), random.randint(0, 255)
        )
        self._rand_to = QColor(
            random.randint(0, 255), random.randint(0, 255), random.randint(0, 255)
        )
        self._rand_progress = 0.0
        self._rand_hold = _RANDOM_HOLD_MS

        # 灯效只在有音乐播放时点亮（暂停 / 无会话 → 镂空处全透明）
        self._playing = False

        # 网易云进程存活状态（None = 还没查过）
        self._netcloud_running: bool | None = None

        # 悬停自动开合：连续离开计时 + 收贴后锁住，避免光标还在附近就反复开合
        self._hover_left = 0
        self._hover_locked = False

        self._build_children()
        self._create_timers()

        if self._form == _FORM_EDGE_BAR and self._edge:
            self._layout_edge_now(self._edge)
        else:
            self._apply_form(self._form, move=False)
            self._move_to_initial_pos()

    # ---------------------------------------------------------------- 状态恢复

    def _state_edge(self) -> str | None:
        edge = self._state.get("edge")
        return edge if edge in _EDGES else None

    def _state_form(self) -> str:
        form = self._state.get("form")
        if form in _FORMS:
            # 启动时不要把控制面板留在展开状态，收成贴边长条即可
            if form == _FORM_EDGE_PANEL:
                return _FORM_EDGE_BAR if self._edge else _FORM_DISC
            if form == _FORM_EDGE_BAR and not self._edge:
                return _FORM_DISC
            return form
        if self._state.get("collapsed"):
            return _FORM_EDGE_BAR if self._edge else _FORM_DISC
        return _FORM_EXPANDED

    def _state_frame(self) -> dict:
        raw = self._state.get("frame")
        if not isinstance(raw, dict):
            return dict(_FRAME_DEFAULT)
        return {
            "mode": "gradient" if raw.get("mode") == "gradient" else "solid",
            "c1": str(raw.get("c1", _FRAME_DEFAULT["c1"])),
            "c2": str(raw.get("c2", _FRAME_DEFAULT["c2"])),
        }

    def _state_skin(self) -> str:
        skin = str(self._state.get("panel_skin", _SKIN_DEFAULT))
        return skin if skin in _PANEL_SKINS else _SKIN_DEFAULT

    # ---------------------------------------------------------------- 子控件 / 定时器

    def _build_children(self) -> None:
        self._btn_prev = _GlyphButton("prev", self)
        self._btn_prev.setToolTip("上一首")
        self._btn_prev.setFixedSize(26, 26)
        self._btn_prev.clicked.connect(lambda: self._control("prev"))

        self._btn_toggle = _GlyphButton("toggle", self)
        self._btn_toggle.setToolTip("播放 / 暂停")
        self._btn_toggle.setFixedSize(26, 26)
        self._btn_toggle.clicked.connect(lambda: self._control("toggle"))

        self._btn_next = _GlyphButton("next", self)
        self._btn_next.setToolTip("下一首")
        self._btn_next.setFixedSize(26, 26)
        self._btn_next.clicked.connect(lambda: self._control("next"))

        self._title = QLabel("网易云音乐", self)
        self._title.setFont(QFont("Microsoft YaHei UI", 9))
        self._title.setStyleSheet("color: rgba(235,235,235,200); background: transparent;")
        self._title.setAlignment(Qt.AlignCenter)

        self._btn_collapse = QPushButton("—", self)
        self._btn_collapse.setFont(QFont("Segoe UI Symbol", 9))
        self._btn_collapse.setToolTip("收起为小圆点")
        self._btn_collapse.setFixedSize(20, 26)
        self._btn_collapse.setStyleSheet(self._BTN_QSS)
        self._btn_collapse.clicked.connect(self.set_collapsed)

    def _create_timers(self) -> None:
        # 状态刷新（纯内存读，无 WinRT）
        self._refresh_timer = QTimer(self)
        self._refresh_timer.setInterval(_REFRESH_MS)
        self._refresh_timer.timeout.connect(self._refresh_state)
        self._refresh_timer.start()
        QTimer.singleShot(1200, self._refresh_state)

        # 网易云进程出现 / 消失 → 悬浮条显示 / 隐藏（与播放状态无关）
        self._vis_timer = QTimer(self)
        self._vis_timer.setInterval(_VISIBILITY_MS)
        self._vis_timer.timeout.connect(self._refresh_visibility)
        self._vis_timer.start()

        # 呼吸灯（显式 start/stop；paintEvent 内纯渲染）
        self._breathe_timer = QTimer(self)
        self._breathe_timer.setInterval(_BREATHE_INTERVAL_MS)
        self._breathe_timer.timeout.connect(self._breathe_tick)
        self._breathe_timer.start()

        # 悬停自动开合：轮询光标位置（贴边态才生效）
        self._hover_timer = QTimer(self)
        self._hover_timer.setInterval(_HOVER_POLL_MS)
        self._hover_timer.timeout.connect(self._hover_tick)
        self._hover_timer.start()

    # ---------------------------------------------------------------- 状态

    def _refresh_visibility(self) -> None:
        """网易云启动 → 悬浮条出现；关闭 → 消失。只看进程，与播放状态无关。"""
        running = _is_cloudmusic_running()
        if running == self._netcloud_running:
            return
        self._netcloud_running = running
        _log(f"visibility: cloudmusic running={running}")
        if running:
            self.show()
            self.raise_()
        else:
            self.hide()

    def _refresh_state(self) -> None:
        try:
            playing = _is_anything_playing()
        except Exception as exc:
            _log_error("refresh_state", exc)
            return
        if playing != self._playing:
            self._playing = playing
            self.update()   # 播放态变化立刻点亮 / 熄灭灯效，不等下一次心跳
        self._btn_toggle.set_playing(playing)

    def _control(self, op: str) -> None:
        def work():
            try:
                _log("control begin " + op)
                ok = _do_control(op)
                _log(f"control {op} -> {ok}")
            except Exception as exc:
                _log_error("control." + op, exc)

        threading.Thread(target=work, name="music-floatbar-click", daemon=True).start()
        QTimer.singleShot(1500, self._refresh_state)

    # ---------------------------------------------------------------- 几何

    def _available_geometry(self) -> QRect:
        scr = self.screen() or QApplication.primaryScreen()
        if scr is not None:
            return scr.availableGeometry()
        return QRect(0, 0, 1280, 720)

    def _form_size(self, form: str) -> tuple[int, int]:
        """某形态的目标尺寸；贴边两形态的厚/长方向由当前贴的是哪条边决定。"""
        vertical = self._edge in ("left", "right")
        if form == _FORM_EXPANDED:
            return _EXPANDED_W, _EXPANDED_H
        if form == _FORM_DISC:
            return _COLLAPSED_W, _COLLAPSED_H
        if form == _FORM_EDGE_BAR:
            return (_EDGE_THICK, _EDGE_LONG) if vertical else (_EDGE_LONG, _EDGE_THICK)
        if self._skin == _SKIN_VINYL:
            return (_VINYL_PERP, _VINYL_ALONG) if vertical else (_VINYL_ALONG, _VINYL_PERP)
        return (_PANEL_THICK, _PANEL_LONG) if vertical else (_PANEL_LONG, _PANEL_THICK)

    def _target_rect(self, form: str) -> QRect:
        """目标几何。

        贴边形态：贴屏那一侧固定压在屏幕边缘上，沿边方向保持当前中心；
        其余形态：以当前窗口中心为锚。从贴边态展开时把贴边侧钉住，
        展开后的窗口不会跑出屏幕（这是 v5 修过的越界问题）。
        """
        avail = self._available_geometry()
        w, h = self._form_size(form)
        cur = self.frameGeometry()
        cx, cy = cur.center().x(), cur.center().y()

        if form in (_FORM_EDGE_BAR, _FORM_EDGE_PANEL) and self._edge:
            if self._edge == "left":
                x, y = avail.left(), cy - h // 2
            elif self._edge == "right":
                x, y = avail.right() - w + 1, cy - h // 2
            elif self._edge == "top":
                x, y = cx - w // 2, avail.top()
            else:
                x, y = cx - w // 2, avail.bottom() - h + 1
        else:
            if self._form in (_FORM_EDGE_BAR, _FORM_EDGE_PANEL) and self._edge:
                if self._edge == "left":
                    cx = avail.left() + w // 2
                elif self._edge == "right":
                    cx = avail.right() - w // 2 + 1
                elif self._edge == "top":
                    cy = avail.top() + h // 2
                else:
                    cy = avail.bottom() - h // 2 + 1
            x, y = cx - w // 2, cy - h // 2

        x = max(avail.left(), min(x, avail.right() - w + 1))
        y = max(avail.top(), min(y, avail.bottom() - h + 1))
        return QRect(x, y, w, h)

    def _apply_form(self, form: str, *, move: bool = True) -> None:
        """立即切换到某形态（尺寸 + 子控件 + 绘制属性），不驱动动画。"""
        if form in (_FORM_EDGE_BAR, _FORM_EDGE_PANEL) and not self._edge:
            form = _FORM_DISC
        target = self._target_rect(form)

        self._form = form
        self.setFixedSize(target.width(), target.height())
        if form == _FORM_DISC:
            self._radius = float(_COLLAPSED_RADIUS)
            self._edge_fade = 0.0
        elif form == _FORM_EXPANDED:
            self._radius = float(_EXPANDED_RADIUS)
            self._edge_fade = 0.0
        else:
            self._radius = min(target.width(), target.height()) / 2.0
            self._edge_fade = 1.0 if form == _FORM_EDGE_PANEL else 0.0
        if move:
            self.move(target.topLeft())
        self._place_children(form, target.width(), target.height())
        self.update()

    def _place_children(self, form: str, width: int, height: int) -> None:
        """子控件摆位（绝对坐标，跟着窗口一起被裁剪）。"""
        buttons = (self._btn_prev, self._btn_toggle, self._btn_next)

        if form == _FORM_EXPANDED:
            for btn in buttons + (self._title, self._btn_collapse):
                btn.setVisible(True)
            self._btn_prev.setGeometry(10, 6, 26, 26)
            self._btn_toggle.setGeometry(38, 6, 26, 26)
            self._btn_next.setGeometry(66, 6, 26, 26)
            self._title.setGeometry(96, 4, 96, 30)
            self._btn_collapse.setGeometry(198, 6, 20, 26)
            return

        if form == _FORM_EDGE_PANEL:
            for btn in buttons:
                btn.setVisible(True)
            self._title.setVisible(False)
            self._btn_collapse.setVisible(False)
            vertical = self._edge in ("left", "right")
            if self._skin == _SKIN_VINYL:
                # 半圆在朝屏内一侧：三个键全部落在贴屏侧的矩形段里，横向排开。
                # 唱片圆心压在矩形段尽头，会盖住最后 cap 一段，所以净空区
                # 只有 cross - 2 * cap（= 面板总长 - 唱片直径）。
                gap = 6
                total = 3 * 26 + 2 * gap
                cap = int(min(width, height) / 2)
                band = max(26, (width - 2 * cap) if vertical else (height - 2 * cap))
                off = (band - total) // 2
                if vertical:
                    x = off if self._edge == "left" else width - band + off
                    y = (height - 26) // 2
                    for i, btn in enumerate(buttons):
                        btn.setGeometry(x + i * (26 + gap), y, 26, 26)
                else:
                    x = (width - 26) // 2
                    y = off if self._edge == "top" else height - band + off
                    for i, btn in enumerate(buttons):
                        btn.setGeometry(x, y + i * (26 + gap), 26, 26)
                return
            # rounded：三个键沿屏幕边缘方向竖排（横向边则转成一行）
            gap = 8
            total = 3 * 26 + 2 * gap
            if vertical:
                x = (width - 26) // 2
                y0 = (height - total) // 2
                for i, btn in enumerate(buttons):
                    btn.setGeometry(x, y0 + i * (26 + gap), 26, 26)
            else:
                y = (height - 26) // 2
                x0 = (width - total) // 2
                for i, btn in enumerate(buttons):
                    btn.setGeometry(x0 + i * (26 + gap), y, 26, 26)
            return

        for btn in buttons + (self._title, self._btn_collapse):
            btn.setVisible(False)

    # ---------------------------------------------------------------- 动画

    def _animate_to(self, form: str) -> None:
        """几何动画：当前矩形 → 目标矩形，贴边形态全程贴着屏幕边缘。"""
        if self._animating or self._form == form:
            return
        if form in (_FORM_EDGE_BAR, _FORM_EDGE_PANEL) and not self._edge:
            return

        self._animating = True
        self._anim_target = form

        start = self.frameGeometry()
        target = self._target_rect(form)
        start_radius = self._radius
        if form == _FORM_DISC:
            end_radius = float(_COLLAPSED_RADIUS)
        elif form == _FORM_EXPANDED:
            end_radius = float(_EXPANDED_RADIUS)
        else:
            end_radius = min(target.width(), target.height()) / 2.0

        fade_from = self._edge_fade
        fade_to = 1.0 if form == _FORM_EDGE_PANEL else 0.0

        # 展开：子控件先按目标尺寸就位，随窗口长大逐步露出来（被窗口裁剪 = 自然揭示）；
        # 收贴：不提前藏按钮，让窗口变窄时把它们逐个裁掉——像跟着面板一起缩回屏幕边框。
        if form == _FORM_EDGE_PANEL:
            self._place_children(form, target.width(), target.height())

        self.setWindowOpacity(1.0)

        anim = QVariantAnimation(self)
        anim.setDuration(_ANIM_DURATION_MS)
        # 展开：起步快、末段收得很轻（像被磁铁吸出来）；收贴：两头缓、中间快
        curve = QEasingCurve.OutQuart if form == _FORM_EDGE_PANEL else QEasingCurve.InOutCubic
        anim.setEasingCurve(curve)
        # QVariantAnimation 只对 float/double 插值，对 tuple 会每帧给终点值。
        # 所以只驱动 0.0→1.0 进度，几何与圆角在回调里自己线性插值。
        anim.setStartValue(0.0)
        anim.setEndValue(1.0)

        def on_update(progress):
            p = float(progress)
            w = start.width() + (target.width() - start.width()) * p
            h = start.height() + (target.height() - start.height()) * p
            x = start.x() + (target.x() - start.x()) * p
            y = start.y() + (target.y() - start.y()) * p
            self.setFixedSize(max(1, int(round(w))), max(1, int(round(h))))
            self.move(int(round(x)), int(round(y)))
            self._radius = start_radius + (end_radius - start_radius) * p
            self._edge_fade = fade_from + (fade_to - fade_from) * p
            self.update()

        def on_finished():
            anim.deleteLater()
            self._animating = False
            self._anim_target = None
            self.setWindowOpacity(1.0)
            self._apply_form(form)
            if form == _FORM_DISC:
                # 缩到圆盘后如果还贴着边 → 顺势变成贴边长条
                edge = self._detect_edge()
                if edge:
                    self._edge = edge
                    self._persist()
                    self._animate_to(_FORM_EDGE_BAR)
                    return
            elif form == _FORM_EXPANDED:
                self._clamp_to_screen()
                self._refresh_state()
            self._persist()

        anim.valueChanged.connect(on_update)
        anim.finished.connect(on_finished)
        anim.start()

    def _layout_edge_now(self, edge: str) -> None:
        """无动画地把窗口摆成贴边长条（启动恢复 / 沿边拖动）。"""
        self._edge = edge
        self._apply_form(_FORM_EDGE_BAR)

    def set_collapsed(self) -> None:
        """胶囊 → 圆盘（贴边时圆盘会顺势收成贴边长条）。"""
        if self._form == _FORM_EXPANDED:
            self._animate_to(_FORM_DISC)

    def set_expanded(self) -> None:
        """圆盘 → 胶囊。"""
        if self._form == _FORM_DISC:
            self._animate_to(_FORM_EXPANDED)

    def expand_edge_panel(self) -> None:
        """贴边长条 → 控制面板。"""
        if self._form == _FORM_EDGE_BAR and self._edge:
            self._hover_left = 0
            self._animate_to(_FORM_EDGE_PANEL)

    def collapse_to_edge_bar(self) -> None:
        """控制面板 → 贴边长条。"""
        if self._form == _FORM_EDGE_PANEL:
            self._animate_to(_FORM_EDGE_BAR)

    def _clamp_to_screen(self) -> None:
        """把窗口整体钳制在当前屏幕可用区域内。"""
        avail = self._available_geometry()
        x = max(avail.left(), min(self.x(), avail.right() - self.width() + 1))
        y = max(avail.top(), min(self.y(), avail.bottom() - self.height() + 1))
        if (x, y) != (self.x(), self.y()):
            self.move(x, y)

    # ---------------------------------------------------------------- 悬停自动开合

    def _cursor_near(self, margin: int) -> bool:
        geo = self.frameGeometry().adjusted(-margin, -margin, margin, margin)
        return geo.contains(QCursor.pos())

    def _hover_tick(self) -> None:
        """光标靠近贴边长条 → 自动展开；移开面板 → 自动收贴。"""
        if self._netcloud_running is False or self._animating or self._drag_pos is not None:
            return
        if QApplication.mouseButtons() != Qt.NoButton:
            self._hover_left = 0
            return

        if self._form == _FORM_EDGE_BAR:
            if self._hover_locked:
                if not self._cursor_near(_HOVER_NEAR_PX):
                    self._hover_locked = False
                return
            if self._cursor_near(_HOVER_NEAR_PX):
                self.expand_edge_panel()
            return

        if self._form == _FORM_EDGE_PANEL:
            if self._cursor_near(_HOVER_STAY_PX):
                self._hover_left = 0
                return
            self._hover_left += _HOVER_POLL_MS
            if self._hover_left >= _HOVER_LEAVE_MS:
                self._hover_left = 0
                self._hover_locked = True   # 光标还在附近时不立刻再弹开
                self.collapse_to_edge_bar()

    # ---------------------------------------------------------------- 呼吸灯

    def _breathe_tick(self) -> None:
        """sin 相位驱动 + 显式 update——paintEvent 里只做渲染。"""
        self._breathe_phase += self._breathe_step_rad
        if self._breathe_phase > 2 * math.pi:
            self._breathe_phase -= 2 * math.pi
        self._glow_phase += _GLOW_STEP_RAD
        if self._glow_phase > 2 * math.pi:
            self._glow_phase -= 2 * math.pi
        if self._effect == "random":
            self._random_advance()
        if self._playing:
            self._vinyl_phase += 0.02
            if self._vinyl_phase > 2 * math.pi:
                self._vinyl_phase -= 2 * math.pi
        if not self._animating and self._playing:
            if self._form in (_FORM_DISC, _FORM_EDGE_BAR):
                self.update()
            elif self._form == _FORM_EDGE_PANEL and self._skin == _SKIN_VINYL:
                self.update()   # 黑胶高光在面板上缓慢转，跟呼吸灯同一条 35 ms 心跳

    def _random_advance(self) -> None:
        """随机彩变：保持一会儿 → 用固定时长平滑过渡到下一个随机 RGB 色。"""
        if self._rand_hold > 0:
            self._rand_hold -= _BREATHE_INTERVAL_MS
            return
        self._rand_progress += _BREATHE_INTERVAL_MS / float(_RANDOM_FADE_MS)
        if self._rand_progress >= 1.0:
            self._rand_progress = 0.0
            self._rand_hold = _RANDOM_HOLD_MS
            self._rand_from = QColor(self._rand_to)
            self._rand_to = QColor(
                random.randint(0, 255), random.randint(0, 255), random.randint(0, 255)
            )

    # ---------------------------------------------------------------- 位置 / 持久化

    def _detect_edge(self) -> str | None:
        """返回距屏幕边缘最近且在吸附阈值内的那条边，否则 None。"""
        geo = self.frameGeometry()
        avail = self._available_geometry()
        dists = {
            "left": geo.left() - avail.left(),
            "right": avail.right() - geo.right(),
            "top": geo.top() - avail.top(),
            "bottom": avail.bottom() - geo.bottom(),
        }
        edge = min(dists, key=dists.get)
        return edge if dists[edge] <= _EDGE_SNAP_PX else None

    def _after_drag(self) -> None:
        """拖动结束：贴边 → 长条 / 面板；拖离边缘 → 圆盘；其余形态只存位置。"""
        edge = self._detect_edge()
        if edge:
            self._edge = edge
            if self._form == _FORM_EDGE_PANEL:
                self._apply_form(_FORM_EDGE_PANEL)   # 面板拖到边上还是面板，只重新贴边
            elif self._form == _FORM_EDGE_BAR:
                self._apply_form(_FORM_EDGE_BAR)     # 沿边滑动：直接重新贴边
                self._hover_locked = True
            else:
                self._animate_to(_FORM_EDGE_BAR)
                self._hover_locked = True
        else:
            self._edge = None
            if self._form in (_FORM_EDGE_BAR, _FORM_EDGE_PANEL):
                self._animate_to(_FORM_DISC)
        self._persist()

    def _move_to_initial_pos(self) -> None:
        screen = QApplication.primaryScreen()
        geo = screen.availableGeometry() if screen is not None else QRect(0, 0, 1280, 720)
        x = self._state.get("x")
        y = self._state.get("y")
        if not isinstance(x, int) or not isinstance(y, int):
            x = geo.right() - self.width() - 24
            y = geo.top() + 80
        else:
            if self._state.get("_saved_w") and self._state.get("_saved_h"):
                old_w = int(self._state.get("_saved_w", _EXPANDED_W))
                old_h = int(self._state.get("_saved_h", _EXPANDED_H))
                cx = x + old_w // 2
                cy = y + old_h // 2
                x = cx - self.width() // 2
                y = cy - self.height() // 2
        x = max(geo.left(), min(x, geo.right() - self.width()))
        y = max(geo.top(), min(y, geo.bottom() - self.height()))
        self.move(x, y)

    def _persist(self) -> None:
        _save_state({
            "x": self.x(),
            "y": self.y(),
            "form": self._form,
            "edge": self._edge,
            "effect": self._effect,
            "frame": self._frame,
            "panel_skin": self._skin,
            "_saved_w": self.width(),
            "_saved_h": self.height(),
        })

    # ---------------------------------------------------------------- 外框颜色

    def _frame_brush(self, rect: QRect) -> QBrush:
        """外框笔刷：单色，或两色沿矩形对角线铺开的线性渐变。"""
        c1 = QColor(self._frame.get("c1", _FRAME_DEFAULT["c1"]))
        if not c1.isValid():
            c1 = QColor(_FRAME_DEFAULT["c1"])
        c1.setAlpha(_FRAME_ALPHA)
        if self._frame.get("mode") != "gradient":
            return QBrush(c1)

        c2 = QColor(self._frame.get("c2", _FRAME_DEFAULT["c2"]))
        if not c2.isValid():
            c2 = QColor(_FRAME_DEFAULT["c2"])
        c2.setAlpha(_FRAME_ALPHA)
        grad = QLinearGradient(QRectF(rect).topLeft(), QRectF(rect).bottomRight())
        grad.setColorAt(0.0, c1)
        grad.setColorAt(1.0, c2)
        return QBrush(grad)

    def _set_frame(self, c1: str, c2: str | None) -> None:
        self._frame = {"mode": "gradient" if c2 else "solid", "c1": c1, "c2": c2 or c1}
        self._persist()
        self.update()

    def _pick_frame_color(self) -> None:
        start = QColor(self._frame.get("c1", _FRAME_DEFAULT["c1"]))
        col = QColorDialog.getColor(start, None, "外框颜色（单色）")
        if col.isValid():
            self._set_frame(col.name(), None)

    def _pick_frame_gradient(self) -> None:
        start1 = QColor(self._frame.get("c1", _FRAME_DEFAULT["c1"]))
        c1 = QColorDialog.getColor(start1, None, "渐变起点颜色")
        if not c1.isValid():
            return
        start2 = QColor(self._frame.get("c2", _FRAME_DEFAULT["c2"]))
        c2 = QColorDialog.getColor(start2, None, "渐变终点颜色")
        if not c2.isValid():
            return
        self._set_frame(c1.name(), c2.name())

    def _frame_preset_checked(self, c1: str, c2: str | None) -> bool:
        mode = "gradient" if c2 else "solid"
        return (
            self._frame.get("mode") == mode
            and self._frame.get("c1") == c1
            and self._frame.get("c2") == (c2 or c1)
        )

    # ---------------------------------------------------------------- 交互（拖动 / 点击）

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() != Qt.LeftButton:
            super().mousePressEvent(event)
            return
        self._begin_drag(event.globalPosition().toPoint())
        event.accept()

    def _begin_drag(self, pos: QPoint) -> None:
        """开始一次拖拽：面板自身按下，或控制键把整段序列转发过来。"""
        self._drag_pos = pos - self.frameGeometry().topLeft()
        self._press_pos = pos
        # 贴边态也允许拖：只按垂直屏边的方向拉出去 = 脱离贴边；
        # 沿着边拖 = 在边上滑动，垂直方向仍钉在屏幕边缘。
        self._drag_edge = self._edge if self._form in (_FORM_EDGE_BAR, _FORM_EDGE_PANEL) else None
        if self._drag_edge in ("top", "bottom"):
            self._drag_along = pos.x() - self.x()
        else:
            self._drag_along = pos.y() - self.y()
        # 按在控制键上时，Qt 的隐式抓取落在子控件上；先拿到窗口级抓取，
        # 之后即使按钮被隐藏（拉出后缩成圆盘）也能继续收到移动与松手。
        if self.isVisible() and QWidget.mouseGrabber() is not self:
            self.grabMouse()
            self._grabbed = True
        _log(f"drag begin form={self._form} edge={self._drag_edge}")

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self._drag_pos is None or not (event.buttons() & Qt.LeftButton):
            super().mouseMoveEvent(event)
            return
        pos = event.globalPosition().toPoint()
        if self._drag_edge:
            if self._pull_distance(pos) >= _EDGE_DETACH_PX:
                self._detach_from_edge(pos)   # 拉出阈值 → 当场换圆盘跟手
            else:
                self._slide_along_edge(pos)
                event.accept()
                return
        self.move(pos - self._drag_pos)
        event.accept()

    def _pull_distance(self, pos: QPoint) -> int:
        """光标沿垂直屏边方向被拉出去多远（正数 = 朝屏幕内侧拉）。"""
        dx = pos.x() - self._press_pos.x()
        dy = pos.y() - self._press_pos.y()
        if self._edge == "left":
            return dx
        if self._edge == "right":
            return -dx
        if self._edge == "top":
            return dy
        return -dy

    def _slide_along_edge(self, pos: QPoint) -> None:
        """贴边态沿边滑动：垂直屏边方向钉死在屏幕边缘上。"""
        avail = self._available_geometry()
        if self._edge == "left":
            self.move(avail.left(), pos.y() - self._drag_along)
        elif self._edge == "right":
            self.move(avail.right() - self.width() + 1, pos.y() - self._drag_along)
        elif self._edge == "top":
            self.move(pos.x() - self._drag_along, avail.top())
        else:
            self.move(pos.x() - self._drag_along, avail.bottom() - self.height() + 1)
        self._clamp_to_screen()

    def _detach_from_edge(self, pos: QPoint) -> None:
        """把贴边条从屏幕边缘拉出来：立刻换成圆盘，圆盘中心跟着光标走。"""
        self._edge = None
        self._drag_edge = None
        self._hover_locked = True   # 松手如果又落回边上，不要立刻弹开
        self._apply_form(_FORM_DISC)
        self._drag_pos = QPoint(self.width() // 2, self.height() // 2)
        self.move(pos - self._drag_pos)
        self._persist()
        _log("drag detach -> disc")

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if event.button() != Qt.LeftButton or self._drag_pos is None:
            super().mouseReleaseEvent(event)
            return
        pressed = self._press_pos if self._press_pos is not None else event.globalPosition().toPoint()
        moved = (event.globalPosition().toPoint() - pressed).manhattanLength()
        self._drag_pos = None
        self._press_pos = None
        self._drag_edge = None
        if self._grabbed:
            self._grabbed = False
            self.releaseMouse()
        _log(f"drag end moved={moved} form={self._form}")
        event.accept()
        if moved < 4:
            if self._form == _FORM_EDGE_BAR:
                self.expand_edge_panel()
            elif self._form == _FORM_DISC:
                self.set_expanded()
            return
        self._after_drag()

    def contextMenuEvent(self, event) -> None:
        menu = QMenu(self)

        fx_menu = menu.addMenu("灯效")
        for key in _EFFECT_ORDER:
            act = fx_menu.addAction(_EFFECTS[key][0])
            act.setCheckable(True)
            act.setChecked(key == self._effect)
            act.triggered.connect(lambda _checked=False, k=key: self.set_effect(k))

        skin_menu = menu.addMenu("面板主题")
        for key, label in (
            (_SKIN_VINYL, "黑胶半圆（控制键横排）"),
            (_SKIN_ROUNDED, "圆角矩形（控制键竖排）"),
        ):
            act = skin_menu.addAction(label)
            act.setCheckable(True)
            act.setChecked(self._skin == key)
            act.triggered.connect(lambda _checked=False, k=key: self._set_skin(k))

        frame_menu = menu.addMenu("外框颜色")
        for label, c1, c2 in _FRAME_PRESETS:
            act = frame_menu.addAction(label)
            act.setCheckable(True)
            act.setChecked(self._frame_preset_checked(c1, c2))
            act.triggered.connect(lambda _checked=False, a=c1, b=c2: self._set_frame(a, b))
        frame_menu.addSeparator()
        frame_menu.addAction("自定义单色…").triggered.connect(self._pick_frame_color)
        frame_menu.addAction("两色渐变…").triggered.connect(self._pick_frame_gradient)

        menu.addSeparator()
        action = menu.addAction("退出悬浮控制器（下次启动恢复）")
        if menu.exec(event.globalPos()) == action:
            self.shutdown()
            self.close()

    def set_effect(self, name: str) -> None:
        """切换灯效方案（右键菜单调用），立即重绘并持久化。"""
        if name not in _EFFECTS or name == self._effect:
            return
        self._effect = name
        self._persist()
        self.update()

    def _set_skin(self, skin: str) -> None:
        """切换面板皮肤（右键菜单调用）：尺寸/按钮排布/绘制都会变，直接重建当前形态。"""
        if skin not in _PANEL_SKINS or skin == self._skin:
            return
        self._skin = skin
        self._persist()
        self._apply_form(self._form, move=True)
        self.update()

    # ---------------------------------------------------------------- 绘制

    def paintEvent(self, event: QPaintEvent) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setRenderHint(QPainter.SmoothPixmapTransform, True)

        rect = self.rect()
        painter.setPen(Qt.NoPen)

        edge_like = self._form in (_FORM_EDGE_BAR, _FORM_EDGE_PANEL) or (
            self._animating and self._anim_target in (_FORM_EDGE_BAR, _FORM_EDGE_PANEL)
        )
        if edge_like and self._edge:
            self._paint_edge(painter, rect)
        elif self._form == _FORM_DISC and not self._animating:
            # 收起态：黑盘 + 靠近边缘的一圈镂空环带（偶奇填充挖空）
            painter.setBrush(self._frame_brush(rect))
            painter.drawPath(self._disc_path(rect))
            self._paint_breathe(painter, rect)
            self._paint_collapsed_glyph(painter, rect)
        else:
            # 展开态 / 过渡态：圆角胶囊（radius 由动画驱动）
            painter.setBrush(self._frame_brush(rect))
            painter.drawRoundedRect(rect, self._radius, self._radius)

        painter.end()

    def _disc_ring_radii(self, rect: QRect) -> tuple[float, float]:
        """返回镂空环带的外半径、内半径（几何唯一来源）。"""
        half = min(rect.width(), rect.height()) / 2.0
        outer = half - _BREATHE_INSET_PX
        inner = outer - _BREATHE_RING_PX
        return outer, inner

    def _disc_path(self, rect: QRect) -> QPainterPath:
        """黑盘路径：外圆实体 + 镂空环带（不亮灯时该环带完全透明）。"""
        center = QRectF(rect).center()
        half = min(rect.width(), rect.height()) / 2.0
        path = QPainterPath()
        path.setFillRule(Qt.OddEvenFill)
        path.addEllipse(center, half, half)
        outer, inner = self._disc_ring_radii(rect)
        if inner > 0.5:
            path.addEllipse(center, outer, outer)
            path.addEllipse(center, inner, inner)
        return path

    def _paint_breathe(self, painter: QPainter, rect: QRect) -> None:
        """呼吸灯：正好填满那圈镂空环带的灯条，按当前灯效方案渲染。

        单色：alpha 从 0 起呼吸，(1 - cos)/2 让相位起点为 0 → 不亮时全透明。
        彩虹：锥形渐变绕圈流动，整体明暗轻微呼吸。
        """
        outer, inner = self._disc_ring_radii(rect)
        if inner <= 0.5:
            return
        if not self._playing:
            return   # 没有音乐在播放：不点灯，镂空环带保持全透明
        center = QRectF(rect).center()
        ring_mid = (outer + inner) / 2.0
        width = outer - inner

        _label, kind, base = _EFFECTS.get(self._effect, _EFFECTS[_EFFECT_DEFAULT])
        if kind in ("rainbow", "rainbow_breath"):
            self._paint_rainbow(
                painter, center, ring_mid, width, breathe=(kind == "rainbow_breath")
            )
            return
        if kind == "random":
            self._paint_random(painter, center, ring_mid, width)
            return

        col = self._solid_alpha_color(base)
        if col is None:
            return
        pen = QPen(col)
        pen.setWidthF(width)
        painter.setPen(pen)
        painter.setBrush(Qt.NoBrush)
        painter.drawEllipse(center, ring_mid, ring_mid)

    def _paint_random(self, painter: QPainter, center, ring_mid: float, width: float) -> None:
        """随机彩变：RGB(0~255³) 内随机换色，换色之间按通道插值平滑过渡。"""
        col = _lerp_color(self._rand_from, self._rand_to, self._rand_progress)
        t = (1.0 - math.cos(self._breathe_phase)) / 2.0
        alpha = int(round(_RANDOM_MIN_ALPHA + (_BREATHE_MAX_ALPHA - _RANDOM_MIN_ALPHA) * t))
        col.setAlpha(alpha)

        pen = QPen(col)
        pen.setWidthF(width)
        painter.setPen(pen)
        painter.setBrush(Qt.NoBrush)
        painter.drawEllipse(center, ring_mid, ring_mid)

    def _paint_rainbow(
        self, painter: QPainter, center, ring_mid: float, width: float, breathe: bool = False
    ) -> None:
        """彩虹流动：锥形渐变作为描边笔刷，起始角随相位旋转 = 颜色沿圆周流动。

        breathe=True 时叠加独立的慢相位明暗呼吸（~4s），与流动（~1.8s 一圈）不同步；
        breathe=False 时亮度恒定，只有颜色在转。
        """
        lum = self._rainbow_lum(breathe)
        angle = math.degrees(self._breathe_phase * 0.9) % 360.0

        grad = QConicalGradient(center, angle)
        steps = 12
        for i in range(steps + 1):
            frac = i / steps
            col = QColor.fromHsvF(frac % 1.0, 1.0, 1.0)   # 首尾同色 → 接缝无缝
            col.setAlphaF(lum)
            grad.setColorAt(frac, col)

        pen = QPen()
        pen.setWidthF(width)
        pen.setBrush(QBrush(grad))
        painter.setPen(pen)
        painter.setBrush(Qt.NoBrush)
        painter.drawEllipse(center, ring_mid, ring_mid)

    # ---- 贴边态：直边贴屏、内端半圆；长条中间挖镂空灯条

    def _edge_dims(self, rect: QRect) -> tuple[float, float, float]:
        """返回 (cross, span, fade)。

        ``cross`` = 垂直屏幕边缘的厚度，``span`` = 沿边缘的长度；
        ``fade`` = 0 长条 / 1 面板，动画中间态也用它插值形体。
        """
        vertical = self._edge in ("left", "right")
        cross = float(rect.width() if vertical else rect.height())
        span = float(rect.height() if vertical else rect.width())
        fade = max(0.0, min(1.0, self._edge_fade))
        return cross, span, fade

    def _edge_transform(self, rect: QRect) -> QTransform:
        """规范坐标（u = 离屏距离，v = 沿边长度）→ 屏幕坐标，四边共用一套几何。"""
        if self._edge == "left":
            return QTransform().translate(rect.x(), rect.y())
        if self._edge == "right":
            return QTransform().translate(rect.x() + rect.width(), rect.y()).scale(-1.0, 1.0)
        if self._edge == "top":
            return QTransform().translate(rect.x() + rect.width(), rect.y()).rotate(90.0)
        return QTransform().translate(rect.x(), rect.y() + rect.height()).rotate(-90.0)

    def _edge_body_path(self, rect: QRect) -> QPainterPath:
        """贴边形体：长条 / 面板两套皮肤，同一套规范坐标 + 四边映射。"""
        cross, span, fade = self._edge_dims(rect)
        if self._skin == _SKIN_VINYL:
            path = self._vinyl_body_path(cross, span, fade)
        else:
            path = self._rounded_body_path(cross, span, fade)
        return self._edge_transform(rect).map(path)

    @staticmethod
    def _vinyl_body_path(cross: float, span: float, fade: float) -> QPainterPath:
        """贴屏侧直边 + 朝屏内一侧半圆（半径 = along / 2，形状同参考图）。

        ``fade`` 从 0 长到 1 时，半圆半径从 0 长到 span/2，矩形段同步让位，
        所以长条 → 面板是同一条轮廓连续长出来的，不会突变。
        """
        radius = span / 2.0 * fade
        body = max(2.0, cross - radius)
        corner = min(5.0, body / 2.0, span / 2.0) * (1.0 - fade)
        path = QPainterPath()
        path.setFillRule(Qt.WindingFill)
        path.addRoundedRect(QRectF(0.0, 0.0, body, span), corner, corner)
        if radius > 0.5:
            # 矩形 ∪ 圆 = 贴屏侧直边 + 朝屏内一侧半圆（无接缝）
            path.addEllipse(QPointF(body, span / 2.0), radius, radius)
        return path

    @staticmethod
    def _rounded_body_path(cross: float, span: float, fade: float) -> QPainterPath:
        """圆角只给朝屏内那一侧。

        贴屏侧（u = 0）两个角补成直角，配合 ``_paint_matte`` 里的贴屏侧阴影带，
        观感就是一块从屏幕边框里伸出来的板子，而不是浮在桌面上的药丸。
        """
        radius = 3.0 + (_ROUNDED_RADIUS - 3.0) * fade
        radius = max(0.0, min(radius, cross / 2.0, span / 2.0))
        path = QPainterPath()
        path.setFillRule(Qt.WindingFill)
        path.addRoundedRect(QRectF(0.0, 0.0, cross, span), radius, radius)
        if radius > 1.0:
            # 与圆角矩形取并集 → 贴屏侧两角变回直角
            path.addRect(QRectF(0.0, 0.0, radius, radius))
            path.addRect(QRectF(0.0, span - radius, radius, radius))
        return path

    def _edge_hole_rect(self, rect: QRect) -> QRectF | None:
        """镂空灯条的位置；随 ``_edge_fade`` 收缩到 0（面板展开后就没有灯条了）。"""
        fade = max(0.0, min(1.0, self._edge_fade))
        if fade > 0.15:
            return None
        cross, span, _fade = self._edge_dims(rect)
        length = (span - _EDGE_END_PAD * 2) * (1.0 - fade)
        if length < 2:
            return None
        # 灯条压在贴屏侧那半个厚度的中线上，不跟着窗口尺寸漂
        center = cross / 2.0
        start = (span - length) / 2.0
        if self._edge == "left":
            return QRectF(
                rect.x() + center - _EDGE_LAMP / 2.0, rect.y() + start, _EDGE_LAMP, length
            )
        if self._edge == "right":
            return QRectF(
                rect.x() + rect.width() - center - _EDGE_LAMP / 2.0,
                rect.y() + start,
                _EDGE_LAMP,
                length,
            )
        if self._edge == "top":
            return QRectF(
                rect.x() + start, rect.y() + center - _EDGE_LAMP / 2.0, length, _EDGE_LAMP
            )
        return QRectF(
            rect.x() + start,
            rect.y() + rect.height() - center - _EDGE_LAMP / 2.0,
            length,
            _EDGE_LAMP,
        )

    def _paint_edge(self, painter: QPainter, rect: QRect) -> None:
        """贴边态：黑体挖一条镂空，灯条铺在镂空里（沿长边方向）。"""
        body = self._edge_body_path(rect)
        hole = self._edge_hole_rect(rect)
        hole_path = None
        if hole is not None:
            hole_path = QPainterPath()
            hole_path.addRoundedRect(hole, _EDGE_LAMP / 2.0, _EDGE_LAMP / 2.0)
            body = body.subtracted(hole_path)

        painter.setPen(Qt.NoPen)
        painter.setBrush(self._frame_brush(rect))
        painter.drawPath(body)   # 镂空处不落笔 = 真透明

        _cross, _span, fade = self._edge_dims(rect)
        if fade > 0.05:
            self._paint_matte(painter, rect, body, fade)

        if hole is None or hole_path is None:
            return
        if self._edge in ("left", "right"):
            cx = hole.center().x()
            brush = self._lamp_brush(cx, hole.top(), cx, hole.bottom())
        else:
            cy = hole.center().y()
            brush = self._lamp_brush(hole.left(), cy, hole.right(), cy)
        if brush is None:
            return   # 不播放 / 不亮：镂空保持透明

        painter.setBrush(brush)
        painter.drawPath(hole_path)

    # ---- 面板质感：哑光底 + 黑胶唱片

    def _noise_brush(self) -> QBrush:
        """一次性生成的噪点笔刷，给面板底色加哑光颗粒（复用，不每帧重建）。"""
        if self._noise is None:
            size = 64
            pix = QPixmap(size, size)
            pix.fill(Qt.transparent)
            painter = QPainter(pix)
            painter.setPen(Qt.NoPen)
            for _ in range(260):
                painter.setBrush(QColor(255, 255, 255, random.randint(6, 16)))
                painter.drawPoint(random.randrange(size), random.randrange(size))
            painter.end()
            self._noise = QBrush(pix)
        return self._noise

    def _paint_matte(self, painter: QPainter, rect: QRect, body: QPainterPath, fade: float) -> None:
        """面板底子：细颗粒 + 顶部微高光 + 底部压暗，全部裁在体内。"""
        cross, span, _fade = self._edge_dims(rect)
        painter.save()
        painter.setClipPath(body, Qt.IntersectClip)
        painter.setPen(Qt.NoPen)

        painter.setOpacity(0.75 * fade)
        painter.setBrush(self._noise_brush())
        painter.drawRect(rect)

        painter.setOpacity(fade)
        sheen = QLinearGradient(
            QPointF(rect.left(), rect.top()), QPointF(rect.left(), rect.bottom())
        )
        sheen.setColorAt(0.0, QColor(255, 255, 255, 22))
        sheen.setColorAt(0.35, QColor(255, 255, 255, 0))
        sheen.setColorAt(1.0, QColor(0, 0, 0, 60))
        painter.setBrush(QBrush(sheen))
        painter.drawRect(rect)

        # 贴屏侧阴影带：屏幕边框压在面板上的投影。配上贴屏侧直角，
        # 观感就是「板子从屏幕外伸出来」；按规范坐标画，四条边通用。
        painter.setWorldTransform(self._edge_transform(rect), False)
        bezel = QLinearGradient(0.0, 0.0, _BEZEL_SHADE, 0.0)
        bezel.setColorAt(0.0, QColor(0, 0, 0, 118))
        bezel.setColorAt(0.45, QColor(0, 0, 0, 42))
        bezel.setColorAt(1.0, QColor(0, 0, 0, 0))
        painter.setBrush(QBrush(bezel))
        painter.drawRect(QRectF(0.0, 0.0, _BEZEL_SHADE, span))
        painter.restore()

        if self._skin == _SKIN_VINYL:
            self._paint_vinyl_disc(painter, rect, cross, span, fade)
            return
        # 沿真实轮廓走一圈淡描边，把面板从桌面背景里拉出来
        painter.save()
        painter.setOpacity(fade)
        painter.setBrush(Qt.NoBrush)
        painter.setPen(QPen(QColor(255, 255, 255, 26), 1.0))
        painter.drawPath(body)
        painter.restore()

    def _paint_vinyl_disc(
        self, painter: QPainter, rect: QRect, cross: float, span: float, fade: float
    ) -> None:
        """半圆那一段画黑胶：径向底光 + 同心纹槽 + 旋转高光 + 标签与中心孔。"""
        radius = span / 2.0 * fade
        if radius < 5.0:
            return
        cu, cv = cross - radius, span / 2.0
        disc_r = radius - 1.0
        painter.save()
        painter.setWorldTransform(self._edge_transform(rect), True)
        painter.setOpacity(fade)
        painter.setPen(Qt.NoPen)

        grad = QRadialGradient(QPointF(cu - disc_r * 0.35, cv - disc_r * 0.35), disc_r * 1.5)
        grad.setColorAt(0.0, QColor(46, 46, 55))
        grad.setColorAt(0.5, QColor(18, 18, 23))
        grad.setColorAt(1.0, QColor(8, 8, 11))
        painter.setBrush(QBrush(grad))
        painter.drawEllipse(QPointF(cu, cv), disc_r, disc_r)

        # 外缘一道冷白细边：哑光底吃不住高光，靠这圈把唱片和面板分开
        painter.setPen(QPen(QColor(255, 255, 255, 34), 1.0))
        painter.setBrush(Qt.NoBrush)
        painter.drawEllipse(QPointF(cu, cv), disc_r, disc_r)

        painter.setPen(QPen(QColor(255, 255, 255, 15), 0.8))
        for i in range(7):
            ring = disc_r * (0.44 + 0.075 * i)
            painter.drawEllipse(QPointF(cu, cv), ring, ring)
        painter.setPen(Qt.NoPen)

        wedge = QConicalGradient(QPointF(cu, cv), -math.degrees(self._vinyl_phase))
        wedge.setColorAt(0.0, QColor(255, 255, 255, 34))
        wedge.setColorAt(0.16, QColor(255, 255, 255, 0))
        wedge.setColorAt(0.5, QColor(255, 255, 255, 12))
        wedge.setColorAt(0.64, QColor(255, 255, 255, 0))
        wedge.setColorAt(1.0, QColor(255, 255, 255, 34))
        painter.setBrush(QBrush(wedge))
        painter.drawEllipse(QPointF(cu, cv), disc_r * 0.97, disc_r * 0.97)

        label_r = disc_r * 0.34
        painter.setBrush(self._vinyl_label_color())
        painter.drawEllipse(QPointF(cu, cv), label_r, label_r)

        hole_r = max(1.6, disc_r * 0.075)
        painter.setBrush(QColor(10, 10, 12, 240))
        painter.drawEllipse(QPointF(cu, cv), hole_r, hole_r)
        painter.restore()

    def _vinyl_label_color(self) -> QColor:
        """唱片标签跟着灯效取色；不播放时退成中性灰。"""
        if not self._playing:
            return QColor(98, 100, 112, 235)
        _label, kind, base = _EFFECTS.get(self._effect, _EFFECTS[_EFFECT_DEFAULT])
        if kind == "solid" and base is not None:
            col = QColor(base)
        elif kind == "random":
            col = _lerp_color(self._rand_from, self._rand_to, self._rand_progress)
        else:
            col = QColor.fromHsvF((self._breathe_phase / (2 * math.pi)) % 1.0, 0.72, 0.92)
        col.setAlpha(240)
        return col

    def _solid_alpha_color(self, base: QColor) -> QColor | None:
        t = (1.0 - math.cos(self._breathe_phase)) / 2.0
        alpha = int(round(_BREATHE_MAX_ALPHA * t))
        if alpha <= 0:
            return None
        col = QColor(base)
        col.setAlpha(alpha)
        return col

    def _rainbow_lum(self, breathe: bool) -> float:
        if not breathe:
            return 1.0
        glow = (1.0 - math.cos(self._glow_phase)) / 2.0
        return 0.08 + 0.92 * glow

    def _lamp_brush(self, x1: float, y1: float, x2: float, y2: float) -> QBrush | None:
        """按当前方案生成灯条笔刷（彩虹用线性渐变沿长边铺色并随相位流动）。"""
        if not self._playing:
            return None
        _label, kind, base = _EFFECTS.get(self._effect, _EFFECTS[_EFFECT_DEFAULT])
        if kind == "solid":
            col = self._solid_alpha_color(base)
            return QBrush(col) if col is not None else None
        if kind == "random":
            col = _lerp_color(self._rand_from, self._rand_to, self._rand_progress)
            t = (1.0 - math.cos(self._breathe_phase)) / 2.0
            col.setAlpha(
                int(round(_RANDOM_MIN_ALPHA + (_BREATHE_MAX_ALPHA - _RANDOM_MIN_ALPHA) * t))
            )
            return QBrush(col)

        lum = self._rainbow_lum(kind == "rainbow_breath")
        shift = (self._breathe_phase / (2 * math.pi)) % 1.0
        grad = QLinearGradient(x1, y1, x2, y2)
        steps = 12
        for i in range(steps + 1):
            frac = i / steps
            col = QColor.fromHsvF((frac + shift) % 1.0, 1.0, 1.0)
            col.setAlphaF(lum)
            grad.setColorAt(frac, col)
        return QBrush(grad)

    def _paint_collapsed_glyph(self, painter: QPainter, rect: QRect) -> None:
        """收起态 ♪ 字符，居中画。"""
        font = QFont("Segoe UI Symbol", 14)
        painter.setFont(font)
        col = QColor(235, 235, 235, 235)
        painter.setPen(QPen(col))
        painter.setBrush(Qt.NoBrush)
        painter.drawText(rect, Qt.AlignCenter, "♪")

    # ---------------------------------------------------------------- 收尾

    def shutdown(self) -> None:
        for t in (self._refresh_timer, self._breathe_timer, self._vis_timer, self._hover_timer):
            try:
                t.stop()
            except Exception:
                pass
