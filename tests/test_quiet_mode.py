# -*- coding: utf-8 -*-
"""免打扰纯逻辑测试（不依赖 Qt、不依赖窗口）。

覆盖 pet/quiet_mode.py 的四件事：时长换算与夹取、暂存队列（含上限）、
结束汇报文案、以及"哪些事件必须穿透免打扰"的判定矩阵。
"""
from __future__ import annotations

import pytest

from pet import quiet_mode


def test_normalize_minutes_clamps_and_falls_back():
    assert quiet_mode.normalize_minutes(60) == 60
    assert quiet_mode.normalize_minutes("45") == 45
    # 非法值回落默认；越界值夹到区间内
    assert quiet_mode.normalize_minutes("abc") == quiet_mode.DEFAULT_QUIET_MINUTES
    assert quiet_mode.normalize_minutes(None) == quiet_mode.DEFAULT_QUIET_MINUTES
    assert quiet_mode.normalize_minutes(0) == quiet_mode.MIN_QUIET_MINUTES
    assert quiet_mode.normalize_minutes(10 ** 6) == quiet_mode.MAX_QUIET_MINUTES


def test_quiet_state_lifecycle_uses_monotonic_clock():
    state = quiet_mode.QuietState()
    assert state.active(now=100.0) is False
    assert state.start(30, now=100.0) == 30
    assert state.active(now=100.0) is True
    assert state.left(now=100.0) == 30 * 60
    assert state.left(now=100.0 + 60) == 29 * 60
    # 到点后不再活跃，且 left 不会变成负数
    assert state.active(now=100.0 + 30 * 60) is False
    assert state.left(now=100.0 + 9999) == 0


def test_expire_if_due_only_fires_once():
    state = quiet_mode.QuietState()
    state.start(15, now=0.0)
    assert state.expire_if_due(now=14 * 60) is False
    assert state.expire_if_due(now=15 * 60) is True
    # 已经关掉之后再调用不应再报"到点"
    assert state.expire_if_due(now=16 * 60) is False
    assert state.active(now=16 * 60) is False


def test_stop_reports_left_and_clears_state():
    state = quiet_mode.QuietState()
    state.start(30, now=0.0)
    assert state.stop(now=10 * 60) == 20 * 60
    assert state.active(now=10 * 60) is False
    # 已经到点时 stop 返回 0（汇报文案据此区分"我回来啦 / 不憋着了"）
    state.start(5, now=0.0)
    assert state.stop(now=5 * 60 + 1) == 0


def test_start_clears_previous_holds():
    state = quiet_mode.QuietState()
    state.hold("上一次攒的")
    state.start(30, now=0.0)
    assert state.held_count() == 0


def test_hold_keeps_only_recent_lines_and_ignores_blank():
    state = quiet_mode.QuietState()
    assert state.hold("") is False
    assert state.hold("   ") is False
    for index in range(quiet_mode.HELD_LIMIT + 5):
        assert state.hold(f"提醒 {index}") is True
    held = state.held
    assert len(held) == quiet_mode.HELD_LIMIT
    assert held[-1] == f"提醒 {quiet_mode.HELD_LIMIT + 4}"
    assert held[0] == "提醒 5"
    # take_held 取走并清空（汇报只报一次）
    taken = state.take_held()
    assert taken == held
    assert state.held_count() == 0


def test_held_summary_wording():
    assert quiet_mode.held_summary(0) == quiet_mode.IDLE_END_LINE
    assert quiet_mode.held_summary(3, "该睡了") == "刚才攒了 3 条提醒，最后一条是「该睡了」"
    assert quiet_mode.held_summary(2, "") == "刚才攒了 2 条提醒"


def test_format_left_reads_minutes_then_seconds():
    assert quiet_mode.format_left(600) == "还剩 10 分钟"
    assert quiet_mode.format_left(59) == "还剩 59 秒"
    assert quiet_mode.format_left(-5) == "还剩 0 秒"


@pytest.mark.parametrize(
    "alert_type,sticky,buttons,expected",
    [
        # 需人操作 / Agent 状态类：必须穿透
        ("approval", True, [("同意", object())], True),
        ("question", True, [("选项", object())], True),
        ("execution/failed", False, None, True),
        ("agent/request-error", False, None, True),
        ("control-result", False, None, True),
        ("lifecycle", False, None, True),
        # 任何带按钮的交互气泡都穿透（用户点不到等于 Agent 卡死）
        ("自定义类型", False, [("继续", object())], True),
        # 普通展示被压住
        ("watchdog", False, None, False),
        ("balance", False, None, False),
        ("task_complete", False, None, False),
        ("turn/end", False, None, False),
        ("self-talk", False, None, False),
    ],
)
def test_alert_survives_quiet_matrix(alert_type, sticky, buttons, expected):
    assert quiet_mode.alert_survives_quiet(
        alert_type, sticky=sticky, buttons=buttons, priority=3
    ) is expected


def test_alert_survives_quiet_is_case_insensitive():
    assert quiet_mode.alert_survives_quiet("APPROVAL") is True
    assert quiet_mode.alert_survives_quiet("") is False
    assert quiet_mode.alert_survives_quiet(None) is False
