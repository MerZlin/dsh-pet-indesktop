"""Qt-backed wakeup monitor for the installed feature state document.

The monitor never treats a filesystem notification as authoritative.  A watcher
or timer tick only schedules a read; :class:`FeatureInstallStateStore.read`
remains the source of truth and its revision is the value exposed to callers.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from PySide6.QtCore import QFileSystemWatcher, QMetaObject, QObject, Qt, QThread, QTimer, Signal, Slot

from .feature_install_state import FeatureInstallStateStore, StateResult


class _StateMonitorWorker(QObject):
    state_changed = Signal(object)
    revision_changed = Signal(int)
    error = Signal(str)

    def __init__(self, store: FeatureInstallStateStore, interval_ms: int) -> None:
        super().__init__()
        self._store = store
        self._interval_ms = max(50, int(interval_ms))
        self._watcher: QFileSystemWatcher | None = None
        self._timer: QTimer | None = None
        self._last_signature: tuple[Any, ...] | None = None
        self._stopped = False

    @staticmethod
    def _make_signature(result: StateResult) -> tuple[Any, ...]:
        state = result.state
        document = state.document() if state is not None else None
        return result.status, result.reason, document

    @Slot()
    def start(self) -> None:
        if self._watcher is not None:
            return
        self._stopped = False
        self._watcher = QFileSystemWatcher(self)
        self._watcher.directoryChanged.connect(self._on_path_changed)
        self._watcher.fileChanged.connect(self._on_path_changed)
        self._timer = QTimer(self)
        self._timer.setInterval(self._interval_ms)
        self._timer.timeout.connect(self._poll)
        self._refresh_watches()
        self._poll()
        self._timer.start()

    @Slot()
    def stop(self) -> None:
        self._stopped = True
        if self._timer is not None:
            self._timer.stop()
        if self._watcher is not None:
            self._watcher.deleteLater()
            self._watcher = None
        self._timer = None

    @Slot(str)
    def _on_path_changed(self, _path: str) -> None:
        if not self._stopped:
            self._refresh_watches()
            self._poll()

    def _refresh_watches(self) -> None:
        watcher = self._watcher
        if watcher is None:
            return
        old_paths = watcher.files() + watcher.directories()
        if old_paths:
            watcher.removePaths(old_paths)
        candidates = [self._store.root, self._store.state_path, self._store.root.parent]
        paths: list[str] = []
        for candidate in candidates:
            path = Path(candidate)
            if path.exists():
                paths.append(str(path))
        if paths:
            watcher.addPaths(paths)

    @Slot()
    def _poll(self) -> None:
        if self._stopped:
            return
        try:
            result = self._store.read()
        except BaseException:
            self.error.emit("state_read_failed")
            return
        signature = self._make_signature(result)
        if signature == self._last_signature:
            return
        self._last_signature = signature
        self.state_changed.emit(result)
        if result.state is not None and type(result.state.revision) is int:
            self.revision_changed.emit(result.state.revision)


class FeatureStateMonitor(QObject):
    """Watch install state from a dedicated Qt thread with polling fallback."""

    state_changed = Signal(object)
    revision_changed = Signal(int)
    error = Signal(str)

    def __init__(
        self,
        store: FeatureInstallStateStore,
        parent: QObject | None = None,
        *,
        interval_ms: int = 1000,
    ) -> None:
        super().__init__(parent)
        self._thread = QThread(self)
        self._worker = _StateMonitorWorker(store, interval_ms)
        self._worker.moveToThread(self._thread)
        self._worker.state_changed.connect(self.state_changed)
        self._worker.revision_changed.connect(self.revision_changed)
        self._worker.error.connect(self.error)
        self._thread.started.connect(self._worker.start)
        self._running = False

    @property
    def running(self) -> bool:
        return self._running and self._thread.isRunning()

    @property
    def thread(self) -> QThread:
        return self._thread

    def start(self) -> bool:
        if self._running:
            return False
        self._running = True
        self._thread.start()
        return True

    def stop(self) -> bool:
        if not self._running:
            return True
        if self._thread.isRunning():
            QMetaObject.invokeMethod(self._worker, "stop", Qt.ConnectionType.BlockingQueuedConnection)
            self._thread.quit()
            self._thread.wait(3000)
        self._running = False
        return not self._thread.isRunning()

    close = stop

    def __del__(self) -> None:
        try:
            self.stop()
        except RuntimeError:
            pass
