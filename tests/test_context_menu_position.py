# -*- coding: utf-8 -*-
"""右键菜单位置避让测试。"""
from __future__ import annotations

from PySide6.QtCore import QPoint, QRect, Qt

from pet.window import pick_context_menu_position


def _menu_size(width: int = 120, height: int = 220):
    class Size:
        pass

    size = Size()
    size.width = lambda: width
    size.height = lambda: height
    return size


def test_places_menu_right_when_space_available():
    pet = QRect(500, 400, 180, 200)
    avail = QRect(0, 0, 1200, 800)
    pos, direction = pick_context_menu_position(
        pet, _menu_size(), submenu_width=90, avail=avail
    )
    assert direction == Qt.LayoutDirection.LeftToRight
    assert pos.x() >= pet.right() + 10
    assert pos.y() >= avail.top()
    root = QRect(pos, QPoint(120, 220))
    assert avail.contains(root)
    assert not root.intersects(pet)


def test_places_menu_left_with_rtl_when_right_edge_not_enough():
    pet = QRect(950, 400, 180, 200)
    avail = QRect(0, 0, 1000, 800)
    pos, direction = pick_context_menu_position(
        pet, _menu_size(), submenu_width=90, avail=avail
    )
    assert direction == Qt.LayoutDirection.RightToLeft
    assert pos.x() + 120 <= pet.left() - 10
    root = QRect(pos, QPoint(120, 220))
    assert avail.contains(root)
    assert not root.intersects(pet)


def test_falls_back_to_minimal_overlap_corner_when_both_sides_blocked():
    pet = QRect(100, 100, 180, 200)
    avail = QRect(0, 0, 400, 400)
    pos, direction = pick_context_menu_position(
        pet, _menu_size(), submenu_width=90, avail=avail
    )
    # 左右都不够放；小屏幕兜底选择与角色重叠面积最小的远角（右上/右下）
    assert pos.x() > avail.center().x()
    assert direction in (
        Qt.LayoutDirection.LeftToRight,
        Qt.LayoutDirection.RightToLeft,
    )
    root = QRect(pos, QPoint(120, 220))
    assert avail.contains(root)
