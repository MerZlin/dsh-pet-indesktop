"""Cross-process catalog invalidation; package state remains in its own service."""
from __future__ import annotations

import logging
from pathlib import Path
from uuid import uuid4

from PySide6.QtCore import QFileSystemWatcher, QObject, QTimer, Signal

from .feature_state_io import StateError, atomic_write, safe_path

_LOG = logging.getLogger(__name__)


def publish_catalog_change(data_root: Path) -> None:
    # A notification token only: no second ledger, paths, credentials or drafts.
    atomic_write(Path(data_root) / "mod-catalog.revision", uuid4().hex.encode("ascii"))


class ModCatalogMonitor(QObject):
    changed = Signal()

    def __init__(self, data_root: Path, parent=None):
        super().__init__(parent)
        self.root = Path(data_root)
        self.watcher = QFileSystemWatcher(self)
        self.watcher.directoryChanged.connect(self.check)
        self.watcher.fileChanged.connect(self.check)
        self.timer = QTimer(self)
        self.timer.setInterval(1500)
        self.timer.timeout.connect(self.check)
        self._stamp = None
        self._closed = False
        self.check()
        self.timer.start()

    def check(self, *_args):
        if self._closed:
            return
        paths = [self.root, self.root / "plugins", self.root / "content/characters", self.root / "mod-catalog.revision"]
        stamp = []
        watch = []
        for path in paths:
            try:
                safe_path(path)
                info = path.stat()
                stamp.append((str(path), info.st_mtime_ns, info.st_size))
                watch.append(str(path))
            except (OSError, ValueError, StateError):
                stamp.append((str(path), None, None))
        registered = set(self.watcher.files() + self.watcher.directories())
        missing = [path for path in watch if path not in registered]
        if missing:
            self.watcher.addPaths(missing)
        if stamp != self._stamp:
            self._stamp = stamp
            self.changed.emit()

    def close(self):
        self._closed = True
        self.timer.stop()
        paths = self.watcher.files() + self.watcher.directories()
        if paths:
            self.watcher.removePaths(paths)


def refresh_feature_catalog(config, host, managers, *, role):
    from .feature_management import attach_feature_management, discover_local_feature_ids

    for owner in discover_local_feature_ids(config):
        if owner not in managers:
            try:
                managers[owner] = attach_feature_management(config, host, feature_id=owner, role=role, management_only=True)
            except (OSError, RuntimeError, TypeError, ValueError):
                _LOG.exception("MOD discovery failed: %s", owner)
    for manager in tuple(managers.values()):
        try:
            manager.ensure_loaded()
        except (OSError, RuntimeError, TypeError, ValueError):
            _LOG.exception("MOD load refresh failed: %s", manager.feature_id)
