"""共享聊天窗定位几何：候选点 / clamp / overlap 选择。

Modern（pet/chat/widgets.py）与 Classic（pet/chat/legacy_widgets.py）两套 UI
各自维护过一份完全相同的候选点/clamp/overlap 逻辑；本模块是其唯一共享实现。
UI 布局差异（Modern 的紧凑屏两栏缩窗、Legacy 的 phone layout）保留在各 UI 类中，
不进入本模块。
"""

from __future__ import annotations

from PySide6.QtCore import QPoint, QRect, QSize


def candidate_points_near_pet(pet_rect: QRect, size: QSize, gap: int = 14) -> list[QPoint]:
    """围绕可见宠物体边界生成 4 个候选窗口左上角坐标。

    顺序与两套 UI 的历史行为一致：右侧、左侧、下方、上方。
    """
    y = pet_rect.center().y() - size.height() // 2
    return [
        QPoint(pet_rect.right() + gap + 1, y),
        QPoint(pet_rect.left() - size.width() - gap, y),
        QPoint(pet_rect.center().x() - size.width() // 2, pet_rect.bottom() + gap + 1),
        QPoint(pet_rect.center().x() - size.width() // 2, pet_rect.top() - size.height() - gap),
    ]


def clamp_point(point: QPoint, size: QSize, available: QRect) -> QPoint:
    """把窗口左上角坐标 clamp 进可用工作区，保证窗口不越界。"""
    x = max(available.left(), min(point.x(), available.right() - size.width() + 1))
    y = max(available.top(), min(point.y(), available.bottom() - size.height() + 1))
    return QPoint(x, y)


def best_position_near_pet(
    pet_rect: QRect, size: QSize, available: QRect, gap: int = 14
) -> QPoint:
    """选出聊天窗应放置的位置（返回窗口左上角坐标）。

    - 优先选视觉遮挡最小的方向：第一个完全落在可用工作区内的候选直接胜出。
    - 若窗口在任何候选点都无法完全放入（例如窗口比工作区还高），则把所有候选
      clamp 进工作区，选择与可见角色重叠面积最小的一个；重叠相同按位移最小、
      再按候选顺序打破平局（与历史实现一致）。
    """
    candidates = candidate_points_near_pet(pet_rect, size, gap)
    for point in candidates:
        candidate = QRect(point, size)
        if available.contains(candidate):
            return point

    ranked = []
    for index, point in enumerate(candidates):
        clamped = clamp_point(point, size, available)
        candidate = QRect(clamped, size)
        intersection = candidate.intersected(pet_rect)
        overlap = intersection.width() * intersection.height() if not intersection.isEmpty() else 0
        displacement = abs(clamped.x() - point.x()) + abs(clamped.y() - point.y())
        ranked.append((overlap, displacement, index, clamped))

    _, _, _, best = min(ranked, key=lambda item: item[:3])
    return best
