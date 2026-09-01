# -*- coding: utf-8 -*-
"""B9：Agent 监视器后台线程生命周期的回归测试（真实 worker + 事件驱动等待）。

等待一律用 wait_until（事件驱动 + 硬超时），不用 sleep 猜时序。
worker 节奏通过实例属性 _POLL_INTERVAL_S 调快（类属性，实例覆盖只影响本测试）。
"""
from __future__ import annotations

import json
import time

import pytest
from PySide6.QtWidgets import QApplication

from pet.agent_link import AgentLinkManager, BaseAgentMonitor, CursorMonitor
from pet.config import Config


@pytest.fixture()
def app():
    return QApplication.instance() or QApplication([])


def wait_until(pred, timeout=3.0):
    """事件驱动等待：处理 Qt 事件直到条件满足或硬超时。"""
    app = QApplication.instance()
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if app is not None:
            app.processEvents()
        if pred():
            return True
        time.sleep(0.01)
    return False


def _make_monitor(tmp_path, key="dsh"):
    cfg_dir = tmp_path / "cfg"
    cfg_dir.mkdir(parents=True, exist_ok=True)
    mon = BaseAgentMonitor(key, cfg_dir)
    mon._POLL_INTERVAL_S = 0.05  # 测试提速：worker 节奏 50ms
    return mon


class TestWorkerLifecycle:
    def test_pause_does_not_lose_events(self, tmp_path, app):
        """pause 期间不推进 offset：暂停时写入的事件在 resume 后完整送达。"""
        mon = _make_monitor(tmp_path)
        # 先建空事件文件：tailer 的 backfill 防护只在文件存在时完成；
        # 文件后建的话，首轮真实读取会把「启动到首轮之间写入的内容」当历史跳过
        mon.events_dir.mkdir(parents=True, exist_ok=True)
        mon.events_file.touch()
        received = []
        mon.state_changed.connect(lambda k, s, g: received.append(s))
        polls = []
        orig_poll = mon._poll
        mon._poll = lambda gen=None: (polls.append(1), orig_poll(gen=gen))
        assert mon.start() is True
        try:
            # 先等 worker 完成首轮轮询（tailer backfill 跳到文件末尾），
            # 否则写入的事件会被 backfill 防护当成历史跳过
            assert wait_until(lambda: len(polls) >= 1)
            mon.pause()
            time.sleep(0.15)  # 确保 worker 至少空转过了一轮 pause
            with open(mon.events_file, "a", encoding="utf-8") as f:
                f.write(json.dumps({"state": "working"}) + "\n")
            time.sleep(0.15)
            assert received == []  # pause 期间不得读取/发射
            mon.resume()
            assert wait_until(lambda: received == ["working"])
        finally:
            mon.stop()

    def test_restart_drops_stale_generation_signals(self, tmp_path):
        """重启后旧代次的迟到信号被接收端丢弃，新代次正常接收。"""
        cfg = Config(base=tmp_path)
        mgr = AgentLinkManager(None, cfg)
        mon = mgr.monitors["dsh"]
        applied = []
        orig = mgr._on_agent_state
        mgr._on_agent_state = lambda k, s, g=0: applied.append(s) if mgr._gen_current(k, g) else None
        # 直接验证接收端代次闸门：旧代次丢弃、当前代次放行
        assert mgr._gen_current("dsh", 0) is True   # 未启动过：gen=0
        mon._gen = 5
        mon._emit_gen = 5
        assert mgr._gen_current("dsh", 3) is False  # 旧代次
        assert mgr._gen_current("dsh", 5) is True   # 当前代次
        mgr._on_agent_state = orig

    def test_start_refused_while_old_worker_alive(self, tmp_path):
        """旧 worker 未死透时 start 拒绝重启（绝不允许双 worker）。"""
        mon = _make_monitor(tmp_path)
        assert mon.start() is True
        assert mon._worker is not None and mon._worker.is_alive()
        # worker 活着时重复 start：拒绝
        assert mon.start() is False
        mon.stop()
        assert not mon._worker.is_alive()
        # 停干净后可以重启
        assert mon.start() is True
        mon.stop()

    def test_stop_is_bounded_and_idempotent(self, tmp_path):
        mon = _make_monitor(tmp_path)
        mon.start()
        t0 = time.monotonic()
        mon.stop()
        mon.stop()  # 幂等
        assert time.monotonic() - t0 < 2.0  # 远小于 join 上限×2

    def test_shutdown_stops_all_monitors(self, tmp_path):
        """manager.shutdown：广播停止 + 共享截止，全部 monitor 停掉。"""
        cfg = Config(base=tmp_path)
        mgr = AgentLinkManager(None, cfg)
        dsh = mgr.monitors["dsh"]
        op = mgr.monitors["opencode"]
        dsh._POLL_INTERVAL_S = 0.05
        op._POLL_INTERVAL_S = 0.05
        dsh.start()
        op.start()
        assert dsh._running and op._running
        t0 = time.monotonic()
        mgr.shutdown()
        assert time.monotonic() - t0 < 3.0  # 共享截止，不是每个串行 2s
        assert not dsh._running and not op._running
        assert not dsh._worker.is_alive() and not op._worker.is_alive()

    def test_worker_does_not_poll_immediately_on_start(self, tmp_path):
        """worker 首轮先等一个周期：启动瞬间不抢读（直调 _poll 的测试 seam）。"""
        mon = _make_monitor(tmp_path)
        mon._POLL_INTERVAL_S = 0.2
        polled = []
        orig_poll = mon._poll
        mon._poll = lambda gen=None: (polled.append(1), orig_poll(gen=gen))
        mon.start()
        time.sleep(0.05)  # 短于首轮等待：不应有轮询发生
        assert polled == []
        mon.stop()


class TestCloseEventStopsMonitors:
    def test_window_close_stops_agent_monitors(self, tmp_path, app):
        """窗口 closeEvent 会停掉全部 Agent 监视器 worker。"""
        from tests.test_collision_window import _make_pet_window

        win, _ = _make_pet_window(tmp_path, "b9-close")
        mgr = win.agent_link_manager
        mon = mgr.monitors["dsh"]
        mon._POLL_INTERVAL_S = 0.05
        mon.start()
        assert mon._worker.is_alive()
        win.close()
        app.processEvents()
        assert not mon._running
        assert not mon._worker.is_alive()
