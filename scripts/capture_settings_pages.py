#!/usr/bin/env python3
"""Capture every settings domain for repeatable visual acceptance."""

from __future__ import annotations

import argparse
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from PySide6.QtCore import QPoint, Qt  # noqa: E402
from PySide6.QtGui import QColor, QPalette  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from pet import modern_settings_dialog as settings_mod  # noqa: E402
from pet.config import Config  # noqa: E402
from pet.modern_settings_dialog import (  # noqa: E402
    ModernSettingsDialog,
    SettingsDisclosureHeader,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("output", type=Path)
    parser.add_argument("--width", type=int, default=1100)
    parser.add_argument("--height", type=int, default=760)
    parser.add_argument("--expanded-ai", action="store_true")
    parser.add_argument("--dark", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    app = QApplication.instance() or QApplication([])
    if args.dark:
        palette = QPalette()
        palette.setColor(QPalette.ColorRole.Window, QColor("#202024"))
        palette.setColor(QPalette.ColorRole.WindowText, QColor("#e4e4e9"))
        palette.setColor(QPalette.ColorRole.Base, QColor("#2a2a30"))
        palette.setColor(QPalette.ColorRole.Text, QColor("#e4e4e9"))
        palette.setColor(QPalette.ColorRole.Button, QColor("#3a3a42"))
        palette.setColor(QPalette.ColorRole.ButtonText, QColor("#e4e4e9"))
        palette.setColor(QPalette.ColorRole.Highlight, QColor("#0a84ff"))
        palette.setColor(QPalette.ColorRole.HighlightedText, Qt.GlobalColor.white)
        app.setPalette(palette)
    settings_mod.autostart_mod.is_enabled = lambda: False
    with tempfile.TemporaryDirectory(prefix="pet-settings-capture-") as temp_dir:
        dialog = ModernSettingsDialog(Config(Path(temp_dir)), include_ai=True)
        dialog.resize(args.width, args.height)
        dialog.show()
        app.processEvents()
        for index in range(dialog.sidebar.count()):
            dialog.sidebar.setCurrentRow(index)
            page = dialog.pages.currentWidget()
            scroll = page.findChild(settings_mod.QScrollArea, "settingsScroll")
            if scroll is not None:
                scroll.verticalScrollBar().setValue(0)
            app.processEvents()
            label = dialog.sidebar.item(index).text()
            target = args.output / f"{index + 1:02d}-{label}.png"
            if not dialog.grab().save(str(target)):
                raise RuntimeError(f"failed to save {target}")

        if args.expanded_ai:
            dialog.sidebar.setCurrentRow(5)
            page = dialog.pages.currentWidget()
            header = next(
                item
                for item in dialog.findChildren(SettingsDisclosureHeader)
                if item.text() == "生成参数（高级）"
                and page.isAncestorOf(item)
            )
            if not header.isChecked():
                header.click()
            app.processEvents()
            scroll = page.findChild(settings_mod.QScrollArea, "settingsScroll")
            if scroll is not None:
                header_y = header.mapTo(scroll.widget(), QPoint(0, 0)).y()
                scroll.verticalScrollBar().setValue(max(0, header_y - 48))
            app.processEvents()
            target = args.output / "08-AI 与对话-高级展开.png"
            if not dialog.grab().save(str(target)):
                raise RuntimeError(f"failed to save {target}")
        dialog.reject()
        app.processEvents()
    return 0


if __name__ == "__main__":
    os.environ.setdefault("QT_QPA_PLATFORM", "cocoa")
    raise SystemExit(main())
