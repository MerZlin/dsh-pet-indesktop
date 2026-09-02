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
from PySide6.QtCore import QEventLoop, QTimer
from PySide6.QtNetwork import QAbstractSocket, QLocalServer, QLocalSocket
from PySide6.QtWidgets import QApplication

from pet import collision
from pet import collision_codec
from pet import collision_ipc
from pet.collision_ipc import (
    CollisionIpcSession,
    _CollisionWorker,
    collision_server_name,
    make_runtime_id,
)
from pet.config import Config


class FakeSocket:
    def __init__(self):
        self.sent = []

    def write(self, data):
        self.sent.append(data)
        return len(data)

    def flush(self):
        return True


class IncomingSocket(FakeSocket):
    def __init__(self, data: bytes):
        super().__init__()
        self.data = data

    def readAll(self):
        data, self.data = self.data, b""
        return data


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


def test_collision_server_name_is_short_stable_and_variant_scoped(monkeypatch):
    monkeypatch.setattr(collision_ipc, "APP_DIR_NAME", "dsh-pet-standalone-webm-chat")
    chat_name = collision_server_name()

    assert chat_name == collision_server_name()
    assert len(chat_name.encode("utf-8")) <= 40

    monkeypatch.setattr(collision_ipc, "APP_DIR_NAME", "dsh-pet-standalone-webm")
    assert collision_server_name() != chat_name


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX Unix-socket path regression")
def test_collision_server_name_listens_with_long_variant_namespace(monkeypatch):
    monkeypatch.setattr(
        collision_ipc,
        "APP_DIR_NAME",
        f"dsh-pet-standalone-webm-chat-test-{uuid.uuid4().hex}",
    )
    name = collision_server_name()
    server = QLocalServer()
    client = QLocalSocket()
    QLocalServer.removeServer(name)
    try:
        assert server.listen(name), server.errorString()
        client.connectToServer(name)
        assert client.waitForConnected(1000), client.errorString()
    finally:
        client.abort()
        server.close()
        QLocalServer.removeServer(name)


def test_welcome_for_all_supported_slots_round_trips_default_protocol():
    worker = _CollisionWorker(_server_name("welcome"), "coordinator", "slot-0", {})
    worker.epoch = "0123456789abcdef01234567"
    worker.members = {
        f"slot-{index}-pid{1000 + index}-abcdefgh": {
            **_state(index, float(index * 32)),
            "runtime_id": f"slot-{index}-pid{1000 + index}-abcdefgh",
            "instance_id": f"slot-{index}",
            "character": "character-" + "x" * 244,
            "circles": [[float(index * 32 + offset), 64.0, 28.0]
                        for offset in (0, 28, 56, 84)],
            "last_seen": time.monotonic(),
        }
        for index in range(128)
    }

    welcome = worker._welcome()
    frame = collision_codec.encode_frame(welcome)

    assert len(frame) - collision_codec.HEADER_SIZE > 4096
    assert collision_codec.FrameStreamDecoder().feed(frame) == [welcome]


def test_large_inbound_state_cannot_poison_the_aggregate_snapshot():
    worker = _CollisionWorker(_server_name("large-state"), "coordinator", "slot-0", {})
    worker.server = object()
    worker.epoch = "epoch-a"
    message = {
        "type": "state",
        **_state(1),
        "padding": "x" * (collision_codec.FRAME_MAX_LENGTH - 8192),
        "character": "界" * 1000,
        "circles": [[float(index), 1.0, 2.0] for index in range(12)],
    }
    oversized_frame = collision_codec.encode_frame(message)
    assert len(oversized_frame) - collision_codec.HEADER_SIZE > collision_codec.STATE_FRAME_MAX_LENGTH
    valid_frame = collision_codec.encode_frame(
        {"type": "state", **_state(2, x=42.0)},
        max_frame_len=collision_codec.STATE_FRAME_MAX_LENGTH,
    )
    socket = IncomingSocket(oversized_frame + valid_frame)
    worker._socket_decoders[socket] = collision_codec.FrameStreamDecoder(
        max_frame_len=collision_codec.STATE_FRAME_MAX_LENGTH,
    )
    worker.peers[socket] = "remote"

    worker._read_socket(socket)

    member = worker.members["remote"]
    assert member["seq"] == 2
    assert member["x"] == 42.0
    assert "padding" not in member
    snapshot = {
        "type": "snapshot",
        "epoch": worker.epoch,
        "tick": worker.tick,
        "members": [worker._public_member(item) for item in worker.members.values()],
    }
    collision_codec.encode_frame(snapshot)


def test_state_ingestion_caps_members_and_preserves_snapshot_budget():
    worker = _CollisionWorker(_server_name("member-budget"), "coordinator", "slot-0", {})
    worker.server = object()
    worker.epoch = "epoch-a"

    for index in range(129):
        socket = FakeSocket()
        worker.peers[socket] = f"slot-{index}-pid{1000 + index}-abcdefgh"
        worker._handle_message(socket, {
            "type": "state",
            **_state(1, float(index)),
            "padding": "must-not-enter-snapshot",
            "character": "character-" + "x" * 500,
            "circles": [[float(index + offset), 64.0, 28.0]
                        for offset in range(8)],
        })

    assert len(worker.members) == 128
    assert all("padding" not in member for member in worker.members.values())
    assert all(len(member["character"].encode("utf-8")) <= 512
               for member in worker.members.values())
    assert all(len(member["circles"]) <= 3 for member in worker.members.values())
    assert (collision_ipc.MAX_COLLISION_MEMBERS
            * (collision_ipc.PUBLIC_MEMBER_MAX_LENGTH + 1)
            + collision_codec.STATE_FRAME_MAX_LENGTH < collision_codec.FRAME_MAX_LENGTH)
    frame = collision_codec.encode_frame(worker._welcome())
    assert len(frame) - collision_codec.HEADER_SIZE <= collision_codec.FRAME_MAX_LENGTH
    assert collision_codec.FrameStreamDecoder().feed(frame) == [worker._welcome()]


def test_schedule_election_reuses_one_timer():
    QApplication.instance() or QApplication([])
    worker = _CollisionWorker(_server_name("timer"), "client", "", {})

    for _ in range(20):
        worker._schedule_election()

    assert len(worker.findChildren(QTimer)) == 1
    assert worker._election_timer.isActive()


@pytest.mark.parametrize(
    ("listen_error", "probe_is_live", "should_recover"),
    [
        (QAbstractSocket.SocketError.AddressInUseError, False, True),
        (QAbstractSocket.SocketError.AddressInUseError, True, False),
        (QAbstractSocket.SocketError.HostNotFoundError, False, False),
    ],
)
def test_posix_listen_recovery_only_removes_address_in_use(
        monkeypatch, listen_error, probe_is_live, should_recover):
    class FakeLocalServer:
        removed = []

        def __init__(self, _parent=None):
            self.listen_calls = 0

        def listen(self, _name):
            self.listen_calls += 1
            return should_recover and self.listen_calls == 2

        def serverError(self):
            return listen_error

        def deleteLater(self):
            pass

        @classmethod
        def removeServer(cls, name):
            cls.removed.append(name)
            return True

    lock = object()
    released = []
    probes = []
    listeners = []
    client_attempts = []
    monkeypatch.setattr(collision_ipc, "QLocalServer", FakeLocalServer)
    monkeypatch.setattr(collision_ipc.sys, "platform", "darwin")
    monkeypatch.setattr(collision_ipc.slot_manager, "acquire_file_lock", lambda _path: lock)
    monkeypatch.setattr(
        collision_ipc.slot_manager,
        "release_file_lock",
        lambda held: released.append(held),
    )
    worker = _CollisionWorker(_server_name("listen"), "runtime", "", {}, lock_path="lock")
    monkeypatch.setattr(
        worker,
        "_probe_live_server",
        lambda: probes.append(True) or probe_is_live,
    )
    monkeypatch.setattr(worker, "_become_listener", listeners.append)
    monkeypatch.setattr(worker, "_connect_client", lambda: client_attempts.append(True))

    worker._try_election()

    assert bool(FakeLocalServer.removed) is should_recover
    assert bool(probes) is (listen_error == QAbstractSocket.SocketError.AddressInUseError)
    assert bool(listeners) is should_recover
    assert bool(client_attempts) is (not should_recover)
    assert released == ([] if should_recover else [lock])


def test_watchdog_timeout_never_removes_named_endpoint(monkeypatch):
    removed = []
    scheduled = []
    aborted = []

    class TimedOutSocket:
        def abort(self):
            aborted.append(True)

        def deleteLater(self):
            pass

    monkeypatch.setattr(
        collision_ipc.QLocalServer,
        "removeServer",
        lambda name: removed.append(name),
    )
    worker = _CollisionWorker(_server_name("watchdog"), "client", "", {})
    monkeypatch.setattr(worker, "_schedule_election", lambda: scheduled.append(True))

    for _ in range(2):
        worker.socket = TimedOutSocket()
        worker._had_client_connection = True
        worker._welcome_timed_out()

    assert aborted == [True, True]
    assert scheduled == [True, True]
    assert removed == []


def test_stop_removes_owned_endpoint_before_releasing_lock(monkeypatch):
    events = []

    class OwnedServer:
        def close(self):
            events.append("close")

        def deleteLater(self):
            pass

    worker = _CollisionWorker(_server_name("stop"), "coordinator", "", {})
    worker.server = OwnedServer()
    worker._coordinator_lock = object()
    monkeypatch.setattr(
        collision_ipc.QLocalServer,
        "removeServer",
        lambda _name: events.append("remove"),
    )
    monkeypatch.setattr(
        collision_ipc.slot_manager,
        "release_file_lock",
        lambda _lock: events.append("release"),
    )
    monkeypatch.setattr(collision_ipc.QTimer, "singleShot", lambda *_args: None)

    worker.stop()

    assert events == ["close", "remove", "release"]


def test_coordinator_submit_leave_broadcasts_remaining_members():
    worker = _CollisionWorker(_server_name("own-leave"), "coordinator", "", {
        "collision_enabled": True,
    })
    worker.server = object()
    worker.epoch = "epoch-a"
    worker._participating = True
    worker.latest_state = _state(1)
    peer_socket = FakeSocket()
    worker.peers[peer_socket] = "peer"
    worker.members = {
        "coordinator": {**_state(1), "runtime_id": "coordinator", "last_seen": 1.0},
        "peer": {**_state(1), "runtime_id": "peer", "last_seen": 1.0},
    }
    worker._now = staticmethod(lambda: 1.0)
    worker._last_snapshot_at = 1.0

    worker.submit_leave()
    worker._coordinator_tick()

    assert "coordinator" not in worker.members
    assert "peer" in worker.members
    assert len(peer_socket.sent) == 1
    snapshot = collision_codec.FrameStreamDecoder().feed(peer_socket.sent[0])[0]
    assert snapshot["type"] == "snapshot"
    assert [member["runtime_id"] for member in snapshot["members"]] == ["peer"]


@pytest.mark.parametrize("collision_enabled", [True, False])
def test_single_member_snapshot_heartbeat_does_not_require_solver(collision_enabled):
    worker = _CollisionWorker(_server_name("one-member"), "coordinator", "", {
        "collision_enabled": collision_enabled,
    })
    worker.server = object()
    worker.epoch = "epoch-a"
    peer_socket = FakeSocket()
    worker.peers[peer_socket] = "peer"
    worker.members = {
        "peer": {**_state(1), "runtime_id": "peer", "last_seen": 1.0},
    }
    times = iter((1.0, 1.6, 1.6))
    worker._now = staticmethod(lambda: next(times))

    worker._coordinator_tick()
    worker._coordinator_tick()

    assert len(peer_socket.sent) == 2
    assert worker.tick == 0
    snapshots = [
        collision_codec.FrameStreamDecoder().feed(frame)[0]
        for frame in peer_socket.sent
    ]
    if collision_enabled:
        assert all(len(snapshot["members"]) == 1 for snapshot in snapshots)
        assert len(worker._welcome()["members"]) == 1
    else:
        assert all(snapshot["members"] == [] for snapshot in snapshots)
        assert worker._welcome()["members"] == []


def test_authoritative_membership_excludes_solver_stale_members():
    worker = _CollisionWorker(_server_name("stale-member"), "coordinator", "", {
        "collision_enabled": True,
    })
    worker.server = object()
    worker.epoch = "epoch-a"
    peer_socket = FakeSocket()
    worker.peers[peer_socket] = "fresh"
    worker.members = {
        "fresh": {**_state(1), "runtime_id": "fresh", "last_seen": 10.0},
        # Solver 已不再使用，但尚未达到 heartbeat 的 3 秒清理阈值。
        "stale": {**_state(1), "runtime_id": "stale", "last_seen": 8.7},
    }
    worker._now = staticmethod(lambda: 10.0)
    worker._membership_dirty = True

    worker._coordinator_tick()

    assert "stale" in worker.members
    snapshot = collision_codec.FrameStreamDecoder().feed(peer_socket.sent[0])[0]
    assert [member["runtime_id"] for member in snapshot["members"]] == ["fresh"]
    assert [member["runtime_id"] for member in worker._welcome()["members"]] == ["fresh"]


def test_policy_toggle_forces_empty_then_full_membership_snapshot():
    worker = _CollisionWorker(_server_name("policy-snapshot"), "coordinator", "", {
        "collision_enabled": True,
    })
    worker.server = object()
    worker.epoch = "epoch-a"
    peer_socket = FakeSocket()
    worker.peers[peer_socket] = "peer"
    worker.members = {
        "peer": {**_state(1), "runtime_id": "peer", "last_seen": 1.0},
    }
    worker._now = staticmethod(lambda: 1.0)
    worker._last_snapshot_at = 1.0

    def seed_solver_history():
        worker._pending_predicted = {"peer": {"_captured_at": 1.0}}
        worker.previous_members = {"peer": {"seq": 0}}
        worker._swept_pair_versions = {"a|b": (1, 1)}
        worker._predicted_pair_ticks = {"a|b": 3}
        worker._position_only_pairs = {"a|b": ((1.0, 2.0), 3)}
        worker.overlap_history = {"a|b": 2}

    def assert_solver_history_cleared():
        assert worker._pending_predicted == {}
        assert worker.previous_members == {}
        assert worker._swept_pair_versions == {}
        assert worker._predicted_pair_ticks == {}
        assert worker._position_only_pairs == {}
        assert worker.overlap_history == {}

    seed_solver_history()
    worker.set_policy({"collision_enabled": False})
    assert_solver_history_cleared()
    worker._coordinator_tick()
    seed_solver_history()
    worker.set_policy({"collision_enabled": True})
    assert_solver_history_cleared()
    worker._coordinator_tick()

    snapshots = [
        collision_codec.FrameStreamDecoder().feed(frame)[0]
        for frame in peer_socket.sent
    ]
    assert [snapshot["members"] for snapshot in snapshots] == [[], [
        worker._public_member(worker.members["peer"])
    ]]


def test_same_epoch_welcome_refreshes_policy_and_membership_snapshot():
    worker = _CollisionWorker(_server_name("same-epoch"), "client", "", {})
    worker.epoch = "epoch-a"
    snapshots = []
    policies = []
    roles = []
    worker.snapshot_ready.connect(snapshots.append)
    worker.policy_changed.connect(policies.append)
    worker.role_changed.connect(lambda coordinator, epoch: roles.append((coordinator, epoch)))
    welcome = {
        "type": "welcome",
        "epoch": "epoch-a",
        "tick": 9,
        "policy": {"collision_enabled": False},
        "members": [],
    }

    worker._handle_message(FakeSocket(), welcome)

    assert snapshots == [welcome]
    assert policies == [welcome["policy"]]
    assert roles == []


def test_client_leave_suppresses_heartbeat_and_stale_failover_registration(monkeypatch):
    worker = _CollisionWorker(_server_name("leave-intent"), "client", "slot-1", {})
    client_socket = FakeSocket()
    worker.socket = client_socket
    worker.latest_state = _state(1)
    worker._participating = True

    worker.submit_leave()
    assert len(client_socket.sent) == 1
    worker._heartbeat()
    assert len(client_socket.sent) == 1

    worker.socket = None
    worker.server = object()
    monkeypatch.setattr(worker, "_start_coordinator_timers", lambda: None)
    worker._announce_coordinator()
    assert worker.runtime_id not in worker.members

    worker.submit_state(_state(2, x=42.0))
    assert worker.members[worker.runtime_id]["x"] == 42.0


def test_accept_connection_reads_bytes_that_arrived_before_signal_hooks():
    class ConnectableSignal:
        def connect(self, _callback):
            pass

    class PendingSocket:
        readyRead = ConnectableSignal()
        disconnected = ConnectableSignal()
        errorOccurred = ConnectableSignal()

        @staticmethod
        def bytesAvailable():
            return 1

    class PendingServer:
        def __init__(self, socket):
            self.socket = socket
            self.pending = True

        def hasPendingConnections(self):
            return self.pending

        def nextPendingConnection(self):
            self.pending = False
            return self.socket

    socket = PendingSocket()
    worker = _CollisionWorker(_server_name("early-bytes"), "coordinator", "", {})
    worker.server = PendingServer(socket)
    reads = []
    worker._read_socket = reads.append

    worker._accept_connection()

    assert worker.peers[socket] == ""
    assert worker._socket_decoders[socket].max_frame_len == collision_codec.STATE_FRAME_MAX_LENGTH
    assert reads == [socket]


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
    worker.overlap_history = {"coordinator|remote": 2, "other|pair": 1}
    worker._position_only_pairs = {
        "coordinator|remote": ((1.0, 2.0), 3),
        "other|pair": ((4.0, 5.0), 6),
    }
    worker.peers.pop(socket)
    worker._remove_member("remote")
    assert "remote" not in worker.members
    assert worker.overlap_history == {"other|pair": 1}
    assert worker._position_only_pairs == {"other|pair": ((4.0, 5.0), 6)}


def test_resign_clears_all_epoch_scoped_collision_history(monkeypatch):
    class Server:
        def close(self):
            pass

        def deleteLater(self):
            pass

    worker = _CollisionWorker(_server_name("resign-history"), "coordinator", "", {})
    worker.server = Server()
    worker.overlap_history = {"a|b": 2}
    worker._position_only_pairs = {"a|b": ((1.0, 2.0), 3)}
    monkeypatch.setattr(collision_ipc.slot_manager, "release_file_lock", lambda _lock: None)
    monkeypatch.setattr(worker, "_connect_client", lambda: None)

    worker._resign_to("winner")

    assert worker.overlap_history == {}
    assert worker._position_only_pairs == {}


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


def test_snapshot_broadcast_encodes_once_for_all_peers(monkeypatch):
    flags = collision.FLAG_VISIBLE | collision.FLAG_COLLISION_ENABLED
    first = {
        "runtime_id": "a", "seq": 1, "x": 0.0, "y": 0.0,
        "radius_x": 30.0, "radius_y": 30.0, "circles": [[0.0, 0.0, 30.0]],
        "vx": 100.0, "vy": 0.0, "flags": flags,
    }
    second = {
        "runtime_id": "b", "seq": 1, "x": 500.0, "y": 0.0,
        "radius_x": 30.0, "radius_y": 30.0, "circles": [[500.0, 0.0, 30.0]],
        "vx": 0.0, "vy": 0.0, "flags": flags,
    }
    worker = _coordinator_with_members(first, second)
    sockets = [FakeSocket() for _ in range(3)]
    worker.peers = {socket: f"peer-{index}" for index, socket in enumerate(sockets)}
    encoded = []
    original_encode = collision_codec.encode_frame
    monkeypatch.setattr(
        collision_codec,
        "encode_frame",
        lambda message: encoded.append(message) or original_encode(message),
    )

    worker._coordinator_tick()

    assert len(encoded) == 1
    assert all(len(socket.sent) == 1 for socket in sockets)


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
from pet import collision
from pet import collision_codec
from pet.collision_ipc import CollisionIpcSession
from pet.config import Config

app = QApplication([])
session = CollisionIpcSession(Config(sys.argv[3], instance_id=sys.argv[2]), server_name=sys.argv[1])
roles = []
snapshots = []
session.role_changed.connect(lambda coordinator, epoch: roles.append((coordinator, epoch)))
session.snapshot_ready.connect(snapshots.append)
session.start()
deadline = time.monotonic() + 6
while time.monotonic() < deadline and not roles:
    app.processEvents(QEventLoop.AllEvents, 10)
    time.sleep(.005)
flags = collision.FLAG_VISIBLE | collision.FLAG_COLLISION_ENABLED
for seq in range(1000):
    session.submit_state({"seq": seq, "ts": time.monotonic(), "x": float(seq), "y": 0,
                          "w": 10, "h": 10, "radius_x": 5, "radius_y": 5,
                          "vx": 0, "vy": 0, "flags": flags})
    app.processEvents(QEventLoop.AllEvents, 1)
deadline = time.monotonic() + 6
while time.monotonic() < deadline and not any(len(item.get("members") or ()) >= 2 for item in snapshots):
    app.processEvents(QEventLoop.AllEvents, 10)
    time.sleep(.005)
initial_roles = list(roles)
initial_epochs = {epoch for _, epoch in initial_roles if epoch}
print(json.dumps({
    "roles": initial_roles,
    "frames_sent": 1000,
    "snapshot_count": len(snapshots),
    "max_members": max((len(item.get("members") or ()) for item in snapshots), default=0),
}), flush=True)
if sys.argv[4] == "hold":
    deadline = time.monotonic() + 8
    while time.monotonic() < deadline and not any(
            coordinator and epoch not in initial_epochs
            for coordinator, epoch in roles[len(initial_roles):]):
        app.processEvents(QEventLoop.AllEvents, 10)
        time.sleep(.005)
    print(json.dumps({"new_roles": roles[len(initial_roles):]}), flush=True)
session.stop()
'''
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen", PYTHONPATH=os.getcwd())
    command = [sys.executable, "-c", script, name, "slot-a", str(tmp_path), "hold"]
    first = subprocess.Popen(command, stdout=subprocess.PIPE, text=True, env=env)
    second = subprocess.Popen(
        [sys.executable, "-c", script, name, "slot-b", str(tmp_path), "hold"],
        stdout=subprocess.PIPE, text=True, env=env,
    )
    try:
        first_info = json.loads(first.stdout.readline())
        second_info = json.loads(second.stdout.readline())
        assert first_info["frames_sent"] == second_info["frames_sent"] == 1000
        assert first_info["snapshot_count"] > 0
        assert second_info["snapshot_count"] > 0
        assert first_info["max_members"] >= 2
        assert second_info["max_members"] >= 2

        coordinator = first if any(role[0] for role in first_info["roles"]) else second
        survivor = second if coordinator is first else first
        initial_epochs = {
            epoch
            for info in (first_info, second_info)
            for _, epoch in info["roles"]
            if epoch
        }
        assert len(initial_epochs) == 1

        coordinator.terminate()
        assert coordinator.wait(timeout=5) is not None
        survivor_info = json.loads(survivor.stdout.readline())
        new_coordinator_epochs = {
            epoch for is_coordinator, epoch in survivor_info["new_roles"]
            if is_coordinator and epoch
        }
        assert len(new_coordinator_epochs) == 1
        assert new_coordinator_epochs.isdisjoint(initial_epochs)
        assert survivor.wait(timeout=5) == 0
    finally:
        for process in (first, second):
            if process.poll() is None:
                process.terminate()
                process.wait(timeout=5)


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
        snapshots = []
        client_session.snapshot_ready.connect(snapshots.append)
        stable_epoch = client_session._worker.epoch
        client_session.submit_leave()
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline and client_session.runtime_id in coord_worker.members:
            _pump(0.05)
        assert client_session.runtime_id not in coord_worker.members

        # 即使成员表已空，snapshot 仍是连接 watchdog 的控制心跳；客户端
        # 必须收到空权威表，并在超过 1.5s 后继续连接到同一 epoch。
        deadline = time.monotonic() + 1.0
        while time.monotonic() < deadline and not any(
                not (item.get("members") or ()) for item in snapshots):
            _pump(0.05)
        assert any(not (item.get("members") or ()) for item in snapshots)
        _pump(1.7)
        assert client_session._worker.socket is not None
        assert client_session._worker.epoch == stable_epoch
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
        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline and session._worker.server is None:
            _pump(0.05)
        assert session._worker.server is not None
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


def test_impulse_malformed_tick_dropped_without_raising():
    """审查 P1-02/P7 回归：畸形 tick 的 impulse 帧逐条丢弃，不抛异常、
    不中断后续合法帧。"""
    worker = _CollisionWorker(_server_name("bad-tick"), "client", "slot-1", {})
    worker.epoch = "epoch-a"
    impulses = []
    worker.impulse_ready.connect(impulses.append)
    for bad_tick in ("bad", None, [1], {"x": 1}):
        worker._handle_message(FakeSocket(), {
            "type": "impulse", "epoch": "epoch-a", "pair": "a|b", "tick": bad_tick,
        })
    assert impulses == []
    worker._handle_message(FakeSocket(), {
        "type": "impulse", "epoch": "epoch-a", "pair": "a|b", "tick": 3,
    })
    assert len(impulses) == 1


def test_set_policy_partial_dict_merges_with_defaults():
    """审查 P2-03 回归：update_policy 公开边界收到部分 dict 时缺键由
    默认值/旧值补齐，coordinator tick 不再可能 KeyError。"""
    worker = _CollisionWorker(_server_name("policy-merge"), "coordinator", "", {})
    worker.set_policy({"collision_enabled": False})
    for key in ("collision_restitution", "collision_friction",
                "collision_mass_scale", "collision_impulse_cap"):
        assert key in worker.policy
    assert worker.policy["collision_enabled"] is False
    # 再次部分更新不清掉既有键
    worker.set_policy({"collision_restitution": 0.5})
    assert worker.policy["collision_restitution"] == 0.5
    assert worker.policy["collision_enabled"] is False


def test_welcome_triggers_immediate_state_resend():
    """审查 DS-M4 回归：收到 welcome（含同 epoch 重连）立即补发当前状态，
    不等下一心跳周期。"""
    worker = _CollisionWorker(_server_name("welcome-resend"), "client", "slot-1", {})
    client_socket = FakeSocket()
    worker.socket = client_socket
    worker.latest_state = _state(5)
    worker._participating = True
    worker.epoch = "epoch-1"  # 同 epoch 重连场景
    worker._handle_message(client_socket, {
        "type": "welcome", "epoch": "epoch-1", "tick": 0, "policy": {}, "members": [],
    })
    frames = [m for frame in client_socket.sent
              for m in collision_codec.FrameStreamDecoder().feed(frame)]
    assert any(m.get("type") == "state" for m in frames)
