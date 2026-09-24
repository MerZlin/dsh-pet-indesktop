"""Session-isolated model-access-failure streak tracking for Pet.

模型访问失败 = 上游服务端限流/过载类错误（真实状态码作为数据字面量匹配，
不体现在命名上），以及上游响应连接/超时类故障（TIMEOUT/ETIMEDOUT/连接断等）。
识别口径与 bridge 端 ``isModelAccessError`` 保持一致（两端同仓同版本发布），
保证桌宠侧按 session 统计的连续计数兜底与桥端写出的 model_access 事件对齐。"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from .agent_event_protocol import AgentEvent

_MODEL_ACCESS_CODES = {"429", "RATE_LIMIT", "TOO_MANY_REQUESTS", "RESOURCE_EXHAUSTED"}

# 连接/超时类失败错误码（与 bridge 端 MODEL_ACCESS_CONN_CODES 对齐）：
# 真实事故（2026-09-11）：llm/retry 连续 5 次 errorCode=TIMEOUT（"upstream
# response headers timed out before streaming started"）却零提醒——限流码之外的
# 重试异常在两端都不进「模型访问失败」计数。超时/连接断同属「本次模型请求未
# 成功、进入重试链」的异常，必须计入连续计数供提醒兜底。
_MODEL_ACCESS_CONN_CODES = frozenset(
    {
        "TIMEOUT",
        "REQUEST_TIMEOUT",
        "UPSTREAM_TIMEOUT",
        "ETIMEDOUT",
        "ESOCKETTIMEDOUT",
        "ECONNABORTED",
        "ECONNRESET",
        "ECONNREFUSED",
        "EPIPE",
        "EAI_AGAIN",
        "ENETUNREACH",
        "EHOSTUNREACH",
        "NETWORK_ERROR",
    }
)

_MODEL_ACCESS_CONN_RE = re.compile(
    r"\btimed?\s?out\b|timed out before|connection (reset|refused|aborted|closed|reset by peer)|"
    r"network (error|unreachable|is unreachable)|socket hang up|eai_again|read ?ec 0|econnreset|etimedout",
    re.IGNORECASE,
)


@dataclass
class RetryStreak:
    count: int = 0
    provider: str = ""
    model: str = ""


class ModelAccessTracker:
    def __init__(self) -> None:
        self._streaks: dict[tuple[str, str], RetryStreak] = {}

    @staticmethod
    def _key(event: AgentEvent) -> tuple[str, str] | None:
        # Missing IDs are deliberately not merged across events.
        if not event.session_id:
            return None
        return (event.source, event.session_id)

    @staticmethod
    def is_model_access(event: AgentEvent) -> bool:
        data = event.data
        failure = data.get("failure") if isinstance(data.get("failure"), dict) else data
        code = str(failure.get("code") or data.get("errorCode") or "").strip().upper()
        message = str(failure.get("message") or data.get("errorMessage") or "")
        if code in _MODEL_ACCESS_CODES:
            return True
        if re.search(r"\b429\b|rate[ -]?limit|too many requests", message, re.I):
            return True
        if code in _MODEL_ACCESS_CONN_CODES or _MODEL_ACCESS_CONN_RE.search(message):
            return True
        return False

    def consume(self, event: AgentEvent) -> dict[str, Any] | None:
        key = self._key(event)
        if event.event.lower() == "llm/retry" and key and self.is_model_access(event):
            data = event.data
            streak = self._streaks.setdefault(key, RetryStreak())
            streak.count += 1
            streak.provider = str(data.get("provider") or streak.provider)
            streak.model = str(data.get("model") or streak.model)
            return {"sessionId": event.session_id, "consecutiveRetryCount": streak.count, "provider": streak.provider, "model": streak.model}
        if key and self._resets(event):
            self._streaks.pop(key, None)
        return None

    @staticmethod
    def _resets(event: AgentEvent) -> bool:
        name = event.event.lower()
        if name in {"turn/start", "turn/end", "tool/call", "assistant/message", "assistant/chunk", "agent/status"}:
            return True
        if name == "tool/result":
            return event.data.get("ok", True) not in (False, 0, "false", "error")
        if name in {"error", "agent/request-error", "llm_error"}:
            return not ModelAccessTracker.is_model_access(event)
        return False

    def clear(self) -> None:
        """清空全部 streak（禁用联动/全局重置用，见 _clear_model_access_alerts）。"""
        self._streaks.clear()
