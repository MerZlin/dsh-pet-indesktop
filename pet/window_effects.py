# -*- coding: utf-8 -*-
"""共享的绘制/命中变换小工具（黄金回旋 + 边缘探头共用）。

旋转在绘制层完成，不改动 _rebuild_frame / 帧缓存 / 解码链。paintEvent 与
_sync_mask 使用同一 begin/end 旋转路径，保证非 Windows mask 与画面一致；
_is_transparent_at 使用 unrotate_point 做逆变换，保证 Windows 逐像素命中一致。
"""
from __future__ import annotations

import math

from PySide6.QtCore import QEasingCurve, QPointF, QRect, QRectF


def begin_rotation(painter, rect: QRect, angle_deg: float) -> None:
    """围绕 rect 中心旋转；调用方在绘制后必须配对 end_rotation。"""
    if abs(float(angle_deg)) < 1e-6:
        return
    center = QPointF(rect.center())
    painter.save()
    painter.translate(center)
    painter.rotate(float(angle_deg))
    painter.translate(-center)


def end_rotation(painter, angle_deg: float) -> None:
    """与 begin_rotation 配对；无旋转时为 no-op。"""
    if abs(float(angle_deg)) < 1e-6:
        return
    painter.restore()


def unrotate_point(point: QPointF, rect: QRect, angle_deg: float) -> QPointF:
    """把窗口内某逻辑点逆旋转回未旋转坐标系。

    用于 _is_transparent_at：先逆变换再查 alpha，保证旋转后的可见像素
    与命中测试一致。angle=0 时原样返回。
    """
    angle = float(angle_deg)
    if abs(angle) < 1e-6:
        return QPointF(point)
    center = QPointF(rect.center())
    rad = math.radians(-angle)
    dx = point.x() - center.x()
    dy = point.y() - center.y()
    return QPointF(
        center.x() + dx * math.cos(rad) - dy * math.sin(rad),
        center.y() + dx * math.sin(rad) + dy * math.cos(rad),
    )


def rotated_region_bounds(region: QRect, pivot_rect: QRect, angle_deg: float) -> QRect:
    """把 region 四角绕 pivot_rect 中心旋转后的轴对齐外接矩形。

    边缘探头在 ±45° 姿态下用该投影宽度计算露出量，避免按未旋转宽度定位导致
    实际露出远小于目标。angle=0 时返回原区域副本。
    """
    angle = float(angle_deg)
    if abs(angle) < 1e-6:
        return QRect(region)
    center = QPointF(pivot_rect.center())
    rad = math.radians(angle)
    cos_a = math.cos(rad)
    sin_a = math.sin(rad)
    # 用“像素外缘”坐标（left / top / left+width / top+height）旋转，
    # 避免 QRect.right() 的含入坐标在 90°/45° 边界少算一列。
    corners = (
        QPointF(region.left(), region.top()),
        QPointF(region.left() + region.width(), region.top()),
        QPointF(region.left(), region.top() + region.height()),
        QPointF(region.left() + region.width(), region.top() + region.height()),
    )
    min_x = min_y = float("inf")
    max_x = max_y = float("-inf")
    for point in corners:
        dx = point.x() - center.x()
        dy = point.y() - center.y()
        rx = center.x() + dx * cos_a - dy * sin_a
        ry = center.y() + dx * sin_a + dy * cos_a
        min_x = min(min_x, rx)
        max_x = max(max_x, rx)
        min_y = min(min_y, ry)
        max_y = max(max_y, ry)
    return QRectF(min_x, min_y, max_x - min_x, max_y - min_y).toAlignedRect()


def eased_progress(
    elapsed_ms: float,
    duration_ms: float,
    curve: QEasingCurve = QEasingCurve.Type.OutCubic,
) -> float:
    """把已流逝毫秒映射到 [0,1] 的缓动进度；duration<=0 视为完成。"""
    if duration_ms <= 0:
        return 1.0
    raw = max(0.0, min(1.0, float(elapsed_ms) / float(duration_ms)))
    easing = QEasingCurve(curve)
    return float(easing.valueForProgress(raw))
