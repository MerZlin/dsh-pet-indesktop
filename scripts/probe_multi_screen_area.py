# -*- coding: utf-8 -*-
"""多屏活动区域探针（issue #186 跨屏拖拽/抛掷排查用）。

回答三个问题：
1. 本机到底识别到几块屏、几何是多少（geometry / availableGeometry / DPR）；
2. 多屏活动区域算出来是什么（DesktopArea.bounds + 逐屏可用区），单屏时为什么
   不启用；
3. 用**真实的**钳制代码做一次「往每块屏拖」的自检：请求副屏中心时窗口落在哪、
   身体框有没有真的到那块屏上、会不会被收进空洞。

用法：python scripts/probe_multi_screen_area.py
输出：纯文本打印（可直接贴进 issue / PR 报告）。单屏机器会明确打印「不适用」，
      那正是「为什么本机不能真实复现跨屏」的证据。退出码恒为 0（诊断脚本）。
"""

from __future__ import annotations

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from PySide6.QtCore import QPoint, QRect  # noqa: E402
from PySide6.QtGui import QCursor, QGuiApplication  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

import pet.catalog as catalog  # noqa: E402
from pet import window_placement  # noqa: E402


class _Config(dict):
    def get(self, key, default=None):
        return super().get(key, default)


class _Host:
    """_move_window_towards 需要的最小宿主（与真实 PetWindow 同一条代码路径）。"""

    def __init__(self, screen):
        self.scale = catalog.DEFAULT_SCALE
        self._w = int(round(catalog.CANVAS_W * self.scale))
        self._h = int(round((catalog.CANVAS_H + catalog.PAD) * self.scale))
        self._capture_headroom = 0
        self._draw_delta = QPoint(0, 0)
        self._collision_local_bounds = None
        self._phys_pos = [0.0, 0.0]
        self._interaction_area = None
        self.cfg = _Config(character=catalog.DEFAULT_CHARACTER)
        self._screen = screen
        self.x = 0
        self.y = 0

    def _screen_available(self, *_args, **_kwargs):
        return self._screen

    def move(self, x, y):
        self.x, self.y = int(x), int(y)

    def pos(self):
        return QPoint(self.x, self.y)

    def update(self):
        pass

    def _sync_mask(self):
        pass


def _rect_text(rect: QRect | None) -> str:
    if rect is None:
        return "None"
    return f"({rect.x()}, {rect.y()}) {rect.width()}x{rect.height()}"


def _fmt(screen) -> str:
    try:
        dpr = screen.devicePixelRatio()
    except Exception:
        dpr = float("nan")
    return f"{screen.name()}: geometry={_rect_text(screen.geometry())} available={_rect_text(screen.availableGeometry())} dpr={dpr}"


def main() -> int:
    app = QApplication.instance() or QApplication([])  # noqa: F841
    screens = list(QGuiApplication.screens() or ())
    print(f"[screens] 识别到 {len(screens)} 块屏")
    for screen in screens:
        print(f"  - {_fmt(screen)}")

    pointer = QGuiApplication.screenAt(QCursor.pos())
    print(f"[pointer] 光标所在屏: {pointer.name() if pointer else 'None'} 光标位置={QCursor.pos().x()},{QCursor.pos().y()}")
    print(f"[primary] 主屏: {_fmt(QGuiApplication.primaryScreen())}" if QGuiApplication.primaryScreen() else "[primary] 主屏: None")

    area = window_placement.desktop_area()
    if area is None:
        print("[area] desktop_area() = None")
        print("       原因：有效屏不足 2 块，或某块屏几何为空/异常。")
        print("       → 拖拽/抛掷按单屏语义走（本屏 availableGeometry），")
        print("         跨屏拖拽/抛掷不适用；本机无法复现 #186。")
        return 0

    print(f"[area] desktop_area().bounds = {_rect_text(area.bounds)}")
    for index, rect in enumerate(area.screens):
        print(f"  screen[{index}] available={_rect_text(rect)}")

    host = _Host(screens[0])
    sbr = window_placement.stable_body_local_rect(host)
    print(f"[body] 身体框（窗口局部）= {_rect_text(sbr)}")
    print(f"[scenario] 桌宠初始停在 {screens[0].name()}（真实场景：在主屏上往各屏拖）")

    for index, screen in enumerate(screens):
        avail = screen.availableGeometry()
        target_x = avail.center().x() - host._w // 2
        target_y = avail.center().y() - host._h // 2
        # 1) 不带快照（= 4.2.1 现状）：被钉在桌宠当前所在屏（screens[0]）
        host._interaction_area = None
        host._screen = screens[0]
        window_placement.move_window_towards(host, target_x, target_y)
        without = (host.x, host.y)
        # 2) 带快照（= 本次修复）：应当落到目标屏上
        host._interaction_area = area
        window_placement.move_window_towards(host, target_x, target_y)
        with_area = (host.x, host.y, QRect(host.pos() + sbr.topLeft() + host._draw_delta, sbr.size()))

        body = with_area[2]
        lands = body.intersects(avail)
        band = window_placement.band_bounds(area, body)
        print(
            f"[drag→{screen.name()}] 目标中心=({target_x},{target_y}) "
            f"无快照=({without[0]},{without[1]}) "
            f"有快照=({with_area[0]},{with_area[1]}) "
            f"身体框={_rect_text(body)} 落在目标屏={lands} "
            f"当时的活动带={_rect_text(band)}"
        )

    print("[branch] 抛掷边界（有快照 / 无快照）:")
    host._interaction_area = area
    host._phys_pos = [float(area.bounds.center().x()), float(area.bounds.center().y())]
    print(f"  有快照: {tuple(round(v, 1) for v in window_placement.throw_bounds(host))}")
    host._interaction_area = None
    print(f"  无快照: {tuple(round(v, 1) for v in window_placement.throw_bounds(host))}")

    print("[verdict] 多屏活动区域已启用：请在上面的 [drag→…] 行确认每块屏的「落在目标屏=True」；再在真实界面上把桌宠从主屏拖/丢到副屏看是否跟手。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
