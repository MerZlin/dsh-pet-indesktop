# -*- coding: utf-8 -*-
"""右键菜单定位与平移动画（纯 GUI 工具，轻依赖）。

从 ``pet/window.py`` 抽出：这三个函数只依赖 PySide6 的 QtCore/QtWidgets 与
QMenu/QPoint/QRect，与主窗口实例、素材库、视频解码等重模块无关。独立成模块
的好处：
- import 成本从 ``from pet.window import ...``（连带 webm_clip/MovieLibrary
  等 ~1.3s）降到 ~0.3s——右键菜单定位的测试在 CI 慢 runner 上不再逼近
  subprocess 超时上限（此前 context_menu 测试 4-6s，5s 预算偶发超时）；
- 菜单定位/动画逻辑与主窗口解耦，可独立复用与测试。

``window.py`` 保留同名 re-export（``from pet.window import pick_context_menu_position``
等既有调用方与测试不受影响）。
"""

from __future__ import annotations

from PySide6.QtCore import QEasingCurve, QPoint, QPropertyAnimation, QRect, Qt
from PySide6.QtWidgets import QMenu


def _clamp_menu_rect(rect: QRect, avail: QRect) -> QRect:
    """把菜单矩形夹到可用屏幕区域内（保持尺寸不变）。"""
    if avail.isEmpty():
        return QRect(rect)
    x = min(max(rect.x(), avail.left()), max(avail.left(), avail.right() - rect.width() + 1))
    y = min(max(rect.y(), avail.top()), max(avail.top(), avail.bottom() - rect.height() + 1))
    return QRect(x, y, rect.width(), rect.height())


def animate_context_menu_to(
    menu: QMenu,
    target: QPoint,
    *,
    duration_ms: int = 140,
) -> QPropertyAnimation | None:
    """Slide a visible menu to its safe target without changing its layout."""
    target = QPoint(target)
    if menu.pos() == target:
        return None
    animation = QPropertyAnimation(menu, b"pos", menu)
    animation.setDuration(max(1, int(duration_ms)))
    animation.setStartValue(menu.pos())
    animation.setEndValue(target)
    animation.setEasingCurve(QEasingCurve.Type.OutCubic)
    menu._position_transition = animation
    animation.start()
    return animation


def pick_context_menu_position(
    pet_rect: QRect,
    menu_size,
    submenu_width: int,
    avail: QRect,
    margin: int = 10,
) -> tuple[QPoint, Qt.LayoutDirection]:
    """选择右键根菜单弹出位置，使其避开角色并保持在可用屏幕内。

    优先级：
    1. 角色右侧（子菜单默认向右展开，远离角色）；
    2. 角色左侧（视觉方向不变，根菜单保持同样的短间距）；
    3. 屏幕里让整棵 LTR 菜单树与角色重叠最少的角落。
    """
    menu_w = max(1, menu_size.width())
    menu_h = max(1, menu_size.height())
    submenu_width = max(0, int(submenu_width))

    # 1) 右侧：根菜单整体在角色右侧，且子菜单向右有空间
    root = _clamp_menu_rect(
        QRect(pet_rect.right() + margin, pet_rect.top(), menu_w, menu_h), avail
    )
    if (
        root.left() >= pet_rect.right() + margin
        and root.right() + submenu_width <= avail.right()
        and avail.contains(root)
    ):
        return root.topLeft(), Qt.LayoutDirection.LeftToRight

    # 2) 左侧：只按根菜单宽度避让角色。Qt 可根据屏幕空间调整子菜单
    # 的实际弹出侧；布局方向仍为 LTR，因此文字、图标和箭头不会镜像。
    root = _clamp_menu_rect(
        QRect(
            pet_rect.left() - margin - menu_w,
            pet_rect.top(),
            menu_w,
            menu_h,
        ),
        avail,
    )
    if (
        root.right() <= pet_rect.left() - margin
        and avail.contains(root)
    ):
        return root.topLeft(), Qt.LayoutDirection.LeftToRight

    # 3) 远角兜底：视觉方向始终 LTR，按整棵菜单树计算占位和重叠。
    tree_w = menu_w + submenu_width
    right_x = max(
        avail.left() + margin,
        avail.right() - tree_w + 1 - margin,
    )
    corners = (
        (QPoint(avail.left() + margin, avail.top() + margin), Qt.LayoutDirection.LeftToRight),
        (QPoint(right_x, avail.top() + margin), Qt.LayoutDirection.LeftToRight),
        (QPoint(avail.left() + margin, max(avail.top() + margin, avail.bottom() - menu_h + 1 - margin)), Qt.LayoutDirection.LeftToRight),
        (QPoint(right_x, max(avail.top() + margin, avail.bottom() - menu_h + 1 - margin)), Qt.LayoutDirection.LeftToRight),
    )
    best = None
    best_area: int | None = None
    for point, direction in corners:
        tree = QRect(point.x(), point.y(), tree_w, menu_h)
        overlap = tree.intersected(pet_rect)
        area = overlap.width() * overlap.height()
        if best_area is None or area < best_area:
            best = (point, direction)
            best_area = area
    return best