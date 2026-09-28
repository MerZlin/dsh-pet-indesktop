# -*- coding: utf-8 -*-
"""菜单动作必须有中文标签（默认中文，不许回落到英文 id）。

背景：菜单编排器用 ``MenuActionRegistry.label()`` 取显示名，而它是
``ACTION_LABELS.get(id, id)`` —— 漏登记的动作会在编排列表里显示成
``voice_chime_now`` 这种英文 id（2026-09-29 用户反馈）。
本文件把"每个注册动作都有中文标签"变成断言，防止以后再漏。
"""
from __future__ import annotations

from pet.context_menus.registry import ACTION_LABELS, MenuActionRegistry


def _has_cjk(text: str) -> bool:
    return any("\u4e00" <= char <= "\u9fff" for char in str(text or ""))


def test_every_registered_action_has_a_label():
    registry = MenuActionRegistry()
    missing = sorted(action_id for action_id in registry.ids if action_id not in ACTION_LABELS)
    assert missing == [], (
        f"这些动作缺 ACTION_LABELS（菜单编排会显示英文 id）：{missing}"
    )


def test_labels_are_chinese_and_not_raw_ids():
    registry = MenuActionRegistry()
    bad = [
        (action_id, registry.label(action_id))
        for action_id in sorted(registry.ids)
        if registry.label(action_id) == action_id or not _has_cjk(registry.label(action_id))
    ]
    assert bad == [], f"这些动作的显示名不是中文：{bad}"


def test_voice_chime_and_festival_labels_are_chinese():
    """用户截图里露出来的四个 id：必须给中文名，而不是英文键名。"""
    for action_id, expected in (
        ("voice_chime_now", "立即报时"),
        ("voice_chime_toggle", "语音报时开关"),
        ("festival_now", "今日节日"),
        ("festival_toggle", "节日提醒开关"),
    ):
        assert ACTION_LABELS.get(action_id) == expected
