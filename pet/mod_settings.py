"""Incremental settings contribution mounts; unrelated editors retain identity."""
from __future__ import annotations

import logging

from PySide6.QtCore import QObject, QTimer, Slot
from PySide6.QtWidgets import QPushButton, QVBoxLayout, QWidget

from .mod_catalog import ModCatalogMonitor, refresh_feature_catalog
from .settings_widgets import ResponsiveActionRow, SettingRow, SettingsSection

_LOG = logging.getLogger(__name__)


class SettingsModSupport(QObject):
    def __init__(self, dialog):
        super().__init__(dialog)
        self.dialog = dialog
        self.closed = False
        self._refreshing = False
        self._registered = set(dialog.feature_managers)
        self.panels = {}
        self.monitor = ModCatalogMonitor(dialog.config.dir, self)
        self.monitor.changed.connect(self.refresh)
        self._initial = QTimer(self)
        self._initial.setSingleShot(True)
        self._initial.timeout.connect(self.sync)
        self._initial.start(0)

    def queue_sync(self):
        if not self.closed:
            self._initial.start(0)

    def register(self, owner, manager):
        dialog = self.dialog
        dialog.feature_managers[owner] = manager
        if owner not in self._registered:
            self._registered.add(owner)
            if manager.endpoint is not None:
                dialog._feature_draft_unsubscribers.append(manager.endpoint.register_draft(dialog._feature_scope, lambda: dialog._feature_draft_dirty(owner)))
            dialog._mod_preparations.append(dialog.feature_host.before_remove(owner, lambda: dialog._prepare_feature_revocation(owner)))
        dialog.mod_controller.connect_managers()

    @Slot()
    def refresh(self):
        if self.closed or self._refreshing:
            return
        self._refreshing = True
        try:
            dialog = self.dialog
            refresh_feature_catalog(dialog.config, dialog.feature_host, dialog.feature_managers, role="settings")
            for owner, manager in tuple(dialog.feature_managers.items()):
                self.register(owner, manager)
            self.sync()
            dialog.mod_center.refresh()
            refresh = getattr(dialog, "_refresh_character_choices", None)
            if callable(refresh):
                refresh()
        finally:
            self._refreshing = False

    @Slot()
    def sync(self):
        if self.closed:
            return
        dialog = self.dialog
        from .official_features import AI_OWNER, SCREEN_OWNER
        for owner in tuple(dialog.feature_managers):
            state = dialog.feature_host.state(owner)
            if state not in ("enabled", "disabled"):
                panel = self.panels.pop(owner, None)
                if panel is not None:
                    panel.hide()
                    panel.deleteLater()
                # Legacy official lifecycle owns disposing its own component.
                if owner not in (AI_OWNER, SCREEN_OWNER):
                    component = dialog._feature_components.pop(owner, None)
                    if component is not None:
                        if state == "fault":
                            dialog._feature_drafts[owner] = component.draft()
                        component.dispose()
                continue
            if owner in dialog._feature_components or state != "enabled":
                continue
            manager = dialog.feature_managers[owner]
            context = getattr(getattr(manager, "startup", None), "context", None)
            handle = dialog.feature_host.settings(owner, dialog._feature_scope)
            if context is None or handle is None:
                continue
            try:
                component = handle.create(context, dialog)
                if component is None:
                    continue
                for method in ("dirty", "draft", "confirm_save", "discard_changes", "dispose"):
                    if not callable(getattr(component, method, None)):
                        raise TypeError("MOD settings lifecycle is incomplete")
                rows = list(getattr(component, "rows", ()))
                if not rows:
                    widget = getattr(component, "widget", component)
                    if not isinstance(widget, QWidget):
                        raise TypeError("MOD settings must expose a QWidget")
                    rows = [SettingRow("mod_settings_" + owner, owner, "扩展设置", widget, stacked=True)]
                # Keep existing special-purpose adapters only for pre-v1 DLCs;
                # third-party mounts never branch on their factory or identity.
                if owner == AI_OWNER:
                    dialog._ai_component = component
                    dialog.ai_page = component.page
                    dialog.include_ai = True
                elif owner == SCREEN_OWNER:
                    dialog._screen_component = component
                    dialog.screen_settings_page = component.vision
                dialog._feature_components[owner] = component
                panel = QWidget(dialog.mod_center)
                layout = QVBoxLayout(panel)
                layout.setContentsMargins(0, 0, 0, 0)
                name = next((entry.name for entry in dialog.mod_controller.entries() if entry.id == owner), owner)
                section = SettingsSection(name + " · 设置", rows, panel, advanced=True)
                layout.addWidget(section)
                save, discard = QPushButton("保存此扩展设置", panel), QPushButton("放弃修改", panel)
                save.clicked.connect(component.confirm_save)
                discard.clicked.connect(component.discard_changes)
                layout.addWidget(ResponsiveActionRow(save, [discard], panel))
                dialog.mod_center.layout().addWidget(panel)
                self.panels[owner] = panel
            except Exception:
                _LOG.exception("MOD settings could not mount: %s", owner)
                dialog.feature_host.fault(owner, "settings_factory_failed")
        import shiboken6
        previous_rows = dialog._search_rows
        rows = [row for row in previous_rows if shiboken6.isValid(row) and dialog.isAncestorOf(row)]
        for row in dialog.findChildren(SettingRow):
            if row not in rows:
                rows.append(row)
        if rows != previous_rows:
            dialog._search_rows = rows
            dialog._search_matches = [row for row in dialog._search_matches if row in rows]
            dialog._search_index = min(dialog._search_index, len(dialog._search_matches) - 1)
            if dialog.search_edit.text().strip():
                dialog._search_settings(dialog.search_edit.text())

    def show(self, owner):
        self.refresh()
        component = self.dialog._feature_components.get(owner)
        if component is None:
            self.dialog.mod_center.summary.setText("请先启用扩展；如仍不可用，请检查对应行的操作结果。")
            return
        panel = self.panels.get(owner)
        if panel is not None:
            for section in panel.findChildren(SettingsSection):
                if section.toggle is not None:
                    section.toggle.setChecked(True)
        rows = panel.findChildren(SettingRow) if panel is not None else list(getattr(component, "rows", ()))
        if rows:
            self.dialog._search_settings(rows[0].objectName())

    def close(self):
        self.closed = True
        self._initial.stop()
        self.monitor.close()
