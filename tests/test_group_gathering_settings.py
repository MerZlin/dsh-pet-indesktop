# -*- coding: utf-8 -*-
"""聚集互动设置行：存在、默认关、碰撞依赖、自动/动画显隐与保存回写。"""
from __future__ import annotations

from PySide6.QtWidgets import QApplication

from pet import modern_settings_dialog as settings_mod
from pet.config import Config
from pet.modern_settings_dialog import ModernSettingsDialog, SettingRow

_FAKE_PRESETS = [
    {"id": "breakfast", "label": "一起吃早餐", "clip": "吃早餐"},
    {"id": "lunch", "label": "一起吃午餐", "clip": "吃午餐"},
]


def _dialog(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(settings_mod.autostart_mod, "is_enabled", lambda: False)
    monkeypatch.setattr(
        settings_mod.group_presets,
        "available_presets_for_character",
        lambda *a, **k: [dict(item) for item in _FAKE_PRESETS],
    )
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
    preset_row = _row(dialog, "group_gathering_preset")
    assert enabled_row.control.isChecked() is False
    assert auto_row.control.isChecked() is False
    assert preset_row.control.currentData() == ""
    assert auto_row.isHidden()
    assert preset_row.isHidden()
    dialog.reject()
    QApplication.processEvents()


def test_collision_off_disables_group_rows(tmp_path, monkeypatch):
    dialog = _dialog(tmp_path, monkeypatch)
    collision = _row(dialog, "collision_enabled").control
    collision.setChecked(False)
    enabled_row = _row(dialog, "group_gathering_enabled")
    auto_row = _row(dialog, "group_gathering_auto")
    preset_row = _row(dialog, "group_gathering_preset")
    assert enabled_row.control.isEnabled() is False
    assert auto_row.control.isEnabled() is False
    assert preset_row.control.isEnabled() is False
    dialog.reject()
    QApplication.processEvents()


def test_auto_and_preset_visibility_follows_group_enabled(tmp_path, monkeypatch):
    dialog = _dialog(tmp_path, monkeypatch)
    enabled = _row(dialog, "group_gathering_enabled").control
    auto = _row(dialog, "group_gathering_auto")
    preset_row = _row(dialog, "group_gathering_preset")
    # 默认总开关关 → 子行隐藏
    assert auto.isHidden()
    assert preset_row.isHidden()
    enabled.setChecked(True)
    assert auto.isHidden() is False
    assert preset_row.isHidden() is False
    enabled.setChecked(False)
    assert auto.isHidden()
    assert preset_row.isHidden()
    dialog.reject()
    QApplication.processEvents()


def test_preset_selection_saved_and_roundtrips(tmp_path, monkeypatch):
    dialog = _dialog(tmp_path, monkeypatch)
    enabled = _row(dialog, "group_gathering_enabled").control
    auto = _row(dialog, "group_gathering_auto").control
    preset = _row(dialog, "group_gathering_preset").control
    enabled.setChecked(True)
    auto.setChecked(True)
    preset.setCurrentData("breakfast")

    assert dialog._write_config() is True
    dialog.deleteLater()
    QApplication.processEvents()

    reloaded = Config(tmp_path)
    assert reloaded.get("group_gathering") == {
        "enabled": True,
        "auto_enabled": True,
        "preset": "breakfast",
    }
