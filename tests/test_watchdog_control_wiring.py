# -*- coding: utf-8 -*-
"""PR57 遗留接线：探索 Watchdog 控制链（档位 / 按钮 / 线程 / 回显 / 开关）。

覆盖五条接线：
1. 风险分达到 control_threshold 时 payload.level=control（不再恒为 warning），
   冷却/相位语义与既有 warning 一致；
2. control 级气泡带「自动优化 / 终止 / 忽略」按钮，warning 级维持纯提醒；
3. 按钮回调经后台线程调用 dsh_control.request，绝不阻塞 GUI 线程；
4. 控制结果经信号回主线程弹气泡，失败原因（timeout / not-found / rejected）可区分；
5. watchdog 总开关关闭时整条链零开销（无提醒、无线程、无请求）。
"""
from __future__ import annotations

import threading
import time

import pytest
from PySide6.QtWidgets import QApplication

import pet.dsh_control as dsh_control
from pet.agent_link import AgentLinkManager
from pet.config import Config
from pet.exploration_watchdog import ExplorationWatchdog


@pytest.fixture
def app():
    return QApplication.instance() or QApplication([])


def _wait_for(predicate, app, timeout=8.0):
    """事件同步等待：跑 Qt 事件循环直到条件成立或超时（CI 慢 runner 宽预算）。"""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        app.processEvents()
        if predicate():
            return True
        time.sleep(0.01)
    app.processEvents()
    return bool(predicate())


# ----------------------------------------------------------------------
# 1. 控制档位生效
# ----------------------------------------------------------------------

class TestWatchdogControlLevel:
    def _wd(self, **config):
        wd = ExplorationWatchdog()
        base = {"exploration_watchdog_enabled": True,
                "exploration_watchdog_warning_threshold": 3,
                "exploration_watchdog_control_threshold": 5,
                "exploration_watchdog_cooldown_steps": 3}
        base.update(config)
        wd.configure(base)
        return wd

    def _fresh_state(self, wd, session="agent"):
        wd.feed_record(session, {"event": "user/message", "text": "goal"})
        # 造一个 current step，让 current_seq 非 0（否则 cooldown 永远不生效）。
        wd.feed_record(session, {"event": "command/run", "step": "s1", "command": "ls"})
        with wd._lock:
            state = wd._states[session]
        state["last_inspected_seq"] = 0
        state["grace_until"] = 0.0  # 默认测 normal 相位；宽限相位由用例显式设置
        return state

    def test_score_between_thresholds_is_warning(self, app):
        wd = self._wd()
        state = self._fresh_state(wd)
        wd._score = lambda w6, w10: (4, ["重复探索"])
        try:
            payload = wd._evaluate_locked("agent", state)
        finally:
            wd.close()
        assert payload is not None
        assert payload["level"] == "warning"
        assert payload["risk"] == 4

    def test_score_at_control_threshold_is_control(self, app):
        wd = self._wd()
        state = self._fresh_state(wd)
        wd._score = lambda w6, w10: (5, ["重复探索"])
        try:
            payload = wd._evaluate_locked("agent", state)
        finally:
            wd.close()
        assert payload is not None, "达到 control_threshold 必须发告警（而非静默）"
        assert payload["level"] == "control"
        assert payload["risk"] == 5

    def test_control_uses_grace_adjusted_threshold(self, app):
        """宽限期内两个阈值都 +1：score=3 未达 control(4) → 仍是 warning。"""
        wd = self._wd(exploration_watchdog_warning_threshold=2,
                      exploration_watchdog_control_threshold=3)
        state = self._fresh_state(wd)
        state["grace_until"] = time.monotonic() + 300.0
        wd._score = lambda w6, w10: (3, ["重复探索"])
        try:
            payload = wd._evaluate_locked("agent", state)
        finally:
            wd.close()
        assert payload is not None
        assert payload["threshold_phase"] == "early/grace"
        assert payload["level"] == "warning"

    def test_control_uses_normal_thresholds_outside_grace(self, app):
        wd = self._wd(exploration_watchdog_warning_threshold=2,
                      exploration_watchdog_control_threshold=3)
        state = self._fresh_state(wd)
        state["grace_until"] = 0.0
        wd._score = lambda w6, w10: (3, ["重复探索"])
        try:
            payload = wd._evaluate_locked("agent", state)
        finally:
            wd.close()
        assert payload is not None
        assert payload["threshold_phase"] == "normal"
        assert payload["level"] == "control"

    def test_control_emit_respects_cooldown_steps(self, app):
        """控制级与 warning 共用同一 cooldown：发射后窗口内不再评估。"""
        wd = self._wd(exploration_watchdog_cooldown_steps=3)
        state = self._fresh_state(wd)
        wd._score = lambda w6, w10: (6, ["重复探索"])
        try:
            first = wd._evaluate_locked("agent", state)
            again = wd._evaluate_locked("agent", state)
        finally:
            wd.close()
        assert first is not None and first["level"] == "control"
        assert again is None, "cooldown 内不得重复发控制告警"

    def test_repeated_steps_escalate_warning_to_control(self, app):
        """端到端：第一步同命令重复 → warning；下一步 fingerprint 重复 → control。"""
        wd = self._wd(exploration_watchdog_cooldown_steps=1)
        seen = []
        wd.warning.connect(lambda session, payload: seen.append(payload))
        try:
            for step in ("s1", "s2"):
                for _ in range(5):
                    wd.feed_record("agent", {"event": "command/run", "step": step, "command": "ls"})
        finally:
            wd.close()
        levels = [payload["level"] for payload in seen]
        assert levels[:2] == ["warning", "control"], f"档位升级链不正确：{levels}"

    def test_long_think_stays_warning_level(self, app):
        """超长 Think 风险分恒为 0，不得误升级为控制级。"""
        wd = self._wd()
        seen = []
        wd.warning.connect(lambda session, payload: seen.append(payload))
        try:
            wd.feed_record("agent", {"event": "reasoning", "step": "s1", "text": "继续思考"})
            with wd._lock:
                wd._states["agent"]["current"].think_started_at -= wd.long_think_seconds + 1
            wd._poll_long_think()
        finally:
            wd.close()
        assert seen and seen[0]["level"] == "warning"


# ----------------------------------------------------------------------
# 2/3/4/5. AgentLinkManager 接线
# ----------------------------------------------------------------------

class FakeWin:
    def __init__(self):
        self.alerts = []
        self.resolved = []
        self.shown = []
        self._visible = True
        self._bubble_suppressed = False

    def isVisible(self):
        return self._visible

    def show_alert(self, text, *, subtitle="", duration_ms=0, buttons=None,
                   sticky=True, alert_id="", priority=3, alert_type="watchdog",
                   metadata=None):
        self.alerts.append({
            "text": str(text), "subtitle": str(subtitle), "buttons": buttons,
            "sticky": bool(sticky), "duration_ms": int(duration_ms),
            "alert_id": str(alert_id), "priority": int(priority),
            "alert_type": str(alert_type), "metadata": dict(metadata or {}),
        })

    def resolve_alert(self, alert_id):
        self.resolved.append(str(alert_id))

    def show_bubble(self, text, duration_ms=3000, sticky=False, buttons=None):
        self.shown.append(str(text))

    def request_link_anim(self, anim):
        pass


def _payload(**over):
    payload = {
        "type": "pet/exploration-watchdog",
        "level": "control",
        "risk": 6,
        "riskScore": 6,
        "reasons": ["W6 同类重复"],
        "steps": [{"behaviors": ["READ"], "targets": ["src/a.py"]}],
        "session_id": "sess-1",
        "goal": "修好登录",
        "agent_key": "dsh",
        "agent_name": "DSH",
        "targetCount": 1,
        "targets": ["src/a.py"],
    }
    payload.update(over)
    return payload


@pytest.fixture
def mgr(app, tmp_path):
    manager = AgentLinkManager(FakeWin(), Config(base=tmp_path))
    manager._clock = lambda: 1000.0
    yield manager
    manager.shutdown()


def _button(alert, label):
    for name, callback in (alert.get("buttons") or []):
        if name == label:
            return callback
    raise AssertionError(f"未找到按钮 {label}：{alert.get('buttons')}")


class TestControlBubbleButtons:
    def test_control_level_bubble_has_action_buttons(self, mgr):
        mgr._on_exploration_warning("sess-1", _payload())
        alert = mgr.win.alerts[-1]
        labels = [name for name, _ in (alert["buttons"] or [])]
        # B2：「自动优化」实效不可靠已移除，只保留「终止 / 忽略」。
        assert labels == ["终止", "忽略"]
        assert alert["sticky"] is True, "带按钮的控制提醒必须常驻，否则按钮会随超时消失"
        assert alert["alert_id"] == "exploration-control:sess-1"
        assert alert["alert_type"] == "control"
        assert alert["priority"] == 2

    def test_warning_level_bubble_stays_plain(self, mgr):
        mgr._on_exploration_warning("sess-1", _payload(level="warning"))
        alert = mgr.win.alerts[-1]
        assert alert["buttons"] is None, "warning 级维持纯提醒"
        assert alert["sticky"] is False
        assert alert["alert_type"] == "watchdog-warning"

    def test_missing_level_defaults_to_warning(self, mgr):
        payload = _payload()
        payload.pop("level")
        mgr._on_exploration_warning("sess-1", payload)
        assert mgr.win.alerts[-1]["buttons"] is None

    def test_control_alert_metadata_carries_context(self, mgr):
        mgr._on_exploration_warning("sess-1", _payload())
        meta = mgr.win.alerts[-1]["metadata"]
        assert meta["sessionId"] == "sess-1"
        assert meta["riskScore"] == 6
        assert meta["riskReasons"] == ["W6 同类重复"]

    def test_control_escalates_over_recent_warning(self, mgr):
        mgr._on_exploration_warning("sess-1", _payload(level="warning"))
        mgr._on_exploration_warning("sess-1", _payload())
        assert len(mgr.win.alerts) == 2, "控制级应覆盖 30s 窗口内的普通提醒"

    def test_warning_throttled_after_recent_control(self, mgr):
        mgr._on_exploration_warning("sess-1", _payload())
        mgr._on_exploration_warning("sess-1", _payload(level="warning"))
        assert len(mgr.win.alerts) == 1, "控制级之后同窗口的普通提醒应被节流"


class TestControlRequestThreading:
    def _capture(self, monkeypatch, behavior=None):
        captured = []
        calls = threading.Event()

        def fake_request(operation, session_id, text="", ports=None, *, goal="",
                         context="", provider="", model="", timeout=30.0, alert_id="",
                         cancel=None):
            captured.append({
                "operation": operation, "session_id": session_id, "goal": goal,
                "context": context, "alert_id": alert_id, "timeout": timeout,
                "thread": threading.current_thread(),
            })
            calls.set()
            if behavior is not None:
                return behavior(operation, session_id)
            return True, '{"ok": true, "phase": "replanned"}'

        monkeypatch.setattr(dsh_control, "request", fake_request)
        return captured, calls

    def test_terminate_button_runs_request_off_gui_thread(self, app, mgr, monkeypatch):
        # B2：「自动优化」已移除（实效不可靠），线程性验证改走「终止」按钮。
        captured, calls = self._capture(monkeypatch)
        mgr._on_exploration_warning("sess-1", _payload())
        _button(mgr.win.alerts[-1], "终止")()
        assert calls.wait(5.0), "按钮回调应真的发起控制请求"
        assert _wait_for(lambda: len(mgr.win.alerts) >= 2, app), "应回显控制结果"
        call = captured[-1]
        assert call["thread"] is not threading.main_thread(), "请求绝不许跑在 GUI 线程"
        assert call["operation"] == "interrupt"
        assert call["session_id"] == "sess-1"
        assert call["goal"] == "修好登录"
        assert "W6 同类重复" in call["context"]
        assert call["alert_id"] == "exploration-control:sess-1"
        assert call["timeout"] == mgr._EXPLORATION_CONTROL_TIMEOUT_S

    def test_interrupt_button_uses_interrupt_operation(self, app, mgr, monkeypatch):
        captured, calls = self._capture(
            monkeypatch, behavior=lambda op, sid: (True, '{"ok": true, "phase": "cancelled"}'))
        mgr._on_exploration_warning("sess-1", _payload())
        _button(mgr.win.alerts[-1], "终止")()
        assert calls.wait(5.0)
        assert captured[-1]["operation"] == "interrupt"

    def test_ignore_button_dismisses_without_request(self, app, mgr, monkeypatch):
        captured, calls = self._capture(monkeypatch)
        mgr._on_exploration_warning("sess-1", _payload())
        _button(mgr.win.alerts[-1], "忽略")()
        assert mgr.win.resolved == ["exploration-control:sess-1"]
        assert not calls.wait(0.2), "忽略不得发起控制请求"

    def test_button_click_does_not_block_gui_thread(self, app, mgr, monkeypatch):
        release = threading.Event()
        started = threading.Event()

        def fake_request(operation, session_id, text="", ports=None, *, goal="",
                         context="", provider="", model="", timeout=30.0, alert_id="",
                         cancel=None):
            started.set()
            release.wait(5.0)
            return True, '{"ok": true, "phase": "replanned"}'

        monkeypatch.setattr(dsh_control, "request", fake_request)
        mgr._on_exploration_warning("sess-1", _payload())
        # B2：「自动优化」已移除，用「终止」按钮验证不阻塞 GUI 线程
        callback = _button(mgr.win.alerts[-1], "终止")
        begin = time.monotonic()
        callback()
        elapsed = time.monotonic() - begin
        try:
            assert elapsed < 1.0, f"按钮回调阻塞了 GUI 线程 {elapsed:.2f}s"
            assert started.wait(5.0), "后台线程应已开始请求"
        finally:
            release.set()
        assert _wait_for(lambda: len(mgr.win.alerts) >= 2, app)

    def test_duplicate_click_while_inflight_ignored(self, app, mgr, monkeypatch):
        release = threading.Event()
        count = []

        def fake_request(operation, session_id, text="", ports=None, *, goal="",
                         context="", provider="", model="", timeout=30.0, alert_id="",
                         cancel=None):
            count.append(operation)
            release.wait(5.0)
            return True, '{"ok": true, "phase": "cancelled"}'

        monkeypatch.setattr(dsh_control, "request", fake_request)
        mgr._on_exploration_warning("sess-1", _payload())
        first = _button(mgr.win.alerts[-1], "终止")
        first()
        # 第一次点击后气泡已收起，直接复用同一回调模拟连点。
        first()
        try:
            assert _wait_for(lambda: len(count) >= 1, app, timeout=5.0)
            assert len(count) == 1, "同一会话在飞请求期间不得重复发起"
        finally:
            release.set()
        assert _wait_for(lambda: len(mgr.win.alerts) >= 2, app)


class TestControlResultEcho:
    def _result_alert(self, mgr):
        alerts = [a for a in mgr.win.alerts if a["alert_type"] == "control-result"]
        assert alerts, "应弹出控制结果气泡"
        return alerts[-1]

    def test_success_replan_echo(self, mgr):
        mgr._show_exploration_control_result("sess-1", "replan", True,
                                             '{"ok": true, "phase": "replanned"}')
        assert "已按新方向重新规划" in self._result_alert(mgr)["text"]
        assert self._result_alert(mgr)["sticky"] is False

    def test_success_interrupt_echo(self, mgr):
        mgr._show_exploration_control_result("sess-1", "interrupt", True,
                                             '{"ok": true, "phase": "cancelled"}')
        assert "已终止本次运行" in self._result_alert(mgr)["text"]

    def test_already_idle_interrupt_echo(self, mgr):
        mgr._show_exploration_control_result("sess-1", "interrupt", True,
                                             '{"ok": true, "phase": "already-idle"}')
        assert "已经是空闲状态" in self._result_alert(mgr)["text"]

    @pytest.mark.parametrize("detail,expected", [
        ("bridge-control-timeout", "超时"),
        ("cancel-timeout", "超时"),
        ("session-not-found", "会话不存在"),
        ("bridge-control-rejected", "拒绝了"),
        ("bridge-internal-error", "失败"),
    ])
    def test_failure_reasons_are_distinguishable(self, mgr, detail, expected):
        mgr._show_exploration_control_result("sess-1", "interrupt", False, detail)
        assert expected in self._result_alert(mgr)["text"]

    def test_result_alert_id_scoped_per_session(self, mgr):
        mgr._show_exploration_control_result("sess-1", "replan", True, "{}")
        mgr._show_exploration_control_result("sess-2", "replan", True, "{}")
        ids = [a["alert_id"] for a in mgr.win.alerts if a["alert_type"] == "control-result"]
        assert ids == ["exploration-control-result:sess-1",
                       "exploration-control-result:sess-2"], "多会话结果不得互相顶替"


class TestControlResultSubagentEcho:
    """子代理归一后的控制回执：区分「已终止会话（含子代理）」与
    「已终止子代理（主代理仍在运行）」，让用户看懂控制作用到了哪个层级。"""

    def _result_alert(self, mgr, expected=1):
        alerts = [a for a in mgr.win.alerts if a["alert_type"] == "control-result"]
        assert len(alerts) >= expected, "应弹出控制结果气泡"
        return alerts[-1]

    def test_interrupt_subagent_normalized_to_root(self, mgr):
        """目标是子代理且已归一到根：应报「已终止会话」，而非误导为只停子代理。"""
        mgr._show_exploration_control_result(
            "sess-sub", "interrupt", True,
            '{"ok": true, "phase": "cancelled", "wasSubagent": true, '
            '"appliedToRoot": true, "rootSessionId": "sess-root"}')
        text = self._result_alert(mgr)["text"]
        assert "已终止会话" in text
        assert "已终止子代理" not in text

    def test_interrupt_subagent_without_root(self, mgr):
        """目标是子代理但父级不可解析（降级只停子代理）：必须如实说明主代理仍在运行。"""
        mgr._show_exploration_control_result(
            "sess-sub", "interrupt", True,
            '{"ok": true, "phase": "cancelled", "wasSubagent": true, '
            '"appliedToRoot": false}')
        text = self._result_alert(mgr)["text"]
        assert "已终止子代理" in text
        assert "主代理仍在运行" in text

    def test_replan_subagent_normalized_to_root(self, mgr):
        """replan 归一到根：提示作用于主会话。"""
        mgr._show_exploration_control_result(
            "sess-sub", "replan", True,
            '{"ok": true, "phase": "replanned", "wasSubagent": true, '
            '"appliedToRoot": true, "rootSessionId": "sess-root"}')
        text = self._result_alert(mgr)["text"]
        assert "已按新方向重新规划" in text
        assert "主会话" in text

    def test_replan_subagent_without_root(self, mgr):
        """replan 无法归一（父级不可解析）：仍报已规划，但不宣称作用于主会话。"""
        mgr._show_exploration_control_result(
            "sess-sub", "replan", True,
            '{"ok": true, "phase": "replanned", "wasSubagent": true, '
            '"appliedToRoot": false}')
        text = self._result_alert(mgr)["text"]
        assert "已按新方向重新规划" in text
        assert "主会话" not in text

    def test_existing_root_interrupt_echo_unchanged(self, mgr):
        """非子代理的顶层会话回执保持原样（回归锁定）。"""
        mgr._show_exploration_control_result("sess-1", "interrupt", True,
                                             '{"ok": true, "phase": "cancelled"}')
        text = self._result_alert(mgr)["text"]
        assert "已终止本次运行" in text


class TestControlSuccessGrantsWatchdogGrace:
    """F12：控制成功后必须给目标 session 宽限，否则按旧历史立即重评会再弹控制告警。"""

    def _watchdog(self, mgr):
        wd = mgr._exploration_watchdog
        wd.configure({
            "exploration_watchdog_enabled": True,
            "exploration_watchdog_warning_threshold": 2,
            "exploration_watchdog_control_threshold": 3,
            "exploration_watchdog_cooldown_steps": 0,
        })
        wd.feed_record("sess-1", {"event": "user/message", "text": "目标"})
        wd.feed_record("sess-1", {"event": "command/run", "step": "s1", "command": "ls"})
        with wd._lock:
            state = wd._states["sess-1"]
        # 启动默认 5 分钟宽限会抬高阈值，先关掉它让 control 档位可复现。
        state["grace_until"] = 0.0
        state["last_inspected_seq"] = 0
        wd._score = lambda w6, w10: (3, ["重复探索"])
        return wd, state

    def test_control_success_downgrades_immediate_repeat(self, mgr):
        wd, state = self._watchdog(mgr)
        first = wd._evaluate_locked("sess-1", state)
        assert first is not None and first["level"] == "control"
        # 用户点「自动优化」，后台 worker 成功回执回主线程。
        mgr._on_exploration_control_result(
            "sess-1", "replan", True, '{"ok": true, "phase": "replanned"}')
        with wd._lock:
            assert wd._states["sess-1"]["grace_until"] > time.monotonic(), \
                "控制成功必须给目标 session 宽限"
        repeat = wd._evaluate_locked("sess-1", state)
        assert repeat is None or repeat["level"] == "warning", \
            f"宽限期内不得再弹 control 级告警：{repeat}"

    def test_control_failure_does_not_grant_grace(self, mgr):
        wd, state = self._watchdog(mgr)
        mgr._on_exploration_control_result(
            "sess-1", "replan", False, "bridge-control-timeout")
        with wd._lock:
            assert wd._states["sess-1"]["grace_until"] == 0.0, "失败回执不该给宽限"


class TestWatchdogDisabledIsZeroOverhead:
    def test_disabled_watchdog_never_warns_or_starts_threads(self, app, mgr, monkeypatch):
        calls = []
        monkeypatch.setattr(dsh_control, "request",
                            lambda *a, **k: calls.append((a, k)) or (True, "{}"))
        mgr._exploration_watchdog.configure({
            "exploration_watchdog_enabled": False,
            "exploration_watchdog_warning_threshold": 1,
            "exploration_watchdog_control_threshold": 1,
            "exploration_watchdog_cooldown_steps": 1,
        })
        for _ in range(8):
            mgr._exploration_watchdog.feed_record(
                "dsh", {"event": "command/run", "step": "s1", "command": "ls"})
        assert mgr.win.alerts == [], "总开关关闭时不得有任何提醒"
        assert mgr._respond_threads == set(), "总开关关闭时不得创建后台线程"
        assert calls == [], "总开关关闭时不得发起控制请求"

    def test_enabled_watchdog_still_warns(self, app, mgr):
        mgr._exploration_watchdog.configure({
            "exploration_watchdog_enabled": True,
            "exploration_watchdog_warning_threshold": 1,
            "exploration_watchdog_control_threshold": 99,
            "exploration_watchdog_cooldown_steps": 1,
        })
        for _ in range(5):
            mgr._exploration_watchdog.feed_record(
                "dsh", {"event": "command/run", "step": "s1", "command": "ls"})
        assert mgr.win.alerts, "开启时仍应正常告警"


class TestWatchdogPauseOnHide:
    def test_manager_pause_stops_watchdog_and_resume_restarts(self, app, tmp_path):
        """桌宠隐藏时 manager.pause() 必须同步暂停探索看门狗（决策：方案A）。"""
        cfg = Config(base=tmp_path)
        agent_cfg = dict(cfg.get("agent_link", {}))
        agent_cfg["dsh"] = True
        cfg.set("agent_link", agent_cfg)
        manager = AgentLinkManager(FakeWin(), cfg)
        try:
            assert manager._exploration_watchdog is not None
            manager.pause()
            assert not manager._exploration_watchdog._think_timer.isActive(), \
                "隐藏时看门狗轮询必须停走（否则告警在隐藏期发射后被丢弃，永久丢失）"
            manager.resume()
            assert manager._exploration_watchdog._think_timer.isActive(), "恢复显示后轮询必须重启"
        finally:
            manager.shutdown()
