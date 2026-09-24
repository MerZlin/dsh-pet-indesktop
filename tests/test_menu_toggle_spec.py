# -*- coding: utf-8 -*-
"""A3 回归：布尔开关菜单项「标签翻转 + 回调」模板的行为契约。

`_flag_toggle_spec` 把语音报时开关与节日提醒开关的同构实现收成一处
（`pet/context_menus/registry.py`）。本用例钉住提炼后的对外行为：

1. 配置开 → 显示「关闭 X」（点了就关）；配置关 → 显示「启用 X」；
2. `cfg` 缺失时按关处理（历史 `getattr(pet, "cfg", None)` 兜底语义）；
3. 点击落到 pet 上对应的 toggle 回调，且 close_on_trigger 语义不变。
"""

from __future__ import annotations

import pytest
from PySide6.QtWidgets import QApplication, QMenu

from pet.context_menus import registry as registry_mod


@pytest.fixture
def app():
    return QApplication.instance() or QApplication([])


class _Cfg:
    def __init__(self, values: dict):
        self._values = dict(values)

    def get(self, key, default=None):
        return self._values.get(key, default)


class _Pet:
    def __init__(self, values: dict | None = None, with_cfg: bool = True):
        if with_cfg:
            self.cfg = _Cfg(values or {})
        self.voice_calls = 0
        self.festival_calls = 0

    def on_toggle_voice_chime(self):
        self.voice_calls += 1

    def on_toggle_festival(self):
        self.festival_calls += 1


@pytest.mark.parametrize(
    "action_id,key,caller,on_label,off_label",
    [
        ("voice_chime_toggle", "voice_chime_enabled", "voice_calls", "关闭语音报时", "启用语音报时"),
        ("festival_toggle", "festival_reminder_enabled", "festival_calls", "关闭节日提醒", "启用节日提醒"),
    ],
)
def test_flag_toggle_spec_flips_label_and_click_calls_back(app, action_id, key, caller, on_label, off_label):
    spec = registry_mod.MENU_ACTIONS._specs[action_id]

    # 关闭态 → 显示「启用 X」，点击调用回调
    pet = _Pet({key: False})
    menu = QMenu()
    action = spec.build(menu, pet)
    assert action.text() == off_label
    action.trigger()
    assert getattr(pet, caller) == 1
    menu.close()

    # 开启态 → 显示「关闭 X」，点击同样调用回调
    pet = _Pet({key: True})
    menu = QMenu()
    action = spec.build(menu, pet)
    assert action.text() == on_label
    action.trigger()
    assert getattr(pet, caller) == 1
    menu.close()
    app.processEvents()


def test_flag_toggle_spec_tolerates_missing_config(app):
    """无 cfg 的宿主（裸测试替身）按「关」渲染，不得抛异常。"""
    spec = registry_mod.MENU_ACTIONS._specs["voice_chime_toggle"]
    pet = _Pet(with_cfg=False)
    menu = QMenu()
    action = spec.build(menu, pet)
    assert action.text() == "启用语音报时"
    action.trigger()
    assert pet.voice_calls == 1
    menu.close()
    app.processEvents()


def test_two_toggles_share_one_factory():
    """两个开关必须由同一工厂产出（防回潮：各自再抄一份同构实现）。"""
    for builder in (registry_mod._build_voice_chime_toggle, registry_mod._build_festival_toggle):
        assert builder.__qualname__.startswith("_flag_toggle_spec.<locals>"), f"{builder.__qualname__} 必须来自 _flag_toggle_spec 工厂"
