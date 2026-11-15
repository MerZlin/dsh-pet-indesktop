"""边缘探头状态机（窗口版）：贴左/右缘后 45° 只露头/脸。"""

from __future__ import annotations

import logging
import math
import time

from PySide6.QtCore import QObject, QTimer, Qt

from ..engine.edge import (
    EDGE_ENGAGE_EXPOSURE,
    EDGE_ENGAGE_TOLERANCE_PX,
    EDGE_ENTER_MS,
    EDGE_IDLE_SECONDS,
    EDGE_PEEK_EXPOSURE,
    EDGE_PROBE_ANGLE,
    EDGE_REST_SECONDS,
    EDGE_RETURN_MS,
    EDGE_STRAIGHTEN_MS,
    edge_side_at_rest,
    edge_snap_x,
    probe_target_x,
    rotated_bounds,
)

logger = logging.getLogger("petcore.ui.edge_probe")

OFF = "OFF"
ENTERING = "ENTERING"
PEEKING = "PEEKING"
STRAIGHTENING = "STRAIGHTENING"
STRAIGHTENED = "STRAIGHTENED"
RETURNING = "RETURNING"

_TRANSITIONS = frozenset({ENTERING, STRAIGHTENING, RETURNING})


def _ease_out_cubic(progress: float) -> float:
    progress = max(0.0, min(1.0, progress))
    return 1.0 - (1.0 - progress) ** 3


class EdgeProbeController(QObject):
    def __init__(self, window, *, clock=None) -> None:
        super().__init__(window if isinstance(window, QObject) else None)
        self.window = window
        self._clock = clock if callable(clock) else time.monotonic
        self._enabled = bool(window.config.get("edge_probe_enabled"))
        self._mode = OFF
        self._side: str | None = None
        self._angle = 0.0
        self._exposure = 1.0
        self._restore_x: int | None = None
        self._idle_remaining = 0.0
        self._started_at = 0.0
        self._duration_ms = 0
        self._from_angle = 0.0
        self._to_angle = 0.0
        self._from_exposure = 1.0
        self._to_exposure = 1.0
        self._paused_at = 0.0
        self._hidden = False
        self._timer = QTimer(self)
        self._timer.setInterval(16)
        self._timer.setTimerType(Qt.TimerType.PreciseTimer)
        self._timer.timeout.connect(self._tick)
        self._rest_timer = QTimer(self)
        self._rest_timer.setSingleShot(True)
        self._rest_timer.timeout.connect(self._enter_if_at_edge)

    @property
    def active(self) -> bool:
        return self._mode != OFF

    @property
    def mode(self) -> str:
        return self._mode

    @property
    def side(self) -> str | None:
        return self._side

    @property
    def enabled(self) -> bool:
        return self._enabled

    def angle(self) -> float:
        return self._angle if self.active else 0.0

    def exposure(self) -> float:
        return self._exposure if self.active else 1.0

    def set_enabled(self, enabled: bool) -> None:
        enabled = bool(enabled)
        if enabled == self._enabled:
            return
        self._enabled = enabled
        if not enabled:
            self.cancel("feature_off", restore=True)
            return
        self.maybe_enter()

    def maybe_enter(self, delay_ms: int = int(EDGE_REST_SECONDS * 1000)) -> None:
        if not self._enabled or self._hidden or self.active:
            return
        if self._throw_or_moving():
            return
        self._rest_timer.start(max(0, int(delay_ms)))

    def _throw_or_moving(self) -> bool:
        """抛掷/击飞接管或仍有明显速度：都不算静息贴边。"""
        effects = getattr(self.window, "effects", None)
        if bool(getattr(effects, "throw_egg_active", False)):
            return True
        motion = getattr(self.window, "motion", None)
        if motion is None:
            return False
        if bool(getattr(motion, "thrown", False)):
            return True
        if bool(getattr(motion, "dragging", False)):
            return True
        velocity = getattr(motion, "velocity", None)
        if callable(velocity):
            try:
                vx, vy = velocity()
                return math.hypot(float(vx), float(vy)) > 8.0
            except (TypeError, ValueError):
                return False
        return False

    def _enter_if_at_edge(self) -> None:
        if not self._enabled or self._hidden or self.active:
            return
        if getattr(self.window, "dragging", False):
            return
        if self._throw_or_moving():
            # 抛掷/击飞途中撞到边界不是「静息贴边」：这时摆 45° 会与飞行叠加，
            # 窗口斜着继续反弹（用户反馈的 45° 探头 bug）。
            return
        bounds = self.window.probe_bounds()
        if bounds is None:
            return
        side = edge_side_at_rest(
            self.window.body_rect(), bounds, EDGE_ENGAGE_TOLERANCE_PX
        )
        if side is None:
            return
        self._side = side
        self.window.stop_for_probe()
        # 落地反弹会停在离边十几像素处：先吸附压边，探头姿态的投影才对得上。
        snapped = edge_snap_x(side, self.window.body_local_rect(), bounds)
        if snapped != self.window.x():
            self.window.move_probe_to(snapped)
        self._restore_x = self.window.x()
        sign = 1.0 if side == "left" else -1.0
        self.window.set_probe_pose(0.0, 1.0)
        self._begin_transition(
            ENTERING,
            EDGE_ENTER_MS,
            to_angle=sign * EDGE_PROBE_ANGLE,
            to_exposure=EDGE_PEEK_EXPOSURE,
        )

    def on_clicked(self) -> bool:
        if not self.active or self._hidden:
            return False
        if self._mode == PEEKING:
            self._begin_transition(
                STRAIGHTENING,
                EDGE_STRAIGHTEN_MS,
                to_angle=0.0,
                to_exposure=EDGE_ENGAGE_EXPOSURE,
            )
            return True
        if self._mode == STRAIGHTENED:
            self._idle_remaining = EDGE_IDLE_SECONDS
            return True
        if self._mode == RETURNING:
            self._begin_transition(
                STRAIGHTENING,
                EDGE_STRAIGHTEN_MS,
                to_angle=0.0,
                to_exposure=EDGE_ENGAGE_EXPOSURE,
            )
            return True
        return self.active

    def on_drag_started(self) -> bool:
        """拖拽开始：取消探头会话；返回「拖拽前确实在探头」。"""
        was_active = self.active
        self._rest_timer.stop()
        if self.active:
            self.cancel("drag_away", restore=False)
        return was_active

    def on_release(self, was_dragging: bool) -> None:
        if was_dragging and not self._throw_or_moving():
            self.maybe_enter()

    def cancel(self, reason: str = "", *, restore: bool = False) -> None:
        was_active = self.active
        restore_x = self._restore_x
        self._mode = OFF
        self._side = None
        self._angle = 0.0
        self._exposure = 1.0
        self._idle_remaining = 0.0
        self._restore_x = None
        self._timer.stop()
        self._rest_timer.stop()
        self.window.set_probe_pose(0.0, 1.0)
        if was_active and restore and restore_x is not None:
            self.window.move_probe_to(restore_x)
        if was_active:
            logger.info("[边缘探头] 退出 reason=%s restore=%s", reason, restore)

    def pause(self) -> None:
        if self._hidden:
            return
        self._hidden = True
        self._paused_at = self._clock()
        self._timer.stop()
        self._rest_timer.stop()

    def resume(self) -> None:
        if not self._hidden:
            return
        self._hidden = False
        now = self._clock()
        if self._mode in _TRANSITIONS:
            elapsed = max(0.0, self._paused_at - self._started_at)
            self._started_at = now - elapsed
        if self._mode in _TRANSITIONS or self._mode == STRAIGHTENED:
            self._last_tick = now
            self._timer.start()

    def shutdown(self) -> None:
        self._rest_timer.stop()
        self._timer.stop()

    def _screen_bounds(self):
        return self.window.probe_bounds()

    def _begin_transition(
        self,
        mode: str,
        duration_ms: int,
        *,
        to_angle: float,
        to_exposure: float,
    ) -> None:
        self._mode = mode
        self._duration_ms = max(1, int(duration_ms))
        self._started_at = self._clock()
        self._from_angle = self._angle
        self._to_angle = float(to_angle)
        self._from_exposure = self._exposure
        self._to_exposure = float(to_exposure)
        self._timer.start()

    def _tick(self) -> None:
        if self._hidden:
            return
        if self._throw_or_moving():
            self.cancel("thrown", restore=False)
            return
        now = self._clock()
        if self._mode in _TRANSITIONS:
            elapsed_ms = max(0.0, (now - self._started_at) * 1000.0)
            progress = _ease_out_cubic(elapsed_ms / self._duration_ms)
            self._angle = self._from_angle + (self._to_angle - self._from_angle) * progress
            self._exposure = (
                self._from_exposure
                + (self._to_exposure - self._from_exposure) * progress
            )
            self._apply_pose()
            if progress < 1.0:
                return
            if self._mode == ENTERING:
                self._mode = PEEKING
                self._timer.stop()
                self.window.remember_probe_geometry()
            elif self._mode == STRAIGHTENING:
                self._mode = STRAIGHTENED
                self._idle_remaining = EDGE_IDLE_SECONDS
                self._last_tick = now
            elif self._mode == RETURNING:
                self._mode = PEEKING
                self._timer.stop()
            return
        if self._mode == STRAIGHTENED:
            delta = max(0.0, now - getattr(self, "_last_tick", now))
            self._last_tick = now
            self._idle_remaining = max(0.0, self._idle_remaining - delta)
            if self._idle_remaining <= 0.0:
                sign = 1.0 if self._side == "left" else -1.0
                self._begin_transition(
                    RETURNING,
                    EDGE_RETURN_MS,
                    to_angle=sign * EDGE_PROBE_ANGLE,
                    to_exposure=EDGE_PEEK_EXPOSURE,
                )

    def _apply_pose(self) -> None:
        bounds = self._screen_bounds()
        if bounds is None or self._side is None:
            return
        body = self.window.body_local_rect()
        width, height = self.window.width(), self.window.height()
        pivot = (width / 2.0, height / 2.0)
        projected = rotated_bounds(body, pivot, self._angle)
        target = probe_target_x(self._side, self._exposure, projected, bounds)
        self.window.set_probe_pose(self._angle, self._exposure)
        self.window.move_probe_to(target)
