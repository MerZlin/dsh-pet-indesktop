# -*- coding: utf-8 -*-
"""规则表 ↔ 表格文本的映射测试（纯逻辑，不依赖 Qt）。

三张表都要能"规则 → 单元格 → 规则"往返一致；写错的行必须返回 None
（编辑器据此拒绝保存并指出第几行）。
"""
from __future__ import annotations

from pet import rules_text as rt


def test_flag_and_lines_helpers():
    assert rt.flag_to_text(True) == "是" and rt.flag_to_text(False) == "否"
    assert rt.text_to_flag("否", True) is False
    assert rt.text_to_flag("", True) is True
    assert rt.text_to_flag("乱七八糟", False) is False
    assert rt.lines_to_text(["a ", "", " b"]) == "a\nb"
    assert rt.text_to_lines("a\n\n b ") == ["a", "b"]


def test_weekday_label_round_trip():
    assert rt.weekday_label([0, 2]) == "周一、周三"
    assert rt.label_to_days("周一、周三") == [0, 2]
    assert rt.label_to_days("周一,周三 周六") == [0, 2, 5]
    assert rt.label_to_days("1, 3") == [1, 3]
    assert rt.label_to_days("乱写") == []


def test_timed_rule_round_trip_daily():
    rule = {"id": "a", "when": "daily", "time": "23:30", "days": [], "every": 60,
            "on": True, "summary": True, "lines": ["睡了", "真的睡了"]}
    cells = rt.timed_rule_to_row(rule)
    assert cells[1] == "每天" and cells[6] == "睡了\n真的睡了"
    back = rt.timed_row_to_rule(cells, rule_id="ui-1")
    assert back["when"] == "daily" and back["time"] == "23:30"
    assert back["summary"] is True and back["lines"] == ["睡了", "真的睡了"]


def test_timed_rule_round_trip_interval_and_weekly():
    interval = {"when": "interval", "every": 15, "on": True, "lines": ["歇会儿"]}
    back = rt.timed_row_to_rule(rt.timed_rule_to_row(interval), rule_id="x")
    assert back["when"] == "interval" and back["every"] == 15
    weekly = {"when": "weekly", "time": "08:00", "days": [0, 4], "on": False,
              "lines": ["周一啦"]}
    cells = rt.timed_rule_to_row(weekly)
    assert cells[3] == "周一、周五"
    back = rt.timed_row_to_rule(cells, rule_id="y")
    assert back["when"] == "weekly" and back["days"] == [0, 4] and back["on"] is False


def test_timed_row_rejects_bad_input():
    assert rt.timed_row_to_rule([]) is None
    # 每天但时间写错
    assert rt.timed_row_to_rule(["是", "每天", "25:99", "", "", "否", "台词"]) is None
    # 每隔但间隔不是数字
    assert rt.timed_row_to_rule(["是", "每隔", "", "", "abc", "否", "台词"]) is None
    # 每隔但没台词（等于不响）
    assert rt.timed_row_to_rule(["是", "每隔", "", "", "10", "否", ""]) is None


def test_usage_rule_round_trip_and_rejects():
    cells = rt.usage_rule_to_row("steam.exe", {"minutes": 120, "repeat": 60,
                                               "on": True, "lines": ["玩久了"]})
    assert cells[:4] == ["是", "steam.exe", "120", "60"]
    exe, rule = rt.usage_row_to_rule(cells)
    assert exe == "steam.exe" and rule["minutes"] == 120 and rule["lines"] == ["玩久了"]
    assert rt.usage_row_to_rule(["是", "", "60", "0", "台词"]) is None      # 没进程名
    assert rt.usage_row_to_rule(["是", "a.exe", "x", "0", "台词"]) is None   # 分钟非法
    assert rt.usage_row_to_rule(["是", "a.exe", "60", "0", ""]) is None     # 没台词


def test_hotkey_rule_round_trip_and_rejects():
    cells = rt.hotkey_rule_to_row({"seq": "ctrl+alt+1", "act": "lines", "on": True,
                                   "lines": ["夸一句"]})
    assert cells[1] == "Ctrl+Alt+1" and cells[2] == "说一句"
    back = rt.hotkey_row_to_rule(cells, rule_id="h1")
    assert back["seq"] == "Ctrl+Alt+1" and back["act"] == "lines"
    # 看天气不需要台词
    weather = rt.hotkey_row_to_rule(["是", "Ctrl+Alt+W", "看天气", ""], rule_id="h2")
    assert weather is not None and weather["act"] == "weather"
    # 非法键位 / 保留键 / 说一句但没台词
    assert rt.hotkey_row_to_rule(["是", "Alt+1", "说一句", "台词"]) is None
    assert rt.hotkey_row_to_rule(["是", "Ctrl+C", "说一句", "台词"]) is None
    assert rt.hotkey_row_to_rule(["是", "Ctrl+Alt+2", "说一句", ""]) is None
