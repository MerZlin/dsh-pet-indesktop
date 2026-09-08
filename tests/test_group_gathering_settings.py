# -*- coding: utf-8 -*-
"""聚集互动设置行：存在、默认关、碰撞依赖与自动开关显隐。"""
from __future__ import annotations

import pytest
from PySide6.QtWidgets import QApplication

from pet import modern_settings_dialog as settings_mod
from pet.config import Config
from pet.modern_settings_dialog import ModernSettingsDialog, SettingRow


def _dialog(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(settings_mod.autostart_mod, "is_enabled", lambda: False)
    dialog = ModernSettingsDialog(Config(tmp_path), include_ai=True)
    return dialog


def _row(dialog, key):
    row = dialog.findChild(SettingRow, f"settingRow_{key}")
    assert row is not None, key
    return row


def test_group_rows_exist_and_default_off(tmp_path, monkeypatch):
    dialog = _dialog(tmp_path, monkeypatch)
    enabled_row = _row(dialog, "group_gathering_enabled")
    auto_row = _row(dialog, "group_gathering_auto")
    assert enabled_row.control.isChecked() is False
    assert auto_row.control.isChecked() is False
    dialog.reject()
    QApplication.processEvents()


def test_collision_off_disables_group_rows(tmp_path, monkeypatch):
    dialog = _dialog(tmp_path, monkeypatch)
    collision = _row(dialog, "collision_enabled").control
    collision.setChecked(False)
    enabled_row = _row(dialog, "group_gathering_enabled")
    auto_row = _row(dialog, "group_gathering_auto")
    assert enabled_row.control.isEnabled() is False
    assert auto_row.control.isEnabled() is False
    dialog.reject()
    QApplication.processEvents()


def test_auto_row_visibility_follows_group_enabled(tmp_path, monkeypatch):
    dialog = _dialog(tmp_path, monkeypatch)
    enabled = _row(dialog, "group_gathering_enabled").control
    auto = _row(dialog, "group_gathering_auto")
    auto_row = auto
    # 默认总开关关 → auto 行隐藏
    assert auto_row.isHidden()
    enabled.setChecked(True)
    assert auto_row.isHidden() is False
    enabled.setChecked(False)
    assert auto_row.isHidden()
    dialog.reject()
    QApplication.processEvents()
