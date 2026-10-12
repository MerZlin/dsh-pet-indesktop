"""Qt-owned file notifications that wake a background JSONL reader."""
import logging
from pathlib import Path

from PySide6.QtCore import QFileSystemWatcher, QObject, QTimer, Slot


log = logging.getLogger(__name__)


class AgentFileWatch(QObject):
    def __init__(self, wake, parent=None):
        super().__init__(parent)
        self.wake = wake
        self.generation = -1
        self.paths = set()
        self.watcher = None
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.setInterval(40)
        self.timer.timeout.connect(self._changed)

    def _schedule(self, path):
        # Do not restart: a stream of writes must not postpone the wake forever.
        if self.generation >= 0 and not self.timer.isActive():
            self.timer.start()

    @Slot(object, int)
    def update_paths(self, paths, generation):
        if generation != self.generation:
            return
        if self.watcher is None:
            self.watcher = QFileSystemWatcher(self)
            self.watcher.fileChanged.connect(self._schedule)
            self.watcher.directoryChanged.connect(self._schedule)
        self.paths = {str(Path(path).absolute()) for path in paths}
        added = self._rearm()
        if added:
            # Catch writes between the reader's last check and registration.
            self.wake.set()

    def _rearm(self):
        wanted = set()
        for name in self.paths:
            path = Path(name)
            if path.is_file():
                wanted.add(str(path))
            parent = path.parent
            while not parent.is_dir() and parent != parent.parent:
                parent = parent.parent
            if parent.is_dir():
                wanted.add(str(parent))
        current = set(self.watcher.files()) | set(self.watcher.directories())
        removed = current - wanted
        if removed:
            self.watcher.removePaths(sorted(removed))
        added = wanted - current
        if added:
            failed = set(self.watcher.addPaths(sorted(added)))
            if failed:
                log.warning("Agent file watch registration failed: %s", sorted(failed))
            return bool(added - failed)
        return False

    def _changed(self):
        if self.generation < 0:
            return
        self._rearm()
        self.wake.set()

    def stop(self):
        self.generation = -1
        self.timer.stop()
        self.paths.clear()
        if self.watcher is not None:
            current = self.watcher.files() + self.watcher.directories()
            if current:
                self.watcher.removePaths(current)
            self.watcher.deleteLater()
            self.watcher = None
