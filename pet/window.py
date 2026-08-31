# -*- coding: utf-8 -*-
"""
桌宠主窗口 —— 透明无边框置顶窗口 + 动画链状态机 + 移动驱动 + 交互。

状态机（对应原插件 dsh-pet lib/client.js 的链式模型，行为 1:1 移植）：
  - 每个动画一次性播放，播完按概率选下一个：30% 待机 / 10% 转向 / 40% 动作 / 20% 移动；
  - 转向（东张西望）播完翻转朝向；facing=right 时水平镜像；
  - 点击回应 / 拖拽动画播完先回待机缓冲，待机播完再进随机链；
  - 移动：动画只提供"走路姿态"（3 选 1），位置由 QTimer 驱动，
    开头/结尾各 2s 不动，中间按播放进度插值；
  - 透明区域鼠标穿透：每帧用当前帧 alpha 生成窗口 mask（等效原版命中层设计）。
"""

from __future__ import annotations

import ctypes
from ctypes import wintypes
import json
import logging
import os
import math
import random
import sys
import threading
import time
from pathlib import Path
from typing import Any

import shiboken6

from PySide6.QtCore import (
    QEasingCurve,
    QElapsedTimer,
    QPoint,
    QPointF,
    QPropertyAnimation,
    QRect,
    Qt,
    QTimer,
    Signal,
    Slot,
)
from PySide6.QtGui import QBitmap, QColor, QCursor, QImage, QPainter, QPen, QPixmap, QRegion
from PySide6.QtWidgets import QApplication, QInputDialog, QMenu, QToolTip, QWidget

from . import autostart as autostart_mod
from . import catalog
from .config import (
    DEFAULT_SELF_TALK_BUBBLE_STYLE,
    DEFAULT_SELF_TALK_DURATION_SECONDS,
    DEFAULT_SELF_TALK_MAX_INTERVAL,
    DEFAULT_SELF_TALK_MIN_INTERVAL,
    DEFAULT_SELF_TALK_TEXTS,
    Config,
    _float_or_default,
)
from .library import MovieLibrary
from .animation_thumbnail import decode_representative_frame, representative_frame_index
from .speech_bubble import PetSpeechBubble, list_self_talk_images
from .fun_image_popup import oijingjing_image_path, resolve_fun_asset
from .context_menu import populate_context_menu as _populate_context_menu
from .context_menus.shared import take_deferred_menu_callbacks
from . import vision as vision_mod
from . import physics as physics_mod
from . import collision
from . import collision_debug
from .click_sound import (
    choose_sound, play_sound, resolve_click_sound_candidates, resolve_click_sound_pair,
    play_press_sound, play_release_sound,
)
from .proactive import effective_proactive_config
from .updater import QUARK_PAN_URL, REPO_URL

# 后台播放音乐时自动播放的唱歌/哼歌动画
SING_ANIM = '悠闲哼歌'


def _resolve_self_talk_image_dir(raw: str) -> str:
    """Resolve the self-talk image directory; empty keeps text-only behavior.

    用户显式配置的外部目录被删除后不再回退到内置彩蛋池（用户删目录的
    意图就是"不要再看图"），直接走纯文本；相对路径（内置 assets）保留
    回退以兼容便携包目录迁移。
    """
    raw = str(raw or '').strip()
    if not raw:
        return ''
    candidate = Path(raw).expanduser()
    if candidate.is_absolute() and not candidate.is_dir():
        return ''
    return str(resolve_fun_asset(raw, oijingjing_image_path().parent))


def _keep_macos_tool_window_visible(window) -> None:
    """Tool windows must remain visible while another application is active.

    This is independent from the configurable z-order. Without the attribute,
    Cocoa automatically hides a Qt.Tool window when the accessory application
    resigns active, which looked like the WebM Chat pet had exited.
    """
    if sys.platform == 'darwin':
        window.setAttribute(Qt.WidgetAttribute.WA_MacAlwaysShowToolWindow, True)


def _mac_set_window_level(view_id: int, level: int) -> bool:
    """macOS 原生：把 NSWindow 层级设为指定值（3=置顶浮动，0=普通）。

    Qt 的 WindowStaysOnTopHint 在 macOS 上对无边框 Tool 窗口/运行时切换不可靠，
    这里用 objc runtime 直接调 [NSWindow setLevel:] 强制生效（ctypes 零依赖）。

    只在真实 cocoa 平台执行：offscreen/minimal 等测试平台下 winId() 不是
    NSView 指针，objc_msgSend 会直接 SIGSEGV（无法被 try/except 捕获）。
    """
    if sys.platform != 'darwin':
        return False
    try:
        from PySide6.QtGui import QGuiApplication
        if QGuiApplication.platformName() != 'cocoa':
            return False
    except Exception:
        return False
    try:
        import ctypes
        import ctypes.util

        lib_path = ctypes.util.find_library('objc') or '/usr/lib/libobjc.A.dylib'
        objc = ctypes.cdll.LoadLibrary(lib_path)

        # 关键：sel_registerName 返回 SEL（64 位指针）。ctypes 默认按 c_int(32 位)
        # 截断返回值，损坏的 SEL 会让 ObjC runtime 段错误（SIGSEGV），必须显式声明
        objc.sel_registerName.restype = ctypes.c_void_p
        objc.sel_registerName.argtypes = [ctypes.c_char_p]

        msg = objc.objc_msgSend
        msg.restype = ctypes.c_void_p

        sel_window = objc.sel_registerName(b'window')
        sel_set_level = objc.sel_registerName(b'setLevel:')
        sel_order_front = objc.sel_registerName(b'orderFrontRegardless')

        # [view window] —— 无参，返回 NSWindow*
        msg.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        window = msg(ctypes.c_void_p(view_id), sel_window)
        if not window:
            return False

        # [window setLevel:level] —— 一个 NSInteger 参数
        msg.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_long]
        msg(ctypes.c_void_p(window), sel_set_level, level)
        if level > 0:
            # Changing WindowStaysOnTopHint recreates the NSWindow. Setting the
            # floating level alone may leave the replacement ordered behind
            # the currently active application until Cocoa's next ordering
            # pass; orderFrontRegardless commits the new level immediately.
            msg.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
            msg(ctypes.c_void_p(window), sel_order_front)
        return True
    except Exception:
        return False


# 直播捕获兼容模式下窗口标题（普通顶层窗口需要可见标题，供直播姬/OBS 选择）
STREAM_CAPTURE_TITLE = 'dsh-pet 桌宠'

IDLE = "IDLE"
PRESS_CANDIDATE = "PRESS_CANDIDATE"
DRAGGING = "DRAGGING"
SLINGSHOT_AIMING = "SLINGSHOT_AIMING"
THROWN = "THROWN"
COLLISION_HIT_MIN_DV = 300.0
# 抛掷中的桌宠也只吸收超过此值的冲量修正：静置接触的 e=0 抵消微冲量
# （十几 px/s）会把贴地桌宠永远顶在静止线以上，形成自供能原地抖动
COLLISION_CONTACT_DV_FLOOR = 50.0


def build_window_flags(config, mouse_through: bool = False, stream_capture_mode: bool = False):
    """构造桌宠窗口 flags。

    默认形态：FramelessWindowHint | Tool（Windows 上映射 WS_EX_TOOLWINDOW，
    不进任务栏/Alt+Tab，但直播姬、OBS 等窗口捕获软件会过滤掉 Tool 窗口）。
    开启直播捕获兼容模式后改用普通顶层窗口（Window）并设置标题，
    使窗口出现在捕获软件的可选窗口列表里；代价是任务栏会显示图标。
    """
    if stream_capture_mode:
        flags = Qt.WindowType.FramelessWindowHint | Qt.WindowType.Window
    else:
        flags = Qt.WindowType.FramelessWindowHint | Qt.WindowType.Tool
    if config.get('on_top', True):
        flags |= Qt.WindowType.WindowStaysOnTopHint
    if mouse_through:
        flags |= Qt.WindowType.WindowTransparentForInput
    return flags


def _squash_geometry(
    window_width: int,
    window_height: int,
    frame_width: int,
    frame_height: int,
    progress: float,
) -> tuple[int, int, int, int]:
    """返回 Q 弹帧的逻辑坐标，避免把 DPR 物理像素当成 QWidget 坐标。"""
    progress = max(0.0, min(1.0, float(progress)))
    pulse = math.sin(math.pi * progress)
    sy = 1.0 - 0.15 * pulse
    sx = 1.0 + 0.10 * pulse
    width = max(1, int(round(frame_width * sx)))
    height = max(1, int(round(frame_height * sy)))
    x = int(round((window_width - width) / 2))
    y = window_height - height
    return x, y, width, height


def _clamp_menu_rect(rect: QRect, avail: QRect) -> QRect:
    """把菜单矩形夹到可用屏幕区域内（保持尺寸不变）。"""
    if avail.isEmpty():
        return QRect(rect)
    x = min(max(rect.x(), avail.left()), max(avail.left(), avail.right() - rect.width() + 1))
    y = min(max(rect.y(), avail.top()), max(avail.top(), avail.bottom() - rect.height() + 1))
    return QRect(x, y, rect.width(), rect.height())


def animate_context_menu_to(
    menu: QMenu,
    target: QPoint,
    *,
    duration_ms: int = 140,
) -> QPropertyAnimation | None:
    """Slide a visible menu to its safe target without changing its layout."""
    target = QPoint(target)
    if menu.pos() == target:
        return None
    animation = QPropertyAnimation(menu, b"pos", menu)
    animation.setDuration(max(1, int(duration_ms)))
    animation.setStartValue(menu.pos())
    animation.setEndValue(target)
    animation.setEasingCurve(QEasingCurve.Type.OutCubic)
    menu._position_transition = animation
    animation.start()
    return animation


def pick_context_menu_position(
    pet_rect: QRect,
    menu_size,
    submenu_width: int,
    avail: QRect,
    margin: int = 10,
) -> tuple[QPoint, Qt.LayoutDirection]:
    """选择右键根菜单弹出位置，使其避开角色并保持在可用屏幕内。

    优先级：
    1. 角色右侧（子菜单默认向右展开，远离角色）；
    2. 角色左侧（视觉方向不变，根菜单保持同样的短间距）；
    3. 屏幕里让整棵 LTR 菜单树与角色重叠最少的角落。
    """
    menu_w = max(1, menu_size.width())
    menu_h = max(1, menu_size.height())
    submenu_width = max(0, int(submenu_width))

    # 1) 右侧：根菜单整体在角色右侧，且子菜单向右有空间
    root = _clamp_menu_rect(
        QRect(pet_rect.right() + margin, pet_rect.top(), menu_w, menu_h), avail
    )
    if (
        root.left() >= pet_rect.right() + margin
        and root.right() + submenu_width <= avail.right()
        and avail.contains(root)
    ):
        return root.topLeft(), Qt.LayoutDirection.LeftToRight

    # 2) 左侧：只按根菜单宽度避让角色。Qt 可根据屏幕空间调整子菜单
    # 的实际弹出侧；布局方向仍为 LTR，因此文字、图标和箭头不会镜像。
    root = _clamp_menu_rect(
        QRect(
            pet_rect.left() - margin - menu_w,
            pet_rect.top(),
            menu_w,
            menu_h,
        ),
        avail,
    )
    if (
        root.right() <= pet_rect.left() - margin
        and avail.contains(root)
    ):
        return root.topLeft(), Qt.LayoutDirection.LeftToRight

    # 3) 远角兜底：视觉方向始终 LTR，按整棵菜单树计算占位和重叠。
    tree_w = menu_w + submenu_width
    right_x = max(
        avail.left() + margin,
        avail.right() - tree_w + 1 - margin,
    )
    corners = (
        (QPoint(avail.left() + margin, avail.top() + margin), Qt.LayoutDirection.LeftToRight),
        (QPoint(right_x, avail.top() + margin), Qt.LayoutDirection.LeftToRight),
        (QPoint(avail.left() + margin, max(avail.top() + margin, avail.bottom() - menu_h + 1 - margin)), Qt.LayoutDirection.LeftToRight),
        (QPoint(right_x, max(avail.top() + margin, avail.bottom() - menu_h + 1 - margin)), Qt.LayoutDirection.LeftToRight),
    )
    best = None
    best_area: int | None = None
    for point, direction in corners:
        tree = QRect(point.x(), point.y(), tree_w, menu_h)
        overlap = tree.intersected(pet_rect)
        area = overlap.width() * overlap.height()
        if best_area is None or area < best_area:
            best = (point, direction)
            best_area = area
    return best


def wander_target_y(
    start_y: float,
    top: float,
    bottom: float,
    height: float,
    margin: float,
    rnd=random,
) -> int:
    """Pick a bounded vertical wander target; injectable RNG keeps it testable."""
    y_lo = top + margin
    y_hi = bottom - height - margin
    if y_hi <= y_lo:
        return int(start_y)
    max_dy = max(40, int((y_hi - y_lo) * 0.25))
    return int(max(y_lo, min(y_hi, start_y + rnd.randint(-max_dy, max_dy))))


# ---- Win32：全屏判定用常量/结构 ----
GWL_STYLE = -16             # GetWindowLongW：取窗口样式
GWL_EXSTYLE = -20           # GetWindowLongW：取扩展样式
_WS_CAPTION = 0x00C00000    # WS_BORDER | WS_DLGFRAME（带标题栏）
_WS_EX_TOPMOST = 0x00000008  # 置顶：真全屏游戏/视频几乎必带，普通最大化窗口不带
_WS_EX_TRANSPARENT = 0x00000020


class _WinRect(ctypes.Structure):
    _fields_ = [('left', ctypes.c_long), ('top', ctypes.c_long),
                ('right', ctypes.c_long), ('bottom', ctypes.c_long)]


class _WinMonitorInfo(ctypes.Structure):
    """GetMonitorInfoW 的 MONITORINFO（只读 rcMonitor：显示器完整几何，物理像素）。"""
    _fields_ = [('cbSize', ctypes.c_ulong), ('rcMonitor', _WinRect),
                ('rcWork', _WinRect), ('dwFlags', ctypes.c_ulong)]


def _set_windows_click_through(hwnd: int, enabled: bool, user32=None) -> bool:
    """切换 layered HWND 的输入穿透扩展样式。"""
    user32 = user32 or ctypes.windll.user32
    style = int(user32.GetWindowLongW(hwnd, GWL_EXSTYLE))
    updated = style | _WS_EX_TRANSPARENT if enabled else style & ~_WS_EX_TRANSPARENT
    if updated == style:
        return False
    user32.SetWindowLongW(hwnd, GWL_EXSTYLE, updated)
    return True


def _set_speech_bubble_interactive(pet) -> None:
    """按当前是否可打开快速对话，切换气泡鼠标穿透/可点击。"""
    setter = getattr(pet._speech_bubble, "set_interactive", None)
    if callable(setter):
        setter(callable(getattr(pet, "on_open_quick_chat", None)))


class WindowsPerPixelInputController:
    """根据光标所在像素动态切换 layered window 的输入穿透。

    HTTRANSPARENT 只能继续命中当前线程的窗口，无法穿透到其他应用。
    WS_EX_TRANSPARENT 会让 Windows 在命中时跳过 layered 桌宠窗口；独立
    定时器在窗口不再收到鼠标消息时仍能检测光标并恢复角色区域交互。
    """

    NORMAL_POLL_INTERVAL_MS = 10
    DRAG_POLL_INTERVAL_MS = 100

    def __init__(self, window: "PetWindow") -> None:
        self._window = window
        self._timer = QTimer(window)
        self._timer.setInterval(self.NORMAL_POLL_INTERVAL_MS)
        self._timer.timeout.connect(self.refresh)
        self._timer.start()

    def should_click_through(self, global_pos: QPoint) -> bool:
        win = self._window
        if win.mouse_through:
            return True
        if getattr(win, '_press_global', None) is not None or not win.isVisible():
            return False
        local = win.mapFromGlobal(global_pos)
        if not QRect(0, 0, win.width(), win.height()).contains(local):
            return False
        return win._is_transparent_at(local)

    def refresh(self) -> None:
        try:
            enabled = self.should_click_through(QCursor.pos())
            _set_windows_click_through(int(self._window.winId()), enabled)
        except (AttributeError, OSError, RuntimeError):
            logging.debug("更新 Windows 逐像素鼠标穿透失败", exc_info=True)

    def set_drag_active(self, active: bool) -> None:
        """拖拽按下/松手时切换轮询频率。

        拖拽（_press_global 非 None）期间 should_click_through 恒返回 False，
        每 10ms 轮询纯属空转：降频到 100ms 减少 Win32/QCursor 调用。
        松手后立即恢复原频率并强制刷新一次穿透状态；非拖拽状态重复调用是 no-op。
        """
        if active:
            if self._timer.interval() != self.DRAG_POLL_INTERVAL_MS:
                self._timer.setInterval(self.DRAG_POLL_INTERVAL_MS)
            return
        if self._timer.interval() == self.NORMAL_POLL_INTERVAL_MS:
            return
        self._timer.setInterval(self.NORMAL_POLL_INTERVAL_MS)
        self.refresh()

    def stop(self) -> None:
        self._timer.stop()
        if not self._window.mouse_through:
            try:
                _set_windows_click_through(int(self._window.winId()), False)
            except (AttributeError, OSError, RuntimeError):
                pass


class PetWindow(QWidget):
    """桌宠窗口本体。"""

    look_done = Signal(str, str, bool)
    fullscreen_changed = Signal(bool)  # 全屏 watcher 线程 → 主线程（隐藏/恢复桌宠）
    cursor_visibility_changed = Signal(str)

    def __init__(self, lib: MovieLibrary, config: Config, collision_session=None) -> None:
        super().__init__()
        self.lib = lib
        self.cfg = config
        self._collision_session = None
        self._collision_seq = 0
        self._collision_last_state = None
        self._collision_last_submit_at = 0.0  # 非 force 提交 20Hz 限流时间戳
        self._applied_collision_policy: dict | None = None  # 已同步到会话的碰撞策略
        self._collision_epoch = ''
        self._collision_peer_snapshots: dict[str, dict[str, Any]] = {}
        self._predicted_bounces: dict[str, float] = {}
        self._pending_predicted_bounce: tuple[float, float] | None = None
        self._pending_predicted_contact: tuple[float, float, list[list[float]]] | None = None
        self._collision_impulse_watermarks = collision.WatermarkDeduplicator()
        self.on_switch_character = None  # 由 app 注入，用于运行时切换角色
        self.on_open_chat = None
        self.on_open_quick_chat = None
        self.on_open_modern_chat = None
        self.on_open_chat_settings = None
        self.on_show_balance = None
        self.on_check_update = None
        self.on_look_synced = None
        self.on_look_screen = None
        self.on_open_legacy_settings = None
        self.on_open_modern_settings = None
        self.on_restore_fun_windows = None
        self.on_spawn_pet = None
        self.on_hidden = None  # 由 app 注入：用户主动隐藏时弹托盘提示
        self._position_listeners = []
        self._animation_icon_image_cache: dict[str, QImage] = {}
        self._animation_icon_inflight: dict[str, threading.Event] = {}
        self._animation_icon_cache_lock = threading.Lock()

        # 根据当前形象实际拥有的动画动态计算分类，支持不同角色动作不一致
        self.cats = catalog.build_categories(lib.names(), getattr(lib, 'manifest', None), getattr(lib, 'folder_map', None), getattr(lib, 'folder_files', None))
        self.idle = self.cats['idle']
        self.turn = self.cats['turn']
        self.idles = self.cats['idles']
        self.turns = self.cats['turns']
        self.moves = self.cats['moves']
        self.clicks = self.cats['clicks']
        self.drag = self.cats['drag']
        self.acts = self.cats['acts']

        # 预载拖拽动画首帧，避免第一次进入拖拽状态时同步解码卡顿
        if self.drag:
            self.lib.movie(self.drag).jumpToFrame(0)

        self.playback_speed: float = float(config.get('playback_speed', 1.0))
        self._user_mouse_through = bool(config.get('mouse_through', False))
        self._auto_cursor_hidden = False
        self._cursor_visibility = 'UNKNOWN'
        self._cursor_hidden_since: float | None = None
        self._cursor_restore_pending = False
        self._cursor_hidden_passthrough = bool(config.get('cursor_hidden_passthrough', True))
        self.mouse_through: bool = self._user_mouse_through
        self.drag_physics: bool = bool(config.get('drag_physics', False))
        self.lock_position: bool = bool(config.get('lock_position', False))
        self.shift_drag: bool = bool(config.get('shift_drag', False))
        self.pet_opacity: int = int(_float_or_default(config.get('pet_opacity', 100), 100, 10, 100))
        self._applied_opacity: float | None = None  # 已应用到窗口的不透明度
        self.click_sound_path: str = str(config.get('click_sound_path', '') or '')
        self.click_show_balance: bool = bool(config.get('click_show_balance', False))
        self.click_show_self_talk: bool = bool(config.get('click_show_self_talk', False))
        self.animation_gap_seconds: float = max(0.0, min(3600.0, float(config.get('animation_gap_seconds', 0.0))))
        self._animation_gap_active = False
        self._animation_gap_timer = QTimer(self)
        self._animation_gap_timer.setSingleShot(True)
        self._animation_gap_timer.timeout.connect(self._on_animation_gap_timeout)
        self._speech_bubble = PetSpeechBubble(
            style_id=str(config.get('self_talk_bubble_style', DEFAULT_SELF_TALK_BUBBLE_STYLE))
        )
        self._speech_bubble.clicked.connect(self._on_speech_bubble_clicked)
        self._look_busy = False
        self._last_look_ts = 0.0
        self.look_done.connect(self._on_look_done)
        self._self_talk_enabled = bool(config.get('self_talk_enabled', False))
        self._self_talk_texts = self._read_self_talk_texts(config.get('self_talk_texts'))
        self._self_talk_duration_seconds = max(
            1.0,
            min(300.0, float(config.get(
                'self_talk_duration_seconds', DEFAULT_SELF_TALK_DURATION_SECONDS
            ))),
        )
        self._self_talk_image_dir = str(config.get('self_talk_image_dir', '') or '')
        self._self_talk_images = list_self_talk_images(_resolve_self_talk_image_dir(self._self_talk_image_dir))
        self._self_talk_min_interval = max(5.0, float(config.get('self_talk_min_interval', DEFAULT_SELF_TALK_MIN_INTERVAL)))
        self._self_talk_max_interval = max(self._self_talk_min_interval, float(config.get('self_talk_max_interval', DEFAULT_SELF_TALK_MAX_INTERVAL)))
        self._self_talk_timer = QTimer(self)
        self._self_talk_timer.setSingleShot(True)
        self._self_talk_timer.timeout.connect(self._on_self_talk_timeout)
        # 后台音乐检测：默认关闭，开启后检测到系统正在输出音频就播放唱歌动画
        self._music_sing_enabled = bool(config.get('music_sing_enabled', False))
        self._music_sing_active = False
        self._music_sing_timer = QTimer(self)
        self._music_sing_timer.setInterval(4000)
        self._music_sing_timer.timeout.connect(self._check_music_sing)
        # 重要气泡（主动识屏先兆/答复、Agent 联动提醒等）占用期间，自言自语让路，
        # 避免"让我看看……"刚出来就被自言自语顶掉、答复又顶掉自言自语的连环抢占。
        self._bubble_busy_until = 0.0
        # 设置窗口打开期间暂停气泡，避免置顶气泡盖住设置界面
        self._bubble_suppressed = False

        # Agent 联动动作衔接：正在播一次性动作时联动动作不打断，存为待播（最新覆盖旧的），
        # 等当前动作播完由 _on_anim_ended 自然接上；联动动作播完仍有 Agent 在忙则接下一个。
        self._pending_link_anim: str | None = None
        self._link_anim_current: str | None = None
        self._link_next_provider = None  # AgentLinkManager 注入：()->str|None

        # 主动识屏后台观察器（必须作为 PetWindow 的子成员，随窗口销毁/重建）
        from .proactive import ProactiveScreenWatcher
        self.proactive_watcher = ProactiveScreenWatcher(self, config)

        # 多 Agent 状态感知管理器
        from .agent_link import AgentLinkManager
        self.agent_link_manager = AgentLinkManager(self, config)

        # ---- 全屏应用自动隐藏（Windows）----
        # 前台窗口覆盖整个屏幕几何（含任务栏区域）时自动隐藏桌宠，
        # 全屏退出后自动恢复。最大化窗口不覆盖任务栏，不会误触发。
        # 后台线程轮询 + 信号回主线程：QTimer 轮询在实测中多起「启动数秒后
        # 静默停发 timeout」的疑难，线程通道不受其影响；检测为纯 win32 调用。
        self.auto_hide_fullscreen: bool = bool(config.get('auto_hide_fullscreen', True))
        self._auto_hidden = False  # 只恢复"由本 watcher 隐藏"的状态，尊重手动隐藏
        self._fs_stop = threading.Event()
        self._fs_thread: threading.Thread | None = None
        self._fs_last = False
        self.fullscreen_changed.connect(self._on_fullscreen_changed)
        self.cursor_visibility_changed.connect(self._on_cursor_visibility_changed)

        # ---- 窗口属性：无边框 + 透明 + 不进任务栏；置顶可配置 ----
        # 直播捕获兼容模式（stream_capture_mode）：Tool → 普通顶层窗口 + 标题，
        # 使直播姬/OBS 的窗口捕获能枚举到桌宠（Tool 窗口会被捕获软件过滤）。
        self._stream_capture_mode = bool(config.get('stream_capture_mode', False))
        flags = build_window_flags(config, self.mouse_through, self._stream_capture_mode)
        self.setWindowFlags(flags)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        if self._stream_capture_mode:
            self.setWindowTitle(STREAM_CAPTURE_TITLE)
        # Cocoa hides Tool windows when an accessory application deactivates.
        # Visibility and z-order are separate: always keep the pet visible,
        # then use WindowStaysOnTopHint/NSWindow level for the on-top setting.
        _keep_macos_tool_window_visible(self)
        app = QApplication.instance()
        if app is not None:
            app.applicationStateChanged.connect(self._on_application_state_changed)

        # ---- 状态 ----
        self.anim: str = self.idle
        self.facing: str = config.get('facing', 'left')  # left | right
        self.scale: float = float(config.get('scale', catalog.DEFAULT_SCALE))
        self.no_move: bool = bool(config.get('no_move', False))  # 不移动：禁用自动移动
        self.movie = None
        self._frame_pixmap: QPixmap | None = None
        # 角色可见轮廓（窗口局部坐标）与逐像素命中缓存；贴边功能复用 _mask_bounds
        self._mask_bounds: QRect | None = None
        # 碰撞体稳定边界：当前动画各帧 _mask_bounds 的并集（只增不减，
        # 切换动画/缩放时重置），避免圆链随动画帧缩放跳动导致漏判
        self._collision_local_bounds: QRect | None = None
        self._hit_alpha_image: QImage | None = None
        self._input_controller: WindowsPerPixelInputController | None = None
        if os.name == "nt":
            self._input_controller = WindowsPerPixelInputController(self)
        # 窗口隐藏时暂停动画解码/定时器；显示时由 showEvent 恢复
        self._hidden_paused = False
        self._ended_fired = False

        # ---- 交互状态 ----
        self._press_global: QPoint | None = None
        self._grab_offset: QPoint | None = None  # 按下时 鼠标全局坐标 - 窗口左上角
        self._dragging = False
        self._just_dragged = False               # 抑制拖拽结束后的幽灵点击
        self._interaction_state = "IDLE"
        self._context_menu_suppressed = False
        self._slingshot_anchor_pos: QPoint | None = None
        self._slingshot_anchor_mouse: QPoint | None = None
        self._slingshot_mouse: QPoint | None = None
        self._slingshot_pull = QPoint(0, 0)
        self.slingshot_enabled = bool(config.get("slingshot_enabled", True))

        # ---- 移动驱动 ----
        self._move_plan: dict | None = None
        self._move_timer = QTimer(self)
        self._move_timer.setInterval(33)         # ~30fps 位置插值
        self._move_timer.timeout.connect(self._on_move_tick)

        # ---- 点击 Q 弹效果 ----
        self._squash_timer = QTimer(self)
        self._squash_timer.setInterval(16)
        self._squash_timer.timeout.connect(self._on_squash_tick)
        self._squash_clock = QElapsedTimer()
        self._squash_active = False
        self._squash_duration_ms = 220
        self._squash_progress = 1.0
        self._last_collision_squash_at = float('-inf')
        self._last_collision_sound_at = float('-inf')
        self._press_sound_pair = None
        self._press_sound_started_at: float | None = None
        self._slingshot_rebound_progress = 0.0

        # ---- 拖动物理 ----
        self._physics_timer = QTimer(self)
        self._physics_timer.setInterval(16)
        self._physics_timer.timeout.connect(self._on_physics_tick)
        self._physics_mode: str | None = None  # None / 'drag' / 'throw'
        self._phys_pos = [0.0, 0.0]
        self._phys_vel = [0.0, 0.0]
        self._drag_target: QPoint | None = None
        self._last_global: QPoint | None = None
        self._last_move_time = 0.0
        self._trail: list[tuple[float, float, float]] = []
        self._throw_speed_cap = physics_mod.throw_speed_cap(config.get("throw_strength"))
        self.throw_strength = physics_mod.normalize_throw_strength(config.get("throw_strength"))
        self._last_physics_tick_time: float | None = None

        self._collision_timer = QTimer(self)
        self._collision_timer.setInterval(500)
        self._collision_timer.timeout.connect(lambda: self._submit_collision_state(force=True))

        # ---- 尺寸与初始状态 ----
        self._apply_scale()
        # 懒加载：不再预先连接全部 91 个 clip 的信号；
        # 实际播放某个动画时由 _switch -> _connect_movie 按需连接。
        self._connected_movies: set[str] = set()

        # 副屏位置恢复：开机自启时副屏可能还没就绪（显示器唤醒慢于自启），
        # 记录的目标屏此刻枚举不到 → 先落主屏，然后等它上线再自动恢复。
        # 等待方式 = 5s 轮询（兜底，覆盖"信号已发但屏尚未进枚举"的竞态）
        #         + screenAdded 即时触发（常规路径秒回）。
        # 用户真正开始拖动/点「回到右下角」立即撤防（尊重手动选择），2 分钟超时撤防。
        self._awaiting_saved_screen: str | None = None
        self._screen_restore_armed = False
        self._screen_retry_deadline = 0.0
        self._screen_retry_timer = QTimer(self)
        self._screen_retry_timer.setInterval(5000)
        self._screen_retry_timer.timeout.connect(self._screen_retry_tick)

        self._restore_position()
        self._switch(self.idle)
        if self._music_sing_enabled:
            self._music_sing_timer.start()
        self._schedule_self_talk()
        if self._watch_required():
            self._start_fs_watch()

        if self._awaiting_saved_screen:
            self._arm_screen_restore_retry()

        self.attach_collision_session(collision_session)

    @property
    def click_sound_enabled(self) -> bool:
        return bool(self.cfg.get('click_sound_enabled', True))

    @click_sound_enabled.setter
    def click_sound_enabled(self, value: bool) -> None:
        self.cfg.set('click_sound_enabled', bool(value))

    @property
    def collision_sound_enabled(self) -> bool:
        return bool(self.cfg.get('collision_sound_enabled', True))

    @collision_sound_enabled.setter
    def collision_sound_enabled(self, value: bool) -> None:
        self.cfg.set('collision_sound_enabled', bool(value))

    @property
    def collision_sound_volume(self) -> float:
        return float(self.cfg.get('collision_sound_volume', 0.70))

    @collision_sound_volume.setter
    def collision_sound_volume(self, value: float) -> None:
        self.cfg.set('collision_sound_volume', float(value))

    def _arm_screen_restore_retry(self) -> None:
        """目标副屏暂未就绪：启动 5s 轮询 + screenAdded 监听，等它上线。"""
        from PySide6.QtGui import QGuiApplication
        app = QGuiApplication.instance()
        if app is None:
            return
        self._screen_retry_deadline = time.monotonic() + 120.0
        if not self._screen_restore_armed:
            app.screenAdded.connect(self._screen_retry_tick)
            self._screen_restore_armed = True
            logging.debug('已监听屏幕变化，等待 %s 上线', self._awaiting_saved_screen)
        self._screen_retry_timer.start()  # start() 即重启，超时窗口随之刷新

    def _disarm_screen_restore_retry(self) -> None:
        self._awaiting_saved_screen = None
        if hasattr(self, '_screen_retry_timer'):
            self._screen_retry_timer.stop()
        if not self._screen_restore_armed:
            return
        self._screen_restore_armed = False
        from PySide6.QtGui import QGuiApplication
        app = QGuiApplication.instance()
        if app is not None:
            try:
                app.screenAdded.disconnect(self._screen_retry_tick)
            except (RuntimeError, TypeError):
                pass

    def _screen_retry_tick(self, *_args) -> None:
        """轮询/screenAdded 共用入口：目标屏一旦进入枚举立即恢复位置。"""
        target = self._awaiting_saved_screen
        if not target:
            self._disarm_screen_restore_retry()
            return
        if time.monotonic() > self._screen_retry_deadline:
            logging.info('等待屏幕 %s 超时（120s），放弃自动恢复', target)
            self._disarm_screen_restore_retry()
            return
        # _screen_available 找不到目标屏时回退当前屏（名字不匹配），找到才算上线
        scr = self._screen_available(target)
        if scr is not None and scr.name() == target:
            self._disarm_screen_restore_retry()
            self._restore_position()
            logging.info('目标屏幕 %s 上线，已恢复到保存位置', target)

    def _on_screen_added_restore(self, screen) -> None:
        """兼容入口：新屏幕上线 → 立即触发一次检查。"""
        self._screen_retry_tick()

    # ================================================================ 尺寸
    def _apply_scale(self) -> None:
        """按缩放计算窗口尺寸：宽度 220×scale，高度 (124+落地偏移)×scale。"""
        self._w = max(1, int(round(catalog.CANVAS_W * self.scale)))
        self._h = max(1, int(round((catalog.CANVAS_H + catalog.PAD) * self.scale)))
        self.setFixedSize(self._w, self._h)

    def change_scale(self, scale: float) -> None:
        """切换缩放；保持窗口底边不动（脚踩的地面不变）。"""
        if abs(scale - self.scale) < 1e-6:
            return
        old_bottom = self.geometry().bottom()
        self.scale = scale
        self._apply_scale()
        self._collision_local_bounds = None
        self.move(self.x(), old_bottom - self._h + 1)
        self._rebuild_frame()
        if self._speech_bubble.isVisible():
            self._speech_bubble.reflow(
                self.visible_content_rect(), pet_scale=self.scale
            )
        self.update()
        self._save_position()

    # ================================================================ 位置
    def _screen_available(self, screen_name: str | None = None):
        """返回指定或窗口所在屏幕；macOS 上 self.screen() 失效时兜底主屏。"""
        from PySide6.QtGui import QGuiApplication
        if screen_name:
            for screen in QGuiApplication.screens():
                if screen.name() == screen_name:
                    return screen
        scr = self.screen()
        if scr is None:
            scr = QGuiApplication.primaryScreen()
        return scr

    def add_position_listener(self, listener) -> None:
        if callable(listener) and listener not in self._position_listeners:
            self._position_listeners.append(listener)

    def remove_position_listener(self, listener) -> None:
        try:
            self._position_listeners.remove(listener)
        except ValueError:
            pass

    def visible_content_rect(self) -> QRect:
        """Return the current visible character bounds in global coordinates.

        The pet window includes a transparent canvas and landing padding. The
        alpha mask is the source of truth for the actual visible character, so
        other windows can be placed beside the character instead of beside the
        transparent canvas.
        """
        frame_rect = self.frameGeometry()
        local_rect = self.character_local_region()
        if not local_rect.isEmpty():
            return QRect(frame_rect.topLeft() + local_rect.topLeft(), local_rect.size())
        mask = self.mask()
        if not mask.isEmpty():
            local_rect = mask.boundingRect()
            if not local_rect.isEmpty():
                return QRect(frame_rect.topLeft() + local_rect.topLeft(), local_rect.size())
        return frame_rect

    def _restore_position(self) -> None:
        """恢复上次位置（按屏幕比例），无记录则落右下角。
        保存位置时所在的屏幕此刻不在线（如开机自启时副屏未就绪）→
        落当前屏并记下目标屏，由 screenAdded 监听在它上线后重新恢复。"""
        saved_screen = self.cfg.get('screen_name')
        scr = self._screen_available(saved_screen)
        if saved_screen and scr.name() != saved_screen:
            self._awaiting_saved_screen = saved_screen
            logging.info('目标屏幕 %s 暂不在线，先落在 %s，等它上线后自动恢复',
                         saved_screen, scr.name())
        else:
            self._awaiting_saved_screen = None
        avail = scr.availableGeometry()
        rx, ry = self.cfg.get('rx'), self.cfg.get('ry')
        if rx is None or ry is None:
            x = avail.right() - self._w - catalog.CORNER_MARGIN
            y = avail.bottom() - self._h
        else:
            x = int(round(avail.left() + rx * avail.width())) - self._w // 2
            y = int(round(avail.top() + ry * avail.height())) - self._h // 2
            x = min(max(x, avail.left()), avail.right() - self._w)
            y = min(max(y, avail.top()), avail.bottom() - self._h)
        # 多开避让：与其他存活实例重叠时逐级向左错开（含双击重复启动
        # 同一实例的场景——它和有名字的 --instance 一样会撞位置）
        _rects_fn = getattr(self, '_live_instance_rects', None)
        others = _rects_fn() if callable(_rects_fn) else []
        if others:
            step = self._w + 48
            for _ in range(12):
                if not any(self._rects_overlap(x, y, self._w, self._h, o) for o in others):
                    break
                nx = max(avail.left(), x - step)
                if nx == x:
                    break  # 已经顶到屏幕左缘，无法再让
                x = nx
        logging.info('恢复位置 screen=%s avail=(%d,%d,%d,%d) dpr=%s -> (%d,%d)',
                     scr.name(), avail.left(), avail.top(), avail.right(),
                     avail.bottom(), scr.devicePixelRatio(), x, y)
        self.move(x, y)
        _marker_fn = getattr(self, '_write_runtime_marker', None)
        if callable(_marker_fn):
            _marker_fn()

    @staticmethod
    def _rects_overlap(x: int, y: int, w: int, h: int, other) -> bool:
        ox, oy, ow, oh = other
        return x < ox + ow and ox < x + w and y < oy + oh and oy < y + h

    @staticmethod
    def _pid_alive(pid: int) -> bool:
        """跨平台探活：Windows 用 OpenProcess，其余用 kill(pid, 0)。"""
        if pid <= 0:
            return False
        if os.name == 'nt':
            # PROCESS_QUERY_LIMITED_INFORMATION
            handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)
            if not handle:
                return False
            ctypes.windll.kernel32.CloseHandle(handle)
            return True
        try:
            os.kill(pid, 0)
            return True
        except OSError:
            return False

    def _live_instance_rects(self) -> list[tuple[int, int, int, int]]:
        """其他存活实例的窗口矩形（配置目录下 runtime-<pid>.json 标记）。

        死进程/损坏文件的标记顺手清理，避免越积越多。
        """
        rects: list[tuple[int, int, int, int]] = []
        try:
            files = list(self.cfg.dir.glob('runtime-*.json'))
        except OSError:
            return rects
        for f in files:
            try:
                data = json.loads(f.read_text(encoding='utf-8'))
                pid = int(data.get('pid', 0))
                if pid == os.getpid():
                    continue
                if not self._pid_alive(pid):
                    raise OSError('stale marker')
                x, y, w, h = (int(data.get(k, 0)) for k in ('x', 'y', 'w', 'h'))
                if w > 0 and h > 0:
                    rects.append((x, y, w, h))
            except (OSError, ValueError, TypeError):
                try:
                    f.unlink()
                except OSError:
                    pass
        return rects

    def _write_runtime_marker(self) -> None:
        """登记本实例的当前位置，供后启动的实例避让。"""
        try:
            marker = self.cfg.dir / f'runtime-{os.getpid()}.json'
            marker.write_text(json.dumps({
                'pid': os.getpid(),
                'x': self.x(), 'y': self.y(), 'w': self._w, 'h': self._h,
            }), encoding='utf-8')
        except OSError:
            pass

    def _save_position(self) -> None:
        """以"窗口中心相对屏幕可用区的比例"持久化位置（分辨率变化后仍正确）。
        等待目标副屏上线期间（_awaiting_saved_screen 非空）不写位置/屏名：
        当前只是临时落脚主屏，写回会把保存的副屏坐标永久覆盖。"""
        scr = self._screen_available()
        avail = scr.availableGeometry()
        if avail.width() <= 0 or avail.height() <= 0:
            return
        if not getattr(self, '_awaiting_saved_screen', None):
            cx = self.x() + self._w / 2
            cy = self.y() + self._h / 2
            self.cfg.set('rx', (cx - avail.left()) / avail.width())
            self.cfg.set('ry', (cy - avail.top()) / avail.height())
            self.cfg.set('screen_name', scr.name())
        self.cfg.set('facing', self.facing)
        self.cfg.set('scale', self.scale)
        self.cfg.save()
        _marker_fn = getattr(self, '_write_runtime_marker', None)
        if callable(_marker_fn):
            _marker_fn()

    def _go_default_corner(self) -> None:
        # 用户明确要求回右下角 = 手动位置决策，撤销"等副屏上线自动恢复"
        _disarm = getattr(self, '_disarm_screen_restore_retry', None)
        if callable(_disarm):
            _disarm()
        # Position can still be written by the animation interpolation timer or
        # drag-physics timer after a direct move. Stop both first, otherwise the
        # pet briefly reaches the corner and is immediately snapped back.
        self._cancel_move()
        self._stop_physics()
        self._drag_target = None
        scr = self._screen_available()
        avail = scr.availableGeometry()
        x = avail.right() - self._w - catalog.CORNER_MARGIN
        y = avail.bottom() - self._h
        logging.info('回到右下角 screen=%s avail=(%d,%d,%d,%d) dpr=%s -> (%d,%d)',
                     scr.name(), avail.left(), avail.top(), avail.right(),
                     avail.bottom(), scr.devicePixelRatio(), x, y)
        self.move(x, y)
        self._save_position()

    def _schedule_macos_window_level(self, on: bool) -> None:
        if sys.platform != 'darwin':
            return
        level = 3 if on else 0

        def apply_current_native_window() -> None:
            _mac_set_window_level(int(self.winId()), level)

        # Apply immediately, then again after Qt/Cocoa have processed the
        # native-window recreation and ordering events. winId is deliberately
        # resolved inside every callback so a stale NSView is never reused.
        apply_current_native_window()
        for delay in (0, 40, 160):
            QTimer.singleShot(delay, self, apply_current_native_window)

    def set_on_top(self, on: bool) -> None:
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, on)
        self.cfg.set('on_top', on)
        self.cfg.save()
        self.show()
        self._schedule_macos_window_level(on)
        if on:
            self.raise_()

    def _restore_on_top_after_context_menu(self) -> None:
        """Reassert the native floating level after menus/app activation changes."""
        if not bool(self.cfg.get('on_top', True)):
            return
        _keep_macos_tool_window_visible(self)
        self._schedule_macos_window_level(True)

    def _on_application_state_changed(self, _state) -> None:
        # Opening a native menu and then clicking another application can make
        # Cocoa reorder its owner Tool window. Reapply the level after the
        # activation transition without activating or stealing keyboard focus.
        QTimer.singleShot(0, self, self._restore_on_top_after_context_menu)

    def showEvent(self, event) -> None:  # noqa: N802 (Qt 命名)
        """窗口显示时校正层级（延迟执行，避免被 Qt 窗口重建覆盖）。"""
        super().showEvent(event)
        self._submit_collision_state(force=True)
        self._schedule_macos_window_level(bool(self.cfg.get('on_top', True)))
        self._apply_opacity()
        # 隐藏期暂停的活动在此恢复（与 hide() 中的 _pause_activity 配对）
        if self._hidden_paused:
            self._hidden_paused = False
            self._phys_vel[:] = [0.0, 0.0]
            self._resume_activity()
            self._submit_collision_state(force=True)
        self._restore_dock_icon_preference()

    def hide(self, *, notify: bool = True) -> None:
        """隐藏桌宠。

        macOS 同步打开 Dock 图标；notify=False 供角色切换等内部替换使用
        （不弹托盘提示、不 arm Dock 点击恢复监听）。
        隐藏即暂停动画解码与全部活动定时器（低功耗：不可见就零消耗）。
        """
        if getattr(self, "_interaction_state", IDLE) == SLINGSHOT_AIMING:
            self._cancel_slingshot_to_anchor()
        self._ensure_dock_icon_on_hide()
        self._hidden_paused = True
        self._pause_activity()
        super().hide()
        self._submit_collision_state(force=True)
        if not notify:
            return
        if callable(getattr(self, "on_hidden", None)):
            self.on_hidden()
        self._arm_dock_reactivate_restore()

    def _pause_activity(self) -> None:
        """暂停动画解码与所有活动定时器（窗口不可见时没有任何可见效果）。"""
        if not hasattr(self, 'movie'):
            return  # 未完整初始化（测试桩/构造早期）无可暂停
        if self.movie is not None:
            self.movie.stop()
        self._move_timer.stop()
        self._physics_timer.stop()
        # 全屏 watcher 不能在"全屏自动隐藏"期间停：它是退出全屏后
        # 重新 show() 的唯一检测路径，停了桌宠就再也回不来。
        # 只有手动隐藏（托盘/右键，_auto_hidden 为 False）才停它。
        if not self._auto_hidden:
            self._stop_fs_watch()
        self._self_talk_timer.stop()
        self._music_sing_timer.stop()
        self._animation_gap_timer.stop()
        self._squash_timer.stop()
        self._squash_active = False
        if hasattr(self, 'proactive_watcher') and self.proactive_watcher is not None:
            self.proactive_watcher.pause()
        if hasattr(self, 'agent_link_manager') and self.agent_link_manager is not None:
            self.agent_link_manager.pause()
        if hasattr(self, 'lib') and self.lib is not None and hasattr(self.lib, 'pause_warm'):
            self.lib.pause_warm()
        self._cancel_move()
        self._cancel_animation_gap()
        self._speech_bubble.hide()

    def _resume_activity(self) -> None:
        """显示时恢复动画与所需定时器（状态与隐藏前一致）。"""
        if not hasattr(self, 'movie'):
            return  # 未完整初始化（测试桩/构造早期）无可恢复
        if self.movie is not None:
            # 从当前动画第一帧重新开始：隐藏期间用户看不到，观感无差异；
            # 若隐藏前正在移动，_cancel_move 已清掉移动计划，不会出现"瞬移"。
            self._switch(self.anim)
        if self._watch_required():
            self._start_fs_watch()
        self._schedule_self_talk()
        if self._music_sing_enabled:
            self._music_sing_timer.start()
        if hasattr(self, 'proactive_watcher') and self.proactive_watcher is not None:
            self.proactive_watcher.resume()
        if hasattr(self, 'agent_link_manager') and self.agent_link_manager is not None:
            self.agent_link_manager.resume()
        if hasattr(self, 'lib') and self.lib is not None and hasattr(self.lib, 'resume_warm'):
            self.lib.resume_warm()

    def attach_collision_session(self, session) -> None:
        """绑定 PetApp 持有的 IPC facade，GUI 不接触 socket。"""
        self._collision_app_session = session
        self.detach_collision_session()
        if session is None or not bool(self.cfg.get('collision_enabled', True)):
            return
        self._collision_session = session
        session.impulse_ready.connect(self._on_collision_impulse, Qt.ConnectionType.QueuedConnection)
        session.snapshot_ready.connect(self._on_collision_snapshot, Qt.ConnectionType.QueuedConnection)
        self._collision_timer.start()
        self._submit_collision_state(force=True)
        self._sync_collision_policy()

    def detach_collision_session(self) -> None:
        session = self._collision_session
        if session is None:
            return
        # 断开前先发 leave：协调者即时移除成员，不必等 stale 超时
        submit_leave = getattr(session, 'submit_leave', None)
        if callable(submit_leave):
            submit_leave()
        try:
            session.impulse_ready.disconnect(self._on_collision_impulse)
        except (RuntimeError, TypeError):
            pass
        try:
            session.snapshot_ready.disconnect(self._on_collision_snapshot)
        except (RuntimeError, TypeError):
            pass
        self._collision_timer.stop()
        self._collision_session = None
        self._collision_epoch = ''
        self._collision_peer_snapshots.clear()
        self._predicted_bounces.clear()
        self._pending_predicted_bounce = None
        self._pending_predicted_contact = None
        self._sync_collision_policy()

    def _sync_collision_policy(self) -> None:
        """把当前配置的碰撞参数同步到会话 policy，运行中改动即时生效。

        协调者配置优先：本进程是协调者时碰撞求解直接用本配置；
        非协调者时本地 policy 仅在本进程未来接管协调者时才生效。
        """
        session = getattr(self, '_collision_session', None)
        policy = {
            'collision_enabled': bool(self.cfg.get('collision_enabled', True)),
            'collision_restitution': float(self.cfg.get('collision_restitution', .82)),
            'collision_friction': float(self.cfg.get('collision_friction', .08)),
            'collision_mass_scale': float(self.cfg.get('collision_mass_scale', 1.0)),
            'collision_impulse_cap': float(self.cfg.get('collision_impulse_cap', 9000.0)),
        }
        if session is None:
            self._applied_collision_policy = None
            return
        if policy == self._applied_collision_policy:
            return
        self._applied_collision_policy = policy
        update_policy = getattr(session, 'update_policy', None)
        if callable(update_policy):
            update_policy(policy)

    def _collision_flags(self) -> int:
        flags = collision.FLAG_VISIBLE if self.isVisible() else 0
        if not self.isVisible() or self._hidden_paused:
            flags |= collision.FLAG_PAUSED
        if self._interaction_state == THROWN or self._physics_mode == 'throw':
            flags |= collision.FLAG_THROWN
        if self._interaction_state == DRAGGING:
            flags |= collision.FLAG_DRAGGING
        if self._interaction_state == SLINGSHOT_AIMING:
            flags |= collision.FLAG_SLINGSHOT_AIMING
        if self.lock_position:
            flags |= collision.FLAG_LOCK_POSITION
        if self.no_move:
            flags |= collision.FLAG_NO_MOVE
        if self.mouse_through:
            flags |= collision.FLAG_MOUSE_THROUGH
        if self._auto_cursor_hidden:
            flags |= collision.FLAG_AUTO_CURSOR_HIDDEN
        if bool(self.cfg.get('collision_enabled', True)):
            flags |= collision.FLAG_COLLISION_ENABLED
        if self._pending_predicted_bounce is not None:
            flags |= collision.FLAG_PREDICTED_BOUNCE
        return flags

    def _collision_velocity(self) -> tuple[float, float]:
        if self._interaction_state == DRAGGING and len(self._trail) >= 2:
            latest_t = self._trail[-1][0]
            samples = [sample for sample in self._trail if latest_t - sample[0] <= 0.1]
            if len(samples) >= 2:
                t0, x0, y0 = samples[0]
                t1, x1, y1 = samples[-1]
                dt = max(0.001, t1 - t0)
                return (x1 - x0) / dt, (y1 - y0) / dt
        return float(self._phys_vel[0]), float(self._phys_vel[1])

    def _collision_state(self) -> dict[str, Any]:
        rect = self.collision_content_rect()
        vx, vy = self._collision_velocity()
        circles = collision.circles_from_rect(rect.x(), rect.y(), rect.width(), rect.height())
        state = {
            'seq': self._collision_seq,
            'ts': time.monotonic(),
            'x': float(rect.center().x()), 'y': float(rect.center().y()),
            'w': float(self._w), 'h': float(self._h),
            'radius_x': max(1.0, rect.width() / 2.0),
            'radius_y': max(1.0, rect.height() / 2.0),
            'circles': circles,
            'vx': 0.0 if not self.isVisible() else vx,
            'vy': 0.0 if not self.isVisible() else vy,
            'flags': self._collision_flags(),
            'character': str(self.cfg.get('character', '')),
            'scale': float(self.scale),
        }
        if self._pending_predicted_bounce is not None:
            state['bounce_vx'], state['bounce_vy'] = self._pending_predicted_bounce
            if self._pending_predicted_contact is not None:
                state['bounce_x'], state['bounce_y'], state['bounce_circles'] = self._pending_predicted_contact
        return state

    def _submit_collision_state(self, force: bool = False) -> None:
        session = getattr(self, '_collision_session', None)
        if session is None:
            return
        state = self._collision_state()
        comparable = dict(state)
        comparable.pop('seq', None)
        # 时间戳不参与"状态是否变化"比较：ts 每次不同会让去重恒失效（死代码）
        comparable.pop('ts', None)
        if not force and comparable == self._collision_last_state:
            return
        now = time.monotonic()
        if not force and now - self._collision_last_submit_at < 0.05:
            # 非 force 提交 20Hz 限流：moveEvent 等 60Hz 高频路径不超标，
            # 运动期间由 _collision_timer（50ms/500ms）兜底强制上报
            return
        self._collision_seq += 1
        state['seq'] = self._collision_seq
        self._collision_last_state = comparable
        self._collision_last_submit_at = now
        session.submit_state(state)
        if self._pending_predicted_bounce is not None:
            self._pending_predicted_bounce = None
            self._pending_predicted_contact = None
        if collision_debug.ENABLED:
            collision_debug.log(
                getattr(session, 'runtime_id', ''), 'state_submit',
                x=state['x'], y=state['y'], vx=state['vx'], vy=state['vy'],
                seq=state['seq'], force=force,
            )
        moving = (self._interaction_state in (DRAGGING, THROWN)
                   or math.hypot(*self._phys_vel) > 20.0)
        self._collision_timer.setInterval(50 if moving else 500)

    @Slot(object)
    def _on_collision_snapshot(self, message: dict[str, Any]) -> None:
        epoch = str(message.get('epoch') or '')
        if not epoch or (self._collision_epoch and epoch != self._collision_epoch):
            return
        if epoch != self._collision_epoch:
            self._pending_predicted_bounce = None
            self._pending_predicted_contact = None
        self._collision_epoch = epoch
        runtime_id = str(getattr(getattr(self, '_collision_session', None), 'runtime_id', ''))
        now = time.monotonic()
        peers = {}
        for raw_member in message.get('members') or ():
            member = dict(raw_member)
            peer_id = str(member.get('runtime_id') or '')
            if peer_id and peer_id != runtime_id:
                member['_received_at'] = now
                peers[peer_id] = member
        self._collision_peer_snapshots = peers

    def _prune_collision_prediction_state(self, now: float) -> None:
        self._collision_peer_snapshots = {
            runtime_id: member for runtime_id, member in self._collision_peer_snapshots.items()
            if now - float(member.get('_received_at', 0.0)) <= 1.5
        }
        self._predicted_bounces = {
            pair: predicted_at for pair, predicted_at in self._predicted_bounces.items()
            if now - predicted_at <= 0.5
        }

    @Slot(object)
    def _on_collision_impulse(self, message: dict[str, Any]) -> None:
        runtime_id = str(getattr(getattr(self, '_collision_session', None), 'runtime_id', ''))
        def discard(reason: str) -> None:
            if collision_debug.ENABLED:
                collision_debug.log(runtime_id, 'impulse_discard', reason=reason,
                                    pair=message.get('pair', ''))
        if self._collision_session is None or not self.isVisible() or self._hidden_paused:
            discard('session_missing_or_hidden')
            return
        epoch = str(message.get('epoch') or '')
        pair_for_watermark = str(message.get('pair') or '')
        tick = message.get('tick')
        if epoch and pair_for_watermark and tick is not None:
            if not self._collision_impulse_watermarks.should_apply(epoch, pair_for_watermark, int(tick)):
                discard('watermark')
                return
        if self._interaction_state == DRAGGING or self._physics_mode == 'drag':
            discard('dragging')
            return
        if message.get('a') == runtime_id:
            dvx, dvy = float(message.get('dvx_a', 0)), float(message.get('dvy_a', 0))
            dx, dy = float(message.get('dx_a', 0)), float(message.get('dy_a', 0))
        elif message.get('b') == runtime_id:
            dvx, dvy = float(message.get('dvx_b', 0)), float(message.get('dvy_b', 0))
            dx, dy = float(message.get('dx_b', 0)), float(message.get('dy_b', 0))
        else:
            discard('runtime_id_mismatch')
            return
        pair = str(message.get('pair') or '|'.join(sorted((str(message.get('a') or ''),
                                                          str(message.get('b') or '')))))
        now = time.monotonic()
        predicted_at = self._predicted_bounces.pop(pair, None)
        if predicted_at is not None and now - predicted_at <= 0.5:
            discard('predicted_bounce_confirmed')
            return
        rect = self.collision_content_rect()
        radius_x = max(1.0, rect.width() / 2.0)
        radius_y = max(1.0, rect.height() / 2.0)
        hit_dv = math.hypot(dvx, dvy)
        is_real_hit = hit_dv >= COLLISION_HIT_MIN_DV
        has_velocity_impulse = abs(dvx) > 1e-9 or abs(dvy) > 1e-9
        # 偏差豁免的本意是"协调者眼中的我已经过期就别瞬移我"——直接比较
        # 协调者 tick 时认定的我方中心（ax/ay 或 bx/by）与当前实际中心，
        # 不从 contact/normal 反推（三种检测路径的 contact 语义不同，反推
        # 会系统性误判，导致所有位置分离被丢弃）
        if message.get('a') == runtime_id:
            expected_x = float(message.get('ax', rect.center().x()))
            expected_y = float(message.get('ay', rect.center().y()))
        else:
            expected_x = float(message.get('bx', rect.center().x()))
            expected_y = float(message.get('by', rect.center().y()))
        threshold = min(radius_x, radius_y) * 0.1 + math.hypot(*self._phys_vel) * 0.2
        contact_deviation = math.hypot(rect.center().x() - expected_x, rect.center().y() - expected_y) > threshold
        if contact_deviation:
            dx = dy = 0.0
            dvx = dvy = 0.0
            if collision_debug.ENABLED:
                collision_debug.log(runtime_id, 'impulse_position_discard',
                                    reason='contact_deviation', pair=message.get('pair', ''))
        if is_real_hit or (self._interaction_state == THROWN
                        and hit_dv >= COLLISION_CONTACT_DV_FLOOR):
            self._phys_vel[0] += dvx
            self._phys_vel[1] += dvy
        speed = math.hypot(*self._phys_vel)
        if speed > self._throw_speed_cap:
            clamped = physics_mod.soft_clamp_speed(speed, self._throw_speed_cap)
            self._phys_vel[:] = [self._phys_vel[0] * clamped / speed, self._phys_vel[1] * clamped / speed]
        if abs(dx) > 1e-9 or abs(dy) > 1e-9:
            self._cancel_move()
            self._cancel_animation_gap()
            clamped_x, clamped_y = self._collision_clamp_pos(self.x() + dx, self.y() + dy)
            left, top = self._collision_clamp_pos(float('-inf'), float('-inf'))
            right, bottom = self._collision_clamp_pos(float('inf'), float('inf'))
            self.move(
                min(max(int(round(clamped_x)), math.ceil(left)), math.floor(right)),
                min(max(int(round(clamped_y)), math.ceil(top)), math.floor(bottom)),
            )
            self._phys_pos[:] = [float(self.x()), float(self.y())]
        if has_velocity_impulse:
            self._just_dragged = True
            QTimer.singleShot(120, self, self._clear_just_dragged)
            # 只有"有分量的撞击"才响：dv 太小（静置非弹性接触的微小抵消）
            # 不播，否则贴贴时每秒 4 声机枪响
            if is_real_hit:
                self._play_collision_sound()
        if is_real_hit and not contact_deviation:
            self._interaction_state = THROWN
            self._enter_physics_mode('throw')
            self._phys_pos[:] = [float(self.x()), float(self.y())]
            self._last_physics_tick_time = None
            self._physics_timer.start()
        now = time.monotonic()
        if (is_real_hit and not self._squash_active
                and now - self._last_collision_squash_at >= 0.25):
            self._last_collision_squash_at = now
            self._start_squash()
        self._submit_collision_state(force=True)
        if collision_debug.ENABLED:
            collision_debug.log(runtime_id, 'impulse_apply', pair=message.get('pair', ''),
                                dv=(dvx, dvy), displacement=(dx, dy), speed=speed)

    _FS_SKIP_CLASSES = {
        'Progman', 'WorkerW', 'Shell_TrayWnd', 'Shell_SecondaryTrayWnd',
        'Windows.UI.Core.CoreWindow',  # 开始菜单/通知中心全屏层
    }

    @staticmethod
    def _fullscreen_geometry_hit(l: float, t: float, r: float, b: float,
                                 geom, has_caption: bool, topmost: bool = False) -> bool:
        """覆盖整屏几何，且（无标题栏 或 置顶）= 真全屏。

        判据组合的原因：
        - 带标题栏的普通/最大化窗口（含 Windows 自动隐藏任务栏场景）不置顶 → 排除；
        - 真全屏游戏/视频：多数去掉标题栏（无标题栏直接命中）；Unity/UE 系游戏
          （如绝区零）全屏时保留 WS_CAPTION 样式位但几乎必带 WS_EX_TOPMOST，用
          置顶位兜住；
        - 已最大化后按 F11 的窗口（IsZoomed 仍为真、标题栏被清掉）也正常命中。

        geom 兼容 QRect（方法访问）与 win32 RECT（属性访问）。
        """
        if has_caption and not topmost:
            return False
        gl = geom.left() if callable(getattr(geom, "left", None)) else geom.left
        gt = geom.top() if callable(getattr(geom, "top", None)) else geom.top
        gr = geom.right() if callable(getattr(geom, "right", None)) else geom.right
        gb = geom.bottom() if callable(getattr(geom, "bottom", None)) else geom.bottom
        return l <= gl and t <= gt and r >= gr and b >= gb

    # ------------------------------------------------------------------
    # 全屏 watcher：后台线程轮询（纯 win32，线程安全）+ 信号回主线程
    # ------------------------------------------------------------------
    def _fg_fullscreen_win32(self) -> bool:
        """前台窗口是否真全屏。仅返回布尔值，诊断细节见 _fg_fullscreen_probe。"""
        try:
            return self._fg_fullscreen_probe()[0]
        except Exception:
            return False

    @staticmethod
    def _fs_user_busy_state() -> tuple[bool, int]:
        """SHQueryUserNotificationState：Windows 自报的全屏/演示忙状态。

        与几何判定互补——几何判定在 DPI 虚拟化、跨屏、DWM 边界差异下可能漏判，
        而这个 API 是 Windows 自己（Focus Assist/通知静默）判定"用户正在
        全屏"的依据，游戏和全屏视频都会触发。返回 (是否全屏忙, 原始状态值)。
        """
        if os.name != 'nt':
            return False, -1
        try:
            state = ctypes.c_int(0)
            hr = ctypes.windll.shell32.SHQueryUserNotificationState(ctypes.byref(state))
            if hr != 0:  # S_OK
                return False, -1
            # 2=QUNS_BUSY(全屏应用运行中) 3=QUNS_RUNNING_D3D_FULL_SCREEN 4=QUNS_PRESENTATION_MODE
            return state.value in (2, 3, 4), state.value
        except Exception:
            return False, -1

    def _fg_fullscreen_probe(self) -> tuple[bool, str]:
        """前台窗口全屏探测，返回 (是否全屏, 诊断描述)。

        可在任意线程调用——不触碰 Qt 对象。判定链：
        1. foreground_window_info()（vision.py）：排除不可见/最小化/cloaked
           窗口，取 DWM 框架边界（物理像素，与本进程 DPI awareness 一致）；
        2. 排除本进程与 shell 窗口；
        3. 几何判定：窗口覆盖所在显示器完整几何（含任务栏），且无标题栏或置顶；
        4. 兜底判定：Windows SHQueryUserNotificationState 报告全屏忙状态。
        """
        if os.name != 'nt':
            return False, "非 Windows"
        u32 = ctypes.windll.user32
        # 句柄是 64 位指针：显式声明签名，避免 ctypes 默认 int32 截断
        u32.MonitorFromWindow.restype = wintypes.HANDLE
        u32.MonitorFromWindow.argtypes = [wintypes.HWND, wintypes.DWORD]
        u32.GetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int]
        u32.GetClassNameW.argtypes = [wintypes.HWND, ctypes.c_wchar_p, ctypes.c_int]
        info = vision_mod.foreground_window_info()
        if not info:
            return False, "无可判定前台窗口(不可见/最小化/cloaked)"
        hwnd = info['hwnd']
        # 排除本进程与其他变体/多开的桌宠进程（置顶小窗，几何不会误判，
        # 但 SHQueryUserNotificationState 兜底需要进程名兜底排除）
        proc = info.get('process', '')
        if info.get('pid') == os.getpid() or proc.lower().startswith('dsh-pet-'):
            return False, f"前台是桌宠自身 {proc}"
        # 排除桌面/任务栏等 shell 窗口
        buf = ctypes.create_unicode_buffer(256)
        u32.GetClassNameW(hwnd, buf, 256)
        cls = buf.value
        if cls in self._FS_SKIP_CLASSES:
            return False, f"shell 窗口 {cls}"

        style = u32.GetWindowLongW(hwnd, GWL_STYLE)
        has_caption = bool(style & _WS_CAPTION)
        exstyle = u32.GetWindowLongW(hwnd, GWL_EXSTYLE)
        topmost = bool(exstyle & _WS_EX_TOPMOST)
        x, y, w, h = info['rect']
        # 窗口所在显示器的完整几何（与 GetWindowRect/DWM 边界同为
        # 本进程 DPI awareness 下的坐标，天然一致）
        mon = u32.MonitorFromWindow(hwnd, 2)  # MONITOR_DEFAULTTONEAREST
        mi = _WinMonitorInfo()
        mi.cbSize = ctypes.sizeof(_WinMonitorInfo)
        if not u32.GetMonitorInfoW(mon, ctypes.byref(mi)):
            return False, f"GetMonitorInfoW 失败 cls={cls}"
        if self._fullscreen_geometry_hit(
                x, y, x + w, y + h, mi.rcMonitor, has_caption, topmost):
            return True, f"几何覆盖 cls={cls} proc={info.get('process', '')}"
        busy, bstate = self._fs_user_busy_state()
        if busy:
            return True, (f"SHQueryUserNotificationState={bstate} "
                          f"cls={cls} proc={info.get('process', '')}")
        detail = (f"未命中 cls={cls} proc={info.get('process', '')} "
                  f"caption={has_caption} topmost={topmost} "
                  f"rect=({x},{y},{x + w},{y + h}) "
                  f"monitor=({mi.rcMonitor.left},{mi.rcMonitor.top},"
                  f"{mi.rcMonitor.right},{mi.rcMonitor.bottom}) busy={bstate}")
        return False, detail

    def _start_fs_watch(self) -> None:
        """启动全屏监视线程（幂等）。"""
        if self._fs_thread is not None and self._fs_thread.is_alive():
            return
        self._fs_stop.clear()
        self._fs_thread = threading.Thread(
            target=self._fs_watch_loop, daemon=True, name="pet-fs-watch")
        self._fs_thread.start()
        logging.info("全屏监视线程已启动")

    def _stop_fs_watch(self) -> None:
        """停止全屏监视线程（不 join，线程 1s 内自行退出，绝不卡 UI）。"""
        self._fs_stop.set()

    def _fs_watch_loop(self) -> None:
        """后台轮询光标与前台窗口，分别使用 20Hz 与 1Hz 节拍。"""
        polls = 0
        consecutive_errors = 0
        next_fullscreen = time.monotonic() + 1.0
        while not self._fs_stop.wait(0.05):
            if shiboken6.isValid(self) is False:
                return
            if self._cursor_hidden_passthrough_enabled():
                try:
                    visibility = vision_mod.get_cursor_visibility()
                    if shiboken6.isValid(self) is False:
                        return
                    self.cursor_visibility_changed.emit(visibility)
                    consecutive_errors = 0
                except (RuntimeError, AttributeError) as exc:
                    if shiboken6.isValid(self) is False:
                        return
                    consecutive_errors += 1
                    backoff = 1.0 if consecutive_errors == 1 else (2.0 if consecutive_errors == 2 else 5.0)
                    logging.debug("光标状态检测瞬时异常 (%s), 退避 %ss 后重试", exc, backoff)
                    if self._fs_stop.wait(backoff):
                        return
                except Exception:
                    try:
                        if shiboken6.isValid(self) is False:
                            return
                        self.cursor_visibility_changed.emit('UNKNOWN')
                    except (RuntimeError, AttributeError) as exc:
                        if shiboken6.isValid(self) is False:
                            return
                        consecutive_errors += 1
                        backoff = 1.0 if consecutive_errors == 1 else (2.0 if consecutive_errors == 2 else 5.0)
                        logging.debug("光标状态降级发射瞬时异常 (%s), 退避 %ss 后重试", exc, backoff)
                        if self._fs_stop.wait(backoff):
                            return
            now = time.monotonic()
            if not self.auto_hide_fullscreen or now < next_fullscreen:
                continue
            next_fullscreen = now + 1.0
            try:
                hit, detail = self._fg_fullscreen_probe()
            except Exception:
                logging.exception("全屏检测异常")
                continue
            polls += 1
            if hit != self._fs_last:
                self._fs_last = hit
                logging.info("全屏检测变化 hit=%s (%s)", hit, detail)
                if shiboken6.isValid(self) is False:
                    return
                self.fullscreen_changed.emit(hit)
            elif polls % 15 == 0:
                logging.info("全屏检测心跳 hit=%s %s", hit, detail)

    def _cursor_hidden_passthrough_enabled(self) -> bool:
        return self._cursor_hidden_passthrough

    def _watch_required(self) -> bool:
        return os.name == 'nt' and (self.auto_hide_fullscreen or self._cursor_hidden_passthrough_enabled())

    def _cursor_transition_blocked(self) -> bool:
        return (self._press_global is not None or self._dragging or
                self._interaction_state in ('DRAGGING', 'SLINGSHOT_AIMING', 'PRESS_CANDIDATE'))

    def _on_cursor_visibility_changed(self, visibility: str) -> None:
        if not self._cursor_hidden_passthrough_enabled():
            return
        now = time.monotonic()
        self._cursor_visibility = visibility
        if visibility == 'HIDDEN':
            if self._cursor_hidden_since is None:
                self._cursor_hidden_since = now
            if now - self._cursor_hidden_since >= 0.2 and not self._cursor_transition_blocked():
                self._auto_cursor_hidden = True
                self._apply_effective_mouse_through()
        elif visibility == 'SHOWING':
            self._cursor_hidden_since = None
            if self._cursor_transition_blocked():
                self._cursor_restore_pending = True
            else:
                self._cursor_restore_pending = False
                self._auto_cursor_hidden = False
                self._apply_effective_mouse_through()
        elif visibility == 'SUPPRESSED':
            self._cursor_hidden_since = None
            logging.debug('系统光标被触摸/笔输入抑制，保持当前自动穿透状态')

    def _on_fullscreen_changed(self, hit: bool) -> None:
        """主线程：全屏出现 → 隐藏桌宠；全屏退出 → 恢复。"""
        logging.info("全屏状态变化 hit=%s auto_hidden=%s visible=%s", hit, self._auto_hidden, self.isVisible())
        if hit:
            if not self._auto_hidden and self.isVisible():
                self._auto_hidden = True
                self._speech_bubble.hide()
                self.hide(notify=False)  # 自动隐藏是内部语义，不弹"桌宠已隐藏"托盘通知
        elif self._auto_hidden:
            self._auto_hidden = False
            self.show()

    def set_auto_hide_fullscreen(self, on: bool) -> None:
        """全屏自动隐藏开关（供设置/菜单调用）。"""
        self.auto_hide_fullscreen = bool(on)
        self.cfg.set('auto_hide_fullscreen', self.auto_hide_fullscreen)
        self.cfg.save()
        if self._watch_required():
            self._start_fs_watch()
        else:
            self._stop_fs_watch()
        if not self.auto_hide_fullscreen and self._auto_hidden:
            self._auto_hidden = False
            self.show()

    def set_cursor_hidden_passthrough(self, on: bool) -> None:
        """切换光标自动穿透，不改变用户手动穿透意图。"""
        on = bool(on)
        self._cursor_hidden_passthrough = on
        self.cfg.set('cursor_hidden_passthrough', on)
        self.cfg.save()
        self._cursor_hidden_since = None
        self._cursor_restore_pending = False
        if not on:
            self._auto_cursor_hidden = False
            self._apply_effective_mouse_through()
        if self._watch_required():
            self._start_fs_watch()
        elif not self._auto_hidden:
            self._stop_fs_watch()

    def set_stream_capture_mode(self, on: bool) -> None:
        """直播捕获兼容模式：Tool → 普通顶层窗口 + 标题。

        直播姬/OBS 的窗口捕获会过滤 Tool 窗口（WS_EX_TOOLWINDOW），
        开启后改为普通窗口并设置可见标题，捕获列表即可看到桌宠；
        代价是任务栏出现图标。setWindowFlags 会重建原生窗口，随后
        showEvent 会自动重新应用置顶。
        """
        on = bool(on)
        if on == self._stream_capture_mode:
            return
        self._stream_capture_mode = on
        self.cfg.set('stream_capture_mode', on)
        self.cfg.save()
        was_visible = self.isVisible()  # setWindowFlags 重建原生窗口会先隐藏
        self.setWindowFlags(build_window_flags(self.cfg, self.mouse_through, on))
        self.setWindowTitle(STREAM_CAPTURE_TITLE if on else '')
        if was_visible:
            self.show()  # 只在原本可见时恢复：手动/自动隐藏的桌宠不被意外唤出

    def _arm_dock_reactivate_restore(self) -> None:
        """macOS：隐藏后点击 Dock 图标激活应用时自动恢复桌宠（一次性监听）。

        连接只建立一次，用 _dock_reactivate_armed 控制响应次数，
        避免对销毁中的窗口反复 connect/disconnect。
        """
        if sys.platform != 'darwin':
            return
        if getattr(self, "_dock_reactivate_armed", False):
            return
        app = QApplication.instance()
        if app is None:
            return
        self._dock_reactivate_armed = True
        app.applicationStateChanged.connect(self._restore_on_dock_reactivate)

    def _restore_on_dock_reactivate(self, state) -> None:
        if state != Qt.ApplicationState.ApplicationActive:
            return
        if not getattr(self, "_dock_reactivate_armed", False):
            return
        self._dock_reactivate_armed = False
        self.show()

    def _ensure_dock_icon_on_hide(self) -> None:
        """macOS：隐藏桌宠时临时开启 Dock 图标，供点击恢复。

        只改运行期策略、绝不写回配置：show_dock_icon 是用户偏好，
        一次隐藏不能把它覆盖掉，也不能经其他路径的 cfg.save() 落盘。
        恢复显示时由 _restore_dock_icon_preference 按偏好还原。
        """
        if sys.platform != 'darwin' or bool(self.cfg.get('show_dock_icon', True)):
            return
        if getattr(self, "_dock_icon_forced", False):
            return
        self._dock_icon_forced = True
        try:
            from .app import _mac_set_dock_icon_visible
            _mac_set_dock_icon_visible(True)
        except Exception:
            self._dock_icon_forced = False

    def _restore_dock_icon_preference(self) -> None:
        """macOS：桌宠恢复显示后按用户偏好还原 Dock 图标策略。"""
        if sys.platform != 'darwin' or not getattr(self, "_dock_icon_forced", False):
            return
        self._dock_icon_forced = False
        try:
            from .app import _mac_set_dock_icon_visible
            _mac_set_dock_icon_visible(bool(self.cfg.get('show_dock_icon', True)))
        except Exception:
            pass

    def set_no_move(self, on: bool) -> None:
        """切换「不移动」：禁用自动移动；勾选瞬间若正在移动则立即停下回待机。"""
        self.no_move = bool(on)
        self.cfg.set('no_move', self.no_move)
        self.cfg.save()
        if self.no_move and self._move_plan is not None:
            if self.idles:
                self._switch(self._pick(self.idles))  # 打断进行中的移动
        self._submit_collision_state(force=True)

    # ================================================================ 播放
    def _connect_movie(self, name: str, movie) -> None:
        """按需连接 clip 信号（懒加载）：同一动画只连接一次。

        兜底说明：主线程被阻塞导致队列溢出、最后一帧被丢弃时，
        frameChanged 永远到不了末尾帧；finished 信号保证动画链一定继续。
        """
        if name in self._connected_movies:
            return
        movie.frameChanged.connect(lambda n, name=name: self._on_frame(name, n))
        movie.finished.connect(lambda name=name: self._on_clip_finished(name))
        self._connected_movies.add(name)

    def _switch(self, name: str) -> None:
        """切换到指定动画（链式模型：全部一次性播放）。"""
        self._cancel_move()
        self.anim = name
        self._collision_local_bounds = None
        movie = self.lib.movie(name)
        self._connect_movie(name, movie)
        self.movie = movie
        movie.stop()
        movie.jumpToFrame(0)
        if hasattr(movie, 'set_playback_speed'):
            movie.set_playback_speed(self.playback_speed)
        self._ended_fired = False
        self._rebuild_frame()
        movie.start()
        self._submit_collision_state(force=True)

    # ---- Agent 联动动作平滑衔接 ----
    def _is_one_shot_playing(self) -> bool:
        """当前是否正在播一次性动作（动作池/点击回应/移动）。待机/转向可立即切换。"""
        return self.anim in self.acts or self.anim in self.clicks or self.anim in self.moves

    def request_link_anim(self, name: str) -> None:
        """Agent 联动动作请求：一次性动作播放中不打断，存为待播（最新覆盖旧的）。"""
        self._pending_link_anim = name
        if not self._is_one_shot_playing():
            self._play_pending_link_anim()

    def _play_pending_link_anim(self) -> None:
        name = self._pending_link_anim
        self._pending_link_anim = None
        if not name:
            return
        self._link_anim_current = name
        self._switch(name)

    def request_link_idle(self) -> None:
        """Agent 回到空闲：取消待播联动；一次性动作让它播完自然回待机，否则立即回待机。"""
        self._pending_link_anim = None
        self._link_anim_current = None
        if self._is_one_shot_playing():
            return
        if self.idles:
            self._switch(self._pick(self.idles))

    def _on_frame(self, name: str, n: int) -> None:
        """媒体帧推进回调：重建画面；最后一帧触发播完处理。"""
        if name != self.anim or self.movie is None:
            return
        self._rebuild_frame()
        self.update()
        if n >= self.lib.frames(name) - 1 and not self._ended_fired:
            self._ended_fired = True
            self.movie.stop()  # 停在最后一帧，等 _on_anim_ended 切走
            self._on_anim_ended(name)

    def _rebuild_frame(self) -> None:
        """重建当前帧：缩放 + 朝向镜像 + 生成窗口 mask。"""
        if self.movie is None:
            return
        pm = self.movie.currentPixmap()
        if pm is None or pm.isNull():
            # ffmpeg 缺失/素材损坏时首帧解码可能失败返回 None，跳过本帧而不是崩溃
            return
        img = pm.toImage()
        # 含文字/方向性画面的动画登记在 lib.no_mirror，朝右时也不镜像（否则文字反显）
        if self.facing == 'right' and self.anim not in getattr(self.lib, 'no_mirror', frozenset()):
            img = img.mirrored(True, False)
        # 按屏幕 DPR 渲染到物理像素，避免高分屏下被 Qt 二次放大导致模糊。
        # 先转预乘 alpha 再缩放：直通 alpha 缩放会让透明像素的 RGB 渗入
        # 半透明边缘，产生暗边/彩边（毛边来源之一）。
        scr = self._screen_available()
        dpr = scr.devicePixelRatio() if scr is not None else 1.0
        w_c = max(1, int(round(catalog.CANVAS_W * self.scale * dpr)))
        h_c = max(1, int(round(catalog.CANVAS_H * self.scale * dpr)))
        img = img.convertToFormat(QImage.Format.Format_ARGB32_Premultiplied)
        img = img.scaled(w_c, h_c,
                         Qt.AspectRatioMode.IgnoreAspectRatio,
                         Qt.TransformationMode.SmoothTransformation)
        img = img.convertToFormat(QImage.Format.Format_ARGB32)
        pm = QPixmap.fromImage(img)
        pm.setDevicePixelRatio(dpr)
        self._frame_pixmap = pm
        self._hit_alpha_image = None  # 帧已变化，逐像素命中缓存失效
        self._sync_mask()

    def _frame_draw_rect(self) -> QRect:
        """当前帧在窗口内的绘制矩形（逻辑坐标）；paintEvent 与命中测试共用。"""
        if self._squash_active:
            x, y, w, h = _squash_geometry(
                self._w,
                self._h,
                int(round(catalog.CANVAS_W * self.scale)),
                int(round(catalog.CANVAS_H * self.scale)),
                self._squash_progress,
            )
            return QRect(x, y, w, h)
        return QRect(0, int(round(catalog.PAD * self.scale)),
                     int(round(catalog.CANVAS_W * self.scale)),
                     int(round(catalog.CANVAS_H * self.scale)))

    def _sync_mask(self) -> None:
        """更新角色可见轮廓与窗口 mask。

        - 非 Windows：继续用 QWidget.setMask 实现透明区域鼠标穿透。
        - Windows：不再 setMask（1-bit 裁剪会破坏半透明边缘），只更新
          _mask_bounds；鼠标穿透由 WindowsPerPixelInputController 负责。
        """
        canvas = QImage(self._w, self._h, QImage.Format.Format_ARGB32)
        canvas.fill(Qt.GlobalColor.transparent)
        p = QPainter(canvas)
        if self._frame_pixmap is not None:
            rect = self._frame_draw_rect()
            # 与 paintEvent 完全相同的绘制调用，保证 mask 与画面逐像素一致
            p.drawPixmap(rect, self._frame_pixmap)
        p.end()
        mask = QBitmap.fromImage(canvas.createAlphaMask())
        self._mask_bounds = QRegion(mask).boundingRect()
        if not self._mask_bounds.isEmpty():
            stable = getattr(self, '_collision_local_bounds', None)
            if stable is None:
                self._collision_local_bounds = QRect(self._mask_bounds)
            else:
                self._collision_local_bounds = stable.united(self._mask_bounds)
        if os.name != "nt":
            self.setMask(mask)
        elif not self.mask().isEmpty():
            self.clearMask()

    def collision_content_rect(self) -> QRect:
        """碰撞用的稳定可见区域（全局坐标）：取当前动画各帧包围盒的并集，
        避免圆链随帧跳动；尚无并集时回退当前帧区域。"""
        frame_rect = self.frameGeometry()
        local = self._collision_local_bounds
        if local is not None and not local.isEmpty():
            return QRect(frame_rect.topLeft() + local.topLeft(), local.size())
        return self.visible_content_rect()

    def character_local_region(self) -> QRect:
        """当前角色可见区域（窗口局部坐标）；供贴边/气泡定位等增量功能复用。"""
        if self._mask_bounds is not None and not self._mask_bounds.isEmpty():
            return QRect(self._mask_bounds)
        return QRect(0, 0, self._w, self._h)

    def _is_transparent_at(self, local: QPoint) -> bool:
        """判断窗口局部坐标处是否透明（供 Windows 命中测试使用）。"""
        if self._frame_pixmap is None or self._frame_pixmap.isNull():
            return False
        rect = self._frame_draw_rect()
        if not rect.contains(local):
            return True
        if self._hit_alpha_image is None:
            self._hit_alpha_image = self._frame_pixmap.toImage()
        img = self._hit_alpha_image
        if img.isNull():
            return False
        dpr = self._frame_pixmap.devicePixelRatio() or 1.0
        px = int(round((local.x() - rect.x()) * dpr))
        py = int(round((local.y() - rect.y()) * dpr))
        if px < 0 or py < 0 or px >= img.width() or py >= img.height():
            return True
        return img.pixelColor(px, py).alpha() < 16

    def paintEvent(self, event) -> None:  # noqa: N802 (Qt 命名)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        if self._frame_pixmap is not None:
            if getattr(self, "_interaction_state", "IDLE") == "SLINGSHOT_AIMING":
                base_rect = QRect(0, int(round(catalog.PAD * self.scale)),
                                  int(round(catalog.CANVAS_W * self.scale)),
                                  int(round(catalog.CANVAS_H * self.scale)))
                x, y, w, h = self._slingshot_geometry(
                    base_rect,
                    self._slingshot_pull,
                    self._slingshot_progress(),
                    QRect(0, 0, self._w, self._h),
                )
                painter.drawPixmap(x, y, w, h, self._frame_pixmap)
                visible = self.character_local_region()
                character_rect = QRect(
                    round(x + (visible.x() - base_rect.x()) * w / base_rect.width()),
                    round(y + (visible.y() - base_rect.y()) * h / base_rect.height()),
                    max(1, round(visible.width() * w / base_rect.width())),
                    max(1, round(visible.height() * h / base_rect.height())),
                )
                if self._slingshot_mouse is not None:
                    mouse_local = self._slingshot_mouse - self.pos()
                    band_start, band_end = self._slingshot_band_points(
                        character_rect, mouse_local, self._slingshot_pull,
                    )
                    painter.setPen(QPen(QColor(104, 174, 196, 105), max(1, round(self.scale))))
                    painter.drawLine(band_start, band_end)
                distance = math.hypot(self._slingshot_pull.x(), self._slingshot_pull.y())
                minimum = physics_mod.SLINGSHOT_MIN_DISTANCE * self.scale
                if distance >= minimum:
                    speed = physics_mod.slingshot_speed(
                        distance, minimum,
                        physics_mod.SLINGSHOT_MAX_DISTANCE * self.scale,
                        self._throw_speed_cap,
                    )
                    length = distance or 1.0
                    vx = self._slingshot_pull.x() / length * speed
                    vy = self._slingshot_pull.y() / length * speed
                    anchor = self._slingshot_trajectory_anchor(
                        character_rect, self._slingshot_pull,
                    )
                    trajectory = physics_mod.slingshot_trajectory(vx, vy)
                    painter.setPen(Qt.PenStyle.NoPen)
                    trajectory = self._slingshot_trajectory_preview(
                        trajectory, anchor, QRect(0, 0, self._w, self._h), self.scale,
                    )
                    for index, (tx, ty) in enumerate(trajectory):
                        fade = 1.0 - index / max(1, len(trajectory) - 1)
                        radius = (2.8 - 1.35 * (1.0 - fade)) * self.scale
                        painter.setBrush(QColor(104, 174, 196, int(150 * fade)))
                        painter.drawEllipse(QPointF(tx, ty), radius, radius)
            elif self._squash_active:
                # Q 弹：使用逻辑帧尺寸；QPixmap.width() 可能是 DPR 物理像素尺寸。
                if self._slingshot_rebound_progress > 0.0:
                    amount = self._slingshot_rebound_progress * (1.0 - self._squash_progress) ** 2
                    x, y, w, h = self._slingshot_geometry(
                        QRect(0, int(round(catalog.PAD * self.scale)),
                              int(round(catalog.CANVAS_W * self.scale)),
                              int(round(catalog.CANVAS_H * self.scale))),
                        QPoint(1, 0), amount, QRect(0, 0, self._w, self._h),
                    )
                else:
                    x, y, w, h = _squash_geometry(
                        self._w,
                        self._h,
                        int(round(catalog.CANVAS_W * self.scale)),
                        int(round(catalog.CANVAS_H * self.scale)),
                        self._squash_progress,
                    )
                painter.drawPixmap(x, y, w, h, self._frame_pixmap)
            else:
                # 落地对齐：整帧下移 PAD×scale，让人物脚底踩在窗口底线
                painter.translate(0, int(round(catalog.PAD * self.scale)))
                painter.drawPixmap(0, 0, self._frame_pixmap)
        painter.end()

    def _start_squash(self) -> None:
        """点击时启动 Q 弹效果：画面先变矮再恢复。"""
        self._squash_active = True
        self._slingshot_rebound_progress = 0.0
        self._squash_progress = 0.0
        self._squash_clock.start()
        self._squash_timer.start()
        self.update()

    def _on_squash_tick(self) -> None:
        elapsed = self._squash_clock.elapsed()
        self._squash_progress = min(1.0, elapsed / self._squash_duration_ms)
        if self._squash_progress >= 1.0:
            self._squash_active = False
            self._slingshot_rebound_progress = 0.0
            self._squash_timer.stop()
        self._sync_mask()  # mask 跟随 squash 几何，避免变形边缘被旧轮廓裁切
        self.update()

    def icon_pixmap(self, size: int = 64) -> QPixmap:
        """托盘/菜单图标：裁掉帧透明留白后再缩放。"""
        pm = self._frame_pixmap
        if pm is None and self.idle:
            pm = self.lib.movie(self.idle).currentPixmap()
        if pm is None or pm.isNull():
            return QPixmap()
        return PetWindow._crop_icon_pixmap(pm, size)

    @staticmethod
    def _crop_icon_pixmap(pm: QPixmap, size: int) -> QPixmap:
        image = pm.toImage()
        bounds = QRegion(QBitmap.fromImage(image.createAlphaMask())).boundingRect()
        if bounds.isValid() and not bounds.isEmpty():
            pm = QPixmap.fromImage(image.copy(bounds))
        return pm.scaled(size, size,
                         Qt.AspectRatioMode.KeepAspectRatio,
                         Qt.TransformationMode.SmoothTransformation)

    def animation_icon_pixmap(self, name: str, size: int = 64) -> QPixmap:
        """Synchronous compatibility path using a representative later frame."""
        image = PetWindow.animation_icon_image(self, name)
        if not image.isNull():
            return PetWindow._crop_icon_pixmap(QPixmap.fromImage(image), size)
        clip = self.lib.movie(name)
        target = representative_frame_index(clip.frameCount())
        if name != self.anim:
            clip.jumpToFrame(target)
        pm = clip.currentPixmap()
        if pm is None or pm.isNull():
            return self.icon_pixmap(size)
        return PetWindow._crop_icon_pixmap(pm, size)

    def animation_icon_image(self, name: str) -> QImage:
        """Decode a representative frame as QImage; safe to call in a worker."""
        lock = getattr(self, "_animation_icon_cache_lock", None)
        if lock is None:
            lock = threading.Lock()
            self._animation_icon_cache_lock = lock
            self._animation_icon_image_cache = {}
            self._animation_icon_inflight = {}
        with lock:
            cached = self._animation_icon_image_cache.get(name)
            if cached is not None:
                return QImage(cached)
            pending = self._animation_icon_inflight.get(name)
            owner = pending is None
            if owner:
                pending = threading.Event()
                self._animation_icon_inflight[name] = pending
        if not owner:
            pending.wait()
            with lock:
                return QImage(self._animation_icon_image_cache.get(name, QImage()))
        path = self.lib.clip_path(name)  # 不在 worker 线程构造 WebMClip（Qt 线程亲和）
        try:
            image = decode_representative_frame(path) if path is not None else QImage()
            with lock:
                if not image.isNull():
                    cache = self._animation_icon_image_cache
                    # 简单上限：动画名数量有限，超限全清后按需重新解码
                    if len(cache) >= 128:
                        cache.clear()
                    cache[name] = QImage(image)
            return image
        finally:
            with lock:
                event = self._animation_icon_inflight.pop(name, None)
                if event is not None:
                    event.set()

    def animation_icon_cached_image(self, name: str) -> QImage:
        """Return a decoded thumbnail without starting any work."""
        lock = getattr(self, "_animation_icon_cache_lock", None)
        if lock is None:
            return QImage()
        with lock:
            return QImage(self._animation_icon_image_cache.get(name, QImage()))

    def _on_clip_finished(self, name: str) -> None:
        """WebMClip 播完兜底：正常路径在末尾帧处由 _on_frame 提前 stop，
        这里只处理“末尾帧被丢弃、结束标记被消费”的异常路径，推进动画链。"""
        if name != self.anim or self.movie is None:
            return
        if not self._ended_fired:
            self._ended_fired = True
            self._on_anim_ended(name)

    # ================================================================ 动画链
    def _on_anim_ended(self, name: str) -> None:
        if name == SING_ANIM:
            # 音乐自动唱歌开启且当前仍处于“唱歌中”时，直接无缝续播；
            # 不再每次播完都查一次音频 COM，降低长时间运行的崩溃风险。
            # 音乐停止由 _check_music_sing 定时检测后清掉 _music_sing_active。
            if self._music_sing_enabled and self._music_sing_active:
                self._switch(SING_ANIM)
                return
            self._music_sing_active = False
        if name == self.drag and self._dragging:
            self.movie.jumpToFrame(0)
            self._ended_fired = False
            self.movie.start()
            return
        # Agent 联动：待播动作优先接上（平滑衔接，不打断刚播完的动作）
        if self._pending_link_anim:
            self._play_pending_link_anim()
            return
        # 联动动作播完仍有 Agent 在忙 → 接下一个联动动作；否则走正常动画链
        if self._link_anim_current is not None and name == self._link_anim_current:
            self._link_anim_current = None
            provider = self._link_next_provider
            nxt = provider() if callable(provider) else None
            if nxt:
                self._link_anim_current = nxt
                self._switch(nxt)
                return
        if name in self.turns:
            self.facing = 'right' if self.facing == 'left' else 'left'
        if name == self.drag or name in self.clicks:
            self._cancel_animation_gap()
            if self.idles:
                self._switch(self._pick(self.idles))
            return
        if self._animation_gap_active:
            if name in self.idles or name in self.turns:
                self._play_animation_gap_step()
            else:
                # 异常状态（gap 期间播了非待机/转向动画）：兜底推进动画链，
                # 避免 return 后动画链停摆
                self._pick_next()
            return
        if self.animation_gap_seconds > 0 and (name in self.acts or name in self.moves):
            self._start_animation_gap()
            return
        self._pick_next()

    def _cancel_animation_gap(self) -> None:
        self._animation_gap_timer.stop()
        self._animation_gap_active = False

    def _start_animation_gap(self) -> None:
        if self.animation_gap_seconds <= 0 or not (self.idles or self.turns):
            self._pick_next()
            return
        self._animation_gap_active = True
        self._animation_gap_timer.start(max(1, int(round(self.animation_gap_seconds * 1000))))
        self._play_animation_gap_step()

    def _play_animation_gap_step(self) -> None:
        pool = self.idles + self.turns
        if pool:
            self._switch(self._pick(pool, exclude=self.anim))

    def _on_animation_gap_timeout(self) -> None:
        self._animation_gap_active = False

    def _pick_next(self) -> None:
        """动画链：30% 待机 / 10% 转向 / 40% 动作 / 20% 移动（空间不够回退动作）。

        「不移动」模式下跳过移动分支，其概率并入动作 → 30% 待机 / 10% 转向 / 60% 动作。
        """
        if not self.acts:
            # 角色包没有随机动作素材（仅核心动画）：需要 acts 的分支与回退
            # 统一改走待机；待机也没有则保持当前动画，绝不 random.choice([]) 崩溃。
            if self.idles:
                self._switch(self._pick(self.idles, exclude=self.anim))
            return
        roll = random.random()
        if roll < catalog.P_IDLE:
            if self.idles:
                self._switch(self._pick(self.idles, exclude=self.anim))
            else:
                self._switch(self._pick(self.acts, exclude=self.anim))
        elif roll < catalog.P_TURN:
            if self.turns:
                self._switch(self._pick(self.turns, exclude=self.anim))
            else:
                self._switch(self._pick(self.acts, exclude=self.anim))
        elif roll < catalog.P_ACTS:
            self._switch(self._pick(self.acts, exclude=self.anim))
        else:
            if self.no_move or not self._try_move():
                self._switch(self._pick(self.acts, exclude=self.anim))

    @staticmethod
    def _pick(pool: list[str], exclude: str | None = None) -> str:
        entries = [n for n in pool if n != exclude] or pool
        return random.choice(entries)

    # ================================================================ 移动
    def _try_move(self, name: str | None = None) -> bool:
        """计划一次朝 facing 方向的移动；屏幕空间不够返回 False。

        name 给定时使用指定动画（手动触发），否则随机选一个移动姿态。
        """
        if (self._physics_mode is not None
                or self._interaction_state in (THROWN, DRAGGING)):
            return False
        if self._move_plan is not None:
            return True  # 已在移动/已计划
        scr = self._screen_available()
        if scr is None:
            return False
        avail = scr.availableGeometry()
        dir_sign = 1 if self.facing == 'right' else -1
        cx = self.x() + self._w / 2
        distance = random.randint(catalog.MOVE_MIN_PX, catalog.MOVE_MAX_PX)
        target_cx = cx + dir_sign * distance
        half_w = self._w / 2
        left_bound = avail.left() + catalog.MOVE_MARGIN + half_w
        right_bound = avail.right() - catalog.MOVE_MARGIN - half_w
        if target_cx < left_bound or target_cx > right_bound:
            return False
        if not self.moves:
            return False
        move_name = name or self._pick(self.moves)
        duration = self.lib.duration(move_name)
        self._switch(move_name)
        self._move_plan = {
            'start_x': self.x(),
            'target_x': int(round(target_cx - half_w)),
            'start_y': self.y(),
            'target_y': wander_target_y(
                self.y(), avail.top(), avail.bottom(), self._h, catalog.MOVE_MARGIN
            ),
            'duration': duration,
        }
        self._move_timer.start()
        return True

    def _trigger_move(self, name: str) -> None:
        """手动触发移动（右键菜单）：先打断当前移动，再朝 facing 方向走动；
        屏幕空间不足则原地播放走路姿态（不位移）。"""
        self._cancel_move()
        self._cancel_animation_gap()
        if not self._try_move(name):
            self._switch(name)  # 贴边放不下：原地播放走路姿态，不位移

    def _on_move_tick(self) -> None:
        """位置驱动：跟随动画播放进度插值（前后各 2s 不动，中间走完全程）。"""
        if self._physics_mode is not None:
            self._move_timer.stop()
            self._move_plan = None
            return
        plan = self._move_plan
        if not plan or self.movie is None:
            self._move_timer.stop()
            return
        t = self.movie.currentTimeSeconds()
        lead, tail = catalog.MOVE_LEAD_SEC, catalog.MOVE_TAIL_SEC
        dur = plan['duration']
        if t <= lead:
            x = plan['start_x']
            y = plan['start_y']
        elif t >= dur - tail:
            x = plan['target_x']
            y = plan['target_y']
        else:
            progress = (t - lead) / max(0.1, dur - lead - tail)
            x = plan['start_x'] + (plan['target_x'] - plan['start_x']) * progress
            y = plan['start_y'] + (plan['target_y'] - plan['start_y']) * progress
        self.move(int(round(x)), int(round(y)))
        if t >= dur - tail:
            # 到位：提交终点，动画自然播完后续链。
            # 不把自动移动的终点写入记忆位置，否则重启后桌宠会停在
            # 上次随机游走的位置，而不是用户手动放置的位置。
            self._move_timer.stop()
            self._move_plan = None

    def _cancel_move(self) -> None:
        self._move_timer.stop()
        self._move_plan = None

    def _collision_clamp_pos(self, x: float, y: float) -> tuple[float, float]:
        """把碰撞分离位置限制在抛掷物理使用的屏幕边界内。"""
        avail = self._screen_available().availableGeometry()
        margin = self._w / 3.0
        left = avail.left() - margin
        top = avail.top()
        right = avail.right() - self._w + margin
        bottom = avail.bottom() - self._h
        return min(max(x, left), right), min(max(y, top), bottom)

    # ================================================================ 交互
    def _slingshot_progress(self) -> float:
        distance = min(math.hypot(self._slingshot_pull.x(), self._slingshot_pull.y()),
                       physics_mod.SLINGSHOT_MAX_DISTANCE * self.scale)
        return max(0.0, min(1.0, distance / max(1.0, physics_mod.SLINGSHOT_MAX_DISTANCE * self.scale)))

    @staticmethod
    def _slingshot_geometry(base_rect: QRect, pull: QPoint, progress: float,
                            bounds: QRect | None = None) -> tuple[int, int, int, int]:
        progress = max(0.0, min(1.0, float(progress)))
        distance = math.hypot(pull.x(), pull.y())
        if distance <= 1e-6:
            width, height = base_rect.width(), base_rect.height()
            x, y = base_rect.x(), base_rect.y()
            if bounds is not None:
                x = max(bounds.x(), min(x, bounds.right() - width + 1))
                y = max(bounds.y(), min(y, bounds.bottom() - height + 1))
            return x, y, width, height
        width_scale, height_scale = physics_mod.slingshot_deformation(
            pull.x(), pull.y(), progress,
        )
        if bounds is not None:
            width_scale = min(width_scale, bounds.width() / max(1, base_rect.width()))
            height_scale = min(height_scale, bounds.height() / max(1, base_rect.height()))
        width = max(1, int(round(base_rect.width() * width_scale)))
        height = max(1, int(round(base_rect.height() * height_scale)))
        # Keep the draw rect centered so the fixed hit canvas never moves.
        x = base_rect.center().x() - width // 2
        y = base_rect.center().y() - height // 2
        if bounds is not None:
            x = max(bounds.x(), min(x, bounds.right() - width + 1))
            y = max(bounds.y(), min(y, bounds.bottom() - height + 1))
        return x, y, width, height

    @staticmethod
    def _slingshot_trajectory_preview(
        trajectory: list[tuple[float, float]], center: QPointF, bounds: QRect,
        scale: float,
    ) -> list[tuple[float, float]]:
        """Translate physical samples from the character edge without distorting the arc."""
        if not trajectory:
            return []
        return [(center.x() + x, center.y() + y)
                for x, y in trajectory]

    @staticmethod
    def _slingshot_trajectory_anchor(character_rect: QRect, launch: QPoint) -> QPointF:
        """Return the edge where a ray from the character center exits its visible rect."""
        if character_rect.isEmpty():
            return QPointF(character_rect.center())
        length = math.hypot(launch.x(), launch.y())
        if length <= 1e-6:
            return QPointF(character_rect.center())
        ux, uy = launch.x() / length, launch.y() / length
        half_width = character_rect.width() / 2.0
        half_height = character_rect.height() / 2.0
        distances = [half_width / abs(ux)] if abs(ux) > 1e-6 else []
        if abs(uy) > 1e-6:
            distances.append(half_height / abs(uy))
        distance = min(distances)
        center = character_rect.center()
        return QPointF(center.x() + ux * distance, center.y() + uy * distance)

    @staticmethod
    def _slingshot_band_points(character_rect: QRect, mouse_local: QPoint,
                               pull: QPoint) -> tuple[QPointF, QPointF]:
        """Return the visible edge and current mouse endpoint of the pull band."""
        direction = QPoint(mouse_local - character_rect.center())
        if direction.isNull():
            direction = QPoint(-pull)
        start = PetWindow._slingshot_trajectory_anchor(character_rect, direction)
        return start, QPointF(mouse_local)

    def _enter_slingshot(self, global_pos: QPoint) -> None:
        self._interaction_state = "SLINGSHOT_AIMING"
        self._slingshot_anchor_pos = QPoint(self.pos())
        self._slingshot_anchor_mouse = QPoint(global_pos)
        self._slingshot_mouse = QPoint(global_pos)
        self._slingshot_pull = QPoint(0, 0)
        self._context_menu_suppressed = True
        self._just_dragged = False
        self._stop_physics()
        self.setFocus(Qt.FocusReason.OtherFocusReason)
        self.update()

    def _update_slingshot_aim(self, global_pos: QPoint) -> None:
        if self._slingshot_anchor_mouse is None:
            return
        pull = self._slingshot_anchor_mouse - global_pos
        max_distance = physics_mod.SLINGSHOT_MAX_DISTANCE * self.scale
        length = math.hypot(pull.x(), pull.y())
        if length > max_distance and length > 0:
            ratio = max_distance / length
            pull = QPoint(round(pull.x() * ratio), round(pull.y() * ratio))
        self._slingshot_mouse = QPoint(global_pos)
        self._slingshot_pull = pull
        self.update()

    def _clear_slingshot_input(self) -> None:
        self._slingshot_anchor_pos = None
        self._slingshot_anchor_mouse = None
        self._slingshot_mouse = None
        self._slingshot_pull = QPoint(0, 0)
        self._press_global = None
        self._grab_offset = None
        self._dragging = False
        self._sync_drag_polling(False)

    def _start_slingshot_rebound(self, progress: float) -> None:
        self._slingshot_rebound_progress = max(0.0, min(1.0, float(progress)))
        if self._slingshot_rebound_progress <= 0.0:
            return
        self._squash_active = True
        self._squash_progress = 0.0
        self._squash_clock.start()
        self._squash_timer.start()

    def _suppress_click_after_slingshot(self) -> None:
        self._just_dragged = True
        QTimer.singleShot(150, self, self._clear_just_dragged)

    def _cancel_slingshot_to_drag(self) -> None:
        progress = self._slingshot_progress()
        self._interaction_state = "DRAGGING"
        self._slingshot_anchor_mouse = None
        self._slingshot_pull = QPoint(0, 0)
        self._context_menu_suppressed = True
        if self.drag_physics and self._drag_target is None:
            self._drag_target = QPoint(self.pos())
        self._start_slingshot_rebound(progress)
        self._submit_collision_state(force=True)
        self.update()

    def _cancel_slingshot_to_anchor(self) -> None:
        progress = self._slingshot_progress()
        if self._slingshot_anchor_pos is not None:
            self.move(self._slingshot_anchor_pos)
        self._clear_slingshot_input()
        self._interaction_state = "IDLE"
        self._context_menu_suppressed = True
        self._stop_physics()
        self._start_slingshot_rebound(progress)
        self._suppress_click_after_slingshot()
        self.update()

    def _launch_slingshot(self, global_pos: QPoint) -> None:
        progress = self._slingshot_progress()
        distance = min(math.hypot(self._slingshot_pull.x(), self._slingshot_pull.y()),
                       physics_mod.SLINGSHOT_MAX_DISTANCE * self.scale)
        anchor = QPoint(self._slingshot_anchor_pos or self.pos())
        pull = QPoint(self._slingshot_pull)
        if distance < physics_mod.SLINGSHOT_MIN_DISTANCE * self.scale:
            self._cancel_slingshot_to_anchor()
            return
        speed = physics_mod.slingshot_speed(
            distance, physics_mod.SLINGSHOT_MIN_DISTANCE * self.scale,
            physics_mod.SLINGSHOT_MAX_DISTANCE * self.scale, self._throw_speed_cap,
        )
        length = math.hypot(pull.x(), pull.y()) or 1.0
        self._phys_pos[:] = [float(anchor.x()), float(anchor.y())]
        self._phys_vel[:] = [pull.x() / length * speed, pull.y() / length * speed]
        self.move(anchor)
        self._clear_slingshot_input()
        self._interaction_state = "THROWN"
        self._suppress_click_after_slingshot()
        self._last_physics_tick_time = None
        self._enter_physics_mode("throw")
        self._physics_timer.start()
        self._context_menu_suppressed = True
        self._start_slingshot_rebound(progress)
        # The launch changes both flags and velocity after move(anchor). Publish
        # it immediately; otherwise the first 50ms can remain behind the 500ms
        # idle heartbeat and a fast throw crosses a peer before registration.
        self._submit_collision_state(force=True)
        self.update()

    def _is_in_interactive_area(self, local_pos) -> bool:
        """由于动画左右有留白，只把窗口中间 1/3 宽度作为可交互区域。"""
        return self._w / 3.0 <= local_pos.x() <= self._w * 2.0 / 3.0

    def _sync_drag_polling(self, active: bool) -> None:
        """Windows 逐像素穿透轮询随拖拽按下/松手降频/恢复（非 Windows 无控制器，no-op）。"""
        ctrl = self._input_controller
        if ctrl is not None:
            ctrl.set_drag_active(active)

    def mousePressEvent(self, event) -> None:  # noqa: N802
        buttons = event.buttons() | event.button()
        if event.button() == Qt.MouseButton.RightButton and buttons & Qt.MouseButton.LeftButton:
            if (self._interaction_state == "DRAGGING" and self.slingshot_enabled
                    and not self.lock_position and not self.mouse_through):
                self._enter_slingshot(event.globalPosition().toPoint())
                event.accept()
                return
        elif event.button() == Qt.MouseButton.RightButton:
            self._context_menu_suppressed = False
        if event.button() == Qt.MouseButton.LeftButton:
            if not self._is_in_interactive_area(event.position().toPoint()):
                return  # 左右留白区域不参与点击/拖拽
            if self.click_sound_enabled:
                pair = resolve_click_sound_pair(self.cfg.get("click_sound_pack"), data_dir=self.cfg.dir)
                if pair is not None:
                    self._press_sound_pair = pair
                    self._press_sound_started_at = time.monotonic()
                    play_press_sound(pair, float(self.cfg.get("click_sound_volume", 0.70)))
            if self.lock_position:
                # 锁定位置：不记录按下，拖拽不会开始；松手时仍按点击处理
                return
            self._press_global = event.globalPosition().toPoint()
            self._sync_drag_polling(True)
            self._interaction_state = "PRESS_CANDIDATE"
            self._grab_offset = self._press_global - self.pos()
            self._dragging = False
            self._cancel_move()  # 按下即打断移动
            self._last_global = self._press_global
            self._last_move_time = time.monotonic()
            self._trail = [(self._last_move_time, self._press_global.x(), self._press_global.y())]
            self._phys_vel = [0.0, 0.0]
            self._phys_pos = [float(self.x()), float(self.y())]
            self._stop_physics()
            self.setFocus(Qt.FocusReason.OtherFocusReason)
            event.accept()
        else:
            super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        buttons = event.buttons() | getattr(event, "button", lambda: Qt.MouseButton.NoButton)()
        if self._interaction_state == "SLINGSHOT_AIMING":
            self._update_slingshot_aim(event.globalPosition().toPoint())
            event.accept()
            return
        if self._press_global is None or not (buttons & Qt.MouseButton.LeftButton):
            return
        g = event.globalPosition().toPoint()
        delta = g - self._press_global
        if not self._dragging:
            if math.hypot(delta.x(), delta.y()) < catalog.DRAG_THRESHOLD * self.scale:
                return  # 未超阈值：仍是点击候选
            if self.shift_drag and not (event.modifiers() & Qt.KeyboardModifier.ShiftModifier):
                # SHIFT+左键才能拖动：拖拽开始（越过阈值）时必须按住 SHIFT。
                # 判定放在阈值处而非按下时：Windows 上 press 事件的修饰键
                # 不一定可靠，且用户可能先按下再补按 SHIFT。未按 SHIFT 时
                # 取消按压状态，松手仍按点击处理。
                self._press_global = None
                self._grab_offset = None
                self._sync_drag_polling(False)
                return
            self._dragging = True
            self._interaction_state = "DRAGGING"
            self._submit_collision_state(force=True)
            # 用户真正开始拖动 = 接管位置决策，撤销"等副屏上线自动恢复"
            # （必须在这里而不是按下时：普通点击/未过阈值/未按 SHIFT 不算接管）
            _disarm = getattr(self, '_disarm_screen_restore_retry', None)
            if callable(_disarm):
                _disarm()
            if self.drag:
                self._switch(self.drag)  # 进入拖拽：播放悬空反馈动画
            if self.drag_physics:
                self._phys_pos = [float(self.x()), float(self.y())]
                self._drag_target = g - self._grab_offset
                self._enter_physics_mode('drag')
                self._last_physics_tick_time = None
                self._physics_timer.start()
            else:
                self.move(g - self._grab_offset)
            self._last_global = g
            self._last_move_time = time.monotonic()
            self._trail.append((self._last_move_time, g.x(), g.y()))
            event.accept()
            return

        # 已经处于拖拽中
        if self.drag_physics:
            now = time.monotonic()
            self._trail.append((now, g.x(), g.y()))
            cutoff = now - physics_mod.TRAIL_KEEP_SEC
            self._trail = [sample for sample in self._trail if sample[0] >= cutoff]
            self._last_global = g
            self._last_move_time = now
            self._drag_target = g - self._grab_offset
            if self._physics_mode != 'drag':
                self._enter_physics_mode('drag')
                self._last_physics_tick_time = None
                self._physics_timer.start()
        else:
            self.move(g - self._grab_offset)  # 跟手（保持抓起时的偏移）
        event.accept()

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.RightButton and self._interaction_state == "SLINGSHOT_AIMING":
            self._cancel_slingshot_to_drag()
            event.accept()
            return
        if event.button() == Qt.MouseButton.LeftButton and self._interaction_state == "SLINGSHOT_AIMING":
            self._launch_slingshot(event.globalPosition().toPoint())
            event.accept()
            return
        if event.button() != Qt.MouseButton.LeftButton:
            super().mouseReleaseEvent(event)
            return
        was_dragging = self._dragging
        g = event.globalPosition().toPoint()
        dist = 0.0
        if self._press_global is not None:
            d = g - self._press_global
            dist = math.hypot(d.x(), d.y())
        if was_dragging:
            self._just_dragged = True  # 抑制拖拽结束后的幽灵点击
            QTimer.singleShot(150, self, self._clear_just_dragged)
            if self.drag_physics:
                rvx, rvy = physics_mod.estimate_release_velocity(
                    self._trail, time.monotonic(), cap=self._throw_speed_cap
                )
                if math.hypot(rvx, rvy) < physics_mod.DEAD_ZONE_SPEED:
                    if self._grab_offset is not None:
                        self.move(g - self._grab_offset)
                    self._stop_physics()
                    self._save_position()
                else:
                    self._phys_vel[:] = [rvx, rvy]
                    self._enter_physics_mode('throw')
                    self._last_physics_tick_time = None
                    self._physics_timer.start()
            else:
                if self._grab_offset is not None:
                    self.move(g - self._grab_offset)  # 停在松手处
                self._save_position()
            if self.idles:
                self._switch(self._pick(self.idles))  # 回待机缓冲
        elif dist < catalog.DRAG_THRESHOLD * self.scale:
            if self._press_sound_pair is not None and self.click_sound_enabled:
                play_release_sound(
                    self._press_sound_pair,
                    float(self.cfg.get("click_sound_volume", 0.70)),
                    self._press_sound_started_at,
                )
            if not self._try_open_quick_chat_from_bubble(g):
                self._on_click()
        self._dragging = False
        self._interaction_state = "IDLE"
        self._press_global = None
        self._grab_offset = None
        self._sync_drag_polling(False)
        if self._cursor_restore_pending or self._cursor_visibility == 'SHOWING':
            self._cursor_restore_pending = False
            self._auto_cursor_hidden = False
        self._apply_effective_mouse_through()
        self._submit_collision_state(force=True)
        event.accept()

    def _clear_just_dragged(self) -> None:
        self._just_dragged = False

    def _on_speech_bubble_clicked(self) -> None:
        if callable(getattr(self, "on_open_quick_chat", None)):
            self.on_open_quick_chat()

    def _try_open_quick_chat_from_bubble(self, global_pos) -> bool:
        """点击桌宠头顶的气泡时打开快速对话（而不是触发 Q 弹）。"""
        callback = getattr(self, "on_open_quick_chat", None)
        if not callable(callback):
            return False
        bubble = getattr(self, "_speech_bubble", None)
        if bubble is None or not bubble.isVisible():
            return False
        if not bubble.geometry().contains(global_pos):
            return False
        callback()
        return True

    def _on_click(self) -> None:
        """真点击 → 随机一个点击回应动画，并重置当前动画（可连续点击打断）。"""
        if self._just_dragged:
            return
        if callable(self.on_restore_fun_windows):
            self.on_restore_fun_windows()
        if not self.clicks:
            return
        # 点击可以打断当前动画（包括正在播放的点击回应），实现连续 Q 弹。
        # 先让 Q 弹/动画立刻开始，音效放到下一轮事件循环，避免任何音频
        # 初始化/文件扫描阻塞点击瞬间的画面更新。
        click_name = self._pick(self.clicks)
        self._cancel_move()
        self._start_squash()
        self._switch(click_name)
        if resolve_click_sound_pair(self.cfg.get("click_sound_pack"), data_dir=self.cfg.dir) is None:
            self._schedule_click_sound()
        if self.click_show_balance and callable(self.on_show_balance):
            self.on_show_balance(self)
        elif self.click_show_self_talk and self._self_talk_enabled:
            if self._show_click_self_talk(click_name):
                self._schedule_self_talk(after_display=True)

    def _schedule_click_sound(self) -> None:
        if not self.click_sound_enabled:
            return

        def play() -> None:
            if shiboken6.isValid(self):
                self._play_click_sound()

        QTimer.singleShot(0, play)

    def _play_click_sound(self) -> None:
        if not self.click_sound_enabled:
            return
        pack = self.cfg.get("click_sound_pack")
        candidates = resolve_click_sound_candidates(pack, data_dir=self.cfg.dir)
        path = choose_sound(candidates)
        if path is None:
            return
        volume = float(self.cfg.get("click_sound_volume", 0.70))
        play_sound(path, volume=volume)

    def _play_collision_sound(self) -> None:
        if not self.collision_sound_enabled:
            return
        now = time.monotonic()
        if now - self._last_collision_sound_at < 0.25:
            return
        self._last_collision_sound_at = now
        volume = self.collision_sound_volume
        pair = resolve_click_sound_pair(self.cfg.get("click_sound_pack"), data_dir=self.cfg.dir)
        if pair is not None:
            play_press_sound(pair, volume)
        else:
            candidates = resolve_click_sound_candidates(self.cfg.get("click_sound_pack"), data_dir=self.cfg.dir)
            path = choose_sound(candidates)
            if path is not None:
                play_sound(path, volume=volume)

    # ================================================================ 看看屏幕
    def _on_look_screen(self) -> None:
        """Capture and analyse the screen outside the GUI thread."""
        if self._look_busy:
            self.show_bubble("上一张还没看完呢…")
            return
        now = time.monotonic()
        if now - self._last_look_ts < 4.0:
            self.show_bubble("喘口气嘛，刚看过啦…")
            return
        self._last_look_ts = now
        self._look_busy = True
        self.show_bubble("让我看看…", 6000)

        # 在主线程解析好快照，避免后台 worker 线程改写共享配置对象
        import copy
        settings = self.cfg.chat_settings()
        provider = copy.copy(settings.active_config)
        provider.api_key = self.cfg.resolve_api_key(provider)
        system_prompt = settings.default_system_prompt

        threading.Thread(
            target=self._look_worker,
            args=(provider, system_prompt),
            daemon=True,
            name="pet-look-screen",
        ).start()

    def _look_worker(self, provider: Any, system_prompt: str) -> None:
        # 延迟导入：无 Chat / 不使用「看看屏幕」的实例启动时不加载 PIL
        from . import vision as vision_mod
        try:
            shot = vision_mod.capture_screen_bytes()
            app_info = vision_mod.foreground_app_info()
            reply = vision_mod.ask_about_screen(
                shot, app_info, system_prompt, provider
            )
            if shiboken6.isValid(self) is False:
                return  # 窗口已销毁（退出/切角色），不再触碰信号
            user_text = f"[看看屏幕] 前台窗口：{app_info}" if app_info else "[看看屏幕]"
            self.look_done.emit(reply, user_text, False)
        except Exception as exc:
            logging.exception("看看屏幕失败")
            if shiboken6.isValid(self) is False:
                return
            self.look_done.emit(str(exc), "", True)

    def _on_look_done(self, text: str, user_text: str, is_error: bool) -> None:
        self._look_busy = False
        if is_error:
            self.show_bubble(f"看不清啊…{text[:60]}", 5000)
            return
        self.show_bubble(text, max(4000, min(12000, len(text) * 150)))
        if callable(self.on_look_synced):
            self.on_look_synced(user_text, text)

    def contextMenuEvent(self, event) -> None:  # noqa: N802
        if self._context_menu_suppressed:
            self._context_menu_suppressed = False
            event.accept()
            return
        if self._interaction_state in ("DRAGGING", "SLINGSHOT_AIMING") and self._press_global is not None:
            event.accept()
            return
        if not self._is_in_interactive_area(event.pos()):
            return
        self._show_context_menu(event.globalPos())

    def keyPressEvent(self, event) -> None:  # noqa: N802
        if event.key() == Qt.Key.Key_Escape and self._interaction_state == "SLINGSHOT_AIMING":
            self._cancel_slingshot_to_anchor()
            event.accept()
            return
        super().keyPressEvent(event)

    def focusOutEvent(self, event) -> None:  # noqa: N802
        if self._interaction_state == "SLINGSHOT_AIMING":
            self._cancel_slingshot_to_anchor()
        super().focusOutEvent(event)

    def hideEvent(self, event) -> None:  # noqa: N802
        if self._interaction_state == "SLINGSHOT_AIMING":
            self._cancel_slingshot_to_anchor()
        super().hideEvent(event)

    def _show_context_menu(self, global_pos: QPoint) -> None:
        self._context_menu_anchor = QPoint(global_pos)
        # 气泡是置顶 Tool 窗口（层级高于原生菜单 popup），右键时先隐藏，
        # 避免气泡盖住菜单
        self._speech_bubble.hide()
        menu = QMenu(self)
        self._active_context_menu = menu
        _populate_context_menu(menu, self)
        menu.aboutToHide.connect(
            lambda self=self: QTimer.singleShot(0, self, self._restore_on_top_after_context_menu)
        )
        # 根菜单避让角色且始终保持 LTR 视觉方向；右侧不够时贴近角色左侧。
        # 子菜单弹出侧由 Qt 按屏幕空间决定，再不行使用整树重叠最少的远角。
        pet_rect = self.visible_content_rect()
        scr = self._screen_available()
        avail = scr.availableGeometry() if scr is not None else QRect()
        submenu_width = max(
            (child.sizeHint().width() for child in menu.findChildren(QMenu)),
            default=0,
        )
        popup_pos, direction = pick_context_menu_position(
            pet_rect, menu.sizeHint(), submenu_width, avail
        )
        menu.setLayoutDirection(direction)
        for child in menu.findChildren(QMenu):
            child.setLayoutDirection(direction)
        menu_size = menu.sizeHint()
        slide_toward_pet = 18 if popup_pos.x() < pet_rect.center().x() else -18
        transition_start = _clamp_menu_rect(
            QRect(
                popup_pos.x() + slide_toward_pet,
                popup_pos.y(),
                menu_size.width(),
                menu_size.height(),
            ),
            avail,
        ).topLeft()
        menu.aboutToShow.connect(
            lambda menu=menu, target=QPoint(popup_pos): QTimer.singleShot(
                0,
                menu,
                lambda menu=menu, target=target: animate_context_menu_to(menu, target),
            )
        )
        menu.exec(transition_start)
        callbacks = take_deferred_menu_callbacks(menu)
        if getattr(self, "_active_context_menu", None) is menu:
            self._active_context_menu = None
        if callbacks:
            def dispatch_callbacks() -> None:
                for callback in callbacks:
                    callback()

            def schedule_after_menu_destroyed(*_args) -> None:
                # Windows may keep the translucent popup's native backing
                # surface alive briefly after exec() returns. Wait for the
                # QMenu QObject to be destroyed, then yield once more before
                # showing or activating another top-level window.
                try:
                    if not shiboken6.isValid(self):
                        return
                    QTimer.singleShot(0, self, dispatch_callbacks)
                except RuntimeError:
                    # The owning pet can be destroyed between isValid() and
                    # registering the context-bound timer during shutdown or
                    # character replacement. Its menu command is no longer
                    # meaningful, so discard it without touching Qt again.
                    return

            menu.destroyed.connect(schedule_after_menu_destroyed)
        # 菜单使用完毕即释放整棵菜单树：QMenu 以长命窗口为 parent，
        # 不删除会随每次右键累积（子菜单/动作/线程池/图标 pixmap）。
        # 先清掉尚未启动的解码任务，避免 QThreadPool 析构时在 GUI 线程
        # 等待运行中的 worker。
        pools = []
        for submenu in menu.findChildren(QMenu):
            pool = getattr(submenu, "_animation_icon_pool", None)
            if pool is not None:
                pool.clear()
                pools.append(pool)

        def delete_when_idle() -> None:
            """非阻塞等待图标解码 worker 结束后再释放菜单树。

            直接 pool.waitForDone(3000) 会阻塞 GUI 线程最多 3 秒，可能造成
            右键菜单关闭时卡顿/假死；这里每 50ms 轮询一次，不阻塞事件循环。
            """
            if any(not pool.waitForDone(0) for pool in pools):
                QTimer.singleShot(50, delete_when_idle)
                return
            menu.deleteLater()

        if pools:
            delete_when_idle()
        else:
            menu.deleteLater()

    def reopen_context_menu(self, menu: QMenu) -> None:
        """Close the old template and immediately show the newly selected one."""
        # QMenu may move the requested right-click point to remain on-screen.
        # Preserve the position the user actually saw, not the raw event point.
        global_pos = QPoint(menu.pos()) if menu is not None else QPoint(
            getattr(self, "_context_menu_anchor", QCursor.pos())
        )
        self._context_menu_anchor = QPoint(global_pos)
        menu.close()
        QTimer.singleShot(10, self, lambda: self._show_context_menu(global_pos))

    @staticmethod
    def _read_self_talk_texts(value) -> list[str]:
        if not isinstance(value, list):
            return list(DEFAULT_SELF_TALK_TEXTS)
        texts = []
        for item in value:
            text = str(item).strip()[:120]
            if text and text not in texts:
                texts.append(text)
        return texts or list(DEFAULT_SELF_TALK_TEXTS)

    def _schedule_self_talk(self, *, after_display: bool = False) -> None:
        self._self_talk_timer.stop()
        if not self._self_talk_enabled or not (
            self._self_talk_texts or self._self_talk_images
        ):
            return
        delay = random.uniform(self._self_talk_min_interval, self._self_talk_max_interval)
        if after_display:
            delay += self._self_talk_duration_seconds
        self._self_talk_timer.start(max(1000, int(round(delay * 1000))))

    def _show_self_talk_text(self, text: str) -> bool:
        if getattr(self, "_bubble_suppressed", False):
            return False
        duration_ms = int(round(self._self_talk_duration_seconds * 1000))
        anchor = self.visible_content_rect()
        _set_speech_bubble_interactive(self)
        self._speech_bubble.show_text(
            text, anchor, duration_ms, pet_scale=self.scale
        )
        return True

    def _show_random_self_talk(self) -> bool:
        if getattr(self, "_bubble_suppressed", False):
            return False
        # 惰性剔除运行期间被删除的图片（列表是启动/设置时的快照）
        live_images = [p for p in self._self_talk_images if p.is_file()]
        if len(live_images) != len(self._self_talk_images):
            self._self_talk_images = live_images
        choices = [
            ("text", text) for text in self._self_talk_texts
        ] + [
            ("image", path) for path in self._self_talk_images
        ]
        if not choices:
            return False
        kind, value = random.choice(choices)
        duration_ms = int(round(self._self_talk_duration_seconds * 1000))
        anchor = self.visible_content_rect()
        _set_speech_bubble_interactive(self)
        if kind == "image":
            return self._speech_bubble.show_image(
                value, anchor, duration_ms, pet_scale=self.scale
            )
        return self._show_self_talk_text(value)

    def _show_click_self_talk(self, click_name: str) -> bool:
        """优先播放当前点击动画绑定的台词；未绑定则回退全局随机自言自语。"""
        character_id = str(self.cfg.get('character', catalog.DEFAULT_CHARACTER))
        texts = self.cfg.click_talk_texts_for(character_id, click_name)
        if texts:
            return self._show_self_talk_text(random.choice(texts))
        return self._show_random_self_talk()

    def _on_self_talk_timeout(self) -> None:
        if time.time() < self._bubble_busy_until:
            # 重要气泡占用中：本次自言自语跳过，重新排队下一次
            self._schedule_self_talk()
            return
        displayed = False
        if self._self_talk_enabled and self.isVisible():
            displayed = self._show_random_self_talk()
        self._schedule_self_talk(after_display=displayed)

    def hold_bubble(self, seconds: float) -> None:
        """声明重要气泡占用时长（自言自语在此期间让路）。"""
        self._bubble_busy_until = max(self._bubble_busy_until, time.time() + max(0.0, seconds))

    def set_bubble_suppressed(self, suppressed: bool) -> None:
        """设置窗口打开期间暂停气泡显示；True 时立即隐藏当前气泡。"""
        self._bubble_suppressed = bool(suppressed)
        if self._bubble_suppressed:
            self._speech_bubble.hide()

    def _check_music_sing(self) -> None:
        """检测后台音乐并自动播放唱歌动画（可配置开关）。

        音乐播放期间唱歌动画会持续循环；音乐停止或开关关闭后恢复普通动画链。
        不打断正在播放的一次性动作/点击/拖拽。
        """
        if not self.isVisible():
            return
        if not self._music_sing_enabled:
            self._music_sing_active = False
            return
        from . import music_detect
        playing = music_detect.is_music_playing()
        if self._music_sing_active:
            if not playing:
                self._music_sing_active = False
            return
        if self._dragging or self._is_one_shot_playing():
            return
        if playing:
            self._music_sing_active = True
            self._switch(SING_ANIM)

    def show_bubble(self, text: str, duration_ms: int = 3200, subtitle: str | None = None) -> None:
        """向桌宠头顶冒泡提示（app 层反馈用，非侵入）。重要气泡会占用气泡位。"""
        if not self.isVisible() or self._bubble_suppressed:
            return
        _set_speech_bubble_interactive(self)
        self.hold_bubble(duration_ms / 1000.0 + 2.0)
        self._speech_bubble.show_text(
            str(text), self.visible_content_rect(), duration_ms,
            pet_scale=self.scale, subtitle=str(subtitle or ""),
        )

    def refresh_pet_settings(self) -> None:
        collision_enabled = bool(self.cfg.get('collision_enabled', True))
        if collision_enabled and self._collision_session is None:
            self.attach_collision_session(getattr(self, '_collision_app_session', None))
        elif not collision_enabled and self._collision_session is not None:
            self.detach_collision_session()
        self._sync_collision_policy()
        desired_scale = float(self.cfg.get('scale', self.scale))
        self.change_scale(desired_scale)
        desired_speed = float(self.cfg.get('playback_speed', self.playback_speed))
        if abs(desired_speed - self.playback_speed) >= 0.001:
            self.set_playback_speed(desired_speed)
        desired_on_top = bool(self.cfg.get('on_top', True))
        current_on_top = bool(self.windowFlags() & Qt.WindowType.WindowStaysOnTopHint)
        if desired_on_top != current_on_top:
            self.set_on_top(desired_on_top)
        desired_no_move = bool(self.cfg.get('no_move', False))
        if desired_no_move != self.no_move:
            self.set_no_move(desired_no_move)
        # 窗口类开关也要立即生效（否则用户保存后得重启或再去菜单切一次）
        desired_mouse_through = bool(self.cfg.get('mouse_through', False))
        if desired_mouse_through != self._user_mouse_through:
            self.set_mouse_through(desired_mouse_through)
        desired_cursor_passthrough = bool(self.cfg.get('cursor_hidden_passthrough', True))
        if desired_cursor_passthrough != self._cursor_hidden_passthrough:
            self.set_cursor_hidden_passthrough(desired_cursor_passthrough)
        desired_auto_hide = bool(self.cfg.get('auto_hide_fullscreen', True))
        if desired_auto_hide != self.auto_hide_fullscreen:
            self.set_auto_hide_fullscreen(desired_auto_hide)
        desired_stream_capture = bool(self.cfg.get('stream_capture_mode', False))
        if desired_stream_capture != self._stream_capture_mode:
            self.set_stream_capture_mode(desired_stream_capture)
        desired_drag_physics = bool(self.cfg.get('drag_physics', False))
        if desired_drag_physics != self.drag_physics:
            self.set_drag_physics(desired_drag_physics)
        desired_lock = bool(self.cfg.get('lock_position', False))
        if desired_lock != self.lock_position:
            self.set_lock_position(desired_lock)
        desired_shift = bool(self.cfg.get('shift_drag', False))
        if desired_shift != self.shift_drag:
            self.set_shift_drag(desired_shift)
        desired_slingshot = bool(self.cfg.get('slingshot_enabled', True))
        if desired_slingshot != self.slingshot_enabled:
            self.slingshot_enabled = desired_slingshot
        desired_opacity = int(_float_or_default(self.cfg.get('pet_opacity', 100), 100, 10, 100))
        if desired_opacity != self.pet_opacity:
            self.set_pet_opacity(desired_opacity)
        else:
            self._apply_opacity()  # 首次/未变时也确保窗口已应用
        self.animation_gap_seconds = max(0.0, min(3600.0, float(self.cfg.get('animation_gap_seconds', 0.0))))
        if self.animation_gap_seconds <= 0:
            self._cancel_animation_gap()
        self._music_sing_enabled = bool(self.cfg.get('music_sing_enabled', False))
        if self._music_sing_enabled:
            # 隐藏期间保持停止，恢复显示时由 _resume_activity 按开关状态启动
            if self.isVisible():
                self._music_sing_timer.start()
        else:
            self._music_sing_active = False
            self._music_sing_timer.stop()
        self._self_talk_enabled = bool(self.cfg.get('self_talk_enabled', False))
        self._speech_bubble.set_style(
            str(self.cfg.get('self_talk_bubble_style', DEFAULT_SELF_TALK_BUBBLE_STYLE))
        )
        self._self_talk_texts = self._read_self_talk_texts(self.cfg.get('self_talk_texts'))
        self._self_talk_duration_seconds = max(
            1.0,
            min(300.0, float(self.cfg.get(
                'self_talk_duration_seconds', DEFAULT_SELF_TALK_DURATION_SECONDS
            ))),
        )
        self._self_talk_image_dir = str(self.cfg.get('self_talk_image_dir', '') or '')
        self._self_talk_images = list_self_talk_images(_resolve_self_talk_image_dir(self._self_talk_image_dir))
        self._self_talk_min_interval = max(5.0, float(self.cfg.get('self_talk_min_interval', DEFAULT_SELF_TALK_MIN_INTERVAL)))
        self._self_talk_max_interval = max(self._self_talk_min_interval, float(self.cfg.get('self_talk_max_interval', DEFAULT_SELF_TALK_MAX_INTERVAL)))
        self.click_sound_path = str(self.cfg.get('click_sound_path', '') or '')
        self._throw_speed_cap = physics_mod.throw_speed_cap(self.cfg.get('throw_strength'))
        self.click_show_balance = bool(self.cfg.get('click_show_balance', False))
        self.click_show_self_talk = bool(self.cfg.get('click_show_self_talk', False))
        self._schedule_self_talk()
        if hasattr(self, 'proactive_watcher') and self.proactive_watcher is not None:
            self.proactive_watcher.apply_config()

    def set_context_menu_template(self, template_id: str) -> None:
        """Persist the selected right-click menu template for the next open."""
        template_id = template_id if template_id in {'legacy', 'modern'} else 'legacy'
        self.cfg.set('context_menu_template', template_id)
        self.cfg.save()

    def set_animation_gap(self, seconds: float) -> None:
        self.animation_gap_seconds = max(0.0, min(3600.0, float(seconds)))
        self.cfg.set('animation_gap_seconds', self.animation_gap_seconds)
        self.cfg.save()
        if self.animation_gap_seconds <= 0:
            self._cancel_animation_gap()

    def set_self_talk_settings(
        self,
        enabled: bool,
        minimum: float,
        maximum: float,
        texts,
        *,
        duration: float | None = None,
        image_dir: str | None = None,
    ) -> None:
        self._self_talk_enabled = bool(enabled)
        self._self_talk_min_interval = max(5.0, float(minimum))
        self._self_talk_max_interval = max(self._self_talk_min_interval, float(maximum))
        self._self_talk_texts = self._read_self_talk_texts(texts)
        if duration is not None:
            self._self_talk_duration_seconds = max(1.0, min(300.0, float(duration)))
        if image_dir is not None:
            self._self_talk_image_dir = str(image_dir or '').strip()
            self._self_talk_images = list_self_talk_images(_resolve_self_talk_image_dir(self._self_talk_image_dir))
        self.cfg.set('self_talk_enabled', self._self_talk_enabled)
        self.cfg.set('self_talk_min_interval', self._self_talk_min_interval)
        self.cfg.set('self_talk_max_interval', self._self_talk_max_interval)
        self.cfg.set('self_talk_texts', list(self._self_talk_texts))
        self.cfg.set('self_talk_duration_seconds', self._self_talk_duration_seconds)
        self.cfg.set('self_talk_image_dir', self._self_talk_image_dir)
        self.cfg.save()
        self._schedule_self_talk()

    def set_chat_status(self, state: str, text: str = '') -> None:
        if not text:
            return
        if not self.isVisible():
            return
        _set_speech_bubble_interactive(self)
        self._speech_bubble.show_text(
            text, self.visible_content_rect(), duration_ms=2200,
            pet_scale=self.scale,
        )


    def _toggle_proactive_enabled(self, on: bool) -> None:
        """右键菜单切换主动识屏总开关。"""
        pro_data = dict(self.cfg.get('proactive_screen', {}))
        pro_data['enabled'] = bool(on)
        self.cfg.set('proactive_screen', pro_data)
        self.cfg.save()
        if hasattr(self, 'proactive_watcher') and self.proactive_watcher is not None:
            self.proactive_watcher.apply_config()
        if on:
            eff = effective_proactive_config(self.cfg.get('proactive_screen', {}))
            if eff['whitelist']:
                self.show_bubble("主动识屏已开启～我会偶尔看看你正在用的软件", duration_ms=4000)
            else:
                self.show_bubble(
                    "主动识屏已开启～但白名单还是空的，在 右键→主动识屏→打开设置 里添加要观察的应用后我才会开始工作",
                    duration_ms=6000,
                )

    def _set_proactive_option(self, key: str, value: Any) -> None:
        """右键菜单修改主动识屏子项选项。"""
        pro_data = dict(self.cfg.get('proactive_screen', {}))
        pro_data[key] = value
        self.cfg.set('proactive_screen', pro_data)
        self.cfg.save()
        if hasattr(self, 'proactive_watcher') and self.proactive_watcher is not None:
            self.proactive_watcher.apply_config()

    def _toggle_agent_link(self, agent_key: str, on: bool, action=None) -> None:
        """右键菜单切换 Agent 状态联动子项。

        set_enabled 返回 False（用户拒绝授权 / hooks 安装失败）时，
        必须把菜单勾选态回滚，否则 UI 显示已开启而实际未生效。"""
        if hasattr(self, 'agent_link_manager') and self.agent_link_manager is not None:
            ok = self.agent_link_manager.set_enabled(agent_key, on)
            if not ok:
                if action is not None:
                    action.blockSignals(True)
                    action.setChecked(not on)
                    action.blockSignals(False)
                return
        else:
            ag_data = dict(self.cfg.get('agent_link', {}))
            ag_data[agent_key] = bool(on)
            self.cfg.set('agent_link', ag_data)
            self.cfg.save()
        if on:
            self.show_bubble(f"已开启 {agent_key.upper()} 状态联动监听～", duration_ms=4000)

    def _set_agent_link_option(self, key: str, on: bool) -> None:
        """联动气泡提醒子项开关（开始干活 / 任务完成），立即写入配置。"""
        ag_data = dict(self.cfg.get('agent_link', {}))
        ag_data[key] = bool(on)
        self.cfg.set('agent_link', ag_data)
        self.cfg.save()

    def _rename_character(self) -> None:
        """自定义当前角色的显示名（空输入 = 恢复默认目录名）。"""
        cid = str(self.cfg.get('character', catalog.DEFAULT_CHARACTER))
        current = self.cfg.character_alias(cid) or catalog.character_display_name(cid)
        name, ok = QInputDialog.getText(
            self, '重命名角色', f'给 {cid} 起个名字（留空恢复默认）：', text=current,
        )
        if not ok:
            return
        self.cfg.set_character_alias(cid, name)
        shown = self.cfg.character_alias(cid) or catalog.character_display_name(cid)
        self.show_bubble(f'角色名：{shown}')

    def _request_switch_character(self, character_id: str) -> None:
        """请求切换角色；优先交给 app 做热切换，否则只保存配置。"""
        if self.on_switch_character is not None:
            self.on_switch_character(character_id)
        else:
            self.cfg.set('character', character_id)
            self.cfg.save()

    def set_playback_speed(self, speed: float) -> None:
        """设置动画播放速率并持久化。"""
        self.playback_speed = max(0.1, float(speed))
        self.cfg.set('playback_speed', self.playback_speed)
        self.cfg.save()
        if self.movie is not None and hasattr(self.movie, 'set_playback_speed'):
            self.movie.set_playback_speed(self.playback_speed)

    def set_mouse_through(self, on: bool) -> None:
        """鼠标穿透：开启后桌宠不接收鼠标事件，点击会穿透到下层。"""
        self._user_mouse_through = bool(on)
        self.cfg.set('mouse_through', self._user_mouse_through)
        self.cfg.save()
        self._apply_effective_mouse_through()
        self._submit_collision_state(force=True)

    def _apply_effective_mouse_through(self, enabled: bool | None = None) -> None:
        effective = (bool(self._user_mouse_through or self._auto_cursor_hidden)
                     if enabled is None else bool(enabled))
        if effective == self.mouse_through:
            return
        self.mouse_through = effective
        was_visible = self.isVisible()  # setWindowFlag 重建原生窗口会先隐藏，
        self.setWindowFlag(Qt.WindowType.WindowTransparentForInput, effective)
        if was_visible:
            self.show()  # 只在原本可见时恢复：手动隐藏的桌宠不被设置保存意外唤出

    def set_throw_strength(self, strength: str) -> None:
        """设置甩出力度档位（gentle / standard / strong / crazy）。"""
        self.throw_strength = physics_mod.normalize_throw_strength(strength)
        self._throw_speed_cap = physics_mod.throw_speed_cap(self.throw_strength)
        self.cfg.set('throw_strength', self.throw_strength)
        self.cfg.set('throw_max_speed', self._throw_speed_cap)
        self.cfg.save()

    def set_drag_physics(self, on: bool) -> None:
        """拖动物理开关。"""
        self.drag_physics = bool(on)
        self.cfg.set('drag_physics', self.drag_physics)
        self.cfg.save()
        if not self.drag_physics:
            self._stop_physics()

    def set_slingshot_enabled(self, on: bool) -> None:
        """Enable or disable the independent slingshot interaction."""
        self.slingshot_enabled = bool(on)
        self.cfg.set('slingshot_enabled', self.slingshot_enabled)
        self.cfg.save()
        if not self.slingshot_enabled and self._interaction_state == SLINGSHOT_AIMING:
            self._cancel_slingshot_to_anchor()

    def set_lock_position(self, on: bool) -> None:
        """锁定位置：开启后桌宠不可拖动（点击互动仍有效）。"""
        self.lock_position = bool(on)
        self.cfg.set('lock_position', self.lock_position)
        self.cfg.save()
        if self.lock_position and self._dragging:
            self._dragging = False
            self._press_global = None
            self._grab_offset = None
            self._sync_drag_polling(False)
            self._stop_physics()
        self._submit_collision_state(force=True)

    def set_shift_drag(self, on: bool) -> None:
        """按住 SHIFT+左键才能拖动。"""
        self.shift_drag = bool(on)
        self.cfg.set('shift_drag', self.shift_drag)
        self.cfg.save()

    def set_pet_opacity(self, value: int) -> None:
        """桌宠窗口不透明度（10-100）。"""
        self.pet_opacity = max(10, min(100, int(value)))
        self.cfg.set('pet_opacity', self.pet_opacity)
        self.cfg.save()
        self._apply_opacity()

    def _apply_opacity(self) -> None:
        """把 pet_opacity 应用到窗口（值未变时跳过，避免重复系统调用）。"""
        opacity = self.pet_opacity / 100.0
        if self._applied_opacity is None or abs(self._applied_opacity - opacity) >= 0.005:
            self.setWindowOpacity(opacity)
            self._applied_opacity = opacity

    def _stop_physics(self) -> None:
        self._physics_timer.stop()
        self._physics_mode = None
        if getattr(self, '_interaction_state', IDLE) == THROWN:
            self._interaction_state = IDLE
        self._phys_vel[:] = [0.0, 0.0]
        self._submit_collision_state(force=True)

    def _enter_physics_mode(self, mode: str) -> None:
        """进入物理模式（'drag'/'throw'）：统一取消自主移动计划与动画间隔，
        避免移动插值与物理位移双写位置（画面在两个位置间闪现）。"""
        self._cancel_move()
        self._cancel_animation_gap()
        self._physics_mode = mode

    def _on_physics_tick(self) -> None:
        now = time.monotonic()
        if self._last_physics_tick_time is None:
            dt = 0.016
        else:
            dt = max(0.0, min(0.05, now - self._last_physics_tick_time))
        self._last_physics_tick_time = now

        if self._physics_mode == 'drag':
            self._tick_drag_physics(min(dt, 0.033))
        elif self._physics_mode == 'throw':
            self._tick_throw_physics(dt)

    def _tick_drag_physics(self, dt: float = 0.016) -> None:
        if self._drag_target is None:
            return
        tx, ty = self._drag_target.x(), self._drag_target.y()
        px, py = self._phys_pos
        self._phys_vel[0] = physics_mod.spring_velocity(self._phys_vel[0], px, tx, dt)
        self._phys_vel[1] = physics_mod.spring_velocity(self._phys_vel[1], py, ty, dt)
        self._phys_pos[0] += self._phys_vel[0] * dt
        self._phys_pos[1] += self._phys_vel[1] * dt
        self.move(int(round(self._phys_pos[0])), int(round(self._phys_pos[1])))

    def _tick_throw_physics(self, dt: float = 0.016) -> None:
        scr = self._screen_available()
        avail = scr.availableGeometry()
        # 忽略左右留白：角色实际可视区域约为窗口中间 1/3，
        # 允许窗口略微超出屏幕边界，让角色形象真正碰到边缘才反弹。
        margin = self._w / 3.0
        left = avail.left() - margin
        top = avail.top()
        right = avail.right() - self._w + margin
        bottom = avail.bottom() - self._h

        max_sub_dt = 0.008
        remaining = dt
        bounced_any = False
        px, py = self._phys_pos[0], self._phys_pos[1]
        vx, vy = self._phys_vel[0], self._phys_vel[1]
        start_px, start_py = px, py

        while remaining > 1e-6:
            step_dt = min(max_sub_dt, remaining)
            px, py, vx, vy, bounced = physics_mod.throw_step(
                px, py, vx, vy, step_dt, left, top, right, bottom,
            )
            bounced_any = bounced_any or bounced
            remaining -= step_dt
            speed = math.hypot(vx, vy)
            if physics_mod.is_at_rest(py, vx, vy, bottom, bounced_any, speed):
                break

        self._phys_pos[:] = [px, py]
        self._phys_vel[:] = [vx, vy]
        predict_bounce = getattr(self, '_predict_collision_bounce', None)
        if callable(predict_bounce):
            predict_bounce(start_px, start_py)
        self.move(int(round(self._phys_pos[0])), int(round(self._phys_pos[1])))
        speed = math.hypot(self._phys_vel[0], self._phys_vel[1])
        # 在地面上且水平速度也很低时，彻底停下
        if physics_mod.is_at_rest(
            self._phys_pos[1], self._phys_vel[0], self._phys_vel[1], bottom, bounced_any, speed
        ):
            self._stop_physics()

    def _predict_collision_bounce(self, start_x: float, start_y: float,
                                  incoming_vx: float | None = None,
                                  incoming_vy: float | None = None) -> None:
        if (self._physics_mode != 'throw'
                or not bool(self.cfg.get('collision_enabled', True))
                or self._collision_session is None):
            return
        now = time.monotonic()
        self._prune_collision_prediction_state(now)
        runtime_id = str(getattr(self._collision_session, 'runtime_id', ''))
        if not runtime_id:
            return

        rect = self.collision_content_rect()
        dx, dy = self._phys_pos[0] - self.x(), self._phys_pos[1] - self.y()
        current_circles = collision.circles_from_rect(
            rect.x() + dx, rect.y() + dy, rect.width(), rect.height())
        previous_circles = [[x - (self._phys_pos[0] - start_x),
                             y - (self._phys_pos[1] - start_y), radius]
                            for x, y, radius in current_circles]
        own = collision.MemberState(
            runtime_id, rect.center().x() + dx, rect.center().y() + dy,
            max(1.0, rect.width() / 2.0), max(1.0, rect.height() / 2.0),
            self._phys_vel[0], self._phys_vel[1],
            mass=collision.calculate_mass(
                max(1.0, rect.width() / 2.0), max(1.0, rect.height() / 2.0),
                scale=float(self.scale),
                collision_mass_scale=float(self.cfg.get('collision_mass_scale', 1.0))),
            flags=self._collision_flags(), circles=current_circles)
        bounce_vx = own.vx if incoming_vx is None else incoming_vx
        bounce_vy = own.vy if incoming_vy is None else incoming_vy

        for peer_id, raw_peer in self._collision_peer_snapshots.items():
            flags = int(raw_peer.get('flags', 0))
            if (not flags & collision.FLAG_VISIBLE or flags & collision.FLAG_PAUSED
                    or not flags & collision.FLAG_COLLISION_ENABLED):
                continue
            age = max(0.0, now - float(raw_peer['_received_at']))
            extrapolation = min(0.05, age)
            peer_vx, peer_vy = float(raw_peer.get('vx', 0.0)), float(raw_peer.get('vy', 0.0))
            peer_dx, peer_dy = peer_vx * extrapolation, peer_vy * extrapolation
            peer_circles = [[float(c[0]) + peer_dx, float(c[1]) + peer_dy, float(c[2])]
                            for c in raw_peer.get('circles') or () if len(c) >= 3]
            if not peer_circles:
                continue
            pair = '|'.join(sorted((runtime_id, peer_id)))
            if pair in self._predicted_bounces:
                continue
            hit = collision.check_collision_circles(current_circles, peer_circles, runtime_id, peer_id)
            if not hit[0]:
                hit = collision.swept_circle_chain_collision(
                    previous_circles, current_circles, peer_circles, peer_circles)
            collided, nx, ny, _, _, _ = hit
            vn = (peer_vx - own.vx) * nx + (peer_vy - own.vy) * ny
            if not collided or vn >= -collision.IMPULSE_MIN_APPROACH_SPEED:
                continue
            radius_x = max(1.0, float(raw_peer.get('radius_x', 1.0)))
            radius_y = max(1.0, float(raw_peer.get('radius_y', 1.0)))
            peer = collision.MemberState(
                peer_id, float(raw_peer.get('x', 0.0)) + peer_dx,
                float(raw_peer.get('y', 0.0)) + peer_dy, radius_x, radius_y,
                peer_vx, peer_vy,
                mass=collision.calculate_mass(
                    radius_x, radius_y,
                    scale=float(raw_peer.get('scale', collision.DEFAULT_BASE_SCALE) or collision.DEFAULT_BASE_SCALE),
                    collision_mass_scale=float(self.cfg.get('collision_mass_scale', 1.0))),
                is_infinite_mass=bool(flags & (collision.FLAG_DRAGGING | collision.FLAG_LOCK_POSITION)),
                flags=flags, circles=peer_circles)
            _, dvx, dvy, _, _ = collision.solve_collision_impulse(
                own, peer, nx, ny,
                restitution=float(self.cfg.get('collision_restitution', .82)),
                friction=float(self.cfg.get('collision_friction', .08)),
                impulse_cap=float(self.cfg.get('collision_impulse_cap', 9000.0)))
            self._phys_vel[0] += dvx
            self._phys_vel[1] += dvy
            speed = math.hypot(*self._phys_vel)
            if speed > self._throw_speed_cap:
                clamped = physics_mod.soft_clamp_speed(speed, self._throw_speed_cap)
                self._phys_vel[:] = [self._phys_vel[0] * clamped / speed,
                                     self._phys_vel[1] * clamped / speed]
            self._predicted_bounces[pair] = now
            self._pending_predicted_bounce = (float(bounce_vx), float(bounce_vy))
            self._pending_predicted_contact = (
                float(own.x), float(own.y),
                [[float(c[0]), float(c[1]), float(c[2])] for c in current_circles],
            )
            self._play_collision_sound()
            self._submit_collision_state(force=True)
            if not self._squash_active and now - self._last_collision_squash_at >= 0.25:
                self._last_collision_squash_at = now
                self._start_squash()
            break

    def _request_quit(self) -> None:
        # 不在这里保存当前位置：退出时若正处于自动移动/物理抛掷后的位置，
        # 会把随机终点写进记忆，导致重启后位置变化。手动放置的位置已在
        # 拖动松手/回右下角/缩放时保存过。
        # The context menu is shown with QMenu.exec(), which owns a nested
        # event loop. Quitting the application from inside QAction.triggered
        # can leave that native menu loop alive (notably on macOS), making the
        # command appear to do nothing. End menu tracking first, then quit on
        # the next GUI event-cycle.
        menu = getattr(self, "_active_context_menu", None)
        app = QApplication.instance()
        if app is None:
            return
        if menu is not None:
            menu.close()
            QTimer.singleShot(0, app.quit)
            return
        # Normal context-menu actions are now dispatched only after
        # QMenu.exec() has returned, so there is no nested menu loop left to
        # unwind. Quitting synchronously avoids the first click being consumed
        # before the zero-delay callback can run.
        app.quit()

    def moveEvent(self, event) -> None:  # noqa: N802
        super().moveEvent(event)
        # 非 force 节流提交：位置变化由去重 + 20Hz 限流兜底，运动期由
        # _collision_timer（50ms）强制上报，避免 60Hz 抛掷移动上报超标
        self._submit_collision_state()
        self._speech_bubble.reposition(self.visible_content_rect())
        for listener in tuple(self._position_listeners):
            try:
                listener(self)
            except Exception:
                logging.exception("\u684c\u5ba0\u4f4d\u7f6e\u76d1\u542c\u5668\u6267\u884c\u5931\u8d25")

    def closeEvent(self, event) -> None:  # noqa: N802
        if getattr(self, "_interaction_state", IDLE) == SLINGSHOT_AIMING:
            self._cancel_slingshot_to_anchor()
        self._disarm_screen_restore_retry()  # 窗口销毁前摘掉 screenAdded 监听/超时回调
        self._stop_fs_watch()
        self.detach_collision_session()
        if self._input_controller is not None:
            self._input_controller.stop()
            self._input_controller = None
        # 不在这里覆盖记忆位置：避免自动移动/抛掷后的随机终点被存下来。
        self._self_talk_timer.stop()
        self._cancel_animation_gap()
        self._speech_bubble.hide()
        super().closeEvent(event)
