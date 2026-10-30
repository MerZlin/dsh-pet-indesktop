# -*- coding: utf-8 -*-
"""macOS 多屏（Retina / 混合 DPR）：跨窗坐标换算不得把点当物理像素。

实机形状（本机 NSScreen 读回）：内建屏 1470×956@2x 在主屏；外接屏 W2466T
2304×1296@2x 位于其正上方、左移 450，Qt 几何 (-450,-1296,2304,1296)。

旧实现 ``_sprite_pos_in`` / ``_place_sprite_in_physical_screen`` 把 Cocoa 的
点（point）当物理像素再乘 ``devicePixelRatioF()``：外接屏的"物理"区域被放大成
4608×2592，和内建屏在坐标上大面积重叠。实机表现：
  - 内建屏中部的宠每 tick 被判到外接屏，再被换回来时 y 偏移几百像素；
  - 拖到外接屏松手后落点被源窗活动区钳住/跳到外接屏顶部，看起来"回到自带屏"。

覆盖 2x+2x（本机）、2x+1x、1x+2x 三种 DPR 组合：换算口径只认 Cocoa 的
逻辑点，与显示器型号/DPR 组合无关。本文件钉两件事：静止不漂移；拖过缝后
落点与松手点一致、归属换到目标屏。
"""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QPointF

import pet.overlay_shell as overlay_mod
from pet.overlay_window import OverlayWindow
from tests.test_overlay_hosts_multiscreen import (
    FakeScreen,
    _cleanup,
    _hittable,
    _install_screens,
    _make_shell,
    _move,
    _press,
    _release,
    _tick,
)


def _global_body_center(shell, sprite):
    host = shell.host_for_sprite(sprite)
    body = sprite.body_rect()
    origin = host.overlay.geometry().topLeft()
    return (host.screen.name(),
            origin.x() + body.center().x(),
            origin.y() + body.center().y())


@pytest.fixture(params=[(2.0, 2.0), (2.0, 1.0), (1.0, 2.0)],
                ids=["2x+2x", "2x+1x", "1x+2x"])
def retina_shell(request, tmp_path, monkeypatch):
    builtin_dpr, external_dpr = request.param
    builtin = FakeScreen((0, 0, 1470, 956), (0, 33, 1470, 831),
                         dpr=builtin_dpr, name="Built-in Retina Display")
    external = FakeScreen((-450, -1296, 2304, 1296), (-450, -1266, 2304, 1266),
                          dpr=external_dpr, name="W2466T")
    # macOS 分支：窗口原点/几何都是点，跨窗换算不得再乘 DPR。
    monkeypatch.setattr(overlay_mod, "_IS_MACOS", True, raising=False)
    monkeypatch.setattr(overlay_mod, "_IS_WINDOWS", False, raising=False)
    # 离屏 Qt 的 QWidget DPR 恒 1；用真实屏 DPR 喂出 Retina 的旧触发条件。
    monkeypatch.setattr(
        OverlayWindow, "devicePixelRatioF",
        lambda self: float(self._screen.devicePixelRatio()), raising=False)
    _install_screens(monkeypatch, builtin, external)
    shell = _make_shell(tmp_path, builtin, {"allow_cross_screen": True})
    try:
        assert shell._active_multi()
        yield shell, builtin, external
    finally:
        _cleanup(shell)


def test_rest_on_retina_builtin_does_not_drift(retina_shell):
    shell, builtin, _external = retina_shell
    sprite = shell.sprite
    shell.move_sprite_to_screen(sprite, builtin, QPointF(700, 300))
    before = _global_body_center(shell, sprite)

    for _ in range(4):
        shell._advance_screen_sync()

    after = _global_body_center(shell, sprite)
    assert after[0] == before[0] == builtin.name()
    assert after[1] == pytest.approx(before[1], abs=0.5)
    assert after[2] == pytest.approx(before[2], abs=0.5)


def test_drag_across_seam_keeps_cursor_position_on_retina(retina_shell):
    shell, builtin, external = retina_shell
    sprite = shell.sprite
    shell.move_sprite_to_screen(sprite, builtin, QPointF(700, 300))

    host = shell.host_for_sprite(sprite)
    overlay = host.overlay
    origin = QPointF(overlay.geometry().topLeft())
    _hittable(sprite)
    hit = QPointF(sprite.rect().center())
    overlay.mousePressEvent(_press(hit, origin + hit))
    cursor = origin + hit
    for _ in range(40):
        cursor = QPointF(cursor.x(), cursor.y() - 30)
        overlay.mouseMoveEvent(_move(cursor - origin, cursor))
        _tick(shell)
    overlay.mouseReleaseEvent(_release(cursor - origin, cursor))

    assert shell.host_for_sprite(sprite).screen is external
    _name, cx, cy = _global_body_center(shell, sprite)
    # 松手点 = 身体中心（抓取点就是 rect 中心）；不许跳回内建屏、也不许弹到别处。
    assert cx == pytest.approx(cursor.x(), abs=3)
    assert cy == pytest.approx(cursor.y(), abs=3)
