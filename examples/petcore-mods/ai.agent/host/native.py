"""原生 Agent transcript 解析：只做格式到统一六态的映射，没有文件 IO、没有 Qt。

每个解析器返回 ``(state, tool)``；``state`` 为空表示这条记录不认识，调用方跳过。
统一的六态词汇与事件名映射仍以 ``protocol.py`` 为准。
"""

from __future__ import annotations

import json

from .protocol import EVENT_STATES, SIX_STATES

_STATES = frozenset(SIX_STATES)


def _explicit(record: dict) -> str:
    value = str(record.get("state") or "").strip().lower()
    return value if value in _STATES else ""


def _event_state(record: dict) -> str:
    event = str(record.get("event") or record.get("type") or "").strip()
    if not event:
        return ""
    return EVENT_STATES.get(event) or EVENT_STATES.get(event.lower()) or ""


def _cursor_content(record: dict):
    message = record.get("message")
    if isinstance(message, dict):
        message = message.get("content")
    return message if isinstance(message, list) else []


def parse_cursor_line(record: dict) -> tuple[str, str]:
    """Cursor agent-transcripts：user→thinking、assistant+tool_use→working、纯文本→idle。"""
    if not isinstance(record, dict):
        return "", ""
    explicit = _explicit(record)
    if explicit:
        return explicit, ""
    role = str(record.get("role") or "").strip().lower()
    if role == "user":
        return "thinking", ""
    if role == "assistant":
        for block in _cursor_content(record):
            if isinstance(block, dict) and block.get("type") == "tool_use":
                return "working", str(block.get("name") or "").strip()
        return "idle", ""
    return _event_state(record), ""


def parse_codex_line(record: dict) -> str:
    """rollout jsonl：只认 ``event_msg.payload.type`` 的生命周期记录。"""
    if not isinstance(record, dict):
        return ""
    explicit = _explicit(record)
    if explicit:
        return explicit
    if str(record.get("type") or "") != "event_msg":
        return ""
    payload = record.get("payload")
    kind = str(payload.get("type") or "") if isinstance(payload, dict) else ""
    return {
        "task_started": "working",
        "user_message": "thinking",
        "agent_reasoning": "thinking",
        "agent_message": "working",
        "task_complete": "idle",
        "turn_aborted": "idle",
        "error": "error",
    }.get(kind, "")


def parse_opencode_row(event_type: str, data) -> tuple[str, str]:
    """OpenCode event 表：message / part / session 三类记录。"""
    if isinstance(data, str):
        try:
            data = json.loads(data)
        except (TypeError, ValueError):
            return "", ""
    if not isinstance(data, dict):
        return "", ""
    kind = str(event_type or "")
    if kind.startswith("message.updated"):
        role = str((data.get("info") or {}).get("role") or "")
        return ("thinking", "") if role == "user" else ("", "")
    if kind.startswith("message.part.updated"):
        part = data.get("part") if isinstance(data.get("part"), dict) else {}
        part_type = str(part.get("type") or "")
        if part_type == "step-finish" and str(part.get("reason") or "") == "tool-calls":
            return "", ""
        if part_type == "tool":
            return "working", str(part.get("tool") or "").strip()
        return {
            "step-start": ("working", ""),
            "reasoning": ("thinking", ""),
            "step-finish": ("idle", ""),
        }.get(part_type, ("", ""))
    if kind.startswith("session.created"):
        return "idle", ""
    return "", ""
