# -*- coding: utf-8 -*-
"""黄金回旋控制器测试：程序化 360° 插值旋转与“点击后接续/直连累计”语义。"""
from __future__ import annotations

from PySide6.QtWidgets import QApplication

from pet.golden_spin import (
    GOLDEN_SPIN_ACCEL,
    GOLDEN_SPIN_CLICK_RUSH_MS,
    GOLDEN_SPIN_DURATION_MS,
    GOLDEN_SPIN_END_ANGLE,
    GOLDEN_SPIN_MIN_REV_MS,
    GoldenSpinController,
)


def _qapp():
    return QApplication.instance() or QApplication([])


class FakeWin:
    def __init__(self):
        self.updates = 0

    def update(self):
        self.updates += 1


def _clock_controller():
    times = [0.0]

    def clock():
        return times[0]

    return GoldenSpinController(FakeWin(), clock=clock), times


def _spin_direct_controller():
    _qapp()
    controller, times = _clock_controller()
    controller.spin_direct()
    assert controller.active
    assert controller.queued_turns == 1
    return controller, times


def test_spin_runs_ccw_and_finishes_at_zero():
    _qapp()
    controller, times = _clock_controller()
    controller.start()
    assert controller.active
    times[0] = GOLDEN_SPIN_DURATION_MS / 1000.0 / 2.0
    controller._update(times[0])
    assert controller.current_angle_deg() < 0
    assert controller.current_angle_deg() > -360.0
    times[0] = GOLDEN_SPIN_DURATION_MS / 1000.0
    controller._update(times[0])
    assert not controller.active
    assert controller.current_angle_deg() == 0.0
    controller.cancel()


def test_start_is_idempotent_while_running():
    _qapp()
    controller, times = _clock_controller()
    controller.start()
    controller.start()
    assert controller.active
    controller.cancel()
    assert not controller.active


def test_click_pending_is_consumed_once_after_click_animation_finishes():
    _qapp()
    controller, times = _clock_controller()
    controller.arm_after_click()
    assert controller.pending_after_click
    controller.consume_click_finished()
    assert not controller.pending_after_click
    assert controller.active
    times[0] = GOLDEN_SPIN_DURATION_MS / 1000.0
    controller._update(times[0])
    assert not controller.active
    # 第二次 consume 没有 pending，不再触发新的旋转。
    controller.consume_click_finished()
    assert not controller.active
    controller.cancel()


def test_cancel_clears_pending_and_stops_active_spin():
    _qapp()
    controller, times = _clock_controller()
    controller.arm_after_click()
    controller.start()
    assert controller.active
    controller.cancel()
    assert not controller.active
    assert not controller.pending_after_click
    assert controller.current_angle_deg() == 0.0


def test_direct_spin_single_turn_finishes_at_base_duration():
    controller, times = _spin_direct_controller()
    times[0] = GOLDEN_SPIN_DURATION_MS / 1000.0
    controller._update(times[0])
    assert not controller.active
    assert controller.queued_turns == 0
    assert controller.current_angle_deg() == 0.0
    controller.cancel()


def test_direct_spin_click_rushes_current_revolution_then_next_is_faster():
    controller, times = _spin_direct_controller()
    # 第一圈中途再点一次：累计第二圈，并把当前圈剩余角度快速收尾。
    mid = GOLDEN_SPIN_DURATION_MS / 1000.0 / 2.0
    times[0] = mid
    controller._update(times[0])
    assert controller.active
    controller.spin_direct()
    assert controller.queued_turns == 2
    assert controller._rev_duration_ms == GOLDEN_SPIN_CLICK_RUSH_MS

    # 当前圈应在点击后 CLICK_RUSH_MS 内完成，而不是等到完整 700ms。
    times[0] = mid + GOLDEN_SPIN_CLICK_RUSH_MS / 1000.0
    controller._update(times[0])
    assert controller.active
    assert controller.queued_turns == 1
    assert abs(controller.current_angle_deg() - GOLDEN_SPIN_END_ANGLE) < 1e-6

    # 第二圈按逐圈加速后的更短 nominal 时长完成。
    second_ms = round(GOLDEN_SPIN_DURATION_MS * GOLDEN_SPIN_ACCEL)
    assert controller._rev_duration_ms == second_ms
    times[0] += second_ms / 1000.0
    controller._update(times[0])
    assert not controller.active
    assert controller.queued_turns == 0
    assert controller.current_angle_deg() == 0.0
    controller.cancel()


def test_direct_spin_repeated_clicks_keep_rushing_same_current_revolution():
    controller, times = _spin_direct_controller()
    times[0] = 0.10
    controller._update(times[0])
    controller.spin_direct()   # queued=2，开始 rush 当前圈
    times[0] += 0.05
    controller._update(times[0])
    controller.spin_direct()   # 同一圈再次被 rush，只累计圈数
    assert controller.queued_turns == 3

    times[0] += GOLDEN_SPIN_CLICK_RUSH_MS / 1000.0
    controller._update(times[0])
    # 当前圈只完成一次，随后还剩两圈待转。
    assert controller.active
    assert controller.queued_turns == 2
    controller.cancel()


def test_direct_spin_speed_floor_never_below_min():
    controller, times = _spin_direct_controller()
    # 同一点击时刻连按 20 次：都会催促当前圈收尾并累计后续圈。
    for _ in range(20):
        controller.spin_direct()
    assert controller.queued_turns == 21

    # 当前圈（被最后一次点击 rush）先快速完成。
    times[0] += GOLDEN_SPIN_CLICK_RUSH_MS / 1000.0
    controller._update(times[0])
    assert controller.active
    assert controller.queued_turns == 20

    # 依次完成剩余圈，记录每圈 nominal 时长；加速不得突破下限。
    durations = []
    for _ in range(20):
        durations.append(controller._rev_duration_ms)
        times[0] += controller._rev_duration_ms / 1000.0
        controller._update(times[0])
    assert durations[-1] == GOLDEN_SPIN_MIN_REV_MS
    assert not controller.active
    assert controller.current_angle_deg() == 0.0
    controller.cancel()
