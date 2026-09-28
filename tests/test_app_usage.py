# -*- coding: utf-8 -*-
"""用久了提醒纯逻辑测试（离线：时间戳全部注入，不睡真时间）。

覆盖：换应用重置、键鼠静默冻结、首次到点、重复间隔、规则开关与空台词、变量替换。
"""
from __future__ import annotations

from pet import app_usage as au

RULES = {
    "steam.exe": {"minutes": 60, "repeat": 30, "on": True, "lines": ["{app} 玩了 {hm}"]},
    "code.exe": {"minutes": 30, "repeat": 0, "on": False, "lines": ["写代码 {minutes} 分钟"]},
}


def test_format_duration_buckets():
    assert au.format_duration(0) == "0 秒"
    assert au.format_duration(59) == "59 秒"
    assert au.format_duration(60) == "1 分"
    assert au.format_duration(2 * 3600 + 10 * 60) == "2 小时 10 分"
    assert au.format_duration("bad") == "0 秒"


def test_fill_vars_keeps_unknown_placeholders():
    assert au.fill_vars("{app} {minutes} {hm}", app="steam.exe", minutes=61,
                        hm="1 小时 1 分") == "steam.exe 61 1 小时 1 分"
    assert au.fill_vars("{unknown}") == "{unknown}"


def test_clean_rules_clamps_and_falls_back():
    assert au.clean_rules(None) == au.DEFAULT_USAGE_RULES
    assert au.clean_rules("nope") == au.DEFAULT_USAGE_RULES
    assert au.clean_rules({}) == au.DEFAULT_USAGE_RULES
    cleaned = au.clean_rules({
        " Steam.EXE ": {"minutes": "0", "repeat": "-5", "lines": [" x ", ""]},
        "bad": "nope",
    })
    assert cleaned == {"steam.exe": {"minutes": 1, "repeat": 0, "on": True, "lines": ["x"]}}


def test_clean_rules_caps_entries():
    many = {f"app{i}.exe": {"minutes": 10, "lines": ["x"]} for i in range(60)}
    assert len(au.clean_rules(many)) == au.USAGE_RULE_LIMIT


def test_tracker_resets_on_app_switch():
    tracker = au.UsageTracker(RULES)
    assert tracker.feed("steam.exe", 0.0) is None            # 第一次只记起点
    assert tracker.feed("steam.exe", 60 * 60) == "steam.exe 玩了 1 小时"  # 到 60 分钟
    # 换应用：重新计时，且不该立刻因为旧累计而开口
    assert tracker.feed("other.exe", 60 * 60 + 1) is None
    assert tracker.feed("other.exe", 60 * 60 + 2) is None


def test_tracker_freezes_when_idle():
    tracker = au.UsageTracker(RULES)
    tracker.feed("steam.exe", 0.0)
    # 键鼠静了 10 分钟以上：这一拍不累计
    assert tracker.feed("steam.exe", 60 * 60, idle_seconds=au.FG_IDLE_FREEZE + 1) is None
    # 人回来了：从冻结那一刻继续，累计仍是 0 → 还没到 60 分钟
    assert tracker.feed("steam.exe", 60 * 60 + 1, idle_seconds=1.0) is None


def test_tracker_repeat_interval():
    tracker = au.UsageTracker(RULES)
    tracker.feed("steam.exe", 0.0)
    assert tracker.feed("steam.exe", 60 * 60) is not None        # 首次到点
    # 同一分钟内不再重复
    assert tracker.feed("steam.exe", 60 * 60 + 1) is None
    # repeat=30 → 再等 30 分钟
    assert tracker.feed("steam.exe", 90 * 60 - 1) is None
    assert tracker.feed("steam.exe", 90 * 60) is not None


def test_tracker_skips_disabled_and_lineless_rules():
    tracker = au.UsageTracker({
        "off.exe": {"minutes": 1, "repeat": 0, "on": False, "lines": ["x"]},
        "empty.exe": {"minutes": 1, "repeat": 0, "on": True, "lines": []},
        "nope.exe": {"minutes": 1, "repeat": 0, "on": True, "lines": ["y"]},
    })
    for name in ("off.exe", "empty.exe", "nope.exe"):
        tracker.feed(name, 0.0)
    assert tracker.feed("off.exe", 600.0) is None
    assert tracker.feed("empty.exe", 600.0) is None
    assert tracker.feed("nope.exe", 600.0) is None    # 没配规则


def test_tracker_variables_and_minutes():
    tracker = au.UsageTracker({"code.exe": {
        "minutes": 30, "repeat": 0, "on": True, "lines": ["写代码 {minutes} 分钟啦"],
    }})
    tracker.feed("code.exe", 0.0)
    assert tracker.feed("code.exe", 45 * 60) == "写代码 45 分钟啦"
