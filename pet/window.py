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
)
from PySide6.QtGui import QBitmap, QColor, QCursor, QImage, QPainter, QPen, QPixmap, QRegion
from PySide6.QtWidgets import QApplication, QInputDialog, QMenu, QToolTip, QWidget

from . import autostart as autostart_mod
from . import catalog
from . import perfstats
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
from .frame_cache import FRAME_CACHE_DEFAULT_MAX_BYTES, FramePixmapCache
from .animation_thumbnail import decode_representative_frame, representative_frame_index
from .speech_bubble import PetSpeechBubble, list_self_talk_images
from .fun_image_popup import oijingjing_image_path, resolve_fun_asset
from .context_menu import normalize_template_id, populate_context_menu as _populate_context_menu
from .context_menus.shared import take_deferred_menu_callbacks
from . import vision as vision_mod
from . import physics as physics_mod
from .collision_client import CollisionClient
from .click_sound import (
    choose_sound, play_sound, resolve_click_sound_candidates, resolve_click_sound_pair,
    play_press_sound, play_release_sound,
)
from .proactive import effective_proactive_config
from .updater import QUARK_PAN_URL, REPO_URL

from . import platform_win
from .platform_mac import _keep_macos_tool_window_visible, _mac_set_window_level
from .platform_win import (
    GWL_STYLE as GWL_STYLE,
    GWL_EXSTYLE as GWL_EXSTYLE,
    _WS_CAPTION as _WS_CAPTION,
    _WS_EX_TOPMOST as _WS_EX_TOPMOST,
    _WS_EX_TRANSPARENT as _WS_EX_TRANSPARENT,
    _WinRect as _WinRect,
    _WinMonitorInfo as _WinMonitorInfo,
    _set_windows_click_through as _set_windows_click_through,
    WindowsPerPixelInputController as WindowsPerPixelInputController,
    _FS_SKIP_CLASSES as _FS_SKIP_CLASSES,
    _fullscreen_geometry_hit as _fullscreen_geometry_hit,
    _fs_user_busy_state as _fs_user_busy_state,
    _fg_fullscreen_probe as _fg_fullscreen_probe,
    _fg_fullscreen_win32 as _fg_fullscreen_win32,
)

# 后台播放音乐时自动播放的唱歌/哼歌动画
SING_ANIM = '悠闲哼歌'

# 动画启动被拒（movie.start() 返回 False，如退役 reader 卡死）时的降级策略：
# 回退到上一个可播放动画/待机，并安排稍后重试被拒动画（B7 审查 P1-1）。
# 重试有次数上限：病态 reader 永不退出时不再无限重试，避免 GUI 反复同步解码。
_SWITCH_RETRY_DELAY_MS = 1500
_SWITCH_RETRY_MAX = 8


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
# 普通拖拽合帧消费节奏：~120Hz（8ms）。高回报率鼠标（125-1000Hz）会在
# 一个显示帧内触发多次 mouseMoveEvent，中间位置屏幕来不及显示；合帧 timer
# 每 tick 只消费最新目标做 self.move，丢弃中间过期位置。
DRAG_MOVE_COALESCE_MS = 8

# ---- 闲置降帧（性能调研 §4.3；批11 联动解码节流）----
# 用户长时间不碰桌宠（无鼠标命中/点击/拖拽/菜单/联动事件）且窗口可见时，
# 动画降帧呈现。批11 前：只省显示不省解码（WebMClip 全速解码 + 窗口按
# 时间线跳帧，24fps 素材播 12fps 效果，动画时长不变）。批11 起：闲置降帧
# 激活时 WebMClip 的消费端 QTimer interval ×divisor、reader 入队由超时丢帧
# 改为有界阻塞（背压）——ffmpeg 解码速率联动下降到 ≈半帧率，被旧过滤器
# 丢弃的帧在 reader 侧就未解码，clip 侧 QImage/QPixmap 零构造（浪费②）。
# 节流路径呈现每帧消费帧（12fps，帧号仍锚定源时间线），时间线推进速率随
# 解码减半（动画时长 ×divisor）——这是解码减半的必然代价。
# 默认闲置阈值 30 秒（配置项 idle_low_fps_threshold）；开关默认关（灰度）。
IDLE_LOW_FPS_DEFAULT_THRESHOLD = 30.0
# 降帧除数：每 N 帧发布 1 帧（2 = 半帧率）。按时间线跳帧（elapsed time 算
# 目标帧），绝不允许改播放速率/QTimer interval 让动画时间变慢/变快。
# 批11：该除数同时是解码节流比率（WebMClip 消费/解码按 1/N 降速）——
# 节流比率可配的预留接口：未来单独配置节流比率时替换 _sync_movie_throttle
# 里的 divisor 来源即可，不硬编码。
IDLE_LOW_FPS_DIVISOR = 2

# ---- 帧缓存素材内容弱指纹（P2）----
# 缓存 key 的 mtime+size 无法识别「同 mtime + 同 size 的原地替换」：复制工具
# 保留 mtime、新文件恰与旧文件等长时，整会话会命中旧成品帧。补一个首尾块
# 内容指纹兜底，但绝不能每帧读文件（key 在 _rebuild_frame 热路径上逐帧计算）。
# 折中：指纹按固定间隔刷新——稳态下每帧只做一次 dict 命中 + monotonic 比较
# （零文件 I/O）；内容被原地替换时最迟一个刷新周期内 key 变化、旧成品失效。
_FRAME_FP_REFRESH_SECS = 2.0
_FRAME_FP_BLOCK = 64  # 头部/尾部各取 64 字节做弱指纹（webm 头尾都含结构信息）


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


def _set_speech_bubble_interactive(pet) -> None:
    """按当前是否可打开快速对话，切换气泡鼠标穿透/可点击。"""
    setter = getattr(pet._speech_bubble, "set_interactive", None)
    if callable(setter):
        setter(callable(getattr(pet, "on_open_quick_chat", None)))


class PetWindow(QWidget):
    """桌宠窗口本体。"""

    look_done = Signal(str, str, bool)
    fullscreen_changed = Signal(bool)  # 全屏 watcher 线程 → 主线程（隐藏/恢复桌宠）
    cursor_visibility_changed = Signal(str)

    def __init__(self, lib: MovieLibrary, config: Config, collision_session=None,
                 broker_facade=None, *, clock=None) -> None:
        super().__init__()
        self.lib = lib
        self.cfg = config
        # P3 broker：PetApp 注入的 BrokerFacade（GUI 线程编排器；默认 None =
        # broker 关，窗口全部 broker 分支 no-op，与历史行为逐位一致）。
        self._broker_facade = broker_facade
        # 已注册的 shareable 会话身份 (name, movie)（终审 P1-2）：收尾按
        # 「注册时的身份」而非「当下开关」——运行期关 collision_enabled 后
        # _broker_shareable() 变 False，若按当下开关判定，收尾会被跳过，
        # 发布 session 残留到 shutdown。
        self._broker_registered: tuple | None = None
        # 首个 idle 是否因 broker 开（等角色就绪 ≤600ms）被延迟：__init__ 置位，
        # attach_collision_session 尾部（角色可查询后）启动轮询。
        self._first_idle_broker_pending = False
        self._broker_first_idle_attempts = 0
        # 闲置降帧的单调时钟（可注入，测试用假时钟控制时间流逝，零抖动）。
        # 注意：只用 time.monotonic 语义的时钟——绝不使用 wall clock。
        self._clock = clock if callable(clock) else time.monotonic
        self._last_activity_ts = self._clock()
        self.idle_low_fps_enabled = bool(config.get('idle_low_fps_enabled', False))
        self.idle_low_fps_threshold = max(
            1.0, min(3600.0, float(config.get('idle_low_fps_threshold',
                                              IDLE_LOW_FPS_DEFAULT_THRESHOLD)))
        )
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
        self._position_sync_pending = False  # moveEvent 同帧合并：气泡/监听器 0ms 去抖待处理
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
        self._self_talk_image_scale = max(0.5, min(3.0, float(config.get('self_talk_image_scale', 100)) / 100.0))
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
        # 动画启动被拒（start() 返回 False）时的回退与重试状态（B7 审查 P1-1）：
        # _pending_switch 记录被拒动画名，_switch_retry_timer 稍后重试；
        # 重试有次数上限，避免病态 reader 永不退出时无限重试。
        # _pending_switch_link 标记待重试是否来自 Agent 联动请求：重试绑定
        # 目标动画身份与请求来源——无关动画的成功切换不吞掉待重试；Agent
        # 回到 idle 只取消联动来源的重试（B7 复审 R2）。
        self._pending_switch: str | None = None
        self._pending_switch_link = False
        self._switch_retry_count = 0
        self._switch_retry_timer = QTimer(self)
        self._switch_retry_timer.setSingleShot(True)
        self._switch_retry_timer.setInterval(_SWITCH_RETRY_DELAY_MS)
        self._switch_retry_timer.timeout.connect(self._on_switch_retry_timeout)
        self._frame_pixmap: QPixmap | None = None
        # 角色可见轮廓（窗口局部坐标）与逐像素命中缓存；贴边功能复用 _mask_bounds
        self._mask_bounds: QRect | None = None
        # 碰撞体稳定边界：当前动画各帧 _mask_bounds 的并集（只增不减，
        # 切换动画/缩放时重置），避免圆链随动画帧缩放跳动导致漏判
        self._collision_local_bounds: QRect | None = None
        self._hit_alpha_image: QImage | None = None
        # 已重建帧的输入签名：movie 身份 + 完整缓存 key（素材路径+mtime+大小、
        # 帧号、朝向、镜像、scale、DPR、动画名）。相同签名重复 rebuild 时整条
        # toImage/镜像/缩放/转换链直接跳过；素材原地替换（mtime/大小变化）使
        # key 不同，快路径同样失效（P1，不得绕过变更检测）。
        self._frame_key: tuple | None = None
        # 当前帧构建时所用的屏幕 DPR：窗口跨屏（moveEvent）时对比新 DPR，
        # 变化即强制 _rebuild_frame，避免旧 DPR 成品继续显示（P1）。
        # 只在重建成功后记账（P1 复审）：失败/快路径跳过时不提前更新，
        # 后续信号/移动仍会按新 DPR 重试。
        self._last_frame_dpr: float | None = None
        # Qt 信号驱动 DPR 变化（P1 复审）：QWindow.screenChanged 与所在屏
        # DPI 变化信号挂到强制 _rebuild_frame（静止窗口也能重建）；
        # showEvent 接线，closeEvent 摘线。
        self._dpr_watch_window = None
        self._dpr_watch_screen = None
        # 预缩放成品帧缓存（方案 A §3.1）：同一动画同一帧在相同
        # (素材路径+mtime+大小+内容弱指纹, 帧号, 朝向, scale, DPR, 动画名)
        # 下结果确定，循环播放直接复用最终 QPixmap，跳过整条 CPU 转换链。
        # 字节预算默认 64MB（可用 frame_cache_max_bytes 配置覆盖），按
        # QPixmap+QImage 双份 ARGB32 记账，超限逐出最久未用（硬上界）；
        # scale/DPR/角色/素材变化（含同 mtime+同 size 的原地替换，见
        # _frame_cache_key / _frame_content_fingerprint）由 key 自动失效。
        try:
            _budget = int(self.cfg.get('frame_cache_max_bytes',
                                       FRAME_CACHE_DEFAULT_MAX_BYTES))
        except (TypeError, ValueError):
            _budget = FRAME_CACHE_DEFAULT_MAX_BYTES
        self._frame_cache = FramePixmapCache(_budget)
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
        # 低优先级预热让路闸门：交互（左键按住/点击动画播放中/右键菜单打开）
        # 期间持有，让 MovieLibrary 的低优先级预热让路；交互结束释放。
        # 状态翻转时才通知库（_set_interaction_hold），避免拖拽高频事件抖动。
        # begin_interaction 返回的 token 存于 _interaction_hold_token，释放时
        # 原样传回：pause_warm 换代后旧 token 的释放是 no-op，配对不被破坏。
        self._interaction_hold_active = False
        self._interaction_hold_token = None
        self._context_menu_open = False
        self._lock_press_active = False   # 锁定位置下左键按住（不拖拽但仍是交互）
        self._click_hold = False          # 点击动画播放中持有让路闸门
        self._closing = False             # closeEvent 后丢弃迟到的动画事件
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
        self._last_collision_sound_at = float('-inf')
        self._press_sound_pair = None
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

        # ---- 普通拖拽合帧 ----
        # 普通拖拽（非物理）的 mouseMoveEvent 只记录最新目标，由 ~120Hz
        # timer 消费最新位置做 self.move；同一显示帧内的中间位置全部丢弃。
        self._drag_move_timer = QTimer(self)
        self._drag_move_timer.setInterval(DRAG_MOVE_COALESCE_MS)
        self._drag_move_timer.timeout.connect(self._consume_drag_move)
        self._drag_move_pending: QPoint | None = None  # 尚未消费的最新拖拽目标

        # ---- 碰撞客户端（组合）----
        # 碰撞会话 attach/detach、状态上报、快照/冲量接收、predicted 本地预测
        # 及碰撞相关状态字段已迁至 CollisionClient（批 6-4）；窗口保留组合与
        # 薄委托，对外行为（碰撞反应、音效、弹开）一丝不变。
        self._collision_app_session = None  # PetApp 持有的 IPC facade（重挂用）
        self._collision_client = CollisionClient(
            self,
            thrown=THROWN,
            dragging=DRAGGING,
            slingshot_aiming=SLINGSHOT_AIMING,
            hit_min_dv=COLLISION_HIT_MIN_DV,
            contact_dv_floor=COLLISION_CONTACT_DV_FLOOR,
        )

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
        if self._broker_delays_first_idle():
            # P3 broker（开）：首个 idle 延迟到「角色就绪或 ≤600ms」——attach
            # 尾部（collision 会话 attach 后）启动轮询再起播。broker 关（默认）
            # 时走 else：与历史逐位相同，构造后立即有动画（大量测试依赖）。
            self._first_idle_broker_pending = True
        else:
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

    # ================================================================ 闲置降帧（性能调研 §4.3）
    def mark_activity(self) -> None:
        """记录一次用户/联动交互，刷新"最近活跃"时刻（单调时钟）。

        闲置降帧的时间线锚点：任何交互都刷新"最近活跃"时刻，下一帧动画
        立即恢复全帧率呈现。

        活跃度判定范围（进入闲置降帧前的计时锚点）：
        - 鼠标：到达桌宠窗口的按下/移动/松手（含点击、拖拽、弹弓瞄准；
          平台穿透 mask 已挡住透明区域，能到达窗口的鼠标事件视为用户注意，
          左右留白收到事件也计入——这是设计选择而非误计）；
        - 键盘：被窗口消费的按键（ESC 取消弹弓等，keyPressEvent）；
        - 失焦取消弹弓（focusOutEvent）；
        - 右键菜单弹出（_show_context_menu）；
        - Agent 联动事件与 request_link_anim；
        - 显示恢复（showEvent）。

        不计入的范围（自动产生的视觉/物理活动，不算用户活跃）：
        - 自动动画链/自动移动/物理抛掷/碰撞反弹/弹弓物理 tick
          ——否则桌宠持续自动活动将永不进入降帧；
        - 未到达窗口或被窗口忽略的输入。
        """
        self._last_activity_ts = self._clock()
        # 任何交互立刻回满帧率：同步把解码节流关掉（幂等，非 WebMClip /
        # 本就未节流时为 no-op），不等下一帧 _on_frame 才恢复（响应更快）。
        self._sync_movie_throttle(False)

    def _agent_busy(self) -> bool:
        """Agent 联动忙碌（dsh 等正在干活）视为活跃，不降帧。"""
        mgr = getattr(self, 'agent_link_manager', None)
        if mgr is None:
            return False
        any_busy = getattr(mgr, 'any_busy', None)
        return bool(any_busy()) if callable(any_busy) else False

    def _idle_reduction_active(self) -> bool:
        """闲置降帧门控：开关开 + 窗口可见 + 超过闲置阈值 + 无活跃按压/菜单 + Agent 不忙。

        隐藏/不可见时维持现有全停语义（_hidden_paused 时动画本就停着），
        这里返回 False 表示不额外降帧；按住/菜单打开/Agent 干活都算活跃。
        """
        if not self.idle_low_fps_enabled:
            return False
        if self._hidden_paused or not self.isVisible():
            return False
        if self._press_global is not None or self._context_menu_open:
            return False
        if self._agent_busy():
            return False
        return self._clock() - self._last_activity_ts >= self.idle_low_fps_threshold

    @staticmethod
    def _is_reduced_publish_frame(frame_index: int, divisor: int = IDLE_LOW_FPS_DIVISOR) -> bool:
        """闲置降帧的隔帧发布判定（按源时间线跳帧）。

        frame_index = 素材源时间线上的 0-based 显示帧索引（= elapsed video
        time × fps；由播放器按源时间线打标，reader 队列满丢帧后仍一致，
        与主线程消费序号无关——P1 复审）。目标呈现帧 =
        floor(elapsed×fps/divisor)×divisor，即帧号能被 divisor 整除的帧才
        发布（24fps 素材 → 12fps 效果）。

        批11 适用范围收窄：本判定只用于**解码未联动节流**的播放器
        （GifClip、测试替身等不支持 set_decode_throttle 的 movie）——
        它们仍全速解码、靠这里跳帧省显示。WebMClip 的闲置降帧走
        _sync_movie_throttle 联动（消费端 interval ×divisor + reader 背压
        阻塞，解码速率 ≈半帧率）：消费端已按 divisor 降速，每帧都是目标
        呈现帧，不再经过本判定（否则会把已减半的流再砍一半成 6fps）。
        """
        return int(frame_index) % max(1, int(divisor)) == 0

    def _movie_decode_throttled(self) -> bool:
        """当前 movie 的解码节流是否生效（WebMClip 联动后 divisor > 1）。"""
        movie = self.movie
        if movie is None:
            return False
        return getattr(movie, 'decode_throttle_divisor', 1) > 1

    def _sync_movie_throttle(self, reduced: bool) -> None:
        """闲置降帧 → 解码节流联动（批11）：把节流比率推到当前 movie。

        reduced=True 时推 IDLE_LOW_FPS_DIVISOR（默认 2，可配接口——
        未来若给节流比率单独配置，改这里一处即可，不硬编码）；False 推 1
        恢复全速。只对暴露 set_decode_throttle 的播放器（WebMClip）生效，
        GifClip / 测试替身自动跳过；比率未变时幂等 no-op（每帧同步调用
        的成本仅一次 int 比较）。必须在 GUI 线程调用（触碰 movie 的 QTimer）。
        """
        movie = self.movie
        if movie is None:
            return
        setter = getattr(movie, 'set_decode_throttle', None)
        if setter is None:
            return
        divisor = IDLE_LOW_FPS_DIVISOR if reduced else 1
        if getattr(movie, 'decode_throttle_divisor', 1) != divisor:
            setter(divisor)

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

    def screen_available(self, screen_name: str | None = None):
        """公开转发：返回指定或窗口所在屏幕（等价 _screen_available）。"""
        return self._screen_available(screen_name)

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

    def save_position(self) -> None:
        """公开转发：以窗口中心相对屏幕可用区的比例持久化位置（等价 _save_position）。"""
        self._save_position()

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

    def go_default_corner(self) -> None:
        """公开转发：手动回到右下角（等价 _go_default_corner）。"""
        self._go_default_corner()

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
        # 原生窗口此刻已就绪：接线 DPR 变化信号（跨屏/显示缩放 → 强制重建）。
        # 幂等；QWindow 被重建后再次 show 会重挂到新 handle。
        self._arm_dpr_change_watch()
        self._submit_collision_state(force=True)
        self._schedule_macos_window_level(bool(self.cfg.get('on_top', True)))
        self._apply_opacity()
        # 隐藏期暂停的活动在此恢复（与 hide() 中的 _pause_activity 配对）
        if self._hidden_paused:
            self._hidden_paused = False
            self._phys_vel[:] = [0.0, 0.0]
            self._resume_activity()
            self._submit_collision_state(force=True)
            # 恢复显示 = 用户重新看着桌宠：重置闲置计时，重新以全帧率呈现
            # （只有再闲置 idle_low_fps_threshold 秒才进入降帧）
            self.mark_activity()
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
            # P3 broker：窗口停播（隐藏/暂停）→ shareable idle 会话中止
            # （publish_abort/subscribe_end，消费端本地回退）；broker 关 = no-op。
            self._broker_unregister(self.anim, self.movie, natural=False)
        # 隐藏期间不重试被拒动画：停掉待重试并清空状态（恢复显示时重新切换）
        self._cancel_pending_switch_retry()
        self._move_timer.stop()
        self._physics_timer.stop()
        self._drag_move_timer.stop()
        self._drag_move_pending = None
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
        # 交互让路闸门随隐藏对称释放（库侧 pause_warm 已换代清零时 end 是
        # no-op；无 pause_warm 的库则真正 end 配对，避免库侧计数泄漏）；
        # 按住状态一并复位（含闸门释放），恢复显示后由 _switch →
        # _update_interaction_hold 重新同步。
        self._reset_press_hold_state()
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
        else:
            # 首个 idle 被 broker 延迟（等角色）期间窗口被隐藏：恢复显示时
            # 若仍未起播则重新武装轮询（broker 关时无挂起 = no-op）。
            self._broker_arm_first_idle_if_pending()
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
        self._collision_client.attach(session)
        # P3 broker：attach 尾部 bind —— 把注入且启用的 BrokerFacade 绑到本窗口
        # attach 的会话（先 unbind 旧会话再 bind 新会话，幂等；decode 转发/角色
        # 镜像随会话走）。broker 关/facade 缺席 = no-op（逐位不变）。
        facade = getattr(self, '_broker_facade', None)
        if facade is not None and bool(getattr(facade, 'enabled', False)):
            facade.unbind()
            facade.bind(session)
        # P3 broker：首个 idle 若因 broker 开被延迟（等角色就绪 ≤600ms），
        # 在此启动轮询；broker 关/无挂起 = no-op（逐位不变）。
        self._broker_arm_first_idle_if_pending()

    # ---- P3 broker：窗口侧接线（只经 BrokerFacade 公开接口）----------------
    def _broker_active(self) -> bool:
        """broker 是否参与本窗口：facade 注入且启用，且 collision 开
        （broker 骑在碰撞 QLocal 通道上，设计 §3.1）。默认关 = False。"""
        facade = getattr(self, '_broker_facade', None)
        if facade is None or not bool(getattr(facade, 'enabled', False)):
            return False
        return bool(self.cfg.get('collision_enabled', True))

    def _broker_shareable(self, name) -> bool:
        """可共享判定（设计 §3.1）：name ∈ self.idles（列表成员测试）且开关开。"""
        return self._broker_active() and name in self.idles

    def _broker_register(self, name, movie) -> None:
        """shareable movie 即将 start() 前调用：按当时角色（is_coordinator）
        经 facade 分流 publish_start/subscribe_start。失败不影响本地播放。

        终审 P1-2：注册成功（非 'local'）时记录身份 (name, movie)，收尾
        （_broker_unregister）按身份执行，不再依赖当下开关状态。注册前若
        残留上一次未收尾的注册（异常路径），先按身份收尾再注册新轮。"""
        if not self._broker_shareable(name):
            return
        facade = self._broker_facade
        if self._broker_registered is not None:
            prev_name, prev_movie = self._broker_registered
            if prev_name != name or prev_movie is not movie:
                self._broker_unregister(prev_name, prev_movie, natural=False)
        try:
            role = facade.shareable_start(name, movie)
        except Exception:
            logging.exception('broker shareable_start 异常，回退本地: %s', name)
            return
        if role != 'local':
            self._broker_registered = (name, movie)

    def _broker_unregister(self, name, movie, natural: bool) -> None:
        """shareable movie 停播/自然播完：通知 facade 解注册。

        natural=True = 自然播完（run_ended_natural 广播）；False = 停播/切走
        （publish_abort / subscribe_end）。幂等；broker 关时 no-op。

        终审 P1-2（=DS 终审 P2-1）：收尾资格看「本窗口是否注册过该
        (name, movie)」，不看当下 _broker_shareable()——运行期关闭
        collision_enabled 或 detach 后开关已变，按当下判定会让已建立的
        发布 session/订阅永远收不到收尾。"""
        registered = self._broker_registered
        if registered is None:
            return
        reg_name, reg_movie = registered
        if reg_name != name or reg_movie is not movie:
            return
        self._broker_registered = None
        facade = getattr(self, '_broker_facade', None)
        if facade is None:
            return
        try:
            facade.shareable_end(name, movie, natural=natural)
        except Exception:
            logging.exception('broker shareable_end 异常: %s', name)

    def _broker_delays_first_idle(self) -> bool:
        """首个 idle 是否延迟决策：broker 开且首个素材可共享（idle 类）。
        返回 True 时 __init__ 不立即 _switch(self.idle)，改由 attach 尾部
        等「角色就绪或 ≤600ms」后起播（设计 P0-2）。"""
        if not self._broker_active():
            return False
        return self.idle in self.idles

    def _broker_arm_first_idle_if_pending(self) -> None:
        if not getattr(self, '_first_idle_broker_pending', False):
            return
        facade = getattr(self, '_broker_facade', None)
        if facade is None:
            self._first_idle_broker_pending = False
            self._switch(self.idle)  # 兜底：无 facade 也照常起播
            return
        self._broker_first_idle_attempts = 0
        QTimer.singleShot(50, self, self._broker_first_idle_tick)

    def _broker_first_idle_tick(self) -> None:
        """50ms×12 轮询角色就绪（≤600ms）：就绪即按角色起播首个 idle；
        超时也起播（facade.role_known=False → shareable_start 回退本地，
        下一轮再按新角色决策）。"""
        if getattr(self, '_closing', False):
            self._first_idle_broker_pending = False
            return
        if self._hidden_paused:
            # 隐藏期间不强行起播、不空转轮询：保持挂起；恢复显示时
            # _resume_activity 重新武装（角色就绪后由同一轮询补起首个 idle）。
            return
        facade = self._broker_facade
        role_known = bool(facade is not None and facade.role_known())
        self._broker_first_idle_attempts += 1
        if role_known or self._broker_first_idle_attempts >= 12:
            self._first_idle_broker_pending = False
            if self.movie is None:
                # 等待期内用户/其它路径已起播（_switch 已置 movie）则不覆盖；
                # 否则首个 idle 起播（role_known → 按角色发布/订阅；超时 →
                # shareable_start 内 role 未定回退本地）。
                self._switch(self.idle)
            return
        QTimer.singleShot(50, self, self._broker_first_idle_tick)

    def detach_collision_session(self) -> None:
        """解绑碰撞会话：发 leave、断开信号、停定时器并清空客户端预测状态。

        P3 broker（P3A P2-2 + 终审 P1-2）：解绑 = broker teardown——先按
        注册身份收尾当前 movie 的 broker 会话（unbind 只断信号/作废
        pending，不中止发布 session；不先收尾则运行期关碰撞后发布记录
        残留到 shutdown），再 facade.unbind()，最后摘掉当前 movie 的
        发布/订阅钩子，避免 broker 停用期间复用旧 clip（同素材重播/回退）
        时误用上一轮的 sink/feed。正在 stream 的 feed 由 movie 的 stop/
        自然结束收尾（reader 的 finally 必 close feed session），此处不
        打断播放。
        """
        self._collision_client.detach()
        movie = getattr(self, 'movie', None)
        if movie is not None:
            self._broker_unregister(self.anim, movie, natural=False)
        facade = getattr(self, '_broker_facade', None)
        if facade is not None:
            try:
                facade.unbind()
            except Exception:
                pass
        if movie is not None:
            try:
                movie._publish_sink = None
            except Exception:
                pass
            try:
                movie._feed_source = None
            except Exception:
                pass

    def _sync_collision_policy(self) -> None:
        """把当前配置的碰撞参数同步到会话 policy，运行中改动即时生效。"""
        self._collision_client._sync_collision_policy()

    # ---- 碰撞客户端委托（逻辑与状态已迁至 pet/collision_client.py 批 6-4）----
    # 以下方法/属性仅为保持窗口既有调用面与测试断言不变而保留的薄委托；
    # 任何碰撞数值路径都只存在于 CollisionClient，窗口不再持有碰撞字段。

    @property
    def _collision_session(self):
        return self._collision_client.session

    @_collision_session.setter
    def _collision_session(self, value):
        self._collision_client.session = value

    @property
    def collision_app_session(self):
        """PetApp 持有的 IPC facade（只读 seam，供 CollisionClient 策略兜底同步）。"""
        return self._collision_app_session

    @property
    def _collision_timer(self):
        return self._collision_client.timer

    @property
    def _collision_last_submit_at(self) -> float:
        return self._collision_client.last_submit_at

    @_collision_last_submit_at.setter
    def _collision_last_submit_at(self, value: float) -> None:
        self._collision_client.last_submit_at = value

    @property
    def _collision_peer_snapshots(self) -> dict[str, dict[str, Any]]:
        return self._collision_client.peer_snapshots

    @_collision_peer_snapshots.setter
    def _collision_peer_snapshots(self, value) -> None:
        self._collision_client.peer_snapshots = value

    @property
    def _predicted_bounces(self) -> dict[str, float]:
        return self._collision_client.predicted_bounces

    @_predicted_bounces.setter
    def _predicted_bounces(self, value) -> None:
        self._collision_client.predicted_bounces = value

    @property
    def _collision_epoch(self) -> str:
        return self._collision_client.epoch

    @_collision_epoch.setter
    def _collision_epoch(self, value: str) -> None:
        self._collision_client.epoch = value

    @property
    def _pending_predicted_bounce(self):
        return self._collision_client.pending_predicted_bounce

    @_pending_predicted_bounce.setter
    def _pending_predicted_bounce(self, value) -> None:
        self._collision_client.pending_predicted_bounce = value

    @property
    def _pending_predicted_contact(self):
        return self._collision_client.pending_predicted_contact

    @_pending_predicted_contact.setter
    def _pending_predicted_contact(self, value) -> None:
        self._collision_client.pending_predicted_contact = value

    @property
    def _last_collision_squash_at(self) -> float:
        return self._collision_client.last_collision_squash_at

    def _submit_collision_state(self, force: bool = False) -> None:
        client = getattr(self, '_collision_client', None)
        if client is None:
            return
        client._submit_collision_state(force=force)

    def _on_collision_impulse(self, message: dict[str, Any]) -> None:
        self._collision_client._on_collision_impulse(message)

    def _prune_collision_prediction_state(self, now: float) -> None:
        self._collision_client._prune_collision_prediction_state(now)

    def _collision_velocity(self) -> tuple[float, float]:
        return self._collision_client._collision_velocity()

    def _collision_flags(self) -> int:
        return self._collision_client._collision_flags()

    @staticmethod
    def _fullscreen_geometry_hit(l: float, t: float, r: float, b: float,
                                 geom, has_caption: bool, topmost: bool = False) -> bool:
        """覆盖整屏几何，且（无标题栏 或 置顶）= 真全屏。

        实现已搬至 pet/platform_win.py（批 6-3），此处为兼容性薄委托。
        """
        return platform_win._fullscreen_geometry_hit(
            l, t, r, b, geom, has_caption, topmost)

    # ------------------------------------------------------------------
    # 全屏 watcher：后台线程轮询（纯 win32，线程安全）+ 信号回主线程
    # ------------------------------------------------------------------
    def _fg_fullscreen_win32(self) -> bool:
        """前台窗口是否真全屏。仅返回布尔值，诊断细节见 _fg_fullscreen_probe。

        实现已搬至 pet/platform_win.py（批 6-3），此处为兼容性薄委托。
        """
        return platform_win._fg_fullscreen_win32()

    @staticmethod
    def _fs_user_busy_state() -> tuple[bool, int]:
        """SHQueryUserNotificationState：Windows 自报的全屏/演示忙状态。

        实现已搬至 pet/platform_win.py（批 6-3），此处为兼容性薄委托。
        """
        return platform_win._fs_user_busy_state()

    def _fg_fullscreen_probe(self) -> tuple[bool, str]:
        """前台窗口全屏探测，返回 (是否全屏, 诊断描述)。

        实现已搬至 pet/platform_win.py（批 6-3），此处为兼容性薄委托。
        """
        return platform_win._fg_fullscreen_probe()

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

    def _switch(self, name: str, _link_request: bool = False) -> bool:
        """切换到指定动画（链式模型：全部一次性播放）。

        若目标动画启动被拒绝（movie.start() 返回 False，如退役 reader 卡死），
        执行明确降级（_switch_fallback）：回退到上一个可播放动画/待机并安排
        稍后重试——绝不留下「anim 已切换但 movie 未在播」的停滞态
        （B7 审查 P1-1）。

        返回 bool：True=目标动画已实际启动；False=被拒绝并已降级。调用方
        （尤其移动路径 _try_move）必须据返回值决定是否继续，绝不能把
        「anim 已切换」当成「播放已成功」（B7 复审 R2）。

        _link_request：本次请求是否来自 Agent 联动（决定失败重试的取消语义，
        B7 复审 R2：Agent 回到 idle 时只取消联动来源的待重试）。
        """
        self._cancel_move()
        prev_anim = self.anim
        prev_movie = self.movie
        prev_click_hold = self._click_hold
        prev_bounds = self._collision_local_bounds
        # P3 broker：离开上一个可共享素材（idle 类）时通知 facade 解注册——
        # 自然播完（_ended_fired=True，末帧已处理）→ run_ended_natural；
        # 打断/切走（仍播放中）→ publish_abort/subscribe_end（消费端本地回退）。
        # 终审 P1-2：不按当下 _broker_shareable() 门控——运行期关碰撞后开关
        # 已变，门控会让已注册的会话收不到收尾；是否收尾由 _broker_unregister
        # 按注册身份判定。
        if prev_movie is not None:
            self._broker_unregister(prev_anim, prev_movie,
                                    natural=bool(self._ended_fired))

        self.anim = name
        # 点击回应动画播放中持有让路闸门；切到非点击动画即视为点击结束。
        # （单一事实来源：_click_hold 随当前 anim 同步，覆盖所有切换路径，
        # 避免"点击动画被拖拽/移动打断后 _click_hold 残留"导致闸门泄漏。）
        self._click_hold = name in self.clicks
        self._collision_local_bounds = None
        movie = self.lib.movie(name)
        self._connect_movie(name, movie)
        self.movie = movie
        # 批11：切换动画时按当前门控同步解码节流（在 start() 之前——start
        # 里会用 _timer_interval 重设 QTimer interval）。clip 实例被库缓存
        # 复用，上次播放遗留的 divisor 必须在此对齐当前门控，否则切到闲置
        # 动画时可能以错误的节流状态开播最多一帧。
        self._sync_movie_throttle(self._idle_reduction_active())
        movie.stop()
        movie.jumpToFrame(0)
        if hasattr(movie, 'set_playback_speed'):
            movie.set_playback_speed(self.playback_speed)
        self._ended_fired = False
        self._rebuild_frame()
        # P3 broker：shareable（idle 类）素材 start() 前注册——发布/订阅由
        # facade 依当时角色（is_coordinator）分流；非 shareable/关 = no-op。
        self._broker_register(name, movie)
        if movie.start() is False:
            # 启动被拒：先撤销刚注册的 broker 会话（movie 未真正起播），再降级
            self._broker_unregister(name, movie, natural=False)
            self._switch_fallback(
                prev_anim, prev_movie, prev_click_hold, prev_bounds, name,
                is_link=_link_request,
            )
            return False
        # 启动成功：仅当待重试的正是本动画时才清除待重试状态——重试绑定
        # 目标动画身份，无关动画的成功切换不得吞掉其他动画的待重试
        # （B7 复审 R2）。
        if self._pending_switch == name:
            self._pending_switch = None
            self._pending_switch_link = False
            self._switch_retry_count = 0
            self._switch_retry_timer.stop()
        self._submit_collision_state(force=True)
        # 动画切换是让路闸门的唯一事实来源之一：点击动画开始播放时持有、
        # 播完（_on_anim_ended 切走）时释放，覆盖所有早期返回路径。
        self._update_interaction_hold()
        return True

    def switch_clip(self, name: str, link_request: bool = False) -> bool:
        """公开转发：切换到指定动画（等价 _switch）。"""
        return self._switch(name, _link_request=link_request)

    def _switch_fallback(self, prev_anim: str, prev_movie, prev_click_hold: bool,
                         prev_bounds, requested: str, is_link: bool = False) -> None:
        """动画启动被拒绝时的明确降级：恢复可播放状态 + 安排稍后重试。

        优先恢复上一动画（其 clip 仍在播则原样继续，已停则从首帧重播）；
        上一动画不可用（无上一动画、或与目标同 clip 同样被拒）时回退到
        可播放 idle。两种回退都保证 pet 有画面在动；极端情况（idle 也被拒）
        保留最后渲染帧并释放点击/交互 hold，交给重试路径在 reader 可回收后
        恢复播放——绝不允许无声无息地停在停滞态。
        """
        logging.warning('动画启动被拒绝，回退可播放动画并安排重试: %s', requested)
        restored = False
        registered_prev = False
        if prev_movie is not None:
            # P3 broker：回退上一动画并（重新）起播。若上一素材可共享且其 clip
            # 当前未在播（自然播完/被停后重播 = 新一轮），start() 会拉起新 reader
            # → start() 前按当前角色注册发布/订阅；仍在播则 start() 为 no-op
            # （其会话已在 _switch 顶部按自然/中止收尾），不必重复注册。
            if (self._broker_shareable(prev_anim)
                    and not getattr(prev_movie, '_running', False)):
                self._broker_register(prev_anim, prev_movie)
                registered_prev = True
            restored = prev_movie.start() is not False
            if not restored and registered_prev:
                # 重播也被拒（病态退役池）：撤销刚注册的会话，交给 idle 回退
                self._broker_unregister(prev_anim, prev_movie, natural=False)
        if restored:
            self.anim = prev_anim
            self._click_hold = prev_click_hold
            self._collision_local_bounds = prev_bounds
            # 上一动画已被重播：播完时须再次走 _on_anim_ended 推进动画链
            self._ended_fired = False
            self.movie = prev_movie
            self._rebuild_frame()
            self._submit_collision_state(force=True)
            self._update_interaction_hold()
        else:
            self._fallback_playable_idle(requested)
        self._schedule_switch_retry(requested, is_link=is_link)

    def _fallback_playable_idle(self, requested: str) -> None:
        """回退到可播放 idle（不同 clip 实例，通常不受同一退役池影响）。

        idle 也拒绝启动（极端：其自身退役池同样卡死）时，保留最后渲染帧并
        释放点击/交互 hold，交给重试路径在 reader 可回收后恢复播放。
        """
        if not self.idles:
            self._click_hold = False
            self._update_interaction_hold()
            return
        idle_name = self._pick(self.idles, exclude=requested)
        movie = self.lib.movie(idle_name)
        self._connect_movie(idle_name, movie)
        self.anim = idle_name
        self._click_hold = idle_name in self.clicks  # idle 不在 clicks → 释放
        self._collision_local_bounds = None
        self._ended_fired = False
        self.movie = movie
        # 批11：idle 回退同样按当前门控对齐解码节流（见 _switch 同名调用）。
        self._sync_movie_throttle(self._idle_reduction_active())
        movie.stop()
        movie.jumpToFrame(0)
        if hasattr(movie, 'set_playback_speed'):
            movie.set_playback_speed(self.playback_speed)
        self._rebuild_frame()
        # P3 broker：回退到可共享 idle 起播前注册（按当前角色分流）。
        self._broker_register(idle_name, movie)
        if movie.start() is False:
            # 极端：idle 也被拒——保留最后渲染帧，释放 hold，等重试恢复
            logging.warning('idle 回退也被拒绝（其退役池卡死）: %s', idle_name)
            self._broker_unregister(idle_name, movie, natural=False)
            self._click_hold = False
            self._update_interaction_hold()
            return
        self._submit_collision_state(force=True)
        self._update_interaction_hold()

    def _schedule_switch_retry(self, requested: str, is_link: bool = False) -> None:
        """安排稍后重试被拒绝的动画（有次数上限，病态 reader 永不退出时放弃）。

        is_link 标记请求来源（Agent 联动）：联动重试随 Agent 回到 idle /
        新联动请求取消（B7 复审 R2）。
        """
        self._pending_switch = requested
        self._pending_switch_link = is_link
        if self._switch_retry_count >= _SWITCH_RETRY_MAX:
            logging.warning('动画启动重试已达上限，放弃稍后重试: %s', requested)
            self._pending_switch = None
            self._pending_switch_link = False
            self._switch_retry_count = 0
            return
        self._switch_retry_count += 1
        self._switch_retry_timer.start()

    def _cancel_pending_switch_retry(self) -> None:
        """取消待重试动画（B7 复审 R2）：停表并清空待重试状态。

        用于：窗口隐藏（pause_activity）、Agent 回到 idle（联动来源）、
        新联动请求覆盖旧联动重试。
        """
        self._switch_retry_timer.stop()
        self._pending_switch = None
        self._pending_switch_link = False
        self._switch_retry_count = 0

    def _on_switch_retry_timeout(self) -> None:
        """重试被拒绝的动画；窗口已隐藏/关闭则不重试。"""
        requested = self._pending_switch
        is_link = self._pending_switch_link
        self._pending_switch = None
        self._pending_switch_link = False
        self._switch_retry_timer.stop()  # 本次重试已执行，单次计时器任务结束
        if requested is None:
            return
        if self._hidden_paused or getattr(self, '_closing', False):
            self._switch_retry_count = 0
            return
        self._switch(requested, _link_request=is_link)

    # ---- Agent 联动动作平滑衔接 ----
    def _is_one_shot_playing(self) -> bool:
        """当前是否正在播一次性动作（动作池/点击回应/移动）。待机/转向可立即切换。"""
        return self.anim in self.acts or self.anim in self.clicks or self.anim in self.moves

    def request_link_anim(self, name: str) -> None:
        """Agent 联动动作请求：一次性动作播放中不打断，存为待播（最新覆盖旧的）。

        B7 复审 R2：新的联动请求覆盖旧的联动失败重试（同一请求流，最新
        覆盖旧的），避免旧重试在 1.5s 后顶掉新联动动作。
        """
        # 联动请求 = 联动事件：刷新闲置降帧的活跃锚点
        self.mark_activity()
        if self._pending_switch_link:
            self._cancel_pending_switch_retry()
        self._pending_link_anim = name
        if not self._is_one_shot_playing():
            self._play_pending_link_anim()

    def _play_pending_link_anim(self) -> None:
        name = self._pending_link_anim
        self._pending_link_anim = None
        if not name:
            return
        self._link_anim_current = name
        self._switch(name, _link_request=True)

    def request_link_idle(self) -> None:
        """Agent 回到空闲：取消待播联动；一次性动作让它播完自然回待机，否则立即回待机。

        B7 复审 R2：联动动画失败安排的稍后重试一并取消（Agent 已 idle 的
        过期动作不得在 1.5s 后突然重播）；非联动来源的待重试不受影响。
        """
        self._pending_link_anim = None
        self._link_anim_current = None
        if self._pending_switch_link:
            self._cancel_pending_switch_retry()
        if self._is_one_shot_playing():
            return
        if self.idles:
            self._switch(self._pick(self.idles))

    def set_link_next_provider(self, value) -> None:
        """公开转发：注入联动动作链"下一个动作提供者"回调（等价 _link_next_provider 赋值）。"""
        self._link_next_provider = value

    def clear_pending_link_anim(self) -> None:
        """公开转发：清除待播联动动作（等价 _pending_link_anim = None）。"""
        self._pending_link_anim = None

    def _on_frame(self, name: str, n: int) -> None:
        """媒体帧推进回调：重建画面；最后一帧触发播完处理。

        n = 素材源时间线上的 0-based 显示帧索引（WebMClip/GifClip 统一契约，
        由播放器按源时间线打标，队列满丢帧后仍一致——P1 复审）。降帧相位
        与末帧判断都以此为准，绝不使用主线程消费序号。
        """
        if self._hidden_paused or getattr(self, '_closing', False):
            # 隐藏/关闭/切角色后丢弃迟到的动画事件：旧窗口不得再推进动画链、
            # 不得对旧库重新建立交互让路 hold（生命周期守卫）。
            return
        if name != self.anim or self.movie is None:
            return
        is_last = n >= self.lib.frames(name) - 1  # n 是 0-based 源帧号：末帧判定不提前
        reduced = self._idle_reduction_active()
        # 批11 解码节流联动：把当前门控状态推给 movie（WebMClip 消费端
        # interval ×divisor + reader 背压阻塞，解码速率 ≈半帧率）。推送先于
        # 发布判定：WebMClip 在门控生效的那一帧起即按节流语义发布（见下）。
        self._sync_movie_throttle(reduced)
        if (reduced and not self._movie_decode_throttled()
                and not self._is_reduced_publish_frame(n)):
            # 闲置降帧（解码未联动节流的播放器：GifClip / 测试替身）：
            # 按时间线跳帧呈现（24fps 素材 → 12fps 效果），本帧不发布——
            # 命中测试继续使用最近一次已发布的 alpha 图，不逐帧重建。
            # WebMClip 节流路径消费端已按 divisor 降速、每帧都是目标呈现
            # 帧，不经过本判定（否则会把已减半的流再砍一半成 6fps）；
            # 帧号锚定（显示帧索引 = 源时间线）在两路径都不变。
            # 末帧的动画链推进绝不能因跳帧而丢（否则停在最后一帧）。
            if is_last and not self._ended_fired:
                self._ended_fired = True
                self.movie.stop()
                self._on_anim_ended(name)
            return
        self._rebuild_frame()
        self.update()
        if is_last and not self._ended_fired:
            self._ended_fired = True
            self.movie.stop()  # 停在最后一帧，等 _on_anim_ended 切走
            self._on_anim_ended(name)

    def _frame_cache_key(self, frame_n: int | None, dpr: float) -> tuple:
        """预缩放缓存的 key：素材路径+mtime+大小+内容弱指纹、帧号、朝向、动画名、scale、DPR。

        任意一项变化都会命中不同条目（scale/DPR/角色切换/素材文件变化
        由此自动失效）；镜像决策（facing + no_mirror）也进 key，避免
        文字动画朝右与朝左共用条目。mtime 之外再记 st_size：复制工具
        保留 mtime 时，内容大小变化仍能失效（P1）。同 mtime+同 size 的
        原地替换靠首尾块弱指纹兜底（_frame_content_fingerprint，固定
        间隔刷新），把「整会话显示旧帧」收窄到最多一个刷新周期（P2）。
        该 key 同时是 _rebuild_frame 快路径签名的一部分：素材原地替换
        （mtime/大小/指纹任一变化）时快路径同样失效，不会绕过变更检测。
        """
        path: str | None = None
        mtime = 0
        size = 0
        fp = 0
        path_getter = getattr(self.lib, 'clip_path', None)
        if callable(path_getter):
            try:
                clip_path = path_getter(self.anim)
            except Exception:
                clip_path = None
            if clip_path is not None:
                try:
                    path = os.fspath(clip_path)
                except TypeError:
                    path = str(clip_path)
                try:
                    # 素材文件变更（mtime/大小/内容指纹变化）必须失效旧成品
                    st = os.stat(clip_path)
                    mtime = st.st_mtime_ns
                    size = st.st_size
                    fp = self._frame_content_fingerprint(path, mtime, size)
                except OSError:
                    mtime = 0
                    size = 0
                    fp = 0
        if path is None:
            # 无 clip_path 的库（测试桩）：退化为 clip 实例身份，保证不串帧
            path = '<clip:%d>' % id(self.movie)
        mirrored = bool(
            self.facing == 'right'
            and self.anim not in getattr(self.lib, 'no_mirror', frozenset())
        )
        return (path, mtime, size, fp, frame_n, self.facing, mirrored,
                self.scale, dpr, self.anim)

    def _frame_content_fingerprint(self, path: str, mtime: int, size: int) -> int:
        """素材内容弱指纹（首尾块）：同 mtime + 同 size 的原地替换也能失效。

        只读文件头部与尾部各 _FRAME_FP_BLOCK 字节做 hash；读整文件在 GUI
        线程热路径上不可接受。指纹按 _FRAME_FP_REFRESH_SECS 间隔刷新：
        - 稳态（同一素材连续重建、元数据未变、上次检查未过期）：dict 命中 +
          monotonic 比较，零文件 I/O；
        - 元数据已变（mtime/size 任一不同）或检查过期：重读首尾块刷新记录。
        内容被原地替换但元数据未变时，最迟一个刷新周期内 key 变化、旧缓存
        条目失效（当前实现替换后至多 2s 内自愈；替换发生瞬间读不到文件则
        退回指纹 0，key 变化触发重建，同样自愈）。
        """
        now = time.monotonic()
        table = getattr(self, '_frame_fp', None)
        if table is None:
            table = self._frame_fp = {}
        rec = table.get(path)
        if (rec is not None and rec[0] == mtime and rec[1] == size
                and now - rec[2] < _FRAME_FP_REFRESH_SECS):
            return rec[3]
        fp = 0
        try:
            with open(path, 'rb') as f:
                head = f.read(_FRAME_FP_BLOCK)
                tail_start = max(0, size - _FRAME_FP_BLOCK)
                f.seek(tail_start)
                tail = f.read(_FRAME_FP_BLOCK)
            fp = hash((head, tail))
        except OSError:
            fp = 0  # 读不到（替换瞬间被独占/删除）：退回 0，key 变化触发重建，自愈
        table[path] = (mtime, size, now, fp)
        return fp

    def _rebuild_frame(self) -> None:
        """重建当前帧：缩放 + 朝向镜像 + 生成窗口 mask。

        帧内容由（素材路径+mtime+大小、帧号、朝向、动画、缩放、DPR）唯一确定。
        两级复用：
        - 同一 movie 同一帧重复 rebuild（_frame_key 相同）：整条链直接跳过；
        - 动画循环回到已构建过的帧：命中预缩放缓存（FramePixmapCache），
          复用最终 QPixmap 与命中测试 alpha 图，跳过 toImage→镜像→预乘→
          Smooth 缩放→ARGB32→fromImage 整条 CPU 链（方案 A §3.1）。
        """
        if self.movie is None:
            return
        if perfstats.ENABLED:
            _rf_t0 = perfstats.clock()
            perfstats.note('rebuild.calls')
        scr = self._screen_available()
        dpr = scr.devicePixelRatio() if scr is not None else 1.0
        # 注意：_last_frame_dpr 只在重建成功后记账（命中缓存也算成功）；
        # 失败路径（解码返回空图）与快路径跳过均不更新，避免把「未按新
        # DPR 重建」记成已重建（P1 复审）。
        try:
            # currentFrameNumber = 0-based 源时间线显示帧索引（P1 复审）：
            # 缓存 key 锚定素材真实帧号，队列满丢帧后不会把不同源帧
            # 的成品误串（消费计数与源帧号在丢帧后不再相等）。
            frame_n = self.movie.currentFrameNumber()
        except AttributeError:
            frame_n = None
        # 快路径签名 = movie 身份 + 完整缓存 key：素材 mtime/大小变化会改变
        # 缓存 key，快路径随之失效——快路径不得绕过素材变更检测（P1）。
        cache_key = self._frame_cache_key(frame_n, dpr)
        key = (id(self.movie), cache_key)
        if key == getattr(self, '_frame_key', None):
            if perfstats.ENABLED:
                perfstats.note('rebuild.skip')
                perfstats.time('rebuild.total', perfstats.clock() - _rf_t0)
            return
        cache = getattr(self, '_frame_cache', None)
        if cache is None:
            cache = FramePixmapCache(
                getattr(self, '_frame_cache_max_bytes', FRAME_CACHE_DEFAULT_MAX_BYTES)
            )
            self._frame_cache = cache
        entry = cache.get(cache_key)
        if entry is not None:
            if perfstats.ENABLED:
                perfstats.note('frame_cache.hit')
            # 命中：直接复用最终 pixmap 与命中测试 alpha 图。两者出自同一
            # 缓存条目，_hit_alpha_image 与 _frame_pixmap 永远逐像素一致。
            self._frame_pixmap = entry.pixmap
            self._hit_alpha_image = entry.image
            self._frame_key = key
            self._last_frame_dpr = dpr
            self._sync_mask()
            if perfstats.ENABLED:
                perfstats.time('rebuild.total', perfstats.clock() - _rf_t0)
            return
        if perfstats.ENABLED:
            perfstats.note('frame_cache.miss')
        pm = self.movie.currentPixmap()
        if pm is None or pm.isNull():
            # ffmpeg 缺失/素材损坏时首帧解码可能失败返回 None，跳过本帧而不是崩溃
            return
        if perfstats.ENABLED:
            _scale_t0 = perfstats.clock()
        img = pm.toImage()
        # 含文字/方向性画面的动画登记在 lib.no_mirror，朝右时也不镜像（否则文字反显）
        if self.facing == 'right' and self.anim not in getattr(self.lib, 'no_mirror', frozenset()):
            img = img.mirrored(True, False)
        # 按屏幕 DPR 渲染到物理像素，避免高分屏下被 Qt 二次放大导致模糊。
        # 先转预乘 alpha 再缩放：直通 alpha 缩放会让透明像素的 RGB 渗入
        # 半透明边缘，产生暗边/彩边（毛边来源之一）。顺序不可交换。
        w_c = max(1, int(round(catalog.CANVAS_W * self.scale * dpr)))
        h_c = max(1, int(round(catalog.CANVAS_H * self.scale * dpr)))
        img = img.convertToFormat(QImage.Format.Format_ARGB32_Premultiplied)
        img = img.scaled(w_c, h_c,
                         Qt.AspectRatioMode.IgnoreAspectRatio,
                         Qt.TransformationMode.SmoothTransformation)
        img = img.convertToFormat(QImage.Format.Format_ARGB32)
        pm = QPixmap.fromImage(img)
        pm.setDevicePixelRatio(dpr)
        if perfstats.ENABLED:
            # 未命中路径的整条转换链：toImage→镜像→预乘→Smooth 缩放→
            # ARGB32→fromImage（P0 观测：帧缓存 miss 时的缩放段成本）。
            perfstats.time('rebuild.scale', perfstats.clock() - _scale_t0)
        cache.put(cache_key, pm, img)
        self._frame_pixmap = pm
        # 直接缓存缩放后的 ARGB32 图：命中测试复用这份数据，
        # 避免 _is_transparent_at 再次 toImage
        self._hit_alpha_image = img
        self._frame_key = key
        self._last_frame_dpr = dpr
        self._sync_mask()
        if perfstats.ENABLED:
            perfstats.time('rebuild.total', perfstats.clock() - _rf_t0)

    def _refresh_frame_for_screen_dpr(self) -> None:
        """窗口所在屏幕 DPR 变化（跨屏/显示缩放变化）时强制按新 DPR 重建帧。

        _rebuild_frame 只在被调用时读取 DPR；窗口跨屏后若帧号/朝向等未变，
        _frame_key 快路径会跳过整条链，旧 DPR 的成品继续显示（模糊/物理
        尺寸不符，P1）。在 moveEvent 中对比「当前屏 DPR」与「当前帧构建
        所用 DPR」，变化即重建并重绘；DPR 未变（同屏移动）零开销。
        """
        scr = self._screen_available()
        dpr = scr.devicePixelRatio() if scr is not None else 1.0
        if (self._frame_pixmap is not None
                and dpr != getattr(self, '_last_frame_dpr', None)):
            self._rebuild_frame()
            self.update()

    # ================================================================ P1 复审：Qt 信号驱动 DPR 变化
    # Qt 6.11 的 QScreen 没有 devicePixelRatioChanged 信号；系统显示缩放变化
    # （改变 devicePixelRatio()）由 logicalDotsPerInchChanged /
    # physicalDotsPerInchChanged 上报。二者 + QWindow.screenChanged 都挂到
    # 强制 _rebuild_frame：缓存 key 用新 DPR，帧号/朝向等未变也不会被快路径
    # 跳过；DPR 确实未变（同 DPI 屏间跨屏等）由快路径自行跳过，零开销。
    # moveEvent 里的 _refresh_frame_for_screen_dpr 保留作兜底。

    def _arm_dpr_change_watch(self) -> None:
        """接线 Qt 信号：窗口跨屏 / 所在屏显示缩放变化 → 强制重建帧。

        幂等：QWindow 被重建（改 flags 等）时重挂到新 handle。QScreen 在
        拔屏时销毁，Qt 自动摘除其连接，无需手动清理。
        """
        win = self.windowHandle()
        if win is None:
            return
        old = getattr(self, '_dpr_watch_window', None)
        if old is win:
            return
        if old is not None:
            try:
                old.screenChanged.disconnect(self._on_window_screen_changed)
            except (RuntimeError, TypeError):
                pass  # 旧 QWindow 已销毁
        self._dpr_watch_window = win
        win.screenChanged.connect(self._on_window_screen_changed)
        scr = win.screen()
        if scr is not None:
            self._wire_screen_dpi_signals(scr)

    def _wire_screen_dpi_signals(self, screen) -> None:
        """把所在屏的 DPI 变化信号挂到强制重建；跨屏时换挂新屏。"""
        old = getattr(self, '_dpr_watch_screen', None)
        if old is screen:
            return
        if old is not None:
            for sig in ('logicalDotsPerInchChanged', 'physicalDotsPerInchChanged'):
                try:
                    getattr(old, sig).disconnect(self._on_screen_dpi_changed)
                except (RuntimeError, TypeError):
                    pass  # 旧屏已销毁
        self._dpr_watch_screen = screen
        for sig in ('logicalDotsPerInchChanged', 'physicalDotsPerInchChanged'):
            getattr(screen, sig).connect(self._on_screen_dpi_changed)

    def _disarm_dpr_change_watch(self) -> None:
        """关闭窗口时摘除信号接线（与 showEvent 的 arm 对称）。"""
        old = getattr(self, '_dpr_watch_window', None)
        if old is not None:
            try:
                old.screenChanged.disconnect(self._on_window_screen_changed)
            except (RuntimeError, TypeError):
                pass
            self._dpr_watch_window = None
        old = getattr(self, '_dpr_watch_screen', None)
        if old is not None:
            for sig in ('logicalDotsPerInchChanged', 'physicalDotsPerInchChanged'):
                try:
                    getattr(old, sig).disconnect(self._on_screen_dpi_changed)
                except (RuntimeError, TypeError):
                    pass
            self._dpr_watch_screen = None

    def _on_window_screen_changed(self, screen) -> None:
        """QWindow.screenChanged：跨屏 → 换挂新屏 DPI 信号并按新 DPR 强制重建。"""
        if screen is not None:
            self._wire_screen_dpi_signals(screen)
        self._rebuild_frame()
        self.update()

    def _on_screen_dpi_changed(self, *_args) -> None:
        """QScreen DPI 变化（系统显示缩放变化，窗口未移动）→ 强制按新 DPR 重建。"""
        self._rebuild_frame()
        self.update()

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

        - 非 Windows：继续用 QWidget.setMask 实现透明区域鼠标穿透，
          _mask_bounds 取自真实 mask 的 boundingRect（行为不变）。
        - Windows：不再 setMask（1-bit 裁剪会破坏半透明边缘），_mask_bounds
          用 Qt C++ 路径（createAlphaMask→QBitmap→QRegion）计算。
          教训：曾改成 Python 逐位扫描掩码，benchmark 实测比 Qt C++ 慢 3.5 倍
          （1.11ms vs 0.32ms/帧），每帧都亏——不要为了"省 Qt 调用"用 Python 扫像素。
        """
        if perfstats.ENABLED:
            _mask_t0 = perfstats.clock()
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
        if os.name != "nt":
            self.setMask(mask)
        elif not self.mask().isEmpty():
            self.clearMask()  # Windows：清掉历史遗留 mask（本路径不 setMask）
        if not self._mask_bounds.isEmpty():
            stable = getattr(self, '_collision_local_bounds', None)
            if stable is None:
                self._collision_local_bounds = QRect(self._mask_bounds)
            else:
                self._collision_local_bounds = stable.united(self._mask_bounds)
        if perfstats.ENABLED:
            # mask 生成（canvas 绘制 + createAlphaMask + QRegion，P0 观测）。
            perfstats.time('rebuild.mask', perfstats.clock() - _mask_t0)

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
            # _rebuild_frame 已把缩放后的 ARGB32 图缓存为 _hit_alpha_image，
            # 此处只是兜底（如测试直接挂 _frame_pixmap 的场景）
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
        if perfstats.ENABLED:
            _paint_t0 = perfstats.clock()
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
        if perfstats.ENABLED:
            # 窗口绘制（paintEvent 全段，含 slingshot/squash 附加绘制，P0 观测）。
            perfstats.time('paint.draw', perfstats.clock() - _paint_t0)

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
            if not pending.wait(timeout=30.0):
                # 解码线程病态卡死的逃生口：不永久挂起等待线程（审查 GLM-L5）
                return QImage()
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
        if self._hidden_paused or getattr(self, '_closing', False):
            return
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
            if self.movie.start() is False:
                # 拖拽动画也被拒（退役池卡死）：回退可播放动画并安排重试，
                # 不让拖拽状态停在"无动画在播"（B7 审查 P1-1）
                self._fallback_playable_idle(name)
                self._schedule_switch_retry(name)
                return
            # 拖拽动画直接重启成功：仅当待重试的正是本动画时清除待重试
            # （与 _switch 同一身份绑定语义，B7 复审 R2——无关动画的
            # 成功启动不得吞掉其他动画的待重试）。
            if self._pending_switch == name:
                self._pending_switch = None
                self._pending_switch_link = False
                self._switch_retry_count = 0
                self._switch_retry_timer.stop()
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
            else:
                # 防御：角色包没有 idle 时点击动画停在最后一帧（movie 已 stop）；
                # _switch 不会执行，显式释放让路闸门，避免 anim 仍是 click 名
                # 导致低优先级预热永久让路。
                self._click_hold = False
                self._update_interaction_hold()
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
        """计划一次朝 facing 方向的移动；返回 False 表示未建立移动计划。

        name 给定时使用指定动画（手动触发），否则随机选一个移动姿态。
        返回 False 的两种情况：屏幕空间不够（目标动画未尝试）；或移动动画
        start() 被拒——此时 _switch 已回退到可播放动画并安排重试，移动计划
        绝不建立（B7 审查 P1-1 / 复审 R2）。
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
        if not self._switch(move_name):
            # 切换被拒：_switch 已回退到上一动画/待机并安排重试（B7 审查
            # P1-1 / 复审 R2）。绝不能按失败移动动画建立移动计划——否则
            # 回退动画播放时仍按失败移动的 duration/坐标位移，画面、动画、
            # 窗口位移三者不一致。
            return False
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
        if self._try_move(name):
            return
        # _try_move 失败两种原因：
        # 1) 空间不足/无移动姿态（目标动画尚未尝试）→ 原地播放走路姿态；
        # 2) 切换被拒 → _switch 已回退到可播放动画并安排重试，绝不能再次
        #    _switch（双重降级/重复重试计数）。以「待重试登记正是该动画且
        #    计时器在跑」区分两种失败（B7 复审 R2）。
        if not (self._pending_switch == name and self._switch_retry_timer.isActive()):
            self._switch(name)  # 贴边放不下：原地播放走路姿态，不位移

    def trigger_move(self, name: str) -> None:
        """公开转发：手动触发移动（等价 _trigger_move）。"""
        self._trigger_move(name)

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
        self._flush_drag_move()  # 进入瞄准前应用最后一次跟手位置（锚点=当前窗口位置）
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
        self._clear_drag_move()  # 合帧目标已在 _enter_slingshot 冲掉，这里兜底
        self._sync_drag_polling(False)
        self._update_interaction_hold()  # 弹弓发射/取消回锚点：左键已释放 → 让路释放

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

    def _set_interaction_hold(self, active: bool) -> None:
        """同步低优先级预热让路闸门：只在状态翻转时通知库，避免每事件抖动。"""
        if active == self._interaction_hold_active:
            return
        self._interaction_hold_active = active
        lib = getattr(self, 'lib', None)
        if lib is None:
            self._interaction_hold_token = None
            return
        if active:
            begin = getattr(lib, 'begin_interaction', None)
            if callable(begin):
                self._interaction_hold_token = begin()
            else:
                self._interaction_hold_token = None
        else:
            end = getattr(lib, 'end_interaction', None)
            token = self._interaction_hold_token
            self._interaction_hold_token = None
            if callable(end):
                end(token)

    def _update_interaction_hold(self) -> None:
        """按当前交互状态重算让路闸门：左键按住（按下/拖拽/弹弓瞄准/锁定位置
        按住）、点击动画播放中、或右键菜单打开中 → 持有；否则释放。"""
        active = (
            self._press_global is not None
            or self._lock_press_active
            or self._context_menu_open
            or self._click_hold
        )
        self._set_interaction_hold(active)

    def _reset_press_hold_state(self) -> None:
        """复位全部交互按住/点击/菜单状态并对称释放让路闸门。

        隐藏/关闭路径调用（自定义 hide()→_pause_activity、原生 hideEvent、
        closeEvent）：隐藏后不再认为自己在拖拽/按住，迟到的动画事件也不会
        因 _press_global 残留而对旧库重新建立 hold；恢复显示后由
        _switch → _update_interaction_hold 按新状态重新同步。"""
        self._press_global = None
        self._grab_offset = None
        self._dragging = False
        self._lock_press_active = False
        self._click_hold = False
        self._context_menu_open = False
        self._set_interaction_hold(False)
        # 拖拽降频对称恢复（全审 P2-3）：按下时已 set_drag_active(True) 把
        # 穿透轮询降频到 100ms，隐藏/关闭打断拖拽后必须恢复 10ms 原频率并
        # 强制刷新一次穿透状态——否则滞留拖拽节奏直到下一次完整按-放循环
        # （re-show 后穿透状态更新延迟 10 倍且缺一次强制刷新）。非拖拽态
        # 重复调用是 no-op（platform_win.set_drag_active 已保证），安全。
        self._sync_drag_polling(False)

    def _sync_drag_polling(self, active: bool) -> None:
        """Windows 逐像素穿透轮询随拖拽按下/松手降频/恢复（非 Windows 无控制器，no-op）。"""
        ctrl = self._input_controller
        if ctrl is not None:
            ctrl.set_drag_active(active)

    def _schedule_drag_move(self, target: QPoint) -> None:
        """普通拖拽合帧：只记录最新目标位置，由 ~120Hz timer 消费。

        同一显示帧内多次 mouseMoveEvent 会不断覆盖 pending，timer tick
        时永远只消费最新目标（丢弃中间过期位置）。"""
        self._drag_move_pending = QPoint(target)
        if not self._drag_move_timer.isActive():
            self._drag_move_timer.start()

    def _consume_drag_move(self) -> None:
        """~120Hz timer 槽：消费最新目标做 self.move；无新目标则停表。"""
        if self._drag_move_pending is None:
            self._drag_move_timer.stop()
            return
        target = self._drag_move_pending
        self._drag_move_pending = None
        self.move(target)

    def _flush_drag_move(self) -> None:
        """拖拽结束/打断前：停止合帧 timer，并立即应用最后一次目标位置。

        松手/进入弹弓等路径调用：保证最后记录的跟手位置不丢失。"""
        self._drag_move_timer.stop()
        if self._drag_move_pending is not None:
            target = self._drag_move_pending
            self._drag_move_pending = None
            self.move(target)

    def _clear_drag_move(self) -> None:
        """丢弃未消费的合帧目标并停止 timer（不移动窗口，防御性清理）。"""
        self._drag_move_timer.stop()
        self._drag_move_pending = None

    def mousePressEvent(self, event) -> None:  # noqa: N802
        # 任何到达桌宠窗口的按下都是"鼠标命中"：立即回满帧率（闲置降帧锚点）。
        # 窗口透明区域在 Windows 逐像素穿透/非 Windows mask 下不会收到事件，
        # 因此能到达这里的一定是用户真的在点桌宠。
        self.mark_activity()
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
                # 每次按下都重置：解析失败/切换音效包时不能复用上一次的旧 pair
                self._press_sound_pair = pair
                # 拖动不应触发点击音效：按下阶段不发声，确认是点击后再播放完整 press+release
            if self.lock_position:
                # 锁定位置：不记录按下（拖拽不会开始），但按住期间仍持有
                # 低优先级预热让路闸门，避免锁定点击瞬间预热抢 CPU/IO；
                # 松手走点击路径时由 _update_interaction_hold 释放。
                self._lock_press_active = True
                self._update_interaction_hold()
                event.accept()
                return
            self._press_global = event.globalPosition().toPoint()
            self._sync_drag_polling(True)
            self._interaction_state = "PRESS_CANDIDATE"
            self._grab_offset = self._press_global - self.pos()
            self._dragging = False
            self._cancel_move()  # 按下即打断移动
            self._clear_drag_move()  # 丢弃上一次拖拽遗留的未消费合帧目标（防御）
            self._last_global = self._press_global
            self._last_move_time = time.monotonic()
            self._trail = [(self._last_move_time, self._press_global.x(), self._press_global.y())]
            self._phys_vel = [0.0, 0.0]
            self._phys_pos = [float(self.x()), float(self.y())]
            self._stop_physics()
            self.setFocus(Qt.FocusReason.OtherFocusReason)
            self._update_interaction_hold()  # 左键按住 → 低优先级预热让路
            event.accept()
        else:
            super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        # 拖拽/弹弓瞄准中的移动 = 交互：刷新闲置降帧的活跃锚点
        self.mark_activity()
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
                self._update_interaction_hold()  # 未按 SHIFT 取消按压 → 释放让路
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
                # 拖拽开始的第一帧仍立即跟手（既有交互语义），此后由
                # ~120Hz 合帧 timer 消费最新目标
                self.move(g - self._grab_offset)
                self._position_sync_now()  # 拖拽开始的第一帧立即同步（气泡/监听器）
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
            # 跟手（保持抓起时的偏移）：只记录最新目标，由 ~120Hz timer 消费，
            # 同一显示帧内的中间位置丢弃，避免一次帧内多次 move + moveEvent 开销
            self._schedule_drag_move(g - self._grab_offset)
        event.accept()

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        # 松手 = 交互结束事件：同样刷新活跃锚点（点击/拖拽松手都算"碰过"）
        self.mark_activity()
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
            self._flush_drag_move()  # 拖拽结束：强制处理最后一次目标位置并停止合帧 timer
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
            self._position_sync_now()  # 松手后的最终位置立即同步（气泡/监听器），不等去抖
            if self.idles:
                self._switch(self._pick(self.idles))  # 回待机缓冲
        elif dist < catalog.DRAG_THRESHOLD * self.scale:
            if self._press_sound_pair is not None and self.click_sound_enabled:
                volume = float(self.cfg.get("click_sound_volume", 0.70))
                play_press_sound(self._press_sound_pair, volume)
                play_release_sound(self._press_sound_pair, volume)
            if not self._try_open_quick_chat_from_bubble(g):
                self._on_click()
        self._dragging = False
        self._interaction_state = "IDLE"
        self._press_global = None
        self._lock_press_active = False
        self._grab_offset = None
        self._sync_drag_polling(False)
        if self._cursor_restore_pending or self._cursor_visibility == 'SHOWING':
            self._cursor_restore_pending = False
            self._auto_cursor_hidden = False
        self._apply_effective_mouse_through()
        self._submit_collision_state(force=True)
        # 松手后重算让路闸门：左键已释放，若点击动画仍在播放则继续保持持有
        self._update_interaction_hold()
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

    def look_at_screen(self) -> None:
        """公开转发：触发一次"看看屏幕"识别（等价 _on_look_screen）。"""
        self._on_look_screen()

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
            # ESC 取消弹弓 = 被窗口消费的键盘交互：刷新闲置降帧活跃锚点
            # （P2 顺修：键盘交互同样计活跃，任何交互立即回满帧率）。
            self.mark_activity()
            self._cancel_slingshot_to_anchor()
            event.accept()
            return
        super().keyPressEvent(event)

    def focusOutEvent(self, event) -> None:  # noqa: N802
        if self._interaction_state == "SLINGSHOT_AIMING":
            # 失焦取消弹弓 = 被窗口消费的交互状态变更：同样刷新活跃锚点
            # （P2 顺修：与 ESC 取消同一语义，避免取消后立刻落入降帧）。
            self.mark_activity()
            self._cancel_slingshot_to_anchor()
        super().focusOutEvent(event)

    def hideEvent(self, event) -> None:  # noqa: N802
        if self._interaction_state == "SLINGSHOT_AIMING":
            self._cancel_slingshot_to_anchor()
        # 生命周期兜底：平台原生 hide（不经自定义 hide()/_pause_activity）同样
        # 停掉拖拽合帧 timer 并丢弃 pending 与位置同步去抖，隐藏期间不再 move 窗口。
        self._clear_drag_move()
        self._position_sync_pending = False
        # 原生隐藏同样暂停预热并对称释放交互让路闸门，避免库侧计数泄漏
        # （自定义 hide() 路径此处是幂等重复，不影响行为）。
        lib = getattr(self, 'lib', None)
        if lib is not None and hasattr(lib, 'pause_warm'):
            lib.pause_warm()
        # 必须与自定义 hide() 一致置位 _hidden_paused：showEvent 据此走
        # _resume_activity() → resume_warm()。缺了它，原生隐藏→显示循环后
        # 预热被永久停用（_warm_paused 永远无法复位）。重复置位是幂等 no-op。
        self._hidden_paused = True
        # 原生隐藏直进路径（不经自定义 hide()/_pause_activity）同样复位全部
        # 按住状态：只清点击/菜单标志而残留 _press_global/_dragging 时，
        # 重新显示后 _resume_activity → _switch → _update_interaction_hold
        # 会把旧按住误判成活跃交互、对库侧重新 begin_interaction()，松手事件
        # 却不再到来 → 库侧 hold 泄漏（与 _pause_activity 语义对齐）。
        self._reset_press_hold_state()
        super().hideEvent(event)

    def _show_context_menu(self, global_pos: QPoint) -> None:
        # 右键菜单弹出 = 用户交互：刷新闲置降帧的活跃锚点。
        # getattr 守卫：测试桩/最小替代对象可以不实现 mark_activity。
        mark = getattr(self, 'mark_activity', None)
        if callable(mark):
            mark()
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
        # 右键菜单打开期间持有低优先级预热让路闸门，避免菜单/缩略图解码
        # 与低优先级预热抢 ffmpeg/CPU；关闭后立即释放。
        # getattr 守卫：测试桩/最小替代对象可以不实现让路钩子。
        self._context_menu_open = True
        update_hold = getattr(self, '_update_interaction_hold', None)
        if callable(update_hold):
            update_hold()
        try:
            menu.exec(transition_start)
        finally:
            self._context_menu_open = False
            if callable(update_hold):
                update_hold()
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

        def delete_when_idle(_attempts: int = 0) -> None:
            """非阻塞等待图标解码 worker 结束后再释放菜单树。

            直接 pool.waitForDone(3000) 会阻塞 GUI 线程最多 3 秒，可能造成
            右键菜单关闭时卡顿/假死；这里每 50ms 轮询一次，不阻塞事件循环。
            总上限 3s（60×50ms）：解码 worker 病态不结束时也强制释放，
            否则菜单树会永久滞留、deferred 回调永不派发（审查 DS-L16）。
            """
            if _attempts >= 60:
                menu.deleteLater()
                return
            if any(not pool.waitForDone(0) for pool in pools):
                QTimer.singleShot(50, lambda: delete_when_idle(_attempts + 1))
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
                value, anchor, duration_ms, pet_scale=self.scale,
                image_scale=self._self_talk_image_scale,
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
        if time.monotonic() < self._bubble_busy_until:
            # 重要气泡占用中：本次自言自语跳过，重新排队下一次
            self._schedule_self_talk()
            return
        displayed = False
        if self._self_talk_enabled and self.isVisible():
            displayed = self._show_random_self_talk()
        self._schedule_self_talk(after_display=displayed)

    def hold_bubble(self, seconds: float) -> None:
        """声明重要气泡占用时长（自言自语在此期间让路）。"""
        self._bubble_busy_until = max(self._bubble_busy_until, time.monotonic() + max(0.0, seconds))

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

    def hide_speech_bubble(self) -> None:
        """公开转发：隐藏当前气泡（等价 _speech_bubble.hide()）。"""
        self._speech_bubble.hide()

    def refresh_pet_settings(self) -> None:
        collision_enabled = bool(self.cfg.get('collision_enabled', True))
        if collision_enabled and self._collision_session is None:
            self.attach_collision_session(getattr(self, '_collision_app_session', None))
        elif not collision_enabled and self._collision_session is not None:
            # 先让协调 worker 停止求解，再提交本地成员 leave。
            self._sync_collision_policy()
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
        # 闲置降帧开关/阈值即时生效（设置保存后无需重启）；降帧门控逐帧
        # 读取这两个字段，关闭后下一帧即恢复全帧率。
        self.idle_low_fps_enabled = bool(self.cfg.get('idle_low_fps_enabled', False))
        self.idle_low_fps_threshold = max(
            1.0, min(3600.0, float(self.cfg.get('idle_low_fps_threshold',
                                                 IDLE_LOW_FPS_DEFAULT_THRESHOLD)))
        )
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
        self._self_talk_image_scale = max(0.5, min(3.0, float(self.cfg.get('self_talk_image_scale', 100)) / 100.0))
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
        template_id = normalize_template_id(template_id)
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

    def toggle_proactive_enabled(self, on: bool) -> None:
        """公开转发：切换主动识屏总开关（等价 _toggle_proactive_enabled）。"""
        self._toggle_proactive_enabled(on)

    def _set_proactive_option(self, key: str, value: Any) -> None:
        """右键菜单修改主动识屏子项选项。"""
        pro_data = dict(self.cfg.get('proactive_screen', {}))
        pro_data[key] = value
        self.cfg.set('proactive_screen', pro_data)
        self.cfg.save()
        if hasattr(self, 'proactive_watcher') and self.proactive_watcher is not None:
            self.proactive_watcher.apply_config()

    def set_proactive_option(self, key: str, value: Any) -> None:
        """公开转发：修改主动识屏子项选项（等价 _set_proactive_option）。"""
        self._set_proactive_option(key, value)

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

    def toggle_agent_link(self, agent_key: str, on: bool, action=None) -> None:
        """公开转发：切换 Agent 状态联动子项（等价 _toggle_agent_link）。"""
        self._toggle_agent_link(agent_key, on, action)

    def _set_agent_link_option(self, key: str, on: bool) -> None:
        """联动气泡提醒子项开关（开始干活 / 任务完成），立即写入配置。"""
        ag_data = dict(self.cfg.get('agent_link', {}))
        ag_data[key] = bool(on)
        self.cfg.set('agent_link', ag_data)
        self.cfg.save()

    def set_agent_link_option(self, key: str, on: bool) -> None:
        """公开转发：联动气泡提醒子项开关（等价 _set_agent_link_option）。"""
        self._set_agent_link_option(key, on)

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

    def rename_character(self) -> None:
        """公开转发：自定义当前角色显示名（等价 _rename_character）。"""
        self._rename_character()

    def _request_switch_character(self, character_id: str) -> None:
        """请求切换角色；优先交给 app 做热切换，否则只保存配置。"""
        if self.on_switch_character is not None:
            self.on_switch_character(character_id)
        else:
            self.cfg.set('character', character_id)
            self.cfg.save()

    def request_switch_character(self, character_id: str) -> None:
        """公开转发：请求切换角色（等价 _request_switch_character）。"""
        self._request_switch_character(character_id)

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
            # 锁定位语义等同松手：立即应用最后一次跟手位置并停止合帧 timer，
            # 否则下一次 tick 仍会把窗口 move 到旧目标，违反锁定语义。
            self._flush_drag_move()
            self._dragging = False
            self._press_global = None
            self._grab_offset = None
            self._sync_drag_polling(False)
            self._stop_physics()
        self._submit_collision_state(force=True)
        self._update_interaction_hold()  # 拖拽被锁定位置打断 → 释放让路

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
        """throw 物理 tick 后的本地弹跳预测（实现已迁至 CollisionClient 批 6-4）。"""
        self._collision_client._predict_collision_bounce(
            start_x, start_y, incoming_vx=incoming_vx, incoming_vy=incoming_vy)


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

    def request_quit(self) -> None:
        """公开转发：请求退出应用（等价 _request_quit）。"""
        self._request_quit()

    def moveEvent(self, event) -> None:  # noqa: N802
        super().moveEvent(event)
        # 跨屏（屏幕 DPR 变化）：_rebuild_frame 只在被调用时读 DPR，帧号/
        # 朝向等未变时 _frame_key 快路径会跳过 → 这里检测 DPR 变化并强制
        # 按新 DPR 重建帧，避免跨屏后继续显示旧 DPR 成品（P1）。
        self._refresh_frame_for_screen_dpr()
        # 非 force 节流提交：位置变化由去重 + 20Hz 限流兜底，运动期由
        # _collision_timer（50ms）强制上报，避免 60Hz 抛掷移动上报超标
        self._submit_collision_state()
        # 气泡重定位与 position listeners 同帧合并：同一 GUI 帧内多次
        # moveEvent 只处理最后一次（0ms 去抖）；拖拽开始/松手关键帧由
        # 调用方 _position_sync_now() 立即同步，去抖回调随后被丢弃。
        self._schedule_position_sync()

    def _schedule_position_sync(self) -> None:
        """moveEvent 同帧合并：同一帧内多次 moveEvent 只安排一次 0ms 去抖，
        回调触发时以最新窗口位置做气泡重定位与 position listeners 通知。"""
        if self._position_sync_pending:
            return
        self._position_sync_pending = True
        QTimer.singleShot(0, self, self._sync_position_debounced)

    def _sync_position_debounced(self) -> None:
        if not self._position_sync_pending:
            return  # 拖拽开始/松手已立即同步，丢弃过期的去抖回调
        self._position_sync_pending = False
        self._position_sync_now()

    def _position_sync_now(self) -> None:
        """立即同步气泡重定位与 position listeners（拖拽开始/松手关键帧）。"""
        self._position_sync_pending = False
        self._speech_bubble.reposition(self.visible_content_rect())
        for listener in tuple(self._position_listeners):
            try:
                listener(self)
            except Exception:
                logging.exception("\u684c\u5ba0\u4f4d\u7f6e\u76d1\u542c\u5668\u6267\u884c\u5931\u8d25")

    def closeEvent(self, event) -> None:  # noqa: N802
        self._closing = True  # 关闭后丢弃迟到的动画事件（生命周期守卫）
        # 摘除 DPR 变化信号接线（与 showEvent 的 arm 对称）
        self._disarm_dpr_change_watch()
        # 停掉 Agent 监视器 worker 线程：worker 经引用链持有本窗口，
        # 不主动停会让旧窗口在 deleteLater 之后仍被轮询线程保活（B9）
        if getattr(self, 'agent_link_manager', None) is not None:
            self.agent_link_manager.shutdown()
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
        self._clear_drag_move()  # 生命周期兜底：停拖拽合帧 timer、丢 pending
        self._position_sync_pending = False  # 丢弃 moveEvent 同帧合并的在途去抖
        self._speech_bubble.hide()
        # 关闭即销毁：暂停预热并对称释放交互让路闸门，避免库侧计数泄漏。
        lib = getattr(self, 'lib', None)
        if lib is not None and hasattr(lib, 'pause_warm'):
            lib.pause_warm()
        # 显式停掉当前动画 reader（Fix D）：窗口关闭不再依赖 GC + destroyed
        # （已实证会失效的路径），关闭即 stop()——reader 收到停止信号、底层
        # ffmpeg 被 terminate、线程退役登记。_closing 已置位，迟到的动画事件
        # 会被丢弃，与此停播语义不冲突；stop() 幂等，对测试替身无副作用。
        movie = getattr(self, 'movie', None)
        if movie is not None:
            try:
                movie.stop()
            except RuntimeError:
                pass  # movie 的 C++ 侧已随库销毁（半销毁场景）：不得中断 closeEvent 后续清理
            # P3 broker：窗口关闭 = 停播 → shareable idle 会话中止（aborted
            # 广播，消费端本地回退）；broker 关 = no-op。
            try:
                self._broker_unregister(getattr(self, 'anim', None), movie,
                                        natural=False)
            except Exception:
                pass  # 关闭期 facade 可能已 shutdown，尽力而为
        self._lock_press_active = False
        self._click_hold = False
        self._context_menu_open = False
        self._set_interaction_hold(False)
        super().closeEvent(event)
