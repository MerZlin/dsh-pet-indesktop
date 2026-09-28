# -*- coding: utf-8 -*-
"""「免打扰与汇总」设置组：行存在 + 建控件 + 写回（pet/settings_quiet.py）。

覆盖三件事，都是设置页的对外契约：

1. 4 行都进了「自动化与联动」域，且 ``objectName`` 是 ``settingRow_<配置键>``
   （设置搜索索引与显隐联动按它工作）；
2. 控件初值来自 config（含区间夹取），非法配置不炸；
3. ``apply_to_config()`` 把控件值按秒/分钟换算写回 4 个键。
"""
from __future__ import annotations

import pytest
from PySide6.QtWidgets import QApplication, QWidget

from pet import settings_quiet

ROWS = (
    ("quiet_minutes_default", "免打扰默认时长"),
    ("quiet_also_on_fullscreen", "全屏时也算免打扰"),
    ("summary_min_seconds", "汇总点名门槛"),
    ("summary_max_apps", "汇总点名条数"),
)


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


def test_build_controls_reads_config(_app):
    dialog = FakeDialog(FakeConfig({
        "quiet_minutes_default": 45,
        "quiet_also_on_fullscreen": False,
        "summary_min_seconds": 1800,
        "summary_max_apps": 3,
    }))
    settings_quiet.build_quiet_controls(dialog)

    assert dialog.quiet_default_minutes_spin.value() == 45
    assert dialog.quiet_fullscreen_check.isChecked() is False
    assert dialog.summary_min_minutes_spin.value() == 30       # 1800 秒 → 30 分钟
    assert dialog.summary_max_apps_spin.value() == 3


def test_build_controls_survives_missing_and_bad_values(_app):
    dialog = FakeDialog(FakeConfig({}))
    settings_quiet.build_quiet_controls(dialog)
    # 缺省值回落（与 config 默认一致：30 分钟 / 全屏算 / 20 分钟 / 2 个）
    assert dialog.quiet_default_minutes_spin.value() == 30
    assert dialog.quiet_fullscreen_check.isChecked() is True
    assert dialog.summary_min_minutes_spin.value() == 20
    assert dialog.summary_max_apps_spin.value() == 2


def test_build_rows_exposes_setting_row_object_names(_app):
    dialog = FakeDialog(FakeConfig({}))
    settings_quiet.build_quiet_controls(dialog)
    rows = settings_quiet.build_quiet_rows(dialog)

    assert [(row.objectName() if hasattr(row, "objectName") else "") for row in rows] == [
        f"settingRow_{key}" for key, _title in ROWS
    ]
    # 行里装的确实是本模块建的控件（SettingRow 会把控件 reparent 进自己的布局）
    assert rows[0].isAncestorOf(dialog.quiet_default_minutes_spin)
    assert rows[1].isAncestorOf(dialog.quiet_fullscreen_check)
    assert rows[2].isAncestorOf(dialog.summary_min_minutes_spin)
    assert rows[3].isAncestorOf(dialog.summary_max_apps_spin)


def test_apply_to_config_writes_four_keys(_app):
    config = FakeConfig({})
    dialog = FakeDialog(config)
    settings_quiet.build_quiet_controls(dialog)
    dialog.quiet_default_minutes_spin.setValue(120)
    dialog.quiet_fullscreen_check.setChecked(False)
    dialog.summary_min_minutes_spin.setValue(45)
    dialog.summary_max_apps_spin.setValue(4)

    settings_quiet.apply_to_config(dialog)

    assert config.written == {
        "quiet_minutes_default": 120,
        "quiet_also_on_fullscreen": False,
        "summary_min_seconds": 45 * 60,
        "summary_max_apps": 4,
    }
