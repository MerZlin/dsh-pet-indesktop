# -*- coding: utf-8 -*-
"""定时提醒纯逻辑测试（离线，不依赖 Qt）。

覆盖：时间/星期/间隔三种形态的清洗与到点判定、同分钟只触发一次、interval 首次不炸、
错过的窗口不补报、关闭或没台词的规则不触发。
"""
from __future__ import annotations

from datetime import datetime

from pet import timed_reminder as tr


def test_normalize_hhmm_accepts_common_shapes():
    assert tr.normalize_hhmm("23:30") == "23:30"
    assert tr.normalize_hhmm("9:5") == "09:05"
    assert tr.normalize_hhmm("930") == "09:30"
    assert tr.normalize_hhmm(" 7:00 ") == "07:00"
    assert tr.normalize_hhmm("25:00", "23:30") == "23:30"
    assert tr.normalize_hhmm("", "08:00") == "08:00"
    assert tr.normalize_hhmm(None, "08:00") == "08:00"
    assert tr.normalize_hhmm("abc") == ""


def test_clean_days_filters_dedupes_sorts():
    assert tr.clean_days([3, 1, 1, 9, -1, "2"]) == [1, 2, 3]
    assert tr.clean_days(None) == []
    assert tr.clean_days(["x"]) == []


def test_clean_timed_rules_falls_back_and_caps():
    assert tr.clean_timed_rules(None) == tr.default_timed_rules()
    assert tr.clean_timed_rules("nope") == tr.default_timed_rules()
    assert tr.clean_timed_rules([]) == tr.default_timed_rules()
    many = [{"id": f"r{i}", "time": "08:00", "lines": ["x"]} for i in range(50)]
    assert len(tr.clean_timed_rules(many)) == tr.TIMED_RULE_LIMIT


def test_clean_timed_rules_fills_defaults_but_not_for_interval():
    daily = tr.clean_timed_rules([{"id": "a", "when": "daily", "time": "7:5"}])[0]
    assert daily["time"] == "07:05"
    assert daily["lines"] == list(tr.DEFAULT_TIMED_LINES)   # 没写台词补内置
    interval = tr.clean_timed_rules([{"id": "b", "when": "interval", "every": 15}])[0]
    assert interval["lines"] == []                          # 间隔规则不塞定时台词
    assert interval["every"] == 15
    bad_every = tr.clean_timed_rules([{"id": "c", "when": "interval", "every": "x"}])[0]
    assert bad_every["every"] == 60


def test_describe_rule_reads_naturally():
    assert tr.describe_rule({"when": "daily", "time": "23:30"}) == "每天 23:30"
    assert tr.describe_rule({"when": "interval", "every": 15}) == "每 15 分钟"
    weekly = tr.describe_rule({"when": "weekly", "time": "08:00", "days": [0, 2]})
    assert weekly == "周一、周三 08:00"
    assert tr.describe_rule({"when": "weekly", "time": "08:00", "days": []}) == "未选星期 08:00"


def test_daily_rule_fires_once_per_minute():
    rules = [{"id": "a", "when": "daily", "time": "23:30", "on": True, "lines": ["睡了"]}]
    now = datetime(2026, 9, 29, 23, 30, 10)
    fired, state = tr.due_rule_ids(rules, {}, now)
    assert [rule["id"] for rule in fired] == ["a"]
    fired_again, state2 = tr.due_rule_ids(rules, state, datetime(2026, 9, 29, 23, 30, 50))
    assert fired_again == []                      # 同一分钟不重复
    assert state2 == state
    fired_next_day, _ = tr.due_rule_ids(rules, state2, datetime(2026, 9, 30, 23, 30, 0))
    assert [rule["id"] for rule in fired_next_day] == ["a"]


def test_daily_rule_does_not_replay_missed_window():
    rules = [{"id": "a", "when": "daily", "time": "23:30", "on": True, "lines": ["睡了"]}]
    fired, _ = tr.due_rule_ids(rules, {}, datetime(2026, 9, 29, 23, 45, 0))
    assert fired == []                            # 错过就错过，不补报


def test_weekly_rule_respects_weekday():
    rules = [{"id": "w", "when": "weekly", "time": "08:00", "days": [0], "on": True,
              "lines": ["周一啦"]}]
    monday = datetime(2026, 9, 28, 8, 0, 5)       # 2026-09-28 是周一
    assert monday.weekday() == 0
    fired, state = tr.due_rule_ids(rules, {}, monday)
    assert [rule["id"] for rule in fired] == ["w"]
    tuesday = datetime(2026, 9, 29, 8, 0, 5)
    fired, _ = tr.due_rule_ids(rules, {}, tuesday)
    assert fired == []


def test_interval_rule_first_tick_only_stamps():
    rules = [{"id": "i", "when": "interval", "every": 10, "on": True, "lines": ["歇会儿"]}]
    now = datetime(2026, 9, 29, 10, 0, 0)
    fired, state = tr.due_rule_ids(rules, {}, now, stamp=1000.0)
    assert fired == [] and state["i"] == "1000"
    fired, state = tr.due_rule_ids(rules, state, now, stamp=1000.0 + 9 * 60)
    assert fired == []                            # 还差一分钟
    fired, state = tr.due_rule_ids(rules, state, now, stamp=1000.0 + 10 * 60)
    assert [rule["id"] for rule in fired] == ["i"]
    assert state["i"] == f"{1000.0 + 600:.0f}"


def test_disabled_or_lineless_rules_are_skipped():
    rules = [
        {"id": "off", "when": "daily", "time": "23:30", "on": False, "lines": ["x"]},
        {"id": "empty", "when": "daily", "time": "23:30", "on": True, "lines": []},
    ]
    fired, state = tr.due_rule_ids(rules, {}, datetime(2026, 9, 29, 23, 30, 0))
    assert fired == []
    assert state == {}                            # 跳过的规则不写去重键
