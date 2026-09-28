# -*- coding: utf-8 -*-
"""「提醒与快捷键」设置组的行与开关（pet/settings_reminders.py）。"""
from __future__ import annotations

import pytest
from PySide6.QtWidgets import QApplication, QWidget

from pet import settings_reminders

SWITCH_ROWS = (
    ("timed_on", "timed_on_check"),
    ("app_usage_on", "app_usage_on_check"),
    ("hotkeys_on", "hotkeys_on_check"),
    ("mem_skip_foreground", "mem_skip_foreground_check"),
)
# 「编辑规则…」那一行是按钮不是开关，所以不参与"默认都开着"的断言
BUTTON_ROWS = (("rules_editor", "rules_editor_btn"),)
ROWS = SWITCH_ROWS + BUTTON_ROWS


class FakeConfig:
    def __init__(self, values=None):
        self.values = dict(values or {})
        self.written = {}

    def get(self, key, default=None):
        return self.values.get(key, default)

    def set(self, key, value):
        self.written[key] = value
        self.values[key] = value


class FakeDialog(QWidget):
    def __init__(self, config):
        super().__init__()
        self.config = config


@pytest.fixture(scope="module")
def _app():
    return QApplication.instance() or QApplication([])


def test_build_controls_reads_config_and_is_idempotent(_app):
    dialog = FakeDialog(FakeConfig({
        "timed_on": False, "app_usage_on": False,
        "hotkeys_on": True, "mem_skip_foreground": False,
    }))
    settings_reminders.build_reminder_controls(dialog)
    first = dialog.timed_on_check

    assert dialog.timed_on_check.isChecked() is False
    assert dialog.app_usage_on_check.isChecked() is False
    assert dialog.hotkeys_on_check.isChecked() is True
    assert dialog.mem_skip_foreground_check.isChecked() is False

    settings_reminders.build_reminder_controls(dialog)   # 二次装配不重建
    assert dialog.timed_on_check is first


def test_build_controls_defaults_to_on(_app):
    dialog = FakeDialog(FakeConfig({}))
    settings_reminders.build_reminder_controls(dialog)
    for _key, attr in SWITCH_ROWS:
        assert getattr(dialog, attr).isChecked() is True
    assert dialog.rules_editor_btn.text() == "编辑规则…"


def test_rows_expose_setting_row_object_names(_app):
    dialog = FakeDialog(FakeConfig({}))
    rows = settings_reminders.build_toggle_rows(dialog)

    assert [row.objectName() for row in rows] == [f"settingRow_{key}" for key, _ in ROWS]
    for row, (_key, attr) in zip(rows, ROWS):
        assert row.isAncestorOf(getattr(dialog, attr))


def test_apply_to_config_writes_four_switches(_app):
    config = FakeConfig({})
    dialog = FakeDialog(config)
    settings_reminders.build_reminder_controls(dialog)
    dialog.timed_on_check.setChecked(False)
    dialog.app_usage_on_check.setChecked(False)
    dialog.hotkeys_on_check.setChecked(False)
    dialog.mem_skip_foreground_check.setChecked(True)

    settings_reminders.apply_to_config(dialog)

    assert config.written == {
        "timed_on": False,
        "app_usage_on": False,
        "hotkeys_on": False,
        "mem_skip_foreground": True,
    }
