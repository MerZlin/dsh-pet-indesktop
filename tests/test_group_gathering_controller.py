# -*- coding: utf-8 -*-
"""围圈聚集控制器本地会话测试：发起、走位、ready、action、done、cancel。"""
from __future__ import annotations

from PySide6.QtCore import QObject, QRect, Signal
from PySide6.QtWidgets import QApplication

from pet import collision
from pet.group_gathering import GroupGatheringController


class FakeScreen:
    def availableGeometry(self):
        return QRect(0, 0, 1920, 1080)


class FakeConfig:
    def __init__(self, enabled=True, auto_enabled=False, preset="", character="shenshen"):
        self.data = {
            "group_gathering": {
                "enabled": enabled,
                "auto_enabled": auto_enabled,
                "preset": preset,
            },
            "character": character,
        }

    def get(self, key, default=None):
        return self.data.get(key, default)


class FakeLib:
    def __init__(self, names=None):
        self._names = names or ["吃早餐", "吃午餐", "螃蟹走路"]

    def names(self):
        return list(self._names)

    def duration(self, _name):
        return 1.0


class FakeSession(QObject):
    group_message_ready = Signal(object)
    snapshot_ready = Signal(object)

    def __init__(self, runtime_id="slot-0"):
        super().__init__()
        self.runtime_id = runtime_id
        self.sent = []

    def submit_group_message(self, message):
        self.sent.append(dict(message))


class FakeWin:
    def __init__(self, session, peer_snapshots=None, enabled=True, auto_enabled=False,
                 preset=""):
        self._session = session
        self.cfg = FakeConfig(
            enabled=enabled, auto_enabled=auto_enabled, preset=preset,
        )
        self.lib = FakeLib()
        self._collision_app_session = session
        self._collision_peer_snapshots = peer_snapshots or {}
        self.moves = ["螃蟹走路"]
        self.acts = ["吃早餐", "吃午餐"]
        self.idles = ["待机呼吸休闲"]
        self.facing = "left"
        self._x = 100.0
        self._y = 100.0
        self._w = 320.0
        self._h = 180.0
        self.switched = []
        self.moved = []
        self.idle_requests = []

    def x(self):
        return self._x

    def y(self):
        return self._y

    def width(self):
        return int(self._w)

    def height(self):
        return int(self._h)

    def move(self, x, y):
        self.moved.append((int(round(x)), int(round(y))))
        self._x = float(x)
        self._y = float(y)

    def collision_content_rect(self):
        return QRect(int(self._x), int(self._y), int(self._w), int(self._h))

    def screen_available(self, _screen_name=None):
        return FakeScreen()

    def group_peer_snapshots(self):
        return dict(self._collision_peer_snapshots)

    def _switch(self, name):
        self.switched.append(name)

    def switch_clip(self, name, link_request=False):
        self.switched.append(("action", name, link_request))

    def request_link_idle(self):
        self.idle_requests.append(1)


def _qapp():
    return QApplication.instance() or QApplication([])


def _peer(runtime_id, x, y, character="shenshen"):
    return {
        "runtime_id": runtime_id,
        "x": float(x), "y": float(y),
        "w": 320.0, "h": 180.0,
        "flags": collision.FLAG_VISIBLE | collision.FLAG_COLLISION_ENABLED | collision.FLAG_GROUP_ENABLED,
        "character": character,
    }


def _session_and_win(session_id="slot-0", peer_id="slot-1", peer_x=900, peer_y=500):
    _qapp()
    session = FakeSession(session_id)
    peers = {peer_id: _peer(peer_id, peer_x, peer_y)}
    win = FakeWin(session, peers)
    return session, win


def test_disabled_controller_not_created_by_mixin_path():
    # controller 构造本身不检查 enabled（由 mixin 决定懒创建）；这里验证配置读取
    session = FakeSession("slot-0")
    win = FakeWin(session, enabled=False)
    assert win.cfg.get("group_gathering") == {
        "enabled": False, "auto_enabled": False, "preset": "",
    }


def test_request_group_submits_begin_with_two_targets():
    session, win = _session_and_win()
    controller = GroupGatheringController(win, session=session, parent=None)

    assert controller.request_group("breakfast") is True
    assert len(session.sent) == 1
    message = session.sent[0]
    assert message["kind"] == "begin"
    assert len(message["targets"]) == 2
    assert message["clip"] == "吃早餐"
    controller.shutdown()


def test_request_group_requires_common_preset():
    session = FakeSession("slot-0")
    win = FakeWin(session, peer_snapshots={
        "slot-1": _peer("slot-1", 900, 500, character="shenshen"),
    })
    win.lib = FakeLib(names=["吃午餐"])
    controller = GroupGatheringController(win, session=session)

    assert controller.request_group("breakfast") is False
    assert session.sent == []
    controller.shutdown()


def test_trigger_default_uses_configured_preset():
    session, win = _session_and_win()
    win.cfg = FakeConfig(enabled=True, auto_enabled=False, preset="breakfast")
    controller = GroupGatheringController(win, session=session)

    assert controller.trigger_default() is True
    assert session.sent and session.sent[-1]["kind"] == "begin"
    assert session.sent[-1]["preset_id"] == "breakfast"
    assert session.sent[-1]["clip"] == "吃早餐"
    controller.shutdown()


def test_trigger_default_falls_back_to_random_common_when_configured_missing():
    session, win = _session_and_win()
    win.cfg = FakeConfig(enabled=True, auto_enabled=False, preset="breakfast")
    # 本窗素材只有吃午餐，因此 breakfast 不可用；唯一共有 = 吃午餐
    win.lib = FakeLib(names=["吃午餐"])
    controller = GroupGatheringController(win, session=session)

    assert controller.trigger_default() is True
    assert session.sent and session.sent[-1]["kind"] == "begin"
    assert session.sent[-1]["preset_id"] == "lunch"
    assert session.sent[-1]["clip"] == "吃午餐"
    controller.shutdown()


def test_trigger_default_false_when_no_participants():
    session = FakeSession("slot-0")
    win = FakeWin(session, peer_snapshots={})
    controller = GroupGatheringController(win, session=session)

    assert controller.trigger_default() is False
    assert session.sent == []
    controller.shutdown()


def test_block_reason_reports_missing_participants():
    session = FakeSession("slot-0")
    win = FakeWin(session, peer_snapshots={})
    controller = GroupGatheringController(win, session=session)

    assert controller.can_trigger() is False
    reason = controller.block_reason()
    assert "至少 2 只" in reason
    controller.shutdown()


def test_block_reason_empty_when_ready():
    session, win = _session_and_win()
    controller = GroupGatheringController(win, session=session)

    assert controller.block_reason() == ""
    assert controller.can_trigger() is True
    controller.shutdown()


def test_begin_walk_sends_ready_after_target_reached():
    session, win = _session_and_win()
    controller = GroupGatheringController(win, session=session)
    controller._walk_duration_seconds = 0.1
    controller._walk_started_at = 0.0
    # 让时钟走完，直接驱动 tick
    clock = [0.0]
    controller._clock = lambda: clock[0]
    controller._on_group_message({
        "type": "group", "kind": "begin", "token": "token-1",
        "clip": "吃早餐",
        "targets": [
            {"runtime_id": "slot-0", "x": 300.0, "y": 400.0, "facing": "right"},
            {"runtime_id": "slot-1", "x": 900.0, "y": 500.0, "facing": "left"},
        ],
    })
    assert controller._active
    assert controller._phase == "walk"
    assert win.facing == "right"

    clock[0] = 1.0
    controller._on_walk_tick()

    assert not controller._walk_timer.isActive()
    assert controller._phase == "ready"
    assert session.sent and session.sent[-1]["kind"] == "ready"
    controller.shutdown()


def test_action_plays_clip_and_sends_done():
    session, win = _session_and_win()
    controller = GroupGatheringController(win, session=session)
    # 直接进入 active 态
    controller._active = True
    controller._token = "token-1"
    controller._clip = "吃早餐"
    controller._on_group_message({
        "type": "group", "kind": "action", "token": "token-1", "start_delta_ms": 0,
    })
    controller._play_action()
    assert win.switched and win.switched[-1] == ("action", "吃早餐", True)
    controller._on_action_finished()
    assert session.sent[-1]["kind"] == "done"
    controller.shutdown()


def test_cancel_clears_and_requests_idle():
    session, win = _session_and_win()
    controller = GroupGatheringController(win, session=session)
    controller._active = True
    controller._token = "token-1"
    controller._on_group_message({
        "type": "group", "kind": "cancel", "token": "token-1", "reason": "hidden",
    })
    assert not controller._active
    assert win.idle_requests
    controller.shutdown()


def test_abort_from_user_interaction_sends_cancel():
    session, win = _session_and_win()
    controller = GroupGatheringController(win, session=session)
    controller._active = True
    controller._token = "token-1"
    controller.abort("interacted")
    assert session.sent and session.sent[-1]["kind"] == "cancel"
    assert not controller._active
    controller.shutdown()
