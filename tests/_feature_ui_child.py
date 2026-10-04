"""Owned native Qt/High-DPI acceptance; captures only this generated dialog."""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

from PySide6.QtCore import QCoreApplication, QEvent, QEventLoop, QRect, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QScrollArea

from pet import feature_distribution
from pet.config import Config
from pet.feature_management_ui import FeatureManagementWidget
from pet.modern_settings_dialog import ModernSettingsDialog


def main():
    value = json.loads(sys.stdin.read())
    app = QApplication([])
    feature_distribution.BUILTIN_SCREEN = False
    dialog = ModernSettingsDialog(Config(base=Path(value["data"])), include_ai=False, standalone=True, initial_page="extensions")
    manager = dialog.feature_management
    try:
        dialog.menu_theme_select.setCurrentIndex(dialog.menu_theme_select.findData(value["theme"]))
        dialog.setStyleSheet(dialog.styleSheet() + "\n#featureManagement QLabel, #featureManagement QPushButton {font-size:18px;}")
        dialog.resize(value["width"], 760)
        dialog.show()
        widget = dialog.findChild(FeatureManagementWidget)
        deadline = time.monotonic() + 30
        while (manager.busy or widget.inspection is None) and time.monotonic() < deadline:
            app.processEvents(QEventLoop.ProcessEventsFlag.AllEvents, 20)
        assert widget.inspection is not None and not manager.busy
        if value["language"] == "en":
            widget.status_label.setText(
                "Waiting for all versions and previous generations to be naturally released; no user application or external process is forcibly closed."
            )
            widget.summary_label.setText(
                "Uninstall preserves profiles, encrypted credentials, bindings, memory, quota and chat history. Immutable confirmation is bound to this source fingerprint: "
                + "a" * 64
            )
            widget.install_button.setText("Choose an official package directory")
            widget.zip_button.setText("Choose an official package ZIP archive")
        dialog.select_page("extensions")
        QTest.qWaitForWindowExposed(dialog, 10000)
        app.processEvents(QEventLoop.ProcessEventsFlag.AllEvents, 20)
        assert dialog.devicePixelRatioF() >= value["scale"] - 0.05
        available_width = dialog.screen().availableGeometry().width()
        if value["width"] <= available_width:
            assert dialog.width() == value["width"]
        else:
            # Windows constrains a top-level window to this generated process's
            # virtualized screen. Record the actual viewport; never pretend
            # that a 1100 logical-pixel High-DPI screen existed on this machine.
            assert 720 <= dialog.width() <= available_width + 4
        assert widget.status_label.hasFocus()
        visited = []
        for _ in range(30):
            focus = app.focusWidget()
            if focus in (widget.install_button, widget.zip_button):
                visited.append(focus)
            if focus is widget.zip_button:
                break
            QTest.keyClick(focus or widget.status_label, Qt.Key.Key_Tab)
        assert visited == [widget.install_button, widget.zip_button], "enabled management actions must remain keyboard reachable in directory/ZIP order"
        dialog.select_page("extensions")
        assert widget.status_label.hasFocus(), "deep-link focus must be restored"
        for area in dialog.findChildren(QScrollArea):
            if area.isVisible():
                assert area.horizontalScrollBar().maximum() == 0
        for button in widget.buttons:
            assert button.accessibleName() and button.accessibleDescription()
            assert button.height() >= button.heightForWidth(button.width())
            assert button.mapTo(widget, button.rect().topRight()).x() < widget.width()
        label = widget.summary_label
        layout_deadline = time.monotonic() + 30
        while True:
            app.processEvents(QEventLoop.ProcessEventsFlag.AllEvents, 20)
            bound = label.fontMetrics().boundingRect(QRect(0, 0, label.width(), 10000), int(Qt.TextFlag.TextWordWrap), label.text())
            if (label.height() >= bound.height() and label.width() >= bound.width()) or time.monotonic() >= layout_deadline:
                break
        if label.height() < bound.height():
            dialog.grab().save(value["screenshot"])
        assert label.height() >= bound.height(), {
            "width": label.width(),
            "height": label.height(),
            "metrics_height": bound.height(),
            "qt_height_for_width": label.heightForWidth(label.width()),
            "text": label.text(),
        }
        assert bound.width() <= label.width(), {"paint_width": bound.width(), "label_width": label.width()}
        assert manager.startup is None and dialog._screen_component is None
        assert not any(name.startswith("_pet_official_screen_") for name in sys.modules)
        image = dialog.grab()
        assert image.save(value["screenshot"])
        print(
            json.dumps(
                {
                    "passed": True,
                    "device_pixel_ratio": dialog.devicePixelRatioF(),
                    "requested_width": value["width"],
                    "available_width": available_width,
                    "native_os_width_clamped": dialog.width() != value["width"],
                    "logical_size": [dialog.width(), dialog.height()],
                    "pixel_size": [image.width(), image.height()],
                    "font_height": label.fontMetrics().height(),
                    "theme": value["theme"],
                    "language": value["language"],
                    "tab_order": [widget.install_button.accessibleName(), widget.zip_button.accessibleName()],
                    "management_only": True,
                }
            ),
            flush=True,
        )
    finally:
        dialog.close()
        manager.close()
        dialog.deleteLater()
        QCoreApplication.sendPostedEvents(dialog, QEvent.Type.DeferredDelete)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
