from __future__ import annotations

import threading
import uuid
from typing import Any

from PySide6.QtCore import QObject, Qt, QThread, Signal
from PySide6.QtWidgets import QApplication
from shiboken6 import isValid

from pet.async_exit import application_exit_gate

from .models import ProviderConfig
from .providers import OpenAICompatibleProvider


class _Worker(QThread):
    delta_received = Signal(str)
    completed = Signal(str)
    failed = Signal(str)
    stopped_by_user = Signal()

    def __init__(self, provider, messages, config, cancel):
        super().__init__()
        self.provider = provider
        self.messages = messages
        self.config = config
        self.cancel = cancel
        self.parts = []
        self._responses = []
        self._close_started = False
        self._close_done = threading.Event()
        self._close_done.set()
        self._join_started = False
        self._joined = threading.Event()

    def cancel_response(self):
        self.cancel.set()
        if self._close_started:
            return
        responses = tuple(self._responses)
        if not responses:
            return  # A later-opened response is closed by the provider's finally.
        self._close_started = True
        self._close_done.clear()

        def close_responses():
            try:
                for response in responses:
                    try:
                        response.close()
                    except Exception:
                        pass
            finally:
                self._close_done.set()

        threading.Thread(target=close_responses, name="ai-response-close", daemon=True).start()

    def begin_join(self):
        if self._join_started:
            return
        self._join_started = True

        def join():
            # finished() can precede thread-local destructors. Synchronize away
            # from the GUI and keep this QThread alive until wait() returns.
            self.wait()
            self._joined.set()

        threading.Thread(target=join, name="ai-request-reap", daemon=True).start()

    def run(self):
        try:
            stream_kwargs = {}
            import inspect

            sig = inspect.signature(self.provider.stream)
            if "response_holder" in sig.parameters:
                stream_kwargs["response_holder"] = self._responses
            for text in self.provider.stream(self.messages, self.config, self.cancel, **stream_kwargs):
                if self.cancel.is_set():
                    self.stopped_by_user.emit()
                    return
                self.parts.append(text)
                self.delta_received.emit(text)
            if self.cancel.is_set():
                self.stopped_by_user.emit()
            else:
                result = "".join(self.parts)
                if result.strip():
                    self.completed.emit(result)
                else:
                    self.failed.emit("模型未返回任何内容，请稍后重试或检查模型配置。")
        except Exception as exc:
            self.stopped_by_user.emit() if self.cancel.is_set() else self.failed.emit(str(exc))


class ChatService(QObject):
    started = Signal(str)
    delta = Signal(str, str)
    finished = Signal(str, str)
    error = Signal(str, str)
    stopped = Signal(str)
    drained = Signal()
    # 进程级「回复完成」订阅（灵动岛事件动效等）：AppShell 注册一次，
    # 回调总在 GUI 线程执行（worker→本对象的链路全是 QueuedConnection）。
    _global_finished_listeners: list = []

    @classmethod
    def register_global_finished(cls, callback):
        if callback not in cls._global_finished_listeners:
            cls._global_finished_listeners.append(callback)

    @classmethod
    def unregister_global_finished(cls, callback):
        if callback in cls._global_finished_listeners:
            cls._global_finished_listeners.remove(callback)

    def __init__(self, provider=None, parent=None, *, authorized=None):
        super().__init__(parent)
        self.provider = provider or OpenAICompatibleProvider()
        self._authorized = authorized
        self._request_id = None
        self._cancel = None
        self._worker = None
        self._workers = set()
        self._generation = 0
        self._accepting = True
        self._closing = False
        self._exit_token = None
        self._gate = None
        app = QApplication.instance()
        if app is not None:
            self._gate = application_exit_gate(app)
            app.aboutToQuit.connect(self.shutdown)
        self.destroyed.connect(lambda *_: self.shutdown())

    @property
    def accepting(self) -> bool:
        return self._accepting and isValid(self) and (self._authorized is None or self._authorized())

    @property
    def is_drained(self) -> bool:
        return not self._workers

    def shutdown(self) -> bool:
        self._closing = True
        self.pause()
        return self.is_drained

    def pause(self) -> bool:
        self._accepting = False
        self.stop()
        for worker in tuple(self._workers):
            worker.cancel_response()
        return self.is_drained

    def resume(self) -> bool:
        if self._closing or not self.is_drained or not isValid(self):
            return False
        self._accepting = True
        return True

    def _ensure_exit_guard(self):
        if self._gate is None:
            self._gate = application_exit_gate()
        if self._exit_token is None:
            self._exit_token = self._gate.register("official.ai-chat", self.shutdown, lambda: self.is_drained)

    @property
    def busy(self):
        return self._worker is not None and self._worker.isRunning()

    def send(self, messages: list[dict[str, Any]], config: ProviderConfig, request_id=None):
        if not self.accepting:
            raise RuntimeError("ai_requests_not_accepting")
        if len(self._workers) >= 8:
            raise RuntimeError("ai_request_drain_capacity")
        self.stop()
        self._ensure_exit_guard()
        rid = request_id or uuid.uuid4().hex
        generation = self._generation
        cancel = threading.Event()
        worker = _Worker(self.provider, messages, config, cancel)
        self._request_id = rid
        self._cancel = cancel
        self._worker = worker
        self._workers.add(worker)
        # A request ID is not an authority token: every delivery also binds the
        # internal generation, including callers reusing an external ID.
        worker.delta_received.connect(lambda text: self._delta(rid, text, generation), Qt.ConnectionType.QueuedConnection)
        worker.completed.connect(lambda text: self._finished(rid, text, generation), Qt.ConnectionType.QueuedConnection)
        worker.failed.connect(lambda text: self._error(rid, text, generation), Qt.ConnectionType.QueuedConnection)
        worker.stopped_by_user.connect(lambda: self._stopped(rid, generation), Qt.ConnectionType.QueuedConnection)
        worker.finished.connect(lambda: self._cleanup(rid, generation, worker), Qt.ConnectionType.QueuedConnection)
        self.started.emit(rid)
        worker.start()
        return rid

    def stop(self):
        self._generation += 1
        if self._worker is not None:
            worker = self._worker
            was_running = worker.isRunning()
            worker.cancel_response()
            if was_running and self._request_id is not None and isValid(self):
                self.stopped.emit(self._request_id)
        elif self._cancel is not None:
            self._cancel.set()

    def _current(self, rid, generation):
        return isValid(self) and self._accepting and generation == self._generation and rid == self._request_id

    def _delta(self, rid, text, generation):
        if self._current(rid, generation):
            self.delta.emit(rid, text)

    def _finished(self, rid, text, generation):
        if self._current(rid, generation):
            self.finished.emit(rid, text)
            for cb in list(type(self)._global_finished_listeners):
                try:
                    cb(text)
                except Exception:
                    pass

    def _error(self, rid, text, generation):
        if self._current(rid, generation):
            self.error.emit(rid, text)

    def _stopped(self, rid, generation):
        if self._current(rid, generation):
            self.stopped.emit(rid)

    def _cleanup(self, rid, generation, worker):
        if self._worker is worker:
            self._worker = None
            self._cancel = None
        worker.begin_join()
        self._collect(worker)

    def _collect(self, worker):
        assert self._gate is not None  # send() creates the app-owned drain gate.
        if not worker._joined.is_set() or not worker._close_done.is_set():
            self._gate.defer(lambda: self._collect(worker))
            return
        self._workers.discard(worker)
        if not self._workers:
            if self._exit_token is not None:
                self._gate.unregister(self._exit_token)
                self._exit_token = None
            if isValid(self):
                self.drained.emit()
