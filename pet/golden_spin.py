# -*- coding: utf-8 -*-
"""黄金回旋：程序化原地逆时针 360° 插值旋转。

控制器只维护角度状态与计时；绘制由 PetWindow/WindowFeatureGateMixin
经 pet/window_effects.py 应用。旋转不依赖素材，不与点击动画叠画面：

- 常规点击触发（armed）：点击动画结束后调用 consume_click_finished() 接一圈。
- 直连点击触发（golden_spin_direct）：点击立即调用 spin_direct()；旋转中再点
  会累计待转圈数、把当前圈剩余角度快速收尾（GOLDEN_SPIN_CLICK_RUSH_MS），
  并让后续每一圈比上一圈更快（GOLDEN_SPIN_ACCEL 加速，单圈时长下限
  GOLDEN_SPIN_MIN_REV_MS）。
"""
from __future__ import annotations

import time
from typing import Any

from PySide6.QtCore import QObject, Qt, QTimer

from .window_effects import eased_progress

GOLDEN_SPIN_DURATION_MS = 700
GOLDEN_SPIN_END_ANGLE = -360.0  # Qt 正角=顺时针，负角=逆时针
GOLDEN_SPIN_ACCEL = 0.82        # 直连模式逐圈加速系数：下一圈 = 上一圈 × 0.82
GOLDEN_SPIN_MIN_REV_MS = 200    # 单圈时长下限，防长连击后转速失控
GOLDEN_SPIN_CLICK_RUSH_MS = 130  # 点击时把当前圈剩余角度快速转完的时长


class GoldenSpinController(QObject):
    """管理逐圈旋转会话。每个 PetWindow 持有同一实例。"""

    def __init__(self, win: Any, *, clock=None, parent=None) -> None:
        super().__init__(parent)
        self.win = win
        self._clock = clock if callable(clock) else time.monotonic
        self._active = False
        self._angle_deg = 0.0
        self._started_at = 0.0
        self._pending_after_click = False
        self._remaining_turns = 0
        self._nominal_rev_ms = GOLDEN_SPIN_DURATION_MS
        self._rev_duration_ms = GOLDEN_SPIN_DURATION_MS
        self._rev_started_at = 0.0
        self._rev_start_angle_deg = 0.0
        self._rev_end_angle_deg = GOLDEN_SPIN_END_ANGLE
        self._timer = QTimer(self)
        self._timer.setInterval(16)
        self._timer.setTimerType(Qt.TimerType.PreciseTimer)
        self._timer.timeout.connect(self._on_timer)

    # ------------------------------------------------------------ 查询
    @property
    def active(self) -> bool:
        return self._active

    @property
    def queued_turns(self) -> int:
        """尚待完成的整圈数（含正在旋转的当前圈）。"""
        return self._remaining_turns

    def current_angle_deg(self) -> float:
        return self._angle_deg

    @property
    def pending_after_click(self) -> bool:
        return self._pending_after_click

    # ------------------------------------------------------------ 启动/取消
    def start(self) -> None:
        """立即开始一段基准时长的 360° 逆时针旋转；已运行/待触发时忽略重复启动。"""
        if self._active:
            return
        self._begin_session(1, GOLDEN_SPIN_DURATION_MS)

    def spin_direct(self) -> None:
        """点击直连：空闲时开转一圈；旋转中再点则累计一圈并让当前圈快速收尾。"""
        if self._active:
            self._remaining_turns += 1
            self._rush_current_revolution()
        else:
            self._begin_session(1, GOLDEN_SPIN_DURATION_MS)
        self.win.update()

    def arm_after_click(self) -> None:
        """点击触发模式：等点击动画自然结束后再接续旋转。"""
        self._pending_after_click = True

    def consume_click_finished(self) -> None:
        """点击动画结束回调：有 pending 才真正启动一次。"""
        if not self._pending_after_click:
            return
        self._pending_after_click = False
        self.start()

    def cancel(self) -> None:
        """取消 pending；若正在旋转则立即归零并停表。"""
        self._pending_after_click = False
        if not self._active:
            return
        self._active = False
        self._timer.stop()
        self._angle_deg = 0.0
        self._remaining_turns = 0
        self._nominal_rev_ms = GOLDEN_SPIN_DURATION_MS
        self._rev_duration_ms = GOLDEN_SPIN_DURATION_MS
        self._rev_start_angle_deg = 0.0
        self._rev_end_angle_deg = GOLDEN_SPIN_END_ANGLE
        self.win.update()

    def cancel_pending(self) -> None:
        """只清 pending，不打断正在进行的旋转（角色切换/隐藏时使用）。"""
        self._pending_after_click = False

    # ------------------------------------------------------------ 会话
    def _begin_session(self, turns: int, rev_ms: int) -> None:
        self._active = True
        self._remaining_turns = max(1, int(turns))
        self._nominal_rev_ms = max(GOLDEN_SPIN_MIN_REV_MS, int(rev_ms))
        self._rev_duration_ms = self._nominal_rev_ms
        self._angle_deg = 0.0
        self._rev_start_angle_deg = 0.0
        self._rev_end_angle_deg = GOLDEN_SPIN_END_ANGLE
        self._rev_started_at = self._clock()
        self._timer.start()
        self.win.update()

    def _rush_current_revolution(self) -> None:
        """点击后把当前圈从当前角度快速转完（约 CLICK_RUSH_MS）。"""
        self._rev_start_angle_deg = self._angle_deg
        self._rev_duration_ms = GOLDEN_SPIN_CLICK_RUSH_MS
        self._rev_started_at = self._clock()

    # ------------------------------------------------------------ 计时
    def _on_timer(self) -> None:
        self._update(self._clock())

    def _update(self, now: float) -> None:
        if not self._active:
            return
        elapsed_ms = max(0.0, (now - self._rev_started_at) * 1000.0)
        progress = eased_progress(
            elapsed_ms,
            self._rev_duration_ms,
        )
        done = progress >= 1.0
        self._angle_deg = (
            self._rev_start_angle_deg
            + (self._rev_end_angle_deg - self._rev_start_angle_deg) * progress
        )
        if not done:
            self.win.update()
            return
        if self._remaining_turns > 1:
            # 当前圈完成：进入下一圈并逐圈加速（普通圈时长用 nominal）。
            self._remaining_turns -= 1
            self._nominal_rev_ms = max(
                GOLDEN_SPIN_MIN_REV_MS,
                int(round(self._nominal_rev_ms * GOLDEN_SPIN_ACCEL)),
            )
            self._rev_start_angle_deg = self._rev_end_angle_deg
            self._rev_end_angle_deg -= 360.0
            self._rev_duration_ms = self._nominal_rev_ms
            self._rev_started_at = now
        else:
            self._active = False
            self._timer.stop()
            self._angle_deg = 0.0
            self._remaining_turns = 0
            self._nominal_rev_ms = GOLDEN_SPIN_DURATION_MS
            self._rev_duration_ms = GOLDEN_SPIN_DURATION_MS
            self._rev_start_angle_deg = 0.0
            self._rev_end_angle_deg = GOLDEN_SPIN_END_ANGLE
        self.win.update()
