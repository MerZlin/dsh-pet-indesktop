"""GUI-owned official settings lifecycle, separate from the page composition."""

from __future__ import annotations

import json
import sys

from PySide6.QtWidgets import QLabel, QMessageBox, QPlainTextEdit

from .settings_widgets import SettingRow, SettingsSection


def _prepare_feature_revocation(self, owner) -> bool:
    from .official_features import SCREEN_OWNER

    if owner == SCREEN_OWNER:
        return self._prepare_screen_revocation()
    component = self._feature_component(owner)
    if component is None or not component.dirty():
        return True
    answer = QMessageBox.question(
        self,
        "扩展有未保存编辑",
        "此扩展有未保存编辑。保存、放弃，还是取消本次操作？",
        QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Discard | QMessageBox.StandardButton.Cancel,
        QMessageBox.StandardButton.Cancel,
    )
    if answer == QMessageBox.StandardButton.Cancel:
        return False
    return component.discard_changes() if answer == QMessageBox.StandardButton.Discard else component.confirm_save()


def _prepare_screen_revocation(self) -> bool:
    component = self._screen_component
    if not component or not component.dirty():
        return True
    answer = QMessageBox.question(
        self,
        "撤销屏幕理解设置",
        "屏幕理解有未保存编辑。保存、放弃，还是取消本次撤销？",
        QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Discard | QMessageBox.StandardButton.Cancel,
        QMessageBox.StandardButton.Cancel,
    )
    if answer == QMessageBox.StandardButton.Cancel:
        return False
    if answer == QMessageBox.StandardButton.Discard:
        return component.discard_changes()
    return component.confirm_save()


def _on_feature_contribution_changed(self, owner: str, state: str) -> None:
    from .official_features import AI_OWNER, SCREEN_OWNER

    if owner == AI_OWNER:
        self._on_ai_contribution_changed(state)
        return
    if owner != SCREEN_OWNER:
        return
    available = set(self.menu_available_actions)
    available.difference_update({"look_screen", "proactive_screen"})
    if state == "enabled":
        available.add("look_screen")
        if sys.platform == "win32":
            available.add("proactive_screen")
    self.menu_available_actions = frozenset(available)
    editor = self.menu_layout_editor
    editor.available_actions = self.menu_available_actions
    # Re-render from the retained raw tree; unavailable actions are NOT deleted.
    editor.set_layout(editor.value())
    if state in ("enabled", "disabled"):
        return
    component = self._screen_component
    removed = set(component.rows) if component else set()
    # Legacy chat deep-link is a screen-owned contribution as well.
    jump = self.findChild(SettingRow, "settingRow_vision_migration")
    if jump:
        removed.add(jump)
    if component:
        if state == "fault":
            self._feature_drafts[owner] = component.draft()
            notice = QLabel("屏幕理解设置发生故障，已停止执行。非敏感草稿保留在本对话框，密码已清除。", self)
            notice.setWordWrap(True)
            self.layout().addWidget(notice)
            # Host-owned, read-only recovery view; never call a faulted component
            # to save, and never retain its password editor or secure references.
            draft_view = QPlainTextEdit(self)
            draft_view.setObjectName("screenContributionDraft")
            draft_view.setReadOnly(True)
            draft_view.setAccessibleName("屏幕理解非敏感草稿")
            draft_view.setMaximumHeight(120)
            draft_view.setPlainText(json.dumps(self._feature_drafts[owner], ensure_ascii=False, indent=2))
            self.layout().addWidget(draft_view)
    for row in removed:
        section = row.parentWidget()
        while section and not isinstance(section, SettingsSection):
            section = section.parentWidget()
        row.hide()
        row.setParent(None)
        row.deleteLater()
        if section and not section.findChildren(SettingRow):
            section.hide()
    if component:
        component.dispose()
    self._search_rows = [row for row in self._search_rows if row not in removed]
    self._search_matches = []
    self._search_index = -1
    self._feature_components.pop(SCREEN_OWNER, None)
    self._screen_component = None
    self.screen_settings_page = None
    for name in tuple(vars(self)):
        if name.startswith(("pro_", "_pro_")):
            delattr(self, name)
    if self.ai_page is not None:
        try:
            self.ai_page.screen_settings_requested.disconnect()
        except RuntimeError:
            pass
    self._search_settings(self.search_edit.text())


def _on_ai_contribution_changed(self, state):
    from .official_features import AI_OWNER

    available = set(self.menu_available_actions)
    available.discard("chat")
    if state == "enabled":
        available.add("chat")
    self.menu_available_actions = frozenset(available)
    self.menu_layout_editor.available_actions = self.menu_available_actions
    self.menu_layout_editor.set_layout(self.menu_layout_editor.value())
    if state in ("enabled", "disabled"):
        return
    component = self._feature_components.pop(AI_OWNER, None)
    if component is not None:
        if state == "fault":
            self._feature_drafts[AI_OWNER] = component.draft()
        removed = set(component.rows)
        for row in removed:
            row.hide()
            row.setParent(None)
            row.deleteLater()
        component.dispose()
        self._search_rows = [row for row in self._search_rows if row not in removed]
    self._ai_component = None
    self.ai_page = None
    self.include_ai = False
    self._search_matches = []
    self._search_index = -1
    self._search_settings(self.search_edit.text())


def release_contributions(self) -> None:
    self._mod_settings.close()
    self.mod_controller.close()
    while self._mod_preparations:
        self._mod_preparations.pop()()
    self._draft_unsubscribe()
    if self._owns_feature_management:
        from .feature_management import close_official_management

        close_official_management(self.feature_host)
    for component in tuple(self._feature_components.values()):
        component.dispose()
    self._feature_components.clear()
    self._screen_component = None
    self._ai_component = None
    self.ai_page = None
    self._feature_unsubscribe()
    self._feature_prepare_unsubscribe()
    for owner in self.feature_managers:
        self.feature_host.detach(owner, self._feature_scope)
