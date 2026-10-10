# -*- coding: utf-8 -*-
"""Feature-owned transport adapter for the proactive screen worker.

The adapter owns the transport boundary only.  Policy, quota state, window
presentation and generation authority remain in ``ProactiveScreenWatcher``.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any, Callable, Mapping, Sequence

from PySide6.QtCore import QObject, QTimer, Signal

from pet.workers.launch import WorkerLaunch
from pet.workers.protocol import WorkerMessage
from pet.workers.supervisor import WorkerSupervisor

log = logging.getLogger("dsh-pet-standalone.worker-proactive-screen")


@dataclass(frozen=True)
class _PendingRequest:
    operation: str
    generation: int | None
    kind: str
    deadline: float


class ProactiveScreenWorkerAdapter(QObject):
    """Translate Core requests to the bounded proactive-screen worker."""

    state_changed = Signal(str)
    observation_ready = Signal(object)
    capture_ready = Signal(object)
    analysis_ready = Signal(object)
    manual_ready = Signal(object)
    request_failed = Signal(str, object)
    failed = Signal(str)
    diagnostic = Signal(str, object)

    ACTIVE_STATES = frozenset(
        {
            WorkerSupervisor.STARTING,
            WorkerSupervisor.HANDSHAKING,
            WorkerSupervisor.READY,
        }
    )

    def __init__(
        self,
        parent: QObject | None = None,
        *,
        mode: str = "auto",
        program: str | None = None,
        arguments: Sequence[str] | None = None,
        budget_checker: Callable[[str, int | None], bool] | None = None,
        launch_factory: Callable[[], WorkerLaunch] | None = None,
    ) -> None:
        super().__init__(parent)
        self.mode = mode if mode in {"auto", "in_process", "disabled"} else "auto"
        self._budget_checker = budget_checker
        self._config: dict[str, Any] = {}
        self._pending: dict[str, _PendingRequest] = {}
        self._deadline_timer = QTimer(self)
        self._deadline_timer.setInterval(500)
        self._deadline_timer.timeout.connect(self._expire_requests)
        self._last_diagnostic: tuple[str, dict[str, Any]] | None = None
        self.supervisor = WorkerSupervisor(
            "proactive-screen",
            self,
            program=program,
            arguments=arguments,
            launch_factory=launch_factory,
            required_capabilities=("foreground.read", "screenshot.capture", "vision.request", "logging.write"),
        )
        self.supervisor.state_changed.connect(self._on_state_changed)
        self.supervisor.response_received.connect(self._on_response)
        self.supervisor.request_received.connect(self._on_request)
        self.supervisor.diagnostic.connect(self._on_diagnostic)
        self.supervisor.failed.connect(self._on_failed)

    @property
    def state(self) -> str:
        return self.supervisor.state

    @property
    def generation(self) -> int:
        return self.supervisor.generation

    @property
    def ready(self) -> bool:
        return self.state == WorkerSupervisor.READY

    @property
    def active(self) -> bool:
        return self.state in self.ACTIVE_STATES

    def start(self, config: Mapping[str, Any] | None = None) -> bool:
        """Start the selected worker unless the adapter is in fallback mode."""
        self._config = self._sanitize_config(config or {})
        if self.mode != "auto":
            return False
        if self.active:
            if self.ready:
                return self.supervisor.configure(self._config)
            return False
        self._pending.clear()
        return self.supervisor.start(self._config)

    def configure(self, config: Mapping[str, Any] | None = None) -> bool:
        if config is not None:
            self._config = self._sanitize_config(config)
        if self.mode != "auto" or not self.ready:
            return False
        return self.supervisor.configure(self._config)

    def stop(self) -> None:
        self._fail_pending("worker_stopped")
        self.supervisor.stop()

    def diagnostics(self) -> dict[str, Any]:
        stage, detail = self._last_diagnostic or ("", {})
        return {
            "worker_id": self.supervisor.worker_id,
            "mode": self.mode,
            "state": self.state,
            "generation": self.generation,
            "pending": len(self._pending),
            "last_stage": stage,
            "last_detail": dict(detail),
        }

    # --------------------------------------------------------------- requests
    def observe_foreground(self, generation: int) -> str | None:
        return self._send("observe_foreground", {"kind": "automatic"}, generation=generation, kind="automatic")

    def capture_foreground(self, window: Mapping[str, Any], generation: int) -> str | None:
        return self._send(
            "capture_foreground",
            {"kind": "automatic", "window": dict(window)},
            generation=generation,
            kind="automatic",
        )

    def analyze_frame(
        self,
        frame_id: str,
        provider: Any,
        system_prompt: str,
        *,
        generation: int,
        memory_context: str = "",
        pet_name: str = "",
    ) -> str | None:
        return self._send(
            "analyze_frame",
            {
                "kind": "automatic",
                "frame_id": str(frame_id),
                "provider": self._provider_payload(provider),
                "system_prompt": str(system_prompt),
                "memory_context": str(memory_context),
                "pet_name": str(pet_name),
            },
            generation=generation,
            kind="automatic",
        )

    def manual_look(
        self,
        provider: Any,
        system_prompt: str,
        *,
        generation: int,
        memory_context: str = "",
        pet_name: str = "",
    ) -> str | None:
        return self._send(
            "manual_look",
            {
                "kind": "manual",
                "provider": self._provider_payload(provider),
                "system_prompt": str(system_prompt),
                "memory_context": str(memory_context),
                "pet_name": str(pet_name),
            },
            generation=generation,
            kind="manual",
        )

    def release_frame(self, frame_id: str, generation: int | None = None) -> str | None:
        return self._send(
            "release_frame",
            {"kind": "automatic", "frame_id": str(frame_id)},
            generation=generation,
            kind="automatic",
        )

    def cancel(self, request_id: str, generation: int | None = None) -> str | None:
        self._pending.pop(request_id, None)
        return self._send(
            "cancel",
            {"kind": "manual", "request_id": str(request_id)},
            generation=generation,
            kind="manual",
        )

    def _send(
        self,
        operation: str,
        arguments: Mapping[str, Any],
        *,
        generation: int | None,
        kind: str,
    ) -> str | None:
        if self.mode != "auto" or not self.ready or len(self._pending) >= 16:
            return None
        request_id = self.supervisor.send_request(operation, arguments, generation=generation)
        if request_id is not None:
            # A missing response must not permanently leave the GUI busy.
            timeout = 240.0 if operation in {"analyze_frame", "manual_look"} else 15.0
            self._pending[request_id] = _PendingRequest(operation, generation, kind, time.monotonic() + timeout)
            self._deadline_timer.start()
        return request_id

    # --------------------------------------------------------------- responses
    def _on_response(self, message: WorkerMessage) -> None:
        request_id = message.request_id or ""
        pending = self._pending.pop(request_id, None)
        payload = dict(message.payload)
        operation = str(payload.get("operation", pending.operation if pending else ""))
        response_generation = self._int_or_none(payload.get("generation"))
        if pending is None:
            self._emit_diag("response", {"reason": "response has no pending request", "operation": operation})
            return
        if operation != pending.operation or response_generation != pending.generation or payload.get("status") not in {"ok", "error"}:
            self._fail_request(request_id, pending, "invalid_response")
            return
        envelope: dict[str, Any] = {
            "request_id": request_id,
            "operation": operation,
            "generation": response_generation,
            "status": str(payload.get("status", "ok")),
        }
        result = payload.get("result")
        if isinstance(result, dict):
            envelope["result"] = dict(result)
        for key in ("error_code", "message", "retryable"):
            if key in payload:
                envelope[key] = payload[key]
        if envelope["status"] == "error":
            self.request_failed.emit(operation, envelope)
            return
        if operation == "observe_foreground":
            self.observation_ready.emit(envelope)
        elif operation == "capture_foreground":
            self.capture_ready.emit(envelope)
        elif operation == "analyze_frame":
            self.analysis_ready.emit(envelope)
        elif operation == "manual_look":
            self.manual_ready.emit(envelope)
        elif operation not in {"release_frame", "cancel"}:
            self._emit_diag("response", {"reason": "unknown response operation", "operation": operation})

    def _on_request(self, message: WorkerMessage) -> None:
        operation = str(message.payload.get("operation", ""))
        if operation != "budget_check" or not message.request_id:
            self._emit_diag("request", {"reason": "unsupported worker request", "operation": operation})
            return
        arguments = message.payload.get("arguments")
        arguments = arguments if isinstance(arguments, dict) else {}
        kind = str(arguments.get("kind", "automatic"))
        generation = self._int_or_none(message.payload.get("generation"))
        allowed = False
        try:
            parent = self._pending.get(str(arguments.get("parent_request_id", "")))
            if (
                self._budget_checker is not None
                and parent is not None
                and parent.operation == "analyze_frame"
                and parent.kind == kind == "automatic"
                and parent.generation == generation
            ):
                allowed = bool(self._budget_checker(kind, generation))
        except Exception:
            log.exception("主动识屏 Worker 额度检查失败")
            allowed = False
        self.supervisor.send_response(
            message.request_id,
            "budget_check",
            {"status": "ok", "result": {"allowed": allowed}},
            generation=generation,
        )

    # -------------------------------------------------------------- lifecycle
    def _on_state_changed(self, state: str) -> None:
        if state in {"crashed", "fault", "stopping", "stopped"}:
            self._fail_pending("worker_unavailable")
        self.state_changed.emit(state)

    def _on_failed(self, reason: str) -> None:
        self._fail_pending("worker_unavailable")
        self.failed.emit(str(reason))

    def _fail_request(self, request_id: str, pending: _PendingRequest, code: str) -> None:
        self.request_failed.emit(
            pending.operation,
            {
                "request_id": request_id,
                "operation": pending.operation,
                "generation": pending.generation,
                "status": "error",
                "error_code": code,
                "message": "识屏任务已结束，请重试",
                "retryable": True,
            },
        )

    def _fail_pending(self, code: str) -> None:
        pending, self._pending = self._pending, {}
        self._deadline_timer.stop()
        for request_id, item in pending.items():
            self._fail_request(request_id, item, code)

    def _expire_requests(self) -> None:
        now = time.monotonic()
        for request_id, item in list(self._pending.items()):
            if now >= item.deadline:
                self._pending.pop(request_id, None)
                if item.operation not in {"cancel", "release_frame"} and self.ready:
                    # No acknowledgement is retained for this best-effort cancellation.
                    self.supervisor.send_request("cancel", {"request_id": request_id}, generation=item.generation)
                self._fail_request(request_id, item, "request_timeout")
        if not self._pending:
            self._deadline_timer.stop()

    def _on_diagnostic(self, stage: str, detail: object) -> None:
        from .worker_diagnostics import safe_diagnostic

        data = safe_diagnostic(detail)
        self._last_diagnostic = (str(stage), data)
        self.diagnostic.emit(str(stage), data)

    def _emit_diag(self, stage: str, detail: Mapping[str, Any]) -> None:
        self._last_diagnostic = (str(stage), dict(detail))
        self.diagnostic.emit(str(stage), dict(detail))

    @staticmethod
    def _int_or_none(value: Any) -> int | None:
        try:
            return None if value is None else int(value)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _sanitize_config(config: Mapping[str, Any]) -> dict[str, Any]:
        allowed = {"max_edge", "jpeg_quality", "frame_ttl", "platform"}
        return {str(key): value for key, value in config.items() if str(key) in allowed}

    @staticmethod
    def _provider_payload(provider: Any) -> dict[str, Any]:
        if hasattr(provider, "to_dict"):
            data = provider.to_dict(include_secret=True)
        elif isinstance(provider, Mapping):
            data = dict(provider)
        else:
            raise TypeError("provider must be a mapping or expose to_dict()")
        if not isinstance(data, dict):
            raise TypeError("provider payload must be an object")
        from ..common.models import VisionRequestConfig

        return VisionRequestConfig.from_dict(data).to_dict(include_secret=True)


__all__ = ["ProactiveScreenWorkerAdapter"]
