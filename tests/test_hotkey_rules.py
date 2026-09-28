# -*- coding: utf-8 -*-
"""全局快捷键规则层测试（纯逻辑，不需要真的注册热键）。

覆盖：键名解析（合法 / 非法 / 必须带 Ctrl / 保留键）、显示名归一、
规则清洗（去重、动作白名单、台词要求、上限）与内置表形状。
"""
from __future__ import annotations

import pytest

from pet import hotkey_rules as hr


def test_normalize_seq_makes_display_form():
    assert hr.normalize_seq("ctrl + alt + 1") == "Ctrl+Alt+1"
    assert hr.normalize_seq("CTRL+SHIFT+f5") == "Ctrl+Shift+F5"
    assert hr.normalize_seq("control+win+space") == "Ctrl+Win+space"
    assert hr.normalize_seq("") == ""


@pytest.mark.parametrize(
    "seq,ok",
    [
        ("Ctrl+Alt+1", True),
        ("ctrl+alt+f12", True),
        ("Ctrl+Space", True),
        ("Ctrl+PageUp", True),
        ("Ctrl+Shift+Z", True),
        ("Alt+1", False),          # 必须带 Ctrl
        ("Shift+1", False),
        ("1", False),
        ("Ctrl", False),           # 只有修饰键
        ("Ctrl+C", False),         # 保留键：别抢用户的复制粘贴
        ("Ctrl+V", False),
        ("Ctrl+-", False),         # 认不出来的符号
        ("Ctrl+F25", False),
        ("", False),
        (None, False),
    ],
)
def test_hotkey_parse_is_conservative(seq, ok):
    parsed = hr.hotkey_parse(seq)
    assert (parsed is not None) is ok
    if ok:
        mods, vk = parsed
        assert mods & hr.HOTKEY_MIN_MODS
        assert isinstance(vk, int) and vk > 0


def test_hotkey_parse_reads_function_keys_and_letters():
    assert hr.hotkey_parse("Ctrl+Alt+F1") == (0x0003, 0x70)
    assert hr.hotkey_parse("Ctrl+Alt+F24") == (0x0003, 0x70 + 23)
    # 用 Ctrl+G 而不是 Ctrl+A：Ctrl+A/C/V/X/Z/S/F/W/T/N/R 都在保留名单里
    # （抢了就等于替用户按了全选/复制/查找……）
    assert hr.hotkey_parse("Ctrl+G") == (0x0002, ord("G"))
    assert hr.hotkey_parse("Ctrl+A") is None


def test_supported_hint_mentions_ctrl_rule():
    hint = hr.hotkey_supported_hint()
    assert "Ctrl" in hint and "F1" in hint


def test_clean_hotkeys_falls_back_and_filters():
    assert hr.clean_hotkeys(None) == hr.default_hotkeys()
    assert hr.clean_hotkeys("nope") == hr.default_hotkeys()
    cleaned = hr.clean_hotkeys([
        {"id": "a", "seq": "Ctrl+Alt+1", "act": "lines", "lines": [" 你好 ", ""]},
        {"id": "b", "seq": "Ctrl+Alt+1", "act": "lines", "lines": ["重复键，丢掉"]},
        {"id": "c", "seq": "Alt+2", "act": "lines", "lines": ["没有 Ctrl，丢掉"]},
        {"id": "d", "seq": "Ctrl+Alt+2", "act": "lines", "lines": []},
        {"id": "e", "seq": "Ctrl+Alt+W", "act": "weather", "lines": []},
        {"id": "f", "seq": "Ctrl+Alt+3", "act": "nonsense", "lines": ["未知动作按说一句"]},
    ])
    assert [rule["id"] for rule in cleaned] == ["a", "e", "f"]
    assert cleaned[0]["seq"] == "Ctrl+Alt+1" and cleaned[0]["lines"] == ["你好"]
    assert cleaned[1]["act"] == "weather" and cleaned[1]["lines"] == []
    assert cleaned[2]["act"] == "lines"      # 未知动作回落"说一句"


def test_clean_hotkeys_caps_count():
    many = [
        {"id": f"h{i}", "seq": f"Ctrl+Alt+F{(i % 24) + 1}", "act": "weather", "lines": []}
        for i in range(30)
    ]
    assert len(hr.clean_hotkeys(many)) <= hr.HOTKEY_LIMIT


def test_default_hotkeys_are_valid():
    for rule in hr.default_hotkeys():
        assert hr.hotkey_parse(rule["seq"]) is not None
        assert rule["lines"]
