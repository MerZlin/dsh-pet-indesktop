# -*- coding: utf-8 -*-
"""「天气」设置组：控件初值、行 objectName、写回（pet/settings_weather.py）。"""
from __future__ import annotations

import pytest
from PySide6.QtWidgets import QApplication, QWidget

from pet import settings_weather


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
    dialog = FakeDialog(FakeConfig({"weather_enabled": False, "weather_city": "汕头"}))
    settings_weather.build_weather_controls(dialog)
    first = dialog.weather_city_edit

    assert dialog.weather_enabled_check.isChecked() is False
    assert dialog.weather_city_edit.text() == "汕头"

    settings_weather.build_weather_controls(dialog)   # 二次装配不重建控件
    assert dialog.weather_city_edit is first


def test_rows_expose_setting_row_object_names(_app):
    dialog = FakeDialog(FakeConfig({}))
    rows = settings_weather.build_weather_rows(dialog)

    assert [row.objectName() for row in rows] == [
        "settingRow_weather_enabled", "settingRow_weather_city",
    ]
    assert rows[0].isAncestorOf(dialog.weather_enabled_check)
    assert rows[1].isAncestorOf(dialog.weather_city_edit)


def test_apply_to_config_writes_three_keys(_app):
    config = FakeConfig({"weather_city_list": ["上海"]})
    dialog = FakeDialog(config)
    settings_weather.build_weather_controls(dialog)
    dialog.weather_enabled_check.setChecked(True)
    dialog.weather_city_edit.setText("  汕头  ")

    settings_weather.apply_to_config(dialog)

    assert config.written["weather_enabled"] is True
    assert config.written["weather_city"] == "汕头"          # 去空白
    assert config.written["weather_city_list"] == ["汕头", "上海"]   # 当前城市排第一
