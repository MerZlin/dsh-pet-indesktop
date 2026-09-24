# -*- coding: utf-8 -*-
"""语义事件字段契约：归一化产物必须携带真实上游事件名。

回归背景（2026-09-10 真实链路取证）：`SemanticEvent` 基类没有 `event` 字段
（只有 `LifecycleEvent` 定义了它），而 `ModelAccessTracker.consume`/`_resets`
依赖 `event.event` 判定「llm/retry 限流计数」与「哪些事件复位计数」。
结果是真实链路上除 turn/* 之外的每个语义事件都抛 AttributeError——
异常在 Qt 槽里被吞掉只打印不中断，导致 `_model_access_retry_counts` 静默恒为空：
model_access 记录未带 consecutiveRetryCount 时兜底计数恒为 0，语义上「连续
限流次数」退化成「桌宠收到几次」。线上 125 条真实记录触发 77 次该异常。

修复口径：归一化点（normalize_event）把上游事件名写进基类字段 `event`
（并归一化 AgentStatus → agent/status），消费端即可按真实语义判定。
"""

from __future__ import annotations

from pet.agent_event_normalizer import normalize_event
from pet.model_access_tracker import ModelAccessTracker


def _norm(event: str, **data):
    ev = normalize_event({"event": event, "agent": "dsh", "sessionId": "s-1", **data})
    assert ev is not None, f"{event} 必须被归一化"
    return ev


def test_semantic_events_carry_upstream_event_name():
    """每个语义事件都带真实上游事件名（基类字段，不再是某个子类独有）。"""
    assert _norm("tool/call", tool="pwsh").event == "tool/call"
    assert _norm("tool/result", tool="pwsh", ok=False).event == "tool/result"
    assert _norm("tool/result", tool="pwsh", ok=True).event == "tool/result"
    assert _norm("llm/retry", retry=1).event == "llm/retry"
    assert _norm("execution/failed", failureType="tool_failed").event == "execution/failed"
    assert _norm("assistant/message").event == "assistant/message"
    assert _norm("turn/start").event == "turn/start"


def test_agent_status_record_normalizes_to_canonical_name():
    """DSH 状态记录写的是 'AgentStatus'，语义名必须是 'agent/status'（复位词表用它）。"""
    assert _norm("AgentStatus", state="idle").event == "agent/status"


def test_consume_counts_consecutive_model_access_retries():
    """连续限流重试必须累加，并带上 session 与计数（连续计数兜底的唯一来源）。"""
    tracker = ModelAccessTracker()
    for i in (1, 2, 3):
        out = tracker.consume(_norm("llm/retry", retry=i, errorCode="RATE_LIMIT", errorMessage="429 too many requests"))
        assert out is not None, "限流重试必须产出一份 streak"
        assert out["consecutiveRetryCount"] == i
        assert out["sessionId"] == "s-1"
    # 再喂一条仍在连续链上 → 计数继续累加（证明 streak 按 session 留存）
    out = tracker.consume(_norm("llm/retry", retry=4, errorCode="RATE_LIMIT", errorMessage="429 too many requests"))
    assert out["consecutiveRetryCount"] == 4


def test_consume_does_not_count_non_model_access_retry():
    tracker = ModelAccessTracker()
    out = tracker.consume(_norm("llm/retry", retry=1, errorCode="server_error", errorMessage="upstream boom"))
    assert out is None
    # 随后的真实限流重试必须从 1 开始（前面那条不该建 streak）
    out = tracker.consume(_norm("llm/retry", retry=2, errorCode="RATE_LIMIT", errorMessage="429 too many requests"))
    assert out["consecutiveRetryCount"] == 1


def test_consume_counts_consecutive_timeout_retries():
    """连接/超时类重试必须累计（真实事故：5 次 TIMEOUT 零提醒）。

    回归（2026-09-11）：会话 session-b5b4f120… 连续 5 次 llm/retry 均为
    errorCode=TIMEOUT（"upstream response headers timed out before streaming
    started"）后恢复正常，旧实现 is_model_access 只认限流码，连续计数永远为空，
    桌宠侧重试异常提醒完全不触发。TIMEOUT 类重试同属「本次模型请求未成功」，
    必须进入连续计数供提醒兜底。
    """
    tracker = ModelAccessTracker()
    for i in (1, 2, 3):
        out = tracker.consume(_norm("llm/retry", retry=i, errorCode="TIMEOUT", errorMessage="upstream response headers timed out before streaming started"))
        assert out is not None, "TIMEOUT 重试必须产出一份 streak"
        assert out["consecutiveRetryCount"] == i
    out = tracker.consume(_norm("llm/retry", retry=4, errorCode="TIMEOUT", errorMessage="upstream response headers timed out before streaming started"))
    assert out["consecutiveRetryCount"] == 4


def test_consume_counts_connection_failure_message_without_code():
    """错误码缺失但消息含连接断词时也计数（消息兜底）。"""
    tracker = ModelAccessTracker()
    out = tracker.consume(_norm("llm/retry", retry=1, errorMessage="upstream connection reset by peer"))
    assert out is not None
    assert out["consecutiveRetryCount"] == 1


def test_consume_resets_streak_on_recovery_events():
    """恢复/交互类事件必须复位连续限流计数（否则模型访问失败提示会一直叠加）。"""
    resetting = (
        ("tool/call", {"tool": "pwsh"}),
        ("turn/start", {}),
        ("assistant/message", {}),
        ("AgentStatus", {"state": "idle"}),
        ("tool/result", {"tool": "pwsh", "ok": True}),
        ("turn/end", {}),
    )
    for event_name, extra in resetting:
        tracker = ModelAccessTracker()  # 每个恢复事件独立验证，互不残留
        streak = tracker.consume(_norm("llm/retry", retry=1, errorCode="RATE_LIMIT", errorMessage="429 too many requests"))
        assert streak["consecutiveRetryCount"] == 1, f"{event_name} 之前应已计数"
        tracker.consume(_norm(event_name, **extra))
        # 复位后重新计数必须从 1 开始（未复位会累加到 2）
        streak = tracker.consume(_norm("llm/retry", retry=2, errorCode="RATE_LIMIT", errorMessage="429 too many requests"))
        assert streak["consecutiveRetryCount"] == 1, f"{event_name} 必须复位连续限流计数"


def test_non_model_access_error_also_resets():
    tracker = ModelAccessTracker()
    streak = tracker.consume(_norm("llm/retry", retry=1, errorCode="RATE_LIMIT", errorMessage="429"))
    assert streak["consecutiveRetryCount"] == 1
    tracker.consume(_norm("error", errorCode="EACCES", errorMessage="permission denied"))
    streak = tracker.consume(_norm("llm/retry", retry=2, errorCode="RATE_LIMIT", errorMessage="429"))
    assert streak["consecutiveRetryCount"] == 1


def test_model_access_error_itself_does_not_reset():
    """限流错误的 error 事件不得复位自己（否则计数会被自己清零）。"""
    tracker = ModelAccessTracker()
    streak = tracker.consume(_norm("llm/retry", retry=1, errorCode="RATE_LIMIT", errorMessage="429"))
    assert streak["consecutiveRetryCount"] == 1
    tracker.consume(_norm("agent/request-error", errorCode="RATE_LIMIT", errorMessage="429 too many requests"))
    streak = tracker.consume(_norm("llm/retry", retry=2, errorCode="RATE_LIMIT", errorMessage="429"))
    assert streak["consecutiveRetryCount"] == 2
