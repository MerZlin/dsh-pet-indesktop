# -*- coding: utf-8 -*-
"""天气设置页的行与控件（控件与行定义都在本模块）。

**为什么单独一个模块**：``modern_settings_dialog.py`` 有行数预算
（``tests/test_architecture.py::MODERN_SETTINGS_DIALOG_PY_LINE_BUDGET``），按
``docs/WINDOW_PY_SPLIT_GUIDE.md`` 的口径「新控件组优先拆出」——对话框里只留
import 与保存链一行接线；行由 ``settings_interaction`` 的「互动」域页内标签承载
（与「点击与音效 / 自言自语」同级）。

**接线契约**（与 settings_interaction / settings_quiet 同口径）：

- 控件在本模块创建并挂到 dialog（``weather_*`` 前缀）；
- ``objectName`` 由 ``SettingRow`` 统一置为 ``settingRow_<配置键>``，所以设置搜索
  索引、显隐联动与保存链照旧工作。
"""
from __future__ import annotations

from PySide6.QtWidgets import QLineEdit

from .settings_widgets import SettingRow, ToggleSwitch


def build_weather_controls(dialog) -> None:
    """建控件（幂等：已建过就不重复建，避免二次装配丢状态）。"""
    host = dialog
    if getattr(host, "weather_city_edit", None) is None:
        host.weather_city_edit = QLineEdit(host)
        host.weather_city_edit.setPlaceholderText("例如：汕头 / Shantou")
        host.weather_city_edit.setFixedWidth(180)
        host.weather_city_edit.setText(str(host.config.get("weather_city", "") or ""))
    if getattr(host, "weather_enabled_check", None) is None:
        host.weather_enabled_check = ToggleSwitch(host)
        host.weather_enabled_check.setChecked(bool(host.config.get("weather_enabled", True)))


def build_weather_rows(dialog) -> list[SettingRow]:
    """「天气」组的行（顺序即渲染顺序）。"""
    build_weather_controls(dialog)
    return [
        SettingRow(
            "weather_enabled",
            "天气",
            "打开后右键菜单「查看天气」可用；数据来自中国天气网，取不到时用 open-meteo 兜底。",
            dialog.weather_enabled_check,
        ),
        SettingRow(
            "weather_city",
            "默认城市",
            "手动填城市名（中英文都行，也能填「鹿城区」这种带区/县的叫法）。"
            "右键菜单「天气城市」里可以联网搜索、切换和删除城市。",
            dialog.weather_city_edit,
        ),
    ]


def apply_to_config(dialog) -> None:
    """把 2 个键写回配置；城市为空表示还没配，菜单会引导先设置。"""
    from .weather_source import clean_city, clean_city_list

    city = clean_city(dialog.weather_city_edit.text())
    dialog.config.set("weather_enabled", bool(dialog.weather_enabled_check.isChecked()))
    dialog.config.set("weather_city", city)
    dialog.config.set(
        "weather_city_list",
        clean_city_list(dialog.config.get("weather_city_list", []), city),
    )
