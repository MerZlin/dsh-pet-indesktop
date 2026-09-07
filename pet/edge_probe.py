# -*- coding: utf-8 -*-
"""边缘探头：桌宠真实可见像素贴到屏幕左/右边缘后的持久探头状态机。

状态机由 PetWindow 侧控制器持有，随窗口生命周期存在。隐藏时 pause() 冻结
当前状态与倒计时，恢复后 resume() 从原进度继续。探头会话期间只允许待机/
转向动画，由 WindowFeatureGateMixin._effects_on_switch 在窗口 _switch 入口过滤。
"""
from __future__ import annotations

import time
from typing import Any

from PySide6.QtCore import QEasingCurve, QRect, Qt, QTimer

from .window_effects import eased_progress, rotated_region_bounds

EDGE_PROBE_ANGLE = 45.0
# 露出比例以“当前姿态（含 ±45° 旋转）的投影 bbox 宽度”为分母；0.55 使常驻探头
# 只露头/脸并贴住屏幕边缘，身体大部分留在屏幕外（0.70 曾导致整个角色探出过多）。
EDGE_PEEK_EXPOSURE = 0.55
# 点击拉直后基本全出（短暂查看完整桌宠）。
EDGE_ENGAGE_EXPOSURE = 0.82
EDGE_ENTER_MS = 300
EDGE_STRAIGHTEN_MS = 250
EDGE_RETURN_MS = 300
EDGE_IDLE_SECONDS = 5.0

OFF = "OFF"
ENTERING = "ENTERING"
PEEKING = "PEEKING"
STRAIGHTENING = "STRAIGHTENING"
STRAIGHTENED = "STRAIGHTENED"
RETURNING = "RETURNING"

_TRANSITION_MODES = {ENTERING, STRAIGHTENING, RETURNING}


def probe_window_x(side: str, exposure: float, vis_local, avail) -> int:
    """把真实可见角色按曝光比例放到屏幕边缘的窗口 x 坐标。

    left：左侧 off-screen 一部分，保留从中心往右的 exposure；
    right：右侧 off-screen 一部分，保留从中心往左的 exposure。
    """
    exposure = max(0.0, min(1.0, float(exposure)))
    offscreen = (1.0 - exposure) * vis_local.width()
    if side == "left":
        return int(round(avail.left() - offscreen - vis_local.left()))
    if side == "right":
        return int(round(avail.right() + offscreen - vis_local.right()))
    raise ValueError(f"unknown edge side: {side!r}")


def edge_side_at_rest(win, avail) -> str | None:
    """按真实可见区域判断桌宠是否贴在屏幕左/右边缘。"""
    local = win.character_local_region()
    if local is None or local.isEmpty():
        return None
    frame = win.frameGeometry()
    left = frame.left() + local.left()
    right = frame.left() + local.right()
    tolerance = 0
    if left <= avail.left() + tolerance:
        return "left"
    if right >= avail.right() - tolerance:
        return "right"
    return None


class EdgeProbeController:
    """纯窗口侧状态机；不是 QObject，避免额外父子关系（timer 由窗口提供）。

    控制器持有 win 引用，直接调用窗口公开/半内部方法完成移动与动画约束。
    """

    def __init__(self, win: Any, *, clock=None) -> None:
        self.win = win
        self._clock = clock if callable(clock) else time.monotonic
        self.enabled = bool(getattr(win, "cfg", None) and win.cfg.get("edge_probe_enabled", False))
        self._mode = OFF
        self._side: str | None = None
        self._angle_deg = 0.0
        self._exposure = 1.0
        self._vis_local = None
        self._restore_x: int | None = None
        self._idle_remaining = 0.0
        self._last_tick_time = 0.0
        self._transition_start = 0.0
        self._transition_duration_ms = 0
        self._transition_from_angle = 0.0
        self._transition_to_angle = 0.0
        self._transition_from_exposure = 1.0
        self._transition_to_exposure = 1.0
        self._hidden = False
        self._paused_at = 0.0
        self._timer = QTimer(win)
        self._timer.setInterval(16)
        self._timer.setTimerType(Qt.TimerType.PreciseTimer)
        self._timer.timeout.connect(self._on_timer)

    # ------------------------------------------------------------ 查询
    @property
    def active(self) -> bool:
        return self._mode != OFF

    @property
    def mode(self) -> str:
        return self._mode

    @property
    def side(self) -> str | None:
        return self._side

    def current_angle_deg(self) -> float:
        return self._angle_deg if self.active else 0.0

    def current_exposure(self) -> float:
        return self._exposure if self.active else 1.0

    # ------------------------------------------------------------ 开关
    def set_enabled(self, on: bool) -> None:
        on = bool(on)
        if on == self.enabled:
            self.enabled = on
            return
        self.enabled = on
        if not on:
            self.cancel("feature_off", restore=True)

    # ------------------------------------------------------------ 进入/取消
    def on_release(self, was_dragging: bool) -> None:
        if not self.enabled or self._hidden:
            return
        if was_dragging:
            # 拖拽中已由 on_drag_started 取消会话；这里仅在拖拽释放时按最终位置
            # 重新评估是否应进入探头（例如把宠物从别处拖到边缘再松手）。
            if self.active:
                self.cancel("drag_away", restore=False)
            self._maybe_enter()
        # 非拖拽点击走窗口 _on_click -> _effects_consume_click -> on_clicked

    def on_drag_started(self) -> None:
        if self.active and self.enabled and not self._hidden:
            self.cancel("drag_away", restore=False)

    def on_clicked(self) -> None:
        """点击真实角色区：PEEKING 转直并拉出；STRAIGHTENED 重置 5s 倒计时。"""
        if not self.active or self._hidden:
            return
        if self._mode == PEEKING:
            self._begin_transition(
                STRAIGHTENING,
                EDGE_STRAIGHTEN_MS,
                to_angle=0.0,
                to_exposure=EDGE_ENGAGE_EXPOSURE,
            )
        elif self._mode == STRAIGHTENED:
            self._idle_remaining = EDGE_IDLE_SECONDS
        elif self._mode == RETURNING:
            self._begin_transition(
                STRAIGHTENING,
                EDGE_STRAIGHTEN_MS,
                to_angle=0.0,
                to_exposure=EDGE_ENGAGE_EXPOSURE,
            )
        elif self._mode == ENTERING or self._mode == STRAIGHTENING:
            # 已在转直/进入中，不重复打断；若已接近转直则重置倒计时稍后由状态落定处理。
            pass

    def cancel(self, reason: str = "", restore: bool = False) -> None:
        """取消会话。restore=True 时恢复进入探头前的窗口 x（用于关闭功能/角色切换）。"""
        was_active = self.active
        self._mode = OFF
        self._side = None
        self._angle_deg = 0.0
        self._exposure = 1.0
        self._idle_remaining = 0.0
        self._timer.stop()
        restore_x = self._restore_x
        self._restore_x = None
        self._vis_local = None
        if was_active and restore and restore_x is not None:
            try:
                self.win.move(restore_x, self.win.y())
            except Exception:
                pass
        if was_active:
            try:
                self.win.update()
            except Exception:
                pass

    # ------------------------------------------------------------ 隐藏冻结
    def pause(self) -> None:
        """窗口隐藏：冻结状态与倒计时，停止 timer；恢复后从原处继续。"""
        if self._hidden:
            return
        self._hidden = True
        if self._timer.isActive():
            self._timer.stop()
        self._paused_at = self._clock()

    def resume(self) -> None:
        """窗口显示：从冻结位置继续状态机。"""
        if not self._hidden:
            return
        self._hidden = False
        if not self.active:
            return
        now = self._clock()
        if self._mode in _TRANSITION_MODES:
            # 平移 transition_start，使已消耗的进度不被隐藏时长吞掉。
            elapsed_at_pause = max(0.0, self._paused_at - self._transition_start)
            self._transition_start = now - elapsed_at_pause
        self._last_tick_time = now
        if self._mode in _TRANSITION_MODES or self._mode == STRAIGHTENED:
            self._timer.start()

    # ------------------------------------------------------------ 状态机
    def _maybe_enter(self) -> None:
        if not self.enabled or self._hidden or self.active:
            return
        scr = self.win.screen_available()
        avail = scr.availableGeometry() if scr is not None else None
        if avail is None:
            return
        side = edge_side_at_rest(self.win, avail)
        if side is None:
            return
        self._side = side
        self._vis_local = self.win.character_local_region()
        self._restore_x = self.win.x()
        # 会话期间只允许 idle/turn：当前若不是，先回 idle。
        anim = getattr(self.win, "anim", None)
        idles = list(getattr(self.win, "idles", ()) or ())
        turns = list(getattr(self.win, "turns", ()) or ())
        if anim not in idles and anim not in turns:
            switch = getattr(self.win, "_switch", None)
            pick = getattr(self.win, "_pick", None)
            if callable(switch) and callable(pick) and idles:
                switch(pick(idles))
        cancel_move = getattr(self.win, "_cancel_move", None)
        if callable(cancel_move):
            cancel_move()
        stop_physics = getattr(self.win, "_stop_physics", None)
        if callable(stop_physics):
            stop_physics()
        sign = 1.0 if side == "left" else -1.0
        self._begin_transition(
            ENTERING,
            EDGE_ENTER_MS,
            to_angle=sign * EDGE_PROBE_ANGLE,
            to_exposure=EDGE_PEEK_EXPOSURE,
        )

    def _begin_transition(
        self,
        mode: str,
        duration_ms: int,
        *,
        to_angle: float,
        to_exposure: float,
    ) -> None:
        now = self._clock()
        self._mode = mode
        self._transition_duration_ms = duration_ms
        self._transition_start = now
        self._last_tick_time = now
        self._transition_from_angle = self._angle_deg
        self._transition_to_angle = float(to_angle)
        self._transition_from_exposure = self._exposure
        self._transition_to_exposure = float(to_exposure)
        self._timer.start()

    def _on_timer(self) -> None:
        if self._hidden:
            return
        now = self._clock()
        if self._mode in _TRANSITION_MODES:
            self._tick_transition(now)
        elif self._mode == STRAIGHTENED:
            dt = max(0.0, now - self._last_tick_time)
            self._last_tick_time = now
            self._idle_remaining = max(0.0, self._idle_remaining - dt)
            if self._idle_remaining <= 0.0:
                self._begin_return()
        # PEEKING 稳态不需要 timer，停留在探头姿态等待点击。

    def _tick_transition(self, now: float) -> None:
        elapsed_ms = max(0.0, (now - self._transition_start) * 1000.0)
        progress = eased_progress(
            elapsed_ms,
            self._transition_duration_ms,
            QEasingCurve.Type.OutCubic,
        )
        done = progress >= 1.0
        self._angle_deg = (
            self._transition_from_angle
            + (self._transition_to_angle - self._transition_from_angle) * progress
        )
        self._exposure = (
            self._transition_from_exposure
            + (self._transition_to_exposure - self._transition_from_exposure) * progress
        )
        self._apply_pose()
        if not done:
            return
        if self._mode == ENTERING:
            self._mode = PEEKING
            self._timer.stop()
        elif self._mode == STRAIGHTENING:
            self._mode = STRAIGHTENED
            self._idle_remaining = EDGE_IDLE_SECONDS
            self._last_tick_time = now
            self._timer.start()
        elif self._mode == RETURNING:
            self._mode = PEEKING
            self._timer.stop()

    def _begin_return(self) -> None:
        sign = 1.0 if self._side == "left" else -1.0
        self._begin_transition(
            RETURNING,
            EDGE_RETURN_MS,
            to_angle=sign * EDGE_PROBE_ANGLE,
            to_exposure=EDGE_PEEK_EXPOSURE,
        )

    def _apply_pose(self) -> None:
        if self._side is None or self._vis_local is None:
            return
        scr = self.win.screen_available()
        avail = scr.availableGeometry() if scr is not None else None
        if avail is None:
            return
        # 旋转中心与 paint/_sync_mask 一致：帧绘制矩形中心。探头姿态 ±45° 时
        # 角色水平投影会变宽，必须用旋转后投影 bbox 作为露出量分母，否则实际
        # 可见像素远小于目标（例如只剩一只眼）。
        frame_fn = getattr(self.win, "_frame_draw_rect", None)
        pivot = frame_fn() if callable(frame_fn) else QRect(
            0, 0, getattr(self.win, "_w", 0), getattr(self.win, "_h", 0)
        )
        bounds = rotated_region_bounds(self._vis_local, pivot, self._angle_deg)
        x = probe_window_x(self._side, self._exposure, bounds, avail)
        if x != self.win.x():
            self.win.move(x, self.win.y())
        try:
            self.win.update()
        except Exception:
            pass
