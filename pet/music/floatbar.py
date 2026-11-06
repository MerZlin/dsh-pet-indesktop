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
    Signal,
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
from PySide6.QtWidgets import (
    QApplication,
    QColorDialog,
    QInputDialog,
    QLabel,
    QMenu,
    QPushButton,
    QWidget,
)

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

# 胶囊条音乐可视化（真实频谱带）
#   位置：above = 胶囊条上方（窗口顶部多留一条带）/ inside = 画在胶囊条内部
#   配色：单色 / 渐变 / 呼吸 / 彩虹 / 随机；半透明度 0~100
#   数据：Windows WASAPI 回环采集 + FFT（pet/music/spectrum.py），采集失败回落程序化波形
_VIZ_H = 26                # 「胶囊上方」时频谱带高度（窗口随之变高）
_VIZ_INSIDE_H = 30         # 「胶囊内部」时频谱条最大高度（铺满胶囊，键与标题压在频谱上）
_VIZ_BARS = 48             # 采集侧的分频段数（也是「极细」档的上限）
_VIZ_GAP = 2.0             # 相邻频谱条的间距
_VIZ_ALPHA_DEFAULT = 45    # 默认半透明度（%）
_VIZ_COLOR_DEFAULT = "#de3434"
_VIZ_COLOR2_DEFAULT = "#ff9a3c"
_VIZ_STEP = 0.34           # 波形相位步长（35 ms / tick，只在没有真实频谱时作回退）
_VIZ_POS_ABOVE = "above"
_VIZ_POS_INSIDE = "inside"
_VIZ_POSITIONS = (_VIZ_POS_ABOVE, _VIZ_POS_INSIDE)
_VIZ_MODE_SOLID = "solid"
_VIZ_MODE_GRADIENT = "gradient"
_VIZ_MODE_BREATHE = "breathe"
_VIZ_MODE_RAINBOW = "rainbow"
_VIZ_MODE_RANDOM = "random"
_VIZ_MODES = (
    _VIZ_MODE_SOLID,
    _VIZ_MODE_GRADIENT,
    _VIZ_MODE_BREATHE,
    _VIZ_MODE_RAINBOW,
    _VIZ_MODE_RANDOM,
)
_VIZ_MODE_LABELS = (
    (_VIZ_MODE_SOLID, "单色"),
    (_VIZ_MODE_GRADIENT, "渐变"),
    (_VIZ_MODE_BREATHE, "呼吸"),
    (_VIZ_MODE_RAINBOW, "彩虹"),
    (_VIZ_MODE_RANDOM, "随机"),
)
_VIZ_MODE_DEFAULT = _VIZ_MODE_SOLID

# 频谱条粗细：5 档，取值是「画面上的条数」（越少越粗）；
# 极细 40 条：214 px 的频谱带里每条净宽约 3.4 px；最粗 10 条，每条约 19.4 px
_VIZ_WIDTHS = (
    ("极细", 40),
    ("细", 30),
    ("标准", 22),
    ("粗", 16),
    ("极粗", 10),
)
_VIZ_WIDTH_DEFAULT = 2
# 彩虹循环：色相独立累加（35 ms × 0.01 ≈ 3.5 s 绕一圈），回绕处相位是连续的
_VIZ_HUE_STEP = 0.010

# 标题滚动：每拍（35 ms）移动的像素、到起点停一会儿、接回开头时留的间距
_TITLE_SCROLL_PX = 1.0
_TITLE_SCROLL_PAUSE = 34   # ≈ 1.2 s
_TITLE_SCROLL_GAP = 56     # 滚出末尾后与本句开头的空隙

# 黑胶圆环频谱：三种可选风格（圆点 / 声波 / 射线），1~5 档密度或层数
_RING_STYLES = (
    ("dots", "圆点"),
    ("wave", "声波"),
    ("bars", "射线"),
)
_RING_STYLE_DEFAULT = "dots"
_RING_COUNT_DEFAULT = 3
_RING_COLOR_DEFAULT = "#de3434"
_RING_SEGMENTS = 180       # 共享轮廓的角度采样数（越多越圆滑）
_RING_SMOOTH_W = 4         # 圆形滑动平均的半径（压掉逐段毛刺）
_RING_FOLLOW = 0.38        # 每帧向目标形状靠近的比例（时间上也不会突跳）
_RING_GLOW_ALPHA = 46      # 声波环柔光 alpha
# 声波环：各层是同一条波轮廓的嵌套副本（不是同心圆），层与层之间留开几个像素
_RING_WAVE_TOP = 0.74      # 最外层流线的基准半径（占盘半径）
_RING_WAVE_GAP = 0.08      # 相邻两层的径向间距（≈2.5 px，稀疏可辨；5 层恰好落到 floor）
_RING_WAVE_AMP = 0.10      # 波峰相对基准线的最大振幅
_RING_WAVE_FLOOR = 0.42    # 波谷不能压过这里（标签在 0.34）
_RING_BAR_LEN = 0.22       # 射线最长伸进盘内的长度（占盘半径）
# 频谱幅度三档：小档是原始设计，每往上一档幅度约 ×3（超界由各风格的半径钳位兜住）
_RING_GAINS = (
    ("small", "小", 1.0),
    ("mid", "中", 3.0),
    ("large", "大", 9.0),
)
_RING_GAIN_DEFAULT = "small"

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


def _spectrum_class():
    """惰性导入：真实频谱依赖 numpy，只在真的需要画频谱时才加载。"""
    from pet.music.spectrum import SystemSpectrum
    return SystemSpectrum


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
        # 竖线统一用暂停图标那两根线的宽度与列位置：
        #   prev 的竖线占 [cx-2bw, cx-bw]，next 的竖线占 [cx+bw, cx+2bw]
        bw = max(2.0, r * 0.095)
        if self._kind == "toggle":
            if self._playing:
                bh = r * 0.44
                painter.drawRoundedRect(QRectF(cx - bw * 2.0, cy - bh / 2.0, bw, bh), 1.0, 1.0)
                painter.drawRoundedRect(QRectF(cx + bw * 1.0, cy - bh / 2.0, bw, bh), 1.0, 1.0)
            else:
                self._paint_tri(painter, cx - s * 0.60, cx + s * 0.60, cy, 1.0, s)
        elif self._kind == "prev":
            painter.drawRoundedRect(QRectF(cx - bw * 2.0, cy - s, bw, s * 2.0), 1.0, 1.0)
            self._paint_tri(painter, cx - bw, cx + bw * 2.0, cy, -1.0, s)
        else:   # next
            self._paint_tri(painter, cx - bw * 2.0, cx + bw, cy, 1.0, s)
            painter.drawRoundedRect(QRectF(cx + bw, cy - s, bw, s * 2.0), 1.0, 1.0)
        painter.end()

    @staticmethod
    def _paint_tri(
        painter: QPainter, left: float, right: float, cy: float, direction: float, half_h: float
    ) -> None:
        """在 [left, right] 这段宽度里画三角形：direction=-1 朝左，+1 朝右。"""
        tip_x = left if direction < 0 else right
        base_x = right if direction < 0 else left
        path = QPainterPath(QPointF(base_x, cy - half_h))
        path.lineTo(tip_x, cy)
        path.lineTo(base_x, cy + half_h)
        path.closeSubpath()
        painter.drawPath(path)


class _ScrollingLabel(QLabel):
    """胶囊条标题：放得下居中显示，放不下就「滚动 → 接回开头」循环播放。"""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._offset = 0.0
        self._pause = _TITLE_SCROLL_PAUSE
        self.setTextFormat(Qt.PlainText)
        # 标题盖在胶囊中间，让它把鼠标事件让给父窗口（否则按在标题上拖不动）
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)

    def setText(self, text: str) -> None:   # noqa: N802 —— 覆盖 Qt 的驼峰命名
        super().setText(text)
        self._offset = 0.0
        self._pause = _TITLE_SCROLL_PAUSE
        self.update()

    def advance(self) -> bool:
        """推进一帧滚动；返回是否需要重绘。

        循环而不是往返：偏移量走到「文本宽 + 间距」后减去这个周期回到起点。
        绘制层同时画两份文本，所以回绕那一帧的画面与上一帧完全重合，看不出接缝。
        """
        span = self.fontMetrics().horizontalAdvance(self.text())
        if span <= self.width():
            if self._offset:
                self._offset = 0.0
                return True
            return False
        if self._pause > 0:
            self._pause -= 1
            return False
        self._offset += _TITLE_SCROLL_PX
        cycle = span + _TITLE_SCROLL_GAP
        if self._offset >= cycle:
            self._offset -= cycle
            self._pause = _TITLE_SCROLL_PAUSE
        return True

    def paintEvent(self, event: QPaintEvent) -> None:   # noqa: N802 —— 覆盖 Qt 的驼峰命名
        painter = QPainter(self)
        painter.setFont(self.font())
        metrics = self.fontMetrics()
        span = metrics.horizontalAdvance(self.text())
        painter.setPen(self.palette().color(self.foregroundRole()))
        y = (self.height() + metrics.ascent() - metrics.descent()) // 2
        painter.setClipRect(self.rect())
        if span <= self.width():
            painter.drawText(QPoint((self.width() - span) // 2, y), self.text())
        else:
            # 两份文本首尾相接：第一份滚出左边时，第二份正好从右边接上
            x = -int(self._offset)
            painter.drawText(QPoint(x, y), self.text())
            painter.drawText(QPoint(x + span + _TITLE_SCROLL_GAP, y), self.text())
        painter.end()


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

    # 后台曲名采样线程 → 主线程（Qt 自动走排队连接）
    _track_changed = Signal(str)

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
        # 当前动画进度（0→1），频谱带的高度跟着它一起长出来 / 收回去
        self._anim_progress = 1.0

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

        # 胶囊条标题显示当前曲目名（后台常驻线程采样 SMTC 元数据）
        self._track_title = ""
        self._track_stop = threading.Event()
        self._track_thread: threading.Thread | None = None
        self._track_changed.connect(self._set_track_title)

        # 音乐可视化（胶囊条上方的频谱带 / 胶囊内部频谱）
        self._viz_pos = self._state_viz_pos()
        self._viz_mode = self._state_viz_mode()
        self._viz_alpha = self._state_viz_alpha()
        self._viz_color = self._state_viz_color()
        self._viz_color2 = self._state_viz_color2()
        self._viz_width = self._state_viz_width()
        # 附加功能开关：关掉后对应线程 / 绘制都完全不启动
        self._viz_on = self._state_flag("viz_enabled")
        self._title_on = self._state_flag("title_enabled")
        # 黑胶盘上的闭合圆环频谱
        self._ring_on = self._state_flag("ring_enabled")
        self._ring_count = self._state_ring_count()
        self._ring_color = self._state_ring_color()
        self._ring_style = self._state_ring_style()
        self._ring_gain = self._state_ring_gain()
        self._ring_prev: list[float] = []
        self._viz_hue = 0.0
        self._viz_phase = 0.0
        # 真实频谱：播放且胶囊条可见时才起采集线程（见 _sync_spectrum）
        self._spectrum = None
        self._spectrum_failed = False
        # 彩虹角度单独累加：相位回绕时角度连续，圆盘接缝不再跳一下
        self._rainbow_angle = 0.0

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

    def _state_viz_pos(self) -> str:
        pos = str(self._state.get("viz_pos", _VIZ_POS_ABOVE))
        return pos if pos in _VIZ_POSITIONS else _VIZ_POS_ABOVE

    def _state_viz_mode(self) -> str:
        mode = str(self._state.get("viz_mode", _VIZ_MODE_DEFAULT))
        return mode if mode in _VIZ_MODES else _VIZ_MODE_DEFAULT

    def _state_viz_alpha(self) -> int:
        try:
            value = int(self._state.get("viz_alpha", _VIZ_ALPHA_DEFAULT))
        except (TypeError, ValueError):
            return _VIZ_ALPHA_DEFAULT
        return max(0, min(100, value))

    def _state_viz_color(self) -> QColor:
        color = QColor(str(self._state.get("viz_color", _VIZ_COLOR_DEFAULT)))
        return color if color.isValid() else QColor(_VIZ_COLOR_DEFAULT)

    def _state_viz_color2(self) -> QColor:
        color = QColor(str(self._state.get("viz_color2", _VIZ_COLOR2_DEFAULT)))
        return color if color.isValid() else QColor(_VIZ_COLOR2_DEFAULT)

    def _state_viz_width(self) -> int:
        try:
            value = int(self._state.get("viz_width", _VIZ_WIDTH_DEFAULT))
        except (TypeError, ValueError):
            return _VIZ_WIDTH_DEFAULT
        return value if 0 <= value < len(_VIZ_WIDTHS) else _VIZ_WIDTH_DEFAULT

    def _state_flag(self, key: str) -> bool:
        """附加功能开关：存档里只认真正的布尔值，缺失 / 非法一律按「开」。"""
        value = self._state.get(key, True)
        return value if isinstance(value, bool) else True

    def _state_ring_count(self) -> int:
        try:
            value = int(self._state.get("ring_count", _RING_COUNT_DEFAULT))
        except (TypeError, ValueError):
            return _RING_COUNT_DEFAULT
        return value if 1 <= value <= 5 else _RING_COUNT_DEFAULT

    def _state_ring_color(self) -> QColor:
        color = QColor(str(self._state.get("ring_color", _RING_COLOR_DEFAULT)))
        return color if color.isValid() else QColor(_RING_COLOR_DEFAULT)

    def _state_ring_style(self) -> str:
        value = str(self._state.get("ring_style", _RING_STYLE_DEFAULT))
        return value if value in {key for key, _label in _RING_STYLES} else _RING_STYLE_DEFAULT

    def _state_ring_gain(self) -> str:
        value = str(self._state.get("ring_gain", _RING_GAIN_DEFAULT))
        return value if value in {key for key, _label, _f in _RING_GAINS} else _RING_GAIN_DEFAULT

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

        # 键帽底画在父窗口上：按下 / 抬起要让父窗口重绘，才会有「按下去」的观感
        for btn in (self._btn_prev, self._btn_toggle, self._btn_next):
            btn.pressed.connect(self.update)
            btn.released.connect(self.update)

        self._title = _ScrollingLabel(self)
        self._title.setFont(QFont("Microsoft YaHei UI", 8))
        self._title.setStyleSheet("color: rgba(235,235,235,210); background: transparent;")
        self._title.setAlignment(Qt.AlignCenter)
        self._set_track_title("")

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
            self._start_track_sampler()
            self.show()
            self.raise_()
        else:
            self._set_track_title("")
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

    def _set_track_title(self, title: str) -> None:
        """胶囊条标题：优先显示当前曲目名（放不下就滚动）；关闭「显示当前曲名」后固定显示「网易云音乐」。"""
        text = (title or "").strip() if self._title_on else ""
        self._track_title = text
        shown = text or "网易云音乐"
        self._title.setText(shown)
        self._title.setToolTip(shown)

    def _start_track_sampler(self) -> None:
        if self._track_thread is not None or sys.platform != "win32" or not self._title_on:
            return
        self._track_stop.clear()
        thread = threading.Thread(target=self._track_loop, name="music-floatbar-track", daemon=True)
        self._track_thread = thread
        thread.start()

    def _stop_track_sampler(self) -> None:
        self._track_stop.set()
        self._track_thread = None

    def _track_loop(self) -> None:
        """常驻线程按周期读一次 SMTC 元数据，取当前曲目名。

        ``get_now_playing`` 自带事件循环，必须常驻在同一线程里反复调用；
        COM 公寓按线程累积，不能每次采样都新起线程。
        """
        while True:
            if self._netcloud_running:
                title = ""
                try:
                    from pet.music.now_playing import get_now_playing

                    playback = get_now_playing()
                    track = getattr(playback, "track", None) if playback else None
                    app_id = str(getattr(playback, "app_id", "") or "").lower()
                    # 只认网易云的会话：别的播放器抢到焦点时不显示它的曲名
                    if track is not None and (not app_id or "cloudmusic" in app_id):
                        title = str(getattr(track, "title", "") or "")
                except Exception as exc:
                    _log_error("track_sample", exc)
                try:
                    self._track_changed.emit(title)
                except RuntimeError:
                    return
            if self._track_stop.wait(_REFRESH_MS / 1000.0):
                return

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

    def _viz_band_h(self) -> int:
        """「胶囊上方」时窗口顶部额外留给频谱带的高度；「胶囊内部」或关闭可视化时为 0。"""
        if not self._viz_on:
            return 0
        return _VIZ_H if self._viz_pos == _VIZ_POS_ABOVE else 0

    def _viz_band_progress(self) -> float:
        """频谱带当前的生长进度（0→1），让带子跟着窗口动画一起长 / 收。"""
        if not self._animating:
            return 1.0 if self._form == _FORM_EXPANDED else 0.0
        if self._anim_target == _FORM_EXPANDED:
            return self._anim_progress
        if self._form == _FORM_EXPANDED:
            return 1.0 - self._anim_progress
        return 0.0

    def _form_size(self, form: str) -> tuple[int, int]:
        """某形态的目标尺寸；贴边两形态的厚/长方向由当前贴的是哪条边决定。"""
        vertical = self._edge in ("left", "right")
        if form == _FORM_EXPANDED:
            return _EXPANDED_W, _EXPANDED_H + self._viz_band_h()
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
            if form == _FORM_EXPANDED:
                # 频谱带向上扩：胶囊本体仍以当前中心为锚，带子只在上面长出来
                x, y = cx - w // 2, cy - _EXPANDED_H // 2 - self._viz_band_h()
            else:
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
            top = self._viz_band_h()
            for btn in buttons + (self._title, self._btn_collapse):
                btn.setVisible(True)
            self._btn_prev.setGeometry(10, 6 + top, 26, 26)
            self._btn_toggle.setGeometry(38, 6 + top, 26, 26)
            self._btn_next.setGeometry(66, 6 + top, 26, 26)
            self._title.setGeometry(96, 4 + top, 96, 30)
            self._btn_collapse.setGeometry(198, 6 + top, 20, 26)
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
        self._anim_progress = 0.0

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
            self._anim_progress = p
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
            self._anim_progress = 1.0
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
        # 彩虹旋转角单独累加并模 360：相位回绕时角度连续，接缝不会再跳一格
        self._rainbow_angle = (self._rainbow_angle + math.degrees(self._breathe_step_rad * 0.9)) % 360.0
        self._sync_spectrum()
        # 曲名放不下时左右往返滚动；放得下就原地居中
        if self._form == _FORM_EXPANDED and not self._title.isHidden() and self._title.advance():
            self._title.update()
        if self._effect == "random":
            self._random_advance()
        if self._playing:
            self._vinyl_phase += 0.02
            if self._vinyl_phase > 2 * math.pi:
                self._vinyl_phase -= 2 * math.pi
            self._viz_phase += _VIZ_STEP
            if self._viz_phase > 2 * math.pi:
                self._viz_phase -= 2 * math.pi
            # 彩虹色相独立累加并模 1：相位回绕时颜色连续，不会跳回起点
            self._viz_hue = (self._viz_hue + _VIZ_HUE_STEP) % 1.0
        if not self._animating and self._playing:
            if self._form in (_FORM_DISC, _FORM_EDGE_BAR):
                self.update()
            elif self._form == _FORM_EDGE_PANEL and self._skin == _SKIN_VINYL:
                self.update()   # 黑胶高光在面板上缓慢转，跟呼吸灯同一条 35 ms 心跳
            elif self._form == _FORM_EXPANDED:
                self.update()   # 胶囊条上的频谱带逐帧推进

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
            "viz_pos": self._viz_pos,
            "viz_mode": self._viz_mode,
            "viz_alpha": self._viz_alpha,
            "viz_color": self._viz_color.name(),
            "viz_color2": self._viz_color2.name(),
            "viz_width": self._viz_width,
            "viz_enabled": self._viz_on,
            "title_enabled": self._title_on,
            "ring_enabled": self._ring_on,
            "ring_count": self._ring_count,
            "ring_color": self._ring_color.name(),
            "ring_style": self._ring_style,
            "ring_gain": self._ring_gain,
            "_saved_w": self.width(),
            "_saved_h": self.height(),
        })

    # ---------------------------------------------------------------- 音乐可视化设置

    def set_viz_pos(self, pos: str) -> None:
        """频谱位置：胶囊条上方（窗口变高）/ 胶囊条内部。"""
        if pos not in _VIZ_POSITIONS or pos == self._viz_pos:
            return
        self._viz_pos = pos
        self._persist()
        # 位置决定「上方」是否要额外高度，几何立刻按当前形态重排；
        # 动画中途不重排（收尾的 _apply_form 会读新值），避免打断几何动画
        if not self._animating:
            self._apply_form(self._form)
        self.update()

    def set_viz_mode(self, mode: str) -> None:
        if mode not in _VIZ_MODES or mode == self._viz_mode:
            return
        self._viz_mode = mode
        self._persist()
        self.update()

    def set_viz_alpha(self, value: int) -> None:
        value = max(0, min(100, int(value)))
        if value == self._viz_alpha:
            return
        self._viz_alpha = value
        self._persist()
        self.update()

    def set_viz_width(self, level: int) -> None:
        """频谱条粗细（0 最细 … 4 最粗）；条数由档位与可用宽度共同决定。"""
        level = int(level)
        if level < 0 or level >= len(_VIZ_WIDTHS) or level == self._viz_width:
            return
        self._viz_width = level
        self._persist()
        self.update()

    def set_viz_enabled(self, enabled: bool) -> None:
        """「音乐可视化」总开关：关掉后不画、不起采集线程，窗口也收回 38 高。"""
        enabled = bool(enabled)
        if enabled == self._viz_on:
            return
        self._viz_on = enabled
        self._persist()
        if not self._animating:
            self._apply_form(self._form)
        self.update()

    def set_title_enabled(self, enabled: bool) -> None:
        """「显示当前曲名」总开关：关掉后标题固定显示「网易云音乐」，采样线程退出。"""
        enabled = bool(enabled)
        if enabled == self._title_on:
            return
        self._title_on = enabled
        self._persist()
        if enabled:
            self._start_track_sampler()
        else:
            self._stop_track_sampler()
        self._set_track_title(self._track_title)

    def _pick_viz_color(self) -> None:
        col = QColorDialog.getColor(self._viz_color, None, "可视化颜色（单色 / 渐变起始色）")
        if col.isValid():
            self._viz_color = col
            self._persist()
            self.update()

    def _pick_viz_color2(self) -> None:
        col = QColorDialog.getColor(self._viz_color2, None, "渐变结束色")
        if col.isValid():
            self._viz_color2 = col
            self._persist()
            self.update()

    def set_ring_enabled(self, enabled: bool) -> None:
        """黑胶盘圆环频谱开关；关掉后不再为面板形态起采集线程。"""
        enabled = bool(enabled)
        if enabled == self._ring_on:
            return
        self._ring_on = enabled
        self._persist()
        self.update()

    def set_ring_count(self, count: int) -> None:
        count = int(count)
        if count < 1 or count > 5 or count == self._ring_count:
            return
        self._ring_count = count
        self._persist()
        self.update()

    def set_ring_style(self, style: str) -> None:
        """圆环频谱风格：dots（圆点）/ wave（声波）/ bars（射线）。"""
        if style not in {key for key, _label in _RING_STYLES} or style == self._ring_style:
            return
        self._ring_style = style
        self._ring_prev = []   # 风格切换后重新积累时间平滑
        self._persist()
        self.update()

    def set_ring_gain(self, gain: str) -> None:
        """频谱幅度档位：small（小）/ mid（中）/ large（大），逐档约 ×3。"""
        if gain not in {key for key, _label, _f in _RING_GAINS} or gain == self._ring_gain:
            return
        self._ring_gain = gain
        self._persist()
        self.update()

    def _ring_gain_factor(self) -> float:
        """当前幅度档位的倍数（小 = 1 / 中 = 3 / 大 = 9）。"""
        for key, _label, factor in _RING_GAINS:
            if key == self._ring_gain:
                return factor
        return 1.0

    def _pick_ring_color(self) -> None:
        col = QColorDialog.getColor(self._ring_color, None, "频谱颜色")
        if col.isValid():
            self._ring_color = col
            self._persist()
            self.update()

    def _pick_viz_alpha(self) -> None:
        value, ok = QInputDialog.getInt(
            None, "音乐可视化", "半透明度（%）", self._viz_alpha, 0, 100, 5
        )
        if ok:
            self.set_viz_alpha(value)

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

    def _build_context_menu(self) -> QMenu:
        """构建右键菜单（不含退出项）。拆出来让测试能直接检查菜单树，
        不必去 patch PySide6 的 QMenu.exec（Shiboken 层拦截不到）。"""
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

        viz_menu = menu.addMenu("音乐可视化")
        pos_menu = viz_menu.addMenu("频谱位置")
        for key, label in ((_VIZ_POS_ABOVE, "胶囊条上方"), (_VIZ_POS_INSIDE, "胶囊条内部")):
            act = pos_menu.addAction(label)
            act.setCheckable(True)
            act.setChecked(self._viz_pos == key)
            act.triggered.connect(lambda _checked=False, k=key: self.set_viz_pos(k))
        width_menu = viz_menu.addMenu("线条粗细")
        for index, (label, count) in enumerate(_VIZ_WIDTHS):
            act = width_menu.addAction(f"{label}（{count} 条）")
            act.setCheckable(True)
            act.setChecked(self._viz_width == index)
            act.triggered.connect(lambda _checked=False, i=index: self.set_viz_width(i))
        mode_menu = viz_menu.addMenu("配色")
        for key, label in _VIZ_MODE_LABELS:
            act = mode_menu.addAction(label)
            act.setCheckable(True)
            act.setChecked(self._viz_mode == key)
            act.triggered.connect(lambda _checked=False, k=key: self.set_viz_mode(k))
        viz_menu.addAction("可视化颜色…").triggered.connect(self._pick_viz_color)
        viz_menu.addAction("渐变第二颜色…").triggered.connect(self._pick_viz_color2)
        alpha_menu = viz_menu.addMenu("半透明度")
        for pct in (20, 40, 60, 80):
            act = alpha_menu.addAction(f"{pct}%")
            act.setCheckable(True)
            act.setChecked(self._viz_alpha == pct)
            act.triggered.connect(lambda _checked=False, v=pct: self.set_viz_alpha(v))
        alpha_menu.addAction("自定义…").triggered.connect(self._pick_viz_alpha)

        viz_menu.addSeparator()
        ring_menu = viz_menu.addMenu("黑胶圆环频谱")
        act = ring_menu.addAction("启用")
        act.setCheckable(True)
        act.setChecked(self._ring_on)
        act.triggered.connect(lambda checked: self.set_ring_enabled(checked))
        style_menu = ring_menu.addMenu("圆环样式")
        for key, label in _RING_STYLES:
            act = style_menu.addAction(label)
            act.setCheckable(True)
            act.setChecked(self._ring_style == key)
            act.triggered.connect(lambda _checked=False, k=key: self.set_ring_style(k))
        gain_menu = ring_menu.addMenu("幅度")
        for key, label, _factor in _RING_GAINS:
            act = gain_menu.addAction(label)
            act.setCheckable(True)
            act.setChecked(self._ring_gain == key)
            act.triggered.connect(lambda _checked=False, k=key: self.set_ring_gain(k))
        # 三风格下这档的语义不同：声波=流线层数，圆点=同心圆环层数，射线=条数与粗细
        count_label = {"wave": "声波层数", "dots": "圆点层数"}.get(self._ring_style, "密度")
        count_menu = ring_menu.addMenu(count_label)
        for count in (1, 2, 3, 4, 5):
            act = count_menu.addAction(f"{count} 档")
            act.setCheckable(True)
            act.setChecked(self._ring_count == count)
            act.triggered.connect(lambda _checked=False, c=count: self.set_ring_count(c))
        ring_menu.addAction("频谱颜色…").triggered.connect(self._pick_ring_color)

        extra_menu = menu.addMenu("附加功能")
        act = extra_menu.addAction("显示当前曲名")
        act.setCheckable(True)
        act.setChecked(self._title_on)
        act.triggered.connect(lambda checked: self.set_title_enabled(checked))
        act = extra_menu.addAction("音乐可视化")
        act.setCheckable(True)
        act.setChecked(self._viz_on)
        act.triggered.connect(lambda checked: self.set_viz_enabled(checked))
        return menu

    def contextMenuEvent(self, event) -> None:
        menu = self._build_context_menu()
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

        expanded_like = self._form == _FORM_EXPANDED or (
            self._animating and self._anim_target == _FORM_EXPANDED
        )
        band = int(round(self._viz_band_h() * self._viz_band_progress())) if expanded_like else 0
        body = rect.adjusted(0, band, 0, 0) if 0 < band < rect.height() else rect

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
            # 展开态 / 过渡态：圆角胶囊（radius 由动画驱动；上方频谱带不参与填充）
            painter.setBrush(self._frame_brush(body))
            painter.drawRoundedRect(body, self._radius, self._radius)

        if self._form == _FORM_EXPANDED and not self._animating:
            self._paint_viz(painter, body)

        painter.end()

    # ---- 胶囊条音乐可视化（真实频谱）

    def _viz_levels(self) -> list[float]:
        """条高（0~1）：优先用 WASAPI 回环采到的真实频谱；采集不可用时回落程序化波形。"""
        spectrum = self._spectrum
        if spectrum is not None and not spectrum.failed:
            return spectrum.levels
        phase = self._viz_phase
        levels = []
        for i in range(_VIZ_BARS):
            x = i / float(_VIZ_BARS - 1)
            wave = (
                0.5 * math.sin(phase * 2.0 + x * 9.0)
                + 0.3 * math.sin(phase * 3.3 - x * 5.0 + 1.7)
                + 0.2 * math.sin(phase * 1.4 + x * 2.5 + 0.5)
            )
            level = (0.55 + 0.45 * wave) * (0.55 + 0.45 * (1.0 - x) ** 0.7)
            levels.append(max(0.10, min(1.0, level)))
        return levels

    def _sync_spectrum(self) -> None:
        """按「正在播放 + 正在显示频谱」启停系统音频采集线程。"""
        expanded = self._form == _FORM_EXPANDED or (
            self._animating and self._anim_target == _FORM_EXPANDED
        )
        ring_panel = self._ring_on and self._form == _FORM_EDGE_PANEL and self._skin == _SKIN_VINYL
        want = self._playing and (
            (self._viz_on and expanded) or ring_panel
        )
        if want:
            if self._spectrum is None and not self._spectrum_failed:
                try:
                    factory = _spectrum_class()
                except Exception as exc:
                    _log_error("spectrum_import", exc)
                    self._spectrum_failed = True
                else:
                    self._spectrum = factory(_VIZ_BARS)
                    self._spectrum.start()
                    _log("spectrum: capture started")
        elif self._spectrum is not None:
            self._spectrum.stop()
            self._spectrum = None
            _log("spectrum: capture stopped")

    def _viz_bar_count(self, width: float) -> int:
        """按「粗细」档位算条数；任何档位都先保证每条不少于 3 px，画不下的部分自动减少条数。"""
        wanted = _VIZ_WIDTHS[self._viz_width][1]
        fit = max(1, int(width // 3.0))
        return max(1, min(wanted, fit))

    def _viz_resample(self, levels: list[float], count: int) -> list[float]:
        """把 22 段原始电平按「取每组最大值」压成 count 条，粗档也不会丢峰。"""
        if count >= len(levels):
            return list(levels[:count])
        out = []
        for i in range(count):
            lo = int(i * len(levels) / count)
            hi = max(lo + 1, int((i + 1) * len(levels) / count))
            out.append(max(levels[lo:hi]))
        return out

    def _viz_bar_color(self, index: int, total: int = _VIZ_BARS) -> QColor:
        """单条频谱的颜色：单色 / 渐变 / 呼吸 / 彩虹 / 随机，统一叠加半透明度。"""
        mode = self._viz_mode
        if mode == _VIZ_MODE_RAINBOW:
            # 色相 = 条序号 + 独立累加的循环偏移；偏移模 1，绕圈处连续不会跳色
            hue = (index / float(max(1, total)) + self._viz_hue) % 1.0
            color = QColor.fromHsvF(hue, 0.85, 1.0)
        elif mode == _VIZ_MODE_RANDOM:
            color = _lerp_color(self._rand_from, self._rand_to, self._rand_progress)
        elif mode == _VIZ_MODE_GRADIENT:
            t = index / float(max(1, total - 1))
            color = _lerp_color(QColor(self._viz_color), QColor(self._viz_color2), t)
        else:
            color = QColor(self._viz_color)
        alpha = int(round(255 * self._viz_alpha / 100.0))
        if mode == _VIZ_MODE_BREATHE:
            breath = (1.0 - math.cos(self._breathe_phase)) / 2.0
            alpha = int(round(alpha * (0.25 + 0.75 * breath)))
        color.setAlpha(max(0, min(255, alpha)))
        return color

    def _paint_viz(self, painter: QPainter, capsule: QRect) -> None:
        """频谱带：胶囊条上方一整条（窗口顶部预留区），或画在胶囊内部。"""
        if not self._playing or not self._viz_on:
            return
        inside = self._viz_pos == _VIZ_POS_INSIDE
        if inside:
            area = QRectF(capsule).adjusted(8.0, 0.0, -8.0, -4.0)
            max_h = min(float(_VIZ_INSIDE_H), max(2.0, area.height() - 2.0))
            baseline = area.bottom()
        else:
            area = QRectF(
                capsule.left() + 6.0,
                capsule.top() - float(_VIZ_H),
                capsule.width() - 12.0,
                float(_VIZ_H - 3),
            )
            max_h = area.height()
            baseline = area.bottom()

        count = self._viz_bar_count(area.width())
        levels = self._viz_resample(self._viz_levels(), count)
        slot = area.width() / len(levels)
        bar_w = max(1.5, slot - _VIZ_GAP)
        painter.save()
        painter.setPen(Qt.NoPen)
        # 圆角封顶 2 px：矮条不能变成圆点，否则整条频谱看起来像一串珠子
        radius = min(bar_w / 2.0, 2.0)
        for i, level in enumerate(levels):
            height = max(1.5, max_h * level)
            x = area.left() + i * slot + (slot - bar_w) / 2.0
            painter.setBrush(self._viz_bar_color(i, len(levels)))
            painter.drawRoundedRect(QRectF(x, baseline - height, bar_w, height), radius, radius)
        painter.restore()

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
        # 角度用独立累加器（模 360）：早先用 breathe_phase*0.9 会在相位回绕处
        # 一次跳 36°，视觉上就是圆盘环带上出现一道不自然的分界
        angle = self._rainbow_angle

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

        self._paint_key_caps(painter, fade)

    def _paint_key_caps(self, painter: QPainter, fade: float) -> None:
        """圆角矩形面板的按键区：先挖一条内凹键槽，再把三个圆角键帽放进去。"""
        caps = [b for b in (self._btn_prev, self._btn_toggle, self._btn_next) if not b.isHidden()]
        if not caps:
            return
        rects = [QRectF(btn.geometry()) for btn in caps]
        painter.save()
        painter.setOpacity(fade)

        # 键槽：比键帽外扩 5 px 的深色圆角槽，顶部一道更黑的投射线，像陷进面板里
        well = rects[0].united(rects[-1]).adjusted(-5.0, -5.0, 5.0, 5.0)
        painter.setPen(QPen(QColor(0, 0, 0, 120), 1.0))
        painter.setBrush(QColor(0, 0, 0, 72))
        painter.drawRoundedRect(well, 10.0, 10.0)
        painter.setPen(QPen(QColor(0, 0, 0, 150), 1.0))
        painter.drawLine(
            QPointF(well.left() + 4.0, well.top() + 1.0),
            QPointF(well.right() - 4.0, well.top() + 1.0),
        )
        painter.setPen(QPen(QColor(255, 255, 255, 18), 1.0))
        painter.drawLine(
            QPointF(well.left() + 4.0, well.bottom() - 1.0),
            QPointF(well.right() - 4.0, well.bottom() - 1.0),
        )

        for btn, cap in zip(caps, rects):
            pressed = btn.isDown()
            # 键帽：上亮下暗 + 圆角 7，未按下时顶边一道高光、底边一道暗线
            graded = QLinearGradient(cap.topLeft(), cap.bottomLeft())
            graded.setColorAt(0.0, QColor(255, 255, 255, 20 if pressed else 62))
            graded.setColorAt(0.55, QColor(255, 255, 255, 12 if pressed else 26))
            graded.setColorAt(1.0, QColor(255, 255, 255, 4 if pressed else 10))
            painter.setBrush(QBrush(graded))
            painter.setPen(QPen(QColor(0, 0, 0, 140 if pressed else 96), 1.0))
            painter.drawRoundedRect(cap.adjusted(0.5, 0.5, -0.5, -0.5), 7.0, 7.0)
            if pressed:
                continue
            painter.setPen(QPen(QColor(255, 255, 255, 70), 1.0))
            painter.drawLine(
                QPointF(cap.left() + 5.0, cap.top() + 1.5),
                QPointF(cap.right() - 5.0, cap.top() + 1.5),
            )
            painter.setPen(QPen(QColor(0, 0, 0, 90), 1.0))
            painter.drawLine(
                QPointF(cap.left() + 5.0, cap.bottom() - 1.5),
                QPointF(cap.right() - 5.0, cap.bottom() - 1.5),
            )
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

        self._paint_ring_spectrum(painter, cu, cv, disc_r)

        hole_r = max(1.6, disc_r * 0.075)
        painter.setBrush(QColor(10, 10, 12, 240))
        painter.drawEllipse(QPointF(cu, cv), hole_r, hole_r)
        painter.restore()

    def _ring_shape(self, levels: list[float], smooth_w: int = _RING_SMOOTH_W) -> list[float]:
        """把 48 段电平变成一条平滑的环形轮廓。

        先按循环 Catmull-Rom 采样到 180 点（段与段之间是曲线而不是折线），
        再做圆形滑动平均压掉逐段毛刺；返回 0~1 的归一化轮廓。
        """
        n = len(levels)
        samples = _RING_SEGMENTS
        raw = []
        for k in range(samples):
            t = k / float(samples) * n
            i = int(t)
            f = t - i
            p0 = levels[(i - 1) % n]
            p1 = levels[i % n]
            p2 = levels[(i + 1) % n]
            p3 = levels[(i + 2) % n]
            raw.append(
                0.5 * (
                    2.0 * p1
                    + (-p0 + p2) * f
                    + (2.0 * p0 - 5.0 * p1 + 4.0 * p2 - p3) * f * f
                    + (-p0 + 3.0 * p1 - 3.0 * p2 + p3) * f * f * f
                )
            )
        out = []
        for k in range(samples):
            total = 0.0
            for j in range(-smooth_w, smooth_w + 1):
                total += raw[(k + j) % samples]
            out.append(total / (2 * smooth_w + 1))
        return out

    def _ring_smooth_over_time(self, shape: list[float]) -> list[float]:
        """逐帧向目标形状靠近一点，避免形状突跳。"""
        if len(self._ring_prev) == len(shape):
            shape = [
                prev + (new - prev) * _RING_FOLLOW for prev, new in zip(self._ring_prev, shape)
            ]
        self._ring_prev = shape
        return shape

    def _paint_ring_spectrum(
        self, painter: QPainter, cu: float, cv: float, disc_r: float
    ) -> None:
        """黑胶盘上的环形频谱，按「圆环样式」分派：圆点 / 声波 / 射线。"""
        if not self._ring_on or not self._playing:
            return
        levels = self._viz_levels()
        if not levels:
            return
        count = max(1, min(5, self._ring_count))
        color = QColor(self._ring_color)
        painter.save()
        painter.setRenderHint(QPainter.Antialiasing, True)
        try:
            if self._ring_style == "dots":
                smooth = self._ring_smooth_over_time(self._ring_shape(levels, 3))
                self._paint_ring_dots(painter, cu, cv, disc_r, smooth, color, count)
            elif self._ring_style == "bars":
                smooth = self._ring_smooth_over_time(self._ring_shape(levels, 2))
                self._paint_ring_bars(painter, cu, cv, disc_r, smooth, color, count)
            else:   # wave
                smooth = self._ring_smooth_over_time(self._ring_shape(levels, 8))
                self._paint_ring_wave(painter, cu, cv, disc_r, smooth, color, count)
        finally:
            painter.restore()

    @staticmethod
    def _sample_shape(shape: list[float], angle: float) -> float:
        """取某个角度上的轮廓电平（角度单位：弧度）。"""
        k = int(round(angle / (2.0 * math.pi) * len(shape))) % len(shape)
        return shape[k]

    def _paint_ring_dots(
        self, painter: QPainter, cu: float, cv: float, disc_r: float,
        shape: list[float], color: QColor, density: int,
    ) -> None:
        """圆点环：density 圈同心圆点环，每颗的大小/亮度随所在角度的电平呼吸。"""
        gain = self._ring_gain_factor()
        # 大档增益下满电平点半径会大到互相重叠并糊出盘面，钳在 0.085r
        # （0.86 + 0.085 < 1.0，最外也不出盘）；低中电平仍严格按档位倍数
        max_dot = disc_r * 0.085
        painter.setPen(Qt.NoPen)
        for ring in range(density):
            # 最外一圈 0.86r，每往内一圈收 0.10r；最内 0.46r，仍避开 0.34r 的标签
            orbit = disc_r * (0.86 - 0.10 * ring)
            # 点数随周长缩放，任意一圈上的点距一致
            dots = max(12, int(round(44 * orbit / (disc_r * 0.86))))
            fade = 1.0 - 0.08 * ring
            for i in range(dots):
                theta = 2.0 * math.pi * i / dots - math.pi / 2.0
                level = self._sample_shape(shape, theta + math.pi / 2.0)
                radius = min(max_dot, disc_r * (0.014 + 0.020 * level * gain))
                col = QColor(color)
                col.setAlpha(int((110 + 145 * level) * fade))
                painter.setBrush(col)
                painter.drawEllipse(
                    QPointF(cu + orbit * math.cos(theta), cv + orbit * math.sin(theta)),
                    radius, radius,
                )

    def _paint_ring_bars(
        self, painter: QPainter, cu: float, cv: float, disc_r: float,
        shape: list[float], color: QColor, density: int,
    ) -> None:
        """射线环：一圈径向圆角频谱条；安静时退成小圆点。

        密度档同时决定条数与线条粗细：越高档条越密、线越粗。
        """
        bars = 28 + 7 * (density - 1)
        # 线宽随密度加粗，但最密一档（56 条）的条距仍大于线宽，不会糊成一整圈
        width = max(1.2, disc_r * (0.028 + 0.013 * (density - 1)))
        # 射线落在唱片纹槽区（标签 0.34 之外），从外圈向内生长，最响也不压标签
        r_out_base = disc_r * 0.84
        r_in_floor = disc_r * 0.50
        gain = self._ring_gain_factor()
        painter.setBrush(Qt.NoBrush)
        for i in range(bars):
            theta = 2.0 * math.pi * i / bars - math.pi / 2.0
            level = self._sample_shape(shape, theta + math.pi / 2.0)
            r_in = max(r_in_floor, r_out_base - disc_r * (0.03 + _RING_BAR_LEN * gain * level))
            col = QColor(color)
            col.setAlpha(int(105 + 150 * level))
            pen = QPen(col, width)
            pen.setCapStyle(Qt.RoundCap)
            painter.setPen(pen)
            painter.drawLine(
                QPointF(cu + r_in * math.cos(theta), cv + r_in * math.sin(theta)),
                QPointF(cu + r_out_base * math.cos(theta), cv + r_out_base * math.sin(theta)),
            )

    def _paint_ring_wave(
        self, painter: QPainter, cu: float, cv: float, disc_r: float,
        shape: list[float], color: QColor, layers_n: int,
    ) -> None:
        """声波环：多条半透明平滑闭合流线，像贴在盘边的一圈水波。"""
        cos_t = [math.cos(2.0 * math.pi * k / _RING_SEGMENTS) for k in range(_RING_SEGMENTS)]
        sin_t = [math.sin(2.0 * math.pi * k / _RING_SEGMENTS) for k in range(_RING_SEGMENTS)]
        painter.setBrush(Qt.NoBrush)
        amp = disc_r * _RING_WAVE_AMP * self._ring_gain_factor()
        # 所有层共享同一条波轮廓，只是基准半径依次向内收一点（紧密嵌套）
        for layer in range(layers_n):
            base = disc_r * (_RING_WAVE_TOP - _RING_WAVE_GAP * layer)
            phase = layer * 0.02
            path = QPainterPath()
            for k in range(_RING_SEGMENTS):
                level = shape[(k + int(phase * _RING_SEGMENTS)) % _RING_SEGMENTS]
                # 波峰不能越过盘沿，波谷不能压进标签区
                radius = min(
                    disc_r * 0.84,
                    max(disc_r * _RING_WAVE_FLOOR, base + amp * (level - 0.5) * 2.0),
                )
                px = cu + radius * cos_t[k]
                py = cv + radius * sin_t[k]
                if k == 0:
                    path.moveTo(px, py)
                else:
                    path.lineTo(px, py)
            path.closeSubpath()

            fade = 1.0 - 0.08 * layer
            glow = QColor(color)
            glow.setAlpha(max(0, int(_RING_GLOW_ALPHA * fade)))
            painter.setPen(QPen(glow, 2.3))
            painter.drawPath(path)

            core = QColor(color)
            core.setAlpha(int(165 * fade))
            painter.setPen(QPen(core, 0.9))
            painter.drawPath(path)

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
        self._stop_track_sampler()
        if self._spectrum is not None:
            self._spectrum.stop()
            self._spectrum = None
