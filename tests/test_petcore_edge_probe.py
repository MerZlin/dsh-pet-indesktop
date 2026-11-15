from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication

from petcore.ui.edge_probe import (
    ENTERING,
    PEEKING,
    RETURNING,
    STRAIGHTENED,
    STRAIGHTENING,
    EdgeProbeController,
)


class FakeClock:
    def __init__(self):
        self.now = 100.0

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


class FakeWindow:
    def __init__(self, *, at_edge=True):
        self.config = _Config({"edge_probe_enabled": True})
        self.dragging = False
        self._x = -153 if at_edge else 500
        self._at_edge = at_edge
        self.poses = []
        self.moves = []
        self.stops = 0
        self.remembered = 0

    def body_local_rect(self):
        return (153, 43, 156, 194)

    def body_rect(self):
        return (self._x + 153, 43, 156, 194)

    def probe_bounds(self):
        return (0, 0, 1280, 752)

    def width(self):
        return 461

    def height(self):
        return 259

    def x(self):
        return self._x

    def set_probe_pose(self, angle, exposure):
        self.poses.append((float(angle), float(exposure)))

    def move_probe_to(self, x):
        self._x = int(round(x))
        self.moves.append(self._x)

    def stop_for_probe(self):
        self.stops += 1

    def remember_probe_geometry(self):
        self.remembered += 1


class _Config:
    def __init__(self, data):
        self.data = dict(data)

    def get(self, key, default=None):
        return self.data.get(key, default)


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def _enter(app):
    clock = FakeClock()
    window = FakeWindow()
    probe = EdgeProbeController(window, clock=clock)
    probe._enter_if_at_edge()
    assert probe.mode == ENTERING
    clock.advance(0.31)
    probe._tick()
    assert probe.mode == PEEKING
    assert probe.angle() == pytest.approx(45.0, abs=1.0)
    assert probe.exposure() == pytest.approx(0.55, abs=0.02)
    return probe, window, clock


def test_enter_and_peek(app):
    probe, window, _clock = _enter(app)
    assert window.stops == 1
    assert window.moves
    assert window.remembered == 1
    probe.shutdown()


def test_click_straightens_and_returns(app):
    probe, _window, clock = _enter(app)
    assert probe.on_clicked() is True
    assert probe.mode == STRAIGHTENING
    clock.advance(0.26)
    probe._tick()
    assert probe.mode == STRAIGHTENED
    clock.advance(5.1)
    probe._tick()
    assert probe.mode == RETURNING
    clock.advance(0.31)
    probe._tick()
    assert probe.mode == PEEKING
    assert probe.angle() == pytest.approx(45.0, abs=1.0)
    probe.shutdown()


def test_drag_cancels_without_restoring(app):
    probe, window, _clock = _enter(app)
    before = window.x()
    probe.on_drag_started()
    assert probe.mode == "OFF"
    assert window.poses[-1] == (0.0, 1.0)
    assert window.x() == before
    probe.shutdown()


def test_disable_restores_the_pre_probe_x(app):
    probe, window, _clock = _enter(app)
    probe.set_enabled(False)
    assert probe.mode == "OFF"
    assert window.x() == -153
    probe.shutdown()


def test_hide_pauses_and_resume_keeps_transition(app):
    clock = FakeClock()
    window = FakeWindow()
    probe = EdgeProbeController(window, clock=clock)
    probe._enter_if_at_edge()
    clock.advance(0.1)
    probe.pause()
    clock.advance(10.0)
    probe.resume()
    clock.advance(0.2)
    probe._tick()
    assert probe.mode == PEEKING
    probe.shutdown()


def test_thrown_or_moving_pet_never_enters_probe(app):
    from types import SimpleNamespace

    window = FakeWindow(at_edge=True)
    window.motion = SimpleNamespace(
        thrown=True, dragging=False, velocity=lambda: (400.0, 0.0)
    )
    probe = EdgeProbeController(window, clock=FakeClock())
    probe._enter_if_at_edge()
    assert probe.mode == "OFF"
    assert window.poses == []
    assert window.moves == []
    probe.on_release(True)
    assert probe._rest_timer.isActive() is False
    probe.shutdown()


def test_settled_pet_still_enters_probe_after_throw(app):
    from types import SimpleNamespace

    window = FakeWindow(at_edge=True)
    window.motion = SimpleNamespace(
        thrown=False, dragging=False, velocity=lambda: (0.0, 0.0)
    )
    probe = EdgeProbeController(window, clock=FakeClock())
    probe._enter_if_at_edge()
    assert probe.mode == "ENTERING"
    probe.shutdown()
