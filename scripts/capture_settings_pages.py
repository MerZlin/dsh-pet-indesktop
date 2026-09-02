#!/usr/bin/env python3
"""Capture every settings capability domain for visual acceptance."""
from __future__ import annotations

import argparse
import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("PYTHON_KEYRING_BACKEND", "keyring.backends.null.Keyring")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtCore import QPoint, Qt  # noqa: E402
from PySide6.QtGui import QColor, QPalette  # noqa: E402
from PySide6.QtWidgets import QApplication, QScrollArea, QWidget  # noqa: E402

from pet import modern_settings_dialog as settings_mod  # noqa: E402
from pet.config import Config  # noqa: E402
from pet.modern_settings_dialog import (  # noqa: E402
    ModernSettingsDialog,
    SettingRow,
    SettingsDisclosureHeader,
)


def _safe_filename(label: str) -> str:
    return label.replace("/", "-").replace(" ", "-")


def _apply_dark_palette(app: QApplication) -> None:
    palette = QPalette(app.palette())
    palette.setColor(QPalette.ColorRole.Window, QColor("#202024"))
    palette.setColor(QPalette.ColorRole.WindowText, QColor("#f0f0f5"))
    palette.setColor(QPalette.ColorRole.Base, QColor("#2a2a30"))
    palette.setColor(QPalette.ColorRole.Text, QColor("#e0e0e6"))
    palette.setColor(QPalette.ColorRole.Button, QColor("#3a3a42"))
    palette.setColor(QPalette.ColorRole.ButtonText, QColor("#e0e0e6"))
    palette.setColor(QPalette.ColorRole.Highlight, QColor("#0a84ff"))
    palette.setColor(QPalette.ColorRole.HighlightedText, Qt.GlobalColor.white)
    app.setPalette(palette)


def _apply_font_scale(root: QWidget, scale: float) -> None:
    if scale == 1.0:
        return
    for widget in [root, *root.findChildren(QWidget)]:
        font = widget.font()
        pixels = font.pixelSize()
        if pixels > 0:
            font.setPixelSize(max(1, round(pixels * scale)))
            widget.setFont(font)


def _apply_extreme_copy(dialog: ModernSettingsDialog) -> None:
    suffix = "（跨平台同步与自动恢复策略的超长本地化标题示例）"
    hint = (
        "用于验证 macOS、Windows 与 Linux 在窄窗口和放大字体下均能完整换行，"
        "且右侧控件、键盘焦点与滚动区域仍然可达。"
    )
    for page_index in range(dialog.pages.count()):
        page = dialog.pages.widget(page_index)
        rows = page.findChildren(SettingRow)
        if not rows:
            continue
        rows[0].label.setText(rows[0].label.text() + suffix)
        rows[0].hint_label.setText(hint)


def _expand_toggle_dependencies(dialog: ModernSettingsDialog) -> None:
    """Expose every macOS-available dependent group for visual acceptance."""
    for toggle in (
        dialog.self_talk_check,
        dialog.menu_translucent_check,
        dialog.island_enabled_check,
        dialog.island_icon_check,
        dialog.island_info_check,
        dialog.egg_enabled_check,
        dialog.collision_enabled_check,
        dialog.collision_sound_check,
        dialog.click_sound_check,
        dialog.agent_sound_check,
    ):
        toggle.setChecked(True)
    dialog.island_info_mode_select.setCurrentData("custom")


def capture(args: argparse.Namespace) -> None:
    app = QApplication.instance() or QApplication([])
    if args.dark:
        _apply_dark_palette(app)
    settings_mod.autostart_mod.is_enabled = lambda: False
    destination = Path(args.destination).resolve()
    destination.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="desktop-pet-settings-capture-") as data_dir:
        dialog = ModernSettingsDialog(Config(Path(data_dir)), include_ai=True)
        dialog.resize(args.width, args.height)
        dialog.show()
        app.processEvents()
        if args.expanded_toggles:
            _expand_toggle_dependencies(dialog)
            app.processEvents()
        if args.extreme_copy:
            _apply_extreme_copy(dialog)
        _apply_font_scale(dialog, args.font_scale)
        dialog.resize(args.width + 1, args.height)
        app.processEvents()
        dialog.resize(args.width, args.height)
        app.processEvents()

        for page_index in range(dialog.pages.count()):
            dialog.sidebar.setCurrentRow(page_index)
            page = dialog.pages.widget(page_index)
            for scroll in page.findChildren(QScrollArea):
                scroll.verticalScrollBar().setValue(0)
                scroll.horizontalScrollBar().setValue(0)
            app.processEvents()
            label = dialog.sidebar.item(page_index).text()
            target = destination / f"{page_index + 1:02d}-{_safe_filename(label)}.png"
            if not dialog.grab().save(str(target)):
                raise RuntimeError(f"failed to save screenshot: {target}")

        if args.expanded_ai:
            ai_index = next(
                index
                for index in range(dialog.sidebar.count())
                if dialog.sidebar.item(index).text() == "AI 与对话"
            )
            dialog.sidebar.setCurrentRow(ai_index)
            page = dialog.pages.currentWidget()
            header = next(
                item
                for item in dialog.findChildren(SettingsDisclosureHeader)
                if item.text() == "生成参数（高级）" and page.isAncestorOf(item)
            )
            if not header.isChecked():
                header.click()
            scroll = page.findChild(QScrollArea, "settingsScroll")
            if scroll is not None:
                header_y = header.mapTo(scroll.widget(), QPoint(0, 0)).y()
                scroll.verticalScrollBar().setValue(max(0, header_y - 48))
            app.processEvents()
            target = destination / "08-AI 与对话-高级展开.png"
            if not dialog.grab().save(str(target)):
                raise RuntimeError(f"failed to save screenshot: {target}")
        dialog.close()
        app.processEvents()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("destination")
    parser.add_argument("--width", type=int, default=1100)
    parser.add_argument("--height", type=int, default=760)
    parser.add_argument("--dark", action="store_true")
    parser.add_argument("--expanded-ai", action="store_true")
    parser.add_argument("--font-scale", type=float, default=1.0)
    parser.add_argument("--extreme-copy", action="store_true")
    parser.add_argument("--expanded-toggles", action="store_true")
    return parser.parse_args()


if __name__ == "__main__":
    capture(parse_args())
