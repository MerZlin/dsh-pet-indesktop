# -*- coding: utf-8 -*-
"""拖动倾斜：被牵着走时的滞后倾角、松手回正与关掉开关。

倾角只是一个渲染通道（与探头/击飞相加），所以既钉弹簧行为，也钉「旋转单一
出处」的逆变换一致性。
"""

from __future__ import annotations

import math
import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QPoint, QRect, Qt
from PySide6.QtGui import QColor, QPainter, QPixmap
from PySide6.QtWidgets import QApplication

from petcore.config import Config
from petcore.contracts.content import CharacterSpec
from petcore.ui.motion import MotionController
from petcore.ui.pet_window import PetWindow


class _Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class _Config:
    def __init__(self, **values) -> None:
        self.values = values

    def get(self, key, default=None):
        return self.values.get(key, default)


class _Window:
    def __init__(self, **config) -> None:
        self.config = _Config(**config)
        self._x = 100
        self._y = 100
        self.updates = 0
        self.stopped = 0
        self.probe_active = False

    def x(self) -> int:
        return self._x

    def y(self) -> int:
        return self._y

    def move(self, x, y) -> None:
        self._x, self._y = int(x), int(y)

    def update(self) -> None:
        self.updates += 1

    def _clamp_position(self, x, y):
        return int(x), int(y)

    def _on_motion_stopped(self) -> None:
        self.stopped += 1


def _drag(ctrl: MotionController, clock: _Clock, steps: list[int], step_s: float = 0.05) -> None:
    x = 100
    ctrl.begin_drag(x, 100)
    for target in steps:
        clock.advance(step_s)
        ctrl.drag_to(target, 100)


def test_dragging_right_leans_clockwise(app) -> None:
    clock = _Clock()
    window = _Window(drag_lean_enabled=True)
    ctrl = MotionController(window, clock=clock)
    try:
        _drag(ctrl, clock, [160, 220, 280])
        clock.advance(0.03)
        ctrl._tick()  # noqa: SLF001
        assert ctrl.lean_angle() > 0, "向右拖 → 身体跟着拖动方向顺时针倾"
        clock.advance(0.03)
        ctrl._tick()  # noqa: SLF001
        assert window.updates >= 2, "角度变化必须触发重绘"
    finally:
        ctrl.shutdown()


def test_drag_lean_is_bounded_and_reverses(app) -> None:
    clock = _Clock()
    window = _Window(drag_lean_enabled=True)
    ctrl = MotionController(window, clock=clock)
    try:
        x = 100
        ctrl.begin_drag(x, 100)
        for _ in range(40):
            x += 60
            clock.advance(0.02)
            ctrl.drag_to(x, 100)
            ctrl._tick()  # noqa: SLF001
        assert ctrl.lean_angle() > 0
        assert abs(ctrl.lean_angle()) <= 7.2 + 1e-6, "默认 30% 必须钳在 ±7.2°"

        for _ in range(60):
            x -= 90
            clock.advance(0.02)
            ctrl.drag_to(x, 100)
            ctrl._tick()  # noqa: SLF001
        assert ctrl.lean_angle() < 0, "反向拖拽必须换向"
    finally:
        ctrl.shutdown()


def test_lean_strength_is_configurable(app) -> None:
    clock = _Clock()
    window = _Window(drag_lean_enabled=True, drag_lean_strength=100)
    ctrl = MotionController(window, clock=clock)
    try:
        x = 100
        ctrl.begin_drag(x, 100)
        for _ in range(40):
            x += 60
            clock.advance(0.02)
            ctrl.drag_to(x, 100)
            ctrl._tick()  # noqa: SLF001
        assert 7.2 < abs(ctrl.lean_angle()) <= 24.0 + 1e-6, "100% = 24°"
    finally:
        ctrl.shutdown()

    clock = _Clock()
    window = _Window(drag_lean_enabled=True, drag_lean_strength=0)
    ctrl = MotionController(window, clock=clock)
    try:
        _drag(ctrl, clock, [180, 260, 340])
        clock.advance(0.03)
        ctrl._tick()  # noqa: SLF001
        assert ctrl.lean_angle() == 0.0, "0% 必须完全不倾斜"
    finally:
        ctrl.shutdown()


def test_release_settles_back_to_zero_and_stops_the_timer(app) -> None:
    clock = _Clock()
    window = _Window(drag_lean_enabled=True)
    ctrl = MotionController(window, clock=clock)
    try:
        _drag(ctrl, clock, [180, 260, 340])
        for _ in range(6):
            clock.advance(0.016)
            ctrl._tick()  # noqa: SLF001
        assert ctrl.lean_angle() != 0
        ctrl.release()
        assert ctrl.lean_angle() != 0, "松手瞬间不能瞬移回正"
        for _ in range(80):
            clock.advance(0.016)
            ctrl._tick()  # noqa: SLF001
        assert ctrl.lean_angle() == pytest.approx(0.0, abs=1e-6)
        assert ctrl._timer.isActive() is False  # noqa: SLF001
        assert window.stopped == 1
    finally:
        ctrl.shutdown()


def test_disabled_setting_never_leans(app) -> None:
    clock = _Clock()
    window = _Window(drag_lean_enabled=False)
    ctrl = MotionController(window, clock=clock)
    try:
        _drag(ctrl, clock, [400, 600, 800])
        for _ in range(10):
            clock.advance(0.016)
            ctrl._tick()  # noqa: SLF001
        assert ctrl.lean_angle() == 0.0
    finally:
        ctrl.shutdown()


def test_throw_clears_the_lean(app) -> None:
    clock = _Clock()
    window = _Window(drag_lean_enabled=True, drag_physics_enabled=True)
    ctrl = MotionController(window, clock=clock)
    try:
        _drag(ctrl, clock, [200, 320])
        clock.advance(0.02)
        ctrl._tick()  # noqa: SLF001
        ctrl.begin_throw(800.0, 0.0)
        assert ctrl.lean_angle() == 0.0, "甩飞由击飞角度接管，倾角必须清零"
    finally:
        ctrl.shutdown()


# ------------------------------------------------------------------ 旋转单一出处


class _Host:
    def __init__(self, characters):
        self._characters = characters

    def characters(self):
        return list(self._characters)

    def build_menu(self, parent=None):
        return None

    def open_settings(self, initial_page=None):
        return None

    def reload_mods(self):
        return None

    def open_data_dir(self):
        return None

    def quit_app(self):
        return None


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def test_rotation_angle_sums_the_lean_and_hit_inverse_matches(app, tmp_path) -> None:
    character = CharacterSpec(
        id="lean", name="Lean", root=Path("."), media_kind="none", animations=()
    )
    window = PetWindow(Config(tmp_path), tmp_path, _Host([character]))
    window.start(character)
    try:
        pixmap = QPixmap(window.width(), window.height())
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        painter.fillRect(QRect(0, 0, 8, window.height()), QColor(255, 0, 0, 255))
        painter.end()
        window._pixmap = pixmap
        window._image = pixmap.toImage()

        window.motion._lean = 8.0  # noqa: SLF001
        window.effects._throw_egg_angle = 2.0  # noqa: SLF001 - 击飞通道示例值
        window.effects._throw_egg_active = True  # noqa: SLF001
        window._probe_angle = -1.0
        assert window._rotation_angle() == pytest.approx(9.0)
        assert round(window._rotation_angle(), 1) in window.input_revision()

        # 正变换（paint 用的旋转）与逆变换必须互逆：取一个点，正向转过去再转回来。
        center = (window.width() / 2.0, window.height() / 2.0)
        radians = math.radians(window._rotation_angle())
        px, py = 120.0, 300.0
        dx, dy = px - center[0], py - center[1]
        rotated = QPoint(
            int(round(center[0] + dx * math.cos(radians) - dy * math.sin(radians))),
            int(round(center[1] + dx * math.sin(radians) + dy * math.cos(radians))),
        )
        back = window._unrotated_point(rotated)
        assert back.x() == pytest.approx(px, abs=1.5)
        assert back.y() == pytest.approx(py, abs=1.5)
    finally:
        window.motion._lean = 0.0  # noqa: SLF001
        window.effects._throw_egg_angle = 0.0  # noqa: SLF001
        window.effects._throw_egg_active = False  # noqa: SLF001
        window.shutdown()
