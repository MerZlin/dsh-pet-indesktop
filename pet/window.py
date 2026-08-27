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

import logging
import math
import random
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

from PySide6.QtCore import QElapsedTimer, QPoint, QRect, Qt, QTimer, Signal
from PySide6.QtGui import QBitmap, QCursor, QImage, QPainter, QPixmap, QRegion
from PySide6.QtWidgets import QApplication, QMenu, QToolTip, QWidget

import shiboken6

from . import autostart as autostart_mod
from . import catalog
from .config import (
    DEFAULT_SELF_TALK_BUBBLE_STYLE,
    DEFAULT_SELF_TALK_DURATION_SECONDS,
    DEFAULT_SELF_TALK_MAX_INTERVAL,
    DEFAULT_SELF_TALK_MIN_INTERVAL,
    DEFAULT_SELF_TALK_TEXTS,
    Config,
)
from .library import MovieLibrary
from .animation_thumbnail import decode_representative_frame, representative_frame_index
from .speech_bubble import PetSpeechBubble, list_self_talk_images
from .fun_image_popup import oijingjing_image_path, resolve_fun_asset
from .context_menu import populate_context_menu as _populate_context_menu
from .context_menus.shared import take_deferred_menu_callbacks
from . import vision as vision_mod
from . import physics as physics_mod


def _resolve_self_talk_image_dir(raw: str) -> str:
    """Resolve the self-talk image directory; empty keeps text-only behavior."""
    raw = str(raw or '').strip()
    if not raw:
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


class PetWindow(QWidget):
    """桌宠窗口本体。"""

    look_done = Signal(str, str, bool)

    def __init__(self, lib: MovieLibrary, config: Config) -> None:
        super().__init__()
        self.lib = lib
        self.cfg = config
        self.on_switch_character = None  # 由 app 注入，用于运行时切换角色
        self.on_open_chat = None
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
        self.mouse_through: bool = bool(config.get('mouse_through', False))
        self.drag_physics: bool = bool(config.get('drag_physics', False))
        self.click_sound_enabled: bool = bool(config.get('click_sound_enabled', True))
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

        # ---- 窗口属性：无边框 + 透明 + 不进任务栏；置顶可配置 ----
        flags = Qt.WindowType.FramelessWindowHint | Qt.WindowType.Tool
        if config.get('on_top', True):
            flags |= Qt.WindowType.WindowStaysOnTopHint
        self.setWindowFlags(flags)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        if self.mouse_through:
            self.setWindowFlag(Qt.WindowType.WindowTransparentForInput, True)
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
        self._ended_fired = False

        # ---- 交互状态 ----
        self._press_global: QPoint | None = None
        self._grab_offset: QPoint | None = None  # 按下时 鼠标全局坐标 - 窗口左上角
        self._dragging = False
        self._just_dragged = False               # 抑制拖拽结束后的幽灵点击

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

        # ---- 尺寸与初始状态 ----
        self._apply_scale()
        for name, movie in lib.movies().items():
            # 默认参数捕获 name，避免闭包晚绑定
            movie.frameChanged.connect(lambda n, name=name: self._on_frame(name, n))
            # 兜底：主线程被阻塞导致队列溢出、最后一帧被丢弃时，
            # frameChanged 永远到不了末尾帧；用 finished 信号保证动画链一定继续。
            movie.finished.connect(lambda name=name: self._on_clip_finished(name))
        self._restore_position()
        self._switch(self.idle)
        self._schedule_self_talk()

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
        mask = self.mask()
        if not mask.isEmpty():
            local_rect = mask.boundingRect()
            if not local_rect.isEmpty():
                return QRect(frame_rect.topLeft() + local_rect.topLeft(), local_rect.size())
        return frame_rect

    def _restore_position(self) -> None:
        """恢复上次位置（按屏幕比例），无记录则落右下角。"""
        scr = self._screen_available(self.cfg.get('screen_name'))
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
        logging.info('恢复位置 screen=%s avail=(%d,%d,%d,%d) dpr=%s -> (%d,%d)',
                     scr.name(), avail.left(), avail.top(), avail.right(),
                     avail.bottom(), scr.devicePixelRatio(), x, y)
        self.move(x, y)

    def _save_position(self) -> None:
        """以"窗口中心相对屏幕可用区的比例"持久化位置（分辨率变化后仍正确）。"""
        scr = self._screen_available()
        avail = scr.availableGeometry()
        if avail.width() <= 0 or avail.height() <= 0:
            return
        cx = self.x() + self._w / 2
        cy = self.y() + self._h / 2
        self.cfg.set('rx', (cx - avail.left()) / avail.width())
        self.cfg.set('ry', (cy - avail.top()) / avail.height())
        self.cfg.set('screen_name', scr.name())
        self.cfg.set('facing', self.facing)
        self.cfg.set('scale', self.scale)
        self.cfg.save()

    def _go_default_corner(self) -> None:
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
        self._schedule_macos_window_level(bool(self.cfg.get('on_top', True)))
        self._restore_dock_icon_preference()

    def hide(self, *, notify: bool = True) -> None:
        """隐藏桌宠。

        macOS 同步打开 Dock 图标；notify=False 供角色切换等内部替换使用
        （不弹托盘提示、不 arm Dock 点击恢复监听）。
        """
        self._ensure_dock_icon_on_hide()
        super().hide()
        if not notify:
            return
        if callable(getattr(self, "on_hidden", None)):
            self.on_hidden()
        self._arm_dock_reactivate_restore()

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

    # ================================================================ 播放
    def _switch(self, name: str) -> None:
        """切换到指定动画（链式模型：全部一次性播放）。"""
        self._cancel_move()
        self.anim = name
        movie = self.lib.movie(name)
        self.movie = movie
        movie.stop()
        movie.jumpToFrame(0)
        if hasattr(movie, 'set_playback_speed'):
            movie.set_playback_speed(self.playback_speed)
        self._ended_fired = False
        self._rebuild_frame()
        movie.start()

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
        if pm.isNull():
            return
        img = pm.toImage()
        if self.facing == 'right':
            img = img.mirrored(True, False)
        # 按屏幕 DPR 渲染到物理像素，避免高分屏下被 Qt 二次放大导致模糊
        scr = self._screen_available()
        dpr = scr.devicePixelRatio() if scr is not None else 1.0
        w_c = max(1, int(round(catalog.CANVAS_W * self.scale * dpr)))
        h_c = max(1, int(round(catalog.CANVAS_H * self.scale * dpr)))
        img = img.scaled(w_c, h_c,
                         Qt.AspectRatioMode.IgnoreAspectRatio,
                         Qt.TransformationMode.SmoothTransformation)
        pm = QPixmap.fromImage(img)
        pm.setDevicePixelRatio(dpr)
        self._frame_pixmap = pm
        self._sync_mask()

    def _sync_mask(self) -> None:
        """按当前帧 alpha 设置窗口 mask：透明区域鼠标穿透到下层窗口。"""
        canvas = QImage(self._w, self._h, QImage.Format.Format_ARGB32)
        canvas.fill(Qt.GlobalColor.transparent)
        p = QPainter(canvas)
        p.translate(0, int(round(catalog.PAD * self.scale)))
        if self._frame_pixmap is not None:
            p.drawPixmap(0, 0, self._frame_pixmap)
        p.end()
        self.setMask(QBitmap.fromImage(canvas.createAlphaMask()))

    def paintEvent(self, event) -> None:  # noqa: N802 (Qt 命名)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        if self._frame_pixmap is not None:
            if self._squash_active:
                # Q 弹：使用逻辑帧尺寸；QPixmap.width() 可能是 DPR 物理像素尺寸。
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
        self._squash_progress = 0.0
        self._squash_clock.start()
        self._squash_timer.start()
        self.update()

    def _on_squash_tick(self) -> None:
        elapsed = self._squash_clock.elapsed()
        self._squash_progress = min(1.0, elapsed / self._squash_duration_ms)
        if self._squash_progress >= 1.0:
            self._squash_active = False
            self._squash_timer.stop()
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
        clip = self.lib.movie(name)
        path = getattr(clip, "path", None)
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
        if name == self.drag and self._dragging:
            self.movie.jumpToFrame(0)
            self._ended_fired = False
            self.movie.start()
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
            # 到位：提交终点，动画自然播完后续链
            self._move_timer.stop()
            self._move_plan = None
            self._save_position()

    def _cancel_move(self) -> None:
        self._move_timer.stop()
        self._move_plan = None

    # ================================================================ 交互
    def _is_in_interactive_area(self, local_pos) -> bool:
        """由于动画左右有留白，只把窗口中间 1/3 宽度作为可交互区域。"""
        return self._w / 3.0 <= local_pos.x() <= self._w * 2.0 / 3.0

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            if not self._is_in_interactive_area(event.position().toPoint()):
                return  # 左右留白区域不参与点击/拖拽
            self._press_global = event.globalPosition().toPoint()
            self._grab_offset = self._press_global - self.pos()
            self._dragging = False
            self._cancel_move()  # 按下即打断移动
            self._last_global = self._press_global
            self._last_move_time = time.monotonic()
            self._trail = [(self._last_move_time, self._press_global.x(), self._press_global.y())]
            self._phys_vel = [0.0, 0.0]
            self._phys_pos = [float(self.x()), float(self.y())]
            self._stop_physics()
            event.accept()
        else:
            super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        if self._press_global is None or not (event.buttons() & Qt.MouseButton.LeftButton):
            return
        g = event.globalPosition().toPoint()
        delta = g - self._press_global
        if not self._dragging:
            if math.hypot(delta.x(), delta.y()) < catalog.DRAG_THRESHOLD * self.scale:
                return  # 未超阈值：仍是点击候选
            self._dragging = True
            if self.drag:
                self._switch(self.drag)  # 进入拖拽：播放悬空反馈动画
            if self.drag_physics:
                self._phys_pos = [float(self.x()), float(self.y())]
                self._drag_target = g - self._grab_offset
                self._physics_mode = 'drag'
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
                self._physics_mode = 'drag'
                self._physics_timer.start()
        else:
            self.move(g - self._grab_offset)  # 跟手（保持抓起时的偏移）
        event.accept()

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
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
                rvx, rvy = physics_mod.estimate_release_velocity(self._trail, time.monotonic())
                if math.hypot(rvx, rvy) < physics_mod.DEAD_ZONE_SPEED:
                    if self._grab_offset is not None:
                        self.move(g - self._grab_offset)
                    self._stop_physics()
                    self._save_position()
                else:
                    self._phys_vel[:] = [rvx, rvy]
                    self._physics_mode = 'throw'
                    self._physics_timer.start()
            else:
                if self._grab_offset is not None:
                    self.move(g - self._grab_offset)  # 停在松手处
                self._save_position()
            if self.idles:
                self._switch(self._pick(self.idles))  # 回待机缓冲
        elif dist < catalog.DRAG_THRESHOLD * self.scale:
            self._on_click()
        self._dragging = False
        self._press_global = None
        self._grab_offset = None
        event.accept()

    def _clear_just_dragged(self) -> None:
        self._just_dragged = False

    def _on_click(self) -> None:
        """真点击 → 随机一个点击回应动画，并重置当前动画（可连续点击打断）。"""
        if self._just_dragged:
            return
        if callable(self.on_restore_fun_windows):
            self.on_restore_fun_windows()
        if not self.clicks:
            return
        # 点击可以打断当前动画（包括正在播放的点击回应），实现连续 Q 弹
        self._cancel_move()
        self._play_click_sound()
        self._start_squash()
        self._switch(self._pick(self.clicks))
        if self.click_show_balance and callable(self.on_show_balance):
            self.on_show_balance(self)
        elif self.click_show_self_talk and self._self_talk_enabled:
            if self._show_random_self_talk():
                self._schedule_self_talk(after_display=True)

    def _play_click_sound(self) -> None:
        if not self.click_sound_enabled:
            return
        candidates = []
        custom = str(self.click_sound_path or "").strip()
        if custom:
            candidates.append(Path(custom).expanduser())
        candidates.append(self.cfg.dir / "sounds" / "click.wav")
        root = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent))
        candidates.append(root / "assets" / "sounds" / "click.wav")
        path = next((item for item in candidates if item.is_file()), None)
        if path is None:
            return
        if sys.platform == "win32":
            try:
                import winsound
                winsound.PlaySound(str(path), winsound.SND_FILENAME | winsound.SND_ASYNC)
            except Exception:
                pass
            return
        player = shutil.which("afplay") or shutil.which("paplay") or shutil.which("aplay")
        if player:
            command = [player, str(path)]
            if Path(player).name == "aplay":
                command.insert(1, "-q")
            try:
                subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            except OSError:
                pass

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
        threading.Thread(target=self._look_worker, daemon=True, name="pet-look-screen").start()

    def _look_worker(self) -> None:
        try:
            settings = self.cfg.chat_settings()
            provider = settings.active_config
            provider.api_key = self.cfg.resolve_api_key(provider)
            shot = vision_mod.capture_screen(self.cfg.dir / "screenshots")
            app_info = vision_mod.foreground_app_info()
            reply = vision_mod.ask_about_screen(
                shot, app_info, settings.default_system_prompt, provider
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
        if not self._is_in_interactive_area(event.pos()):
            return
        self._show_context_menu(event.globalPos())

    def _show_context_menu(self, global_pos: QPoint) -> None:
        self._context_menu_anchor = QPoint(global_pos)
        menu = QMenu(self)
        self._active_context_menu = menu
        _populate_context_menu(menu, self)
        menu.aboutToHide.connect(
            lambda self=self: QTimer.singleShot(0, self, self._restore_on_top_after_context_menu)
        )
        menu.exec(global_pos)
        callbacks = take_deferred_menu_callbacks(menu)
        if getattr(self, "_active_context_menu", None) is menu:
            self._active_context_menu = None
        # QMenu.exec() has now returned, so the native NSMenu tracking loop is
        # gone. Heavy dialogs and QApplication.quit() are safe at this point.
        for callback in callbacks:
            callback()
        # 菜单使用完毕即释放整棵菜单树：QMenu 以长命窗口为 parent，
        # 不删除会随每次右键累积（子菜单/动作/线程池/图标 pixmap）。
        # 先清掉尚未启动的解码任务，避免 QThreadPool 析构时在 GUI 线程
        # 等待运行中的 worker。
        for submenu in menu.findChildren(QMenu):
            pool = getattr(submenu, "_animation_icon_pool", None)
            if pool is not None:
                pool.clear()
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

    def _show_random_self_talk(self) -> bool:
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
        if kind == "image":
            return self._speech_bubble.show_image(
                value, anchor, duration_ms, pet_scale=self.scale
            )
        self._speech_bubble.show_text(
            value, anchor, duration_ms, pet_scale=self.scale
        )
        return True

    def _on_self_talk_timeout(self) -> None:
        displayed = False
        if self._self_talk_enabled and self.isVisible():
            displayed = self._show_random_self_talk()
        self._schedule_self_talk(after_display=displayed)

    def show_bubble(self, text: str, duration_ms: int = 3200) -> None:
        self._speech_bubble.show_text(
            str(text), self.visible_content_rect(), duration_ms, pet_scale=self.scale
        )

    def refresh_pet_settings(self) -> None:
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
        desired_drag_physics = bool(self.cfg.get('drag_physics', False))
        if desired_drag_physics != self.drag_physics:
            self.set_drag_physics(desired_drag_physics)
        self.animation_gap_seconds = max(0.0, min(3600.0, float(self.cfg.get('animation_gap_seconds', 0.0))))
        if self.animation_gap_seconds <= 0:
            self._cancel_animation_gap()
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
        self.click_sound_enabled = bool(self.cfg.get('click_sound_enabled', True))
        self.click_sound_path = str(self.cfg.get('click_sound_path', '') or '')
        self.click_show_balance = bool(self.cfg.get('click_show_balance', False))
        self.click_show_self_talk = bool(self.cfg.get('click_show_self_talk', False))
        self._schedule_self_talk()

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
        self._speech_bubble.show_text(
            text, self.visible_content_rect(), duration_ms=2200,
            pet_scale=self.scale,
        )
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
        self.mouse_through = bool(on)
        self.cfg.set('mouse_through', self.mouse_through)
        self.cfg.save()
        self.setWindowFlag(Qt.WindowType.WindowTransparentForInput, self.mouse_through)
        self.show()

    def set_drag_physics(self, on: bool) -> None:
        """拖动物理开关。"""
        self.drag_physics = bool(on)
        self.cfg.set('drag_physics', self.drag_physics)
        self.cfg.save()
        if not self.drag_physics:
            self._stop_physics()

    def _stop_physics(self) -> None:
        self._physics_timer.stop()
        self._physics_mode = None

    def _on_physics_tick(self) -> None:
        if self._physics_mode == 'drag':
            self._tick_drag_physics()
        elif self._physics_mode == 'throw':
            self._tick_throw_physics()

    def _tick_drag_physics(self) -> None:
        if self._drag_target is None:
            return
        dt = 0.016
        tx, ty = self._drag_target.x(), self._drag_target.y()
        px, py = self._phys_pos
        self._phys_vel[0] = physics_mod.spring_velocity(self._phys_vel[0], px, tx, dt)
        self._phys_vel[1] = physics_mod.spring_velocity(self._phys_vel[1], py, ty, dt)
        self._phys_pos[0] += self._phys_vel[0] * dt
        self._phys_pos[1] += self._phys_vel[1] * dt
        self.move(int(round(self._phys_pos[0])), int(round(self._phys_pos[1])))

    def _tick_throw_physics(self) -> None:
        dt = 0.016
        scr = self._screen_available()
        avail = scr.availableGeometry()
        # 忽略左右留白：角色实际可视区域约为窗口中间 1/3，
        # 允许窗口略微超出屏幕边界，让角色形象真正碰到边缘才反弹。
        margin = self._w / 3.0
        left = avail.left() - margin
        top = avail.top()
        right = avail.right() - self._w + margin
        bottom = avail.bottom() - self._h
        px, py, vx, vy, bounced = physics_mod.throw_step(
            self._phys_pos[0], self._phys_pos[1],
            self._phys_vel[0], self._phys_vel[1], dt,
            left, top, right, bottom,
        )
        self._phys_pos[:] = [px, py]
        self._phys_vel[:] = [vx, vy]
        self.move(int(round(self._phys_pos[0])), int(round(self._phys_pos[1])))
        speed = math.hypot(self._phys_vel[0], self._phys_vel[1])
        # 在地面上且水平速度也很低时，彻底停下
        if physics_mod.is_at_rest(
            self._phys_pos[1], self._phys_vel[0], self._phys_vel[1], bottom, bounced, speed
        ):
            self._stop_physics()
            self._save_position()

    def _request_quit(self) -> None:
        self._save_position()
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
        self._speech_bubble.reposition(self.visible_content_rect())
        for listener in tuple(self._position_listeners):
            try:
                listener(self)
            except Exception:
                logging.exception("\u684c\u5ba0\u4f4d\u7f6e\u76d1\u542c\u5668\u6267\u884c\u5931\u8d25")

    def closeEvent(self, event) -> None:  # noqa: N802
        self._save_position()
        self._self_talk_timer.stop()
        self._cancel_animation_gap()
        self._speech_bubble.hide()
        super().closeEvent(event)
