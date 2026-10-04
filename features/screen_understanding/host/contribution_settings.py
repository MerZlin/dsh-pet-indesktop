"""Settings contribution ownership: editor, strategy, drafts and cleanup."""

from __future__ import annotations

import sys

from PySide6.QtCore import QSignalBlocker, QTimer
from PySide6.QtWidgets import QAbstractButton, QComboBox, QDoubleSpinBox, QLineEdit, QPlainTextEdit, QSpinBox, QWidget

from pet.settings_widgets import SettingRow, ToggleSwitch

from .settings import ScreenSettingsPage
from .strategy_settings import StrategySettings


class ScreenContributionSettings(QWidget):
    def __init__(self, context, parent=None):
        super().__init__(parent)
        self.context = context
        self.vision = ScreenSettingsPage(context.vision, self)
        self.vision_rows = list(self.vision.findChildren(SettingRow))
        self.strategy = StrategySettings(context, self) if sys.platform == "win32" else None
        self.rows = self.vision_rows + (self.strategy.rows if self.strategy else [])
        self._disposed = False
        self._vision_baseline = self._values(self.vision_rows)
        self._strategy_baseline = self._values(self.strategy.rows) if self.strategy else {}
        self.vision.saved.connect(self._vision_saved)
        self.hide()

    def _values(self, rows):
        result = {}
        for row in rows:
            fields: list[str | int | float | bool | None] = []
            for widget in row.findChildren(QWidget):
                if widget is self.vision.key_edit:
                    continue
                if isinstance(widget, QLineEdit):
                    fields.append(widget.text())
                elif isinstance(widget, QPlainTextEdit):
                    fields.append(widget.toPlainText())
                elif isinstance(widget, QComboBox):
                    fields.append(widget.currentData())
                elif isinstance(widget, (QSpinBox, QDoubleSpinBox)):
                    fields.append(widget.value())
                elif isinstance(widget, ToggleSwitch) or (isinstance(widget, QAbstractButton) and widget.isCheckable()):
                    fields.append(widget.isChecked())
            result[row.objectName()] = fields
        return result

    def _vision_saved(self):
        self._vision_baseline = self._values(self.vision_rows)

    def dirty(self):
        if self._disposed:
            return False
        return (
            bool(self.vision.key_edit.text())
            or self._values(self.vision_rows) != self._vision_baseline
            or (self.strategy is not None and self._values(self.strategy.rows) != self._strategy_baseline)
        )

    def draft(self):
        # No password value, keyring reference or request payload is exported.
        return {"vision": self._values(self.vision_rows), "strategy": self._values(self.strategy.rows) if self.strategy else {}}

    def save_strategy(self):
        # Outer Apply deliberately does NOT save independent vision/key drafts.
        if not self._disposed and self.strategy:
            self.strategy.save()

    def strategy_saved(self):
        if not self._disposed:
            self._strategy_baseline = self._values(self.strategy.rows) if self.strategy else {}

    def confirm_save(self):
        if self._disposed:
            return False
        if self._values(self.vision_rows) != self._vision_baseline or self.vision.key_edit.text():
            self.vision._save()
            if self._values(self.vision_rows) != self._vision_baseline or self.vision.key_edit.text():
                return False
        self.save_strategy()
        if not self.context.preferences.flush():
            return False
        self._strategy_baseline = self._values(self.strategy.rows) if self.strategy else {}
        return True

    def discard_changes(self):
        """Explicit owning-UI action only; restore baseline without persisting it."""
        if self._disposed:
            return False
        for rows, baseline in ((self.vision_rows, self._vision_baseline), (self.strategy.rows if self.strategy else [], self._strategy_baseline)):
            for row in rows:
                values = iter(baseline.get(row.objectName(), ()))
                for widget in row.findChildren(QWidget):
                    if widget is self.vision.key_edit:
                        continue
                    if not isinstance(widget, (QLineEdit, QPlainTextEdit, QComboBox, QSpinBox, QDoubleSpinBox, ToggleSwitch)) and not (
                        isinstance(widget, QAbstractButton) and widget.isCheckable()
                    ):
                        continue
                    value = next(values)
                    with QSignalBlocker(widget):
                        if isinstance(widget, QLineEdit):
                            widget.setText(value)
                        elif isinstance(widget, QPlainTextEdit):
                            widget.setPlainText(value)
                        elif isinstance(widget, QComboBox):
                            widget.setCurrentIndex(widget.findData(value))
                        elif isinstance(widget, (QSpinBox, QDoubleSpinBox)):
                            widget.setValue(value)
                        else:
                            widget.setChecked(value)
        self.vision.key_edit.clear()
        return not self.dirty()

    def dispose(self):
        if self._disposed:
            return
        self._disposed = True
        self.vision.key_edit.clear()
        for timer in self.findChildren(QTimer):
            timer.stop()
            timer.timeout.disconnect()
        for row in self.rows:
            for widget in row.findChildren(QWidget):
                widget.blockSignals(True)
            row.setEnabled(False)
            row.hide()
            row.setParent(None)
            row.deleteLater()
        self.vision.blockSignals(True)
        self.vision.deleteLater()
        self.deleteLater()
