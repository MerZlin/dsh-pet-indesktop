"""Session-isolated rate-limit streak tracking for Pet."""
from __future__ import annotations
from dataclasses import dataclass
from typing import Any
import re
from .agent_event_protocol import AgentEvent

_RATE_CODES = {"429", "RATE_LIMIT", "TOO_MANY_REQUESTS", "RESOURCE_EXHAUSTED"}

@dataclass
class RetryStreak:
    count: int = 0
    provider: str = ""
    model: str = ""

class RateLimitTracker:
    def __init__(self) -> None:
        self._streaks: dict[tuple[str, str], RetryStreak] = {}

    @staticmethod
    def _key(event: AgentEvent) -> tuple[str, str] | None:
        # Missing IDs are deliberately not merged across events.
        if not event.session_id:
            return None
        return (event.source, event.session_id)

    @staticmethod
    def is_rate_limit(event: AgentEvent) -> bool:
        data = event.data
        failure = data.get("failure") if isinstance(data.get("failure"), dict) else data
        code = str(failure.get("code") or data.get("errorCode") or "").strip().upper()
        message = str(failure.get("message") or data.get("errorMessage") or data.get("errorText") or "")
        return code in _RATE_CODES or bool(re.search(r"\b429\b|rate[ -]?limit|too many requests", message, re.I))

    def consume(self, event: AgentEvent) -> dict[str, Any] | None:
        key = self._key(event)
        if event.event.lower() == "llm/retry" and key and self.is_rate_limit(event):
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
        if name in {"turn/start", "turn/end", "tool/call", "assistant/message", "assistant/chunk", "agent/status"}: return True
        if name == "tool/result":
            return event.data.get("ok", True) not in (False, 0, "false", "error")
        if name in {"error", "agent/request-error", "llm_error"}:
            return not RateLimitTracker.is_rate_limit(event)
        return False

    def count(self, source: str, session_id: str) -> int:
        item = self._streaks.get((source, session_id))
        return item.count if item else 0

    def reset(self, source: str, session_id: str) -> None:
        self._streaks.pop((source, session_id), None)
