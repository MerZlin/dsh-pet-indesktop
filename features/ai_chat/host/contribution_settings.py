"""AI settings/drafts belong to the verified owner, never to management UI."""

from __future__ import annotations

from PySide6.QtWidgets import QAbstractButton, QComboBox, QDoubleSpinBox, QLineEdit, QPlainTextEdit, QSpinBox, QVBoxLayout, QWidget

from pet.settings_widgets import SettingRow

from . import settings_file_interpret
from .chat.ai_settings_page import _AiSettingsPage
from .config import AiConfiguration


class AiContributionSettings(QWidget):
    def __init__(self, context, parent=None):
        super().__init__(parent)
        self.config = AiConfiguration(context)
        self.page = _AiSettingsPage(self.config, self)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.page)
        self.file_controls = QWidget(self)
        self.file_controls.config = self.config
        settings_file_interpret.create_file_interpret_controls(self.file_controls)
        self.file_page = settings_file_interpret.build_file_interpret_page(self.file_controls)
        self.file_rows = list(self.file_page.findChildren(SettingRow))
        # The screen deep-link is removed by the screen contribution lifecycle;
        # it owns no AI draft fields and must not enter our retained row set.
        self.rows = [row for row in self.page.findChildren(SettingRow) if row.objectName() != "settingRow_vision_migration"] + self.file_rows
        self._disposed = False
        self._baseline = self._values()
        self.hide()

    def _values(self):
        result = {}
        for row in self.rows:
            fields: list[object] = []
            for widget in row.findChildren(QWidget):
                if widget in (self.page.key, self.page.vision_key):
                    continue
                if isinstance(widget, QLineEdit):
                    fields.append(widget.text())
                elif isinstance(widget, QPlainTextEdit):
                    fields.append(widget.toPlainText())
                elif isinstance(widget, QComboBox):
                    fields.append(widget.currentData())
                elif isinstance(widget, (QSpinBox, QDoubleSpinBox)):
                    fields.append(widget.value())
                elif isinstance(widget, QAbstractButton) and widget.isCheckable():
                    fields.append(widget.isChecked())
            result[row.objectName()] = fields
        return result

    def dirty(self):
        return not self._disposed and (
            bool(self.page.key.text() or self.page.vision_key.text())
            or self._values() != self._baseline
            or any(draft.get("key") or draft.get("vision_key") for draft in self.page._provider_drafts.values())
            or bool(self.page._test_thread and self.page._test_thread.is_alive())
        )

    def draft(self):
        return {"values": self._values(), "connection_test_active": bool(self.page._test_thread and self.page._test_thread.is_alive())}

    def confirm_save(self):
        if self._disposed or (self.page._test_thread and self.page._test_thread.is_alive()):
            return False
        self.page.save()
        settings_file_interpret.save_file_interpret_settings(self.file_controls, commit=False)
        if not self.config.save():
            return False
        self.page.key.clear()
        self.page.vision_key.clear()
        for draft in self.page._provider_drafts.values():
            draft.pop("key", None)
            draft.pop("vision_key", None)
        self._baseline = self._values()
        return True

    def discard_changes(self):
        if self._disposed or (self.page._test_thread and self.page._test_thread.is_alive()):
            return False
        # Explicit owning-UI discard keeps the same mounted rows and QObject
        # identities. Search and navigation still point to those live controls.
        self.config.reload()
        self.page.reload_configuration()
        defaults = settings_file_interpret._default_file_interpret_data()
        data = self.config.get("file_interpret") or {}
        self.file_controls.file_interpret_enabled_check.setChecked(bool(data.get("enabled", defaults["enabled"])))
        self.file_controls.file_interpret_interval_spin.setValue(
            settings_file_interpret._clamp_interval(data.get("progress_interval_seconds", defaults["progress_interval_seconds"]))
        )
        self._baseline = self._values()
        return True

    def dispose(self):
        self._disposed = True
        self.hide()
        self.deleteLater()
