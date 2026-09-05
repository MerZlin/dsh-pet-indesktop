# -*- coding: utf-8 -*-
"""Unified, bounded Agent event protocol."""
from __future__ import annotations
from dataclasses import dataclass, field
import json
import time
from typing import Any, Mapping

SCHEMA = "agent-event/v1"
MAX_DATA_BYTES = 16 * 1024
MAX_STRING_CHARS = 2000

def _bounded(value: Any, depth: int = 0) -> Any:
    if depth > 5: return "[truncated]"
    if isinstance(value, str): return value[:MAX_STRING_CHARS]
    if value is None or isinstance(value, (bool, int, float)): return value
    if isinstance(value, Mapping):
        return {str(k)[:120]: _bounded(v, depth + 1) for k, v in list(value.items())[:128]}
    if isinstance(value, (list, tuple)): return [_bounded(v, depth + 1) for v in list(value)[:128]]
    return str(value)[:MAX_STRING_CHARS]

def bounded_data(value: Any) -> dict[str, Any]:
    raw = _bounded(value if isinstance(value, Mapping) else {})
    if not isinstance(raw, dict): raw = {}
    while len(json.dumps(raw, ensure_ascii=False, separators=(",", ":")).encode("utf-8")) > MAX_DATA_BYTES and raw:
        raw.pop(next(reversed(raw)))
    return raw

def _first(record: Mapping[str, Any], *names: str, default: Any = None) -> Any:
    for name in names:
        if name in record and record[name] is not None: return record[name]
    return default

@dataclass(frozen=True)
class AgentEvent:
    schema: str
    timestamp: float
    source: str
    agent_name: str
    project_id: str
    project_name: str
    session_id: str
    session_name: str
    turn: int | str | None
    step: int | str | None
    event: str
    data: dict[str, Any] = field(default_factory=dict)
    call_id: str = ""
    request_id: str = ""

    @classmethod
    def from_record(cls, record: Mapping[str, Any], *, source_hint: str = "", agent_name_hint: str = "") -> "AgentEvent":
        if not isinstance(record, Mapping): record = {}
        source = str(_first(record, "source", "agent", default=source_hint) or source_hint)
        name = str(_first(record, "agentName", "agent_name", default=agent_name_hint or source) or source)
        session = str(_first(record, "sessionId", "session_id", default="") or "")
        project_id = str(_first(record, "projectId", "project_id", default="") or "")
        project_name = str(_first(record, "projectName", "project_name", default="") or "")
        session_name = str(_first(record, "sessionName", "session_name", default="") or "")
        event = str(_first(record, "event", "type", default="") or "")
        if not event and "state" in record:
            event = "AgentStatus"
        timestamp = _first(record, "ts", "timestamp", default=time.time())
        try: timestamp = float(timestamp)
        except (TypeError, ValueError): timestamp = time.time()
        data = _first(record, "data", default=None)
        if not isinstance(data, Mapping):
            excluded = {"schema", "ts", "timestamp", "source", "agent", "agentName", "agent_name", "projectId", "project_id", "projectName", "project_name", "sessionId", "session_id", "sessionName", "session_name", "turn", "step", "event", "type", "callId", "call_id", "requestId", "request_id"}
            data = {k: v for k, v in record.items() if k not in excluded}
        return cls(str(_first(record, "schema", default=SCHEMA) or SCHEMA), timestamp, source, name, project_id, project_name, session, session_name, _first(record, "turn"), _first(record, "step"), event, bounded_data(data), str(_first(record, "callId", "call_id", default="") or ""), str(_first(record, "requestId", "request_id", default="") or ""))

    def display_context(self) -> str:
        """Human-facing context with conservative fallbacks; IDs are never displayed."""
        project = self.project_name.strip()
        session = self.session_name.strip()
        agent = self.agent_name.strip() or self.source.strip() or "Agent"
        if project and session: return f"{project} · {session}"
        if session: return session
        return agent

    def to_record(self) -> dict[str, Any]:
        record = {"schema": self.schema or SCHEMA, "ts": self.timestamp, "source": self.source, "agentName": self.agent_name, "projectId": self.project_id, "projectName": self.project_name, "sessionId": self.session_id, "sessionName": self.session_name, "event": self.event, "data": bounded_data(self.data)}
        if self.turn is not None: record["turn"] = self.turn
        if self.step is not None: record["step"] = self.step
        if self.call_id: record["callId"] = self.call_id
        if self.request_id: record["requestId"] = self.request_id
        return record

def parse_agent_event(record: Mapping[str, Any], *, source_hint: str = "", agent_name_hint: str = "") -> AgentEvent:
    return AgentEvent.from_record(record, source_hint=source_hint, agent_name_hint=agent_name_hint)
