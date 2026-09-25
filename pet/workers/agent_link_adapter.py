# -*- coding: utf-8 -*-
"""Core-side adapter for the Agent Link event collection worker.

Only this adapter knows how to translate the worker's bounded event payloads to
legacy ``BaseAgentMonitor``-shaped Qt signals.  Presentation and Agent Link
policy remain in ``pet.agent_link.AgentLinkManager``.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Mapping, Sequence
from typing import Any

from PySide6.QtCore import QObject, Signal

from ..agent_event_normalizer import normalize_event
from ..agent_event_protocol import parse_agent_event
from .protocol import WorkerMessage
from .supervisor import WorkerSupervisor

log = logging.getLogger("dsh-pet-standalone.worker-agent-link")


class WorkerAgentEventSource(QObject):
    """Expose worker events through the legacy monitor signal surface."""

    state_changed = Signal(str, str)
    activity = Signal(str, str)
    state_event = Signal(object)
    activity_event = Signal(object)
    approval_requested = Signal(str, object)
    approval_resolved = Signal(str, object)
    question_requested = Signal(str, object)
    question_resolved = Signal(str, object)
    cordis_requested = Signal(str, object)
    cordis_resolved = Signal(str, object)
    raw_record = Signal(str, object)
    normalized_event = Signal(object)
    execution_failed = Signal(str, object)
    session_meta = Signal(str, object)
    model_access = Signal(str, object)
    llm_error = Signal(str, object)
    user_action = Signal(str, object)
    unknown_bridge_event = Signal(str, object)
    worker_fault = Signal(str)
    worker_diagnostic = Signal(str, object)

    _OUTBOX_CAP = 256

    def __init__(self, parent: QObject | None = None, *, program: str | None = None, arguments: list[str] | None = None) -> None:
        super().__init__(parent)
        self.supervisor = WorkerSupervisor("agent-link-events", self, program=program, arguments=arguments)
        self.supervisor.message_received.connect(self._on_message)
        self.supervisor.diagnostic.connect(self._on_diagnostic)
        self.supervisor.state_changed.connect(self._on_state_changed)
        self.supervisor.ready.connect(self._on_ready)
        self._sources: list[dict[str, Any]] = []
        self._poll_interval = 0.25
        self._running = False
        self._paused = False
        self._emit_gen = -1
        self._outbox: list[tuple[Any, tuple[Any, ...]]] = []
        self._dropped_outbox = 0

    @property
    def state(self) -> str:
        return self.supervisor.state

    @property
    def generation(self) -> int:
        return self.supervisor.generation

    @property
    def active(self) -> bool:
        return self._running and self.supervisor.state in {self.supervisor.STARTING, self.supervisor.HANDSHAKING, self.supervisor.READY}

    def start(self, sources: Sequence[Mapping[str, Any]], *, poll_interval: float = 0.25) -> bool:
        self._sources = [dict(item) for item in sources]
        self._poll_interval = min(10.0, max(0.05, float(poll_interval)))
        self._running = True
        self._paused = False
        self._outbox.clear()
        self._dropped_outbox = 0
        started = self.supervisor.start(self._config_payload())
        if started:
            self._emit_gen = self.supervisor.generation
        else:
            self._running = False
        return started

    def configure(self, sources: Sequence[Mapping[str, Any]] | None = None, *, paused: bool | None = None) -> bool:
        if sources is not None:
            self._sources = [dict(item) for item in sources]
        if paused is not None:
            self._paused = bool(paused)
        if self.supervisor.state not in {self.supervisor.STARTING, self.supervisor.HANDSHAKING, self.supervisor.READY}:
            return False
        return self.supervisor.configure(self._config_payload()) if self.supervisor.state == self.supervisor.READY else False

    def _config_payload(self) -> dict[str, Any]:
        return {"sources": list(self._sources), "poll_interval": self._poll_interval, "paused": self._paused}

    def stop(self) -> None:
        self._running = False
        self._paused = False
        self._emit_gen = -1
        self._outbox.clear()
        self._dropped_outbox = 0
        self.supervisor.stop()

    begin_stop = stop

    def finish_stop(self, deadline: float | None = None) -> None:
        # QProcess shutdown is intentionally asynchronous.  Keep this method as
        # a compatibility no-op so AgentLinkManager can use one lifecycle path.
        del deadline

    def pause(self) -> None:
        if not self._running:
            return
        self._paused = True
        self.configure(paused=True)

    def resume(self) -> None:
        if not self._running:
            return
        self._paused = False
        self.configure(paused=False)
        pending = list(self._outbox)
        self._outbox.clear()
        for signal, args in pending:
            signal.emit(*args)

    def _on_ready(self) -> None:
        self._running = True
        self._emit_gen = self.supervisor.generation

    def _on_state_changed(self, state: str) -> None:
        if state == self.supervisor.FAULT:
            self._running = False
            self._emit_gen = -1
            self.worker_fault.emit("worker entered fault state")

    def _on_diagnostic(self, stage: str, detail: object) -> None:
        self.worker_diagnostic.emit(stage, detail)

    def _record_backpressure(self, reason: str) -> None:
        self._dropped_outbox += 1
        # Report the first drop and then sparse milestones; a flooded worker
        # must not turn its own diagnostic path into another unbounded queue.
        if self._dropped_outbox == 1 or self._dropped_outbox % self._OUTBOX_CAP == 0:
            self.worker_diagnostic.emit(
                "backpressure",
                {
                    "reason": reason,
                    "capacity": self._OUTBOX_CAP,
                    "dropped": self._dropped_outbox,
                },
            )

    def _evict_oldest_activity(self) -> bool:
        for index, (old_signal, _) in enumerate(self._outbox):
            if old_signal in (self.activity, self.activity_event):
                del self._outbox[index]
                self._record_backpressure("evicted_activity")
                return True
        return False

    def _emit(self, signal: Any, args: tuple[Any, ...]) -> None:
        if self._paused and self._running:
            if len(self._outbox) >= self._OUTBOX_CAP and not self._evict_oldest_activity():
                self._record_backpressure("dropped_event")
                return
            self._outbox.append((signal, args))
            return
        signal.emit(*args)

    def _emit_pair(self, legacy_signal: Any, legacy_args: tuple[Any, ...], event_signal: Any, event_args: tuple[Any, ...], *, state: str | None) -> None:
        if not (self._paused and self._running):
            legacy_signal.emit(*legacy_args)
            event_signal.emit(*event_args)
            return
        if state is not None:
            for signal, args in reversed(self._outbox):
                if signal is self.state_event and args and getattr(args[0], "state", None) == state:
                    return
        while len(self._outbox) + 2 > self._OUTBOX_CAP:
            if not self._evict_oldest_activity():
                self._record_backpressure("dropped_event_pair")
                return
        self._outbox.extend(((legacy_signal, legacy_args), (event_signal, event_args)))

    @staticmethod
    def _agent_event(agent_key: str, kind: str, generation: int, *, state: str = "", tool: str = "") -> object:
        # Lazy import avoids importing the large Qt-facing agent_link module in
        # the worker process while preserving the public event dataclass in Core.
        from ..agent_link import AgentEvent

        return AgentEvent(agent_key, kind, gen=generation, state=state, tool=tool)

    def _emit_state(self, agent_key: str, state: str, generation: int) -> None:
        event = self._agent_event(agent_key, "state", generation, state=state)
        self._emit_pair(self.state_changed, (agent_key, state), self.state_event, (event,), state=state)

    def _emit_tool(self, agent_key: str, tool: str, generation: int) -> None:
        event = self._agent_event(agent_key, "tool", generation, tool=tool)
        self._emit_pair(self.activity, (agent_key, tool), self.activity_event, (event,), state=None)

    def _dispatch(self, agent_key: str, record: dict[str, Any], generation: int) -> None:
        from ..agent_link import _RAW_BRIDGE_KNOWN_EVENTS, _cordis_requires_approval, normalize_event_state

        event_name = str(record.get("event", ""))
        explicit_state = str(record.get("state", ""))
        tool = str(record.get("tool", "") or "").strip()
        normalized = None
        try:
            normalized = normalize_event(parse_agent_event(record, source_hint=agent_key, agent_name_hint=agent_key))
            if normalized is not None:
                self.normalized_event.emit(normalized)
        except Exception:
            log.debug("Worker AgentEvent 解析失败", exc_info=True)
        self._emit(self.raw_record, (agent_key, record))
        if event_name in {"approval/request", "approval/requested"}:
            self._emit(self.approval_requested, (agent_key, record))
        if event_name in {"approval/decided", "approval/resolved"}:
            self._emit(self.approval_resolved, (agent_key, record))
        if event_name == "question/requested":
            self._emit(self.question_requested, (agent_key, record))
        if event_name == "question/resolved":
            self._emit(self.question_resolved, (agent_key, record))
        if event_name == "cordis/request-run" and _cordis_requires_approval(record):
            self._emit(self.cordis_requested, (agent_key, record))
        if event_name == "cordis/request-run-resolved":
            self._emit(self.cordis_resolved, (agent_key, record))
        if event_name == "execution/failed":
            self._emit(self.execution_failed, (agent_key, record))
        if tool:
            self._emit_tool(agent_key, tool, generation)
        meta_type = str(record.get("type", ""))
        if meta_type == "session/meta":
            self._emit(self.session_meta, (agent_key, record))
        if meta_type == "debug/session-shape":
            log.info("[dsh-pet-bridge] session shape: %s", json.dumps(record, ensure_ascii=False)[:500])
        if event_name == "model_access":
            self._emit(self.model_access, (agent_key, record))
        if event_name == "llm_error":
            self._emit(self.llm_error, (agent_key, record))
        if event_name == "user_action":
            self._emit(self.user_action, (agent_key, record))
        if (
            agent_key == "dsh"
            and bool(event_name)
            and normalized is None
            and not normalize_event_state(event_name, "")
            and event_name not in _RAW_BRIDGE_KNOWN_EVENTS
            and meta_type not in ("session/meta", "debug/session-shape")
        ):
            self._emit(self.unknown_bridge_event, (agent_key, record))
        state = normalize_event_state(event_name, explicit_state)
        if state:
            self._emit_state(agent_key, state, generation)

    def _on_message(self, message: WorkerMessage) -> None:
        if message.type == "error":
            self.worker_diagnostic.emit("worker_error", message.payload)
            return
        if message.type != "event":
            return
        payload = message.payload
        try:
            generation = int(payload.get("generation", -1))
        except (TypeError, ValueError):
            self.worker_diagnostic.emit("generation", {"reason": "event generation is invalid"})
            return
        if not self._running or generation != self._emit_gen:
            self.worker_diagnostic.emit("stale_event", {"generation": generation, "current": self._emit_gen})
            return
        agent_key = str(payload.get("agent", ""))
        record = payload.get("record")
        if not agent_key or not isinstance(record, dict):
            self.worker_diagnostic.emit("event", {"reason": "event requires agent and record"})
            return
        try:
            self._dispatch(agent_key, record, generation)
        except Exception as exc:
            self.worker_diagnostic.emit("dispatch", {"reason": str(exc), "agent": agent_key})
            log.debug("Worker Agent Link 事件转发失败", exc_info=True)


__all__ = ["WorkerAgentEventSource"]
