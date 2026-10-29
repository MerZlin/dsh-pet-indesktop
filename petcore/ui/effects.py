"""窗口级视觉效果：点击 Q 弹、黄金回旋、探头击飞旋转。

只维护角度/进度与计时；绘制与命中由 ``PetWindow`` 统一应用。
"""

from __future__ import annotations

import math
import time

from PySide6.QtCore import QObject, QTimer, Qt

from ..engine.spin import (
    GOLDEN_SPIN_ACCEL,
    GOLDEN_SPIN_CLICK_RUSH_MS,
    GOLDEN_SPIN_DURATION_MS,
    GOLDEN_SPIN_MIN_REV_MS,
    eased_progress,
)
from ..engine.squash import squash_geometry

THROW_EGG_RECOVER_SPEED = 780.0
THROW_EGG_AIR_FOLLOW_SPEED = 150.0


class EffectController(QObject):
    def __init__(self, window, *, clock=None) -> None:
        super().__init__(window if isinstance(window, QObject) else None)
        self.window = window
        self._clock = clock if callable(clock) else time.monotonic
        self._timer = QTimer(self)
        self._timer.setInterval(16)
        self._timer.setTimerType(Qt.TimerType.PreciseTimer)
        self._timer.timeout.connect(self._tick)

        self._squash_active = False
        self._squash_started = 0.0
        self._squash_duration_ms = 220.0
        self._squash_strength = 1.0
        self._squash_bounces = 1

        self._spin_active = False
        self._spin_pending = False
        self._spin_angle = 0.0
        self._spin_remaining = 0
        self._spin_nominal_ms = GOLDEN_SPIN_DURATION_MS
        self._spin_rev_ms = GOLDEN_SPIN_DURATION_MS
        self._spin_rev_started = 0.0
        self._spin_rev_start_angle = 0.0
        self._spin_rev_end_angle = -360.0

        self._throw_egg_active = False
        self._throw_egg_angle = 0.0

    # ---------------------------------------------------------------- 查询
    @property
    def squash_active(self) -> bool:
        return self._squash_active

    def squash_progress(self) -> float:
        if not self._squash_active:
            return 1.0
        elapsed_ms = max(0.0, self._clock() - self._squash_started) * 1000.0
        return min(1.0, elapsed_ms / max(1.0, self._squash_duration_ms))

    @property
    def spin_active(self) -> bool:
        return self._spin_active

    @property
    def spin_pending(self) -> bool:
        return self._spin_pending

    def spin_angle(self) -> float:
        return self._spin_angle if self._spin_active else 0.0

    @property
    def throw_egg_active(self) -> bool:
        return self._throw_egg_active

    def effect_angle(self) -> float:
        return self.spin_angle() + (self._throw_egg_angle if self._throw_egg_active else 0.0)

    def squash_rect(self) -> tuple[int, int, int, int]:
        return squash_geometry(
            self.window.width(),
            self.window.height(),
            self.window.width(),
            self.window.height(),
            self.squash_progress(),
            strength=self._squash_strength,
            bounces=self._squash_bounces,
        )

    def any_active(self) -> bool:
        return self._squash_active or self._spin_active or self._throw_egg_active

    # ---------------------------------------------------------------- Q 弹
    def start_squash(self, duration_ms: int | None = None) -> None:
        if not bool(self.window.config.get("click_squash_enabled", True)):
            return
        config = self.window.config
        self._squash_active = True
        self._squash_started = self._clock()
        self._squash_duration_ms = float(
            duration_ms or config.get("click_squash_ms", 220) or 220
        )
        try:
            self._squash_strength = max(
                0.3, min(2.0, float(config.get("click_squash_strength", 100) or 100) / 100.0)
            )
        except (TypeError, ValueError):
            self._squash_strength = 1.0
        try:
            self._squash_bounces = max(
                1, min(5, int(config.get("click_squash_bounces", 1) or 1))
            )
        except (TypeError, ValueError):
            self._squash_bounces = 1
        self._ensure_timer()
        self.window.update()

    # ---------------------------------------------------------------- 黄金回旋
    def start_spin(self, *, direct: bool = False) -> None:
        if direct and self._spin_active:
            self._spin_remaining += 1
            self._spin_rev_start_angle = self._spin_angle
            self._spin_rev_ms = GOLDEN_SPIN_CLICK_RUSH_MS
            self._spin_rev_started = self._clock()
        elif not self._spin_active:
            self._begin_spin(1, GOLDEN_SPIN_DURATION_MS)
        self._ensure_timer()
        self.window.update()

    def arm_spin_after_click(self) -> None:
        self._spin_pending = True

    def consume_click_finished(self) -> None:
        if not self._spin_pending:
            return
        self._spin_pending = False
        if not self._spin_active:
            self._begin_spin(1, GOLDEN_SPIN_DURATION_MS)

    def cancel_spin(self) -> None:
        self._spin_pending = False
        if not self._spin_active:
            return
        self._spin_active = False
        self._spin_angle = 0.0
        self._spin_remaining = 0
        self._spin_nominal_ms = GOLDEN_SPIN_DURATION_MS
        self._spin_rev_ms = GOLDEN_SPIN_DURATION_MS
        self._spin_rev_start_angle = 0.0
        self._spin_rev_end_angle = -360.0
        self.window.update()

    def _begin_spin(self, turns: int, duration_ms: int) -> None:
        self._spin_active = True
        self._spin_angle = 0.0
        self._spin_remaining = max(1, int(turns))
        self._spin_nominal_ms = max(1, int(duration_ms))
        self._spin_rev_ms = self._spin_nominal_ms
        self._spin_rev_started = self._clock()
        self._spin_rev_start_angle = 0.0
        self._spin_rev_end_angle = -360.0
        # 点击动画结束后的接续旋转也必须起表，否则角度停在 0°（看不见）。
        self._ensure_timer()
        self.window.update()

    def _update_spin(self, now: float) -> None:
        if not self._spin_active:
            return
        elapsed_ms = max(0.0, (now - self._spin_rev_started) * 1000.0)
        progress = eased_progress(elapsed_ms, self._spin_rev_ms)
        self._spin_angle = self._spin_rev_start_angle + (
            self._spin_rev_end_angle - self._spin_rev_start_angle
        ) * progress
        if progress < 1.0:
            return
        if self._spin_remaining > 1:
            self._spin_remaining -= 1
            self._spin_nominal_ms = max(
                GOLDEN_SPIN_MIN_REV_MS,
                int(round(self._spin_nominal_ms * GOLDEN_SPIN_ACCEL)),
            )
            self._spin_rev_start_angle = self._spin_rev_end_angle
            self._spin_rev_end_angle -= 360.0
            self._spin_rev_ms = self._spin_nominal_ms
            self._spin_rev_started = now
        else:
            self.cancel_spin()

    # ---------------------------------------------------------------- 探头击飞
    def arm_throw_egg(self) -> None:
        if not bool(self.window.config.get("throw_egg_enabled", True)):
            return
        self._throw_egg_active = True
        self._throw_egg_angle = 0.0
        self._ensure_timer()
        self.window.update()

    def update_throw_egg(self, vx: float, vy: float, touching_boundary: bool) -> None:
        if not self._throw_egg_active:
            return
        speed = math.hypot(vx, vy)
        threshold = THROW_EGG_RECOVER_SPEED if touching_boundary else THROW_EGG_AIR_FOLLOW_SPEED
        if speed >= threshold:
            self._throw_egg_angle = 90.0 + math.degrees(math.atan2(vy, vx))
        if speed < THROW_EGG_RECOVER_SPEED and touching_boundary:
            self.end_throw_egg()

    def end_throw_egg(self) -> None:
        if not self._throw_egg_active:
            return
        self._throw_egg_active = False
        self._throw_egg_angle = 0.0
        self.window.update()

    # ---------------------------------------------------------------- 计时
    def _ensure_timer(self) -> None:
        if not self._timer.isActive():
            self._timer.start()

    def _tick(self) -> None:
        now = self._clock()
        if self._squash_active:
            if self.squash_progress() >= 1.0:
                self._squash_active = False
        self._update_spin(now)
        if not self.any_active():
            self._timer.stop()
        self.window.update()

    def reset(self) -> None:
        self._squash_active = False
        self._spin_active = False
        self._spin_pending = False
        self._spin_angle = 0.0
        self._throw_egg_active = False
        self._throw_egg_angle = 0.0
        self._timer.stop()
        self.window.update()

    def shutdown(self) -> None:
        self._timer.stop()
