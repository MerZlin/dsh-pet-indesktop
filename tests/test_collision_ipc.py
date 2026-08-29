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


@pytest.mark.parametrize("instance_id", ["", "slot-1"])
def test_runtime_id_has_session_randomness(instance_id):
    first = make_runtime_id(instance_id, 123)
    second = make_runtime_id(instance_id, 123)
    assert first != second
    assert f"pid123-" in first


def test_two_sessions_elect_one_coordinator_and_stop(tmp_path):
    QApplication.instance() or QApplication([])
    name = "dsh-test-collision-" + uuid.uuid4().hex
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
    name = "dsh-test-collision-subprocess-" + uuid.uuid4().hex
    script = r'''
import json, sys, time
from PySide6.QtCore import QCoreApplication, QEventLoop
from pet.collision_ipc import CollisionIpcSession
from pet.config import Config

app = QCoreApplication([])
session = CollisionIpcSession(Config(sys.argv[3], instance_id=sys.argv[2]), server_name=sys.argv[1])
roles = []
session.role_changed.connect(lambda coordinator, epoch: roles.append((coordinator, epoch)))
session.start()
deadline = time.monotonic() + 2
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
    deadline = time.monotonic() + 4
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
