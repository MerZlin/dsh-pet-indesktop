# -*- coding: utf-8 -*-
"""Phase 2 IPC 会话测试：协议边界、线程生命周期和实际 QLocal 选举。"""
from __future__ import annotations

import os
import json
import subprocess
import sys
import time
import uuid

import pytest
from PySide6.QtCore import QEventLoop
from PySide6.QtWidgets import QApplication

from pet import collision
from pet.collision_ipc import CollisionIpcSession, _CollisionWorker, make_runtime_id
from pet.config import Config


class FakeSocket:
    def __init__(self):
        self.sent = []

    def write(self, data):
        self.sent.append(data)
        return len(data)

    def flush(self):
        return True


def _pump(seconds: float) -> None:
    app = QApplication.instance() or QApplication([])
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        app.processEvents(QEventLoop.ProcessEventsFlag.AllEvents, 10)
        time.sleep(0.005)


def _state(seq, x=0.0):
    return {"seq": seq, "ts": time.monotonic(), "x": x, "y": 0.0,
            "w": 100, "h": 100, "radius_x": 40.0, "radius_y": 40.0,
            "vx": 0.0, "vy": 0.0, "flags": collision.FLAG_VISIBLE | collision.FLAG_COLLISION_ENABLED}


def _server_name(label: str) -> str:
    """Keep POSIX Unix-socket paths below the platform length limit."""
    return f"d42-{label}-{uuid.uuid4().hex[:8]}"


def test_fake_socket_join_duplicate_seq_and_disconnect():
    worker = _CollisionWorker("unused-" + uuid.uuid4().hex, "coordinator", "", {
        "collision_enabled": True, "collision_restitution": .82,
        "collision_friction": .08, "collision_mass_scale": 1.0,
        "collision_impulse_cap": 9000.0,
    })
    worker.epoch = "epoch-a"
    worker.server = object()
    socket = FakeSocket()
    worker.peers[socket] = "remote"
    worker._handle_message(socket, {"type": "state", **_state(2, 10)})
    worker._handle_message(socket, {"type": "state", **_state(1, 99)})
    assert worker.members["remote"]["x"] == 10
    worker.peers.pop(socket)
    worker._remove_member("remote")
    assert "remote" not in worker.members


def test_coordinator_sweeps_fast_circle_chain_and_emits_impulse():
    worker = _CollisionWorker("unused-" + uuid.uuid4().hex, "coordinator", "", {
        "collision_enabled": True, "collision_restitution": .82,
        "collision_friction": .08, "collision_mass_scale": 1.0,
        "collision_impulse_cap": 9000.0,
    })
    worker.server = object()
    worker.epoch = "epoch-a"
    worker._now = staticmethod(lambda: 1.0)
    flags = collision.FLAG_VISIBLE | collision.FLAG_COLLISION_ENABLED
    state_a = {"seq": 1, "x": 450.0, "y": 0.0, "radius_x": 30.0, "radius_y": 30.0,
               "vx": 4800.0, "vy": 0.0, "flags": flags,
               "circles": [[450.0, 0.0, 30.0]]}
    state_b = {"seq": 1, "x": 225.0, "y": 0.0, "radius_x": 30.0, "radius_y": 30.0,
               "vx": 0.0, "vy": 0.0, "flags": flags,
               "circles": [[225.0, 0.0, 30.0]]}
    worker.members = {
        "a": dict(state_a, runtime_id="a", last_seen=1.0),
        "b": dict(state_b, runtime_id="b", last_seen=1.0),
    }
    worker.previous_members = {
        "a": dict(state_a, seq=0, x=0.0, circles=[[0.0, 0.0, 30.0]]),
        "b": dict(state_b, seq=0),
    }
    received = []
    worker.impulse_ready.connect(received.append)
    worker._coordinator_tick()
    assert received
    assert received[0]["j"] > 0.0
    # TOI 语义：首次接触时 A 圆心在 225 − 60 = 165
    assert received[0]["contact_x"] == pytest.approx(165.0)


def _coordinator_with_members(*states):
    worker = _CollisionWorker("unused-" + uuid.uuid4().hex, "coordinator", "", {
        "collision_enabled": True, "collision_restitution": .82,
        "collision_friction": .08, "collision_mass_scale": 1.0,
        "collision_impulse_cap": 9000.0,
    })
    worker.server = object()
    worker.epoch = "epoch-a"
    worker._now = staticmethod(lambda: 1.0)
    worker.members = {
        state["runtime_id"]: dict(state, last_seen=1.0) for state in states
    }
    for state in states:
        if (int(state.get("flags", 0)) & collision.FLAG_PREDICTED_BOUNCE
                and state.get("bounce_vx") is not None
                and state.get("bounce_vy") is not None):
            worker._pending_predicted[state["runtime_id"]] = {**state, "_captured_at": 1.0}
    return worker


@pytest.mark.parametrize(
    ("shooter_scale", "target_scale", "minimum_target_dv"),
    [(0.3, 2.0, 500.0), (2.0, 0.3, 1500.0)],
)
def test_predicted_bounce_event_sends_impulse_only_to_target(
        shooter_scale, target_scale, minimum_target_dv):
    flags = collision.FLAG_VISIBLE | collision.FLAG_COLLISION_ENABLED
    shooter = {
        "runtime_id": "a", "seq": 2, "x": 0.0, "y": 0.0,
        "radius_x": 30.0, "radius_y": 30.0, "circles": [[0.0, 0.0, 30.0]],
        "vx": -800.0, "vy": 0.0, "bounce_vx": 2000.0, "bounce_vy": 0.0,
        "scale": shooter_scale, "flags": flags | collision.FLAG_PREDICTED_BOUNCE,
    }
    target = {
        "runtime_id": "b", "seq": 1, "x": 50.0, "y": 0.0,
        "radius_x": 30.0, "radius_y": 30.0, "circles": [[50.0, 0.0, 30.0]],
        "vx": 0.0, "vy": 0.0, "scale": target_scale, "flags": flags,
    }
    worker = _coordinator_with_members(shooter, target)
    received = []
    worker.impulse_ready.connect(received.append)

    worker._coordinator_tick()

    assert len(received) == 1
    assert received[0]["dvx_a"] == 0.0
    assert received[0]["dvy_a"] == 0.0
    assert received[0]["dvx_b"] > minimum_target_dv
    assert worker.members["a"]["vx"] == -800.0
    assert not worker.members["a"]["flags"] & collision.FLAG_PREDICTED_BOUNCE
    assert "bounce_vx" not in worker.members["a"]

    worker._coordinator_tick()
    assert len(received) == 1


def test_predicted_bounce_and_sweep_emit_pair_only_once_in_same_tick():
    flags = collision.FLAG_VISIBLE | collision.FLAG_COLLISION_ENABLED
    shooter = {
        "runtime_id": "a", "seq": 2, "x": 60.0, "y": 0.0,
        "radius_x": 30.0, "radius_y": 30.0, "circles": [[60.0, 0.0, 30.0]],
        "vx": -500.0, "vy": 0.0, "bounce_vx": 2000.0, "bounce_vy": 0.0,
        "flags": flags | collision.FLAG_PREDICTED_BOUNCE,
    }
    target = {
        "runtime_id": "b", "seq": 1, "x": 120.0, "y": 0.0,
        "radius_x": 30.0, "radius_y": 30.0, "circles": [[120.0, 0.0, 30.0]],
        "vx": 0.0, "vy": 0.0, "flags": flags,
    }
    worker = _coordinator_with_members(shooter, target)
    worker.previous_members = {
        "a": dict(shooter, seq=1, x=0.0, circles=[[0.0, 0.0, 30.0]]),
        "b": dict(target),
    }
    received = []
    worker.impulse_ready.connect(received.append)
    worker._coordinator_tick()
    assert [message["pair"] for message in received] == ["a|b"]


def test_state_without_predicted_bounce_fields_uses_normal_collision_path():
    flags = collision.FLAG_VISIBLE | collision.FLAG_COLLISION_ENABLED
    moving = {
        "runtime_id": "a", "seq": 1, "x": 0.0, "y": 0.0,
        "radius_x": 30.0, "radius_y": 30.0, "circles": [[0.0, 0.0, 30.0]],
        "vx": 500.0, "vy": 0.0, "flags": flags,
    }
    target = {
        "runtime_id": "b", "seq": 1, "x": 50.0, "y": 0.0,
        "radius_x": 30.0, "radius_y": 30.0, "circles": [[50.0, 0.0, 30.0]],
        "vx": 0.0, "vy": 0.0, "flags": flags,
    }
    worker = _coordinator_with_members(moving, target)
    received = []
    worker.impulse_ready.connect(received.append)
    worker._coordinator_tick()
    assert len(received) == 1
    assert received[0]["dvx_a"] < 0.0
    assert received[0]["dvx_b"] > 0.0


def test_predicted_bounce_overwritten_by_unflagged_state_still_emits_predicted_impulse():
    flags = collision.FLAG_VISIBLE | collision.FLAG_COLLISION_ENABLED
    worker = _CollisionWorker("unused-" + uuid.uuid4().hex, "coordinator", "", {
        "collision_enabled": True, "collision_restitution": .82,
        "collision_friction": .08, "collision_mass_scale": 1.0,
        "collision_impulse_cap": 9000.0,
    })
    worker.server = object()
    worker.epoch = "epoch-a"
    worker._now = staticmethod(lambda: 1.0)

    socket_a = FakeSocket()
    socket_b = FakeSocket()
    worker.peers[socket_a] = "a"
    worker.peers[socket_b] = "b"

    # target state
    worker._handle_message(socket_b, {
        "type": "state", "seq": 1, "x": 50.0, "y": 0.0,
        "radius_x": 30.0, "radius_y": 30.0, "circles": [[50.0, 0.0, 30.0]],
        "vx": 0.0, "vy": 0.0, "scale": 1.0, "flags": flags,
    })

    # shooter state with predicted bounce
    worker._handle_message(socket_a, {
        "type": "state", "seq": 2, "x": 0.0, "y": 0.0,
        "radius_x": 30.0, "radius_y": 30.0, "circles": [[0.0, 0.0, 30.0]],
        "vx": -800.0, "vy": 0.0, "bounce_vx": 1500.0, "bounce_vy": 0.0,
        "scale": 1.0, "flags": flags | collision.FLAG_PREDICTED_BOUNCE,
    })
    assert "a" in worker._pending_predicted

    # overwrite shooter state in members table with unflagged state
    worker._handle_message(socket_a, {
        "type": "state", "seq": 3, "x": 0.0, "y": 0.0,
        "radius_x": 30.0, "radius_y": 30.0, "circles": [[0.0, 0.0, 30.0]],
        "vx": -800.0, "vy": 0.0,
        "scale": 1.0, "flags": flags,
    })
    assert not (worker.members["a"]["flags"] & collision.FLAG_PREDICTED_BOUNCE)
    assert "bounce_vx" not in worker.members["a"]
    assert "a" in worker._pending_predicted

    received = []
    worker.impulse_ready.connect(received.append)
    worker._coordinator_tick()

    assert len(received) == 1
    assert received[0]["dvx_a"] == 0.0
    assert received[0]["dvy_a"] == 0.0
    assert received[0]["dvx_b"] > 500.0
    assert "a" not in worker._pending_predicted


def test_predicted_bounce_ttl_expiration_cleans_pending_and_no_impulse():
    flags = collision.FLAG_VISIBLE | collision.FLAG_COLLISION_ENABLED
    target = {
        "runtime_id": "b", "seq": 1, "x": 100.0, "y": 0.0,
        "radius_x": 30.0, "radius_y": 30.0, "circles": [[100.0, 0.0, 30.0]],
        "vx": 0.0, "vy": 0.0, "scale": 1.0, "flags": flags,
    }
    worker = _coordinator_with_members(target)
    worker._now = staticmethod(lambda: 2.0)
    worker.members["a"] = {
        "runtime_id": "a", "seq": 2, "x": 0.0, "y": 0.0,
        "radius_x": 30.0, "radius_y": 30.0, "circles": [[0.0, 0.0, 30.0]],
        "vx": -800.0, "vy": 0.0, "scale": 1.0, "flags": flags, "last_seen": 2.0,
    }
    # _captured_at is 1.6, diff = 0.4s > 0.3s TTL
    # x=0.0 vs x=100.0 with r=30 does not collide normally, but if pending were processed with bounce_vx=1500 (or overlap), it won't hit here anyway unless overlap, but at x=0 & x=50 overlap was 10px so regular collision hit.
    worker._pending_predicted["a"] = {
        "runtime_id": "a", "seq": 2, "x": 50.0, "y": 0.0,
        "radius_x": 30.0, "radius_y": 30.0, "circles": [[50.0, 0.0, 30.0]],
        "vx": -800.0, "vy": 0.0, "bounce_vx": 1500.0, "bounce_vy": 0.0,
        "scale": 1.0, "flags": flags | collision.FLAG_PREDICTED_BOUNCE,
        "_captured_at": 1.6,
    }

    received = []
    worker.impulse_ready.connect(received.append)
    worker._coordinator_tick()

    assert "a" not in worker._pending_predicted
    assert len(received) == 0


def test_remove_member_cleans_pending_predicted():
    flags = collision.FLAG_VISIBLE | collision.FLAG_COLLISION_ENABLED
    shooter = {
        "runtime_id": "a", "seq": 2, "x": 0.0, "y": 0.0,
        "radius_x": 30.0, "radius_y": 30.0, "circles": [[0.0, 0.0, 30.0]],
        "vx": -800.0, "vy": 0.0, "bounce_vx": 2000.0, "bounce_vy": 0.0,
        "scale": 1.0, "flags": flags | collision.FLAG_PREDICTED_BOUNCE,
    }
    worker = _coordinator_with_members(shooter)
    assert "a" in worker._pending_predicted
    worker._remove_member("a")
    assert "a" not in worker._pending_predicted
    assert "a" not in worker.members


def test_client_watermark_and_epoch_switch():
    worker = _CollisionWorker("unused-" + uuid.uuid4().hex, "client", "", {})
    worker.epoch = "epoch-a"
    received = []
    worker.impulse_ready.connect(received.append)
    socket = FakeSocket()
    worker._handle_message(socket, {"type": "impulse", "epoch": "epoch-a", "pair": "a|b", "tick": 4})
    worker._handle_message(socket, {"type": "impulse", "epoch": "epoch-a", "pair": "a|b", "tick": 4})
    assert len(received) == 1
    worker._handle_message(socket, {"type": "impulse", "epoch": "epoch-b", "pair": "a|b", "tick": 1})
    assert len(received) == 1


def test_coordinator_suppresses_repeated_position_only_contact():
    flags = collision.FLAG_VISIBLE | collision.FLAG_COLLISION_ENABLED
    a = {"runtime_id": "a", "seq": 1, "x": 0.0, "y": 0.0,
         "radius_x": 30.0, "radius_y": 30.0, "circles": [[0.0, 0.0, 30.0]],
         "vx": 0.0, "vy": 0.0, "flags": flags}
    b = {"runtime_id": "b", "seq": 1, "x": 50.0, "y": 0.0,
         "radius_x": 30.0, "radius_y": 30.0, "circles": [[50.0, 0.0, 30.0]],
         "vx": 0.0, "vy": 0.0, "flags": flags}
    worker = _coordinator_with_members(a, b)
    received = []
    worker.impulse_ready.connect(received.append)

    worker._coordinator_tick()
    first_count = len(received)
    assert first_count == 1
    for _ in range(3):
        worker._coordinator_tick()
    assert len(received) == first_count


def test_client_watchdog_stays_alive_while_snapshots_arrive(monkeypatch):
    worker = _CollisionWorker("unused-" + uuid.uuid4().hex, "client", "", {})
    worker.epoch = "epoch-a"
    worker.socket = object()
    worker._had_client_connection = True
    calls = []
    monkeypatch.setattr(worker, "_welcome_timed_out", lambda: calls.append(True))

    for _ in range(4):
        worker._last_control_message = worker._now() - 2.0
        worker._handle_message(worker.socket, {"type": "snapshot", "epoch": "epoch-a"})
        worker._check_client_silence()

    assert calls == []


@pytest.mark.parametrize("instance_id", ["", "slot-1"])
def test_runtime_id_has_session_randomness(instance_id):
    first = make_runtime_id(instance_id, 123)
    second = make_runtime_id(instance_id, 123)
    assert first != second
    assert f"pid123-" in first


def test_two_sessions_elect_one_coordinator_and_stop(tmp_path):
    QApplication.instance() or QApplication([])
    name = _server_name("elect")
    first = CollisionIpcSession(Config(tmp_path, instance_id="slot-a"), server_name=name)
    second = CollisionIpcSession(Config(tmp_path, instance_id="slot-b"), server_name=name)
    roles = []
    first.role_changed.connect(lambda is_coordinator, epoch: roles.append((1, is_coordinator, epoch)))
    second.role_changed.connect(lambda is_coordinator, epoch: roles.append((2, is_coordinator, epoch)))
    first.start()
    second.start()
    _pump(1.2)
    assert sum(is_coordinator for _, is_coordinator, _ in roles) == 1
    epochs = {epoch for _, _, epoch in roles if epoch}
    assert len(epochs) == 1
    first.stop()
    second.stop()
    assert not first._thread.isRunning()
    assert not second._thread.isRunning()


def test_subprocess_sessions_send_frames_and_reelect_after_parent_exits(tmp_path):
    name = _server_name("sub")
    script = r'''
import json, sys, time
from PySide6.QtCore import QEventLoop
from PySide6.QtWidgets import QApplication
from pet.collision_ipc import CollisionIpcSession
from pet.config import Config

app = QApplication([])
session = CollisionIpcSession(Config(sys.argv[3], instance_id=sys.argv[2]), server_name=sys.argv[1])
roles = []
session.role_changed.connect(lambda coordinator, epoch: roles.append((coordinator, epoch)))
session.start()
deadline = time.monotonic() + 6
while time.monotonic() < deadline and not roles:
    app.processEvents(QEventLoop.AllEvents, 10)
    time.sleep(.005)
for seq in range(1000):
    session.submit_state({"seq": seq, "ts": time.monotonic(), "x": float(seq), "y": 0,
                          "w": 10, "h": 10, "radius_x": 5, "radius_y": 5,
                          "vx": 0, "vy": 0, "flags": 3})
    app.processEvents(QEventLoop.AllEvents, 1)
print(json.dumps({"roles": roles, "frames": 1000}), flush=True)
if sys.argv[4] == "hold":
    deadline = time.monotonic() + 12
    while time.monotonic() < deadline:
        app.processEvents(QEventLoop.AllEvents, 10)
        time.sleep(.005)
    print(json.dumps({"roles": roles}), flush=True)
session.stop()
'''
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen", PYTHONPATH=os.getcwd())
    command = [sys.executable, "-c", script, name, "slot-a", str(tmp_path), "hold"]
    first = subprocess.Popen(command, stdout=subprocess.PIPE, text=True, env=env)
    second = subprocess.Popen(
        [sys.executable, "-c", script, name, "slot-b", str(tmp_path), "hold"],
        stdout=subprocess.PIPE, text=True, env=env,
    )
    first_info = json.loads(first.stdout.readline())
    second_info = json.loads(second.stdout.readline())
    assert first_info["frames"] == second_info["frames"] == 1000
    coordinator = first if any(role[0] for role in first_info["roles"]) else second
    survivor = second if coordinator is first else first
    coordinator.terminate()
    assert coordinator.wait(timeout=5) is not None
    survivor_info = json.loads(survivor.stdout.readline())
    assert any(role[0] for role in survivor_info["roles"])
    assert survivor.wait(timeout=5) == 0


def test_submit_leave_removes_member_immediately(tmp_path):
    """客户端 submit_leave 后，协调者成员表即时移除（不等 stale 超时）。"""
    QApplication.instance() or QApplication([])
    name = _server_name("leave")
    coordinator = CollisionIpcSession(Config(tmp_path, instance_id="slot-c"), server_name=name)
    client = CollisionIpcSession(Config(tmp_path, instance_id="slot-p"), server_name=name)
    coordinator.start()
    client.start()
    try:
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            _pump(0.1)
            if coordinator._worker.server is not None or client._worker.server is not None:
                break
        # 服务端已出现，再给客户端连接/握手留出时间
        _pump(0.5)
        if coordinator._worker.server is not None:
            coord_worker, client_session = coordinator._worker, client
        else:
            coord_worker, client_session = client._worker, coordinator
        assert coord_worker.server is not None

        # 客户端上报 state 成为成员（CI 上 QLocal 投递可能较慢，轮询等待）
        client_session.submit_state(_state(1, x=10.0))
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline and client_session.runtime_id not in coord_worker.members:
            _pump(0.05)
        assert client_session.runtime_id in coord_worker.members

        # 发送 leave：成员即时移除（3s stale 移除阈值远未到）
        client_session.submit_leave()
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline and client_session.runtime_id in coord_worker.members:
            _pump(0.05)
        assert client_session.runtime_id not in coord_worker.members
    finally:
        coordinator.stop()
        client.stop()


def test_update_policy_live_applies_to_worker(tmp_path):
    """运行中 update_policy：经 queued 接线更新到 worker 线程的 policy。"""
    QApplication.instance() or QApplication([])
    name = _server_name("policy")
    session = CollisionIpcSession(Config(tmp_path, instance_id="slot-p"), server_name=name)
    session.start()
    try:
        _pump(0.5)
        session.update_policy({"collision_enabled": True, "collision_restitution": 0.5,
                               "collision_friction": 0.15, "collision_mass_scale": 1.5,
                               "collision_impulse_cap": 6000.0})
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline and session._worker.policy.get("collision_restitution") != 0.5:
            _pump(0.05)
        assert session._worker.policy["collision_restitution"] == pytest.approx(0.5)
        assert session._worker.policy["collision_friction"] == pytest.approx(0.15)
        assert session._worker.policy["collision_mass_scale"] == pytest.approx(1.5)
        assert session._worker.policy["collision_impulse_cap"] == pytest.approx(6000.0)
    finally:
        session.stop()
def test_coordinator_own_predicted_bounce_via_submit_state_reaches_target():
    """回归：协调者自身（主桌宠）经进程内 submit_state 上报预测反弹时，
    也必须进入 _pending_predicted 捕获队列——否则主桌宠撞别的桌宠时
    目标永远收不到权威冲量（只有 socket 路径有捕获）。"""
    flags = collision.FLAG_VISIBLE | collision.FLAG_COLLISION_ENABLED
    target = {
        "runtime_id": "b", "seq": 1, "x": 50.0, "y": 0.0,
        "radius_x": 30.0, "radius_y": 30.0, "circles": [[50.0, 0.0, 30.0]],
        "vx": 0.0, "vy": 0.0, "scale": 1.0, "flags": flags,
    }
    worker = _coordinator_with_members(target)  # worker.runtime_id == "coordinator"
    shooter_state = {
        "seq": 2, "x": 0.0, "y": 0.0, "radius_x": 30.0, "radius_y": 30.0,
        "circles": [[0.0, 0.0, 30.0]], "vx": -800.0, "vy": 0.0,
        "bounce_vx": 2000.0, "bounce_vy": 0.0, "scale": 1.0,
        "flags": flags | collision.FLAG_PREDICTED_BOUNCE,
    }
    received = []
    worker.impulse_ready.connect(received.append)

    worker.submit_state(shooter_state)  # 协调者进程内直送路径
    assert "coordinator" in worker._pending_predicted

    worker._coordinator_tick()

    assert len(received) == 1
    # pair 排序后 a="b"（目标），冲量只给目标
    assert received[0]["dvx_a"] > 500.0
    assert received[0]["dvx_b"] == 0.0
    assert "coordinator" not in worker._pending_predicted
