"""Queued GUI preparation. OS leases, not GUI replies, prove code release."""

from __future__ import annotations

import os
import threading

from PySide6.QtCore import QObject, Qt, Signal, Slot

from .feature_lifecycle_contract import LifecyclePrepareRequest
from .feature_package_transactions import RuntimePreparation
from .feature_state_io import StateError


class _Reply:
    def __init__(self, request):
        self.request = request
        self.done = threading.Event()
        self.cancelled = threading.Event()
        self.result = RuntimePreparation("awaiting_release", "lifecycle_unavailable")


class FeatureLifecycleEndpoint(QObject):
    """Created and retained on the GUI/host owner thread; never moved."""

    requested = Signal(object)
    draft_blocked = Signal(object)

    def __init__(self, store, host, parent=None):
        super().__init__(parent)
        self.store, self.host = store, host
        self._owner_thread_id = threading.get_ident()
        self._closed = False
        self._draft_guards = {}
        self.requested.connect(self._prepare, Qt.ConnectionType.QueuedConnection)

    def register_draft(self, identity: str, dirty):
        self.host.registry.check_thread()
        token = object()
        self._draft_guards[token] = identity, dirty

        def remove():
            self._draft_guards.pop(token, None)

        return remove

    def close(self):
        self._closed = True
        self._draft_guards.clear()

    @Slot(object)
    def _prepare(self, reply):
        if reply.cancelled.is_set():
            reply.done.set()
            return
        request = reply.request
        try:
            if self._closed:
                raise StateError("lifecycle_endpoint_closed")
            if not isinstance(request, LifecyclePrepareRequest) or request.operation not in ("install", "upgrade", "rollback", "uninstall", "disable"):
                raise StateError("lifecycle_request_invalid")
            state = self.store.read().state
            if (
                state is None
                or request.feature_id != self.store.feature_id
                or state.revision != request.revision
                or not set(request.versions) <= set(state.versions)
            ):
                raise StateError("revision_conflict")
            drafts = []
            for identity, dirty in tuple(self._draft_guards.values()):
                if dirty():
                    drafts.append(identity)
            if drafts:
                reply.result = RuntimePreparation("draft_blocked", "unsaved_settings", details={"drafts": tuple(drafts), "pid": os.getpid()})
                self.draft_blocked.emit(reply.result.details)
            else:
                owner = self.store.feature_id
                if request.operation == "disable":
                    self.host.disable(owner)
                elif not self.host.remove(owner):
                    reply.result = RuntimePreparation("draft_blocked", "unsaved_settings")
                    reply.done.set()
                    return
                # stop() is requested through FeatureHost callbacks; callers
                # still must wait for every native Worker/host/settings lease.
                reply.result = RuntimePreparation(details={"pid": os.getpid(), "prepared_nonce": request.nonce})
        except Exception:
            reply.result = RuntimePreparation("awaiting_release", "lifecycle_prepare_failed")
        finally:
            reply.done.set()


class QueuedFeatureLifecycle:
    """Backend wait is bounded; GUI thread never waits for itself."""

    def __init__(self, endpoint: FeatureLifecycleEndpoint, *, timeout: float = 10):
        if not 0.01 <= timeout <= 30:
            raise ValueError("lifecycle timeout invalid")
        self.endpoint, self.timeout = endpoint, timeout

    def prepare(self, request: LifecyclePrepareRequest) -> RuntimePreparation:
        # QObject.thread() reparents its borrowed QThread wrapper in PySide.
        # A cyclic endpoint can then let GC destroy Qt's adopted main thread.
        # Match the creating-thread contract without borrowing that wrapper.
        if threading.get_ident() == self.endpoint._owner_thread_id:
            return RuntimePreparation("awaiting_release", "queued_lifecycle_requires_background")
        reply = _Reply(request)
        try:
            self.endpoint.requested.emit(reply)
        except RuntimeError:
            return RuntimePreparation("awaiting_release", "lifecycle_endpoint_closed")
        if not reply.done.wait(self.timeout):
            reply.cancelled.set()
            return RuntimePreparation("awaiting_release", "lifecycle_prepare_timeout")
        return reply.result
