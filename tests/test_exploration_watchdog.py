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


def test_plugin_user_message_does_not_override_goal(app):
    """agent.inject() 注入上下文（sourceKind=plugin）不得覆盖真人目标。

    每轮 4-5 条 system-reminder/技能目录注入记录都是 user/message,若不区分,
    goal 会被注入文案冲掉,探索看门狗据此误判「对话目标」。
    """
    wd = ExplorationWatchdog()
    try:
        wd.feed_record("agent", {"event": "user/message", "sourceKind": "user", "text": "帮我修 user/message 事件"})
        with wd._lock:
            assert wd._states["agent"]["goal"] == "帮我修 user/message 事件"
        wd.feed_record("agent", {"event": "user/message", "sourceKind": "plugin", "text": "<system-reminder> 技能目录……"})
        with wd._lock:
            assert wd._states["agent"]["goal"] == "帮我修 user/message 事件"
    finally:
        wd.close()


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


class _FakeClock:
    """单调钟替身：只实现 watchdog 模块用到的 time.monotonic。"""

    def __init__(self, now: float = 1000.0):
        self.now = now

    def monotonic(self) -> float:
        return self.now


def test_pause_stops_think_timer_and_ignores_feed(app):
    """桌宠隐藏（决策：方案A）——看门狗暂停：轮询停走、喂入忽略。"""
    wd = ExplorationWatchdog()
    try:
        wd.pause()
        assert not wd._think_timer.isActive(), "暂停后 1s 轮询必须停走"
        wd.feed_record("agent", {"event": "reasoning", "step": "s1", "text": "思考"})
        assert "agent" not in wd._states, "暂停期间喂入必须忽略，不积累状态"
        wd.pause()  # 幂等
        wd.resume()
        assert wd._think_timer.isActive(), "恢复后轮询必须重启"
        wd.resume()  # 幂等
    finally:
        wd.close()


def test_pause_does_not_latch_long_think_and_resume_reanchors(app, monkeypatch):
    """方案A：隐藏时长不计入任何时长判定，计时锚点整体后移暂停时长。

    已超阈值的长思考在暂停期间手动轮询也不得发射或置位已上报标志；
    恢复后思考被保守解除武装，真实继续的 Think（下一条 reasoning 重新
    武装）按恢复后的可见时长重新积累到阈值即提醒——推迟而非丢失，
    也不对隐藏期间已结束的思考补发过期提醒。
    """
    import pet.exploration_watchdog as watchdog_mod

    clock = _FakeClock()
    monkeypatch.setattr(watchdog_mod, "time", clock)
    wd = ExplorationWatchdog()
    seen = []
    wd.warning.connect(lambda session, payload: seen.append(payload))
    try:
        wd.feed_record("agent", {"event": "reasoning", "step": "s1", "text": "思考"})
        with wd._lock:
            state = wd._states["agent"]
            state["current"].think_started_at = clock.now - wd.long_think_seconds - 5
            started_before = state["started_at"]
            grace_delta_before = state["grace_until"] - state["started_at"]

        wd.pause()
        wd._poll_long_think()  # 暂停期间即使手动轮询也不得发射/置位
        assert not seen, "暂停期间不得发射长思考提醒"
        with wd._lock:
            assert wd._states["agent"]["current"].long_think_reported is False, "暂停期间轮询不得置位已上报标志（否则恢复后提醒永久丢失）"

        clock.now += 600  # 隐藏 10 分钟
        wd.resume()
        with wd._lock:
            state = wd._states["agent"]
            assert state["started_at"] == started_before + 600, "隐藏时长不得计入累计"
            assert abs((state["grace_until"] - state["started_at"]) - grace_delta_before) < 1e-6, "宽限期与起始时间的相对关系必须保持不变"
            # 恢复即保守解除武装：暂停期丢弃了 step/end，无法判断思考是否已结束
            assert state["current"].think_active is False
            assert state["current"].think_started_at is None

        wd._poll_long_think()
        assert not seen, "解除武装的旧思考不得在恢复后立即补发"

        # 思考真实继续：下一条 reasoning 重新武装，按恢复后的可见时长重新积累
        wd.feed_record("agent", {"event": "reasoning", "step": "s1", "text": "还在思考"})
        clock.now += wd.long_think_seconds
        wd._poll_long_think()
        assert len(seen) == 1, "恢复后继续的思考达到阈值应提醒（推迟而非丢失）"
        assert seen[0]["threshold_phase"] == "long-think"
    finally:
        wd.close()


def test_resume_does_not_replay_ended_think(app, monkeypatch):
    """X1 回归：思考在隐藏期内结束（step/end 记录被暂停丢弃），恢复后不得补发。

    旧实现（隐藏期照常检测）对该场景不补发；若暂停实现只丢记录不解除
    武装，恢复后会对已结束的思考补发过期长思考告警。
    """
    import pet.exploration_watchdog as watchdog_mod

    clock = _FakeClock()
    monkeypatch.setattr(watchdog_mod, "time", clock)
    wd = ExplorationWatchdog()
    seen = []
    wd.warning.connect(lambda session, payload: seen.append(payload))
    try:
        wd.feed_record("agent", {"event": "reasoning", "step": "s1", "text": "思考"})
        with wd._lock:
            wd._states["agent"]["current"].think_started_at = clock.now - wd.long_think_seconds - 5
        wd.pause()
        clock.now += 60
        wd.feed_record("agent", {"event": "step/end", "step": "s1"})  # 被暂停丢弃
        wd.resume()
        wd._poll_long_think()
        assert not seen, "隐藏期间已结束的思考不得在恢复后补发过期提醒"
        with wd._lock:
            assert wd._states["agent"]["current"].think_active is False
    finally:
        wd.close()


def test_turn_reset_preserves_time_anchors(app, monkeypatch):
    """方案A1：turn 边界重建状态时沿用旧的 started_at/grace_until。

    宽限/长运行是任务级语义（设置页文案：「Agent 启动后的前 N 分钟」
    「连续运行超过 N 分钟」），每轮重置会让宽限在 30 秒一轮的 workload 下
    永久生效、长运行永不触发。
    """
    import pet.exploration_watchdog as watchdog_mod

    clock = _FakeClock()
    monkeypatch.setattr(watchdog_mod, "time", clock)
    wd = ExplorationWatchdog()
    try:
        for _ in range(5):
            wd.feed_record("agent", {"event": "command/run", "step": "s1", "command": "ls"})
        with wd._lock:
            state = wd._states["agent"]
            started_at = state["started_at"]
            grace_until = state["grace_until"]
        clock.now += 30  # 本轮 30 秒
        wd.feed_record("agent", {"event": "turn/end", "step": "s1"})
        clock.now += 5
        for _ in range(5):
            wd.feed_record("agent", {"event": "command/run", "step": "s2", "command": "ls"})
        with wd._lock:
            state2 = wd._states["agent"]
            assert state2["steps"] == {}, "重复窗口应随重建清空（旧 step 不得残留）"
            assert state2["started_at"] == started_at, "turn 重置不得刷新 started_at（任务级计时）"
            assert state2["grace_until"] == grace_until, "turn 重置不得刷新 grace_until（启动宽限只送一次）"
    finally:
        wd.close()


def test_user_message_does_not_refresh_time_anchors(app, monkeypatch):
    """C1 回归：user/message 每轮必发，其建状态路径也必须沿用锚点记忆。

    真实事件顺序 turn/end → user/message → tool/call：若 user/message 分支
    用新时钟建状态，锚点会被刷新，任务级语义被绕过（每轮宽限续期）。
    """
    import pet.exploration_watchdog as watchdog_mod

    clock = _FakeClock()
    monkeypatch.setattr(watchdog_mod, "time", clock)
    wd = ExplorationWatchdog()
    try:
        for _ in range(5):
            wd.feed_record("agent", {"event": "command/run", "step": "s1", "command": "ls"})
        with wd._lock:
            started_at = wd._states["agent"]["started_at"]
            grace_until = wd._states["agent"]["grace_until"]
        clock.now += 30
        wd.feed_record("agent", {"event": "turn/end", "step": "s1"})
        clock.now += 5
        wd.feed_record("agent", {"event": "user/message", "text": "继续，顺便看看 src"})
        clock.now += 5
        wd.feed_record("agent", {"event": "tool/call", "step": "s2", "tool": "read", "filePath": "src/a.py"})
        with wd._lock:
            state = wd._states["agent"]
            assert state["goal"] == "继续，顺便看看 src", "目标提取不受影响"
            assert state["started_at"] == started_at, "user/message 建状态不得刷新 started_at"
            assert state["grace_until"] == grace_until, "user/message 建状态不得刷新 grace_until"
    finally:
        wd.close()


def test_resume_shifts_anchor_memory(app, monkeypatch):
    """pause/resume 的锚点后移必须同时作用于锚点记忆，否则隐藏时长会被计入。"""
    import pet.exploration_watchdog as watchdog_mod

    clock = _FakeClock()
    monkeypatch.setattr(watchdog_mod, "time", clock)
    wd = ExplorationWatchdog()
    try:
        for _ in range(5):
            wd.feed_record("agent", {"event": "command/run", "step": "s1", "command": "ls"})
        with wd._lock:
            started_before = wd._states["agent"]["started_at"]
        wd.pause()
        clock.now += 600
        wd.resume()
        wd.feed_record("agent", {"event": "turn/end", "step": "s1"})
        for _ in range(5):
            wd.feed_record("agent", {"event": "command/run", "step": "s2", "command": "ls"})
        with wd._lock:
            assert wd._states["agent"]["started_at"] == started_before + 600, "恢复后重置引用的锚点记忆必须已整体后移（隐藏时长不得计入）"
    finally:
        wd.close()


def test_idle_reset_preserves_anchors_under_wall_clock(app, monkeypatch):
    """A1 墙钟语义：idle/sleeping 重置同样沿用锚点（任务时间轴连续）。"""
    import pet.exploration_watchdog as watchdog_mod

    clock = _FakeClock()
    monkeypatch.setattr(watchdog_mod, "time", clock)
    wd = ExplorationWatchdog()
    try:
        for _ in range(5):
            wd.feed_record("agent", {"event": "command/run", "step": "s1", "command": "ls"})
        with wd._lock:
            started_at = wd._states["agent"]["started_at"]
        clock.now += 120  # 干活 2 分钟后 idle
        wd.feed_record("agent", {"event": "AgentStatus", "state": "idle"})
        clock.now += 1800  # 用户离开半小时再回来
        for _ in range(5):
            wd.feed_record("agent", {"event": "command/run", "step": "s2", "command": "ls"})
        with wd._lock:
            assert wd._states["agent"]["started_at"] == started_at, "idle 重置不得刷新 started_at（A1 墙钟语义）"
    finally:
        wd.close()
