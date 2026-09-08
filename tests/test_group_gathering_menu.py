# -*- coding: utf-8 -*-
"""围圈聚集右键菜单入口与注册测试。"""
from __future__ import annotations

from PySide6.QtWidgets import QApplication, QMenu

from pet.context_menus.registry import MENU_ACTIONS
from pet.context_menus.shared import add_group_gathering_menu


class FakePet:
    def __init__(self, wanted=True, ready=True, reason=""):
        self._wanted = wanted
        self._ready = ready
        self._reason = reason
        self.triggered = []

    def group_gather_wanted(self):
        return self._wanted

    def group_gather_ready(self):
        return self._ready

    def group_gather_block_reason(self):
        return self._reason

    def trigger_group_gather(self):
        self.triggered.append(1)
        return True


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


def test_add_group_menu_disables_with_reason_when_not_ready():
    _qapp()
    menu = QMenu()
    pet = FakePet(wanted=True, ready=False, reason="需要至少 2 只同屏桌宠")
    action = add_group_gathering_menu(menu, pet, icons=False)
    assert action is not None
    assert action.isEnabled() is False
    assert "需要至少 2 只" in action.toolTip()
    action.trigger()
    assert pet.triggered == []
    menu.deleteLater()


def test_add_group_menu_triggers_configured_gather_when_ready():
    _qapp()
    menu = QMenu()
    pet = FakePet(wanted=True, ready=True)
    action = add_group_gathering_menu(menu, pet, icons=False)
    assert action is not None
    assert action.isEnabled() is True
    action.trigger()
    assert pet.triggered == [1]
    menu.deleteLater()
