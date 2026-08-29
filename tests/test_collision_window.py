# -*- coding: utf-8 -*-
"""Phase 3 验收测试：PetWindow 碰撞接入、状态上报与冲量响应。"""
from __future__ import annotations

import math
import pytest
from PySide6.QtCore import QEvent, QObject, QPoint, QPointF, QRect, Qt, Signal
from PySide6.QtGui import QMouseEvent, QPixmap
from PySide6.QtWidgets import QApplication

from pet import catalog, collision
from pet import physics as physics_mod
from pet.config import Config
from pet.window import PetWindow

NAMES = [
    catalog.IDLE,
    catalog.TURN,
    catalog.MOVES[0],
    catalog.CLICKS[0],
    catalog.DRAG,
    "写代码",
]


class FakeClip(QObject):
    frameChanged = Signal(int)
    finished = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._running = False
        self.speed = 1.0
        self._pm = QPixmap(100, 100)
        self._pm.fill()

    def stop(self):
        self._running = False

    def start(self):
        self._running = True

    def jumpToFrame(self, frame_index):
        return frame_index <= 0

    def set_playback_speed(self, speed):
        self.speed = speed

    def currentPixmap(self):
        return self._pm

    def currentFrameNumber(self):
        return 0

    def frameCount(self):
        return 1

    def duration(self):
        return 1.0

    def currentTimeSeconds(self):
        return 0.0


class FakeLibrary:
    def __init__(self):
        self._clips = {name: FakeClip() for name in NAMES}
        self.manifest = {}
        self.folder_map = {}
        self.folder_files = None
        self.no_mirror = set()

    def names(self):
        return list(NAMES)

    def movies(self):
        return dict(self._clips)

    def movie(self, name):
        return self._clips[name]

    def frames(self, name):
        return 1

    def duration(self, name):
        return 1.0


class FakeCollisionSession(QObject):
    impulse_ready = Signal(object)
    snapshot_ready = Signal(object)
    policy_changed = Signal(object)
    role_changed = Signal(bool, str)

    def __init__(self, runtime_id: str = "test-slot-pid1-abc12345", parent=None):
        super().__init__(parent)
        self.runtime_id = runtime_id
        self.submitted_states: list[dict] = []

    def submit_state(self, state: dict) -> None:
        self.submitted_states.append(dict(state))


@pytest.fixture
def app():
    return QApplication.instance() or QApplication([])


def _make_pet_window(tmp_path, runtime_id="test-slot-pid1-abc12345", collision_enabled=True):
    cfg = Config(str(tmp_path / f"cfg_{runtime_id}.json"))
    cfg.set("collision_enabled", collision_enabled)
    lib = FakeLibrary()
    session = FakeCollisionSession(runtime_id)
    win = PetWindow(lib, cfg, collision_session=session)
    win.resize(100, 100)
    win.show()
    return win, session


def test_impulse_via_queued_signal_applied_immediately_without_physics_tick(tmp_path, app):
    """1. impulse 经 queued Signal 即达应用（不等 _on_physics_tick）。"""
    win, session = _make_pet_window(tmp_path, "pet_a")
    assert win._physics_mode is None
    assert win._phys_vel == [0.0, 0.0]

    # 发送 impulse 冲量
    msg = {
        "a": "pet_a",
        "b": "pet_b",
        "dvx_a": 250.0,
        "dvy_a": -150.0,
        "dx_a": 5.0,
        "dy_a": 0.0,
        "contact_x": float(win.visible_content_rect().center().x() + win.visible_content_rect().width() / 2.0),
        "contact_y": float(win.visible_content_rect().center().y()),
        "nx": -1.0,
        "ny": 0.0,
    }
    session.impulse_ready.emit(msg)
    app.processEvents()

    # 冲量已即时加到速度，且产生位置移动（速度经 soft_clamp 略有衰减）
    assert win._phys_vel[0] > 0.0
    assert win._phys_vel[1] < 0.0
    assert win._squash_active is True
    win.close()


def test_idle_pet_enters_thrown_and_physics_timer_starts_on_dead_zone_speed(tmp_path, app):
    """2. 空闲桌宠收到 ≥DEAD_ZONE_SPEED 冲量后进入 THROWN 且 _physics_timer 启动。"""
    win, session = _make_pet_window(tmp_path, "pet_a")
    assert not win._physics_timer.isActive()
    assert win._interaction_state == 'IDLE'

    # DEAD_ZONE_SPEED 为 500.0 px/s，发送一个足以超过 500 px/s 的冲量
    msg = {
        "a": "pet_a",
        "b": "pet_b",
        "dvx_a": 1200.0,
        "dvy_a": 0.0,
        "dx_a": 0.0,
        "dy_a": 0.0,
        "contact_x": float(win.visible_content_rect().center().x() + win.visible_content_rect().width() / 2.0),
        "contact_y": float(win.visible_content_rect().center().y()),
        "nx": -1.0,
        "ny": 0.0,
    }
    session.impulse_ready.emit(msg)
    app.processEvents()

    assert win._interaction_state == 'THROWN'
    assert win._physics_mode == 'throw'
    assert win._physics_timer.isActive()

    # 低于 DEAD_ZONE_SPEED 冲量：不进入 THROWN，不启动持续抛掷 timer
    win2, session2 = _make_pet_window(tmp_path, "pet_b")
    msg_small = {
        "a": "pet_b",
        "b": "pet_a",
        "dvx_a": 50.0,  # 50 < DEAD_ZONE_SPEED (500)
        "dvy_a": 0.0,
        "contact_x": float(win2.visible_content_rect().center().x() + win2.visible_content_rect().width() / 2.0),
        "contact_y": float(win2.visible_content_rect().center().y()),
        "nx": -1.0,
        "ny": 0.0,
    }
    session2.impulse_ready.emit(msg_small)
    app.processEvents()

    assert win2._interaction_state != 'THROWN'
    assert not win2._physics_timer.isActive()

    win.close()
    win2.close()


def test_impulse_discarded_during_paused_or_hidden_and_zero_vel_re_registered(tmp_path, app):
    """3. PAUSED/隐藏期间 impulse 被丢弃，恢复后零速度重新注册。"""
    win, session = _make_pet_window(tmp_path, "pet_a")
    win.hide()
    app.processEvents()

    # 隐藏时发送冲量
    msg = {
        "a": "pet_a",
        "b": "pet_b",
        "dvx_a": 400.0,
        "dvy_a": 0.0,
    }
    session.impulse_ready.emit(msg)
    app.processEvents()

    # 冲量被丢弃，速度和状态未变
    assert win._phys_vel == [0.0, 0.0]
    assert win._interaction_state != 'THROWN'

    # 恢复显示：重新注册状态
    session.submitted_states.clear()
    win.show()
    app.processEvents()

    # 零速度且包含 VISIBLE flag
    assert win._phys_vel == [0.0, 0.0]
    assert len(session.submitted_states) > 0
    last_state = session.submitted_states[-1]
    assert last_state["vx"] == 0.0
    assert last_state["vy"] == 0.0
    assert (last_state["flags"] & collision.FLAG_VISIBLE) != 0

    win.close()


def test_contact_deviation_over_10_percent_applies_velocity_without_displacement(tmp_path, app):
    """4. 接触点偏差 >半径10% 时只应用速度冲量、无位置位移（高速擦碰不反向位移）。"""
    win, session = _make_pet_window(tmp_path, "pet_a")
    start_pos = (win.x(), win.y())
    rect = win.visible_content_rect()
    radius_x = max(1.0, rect.width() / 2.0)

    # 构造一个严重偏离的期望接触点（远超 10% 半径）
    far_contact_x = rect.center().x() + radius_x * 5.0
    msg = {
        "a": "pet_a",
        "b": "pet_b",
        "dvx_a": 200.0,
        "dvy_a": 0.0,
        "dx_a": 10.0,  # 本应产生 10px 位移
        "dy_a": 0.0,
        "contact_x": far_contact_x,
        "contact_y": float(rect.center().y()),
        "nx": -1.0,
        "ny": 0.0,
    }
    session.impulse_ready.emit(msg)
    app.processEvents()

    # 速度冲量生效
    assert win._phys_vel[0] > 0.0
    # 位置位移被丢弃，保持原位
    assert (win.x(), win.y()) == start_pos

    win.close()


def test_click_suppressed_for_120ms_after_impulse(tmp_path, app):
    """5. impulse 后 120ms 内 click 被抑制。"""
    win, session = _make_pet_window(tmp_path, "pet_a")
    assert win._just_dragged is False

    msg = {
        "a": "pet_a",
        "b": "pet_b",
        "dvx_a": 150.0,
        "dvy_a": 0.0,
    }
    session.impulse_ready.emit(msg)
    app.processEvents()

    # 触发后 _just_dragged 置为 True，阻止 click 响应
    assert win._just_dragged is True

    # 模拟在 120ms 内点击
    win.clicks = ["写代码"]
    initial_anim = win.anim
    win._on_click()
    # 点击被抑制，动画未切换
    assert win.anim == initial_anim

    # 清除 _just_dragged 后可以点击响应
    win._clear_just_dragged()
    assert win._just_dragged is False
    win._on_click()
    assert win.anim == "写代码"

    win.close()


def test_dragging_reports_position_delta_velocity_not_phys_vel(tmp_path, app):
    """6. 拖拽中上报的 vx/vy 是位置差分而非 _phys_vel。"""
    win, session = _make_pet_window(tmp_path, "pet_a")
    win._phys_vel = [0.0, 0.0]
    win._interaction_state = 'DRAGGING'
    session.submitted_states.clear()

    # 模拟 trail 位置差分
    # 0.1s 移动 (20, 10) -> 速度为 (200.0, 100.0)
    t0 = 100.0
    t1 = 100.1
    win._trail = [(t0, 100, 100), (t1, 120, 110)]

    win._submit_collision_state(force=True)
    app.processEvents()

    assert len(session.submitted_states) > 0
    state = session.submitted_states[-1]
    assert state["vx"] == pytest.approx(200.0, abs=1.0)
    assert state["vy"] == pytest.approx(100.0, abs=1.0)
    # _phys_vel 仍为 0.0，证明上报的是差分速度
    assert win._phys_vel == [0.0, 0.0]

    win.close()


def test_collision_disabled_does_not_report_receive_and_detaches(tmp_path, app):
    """7. collision_enabled=False 时不上报、不接收、从成员表退出。"""
    win, session = _make_pet_window(tmp_path, "pet_a", collision_enabled=False)
    assert win._collision_session is None

    session.submitted_states.clear()
    win._submit_collision_state(force=True)
    # 不上报
    assert len(session.submitted_states) == 0

    # 冲量被忽略
    msg = {
        "a": "pet_a",
        "b": "pet_b",
        "dvx_a": 300.0,
        "dvy_a": 0.0,
    }
    session.impulse_ready.emit(msg)
    app.processEvents()
    assert win._phys_vel == [0.0, 0.0]

    # 动态切换：开启后 attach，关闭后 detach
    win.cfg.set("collision_enabled", True)
    win.refresh_pet_settings()
    assert win._collision_session is session

    win.cfg.set("collision_enabled", False)
    win.refresh_pet_settings()
    assert win._collision_session is None

    win.close()
