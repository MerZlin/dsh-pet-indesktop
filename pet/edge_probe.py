# -*- coding: utf-8 -*-
"""边缘探头：桌宠真实可见像素贴到屏幕左/右边缘后的持久探头状态机。

状态机由 PetWindow 侧控制器持有，随窗口生命周期存在。隐藏时 pause() 冻结
当前状态与倒计时，恢复后 resume() 从原进度继续。探头会话期间只允许待机/
转向动画，由 WindowFeatureGateMixin._effects_filter_switch 在窗口 _switch
入口过滤；位移（自动/手动移动）由 PetWindow._try_move 入口的
_effects_probe_active 闸门整体拦截，防止挂着探头姿态被平移出屏幕边缘。
"""

from __future__ import annotations

import logging
import time
from typing import Any

from PySide6.QtCore import QEasingCurve, QPoint, QRect, Qt, QTimer

from .window_effects import eased_progress, rotated_region_bounds

# 用根 logger：桌宠的日志配置挂在根上，写进 pet-<pid>.log。
# 用户报「探头像是按框的区域来的」时需要这三个锚点数据才能定案——
# 见 _maybe_enter / _apply_pose / edge_side_at_rest 的日志字段。
log = logging.getLogger(__name__)


def _win_delta(win) -> QPoint:
    """贴边绘制偏移；轻量桩没有该属性时为零（语义=改造前）。"""
    delta = getattr(win, "_draw_delta", None)
    return delta if delta is not None else QPoint(0, 0)


def _virtual_xy(win) -> tuple[int, int]:
    """虚拟窗口坐标 = 实际位置 + 绘制偏移（角色无约束时窗口该在的位置）。"""
    delta = _win_delta(win)
    return win.x() + delta.x(), win.y() + delta.y()


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
# 碰撞撞飞落地停稳后允许重新进入探头吸附前的等待秒数（批 A）。
EDGE_REENTRY_SECONDS = 5.0

OFF = "OFF"
ENTERING = "ENTERING"
PEEKING = "PEEKING"
STRAIGHTENING = "STRAIGHTENING"
STRAIGHTENED = "STRAIGHTENED"
RETURNING = "RETURNING"

_TRANSITION_MODES = {ENTERING, STRAIGHTENING, RETURNING}


def probe_window_x(side: str, exposure: float, vis_local, avail) -> int:
    """把真实可见角色按曝光比例放到屏幕边缘的（虚拟）窗口 x 坐标。

    left：左侧 off-screen 一部分，保留从中心往右的 exposure；
    right：右侧 off-screen 一部分，保留从中心往左的 exposure。
    返回值为虚拟窗口坐标（vis_local 同为虚拟窗口坐标系）：GNOME 不允许
    窗口出屏，"藏出屏"由 PetWindow._move_window_towards 的绘制偏移兑现。
    """
    exposure = max(0.0, min(1.0, float(exposure)))
    offscreen = (1.0 - exposure) * vis_local.width()
    if side == "left":
        return int(round(avail.left() - offscreen - vis_local.left()))
    if side == "right":
        return int(round(avail.right() + offscreen - vis_local.right()))
    raise ValueError(f"unknown edge side: {side!r}")


def edge_side_at_rest(win, avail) -> str | None:
    """判断桌宠是否贴在屏幕左/右边缘。

    优先用稳定身体框（虚拟窗口坐标）：与贴边 clamp 同源，且不受素材羽化
    边缘影响（alpha 16..128 的半透明过渡带不进 mask，像素口径会留出
    十几像素判定间隙导致永远判不上——GNOME 上该功能曾因此静默失效）。
    无身体框接口的轻量桩回退 mask 像素口径（旧行为）。
    """
    vp_fn = getattr(win, "_virtual_pos", None)
    sbr_fn = getattr(win, "_stable_body_local_rect", None)
    if callable(vp_fn) and callable(sbr_fn):
        sbr = sbr_fn()
        if not sbr.isEmpty():
            vp = vp_fn()
            if vp.x() + sbr.left() <= avail.left():
                return "left"
            if vp.x() + sbr.right() >= avail.right():
                return "right"
            return None
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


def probe_body_bounds(avail: QRect, sbr: QRect) -> QRect:
    """探头姿态下放宽的身体钳位区间（身体可整体推出屏幕）。

    正常移动时身体被钳在工作区内（角色不会被拖丢）；探头要"藏一半出屏"，
    所以这里把区间左右各放宽「一个完整身体宽度」。
    必须**减去 sbr.x()**：sbr 是窗口局部矩形，身体框在画布里本就右偏
    （shenshen 局部 x=212），不减掉这一项时左向只放宽到 -4px，物理上不允许
    身体离屏超过 4px——那不是"藏半边"，是把身体钉在边缘。
    """
    return QRect(
        avail.left() - sbr.x() - sbr.width(),
        avail.top() - sbr.y(),
        avail.width() + 2 * sbr.width() + 2 * sbr.x(),
        avail.height() + 2 * sbr.y(),
    )


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
        # 批 A：碰撞撞飞取消会话后，是否等待/正在执行“落地停稳→边缘重进”流程。
        # _reentry_armed    True 表示本次撞飞取消源于碰撞，落地后可重新进入探头。
        # _reentry_active   True 表示 5 秒重进倒计时正在进行。
        # _reentry_remaining 剩余秒数。
        self._reentry_armed = False
        self._reentry_active = False
        self._reentry_remaining = 0.0
        self._last_pose_log_at = 0.0
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
        if self._reentry_active:
            # 批 A：重新进入探头吸附的倒计时期间发生拖拽，作废本次倒计时。
            self._cancel_reentry()
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

    def on_throw_settled(self) -> None:
        """碰撞撞飞落地停稳后触发（由 CollisionClient 在 throw 物理结束时回调）。

        若会话此前因碰撞被取消（_reentry_armed）且当前静止于屏幕边缘，则开始
        5 秒重进倒计时；倒计时内拖拽作废（on_drag_started），到期仍静止于边缘
        则重新进入探头吸附。幂等：未 armed、被禁用/隐藏、已激活或不在边缘时直接返回。
        """
        if not self._reentry_armed:
            return
        self._reentry_armed = False
        if not self.enabled or self._hidden or self.active:
            return
        scr = self.win.screen_available()
        avail = scr.availableGeometry() if scr is not None else None
        if avail is None:
            return
        if edge_side_at_rest(self.win, avail) is None:
            return
        self._reentry_active = True
        self._reentry_remaining = EDGE_REENTRY_SECONDS
        self._last_tick_time = self._clock()
        self._timer.start()

    def _cancel_reentry(self) -> None:
        """作废正在执行的重进倒计时（拖拽/取消会话/到期前被打断时调用）。幂等。"""
        if not self._reentry_active:
            return
        self._reentry_active = False
        self._reentry_remaining = 0.0
        self._timer.stop()

    def cancel(self, reason: str = "", restore: bool = False) -> None:
        """取消会话。restore=True 时恢复进入探头前的窗口 x（用于关闭功能/角色切换）。

        批 A：碰撞撞飞（reason == "collision_throw"）并确实处于激活会话时，标记
        落地后允许重新进入探头（_reentry_armed），由 on_throw_settled 接续。
        """
        was_active = self.active
        side_before = self._side
        self._mode = OFF
        self._side = None
        self._angle_deg = 0.0
        self._exposure = 1.0
        self._idle_remaining = 0.0
        self._timer.stop()
        if was_active and reason == "collision_throw":
            self._reentry_armed = True
            _egg = getattr(self.win, "_throw_egg", None)
            if _egg is not None:
                _egg.arm()
        else:
            self._reentry_armed = False
        self._cancel_reentry()
        restore_x = self._restore_x
        self._restore_x = None
        self._vis_local = None
        if was_active:
            log.info(
                "[边缘探头] 退出 reason=%s side=%s restore=%s",
                reason,
                side_before,
                restore,
            )
        if was_active and restore and restore_x is not None:
            try:
                mover = getattr(self.win, "_move_window_towards", None)
                if callable(mover):
                    mover(restore_x, _virtual_xy(self.win)[1])
                else:
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
        if not self.active and not self._reentry_active:
            return
        now = self._clock()
        if self._mode in _TRANSITION_MODES:
            # 平移 transition_start，使已消耗的进度不被隐藏时长吞掉。
            elapsed_at_pause = max(0.0, self._paused_at - self._transition_start)
            self._transition_start = now - elapsed_at_pause
        self._last_tick_time = now
        if self._mode in _TRANSITION_MODES or self._mode == STRAIGHTENED or self._reentry_active:
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
        sbr = None
        sbr_fn = getattr(self.win, "_stable_body_local_rect", None)
        if callable(sbr_fn):
            sbr = sbr_fn()
        vis_rect = self.win.character_local_region()
        delta = _win_delta(self.win)
        vp = _virtual_xy(self.win)
        # 定案日志：露出量的分母（vis_local）与身体框/画布各是多少、画布留白多少，
        # 「按框探」的签名是 vis_local.width() == 窗口宽（角色可见区没被量到）。
        log.info(
            "[边缘探头] 入场 side=%s 虚拟x=%d 绘制偏移=(%d,%d) 窗口=%dx%d 身体框=%s 可见区(含偏移)=%s 画布留白左=%d 右=%d 可用区=%s",
            side,
            vp[0],
            delta.x(),
            delta.y(),
            getattr(self.win, "_w", 0),
            getattr(self.win, "_h", 0),
            sbr.getRect() if sbr is not None else None,
            vis_rect.getRect(),
            vis_rect.left(),
            getattr(self.win, "_w", 0) - vis_rect.right() - 1,
            avail.getRect(),
        )
        # 可见区必须换算到虚拟窗口坐标系（= 窗口内容坐标，与 _draw_delta 无关）：
        # character_local_region() 返回的 _mask_bounds 是「当前帧按含偏移的绘制
        # 矩形渲染」得到的可见像素包围盒（_sync_mask 用 _frame_draw_rect 画帧，
        # 贴边时 delta≠0 把身体在窗口内整体平移了一个 delta）——直接当分母框用，
        # probe_window_x 会把它按虚拟坐标解释，入场第一帧就把 delta 重算平、
        # 角色跳回修复前位置（issue #146「回弹」）。这里反向平移 -delta 还原成
        # 虚拟窗口坐标；_apply_pose 的旋转基准 pivot 同系（同样反平移）。
        self._vis_local = QRect(self.win.character_local_region()).translated(-delta)
        self._restore_x = _virtual_xy(self.win)[0]
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
        if self._reentry_active:
            dt = max(0.0, now - self._last_tick_time)
            self._last_tick_time = now
            self._reentry_remaining = max(0.0, self._reentry_remaining - dt)
            if self._reentry_remaining <= 0.0:
                self._cancel_reentry()
                self._maybe_enter()
            return
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
        self._angle_deg = self._transition_from_angle + (self._transition_to_angle - self._transition_from_angle) * progress
        self._exposure = self._transition_from_exposure + (self._transition_to_exposure - self._transition_from_exposure) * progress
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
        # 旋转中心与 paint/_sync_mask 一致：帧绘制矩形中心。**必须换算到虚拟窗口
        # 坐标系（反平移 -delta）**——_frame_draw_rect 含绘制偏移（_content_frame_rect
        # 的 x/y 起点就是 delta），而 _vis_local 在 _maybe_enter 已还原成不含偏移的
        # 窗口内容坐标；两者必须在同一坐标系里算投影，否则分母框会平白多出一个
        # delta（实测左向探头 delta≈-424 时，分母框左边界被推到屏幕外 -467，算出
        # x=-20 而不是 -133，角色于是几乎整只留在屏幕内、「藏半边」失效）。
        frame_fn = getattr(self.win, "_frame_draw_rect", None)
        if callable(frame_fn):
            pivot = frame_fn().translated(-_win_delta(self.win))
        else:
            pivot = QRect(0, 0, getattr(self.win, "_w", 0), getattr(self.win, "_h", 0))
        bounds = rotated_region_bounds(self._vis_local, pivot, self._angle_deg)
        x = probe_window_x(self._side, self._exposure, bounds, avail)
        vx, vy = _virtual_xy(self.win)
        # 节流：_apply_pose 每 16ms 被调一次，不节流会把日志刷爆。
        now = self._clock()
        if now - self._last_pose_log_at >= 0.5:
            self._last_pose_log_at = now
            log.info(
                "[边缘探头] 姿态 x=%d（当前虚拟x=%d）side=%s 曝光=%.2f 角度=%.1f 分母框=%s 可用区=%s 实际窗口x=%d",
                x,
                vx,
                self._side,
                self._exposure,
                self._angle_deg,
                bounds.getRect(),
                avail.getRect(),
                self.win.x(),
            )
        mover = getattr(self.win, "_move_window_towards", None)
        if callable(mover):
            # 探头要把身体藏一部分出屏：放宽身体钳位区间；窗口本体仍被钳在
            # 工作区内，"出屏"由绘制偏移兑现（GNOME 不允许窗口出屏）。
            sbr_fn = getattr(self.win, "_stable_body_local_rect", None)
            sbr = sbr_fn() if callable(sbr_fn) else None
            body_bounds = None
            if sbr is not None:
                body_bounds = probe_body_bounds(avail, sbr)
            if x != vx:
                mover(x, vy, body_bounds=body_bounds)
        elif x != self.win.x():
            self.win.move(x, self.win.y())
        try:
            self.win.update()
        except Exception:
            pass
