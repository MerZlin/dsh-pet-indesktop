# -*- coding: utf-8 -*-
"""围圈聚集右键菜单入口与注册测试。"""
from __future__ import annotations

from PySide6.QtWidgets import QApplication, QMenu

from pet.context_menus.registry import MENU_ACTIONS
from pet.context_menus.shared import add_group_gathering_menu


class FakePet:
    def __init__(self, wanted=True, presets=None):
        self._wanted = wanted
        self.presets = presets or []
        self.started = []

    def group_gather_wanted(self):
        return self._wanted

    def group_common_presets(self):
        return list(self.presets)

    def start_group_gather(self, preset_id):
        self.started.append(preset_id)


def _qapp():
    return QApplication.instance() or QApplication([])


def test_gather_action_registered_with_label():
    assert "gather" in MENU_ACTIONS.ids
    assert MENU_ACTIONS.label("gather") == "聚集互动"


def test_add_group_menu_returns_none_when_disabled():
    _qapp()
    menu = QMenu()
    pet = FakePet(wanted=False)
    assert add_group_gathering_menu(menu, pet, icons=False) is None
    menu.deleteLater()


def test_add_group_menu_populates_common_presets_on_open():
    _qapp()
    menu = QMenu()
    pet = FakePet(presets=[
        {"id": "breakfast", "label": "一起吃早餐"},
        {"id": "lunch", "label": "一起吃午餐"},
    ])
    sub = add_group_gathering_menu(menu, pet, icons=False)
    assert sub is not None
    sub.aboutToShow.emit()
    labels = [action.text() for action in sub.actions()]
    assert labels == ["一起吃早餐", "一起吃午餐"]
    # 触发第二个动作并确保写回发起者
    actions = sub.actions()
    actions[1].trigger()
    assert pet.started == ["lunch"]
    menu.deleteLater()


def test_add_group_menu_shows_disabled_empty_state_when_no_presets():
    _qapp()
    menu = QMenu()
    pet = FakePet(presets=[])
    sub = add_group_gathering_menu(menu, pet, icons=False)
    sub.aboutToShow.emit()
    assert len(sub.actions()) == 1
    assert sub.actions()[0].isEnabled() is False
    menu.deleteLater()
