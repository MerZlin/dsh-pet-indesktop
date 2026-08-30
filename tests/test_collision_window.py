# -*- coding: utf-8 -*-
"""Phase 3 验收测试：PetWindow 碰撞接入、状态上报与冲量响应。"""
from __future__ import annotations

import math
import time
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
        self.policy_updates: list[dict] = []
        self.leave_calls = 0

    def submit_state(self, state: dict) -> None:
        self.submitted_states.append(dict(state))

    def update_policy(self, policy: dict) -> None:
        self.policy_updates.append(dict(policy))

    def submit_leave(self) -> None:
        self.leave_calls += 1


@pytest.fixture
def app():
    return QApplication.instance() or QApplication([])


def _make_pet_window(tmp_path, runtime_id="test-slot-pid1-abc12345", collision_enabled=True, lock_position=False):
    cfg = Config(str(tmp_path / f"cfg_{runtime_id}.json"))
    cfg.set("collision_enabled", collision_enabled)
    cfg.set("lock_position", lock_position)
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


def test_collision_squash_is_not_restarted_within_250ms(tmp_path, app):
    """碰撞 squash 活跃期间，250ms 内的第二次 impulse 不重置动画进度。"""
    win, session = _make_pet_window(tmp_path, "pet_a")
    msg = {
        "a": "pet_a", "b": "pet_b", "dvx_a": 250.0, "dvy_a": 0.0,
        "dx_a": 0.0, "dy_a": 0.0,
    }
    session.impulse_ready.emit(msg)
    app.processEvents()
    assert win._squash_active is True

    win._squash_progress = 0.4
    session.impulse_ready.emit(msg)
    app.processEvents()
    assert win._squash_progress == pytest.approx(0.4)

    win.close()


def test_position_only_collision_does_not_throw_or_squash(tmp_path, app):
    """低速接触的 position-only 消息不触发 THROWN 或 squash。"""
    win, session = _make_pet_window(tmp_path, "pet_a")
    msg = {
        "a": "pet_a", "b": "pet_b", "dvx_a": 0.0, "dvy_a": 0.0,
        "dx_a": -2.0, "dy_a": 0.0,
    }
    session.impulse_ready.emit(msg)
    app.processEvents()

    assert win._interaction_state != "THROWN"
    assert win._physics_mode is None
    assert win._squash_active is False
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


def test_collision_throw_cancels_move_plan_and_timer(tmp_path, app):
    win, session = _make_pet_window(tmp_path, "pet_a")
    win._move_plan = {"start_x": 0, "target_x": 20, "start_y": 0, "target_y": 0, "duration": 1.0}
    win._move_timer.start()
    session.impulse_ready.emit({"a": "pet_a", "b": "pet_b", "dvx_a": 1200.0, "dvy_a": 0.0,
                                "dx_a": 0.0, "dy_a": 0.0})
    app.processEvents()
    assert win._interaction_state == "THROWN"
    assert win._move_plan is None
    assert not win._move_timer.isActive()
    win.close()


def test_slingshot_launch_cancels_move_plan(tmp_path, app):
    win, session = _make_pet_window(tmp_path, "pet_a")
    win._move_plan = {"start_x": 0, "target_x": 20, "start_y": 0, "target_y": 0, "duration": 1.0}
    win._move_timer.start()
    win._slingshot_anchor_pos = QPoint(win.pos())
    win._slingshot_pull = QPoint(100, 0)
    win._launch_slingshot(QPoint(win.x() + 100, win.y()))
    assert win._interaction_state == "THROWN"
    assert win._move_plan is None
    assert not win._move_timer.isActive()
    assert session.submitted_states[-1]["flags"] & collision.FLAG_THROWN
    assert session.submitted_states[-1]["vx"] != 0.0 or session.submitted_states[-1]["vy"] != 0.0
    win.close()


def test_physics_mode_guards_movement_and_stop_restores_it(tmp_path, app):
    win, _ = _make_pet_window(tmp_path, "pet_a")
    win._physics_mode = "throw"
    assert win._try_move() is False
    win._move_plan = {"start_x": 0, "target_x": 20, "start_y": 0, "target_y": 0, "duration": 1.0}
    win._move_timer.start()
    win._on_move_tick()
    assert win._move_plan is None
    assert not win._move_timer.isActive()
    win._stop_physics()
    win._move_plan = {"start_x": 0, "target_x": 20, "start_y": 0, "target_y": 0, "duration": 1.0}
    assert win._try_move() is True
    win.close()


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

    # 偏差豁免按"协调者认定的我方中心"判定：构造一个严重偏离的 ax（远超 10% 半径）
    far_ax = rect.center().x() + radius_x * 5.0
    msg = {
        "a": "pet_a",
        "b": "pet_b",
        "dvx_a": 200.0,
        "dvy_a": 0.0,
        "dx_a": 10.0,  # 本应产生 10px 位移
        "dy_a": 0.0,
        "ax": far_ax,
        "ay": float(rect.center().y()),
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


def test_lock_position_window_ignores_impulse(tmp_path, app):
    """锁定位置窗口收到 impulse：位置不变、不进 THROWN、无速度（与协调者侧无限质量互为双保险）。"""
    win, session = _make_pet_window(tmp_path, "pet_lock", lock_position=True)
    assert win.lock_position is True
    start_pos = (win.x(), win.y())
    rect = win.visible_content_rect()

    msg = {
        "a": "pet_lock",
        "b": "pet_other",
        "dvx_a": 1200.0,
        "dvy_a": 0.0,
        "dx_a": 20.0,
        "dy_a": 0.0,
        "contact_x": float(rect.center().x() + rect.width() / 2.0),
        "contact_y": float(rect.center().y()),
        "nx": -1.0,
        "ny": 0.0,
    }
    session.impulse_ready.emit(msg)
    app.processEvents()

    assert (win.x(), win.y()) == start_pos
    assert win._phys_vel == [0.0, 0.0]
    assert win._interaction_state != 'THROWN'
    assert win._physics_mode is None

    win.close()


def test_detach_collision_session_sends_leave(tmp_path, app):
    """detach_collision_session 时向会话发 leave：协调者即时移除成员（不等 stale 超时）。"""
    win, session = _make_pet_window(tmp_path, "pet_a")
    assert win._collision_session is session
    assert session.leave_calls == 0

    win.detach_collision_session()
    assert win._collision_session is None
    assert session.leave_calls == 1

    # 再次 detach（已无会话）不发 leave
    win.detach_collision_session()
    assert session.leave_calls == 1

    win.close()


def test_submit_collision_state_dedup_excludes_ts(tmp_path, app):
    """comparable 不含 ts：状态未变化时非 force 提交被去重（去重不再恒失效）。"""
    win, session = _make_pet_window(tmp_path, "pet_a")
    win._collision_timer.stop()  # 排除定时器 force 兜底对计数的干扰
    session.submitted_states.clear()
    win._collision_last_submit_at = 0.0  # 清掉 showEvent force 提交留下的限流窗口

    # 与上次 force 提交（showEvent）状态一致 → 全部被去重
    win._submit_collision_state()
    win._submit_collision_state()
    assert len(session.submitted_states) == 0

    # 位置变化 → 非 force 放行一次
    win.move(win.x() + 1, win.y())
    app.processEvents()
    assert len(session.submitted_states) == 1

    # 把限流时钟拨到"刚提交过"：再次变化也被 50ms 窗口跳过
    win._collision_last_submit_at = time.monotonic()
    win.move(win.x() + 2, win.y())
    app.processEvents()
    assert len(session.submitted_states) == 1

    # 拨回"很久以前"：限流窗口已过，再次变化放行
    win._collision_last_submit_at = time.monotonic() - 0.2
    win.move(win.x() + 3, win.y())
    app.processEvents()
    assert len(session.submitted_states) == 2

    win.close()


def test_move_event_submits_throttled_not_forced(tmp_path, app):
    """moveEvent 非 force 节流提交：60Hz 连续移动不超标（上限 20Hz）。"""
    win, session = _make_pet_window(tmp_path, "pet_a")
    win._collision_timer.stop()  # 排除定时器 force 兜底对计数的干扰
    session.submitted_states.clear()
    win._collision_last_submit_at = 0.0  # 保证首个 move 放行
    start = win.pos()

    # 模拟 60Hz 抛掷：极短时间内连续 20 次移动，全部落在 50ms 限流窗口内
    for i in range(1, 21):
        win.move(start.x() + i, start.y())
    app.processEvents()

    # moveEvent 路径只放行首个提交，其余被节流（运动期由 _collision_timer 兜底）
    assert len(session.submitted_states) == 1

    win.close()


def _prediction_peer(win, runtime_id="pet_b", vx=0.0, flags=None):
    rect = win.collision_content_rect()
    own_circles = collision.circles_from_rect(rect.x(), rect.y(), rect.width(), rect.height())
    cx, cy = rect.center().x(), rect.center().y()
    peer_circles = [[x + 30.0, y, r] for x, y, r in own_circles]
    return {
        "runtime_id": runtime_id,
        "x": cx + 30.0, "y": cy, "radius_x": rect.width() / 2.0,
        "radius_y": rect.height() / 2.0, "vx": vx, "vy": 0.0,
        "flags": flags if flags is not None else collision.FLAG_VISIBLE | collision.FLAG_COLLISION_ENABLED,
        "circles": peer_circles, "scale": win.scale,
    }


def test_collision_snapshot_stores_epoch_and_clears_stale_members(tmp_path, app):
    win, session = _make_pet_window(tmp_path, "pet_a")
    session.snapshot_ready.emit({"epoch": "epoch-1", "members": [_prediction_peer(win)]})
    app.processEvents()
    assert "pet_b" in win._collision_peer_snapshots
    assert win._collision_peer_snapshots["pet_b"]["_received_at"] > 0

    win._collision_peer_snapshots["pet_b"]["_received_at"] = time.monotonic() - 1.6
    win._prune_collision_prediction_state(time.monotonic())
    assert win._collision_peer_snapshots == {}

    session.snapshot_ready.emit({"epoch": "epoch-2", "members": [_prediction_peer(win)]})
    app.processEvents()
    assert win._collision_peer_snapshots == {}
    win.close()


def test_throw_predicts_bounce_and_authoritative_impulse_is_reconciled(tmp_path, app):
    win, session = _make_pet_window(tmp_path, "pet_a")
    win._physics_mode = "throw"
    win._interaction_state = "THROWN"
    win._phys_pos[:] = [float(win.x()), float(win.y())]
    win._phys_vel[:] = [9000.0, 0.0]
    win.cfg.set("collision_impulse_cap", 20000.0)
    peer = _prediction_peer(win, flags=collision.FLAG_VISIBLE | collision.FLAG_COLLISION_ENABLED |
                            collision.FLAG_LOCK_POSITION)
    peer["_received_at"] = time.monotonic()
    win._collision_peer_snapshots["pet_b"] = peer

    before = win._phys_vel[0]
    win._predict_collision_bounce(win._phys_pos[0], win._phys_pos[1])
    assert win._phys_vel[0] < 0.0
    assert math.hypot(*win._phys_vel) <= win._throw_speed_cap + 1e-6
    assert win._squash_active is True
    assert "pet_a|pet_b" in win._predicted_bounces

    predicted_velocity = tuple(win._phys_vel)
    predicted_position = (win.x(), win.y())
    session.impulse_ready.emit({"a": "pet_a", "b": "pet_b", "pair": "pet_a|pet_b",
                                "dvx_a": 4000.0, "dvy_a": 0.0, "dx_a": 9.0, "dy_a": 0.0})
    app.processEvents()
    assert tuple(win._phys_vel) == predicted_velocity
    assert (win.x(), win.y()) == predicted_position
    win.close()


def test_throw_prediction_requires_approach_speed_and_enabled_throw_mode(tmp_path, app):
    win, _ = _make_pet_window(tmp_path, "pet_a")
    win._physics_mode = "throw"
    win._interaction_state = "THROWN"
    win._phys_pos[:] = [float(win.x()), float(win.y())]
    win._phys_vel[:] = [10.0, 0.0]
    peer = _prediction_peer(win, vx=0.0)
    peer["_received_at"] = time.monotonic()
    win._collision_peer_snapshots["pet_b"] = peer
    win._predict_collision_bounce(win._phys_pos[0], win._phys_pos[1])
    assert win._predicted_bounces == {}

    win._physics_mode = None
    win._phys_vel[:] = [9000.0, 0.0]
    win.cfg.set("collision_enabled", False)
    win._predict_collision_bounce(win._phys_pos[0], win._phys_pos[1])
    assert win._predicted_bounces == {}
    win.close()


def test_authoritative_impulse_applies_after_prediction_window(tmp_path, app):
    win, session = _make_pet_window(tmp_path, "pet_a")
    win._predicted_bounces["pet_a|pet_b"] = time.monotonic() - 0.21
    session.impulse_ready.emit({"a": "pet_a", "b": "pet_b", "pair": "pet_a|pet_b",
                                "dvx_a": 200.0, "dvy_a": 0.0})
    app.processEvents()
    assert win._phys_vel[0] > 0.0
    win.close()


def test_move_event_submits_throttled_not_forced(tmp_path, app):
    """moveEvent 非 force 节流提交：60Hz 连续移动不超标（上限 20Hz）。"""
    win, session = _make_pet_window(tmp_path, "pet_a")
    session.submitted_states.clear()
    win._collision_last_submit_at = 0.0  # 保证首个 move 放行
    start = win.pos()

    # 模拟 60Hz 抛掷：极短时间内连续 20 次移动，全部落在 50ms 限流窗口内
    for i in range(1, 21):
        win.move(start.x() + i, start.y())
    app.processEvents()

    # moveEvent 路径只放行首个提交，其余被节流（运动期由 _collision_timer 兜底）
    assert len(session.submitted_states) == 1

    win.close()
