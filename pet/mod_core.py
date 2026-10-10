"""Core-owned adapters for generic MOD mounts and resource controls."""
from __future__ import annotations

from PySide6.QtCore import QObject

from .mod_catalog import ModCatalogMonitor, refresh_feature_catalog
from .mod_resource_ipc import ResourceServer, handle_resource_request
from .mod_runtime import GenericModMounts


class CoreModSupport(QObject):
    def __init__(self, shell):
        super().__init__()
        self.shell = shell
        self.closed = False
        self.mounts = GenericModMounts(shell.feature_host, self.context)
        self.monitor = ModCatalogMonitor(shell.config.dir, self)
        self.monitor.changed.connect(self.refresh)
        self.resources = ResourceServer(shell.config.dir, lambda request: handle_resource_request(shell, request), self)

    def context(self, owner):
        manager = self.shell.feature_managers.get(owner)
        startup = getattr(manager, "startup", None)
        return getattr(startup, "context", None)

    def refresh(self):
        if self.closed:
            return
        shell = self.shell
        refresh_feature_catalog(shell.config, shell.feature_host, shell.feature_managers, role="core")
        self.mounts.sync()
        from . import catalog
        catalog.character_body_box.cache_clear()
        catalog.character_head_box.cache_clear()
        if getattr(shell, "tray", None) is not None:
            shell._refresh_tray_menu()

    def close(self):
        if self.closed:
            return
        self.closed = True
        self.monitor.close()
        self.resources.close()
        self.mounts.close()
