"""v1 GUI-thread worker client. Launch authority always comes from Core."""
from __future__ import annotations

from uuid import uuid4

from PySide6.QtCore import QObject, Signal

from pet.workers.supervisor import WorkerSupervisor


class WorkerClient(QObject):
    ready = Signal()
    response = Signal(str, object)
    error = Signal(str)

    def __init__(self, context, parent=None):
        super().__init__(parent)
        self.context = context
        self.pending = set()
        self.closed = False
        self.supervisor = WorkerSupervisor(context.owner, self, launch_factory=self._launch, max_restarts=0)
        self.supervisor.ready.connect(self.ready)
        self.supervisor.response_received.connect(self._response)
        self.supervisor.failed.connect(self._failed)
        self._unbind = context.bind_execution(self.stop, lambda: None) if context.bind_execution else lambda: None

    def _failed(self, reason):
        # Never forward an OS errorString, executable path or worker output.
        codes = {"external launch validation failed": "worker_launch_rejected",
                 "handshake timeout": "worker_handshake_timeout",
                 "heartbeat timeout": "worker_heartbeat_timeout",
                 "worker exited": "worker_exited",
                 "worker failed to start": "worker_start_failed"}
        self.pending.clear()
        self.error.emit(codes.get(reason, "worker_process_failed"))

    def _authorized(self):
        return not self.closed and callable(self.context.execution_authorized) and self.context.execution_authorized()

    def _launch(self):
        if not self._authorized() or not callable(self.context.worker_launch_factory):
            raise PermissionError("worker execution not authorized")
        return self.context.worker_launch_factory()

    @property
    def is_ready(self):
        return self._authorized() and self.supervisor.state == self.supervisor.READY

    def start(self):
        if not self._authorized() or not callable(self.context.worker_launch_factory):
            self.error.emit("worker_not_authorized")
            return False
        return self.supervisor.start()

    def request(self, operation, arguments=None):
        if not self.is_ready:
            return None
        rid = uuid4().hex
        self.pending.add(rid)
        if self.supervisor.send_request(operation, arguments, request_id=rid) is None:
            self.pending.discard(rid)
            return None
        return rid

    def cancel(self, request_id):
        if request_id not in self.pending:
            return False
        self.pending.discard(request_id)
        if self.is_ready:
            self.supervisor.send_request("cancel", {"request_id": request_id})
        return True

    def _response(self, message):
        if message.request_id not in self.pending or not self._authorized():
            return
        self.pending.discard(message.request_id)
        self.response.emit(message.request_id, dict(message.payload))

    def stop(self):
        self.pending.clear()
        self.supervisor.stop()

    def close(self):
        if self.closed:
            return
        self.closed = True
        self._unbind()
        self.stop()
