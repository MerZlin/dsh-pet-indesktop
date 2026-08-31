# -*- coding: utf-8 -*-
"""右键菜单位置避让测试。"""
from __future__ import annotations

import json
import os
import subprocess
import sys

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


def test_places_menu_left_without_mirroring_when_right_edge_not_enough():
    pet = QRect(950, 400, 180, 200)
    avail = QRect(0, 0, 1000, 800)
    pos, direction = pick_context_menu_position(
        pet, _menu_size(), submenu_width=90, avail=avail
    )
    assert direction == Qt.LayoutDirection.LeftToRight
    assert pos.x() + 120 <= pet.left() - 10
    root = QRect(pos, QPoint(120, 220))
    assert avail.contains(root)
    assert not root.intersects(pet)


def test_root_menu_keeps_the_same_short_gap_on_either_side_of_pet():
    left_pet = QRect(20, 400, 180, 200)
    right_pet = QRect(1000, 400, 180, 200)
    avail = QRect(0, 0, 1200, 800)
    submenu_width = 90
    right_pos, right_direction = pick_context_menu_position(
        left_pet, _menu_size(), submenu_width=submenu_width, avail=avail
    )
    left_pos, left_direction = pick_context_menu_position(
        right_pet, _menu_size(), submenu_width=submenu_width, avail=avail
    )

    assert right_direction == Qt.LayoutDirection.LeftToRight
    assert left_direction == Qt.LayoutDirection.LeftToRight
    assert right_pos.x() == left_pet.right() + 10
    assert left_pos.x() + 120 == right_pet.left() - 10


def test_falls_back_to_minimal_overlap_corner_when_both_sides_blocked():
    pet = QRect(100, 100, 180, 200)
    avail = QRect(0, 0, 400, 400)
    pos, direction = pick_context_menu_position(
        pet, _menu_size(), submenu_width=90, avail=avail
    )
    # 左右都不够放；小屏幕兜底选择与角色重叠面积最小的远角（右上/右下）
    assert pos.x() + (120 + 90) // 2 > avail.center().x()
    assert direction == Qt.LayoutDirection.LeftToRight
    assert pos.x() + 120 + 90 <= avail.right() + 1
    root = QRect(pos, QPoint(120, 220))
    assert avail.contains(root)


def test_context_menu_transitions_smoothly_to_safe_target():
    script = r'''
import json
from PySide6.QtCore import QPoint
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QMenu
from pet.window import animate_context_menu_to

app = QApplication([])
menu = QMenu()
menu.addAction("测试动作")
start = QPoint(40, 80)
target = QPoint(100, 80)
menu.popup(start)
QTest.qWait(20)
shown = menu.pos()
animate_context_menu_to(menu, target, duration_ms=120)
QTest.qWait(50)
middle = menu.pos()
QTest.qWait(120)
end = menu.pos()
print(json.dumps({"shown": [shown.x(), shown.y()],
                  "middle": [middle.x(), middle.y()],
                  "end": [end.x(), end.y()]}))
menu.close()
'''
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen", PYTHONPATH=os.getcwd())
    result = subprocess.run(
        [sys.executable, "-c", script],
        check=True,
        capture_output=True,
        text=True,
        env=env,
        timeout=5,
    )
    observed = json.loads(result.stdout.strip().splitlines()[-1])
    assert observed["shown"] == [40, 80]
    assert 40 < observed["middle"][0] < 100
    assert observed["middle"][1] == 80
    assert observed["end"] == [100, 80]
