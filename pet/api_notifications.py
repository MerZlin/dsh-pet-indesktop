"""Queued same-process publication, plus committed-version polling across processes."""

import os
from threading import RLock

import shiboken6
from PySide6.QtCore import QObject, Qt, QTimer, Signal, Slot
from PySide6.QtWidgets import QApplication

_HUBS = {}
_LOCK = RLock()


class _Hub(QObject):
    changed = Signal()
    request_observed = Signal(str, str, str)


def _key(path):
    return os.path.normcase(str(path.resolve()))


def publish_api(path):
    with _LOCK:
        hub = _HUBS.get(_key(path))
        if hub is not None and shiboken6.isValid(hub):
            hub.changed.emit()


def observe_api_request(path, owner, purpose, version):
    """Record the accepted snapshot before queued saves can coalesce its grant."""
    with _LOCK:
        hub = _HUBS.get(_key(path))
        if hub is not None and shiboken6.isValid(hub):
            hub.request_observed.emit(owner, purpose, version)


class _Subscription(QObject):
    def __init__(self, path, bindings, version, callback, owner):
        app = QApplication.instance()
        super().__init__(app)
        self.bindings, self.version, self.callback = bindings, version, callback
        self.owner = owner
        self.previous = {purpose: version(purpose) for purpose in bindings()}
        with _LOCK:
            key = _key(path)
            hub = _HUBS.get(key)
            if hub is None or not shiboken6.isValid(hub):
                hub = _Hub(app)
                _HUBS[key] = hub
            self.hub = hub
        self.hub.changed.connect(self.poll, Qt.ConnectionType.QueuedConnection)
        self.hub.request_observed.connect(self.observe, Qt.ConnectionType.QueuedConnection)
        self.timer = QTimer(self)
        self.timer.setInterval(500)
        self.timer.timeout.connect(self.poll)
        self.timer.start()

    @Slot(str, str, str)
    def observe(self, owner, purpose, version):
        if not getattr(self, "closed", False) and owner == self.owner:
            self.previous[purpose] = version

    @Slot()
    def poll(self):
        if getattr(self, "closed", False):
            return
        try:
            purposes = set(self.previous) | set(self.bindings())
            latest = {p: self.version(p) for p in purposes}
        except (ValueError, OSError, PermissionError):
            latest = dict.fromkeys(self.previous, "unavailable")
        for purpose, value in latest.items():
            if value != self.previous.get(purpose, "unavailable"):
                self.callback(purpose, value == "unavailable")
        self.previous = latest

    def close(self):
        if getattr(self, "closed", False):
            return
        self.closed = True
        self.timer.stop()
        self.hub.changed.disconnect(self.poll)
        self.hub.request_observed.disconnect(self.observe)
        self.callback = lambda *args: None
        self.deleteLater()


def subscribe_api(path, bindings, version, callback, *, owner=None):
    if QApplication.instance() is None:
        return lambda: None
    subscription = _Subscription(path, bindings, version, callback, owner)
    return subscription.close
