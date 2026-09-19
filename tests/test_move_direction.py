# -*- coding: utf-8 -*-
"""朝向规则纯函数：边缘可达性闸门与中线滞回（pet/movement.py，无 Qt）。"""
from __future__ import annotations

import random

import pytest

from pet.movement import body_reach, choose_move_direction, inward_facing


class _Rng:
    """记录被调用情况的 RNG 桩：既能钉住结果，也能证明分支未掷骰。"""

    def __init__(self, value: int):
        self.value = value
        self.seen: list[list[int]] = []

    def choice(self, seq):
        self.seen.append(list(seq))
        return self.value


# ---------------------------------------------------------------- 可达界
def test_body_reach_insets_both_bounds_by_margin_and_half_body():
    """可达界 = 工作区边 + 安全边距 + 身体半宽；中心在身体框中心。"""
    cx, left, right = body_reach(0.0, 1000.0, 200.0, 100.0, 20.0)
    assert cx == 250.0, "中心是身体框中心（body_left + 半宽）"
    assert left == 70.0 and right == 930.0


def test_body_reach_respects_available_range_offset():
    """多屏/留白工作区：可达界跟着可用区间平移，不是从 0 起算。"""
    cx, left, right = body_reach(1920.0, 2560.0, 2000.0, 100.0, 20.0)
    assert cx == 2050.0
    assert left == 1990.0 and right == 2490.0


def test_body_reach_degenerate_range_gives_inverted_bounds():
    """窄工作区：左右可达界会交叉——剩余空间随之变负，交由闸门拒绝。"""
    cx, left, right = body_reach(0.0, 100.0, 120.0, 200.0, 20.0)
    assert cx == 220.0
    assert left == 120.0 and right == -20.0
    assert right < left, "可达区间交叉"
    assert right - cx < 0, "右侧剩余空间为负"
    assert choose_move_direction(cx, left, right, 200, _Rng(1)) is None


# ---------------------------------------------------------------- 可达性闸门
@pytest.mark.parametrize("sign", [-1, 1])
def test_both_sides_reachable_defers_to_rng(sign):
    """两侧都够得到下限时交给 RNG 二选一（结果只能是 ±1）。"""
    rnd = _Rng(sign)
    assert choose_move_direction(500.0, 0.0, 1000.0, 60, rnd) == sign
    assert rnd.seen == [[-1, 1]], "候选集必须恰为左右两个方向"


def test_both_signs_occur_with_module_rng():
    """默认 RNG（模块级 random）下两个方向都会出现——闸门不是恒定偏向。"""
    signs = {choose_move_direction(500.0, 0.0, 1000.0, 60) for _ in range(200)}
    assert signs == {-1, 1}


def test_only_left_reachable_picks_left_without_rng():
    """右侧不足下限时只能向左，且不消耗随机数。"""
    rnd = _Rng(1)
    assert choose_move_direction(100.0, 0.0, 120.0, 60, rnd) == -1
    assert rnd.seen == [], "单侧可达分支不得掷骰"


def test_only_right_reachable_picks_right_without_rng():
    rnd = _Rng(-1)
    assert choose_move_direction(40.0, 0.0, 200.0, 60, rnd) == 1
    assert rnd.seen == [], "单侧可达分支不得掷骰"


def test_neither_side_reachable_returns_none():
    """两侧空间都小于下限 → None（调用方据此拒绝建立移动计划）。"""
    rnd = _Rng(-1)
    assert choose_move_direction(100.0, 50.0, 150.0, 60, rnd) is None
    assert rnd.seen == [], "闸门拦截分支不得掷骰"


def test_exactly_min_distance_is_reachable():
    """下限是「可达」的闭区间端点：等于下限算够，差 1px 才算不够。"""
    assert choose_move_direction(60.0, 0.0, 100.0, 60, _Rng(1)) == -1
    assert choose_move_direction(40.0, 0.0, 100.0, 60, _Rng(-1)) == 1
    assert choose_move_direction(59.0, 0.0, 100.0, 60, _Rng(1)) is None


def test_zero_min_distance_always_reachable_when_inside_bounds():
    assert choose_move_direction(50.0, 50.0, 50.0, 0, _Rng(-1)) == -1


# ---------------------------------------------------------------- 中线滞回
def test_hysteresis_band_edges_at_seven_percent():
    """0..1000 的可用区间：中线 500，带宽 70；带内 None，带外给出内侧方向。"""
    assert inward_facing(429.0, 0.0, 1000.0) == "right"
    assert inward_facing(430.0, 0.0, 1000.0) is None
    assert inward_facing(500.0, 0.0, 1000.0) is None
    assert inward_facing(570.0, 0.0, 1000.0) is None
    assert inward_facing(571.0, 0.0, 1000.0) == "left"


def test_outside_band_far_from_center():
    assert inward_facing(0.0, 0.0, 1000.0) == "right"
    assert inward_facing(1000.0, 0.0, 1000.0) == "left"


def test_band_scales_with_ratio():
    """带宽 = 可用区间宽 × ratio：ratio 越大越早触发纠正。"""
    assert inward_facing(299.0, 0.0, 1000.0, ratio=0.2) == "right"
    assert inward_facing(300.0, 0.0, 1000.0, ratio=0.2) is None
    assert inward_facing(701.0, 0.0, 1000.0, ratio=0.2) == "left"


def test_zero_ratio_keeps_only_exact_center_neutral():
    assert inward_facing(400.0, 0.0, 1000.0, ratio=0.0) == "right"
    assert inward_facing(500.0, 0.0, 1000.0, ratio=0.0) is None
    assert inward_facing(600.0, 0.0, 1000.0, ratio=0.0) == "left"


def test_offset_available_range_uses_its_own_center():
    """可用区间不始于 0 时中线随之平移（多屏/留白工作区）。"""
    assert inward_facing(1500.0, 1000.0, 2000.0) is None
    assert inward_facing(1429.0, 1000.0, 2000.0) == "right"
    assert inward_facing(1571.0, 1000.0, 2000.0) == "left"


def test_inward_facing_never_consumes_randomness(monkeypatch):
    """滞回是纯几何判定：不得触碰模块级 random（否则污染确定性随机流）。"""
    monkeypatch.setattr(random, "choice", lambda seq: pytest.fail("不得掷骰"))
    assert inward_facing(0.0, 0.0, 1000.0) == "right"
