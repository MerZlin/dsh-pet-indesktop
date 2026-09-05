"""Compatibility normalizer from AgentEvent facts to Pet semantic events."""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any
from .agent_event_protocol import AgentEvent, parse_agent_event

@dataclass(frozen=True)
class SemanticEvent:
    source: str
    agent_name: str
    session_id: str
    step: int | str | None = None
    data: dict[str, Any] = field(default_factory=dict)

@dataclass(frozen=True)
class LifecycleEvent(SemanticEvent): event: str = ""
@dataclass(frozen=True)
class ToolCallEvent(SemanticEvent):
    tool: str = ""
    target: str = ""
    action_class: str = ""
@dataclass(frozen=True)
class ToolResultEvent(SemanticEvent):
    tool: str = ""
    ok: bool = True
    target: str = ""
@dataclass(frozen=True)
class ReasoningEvent(SemanticEvent):
    summary: str = ""
    delta: bool = False
@dataclass(frozen=True)
class EvidenceEvent(SemanticEvent):
    tool: str = ""
    target: str = ""
    status: str = ""
@dataclass(frozen=True)
class ActionEvent(ToolCallEvent): pass
@dataclass(frozen=True)
class RetryEvent(SemanticEvent):
    code: str = ""
    message: str = ""
    retry: int | str | None = None
@dataclass(frozen=True)
class ErrorEvent(SemanticEvent):
    code: str = ""
    message: str = ""
@dataclass(frozen=True)
class ApprovalEvent(SemanticEvent): status: str = ""
@dataclass(frozen=True)
class QuestionEvent(SemanticEvent): status: str = ""
@dataclass(frozen=True)
class UserActionEvent(SemanticEvent): action: str = ""
@dataclass(frozen=True)
class InteractionResolvedEvent(SemanticEvent):
    kind: str = ""
    request_id: str = ""
    rpc_id: str = ""
    approval_id: str = ""
    call_id: str = ""
    outcome: str = ""

@dataclass(frozen=True)
class ControlResultEvent(SemanticEvent):
    request_id: str = ""
    operation: str = ""
    ok: bool = False
    phase: str = ""

_LIFECYCLE = {"agent/status", "AgentStatus", "session/created", "session/disposed", "turn/start", "turn/end", "step/start", "step/end", "task_started", "task_complete", "thread_rolled_back"}
_EXPLORATION = {"read", "grep", "glob", "search", "web_search", "web_search_begin", "exec_command_begin"}
_ACTION = {"edit", "write", "patch", "shell", "pwsh", "bash", "pytest", "npm test", "playwright", "run"}

def _data(ev: AgentEvent) -> dict[str, Any]:
    return ev.data

def normalize_event(record: AgentEvent | dict, *, source_hint: str = "", agent_name_hint: str = "") -> SemanticEvent | None:
    ev = record if isinstance(record, AgentEvent) else parse_agent_event(record, source_hint=source_hint, agent_name_hint=agent_name_hint)
    data = _data(ev)
    typ = ev.event
    lower = typ.lower()
    common = dict(source=ev.source, agent_name=ev.agent_name, session_id=ev.session_id, step=ev.step, data=data)
    if typ in _LIFECYCLE or lower in _LIFECYCLE:
        return LifecycleEvent(**common, event=typ)
    if lower in {"assistant/message", "assistant/chunk", "agent_reasoning", "agent_reasoning_raw_content", "reasoning"}:
        return ReasoningEvent(**common, summary=str(data.get("summary") or data.get("text") or data.get("content") or "")[:300], delta=lower.endswith("chunk") or "delta" in data)
    if lower in {"tool/call", "command/run", "tool-workflow/run-start", "exec_command_begin", "mcp_tool_call_begin"}:
        tool = str(data.get("tool") or data.get("toolName") or data.get("name") or "")
        target = str(data.get("target") or data.get("filePath") or data.get("path") or data.get("query") or "")[:300]
        cls = "ACTION" if tool.lower() in _ACTION or lower == "command/run" else "EXPLORATION" if tool.lower() in _EXPLORATION or lower in _EXPLORATION else "OTHER"
        typ_cls = ActionEvent if cls == "ACTION" else ToolCallEvent
        return typ_cls(**common, tool=tool, target=target, action_class=cls)
    if lower in {"tool/result", "exec_command_end", "mcp_tool_call_end"}:
        ok = data.get("ok", True) not in (False, 0, "false", "error")
        result = ToolResultEvent(**common, tool=str(data.get("tool") or data.get("toolName") or ""), ok=ok, target=str(data.get("target") or data.get("path") or "")[:300])
        return result if not ok else EvidenceEvent(**common, tool=result.tool, target=result.target, status=str(data.get("evidenceStatus") or "new"))
    if lower == "llm/retry":
        failure = data.get("failure") if isinstance(data.get("failure"), dict) else data
        return RetryEvent(**common, code=str(failure.get("code") or data.get("errorCode") or ""), message=str(failure.get("message") or data.get("errorMessage") or "")[:300], retry=data.get("retry"))
    if lower in {"agent/request-error", "execution/failed", "llm_error", "error"}:
        return ErrorEvent(**common, code=str(data.get("errorCode") or data.get("code") or ""), message=str(data.get("errorMessage") or data.get("errorText") or "")[:300])
    if lower.startswith("approval/"): return ApprovalEvent(**common, status=lower.split("/", 1)[1])
    if lower.startswith("question/"): return QuestionEvent(**common, status=lower.split("/", 1)[1])
    if lower == "interaction/resolved":
        return InteractionResolvedEvent(**common, kind=str(data.get("kind") or ""), request_id=str(data.get("requestId") or ev.request_id or ""), rpc_id=str(data.get("rpcId") or ""), approval_id=str(data.get("approvalId") or ""), call_id=str(data.get("callId") or ""), outcome=str(data.get("outcome") or ""))
    if lower in {"approval/decided", "approval/resolved", "question/resolved"}:
        kind = "question" if lower == "question/resolved" else "approval"
        outcome = str(data.get("outcome") or data.get("decision") or ("answered" if kind == "question" else "approved"))
        return InteractionResolvedEvent(**common, kind=kind, request_id=str(data.get("requestId") or ev.request_id or ""), rpc_id=str(data.get("rpcId") or ""), approval_id=str(data.get("approvalId") or ""), call_id=str(data.get("callId") or ""), outcome=outcome)
    if lower == "user_action":
        action = str(data.get("action") or "")
        if "resolved" in action or "decided" in action:
            kind = "question" if "question" in action else "approval"
            return InteractionResolvedEvent(**common, kind=kind, request_id=str(data.get("requestId") or ev.request_id or ""), rpc_id=str(data.get("rpcId") or ""), approval_id=str(data.get("approvalId") or ""), call_id=str(data.get("callId") or ""), outcome=str(data.get("outcome") or data.get("decision") or ""))
        return UserActionEvent(**common, action=action)
    if lower in {"control-result", "bridge/control-result", "watchdog/control-result"}:
        return ControlResultEvent(**common, request_id=ev.request_id or str(data.get("requestId") or ""), operation=str(data.get("operation") or ""), ok=bool(data.get("ok", False)), phase=str(data.get("phase") or ""))
    return None
