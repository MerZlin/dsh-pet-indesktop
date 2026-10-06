"""Real in-process AI request/session lifecycle, bound to Core owner authorization."""

from __future__ import annotations

from typing import Callable, cast

from PySide6.QtCore import QObject, QTimer
from PySide6.QtWidgets import QApplication, QWidget
from shiboken6 import isValid

from pet.async_exit import application_exit_gate

from .config import AiConfiguration


class AiRuntime(QObject):
    def __init__(self, context):
        super().__init__()
        if context.execution_authorized is None or context.bind_execution is None:
            raise PermissionError("ai_runtime_requires_execution_ports")
        self.context = context
        self.config = AiConfiguration(context)
        self._services = []
        self._windows = []
        self._controllers = []
        self._finished_listeners = {}
        self._paused = False
        self._closing = False
        self._drain = None
        self._resume_timer = QTimer(self)
        self._resume_timer.setInterval(50)
        self._resume_timer.timeout.connect(self.resume)
        self._unsubscribe = context.bind_execution(self.pause, self.resume)
        self._gate = application_exit_gate(QApplication.instance())
        self._exit_token = self._gate.register(context.owner, self.close, lambda: self.drain_status == "completed")
        if context.user_data is not None:
            from .chat.session_store import SessionStore, pending_session_drain

            self._drain = pending_session_drain(SessionStore(self.config.dir, self.config.instance_id).root)
            if self._drain is not None:
                self._paused = True
                self.resume()

    @property
    def accepting(self):
        return not self._paused and not self._closing and self.context.execution_authorized()

    def create_service(self, *, provider=None):
        if not self.accepting:
            raise RuntimeError("ai_requests_not_accepting")
        from .chat.service import ChatService

        service = ChatService(provider=provider, parent=self, authorized=lambda: self.accepting)
        # The runtime and Core exit gate retain services through actual QThread
        # join; a window disappearing cannot delete an in-flight QThread.
        service.finished.connect(self._notify_finished)
        self._services.append(service)
        return service

    def subscribe_finished(self, callback):
        """Narrow presentation notification; never exposes request threads."""
        token = object()
        self._finished_listeners[token] = callback
        return lambda: self._finished_listeners.pop(token, None)

    def _notify_finished(self, _request_id, text):
        if self.accepting:
            for callback in tuple(self._finished_listeners.values()):
                callback(text)

    def create_window(self, kind, *, view=None, notifier=None, auth_callback=None):
        if not self.accepting:
            raise RuntimeError("ai_requests_not_accepting")
        if kind not in {"modern", "classic", "quick", "island"}:
            raise ValueError("unsupported_ai_window")
        # A signed host lazily imports its own business implementation, never
        # the legacy Core aliases. Runtime owns the service beyond UI close.
        window_factory: Callable[..., QWidget]
        if kind == "modern":
            from .chat.widgets import ChatWindow

            window_factory = ChatWindow
        elif kind == "classic":
            from .chat.legacy_widgets import ChatWindow as ClassicChatWindow

            window_factory = ClassicChatWindow
        elif kind == "quick":
            from .quick_chat import QuickChatBubble

            window_factory = QuickChatBubble
        else:
            from .island_chat import IslandChatBubble

            window_factory = IslandChatBubble
        # Each validated kind has a distinct constructor contract. Keep the
        # chosen callable broad; mypy must not union incompatible signatures.
        construct_window = cast(Callable[..., QWidget], window_factory)
        service = self.create_service()
        try:
            if kind in {"modern", "classic"}:
                window = construct_window(
                    self.config, str(self.config.get("character", "shenshen")), pet_window=view, notifier=notifier, auth_callback=auth_callback, service=service
                )
            elif kind == "quick":
                window = construct_window(self.config, pet_window=view, service=service)
            else:
                window = construct_window(self.config, service=service)
        except Exception:
            service.shutdown()
            raise
        self._windows.append(window)
        return window

    def create_file_interpreter(self, surface, *, provider=None):
        if not self.accepting:
            raise RuntimeError("ai_requests_not_accepting")
        from pet.ai_bindings import AiFileView

        from .file_interpret import FileInterpretController

        controller = FileInterpretController(
            AiFileView(surface, self.config),
            parent=self,
            service_factory=lambda: self.create_service(provider=provider),
            owns_service=False,
            authorized=lambda: self.accepting,
        )
        self._controllers.append(controller)
        return controller

    def create_external_receiver(self, route, target):
        if not self.accepting:
            raise RuntimeError("ai_requests_not_accepting")
        from .chat.external_turns import ExternalTurnReceiver

        return ExternalTurnReceiver(self.config, route, target, authorized=lambda: self.accepting)

    def send(self, service, messages, provider, *, operation="chat.send", request_id=None):
        if not self.accepting or service not in self._services:
            raise RuntimeError("ai_requests_not_accepting")
        return service.send(messages, self.config.request_config(provider, operation=operation), request_id=request_id)

    def pause(self):
        self._resume_timer.stop()
        if self._paused:
            return self.drain_status == "completed"
        self._paused = True
        for controller in self._controllers:
            if isValid(controller):
                controller.suspend_execution()
        for window in self._windows:
            if isValid(window):
                window.suspend_execution()
                store, session = getattr(window, "store", None), getattr(window, "session", None)
                if store is not None and session is not None and not store.save(session):
                    raise RuntimeError("ai_session_snapshot_rejected")
        for service in self._services:
            service.pause()
        from .chat.session_store import SessionStore, begin_session_drain

        root = SessionStore(self.config.dir, self.config.instance_id).root if self.context.user_data is not None else None
        self._drain = begin_session_drain(root=root)
        self._drain.observe_readiness(lambda: all(service.is_drained for service in self._services))
        return self.drain_status == "completed"

    @property
    def drain_status(self):
        if any(not service.is_drained for service in self._services):
            return "awaiting_release"
        if self._drain is not None:
            return self._drain.status
        return "completed" if self._paused else "active"

    def resume(self):
        if self._closing or not self.context.execution_authorized():
            self._resume_timer.stop()
            return False
        if self._drain is not None:
            status = self.drain_status
            if status == "awaiting_release":
                self._resume_timer.start()
                return False
            if status != "completed":
                self._resume_timer.stop()
                return False
            if not self._drain.resume():
                # Another bounded closer can still own the registry fence.
                self._resume_timer.start()
                return False
            self._drain = None
        for service in self._services:
            if not service.resume():
                return False
        self._paused = False
        for window in self._windows:
            if isValid(window):
                window.setEnabled(True)
        self._resume_timer.stop()
        return True

    def close(self):
        self._closing = True
        self._finished_listeners.clear()
        self.pause()
        for service in self._services:
            service.shutdown()
        if self._unsubscribe is not None:
            self._unsubscribe()
            self._unsubscribe = None
        return self.drain_status == "completed"
