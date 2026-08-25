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
import logging
import math
import os
import random
import sys
import threading
import time
import webbrowser
from pathlib import Path

from PySide6.QtCore import QElapsedTimer, QPoint, QRect, Qt, QTimer, Signal
from PySide6.QtGui import QBitmap, QColor, QImage, QPainter, QPixmap
from PySide6.QtWidgets import QApplication, QMenu, QToolTip, QWidget

from . import autostart as autostart_mod
from . import catalog
from .config import (
    DEFAULT_SELF_TALK_MAX_INTERVAL,
    DEFAULT_SELF_TALK_MIN_INTERVAL,
    DEFAULT_SELF_TALK_TEXTS,
    Config,
)
from .harness_launcher import launch_harness_gui
from .library import MovieLibrary
from . import physics as physics_mod
from . import vision as vision_mod
from .speech_bubble import PetSpeechBubble
from .updater import QUARK_PAN_URL, REPO_URL


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

        # [view window] —— 无参，返回 NSWindow*
        msg.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        window = msg(ctypes.c_void_p(view_id), sel_window)
        if not window:
            return False

        # [window setLevel:level] —— 一个 NSInteger 参数
        msg.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_long]
        msg(ctypes.c_void_p(window), sel_set_level, level)
        return True
    except Exception:
        return False


def _win_set_topmost(hwnd: int, on: bool) -> bool:
    """Windows 原生：SetWindowPos(HWND_TOPMOST / HWND_NOTOPMOST) 强制置顶/取消。

    Qt 的 WindowStaysOnTopHint 在资源管理器重启、分辨率/DPI 变更、休眠唤醒、
    显卡驱动更新等系统事件后可能丢失（Qt 已知问题 QTBUG-30359），这里在
    每次窗口显示时用 Win32 直接重设，作为 Qt hint 之外的兜底。
    """
    if sys.platform != 'win32':
        return False
    try:
        import ctypes
        from ctypes import wintypes

        HWND_TOPMOST = -1
        HWND_NOTOPMOST = -2
        SWP_NOSIZE = 0x0001
        SWP_NOMOVE = 0x0002
        SWP_NOACTIVATE = 0x0010
        user32 = ctypes.windll.user32
        # 64 位下 HWND 是指针，不声明 argtypes 会被截断成 32 位导致无效句柄
        user32.SetWindowPos.argtypes = [
            wintypes.HWND, wintypes.HWND,
            ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
            ctypes.c_uint,
        ]
        user32.SetWindowPos.restype = wintypes.BOOL
        return bool(user32.SetWindowPos(
            wintypes.HWND(hwnd),
            wintypes.HWND(HWND_TOPMOST if on else HWND_NOTOPMOST),
            0, 0, 0, 0,
            SWP_NOSIZE | SWP_NOMOVE | SWP_NOACTIVATE,
        ))
    except Exception:
        return False


def _win_is_topmost(hwnd: int) -> bool:
    """Windows：查询窗口是否带 WS_EX_TOPMOST 扩展样式。"""
    if sys.platform != 'win32':
        return False
    try:
        import ctypes
        from ctypes import wintypes

        GWL_EXSTYLE = -20
        WS_EX_TOPMOST = 0x00000008
        user32 = ctypes.windll.user32
        user32.GetWindowLongPtrW.restype = ctypes.c_ssize_t
        user32.GetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int]
        style = user32.GetWindowLongPtrW(wintypes.HWND(hwnd), GWL_EXSTYLE)
        return bool(style & WS_EX_TOPMOST)
    except Exception:
        return False


def _squash_geometry(
    window_width: int,
    window_height: int,
    frame_width: int,
    frame_height: int,
    progress: float,
) -> tuple[int, int, int, int]:
    """返回 Q 弹帧的逻辑坐标，避免把 DPR 物理像素当成 QWidget 坐标。

    只压扁高度（sy 0.85，底部贴地）：窗口尺寸与窗口 mask 都按原始帧
    生成，宽度放大（sx>1）会让角色伸出 mask/窗口边界被裁剪成透明
    边缘——贴近边缘的耳朵等部位会看起来"被挡住"。压扁效果由高度
    压缩 + 底部对齐提供，无需宽度膨胀。
    """
    progress = max(0.0, min(1.0, float(progress)))
    pulse = math.sin(math.pi * progress)
    sy = 1.0 - 0.15 * pulse
    width = max(1, int(round(frame_width)))
    height = max(1, int(round(frame_height * sy)))
    x = int(round((window_width - width) / 2))
    y = window_height - height
    return x, y, width, height

def wander_target_y(start_y: float, top: float, bottom: float, height: float,
                    margin: float, rnd=random) -> int:
    """纵向游走目标 y：当前位置 ±25% 可用高度内随机，夹在可用区内。
    可用区不足时返回原 y（退化为纯水平移动）。可注入 rnd 便于测试。"""
    y_lo = top + margin
    y_hi = bottom - height - margin
    if y_hi <= y_lo:
        return int(start_y)
    max_dy = max(40, int((y_hi - y_lo) * 0.25))
    dy = rnd.randint(-max_dy, max_dy)
    return int(max(y_lo, min(y_hi, start_y + dy)))



def _make_placeholder_pixmap(character_id: str, scale: float) -> QPixmap:
    """解码失败时的占位画面：半透明圆 + 角色首字，窗口不至于完全不可见。

    触发场景：ffmpeg 视频解码组件不可用（如被杀毒软件隔离/删除）时
    WebMClip 首帧解码失败、currentPixmap() 返回 None；用占位帧代替
    空画面，既避免 NoneType 崩溃，也让用户看到桌宠仍在运行。
    """
    w = max(1, int(round(catalog.CANVAS_W * scale)))
    h = max(1, int(round(catalog.CANVAS_H * scale)))
    img = QImage(w, h, QImage.Format.Format_ARGB32)
    img.fill(Qt.GlobalColor.transparent)
    p = QPainter(img)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor(90, 140, 220, 190))
    p.drawEllipse(0, 0, w, h)
    ch = (str(character_id or '宠').strip()[:1]) or '宠'
    font = p.font()
    font.setPixelSize(max(8, int(h * 0.5)))
    p.setFont(font)
    p.setPen(QColor(255, 255, 255, 255))
    p.drawText(QRect(0, 0, w, h), Qt.AlignmentFlag.AlignCenter, ch)
    p.end()
    return QPixmap.fromImage(img)

class PetWindow(QWidget):
    """桌宠窗口本体。"""

    look_done = Signal(str, str, bool)  # 看看屏幕完成（回复, 记录用的用户文本, 是否失败）

    def __init__(self, lib: MovieLibrary, config: Config) -> None:
        super().__init__()
        self.lib = lib
        self.cfg = config
        self.on_switch_character = None  # 由 app 注入，用于运行时切换角色
        self.on_open_chat = None
        self.on_open_chat_settings = None
        self.on_open_settings = None
        self.on_check_update = None  # 由 app 注入：检查更新（含直接下载）
        self.on_show_balance = None  # 由 app 注入：DeepSeek 余额气泡
        self.on_look_synced = None   # 由 app 注入：看看屏幕结果同步到 AI 对话
        self._position_listeners = []

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
        self.click_show_balance: bool = bool(config.get('click_show_balance', False))
        self.click_show_self_talk: bool = bool(config.get('click_show_self_talk', False))
        # 点击行为序列播放器（余额 → 间隔 → 自言自语；多次点击重置，只按最后一次完整显示）
        self._click_effect_timer = QTimer(self)
        self._click_effect_timer.setSingleShot(True)
        self._click_effect_timer.timeout.connect(self._on_click_effect_timeout)
        self._click_effect_phase = 0
        self.animation_gap_seconds: float = max(0.0, min(3600.0, float(config.get('animation_gap_seconds', 0.0))))
        self._animation_gap_active = False
        self._animation_gap_timer = QTimer(self)
        self._animation_gap_timer.setSingleShot(True)
        self._animation_gap_timer.timeout.connect(self._on_animation_gap_timeout)
        self._speech_bubble = PetSpeechBubble()
        self._look_busy = False  # 看看屏幕请求进行中
        self._last_look_ts = 0.0  # 上次成功发起看看屏幕的时间（冷却用）
        self.look_done.connect(self._on_look_done)
        self._self_talk_enabled = bool(config.get('self_talk_enabled', False))
        self._self_talk_texts = self._read_self_talk_texts(config.get('self_talk_texts'))
        self._self_talk_min_interval = max(5.0, float(config.get('self_talk_min_interval', DEFAULT_SELF_TALK_MIN_INTERVAL)))
        self._self_talk_max_interval = max(self._self_talk_min_interval, float(config.get('self_talk_max_interval', DEFAULT_SELF_TALK_MAX_INTERVAL)))
        self._self_talk_timer = QTimer(self)
        self._self_talk_timer.setSingleShot(True)
        self._self_talk_timer.timeout.connect(self._on_self_talk_timeout)

        # 置顶保活看门狗：系统事件（explorer 重启/DPI 变更/休眠唤醒）或
        # z-order 竞争可能让置顶丢失，5s 巡检一次，丢失则原生重设。
        self._topmost_watchdog = QTimer(self)
        self._topmost_watchdog.setInterval(5000)
        self._topmost_watchdog.timeout.connect(self._enforce_topmost)
        if config.get('on_top', True):
            self._topmost_watchdog.start()

        # ---- 窗口属性：无边框 + 透明 + 不进任务栏；置顶可配置 ----
        flags = Qt.WindowType.FramelessWindowHint | Qt.WindowType.Tool
        if config.get('on_top', True):
            flags |= Qt.WindowType.WindowStaysOnTopHint
        self.setWindowFlags(flags)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        if self.mouse_through:
            self.setWindowFlag(Qt.WindowType.WindowTransparentForInput, True)
        if sys.platform == 'darwin' and config.get('on_top', True):
            # macOS 上 Tool 窗口的置顶由 WA_MacAlwaysShowToolWindow 控制，
            # WindowStaysOnTopHint 对 Tool 窗口不可靠（Qt 官方已知问题 QTBUG-38580）
            self.setAttribute(Qt.WidgetAttribute.WA_MacAlwaysShowToolWindow, True)

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
        self._trail: list = []  # 拖拽途中鼠标轨迹采样 [(t, x, y)]，松手时用它估算抛掷初速

        # ---- 全屏应用自动隐藏（Windows）----
        # 前台窗口覆盖整个屏幕几何（含任务栏区域）时自动隐藏桌宠，
        # 全屏退出后自动恢复。最大化窗口不覆盖任务栏，不会误触发。
        self.auto_hide_fullscreen: bool = bool(config.get('auto_hide_fullscreen', True))
        self._auto_hidden = False  # 只恢复“由本 watcher 隐藏”的状态，尊重手动隐藏
        self._fullscreen_timer = QTimer(self)
        self._fullscreen_timer.setInterval(1000)
        self._fullscreen_timer.timeout.connect(self._check_fullscreen)

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
        if self.auto_hide_fullscreen and os.name == 'nt':
            self._fullscreen_timer.start()

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
        self.update()
        self._save_position()

    # ================================================================ 位置
    def _screen_available(self):
        """窗口所在屏幕；macOS 上 self.screen() 可能失效，兜底主屏。"""
        from PySide6.QtGui import QGuiApplication
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
        scr = self._screen_available()
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
        self.cfg.set('facing', self.facing)
        self.cfg.set('scale', self.scale)
        self.cfg.save()

    def _go_default_corner(self) -> None:
        scr = self._screen_available()
        avail = scr.availableGeometry()
        x = avail.right() - self._w - catalog.CORNER_MARGIN
        y = avail.bottom() - self._h
        logging.info('回到右下角 screen=%s avail=(%d,%d,%d,%d) dpr=%s -> (%d,%d)',
                     scr.name(), avail.left(), avail.top(), avail.right(),
                     avail.bottom(), scr.devicePixelRatio(), x, y)
        self.move(x, y)
        self._save_position()

    def set_on_top(self, on: bool) -> None:
        if sys.platform == 'darwin':
            # 先设属性再改 flag：setWindowFlag 触发窗口重建时一并应用
            self.setAttribute(Qt.WidgetAttribute.WA_MacAlwaysShowToolWindow, on)
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, on)
        self.cfg.set('on_top', on)
        self.cfg.save()
        self.show()
        if sys.platform == 'darwin':
            # 延迟到 Qt 窗口重建完成后再强制原生层级，避免被 Qt 覆盖
            QTimer.singleShot(0, lambda: _mac_set_window_level(int(self.winId()), 3 if on else 0))
        elif sys.platform == 'win32':
            QTimer.singleShot(0, lambda: _win_set_topmost(int(self.winId()), on))
        if on:
            self._topmost_watchdog.start()
            self.raise_()
        else:
            self._topmost_watchdog.stop()

    def showEvent(self, event) -> None:  # noqa: N802 (Qt 命名)
        """窗口显示时校正层级（延迟执行，避免被 Qt 窗口重建覆盖）。"""
        super().showEvent(event)
        on = bool(self.cfg.get('on_top', True))
        if sys.platform == 'darwin':
            QTimer.singleShot(0, lambda: _mac_set_window_level(int(self.winId()), 3 if on else 0))
        elif sys.platform == 'win32':
            QTimer.singleShot(0, lambda: _win_set_topmost(int(self.winId()), on))

    def _enforce_topmost(self) -> None:
        """置顶看门狗巡检：仅在置顶开启且可见（未被全屏隐藏）时动作。

        Windows：检测 WS_EX_TOPMOST 丢失才原生重设（避免无效 SetWindowPos，
        且 _win_set_topmost 正确声明了 argtypes，不会截断 64 位 HWND）。
        其他平台：raise_() 兜底。
        覆盖资源管理器重启、DPI 变更、休眠唤醒、z-order 竞争等场景。
        """
        if not bool(self.cfg.get('on_top', True)) or not self.isVisible() or self._auto_hidden:
            return
        if sys.platform == 'win32':
            try:
                hwnd = int(self.winId())
            except Exception:
                return
            if not _win_is_topmost(hwnd):
                if _win_set_topmost(hwnd, True):
                    logging.info('检测到置顶丢失，已重新置顶（watchdog）')
        else:
            self.raise_()

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
        if pm is None or pm.isNull():
            # 解码失败（如 ffmpeg 被杀毒软件隔离/删除，_current_pixmap 为 None）：
            # 用占位帧降级，避免 'NoneType' object has no attribute 'isNull' 崩溃。
            pm = self._placeholder_pixmap()
        img = pm.toImage()
        if self.facing == 'right' and self.anim not in getattr(self.lib, 'no_mirror', ()):
            img = img.mirrored(True, False)  # 含文字的动画不镜像（防文字反显）
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

    def _squash_rect(self):
        """当前 Q 弹几何（逻辑坐标）；paintEvent 与 _sync_mask 共用，保证一致。"""
        return _squash_geometry(
            self._w,
            self._h,
            int(round(catalog.CANVAS_W * self.scale)),
            int(round(catalog.CANVAS_H * self.scale)),
            self._squash_progress,
        )

    def _sync_mask(self) -> None:
        """按当前帧 alpha 设置窗口 mask：透明区域鼠标穿透到下层窗口。

        Q 弹期间 mask 必须与压扁画面使用同一几何——压扁画面底部贴窗底、
        角色整体下移，若 mask 仍按原始帧位置绘制，下移后的耳朵/头顶装饰
        会落在原始轮廓之外被 mask 裁剪（表现为"耳朵被挡"）。
        """
        canvas = QImage(self._w, self._h, QImage.Format.Format_ARGB32)
        canvas.fill(Qt.GlobalColor.transparent)
        p = QPainter(canvas)
        if self._squash_active:
            x, y, w, h = self._squash_rect()
            if self._frame_pixmap is not None:
                p.drawPixmap(x, y, w, h, self._frame_pixmap)
        else:
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
                x, y, w, h = self._squash_rect()
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

    def _placeholder_pixmap(self) -> QPixmap:
        """解码失败降级用的占位帧（半透明圆 + 角色首字），按 scale 缓存。"""
        cache = self.__dict__.get('_placeholder_cache')
        if cache is None or cache[0] != self.scale:
            cache = (
                self.scale,
                _make_placeholder_pixmap(
                    str(self.cfg.get('character', catalog.DEFAULT_CHARACTER)),
                    self.scale,
                ),
            )
            self._placeholder_cache = cache
        return cache[1]

    def icon_pixmap(self, size: int = 64) -> QPixmap:
        """托盘图标：取当前帧（无则待机首帧）缩放；均不可用则占位帧。"""
        pm = self._frame_pixmap
        if (pm is None or pm.isNull()) and self.idle:
            pm = self.lib.movie(self.idle).currentPixmap()
        if pm is None or pm.isNull():
            pm = self._placeholder_pixmap()
        return pm.scaled(size, size,
                         Qt.AspectRatioMode.KeepAspectRatio,
                         Qt.TransformationMode.SmoothTransformation)

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
        avail = self.screen().availableGeometry()
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
        # 纵向游走：约一半的移动附带竖直位移。走路动画只有左右朝向，
        # 竖直分量跟随同一进度曲线，看起来是"溜达过去"而非竖直平移
        start_y = self.y()
        target_y = start_y
        if random.random() < 0.55:
            target_y = wander_target_y(
                start_y, float(avail.top()), float(avail.bottom()),
                float(self._h), float(catalog.MOVE_MARGIN))
        move_name = name or self._pick(self.moves)
        duration = self.lib.duration(move_name)
        self._switch(move_name)
        self._move_plan = {
            'start_x': self.x(),
            'target_x': int(round(target_cx - half_w)),
            'start_y': start_y,
            'target_y': target_y,
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
            x, y = plan['start_x'], plan['start_y']
        elif t >= dur - tail:
            x, y = plan['target_x'], plan['target_y']
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
            self._phys_vel = [0.0, 0.0]
            self._trail = [(time.monotonic(), self._press_global.x(), self._press_global.y())]
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
            self._trail.append((time.monotonic(), g.x(), g.y()))
            event.accept()
            return

        # 已经处于拖拽中
        if self.drag_physics:
            now = time.monotonic()
            self._trail.append((now, g.x(), g.y()))
            # 只保留最近一段轨迹，松手初速由这段窗口估算
            cutoff = now - physics_mod.TRAIL_KEEP_SEC
            self._trail = [s for s in self._trail if s[0] >= cutoff]
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
            QTimer.singleShot(150, self._clear_just_dragged)
            if self.drag_physics:
                # 松手初速：轨迹窗口定方向 + 峰值加权定大小 + 末段加速度增益
                # + 软上限——甩得越快/越在加速，飞得越快（见 pet/physics.py 与单测）
                rvx, rvy = physics_mod.estimate_release_velocity(self._trail, time.monotonic())
                if math.hypot(rvx, rvy) < physics_mod.DEAD_ZONE_SPEED:
                    if self._grab_offset is not None:
                        self.move(g - self._grab_offset)  # 慢放：原地放下
                    self._stop_physics()
                    self._save_position()
                else:
                    self._phys_vel[0] = rvx
                    self._phys_vel[1] = rvy
                    self._physics_mode = 'throw'
                    self._physics_timer.start()
            else:
                if self._grab_offset is not None:
                    self.move(g - self._grab_offset)  # 停在松手处
                self._save_position()
            if self.idles:
                self._switch(self._pick(self.idles))  # 回待机缓冲
            if sys.platform == 'win32':
                self.raise_()  # 拖拽结束把自己带回置顶组最前（不抢键盘焦点）
        elif dist < catalog.DRAG_THRESHOLD * self.scale:
            self._on_click()
        self._dragging = False
        self._press_global = None
        self._grab_offset = None
        event.accept()

    # ================================================================ 看看屏幕
    def _on_look_screen(self) -> None:
        """右键「看看屏幕」：截屏发给视觉模型，让她用人设口吻吐槽主人在干嘛。"""
        if self._look_busy:
            self._speech_bubble.show_text('上一张还没看完呢…', self.visible_content_rect())
            return
        now = time.monotonic()
        if now - self._last_look_ts < 4.0:  # 4s 冷却，防连点刷请求
            self._speech_bubble.show_text('喘口气嘛，刚看过啦…', self.visible_content_rect())
            return
        self._last_look_ts = now
        self._look_busy = True
        self._speech_bubble.show_text('让我看看…', self.visible_content_rect())
        threading.Thread(target=self._look_worker, daemon=True).start()

    def _look_worker(self) -> None:
        try:
            settings = self.cfg.chat_settings()
            provider = settings.active_config
            provider.api_key = self.cfg.resolve_api_key(provider)
            shot = vision_mod.capture_screen(self.cfg.dir / 'screenshots')
            app_info = vision_mod.foreground_app_info()
            reply = vision_mod.ask_about_screen(
                shot, app_info,
                settings.default_system_prompt, provider,
            )
            user_text = f'[看看屏幕] 前台窗口：{app_info}' if app_info else '[看看屏幕]'
            self.look_done.emit(reply, user_text, False)
        except Exception as exc:  # noqa: BLE001 - 任何失败都走气泡提示
            logging.getLogger('dsh-pet-standalone').exception('看看屏幕失败')
            self.look_done.emit(str(exc), '', True)

    def _on_look_done(self, text: str, user_text: str, is_error: bool) -> None:
        self._look_busy = False
        if is_error:
            self._speech_bubble.show_text(f'看不清啊…{text[:60]}', self.visible_content_rect(), 5000)
        else:
            self._speech_bubble.show_text(text, self.visible_content_rect(), max(4000, min(12000, len(text) * 150)))
            # 同步到 AI 对话当前会话（由 app 注入回调；聊天窗未打开时跳过）
            if self.on_look_synced is not None:
                self.on_look_synced(user_text, text)

    def _schedule_click_effects(self) -> None:
        """点击行为序列：余额 →（间隔 1s）→ 自言自语。

        多次点击会重置：取消上一次未完成的序列，只按最后一次点击的
        配置从头完整显示（防抖，避免点击洪峰叠出一堆气泡）。
        """
        self._click_effect_timer.stop()
        self._click_effect_phase = 0
        self._run_click_effects()

    def _run_click_effects(self) -> None:
        # 阶段 0：余额气泡（含查询，展示约 6s），随后隔 1s 进入下一项
        if self._click_effect_phase == 0 and self.click_show_balance and self.on_show_balance is not None:
            self.on_show_balance(self)
            self._click_effect_phase = 1
            self._click_effect_timer.start(7000)
            return
        # 阶段 1：随机一条用户自定义自言自语
        if self._click_effect_phase <= 1 and self.click_show_self_talk and self._self_talk_enabled and self._self_talk_texts:
            text = random.choice(self._self_talk_texts)
            self._speech_bubble.show_text(text, self.visible_content_rect())
            self._click_effect_phase = 2
        # 序列结束（无更多阶段）

    def _on_click_effect_timeout(self) -> None:
        self._run_click_effects()

    def _clear_just_dragged(self) -> None:
        self._just_dragged = False

    def _play_click_sound(self) -> None:
        """点击 Q 弹音效：内置 assets/sounds/click.wav，可用用户数据目录 sounds/ 覆盖。

        Windows 用 winsound（系统内置，零依赖）；其他平台用 afplay。
        """
        if not self.click_sound_enabled:
            return
        path = self._find_click_sound()
        if path is None:
            return
        try:
            if os.name == 'nt':
                import winsound
                winsound.PlaySound(str(path), winsound.SND_FILENAME | winsound.SND_ASYNC)
            else:
                import subprocess
                subprocess.Popen(
                    ['afplay', str(path)],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                )
        except Exception:
            pass

    def _find_click_sound(self):
        """音效查找顺序：用户数据目录 sounds/click.wav（可自定义替换）→ 内置 assets/sounds。

        onedir 打包后数据在 sys._MEIPASS（_internal）下，不能用 exe 所在目录。
        """
        candidates = [
            self.cfg.dir / 'sounds' / 'click.wav',
        ]
        meipass = getattr(sys, '_MEIPASS', None)
        if meipass:
            candidates.append(Path(meipass) / 'assets' / 'sounds' / 'click.wav')
        else:
            candidates.append(Path(__file__).resolve().parent.parent / 'assets' / 'sounds' / 'click.wav')
        for path in candidates:
            if path.is_file():
                return path
        return None

    def _on_click(self) -> None:
        """真点击 → 随机一个点击回应动画，并重置当前动画（可连续点击打断）。"""
        if self._just_dragged:
            return
        if not self.clicks:
            return
        # 点击可以打断当前动画（包括正在播放的点击回应），实现连续 Q 弹
        self._cancel_move()
        self._play_click_sound()
        self._schedule_click_effects()
        # 先切动画再启动 Q 弹：squash 压扁的是新动画首帧，
        # 避免 Q 弹期间显示上一动画的帧残留（旧顺序会先画旧帧）。
        self._switch(self._pick(self.clicks))
        self._start_squash()
        if sys.platform == 'win32':
            self.raise_()  # 交互时把桌宠带回置顶组最前（不抢键盘焦点）

    def contextMenuEvent(self, event) -> None:  # noqa: N802
        if not self._is_in_interactive_area(event.pos()):
            return
        menu = QMenu(self)
        if self.on_open_chat is not None:
            menu.addAction('AI 对话', self.on_open_chat)
        if self.on_open_chat_settings is not None:
            menu.addAction('看看屏幕', self._on_look_screen)
            menu.addAction('AI 设置', self.on_open_chat_settings)
        if self.on_open_settings is not None:
            menu.addAction('桌宠设置', self.on_open_settings)
        if self.on_open_chat is not None or self.on_open_chat_settings is not None or self.on_open_settings is not None:
            menu.addSeparator()

        # ---- 动画（二级菜单：动画 → 分类 → 具体动画，减少右键菜单臃肿） ----
        m_anim = menu.addMenu('动画')
        if self.idles:
            m_idle = m_anim.addMenu('待机')
            for n in self.idles:
                m_idle.addAction(n, lambda n=n: self._switch(n))
        if self.turns:
            m_turn = m_anim.addMenu('转向')
            for n in self.turns:
                m_turn.addAction(n, lambda n=n: self._switch(n))
        m_moves = m_anim.addMenu('移动')
        for n in self.moves:
            m_moves.addAction(n, lambda n=n: self._trigger_move(n))
        m_clicks = m_anim.addMenu('点击回应')
        for n in self.clicks:
            m_clicks.addAction(n, lambda n=n: self._switch(n))
        m_acts = m_anim.addMenu('随机动作')
        for n in self.acts:
            m_acts.addAction(n, lambda n=n: self._switch(n))
        m_speed = m_anim.addMenu('播放速率')
        for i in range(10, 21):
            v = i / 10.0
            act = m_speed.addAction(f'{v:.1f}x')
            act.setCheckable(True)
            act.setChecked(abs(self.playback_speed - v) < 0.01)
            act.triggered.connect(lambda checked=False, v=v: self.set_playback_speed(v))

        # 常用开关放主菜单（不进「动画」二级菜单）
        drag_physics_act = menu.addAction('拖动物理')
        drag_physics_act.setCheckable(True)
        drag_physics_act.setChecked(self.drag_physics)
        drag_physics_act.toggled.connect(self.set_drag_physics)

        m_char = menu.addMenu('切换角色')
        current = str(self.cfg.get('character', catalog.DEFAULT_CHARACTER))
        for cid in catalog.list_available_characters():
            act = m_char.addAction(cid)
            act.setCheckable(True)
            act.setChecked(cid == current)
            act.triggered.connect(lambda checked=False, cid=cid: self._request_switch_character(cid))

        menu.addSeparator()
        menu.addAction('回到右下角', self._go_default_corner)
        menu.addAction('隐藏桌宠', self.hide)

        on_top = menu.addAction('窗口置顶')
        on_top.setCheckable(True)
        on_top.setChecked(bool(self.cfg.get('on_top', True)))
        on_top.toggled.connect(self.set_on_top)

        no_move = menu.addAction('不移动')
        no_move.setCheckable(True)
        no_move.setChecked(self.no_move)
        no_move.toggled.connect(self.set_no_move)

        m_scale = menu.addMenu('大小')
        for s in catalog.SCALE_STEPS:
            px = int(round(catalog.CANVAS_W * s))
            act = m_scale.addAction(f'{px}px')
            act.setCheckable(True)
            act.setChecked(abs(self.scale - s) < 0.02)
            act.triggered.connect(lambda checked=False, s=s: self.change_scale(s))

        menu.addSeparator()
        if self.on_show_balance is not None:
            menu.addAction('DeepSeek 余额', lambda: self.on_show_balance(self))
        # 更新/帮助：检查更新 + 下载渠道收进二级菜单
        m_update = menu.addMenu('更新 / 帮助')
        if self.on_check_update is not None:
            m_update.addAction('检查更新', lambda: self.on_check_update(self))
        m_update.addAction('GitHub 项目页', lambda: webbrowser.open(REPO_URL))
        if sys.platform == 'win32':
            m_update.addAction('夸克网盘下载', lambda: webbrowser.open(QUARK_PAN_URL))
        menu.addAction('启动 DeepSeek Harness', lambda: launch_harness_gui(self))
        menu.addSeparator()
        menu.addAction('退出', self._request_quit)
        menu.exec(event.globalPos())

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

    def _schedule_self_talk(self) -> None:
        self._self_talk_timer.stop()
        if not self._self_talk_enabled or not self._self_talk_texts:
            return
        delay = random.uniform(self._self_talk_min_interval, self._self_talk_max_interval)
        self._self_talk_timer.start(max(1000, int(round(delay * 1000))))

    def _on_self_talk_timeout(self) -> None:
        if self._self_talk_enabled and self._self_talk_texts and self.isVisible():
            self._speech_bubble.show_text(random.choice(self._self_talk_texts), self.visible_content_rect())
        self._schedule_self_talk()

    def show_bubble(self, text: str, duration_ms: int = 3200) -> None:
        """向桌宠头顶冒泡提示（app 层反馈用，非侵入）。"""
        self._speech_bubble.show_text(text, self.visible_content_rect(), duration_ms)

    def refresh_pet_settings(self) -> None:
        self.animation_gap_seconds = max(0.0, min(3600.0, float(self.cfg.get('animation_gap_seconds', 0.0))))
        if self.animation_gap_seconds <= 0:
            self._cancel_animation_gap()
        self._self_talk_enabled = bool(self.cfg.get('self_talk_enabled', False))
        self._self_talk_texts = self._read_self_talk_texts(self.cfg.get('self_talk_texts'))
        self._self_talk_min_interval = max(5.0, float(self.cfg.get('self_talk_min_interval', DEFAULT_SELF_TALK_MIN_INTERVAL)))
        self._self_talk_max_interval = max(self._self_talk_min_interval, float(self.cfg.get('self_talk_max_interval', DEFAULT_SELF_TALK_MAX_INTERVAL)))
        self.click_sound_enabled = bool(self.cfg.get('click_sound_enabled', True))
        self.click_show_balance = bool(self.cfg.get('click_show_balance', False))
        self.click_show_self_talk = bool(self.cfg.get('click_show_self_talk', False))
        self._schedule_self_talk()

    def set_animation_gap(self, seconds: float) -> None:
        self.animation_gap_seconds = max(0.0, min(3600.0, float(seconds)))
        self.cfg.set('animation_gap_seconds', self.animation_gap_seconds)
        self.cfg.save()
        if self.animation_gap_seconds <= 0:
            self._cancel_animation_gap()

    def set_self_talk_settings(self, enabled: bool, minimum: float, maximum: float, texts) -> None:
        self._self_talk_enabled = bool(enabled)
        self._self_talk_min_interval = max(5.0, float(minimum))
        self._self_talk_max_interval = max(self._self_talk_min_interval, float(maximum))
        self._self_talk_texts = self._read_self_talk_texts(texts)
        self.cfg.set('self_talk_enabled', self._self_talk_enabled)
        self.cfg.set('self_talk_min_interval', self._self_talk_min_interval)
        self.cfg.set('self_talk_max_interval', self._self_talk_max_interval)
        self.cfg.set('self_talk_texts', list(self._self_talk_texts))
        self.cfg.save()
        self._schedule_self_talk()

    def set_chat_status(self, state: str, text: str = '') -> None:
        if not text:
            return
        self._speech_bubble.show_text(text, self.visible_content_rect(), duration_ms=2200)
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
        if self.mouse_through and sys.platform == 'win32':
            # 非侵入提示：穿透期间桌宠无法被点击唤回置顶组最前
            self._speech_bubble.show_text(
                '鼠标穿透已开启：点击会穿透到桌面；'
                '需要点击桌宠或唤回置顶时，请关闭穿透。',
                self.visible_content_rect(),
                duration_ms=4200,
            )

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
        # 弹簧跟随 + 过阻尼（ζ≈1.06）：紧致跟手、不 overshoot，
        # 鼠标速度只在松手时作为抛掷初速（见 mouseReleaseEvent），不在此处注入
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
        left = float(avail.left() - margin)
        top = float(avail.top())
        right = float(avail.right() - self._w + margin)
        bottom = float(avail.bottom() - self._h)
        px, py, vx, vy, bounced = physics_mod.throw_step(
            self._phys_pos[0], self._phys_pos[1],
            self._phys_vel[0], self._phys_vel[1],
            dt, left, top, right, bottom)
        self._phys_pos = [px, py]
        self._phys_vel = [vx, vy]
        self.move(int(round(px)), int(round(py)))
        # 贴地且双轴低速（或碰边后整体低速）时彻底停下
        if physics_mod.is_at_rest(py, vx, vy, bottom, bounced, math.hypot(vx, vy)):
            self._stop_physics()
            self._save_position()

    # ================================================================ 全屏自动隐藏
    _FS_SKIP_CLASSES = {
        'Progman', 'WorkerW', 'Shell_TrayWnd', 'Shell_SecondaryTrayWnd',
        'Windows.UI.Core.CoreWindow',  # 开始菜单/通知中心全屏层
    }

    def _foreground_covers_fullscreen(self) -> bool:
        """前台窗口是否覆盖整个屏幕几何（含任务栏）= 真全屏。仅 Windows。

        只判定真全屏（全屏视频/游戏/浏览器 F11，窗口覆盖含任务栏的
        全屏几何）；普通最大化窗口（任务栏未被覆盖）不触发隐藏。

        注意：GetWindowRect 返回物理像素，而 Qt geometry 是逻辑坐标——
        高 DPI（125%/150%）下直接比较会把最大化窗口误判为"覆盖全屏"
        （物理边界 > 逻辑边界）。必须统一换算到逻辑像素。
        """
        if os.name != 'nt':
            return False
        try:
            u32 = ctypes.windll.user32
            hwnd = u32.GetForegroundWindow()
            if not hwnd:
                return False
            # 排除本进程（桌宠自身/聊天窗/设置窗）
            pid = ctypes.c_ulong(0)
            u32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            if pid.value == os.getpid():
                return False
            # 排除桌面/任务栏等 shell 窗口
            buf = ctypes.create_unicode_buffer(256)
            u32.GetClassNameW(hwnd, buf, 256)
            if buf.value in self._FS_SKIP_CLASSES:
                return False

            class RECT(ctypes.Structure):
                _fields_ = [('left', ctypes.c_long), ('top', ctypes.c_long),
                            ('right', ctypes.c_long), ('bottom', ctypes.c_long)]
            rect = RECT()
            if not u32.GetWindowRect(hwnd, ctypes.byref(rect)):
                return False
            # 窗口中心点（物理像素）→ 先定位屏幕，再用该屏 DPR 换算逻辑坐标
            cx = (rect.left + rect.right) // 2
            cy = (rect.top + rect.bottom) // 2
            scr = QApplication.screenAt(QPoint(cx, cy)) or self.screen()
            if scr is None:
                return False
            dpr = scr.devicePixelRatio()
            if dpr and dpr != 1.0:
                scr = QApplication.screenAt(QPoint(int(cx / dpr), int(cy / dpr))) or scr
                dpr = scr.devicePixelRatio()
            # 物理像素 → 逻辑像素
            l, t = rect.left / dpr, rect.top / dpr
            r, b = rect.right / dpr, rect.bottom / dpr
            g = scr.geometry()
            # 覆盖全屏几何（含任务栏）= 真全屏
            return (l <= g.left() and t <= g.top()
                    and r >= g.right() and b >= g.bottom())
        except Exception:
            return False

    def _check_fullscreen(self) -> None:
        if self._foreground_covers_fullscreen():
            if not self._auto_hidden and self.isVisible():
                self._auto_hidden = True
                self._speech_bubble.hide()
                self.hide()
        else:
            if self._auto_hidden:
                self._auto_hidden = False
                self.show()

    def set_auto_hide_fullscreen(self, on: bool) -> None:
        """全屏自动隐藏开关（供设置/菜单调用）。"""
        self.auto_hide_fullscreen = bool(on)
        self.cfg.set('auto_hide_fullscreen', self.auto_hide_fullscreen)
        self.cfg.save()
        if self.auto_hide_fullscreen and os.name == 'nt':
            self._fullscreen_timer.start()
        else:
            self._fullscreen_timer.stop()
            if self._auto_hidden:
                self._auto_hidden = False
                self.show()

    def _request_quit(self) -> None:
        self._save_position()
        QApplication.instance().quit()

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
