# -*- coding: utf-8 -*-
"""多桌宠围圈/面对面排布的纯几何函数（无 Qt 依赖，便于单元测试）。

成员中心坐标语义与碰撞 IPC 一致：``x/y`` 是内容矩形中心，
``w/h`` 是桌宠窗口逻辑尺寸。返回值中 ``x/y`` 为目标中心坐标，
调用方再换算成 QWidget.move() 的左上角坐标。
"""
from __future__ import annotations

import math
from typing import Any, Iterable

_MARGIN = 24.0


def _num(screen: dict[str, Any], key: str, default: float) -> float:
    value = screen.get(key, default)
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _screen_bounds(screen: dict[str, Any]) -> tuple[float, float, float, float]:
    left = _num(screen, "left", 0.0)
    top = _num(screen, "top", 0.0)
    right = _num(screen, "right", left + 1920.0)
    bottom = _num(screen, "bottom", top + 1080.0)
    return left, top, right, bottom


def same_screen_members(
    members: Iterable[dict[str, Any]], screen: dict[str, Any]
) -> list[dict[str, Any]]:
    """按屏幕可用区过滤成员（成员 x/y 为中心坐标）。"""
    left, top, right, bottom = _screen_bounds(screen)
    result: list[dict[str, Any]] = []
    for member in members:
        try:
            x = float(member.get("x", 0.0))
            y = float(member.get("y", 0.0))
        except (TypeError, ValueError):
            continue
        if left <= x <= right and top <= y <= bottom:
            result.append(member)
    return result


def centroid(members: Iterable[dict[str, Any]]) -> tuple[float, float]:
    items = list(members)
    if not items:
        return 0.0, 0.0
    return (
        sum(float(m.get("x", 0.0)) for m in items) / len(items),
        sum(float(m.get("y", 0.0)) for m in items) / len(items),
    )


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _clamp_target(
    x: float, y: float, max_dim: float,
    left: float, top: float, right: float, bottom: float,
) -> tuple[float, float]:
    """把圆环目标中心夹进屏幕，同时让完整窗口仍留在可用区。"""
    margin = _MARGIN + max_dim / 2.0
    x = _clamp(x, left + margin, right - margin)
    y = _clamp(y, top + margin, bottom - margin)
    return x, y


def _arrange_two(
    members: list[dict[str, Any]],
    left: float, top: float, right: float, bottom: float,
) -> dict[str, dict[str, float]]:
    a, b = sorted(members, key=lambda m: str(m.get("runtime_id", "")))
    center_x, center_y = centroid(members)
    distance = max(float(a.get("w", 100.0)), float(b.get("w", 100.0))) * 1.1 + 40.0
    max_distance = min(
        center_x - (left + _MARGIN),
        (right - _MARGIN) - center_x,
    ) * 2.0
    distance = _clamp(distance, 40.0, max(40.0, max_distance))
    y = _clamp(center_y, top + _MARGIN, bottom - _MARGIN)
    ax = _clamp(center_x - distance / 2.0, left + _MARGIN, right - _MARGIN)
    bx = _clamp(center_x + distance / 2.0, left + _MARGIN, right - _MARGIN)
    return {
        str(a["runtime_id"]): {"x": ax, "y": y, "facing": "right"},
        str(b["runtime_id"]): {"x": bx, "y": y, "facing": "left"},
    }


def _arrange_ring(
    members: list[dict[str, Any]],
    left: float, top: float, right: float, bottom: float,
) -> dict[str, dict[str, float]]:
    sorted_members = sorted(members, key=lambda m: str(m.get("runtime_id", "")))
    count = len(sorted_members)
    center_x, center_y = centroid(sorted_members)
    max_dim = max(
        max(float(m.get("w", 100.0)) for m in sorted_members),
        max(float(m.get("h", 80.0)) for m in sorted_members),
    )
    # 相邻环上成员中心距至少能放下窗口尺寸（保守乘 1.6）
    required_chord = max_dim * 1.6
    required_radius = (
        required_chord / (2.0 * math.sin(math.pi / count))
        if count > 2
        else required_chord / 2.0
    )
    fit_radius = min(
        center_x - (left + _MARGIN + max_dim / 2.0),
        (right - _MARGIN - max_dim / 2.0) - center_x,
        center_y - (top + _MARGIN + max_dim / 2.0),
        (bottom - _MARGIN - max_dim / 2.0) - center_y,
    )
    radius = max(0.0, min(required_radius, fit_radius))
    result: dict[str, dict[str, float]] = {}
    for index, member in enumerate(sorted_members):
        angle = -math.pi / 2.0 + (2.0 * math.pi * index / count)
        tx, ty = center_x + radius * math.cos(angle), center_y + radius * math.sin(angle)
        tx, ty = _clamp_target(tx, ty, max_dim, left, top, right, bottom)
        facing = "left" if tx > center_x + 1.0 else "right" if tx < center_x - 1.0 else "right"
        result[str(member["runtime_id"])] = {
            "x": tx,
            "y": ty,
            "facing": facing,
        }
    return result


def arrange_group(
    members: Iterable[dict[str, Any]], screen: dict[str, Any]
) -> dict[str, dict[str, float]]:
    """生成 2 只面对面 / ≥3 只圆环的排布；返回 {runtime_id: {x,y,facing}}。

    members 必须已经过同屏/可见/开关过滤；少于 2 只返回空 dict。
    """
    items = list(members)
    if len(items) < 2:
        return {}
    left, top, right, bottom = _screen_bounds(screen)
    if len(items) == 2:
        return _arrange_two(items, left, top, right, bottom)
    return _arrange_ring(items, left, top, right, bottom)
