# -*- coding: utf-8 -*-
"""B9：Agent 监视器后台线程生命周期的回归测试（真实 worker + 事件驱动等待）。

等待一律用 wait_until（事件驱动 + 硬超时），不用 sleep 猜时序。
worker 节奏通过实例属性 _POLL_INTERVAL_S 调快（类属性，实例覆盖只影响本测试）。
"""
from __future__ import annotations

import gc
import json
import threading
import time
import weakref

import pytest
from PySide6.QtCore import QObject
from PySide6.QtWidgets import QApplication

from pet.agent_link import AgentEvent, AgentLinkManager, BaseAgentMonitor, CursorMonitor
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
        mon.state_changed.connect(lambda ev: received.append(ev.state))
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

    def test_restart_drops_stale_generation_signals(self, tmp_path, app):
        """重启后旧代次的迟到信号被接收端丢弃，新代次正常接收（真实信号路径）。"""
        cfg = Config(base=tmp_path)

        class DummyWin:
            idles = ["待机"]
            cats = {"acts": ["写代码"]}
            def __init__(self): self.switched = []
            def isVisible(self): return True
            def request_link_anim(self, name): self.switched.append(name)
            def request_link_idle(self): pass
            def _pick(self, lst): return lst[0]
            def show_bubble(self, *a, **k): pass

        win = DummyWin()
        mgr = AgentLinkManager(win, cfg, min_interval=0.0)
        mon = mgr.monitors["dsh"]
        mon._POLL_INTERVAL_S = 0.05
        # 先建空事件文件再启动：tailer backfill 防护只在文件存在时完成，
        # 否则启动后首轮读取会把启动间隙写入的内容当历史跳过
        mon.events_dir.mkdir(parents=True, exist_ok=True)
        mon.events_file.touch()
        # 等 worker 完成首轮 backfill 再写事件，否则被当历史跳过
        polls = []
        orig_poll = mon._poll
        mon._poll = lambda gen=None: (polls.append(1), orig_poll(gen=gen))
        assert mon.start() is True
        assert wait_until(lambda: len(polls) >= 1)
        gen1 = mon._emit_gen
        with open(mon.events_file, "a", encoding="utf-8") as f:
            f.write(json.dumps({"state": "working"}) + "\n")
        assert wait_until(lambda: len(win.switched) >= 1)
        # 停止：当前代次立即作废，旧代次信号被拒收
        mon.stop()
        n_after_stop = len(win.switched)
        mon.state_changed.emit(AgentEvent(agent="dsh", kind="state", state="working", gen=gen1))  # 迟到旧信号（直发=同步派发）
        app.processEvents()
        assert len(win.switched) == n_after_stop         # 被丢弃
        # 重启后新代次正常
        assert mon.start() is True
        mon.stop()

    def test_stop_invalidates_current_generation(self, tmp_path, app):
        """stop 后（未重启）当前代次的在途信号也被接收端拒收。"""
        cfg = Config(base=tmp_path)

        class DummyWin:
            idles = ["待机"]
            cats = {"acts": ["写代码"]}
            def __init__(self): self.switched = []
            def isVisible(self): return True
            def request_link_anim(self, name): self.switched.append(name)
            def request_link_idle(self): pass
            def _pick(self, lst): return lst[0]
            def show_bubble(self, *a, **k): pass

        win = DummyWin()
        mgr = AgentLinkManager(win, cfg, min_interval=0.0)
        mon = mgr.monitors["dsh"]
        mon._POLL_INTERVAL_S = 0.05
        assert mon.start() is True
        gen = mon._emit_gen
        mon.stop()
        # stop 后带 stop 前代次的信号到达：必须被拒（_emit_gen 已作废为 -1）
        mon.state_changed.emit(AgentEvent(agent="dsh", kind="state", state="working", gen=gen))
        app.processEvents()
        assert win.switched == []

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

class TestDestroyedFallback:
    """全审 P1-2：未 shutdown 直接销毁 monitor/manager 时，worker 不得变僵尸。

    机理（与已修复的 webm reader 僵尸同构）：worker 线程 target 是 bound
    method → 线程强引用 monitor wrapper → __del__ 永不触发；C++ 对象销毁
    时自身 bound-method 槽被 PySide6 跳过，必须靠 destroyed 无 receiver
    callable 兜底（Fix A1 模式）。"""

    def test_worker_exits_when_manager_discarded_without_shutdown(self, tmp_path, app):
        """用完即弃场景：AgentLinkManager 未 shutdown 直接丢弃（测试建窗
        不 close / 管理器被 GC）→ monitor C++ 随父链删除 → worker 必须退出。"""
        cfg = Config(base=tmp_path)
        mgr = AgentLinkManager(None, cfg)
        mon = mgr.monitors["dsh"]
        mon._POLL_INTERVAL_S = 0.05
        mon.start()
        worker = mon._worker
        assert worker is not None and worker.is_alive()
        del mgr
        gc.collect()  # 管理器 wrapper 回收 → C++ 父链删除 → destroyed 兜底
        assert wait_until(lambda: not worker.is_alive(), timeout=5.0)

    def test_worker_exits_when_parent_object_destroyed_without_shutdown(self, tmp_path, app):
        """更直接的父链场景：monitor 挂普通 QObject 父，父被删除即销毁，
        destroyed 兜底必须停掉 worker（不经 stop/shutdown）。"""
        holder = QObject()
        mon = _make_monitor(tmp_path)
        mon.setParent(holder)
        mon.start()
        worker = mon._worker
        assert worker is not None and worker.is_alive()
        del holder
        gc.collect()
        assert wait_until(lambda: not worker.is_alive(), timeout=5.0)

    def test_destroyed_guard_explicitly_breaks_lambda_cycle(self, tmp_path, app):
        """B9 复审 P2（Fix A1 对照）：销毁兜底必须显式断开 destroyed 的
        lambda 连接并清空 connection——否则 monitor→connection→lambda→monitor
        引用环只依赖 Qt 内部清理，不等价于 WebMClip.cleanup 的完整生命周期
        闭环。重复调用幂等。"""
        mon = _make_monitor(tmp_path)
        assert mon._destroyed_conn is not None
        BaseAgentMonitor._destroyed_guard(mon)
        assert mon._destroyed_conn is None          # 显式断环
        BaseAgentMonitor._destroyed_guard(mon)      # 重复销毁：幂等无操作
        assert mon._destroyed_conn is None
        assert not mon._running

    def test_stop_breaks_destroyed_lambda_cycle(self, tmp_path, app):
        """B9 复审 P2：正常 stop 路径同样显式断开 destroyed 连接（清理路径
        断环），此后 wrapper 无 lambda 环、可被 GC。"""
        mon = _make_monitor(tmp_path)
        mon.start()
        assert mon._destroyed_conn is not None
        mon.stop()
        assert mon._destroyed_conn is None
        # 重启会重建兜底连接：生命周期闭环（stop 断环 → start 重新接线）
        assert mon.start() is True
        assert mon._destroyed_conn is not None
        mon.stop()

    def test_destroyed_guard_does_not_block_calling_thread(self, tmp_path, app):
        """B9 复审 P2：destroyed 在 GUI 线程触发，兜底里的有界 join 会阻塞
        GUI 最多 2 秒——join 必须挪出调用线程（reaper），调用立即返回，
        worker 最终仍退出。"""
        mon = _make_monitor(tmp_path)
        entered = threading.Event()
        block = threading.Event()
        orig_poll = mon._poll

        def slow_poll(gen=None):
            entered.set()
            assert block.wait(timeout=10.0), "测试释放信号未到达"
            return orig_poll(gen=gen)

        mon._poll = slow_poll
        mon.start()
        assert entered.wait(timeout=5.0), "worker 未进入轮询"
        t0 = time.monotonic()
        BaseAgentMonitor._destroyed_guard(mon)      # 模拟 GUI 线程上的销毁兜底
        elapsed = time.monotonic() - t0
        assert elapsed < 0.5, f"销毁兜底阻塞了调用线程 {elapsed:.2f}s（应在 reaper 里 join）"
        block.set()                                 # 释放卡死的 I/O
        assert wait_until(lambda: not mon._worker.is_alive(), timeout=5.0)

    def test_destroyed_guard_repeated_calls_spawn_single_reaper(self, tmp_path, app):
        """B9 复审 P2：worker 仍卡住时重复调用 guard——只允许一个 reaper
        （一次性退役标记保证真正幂等），且 reaper 是 daemon、有界 join、
        做完即退（无泄漏）。"""
        mon = _make_monitor(tmp_path)
        entered = threading.Event()
        block = threading.Event()
        orig_poll = mon._poll

        def slow_poll(gen=None):
            entered.set()
            assert block.wait(timeout=10.0), "测试释放信号未到达"
            return orig_poll(gen=gen)

        mon._poll = slow_poll
        mon.start()
        assert entered.wait(timeout=5.0), "worker 未进入轮询"
        reap_name = f"agent-monitor-reap-{mon.agent_key}"

        def alive_reapers():
            return [t for t in threading.enumerate()
                    if t.name == reap_name and t.is_alive()]

        before = alive_reapers()
        # 重复触发 guard（worker 仍卡住）：旧实现每次都会新建一个 reaper
        BaseAgentMonitor._destroyed_guard(mon)
        BaseAgentMonitor._destroyed_guard(mon)
        BaseAgentMonitor._destroyed_guard(mon)
        after = alive_reapers()
        assert len(after) == len(before) + 1, \
            f"重复 guard 产生 {len(after) - len(before)} 个 reaper（应只有 1 个）"
        reaper = next(t for t in after if t not in before)
        assert reaper.daemon          # daemon：不阻止进程退出
        block.set()                   # 释放卡死的 I/O
        assert wait_until(lambda: not mon._worker.is_alive(), timeout=5.0)
        assert wait_until(lambda: not reaper.is_alive(), timeout=5.0)  # 做完即退

    def test_wrapper_collectable_after_destroyed_guard(self, tmp_path, app):
        """B9 复审 P2：显式断环后，父链销毁 + 兜底停 worker，wrapper 可被
        GC 回收（弱引用失效）——不留因 lambda 环导致的 Python 侧泄漏。"""
        holder = QObject()
        mon = _make_monitor(tmp_path)
        mon.setParent(holder)
        mon.start()
        worker = mon._worker
        assert worker is not None and worker.is_alive()
        ref = weakref.ref(mon)
        del holder          # C++ 父链删除 → destroyed → 兜底（断环 + 停 worker）
        gc.collect()
        assert wait_until(lambda: not worker.is_alive(), timeout=5.0)
        del mon
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            gc.collect()
            if not ref():
                break
            time.sleep(0.05)
        assert not ref(), "wrapper 未被回收（引用环未断）"


class TestCloseEventStopsMonitors:
    def test_window_close_stops_agent_monitors(self, tmp_path, app):
        """窗口 closeEvent 会停掉全部 Agent 监视器 worker。"""
        from tests.test_collision_window import _make_pet_window

        win, _ = _make_pet_window(tmp_path, "b9-close")
        # Phase 1：默认 Agent 联动全关，窗口不会自动创建管理器；测试显式装配。
        mgr = win._ensure_agent_link_manager()
        mon = mgr.monitors["dsh"]
        mon._POLL_INTERVAL_S = 0.05
        mon.start()
        assert mon._worker.is_alive()
        win.close()
        app.processEvents()
        assert not mon._running
        assert not mon._worker.is_alive()


def _make_opencode_db(path, rows):
    import sqlite3
    if path.exists():
        path.unlink()
    db = sqlite3.connect(path)
    db.execute("CREATE TABLE event (type TEXT, data TEXT)")
    db.execute("CREATE TABLE session (id TEXT PRIMARY KEY, parent_id TEXT)")
    db.execute("INSERT INTO session (id, parent_id) VALUES ('s1', NULL)")
    for t, d in rows:
        db.execute("INSERT INTO event (type, data) VALUES (?, ?)", (t, json.dumps(d)))
    db.commit()
    db.close()


class TestOutboxPolicy:
    def test_outbox_never_drops_state_events(self, tmp_path):
        """pause 期间 outbox 满：丢最旧的 activity；状态事件绝不丢
        （无 activity 可丢时允许超容量——已去重，现实增长有界）。"""
        mon = _make_monitor(tmp_path)
        mon._running = True
        mon._paused = True  # 白盒模拟 pause 中（不真起线程）
        for i in range(600):
            mon._emit_tool(f"tool{i}", 1)
        for s in ["working", "thinking", "attention"]:
            mon._emit_state(s, 1)
        states = [a[0] for sig, a in mon._outbox if sig is mon.state_changed]
        assert [e.state for e in states] == ["working", "thinking", "attention"]  # 尾部全保留
        assert len(mon._outbox) <= mon._OUTBOX_CAP  # 容量有界

    def test_outbox_dedupes_consecutive_states(self, tmp_path):
        """连续重复状态在 outbox 里去重（状态流大量是同态重复）。"""
        mon = _make_monitor(tmp_path)
        mon._running = True
        mon._paused = True
        for _ in range(100):
            mon._emit_state("working", 1)
        states = [a[0] for sig, a in mon._outbox if sig is mon.state_changed]
        assert len(states) == 1

    def test_resume_flushes_outbox(self, tmp_path, app):
        """resume 把 pause 期间暂存的发射补发出去。"""
        mon = _make_monitor(tmp_path)
        received = []
        mon.state_changed.connect(lambda ev: received.append(ev.state))
        mon._running = True
        mon._paused = True
        mon._emit_state("working", 1)
        assert received == []
        mon.resume()
        app.processEvents()
        assert received == ["working"]


class TestOpenCodeDbRotation:
    def test_db_replacement_rebackfills(self, tmp_path):
        """OpenCode 库被替换/重建：身份变化自动重新 backfill，不回放旧库事件、不漏新库事件。"""
        from pet.agent_link import OpenCodeMonitor

        db1 = tmp_path / "opencode.db"
        _make_opencode_db(db1, [("session.created", {"sessionID": "s1"})])
        cfg_dir = tmp_path / "cfg"
        cfg_dir.mkdir()
        mon = OpenCodeMonitor(cfg_dir, db_path=db1)
        mon._running = True
        received = []
        mon.state_changed.connect(lambda ev: received.append(ev.state))
        mon._worker_started()  # 模拟 worker 开场（worker 线程独占初始化）
        mon._poll()  # 首轮 backfill：跳到末尾
        assert received == []
        # 替换整个库文件（新内容、新 rowid 序列）
        _make_opencode_db(db1, [("session.created", {"sessionID": "s1"})])
        mon._poll()  # 检测到身份变化 → 重新 backfill，不回放新库历史
        assert received == []
        # 新库的新事件正常送达（往【当前】库追加，而不是再替换一次——
        # 替换时点已存在的内容按历史处理，这是 backfill 防护的设计语义）
        import sqlite3
        db = sqlite3.connect(db1)
        db.execute(
            "INSERT INTO event (type, data) VALUES (?, ?)",
            ("message.updated", json.dumps({"info": {"role": "user"}, "sessionID": "s1"})),
        )
        db.commit()
        db.close()
        mon._poll()
        assert "thinking" in received
