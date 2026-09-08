# -*- coding: utf-8 -*-
"""围圈/面对面排布的纯几何测试。"""
from __future__ import annotations

import math

from pet.group_geometry import arrange_group, centroid, same_screen_members


def _member(runtime_id, x, y, w=100.0, h=80.0):
    return {"runtime_id": runtime_id, "x": float(x), "y": float(y), "w": float(w), "h": float(h)}


def _screen(x=0, y=0, w=1920, h=1080):
    return {"left": float(x), "top": float(y), "right": float(x + w), "bottom": float(y + h)}


def test_centroid_averages_centers():
    members = [_member("a", 100, 100), _member("b", 300, 300)]
    assert centroid(members) == (200.0, 200.0)


def test_same_screen_members_filters_by_available_geometry():
    screen = _screen(0, 0, 1920, 1080)
    inside = _member("a", 100, 100)
    outside = _member("b", 2000, 100)
    assert same_screen_members([inside, outside], screen) == [inside]


def test_two_members_face_each_other_horizontally():
    screen = _screen(0, 0, 2000, 1000)
    members = [_member("b", 1000, 500, w=100), _member("a", 300, 300, w=120)]
    result = arrange_group(members, screen)
    assert set(result) == {"a", "b"}
    left = result["a"] if result["a"]["x"] < result["b"]["x"] else result["b"]
    right = result["a"] if left is not result["a"] else result["b"]
    assert left["x"] < right["x"]
    assert left["facing"] == "right"
    assert right["facing"] == "left"
    # 两只 y 相同
    assert left["y"] == right["y"]


def test_three_members_form_ring_around_centroid():
    screen = _screen(0, 0, 1920, 1080)
    members = [
        _member("a", 300, 300, w=200, h=120),
        _member("b", 600, 500, w=200, h=120),
        _member("c", 900, 300, w=200, h=120),
    ]
    result = arrange_group(members, screen)
    assert set(result) == {"a", "b", "c"}
    cx, cy = centroid(members)
    # 所有目标到质心距离应接近同一半径
    radii = [math.hypot(v["x"] - cx, v["y"] - cy) for v in result.values()]
    assert max(radii) - min(radii) < 1e-6
    # 目标全部夹在屏幕可用区内且留出安全边距
    for v in result.values():
        assert screen["left"] < v["x"] < screen["right"]
        assert screen["top"] < v["y"] < screen["bottom"]
    # 两两中心距足以容纳窗口宽度（不重叠，取保守宽度余量）
    ids = sorted(result)
    for i, a in enumerate(ids):
        for b in ids[i + 1:]:
            dx = result[a]["x"] - result[b]["x"]
            dy = result[a]["y"] - result[b]["y"]
            dist = math.hypot(dx, dy)
            assert dist > 140, (a, b, dist)


def test_ring_facing_points_inward_horizontally():
    screen = _screen(0, 0, 2000, 1200)
    members = [_member("a", 700, 500, w=200, h=120), _member("b", 900, 600, w=200, h=120),
               _member("c", 1100, 500, w=200, h=120)]
    result = arrange_group(members, screen)
    cx, _cy = centroid(members)
    for rid, target in result.items():
        if target["x"] > cx + 1:
            assert target["facing"] == "left"
        elif target["x"] < cx - 1:
            assert target["facing"] == "right"


def test_small_screen_clamps_ring_inside():
    screen = _screen(0, 0, 600, 400)
    members = [_member("a", 300, 200, w=300, h=180), _member("b", 350, 200, w=300, h=180),
               _member("c", 400, 200, w=300, h=180)]
    result = arrange_group(members, screen)
    for v in result.values():
        assert screen["left"] - 1 <= v["x"] <= screen["right"] + 1
        assert screen["top"] - 1 <= v["y"] <= screen["bottom"] + 1
