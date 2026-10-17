"""Hermes-to-dsh-pet Agent Link bridge.

Writes activity states and identity-only human-approval events to one JSONL
file. Prompts, messages, tool arguments/results, paths, and credentials are not
persisted; api_server chat activity is excluded to keep the two UI paths apart.
"""
from __future__ import annotations

import json
import os
import threading
import time
import uuid
from pathlib import Path
from typing import Any

_AGENT_KEY = "hermes"
_INSTANCE_ID = uuid.uuid4().hex
_LOCK = threading.RLock()
_SESSION_STATES: dict[tuple[str, str], str] = {}
_ACTIVE_TOOLS: dict[tuple[str, str], set[str]] = {}
_FOREGROUND_TURNS: set[tuple[str, str]] = set()
_TURN_SESSION_IDS: dict[tuple[str, str], set[str]] = {}
_API_SERVER_SESSIONS: set[str] = set()
_LAST_EMITTED: tuple[str, str] | None = None


def _events_path() -> Path:
    override = os.environ.get("DSH_PET_HERMES_EVENTS", "").strip()
    if override:
        return Path(override).expanduser()
    appdata = os.environ.get("APPDATA", "").strip()
    if appdata:
        return Path(appdata) / "dsh-pet-standalone-webm-chat" / "agent-events" / "hermes.jsonl"
    return Path.home() / ".config" / "dsh-pet-standalone-webm-chat" / "agent-events" / "hermes.jsonl"


def _aggregate_state() -> str:
    states = tuple(_SESSION_STATES.values())
    if "working" in states:
        return "working"
    if "thinking" in states:
        return "thinking"
    return "idle"


def _append_event(state: str, tool: str = "") -> None:
    """Best-effort append; failures never interfere with the Hermes agent loop."""
    global _LAST_EMITTED
    state = state if state in {"thinking", "working", "attention", "error", "idle", "sleeping"} else "idle"
    tool = str(tool or "").strip().lower()[:48]
    fingerprint = (state, tool)
    with _LOCK:
        if fingerprint == _LAST_EMITTED:
            return
        path = _events_path()
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            record: dict[str, Any] = {
                "ts": time.time(), "agent": _AGENT_KEY, "inst": _INSTANCE_ID, "state": state,
            }
            if tool and state == "working":
                record["tool"] = tool
            payload = (
                json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n"
            ).encode("utf-8")
            # One append-only write avoids interleaving JSON rows between concurrent hooks.
            fd = os.open(path, os.O_APPEND | os.O_CREAT | os.O_WRONLY, 0o600)
            try:
                os.write(fd, payload)
            finally:
                os.close(fd)
        except Exception:
            return
        _LAST_EMITTED = fingerprint


def _session_key(kwargs: dict[str, Any]) -> tuple[str, str]:
    # Compression can rotate session_id mid-turn; turn_id stays stable.
    turn_id = str(kwargs.get("turn_id") or "")
    return ("turn", turn_id) if turn_id else ("session", _session_id(kwargs))


def _session_id(kwargs: dict[str, Any]) -> str:
    return str(kwargs.get("session_id") or kwargs.get("task_id") or "default")


def _note_session_alias(kwargs: dict[str, Any]) -> None:
    _TURN_SESSION_IDS.setdefault(_session_key(kwargs), set()).add(_session_id(kwargs))


def _is_foreground_tool(kwargs: dict[str, Any]) -> bool:
    # Persistence-disabled background-review forks share a parent's session ID,
    # emit tool hooks, but deliberately skip pre_llm_call and on_session_end.
    # Admit only lifecycle-attested turns, not every tool in that session.
    # Missing turn IDs retain compatibility with older/standalone hook callers.
    return not kwargs.get("turn_id") or _session_key(kwargs) in _FOREGROUND_TURNS


def _is_api_server_session(kwargs: dict[str, Any], *, clear: bool = False) -> bool:
    """Suppress status events for pet Chat requests, including their tool callbacks."""
    session_id = str(kwargs.get("session_id") or "")[:128]
    platform = str(kwargs.get("platform") or "").lower()
    parent_id = str(kwargs.get("parent_session_id") or "")[:128]
    with _LOCK:
        suppressed = platform == "api_server" or bool(
            (session_id and session_id in _API_SERVER_SESSIONS)
            or (parent_id and parent_id in _API_SERVER_SESSIONS)
        )
        if suppressed and session_id:
            _API_SERVER_SESSIONS.add(session_id)
        if clear and session_id:
            _API_SERVER_SESSIONS.discard(session_id)
        return suppressed


def _set_session_state(state: str, kwargs: dict[str, Any], tool: str = "") -> None:
    with _LOCK:
        _note_session_alias(kwargs)
        _SESSION_STATES[_session_key(kwargs)] = state
        aggregate = _aggregate_state()
        _append_event(aggregate, tool if aggregate == "working" else "")


def _append_human_input_event(event: str, kwargs: dict[str, Any]) -> None:
    """Write only the approval identity needed to open/close the pet's generic hint."""
    request_id = str(kwargs.get("request_id") or "").strip()[:128]
    if not request_id:
        return  # The pet requires a stable identity to close a pending approval safely.
    session_id = str(kwargs.get("session_id") or "").strip()[:128]
    record: dict[str, Any] = {
        "ts": time.time(), "agent": _AGENT_KEY, "inst": _INSTANCE_ID, "event": event,
        "requestId": request_id,
    }
    if session_id:
        record["sessionId"] = session_id
    payload = (
        json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n"
    ).encode("utf-8")
    with _LOCK:
        try:
            path = _events_path()
            path.parent.mkdir(parents=True, exist_ok=True)
            fd = os.open(path, os.O_APPEND | os.O_CREAT | os.O_WRONLY, 0o600)
            try:
                os.write(fd, payload)
            finally:
                os.close(fd)
        except Exception:
            return


def _on_pre_llm_call(**kwargs: Any) -> None:
    if _is_api_server_session(kwargs):
        return
    with _LOCK:
        _FOREGROUND_TURNS.add(_session_key(kwargs))
        _set_session_state("thinking", kwargs)


def _on_pre_tool_call(**kwargs: Any) -> None:
    if _is_api_server_session(kwargs):
        return
    tool = kwargs.get("tool_name")
    with _LOCK:
        if not _is_foreground_tool(kwargs):
            return
        _ACTIVE_TOOLS.setdefault(_session_key(kwargs), set()).add(
            str(kwargs.get("tool_call_id") or "legacy")
        )
        _set_session_state("working", kwargs, str(tool or ""))


def _on_post_tool_call(**kwargs: Any) -> None:
    if _is_api_server_session(kwargs):
        return
    status = str(kwargs.get("status") or "").lower()
    failed = (
        status in {"error", "failed", "blocked", "timeout"}
        or bool(kwargs.get("error_type"))
    )
    with _LOCK:
        if not _is_foreground_tool(kwargs):
            return
        key = _session_key(kwargs)
        _note_session_alias(kwargs)
        active = _ACTIVE_TOOLS.get(key, set())
        active.discard(str(kwargs.get("tool_call_id") or "legacy"))
        state = "working" if active else "thinking"
        if not active:
            _ACTIVE_TOOLS.pop(key, None)
        _SESSION_STATES[key] = state
        if failed:
            # Keep other in-flight tools busy in memory, but show this failure.
            _append_event("error")
        else:
            _set_session_state(state, kwargs)


def _on_session_end(**kwargs: Any) -> None:
    if _is_api_server_session(kwargs, clear=True):
        with _LOCK:
            _SESSION_STATES.pop(_session_key(kwargs), None)
            _ACTIVE_TOOLS.pop(_session_key(kwargs), None)
            _FOREGROUND_TURNS.discard(_session_key(kwargs))
            _TURN_SESSION_IDS.pop(_session_key(kwargs), None)
        return
    key = _session_key(kwargs)
    interrupted = bool(kwargs.get("interrupted"))
    failed = bool(kwargs.get("failed"))
    with _LOCK:
        # CLI shutdown can end a session without a turn ID; retire all its turns.
        keys = {key}
        if not kwargs.get("turn_id"):
            keys.update(k for k, aliases in _TURN_SESSION_IDS.items() if _session_id(kwargs) in aliases)
        for ended_key in keys:
            _SESSION_STATES.pop(ended_key, None)
            _ACTIVE_TOOLS.pop(ended_key, None)
            _FOREGROUND_TURNS.discard(ended_key)
            _TURN_SESSION_IDS.pop(ended_key, None)
        aggregate = _aggregate_state()
        if failed:
            _append_event("error")
        elif interrupted:
            _append_event("attention")
        else:
            _append_event(aggregate)


def _on_human_input_request(**kwargs: Any) -> None:
    if str(kwargs.get("kind") or "").lower() == "approval":
        _append_human_input_event("approval/request", kwargs)


def _on_human_input_resolved(**kwargs: Any) -> None:
    if str(kwargs.get("kind") or "").lower() == "approval":
        _append_human_input_event("approval/resolved", kwargs)


def register(ctx: Any) -> None:
    """Register activity and approval observers; pet Chat is status-suppressed."""
    ctx.register_hook("pre_llm_call", _on_pre_llm_call)
    ctx.register_hook("pre_tool_call", _on_pre_tool_call)
    ctx.register_hook("post_tool_call", _on_post_tool_call)
    ctx.register_hook("on_session_end", _on_session_end)
    ctx.register_hook("on_human_input_request", _on_human_input_request)
    ctx.register_hook("on_human_input_resolved", _on_human_input_resolved)


# Small public seam used by tests and local diagnostics; it returns the resolved
# path only and never exposes configuration or credential values.
def events_path() -> Path:
    return _events_path()
