# -*- coding: utf-8 -*-
"""DSH 统一状态收敛（dsh_state.DshStateConverger）单元测试。

减法后（2026-10）dsh_state 只剩纯逻辑：不建 Qt 对象、不读文件、不探端口——
直接驱动收敛器的 handle_record / set_online / tick，验证 edge-trigger 去重、
离线恢复与审批/问题锁存。读方（DshMonitor）与消费侧的接线回归见
tests/test_agent_link_subtraction.py 的单读方/审批收敛测试族。
"""
from __future__ import annotations

import pytest

from pet.agent_link import dsh_state
from pet.agent_link.dsh_state import (
    APPROVAL_LATCH_TIMEOUT_S,
    DshState,
    DshStateConverger,
    TurnCompletionTracker,
    candidate_ports,
    map_event_to_state,
)


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


def _feed(conv, *records):
    out = []
    for rec in records:
        out.extend(conv.handle_record(rec))
    return out


def _states(outputs):
    return [to for kind, _f, to, *_src in outputs if kind == "state"]


def test_offline_when_dsh_down(caplog):
    conv = DshStateConverger()
    with caplog.at_level("INFO"):
        out = conv.set_online(False)
    assert conv.current_state is DshState.OFFLINE
    assert _states(out) == ["offline"]
    assert any("[DSH STATE] offline" in r.getMessage() for r in caplog.records)


def test_online_goes_idle(caplog):
    conv = DshStateConverger()
    with caplog.at_level("INFO"):
        out = conv.set_online(True)
    assert conv.current_state is DshState.IDLE
    # DSH 一开始就在线：首个状态直接是 idle（from 为空，不是 offline -> idle）
    assert _states(out) == ["idle"]
    assert any("[DSH STATE] idle" in r.getMessage() for r in caplog.records)


def test_offline_then_online_idle(caplog):
    """先 offline，DSH 上线后切 idle：日志体现 offline -> idle。"""
    conv = DshStateConverger()
    conv.set_online(False)
    assert conv.current_state is DshState.OFFLINE
    with caplog.at_level("INFO"):
        out = conv.set_online(True)
    assert conv.current_state is DshState.IDLE
    assert _states(out) == ["idle"]
    assert any("[DSH STATE] offline -> idle" in r.getMessage() for r in caplog.records)


def test_edge_trigger_dedup():
    """同状态重复事件不重复产出。"""
    conv = DshStateConverger()
    conv.set_online(True)  # -> idle
    out = _feed(conv, *(_records_for("user/message") * 2))  # thinking ×2
    assert conv.current_state is DshState.THINKING
    assert _states(out) == ["thinking"]  # 只有一次 thinking 转换


def test_full_pipeline():
    """完整生命周期：thinking -> working -> waiting_approval -> working -> success -> idle。

    审批锁存的权威触发是 cordis/request-run（requiresApproval 严格 True）；
    解锁用 cordis/request-run-resolved（approval/decided 同款兼容路径另测）。
    """
    conv = DshStateConverger()
    conv.set_online(True)  # idle
    out = _feed(conv, *(_records_for("user/message", "tool/call")))
    out += _feed(conv, {"event": "cordis/request-run", "requestId": "r-1",
                        "payload": {"requiresApproval": True}})
    out += _feed(conv, {"event": "cordis/request-run-resolved", "requestId": "r-1"})
    out += _feed(conv, *(_records_for("turn/end", "idle")))
    assert _states(out) == [
        "thinking", "working", "waiting_approval", "working", "success", "idle",
    ]
    assert conv.current_state is DshState.IDLE


def test_approval_asked_does_not_latch():
    """H1 钉住：裸 approval/asked（普通工具调用也会被宿主打标的审计信号）
    绝不收敛为 waiting_approval——它是误报源，不是权威审批事件。"""
    conv = DshStateConverger()
    conv.set_online(True)  # idle
    out = _feed(conv, {"event": "approval/asked", "tool": "pwsh"})
    assert _states(out) == []
    assert conv.current_state is DshState.IDLE
    assert conv._pending_approval is False


def test_cordis_request_run_requires_strict_true():
    """cordis/request-run 只在 requiresApproval 严格布尔 True 时锁存审批态。"""
    conv = DshStateConverger()
    conv.set_online(True)
    # 平铺 False / 缺失 / 字符串 "true" 都不锁存
    _feed(conv, {"event": "cordis/request-run", "requestId": "r-a",
                 "payload": {"requiresApproval": False}})
    _feed(conv, {"event": "cordis/request-run", "requestId": "r-b"})
    _feed(conv, {"event": "cordis/request-run", "requestId": "r-c",
                 "payload": {"requiresApproval": "true"}})
    assert conv.current_state is DshState.IDLE
    # 嵌套 True 才锁存
    out = _feed(conv, {"event": "cordis/request-run", "requestId": "r-d",
                       "payload": {"requiresApproval": True}})
    assert _states(out) == ["waiting_approval"]
    assert conv.current_state is DshState.WAITING_APPROVAL


def test_approval_decided_releases_latch_for_compat():
    """approval/decided 保留为解锁兼容路径（旧桥/自定义通道的 approval 对）。"""
    conv = DshStateConverger()
    conv.set_online(True)
    _feed(conv, {"event": "approval/request", "approvalId": "ap-1"})  # 旧式审批事件
    assert conv.current_state is DshState.WAITING_APPROVAL
    out = _feed(conv, {"event": "approval/decided", "approvalId": "ap-1"})
    assert _states(out) == ["working"]
    assert conv.current_state is DshState.WORKING


def test_approval_latch_ignores_working():
    """审批锁存期间 working 事件被忽略，直到 cordis/request-run-resolved。"""
    conv = DshStateConverger()
    conv.set_online(True)
    # waiting_approval 后，即便又来 working（agent 仍在跑），也不被顶掉
    _feed(conv, {"event": "cordis/request-run", "requestId": "r-1",
                 "payload": {"requiresApproval": True}})
    _feed(conv, *(_records_for("tool/call", "working")))
    assert conv.current_state is DshState.WAITING_APPROVAL

    _feed(conv, {"event": "cordis/request-run-resolved", "requestId": "r-1"})
    assert conv.current_state is DshState.WORKING


def test_offline_force_overrides_work():
    """DSH 被强制关闭时切 offline，不崩。"""
    conv = DshStateConverger()
    conv.set_online(True)
    _feed(conv, *(_records_for("working")))
    assert conv.current_state is DshState.WORKING

    conv.set_online(False)
    assert conv.current_state is DshState.OFFLINE


def test_question_latch_ignores_working():
    """问题锁存期间 working 事件被忽略，直到 question/resolved。"""
    conv = DshStateConverger()
    conv.set_online(True)
    _feed(conv, {"event": "question/requested", "questions": [
        {"id": "q1", "question": "要执行哪个方案？",
         "options": [{"label": "方案 A"}, {"label": "方案 B"}]}]})
    _feed(conv, *(_records_for("tool/call", "working")))
    assert conv.current_state is DshState.WAITING_QUESTION

    _feed(conv, {"event": "question/resolved"})
    assert conv.current_state is DshState.WORKING


def test_question_pipeline():
    """完整生命周期：thinking -> working -> waiting_question -> working -> success -> idle。"""
    conv = DshStateConverger()
    conv.set_online(True)  # idle
    out = []
    out.extend(_feed(conv, *(_records_for("user/message", "tool/call"))))
    out.extend(_feed(conv, {"event": "question/requested", "questions": [
        {"id": "q1", "question": "选 A 还是 B？", "options": [{"label": "A"}, {"label": "B"}]}]}))
    out.extend(_feed(conv, {"event": "question/resolved"}))
    out.extend(_feed(conv, *(_records_for("turn/end", "idle"))))
    assert _states(out) == [
        "thinking", "working", "waiting_question", "working", "success", "idle",
    ]
    assert conv.current_state is DshState.IDLE


def test_offline_releases_question_latch():
    """DSH 离线时问题锁存一并清除，且离线后不再被残留 resolved 顶成 working。"""
    conv = DshStateConverger()
    conv.set_online(True)
    _feed(conv, {"event": "question/requested", "questions": [{"id": "q", "question": "?"}]})
    assert conv.current_state is DshState.WAITING_QUESTION

    out = conv.set_online(False)
    assert conv.current_state is DshState.OFFLINE
    assert _states(out) == ["offline"]

    # 残留 resolved 不再把 offline 顶成 working（map 得 working，但读方在离线时
    # 不喂记录；即便防御性喂入，resolved 也只是解除一个已清的锁存）
    out = _feed(conv, {"event": "question/resolved"})
    assert conv.current_state is DshState.OFFLINE


def test_recovery_after_restart():
    """DSH 重启后自动回 idle 并继续消费事件。"""
    conv = DshStateConverger()
    conv.set_online(True)
    conv.set_online(False)
    assert conv.current_state is DshState.OFFLINE

    conv.set_online(True)
    assert conv.current_state is DshState.IDLE

    _feed(conv, *(_records_for("turn/start")))
    assert conv.current_state is DshState.THINKING


def test_user_message_plugin_source_ignored():
    """agent.inject() 注入上下文（sourceKind=plugin）不触发对话开始、不进状态机。

    每轮 DSH 会注入 4-5 条 system-reminder/技能目录等 user/message,必须与真人
    消息区分,否则「对话开始」被注入文案污染。
    """
    conv = DshStateConverger()
    conv.set_online(True)  # idle
    out = _feed(conv, {"event": "user/message", "sourceKind": "plugin",
                       "text": "<system-reminder> 技能目录……", "sessionId": "s1"})
    assert [o for o in out if o[0] == "user_message"] == []
    assert conv.current_state is DshState.IDLE


def test_user_message_real_emits_output():
    """真人消息（sourceKind=user）产出 user_message(session, text) 并收敛 thinking。"""
    conv = DshStateConverger()
    conv.set_online(True)  # idle
    out = _feed(conv, {"event": "user/message", "sourceKind": "user",
                       "text": "看看还有没有这个事件", "sessionId": "s1"})
    assert [o for o in out if o[0] == "user_message"] == [("user_message", "s1", "看看还有没有这个事件")]
    assert conv.current_state is DshState.THINKING


def test_user_message_legacy_record_keeps_output():
    """旧版桥接记录无 sourceKind：按真人消息兼容处理，绝不静默丢事件。"""
    conv = DshStateConverger()
    conv.set_online(True)  # idle
    out = _feed(conv, {"event": "user/message", "text": "hi", "sessionId": "s2"})
    assert [o for o in out if o[0] == "user_message"] == [("user_message", "s2", "hi")]
    assert conv.current_state is DshState.THINKING


def test_unknown_event_ignored():
    conv = DshStateConverger()
    conv.set_online(True)
    _feed(conv, {"event": "some/unknown", "foo": 1})
    assert conv.current_state is DshState.IDLE


def test_llm_error_record_enters_error_state():
    """桥接真实写的是 llm_error（index.js），状态机必须认它——否则 ERROR 态不可达。

    桥接在 bad_response_status_code（API 级错误）分支写 ``event: "llm_error"``；
    旧键名 "llm/error" 与它只差一个分隔符，导致 API 错误永远进不了 ERROR。
    """
    conv = DshStateConverger()
    conv.set_online(True)  # idle
    out = _feed(conv, {"event": "tool/call"},  # working
                {
                    "event": "llm_error", "errorCode": "bad_response_status_code",
                    "errorMessage": "404", "errorKind": "api",
                })
    assert conv.current_state is DshState.ERROR
    assert _states(out) == ["working", "error"]


def test_success_edge_needs_a_real_transition():
    """``turn/end`` 的 success 是边沿触发：已是 SUCCESS 时再来一条不产出。

    中途挂载场景：回填跳过 ``turn/start`` 之后，同一会话的 ``turn/end`` 是收敛器
    唯一能看到的那条记录——它能产出 success，靠的是「接上时收敛器已被探活基线
    推到 idle」这一次真实转换，不是「turn/end 每条都产出」。反过来，重复的
    ``turn/end``（同状态帧）必须零产出，否则完成提醒会被重复调度。
    """
    conv = DshStateConverger()
    conv.set_online(True)  # idle（接上时的基线转换）
    out = _feed(conv, {"event": "turn/end"})
    assert conv.current_state is DshState.SUCCESS
    assert _states(out) == ["success"]

    out = _feed(conv, {"event": "turn/end"}, {"event": "turn/end"})
    assert conv.current_state is DshState.SUCCESS
    assert _states(out) == [], "已 SUCCESS 时重复的 turn/end 不得再产出"


def test_dead_event_keys_removed_from_state_map():
    """状态表不得保留桥接从不写的事件键。

    index.js 明确不转发流式 assistant/chunk，plan/mode 也只在 DSH 内部词汇里
    出现——留着这两条只会让状态表看起来覆盖了并不存在的事件。
    """
    from pet.agent_link.dsh_state import _EVENT_TO_STATE

    assert "llm_error" in _EVENT_TO_STATE
    assert "llm/error" not in _EVENT_TO_STATE, "桥接实际写 llm_error，旧键名匹配不到"
    assert "assistant/chunk" not in _EVENT_TO_STATE
    assert "plan/mode" not in _EVENT_TO_STATE


def test_approval_latch_timeout_releases_to_working():
    """审批锁存超时兜底：DSH 漏发 approval/decided 时 tick() 强制回 working。"""
    clock = [1000.0]
    conv = DshStateConverger(clock=lambda: clock[0])
    conv.set_online(True)
    _feed(conv, {"event": "tool/call"})
    _feed(conv, {"event": "cordis/request-run", "requestId": "r-1",
                 "payload": {"requiresApproval": True}})
    assert conv.current_state is DshState.WAITING_APPROVAL

    clock[0] += APPROVAL_LATCH_TIMEOUT_S - 1
    assert conv.tick() == [], "未到超时点不得解除锁存"
    assert conv.current_state is DshState.WAITING_APPROVAL

    clock[0] += 2.0
    out = conv.tick()
    assert _states(out) == ["working"]
    assert conv.current_state is DshState.WORKING


def test_candidate_ports_include_desktop_host():
    """端口候选必须含 desktop 桌面端硬编码端口 19387（web 3080 / 避让 38080 之外）。"""
    ports = candidate_ports()
    assert 3080 in ports
    assert 38080 in ports
    assert 19387 in ports, "desktop 桌面端 host 在 127.0.0.1:19387，漏探会把桌面端误判 offline"


# ---------------------------------------------------------------------------
# live 回合收尾身份（TurnCompletionTracker）：完成提醒的判据
# ---------------------------------------------------------------------------
#
# 判定为什么不能挂在收敛状态上：``turn/end`` 不带源 ID、收敛器是 edge-trigger
# （已停在 SUCCESS 时再收一条收尾零产出），而「中途挂载 / 聚合 idle 基线先到 /
# 关开联动残留终态」这些来向上呈现管线里的上一个状态都不是 busy。唯一稳定的事实
# 是这条 live 记录本身——它的身份由本模块构造，消费侧只按身份去重（见
# tests/test_dsh_mid_attach_done.py 的真 tailer + 真 monitor 链路用例）。
#
# 身份 = (session_id, step, token)：
# - session_id / step：桥接真实写出的字段（桥缺 step 时回退 ``turn:<n>``）；
# - token：**全局单调 generation**，每个新回合递增一次。带回合号的记录按
#   ``(会话, 回合号) → token`` 绑定表定身份（同一回合的镜像副本落回同一个 token），
#   旧桥（无回合号）按会话表定身份。全局计数是硬要求，不是实现细节：消费侧按身份
#   去重，token 一旦复用就会被当成「同一回合的重复投递」而吞掉新回合。「每会话自增 +
#   只加容量」的朴素修法在会话被挤出后正好撞身份，对照实现见
#   .scratch/product-dsh-tracker-bound-20261011/probe_naive_lru_collision.py。
#
# 两张表都有界（``dsh_state.TURN_TOKEN_MEMORY`` / ``TURN_BINDING_MEMORY``），会话表
# 只由 live 起点/本会话消息写入：只见过收尾的会话（中途挂载）token 恒为 0、不落表
# ——身份靠 session + step 逐条唯一。


def test_turn_completion_identity_from_turn_end():
    tracker = TurnCompletionTracker()
    end = {"event": "turn/end", "sessionId": "s-1", "step": "turn:3"}
    identity = tracker.observe(end)
    assert identity == ("turn-end", "s-1", "turn:3", 0)
    # 同一回合的重复投递（另一个文件的游标）身份相同 → 消费侧据此只提醒一次
    assert tracker.observe(end, ("dsh-b.jsonl", (1, 2), 7)) == identity


def test_turn_completion_token_separates_successive_turns():
    """同会话连续回合靠 live 起点 token 分开；两回合 step 取值相同也不撞。"""
    tracker = TurnCompletionTracker()
    assert tracker.observe({"event": "turn/start", "sessionId": "s", "step": "5"}) is None
    first = tracker.observe({"event": "turn/end", "sessionId": "s", "step": "5"})
    assert tracker.observe({"event": "user/message", "sessionId": "s"}) is None
    second = tracker.observe({"event": "turn/end", "sessionId": "s", "step": "5"})
    assert first != second, "同一会话的下一回合必须有不同身份"
    assert first[:3] == second[:3] == ("turn-end", "s", "5"), "差异只能来自起点 token"
    assert second[3] > first[3], "后一个起点的 token 必须严格更大（token 只增不重用）"
    # 另一会话的起点不影响本会话 token（身份按会话独立）
    tracker.observe({"event": "turn/start", "sessionId": "other"})
    assert tracker.observe({"event": "turn/end", "sessionId": "s", "step": "5"}) == second


def test_turn_completion_session_table_is_bounded():
    """会话表有界：1000 个不同会话的 live 起点不得把表撑成 1000 条。

    长期运行的实例会看过成千上万个会话（DSH 每换一次会话就是一个新 sessionId），
    表随会话数增长即无界内存——修复前实测 1000 个会话 → 1000 条
    （.scratch/product-dsh-tracker-bound-20261011/01-probe-RED-prefix.txt）。
    不变量：条数 ≤ ``TURN_TOKEN_MEMORY``，且淘汰从最久没有起点的那一端开始。
    """
    tracker = TurnCompletionTracker()
    for i in range(1000):
        tracker.observe({"event": "turn/start", "sessionId": f"s-{i}"})
    limit = dsh_state.TURN_TOKEN_MEMORY
    assert len(tracker._tokens) <= limit, f"会话表必须有界：{len(tracker._tokens)} 条 > {limit}"
    assert "s-999" in tracker._tokens, "最近的会话必须在表内"
    assert "s-0" not in tracker._tokens, "最久没有起点的会话应被淘汰"


def test_turn_completion_evicted_session_next_turn_gets_fresh_identity():
    """被容量挤出的会话：同 session + 同 step 的新回合必须拿到**不同**身份。

    这是 token 必须全局单调（而非每会话自增）的原因——消费侧按身份去重，身份重用
    = 新回合被当成重复投递吞掉（无完成气泡、无 done 音效）。「只加容量、token 语义
    不变」的修法在本用例下必红（对照实现见 scratch 的 probe_naive_lru_collision.py：
    驱逐后新旧身份同为 ``("turn-end", "s-x", "turn:1", 1)``）。
    """
    tracker = TurnCompletionTracker()
    end = {"event": "turn/end", "sessionId": "s-x", "step": "turn:1"}
    tracker.observe({"event": "turn/start", "sessionId": "s-x", "step": "turn:1"})
    first = tracker.observe(end)
    assert first is not None

    for i in range(dsh_state.TURN_TOKEN_MEMORY + 8):
        tracker.observe({"event": "turn/start", "sessionId": f"flood-{i}"})
    assert "s-x" not in tracker._tokens, "前提：s-x 已被容量淘汰"

    tracker.observe({"event": "turn/start", "sessionId": "s-x", "step": "turn:1"})
    second = tracker.observe(end)
    assert second is not None
    assert second != first, "驱逐后同一会话的新回合不得沿用旧身份"
    assert first[:3] == second[:3] == ("turn-end", "s-x", "turn:1")


def test_turn_completion_end_only_sessions_do_not_grow_the_table():
    """中途挂载（从没见过 live 起点）的会话：收尾照常出身份，但**不落表**。

    表只能由 live 起点驱动——否则任意 sessionId 的收尾帧都能往里塞行，等于把无界
    增长从「起点表」挪到「收尾表」。这类会话 token 恒为 0（= 从未见过起点），身份
    仍靠 session + step 逐条唯一，不按正文造 ID、不写任何持久状态。
    """
    tracker = TurnCompletionTracker()
    identities = [
        tracker.observe({"event": "turn/end", "sessionId": f"attach-{i}", "step": "turn:1"})
        for i in range(1000)
    ]
    assert len(tracker._tokens) == 0, "只收尾的会话不得落表"
    assert all(identity is not None for identity in identities)
    assert len(set(identities)) == 1000, "同 step 的不同会话必须各自身份唯一"
    assert {identity[3] for identity in identities} == {0}, "无起点会话的 token 恒为 0"


def test_turn_completion_end_identity_stable_across_other_sessions():
    """同一回合的重复投递身份不变——别的会话的起点不得动摇本会话的 token。

    全局 generation 是「给起点发号」，不是「拿当前号当身份」：本会话的 token 只在自己
    的起点上更新。若身份跟着全局计数走，同一回合的重复帧会被算成新回合而重复提醒。
    """
    tracker = TurnCompletionTracker()
    tracker.observe({"event": "turn/start", "sessionId": "s-r", "step": "turn:1"})
    end = {"event": "turn/end", "sessionId": "s-r", "step": "turn:1"}
    identity = tracker.observe(end)
    assert identity is not None
    for i in range(64):
        tracker.observe({"event": "turn/start", "sessionId": f"noise-{i}"})
        assert tracker.observe(end, ("dsh-other.jsonl", (1, 1), i)) == identity


def test_turn_completion_ignores_non_completion_records():
    tracker = TurnCompletionTracker()
    for record in ({"event": "tool/call", "sessionId": "s"},
                   {"event": "llm/retry", "sessionId": "s"},
                   {"event": "llm_error", "sessionId": "s"},
                   {"event": "execution/failed", "sessionId": "s"},
                   {"event": "AgentStatus", "state": "working"},
                   {"event": "AgentStatus", "state": "error"},
                   {"event": "cordis/request-run", "sessionId": "s",
                    "payload": {"requiresApproval": True}}):
        assert tracker.observe(record) is None, record


def test_turn_completion_accepts_any_success_shaped_record():
    """任何收敛为 success 的记录形状都算收尾（含旧式 AgentStatus 基线）。

    ``AgentStatus`` 不带 sessionId/step（桥接的聚合基线形状），此时身份退回摄入
    游标——注意**不能**退化成「同代次所有无会话收尾都是同一个身份」，那会把两条
    不同的收尾压成一次提醒。
    """
    assert TurnCompletionTracker().observe(
        {"event": "AgentStatus", "state": "success"}, ("dsh.jsonl", (1, 1), 0)) == \
        ("turn-end-cursor", "dsh.jsonl", (1, 1), 0)
    assert TurnCompletionTracker().observe(
        {"event": "turn/end", "session_id": "s-2"}) == ("turn-end", "s-2", "", 0)


def test_turn_completion_cursor_fallback_and_reset():
    """无 session/step 时退回摄入游标；连游标都没有时给不出身份（消费侧不排定）。"""
    assert TurnCompletionTracker().observe(
        {"event": "turn/end"}, ("dsh.jsonl", (9, 9), 42)) == \
        ("turn-end-cursor", "dsh.jsonl", (9, 9), 42)
    assert TurnCompletionTracker().observe({"event": "turn/end"}) is None

    tracker = TurnCompletionTracker()
    tracker.observe({"event": "turn/start", "sessionId": "s"})
    tracker.observe({"event": "turn/end", "sessionId": "s", "step": "turn:1"})
    tracker.reset()
    assert tracker.observe({"event": "turn/end", "sessionId": "s", "step": "turn:1"}) == \
        ("turn-end", "s", "turn:1", 0), "重启后起点记忆不得跨代次存活（清表 → token 回 0）"


def test_turn_completion_reset_clears_table_but_never_reuses_tokens():
    """reset 清会话表、**不回收**全局计数：跨代次 token 不复用。

    消费侧的身份记忆（``AgentLinkManager._dsh_turn_ids``）不随监视器生命周期清空，
    所以「关掉再开启联动、同一会话继续跑」时若 token 从头重来，新回合会撞上旧身份被
    当成重复吞掉。清表已足够隔离代次（未见起点的会话 token 回 0，既有断言不变），
    计数不回收则再堵住这条真实路径。
    """
    tracker = TurnCompletionTracker()
    end = {"event": "turn/end", "sessionId": "s", "step": "turn:1"}
    tracker.observe({"event": "turn/start", "sessionId": "s", "step": "turn:1"})
    before = tracker.observe(end)
    tracker.reset()
    assert tracker.observe(end) == ("turn-end", "s", "turn:1", 0), "清表后 token 回 0"
    tracker.observe({"event": "turn/start", "sessionId": "s", "step": "turn:1"})
    after = tracker.observe(end)
    assert after != before, "跨代次（关开联动）同会话同 step 的新回合不得撞旧身份"
    assert after is not None and after[3] > before[3]


# ---- 用户中止的回合静默收尾（桥接 turn/end 携带 reasonKind）----

def test_aborted_turn_end_maps_to_idle_not_success():
    from pet.agent_link.dsh_state import map_event_to_state
    assert map_event_to_state({"event": "turn/end", "reasonKind": "aborted"}) is DshState.IDLE
    assert map_event_to_state({"event": "turn/end", "reasonKind": "completed"}) is DshState.SUCCESS
    # 旧桥接：无 reasonKind → 保持原 SUCCESS 行为
    assert map_event_to_state({"event": "turn/end"}) is DshState.SUCCESS


def test_aborted_turn_end_yields_no_completion_identity():
    from pet.agent_link.dsh_state import TurnCompletionTracker
    tr = TurnCompletionTracker()
    tr.observe({"event": "turn/start", "sessionId": "s1"})
    assert tr.observe({"event": "turn/end", "sessionId": "s1", "step": "1",
                       "reasonKind": "aborted"}) is None
    tr.observe({"event": "turn/start", "sessionId": "s1"})
    assert tr.observe({"event": "turn/end", "sessionId": "s1", "step": "2",
                       "reasonKind": "completed"}) is not None


def test_aborted_turn_end_transition_carries_source():
    """中止 turn/end 收敛为 idle 时必须带 source_event（消费侧据此回待机/撤确认）。"""
    conv = DshStateConverger()
    conv.handle_record({"event": "turn/start", "sessionId": "s"})
    out = conv.handle_record({"event": "turn/end", "sessionId": "s", "reasonKind": "aborted"})
    assert out == [("state", "thinking", "idle", "turn/end")], out


def test_aborted_turn_end_after_idle_baseline_still_emits_marker():
    """旧 AgentStatus idle 基线先到时，中止 turn/end 不得被同态去重吞掉。"""
    conv = DshStateConverger()
    conv.handle_record({"event": "turn/start", "sessionId": "s"})
    conv.handle_record({"event": "AgentStatus", "state": "idle"})
    assert conv.current_state is DshState.IDLE
    out = conv.handle_record({"event": "turn/end", "sessionId": "s", "reasonKind": "aborted"})
    assert out == [("state", "idle", "idle", "turn/end")], out


def test_aborted_turn_end_releases_approval_and_question_latch():
    """锁存（审批/问题）期间用户中止：中止终结整轮，锁存解除并回 idle，不得被吞。"""
    for req in ({"event": "approval/request", "sessionId": "s"},
                {"event": "question/requested", "sessionId": "s"}):
        conv = DshStateConverger()
        conv.handle_record({"event": "turn/start", "sessionId": "s"})
        conv.handle_record(req)
        out = conv.handle_record({"event": "turn/end", "sessionId": "s", "reasonKind": "aborted"})
        assert conv.current_state is DshState.IDLE, (req, out)
        assert out and out[-1][2:] == ("idle", "turn/end"), out
        # 锁存已解除：后续 working 正常推进
        assert conv.handle_record({"event": "tool/call", "sessionId": "s", "tool": "bash"})


# ---------------------------------------------------------------------------
# 多写者（多 DSH 实例）串扰：状态与锁存按写者独立，对外折叠成单出口
# ---------------------------------------------------------------------------
#
# 回归背景（2026-10，实机复现）：用户同时开两个 DSH 实例，各自写自己的
# ``dsh-{pid}.jsonl``，桌宠 glob 全部读入——而收敛器此前只有**一份**状态与一份
# 锁存，于是 A 实例回待机（``AgentStatus idle``）会顶掉 B 实例的 working、A 的
# ``question/resolved`` 会解开 B 的问题锁存。
#
# 修法：``handle_record`` 带写者归属（``owner``，摄入侧从插件 boot UUID 或
# （文件路径, 文件身份）推出，见 ``agent_link.record_owner``），状态/锁存按写者
# 分片保存，对外只产出 :meth:`DshStateConverger._fold` 的折叠态——只要还有写者锁存
# 在等待/在忙，别的写者回待机就改不了折叠态。
_OWNER_A = "inst:writer-a"
_OWNER_B = "inst:writer-b"


def test_writer_idle_and_resolved_do_not_cross_writers():
    """① A 回待机顶不掉 B 的问题；② A 自己的 resolved 也解不开 B 的锁存。"""
    conv = DshStateConverger()
    conv.set_online(True)                                   # idle 基线
    out = conv.handle_record({"event": "question/requested", "sessionId": "sB"}, owner=_OWNER_B)
    assert _states(out) == ["waiting_question"]
    assert conv._pending_question is True

    # ① A 实例回待机（它自己没在等任何东西）：折叠态必须原样停在 B 的等待上
    out = conv.handle_record({"event": "AgentStatus", "state": "idle"}, owner=_OWNER_A)
    assert _states(out) == [], "另一个写者的 idle 不得改变折叠态"
    assert conv.current_state is DshState.WAITING_QUESTION
    assert conv._pending_question is True, "B 的问题锁存不得被 A 的 idle 清掉"

    # ② A 发一条 question/resolved：它解开的是**它自己**的锁存（本来就没锁）
    out = conv.handle_record({"event": "question/resolved", "sessionId": "sA"}, owner=_OWNER_A)
    assert _states(out) == []
    assert conv.current_state is DshState.WAITING_QUESTION, "A 的收尾不得解开 B 的锁存"

    # ③ B 自己的 resolved 才解锁
    out = conv.handle_record({"event": "question/resolved", "sessionId": "sB"}, owner=_OWNER_B)
    assert _states(out) == ["working"]
    assert conv.current_state is DshState.WORKING


def test_busy_writer_survives_other_writer_idle_and_bucket_is_isolated():
    """③ 任一写者忙就不算待机；④ 无主桶（旧桥/直调）与具名写者互不干扰。"""
    # 顺序一：A 先忙、B 回待机
    conv = DshStateConverger()
    conv.set_online(True)
    conv.handle_record({"event": "tool/call"}, owner=_OWNER_A)
    out = conv.handle_record({"event": "AgentStatus", "state": "idle"}, owner=_OWNER_B)
    assert conv.current_state is DshState.WORKING, "另一个写者回待机不得把整体判成待机"
    assert _states(out) == []

    # 顺序二：B 先回待机、A 后忙
    conv2 = DshStateConverger()
    conv2.set_online(True)
    conv2.handle_record({"event": "AgentStatus", "state": "idle"}, owner=_OWNER_B)
    out = conv2.handle_record({"event": "tool/call"}, owner=_OWNER_A)
    assert conv2.current_state is DshState.WORKING
    assert _states(out) == ["working"], "真正进入忙碌时该有的边沿不得丢"

    # ④ 无主桶：owner 缺省（旧桥记录/直调）是**另一个桶**，不是通配
    conv3 = DshStateConverger()
    conv3.set_online(True)
    conv3.handle_record({"event": "question/requested", "sessionId": "sA"}, owner=_OWNER_A)
    conv3.handle_record({"event": "AgentStatus", "state": "idle"})       # 无主桶 idle
    assert conv3.current_state is DshState.WAITING_QUESTION, "无主桶的 idle 不得清具名写者的锁存"
    conv3.handle_record({"event": "question/resolved"})                  # 无主桶自己的锁存（本来就没锁）
    assert conv3.current_state is DshState.WAITING_QUESTION

    conv4 = DshStateConverger()
    conv4.set_online(True)
    conv4.handle_record({"event": "question/requested", "sessionId": "s"})   # 无主桶锁存
    conv4.handle_record({"event": "AgentStatus", "state": "idle"}, owner=_OWNER_A)
    assert conv4.current_state is DshState.WAITING_QUESTION, "具名写者的 idle 不得清无主桶的锁存"
    out = conv4.handle_record({"event": "question/resolved"})
    assert _states(out) == ["working"], "无主桶自己的收尾照常解锁"


def test_scope_registry_refuses_new_writer_instead_of_merging(caplog):
    """注册表满且写者都在忙/等待中：**拒接**新写者，绝不并入已有写者的桶。

    并桶（旧实现把超容量写者并进无主桶）等于让两个不同的写者共用一个锁存与状态：
    C 的 idle 会撤掉 D 的等待、D 的收尾会解开 C 的锁存——多实例串扰本身。拒接只让
    新写者的状态分片不进收敛器，它自己的交互归属照旧（消费侧按它自己的 owner 记）。
    """
    limit = dsh_state.SCOPE_REGISTRY_MAX
    conv = DshStateConverger()
    conv.set_online(True)
    for i in range(limit):
        conv.handle_record({"event": "tool/call"}, owner=f"inst:busy-{i}")   # 全忙
    assert len(conv._scope_keys) == limit

    with caplog.at_level("WARNING"):
        conv.handle_record({"event": "question/requested", "sessionId": "sC"}, owner="inst:C")
        conv.handle_record({"event": "AgentStatus", "state": "idle"}, owner="inst:D")

    assert len(conv._scope_keys) <= limit, "写者注册表必须有界"
    assert len(conv._scopes) <= limit, "拒接不得新建分片，更不得并桶"
    assert "inst:C" not in conv._scope_keys and "inst:D" not in conv._scope_keys
    assert conv._scope_overflow_count >= 1, "拒接必须可诊断（不是静默丢）"
    assert any("保守拒接新写者" in r.getMessage() for r in caplog.records), \
        "拒接必须留下显式诊断，不能只有行为变化"
    assert conv._pending_question is False, "被拒接的写者不得把锁存塞进别人的桶"
    assert conv.current_state is DshState.WORKING, "其余写者的折叠态不受影响"

    # 认证过的新写者会重新尝试准入：腾出安静名额后能被收编（拒接不是永久拉黑）
    conv.retire_scope("inst:busy-0")
    key, admitted = conv.admit_owner("inst:C")
    assert (key, admitted) == ("inst:C", True)
    assert len(conv._scope_keys) <= limit

    # 启停清（读方 start 时调 reset）：分片/注册表/计数一起回初始
    conv.reset()
    assert conv._scopes == {} and conv._scope_keys == {}
    assert conv._scope_overflow_count == 0
    assert conv.current_state is None


def test_rejected_owner_diagnostic_is_per_record_and_throttled(caplog):
    """拒接诊断：每条被拒记录恰一次（准入裁决本身不产诊断）且告警按固定间隔节流。

    一条记录会经两次准入（摄入侧盖归属 + ``handle_record`` 建分片），诊断若挂在
    ``admit_owner`` 上就是每条记录两条告警；被拒写者持续写盘又会逐条刷屏。这里钉住
    两件事：计数按记录推进、告警首次立即可见且此后汇总——诊断不被节流吃掉，也不
    随写者数量无界增长（只用计数器，不存"已拒接 owner"集合）。
    """
    limit = dsh_state.SCOPE_REGISTRY_MAX
    every = dsh_state.SCOPE_REJECT_LOG_EVERY
    conv = DshStateConverger()
    conv.set_online(True)
    for i in range(limit):
        conv.handle_record({"event": "tool/call"}, owner=f"inst:busy-{i}")   # 全忙

    # 准入裁决本身不是诊断点：它一条记录会被调两次，挂在这里诊断就翻倍
    with caplog.at_level("WARNING"):
        key, admitted = conv.admit_owner("inst:new")
    assert (key, admitted) == ("inst:new", False)
    assert conv._scope_overflow_count == 0, "准入裁决不该自己计诊断"
    assert not [r for r in caplog.records if "保守拒接新写者" in r.getMessage()]

    burst = every * 2 + 1
    with caplog.at_level("WARNING"):
        for _ in range(burst):
            conv.handle_record({"event": "AgentStatus", "state": "working"}, owner="inst:new")
    assert conv._scope_overflow_count == burst, \
        f"每条被拒记录恰计一次，实为 {conv._scope_overflow_count}"
    warnings = [r for r in caplog.records if "保守拒接新写者" in r.getMessage()]
    assert len(warnings) == 1 + burst // every, f"告警必须节流：{len(warnings)} 条"
    assert "inst:new" not in conv._scope_keys, "被拒写者不得占名额"
    assert "inst:new" not in conv._scopes, "被拒写者不得新建分片，更不得并桶"
    assert "" not in conv._scopes, "被拒写者不得被并进无主桶"
    assert conv.current_state is DshState.WORKING, "其余写者的折叠态不受影响"


def test_scope_registry_reclaims_quiet_writers_within_bounds():
    """界内安全退役：只回收安静的写者，忙/等待中的写者一个都不许退役。

    DSH 反复重启（每次新 boot UUID = 新写者）会不断占名额；名声额只增不减就会在
    若干次重启后收编不了新写者。回收只挑"没有锁存、也不是 thinking/working"的安静
    写者——退役它们不改变折叠态里任何活着的工作，且它们的下一条记录会重新登记。
    """
    limit = dsh_state.SCOPE_REGISTRY_MAX

    # 全是安静写者：新写者进来时回收最久没动的那一个
    quiet = DshStateConverger()
    quiet.set_online(True)
    for i in range(limit):
        quiet.handle_record({"event": "tool/call"}, owner=f"inst:q-{i}")
        quiet.handle_record({"event": "AgentStatus", "state": "idle"}, owner=f"inst:q-{i}")
    key, admitted = quiet.admit_owner("inst:new")
    assert (key, admitted) == ("inst:new", True), "有安静写者就该收编新写者"
    assert len(quiet._scope_keys) <= limit
    assert "inst:q-0" not in quiet._scope_keys, "退役的应是最久没动的安静写者"
    assert "inst:q-%d" % (limit - 1) in quiet._scope_keys, "最近的安静写者不该被退役"

    # 全是忙/等待写者：一个都不退役（退役会把活着的工作判成结束 = 假 idle/假完成）
    busy = DshStateConverger()
    busy.set_online(True)
    for i in range(limit):
        busy.handle_record({"event": "question/requested", "sessionId": f"s{i}"},
                           owner=f"inst:w-{i}")
    before = dict(busy._scopes)
    key, admitted = busy.admit_owner("inst:x")
    assert admitted is False, "忙/等待中的写者不得为了腾名额被退役"
    assert busy._scopes == before
    assert busy._pending_question is True, "已有写者的锁存原样保留"

    # 反复「新 boot 出现 → 写者死亡退役」不会积满（重启多次也不会）
    cycling = DshStateConverger()
    cycling.set_online(True)
    for round_index in range(limit * 3):
        owner = f"inst:boot-{round_index}"
        cycling.handle_record({"event": "tool/call"}, owner=owner)
        cycling.retire_scope(owner)
    assert len(cycling._scope_keys) <= 1, "退役必须把注册名额还回去"
    assert cycling._scope_overflow_count == 0, "正常启停不该出现拒接"


def test_mirrored_turn_start_and_end_share_one_identity():
    """同一回合被两个写者各写一份 turn/start + turn/end：身份相同 → 只提醒一次。

    会话表是**全写者共享的一张表**：sessionId 没有写者 UUID，不能按写者把计数器
    切开——按写者分表时两份 turn/end 会各拿一个 token，算出两个身份，消费侧去重
    失效（同一回合提醒两次）。这里同时钉住三条边界：别的写者写的镜像 start 不重新
    发号；**同一写者自己的下一回合照常发新号**（否则新回合会被当重复投递吞掉）；
    旧桥没有可用的写者身份（文件身份会随轮转变化）时不做镜像判定。
    """
    end = {"event": "turn/end", "sessionId": "s", "step": "turn:1"}
    start = {"event": "turn/start", "sessionId": "s", "step": "turn:1"}

    # 顺序镜像：A 的 start+end，随后 B 的镜像 start+end
    tracker = TurnCompletionTracker()
    tracker.observe(dict(start), None, "inst:writer-a")
    first = tracker.observe(end, None, "inst:writer-a")
    tracker.observe(dict(start), None, "inst:writer-b")
    second = tracker.observe(end, None, "inst:writer-b")
    assert first is not None
    assert first == second, "同一回合的镜像副本必须落在同一个身份上"

    # 交错镜像：两个写者的 start 先到、两个 end 后到
    inter = TurnCompletionTracker()
    s2 = {"event": "turn/start", "sessionId": "s2", "step": "turn:1"}
    e2 = {"event": "turn/end", "sessionId": "s2", "step": "turn:1"}
    inter.observe(dict(s2), None, "inst:writer-a")
    inter.observe(dict(s2), None, "inst:writer-b")
    a = inter.observe(e2, None, "inst:writer-a")
    b = inter.observe(e2, None, "inst:writer-b")
    assert a is not None and a == b

    # 同一写者自己的下一回合（step 相同）：必须照常发新号
    own = TurnCompletionTracker()
    s3 = {"event": "turn/start", "sessionId": "s3", "step": "turn:1"}
    e3 = {"event": "turn/end", "sessionId": "s3", "step": "turn:1"}
    own.observe(dict(s3), None, "inst:writer-a")
    one = own.observe(e3, None, "inst:writer-a")
    own.observe(dict(s3), None, "inst:writer-a")
    two = own.observe(e3, None, "inst:writer-a")
    assert one != two, "同一写者的新回合影不得被当成镜像副本吞掉"

    # 旧桥（写者归属 = 文件身份，轮转会变）：不做镜像判定
    legacy = TurnCompletionTracker()
    s4 = {"event": "turn/start", "sessionId": "s4", "step": "turn:1"}
    e4 = {"event": "turn/end", "sessionId": "s4", "step": "turn:1"}
    legacy.observe(dict(s4), None, "file:/x/dsh.jsonl:(1, 2)")
    l1 = legacy.observe(e4, None, "file:/x/dsh.jsonl:(1, 2)")
    legacy.observe(dict(s4), None, "file:/x/dsh.jsonl:(3, 4)")   # 轮转后的新文件身份
    l2 = legacy.observe(e4, None, "file:/x/dsh.jsonl:(3, 4)")
    assert l1 != l2, "旧桥轮转后同一写者的新回合不得被当成镜像吞掉"

    # 真正的下一回合仍然分开：step 变了 / 用户新消息（新的回合入口）
    nxt = TurnCompletionTracker()
    nxt.observe({"event": "turn/start", "sessionId": "s5", "step": "turn:1"})
    one = nxt.observe({"event": "turn/end", "sessionId": "s5", "step": "turn:1"})
    nxt.observe({"event": "turn/start", "sessionId": "s5", "step": "turn:2"})
    two = nxt.observe({"event": "turn/end", "sessionId": "s5", "step": "turn:2"})
    assert one != two, "换了 step 的下一回合必须是新身份"

    human = TurnCompletionTracker()
    human.observe({"event": "turn/start", "sessionId": "s6", "step": "5"})
    e1 = human.observe({"event": "turn/end", "sessionId": "s6", "step": "5"})
    human.observe({"event": "user/message", "sessionId": "s6"})   # 真人再问一轮
    e2 = human.observe({"event": "turn/end", "sessionId": "s6", "step": "5"})
    assert e1 != e2, "旧桥（本会话没有回合号记忆）下真人消息仍是新的回合入口"


def test_retire_scope_withdraws_only_that_writer_and_never_reports_success():
    """写者被确认死亡：只撤它自己的状态/锁存，绝不产出 success。"""
    conv = DshStateConverger()
    conv.set_online(True)
    conv.handle_record({"event": "question/requested", "sessionId": "sB"}, owner=_OWNER_B)
    conv.handle_record({"event": "tool/call"}, owner=_OWNER_A)

    out = conv.retire_scope(_OWNER_B)
    assert _states(out) == ["working"], "折叠态从「B 在等」落到「A 还在忙」（A 的状态原样保留）"
    assert conv.current_state is DshState.WORKING
    assert conv._pending_question is False, "退役要把该写者自己的锁存撤掉"

    out = conv.retire_scope(_OWNER_A)
    assert _states(out) == ["idle"], "最后一个写者退役 -> 回基线 idle"
    assert "success" not in _states(out), "写者消失不是回合收尾，绝不产出 success"
    assert _OWNER_B not in conv._scope_keys, "退役的写者不得继续占着注册表名额"

    assert conv.retire_scope("inst:never-seen") == [], "未登记的写者退役是零产出的 no-op"


# ---- 多写者镜像信封的回合号绑定（X1 + F2）----
#
# 镜像信封 = user/message + turn/start + turn/end 被两个写者各写一份。回合号
# （``sourceTurn`` = DSH ``event.data.turn``）**只有 turn/start / turn/end 带**：
# 真实 ``UserMessage`` 是 ``{id, role, content, source}``，没有 turn 字段，所以桥接
# 写出的 user/message 既不带 step 也不带 sourceTurn，而且每回合有多条（真人 1 条 +
# agent.inject() 注入的上下文若干条）。
#
# 于是身份判据分两层：
# - **回合号绑定**（带 sourceTurn 的记录）：``(sessionId, sourceTurn) → token`` 有界
#   绑定表。回合号相同 = 同一回合的另一份副本（镜像/迟到重投），不发新号；回合号不同
#   = 另一个回合（照常发新号，即使来自别的实例——「别的写者写的一律算镜像」会吞掉在
#   另一实例上继续的真实回合）。迟到的旧回合既不得重新发号（否则同一回合两个完成
#   身份 = 重复提醒），也不得把会话表里"当前回合"的记录顶回去（否则新回合的收尾会
#   退回旧 token = 新回合被当成重复投递吞掉）。
# - **旧桥兼容**（无回合号）：user/message 不再覆盖会话表已有的 step / owner /
#   sourceTurn，只在没有回合号记忆时才是新的回合入口；镜像判定退回
#   「(会话, step) + 两个 inst: 写者归属不同」，空 step 不参与比对。


def _real_turn_records(session, turn, *,
                       injections=("agent-instructions", "skill-catalog", "skill-invocation")):
    """一个回合的**真实形状**记录：真人消息 + 注入上下文 + turn/start + turn/end。"""
    step = "turn:%s" % (turn,)
    records = [{"event": "user/message", "sessionId": session, "sourceKind": "user"}]
    records += [{"event": "user/message", "sessionId": session, "sourceKind": kind}
                for kind in injections]
    records.append({"event": "turn/start", "sessionId": session, "step": step,
                    "sourceTurn": str(turn)})
    records.append({"event": "turn/end", "sessionId": session, "step": step,
                    "sourceTurn": str(turn)})
    return records


def _completion_identities(tracker, records, owner):
    return [identity for identity in
            (tracker.observe(dict(record), None, owner) for record in records)
            if identity is not None]


def test_source_turn_binds_mirrored_user_message_to_one_identity():
    """整封真实形状的镜像信封（含多条 user/message）落在同一个完成身份上。"""
    envelope = _real_turn_records("s", 1)
    tracker = TurnCompletionTracker()
    first = _completion_identities(tracker, envelope, "inst:writer-a")
    assert len(first) == 1 and first[0][:3] == ("turn-end", "s", "turn:1")

    mirrored = _completion_identities(tracker, envelope, "inst:writer-b")
    assert mirrored == first, "镜像收尾必须落在同一身份上（镜像起点不得重新发号）"
    assert [tracker.observe(dict(r), None, "inst:writer-a") for r in envelope[:-1]] == \
        [None] * (len(envelope) - 1), "同一回合的重复起点一律零产出"


def test_real_shaped_user_message_never_overwrites_the_recorded_turn():
    """user/message（真实形状，无 step / sourceTurn）只刷新 LRU，不覆盖已有记录。

    它既不重新发号（发号会让镜像副本把同一回合的 token 顶到新值上 → 同一回合两个
    完成身份 → 重复提醒），也不把会话表里的 step / owner / sourceTurn 写坏（写坏会让
    后续真实起点的镜像判据失去比对基准）。
    """
    tracker = TurnCompletionTracker()
    tracker.observe({"event": "turn/start", "sessionId": "s", "step": "turn:7",
                     "sourceTurn": "7"}, None, "inst:a")
    before = tuple(tracker._tokens["s"])
    for kind in ("user", "plugin", "agent-instructions", "skill-catalog", "skill-invocation"):
        tracker.observe({"event": "user/message", "sessionId": "s", "sourceKind": kind},
                        None, "inst:a")
    assert tuple(tracker._tokens["s"]) == before, \
        "user/message 不得改写会话表里已记录的 (token, step, owner, sourceTurn)"
    assert list(tracker._tokens) == ["s"], "至多刷新 LRU，不得为它新建行"


def test_real_shaped_mirrored_user_message_is_a_duplicate_start():
    """真实形状的镜像 user/message 必须被认成重复起点（否则收敛器会被它推回忙）。

    判定只用「两个写者都是插件 boot UUID 且归属不同」——真实 user/message 不带 step，
    空 step 不参与比对；没有可用的写者身份时绝不猜。
    """
    tracker = TurnCompletionTracker()
    tracker.observe({"event": "user/message", "sessionId": "s", "sourceKind": "user"},
                    None, "inst:a")
    tracker.observe({"event": "turn/start", "sessionId": "s", "step": "turn:7",
                     "sourceTurn": "7"}, None, "inst:a")
    duplicate = {"event": "user/message", "sessionId": "s", "sourceKind": "user"}
    assert tracker.is_duplicate_start(dict(duplicate), "inst:a") is False, \
        "同一写者自己的 user/message 不是镜像"
    assert tracker.is_duplicate_start(dict(duplicate), "inst:b") is True, \
        "另一个实例写的同一条真人消息是镜像副本"
    assert tracker.is_duplicate_start(dict(duplicate), "") is False, \
        "写者身份不行（无 inst:）时不做镜像判定"


def test_redelivered_old_turn_does_not_steal_the_current_turn_identity():
    """F2 实测序列：A:start7→A:end7→A:start8→B:start7→B:end7→A:end8。

    修复前 turn7 拿到两个不同的完成身份（A 的收尾 token1、B 的收尾 token3），用户被
    提醒两次；turn8 的收尾还退回旧 token。
    """
    tracker = TurnCompletionTracker()

    def start(turn, owner):
        return tracker.observe({"event": "turn/start", "sessionId": "s",
                                "step": "turn:%s" % turn, "sourceTurn": str(turn)}, None, owner)

    def end(turn, owner):
        return tracker.observe({"event": "turn/end", "sessionId": "s",
                                "step": "turn:%s" % turn, "sourceTurn": str(turn)}, None, owner)

    start(7, "inst:a")
    seven = end(7, "inst:a")
    start(8, "inst:a")
    start(7, "inst:b")
    seven_again = end(7, "inst:b")
    eight = end(8, "inst:a")

    assert seven == seven_again, "同一回合（turn7）的镜像收尾必须落在同一身份上"
    assert seven[:3] == ("turn-end", "s", "turn:7")
    assert eight != seven and eight[:3] == ("turn-end", "s", "turn:8"), \
        "turn8 必须有自己的身份（旧回合重投不得推进当前回合状态）"


def test_older_turn_first_seen_late_does_not_roll_back_current_turn():
    """旧回合**首次**被读到（起点曾被回填跳过）时：给它稳定身份，但不回退当前回合。"""
    tracker = TurnCompletionTracker()
    tracker.observe({"event": "turn/start", "sessionId": "s", "step": "turn:8",
                     "sourceTurn": "8"}, None, "inst:a")
    eight = tracker.observe({"event": "turn/end", "sessionId": "s", "step": "turn:8",
                             "sourceTurn": "8"}, None, "inst:a")
    tracker.observe({"event": "turn/start", "sessionId": "s", "step": "turn:7",
                     "sourceTurn": "7"}, None, "inst:b")
    seven = tracker.observe({"event": "turn/end", "sessionId": "s", "step": "turn:7",
                             "sourceTurn": "7"}, None, "inst:b")
    assert seven != eight
    assert tracker.observe({"event": "turn/end", "sessionId": "s", "step": "turn:8"}) == eight, \
        "当前回合仍是 turn8：无回合号的旧式收尾不得退回旧回合的 token"


def test_source_turn_difference_is_a_legit_new_turn_on_another_writer():
    """两侧回合号不同 → 不是镜像：同 (会话, step) + 另一个写者仍必须发新号。

    这是「user/message 来自别的写者就算镜像」那条被否掉的口径的反例：回合号不同说明
    是**另一回合**（在另一个实例上继续的真实回合），吞掉它就是漏报完成。
    """
    tracker = TurnCompletionTracker()
    tracker.observe({"event": "user/message", "sessionId": "s", "step": "turn:1",
                     "sourceTurn": "7"}, None, "inst:writer-a")
    first = tracker.observe({"event": "turn/end", "sessionId": "s", "step": "turn:1",
                             "sourceTurn": "7"}, None, "inst:writer-a")
    assert first == ("turn-end", "s", "turn:1", 1)

    tracker.observe({"event": "user/message", "sessionId": "s", "step": "turn:1",
                     "sourceTurn": "8"}, None, "inst:writer-b")
    second = tracker.observe({"event": "turn/end", "sessionId": "s", "step": "turn:1",
                              "sourceTurn": "8"}, None, "inst:writer-b")
    assert second == ("turn-end", "s", "turn:1", 2), "另一回合必须重新发号"


def test_source_turn_rule_falls_back_when_either_side_lacks_it():
    """任一缺失回合号 → 逐条退回原判据（旧桥兼容；旧行为逐条不变）。"""
    # ① 两侧都没有（旧桥）：真人消息仍是新的回合入口
    plain = TurnCompletionTracker()
    plain.observe({"event": "turn/start", "sessionId": "s1", "step": "5"})
    first = plain.observe({"event": "turn/end", "sessionId": "s1", "step": "5"})
    assert plain.observe({"event": "user/message", "sessionId": "s1", "step": "5"}) is None
    assert plain.observe({"event": "turn/end", "sessionId": "s1", "step": "5"}) != first

    # ② 入来的带回合号、已跟踪的不带：不猜，退回 (会话, step) + inst: 归属的原判据
    legacy = TurnCompletionTracker()
    legacy.observe({"event": "turn/start", "sessionId": "s2", "step": "turn:1"}, None, "inst:a")
    assert legacy.is_duplicate_start(
        {"event": "turn/start", "sessionId": "s2", "step": "turn:1", "sourceTurn": "9"},
        "inst:b") is True, "缺一侧时原判据照旧生效（回合号不参与）"
    # user/message 现在同样是镜像判定的对象（真实形状不带 step → 空 step 不参与比对，
    # 剩下的判据就是「另一个 inst: 写者在写同一条会话」）。旧实现直接放过它，于是镜像
    # 副本的起点把同一回合的 token 顶掉 → 同一回合两个完成身份。
    assert legacy.is_duplicate_start(
        {"event": "user/message", "sessionId": "s2", "step": "turn:1", "sourceTurn": "9"},
        "inst:b") is True
    assert legacy.is_duplicate_start(
        {"event": "user/message", "sessionId": "s2", "sourceKind": "user"},
        "inst:b") is True, "真实形状（无 step）落在同一条镜像判据上"

    # ③ 已跟踪的带回合号、入来的不带：user/message 不发号、不覆盖已绑定的回合身份
    mixed = TurnCompletionTracker()
    mixed.observe({"event": "turn/start", "sessionId": "s3", "step": "turn:5",
                   "sourceTurn": "5"}, None, "inst:a")
    one = mixed.observe({"event": "turn/end", "sessionId": "s3", "step": "turn:5",
                         "sourceTurn": "5"}, None, "inst:a")
    mixed.observe({"event": "user/message", "sessionId": "s3", "sourceKind": "user"},
                  None, "inst:a")
    assert mixed.observe({"event": "turn/end", "sessionId": "s3", "step": "turn:5",
                          "sourceTurn": "5"}, None, "inst:a") == one, \
        "真实 user/message 不带回合号 → 不得把同回合的重投递算成新回合"
    # 下一回合由**它自己的回合号**发号（不靠 user/message）
    mixed.observe({"event": "turn/start", "sessionId": "s3", "step": "turn:6",
                   "sourceTurn": "6"}, None, "inst:a")
    assert mixed.observe({"event": "turn/end", "sessionId": "s3", "step": "turn:6",
                          "sourceTurn": "6"}, None, "inst:a") != one


def test_real_envelope_successive_turns_stay_distinct_on_every_writer():
    """真实形状下连续两回合各提醒一次：镜像副本不得把下一回合吞成重复投递。"""
    tracker = TurnCompletionTracker()
    seven = _completion_identities(tracker, _real_turn_records("s", 7), "inst:a")
    eight = _completion_identities(tracker, _real_turn_records("s", 8), "inst:a")
    assert len(seven) == 1 and len(eight) == 1
    assert seven != eight, "同一会话的下一回合必须有不同身份"
    assert seven[0][:3] == ("turn-end", "s", "turn:7")
    assert eight[0][:3] == ("turn-end", "s", "turn:8")
    assert _completion_identities(tracker, _real_turn_records("s", 8), "inst:b") == eight, \
        "同回合的镜像收尾仍绑在同一身份上"


def test_turn_binding_table_is_bounded():
    """(会话, 回合号) 绑定表有界：换成千上万个回合不得把内存撑成无界。"""
    tracker = TurnCompletionTracker()
    for i in range(dsh_state.TURN_BINDING_MEMORY + 64):
        tracker.observe({"event": "turn/start", "sessionId": "s", "step": "turn:%d" % i,
                         "sourceTurn": str(i)}, None, "inst:a")
    assert len(tracker._turn_tokens) <= dsh_state.TURN_BINDING_MEMORY, \
        f"绑定表必须有界：{len(tracker._turn_tokens)} 条"
    assert ("s", str(dsh_state.TURN_BINDING_MEMORY + 63)) in tracker._turn_tokens, \
        "最近的回合必须在表内"


# ---- 注入上下文的白名单（X2）----

def test_injected_user_message_kinds_are_not_human_input():
    """只有 sourceKind ∈ ('user', '') 算真人输入；其余注入 kind 整体忽略。

    agent.inject() 注入的上下文（plugin / agent-instructions / skill-catalog /
    skill-invocation …）每轮 4-5 条，若按真人消息处理，「对话开始」触发会被注入
    文案污染（同 tick 批量应用 + 边沿去重 → 时触时不触）。
    """
    from pet.agent_link.dsh_state import is_human_user_message

    for kind in ("plugin", "agent-instructions", "skill-catalog", "skill-invocation"):
        assert is_human_user_message({"event": "user/message", "sourceKind": kind}) is False
    assert is_human_user_message({"event": "user/message", "sourceKind": "user"}) is True
    assert is_human_user_message({"event": "user/message"}) is True, "旧桥不写 sourceKind"


@pytest.mark.parametrize("kind", ["plugin", "agent-instructions", "skill-catalog",
                                  "skill-invocation"])
def test_injected_user_message_kinds_do_not_start_a_conversation(kind):
    conv = DshStateConverger()
    conv.set_online(True)                                            # idle 基线
    out = _feed(conv, {"event": "user/message", "sourceKind": kind,
                       "text": "<system-reminder> 技能目录……", "sessionId": "s1"})
    assert [item for item in out if item[0] == "user_message"] == []
    assert conv.current_state is DshState.IDLE, "注入上下文不得进状态机"


# ---- 失败回合的收尾（F4）----

def test_error_turn_end_maps_to_error_terminal():
    """``reasonKind=error`` 的 turn/end 落错误终态，与 aborted（静默回待机）区分。"""
    assert map_event_to_state({"event": "turn/end", "reasonKind": "error"}) is DshState.ERROR
    assert map_event_to_state({"event": "turn/end", "reasonKind": "aborted"}) is DshState.IDLE
    assert map_event_to_state({"event": "turn/end", "reasonKind": "completed"}) is DshState.SUCCESS
    assert map_event_to_state({"event": "turn/end"}) is DshState.SUCCESS, "旧桥：无 reasonKind"


def test_error_turn_end_never_converges_to_success():
    """失败回合不得收敛 success（桥端重试<4 或有工具成功时不写 execution/failed）。

    此前只有 execution/failed 会打抑制标记，重试中断或"有工具成功"的错误回合拿不到
    任何标记，于是 turn/end 一路收敛 success → 绿点 + done 音效 + 「干完活啦」。
    """
    conv = DshStateConverger()
    conv.set_online(True)
    out = _feed(conv, {"event": "user/message", "sourceKind": "user", "sessionId": "s"},
                {"event": "turn/end", "sessionId": "s", "reasonKind": "error"})
    assert "success" not in _states(out), out
    assert conv.current_state is DshState.ERROR


def test_error_turn_end_yields_no_completion_identity():
    tracker = TurnCompletionTracker()
    tracker.observe({"event": "turn/start", "sessionId": "s", "step": "turn:1",
                     "sourceTurn": "1"}, None, "inst:a")
    assert tracker.observe({"event": "turn/end", "sessionId": "s", "step": "turn:1",
                            "sourceTurn": "1", "reasonKind": "error"}, None, "inst:a") is None


def test_error_turn_end_produces_sourced_edge_and_clears_latches():
    """F4 呈现链：error 回合的 turn/end 必须带 source 产出（消费侧据此注入 error），
    并解除本写者的审批/问题锁存——否则锁存闸会把这条收尾吞掉，岛一直等待。"""
    conv = DshStateConverger()
    conv.handle_record({"event": "turn/start", "sessionId": "s"})
    conv.handle_record({"event": "approval/request", "sessionId": "s"})
    out = conv.handle_record({"event": "turn/end", "sessionId": "s", "reasonKind": "error"})
    assert conv.current_state is DshState.ERROR
    assert out and out[-1][2:] == ("error", "turn/end"), out
    # 锁存已解除：后续新回合起点照常推进（不被旧锁存吞掉）
    assert conv.handle_record({"event": "tool/call", "sessionId": "s", "tool": "bash"})
