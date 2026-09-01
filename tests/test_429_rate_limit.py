# -*- coding: utf-8 -*-
"""429 限流提醒专项测试：独立事件、同 session 合并、多 session 隔离、可关闭。

覆盖 429 显示链路的关键分支，不依赖其他测试模块（避免被无关导入拖垮）：
- 首次 429 → 高优先级提醒，alert_id 带 sessionId；
- 同 session 8s 内连续 429 → 合并计数并刷新文案；
- 不同 session 并发 429 → 各自独立，互不顶替；
- 点「知道了」→ 清理缓存并关闭对应 alert；
- 429 活跃期间 execution/failed → 抑制通用失败横幅，避免双重通知。
"""
from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from pet.agent_link import AgentLinkManager
from pet.config import Config


class FakeCfg:
    """极简配置桩：AgentLinkManager 只需要 .dir / .get / .set / .save。

    不用真实 Config（其 base 会落到系统 Temp，测试沙箱可能拒绝写入），
    避免文件系统权限干扰，专注 429 显示链路本身。
    """

    def __init__(self, data=None):
        self._data = data if data is not None else {}
        self.dir = Path(tempfile.gettempdir()) / "dsh-test-noop"

    def get(self, key, default=None):
        return self._data.get(key, default)

    def set(self, key, value):
        self._data[key] = value

    def save(self):
        pass

    def _load(self):
        pass


class RecordingWin:
    """记录 show_alert / resolve_alert 的测试窗口。

    有意不提供 ``_bubble_busy_until``：``AgentLinkManager._schedule_429_dismiss``
    会据该 sentinel 跳过 QTimer 分支，从而在无 Qt 事件循环的测试环境下稳定运行。
    """

    def __init__(self):
        self.alerts: list[dict] = []
        self.resolved: list[str] = []
        self.shown: list[str] = []
        self._visible = True

    def isVisible(self) -> bool:
        return self._visible

    def show_alert(self, text, *, subtitle="", duration_ms=0, buttons=None,
                   sticky=True, alert_id="", priority=3, alert_type="watchdog",
                   metadata=None):
        self.alerts.append({
            "text": str(text),
            "sticky": bool(sticky),
            "duration_ms": int(duration_ms),
            "alert_id": str(alert_id),
            "priority": int(priority),
            "alert_type": str(alert_type),
        })

    def resolve_alert(self, alert_id) -> None:
        self.resolved.append(str(alert_id))

    def show_bubble(self, text, duration_ms=3200, sticky=False, buttons=None) -> None:
        self.shown.append(str(text))


@pytest.fixture
def mgr():
    return AgentLinkManager(RecordingWin(), FakeCfg())


def test_first_429_shows_high_priority_reminder(mgr):
    mgr._on_rate_limit("dsh", {"sessionId": "sess-1"})
    assert mgr.win.alerts, "首次 429 应弹出提醒"
    alert = mgr.win.alerts[-1]
    # alert_id 必须带 sessionId，供多 session 隔离
    assert alert["alert_id"] == "429-rate-limit:sess-1"
    assert "429" in alert["text"], "文案包含 429 状态码"
    # 文案应表达「请求受限/限流」语义（用等宽片段，避免依赖控制台中文渲染）
    assert "受限" in alert["text"] or "限流" in alert["text"]
    assert alert["priority"] == 1, "高于普通状态气泡和 Watchdog（3）"
    assert alert["sticky"] is True, "sticky 以展示「知道了」按钮"


def test_consecutive_429_merged_same_session(mgr):
    mgr._on_rate_limit("dsh", {"sessionId": "sess-2"})
    mgr._on_rate_limit("dsh", {"sessionId": "sess-2"})  # 8s 冷却窗口内 → 合并
    assert mgr._429_cache["sess-2"]["count"] == 2
    last = mgr.win.alerts[-1]
    assert "已连续限流 2 次" in last["text"]
    # 同 session 复用同一 alert_id，实现就地升级而非排队
    assert mgr.win.alerts[-2]["alert_id"] == last["alert_id"]


def test_multi_session_isolated(mgr):
    mgr._on_rate_limit("dsh", {"sessionId": "sess-A"})
    mgr._on_rate_limit("dsh", {"sessionId": "sess-B"})
    ids = [a["alert_id"] for a in mgr.win.alerts]
    assert ids == ["429-rate-limit:sess-A", "429-rate-limit:sess-B"]
    assert set(mgr._429_cache) == {"sess-A", "sess-B"}, "两个 session 各自独立缓存"


def test_dismiss_clears_cache_and_closes_alert(mgr):
    mgr._on_rate_limit("dsh", {"sessionId": "sess-3"})
    mgr._dismiss_429_alert("sess-3")
    assert "sess-3" not in mgr._429_cache, "关闭后清理缓存"
    assert "429-rate-limit:sess-3" in mgr.win.resolved, "关闭对应 alert"


def test_execution_failed_suppressed_while_429_active(mgr):
    """429 冷却窗口内出现 execution/failed：不弹通用失败横幅，避免双重通知。"""
    mgr._on_rate_limit("dsh", {"sessionId": "sess-4"})
    before = len(mgr.win.alerts)
    mgr._on_execution_failed("dsh", {"sessionId": "sess-4", "source": "model_request",
                                     "retryExhausted": True})
    assert len(mgr.win.alerts) == before, "429 活跃时抑制通用失败横幅"
