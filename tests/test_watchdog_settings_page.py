# -*- coding: utf-8 -*-
"""行为重复检测（pattern_detect）设置页补组回归。

PR57 审计 F15：``pattern_detect`` 开关与一组双窗口阈值在 ``config.py`` 里
默认开启，但设置页只有「循环检测」和「卡住检测」两组，用户完全无法调整
行为重复识别阈值。本文件钉住补组后的控件存在性与 config 读写。
"""

from __future__ import annotations

import pytest
from PySide6.QtWidgets import QApplication

from pet.config import Config
from pet.exploration_watchdog_settings import WatchdogSettingsPage
from pet.modern_settings_dialog import SettingRow

_PATTERN_DEFAULTS = {
    "pattern_w6_control": 3,
    "pattern_w10_warn": 3,
    "pattern_w10_control": 4,
    "pattern_macro_w6_explore": 5,
    "pattern_macro_w6_action": 0,
    "pattern_macro_w10_explore": 7,
    "pattern_macro_w10_action": 1,
    "pattern_min_steps_between": 3,
    "pattern_cooldown_seconds": 60,
}


@pytest.fixture
def app():
    return QApplication.instance() or QApplication([])


def _make_page(tmp_path):
    cfg = Config(base=tmp_path)
    return WatchdogSettingsPage(cfg, cfg.get("agent_link", {}))


def test_pattern_group_rows_exist(app, tmp_path):
    page = _make_page(tmp_path)
    try:
        assert page.findChild(SettingRow, "settingRow_pattern_detect") is not None, "行为重复检测缺少总开关行"
        for key in _PATTERN_DEFAULTS:
            assert page.findChild(SettingRow, f"settingRow_{key}") is not None, f"行为重复检测缺少阈值行 {key}"
    finally:
        page.deleteLater()


def test_reads_pattern_defaults_from_config(app, tmp_path):
    page = _make_page(tmp_path)
    try:
        assert page.pattern_enabled_check.isChecked() is True, "默认开"
        for key, expected in _PATTERN_DEFAULTS.items():
            control = getattr(page, f"{key}_spin")
            assert control.value() == expected, f"{key} 初值应取 config 默认"
    finally:
        page.deleteLater()


def test_reads_pattern_overrides_from_config(app, tmp_path):
    cfg = Config(base=tmp_path)
    agent_cfg = dict(cfg.get("agent_link", {}))
    agent_cfg.update({"pattern_detect": False, "pattern_w6_control": 8, "pattern_macro_w6_action": 2, "pattern_cooldown_seconds": 90})
    page = WatchdogSettingsPage(cfg, agent_cfg)
    try:
        assert page.pattern_enabled_check.isChecked() is False
        assert page.pattern_w6_control_spin.value() == 8
        assert page.pattern_macro_w6_action_spin.value() == 2
        assert page.pattern_cooldown_seconds_spin.value() == 90
    finally:
        page.deleteLater()


def test_apply_to_config_writes_pattern_keys(app, tmp_path):
    page = _make_page(tmp_path)
    try:
        page.pattern_enabled_check.setChecked(False)
        page.pattern_w6_control_spin.setValue(6)
        page.pattern_w10_warn_spin.setValue(2)
        page.pattern_w10_control_spin.setValue(5)
        page.pattern_macro_w6_explore_spin.setValue(9)
        page.pattern_macro_w6_action_spin.setValue(2)
        page.pattern_macro_w10_explore_spin.setValue(11)
        page.pattern_macro_w10_action_spin.setValue(3)
        page.pattern_min_steps_between_spin.setValue(4)
        page.pattern_cooldown_seconds_spin.setValue(120)
        updated = page.apply_to_config({"dsh": True})
        assert updated["dsh"] is True, "写回不得覆盖同组其他键"
        assert updated["pattern_detect"] is False
        assert updated["pattern_w6_control"] == 6
        assert updated["pattern_w10_warn"] == 2
        assert updated["pattern_w10_control"] == 5
        assert updated["pattern_macro_w6_explore"] == 9
        assert updated["pattern_macro_w6_action"] == 2
        assert updated["pattern_macro_w10_explore"] == 11
        assert updated["pattern_macro_w10_action"] == 3
        assert updated["pattern_min_steps_between"] == 4
        assert updated["pattern_cooldown_seconds"] == 120
    finally:
        page.deleteLater()


def test_pattern_rows_grouped_under_own_section(app, tmp_path):
    """设置对话框必须把 pattern 行单独成组，不能混进循环/卡住检测组。"""
    from pet.modern_settings_dialog import ModernSettingsDialog
    from pet.settings_widgets import SettingsSection

    dlg = ModernSettingsDialog(Config(base=tmp_path), include_ai=False)
    try:
        page = None
        for index in range(dlg.sidebar.count()):
            if dlg.sidebar.item(index).text() == "自动化与联动":
                page = dlg.pages.widget(index)
                break
        assert page is not None, "缺少「自动化与联动」页"
        row = page.findChild(SettingRow, "settingRow_pattern_detect")
        assert row is not None, "行为重复检测组必须装配进「自动化与联动」域"
        section = row.parent()
        while section is not None and not isinstance(section, SettingsSection):
            section = section.parent()
        assert section is not None, "行为重复检测行必须落在某个设置分组里"
        ids = {r.objectName() for r in section.rows}
        assert "settingRow_pattern_detect" in ids
        assert "settingRow_pattern_cooldown_seconds" in ids
        assert not any(i.startswith(("settingRow_stuck_", "settingRow_exploration_watchdog_")) for i in ids), (
            f"行为重复检测组不得混入其他检测器行：{sorted(ids)}"
        )
    finally:
        dlg.deleteLater()
