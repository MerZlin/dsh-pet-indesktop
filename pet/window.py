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
import sys
import time

from PySide6.QtCore import QElapsedTimer, QPoint, Qt, QTimer
from PySide6.QtGui import QBitmap, QImage, QPainter, QPixmap
from PySide6.QtWidgets import QApplication, QMenu, QWidget

from . import autostart as autostart_mod
from . import catalog
from .config import Config
from .library import MovieLibrary


def _mac_set_window_level(view_id: int, level: int) -> bool:
    """macOS 原生：把 NSWindow 层级设为指定值（3=置顶浮动，0=普通）。

    Qt 的 WindowStaysOnTopHint 在 macOS 上对无边框 Tool 窗口/运行时切换不可靠，
    这里用 objc runtime 直接调 [NSWindow setLevel:] 强制生效（ctypes 零依赖）。
    """
    if sys.platform != 'darwin':
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


class PetWindow(QWidget):
    """桌宠窗口本体。"""

    def __init__(self, lib: MovieLibrary, config: Config) -> None:
        super().__init__()
        self.lib = lib
        self.cfg = config
        self.on_switch_character = None  # 由 app 注入，用于运行时切换角色

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
        self._last_global: QPoint | None = None
        self._last_move_time = 0.0

        # ---- 尺寸与初始状态 ----
        self._apply_scale()
        for name, movie in lib.movies().items():
            # 默认参数捕获 name，避免闭包晚绑定
            movie.frameChanged.connect(lambda n, name=name: self._on_frame(name, n))
        self._restore_position()
        self._switch(self.idle)

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
        if on:
            self.raise_()

    def showEvent(self, event) -> None:  # noqa: N802 (Qt 命名)
        """窗口显示时校正层级（延迟执行，避免被 Qt 窗口重建覆盖）。"""
        super().showEvent(event)
        if sys.platform == 'darwin':
            on = bool(self.cfg.get('on_top', True))
            QTimer.singleShot(0, lambda: _mac_set_window_level(int(self.winId()), 3 if on else 0))

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
                # Q 弹：垂直变矮 + 水平微微变宽，脚底保持不动
                sy = 1.0 - 0.15 * math.sin(math.pi * self._squash_progress)
                sx = 1.0 + 0.10 * math.sin(math.pi * self._squash_progress)
                w = max(1, int(round(self._frame_pixmap.width() * sx)))
                h = max(1, int(round(self._frame_pixmap.height() * sy)))
                x = (self._w - w) // 2
                y = self._h - h
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
        """托盘图标：取当前帧（无则待机首帧）缩放。"""
        pm = self._frame_pixmap
        if pm is None and self.idle:
            pm = self.lib.movie(self.idle).currentPixmap()
        return pm.scaled(size, size,
                         Qt.AspectRatioMode.KeepAspectRatio,
                         Qt.TransformationMode.SmoothTransformation)

    # ================================================================ 动画链
    def _on_anim_ended(self, name: str) -> None:
        if name == self.drag and self._dragging:
            # 超长拖拽：拖拽动画循环重播，继续跟手
            self.movie.jumpToFrame(0)
            self._ended_fired = False
            self.movie.start()
            return
        if name in self.turns:
            # 转向动画播完 → 翻转朝向
            self.facing = 'right' if self.facing == 'left' else 'left'
        if name == self.drag or name in self.clicks:
            # 交互打断的动画播完 → 待机缓冲（一次性），待机播完再进链
            if self.idles:
                self._switch(self._pick(self.idles))
            return
        self._pick_next()

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
        move_name = name or self._pick(self.moves)
        duration = self.lib.duration(move_name)
        self._switch(move_name)
        self._move_plan = {
            'start_x': self.x(),
            'target_x': int(round(target_cx - half_w)),
            'y': self.y(),
            'duration': duration,
        }
        self._move_timer.start()
        return True

    def _trigger_move(self, name: str) -> None:
        """手动触发移动（右键菜单）：先打断当前移动，再朝 facing 方向走动；
        屏幕空间不足则原地播放走路姿态（不位移）。"""
        self._cancel_move()
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
        elif t >= dur - tail:
            x = plan['target_x']
        else:
            progress = (t - lead) / max(0.1, dur - lead - tail)
            x = plan['start_x'] + (plan['target_x'] - plan['start_x']) * progress
        self.move(int(round(x)), plan['y'])
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
            event.accept()
            return

        # 已经处于拖拽中
        if self.drag_physics:
            now = time.monotonic()
            dt = now - self._last_move_time
            if dt > 0 and self._last_global is not None:
                inst_vx = (g.x() - self._last_global.x()) / dt
                inst_vy = (g.y() - self._last_global.y()) / dt
                self._phys_vel[0] = self._phys_vel[0] * 0.6 + inst_vx * 0.4
                self._phys_vel[1] = self._phys_vel[1] * 0.6 + inst_vy * 0.4
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
            QTimer.singleShot(150, self._clear_just_dragged)
            if self.drag_physics:
                # 松手后进入抛掷物理：保留当前速度，重力 + 反弹 + 衰减
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
        if not self.clicks:
            return
        # 点击可以打断当前动画（包括正在播放的点击回应），实现连续 Q 弹
        self._cancel_move()
        self._start_squash()
        self._switch(self._pick(self.clicks))

    def contextMenuEvent(self, event) -> None:  # noqa: N802
        if not self._is_in_interactive_area(event.pos()):
            return
        menu = QMenu(self)

        if self.idles:
            m_idle = menu.addMenu('动画 · 待机')
            for n in self.idles:
                m_idle.addAction(n, lambda n=n: self._switch(n))
        if self.turns:
            m_turn = menu.addMenu('动画 · 转向')
            for n in self.turns:
                m_turn.addAction(n, lambda n=n: self._switch(n))

        m_moves = menu.addMenu('动画 · 移动')
        for n in self.moves:
            m_moves.addAction(n, lambda n=n: self._trigger_move(n))

        m_clicks = menu.addMenu('动画 · 点击回应')
        for n in self.clicks:
            m_clicks.addAction(n, lambda n=n: self._switch(n))

        m_acts = menu.addMenu('动画 · 随机动作')
        for n in self.acts:
            m_acts.addAction(n, lambda n=n: self._switch(n))

        m_speed = menu.addMenu('播放速率')
        for i in range(10, 21):
            v = i / 10.0
            act = m_speed.addAction(f'{v:.1f}x')
            act.setCheckable(True)
            act.setChecked(abs(self.playback_speed - v) < 0.01)
            act.triggered.connect(lambda checked=False, v=v: self.set_playback_speed(v))

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

        on_top = menu.addAction('窗口置顶')
        on_top.setCheckable(True)
        on_top.setChecked(bool(self.cfg.get('on_top', True)))
        on_top.toggled.connect(self.set_on_top)

        no_move = menu.addAction('不移动')
        no_move.setCheckable(True)
        no_move.setChecked(self.no_move)
        no_move.toggled.connect(self.set_no_move)

        auto = menu.addAction('开机自启')
        auto.setCheckable(True)
        auto.setChecked(autostart_mod.is_enabled())
        auto.toggled.connect(autostart_mod.set_enabled)

        m_scale = menu.addMenu('大小')
        for s in catalog.SCALE_STEPS:
            px = int(round(catalog.CANVAS_W * s))
            act = m_scale.addAction(f'{px}px')
            act.setCheckable(True)
            act.setChecked(abs(self.scale - s) < 0.02)
            act.triggered.connect(lambda checked=False, s=s: self.change_scale(s))

        menu.addSeparator()
        menu.addAction('退出', self._request_quit)
        menu.exec(event.globalPos())

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
        # 弹簧跟随 + 阻尼，产生惯性/离心感
        ax = (tx - px) * 80.0 - self._phys_vel[0] * 10.0
        ay = (ty - py) * 80.0 - self._phys_vel[1] * 10.0
        self._phys_vel[0] += ax * dt
        self._phys_vel[1] += ay * dt
        self._phys_pos[0] += self._phys_vel[0] * dt
        self._phys_pos[1] += self._phys_vel[1] * dt
        self.move(int(round(self._phys_pos[0])), int(round(self._phys_pos[1])))

    def _tick_throw_physics(self) -> None:
        dt = 0.016
        self._phys_vel[1] += 1400.0 * dt  # 重力
        self._phys_pos[0] += self._phys_vel[0] * dt
        self._phys_pos[1] += self._phys_vel[1] * dt
        scr = self._screen_available()
        avail = scr.availableGeometry()
        # 忽略左右留白：角色实际可视区域约为窗口中间 1/3，
        # 允许窗口略微超出屏幕边界，让角色形象真正碰到边缘才反弹。
        margin = self._w / 3.0
        left = avail.left() - margin
        top = avail.top()
        right = avail.right() - self._w + margin
        bottom = avail.bottom() - self._h
        bounced = False
        if self._phys_pos[0] < left:
            self._phys_pos[0] = left
            self._phys_vel[0] = abs(self._phys_vel[0]) * 0.78
            bounced = True
        elif self._phys_pos[0] > right:
            self._phys_pos[0] = right
            self._phys_vel[0] = -abs(self._phys_vel[0]) * 0.78
            bounced = True
        if self._phys_pos[1] < top:
            self._phys_pos[1] = top
            self._phys_vel[1] = abs(self._phys_vel[1]) * 0.78
            bounced = True
        elif self._phys_pos[1] >= bottom:
            self._phys_pos[1] = bottom
            # 地面摩擦力：水平速度逐渐衰减，避免一直在地面滑/弹
            friction = 2.5 * dt
            self._phys_vel[0] *= max(0.0, 1.0 - friction)
            if abs(self._phys_vel[1]) < 40:
                self._phys_vel[1] = 0.0
            else:
                self._phys_vel[1] = -abs(self._phys_vel[1]) * 0.78
            bounced = True
        self.move(int(round(self._phys_pos[0])), int(round(self._phys_pos[1])))
        speed = math.hypot(self._phys_vel[0], self._phys_vel[1])
        # 在地面上且水平速度也很低时，彻底停下
        if self._phys_pos[1] >= bottom - 1 and abs(self._phys_vel[1]) < 1 and abs(self._phys_vel[0]) < 15:
            self._stop_physics()
            self._save_position()
        elif bounced and speed < 40 and abs(self._phys_vel[1]) < 1:
            self._stop_physics()
            self._save_position()

    def _request_quit(self) -> None:
        self._save_position()
        QApplication.instance().quit()

    def closeEvent(self, event) -> None:  # noqa: N802
        self._save_position()
        super().closeEvent(event)
