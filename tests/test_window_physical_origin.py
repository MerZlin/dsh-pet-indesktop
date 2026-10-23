# -*- coding: utf-8 -*-
"""窗口物理原点换算：没有原生几何时，回退分支也必须带上该窗的 DPR（issue #354）。

macOS 双 2x 屏、两块合成窗原点分别是 ``(0,30)`` 与 ``(2560,30)``。旧实现在拿不到
``GetWindowRect``（非 Windows / 无原生窗）时直接返回**逻辑**原点，而三个调用点
（``_sprite_pos_in``、``_place_sprite_in_physical_screen``、多窗物理域）都按物理
像素消费它（局部坐标 × DPR）：副屏窗的物理原点被算成 2560 而不是 5120，两张窗的
物理判定区因此重叠 2560–5080，副屏靠近主屏的那半边会被判成落在主屏窗里。

用户表现：开启「允许桌宠跨屏」后，副屏左侧约一半宽度放不下桌宠——一放就被判到
主屏并瞬移，档案里的 ``screen_name`` 也会被改写成主屏。
"""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPoint, QRect

from pet.overlay_shell import OverlayShell


class _Overlay:
    """overlay 的最小替身：只要 geometry / DPR，且**没有** winId（模拟无原生窗）。"""

    def __init__(self, rect, dpr):
        self._rect = QRect(*rect)
        self._dpr = dpr

    def geometry(self):
        return QRect(self._rect)

    def devicePixelRatioF(self):
        return self._dpr


def test_physical_origin_scales_by_window_dpr():
    """2x 屏：副窗逻辑原点 (2560,30) → 物理 (5120,60)。"""
    overlay = _Overlay((2560, 30, 2540, 1265), 2.0)
    assert OverlayShell._window_physical_origin(overlay) == QPoint(5120, 60)


def test_physical_origin_unchanged_at_dpr_one():
    """单倍屏与原行为逐像素一致。"""
    overlay = _Overlay((0, 30, 2540, 1390), 1.0)
    assert OverlayShell._window_physical_origin(overlay) == QPoint(0, 30)


def test_physical_origin_degrades_to_one_without_dpr():
    """拿不到 DPR 的替身：按 1.0 退回，不抛、不改行为。"""

    class _NoDpr(_Overlay):
        def devicePixelRatioF(self):  # noqa: N802 - Qt 命名
            raise AttributeError("no native window")

    overlay = _NoDpr((0, 30, 100, 100), 1.0)
    assert OverlayShell._window_physical_origin(overlay) == QPoint(0, 30)


def test_side_by_side_2x_windows_do_not_overlap_in_physical_space():
    """回归 #354：并排两块 2x 窗的物理判定区不许重叠，副屏左半边得归副屏。"""
    left = _Overlay((0, 30, 2540, 1390), 2.0)
    right = _Overlay((2560, 30, 2540, 1265), 2.0)

    def _area(overlay):
        origin = OverlayShell._window_physical_origin(overlay)
        geo = overlay.geometry()
        dpr = overlay.devicePixelRatioF()
        return QRect(origin.x(), origin.y(),
                     int(geo.width() * dpr), int(geo.height() * dpr))

    left_area, right_area = _area(left), _area(right)
    assert not left_area.intersects(right_area)

    # 副屏左半边的一点（局部 x=200）必须落在副屏自己的物理区里
    probe = QPoint(OverlayShell._window_physical_origin(right).x() + 200 * 2,
                   OverlayShell._window_physical_origin(right).y() + 300 * 2)
    assert right_area.contains(probe)
    assert not left_area.contains(probe)
