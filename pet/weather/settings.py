# -*- coding: utf-8 -*-
"""设置页「互动 · 天气」标签：控件创建 / 行装配 / 保存。

**为什么独立模块**：``modern_settings_dialog.py`` 的行数预算已无余量（该文件顶部
与多处注释反复登记）。本页控件与行全部在此构建，对话框只做一处接线：
``_write_config`` 里的 ``settings_weather.save_weather_settings(self)``。

**为什么挂在「互动」域的页内标签而不是新开侧栏域**：侧栏域清单是外部契约
（``SETTINGS_DOMAIN_NAV``，多处用例与截图脚本按名称/索引依赖）；天气与
「点击显示余额」同类——都是"桌宠替你看一眼外部数据"的互动面，正好是互动域
（``settings_interaction``）的第三个同级任务。

**配置键（config.py 顶层平铺 4 个）**：``weather_enabled`` / ``weather_city`` /
``weather_city_list`` / ``weather_city_codes``；默认口径（含"为什么不猜一个默认
城市"）见 config.py 的注释。城市名的归一化只有一处实现（``pet/weather.py``），
设置页、右键菜单、取数共用；``weather_city_codes``（地区码）由菜单添加城市时写入，
本页保存时按列表收敛（删掉的城市不留身份）。
"""

from __future__ import annotations

from PySide6.QtWidgets import QLineEdit, QPlainTextEdit, QVBoxLayout, QWidget

from ..settings_widgets import (
    SettingRow,
    SettingsSection,
    ToggleSwitch,
)
from pet.weather import (
    MAX_CITIES,
    normalize_city_codes,
    normalize_city_list,
    normalize_city_name,
    same_city,
)


def create_weather_controls(dialog) -> None:
    """在对话框上创建本页控件（幂等；行由 ``build_weather_rows`` 装配）。"""
    if getattr(dialog, "weather_enabled_check", None) is not None:
        return
    dialog.weather_enabled_check = ToggleSwitch(dialog)
    dialog.weather_enabled_check.setChecked(bool(dialog.config.get("weather_enabled", True)))

    dialog.weather_city_edit = QLineEdit(dialog)
    dialog.weather_city_edit.setPlaceholderText("如 鹿城（留空 = 还没选，菜单里会引导去添加）")
    dialog.weather_city_edit.setText(str(dialog.config.get("weather_city", "") or ""))

    dialog.weather_city_list_edit = QPlainTextEdit(dialog)
    dialog.weather_city_list_edit.setMinimumSize(280, 84)
    dialog.weather_city_list_edit.setMaximumHeight(180)
    dialog.weather_city_list_edit.setPlaceholderText(
        f"每行一个城市，最多 {MAX_CITIES} 个，例如：\n北京\n鹿城\nTokyo"
    )
    dialog.weather_city_list_edit.setPlainText(
        "\n".join(normalize_city_list(dialog.config.get("weather_city_list")))
    )
    # 三个键是**进程级**（不在 PER_SLOT_SETTING_KEYS，右键菜单与「查看天气」一律读
    # 主配置）：子宠页保存由设置进程的配置路由直写主配置
    # （pet/settings_slot_config.py），改了真生效——此前按死开关口径置灰，现摘除。


def build_weather_rows(dialog) -> list[SettingRow]:
    """本页设置行（objectName 前缀 ``settingRow_weather_``，参与设置搜索索引）。"""
    create_weather_controls(dialog)
    return [
        SettingRow(
            "weather_enabled",
            "启用天气",
            "右键菜单显示「查看天气」与「天气城市」。只在点「查看天气」或切换城市时"
            "联网取一次数（中国天气网优先，open-meteo 兜底），平时不请求、不打扰；"
            "关掉后菜单里保留这两个入口并置灰，悬停能看到关闭原因。",
            dialog.weather_enabled_check,
        ),
        SettingRow(
            "weather_city",
            "当前城市",
            "气泡里报的那个城市。留空 = 还没选，此时点「查看天气」会提示去菜单里添加。",
            dialog.weather_city_edit,
        ),
        SettingRow(
            "weather_city_list",
            f"城市列表（最多 {MAX_CITIES} 个）",
            "每行一个。右键菜单「天气城市」里点一下即可切换，当前城市打勾；"
            "列表保留你写的名字，取数时对带行政区后缀的名字（鹿城区）会自动再试一次。",
            dialog.weather_city_list_edit,
            stacked=True,
        ),
    ]


def build_weather_tab(dialog) -> QWidget:
    """「互动」域的第三个页内标签内容（由 ``settings_interaction`` 挂载）。"""
    content = QWidget(dialog)
    layout = QVBoxLayout(content)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(18)
    layout.addWidget(SettingsSection("天气", build_weather_rows(dialog), content))
    layout.addStretch(1)
    return content


def save_weather_settings(dialog) -> None:
    """把本页控件写回 config（``_write_config`` 委托；控件不存在时静默跳过）。"""
    check = getattr(dialog, "weather_enabled_check", None)
    if check is None:
        return
    config = dialog.config
    config.set("weather_enabled", bool(check.isChecked()))
    city = normalize_city_name(dialog.weather_city_edit.text())
    listed = normalize_city_list(dialog.weather_city_list_edit.toPlainText().splitlines())
    if city and city not in listed:
        # 当前城市必须落在列表里，否则菜单里勾不到它（列表满了也不挤掉别人：
        # 当前城市是"指针"，列表只是可选集合）。
        if len(listed) < MAX_CITIES:
            listed = listed + [city]
    config.set("weather_city", city)
    config.set("weather_city_list", listed)
    # 地区码表跟着列表收敛：不在列表里的城市，身份（地区码）也一起清掉——
    # 否则"删掉再重加同名城市"会带着旧码去取数（同名城市选哪项都白搭）。
    codes = normalize_city_codes(config.get("weather_city_codes"))
    kept = {label: code for label, code in codes.items()
            if any(same_city(label, name) for name in listed)}
    if kept != codes:
        config.set("weather_city_codes", kept)
