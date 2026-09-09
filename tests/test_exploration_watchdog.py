# -*- coding: utf-8 -*-
"""探索循环 Watchdog 的 payload 回归测试。

P1-1：`_evaluate_locked` / `_poll_long_think` 构造 payload 时读取未定义的
`self.mode`，首次触发 warning 必抛 AttributeError，提醒永远发不出。
"""
from __future__ import annotations

import pytest
from PySide6.QtWidgets import QApplication

from pet.exploration_watchdog import ExplorationWatchdog


@pytest.fixture
def app():
    return QApplication.instance() or QApplication([])


def test_repeated_command_warning_payload_is_emitted(app):
    """同一命令重复 5 次触发 warning：payload 构造不得抛 AttributeError。"""
    wd = ExplorationWatchdog()
    seen = []
    wd.warning.connect(lambda session, payload: seen.append((session, payload)))
    try:
        for _ in range(5):
            wd.feed_record("agent", {"event": "command/run", "step": "s1", "command": "ls"})
    finally:
        wd.close()
    assert seen, "同一命令重复 5 次应触发 warning"
    session, payload = seen[0]
    assert session == "agent"
    assert payload["type"] == "pet/exploration-watchdog"
    assert payload["level"] == "warning"
    assert payload["reasons"]
    # mode 在 watchdog 内没有任何数据源、pet 侧也无消费方：不再输出未定义字段。
    assert "mode" not in payload


def test_long_think_warning_payload_is_emitted(app):
    """超长 Think 的定时轮询路径同样构造 payload，不得抛 AttributeError。"""
    wd = ExplorationWatchdog()
    seen = []
    wd.warning.connect(lambda session, payload: seen.append(payload))
    try:
        wd.feed_record("agent", {"event": "reasoning", "step": "s1", "text": "继续思考"})
        with wd._lock:
            state = wd._states["agent"]
            state["current"].think_started_at -= wd.long_think_seconds + 1
        wd._poll_long_think()
    finally:
        wd.close()
    assert seen, "超长 Think 应触发 warning"
    assert seen[0]["threshold_phase"] == "long-think"
    assert "mode" not in seen[0]
