# -*- coding: utf-8 -*-
"""天气设置（互动域 · 天气标签）：行归属、默认值口径、列表归一化与保存回写。

门禁口径（docs/SETTINGS-CHANGE-GATES.md）：每个持久设置都要有归属域、默认值、
保存语义与恢复路径。天气三键的归属是「互动」域的页内标签「天气」（与「点击与
音效 / 自言自语」同级任务），行由 ``pet/settings_weather.py`` 构建，侧栏 10 个
域不变（那是外部契约）。
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication  # noqa: E402

from pet import weather as W  # noqa: E402

app = QApplication.instance() or QApplication([])

WEATHER_KEYS = ("weather_enabled", "weather_city", "weather_city_list")


@pytest.fixture
def dialog(tmp_path, monkeypatch):
    import pet.modern_settings_dialog as settings_mod
    from pet.config import Config

    monkeypatch.setattr(settings_mod.autostart_mod, "is_enabled", lambda: False)
    dlg = settings_mod.ModernSettingsDialog(Config(tmp_path), include_ai=True)
    yield dlg
    dlg.close()
    app.processEvents()


def _row(dialog, key: str):
    from pet.modern_settings_dialog import SettingRow

    row = dialog.findChild(SettingRow, f"settingRow_{key}")
    assert row is not None, f"缺少设置行 {key}"
    return row


def _weather_tab(dialog):
    from pet.settings_widgets import SettingsTabContainer

    index = next(i for i in range(dialog.sidebar.count())
                 if dialog.sidebar.item(i).text() == "互动")
    page = dialog.pages.widget(index)
    tabs = page.findChild(SettingsTabContainer, "settingsTaskTabs")
    assert tabs is not None, "互动域没有页内标签容器"
    return tabs


# ---------------------------------------------------------------- 归属与默认值
def test_weather_rows_live_in_their_own_interaction_tab(dialog):
    tabs = _weather_tab(dialog)
    assert "weather" in tabs.keys()
    assert tabs.labels()[tabs.keys().index("weather")] == "天气"
    for key in WEATHER_KEYS:
        assert tabs.key_for_descendant(_row(dialog, key)) == "weather"


def test_default_values_are_the_documented_ones(dialog):
    """默认口径：功能开（点一下才联网，不打扰）、城市为空（不替用户猜城市）。"""
    assert dialog.config.get("weather_enabled") is True
    assert dialog.config.get("weather_city") == ""
    assert dialog.config.get("weather_city_list") == []
    assert dialog.weather_enabled_check.isChecked() is True
    assert dialog.weather_city_edit.text() == ""
    assert dialog.weather_city_list_edit.toPlainText().strip() == ""


def test_controls_round_trip_from_config(tmp_path, monkeypatch):
    import pet.modern_settings_dialog as settings_mod
    from pet.config import Config

    monkeypatch.setattr(settings_mod.autostart_mod, "is_enabled", lambda: False)
    config = Config(tmp_path)
    config.set("weather_city", "鹿城")
    config.set("weather_city_list", ["北京", "鹿城"])
    # #271：空身份会话按 slot0 分流，全局键的读源是路由视图另建的主配置（初值只取盘）
    # ——预置必须落盘，内存预置对话框读不到。
    assert config.save() is True
    dlg = settings_mod.ModernSettingsDialog(config, include_ai=True)
    try:
        assert dlg.weather_city_edit.text() == "鹿城"
        assert dlg.weather_city_list_edit.toPlainText().splitlines() == ["北京", "鹿城"]
    finally:
        dlg.close()
        app.processEvents()


# ---------------------------------------------------------------- 保存
def test_save_writes_the_three_keys_and_keeps_names_as_typed(dialog):
    dialog.weather_enabled_check.setChecked(False)
    dialog.weather_city_edit.setText("鹿城区")
    dialog.weather_city_list_edit.setPlainText("北京\n 鹿城 \n北京\n\n上海")

    assert dialog._write_config() is True
    assert dialog.config.get("weather_enabled") is False
    # M1：城市名保留用户输入的原名（不再被剥成「鹿城」）
    assert dialog.config.get("weather_city") == "鹿城区"
    assert dialog.config.get("weather_city_list") == ["北京", "鹿城", "上海"]


def test_saving_other_rows_never_erodes_the_stored_city_name(dialog):
    """M1 的整表归一化回归：反复保存（每次都跑 _normalize_pet_settings）名字不变。"""
    dialog.weather_city_edit.setText("景德镇市")
    dialog.weather_city_list_edit.setPlainText("新乡\n景德镇")
    assert dialog._write_config() is True
    for _ in range(3):
        assert dialog._write_config() is True
    assert dialog.config.get("weather_city") == "景德镇市"
    assert dialog.config.get("weather_city_list") == ["新乡", "景德镇"]


def test_save_drops_area_codes_of_cities_that_are_no_longer_listed(tmp_path, monkeypatch):
    """地区码表跟着城市列表收敛：删掉的城市不能留着身份（否则重加会串号）。"""
    import pet.modern_settings_dialog as settings_mod
    from pet.config import Config

    monkeypatch.setattr(settings_mod.autostart_mod, "is_enabled", lambda: False)
    config = Config(tmp_path)
    config.set("weather_city_list", ["北京", "上海"])
    config.set("weather_city", "北京")
    config.set("weather_city_codes", {"北京": "101010100", "上海": "101020100"})
    # #271：空身份会话按 slot0 分流，全局键的读源是路由视图另建的主配置（初值只取盘）
    # ——预置必须落盘，内存预置对话框读不到。
    assert config.save() is True
    dlg = settings_mod.ModernSettingsDialog(config, include_ai=True)
    try:
        dlg.weather_city_list_edit.setPlainText("北京")
        assert dlg._write_config() is True
        assert dlg.config.get("weather_city_codes") == {"北京": "101010100"}
    finally:
        dlg.close()
        app.processEvents()


def test_save_caps_the_city_list_and_keeps_the_current_city_listed(dialog):
    names = [f"城市{i}" for i in range(W.MAX_CITIES + 4)]
    dialog.weather_city_list_edit.setPlainText("\n".join(names))
    dialog.weather_city_edit.setText("城市3")
    assert dialog._write_config() is True
    listed = dialog.config.get("weather_city_list")
    assert len(listed) == W.MAX_CITIES
    assert listed[:2] == ["城市0", "城市1"], "超上限时按书写顺序截断"
    assert "城市3" in listed, "当前城市必须落在列表里，否则菜单里勾不到它"

    # 列表满员且当前城市不在其中：保持用户所写，只保留"当前城市"这个指针
    # （挤掉用户写的某个城市比"指针不在列表里"更糟：那是静默丢数据）
    dialog.weather_city_edit.setText("北京")
    assert dialog._write_config() is True
    assert dialog.config.get("weather_city") == "北京"
    assert dialog.config.get("weather_city_list") == listed


def test_save_with_an_empty_current_city_clears_it(dialog):
    dialog.weather_city_edit.setText("   ")
    assert dialog._write_config() is True
    assert dialog.config.get("weather_city") == ""


def test_saving_an_empty_list_does_not_resurrect_the_current_city(dialog):
    """列表清空 + 当前城市清空 = 回到"还没选城市"的状态（菜单显示空提示）。"""
    dialog.weather_city_edit.setText("")
    dialog.weather_city_list_edit.setPlainText("")
    assert dialog._write_config() is True
    assert dialog.config.get("weather_city_list") == []
    assert dialog.config.get("weather_city") == ""


def test_search_finds_the_weather_rows_and_switches_to_the_tab(dialog):
    tabs = _weather_tab(dialog)
    tabs.setCurrentKey("click")
    dialog.search_edit.setText("天气")
    for _ in range(3):
        app.processEvents()
    assert dialog.sidebar.currentItem().text() == "互动"
    assert tabs.currentKey() == "weather"
    assert _row(dialog, "weather_enabled").isVisibleTo(tabs) is True


# ---------------------------------------------------------------- 子宠页作用域
def _main_and_sub_dialogs(tmp_path, monkeypatch):
    """主宠页 + 子宠页（``instance_id=slot-1``）两个设置对话框。"""
    import pet.modern_settings_dialog as settings_mod
    from pet.config import Config

    monkeypatch.setattr(settings_mod.autostart_mod, "is_enabled", lambda: False)
    master = settings_mod.ModernSettingsDialog(Config(tmp_path), include_ai=True)
    child = settings_mod.ModernSettingsDialog(
        Config(base=tmp_path, instance_id="slot-1"), include_ai=True)
    return master, child


def test_weather_controls_are_editable_on_the_child_page(tmp_path, monkeypatch):
    """子宠页：天气三键可编辑，保存落**主配置**（消费方读的就是它）。

    这三个键不在 ``PER_SLOT_SETTING_KEYS`` 里（子宠页曾经置灰：写了 slot 文件、
    运行期读主配置 = 死开关）。独立设置进程现在按 key 分流到主配置，故恢复可用。
    """
    import json

    master, child = _main_and_sub_dialogs(tmp_path, monkeypatch)
    try:
        for control in (master.weather_enabled_check, master.weather_city_edit,
                        master.weather_city_list_edit,
                        child.weather_enabled_check, child.weather_city_edit,
                        child.weather_city_list_edit):
            assert control.isEnabled() is True
            assert control.toolTip() != "仅主桌宠可设置"

        child.weather_enabled_check.setChecked(False)
        child.weather_city_edit.setText("鹿城")
        child.weather_city_list_edit.setPlainText("北京\n鹿城")
        assert child._write_config() is True
        # #271：空身份（主宠页）会话的配置面指向 config-slot-0.json，
        # 天气三键是全局键、落主配置——读盘用主配置路径。
        on_disk = json.loads(master.config._main.path.read_text(encoding="utf-8"))
        assert on_disk["weather_enabled"] is False, "子宠页改的键必须真正生效"
        assert on_disk["weather_city"] == "鹿城"
        assert on_disk["weather_city_list"] == ["北京", "鹿城"]
    finally:
        for dlg in (master, child):
            dlg.close()
            dlg.deleteLater()
        app.processEvents()
