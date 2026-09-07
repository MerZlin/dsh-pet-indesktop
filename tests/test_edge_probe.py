# -*- coding: utf-8 -*-
"""边缘探头控制器/几何测试。"""
from __future__ import annotations

from PySide6.QtCore import QObject, QRect
from PySide6.QtWidgets import QApplication

from pet.edge_probe import (
    EDGE_ENGAGE_EXPOSURE,
    EDGE_ENTER_MS,
    EDGE_IDLE_SECONDS,
    EDGE_PEEK_EXPOSURE,
    EDGE_PROBE_ANGLE,
    EDGE_RETURN_MS,
    EDGE_STRAIGHTEN_MS,
    PEEKING,
    STRAIGHTENED,
    EdgeProbeController,
    edge_side_at_rest,
    probe_window_x,
)


def _qapp():
    return QApplication.instance() or QApplication([])


class Avail:
    def availableGeometry(self):
        return QRect(0, 0, 1000, 800)


class FakeCfg:
    def get(self, key, default=None):
        return default


class FakeWin(QObject):
    def __init__(self):
        super().__init__()
        self._x = 0
        self._y = 100
        self._w = 400
        self._h = 300
        self.cfg = FakeCfg()
        self.anim = "drag"
        self.idles = ["idle"]
        self.turns = ["turn"]
        self.switched = []

    def frameGeometry(self):
        return QRect(self._x, self._y, self._w, self._h)

    def x(self):
        return self._x

    def y(self):
        return self._y

    def move(self, x, y):
        self._x = int(x)
        self._y = int(y)

    def update(self):
        pass

    def screen_available(self, *_args):
        return Avail()

    def character_local_region(self):
        return QRect(100, 0, 200, 200)

    def _frame_draw_rect(self):
        return QRect(0, 0, self._w, self._h)

    def _switch(self, name):
        self.switched.append(name)
        self.anim = name
        return True

    def _pick(self, pool):
        return pool[0]

    def _cancel_move(self):
        pass

    def _stop_physics(self):
        pass


def test_probe_window_x_left_and_right():
    local = QRect(100, 0, 200, 200)
    avail = QRect(0, 0, 1000, 800)
    # left：露出 55%，左侧 off-screen = 90px；窗口 x = 0 - 90 - 100 = -190
    assert probe_window_x("left", EDGE_PEEK_EXPOSURE, local, avail) == -190
    # left：露出 82%，off-screen = 36px；x = -136
    assert probe_window_x("left", EDGE_ENGAGE_EXPOSURE, local, avail) == -136
    # right：露出 55%，右侧 off-screen = 90px；x = 999 + 90 - 299 = 790
    assert probe_window_x("right", EDGE_PEEK_EXPOSURE, local, avail) == 790


def test_edge_side_at_rest_detects_left_and_right():
    win = FakeWin()
    avail = Avail().availableGeometry()
    win.move(-100, 100)
    assert edge_side_at_rest(win, avail) == "left"
    win.move(700, 100)
    assert edge_side_at_rest(win, avail) == "right"
    win.move(0, 100)
    assert edge_side_at_rest(win, avail) is None


def _controller_and_clock():
    times = [0.0]

    def clock():
        return times[0]

    ctrl = EdgeProbeController(FakeWin(), clock=clock)
    ctrl.enabled = True
    return ctrl, times


def test_release_at_left_edge_enters_peek_pose():
    _qapp()
    ctrl, times = _controller_and_clock()
    ctrl.win.move(-100, 100)
    ctrl.win.anim = "drag"
    ctrl.on_release(was_dragging=True)
    assert ctrl.active
    assert ctrl.side == "left"
    assert ctrl.win.anim == "idle"
    times[0] += EDGE_ENTER_MS / 1000.0
    ctrl._on_timer()
    assert ctrl.mode == PEEKING
    assert ctrl.win.x() == -220
    assert ctrl.current_angle_deg() == EDGE_PROBE_ANGLE
    assert abs(ctrl.current_exposure() - EDGE_PEEK_EXPOSURE) < 1e-6
    ctrl.cancel(restore=True)


def test_release_at_right_edge_enters_peek_pose():
    _qapp()
    ctrl, times = _controller_and_clock()
    ctrl.win.move(700, 100)
    ctrl.win.anim = "idle"
    ctrl.on_release(was_dragging=True)
    assert ctrl.active
    assert ctrl.side == "right"
    times[0] += EDGE_ENTER_MS / 1000.0
    ctrl._on_timer()
    assert ctrl.mode == PEEKING
    assert ctrl.win.x() == 821
    assert ctrl.current_angle_deg() == -EDGE_PROBE_ANGLE
    assert abs(ctrl.current_exposure() - EDGE_PEEK_EXPOSURE) < 1e-6
    ctrl.cancel(restore=True)


def test_click_straightens_then_returns_after_idle():
    _qapp()
    ctrl, times = _controller_and_clock()
    ctrl.win.move(-100, 100)
    ctrl.win.anim = "idle"
    ctrl.on_release(was_dragging=True)
    times[0] += EDGE_ENTER_MS / 1000.0
    ctrl._on_timer()

    ctrl.on_clicked()
    assert ctrl.mode == "STRAIGHTENING"
    times[0] += EDGE_STRAIGHTEN_MS / 1000.0
    ctrl._on_timer()
    assert ctrl.mode == STRAIGHTENED
    assert ctrl.current_angle_deg() == 0.0
    assert abs(ctrl.current_exposure() - EDGE_ENGAGE_EXPOSURE) < 1e-6
    assert abs(ctrl._idle_remaining - EDGE_IDLE_SECONDS) < 1e-6
    assert ctrl.win.x() == -136

    times[0] += EDGE_IDLE_SECONDS + 0.1
    ctrl._on_timer()
    assert ctrl.mode == "RETURNING"
    times[0] += EDGE_RETURN_MS / 1000.0
    ctrl._on_timer()
    assert ctrl.mode == PEEKING
    assert ctrl.current_angle_deg() == EDGE_PROBE_ANGLE
    ctrl.cancel(restore=True)


def test_hidden_pause_freezes_state_and_countdown():
    _qapp()
    ctrl, times = _controller_and_clock()
    ctrl.win.move(-100, 100)
    ctrl.win.anim = "idle"
    ctrl.on_release(was_dragging=True)
    times[0] += EDGE_ENTER_MS / 1000.0
    ctrl._on_timer()
    ctrl.on_clicked()
    times[0] += EDGE_STRAIGHTEN_MS / 1000.0
    ctrl._on_timer()
    assert ctrl.mode == STRAIGHTENED
    remaining_before = ctrl._idle_remaining
    ctrl.pause()
    times[0] += 10.0
    ctrl._on_timer()  # hidden 状态应直接忽略，不消耗倒计时
    assert ctrl.mode == STRAIGHTENED
    assert abs(ctrl._idle_remaining - remaining_before) < 1e-6
    ctrl.resume()
    assert ctrl.active
    assert ctrl.mode == STRAIGHTENED
    ctrl.cancel(restore=True)


def test_drag_away_cancels_without_snap_back():
    _qapp()
    ctrl, times = _controller_and_clock()
    ctrl.win.move(-100, 100)
    ctrl.win.anim = "idle"
    ctrl.on_release(was_dragging=True)
    times[0] += EDGE_ENTER_MS / 1000.0
    ctrl._on_timer()
    assert ctrl.win.x() == -220
    ctrl.on_drag_started()
    assert not ctrl.active
    assert ctrl.win.x() == -220  # 用户拖离时位置由用户接管，不 snap
    ctrl.cancel(restore=True)


def test_feature_off_cancels_and_restores_position():
    _qapp()
    ctrl, times = _controller_and_clock()
    ctrl.win.move(-100, 100)
    ctrl.win.anim = "idle"
    ctrl.on_release(was_dragging=True)
    times[0] += EDGE_ENTER_MS / 1000.0
    ctrl._on_timer()
    assert ctrl.win.x() == -220
    ctrl.set_enabled(False)
    assert not ctrl.active
    assert ctrl.win.x() == -100
