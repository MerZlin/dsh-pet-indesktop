# -*- coding: utf-8 -*-
"""window_effects 纯几何助手测试。"""
from __future__ import annotations

from PySide6.QtCore import QRect

from pet.window_effects import rotated_region_bounds


def test_rotated_region_bounds_zero_angle_returns_copy():
    region = QRect(10, 20, 100, 40)
    bounds = rotated_region_bounds(region, QRect(0, 0, 200, 200), 0.0)
    assert bounds == region


def test_rotated_region_bounds_90_degrees_swaps_dimensions():
    # 区域与旋转中心同心，90° 后宽高互换（外缘坐标不含 off-by-one）。
    pivot = QRect(0, 0, 200, 200)
    bounds = rotated_region_bounds(QRect(50, 80, 100, 40), pivot, 90.0)
    assert bounds.width() == 40
    assert bounds.height() == 100


def test_rotated_region_bounds_45_degrees_square_grows():
    # 同心正方形绕中心转 45°，外接 bbox 宽高约 = 边长 × √2。
    pivot = QRect(0, 0, 200, 200)
    bounds = rotated_region_bounds(QRect(50, 50, 100, 100), pivot, 45.0)
    expected = round(100 * 2 ** 0.5)
    assert abs(bounds.width() - expected) <= 2
    assert abs(bounds.height() - expected) <= 2
