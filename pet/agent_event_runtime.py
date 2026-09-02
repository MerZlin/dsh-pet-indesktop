"""Fan-out point for unified AgentEvent records."""
from __future__ import annotations
from typing import Callable, Iterable
from .agent_event_normalizer import SemanticEvent, normalize_event
from .agent_event_protocol import AgentEvent, parse_agent_event

class AgentEventRuntime:
    """Dispatch normalized events while retaining independent legacy consumers."""
    def __init__(self, consumers: Iterable[Callable[[SemanticEvent], None]] = ()) -> None:
        self._consumers: list[Callable[[SemanticEvent], None]] = list(consumers)

    def add_consumer(self, consumer: Callable[[SemanticEvent], None]) -> None:
        if callable(consumer): self._consumers.append(consumer)

    def dispatch(self, event: AgentEvent | SemanticEvent | dict, *, source_hint: str = "", agent_name_hint: str = "") -> SemanticEvent | None:
        if isinstance(event, SemanticEvent):
            semantic = event
        else:
            semantic = normalize_event(event if isinstance(event, AgentEvent) else parse_agent_event(event, source_hint=source_hint, agent_name_hint=agent_name_hint))
        if semantic is None: return None
        for consumer in tuple(self._consumers):
            try: consumer(semantic)
            except Exception: continue
        return semantic
