# -*- coding: utf-8 -*-
"""DSH 统一状态跟踪（dsh_state）单元测试。

通过向临时桥目录写 dsh.jsonl + 模拟 harness_launcher.is_running，直接驱动
DshStateTracker 的事件/在线轮询，验证 edge-trigger 去重、离线恢复与审批锁存。
"""
from __future__ import annotations

import json

from PySide6.QtWidgets import QApplication

from pet import harness_launcher
from pet.dsh_state import DshState, DshStateTracker


def _qapp():
    return QApplication.instance() or QApplication([])


def _make_tracker(tmp_path, monkeypatch, online=False):
    """构造一个指向临时桥目录的跟踪器，并把在线探测钉死到 online。"""
    _qapp()
    base = tmp_path / "base"
    config_dir = base / "cfg"
    config_dir.mkdir(parents=True)
    bridge_dir = base / "dsh-pet-bridge"
    bridge_dir.mkdir(parents=True)
    monkeypatch.setattr(harness_launcher, "is_running", lambda port: online)
    tracker = DshStateTracker(config_dir, parent=None, scan_interval=0.0)
    # 关闭 ByteOffsetTailer 的 backfill 防护，让测试能立即读到已写入内容
    tracker._tailer._initial_backfill_done = True
    return tracker, bridge_dir


def _write(bridge_dir, *records):
    with (bridge_dir / "dsh.jsonl").open("a", encoding="utf-8") as f:
        for rec in records:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def _records_for(*events):
    """把事件名序列转为 AgentStatus(working/idle) 或原始事件记录。"""
    out = []
    for ev in events:
        if ev in ("idle", "working", "thinking", "waiting_approval", "waiting_question",
                  "success", "error"):
            out.append({"event": "AgentStatus", "state": ev})
        else:
            out.append({"event": ev})
    return out


def test_offline_when_dsh_down(tmp_path, monkeypatch, caplog):
    tracker, _ = _make_tracker(tmp_path, monkeypatch, online=False)
    with caplog.at_level("INFO"):
        tracker._poll_online()
    assert tracker.current_state is DshState.OFFLINE
    assert any("[DSH STATE] offline" in r.getMessage() for r in caplog.records)


def test_online_goes_idle(tmp_path, monkeypatch, caplog):
    tracker, _ = _make_tracker(tmp_path, monkeypatch, online=True)
    with caplog.at_level("INFO"):
        tracker._poll_online()
    assert tracker.current_state is DshState.IDLE
    # DSH 一开始就在线：首个状态直接是 idle（from 为空，不是 offline -> idle）
    assert any("[DSH STATE] idle" in r.getMessage() for r in caplog.records)


def test_offline_then_online_idle(tmp_path, monkeypatch, caplog):
    """先 offline，DSH 上线后切 idle：日志体现 offline -> idle。"""
    tracker, _ = _make_tracker(tmp_path, monkeypatch, online=False)
    tracker._poll_online()
    assert tracker.current_state is DshState.OFFLINE
    monkeypatch.setattr(harness_launcher, "is_running", lambda port: True)
    with caplog.at_level("INFO"):
        tracker._poll_online()
    assert tracker.current_state is DshState.IDLE
    assert any("[DSH STATE] offline -> idle" in r.getMessage() for r in caplog.records)


def test_edge_trigger_dedup(tmp_path, monkeypatch, caplog):
    """同状态重复事件不重复 emit。"""
    tracker, bridge_dir = _make_tracker(tmp_path, monkeypatch, online=True)
    tracker._poll_online()  # -> idle
    tracker._poll_events()
    emitted = []
    tracker.state_changed.connect(lambda f, t: emitted.append((f, t)))

    _write(bridge_dir, *(_records_for("user/message")))   # thinking
    _write(bridge_dir, *(_records_for("user/message")))   # thinking 重复
    tracker._poll_events()
    assert tracker.current_state is DshState.THINKING
    # 只有一次 thinking 转换
    assert [t for _f, t in emitted].count("thinking") == 1


def test_full_pipeline(tmp_path, monkeypatch):
    """完整生命周期：thinking -> working -> waiting_approval -> working -> success -> idle。"""
    tracker, bridge_dir = _make_tracker(tmp_path, monkeypatch, online=True)
    tracker._poll_online()  # idle

    states = []
    tracker.state_changed.connect(lambda f, t: states.append(t))

    _write(bridge_dir, *(_records_for("user/message", "tool/call", "approval/asked",
                                       "approval/decided", "turn/end", "idle")))
    tracker._poll_events()

    assert states == [
        "thinking", "working", "waiting_approval", "working", "success", "idle",
    ]
    assert tracker.current_state is DshState.IDLE


def test_approval_latch_ignores_working(tmp_path, monkeypatch):
    """审批锁存期间 working 事件被忽略，直到 approval/decided。"""
    tracker, bridge_dir = _make_tracker(tmp_path, monkeypatch, online=True)
    tracker._poll_online()  # idle

    # waiting_approval 后，即便又来 working（agent 仍在跑），也不被顶掉
    _write(bridge_dir, *(_records_for("approval/asked", "tool/call", "working")))
    tracker._poll_events()
    assert tracker.current_state is DshState.WAITING_APPROVAL

    _write(bridge_dir, *(_records_for("approval/decided")))
    tracker._poll_events()
    assert tracker.current_state is DshState.WORKING


def test_offline_force_overrides_work(tmp_path, monkeypatch):
    """DSH 被强制关闭时切 offline，不崩。"""
    tracker, bridge_dir = _make_tracker(tmp_path, monkeypatch, online=True)
    tracker._poll_online()
    _write(bridge_dir, *(_records_for("working")))
    tracker._poll_events()
    assert tracker.current_state is DshState.WORKING

    # 模拟 DSH 下线
    monkeypatch.setattr(harness_launcher, "is_running", lambda port: False)
    tracker._poll_online()
    assert tracker.current_state is DshState.OFFLINE


def test_question_latch_ignores_working(tmp_path, monkeypatch):
    """问题锁存期间 working 事件被忽略，直到 question/resolved。"""
    tracker, bridge_dir = _make_tracker(tmp_path, monkeypatch, online=True)
    tracker._poll_online()  # idle

    # question/requested 后，即便又来 working（agent 仍在等回答），也不被顶掉
    _write(bridge_dir, {"event": "question/requested", "questions": [
        {"id": "q1", "question": "要执行哪个方案？",
         "options": [{"label": "方案 A"}, {"label": "方案 B"}]}]})
    _write(bridge_dir, *(_records_for("tool/call", "working")))
    tracker._poll_events()
    assert tracker.current_state is DshState.WAITING_QUESTION

    _write(bridge_dir, {"event": "question/resolved"})
    tracker._poll_events()
    assert tracker.current_state is DshState.WORKING


def test_question_pipeline(tmp_path, monkeypatch):
    """完整生命周期：thinking -> working -> waiting_question -> working -> success -> idle。"""
    tracker, bridge_dir = _make_tracker(tmp_path, monkeypatch, online=True)
    tracker._poll_online()  # idle

    states = []
    tracker.state_changed.connect(lambda f, t: states.append(t))

    _write(bridge_dir, *(_records_for("user/message", "tool/call")))
    _write(bridge_dir, {"event": "question/requested", "questions": [
        {"id": "q1", "question": "选 A 还是 B？", "options": [{"label": "A"}, {"label": "B"}]}]})
    _write(bridge_dir, {"event": "question/resolved"})
    _write(bridge_dir, *(_records_for("turn/end", "idle")))
    tracker._poll_events()

    assert states == [
        "thinking", "working", "waiting_question", "working", "success", "idle",
    ]
    assert tracker.current_state is DshState.IDLE


def test_offline_releases_question_latch(tmp_path, monkeypatch):
    """DSH 离线时问题锁存一并清除，且离线后不再被残留 resolved 顶成 working。"""
    tracker, bridge_dir = _make_tracker(tmp_path, monkeypatch, online=True)
    tracker._poll_online()
    _write(bridge_dir, {"event": "question/requested", "questions": [{"id": "q", "question": "?"}]})
    tracker._poll_events()
    assert tracker.current_state is DshState.WAITING_QUESTION

    monkeypatch.setattr(harness_launcher, "is_running", lambda port: False)
    tracker._poll_online()
    assert tracker.current_state is DshState.OFFLINE
    assert tracker._pending_question is False


def test_recovery_after_restart(tmp_path, monkeypatch):
    """DSH 重启后自动回 idle 并继续消费事件。"""
    tracker, bridge_dir = _make_tracker(tmp_path, monkeypatch, online=True)
    tracker._poll_online()

    # DSH 下线 -> offline
    monkeypatch.setattr(harness_launcher, "is_running", lambda port: False)
    tracker._poll_online()
    assert tracker.current_state is DshState.OFFLINE

    # DSH 重启 -> idle，然后继续接收事件
    monkeypatch.setattr(harness_launcher, "is_running", lambda port: True)
    tracker._poll_online()
    assert tracker.current_state is DshState.IDLE

    _write(bridge_dir, *(_records_for("turn/start")))
    tracker._poll_events()
    assert tracker.current_state is DshState.THINKING


def test_unknown_event_ignored(tmp_path, monkeypatch):
    tracker, bridge_dir = _make_tracker(tmp_path, monkeypatch, online=True)
    tracker._poll_online()
    _write(bridge_dir, {"event": "some/unknown", "foo": 1})
    tracker._poll_events()
    assert tracker.current_state is DshState.IDLE
