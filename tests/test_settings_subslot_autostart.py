# -*- coding: utf-8 -*-
"""测试设置对话框中副槽位对自启动开关的禁用与提示。"""
from __future__ import annotations

import pytest
from PySide6.QtWidgets import QApplication

from pet.config import Config
from pet.modern_settings_dialog import ModernSettingsDialog


@pytest.fixture
def app():
    return QApplication.instance() or QApplication([])


def test_modern_settings_dialog_disables_autostart_on_sub_slot(app, tmp_path):
    # 主槽（instance_id 为空）
    master_cfg = Config(base=tmp_path)
    d_master = ModernSettingsDialog(master_cfg, include_ai=False)
    try:
        assert d_master.autostart_check.isEnabled() is True
    finally:
        d_master.close()

    # 副槽（instance_id="slot-1"）
    slot1_cfg = Config(base=tmp_path, instance_id="slot-1")
    d_slot1 = ModernSettingsDialog(slot1_cfg, include_ai=False)
    try:
        assert d_slot1.autostart_check.isEnabled() is False
        assert d_slot1.autostart_check.toolTip() == "仅主桌宠可设置"
    finally:
        d_slot1.close()
