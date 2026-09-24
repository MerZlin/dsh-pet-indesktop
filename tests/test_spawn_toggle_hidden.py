# -*- coding: utf-8 -*-
"""拓扑收口 Phase A：设置页隐藏「单进程多开」实验开关（配置兼容保留）。

多进程已确定为唯一多宠拓扑方向（单进程共享解码层待下线）。锁定：
1. 设置页不再展示「多开」分组（用户不再被实验选项困扰）；
2. 存量配置兼容：已开启的用户保存设置后原值不丢（配置键照常读写）；
3. experimental_single_process_spawn 的 config round trip 不受影响。
"""

from __future__ import annotations

import pytest
from PySide6.QtWidgets import QApplication

from pet.config import Config


@pytest.fixture
def app():
    return QApplication.instance() or QApplication([])


def _section_titles(dialog):
    from PySide6.QtWidgets import QLabel

    from pet.modern_settings_dialog import SettingsSection

    titles = []
    for section in dialog.findChildren(SettingsSection):
        labels = [l.text() for l in section.findChildren(QLabel) if l.text()]
        if labels:
            titles.append(labels[0])  # 每个分组的第一个 QLabel 即分组标题
    return titles


def test_spawn_section_hidden_from_settings(app, tmp_path):
    from pet.modern_settings_dialog import ModernSettingsDialog

    cfg = Config(base=tmp_path)
    dialog = ModernSettingsDialog(cfg, include_ai=False, standalone=True)
    try:
        assert "多开" not in _section_titles(dialog), "「多开」实验分组必须隐藏"
    finally:
        dialog.close()
        app.processEvents()


def test_spawn_config_value_preserved_on_save(app, tmp_path):
    """存量开启用户：隐藏开关后保存设置，原值不丢（兼容 + 回滚路径）。"""
    from pet.modern_settings_dialog import ModernSettingsDialog

    cfg = Config(base=tmp_path)
    cfg.set("experimental_single_process_spawn", True)
    dialog = ModernSettingsDialog(cfg, include_ai=False, standalone=True)
    try:
        dialog._save()
    finally:
        dialog.close()
        app.processEvents()
    assert cfg.get("experimental_single_process_spawn") is True


def test_spawn_config_round_trip_default_false(app, tmp_path):
    from pet.modern_settings_dialog import ModernSettingsDialog

    cfg = Config(base=tmp_path)
    dialog = ModernSettingsDialog(cfg, include_ai=False, standalone=True)
    try:
        dialog._save()
    finally:
        dialog.close()
        app.processEvents()
    assert cfg.get("experimental_single_process_spawn") is False
