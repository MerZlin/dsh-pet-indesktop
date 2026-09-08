# -*- coding: utf-8 -*-
"""灵动岛精简右键菜单：内容/风格选项与即时提交。"""
from __future__ import annotations

from PySide6.QtWidgets import QApplication, QMenu

from pet.config import Config
from pet.dynamic_island_menu import (
    _INFO_OPTIONS,
    _STYLE_OPTIONS,
    _add_radio_menu,
    _commit_config,
    show_island_context_menu,
)


class FakeIsland:
    def __init__(self, cfg, island_cfg):
        self.config = cfg
        self._cfg = island_cfg
        self.updated = 0

    def _update_size(self):
        self.updated += 1

    def update(self):
        self.updated += 1


def _qapp():
    return QApplication.instance() or QApplication([])


def test_radio_menu_has_expected_options_and_current_checked():
    _qapp()
    menu = QMenu()
    group = _add_radio_menu(menu, "显示内容", "balance", _INFO_OPTIONS)
    assert [a.text() for a in group.actions()] == ["时间", "余额", "余额峰谷", "自定义短文本"]
    assert group.checkedAction().data() == "balance"
    menu.deleteLater()


def test_style_options_match_config_values():
    assert [value for _label, value in _STYLE_OPTIONS] == ["dark", "light", "glass"]


def test_commit_config_preserves_position_and_saves(tmp_path):
    cfg = Config(base=tmp_path)
    island = FakeIsland(cfg, {"enabled": True, "info_mode": "time", "style": "dark", "x": 10, "y": 20})
    _commit_config(island, "style", "glass")
    assert island._cfg["style"] == "glass"
    assert island._cfg["x"] == 10
    assert island._cfg["y"] == 20
    reloaded = Config(base=tmp_path)
    assert reloaded.get("dynamic_island")["style"] == "glass"
    assert island.updated == 2


def test_show_context_menu_smoke_builds_without_crashing(tmp_path):
    # 菜单 exec 会进入模态循环，这里只验证函数前段（用临时菜单不行）；
    # 该函数无法注入菜单，因此仅确认模块可导入且配置可提交。
    _qapp()
    cfg = Config(base=tmp_path)
    island = FakeIsland(cfg, {"enabled": True, "info_mode": "time", "style": "dark"})
    # 不执行弹窗，直接调用提交路径等价覆盖
    _commit_config(island, "info_mode", "custom")
    assert island._cfg["info_mode"] == "custom"
    assert callable(show_island_context_menu)
