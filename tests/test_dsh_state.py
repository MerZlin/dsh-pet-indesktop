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


def _make_tracker(tmp_path, monkeypatch, online=False, *, name="base"):
    """构造一个指向临时桥目录的跟踪器，并把在线探测钉死到 online。"""
    _qapp()
    root = tmp_path / name
    config_dir = root / "cfg"
    config_dir.mkdir(parents=True, exist_ok=True)
    bridge_dir = root / "dsh-pet-bridge"
    bridge_dir.mkdir(parents=True, exist_ok=True)
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
    assert tracker.current_state is DshState.WORKING


def test_user_message_plugin_source_ignored(tmp_path, monkeypatch):
    """agent.inject() 注入上下文（sourceKind=plugin）不触发对话开始、不进状态机。

    每轮 DSH 会注入 4-5 条 system-reminder/技能目录等 user/message,必须与真人
    消息区分,否则「对话开始」被注入文案污染。
    """
    tracker, bridge_dir = _make_tracker(tmp_path, monkeypatch, online=True)
    tracker._poll_online()  # idle
    messages = []
    tracker.user_message.connect(lambda sid, text: messages.append((sid, text)))

    _write(bridge_dir, {"event": "user/message", "sourceKind": "plugin",
                        "text": "<system-reminder> 技能目录……", "sessionId": "s1"})
    tracker._poll_events()

    assert messages == []
    assert tracker.current_state is DshState.IDLE


def test_user_message_real_emits_signal(tmp_path, monkeypatch):
    """真人消息（sourceKind=user）触发 user_message(session, text) 并收敛 thinking。"""
    tracker, bridge_dir = _make_tracker(tmp_path, monkeypatch, online=True)
    tracker._poll_online()  # idle
    messages = []
    tracker.user_message.connect(lambda sid, text: messages.append((sid, text)))

    _write(bridge_dir, {"event": "user/message", "sourceKind": "user",
                        "text": "看看还有没有这个事件", "sessionId": "s1"})
    tracker._poll_events()

    assert messages == [("s1", "看看还有没有这个事件")]
    assert tracker.current_state is DshState.THINKING


def test_user_message_legacy_record_keeps_signal(tmp_path, monkeypatch):
    """旧版桥接记录无 sourceKind：按真人消息兼容处理，绝不静默丢事件。"""
    tracker, bridge_dir = _make_tracker(tmp_path, monkeypatch, online=True)
    tracker._poll_online()  # idle
    messages = []
    tracker.user_message.connect(lambda sid, text: messages.append((sid, text)))

    _write(bridge_dir, {"event": "user/message", "text": "hi", "sessionId": "s2"})
    tracker._poll_events()

    assert messages == [("s2", "hi")]
    assert tracker.current_state is DshState.THINKING


def test_unknown_event_ignored(tmp_path, monkeypatch):
    tracker, bridge_dir = _make_tracker(tmp_path, monkeypatch, online=True)
    tracker._poll_online()
    _write(bridge_dir, {"event": "some/unknown", "foo": 1})
    tracker._poll_events()
    assert tracker.current_state is DshState.IDLE


def test_llm_error_record_enters_error_state(tmp_path, monkeypatch):
    """桥接真实写的是 llm_error（index.js），状态机必须认它——否则 ERROR 态不可达。

    桥接在 bad_response_status_code（API 级错误）分支写 ``event: "llm_error"``；
    旧键名 "llm/error" 与它只差一个分隔符，导致 API 错误永远进不了 ERROR。
    """
    tracker, bridge_dir = _make_tracker(tmp_path, monkeypatch, online=True)
    tracker._poll_online()  # idle

    states = []
    tracker.state_changed.connect(lambda f, t: states.append(t))

    _write(bridge_dir, {"event": "tool/call"})  # working
    _write(bridge_dir, {
        "event": "llm_error", "errorCode": "bad_response_status_code",
        "errorMessage": "404", "errorKind": "api",
    })
    tracker._poll_events()

    assert tracker.current_state is DshState.ERROR
    assert states == ["working", "error"]


def test_dead_event_keys_removed_from_state_map():
    """状态表不得保留桥接从不写的事件键。

    index.js:858 明确不转发流式 assistant/chunk，plan/mode 也只在 DSH 内部词汇里
    出现——留着这两条只会让状态表看起来覆盖了并不存在的事件。
    """
    from pet.dsh_state import _EVENT_TO_STATE

    assert "llm_error" in _EVENT_TO_STATE
    # 斜杠拼写保留为**旧 producer 兼容**（consumer 测试 test_llm_error_event_...
    # 明确要求 legacy slash 仍被接受）；真正要锁死的是"状态表不得覆盖 bridge
    # 从不发出的流式事件"——assistant/chunk、plan/mode 才是核心 dead key。
    assert "assistant/chunk" not in _EVENT_TO_STATE
    assert "plan/mode" not in _EVENT_TO_STATE


def test_stop_drops_inflight_probe_result(tmp_path, monkeypatch):
    """stop() 后在途在线探测的结果必须被丢弃。

    崩溃族（macOS 全量 teardown segfault）：_probe_worker 是 daemon 线程、
    可能正阻塞在 socket 探测上；若 stop()/QObject 销毁后它仍跨线程 emit
    ``_online_checked``，会与 Qt 收尾（conftest deleteLater + processEvents）
    竞争。修复：stop() 换代，worker 回来后代次不匹配即静默返回。
    """
    tracker, _ = _make_tracker(tmp_path, monkeypatch, online=False)
    # 探测进行中 tracker 被 stop()（模拟测试 teardown）
    tracker._started = True
    generation = tracker._probe_generation
    tracker.stop()
    assert tracker._probe_generation == generation + 1
    assert tracker._probe_inflight is False

    applied: list[bool] = []
    tracker._apply_online = lambda online: applied.append(online)  # type: ignore[method-assign]
    # 模拟在途 worker 此刻才探测完：is_running 现在会返回 True
    monkeypatch.setattr(harness_launcher, "is_running", lambda port: True)
    tracker._probe_worker(generation, [38080, 3080])
    assert applied == [], "stop() 后的迟到探测结果必须丢弃，不得 emit/_apply_online"


def test_probe_worker_applies_result_while_started(tmp_path, monkeypatch):
    """未 stop 的正常路径：worker 探测结果仍被应用（防止上面丢弃逻辑误伤）。"""
    tracker, _ = _make_tracker(tmp_path, monkeypatch, online=False)
    monkeypatch.setattr(harness_launcher, "is_running", lambda port: True)
    applied: list[bool] = []
    original = tracker._apply_online

    def spy(online: bool) -> None:
        applied.append(online)
        original(online)

    tracker._started = True
    tracker._apply_online = spy  # type: ignore[method-assign]
    tracker._probe_worker(tracker._probe_generation, [38080, 3080])
    assert applied == [True]
    assert tracker.current_state is DshState.IDLE
    tracker.stop()


def test_shutdown_live_for_tests_stops_trackers(tmp_path, monkeypatch):
    """conftest 收口入口：_shutdown_live_for_tests() 把存活 tracker 全部 stop。"""
    from pet import dsh_state as dsh_state_mod

    tracker_a, _ = _make_tracker(tmp_path, monkeypatch, online=False, name="a")
    tracker_b, _ = _make_tracker(tmp_path, monkeypatch, online=False, name="b")
    tracker_a._started = True
    tracker_b._started = True
    generation_a = tracker_a._probe_generation
    generation_b = tracker_b._probe_generation

    dsh_state_mod._shutdown_live_for_tests()
    assert tracker_a._started is False
    assert tracker_b._started is False
    assert tracker_a._probe_generation == generation_a + 1
    assert tracker_b._probe_generation == generation_b + 1
