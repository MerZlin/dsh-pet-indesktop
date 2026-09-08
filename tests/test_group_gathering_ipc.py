# -*- coding: utf-8 -*-
"""围圈聚集 IPC：编解码、协调者单会话中继、ready/action/done/cancel。"""
from __future__ import annotations

import time
import uuid

from PySide6.QtWidgets import QApplication

from pet import collision
from pet import collision_codec
from pet import collision_ipc


class FakeSocket:
    def __init__(self):
        self.sent = []

    def write(self, data):
        self.sent.append(data)
        return len(data)

    def flush(self):
        return True


def _worker():
    app = QApplication.instance() or QApplication([])
    name = f"gg-ipc-{uuid.uuid4().hex[:8]}"
    worker = collision_ipc._CollisionWorker(name, "coordinator", "slot-0", {})
    worker.server = object()
    worker.epoch = "epoch"
    return worker


def _peers(worker, count=3):
    sockets = []
    for index in range(count):
        sock = FakeSocket()
        worker.peers[sock] = f"slot-{index}"
        sockets.append(sock)
    return sockets


def _collect(worker):
    messages = []

    def on_message(message):
        messages.append(message)

    worker.group_message_ready.connect(on_message)
    return messages


def _begin(token, targets=("slot-0", "slot-1")):
    return {
        "type": "group", "kind": "begin", "token": token, "leader": "slot-0",
        "preset_id": "breakfast", "clip": "吃早餐",
        "targets": [
            {"runtime_id": rid, "x": float(index), "y": 0.0, "facing": "right"}
            for index, rid in enumerate(targets)
        ],
        "screen": {"left": 0.0, "top": 0.0, "right": 1920.0, "bottom": 1080.0},
    }


def _decode_sent(sock):
    return collision_codec.FrameStreamDecoder().feed(b"".join(sock.sent))


def test_group_message_codec_roundtrip():
    message = _begin("token-1")
    frame = collision_codec.encode_frame(message)
    decoded = collision_codec.FrameStreamDecoder().feed(frame)
    assert decoded == [message]


def test_normalize_state_preserves_group_enabled_flag():
    state = {
        "seq": 1, "ts": time.monotonic(), "x": 1.0, "y": 2.0, "w": 100.0,
        "h": 80.0, "radius_x": 30.0, "radius_y": 30.0,
        "vx": 0.0, "vy": 0.0,
        "flags": collision.FLAG_VISIBLE | collision.FLAG_COLLISION_ENABLED | collision.FLAG_GROUP_ENABLED,
        "character": "shenshen", "scale": 1.0,
    }
    normalized = collision_ipc._normalize_state(state)
    assert normalized["flags"] & collision.FLAG_GROUP_ENABLED


def test_worker_begin_broadcasts_to_peers_and_local_once():
    worker = _worker()
    peers = _peers(worker)
    local = _collect(worker)
    message = _begin("token-1")

    worker._handle_group_message(message, origin_id="slot-0")

    assert worker._active_group is not None
    assert local == [message]
    # 每个 peer 都收到同一条 begin
    for sock in peers:
        assert _decode_sent(sock) == [message]


def test_worker_ignores_second_begin_while_active():
    worker = _worker()
    _peers(worker)
    worker._handle_group_message(_begin("token-1"), origin_id="slot-0")
    second = _begin("token-2", targets=("slot-0", "slot-1", "slot-2"))

    worker._handle_group_message(second, origin_id="slot-2")

    assert worker._active_group["token"] == "token-1"
    assert len(worker.peers) == 3


def test_worker_emits_action_after_all_ready():
    worker = _worker()
    peers = _peers(worker)
    local = _collect(worker)
    begin = _begin("token-1", targets=("slot-0", "slot-1"))
    worker._handle_group_message(begin, origin_id="slot-0")
    local.clear()

    worker._handle_group_message(
        {"type": "group", "kind": "ready", "token": "token-1"}, origin_id="slot-0"
    )
    # 只有一只 ready 还不广播 action
    assert local == []
    worker._handle_group_message(
        {"type": "group", "kind": "ready", "token": "token-1"}, origin_id="slot-1"
    )

    assert len(local) == 1
    assert local[0]["kind"] == "action"
    assert local[0]["token"] == "token-1"
    assert local[0]["clip"] == "吃早餐"
    for sock in peers:
        decoded = _decode_sent(sock)
        assert any(item.get("kind") == "action" for item in decoded)


def test_worker_cancel_clears_and_broadcasts():
    worker = _worker()
    peers = _peers(worker)
    local = _collect(worker)
    worker._handle_group_message(_begin("token-1"), origin_id="slot-0")
    local.clear()

    cancel = {"type": "group", "kind": "cancel", "token": "token-1", "reason": "interrupted"}
    worker._handle_group_message(cancel, origin_id="slot-1")

    assert worker._active_group is None
    assert local == [cancel]
    for sock in peers:
        decoded = _decode_sent(sock)
        assert decoded[-1] == cancel


def test_worker_done_all_clears_without_broadcast():
    worker = _worker()
    _peers(worker)
    local = _collect(worker)
    worker._handle_group_message(_begin("token-1", targets=("slot-0", "slot-1")), origin_id="slot-0")
    local.clear()

    worker._handle_group_message(
        {"type": "group", "kind": "done", "token": "token-1"}, origin_id="slot-0"
    )
    assert worker._active_group is not None
    worker._handle_group_message(
        {"type": "group", "kind": "done", "token": "token-1"}, origin_id="slot-1"
    )
    assert worker._active_group is None
    assert local == []
